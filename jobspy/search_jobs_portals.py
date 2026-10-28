"""
JobSpy High-Traffic Tech Portals Scraper (Built In, Himalayas, Remotive, Jobicy, RemoteOK, Arbeitnow)
Pulls data & analytics job listings from premier tech job platforms,
normalizes them to the JobSpy schema, deduplicates, and saves to CSV.
"""

import os
import sys
import csv
import time
import json
import logging
import argparse
import email.utils
from datetime import datetime, timezone, timedelta
from pathlib import Path
import re
import html
import xml.etree.ElementTree as ET
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
    "posted_at",
    "first_seen_at",
    "description",
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
    "Junior Backend Engineer",
    "Backend Engineer",
    "Platform Engineer",
    "Product Engineer",
    "Associate Product Engineer",
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
    "BizOps Analyst",
    "RevOps Analyst",
    "Revenue Operations Analyst",
    "Sales Operations Analyst",
    "GTM Operations Analyst",
    "Solutions Analyst",
    "Commercial Analytics Associate",
    # Early Career & Junior Roles
    "New Grad Software Engineer",
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

# Compiled regex pattern for title matching across broad feeds
ROLE_REGEX = re.compile(
    r"\b(?:"
    r"data analysts?|business analysts?|data scientists?|statisticians?|quantitative analysts?|"
    r"operations research analysts?|product analysts?|growth analysts?|analytics engineers?|"
    r"full stack analytics engineers?|data engineers?|business intelligence analysts?|"
    r"bi developers?|bi engineers?|reporting analysts?|healthcare analysts?|clinical data analysts?|"
    r"clinical research analysts?|health informatics analysts?|financial analysts?|"
    r"market research analysts?|marketing data analysts?|marketing analytics specialists?|"
    r"supply chain analysts?|pricing analysts?|revenue analysts?|sales operations analysts?|"
    r"risk analysts?|business analytics consultants?|computer systems analysts?|programmer analysts?|"
    r"associate data & analytics|technology advisory associates?|strategy consultants?|"
    r"associate product managers?|apm|business operations associates?|bizops|"
    r"revops analysts?|revenue operations|sales operations|gtm operations|solutions analysts?|"
    r"commercial analytics|insights analysts?|"
    r"software engineers?|software developers?|swe|junior developers?|"
    r"backend engineers?|back-end engineers?|backend developers?|back-end developers?|"
    r"platform engineers?|product engineers?|frontend engineers?|front-end engineers?|"
    r"cloud engineers?|devops engineers?|site reliability engineers?|sre|"
    r"cybersecurity analysts?|information security analysts?|infosec|"
    r"security engineers?|security analysts?|soc analysts?"
    r")\b",
    re.IGNORECASE,
)

TARGET_STATE_PATTERN = re.compile(r"\b(?:california|ca|oregon|or|washington|wa)\b", re.I)
TARGET_LOCATION_PATTERN = re.compile(
    r"\b(?:seattle|san francisco|los angeles|portland|california|oregon|washington|ca|or|wa)\b", re.I
)
US_REMOTE_PATTERN = re.compile(
    r"\b(?:us|usa|u\.s\.|united states|us only|north america|worldwide|anywhere in the world)\b", re.I
)


def parse_posted_at(value) -> datetime | None:
    if value is None or value == "":
        return None
    try:
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(value, tz=timezone.utc)
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except (ValueError, OSError, OverflowError):
        try:
            parsed = email.utils.parsedate_to_datetime(str(value))
            return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)
        except (TypeError, ValueError, OverflowError):
            return None


def is_target_location(location: str, is_remote: bool = False) -> bool:
    text = (location or "").strip()
    if TARGET_LOCATION_PATTERN.search(text):
        return True
    return bool(is_remote and US_REMOTE_PATTERN.search(text))


def normalize_portal_job(source, job_id, title, company, url, location, posted_at, term, description=""):
    posted = parse_posted_at(posted_at)
    if not title or not ROLE_REGEX.search(title) or not url or not posted:
        return None
    location = str(location or "")
    is_remote = "remote" in location.lower() or source == "We Work Remotely"
    if not is_target_location(location, is_remote):
        return None
    posted_text = str(posted_at or "").strip()
    has_exact_time = isinstance(posted_at, (int, float)) or bool(re.search(r"[T ]\d{2}:\d{2}", posted_text))
    return {
        "id": f"{source.lower().replace(' ', '-')}-{job_id or url}",
        "site": source,
        "job_url": url,
        "job_url_direct": url,
        "title": html.unescape(str(title)).strip(),
        "company": html.unescape(str(company or "Unknown employer")).strip(),
        "location": location,
        "date_posted": posted.strftime("%Y-%m-%d"),
        "posted_at": posted.isoformat() if has_exact_time else "",
        "description": html.unescape(str(description or "")).strip(),
        "is_remote": is_remote,
        "search_keyword": term,
    }


def parse_dice_jobs(page_text: str, term: str = "Dice") -> list[dict]:
    jobs = []
    for match in re.finditer(r"self\.__next_f\.push\((.+?)\);?\s*</script>", page_text, re.S):
        try:
            frame = json.loads(match.group(1))
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(frame, list) or len(frame) < 2 or not isinstance(frame[1], str):
            continue
        payload = frame[1]
        marker = '"jobList":{"data":'
        start = payload.find(marker)
        if start < 0:
            continue
        start += len(marker)
        try:
            data, _ = json.JSONDecoder().raw_decode(payload[start:])
        except json.JSONDecodeError:
            continue
        for job in data:
            loc = job.get("jobLocation") or ""
            if isinstance(loc, dict):
                loc = ", ".join(str(loc.get(key)) for key in ("city", "adminDistrict", "state", "country") if loc.get(key))
            elif isinstance(loc, list):
                loc = ", ".join(str(value) for value in loc)
            workplace = " ".join(job.get("workplaceTypes") or [])
            summary = job.get("summary", "")
            if job.get("isRemote") or "remote" in workplace.lower():
                loc = f"{loc} Remote US {summary[:300]}"
            normalized = normalize_portal_job(
                "Dice", job.get("guid") or job.get("id"), job.get("title"),
                job.get("companyName"), job.get("detailsPageUrl"), loc,
                job.get("postedDate"), term,
                description=summary,
            )
            if normalized:
                jobs.append(normalized)
        break
    return jobs


def parse_weworkremotely_feed(xml_text: str, term: str = "We Work Remotely") -> list[dict]:
    jobs = []
    root = ET.fromstring(xml_text)
    for item in root.findall(".//item"):
        jobs.append(normalize_portal_job(
            "We Work Remotely", item.findtext("guid"), item.findtext("title"),
            item.findtext("company"), item.findtext("link"),
            item.findtext("region") or item.findtext("location") or "",
            item.findtext("pubDate"), term,
            description=item.findtext("description") or "",
        ))
    return [job for job in jobs if job]


def fetch_python_job_detail(url: str) -> dict:
    response = _get_request(url, headers=HEADERS, retries=2)
    if not response or response.status_code != 200:
        return {}
    soup = BeautifulSoup(response.text, "html.parser")
    time_element = soup.find("time", attrs={"datetime": True})
    description = soup.find("div", class_="job-content") or soup.find("main") or soup
    return {
        "posted_at": time_element.get("datetime") if time_element else None,
        "description": description.get_text(" ", strip=True),
    }


def parse_hn_job(item: dict, term: str = "Hacker News") -> dict | None:
    if not isinstance(item, dict) or item.get("deleted") or item.get("dead"):
        return None
    title = html.unescape(item.get("title", ""))
    body = BeautifulSoup(item.get("text", ""), "html.parser").get_text(" ", strip=True)
    location_match = TARGET_LOCATION_PATTERN.search(f"{title} {body}")
    location = location_match.group(0) if location_match else ""
    if re.search(r"\bremote\b", f"{title} {body}", re.I) and US_REMOTE_PATTERN.search(f"{title} {body}"):
        location = "Remote, United States"
    return normalize_portal_job(
        "Hacker News", item.get("id"), title, title.split(" is hiring", 1)[0],
        item.get("url") or f"https://news.ycombinator.com/item?id={item.get('id')}",
        location, item.get("time"), term,
        description=body,
    )


def parse_python_jobs_feed(xml_text: str, term: str = "Python.org", detail_loader=None) -> list[dict]:
    jobs = []
    root = ET.fromstring(xml_text)
    for item in root.findall(".//item"):
        description = BeautifulSoup(item.findtext("description") or "", "html.parser").get_text(" ", strip=True)
        url = item.findtext("link")
        posted_at = item.findtext("pubDate")
        if not posted_at and detail_loader and url:
            try:
                detail = detail_loader(url)
                posted_at = detail.get("posted_at")
                description = f"{description} {detail.get('description', '')}"
            except Exception as exc:
                logging.debug("Python.org detail lookup failed for %s: %s", url, exc)
        jobs.append(normalize_portal_job(
            "Python.org", item.findtext("guid"), item.findtext("title"),
            "Python Job Board employer", url, description,
            posted_at, term,
            description=description,
        ))
    return [job for job in jobs if job]


def scrape_fast_sources(term: str = "Data & Software", is_smoke: bool = False, hours_old: int = 336) -> list[dict]:
    jobs = []
    dice_locations = ["Seattle, WA", "San Francisco, CA", "Los Angeles, CA", "Portland, OR", "Remote, US"]
    dice_terms = [
        "Data Analyst", "Data Engineer", "Analytics Engineer",
        "Software Engineer", "Security Engineer", "Platform Engineer",
    ]
    if is_smoke:
        dice_locations = dice_locations[:1]
        dice_terms = dice_terms[:1]
    posted_filter = "ONE" if hours_old <= 24 else "THREE" if hours_old <= 72 else "SEVEN" if hours_old <= 168 else "THIRTY"
    for location in dice_locations:
        for dice_term in dice_terms:
            response = _get_request(
                "https://www.dice.com/jobs",
                headers=HEADERS,
                params={"q": dice_term, "location": location, "countryCode": "US", "page": 1, "pageSize": 100, "sortBy": "DATE", "filters.postedDate": posted_filter},
            )
            if response and response.status_code == 200:
                jobs.extend(parse_dice_jobs(response.text, term))

    feeds = [
        ("https://weworkremotely.com/remote-jobs.rss", parse_weworkremotely_feed),
        ("https://www.python.org/jobs/feed/rss/", parse_python_jobs_feed),
    ]
    for url, parser in feeds:
        response = _get_request(url, headers=HEADERS)
        if response and response.status_code == 200:
            if parser is parse_python_jobs_feed:
                jobs.extend(parser(response.text, term, detail_loader=fetch_python_job_detail))
            else:
                jobs.extend(parser(response.text, term))

    response = _get_request("https://hacker-news.firebaseio.com/v0/jobstories.json", headers=JSON_HEADERS)
    if response and response.status_code == 200:
        for job_id in response.json()[:20]:
            item_response = _get_request(
                f"https://hacker-news.firebaseio.com/v0/item/{job_id}.json", headers=JSON_HEADERS
            )
            if item_response and item_response.status_code == 200:
                job = parse_hn_job(item_response.json(), term)
                if job:
                    jobs.append(job)
    return jobs

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

            # Extract job description summaries from JSON-LD schema if present
            ld_descriptions: dict[str, str] = {}
            for s in soup.find_all("script", type="application/ld+json"):
                if not s.string:
                    continue
                try:
                    data = json.loads(s.string)
                    items = data.get("@graph", []) if isinstance(data, dict) else (data if isinstance(data, list) else [])
                    for item in items:
                        if isinstance(item, dict) and item.get("@type") == "ItemList":
                            for elem in item.get("itemListElement", []):
                                if isinstance(elem, dict) and elem.get("url") and elem.get("description"):
                                    elem_url = elem["url"].rstrip("/")
                                    ld_descriptions[elem_url] = elem["description"]
                except Exception:
                    pass

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
                loc = "Seattle, WA" if "seattle" in base_url else ("Remote" if is_remote else "")

                # Match description from JSON-LD or fallback to card body text
                job_clean_url = job_url.rstrip("/")
                description = ld_descriptions.get(job_clean_url, "")
                if not description:
                    # Check partial URL match in ld_descriptions
                    for u, desc in ld_descriptions.items():
                        if u.endswith(href) or href.endswith(u.replace("https://www.builtin.com", "").replace("https://www.builtinseattle.com", "")):
                            description = desc
                            break

                date_posted = ""
                job_id = card.get("id", "").replace("job-card-", "") or job_url

                jobs.append({
                    "id": f"builtin-{job_id}",
                    "site": site_name,
                    "job_url": job_url,
                    "job_url_direct": job_url,
                    "title": title,
                    "company": company or "BuiltIn Employer",
                    "location": loc,
                    "date_posted": date_posted,
                    "posted_at": "",
                    "description": description,
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

            app_link = j.get("applicationLink") or ""
            if not app_link:
                continue

            restrictions = j.get("locationRestrictions") or []
            timezones = j.get("timezoneRestrictions") or []
            us_allowed = not restrictions or any("united states" in str(value).lower() or str(value).lower() == "us" for value in restrictions)
            west_allowed = not timezones or any(offset in timezones for offset in (-8, -7, -6, -5))
            if not us_allowed or not west_allowed:
                continue
            location = "Remote, Worldwide" if not restrictions else "Remote, US"
            posted_at = parse_posted_at(j.get("pubDate"))
            date_posted = posted_at.strftime("%Y-%m-%d") if posted_at else ""

            job_id = str(j.get("guid") or app_link)

            jobs.append({
                "id": f"himalayas-{job_id}",
                "site": "Himalayas",
                "job_url": app_link,
                "job_url_direct": app_link,
                "title": title,
                "company": j.get("companyName", "Himalayas Employer"),
                "location": location,
                "date_posted": date_posted,
                "posted_at": posted_at.isoformat() if posted_at else "",
                "is_remote": True,
                "search_keyword": term,
            })
    except Exception as e:
        logging.debug(f"Error scraping Himalayas: {e}")
    return jobs


def scrape_yc_portal(is_smoke: bool = False) -> list[dict]:
    """Scrape Y Combinator / Work at a Startup listings."""
    jobs = []
    headers = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    }
    url = "https://www.workatastartup.com/jobs"
    try:
        resp = _get_request(url, headers=headers)
        if not resp or resp.status_code != 200:
            return jobs

        soup = BeautifulSoup(resp.text, "html.parser")
        div = soup.find(attrs={"data-page": True})
        if not div:
            return jobs

        data = json.loads(html.unescape(div["data-page"]))
        raw_jobs = data.get("props", {}).get("jobs", [])
        if is_smoke:
            raw_jobs = raw_jobs[:10]

        for j in raw_jobs:
            title = j.get("title", "")
            if not title or not ROLE_REGEX.search(title):
                continue

            job_id = str(j.get("id", ""))
            job_url = f"https://www.workatastartup.com/jobs/{job_id}"
            company = j.get("companyName", "YC Startup")
            loc = j.get("location", "Remote")
            is_remote = "remote" in loc.lower()

            desc_parts = []
            if j.get("companyOneLiner"):
                desc_parts.append(f"Company: {j['companyOneLiner']}")
            if j.get("salary"):
                desc_parts.append(f"Compensation: {j['salary']}")
            if j.get("roleType"):
                desc_parts.append(f"Role Type: {j['roleType']}")
            description = "\n".join(desc_parts)

            jobs.append({
                "id": f"yc-{job_id}",
                "site": "Y Combinator",
                "job_url": job_url,
                "job_url_direct": job_url,
                "title": title,
                "company": company,
                "location": loc,
                "date_posted": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                "posted_at": datetime.now(timezone.utc).isoformat(),
                "description": description,
                "is_remote": is_remote,
                "search_keyword": "YC Startup",
            })
    except Exception as e:
        logging.debug(f"Error scraping Y Combinator portal: {e}")
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

            posted_at = parse_posted_at(j.get("publication_date"))
            pub_date = posted_at.strftime("%Y-%m-%d") if posted_at else ""
            job_id = str(j.get("id") or app_link)

            jobs.append({
                "id": f"remotive-{job_id}",
                "site": "Remotive",
                "job_url": app_link,
                "job_url_direct": app_link,
                "title": title,
                "company": j.get("company_name", "Remotive Employer"),
                "location": j.get("candidate_required_location") or "",
                "date_posted": pub_date,
                "posted_at": posted_at.isoformat() if posted_at else "",
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

            posted_at = parse_posted_at(j.get("pubDate"))
            pub_date = posted_at.strftime("%Y-%m-%d") if posted_at else ""
            job_id = str(j.get("id") or app_link)

            jobs.append({
                "id": f"jobicy-{job_id}",
                "site": "Jobicy",
                "job_url": app_link,
                "job_url_direct": app_link,
                "title": title,
                "company": j.get("companyName", "Jobicy Employer"),
                "location": j.get("jobGeo") or "",
                "date_posted": pub_date,
                "posted_at": posted_at.isoformat() if posted_at else "",
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

            posted_at = parse_posted_at(j.get("date"))
            date_str = posted_at.strftime("%Y-%m-%d") if posted_at else ""
            job_id = str(j.get("id") or job_url)

            jobs.append({
                "id": f"remoteok-{job_id}",
                "site": "RemoteOK",
                "job_url": job_url,
                "job_url_direct": job_url,
                "title": title,
                "company": html.unescape(j.get("company", "RemoteOK Employer")),
                "location": j.get("location") or "",
                "date_posted": date_str,
                "posted_at": posted_at.isoformat() if posted_at else "",
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

            job_id = str(j.get("slug") or app_link)
            is_remote = bool(j.get("remote"))
            posted_at = parse_posted_at(j.get("created_at") or j.get("date"))

            jobs.append({
                "id": f"arbeitnow-{job_id}",
                "site": "Arbeitnow",
                "job_url": app_link,
                "job_url_direct": app_link,
                "title": title,
                "company": j.get("company_name", "Arbeitnow Employer"),
                "location": j.get("location") or "",
                "date_posted": posted_at.strftime("%Y-%m-%d") if posted_at else "",
                "posted_at": posted_at.isoformat() if posted_at else "",
                "is_remote": is_remote,
                "search_keyword": term,
            })
    except Exception as e:
        logging.debug(f"Error scraping Arbeitnow: {e}")
    return jobs


def main():
    parser = argparse.ArgumentParser(description="JobSpy High-Traffic Tech Portals Scraper")
    parser.add_argument("--smoke-test", action="store_true", help="Quick run on 1 term across portals")
    parser.add_argument("--hours-old", type=int, default=336, help="Keep postings from the last N hours (default: 336)")
    parser.add_argument("--watch", action="store_true", help="Poll fast sources continuously, without sending email")
    parser.add_argument("--poll-interval", type=int, default=600, help="Seconds between watch passes (minimum: 60)")
    args = parser.parse_args()

    # Paths
    base_dir = Path(__file__).resolve().parent
    now = datetime.now()
    month_str = now.strftime("%Y_%m")
    day_str = now.strftime("%Y_%m_%d")
    env_results = os.environ.get("JOBSPY_RESULTS_DIR")
    results_dir = Path(env_results) if env_results else (base_dir / "scraped jobs" / "results" / month_str / day_str)
    env_logs = os.environ.get("JOBSPY_LOGS_DIR")
    log_dir = Path(env_logs) if env_logs else (base_dir / "scraped jobs" / "logs" / month_str / day_str)
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

    out_csv = results_dir / "tech_portals_jobs.csv"
    first_seen_path = base_dir.parent / ".pipeline_state" / "portal_first_seen.json"

    def collect_once():
        all_jobs = scrape_fast_sources(
            is_smoke=args.smoke_test,
            hours_old=24 if args.watch else args.hours_old,
        )
        if not args.watch:
            all_jobs.extend(scrape_himalayas_portal("Data & Analytics", is_smoke=args.smoke_test))
            all_jobs.extend(scrape_yc_portal(is_smoke=args.smoke_test))
            all_jobs.extend(scrape_remotive_portal("Data & Analytics", is_smoke=args.smoke_test))
            all_jobs.extend(scrape_remoteok_portal("Data & Software", is_smoke=args.smoke_test))
            all_jobs.extend(scrape_jobicy_portal("Data & Analytics", is_smoke=args.smoke_test))
            all_jobs.extend(scrape_arbeitnow_portal("Data & Software", is_smoke=args.smoke_test))
            terms = ["Data Analyst"] if args.smoke_test else SEARCH_TERMS
            for term in terms:
                all_jobs.extend(scrape_builtin_portal(term, is_smoke=args.smoke_test))

        now_utc = datetime.now(timezone.utc)
        cutoff = now_utc - timedelta(hours=max(1, args.hours_old))
        kept = {}
        for job in all_jobs:
            posted = parse_posted_at(job.get("posted_at") or job.get("date_posted"))
            url = str(job.get("job_url", "")).strip()
            if (
                not posted
                or posted < cutoff
                or posted > now_utc
                or not url
                or not ROLE_REGEX.search(str(job.get("title", "")))
                or not is_target_location(str(job.get("location", "")), bool(job.get("is_remote")))
            ):
                continue
            if job.get("posted_at"):
                job["posted_at"] = posted.isoformat()
            job["date_posted"] = posted.strftime("%Y-%m-%d")
            kept[url] = job

        previous = {}
        seen = {}
        try:
            with open(first_seen_path, encoding="utf-8") as state_file:
                seen = json.load(state_file)
        except (OSError, json.JSONDecodeError):
            pass
        if args.watch and out_csv.exists():
            with open(out_csv, newline="", encoding="utf-8") as old_file:
                for row in csv.DictReader(old_file):
                    url = row.get("job_url", "").strip()
                    posted = parse_posted_at(row.get("posted_at") or row.get("date_posted"))
                    if url and posted and cutoff <= posted <= now_utc:
                        previous[url] = row

        for job in kept.values():
            key = job["job_url"]
            seen.setdefault(key, now_utc.isoformat())
            job["first_seen_at"] = seen[key]
        for url, row in previous.items():
            seen.setdefault(url, row.get("first_seen_at") or now_utc.isoformat())
        merged = {**previous, **kept}
        seen = {url: seen.get(url, now_utc.isoformat()) for url in merged}
        final_list = sorted(merged.values(), key=lambda job: job.get("posted_at", ""), reverse=True)

        temp_csv = out_csv.with_suffix(".csv.tmp")
        with open(temp_csv, mode="w", newline="", encoding="utf-8") as output_file:
            writer = csv.DictWriter(output_file, fieldnames=COLUMNS, quoting=csv.QUOTE_NONNUMERIC, escapechar="\\")
            writer.writeheader()
            writer.writerows({key: row.get(key, "") for key in COLUMNS} for row in final_list)
        os.replace(temp_csv, out_csv)

        first_seen_path.parent.mkdir(parents=True, exist_ok=True)
        temp_state = first_seen_path.with_suffix(".json.tmp")
        temp_state.write_text(json.dumps(seen, indent=2), encoding="utf-8")
        os.replace(temp_state, first_seen_path)
        logging.info("Saved %s fresh portal jobs to %s", len(final_list), out_csv)
        print(f"DONE: {len(final_list)} jobs written to {out_csv}")

    if args.watch:
        interval = max(60, args.poll_interval)
        logging.info("Watching fast sources every %s seconds; press Ctrl+C to stop", interval)
        while True:
            try:
                collect_once()
            except KeyboardInterrupt:
                break
            except Exception:
                logging.exception("Portal watch pass failed; prior CSV is retained")
            time.sleep(interval)
    else:
        collect_once()


if __name__ == "__main__":
    main()
