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
import pandas as pd
from jobspy import scrape_jobs
# 1. Define Locations and Search Terms
locations = [
    "Seattle, WA", 
    "San Francisco, CA", 
    "Los Angeles, CA"
]

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

""" # 2. Settings
results_per_term = 9999  # Reduced slightly since we are searching more locations
hours_old = 240
"""
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
        "Seattle_LA_SF_remote_jobs.csv",
        quoting=csv.QUOTE_NONNUMERIC,
        escapechar="\\",
        index=False,
    )
    print("Saved to Seattle_LA_SF_remote_jobs.csv")
else:
    print("No jobs found.")
