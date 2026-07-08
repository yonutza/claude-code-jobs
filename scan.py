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
classification, seen_jobs.json bookkeeping).

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
from urllib.parse import urlparse

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
            queries.append(("cs", f'"{term}" customer success OR operations Israel jobs', all_domains))

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
You are extracting structured job postings from raw search results for a
candidate in Israel with ~2 years of experience, looking for Customer
Success (CS) and Operations roles.

For each item below, decide if it is a genuine, currently-open individual
job posting (not a company careers overview page, a blog post, or a list of
multiple unrelated jobs). Skip anything that isn't a real single job
posting.

For each genuine job posting, extract:
- "url": the exact url of the posting, copied verbatim from the input
- "company": best-guess company name from the title/content
- "title": the job title
- "location": city/area if mentioned, else ""
- "source": one short label for where this came from (e.g. "LinkedIn",
  "AllJobs", "Greenhouse", "Lever", "Gotfriends", "Nisha", "Secret Tel Aviv")
- "description": one sentence (in Hebrew) summarizing what the role/company
  is, for internal notes
- "german": true if the posting mentions German as a requirement or
  advantage (German speaker, Deutsch, דובר גרמנית), else false
- "physical_product": true if the company's core product is physical/
  tangible (hardware, energy, medtech, consumer goods, fintech with a
  physical footprint) rather than deep cloud/dev-tools infrastructure
- "europe_customers": true if the company appears to have a meaningful
  international footprint with European clients/offices
- "stable_growth": true if there are positive signals of growth or recent
  funding and no signs of instability/layoffs

Only include items where the role is clearly Junior or Mid-level (exclude
Senior, Lead, Head of, Director, VP, Principal, Staff, בכיר).

Return a JSON object: {{"jobs": [...]}}.

Items:
{items}
"""


def extract_with_gpt(openai_client, hits):
    if not hits:
        return []

    log("Sample of raw hits fed to GPT-4o:")
    for h in hits[:8]:
        log(f"  {h['url']}  |  {h['title']}")

    lines = [
        f'- url: {h["url"]} | title: {h["title"]} | snippet: {h["content"]}'
        for h in hits
    ]
    prompt = EXTRACTION_PROMPT.format(items="\n".join(lines))

    resp = openai_client.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "user", "content": prompt}],
        response_format={"type": "json_object"},
        temperature=0.2,
    )
    raw = resp.choices[0].message.content
    data = json.loads(raw)
    jobs = data.get("jobs", [])
    if not jobs:
        log(f"GPT-4o returned 0 jobs. Raw response (first 1500 chars): {raw[:1500]}")
    return jobs


def stable_id(source, url):
    h = hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]
    return f"{(source or 'unknown').lower().replace(' ', '_')}_{h}"


def main():
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

    extracted = extract_with_gpt(openai_client, hits)
    log(f"GPT-4o extracted {len(extracted)} candidate job postings.")

    raw_jobs = []
    for job in extracted:
        url = job.get("url", "")
        if not url:
            continue
        job["id"] = stable_id(job.get("source"), url)
        raw_jobs.append(job)

    seen_ids = js.get_seen_ids()
    results = js.process_jobs(raw_jobs, seen_ids)

    js.update_seen(results)
    js.save_results(results)
    write_readme(results)

    total = len(results["cs"]) + len(results["operations"])
    log(f"Done. {total} new jobs ({len(results['cs'])} CS, {len(results['operations'])} Ops).")


def write_readme(results):
    lines = [
        "# claude-code-jobs",
        "",
        "Daily job scanner for Customer Success and Operations roles in Israel.",
        "Runs via GitHub Actions: searches via the Tavily Search API, extracted and",
        "scored by GPT-4o, filtered/deduplicated by job_scraper.py.",
        "",
        "- `seen_jobs.json` - IDs of jobs already reported (used for deduplication).",
        "- `latest_results.json` - results from the most recent scan.",
        "- `config.json` - search terms, filters, and the German-language preference.",
        "",
        js.format_report(results),
        "",
    ]
    with open(js.BASE_DIR / "README.md", "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
