import csv
import os
from datetime import datetime
from pathlib import Path

import pandas as pd
from jobspy import scrape_jobs

try:
    from jobspy.role_taxonomy import HIGH_YIELD_PIVOT_TERMS, merge_terms
except ImportError:
    from role_taxonomy import HIGH_YIELD_PIVOT_TERMS, merge_terms  # type: ignore[no-redef]

# All outputs go into scraped jobs/results/YYYY_MM/YYYY_MM_DD/ next to this script
default_out = Path(__file__).parent / "scraped jobs" / "results" / datetime.now().strftime("%Y_%m") / datetime.now().strftime("%Y_%m_%d")
OUTPUT_DIR = Path(os.environ.get("JOBSPY_RESULTS_DIR", default_out))
os.makedirs(OUTPUT_DIR, exist_ok=True)

# 1. Define Locations and Search Terms
locations = [
    "Seattle, WA",
]

search_terms = [
    # Early Career, New Grad & Junior Roles First (highest yield)
    "Associate Software Engineer",
    "Junior Software Engineer",
    "Software Engineer - Early Career",
    "Software Engineer - New Grad",
    "Junior Developer",
    "Junior Full Stack Developer",
    "Junior Frontend Engineer",
    "Junior Backend Engineer",
    "Junior DevOps Engineer",
    "Associate Product Engineer",
    "Junior Security Engineer",
    "Associate Security Engineer",
    "Associate Product Manager",
    "APM",
    "Business Operations Associate",
    "Strategy & Operations Associate",
    "Associate Analyst",
    "Rotational Analyst",
    "Associate Data & Analytics",
    "Technology Advisory Associate",

    # Core Data & Analytics
    "Data Analyst",
    "Business Analyst",
    "Data Scientist",
    "Statistician",
    "Quantitative Analyst",
    "Operations Research Analyst",
    "Product Analyst",
    "Growth Analyst",

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
    "Strategy Consultant",

    # Expanded Data & Analytics Roles
    "Analytics Engineer",
    "Full Stack Analytics Engineer",
    "Data Operations Analyst",
    "Reporting Analyst",
    "Insights Analyst",
    "Decision Support Analyst",
    "Commercial Analytics Analyst",
    "Revenue Operations Analyst",
    "Customer Analytics Analyst",

    # Product & Business Operations Roles
    "BizOps Analyst",
    "Project Coordinator",
    "Operations Coordinator",
    "Solutions Analyst",
    "Data Operations Specialist",

    # Engineering & Security Roles
    "Data Engineer",
    "Backend Engineer",
    "Platform Engineer",
    "Product Engineer",
    "Security Engineer",
    "Application Security Engineer",
    "Cybersecurity Analyst",
    "Information Security Analyst",
    "SOC Analyst",
]
search_terms = merge_terms(search_terms, HIGH_YIELD_PIVOT_TERMS)

def flush_jobs_to_csv(jobs_list: list[pd.DataFrame], output_path: Path) -> int:
    """Atomically write collected job dataframes to CSV with deduplication."""
    if not jobs_list:
        return 0
    combined = pd.concat(jobs_list, ignore_index=True)
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


if __name__ == "__main__":
    output_path = OUTPUT_DIR / "seattle_jobs.csv"
    all_jobs_list = []
    had_errors = False

    print(f"Starting scrape for {len(locations)} locations × {len(search_terms)} terms...\n")

    for loc in locations:
        print(f"--- {loc} ---")
        for term in search_terms:
            print(f"  > Scraping: '{term}' in '{loc}'")
            try:
                jobs = scrape_jobs(
                    site_name=[
                        "indeed",
                        "linkedin",
                    ],
                    search_term=term,
                    location=loc,
                    country_indeed="USA",
                    results_wanted=100,
                    hours_old=48,
                    linkedin_fetch_description=False,
                )
                if jobs.attrs.get("scrape_errors"):
                    had_errors = True
                if not jobs.empty:
                    jobs["search_keyword"] = term
                    jobs["search_city"] = loc
                    all_jobs_list.append(jobs)
                    total_unique = flush_jobs_to_csv(all_jobs_list, output_path)
                    print(f"    Found {len(jobs)} jobs ({total_unique} unique saved)")
                else:
                    print("    Found 0 jobs")
            except Exception as e:
                had_errors = True
                print(f"    Error scraping {term} in {loc}: {e}")

    if all_jobs_list:
        total_unique = flush_jobs_to_csv(all_jobs_list, output_path)
        print(f"\nTotal unique jobs: {total_unique}")
        print(f"Saved to {output_path}")
    else:
        print("No jobs found.")

    if had_errors:
        print("Some queries failed; any collected partial results were retained.")
        if not all_jobs_list:
            raise SystemExit(1)
