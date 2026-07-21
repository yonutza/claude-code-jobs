#!/usr/bin/env python3
"""
Daily job scan orchestrator, run by .github/workflows/daily-scan.yml.

Searches job sites via the Tavily Search API (avoids the 403 bot-blocking
that direct scraping of LinkedIn/Greenhouse/Lever/AllJobs ran into), uses
GPT-4o to extract clean structured fields from noisy search snippets and to
judge the qualitative fit signals (physical product, Europe customers,
growth stage, German language), then hands off to job_scraper.py for the
deterministic filtering/dedup/scoring/persistence logic already used by the
repo's Claude trigger (senior exclusion, location allow-list, CS/Ops
classification, seen_jobs.json bookkeeping). A final liveness check
(is_job_closed) then drops any of the shortlisted jobs that are no longer
accepting applications, via a direct HTTP GET (not a paid API call).

This runs on GitHub Actions rather than the Claude scheduled trigger because
the trigger's sandboxed environment blocks git pushes carrying an embedded
credential — GitHub Actions' own encrypted Secrets don't hit that
restriction.

Requires TAVILY_API_KEY and OPENAI_API_KEY in the environment.
"""

import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone
from urllib.parse import urlparse

import requests
from tavily import TavilyClient
from openai import OpenAI

import job_scraper as js

SOURCE_DOMAINS = {
    "linkedin": ["linkedin.com/jobs"],
    "alljobs": ["alljobs.co.il"],
    "gotfriends": ["gotfriends.co.il"],
    "nisha": ["nisha.co.il"],
    "secret_tel_aviv": ["secrettelaviv.com"],
    "greenhouse_lever": ["boards.greenhouse.io", "jobs.lever.co"],
}


def log(msg):
    print(msg, file=sys.stderr)


def load_config():
    config = js.load_json(js.CONFIG_FILE)
    if not config:
        log("config.json missing or empty — cannot build search queries.")
        sys.exit(1)
    return config


def build_queries(config):
    # Tavily is a semantic search API, not a literal search-operator engine -
    # domain scoping must go through include_domains, not a "site:" token in
    # the query text (which Tavily would just treat as ordinary words).
    queries = []
    sources = config.get("sources", {})
    search_terms = config.get("search_terms", {})
    for source, enabled in sources.items():
        if not enabled or source not in SOURCE_DOMAINS:
            continue
        domains = SOURCE_DOMAINS[source]
        for category, terms in search_terms.items():
            term_clause = " OR ".join(f'"{t}"' for t in terms[:4])
            queries.append((category, f"{term_clause} job Israel", domains))

    german_cfg = config.get("german_preference", {})
    if german_cfg.get("enabled"):
        all_domains = sorted({d for domains in SOURCE_DOMAINS.values() for d in domains})
        for term in german_cfg.get("search_terms", [])[:4]:
            queries.append(("cs", f'"{term}" customer success OR account executive OR account manager Israel jobs', all_domains))

    return queries


# Job boards rank listing/search/category pages highly for generic queries,
# but those aren't linkable to a single job — filter them out before they
# ever reach GPT so extraction isn't wasted judging pages with no real URL
# to give the candidate.
LISTING_PAGE_PATTERNS = [
    re.compile(r"SearchResults(Guest)?\.aspx", re.I),
    re.compile(r"SalaryCompound", re.I),
    re.compile(r"[?&]page=\d+"),
    re.compile(r"/jobs/search", re.I),
    re.compile(r"/jobs/collections", re.I),
    re.compile(r"linkedin\.com/jobs/?(\?.*)?$", re.I),
]


def looks_like_listing_page(url: str) -> bool:
    return any(p.search(url) for p in LISTING_PAGE_PATTERNS)


def source_for_url(url: str) -> str:
    # Derived from the URL's own domain rather than left to GPT to guess -
    # a source label is now part of the dedup ID (see stable_id), so it must
    # be deterministic across runs regardless of how GPT phrases things.
    for source, domains in SOURCE_DOMAINS.items():
        if any(d in url for d in domains):
            return source
    return "unknown"


def search_jobs(tavily, queries):
    raw_hits = {}
    skipped_listing = 0
    for category, query, domains in queries:
        try:
            resp = tavily.search(
                query=query,
                include_domains=domains,
                max_results=8,
                search_depth="advanced",
            )
        except Exception as e:
            log(f"Tavily search failed for query {query!r}: {e}")
            continue
        for r in resp.get("results", []):
            url = r.get("url")
            if not url or url in raw_hits:
                continue
            if looks_like_listing_page(url):
                skipped_listing += 1
                continue
            raw_hits[url] = {
                "url": url,
                "title": r.get("title", ""),
                "content": (r.get("content") or "")[:600],
                "source_key": source_for_url(url),
            }

    hits = list(raw_hits.values())
    domain_counts = {}
    for h in hits:
        domain = urlparse(h["url"]).netloc
        domain_counts[domain] = domain_counts.get(domain, 0) + 1
    log(f"Skipped {skipped_listing} listing/search-result pages.")
    log(f"Remaining hits by domain: {domain_counts}")
    return hits


EXTRACTION_PROMPT = """\
Below is a JSON array of search results, each already confirmed to be an
individual job posting page (not a listing/search page) from a job board in
Israel. Extract structured data for EVERY item — do not filter by
seniority or role relevance, that is handled separately downstream. Only
skip an item if it's clearly not a job at all (e.g. a login wall, an error
page, a generic homepage with no job title visible).

For each item, output:
- "url": the exact url, copied verbatim from the input
- "company": best-guess company name from the title/snippet ("" if you
  really can't tell)
- "title": the job title, cleaned up (strip site-name suffixes like
  "| AllJobs" or "- LinkedIn")
- "location": city/area if mentioned, else ""
- "source": one short label (e.g. "LinkedIn", "AllJobs", "Greenhouse",
  "Lever", "Gotfriends", "Nisha", "Secret Tel Aviv")
- "description": one short sentence (in Hebrew) about the role/company
- "german": true only if the posting explicitly mentions German as a
  requirement or advantage (German speaker, Deutsch, דובר גרמנית), else false
- "physical_product": true if the company's core product is physical/
  tangible (hardware, energy, medtech, consumer goods) rather than deep
  cloud/dev-tools infrastructure; false if unclear
- "europe_customers": true if there's a signal of European clients/offices;
  false if unclear
- "stable_growth": true if there's a positive growth/funding signal; false
  if unclear

Return a JSON object: {{"jobs": [...]}}. One entry per input item (unless
skipped per the rule above).

Items (JSON array):
{items}
"""


def extract_with_gpt(openai_client, hits):
    if not hits:
        return []

    log("Sample of raw hits fed to GPT-4o:")
    for h in hits[:8]:
        log(f"  {h['url']}  |  {h['title']}")

    items_json = json.dumps(
        [{"url": h["url"], "title": h["title"], "snippet": h["content"]} for h in hits],
        ensure_ascii=False,
    )
    prompt = EXTRACTION_PROMPT.format(items=items_json)

    resp = openai_client.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "user", "content": prompt}],
        response_format={"type": "json_object"},
        temperature=0.2,
        max_tokens=8000,
    )
    raw = resp.choices[0].message.content
    data = json.loads(raw)
    jobs = data.get("jobs", [])
    if not jobs:
        log(f"GPT-4o returned 0 jobs. Raw response (first 1500 chars): {raw[:1500]}")
    return jobs


# Verified 2026-07-21 against real listings the candidate flagged as closed:
# LinkedIn still returns 200 for a closed posting but marks it with this exact
# class name; AllJobs instead returns a plain HTTP 410 for closed postings.
# Other sites aren't verified yet, so only the universal status-code check
# applies to them for now - no unverified keyword guessing.
CLOSED_TEXT_SIGNALS = {
    "linkedin.com": ["closed-job__flavor--closed"],
}
CLOSED_STATUS_CODES = {404, 410}
LIVENESS_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


def is_job_closed(url: str) -> bool:
    # Runs only on the final ~10-20 candidates per category, not the raw
    # search hits - a plain HTTP GET, no paid API involved. Fails open (keeps
    # the job) on network errors or unrecognized-site responses, since wrongly
    # dropping an open job is worse than occasionally missing a closed one.
    try:
        resp = requests.get(url, headers={"User-Agent": LIVENESS_USER_AGENT}, timeout=8)
    except requests.RequestException as e:
        log(f"Liveness check failed for {url}: {e} - keeping job.")
        return False

    if resp.status_code in CLOSED_STATUS_CODES:
        return True
    if resp.status_code >= 400:
        return False  # probably bot-blocked, not necessarily closed

    domain = urlparse(url).netloc
    html_lower = resp.text.lower()
    for site, signals in CLOSED_TEXT_SIGNALS.items():
        if site in domain:
            return any(s in html_lower for s in signals)
    return False


def filter_closed_jobs(jobs: list) -> tuple:
    open_jobs = []
    closed_ids = []
    for job in jobs:
        url = job.get("url", "")
        if url and is_job_closed(url):
            log(f"Dropping closed job: {job.get('title', '')} ({url})")
            closed_ids.append(job["id"])
            continue
        open_jobs.append(job)
    return open_jobs, closed_ids


def stable_id(source, url):
    h = hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]
    return f"{(source or 'unknown').lower().replace(' ', '_')}_{h}"


def already_scanned_today() -> bool:
    """The workflow fires several times a day as retry attempts (GitHub's
    schedule trigger is unreliable - see .claude/skills/job-scraper.md). Once
    any attempt succeeds, later attempts the same day should no-op instead of
    re-searching and potentially double-reporting."""
    results = js.load_json(js.RESULTS_FILE)
    run_date = results.get("run_date", "")
    today = datetime.now(timezone.utc).date().isoformat()
    return run_date[:10] == today


def main():
    if already_scanned_today():
        log("Already scanned successfully today - skipping this retry attempt.")
        return

    tavily_key = os.environ.get("TAVILY_API_KEY")
    openai_key = os.environ.get("OPENAI_API_KEY")
    if not tavily_key or not openai_key:
        log("Missing TAVILY_API_KEY or OPENAI_API_KEY in environment.")
        sys.exit(1)

    config = load_config()
    tavily = TavilyClient(api_key=tavily_key)
    openai_client = OpenAI(api_key=openai_key)

    queries = build_queries(config)
    log(f"Running {len(queries)} Tavily queries...")
    hits = search_jobs(tavily, queries)
    log(f"Found {len(hits)} raw search hits.")

    # Dedup against already-reported URLs BEFORE spending GPT tokens on them -
    # the dedup ID only depends on the URL (via source_for_url + hash), so
    # this check doesn't need to wait for extraction/classification.
    seen_ids = js.get_seen_ids()
    id_by_url = {}
    new_hits = []
    skipped_seen = 0
    for h in hits:
        job_id = stable_id(h["source_key"], h["url"])
        id_by_url[h["url"]] = job_id
        if job_id in seen_ids:
            skipped_seen += 1
            continue
        new_hits.append(h)
    log(f"Skipped {skipped_seen} already-seen URLs before extraction.")

    extracted = extract_with_gpt(openai_client, new_hits)
    log(f"GPT-4o extracted {len(extracted)} candidate job postings.")

    raw_jobs = []
    for job in extracted:
        url = job.get("url", "")
        if not url or url not in id_by_url:
            continue
        job["id"] = id_by_url[url]
        raw_jobs.append(job)

    results = js.process_jobs(raw_jobs, seen_ids)

    results["cs"], cs_closed_ids = filter_closed_jobs(results["cs"])
    results["operations"], ops_closed_ids = filter_closed_jobs(results["operations"])
    closed_ids = cs_closed_ids + ops_closed_ids
    if closed_ids:
        log(f"Dropped {len(closed_ids)} closed jobs after liveness check.")

    js.update_seen(results, extra_ids=closed_ids)
    js.save_results(results)
    write_readme(results)

    total = len(results["cs"]) + len(results["operations"])
    log(f"Done. {total} new jobs ({len(results['cs'])} CS, {len(results['operations'])} Ops).")


REPORT_MARKER = "# דוח משרות יומי - "
MAX_README_DAYS = 14


def write_readme(results):
    header = [
        "# claude-code-jobs",
        "",
        "Daily job scanner for Customer Success and Account Executive/Manager roles in Israel.",
        "Runs via GitHub Actions: searches via the Tavily Search API, extracted and",
        "scored by GPT-4o, filtered/deduplicated by job_scraper.py.",
        "",
        "- `seen_jobs.json` - IDs of jobs already reported (used for deduplication).",
        "- `latest_results.json` - results from the most recent scan.",
        "- `config.json` - search terms, filters, and the German-language preference.",
        "- Shortlisted jobs get a final liveness check; postings no longer accepting",
        "  applications are dropped before the report is written.",
        f"- Keeps the last {MAX_README_DAYS} days of reports below, so a missed day is still visible.",
        "",
    ]

    readme_path = js.BASE_DIR / "README.md"
    previous_reports = []
    if readme_path.exists():
        existing = readme_path.read_text(encoding="utf-8")
        idx = existing.find(REPORT_MARKER)
        if idx != -1:
            previous_reports = [
                REPORT_MARKER + chunk.strip()
                for chunk in existing[idx:].split(REPORT_MARKER)
                if chunk.strip()
            ]

    today_report = js.format_report(results)
    all_reports = ([today_report] + previous_reports)[:MAX_README_DAYS]

    with open(readme_path, "w", encoding="utf-8") as f:
        f.write("\n".join(header) + "\n" + "\n\n".join(all_reports) + "\n")


if __name__ == "__main__":
    main()
