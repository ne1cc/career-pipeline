import csv
import os
import re
from datetime import datetime, date, timedelta
from pathlib import Path

import pandas as pd
from jobspy import scrape_jobs

# All outputs go into scraped jobs/results/YYYY_MM/YYYY_MM_DD/ next to this script
default_out = Path(__file__).parent / "scraped jobs" / "results" / datetime.now().strftime("%Y_%m") / datetime.now().strftime("%Y_%m_%d")
OUTPUT_DIR = Path(os.environ.get("JOBSPY_RESULTS_DIR", default_out))
os.makedirs(OUTPUT_DIR, exist_ok=True)

# 1. Define Location and Search Terms
locations = [
    "Seattle, WA",
]

# Broad set of search terms that align with roles business analytics/MSBA graduates typically target.
# Filtering will clean out senior positions and non-relevant degree requirements.
search_terms = [
    "Business Analyst",
    "Data Analyst",
    "Business Intelligence Analyst",
    "Analytics Engineer",
    "Product Analyst",
    "Marketing Analyst",
    "Data Scientist",
    "Financial Analyst",
    "Operations Analyst",
    "Supply Chain Analyst",
    "Pricing Analyst",
    "Revenue Analyst",
    "Sales Operations Analyst",
    "Risk Analyst",
    "Business Analytics Consultant",
    "Systems Analyst",
]

def is_entry_to_mid_level(title: str, description: str) -> bool:
    """
    Filters out senior/lead/management roles to keep only entry to mid-level roles.
    Checks the title for senior terms and checks description for high years of experience.
    """
    title_lower = title.lower()
    
    # Senior / leadership keywords to exclude from title
    exclude_terms = [
        "senior", "sr.", "sr ", "lead", "principal", "director", 
        "manager", "vp", "vice president", "head of", "chief", 
        "architect", "staff", "expert", "distinguished", "fellow",
        "iii", "iv", "v"  # levels III, IV, V are typically senior
    ]
    
    for term in exclude_terms:
        if term == "lead" and not re.search(r'\blead\b', title_lower):
            continue
        if term == "staff" and not re.search(r'\bstaff\b', title_lower):
            continue
        if term in title_lower:
            return False
            
    # Check description for high experience requirements (e.g. 6+, 7+, 8+, 10+ years of experience)
    # Entry to mid-level usually ranges from 0 to 5 years.
    high_exp_patterns = [
        r'\b[6-9]\+\s*(?:years|yrs)\b',
        r'\b1[0-9]\+\s*(?:years|yrs)\b',
        r'\b[6-9]\s*to\s*[0-9]+\s*(?:years|yrs)\b',
        r'\b1[0-9]\s*to\s*[0-9]+\s*(?:years|yrs)\b',
        r'\b[6-9]\s*or\s*more\s*(?:years|yrs)\b',
        r'\b1[0-9]\s*or\s*more\s*(?:years|yrs)\b'
    ]
    
    if isinstance(description, str) and description.strip():
        desc_lower = description.lower()
        for pattern in high_exp_patterns:
            if re.search(pattern, desc_lower):
                return False
                
    return True


def recruits_msba_graduates(title: str, description: str) -> bool:
    """
    Checks if the job recruits MSBA / Business Analytics graduates.
    We look for explicit mentions of Business Analytics/MSBA degrees, or
    mentions of a quantitative/analytics Master's degree.
    """
    title_lower = title.lower()
    desc_lower = description.lower() if isinstance(description, str) else ""
    
    # 1. Explicit mentions of Business Analytics or MSBA in title or description
    if "business analytics" in desc_lower or "business analytics" in title_lower:
        return True
    if re.search(r'\bmsba\b', desc_lower) or re.search(r'\bmsba\b', title_lower):
        return True
    if "business intelligence & analytics" in desc_lower or "business intelligence and analytics" in desc_lower:
        return True
        
    # 2. Master's in analytics / MS in Analytics
    analytics_degree_patterns = [
        r"master's\s+(?:degree\s+)?in\s+analytics",
        r"masters\s+(?:degree\s+)?in\s+analytics",
        r"ms\s+in\s+analytics",
        r"m\.s\.\s+in\s+analytics",
        r"master\s+of\s+science\s+in\s+analytics",
        r"master\s+of\s+science\s+in\s+business"
    ]
    for pattern in analytics_degree_patterns:
        if re.search(pattern, desc_lower):
            return True
            
    # 3. Graduate/Master's degree requirement in a quantitative field (Statistics, DS, Economics, etc.)
    # MSBA graduates are highly competitive and actively recruited for these roles.
    has_masters_req = any(term in desc_lower for term in ["master's", "masters degree", "ms degree", "m.s. degree", "graduate degree", "advanced degree"])
    if has_masters_req:
        quantitative_fields = ["quantitative", "statistics", "data science", "operations research", "applied mathematics", "information systems"]
        if any(field in desc_lower for field in quantitative_fields):
            return True

    # 4. Fallback when description is omitted/empty: validate against analytics/MSBA title patterns
    if not desc_lower.strip():
        msba_title_pattern = r"\b(?:analyst|analytics|data\s+scientist|data|business\s+intelligence|bi\b|statistician|operations\s+research|consultant)\b"
        return bool(re.search(msba_title_pattern, title_lower))

    return False


if __name__ == "__main__":
    all_jobs_list = []

    print(f"Starting Seattle MSBA Job Scrape for last 48 hours...")
    print(f"Locations: {locations}")
    print(f"Search Terms: {len(search_terms)} terms\n")

    for loc in locations:
        print(f"--- {loc} ---")
        for term in search_terms:
            print(f"  > Scraping: '{term}' in '{loc}'")
            try:
                # We fetch jobs posted within the last 48 hours
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
                    country_indeed="USA",
                    results_wanted=9999,
                    hours_old=48,
                    linkedin_fetch_description=False,
                )
                
                if not jobs.empty:
                    jobs["search_keyword"] = term
                    jobs["search_city"] = loc
                    all_jobs_list.append(jobs)
                    print(f"    Found {len(jobs)} jobs (raw)")
                else:
                    print("    Found 0 jobs")
            except Exception as e:
                print(f"    Error scraping {term} in {loc}: {e}")

    if all_jobs_list:
        combined_jobs = pd.concat(all_jobs_list, ignore_index=True)
        combined_jobs.drop_duplicates(subset=["job_url"], keep="first", inplace=True)
        
        raw_count = len(combined_jobs)
        print(f"\nScraped {raw_count} unique raw jobs. Applying filters...")
        
        # Apply entry-to-mid level and MSBA recruitment filters
        filtered_rows = []
        today = date.today()
        two_days_ago = today - timedelta(days=2)
        
        for idx, row in combined_jobs.iterrows():
            title = str(row.get("title")) if pd.notna(row.get("title")) else ""
            description = str(row.get("description")) if pd.notna(row.get("description")) else ""
            date_posted = row.get("date_posted")
            
            # 1. Double check date if present (must be within last 48 hours / 2 days)
            # Convert date_posted string/date object if necessary
            is_recent = True
            if pd.notna(date_posted):
                if isinstance(date_posted, str):
                    try:
                        job_date = datetime.strptime(date_posted, "%Y-%m-%d").date()
                    except ValueError:
                        job_date = None
                elif isinstance(date_posted, (date, datetime)):
                    job_date = date_posted if isinstance(date_posted, date) else date_posted.date()
                else:
                    job_date = None
                    
                if job_date and job_date < two_days_ago:
                    is_recent = False
                    
            if not is_recent:
                continue
                
            # 2. Check level (Entry to Mid)
            if not is_entry_to_mid_level(title, description):
                continue
                
            # 3. Check if role recruits MSBA / Business Analytics grads
            if not recruits_msba_graduates(title, description):
                continue
                
            filtered_rows.append(row)
            
        if filtered_rows:
            final_jobs = pd.DataFrame(filtered_rows)
            if "title" in final_jobs.columns:
                final_jobs.sort_values(
                    by="title",
                    key=lambda col: col.astype(str).str.lower(),
                    ascending=True,
                    na_position="last",
                    inplace=True,
                )
            print(f"Filters complete. Kept {len(final_jobs)} of {raw_count} jobs.")
            
            output_path = OUTPUT_DIR / "seattle_msba_jobs.csv"
            final_jobs.to_csv(
                output_path,
                quoting=csv.QUOTE_NONNUMERIC,
                escapechar="\\",
                index=False,
            )
            print(f"Saved results to {output_path}")
        else:
            print("No jobs matched the level and MSBA graduation recruitment criteria.")
    else:
        print("No jobs found in the initial scrape.")
