from __future__ import annotations

import re
import time
from datetime import date, datetime, timezone
from typing import Any, Mapping
from urllib.parse import urlparse

import pandas as pd
import requests

INTERN_TITLE_PATTERN = re.compile(
    r"(?i)\b(intern|internship|interns|co-?op|summer (?:analyst|associate))\b"
)
YEAR_2027_PATTERN = re.compile(r"(?i)\b2027\b")
OTHER_SEASON_PATTERN = re.compile(
    r"(?i)\b(?:fall|spring|winter)\s*2027\b|\bsummer\s*2026\b|\bsummer\s*2028\b"
)
SUMMER_2027_PATTERN = re.compile(r"(?i)\bsummer\s*2027\b")
RETURN_TO_SCHOOL_PATTERN = re.compile(
    r"(?i)(?:return to (?:school|studies|university|college|classes)|"
    r"plan to return|must be returning|"
    r"continue(?:d)? (?:your )?(?:studies|enrollment)|"
    r"rising (?:junior|senior)s?)"
)

UNDERGRAD_TITLE_PATTERN = re.compile(r"(?i)\bundergraduate\b")
UNDERGRAD_TEXT_PATTERN = re.compile(
    r"(?i)(?:undergraduate (?:program|students?|degrees?)|"
    r"enrolled in an undergraduate|"
    r"rising (?:junior|senior)s?|"
    r"(?:working toward|pursuing)(?: an?| your)? bachelor'?s degree)"
)
GRAD_SIGNAL_PATTERN = re.compile(
    r"(?i)\b(master'?s|graduate|mba|ph\.?d|m\.?s\.?ba|m\.?s\.?pa|m\.?s\.?(?!\s*excel))\b"
)

CLOSED_PHRASES = (
    "sorry, this job is no longer available",
    "no longer accepting applications",
    "the position has been filled",
    "this job has expired",
    "this posting has expired",
    "this role has been closed",
    "this job is no longer available",
    "job not found",
    "position is closed",
)

HOST_DELAY_S = 0.5


def internship_flags(
    title: str | None,
    description: str | None = None,
    job_type: str | None = None,
    degrees: str | list[str] | None = None,
) -> dict[str, bool]:
    """Compute boolean classifier flags for internship postings."""
    t_str = "" if title is None or pd.isna(title) else str(title).strip()
    d_str = "" if description is None or pd.isna(description) else str(description).strip()
    jt_str = "" if job_type is None or pd.isna(job_type) else str(job_type).strip().lower()

    # 1. is_internship: title matches pattern or job_type is explicitly internship
    has_intern_title = bool(INTERN_TITLE_PATTERN.search(t_str))
    is_intern = has_intern_title or jt_str == "internship"

    if not is_intern:
        return {
            "is_internship": False,
            "mentions_2027": False,
            "wrong_season": False,
            "needs_return_to_school": False,
            "undergrad_only": False,
        }

    combined_text = f"{t_str} {d_str}".strip()

    # 2. mentions_2027
    mentions_2027 = bool(YEAR_2027_PATTERN.search(combined_text))

    # 3. wrong_season: matches other seasons unless Summer 2027 is also present
    has_other_season = bool(OTHER_SEASON_PATTERN.search(combined_text))
    has_summer_2027 = bool(SUMMER_2027_PATTERN.search(combined_text))
    wrong_season = has_other_season and not has_summer_2027

    # 4. needs_return_to_school
    needs_return = bool(RETURN_TO_SCHOOL_PATTERN.search(combined_text))

    # 5. undergrad_only
    # Check structured degrees first if provided
    undergrad_only = False
    if degrees is not None and not (isinstance(degrees, float) and pd.isna(degrees)):
        deg_list: list[str]
        if isinstance(degrees, list):
            deg_list = [str(x).strip() for x in degrees if str(x).strip()]
        else:
            deg_list = [x.strip() for x in str(degrees).split(",") if x.strip()]

        if deg_list:
            deg_lower = [d.lower() for d in deg_list]
            has_grad_deg = any(
                "master" in d or "phd" in d or "mba" in d for d in deg_lower
            )
            has_undergrad_deg = any(
                "bachelor" in d or "associate" in d for d in deg_lower
            )
            if has_undergrad_deg and not has_grad_deg:
                undergrad_only = True
            else:
                undergrad_only = False
            return {
                "is_internship": True,
                "mentions_2027": mentions_2027,
                "wrong_season": wrong_season,
                "needs_return_to_school": needs_return,
                "undergrad_only": undergrad_only,
            }

    # Text-based detection when structured degrees are absent
    has_undergrad_signal = (
        bool(UNDERGRAD_TITLE_PATTERN.search(t_str))
        or bool(UNDERGRAD_TEXT_PATTERN.search(combined_text))
    )
    has_grad_signal = bool(GRAD_SIGNAL_PATTERN.search(combined_text))

    if has_undergrad_signal and not has_grad_signal:
        undergrad_only = True

    return {
        "is_internship": True,
        "mentions_2027": mentions_2027,
        "wrong_season": wrong_season,
        "needs_return_to_school": needs_return,
        "undergrad_only": undergrad_only,
    }


def classify(df: pd.DataFrame) -> pd.DataFrame:
    """Classify rows in a DataFrame, attaching flags and filtering to is_internship == True."""
    if df.empty:
        return df.copy()

    classified_rows: list[dict[str, Any]] = []
    for _, row in df.iterrows():
        flags = internship_flags(
            row.get("title"),
            row.get("description"),
            job_type=row.get("job_type"),
            degrees=row.get("degrees"),
        )
        if not flags["is_internship"]:
            continue
        row_dict = row.to_dict()
        row_dict.update(flags)
        classified_rows.append(row_dict)

    if not classified_rows:
        return pd.DataFrame(columns=list(df.columns) + [
            "is_internship", "mentions_2027", "wrong_season", "needs_return_to_school", "undergrad_only"
        ])

    return pd.DataFrame(classified_rows)


def parse_simplify(listings: list[dict[str, Any]]) -> pd.DataFrame:
    """Parse Simplify listings.json records into standard ingest format."""
    cols = [
        "id", "site", "company", "title", "job_url", "job_url_direct",
        "location", "is_remote", "date_posted", "description", "degrees"
    ]
    if not listings:
        return pd.DataFrame(columns=cols)

    rows: list[dict[str, Any]] = []
    for item in listings:
        if not item.get("active") or not item.get("is_visible"):
            continue
        terms = item.get("terms") or []
        if "Summer 2027" not in terms:
            continue

        locs = item.get("locations") or []
        location_str = "; ".join(locs) if locs else ""
        is_remote = any("remote" in l.lower() for l in locs)

        date_posted_epoch = item.get("date_posted")
        if date_posted_epoch:
            date_posted_str = datetime.fromtimestamp(date_posted_epoch, tz=timezone.utc).strftime("%Y-%m-%d")
        else:
            date_posted_str = None

        url = item.get("url") or item.get("company_url") or ""
        degrees_val = ", ".join(item.get("degrees") or [])

        rows.append({
            "id": item.get("id"),
            "site": "simplify",
            "company": (item.get("company_name") or "").strip(),
            "title": (item.get("title") or "").strip(),
            "job_url": url,
            "job_url_direct": url,
            "location": location_str,
            "is_remote": is_remote,
            "date_posted": date_posted_str,
            "description": f"{item.get('title', '')} - {', '.join(terms)}",
            "degrees": degrees_val,
        })

    if not rows:
        return pd.DataFrame(columns=cols)
    return pd.DataFrame(rows)


_JOBRIGHT_ROW_RE = re.compile(
    r"^\|\s*\*\*\[(?P<company>[^\]]+)\]\((?P<comp_url>[^\)]+)\)\*\*\s*\|"
    r"\s*\*\*\[(?P<title>[^\]]+)\]\((?P<job_url>[^\)]+)\)\*\*\s*\|"
    r"\s*(?P<location>[^\|]+)\|\s*(?P<work_model>[^\|]+)\|\s*(?P<date_posted>[^\|]+)\|"
)

_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}


def parse_jobright(md_text: str, today: date | None = None) -> pd.DataFrame:
    """Parse Jobright markdown table postings into standard ingest format."""
    cols = [
        "id", "site", "company", "title", "job_url", "job_url_direct",
        "location", "is_remote", "date_posted", "description"
    ]
    if not md_text:
        return pd.DataFrame(columns=cols)

    today = today or date.today()
    rows: list[dict[str, Any]] = []

    for line in md_text.splitlines():
        line = line.strip()
        m = _JOBRIGHT_ROW_RE.match(line)
        if not m:
            continue

        company = m.group("company").strip()
        title = m.group("title").strip()
        job_url = m.group("job_url").strip()
        location = m.group("location").strip()
        work_model = m.group("work_model").strip()
        raw_date = m.group("date_posted").strip()

        is_remote = "remote" in location.lower() or "remote" in work_model.lower()

        # Parse date like "Nov 03"
        date_posted_str = None
        date_parts = raw_date.split()
        if len(date_parts) == 2:
            m_str, d_str = date_parts[0].lower()[:3], date_parts[1]
            if m_str in _MONTHS and d_str.isdigit():
                m_num = _MONTHS[m_str]
                d_num = int(d_str)
                cand_year = today.year
                try:
                    cand_date = date(cand_year, m_num, d_num)
                    if cand_date > today:
                        cand_date = date(cand_year - 1, m_num, d_num)
                    date_posted_str = cand_date.strftime("%Y-%m-%d")
                except ValueError:
                    pass

        rows.append({
            "id": job_url,
            "site": "jobright",
            "company": company,
            "title": title,
            "job_url": job_url,
            "job_url_direct": job_url,
            "location": location,
            "is_remote": is_remote,
            "date_posted": date_posted_str,
            "description": f"{title} at {company} ({work_model})",
        })

    if not rows:
        return pd.DataFrame(columns=cols)
    return pd.DataFrame(rows)


def check_url_open(
    url: str,
    session: requests.Session | Any = None,
    timeout: float = 10.0,
) -> bool | None:
    """Verify if an apply URL is still active.

    Returns:
    - True: actively open
    - False: definitively closed (404/410 or explicit closure text)
    - None: unknown / rate-limited / temporary network error
    """
    if not url:
        return None

    s = session or requests.Session()
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }

    try:
        resp = s.get(url, headers=headers, timeout=timeout, allow_redirects=True)
    except Exception:
        return None

    if resp.status_code in (404, 410):
        return False
    if resp.status_code in (403, 429, 500, 502, 503, 504):
        return None
    if resp.status_code != 200:
        return None

    text_lower = (resp.text or "").lower()
    for phrase in CLOSED_PHRASES:
        if phrase in text_lower:
            return False

    return True
