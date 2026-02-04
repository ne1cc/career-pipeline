import csv
import pandas as pd
from jobspy import scrape_jobs

search_terms = [
    "Operations Research Analyst",
    "Market Research Analyst",
    "Business Analytics Consultant",
    "Financial Analyst",
    "Programmer Analyst",
    "Computer Systems Analyst",
    "Business Analyst",
    "Statistician",
    "Data Scientist",
    "Data Architect",
    "Quantitative Analyst",
    "Entry Level Help Desk",
    "IT Support Specialist",
    "Junior IT Administrator",
    "QA Tester",
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
            hours_old=256,
        )

        jobs["search_keyword"] = term
        all_jobs_list.append(jobs)
        print(f"    Found {len(jobs)} jobs")

    except Exception as e:
        print(f"    Error scraping {term}: {e}")

if all_jobs_list:
    combined_jobs = pd.concat(all_jobs_list, ignore_index=True)
    combined_jobs.drop_duplicates(subset=["job_url"], keep="first", inplace=True)
    print(f"\nTotal unique jobs: {len(combined_jobs)}")
    combined_jobs.to_csv(
        "all_city_remote_jobs.csv",
        quoting=csv.QUOTE_NONNUMERIC,
        escapechar="\\",
        index=False,
    )
    print("Saved to all_city_remote_jobs.csv")
else:
    print("No jobs found.")