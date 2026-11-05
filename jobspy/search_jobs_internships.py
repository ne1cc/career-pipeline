#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import pandas as pd
import requests

from cockpit.internships import (
    INTERN_TITLE_PATTERN,
    classify,
    parse_jobright,
    parse_simplify,
)
from jobspy import scrape_jobs

# All outputs go into scraped jobs/results/YYYY_MM/YYYY_MM_DD/ next to this script
default_out = (
    Path(__file__).parent
    / "scraped jobs"
    / "results"
    / datetime.now().strftime("%Y_%m")
    / datetime.now().strftime("%Y_%m_%d")
)
OUTPUT_DIR = Path(os.environ.get("JOBSPY_RESULTS_DIR", default_out))
os.makedirs(OUTPUT_DIR, exist_ok=True)

DEFAULT_LOCATIONS = [
    "Seattle, WA",
    "Remote",
]

DEFAULT_SEARCH_TERMS = [
    "Data Analyst Intern",
    "Business Analyst Intern",
    "Data Scientist Intern",
    "Analytics Engineer Intern",
    "Business Intelligence Intern",
    "Quantitative Analyst Intern",
    "Machine Learning Intern",
    "Software Engineer Intern",
]

SIMPLIFY_JSON_URL = (
    "https://raw.githubusercontent.com/SimplifyJobs/Summer2027-Internships/dev/.github/scripts/listings.json"
)
JOBRIGHT_README_URL = (
    "https://raw.githubusercontent.com/jobright-ai/2026-Data-Analysis-Internship/master/README.md"
)


def flush_jobs_to_csv(jobs_list: list[pd.DataFrame], output_path: Path) -> int:
    """Atomically write collected job dataframes to CSV with deduplication."""
    if not jobs_list:
        return 0
    valid_frames = [df for df in jobs_list if not df.empty]
    if not valid_frames:
        return 0
    combined = pd.concat(valid_frames, ignore_index=True)
    if "job_url" in combined.columns:
        combined.drop_duplicates(subset=["job_url"], keep="first", inplace=True)
    if "title" in combined.columns:
        combined.sort_values(
            by="title",
            key=lambda col: col.astype(str).str.lower(),
            ascending=True,
            na_position="last",
            inplace=True,
        )
    tmp_path = output_path.with_suffix(".tmp")
    combined.to_csv(
        tmp_path,
        quoting=csv.QUOTE_NONNUMERIC,
        escapechar="\\",
        index=False,
    )
    tmp_path.replace(output_path)
    return len(combined)


def scrape_jobspy_boards(
    terms: list[str],
    locations: list[str],
    smoke_test: bool = False,
) -> list[pd.DataFrame]:
    """Scrape JobSpy boards (Indeed, LinkedIn, Google) for internship listings."""
    print(f"\n[1/3] Scraping JobSpy boards ({len(locations)} locations × {len(terms)} terms)...")
    collected: list[pd.DataFrame] = []

    for loc in locations:
        print(f"\n--- Location: {loc} ---")
        for term in terms:
            print(f"  > Scraping: '{term}' in '{loc}'...", end=" ", flush=True)
            try:
                results_wanted = 15 if smoke_test else 100
                jobs = scrape_jobs(
                    site_name=["indeed", "linkedin", "google"],
                    search_term=term,
                    location=loc,
                    google_search_term=f"{term} jobs near {loc}",
                    country_indeed="USA",
                    results_wanted=results_wanted,
                    job_type="internship",
                    linkedin_fetch_description=True,
                )
                if not jobs.empty:
                    jobs["search_keyword"] = term
                    jobs["search_city"] = loc
                    collected.append(jobs)
                    print(f"found {len(jobs)} jobs")
                else:
                    print("found 0 jobs (WARN: zero results from query)")
            except Exception as e:
                print(f"error: {e}")

            if smoke_test:
                break
        if smoke_test:
            break

    return collected


def scrape_ats_internships(smoke_test: bool = False) -> list[pd.DataFrame]:
    """Scrape direct ATS boards for target companies and filter for internship titles."""
    print(f"\n[2/3] Scraping target company ATS boards for internships...")
    collected: list[pd.DataFrame] = []

    try:
        from jobspy import search_jobs_ats

        target_companies = search_jobs_ats.TARGET_COMPANIES
        if smoke_test:
            # Narrow to 2 companies in smoke test mode
            target_companies = {"greenhouse": ["stripe", "figma"]}

        ats_jobs = search_jobs_ats.scrape_target_companies(target_companies)
        intern_jobs = [
            j for j in ats_jobs if INTERN_TITLE_PATTERN.search(j.get("title", ""))
        ]
        if intern_jobs:
            df = pd.DataFrame(intern_jobs)
            collected.append(df)
            print(f"Found {len(intern_jobs)} matching internship roles from direct ATS boards.")
        else:
            print("Found 0 matching internship roles from direct ATS boards.")
    except Exception as e:
        print(f"Error scraping ATS boards: {e}")

    return collected


def scrape_curated_lists(smoke_test: bool = False) -> list[pd.DataFrame]:
    """Fetch and parse curated lists (SimplifyJobs Summer 2027 and Jobright)."""
    print(f"\n[3/3] Fetching curated internship listings...")
    collected: list[pd.DataFrame] = []
    session = requests.Session()
    headers = {"User-Agent": "Mozilla/5.0 (compatible; CareerPipeline/1.0)"}

    # 1. Simplify Summer 2027
    print("  > Fetching Simplify Summer 2027 JSON...", end=" ", flush=True)
    try:
        resp = session.get(SIMPLIFY_JSON_URL, headers=headers, timeout=30)
        if resp.status_code == 200:
            listings = resp.json()
            simplify_df = parse_simplify(listings)
            if not simplify_df.empty:
                collected.append(simplify_df)
                print(f"parsed {len(simplify_df)} active Summer 2027 postings.")
            else:
                print("0 postings parsed.")
        else:
            print(f"HTTP {resp.status_code}")
    except Exception as e:
        print(f"failed: {e}")

    # 2. Jobright Data Analysis Internships
    print("  > Fetching Jobright Data Analysis README...", end=" ", flush=True)
    try:
        resp = session.get(JOBRIGHT_README_URL, headers=headers, timeout=30)
        if resp.status_code == 200:
            jobright_df = parse_jobright(resp.text)
            if not jobright_df.empty:
                collected.append(jobright_df)
                print(f"parsed {len(jobright_df)} postings.")
            else:
                print("0 postings parsed.")
        else:
            print(f"HTTP {resp.status_code}")
    except Exception as e:
        print(f"failed: {e}")

    return collected


def main() -> None:
    parser = argparse.ArgumentParser(description="Career Pipeline dedicated internship scraper.")
    parser.add_argument("--smoke-test", action="store_true", help="Quick test run with minimal queries.")
    parser.add_argument("--no-jobspy", action="store_true", help="Skip JobSpy board queries.")
    parser.add_argument("--no-ats", action="store_true", help="Skip direct ATS company scrapes.")
    parser.add_argument("--no-curated", action="store_true", help="Skip Simplify/Jobright lists.")
    args = parser.parse_args()

    output_path = OUTPUT_DIR / "internship_jobs.csv"
    terms = DEFAULT_SEARCH_TERMS[:2] if args.smoke_test else DEFAULT_SEARCH_TERMS
    locations = DEFAULT_LOCATIONS[:1] if args.smoke_test else DEFAULT_LOCATIONS

    all_frames: list[pd.DataFrame] = []

    if not args.no_jobspy:
        all_frames.extend(scrape_jobspy_boards(terms, locations, smoke_test=args.smoke_test))

    if not args.no_ats:
        all_frames.extend(scrape_ats_internships(smoke_test=args.smoke_test))

    if not args.no_curated:
        all_frames.extend(scrape_curated_lists(smoke_test=args.smoke_test))

    if not all_frames:
        print("\nNo jobs collected from any source.")
        return

    combined = pd.concat([df for df in all_frames if not df.empty], ignore_index=True)
    print(f"\nTotal raw collected rows: {len(combined):,}")

    classified = classify(combined)
    print(f"Classified internship rows: {len(classified):,}")

    total_saved = flush_jobs_to_csv([classified], output_path)
    print(f"✔ Successfully saved {total_saved:,} unique internship postings to {output_path}")


if __name__ == "__main__":
    main()
