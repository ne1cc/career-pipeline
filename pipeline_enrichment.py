"""Job-listing enrichment for the daily pipeline (apply type, new jobs, fresh posts)."""

from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterable

import pandas as pd
from openpyxl.styles import PatternFill

NEW_FILL = PatternFill(start_color="C6EFCE", end_color="C6EFCE", fill_type="solid")
FRESH_FILL = PatternFill(start_color="FFEB9C", end_color="FFEB9C", fill_type="solid")
NEW_AND_FRESH_FILL = PatternFill(start_color="B4D7FF", end_color="B4D7FF", fill_type="solid")

ENRICHMENT_COLUMNS = ("apply_type", "is_new", "is_fresh_post")
FRESH_POST_DAYS = 2
ANALYST_PATTERN = r"\b(?:analyst|analytics|data)\b"


def load_previous_urls(state_path: Path) -> dict[str, set[str]]:
    if not state_path.exists():
        return {}
    try:
        payload = json.loads(state_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    segments = payload.get("segments", {})
    return {name: set(urls) for name, urls in segments.items() if isinstance(urls, list)}


def save_current_urls(state_path: Path, segment_urls: dict[str, Iterable[str]]) -> None:
    state_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "last_run": datetime.now().isoformat(timespec="seconds"),
        "segments": {name: sorted(set(urls)) for name, urls in segment_urls.items()},
    }
    state_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def parse_posted_date(value) -> date | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    text = str(value).strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%m/%d/%Y"):
        try:
            return datetime.strptime(text[:19], fmt).date()
        except ValueError:
            continue
    return None


def classify_apply_type(row: pd.Series) -> str:
    site = str(row.get("site", "") or "").lower()
    job_url = str(row.get("job_url", "") or "").lower()
    job_url_direct = str(row.get("job_url_direct", "") or "").strip().lower()
    combined_urls = f"{job_url} {job_url_direct}"

    if "greenhouse.io" in combined_urls:
        return "Greenhouse Direct"
    if "lever.co" in combined_urls:
        return "Lever Direct"
    if "ashbyhq.com" in combined_urls:
        return "Ashby Direct"
    if "myworkdayjobs.com" in combined_urls or "workday" in combined_urls:
        return "Workday Direct"
    if "smartrecruiters.com" in combined_urls:
        return "SmartRecruiters Direct"
    if "builtin" in combined_urls or "builtin" in site:
        return "BuiltIn Direct"
    if "himalayas" in combined_urls or "himalayas" in site:
        return "Himalayas Direct"
    if "remotive" in combined_urls or "remotive" in site:
        return "Remotive Direct"
    if "jobicy" in combined_urls or "jobicy" in site:
        return "Jobicy Direct"
    if "remoteok" in combined_urls or "remoteok" in site:
        return "RemoteOK Direct"
    if "arbeitnow" in combined_urls or "arbeitnow" in site:
        return "Arbeitnow Direct"
    if "hiringcafe" in combined_urls or "hiringcafe" in site:
        return "HiringCafe Direct"

    if site == "ats":
        return "Company ATS Direct"

    if site == "indeed":
        if "indeed-apply" in job_url_direct or "indeed-apply" in job_url:
            return "Indeed Easy Apply"
        if job_url_direct and job_url_direct not in ("", "nan", "none"):
            return "Company Site"
        return "Indeed (unknown apply)"

    if site == "linkedin":
        if not job_url_direct or job_url_direct in ("nan", "none"):
            return "LinkedIn Easy Apply"
        return "Company Site"

    if site == "zip_recruiter":
        return "ZipRecruiter Apply"

    if site == "glassdoor":
        return "Glassdoor Apply"

    if site == "google":
        return "Google Jobs (redirect)"

    if job_url_direct and job_url_direct not in ("", "nan", "none"):
        return "Company Site"
    return "Unknown"


def enrich_dataframe(
    df: pd.DataFrame,
    previous_urls: set[str],
    *,
    reference_date: date | None = None,
    has_previous_run: bool,
) -> pd.DataFrame:
    if df.empty:
        for col in ENRICHMENT_COLUMNS:
            df[col] = pd.Series(dtype=str if col == "apply_type" else bool)
        return df

    today = reference_date or date.today()
    fresh_cutoff = today - timedelta(days=FRESH_POST_DAYS)

    enriched = df.copy()
    enriched["apply_type"] = enriched.apply(classify_apply_type, axis=1)

    if "job_url" in enriched.columns and has_previous_run:
        enriched["is_new"] = ~enriched["job_url"].astype(str).isin(previous_urls)
    else:
        enriched["is_new"] = False

    posted_dates = enriched.get("date_posted", pd.Series(index=enriched.index)).map(parse_posted_date)
    enriched["is_fresh_post"] = posted_dates.map(
        lambda posted: bool(posted and posted >= fresh_cutoff)
    )

    front = [col for col in ENRICHMENT_COLUMNS if col in enriched.columns]
    other = [col for col in enriched.columns if col not in front]
    return enriched[front + other]


TRACK_A_PATTERN = r"\b(?:analyst|analytics|data|data scientist|statistician|quantitative|analytics engineer|bi\b|bi developer|business intelligence|reporting|insights)\b"
TRACK_C_PATTERN = r"\b(?:product manager|apm\b|business operations|bizops|operations analyst|project coordinator|strategy & operations|strategy and operations|strategy analyst)\b"
TRACK_B_PATTERN = r"\b(?:software engineer|developer|swe\b|frontend|backend|full stack)\b"

SENIOR_TITLE_PATTERN = r"\b(?:senior|sr\b|sr\.|lead|principal|staff|(?<!product\s)(?<!project\s)manager|director|vp\b|vice president|head of|chief|architect|distinguished|fellow|iii|iv|v)\b"
HIGH_YOE_PATTERN = r"\b(?:[5-9]|1[0-9])\+\s*(?:years|yrs)\b|\b(?:[5-9]|1[0-9])\s*(?:to|-|–)\s*[0-9]+\s*(?:years|yrs)\b"
ENTRY_TITLE_TAG_PATTERN = r"\b(?:new grad|junior|associate|entry level|early career|university|intern|internship|campus)\b"


def matches_fresher_track(title: str) -> bool:
    if not title or pd.isna(title):
        return False
    t = str(title).lower()
    return bool(
        re.search(TRACK_A_PATTERN, t)
        or re.search(TRACK_C_PATTERN, t)
        or (re.search(TRACK_B_PATTERN, t) and re.search(ENTRY_TITLE_TAG_PATTERN, t))
    )


def is_senior_or_high_yoe(title: str, description: str = "") -> bool:
    t = "" if pd.isna(title) else str(title or "").lower()
    if re.search(SENIOR_TITLE_PATTERN, t):
        return True
    d = "" if pd.isna(description) else str(description or "").lower()
    if d and re.search(HIGH_YOE_PATTERN, d):
        return True
    return False


def calculate_fresher_priority_score(row: pd.Series) -> int:
    score = 0
    is_new = row.get("is_new", False)
    if pd.notna(is_new) and bool(is_new):
        score += 5
    is_fresh = row.get("is_fresh_post", False)
    if pd.notna(is_fresh) and bool(is_fresh):
        score += 3

    title = "" if pd.isna(row.get("title")) else str(row.get("title", "")).lower()
    if re.search(ENTRY_TITLE_TAG_PATTERN, title):
        score += 3

    apply_type = "" if pd.isna(row.get("apply_type")) else str(row.get("apply_type", "")).lower()
    if any(k in apply_type for k in ["direct", "easy apply"]):
        score += 2

    if re.search(TRACK_A_PATTERN, title):
        score += 2
    elif re.search(TRACK_C_PATTERN, title) or re.search(TRACK_B_PATTERN, title):
        score += 1

    return score


def deduplicate_action_items(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    df = df.copy()
    # Prefer Direct ATS and higher priority scores
    if "_score" in df.columns:
        df = df.sort_values(by=["_score"], ascending=False)
    # Deduplicate on normalized title and company
    if "title" in df.columns and "company" in df.columns:
        clean_title = df["title"].astype(str).str.lower().str.replace(r"[^\w\s]", "", regex=True)
        clean_company = df["company"].astype(str).str.lower().str.replace(r"[^\w\s]", "", regex=True)
        df["_dedup_key"] = clean_title + "::" + clean_company
        df = df.drop_duplicates(subset=["_dedup_key"], keep="first").drop(columns=["_dedup_key"])
    return df


def analyst_mask(df: pd.DataFrame) -> pd.Series:
    if "title" not in df.columns:
        return pd.Series(False, index=df.index)
    return df["title"].astype(str).str.contains(ANALYST_PATTERN, flags=re.IGNORECASE, regex=True, na=False)


def build_action_items_sheet(segment_frames: dict[str, pd.DataFrame]) -> pd.DataFrame:
    pieces = []
    for segment_name, df in segment_frames.items():
        if df.empty:
            continue
        desc_col = "description" if "description" in df.columns else None

        # Filter for fresher track & exclude senior using boolean mask
        title_series = df["title"].fillna("").astype(str) if "title" in df.columns else pd.Series("", index=df.index)
        desc_series = df[desc_col].fillna("").astype(str) if desc_col else pd.Series("", index=df.index)
        mask = [
            matches_fresher_track(t) and not is_senior_or_high_yoe(t, d)
            for t, d in zip(title_series, desc_series)
        ]

        subset = df[mask].copy()
        if subset.empty:
            continue
        subset.insert(0, "segment", segment_name)
        pieces.append(subset)

    if not pieces:
        return pd.DataFrame(columns=["segment", *ENRICHMENT_COLUMNS])

    combined = pd.concat(pieces, ignore_index=True)
    combined["_score"] = combined.apply(calculate_fresher_priority_score, axis=1)
    combined = deduplicate_action_items(combined)

    if "date_posted" in combined.columns:
        combined["_date_sort"] = pd.to_datetime(combined["date_posted"], errors="coerce")
        combined = combined.sort_values(by=["_score", "_date_sort"], ascending=[False, False]).drop(columns=["_score", "_date_sort"])
    else:
        combined = combined.sort_values(by="_score", ascending=False).drop(columns="_score")

    return combined.reset_index(drop=True)


def apply_row_highlights(ws, df: pd.DataFrame) -> None:
    """Highlight full rows for new listings and recently posted jobs."""
    if df.empty or ws.max_row < 2:
        return

    col_index = {name: idx + 1 for idx, name in enumerate(df.columns)}
    new_idx = col_index.get("is_new")
    fresh_idx = col_index.get("is_fresh_post")
    if not new_idx or not fresh_idx:
        return

    for row_idx in range(2, ws.max_row + 1):
        is_new = bool(ws.cell(row=row_idx, column=new_idx).value)
        is_fresh = bool(ws.cell(row=row_idx, column=fresh_idx).value)
        if not is_new and not is_fresh:
            continue
        fill = NEW_AND_FRESH_FILL if is_new and is_fresh else NEW_FILL if is_new else FRESH_FILL
        for col_idx in range(1, ws.max_column + 1):
            ws.cell(row=row_idx, column=col_idx).fill = fill


def summarize_enrichment(segment_frames: dict[str, pd.DataFrame]) -> dict[str, int]:
    totals = {
        "total_jobs": 0,
        "new_jobs": 0,
        "fresh_posts": 0,
        "easy_apply": 0,
        "direct_ats": 0,
        "action_items": 0,
    }
    for df in segment_frames.values():
        if df.empty:
            continue
        totals["total_jobs"] += len(df)
        if "is_new" in df.columns:
            totals["new_jobs"] += int(df["is_new"].sum())
        if "is_fresh_post" in df.columns:
            totals["fresh_posts"] += int(df["is_fresh_post"].sum())
        if "apply_type" in df.columns:
            totals["easy_apply"] += int(
                df["apply_type"].str.contains("Easy Apply", case=False, na=False).sum()
            )
            totals["direct_ats"] += int(
                df["apply_type"].str.contains("Direct", case=False, na=False).sum()
            )
    action_items = build_action_items_sheet(segment_frames)
    totals["action_items"] = len(action_items)
    return totals


def format_email_summary(stats: dict[str, int], *, has_previous_run: bool) -> str:
    new_line = (
        f"- New since last run: {stats.get('new_jobs', 0)}\n"
        if has_previous_run
        else "- New since last run: (first run — all jobs treated as baseline)\n"
    )
    direct_ats_count = stats.get("direct_ats", 0)
    return (
        f"Quick stats:\n"
        f"- Total Raw Postings: {stats.get('total_jobs', 0)}\n"
        f"- Action Items (Fresher Spotlight): {stats.get('action_items', 0)}\n"
        f"- Direct ATS listings: {direct_ats_count}\n"
        f"{new_line}"
        f"- Posted in last {FRESH_POST_DAYS} days: {stats.get('fresh_posts', 0)}\n"
        f"- Easy Apply listings: {stats.get('easy_apply', 0)}\n\n"
        f"Legend in Excel:\n"
        f"- Green row = new since your last daily run\n"
        f"- Yellow row = posted within {FRESH_POST_DAYS} days\n"
        f"- Blue row = both new and fresh\n"
        f"- apply_type column = Indeed/LinkedIn Easy Apply vs company site / Direct ATS\n"
    )
