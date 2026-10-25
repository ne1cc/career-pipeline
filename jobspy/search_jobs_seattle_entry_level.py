import csv
import os
from datetime import datetime
from pathlib import Path

import pandas as pd
from jobspy import scrape_jobs

# All outputs go into scraped jobs/results/YYYY_MM/YYYY_MM_DD/ next to this script
default_out = Path(__file__).parent / "scraped jobs" / "results" / datetime.now().strftime("%Y_%m") / datetime.now().strftime("%Y_%m_%d")
OUTPUT_DIR = Path(os.environ.get("JOBSPY_RESULTS_DIR", default_out))
os.makedirs(OUTPUT_DIR, exist_ok=True)

# 1. Define Locations and Search Terms
locations = [
    "Seattle, WA",
]

# Specifically focused on entry-level, junior, and associate roles
search_terms = [
    # Core Data & Analytics (Entry Level)
    "Entry Level Data Analyst",
    "Junior Data Analyst",
    "Associate Data Analyst",
    "Entry Level Business Analyst",
    "Junior Business Analyst",
    "Entry Level Data Scientist",
    "Junior Data Scientist",
    "Entry Level Analytics Engineer",
    "Junior Analytics Engineer",

    # Business Intelligence & Systems (Entry Level)
    "Entry Level BI Developer",
    "Junior BI Engineer",
    "Entry Level Business Intelligence Analyst",
    "Junior Computer Systems Analyst",
    "Entry Level Programmer Analyst",

    # Domain-Specific Analytics (Entry Level)
    "Entry Level Financial Analyst",
    "Junior Marketing Data Analyst",
    "Entry Level Supply Chain Analyst",
    "Junior Pricing Analyst",
    "Entry Level Risk Analyst",
    "Junior Sales Operations Analyst",

    # Consulting & General (Entry Level)
    "Associate Data & Analytics",
    "Technology Advisory Associate",
    "Entry Level Strategy Consultant",
    "Junior Business Analytics Consultant",

    # Software Engineering & Developer (Entry Level)
    "Associate Software Engineer",
    "Junior Software Engineer",
    "Entry Level Software Engineer",
    "Junior Developer",
    "Junior Full Stack Developer",
    "Entry Level Software Developer",

    # Security (Entry Level)
    "Junior Security Engineer",
    "Associate Security Engineer",
    "Entry Level Security Engineer",
    "Cybersecurity Analyst",
    "SOC Analyst",
]

all_jobs_list = []
had_errors = False

print(f"Starting scrape for {len(locations)} locations × {len(search_terms)} terms (Entry Level focus)...\n")

for loc in locations:
    print(f"--- {loc} ---")
    for term in search_terms:
        print(f"  > Scraping: '{term}' in '{loc}'")
        try:
            jobs = scrape_jobs(
                site_name=[
                    "indeed",
                    "linkedin",
                    "zip_recruiter",
                    "glassdoor",
                    "google",
                ],
                search_term=term,
                location=loc,
                google_search_term=f"{term} jobs near {loc} since yesterday",
                country_indeed="USA",
                results_wanted=9999,
                hours_old=48,
                linkedin_fetch_description=True,
            )
            if jobs.attrs.get("scrape_errors"):
                had_errors = True
            if not jobs.empty:
                jobs["search_keyword"] = term
                jobs["search_city"] = loc
                all_jobs_list.append(jobs)
                print(f"    Found {len(jobs)} jobs")
            else:
                print("    Found 0 jobs")
        except Exception as e:
            had_errors = True
            print(f"    Error scraping {term} in {loc}: {e}")

if all_jobs_list:
    combined_jobs = pd.concat(all_jobs_list, ignore_index=True)
    combined_jobs.drop_duplicates(subset=["job_url"], keep="first", inplace=True)
    if "title" in combined_jobs.columns:
        combined_jobs.sort_values(
            by="title",
            key=lambda col: col.astype(str).str.lower(),
            ascending=True,
            na_position="last",
            inplace=True,
        )
    print(f"\nTotal unique entry-level jobs: {len(combined_jobs)}")
    output_path = OUTPUT_DIR / "seattle_entry_level_jobs.csv"
    combined_jobs.to_csv(
        output_path,
        quoting=csv.QUOTE_NONNUMERIC,
        escapechar="\\",
        index=False,
    )
    print(f"Saved to {output_path}")
else:
    print("No jobs found.")

if had_errors:
    print("Some queries failed; any collected partial results were retained.")
    if not all_jobs_list:
        raise SystemExit(1)
