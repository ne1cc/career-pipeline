# <------ IT Support ------->

# import csv
# import pandas as pd
# import time
# from jobspy import scrape_jobs



# search_terms = [
#     "Entry Level Help Desk",
#     "Entry Level Technical Support",
#     "IT Support Specialist",
#     "Junior IT Administrator",
#     "Desktop Support",
#     "QA Tester",
#     "Entry Level IT"
# ]



# print(f"Starting scrape for {len(locations)} locations and {len(search_terms)} terms...\n")

# # 3. Nested Loop: Iterate through Locations first, then Terms
# for loc in locations:
#     print(f"--- Processing Location: {loc} ---")
    
#     for term in search_terms:
#         print(f"  > Scraping: '{term}' in '{loc}'")
        
#         try:
#             jobs = scrape_jobs(
#                 site_name=["indeed", "linkedin", "zip_recruiter", "glassdoor", "google"],
#                 search_term=term,
#                 google_search_term=f"{term} jobs near {loc} since yesterday",
#                 location=loc,
#                 results_wanted=results_per_term,
#                 hours_old=hours_old,
#                 country_indeed='USA',
#                 # linkedin_fetch_description=True 
#             )
            
#             # Tag the data so you know which city/term found it
#             jobs['search_city'] = loc
#             jobs['search_keyword'] = term
            
#             all_jobs_list.append(jobs)
#             print(f"    Found {len(jobs)} jobs")
            
#             # IMPORTANT: Pause for 5 seconds to avoid getting blocked
#             time.sleep(5)
            
#         except Exception as e:
#             print(f"    Error scraping {term} in {loc}: {e}")

# # 4. Combine and Clean
# if all_jobs_list:
#     combined_jobs = pd.concat(all_jobs_list, ignore_index=True)
    
#     # Deduplicate: A remote job might appear in searches for both Seattle and SF.
#     # We keep the first occurrence.
#     combined_jobs.drop_duplicates(subset=['job_url'], keep='first', inplace=True)
    
#     print(f"\nTotal unique jobs found: {len(combined_jobs)}")
    
#     # Save to CSV
#     filename = "multi_city_it_jobs.csv"
#     combined_jobs.to_csv(filename, quoting=csv.QUOTE_NONNUMERIC, escapechar="\\", index=False)
#     print(f"Saved results to {filename}")
# else:
#     print("No jobs found.")

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
    "San Francisco, CA",
    "Los Angeles, CA",
    "Portland, OR",
]

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
    "Associate Product Manager",
    "APM",
    "Business Operations Associate",
    "BizOps Analyst",
    "Project Coordinator",
    "Strategy & Operations Associate",

    # Junior Software Engineering & Developer Roles
    "Associate Software Engineer",
    "Junior Software Engineer",
    "Software Engineer - Early Career",
    "Software Engineer - New Grad",
    "Junior Developer",
    "Junior Full Stack Developer",
    "Junior Frontend Engineer",
    "Junior Backend Engineer",
    "Junior DevOps Engineer",
    "Data Engineer",
    "Backend Engineer",
    "Platform Engineer",
    "Product Engineer",
    "Associate Product Engineer",

    # Security Roles
    "Junior Security Engineer",
    "Security Engineer",
    "Application Security Engineer",
    "Associate Security Engineer",
    "Cybersecurity Analyst",
    "Information Security Analyst",
    "SOC Analyst",
]

if __name__ == "__main__":
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
        print(f"\nTotal unique jobs: {len(combined_jobs)}")
        output_path = OUTPUT_DIR / "west_coast_jobs.csv"
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
        raise SystemExit(1)
