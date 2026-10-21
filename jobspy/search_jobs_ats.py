import argparse
import csv
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd
import ats_scrapers
from ats_scrapers import Client
from ats_scrapers.scrapers import (
    AshbyScraper,
    GemScraper,
    GreenhouseScraper,
    LeverScraper,
    get_scraper,
)

# Output directory: jobspy/scraped jobs/results/YYYY_MM/YYYY_MM_DD/
default_out = Path(__file__).parent / "scraped jobs" / "results" / datetime.now().strftime("%Y_%m") / datetime.now().strftime("%Y_%m_%d")
OUTPUT_DIR = Path(os.environ.get("JOBSPY_RESULTS_DIR", default_out))
os.makedirs(OUTPUT_DIR, exist_ok=True)

# Expanded Search Keywords
DEFAULT_SEARCH_TERMS = [
    # Core Data & Analytics
    "Data Analyst",
    "Business Analyst",
    "Data Scientist",
    "Statistician",
    "Quantitative Analyst",
    "Operations Research Analyst",
    "Product Analyst",
    "Growth Analyst",
    "Analytics Engineer",
    "Full Stack Analytics Engineer",
    # Business Intelligence
    "Business Intelligence Analyst",
    "BI Developer",
    "BI Engineer",
    # Domain-Specific Analytics
    "Financial Analyst",
    "Market Research Analyst",
    "Marketing Data Analyst",
    "Marketing Analytics Specialist",
    "Supply Chain Analyst",
    "Pricing Analyst",
    "Revenue Analyst",
    "Sales Operations Analyst",
    "Risk Analyst",
    # Systems & Consulting
    "Business Analytics Consultant",
    "Computer Systems Analyst",
    "Programmer Analyst",
    "Associate Data & Analytics",
    "Technology Advisory Associate",
    "Strategy Consultant",
    # Product & Operations
    "Associate Product Manager",
    "Business Operations Associate",
    # Early Career & Junior Roles
    "New Grad Software Engineer",
    "Data Engineer",
    "Junior Backend Engineer",
    "Backend Engineer",
    "Platform Engineer",
    "Product Engineer",
    "Associate Product Engineer",
    "Junior Data Analyst",
    "Early Career Analyst",
    "Junior Software Engineer",
    "Associate Software Engineer",
    "Junior Developer",
    "Junior Security Engineer",
    "Cybersecurity Analyst",
    "SOC Analyst",
]
SEARCH_TERMS = DEFAULT_SEARCH_TERMS

# Target Company Registry across Greenhouse, Lever, Ashby
TARGET_COMPANIES: Dict[str, List[str]] = {
    "greenhouse": [
        "stripe",
        "airbnb",
        "databricks",
        "snowflake",
        "figma",
        "doordash",
        "instacart",
        "pinterest",
        "dropbox",
        "reddit",
        "robinhood",
    ],
    "lever": [
        "spotify",
        "eventbrite",
    ],
    "ashby": [
        "openai",
        "anthropic",
        "ramp",
        "notion",
        "perplexity",
        "retool",
        "cursor",
        "linear",
        "vercel",
    ],
}

# Explicit aliases/mappings for company boards that reside on specific slugs or ATS types
COMPANY_ALIASES = {
    "snowflake": ("ashby", "snowflake"),
    "anthropic": ("greenhouse", "anthropic"),
    "doordash": ("greenhouse", "doordashusa"),
    "eventbrite": ("greenhouse", "eventbriteinc"),
    "retool": ("gem", "retool"),
}

# Regex filter for analytics/data/bi job titles in target company boards
ROLE_PATTERN = re.compile(
    r"\b(?:analyst|analytics|data|scientist|statistician|quantitative|bi|"
    r"software|developer|backend|back-end|platform|product engineer|security|cybersecurity|"
    r"infosec|soc|devops|sre)\b",
    re.IGNORECASE,
)

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
    "is_remote",
    "search_keyword",
]


def normalize_job_object(job: Any, search_keyword: str) -> Dict[str, Any]:
    """Normalize an ats_scrapers Job model instance to standard schema."""
    job_url = str(job.url) if job.url else (str(job.apply_url) if job.apply_url else "")
    apply_url = str(job.apply_url) if job.apply_url else job_url
    if not job_url:
        job_url = apply_url

    job_id = getattr(job, "global_id", None) or getattr(job, "ats_id", None) or job_url
    posted_at = str(job.posted_at) if job.posted_at else None

    is_remote = job.is_remote
    if is_remote is None:
        # Infer remote if location mentions remote
        loc_str = str(job.location or "").lower()
        is_remote = "remote" in loc_str

    return {
        "id": str(job_id),
        "site": "ats",
        "job_url": job_url,
        "job_url_direct": apply_url,
        "title": str(job.title or "").strip(),
        "company": str(job.company or "").strip(),
        "location": str(job.location or "").strip(),
        "date_posted": posted_at[:10] if posted_at else None,
        "posted_at": posted_at,
        "is_remote": bool(is_remote),
        "search_keyword": search_keyword,
    }


def normalize_dataset_row(row: pd.Series, search_keyword: str) -> Dict[str, Any]:
    """Normalize a hosted dataset DataFrame row to standard schema."""
    job_url = str(row.get("url", "")) if pd.notna(row.get("url")) else ""
    apply_url = str(row.get("apply_url", "")) if pd.notna(row.get("apply_url")) else job_url
    if not job_url or job_url == "nan":
        job_url = apply_url

    job_id = row.get("global_id") or row.get("ats_id") or job_url
    if pd.isna(job_id) or str(job_id) == "nan":
        job_id = job_url

    posted_at = str(row.get("posted_at")) if pd.notna(row.get("posted_at")) else None
    if posted_at == "nan":
        posted_at = None

    is_remote = row.get("is_remote")
    if pd.isna(is_remote):
        loc_str = str(row.get("location", "")).lower()
        is_remote = "remote" in loc_str
    else:
        is_remote = bool(is_remote)

    title = str(row.get("title", "")) if pd.notna(row.get("title")) else ""
    company = str(row.get("company", "")) if pd.notna(row.get("company")) else ""
    location = str(row.get("location", "")) if pd.notna(row.get("location")) else ""

    return {
        "id": str(job_id),
        "site": "ats",
        "job_url": job_url,
        "job_url_direct": apply_url,
        "title": title.strip(),
        "company": company.strip(),
        "location": location.strip(),
        "date_posted": posted_at[:10] if posted_at else None,
        "posted_at": posted_at,
        "is_remote": is_remote,
        "search_keyword": search_keyword,
    }


def scrape_target_companies(
    target_companies: Dict[str, List[str]],
) -> List[Dict[str, Any]]:
    """Query direct company ATS boards and filter for analytics/data/bi roles."""
    all_matched_jobs: List[Dict[str, Any]] = []

    print(f"\n[1/2] Scraping direct ATS boards for target companies...")
    for default_ats, companies in target_companies.items():
        for company in companies:
            ats_type, slug = COMPANY_ALIASES.get(company, (default_ats, company))
            print(f" > Fetching '{company}' ({ats_type}:{slug})...", end=" ", flush=True)

            try:
                scraper = get_scraper(ats_type, slug)
                jobs = scraper.fetch()
                matched = [j for j in jobs if ROLE_PATTERN.search(j.title or "")]

                for job in matched:
                    # If job.company is missing, default to company name
                    if not getattr(job, "company", None):
                        job.company = company
                    record = normalize_job_object(job, search_keyword="target_company")
                    all_matched_jobs.append(record)

                print(f"found {len(matched)} matching roles (out of {len(jobs)} total)")
            except Exception as e:
                print(f"error: {e}")

    return all_matched_jobs


def scrape_hosted_dataset(
    search_terms: List[str],
    is_smoke_test: bool = False,
) -> List[Dict[str, Any]]:
    """Query hosted ATS dataset snapshot for keywords."""
    all_matched_jobs: List[Dict[str, Any]] = []

    if is_smoke_test:
        print("\n[2/2] Skipping hosted dataset download in smoke-test mode.")
        return all_matched_jobs

    print("\n[2/2] Loading hosted Ashby dataset snapshot...")
    try:
        client = Client()
        df_snapshot = client.load(ats="ashby")
        print(f"Loaded snapshot with {len(df_snapshot)} postings.")

        for term in search_terms:
            print(f" > Filtering hosted snapshot for '{term}'...", end=" ", flush=True)
            # Case-insensitive title match
            pattern = re.escape(term)
            sub_df = df_snapshot[
                df_snapshot["title"].str.contains(pattern, case=False, na=False)
            ]

            term_records = []
            for _, row in sub_df.iterrows():
                record = normalize_dataset_row(row, search_keyword=term)
                term_records.append(record)

            all_matched_jobs.extend(term_records)
            print(f"found {len(term_records)} roles")

    except Exception as e:
        print(f"Error querying hosted dataset: {e}")

    return all_matched_jobs


def run_ats_scrape(smoke_test: bool = False, skip_dataset: bool = False) -> Path:
    """Run native ATS scraping flow and save deduplicated, sorted results to CSV."""
    if smoke_test:
        print("=== RUNNING IN SMOKE-TEST MODE ===")
        terms_to_search = ["Data Analyst"]
        companies_to_scrape = {
            "greenhouse": ["stripe"],
            "ashby": ["openai"],
        }
    else:
        terms_to_search = SEARCH_TERMS
        companies_to_scrape = TARGET_COMPANIES

    all_jobs: List[Dict[str, Any]] = []

    # 1. Scrape direct target companies
    company_jobs = scrape_target_companies(companies_to_scrape)
    all_jobs.extend(company_jobs)

    # 2. Scrape hosted dataset
    if not skip_dataset:
        dataset_jobs = scrape_hosted_dataset(terms_to_search, is_smoke_test=smoke_test)
        all_jobs.extend(dataset_jobs)

    if not all_jobs:
        print("\nNo jobs collected.")
        return OUTPUT_DIR / "ats_direct_jobs.csv"

    df = pd.DataFrame(all_jobs)

    # Ensure all standard columns exist
    for col in STANDARD_COLUMNS:
        if col not in df.columns:
            df[col] = None
    df = df[STANDARD_COLUMNS]

    # Deduplicate by job_url
    initial_count = len(df)
    df.drop_duplicates(subset=["job_url"], keep="first", inplace=True)
    deduped_count = len(df)

    # Sort alphabetically by title (case-insensitive)
    df.sort_values(
        by="title",
        key=lambda col: col.astype(str).str.lower(),
        ascending=True,
        na_position="last",
        inplace=True,
    )

    output_path = OUTPUT_DIR / "ats_direct_jobs.csv"
    df.to_csv(
        output_path,
        quoting=csv.QUOTE_NONNUMERIC,
        escapechar="\\",
        index=False,
    )

    print(
        f"\nFinished ATS scraping. Extracted {initial_count} roles -> {deduped_count} unique roles."
    )
    print(f"Saved to: {output_path}")

    return output_path


def main():
    parser = argparse.ArgumentParser(
        description="Scrape jobs directly from ATS endpoints and hosted datasets."
    )
    parser.add_argument(
        "--smoke-test",
        action="store_true",
        help="Fast smoke test on a small subset of terms and companies.",
    )
    parser.add_argument(
        "--skip-dataset",
        action="store_true",
        help="Skip hosted dataset snapshot download and only query direct company ATS boards.",
    )
    args = parser.parse_args()

    run_ats_scrape(smoke_test=args.smoke_test, skip_dataset=args.skip_dataset)


if __name__ == "__main__":
    main()
