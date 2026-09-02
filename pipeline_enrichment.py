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


def analyst_mask(df: pd.DataFrame) -> pd.Series:
    if "title" not in df.columns:
        return pd.Series(False, index=df.index)
    return df["title"].astype(str).str.contains(ANALYST_PATTERN, flags=re.IGNORECASE, regex=True, na=False)


def build_action_items_sheet(segment_frames: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Rows worth applying to today: analyst/data filter + new or freshly posted."""
    pieces = []
    for segment_name, df in segment_frames.items():
        if df.empty:
            continue
        subset = df[analyst_mask(df) & (df["is_new"] | df["is_fresh_post"])].copy()
        if subset.empty:
            continue
        subset.insert(0, "segment", segment_name)
        pieces.append(subset)

    if not pieces:
        return pd.DataFrame(columns=["segment", *ENRICHMENT_COLUMNS])

    combined = pd.concat(pieces, ignore_index=True)
    easy_apply_rank = combined["apply_type"].str.contains("Easy Apply", case=False, na=False)
    combined["_sort"] = (
        combined["is_new"].astype(int) * 4
        + combined["is_fresh_post"].astype(int) * 2
        + easy_apply_rank.astype(int)
    )
    if "title" in combined.columns:
        combined["_title_sort"] = combined["title"].astype(str).str.lower()
        combined = combined.sort_values(
            by=["_sort", "_title_sort"],
            ascending=[False, True],
        ).drop(columns=["_sort", "_title_sort"])
    else:
        combined = combined.sort_values("_sort", ascending=False).drop(columns="_sort")
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
        "action_items": 0,
    }
    for df in segment_frames.values():
        if df.empty:
            continue
        totals["total_jobs"] += len(df)
        totals["new_jobs"] += int(df["is_new"].sum())
        totals["fresh_posts"] += int(df["is_fresh_post"].sum())
        totals["easy_apply"] += int(
            df["apply_type"].str.contains("Easy Apply", case=False, na=False).sum()
        )
    action_items = build_action_items_sheet(segment_frames)
    totals["action_items"] = len(action_items)
    return totals


def format_email_summary(stats: dict[str, int], *, has_previous_run: bool) -> str:
    new_line = (
        f"- New since last run: {stats['new_jobs']}\n"
        if has_previous_run
        else "- New since last run: (first run — all jobs treated as baseline)\n"
    )
    return (
        f"Quick stats:\n"
        f"- Total jobs in report: {stats['total_jobs']}\n"
        f"{new_line}"
        f"- Posted in last {FRESH_POST_DAYS} days: {stats['fresh_posts']}\n"
        f"- Easy Apply listings: {stats['easy_apply']}\n"
        f"- Action Items tab (new/fresh + analyst/data filter): {stats['action_items']}\n\n"
        f"Legend in Excel:\n"
        f"- Green row = new since your last daily run\n"
        f"- Yellow row = posted within {FRESH_POST_DAYS} days\n"
        f"- Blue row = both new and fresh\n"
        f"- apply_type column = Indeed/LinkedIn Easy Apply vs company site\n"
    )
