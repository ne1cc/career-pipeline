#!/usr/bin/env python3
"""Campus & Early-Career Job Ingestion.

Pulls curated, real-time structured listings for new-grad, early-career,
and entry-level analyst/data/software roles from open community repositories
(e.g., SimplifyJobs New Grad feed) and exports them into standard Cockpit CSV format.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

# Output directory: jobspy/scraped jobs/results/YYYY_MM/YYYY_MM_DD/
default_out = Path(__file__).parent / "scraped jobs" / "results" / datetime.now().strftime("%Y_%m") / datetime.now().strftime("%Y_%m_%d")
OUTPUT_DIR = Path(os.environ.get("JOBSPY_RESULTS_DIR", default_out))
os.makedirs(OUTPUT_DIR, exist_ok=True)

# SimplifyJobs open-source new-grad listings feed
SIMPLIFY_NEW_GRAD_URL = "https://raw.githubusercontent.com/SimplifyJobs/New-Grad-Positions/dev/.github/scripts/listings.json"

# Additional curated Jobright 2026/2027 New Grad README feeds
JOBRIGHT_NEW_GRAD_URLS = [
    "https://raw.githubusercontent.com/jobright-ai/2026-Data-Analysis-New-Grad/master/README.md",
    "https://raw.githubusercontent.com/jobright-ai/2026-Software-Engineer-New-Grad/master/README.md",
]

STANDARD_COLUMNS = [
    "id",
    "site",
    "job_url",
    "job_url_direct",
    "title",
    "company",
    "location",
    "date_posted",
    "posted_at",
    "first_seen_at",
    "description",
    "is_remote",
    "search_keyword",
]

# Role filtering pattern for campus/early career listings
CAMPUS_ROLE_PATTERN = re.compile(
    r"\b(?:"
    r"analyst|analytics|data|scientist|statistician|quantitative|bi|"
    r"business analyst|operations|bizops|revops|solutions|product|"
    r"software|developer|backend|platform|security|cybersecurity"
    r")\b",
    re.IGNORECASE,
)


def fetch_simplify_listings(timeout_secs: int = 15) -> List[Dict[str, Any]]:
    """Fetch raw listings JSON from SimplifyJobs GitHub repo."""
    print(f"Fetching SimplifyJobs early-career listings from:\n  {SIMPLIFY_NEW_GRAD_URL}")
    req = urllib.request.Request(
        SIMPLIFY_NEW_GRAD_URL,
        headers={"User-Agent": "Mozilla/5.0 (compatible; CareerPipelineCampusBot/1.0)"},
    )
    with urllib.request.urlopen(req, timeout=timeout_secs) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    print(f"Fetched {len(data)} total listings from repository.")
    return data


def normalize_simplify_item(item: Dict[str, Any]) -> Dict[str, Any] | None:
    """Normalize a Simplify listing dictionary into standard Cockpit schema."""
    # Skip inactive listings
    if not item.get("active", True):
        return None

    title = str(item.get("title") or "").strip()
    company = str(item.get("company_name") or "").strip()
    apply_url = str(item.get("url") or "").strip()

    if not (title and company and apply_url):
        return None

    # Filter for relevant analytics / data / software roles
    if not CAMPUS_ROLE_PATTERN.search(title):
        return None

    locations = item.get("locations") or []
    loc_str = ", ".join(locations) if isinstance(locations, list) else str(locations)
    is_remote = any("remote" in loc.lower() for loc in locations) if locations else False

    # Format date posted
    date_posted_ts = item.get("date_posted") or item.get("date_updated")
    date_posted_str = None
    posted_at_iso = None
    if date_posted_ts:
        try:
            dt = datetime.fromtimestamp(int(date_posted_ts), tz=timezone.utc)
            posted_at_iso = dt.isoformat()
            date_posted_str = dt.strftime("%Y-%m-%d")
        except Exception:
            pass

    job_id = str(item.get("id") or apply_url)
    company_url = str(item.get("company_url") or "")
    category = str(item.get("category") or "early_career")

    # Build description summary
    description_lines = [
        f"Role: {title}",
        f"Company: {company}",
        f"Category: {category}",
        f"Locations: {loc_str}",
        f"Sponsorship: {item.get('sponsorship', 'Not specified')}",
        f"Simplify URL: {company_url}",
    ]
    description = "\n".join(description_lines)

    return {
        "id": job_id,
        "site": "simplify_campus",
        "job_url": apply_url,
        "job_url_direct": apply_url,
        "title": title,
        "company": company,
        "location": loc_str,
        "date_posted": date_posted_str,
        "posted_at": posted_at_iso,
        "first_seen_at": posted_at_iso or datetime.now(timezone.utc).isoformat(),
        "description": description,
        "is_remote": is_remote,
        "search_keyword": "early_career",
    }


def fetch_jobright_new_grad(timeout_secs: int = 15) -> List[Dict[str, Any]]:
    """Fetch and normalize curated Jobright New Grad markdown tables (best-effort)."""
    from cockpit.internships import parse_jobright

    rows: List[Dict[str, Any]] = []
    for url in JOBRIGHT_NEW_GRAD_URLS:
        try:
            req = urllib.request.Request(
                url,
                headers={"User-Agent": "Mozilla/5.0 (compatible; CareerPipelineCampusBot/1.0)"},
            )
            with urllib.request.urlopen(req, timeout=timeout_secs) as resp:
                md_text = resp.read().decode("utf-8", errors="replace")
            df = parse_jobright(md_text)
            if df.empty:
                continue
            for _, item in df.iterrows():
                title = str(item.get("title") or "").strip()
                company = str(item.get("company") or "").strip()
                apply_url = str(item.get("job_url") or "").strip()
                if not (title and company and apply_url):
                    continue
                if not CAMPUS_ROLE_PATTERN.search(title):
                    continue
                loc_str = str(item.get("location") or "").strip()
                rows.append(
                    {
                        "id": str(item.get("id") or apply_url),
                        "site": "jobright_campus",
                        "job_url": apply_url,
                        "job_url_direct": apply_url,
                        "title": title,
                        "company": company,
                        "location": loc_str,
                        "date_posted": item.get("date_posted"),
                        "posted_at": None,
                        "first_seen_at": datetime.now(timezone.utc).isoformat(),
                        "description": str(item.get("description") or f"Role: {title}\nCompany: {company}"),
                        "is_remote": bool(item.get("is_remote", False)),
                        "search_keyword": "early_career",
                    }
                )
            print(f"Fetched {len(df)} rows from {url.rsplit('/', 3)[-3]}.")
        except Exception as exc:
            print(f"Skipping Jobright feed ({url}): {exc}")
    return rows


def scrape_campus_feed(smoke_test: bool = False) -> Path:
    """Fetch, filter, and write campus early-career listings to CSV."""
    raw_listings = fetch_simplify_listings()
    if smoke_test:
        raw_listings = raw_listings[:500]

    normalized_rows = []
    seen_urls: set[str] = set()
    for item in raw_listings:
        row = normalize_simplify_item(item)
        if row and row["job_url"] not in seen_urls:
            seen_urls.add(row["job_url"])
            normalized_rows.append(row)

    if not smoke_test:
        for row in fetch_jobright_new_grad():
            if row["job_url"] not in seen_urls:
                seen_urls.add(row["job_url"])
                normalized_rows.append(row)

    print(f"Filtered down to {len(normalized_rows)} active matching roles.")

    if not normalized_rows:
        print("No matching campus roles found.")
        return OUTPUT_DIR / "campus_early_career_jobs.csv"

    output_path = OUTPUT_DIR / "campus_early_career_jobs.csv"
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=STANDARD_COLUMNS,
            quoting=csv.QUOTE_NONNUMERIC,
            escapechar="\\",
        )
        writer.writeheader()
        writer.writerows(normalized_rows)

    print(f"Saved {len(normalized_rows)} campus listings to:\n  {output_path}")
    return output_path


def main():
    parser = argparse.ArgumentParser(
        description="Ingest campus and early-career jobs from open curated feeds."
    )
    parser.add_argument(
        "--smoke-test",
        action="store_true",
        help="Run on a small subset of listings for fast verification.",
    )
    args = parser.parse_args()

    scrape_campus_feed(smoke_test=args.smoke_test)


if __name__ == "__main__":
    main()
