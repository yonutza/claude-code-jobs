#!/usr/bin/env python3
"""
Helper module for job scraper logic: filtering, deduplication, classification, formatting.
Actual web fetching is done by the Claude agent using WebSearch/WebFetch tools.
"""

import json
import re
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).parent
SEEN_JOBS_FILE = BASE_DIR / "seen_jobs.json"
CONFIG_FILE = BASE_DIR / "config.json"
RESULTS_FILE = BASE_DIR / "latest_results.json"

MAX_SEEN_IDS = 5000


def load_json(path: Path):
    if path.exists():
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_json(path: Path, data) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def make_job_id(source: str, title: str, company: str) -> str:
    t = re.sub(r'[^a-z0-9]', '_', title.lower()[:40])
    c = re.sub(r'[^a-z0-9]', '_', company.lower()[:20])
    return f"{source}_{t}_{c}"


EXCLUDE_SENIORITY = [
    "senior", "sr.", "sr ", "lead", "head of", "director", "vp ", "vice president",
    "principal", "staff", "בכיר", "מנהל בכיר", "team lead", "manager of managers",
]

INCLUDE_LOCATIONS = [
    "תל אביב", "גוש דן", "מרכז", "רמת גן", "פתח תקווה", "הרצליה", "רעננה",
    "נתניה", "ראשון לציון", "בני ברק", "גבעתיים", "חולון", "אור יהודה",
    "חיפה", "יקנעם", "קריית ביאליק", "קריות",
    "tel aviv", "herzliya", "raanana", "haifa", "yokneam", "center", "central",
    "petah tikva", "rishon", "netanya", "ramat gan",
    "remote", "hybrid",  # include remote/hybrid - can filter manually
]

CS_TERMS = [
    "customer success", "csm", "customer success manager", "customer success specialist",
    "account manager", "client success", "הצלחת לקוחות", "לקוחות",
    "customer experience", "cx manager", "client manager", "customer advocate",
]

OPS_TERMS = [
    "operations manager", "operations specialist", "ops manager",
    "revenue operations", "revops", "sales operations", "sales ops",
    "customer operations", "business operations", "biz ops",
    "operations analyst", "operations coordinator",
    "אופרציה", "תפעול", "מנהל תפעול",
]


def is_senior(title: str) -> bool:
    t = title.lower()
    return any(s in t for s in EXCLUDE_SENIORITY)


def is_valid_location(location: str) -> bool:
    if not location or location.strip() == "":
        return True  # no location specified = include (might be remote)
    loc = location.lower()
    return any(l.lower() in loc for l in INCLUDE_LOCATIONS)


GERMAN_TERMS = [
    "german speaker", "german language", "deutsch", "deutschkenntnisse",
    "דובר גרמנית", "דוברי גרמנית", "speaks german", "german proficiency",
]


def score_job(job: dict) -> int:
    """Returns a priority score. Higher = show first."""
    score = 0
    text = (job.get("title", "") + " " + job.get("description", "") + " " + job.get("notes", "")).lower()
    if any(t in text for t in GERMAN_TERMS):
        score += 2
        job["german"] = True
    if job.get("physical_product") or job.get("europe_customers"):
        score += 1
    if job.get("stable_growth"):
        score += 1
    return score


def classify_job(title: str) -> str | None:
    t = title.lower()
    cs_score = sum(1 for term in CS_TERMS if term.lower() in t)
    ops_score = sum(1 for term in OPS_TERMS if term.lower() in t)
    if cs_score > 0 and cs_score >= ops_score:
        return "cs"
    if ops_score > 0:
        return "operations"
    return None


def process_jobs(raw_jobs: list[dict], seen_ids: set) -> dict[str, list[dict]]:
    """Filter, classify, and deduplicate raw job list."""
    cs_jobs = []
    ops_jobs = []
    title_company_seen = set()

    for job in raw_jobs:
        job_id = job.get("id") or make_job_id(
            job.get("source", "unknown"), job.get("title", ""), job.get("company", "")
        )
        job["id"] = job_id

        if job_id in seen_ids:
            continue

        title = job.get("title", "")
        company = job.get("company", "")
        location = job.get("location", "")

        key = f"{title.lower().strip()}|{company.lower().strip()}"
        if key in title_company_seen:
            continue
        title_company_seen.add(key)

        if is_senior(title):
            continue
        if not is_valid_location(location):
            continue

        job_type = classify_job(title)
        if job_type == "cs":
            cs_jobs.append(job)
        elif job_type == "operations":
            ops_jobs.append(job)

    # Score and sort: German-preferred jobs first
    for job in cs_jobs:
        job["score"] = score_job(job)
    for job in ops_jobs:
        job["score"] = score_job(job)

    cs_jobs.sort(key=lambda j: j.get("score", 0), reverse=True)
    ops_jobs.sort(key=lambda j: j.get("score", 0), reverse=True)

    return {
        "cs": cs_jobs[:10],
        "operations": ops_jobs[:10],
    }


def update_seen(results: dict[str, list[dict]]) -> None:
    seen_data = load_json(SEEN_JOBS_FILE)
    seen_ids = list(seen_data.get("seen_ids", []))
    new_ids = [j["id"] for j in results["cs"] + results["operations"]]
    all_ids = seen_ids + new_ids
    if len(all_ids) > MAX_SEEN_IDS:
        all_ids = all_ids[-MAX_SEEN_IDS:]
    seen_data["seen_ids"] = all_ids
    seen_data["last_run"] = datetime.now().isoformat()
    save_json(SEEN_JOBS_FILE, seen_data)


def save_results(results: dict[str, list[dict]]) -> None:
    save_json(RESULTS_FILE, {
        "run_date": datetime.now().isoformat(),
        "cs": results["cs"],
        "operations": results["operations"],
    })


def format_report(results: dict[str, list[dict]]) -> str:
    run_date = datetime.now().strftime("%d/%m/%Y")
    lines = [
        f"# דוח משרות יומי - {run_date}",
        "",
        f"## Customer Success ({len(results['cs'])} משרות)",
        "",
    ]
    for i, job in enumerate(results["cs"], 1):
        url = job.get("url", "")
        company = job.get("company", "?")
        title = job.get("title", "")
        location = job.get("location", "")
        source = job.get("source", "")
        flag = "🇩🇪 " if job.get("german") else ""
        if url:
            lines.append(f"{i}. {flag}**[{company}]({url})** – {title}")
        else:
            lines.append(f"{i}. {flag}**{company}** – {title}")
        meta = " | ".join(filter(None, [location, source]))
        if meta:
            lines.append(f"   _{meta}_")
        lines.append("")

    lines += [f"## Operations ({len(results['operations'])} משרות)", ""]
    for i, job in enumerate(results["operations"], 1):
        url = job.get("url", "")
        company = job.get("company", "?")
        title = job.get("title", "")
        location = job.get("location", "")
        source = job.get("source", "")
        flag = "🇩🇪 " if job.get("german") else ""
        if url:
            lines.append(f"{i}. {flag}**[{company}]({url})** – {title}")
        else:
            lines.append(f"{i}. {flag}**{company}** – {title}")
        meta = " | ".join(filter(None, [location, source]))
        if meta:
            lines.append(f"   _{meta}_")
        lines.append("")

    total = len(results["cs"]) + len(results["operations"])
    german_count = sum(1 for j in results["cs"] + results["operations"] if j.get("german"))
    lines.append(f"---")
    german_note = f" | {german_count} עם יתרון גרמנית 🇩🇪" if german_count else ""
    lines.append(f'סה"כ: {total} משרות חדשות{german_note}')
    return "\n".join(lines)


def get_seen_ids() -> set:
    seen_data = load_json(SEEN_JOBS_FILE)
    return set(seen_data.get("seen_ids", []))
