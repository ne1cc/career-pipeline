from __future__ import annotations

import html
import re
from typing import Any, Callable
from urllib.parse import urlparse

import duckdb
import requests
from bs4 import BeautifulSoup

from cockpit.normalizer import load_rules, score_job

_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; CareerPipelineEnricher/1.0)"}
_GH_RE = re.compile(r"greenhouse\.io/([^/]+)/jobs/(\d+)")
_LEVER_RE = re.compile(r"jobs\.lever\.co/([^/]+)/([a-f0-9-]+)", re.I)


def is_stub_description(description: str | None) -> bool:
    """Return True when description is missing, very short, or a Simplify stub."""
    if not description or not description.strip():
        return True
    text = description.strip()
    if len(text) < 250:
        return True
    return text.startswith("Role:") and "Simplify URL:" in text


def _html_to_text(raw_html: str) -> str:
    soup = BeautifulSoup(raw_html, "html.parser")
    for tag in soup(["script", "style", "noscript", "nav", "header", "footer"]):
        tag.decompose()
    container = soup.find("main") or soup.find("article") or soup.body or soup
    text = container.get_text("\n", strip=True)
    return html.unescape(re.sub(r"\n{3,}", "\n\n", text)).strip()


def fetch_jd_from_url(url: str, timeout: int = 12) -> str | None:
    """Fetch full job description text from ATS APIs or generic job posting HTML."""
    if not url or not url.startswith(("http://", "https://")):
        return None
    try:
        gh_match = _GH_RE.search(url)
        if gh_match:
            slug, job_id = gh_match.groups()
            api = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs/{job_id}"
            resp = requests.get(api, headers=_HEADERS, timeout=timeout)
            if resp.status_code == 200:
                content = resp.json().get("content") or ""
                text = _html_to_text(html.unescape(content))
                if len(text) >= 200:
                    return text

        lever_match = _LEVER_RE.search(url)
        if lever_match:
            slug, job_id = lever_match.groups()
            api = f"https://api.lever.co/v0/postings/{slug}/{job_id}"
            resp = requests.get(api, headers=_HEADERS, timeout=timeout)
            if resp.status_code == 200:
                data = resp.json()
                parts = [data.get("descriptionPlain") or ""]
                for lst in data.get("lists") or []:
                    parts.append(f"{lst.get('text', '')}\n{lst.get('content', '')}")
                text = _html_to_text("\n".join(parts))
                if len(text) >= 200:
                    return text

        host = urlparse(url).netloc.lower()
        if "linkedin.com" in host or "indeed.com" in host:
            # Avoid tripping anti-bot blocks on job boards during post-ingest enrichment
            return None

        resp = requests.get(url, headers=_HEADERS, timeout=timeout, allow_redirects=True)
        if resp.status_code != 200:
            return None
        text = _html_to_text(resp.text)
        return text if len(text) >= 200 else None
    except Exception:
        return None


def enrich_queued_jobs(
    con: duckdb.DuckDBPyConnection,
    *,
    limit: int = 100,
    min_score: float = 10.0,
    fetcher: Callable[[str], str | None] = fetch_jd_from_url,
    rules: dict[str, Any] | None = None,
) -> dict[str, int]:
    """Fetch full JDs for top-scoring QUEUED stub postings and re-score them."""
    rules = rules or load_rules()
    rows = con.execute(
        """
        SELECT dedup_key, title, description, location, is_remote, company, apply_url, priority_score
        FROM jobs_master
        WHERE status = 'QUEUED'
          AND (is_open IS NULL OR is_open = TRUE)
          AND priority_score >= ?
        ORDER BY priority_score DESC, discovered_at DESC
        """,
        [min_score],
    ).fetchall()

    targets = [r for r in rows if is_stub_description(r[2])][:limit]
    checked = 0
    enriched = 0
    dropped = 0

    for key, title, _desc, loc, remote, company, apply_url, _old_score in targets:
        checked += 1
        full_jd = fetcher(apply_url or "")
        if not full_jd:
            continue
        scored = score_job(
            title,
            full_jd,
            rules,
            loc or "",
            bool(remote),
            company=company or "",
        )
        con.execute(
            """
            UPDATE jobs_master
            SET description = ?, priority_score = ?, resume_archetype = ?, resume_path = ?
            WHERE dedup_key = ?
            """,
            [full_jd, scored.score, scored.archetype.value, scored.resume_path, key],
        )
        enriched += 1
        if scored.score < min_score:
            dropped += 1

    return {"checked": checked, "enriched": enriched, "dropped": dropped}
