"""Fetch a job description from its posting URL.

Used to backfill JDs for rows that were scraped without one (LinkedIn without
`linkedin_fetch_description`, internship feeds that only carry a title), so
dossiers are generated from the real posting.

Limits: self-contained (requests + bs4 + markdownify) so the cockpit never has
to import the heavier `jobspy` package.
"""

from __future__ import annotations

import html
import json
import re
import time
from typing import Any

import requests
from bs4 import BeautifulSoup
from markdownify import markdownify as md

LINKEDIN_GUEST_POSTING = "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{}"
_LINKEDIN_ID = re.compile(r"linkedin\.com/jobs/view/(?:[^/?#]*-)?(\d+)")
_HEADERS = {
    "user-agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "accept-language": "en-US,en;q=0.9",
}
MIN_FALLBACK_CHARS = 300


def linkedin_job_id(url: str | None) -> str | None:
    """Return the numeric LinkedIn job id from a /jobs/view/ URL, or None for any other URL."""
    m = _LINKEDIN_ID.search(url or "")
    return m.group(1) if m else None


def _get(url: str, timeout: float, retries: int) -> str | None:
    """GET a page, retrying throttling and server errors.

    Contract:
    - Returns the body text on HTTP 200.
    - Returns None on other statuses, network errors, or when retries run out.

    Limits: backoff is 1s, 2s, 4s…; only 429 and 5xx are retried.
    """
    for attempt in range(retries + 1):
        try:
            resp = requests.get(url, headers=_HEADERS, timeout=timeout)
        except requests.RequestException:
            resp = None
        if resp is not None and resp.status_code == 200:
            return resp.text
        if resp is not None and resp.status_code != 429 and resp.status_code < 500:
            return None
        if attempt < retries:
            time.sleep(2**attempt)
    return None


def parse_linkedin(page: str) -> str | None:
    """Return the markdown JD from a LinkedIn guest posting page, or None when the markup is absent."""
    node = BeautifulSoup(page, "html.parser").find(
        "div", class_=lambda c: c and "show-more-less-html__markup" in c
    )
    text = md(str(node)).strip() if node else ""
    return text or None


def _json_ld_postings(soup: BeautifulSoup) -> list[dict[str, Any]]:
    """Collect every schema.org JobPosting object from the page's JSON-LD blocks (bad JSON is skipped)."""
    found: list[dict[str, Any]] = []
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or "")
        except (json.JSONDecodeError, TypeError):
            continue
        stack = data if isinstance(data, list) else [data]
        while stack:
            item = stack.pop()
            if not isinstance(item, dict):
                continue
            stack.extend(item.get("@graph", []) if isinstance(item.get("@graph"), list) else [])
            kind = item.get("@type")
            if kind == "JobPosting" or (isinstance(kind, list) and "JobPosting" in kind):
                found.append(item)
    return found


def parse_generic(page: str) -> str | None:
    """Extract a JD from an ATS/company posting page.

    Contract:
    - Returns markdown from the JSON-LD JobPosting.description when present
      (Greenhouse, Lever, Ashby, Workday and most ATS pages ship it).
    - Otherwise returns the text of <main>/<article> if it is at least
      MIN_FALLBACK_CHARS long, else None.

    Limits: the fallback can include page chrome; JS-rendered pages with no
    JSON-LD and no server-side text return None.
    """
    soup = BeautifulSoup(page, "html.parser")

    # 1. Structured data wins → it is the posting itself, without nav or cookie banners
    for posting in _json_ld_postings(soup):
        desc = posting.get("description")
        if isinstance(desc, str) and desc.strip():
            return md(html.unescape(desc)).strip()

    # 2. Fall back to the main content block → reject short text (likely a shell or login wall)
    node = soup.find("main") or soup.find("article")
    if node is None:
        return None
    for junk in node.find_all(["script", "style", "nav", "header", "footer"]):
        junk.decompose()
    text = node.get_text("\n", strip=True)
    return text if len(text) >= MIN_FALLBACK_CHARS else None


def fetch_jd(url: str | None, timeout: float = 10, retries: int = 2) -> str | None:
    """Fetch and extract the job description behind a posting URL.

    Contract:
    - Returns the JD as markdown/plain text on success.
    - Returns None when the URL is empty, unreachable, or has no extractable JD. Never raises.

    Limits: LinkedIn URLs go through the logged-out guest endpoint, which avoids
    the signup redirect but can still be rate-limited (429 is retried).
    """
    if not url or not url.startswith("http"):
        return None
    job_id = linkedin_job_id(url)
    page = _get(LINKEDIN_GUEST_POSTING.format(job_id) if job_id else url, timeout, retries)
    if not page:
        return None
    return parse_linkedin(page) if job_id else parse_generic(page)
