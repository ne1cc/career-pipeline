"""
JobSpy High-Traffic Tech Portals Scraper (Built In, Himalayas, Remotive, Jobicy, RemoteOK, Arbeitnow)
Pulls data & analytics job listings from premier tech job platforms,
normalizes them to the JobSpy schema, deduplicates, and saves to CSV.
"""

import os
import sys
import csv
import time
import logging
import argparse
from datetime import datetime, timezone
from pathlib import Path
import re
import html
import requests
from bs4 import BeautifulSoup

# Standard JobSpy column order
COLUMNS = [
    "id",
    "site",
    "job_url",
    "job_url_direct",
    "title",
    "company",
    "location",
    "date_posted",
    "is_remote",
    "search_keyword",
]

# Expanded Target Roles
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
    "Data Engineer",
    # Business Intelligence
    "Business Intelligence Analyst",
    "BI Developer",
    "BI Engineer",
    "Reporting Analyst",
    # Healthcare & Clinical Analytics
    "Healthcare Analyst",
    "Healthcare Data Analyst",
    "Clinical Data Analyst",
    "Clinical Research Analyst",
    "Health Informatics Analyst",
    "Population Health Analyst",
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
    "Junior Data Analyst",
    "Early Career Analyst",
]
SEARCH_TERMS = DEFAULT_SEARCH_TERMS

# Compiled regex pattern for title matching across broad feeds
ROLE_REGEX = re.compile(
    r"\b(?:"
    r"data analyst|business analyst|data scientist|statistician|quantitative|"
    r"analytics engineer|data engineer|business intelligence|bi developer|bi engineer|"
    r"reporting analyst|healthcare analyst|clinical data|health informatics|"
    r"financial analyst|market research|marketing analytics|supply chain analyst|"
    r"pricing analyst|revenue analyst|operations analyst|product analyst|growth analyst|"
    r"risk analyst|strategy consultant"
    r")\b",
    re.IGNORECASE,
)

# Standard HTTP headers mimicking desktop browsers
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

JSON_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
}


def _get_request(url: str, headers: dict = None, params: dict = None, retries: int = 3) -> requests.Response:
    """Execute GET request with exponential backoff on failure."""
    for attempt in range(1, retries + 1):
        try:
            resp = requests.get(url, headers=headers or HEADERS, params=params, timeout=15)
            if resp.status_code in [429, 500, 502, 503, 504] and attempt < retries:
                time.sleep(2 ** attempt)
                continue
            return resp
        except requests.exceptions.RequestException:
            if attempt < retries:
                time.sleep(2 ** attempt)
                continue
    return None


def scrape_builtin_portal(term: str, is_smoke: bool = False) -> list[dict]:
    """Scrape BuiltIn Seattle & National for a specific job title."""
    jobs = []
    urls = [
        ("https://www.builtinseattle.com/jobs", "BuiltIn Seattle"),
        ("https://builtin.com/jobs", "BuiltIn"),
    ]
    if is_smoke:
        urls = urls[:1]

    for base_url, site_name in urls:
        try:
            params = {"search": term}
            resp = _get_request(base_url, headers=HEADERS, params=params)
            if not resp or resp.status_code != 200:
                continue

            soup = BeautifulSoup(resp.text, "html.parser")
            cards = soup.find_all("div", attrs={"data-id": "job-card"})
            for card in cards:
                title_el = card.find("h2") or card.find("a", attrs={"data-id": "job-card-title"})
                title = title_el.get_text(strip=True) if title_el else ""
                if not title or not ROLE_REGEX.search(title):
                    continue

                link_el = card.find("a", href=lambda h: h and "/job/" in h)
                if not link_el:
                    continue
                href = link_el["href"]
                job_url = href if href.startswith("http") else f"https://www.builtin.com{href}"

                company_el = card.find("a", href=lambda h: h and "/company/" in h)
                company = company_el.get_text(strip=True) if company_el else ""
                if not company:
                    img_el = card.find("img", attrs={"data-id": "company-img"})
                    if img_el and img_el.get("alt"):
                        company = img_el["alt"].replace(" Logo", "").strip()

                card_text = card.get_text(" ", strip=True)
                is_remote = "remote" in card_text.lower()
                loc = "Seattle, WA" if "seattle" in base_url else "Remote"

                date_posted = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                job_id = card.get("id", "").replace("job-card-", "") or str(abs(hash(job_url)))

                jobs.append({
                    "id": f"builtin-{job_id}",
                    "site": site_name,
                    "job_url": job_url,
                    "job_url_direct": job_url,
                    "title": title,
                    "company": company or "BuiltIn Employer",
                    "location": loc,
                    "date_posted": date_posted,
                    "is_remote": is_remote,
                    "search_keyword": term,
                })
        except Exception as e:
            logging.debug(f"Error scraping BuiltIn for '{term}': {e}")
    return jobs


def scrape_himalayas_portal(term: str, is_smoke: bool = False) -> list[dict]:
    """Scrape Himalayas Public API for remote tech listings."""
    jobs = []
    limit = 50 if is_smoke else 100
    url = f"https://himalayas.app/jobs/api?limit={limit}"
    try:
        resp = _get_request(url, headers=JSON_HEADERS)
        if not resp or resp.status_code != 200:
            return jobs

        data = resp.json()
        raw_jobs = data.get("jobs", [])
        for j in raw_jobs:
            title = j.get("title", "")
            if not title or not ROLE_REGEX.search(title):
                continue

            app_link = j.get("applicationLink") or j.get("guid") or ""
            if not app_link:
                continue

            pub_date_ts = j.get("pubDate")
            date_posted = ""
            if pub_date_ts:
                try:
                    date_posted = datetime.fromtimestamp(pub_date_ts, tz=timezone.utc).strftime("%Y-%m-%d")
                except Exception:
                    date_posted = datetime.now(timezone.utc).strftime("%Y-%m-%d")

            job_id = str(j.get("guid", abs(hash(app_link))))

            jobs.append({
                "id": f"himalayas-{job_id}",
                "site": "Himalayas",
                "job_url": app_link,
                "job_url_direct": app_link,
                "title": title,
                "company": j.get("companyName", "Himalayas Employer"),
                "location": "Remote, US",
                "date_posted": date_posted,
                "is_remote": True,
                "search_keyword": term,
            })
    except Exception as e:
        logging.debug(f"Error scraping Himalayas: {e}")
    return jobs


def scrape_remotive_portal(term: str, is_smoke: bool = False) -> list[dict]:
    """Scrape Remotive Public API for data and analytics listings."""
    jobs = []
    url = "https://remotive.com/api/remote-jobs?category=data"
    try:
        resp = _get_request(url, headers=JSON_HEADERS)
        if not resp or resp.status_code != 200:
            return jobs

        data = resp.json()
        raw_jobs = data.get("jobs", [])
        for j in raw_jobs:
            title = j.get("title", "")
            if not title or not ROLE_REGEX.search(title):
                continue

            app_link = j.get("url", "")
            if not app_link:
                continue

            pub_date = j.get("publication_date", "")[:10]
            job_id = str(j.get("id", abs(hash(app_link))))

            jobs.append({
                "id": f"remotive-{job_id}",
                "site": "Remotive",
                "job_url": app_link,
                "job_url_direct": app_link,
                "title": title,
                "company": j.get("company_name", "Remotive Employer"),
                "location": j.get("candidate_required_location") or "Remote",
                "date_posted": pub_date or datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                "is_remote": True,
                "search_keyword": term,
            })
    except Exception as e:
        logging.debug(f"Error scraping Remotive: {e}")
    return jobs


def scrape_jobicy_portal(term: str, is_smoke: bool = False) -> list[dict]:
    """Scrape Jobicy Public API for US remote tech roles."""
    jobs = []
    count = 20 if is_smoke else 50
    url = f"https://jobicy.com/api/v2/remote-jobs?count={count}&geo=usa"
    try:
        resp = _get_request(url, headers=JSON_HEADERS)
        if not resp or resp.status_code != 200:
            return jobs

        data = resp.json()
        raw_jobs = data.get("jobs", [])
        for j in raw_jobs:
            title = j.get("jobTitle", "")
            if not title or not ROLE_REGEX.search(title):
                continue

            app_link = j.get("url", "")
            if not app_link:
                continue

            pub_date = j.get("pubDate", "")[:10]
            job_id = str(j.get("id", abs(hash(app_link))))

            jobs.append({
                "id": f"jobicy-{job_id}",
                "site": "Jobicy",
                "job_url": app_link,
                "job_url_direct": app_link,
                "title": title,
                "company": j.get("companyName", "Jobicy Employer"),
                "location": j.get("jobGeo") or "Remote, US",
                "date_posted": pub_date or datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                "is_remote": True,
                "search_keyword": term,
            })
    except Exception as e:
        logging.debug(f"Error scraping Jobicy: {e}")
    return jobs


def scrape_remoteok_portal(term: str, is_smoke: bool = False) -> list[dict]:
    """Scrape RemoteOK Public API for data roles."""
    jobs = []
    url = "https://remoteok.com/api?tag=data"
    try:
        resp = _get_request(url, headers=HEADERS)
        if not resp or resp.status_code != 200:
            return jobs

        data = resp.json()
        for j in data:
            if not isinstance(j, dict) or "position" not in j:
                continue

            title = html.unescape(j.get("position", ""))
            if not title or not ROLE_REGEX.search(title):
                continue

            url_slug = j.get("url", "")
            job_url = f"https://remoteok.com{url_slug}" if url_slug.startswith("/") else url_slug
            if not job_url:
                continue

            date_str = j.get("date", "")[:10]
            job_id = str(j.get("id", abs(hash(job_url))))

            jobs.append({
                "id": f"remoteok-{job_id}",
                "site": "RemoteOK",
                "job_url": job_url,
                "job_url_direct": job_url,
                "title": title,
                "company": html.unescape(j.get("company", "RemoteOK Employer")),
                "location": j.get("location") or "Remote",
                "date_posted": date_str or datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                "is_remote": True,
                "search_keyword": term,
            })
    except Exception as e:
        logging.debug(f"Error scraping RemoteOK: {e}")
    return jobs


def scrape_arbeitnow_portal(term: str, is_smoke: bool = False) -> list[dict]:
    """Scrape Arbeitnow Public API for tech and data jobs."""
    jobs = []
    url = "https://www.arbeitnow.com/api/job-board-api"
    try:
        resp = _get_request(url, headers=JSON_HEADERS)
        if not resp or resp.status_code != 200:
            return jobs

        data = resp.json()
        raw_jobs = data.get("data", [])
        for j in raw_jobs:
            title = j.get("title", "")
            if not title or not ROLE_REGEX.search(title):
                continue

            app_link = j.get("url", "")
            if not app_link:
                continue

            job_id = str(j.get("slug", abs(hash(app_link))))
            is_remote = bool(j.get("remote"))

            jobs.append({
                "id": f"arbeitnow-{job_id}",
                "site": "Arbeitnow",
                "job_url": app_link,
                "job_url_direct": app_link,
                "title": title,
                "company": j.get("company_name", "Arbeitnow Employer"),
                "location": j.get("location") or ("Remote" if is_remote else "USA"),
                "date_posted": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                "is_remote": is_remote,
                "search_keyword": term,
            })
    except Exception as e:
        logging.debug(f"Error scraping Arbeitnow: {e}")
    return jobs


def main():
    parser = argparse.ArgumentParser(description="JobSpy High-Traffic Tech Portals Scraper")
    parser.add_argument("--smoke-test", action="store_true", help="Quick run on 1 term across portals")
    args = parser.parse_args()

    # Paths
    base_dir = Path(__file__).resolve().parent
    now = datetime.now()
    month_str = now.strftime("%Y_%m")
    log_dir = base_dir / "scraped jobs" / "logs" / month_str
    results_dir = base_dir / "scraped jobs" / "results" / month_str
    log_dir.mkdir(parents=True, exist_ok=True)
    results_dir.mkdir(parents=True, exist_ok=True)

    log_file = log_dir / "search_jobs_portals.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.FileHandler(log_file, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )

    terms = ["Data Analyst"] if args.smoke_test else SEARCH_TERMS
    logging.info(f"Starting Tech Portals Scraper with {len(terms)} search terms (smoke_test={args.smoke_test})")

    all_jobs = []

    # 1. Broad feed portals (query once and match against regex)
    logging.info("--- Querying Himalayas API ---")
    himalayas_jobs = scrape_himalayas_portal("Data & Analytics", is_smoke=args.smoke_test)
    logging.info(f"Himalayas returned {len(himalayas_jobs)} matched jobs")
    all_jobs.extend(himalayas_jobs)

    logging.info("--- Querying Remotive API ---")
    remotive_jobs = scrape_remotive_portal("Data & Analytics", is_smoke=args.smoke_test)
    logging.info(f"Remotive returned {len(remotive_jobs)} matched jobs")
    all_jobs.extend(remotive_jobs)

    logging.info("--- Querying RemoteOK API ---")
    remoteok_jobs = scrape_remoteok_portal("Data & Analytics", is_smoke=args.smoke_test)
    logging.info(f"RemoteOK returned {len(remoteok_jobs)} matched jobs")
    all_jobs.extend(remoteok_jobs)

    logging.info("--- Querying Jobicy API ---")
    jobicy_jobs = scrape_jobicy_portal("Data & Analytics", is_smoke=args.smoke_test)
    logging.info(f"Jobicy returned {len(jobicy_jobs)} matched jobs")
    all_jobs.extend(jobicy_jobs)

    logging.info("--- Querying Arbeitnow API ---")
    arbeitnow_jobs = scrape_arbeitnow_portal("Data & Analytics", is_smoke=args.smoke_test)
    logging.info(f"Arbeitnow returned {len(arbeitnow_jobs)} matched jobs")
    all_jobs.extend(arbeitnow_jobs)

    # 2. Term-based portals (BuiltIn)
    logging.info(f"--- Querying BuiltIn Portals ({len(terms)} terms) ---")
    for i, term in enumerate(terms, 1):
        logging.info(f"[{i}/{len(terms)}] BuiltIn searching: {term}")
        bi_jobs = scrape_builtin_portal(term, is_smoke=args.smoke_test)
        logging.info(f"  Found {len(bi_jobs)} jobs for '{term}'")
        all_jobs.extend(bi_jobs)

    # Deduplicate by job_url
    deduped = {}
    for job in all_jobs:
        url = job.get("job_url", "").strip()
        if url and url not in deduped:
            deduped[url] = job

    final_list = list(deduped.values())
    final_list.sort(key=lambda x: x.get("title", "").lower())

    out_csv = results_dir / "tech_portals_jobs.csv"
    with open(out_csv, mode="w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=COLUMNS,
            quoting=csv.QUOTE_NONNUMERIC,
            escapechar="\\",
        )
        writer.writeheader()
        for row in final_list:
            filtered_row = {k: row.get(k, "") for k in COLUMNS}
            writer.writerow(filtered_row)

    logging.info(f"Saved {len(final_list)} deduplicated tech portal jobs to {out_csv}")
    print(f"DONE: {len(final_list)} jobs written to {out_csv}")


if __name__ == "__main__":
    main()
