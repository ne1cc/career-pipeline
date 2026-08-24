import csv
import os
from datetime import datetime
from pathlib import Path

import pandas as pd
from jobspy import scrape_jobs

# All outputs go into scraped jobs/results/YYYY_MM/ next to this script
OUTPUT_DIR = Path(__file__).parent / "scraped jobs" / "results" / datetime.now().strftime("%Y_%m")
os.makedirs(OUTPUT_DIR, exist_ok=True)

search_terms = [
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
]

all_jobs_list = []

print(f"Starting NATIONWIDE REMOTE scrape for {len(search_terms)} terms...\n")

for term in search_terms:
    print(f" > Scraping: '{term}'")
    try:
        jobs = scrape_jobs(
            site_name=[
                "indeed",
                "linkedin",
                "zip_recruiter",
                "glassdoor",
                "google",
            ],  # all supported major US boards
            search_term=term,
            location="United States",
            google_search_term=f"{term} jobs united states since yesterday",
            country_indeed="USA",
            results_wanted=9999,
            is_remote=True,  # fixes Glassdoor location parsing for nationwide searches
            hours_old=256,
        )

        if not jobs.empty:
            jobs["search_keyword"] = term
            all_jobs_list.append(jobs)
            print(f"    Found {len(jobs)} jobs")
        else:
            print("    Found 0 jobs")

    except Exception as e:
        print(f"    Error scraping {term}: {e}")

if all_jobs_list:
    combined_jobs = pd.concat(all_jobs_list, ignore_index=True)
    combined_jobs.drop_duplicates(subset=["job_url"], keep="first", inplace=True)
    print(f"\nTotal unique jobs: {len(combined_jobs)}")
    output_path = OUTPUT_DIR / "all_city_remote_jobs.csv"
    combined_jobs.to_csv(
        output_path,
        quoting=csv.QUOTE_NONNUMERIC,
        escapechar="\\",
        index=False,
    )
    print(f"Saved to {output_path}")
else:
    print("No jobs found.")