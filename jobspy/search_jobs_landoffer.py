#!/usr/bin/env python3
"""LandOffer job feed.

Intent: pull recent early-career data / analytics / software postings from landoffer.ai's
public job pages, with the full description and the employer's own apply link, and write
them in the standard Cockpit CSV format.
Contract: reads only public HTML pages under /jobs (robots.txt allows them and disallows
/api/, which is never called). One request at a time with a pause between requests, a cap
on detail pages, and an overall time budget, so a run always ends and stays light on the site.
Limits: parses the site's current markup (job-card classes, JobPosting JSON-LD); a redesign
yields fewer or zero rows rather than an error. No login, no personalized results.
"""

from __future__ import annotations

import argparse
import csv
import html
import json
import os
import re
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

default_out = Path(__file__).parent / "scraped jobs" / "results" / datetime.now().strftime("%Y_%m") / datetime.now().strftime("%Y_%m_%d")
OUTPUT_DIR = Path(os.environ.get("JOBSPY_RESULTS_DIR", default_out))
OUTPUT_NAME = "landoffer_jobs.csv"

BASE_URL = "https://landoffer.ai"
USER_AGENT = "Mozilla/5.0 (compatible; personal-job-search-script)"
QUERIES = [
    "data engineer",
    "analytics engineer",
    "data analyst",
    "business intelligence analyst",
    "business analyst",
    "software engineer new grad",
    "backend engineer",
    "platform engineer",
    "product analyst",
    "solutions engineer",
    "forward deployed engineer",
    "implementation engineer",
    "technical support engineer",
    "full stack engineer",
]
MAX_PAGES = 3  # list pages per query (24 jobs each)
MAX_DETAILS = 150  # detail pages per run
DELAY_S = 1.0  # pause between requests
BUDGET_S = 420.0  # whole-run time limit

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
    "first_seen_at",
    "description",
    "is_remote",
    "search_keyword",
]

ROLE_PATTERN = re.compile(
    r"\b(?:analyst|analytics|data|bi|business intelligence|software|developer|backend|platform|engineer|"
    r"solutions|implementation|support|qa|product|operations|bizops|revops|full stack|ai|ml)\b",
    re.IGNORECASE,
)
SENIOR_PATTERN = re.compile(
    r"\b(?:senior|sr|staff|principal|lead|manager|director|head|vp|architect|iii|iv)\b\.?",
    re.IGNORECASE,
)

_CARD = re.compile(
    r'<a class="job-card" href="(?P<href>/jobs/[^"]+)".*?'
    r'<span class="job-card-title">(?P<title>.*?)</span>'
    r'<span class="job-card-sub">(?P<sub>.*?)</span>',
    re.DOTALL,
)
_LD_JSON = re.compile(r'<script type="application/ld\+json"[^>]*>(.*?)</script>', re.DOTALL)
_APPLY_LINK = re.compile(r'<a[^>]*href="(https?://[^"]+)"[^>]*>(?:(?!</a>).){0,200}?apply', re.DOTALL | re.IGNORECASE)
_TAG = re.compile(r"<[^>]+>")


def _text(fragment: str) -> str:
    """HTML fragment -> plain text on one line."""
    return re.sub(r"\s+", " ", html.unescape(_TAG.sub("", fragment))).strip()


def html_to_text(fragment: str) -> str:
    """Job description HTML -> readable text: one line per paragraph, "- " per list item."""
    s = re.sub(r"<li[^>]*>", "\n- ", fragment or "")
    s = re.sub(r"</(?:p|div|h\d|ul|ol|li)>|<br\s*/?>", "\n", s)
    lines = (re.sub(r"[ \t]+", " ", line).strip() for line in html.unescape(_TAG.sub("", s)).splitlines())
    return "\n".join(line for line in lines if line)


def parse_cards(page: str) -> list[dict[str, str]]:
    """List page -> [{href, title, company, location}] in page order."""
    cards = []
    for m in _CARD.finditer(page):
        company, _, location = _text(m["sub"]).partition(" · ")
        cards.append({"href": m["href"], "title": _text(m["title"]), "company": company, "location": location})
    return cards


def wanted(title: str) -> bool:
    """Keep target-family titles; drop senior / lead / manager ones before spending a request."""
    return bool(ROLE_PATTERN.search(title)) and not SENIOR_PATTERN.search(title)


def parse_detail(page: str) -> dict[str, Any]:
    """Detail page -> {description, posted_at, location, apply_url}; missing parts are ""."""
    out: dict[str, Any] = {"description": "", "posted_at": "", "location": "", "apply_url": ""}
    # 1. The JobPosting JSON-LD block holds the description, post date and location.
    for raw in _LD_JSON.findall(page):
        try:
            data = json.loads(raw)
        except ValueError:
            continue
        nodes = data.get("@graph", [data]) if isinstance(data, dict) else []
        for node in nodes:
            if not isinstance(node, dict) or node.get("@type") != "JobPosting":
                continue
            out["description"] = html_to_text(str(node.get("description") or ""))
            out["posted_at"] = str(node.get("datePosted") or "")
            places = node.get("jobLocation") or []
            places = places if isinstance(places, list) else [places]
            parts = []
            for place in places:
                addr = place.get("address", {}) if isinstance(place, dict) else {}
                parts.append(", ".join(str(addr[k]) for k in ("addressLocality", "addressRegion", "addressCountry") if addr.get(k)))
            out["location"] = "; ".join(p for p in parts if p)
    # 2. The employer's own posting is the first off-site link labelled "Apply".
    for url in _APPLY_LINK.findall(page):
        url = html.unescape(url)
        if "landoffer.ai" not in urllib.parse.urlparse(url).netloc:
            out["apply_url"] = url
            break
    return out


def fetch(url: str, timeout: float = 20.0) -> str:
    """GET a public page; raises on network or HTTP errors."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def scrape(
    queries: list[str] = QUERIES,
    hours: int = 24,
    max_pages: int = MAX_PAGES,
    max_details: int = MAX_DETAILS,
    get: Callable[[str], str] = fetch,
    delay: float = DELAY_S,
    budget: float = BUDGET_S,
) -> list[dict[str, Any]]:
    """Collect standard-format rows for wanted jobs posted in the last `hours`.

    Contract: at most len(queries) * max_pages list requests and max_details detail requests,
    `delay` seconds apart, stopping early when `budget` seconds have passed. A page that fails
    to load is skipped; a job whose detail page fails keeps its list-page fields.
    """
    deadline = time.monotonic() + budget
    now = datetime.now(timezone.utc).isoformat()

    def get_page(url: str) -> str | None:
        if time.monotonic() > deadline:
            return None
        try:
            page = get(url)
        except Exception as e:
            print(f"  skipped {url}: {e}")
            page = None
        time.sleep(delay)
        return page

    # 1. List pages: newest first per query, deduplicated by job path.
    picked: dict[str, tuple[dict[str, str], str]] = {}
    for query in queries:
        for page_no in range(1, max_pages + 1):
            params = {"q": query, "posted_since": hours, "page": page_no}
            page = get_page(f"{BASE_URL}/jobs?{urllib.parse.urlencode(params)}")
            cards = parse_cards(page) if page else []
            for card in cards:
                if wanted(card["title"]):
                    picked.setdefault(card["href"], (card, query))
            if len(cards) < 24:  # short page = last page
                break

    # 2. Detail pages for the description and the employer's apply link.
    rows = []
    for n, (href, (card, query)) in enumerate(picked.items()):
        job_url = BASE_URL + href
        page = get_page(job_url) if n < max_details else None
        detail = parse_detail(page) if page else {"description": "", "posted_at": "", "location": "", "apply_url": ""}
        location = detail["location"] or card["location"]
        rows.append({
            "id": "landoffer_" + href.rsplit("-", 1)[-1],
            "site": "landoffer",
            "job_url": job_url,
            "job_url_direct": detail["apply_url"] or job_url,
            "title": card["title"],
            "company": card["company"],
            "location": location,
            "date_posted": detail["posted_at"][:10] or None,
            "posted_at": detail["posted_at"] or None,
            "first_seen_at": now,
            "description": detail["description"],
            "is_remote": "remote" in f"{card['title']} {location}".lower(),
            "search_keyword": query,
        })
    return rows


def write_csv(rows: list[dict[str, Any]], out_dir: Path = OUTPUT_DIR) -> Path:
    """Write rows to landoffer_jobs.csv; nothing is written when there are no rows."""
    path = out_dir / OUTPUT_NAME
    if rows:
        out_dir.mkdir(parents=True, exist_ok=True)
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=STANDARD_COLUMNS, quoting=csv.QUOTE_NONNUMERIC, escapechar="\\")
            writer.writeheader()
            writer.writerows(rows)
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest recent jobs from landoffer.ai's public job pages.")
    parser.add_argument("--hours", type=int, default=24, help="Only jobs posted in the last N hours (default 24).")
    parser.add_argument("--smoke-test", action="store_true", help="One query, one page, five detail pages.")
    args = parser.parse_args()

    if args.smoke_test:
        rows = scrape(queries=QUERIES[:1], hours=args.hours, max_pages=1, max_details=5)
    else:
        rows = scrape(hours=args.hours)
    path = write_csv(rows)
    print(f"Saved {len(rows)} LandOffer listings to:\n  {path}" if rows else "No matching LandOffer roles found.")


if __name__ == "__main__":
    main()
