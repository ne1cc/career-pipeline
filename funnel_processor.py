"""Deterministic post-scrape funnel processor for Job_Report workbooks and feeds.

Intent: transform raw multi-segment job scrape DataFrames into deduplicated,
normalized, tiered application funnel views, lead links, and a TUI/CLI JSON feed.

Contract:
- `process_funnel` accepts `{segment_name: DataFrame}` and returns a `FunnelResult`
  containing ready-to-render DataFrames (`tier1_df`, `tier2_df`, `company_clusters_df`,
  `role_tracks_df`, `outreach_leads_df`, `excluded_df`), `tui_feed_records`, and
  `summary_stats`.
- Never mutates caller DataFrames in place; returns empty DataFrames with canonical
  columns when input frames are empty.
- Persists delta state only when `seen_state_path` is provided.

Limits: company alias normalization and staffing/AI-training detection are rule-based
lists; novel employer variants fall back to suffix-stripped names. Salary annualization
assumes 2,080 hours/year and 12 months/year.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus

import pandas as pd

from pipeline_enrichment import (
    DEVELOPER_PATTERN,
    FRESH_POST_DAYS,
    SECURITY_PATTERN,
    TRACK_A_PATTERN,
    TRACK_B_PATTERN,
    TRACK_C_PATTERN,
    _canonical_job_url,
    is_senior_or_high_yoe,
    parse_posted_date,
)

# ───────────────────────── Normalization Constants ─────────────────────────

COMPANY_ALIAS_MAP: dict[str, str] = {
    "amazon": "Amazon",
    "amazoncom": "Amazon",
    "amazon web services": "Amazon",
    "amazon web services aws": "Amazon",
    "aws": "Amazon",
    "amazon science": "Amazon",
    "amazon robotics": "Amazon",
    "google": "Google",
    "google cloud": "Google",
    "alphabet": "Google",
    "microsoft": "Microsoft",
    "meta": "Meta",
    "meta platforms": "Meta",
    "facebook": "Meta",
    "apple": "Apple",
    "jpmorgan chase": "JPMorgan Chase",
    "jp morgan": "JPMorgan Chase",
    "jpmorgan": "JPMorgan Chase",
    "deloitte consulting": "Deloitte",
    "deloitte": "Deloitte",
    "walmart global tech": "Walmart",
    "walmart": "Walmart",
    "salesforcecom": "Salesforce",
    "salesforce": "Salesforce",
}

COMPANY_SUFFIX_RE = re.compile(
    r"(?:,\s*|\s+)(?:inc\.?|incorporated|llc\.?|l\.l\.c\.?|ltd\.?|limited|"
    r"corp\.?|corporation|co\.?|company|plc\.?|holdings?|group|technologies|"
    r"technology|solutions|services|labs|usa|us)\b\.?$",
    re.IGNORECASE,
)

PARENS_SUFFIX_RE = re.compile(r"\s*\([^)]*\)\s*$")

STAFFING_AI_TRAINING_RE = re.compile(
    r"\b(?:"
    r"alignerr|outlier|scale ai|remotasks|dataannotation|data annotation|"
    r"robert half|horizontal talent|insight global|teksystems|randstad|"
    r"apex systems|kforce|cybercoders|actalent|jobot|lensa|"
    r"motion recruitment|beacon hill|kelly services|manpower|adecco|"
    r"aston carter|aerotek|vaco|judge group|collabera|revature|"
    r"synergisticit|braintrust|turing|mercor|telus digital|appen|welocalize"
    r")\b",
    re.IGNORECASE,
)

DATA_ENG_PATTERN = re.compile(
    r"\b(?:data engineer|analytics engineer|bi engineer|etl developer|"
    r"data platform|data warehouse|dbt\b|data pipeline)\b",
    re.IGNORECASE,
)
DATA_SCI_PATTERN = re.compile(
    r"\b(?:data scientist|machine learning|ml engineer|ai engineer|"
    r"applied scientist|statistician|quantitative|research scientist)\b",
    re.IGNORECASE,
)
DATA_BI_PATTERN = re.compile(
    r"\b(?:data analyst|business intelligence|bi analyst|bi developer|"
    r"business analyst|marketing analyst|operations analyst|"
    r"financial analyst|product analyst|reporting analyst|insights analyst|"
    r"analytics|analyst|data)\b",
    re.IGNORECASE,
)
INTERNSHIP_PATTERN = re.compile(r"\b(?:intern|internship|co-op|coop)\b", re.IGNORECASE)

DIRECT_APPLY_TOKENS = ("direct", "company site", "ats")
EASY_APPLY_TOKENS = ("easy apply", "ziprecruiter", "glassdoor")

# Columns preserved during cross-site deduplication backfill
METADATA_BACKFILL_COLUMNS = (
    "date_posted",
    "posted_at",
    "min_amount",
    "max_amount",
    "interval",
    "currency",
    "salary_source",
    "emails",
    "company_industry",
    "company_revenue",
    "company_num_employees",
    "company_url",
    "company_url_direct",
    "job_url_direct",
    "job_level",
    "job_function",
    "skills",
    "experience_range",
    "description",
)


@dataclass
class FunnelResult:
    """Container for all deterministic funnel outputs and summary metrics."""

    tier1_df: pd.DataFrame
    tier2_df: pd.DataFrame
    all_active_df: pd.DataFrame
    company_clusters_df: pd.DataFrame
    role_tracks_df: pd.DataFrame
    outreach_leads_df: pd.DataFrame
    excluded_df: pd.DataFrame
    tui_feed_records: list[dict[str, Any]] = field(default_factory=list)
    summary_stats: dict[str, Any] = field(default_factory=dict)


# ───────────────────────── Normalization Helpers ─────────────────────────


def _clean_str(value: Any) -> str:
    """Return stripped string or empty string for NaN/None/sentinel values."""
    if value is None:
        return ""
    if isinstance(value, float) and pd.isna(value):
        return ""
    text = str(value).strip()
    return "" if text.lower() in {"nan", "none", "null", "nat"} else text


def normalize_company(raw: Any) -> str:
    """Normalize employer strings into a stable `company_group` key.

    Contract:
    - Returns cleaned company display name (e.g. 'Amazon' for 'Amazon Web Services (AWS)').
    - Returns 'Unknown' when input is blank or NaN.

    Limits: alias folding covers common high-volume tech employers; unknown
    subsidiaries keep their stripped base name.
    """
    text = _clean_str(raw)
    if not text:
        return "Unknown"

    # 1. Strip trailing parenthetical qualifiers like "(AWS)" or "(US)" and ".com"
    cleaned = PARENS_SUFFIX_RE.sub("", text).strip()
    cleaned = re.sub(r"\.com\b", "", cleaned, flags=re.IGNORECASE).strip()

    # Check canonical alias map before suffix stripping so "Amazon Web Services" matches directly
    pre_key = re.sub(r"\s+", " ", re.sub(r"[^a-z0-9\s]", "", cleaned.lower())).strip()
    if pre_key in COMPANY_ALIAS_MAP:
        return COMPANY_ALIAS_MAP[pre_key]

    # 2. Iteratively strip legal entity suffixes ("Inc.", "LLC", "Corp.")
    for _ in range(3):
        stripped = COMPANY_SUFFIX_RE.sub("", cleaned).strip(" ,.-")
        if stripped == cleaned or not stripped:
            break
        cleaned = stripped

    # 3. Check canonical alias map again on post-stripped lowercase key
    alias_key = re.sub(r"\s+", " ", re.sub(r"[^a-z0-9\s]", "", cleaned.lower())).strip()
    if alias_key in COMPANY_ALIAS_MAP:
        return COMPANY_ALIAS_MAP[alias_key]

    return cleaned or text


def normalize_location(raw_location: Any, is_remote: Any = False) -> tuple[str, str, str]:
    """Parse raw location text into `(city, state, location_display)`.

    Contract:
    - Returns `(city, state, display)` strings; remote roles default to `'Remote'`
      when no city is present or append `'(Remote)'` when a city is listed.
    - Never raises on malformed or missing location strings.

    Limits: assumes US `City, ST` formatting when comma-separated.
    """
    remote_flag = bool(is_remote) if not (isinstance(is_remote, float) and pd.isna(is_remote)) else False
    text = _clean_str(raw_location)
    if not text:
        return ("Remote", "", "Remote") if remote_flag else ("", "", "Unknown")

    if "remote" in text.lower():
        remote_flag = True

    # 1. Split comma-separated parts ("Seattle, WA, US" -> city="Seattle", state="WA")
    parts = [p.strip() for p in text.split(",") if p.strip()]
    city = parts[0] if parts else text
    state = ""
    if len(parts) >= 2 and len(parts[1]) <= 20:
        state = parts[1].upper() if len(parts[1]) == 2 else parts[1]

    if city.lower() == "remote":
        return ("Remote", "", "Remote")

    display = f"{city}, {state}" if state else city
    if remote_flag and "remote" not in display.lower():
        display = f"{display} (Remote)"
    return (city, state, display)


def _to_float(value: Any) -> float | None:
    """Convert numeric or numeric-string value to float; returns None on invalid/NaN."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    text = str(value).strip().replace(",", "").replace("$", "")
    if not text or text.lower() in {"nan", "none", "null"}:
        return None
    try:
        num = float(text)
        return num if num > 0 else None
    except ValueError:
        return None


def annualize_salary(
    min_amount: Any, max_amount: Any, interval: Any
) -> tuple[float | None, float | None, str]:
    """Convert mixed salary intervals into annualized `(annual_min, annual_max, display)`.

    Contract:
    - Returns `(annual_min, annual_max, display_str)` where display is e.g.
      `'$95k–$125k'` or `'$110k'`, or `''` when both bounds are missing.
    - Handles `yearly`, `monthly` (*12), `weekly` (*52), `daily` (*260), `hourly` (*2080).

    Limits: treats missing/unknown interval as yearly when values >= 1000, hourly when
    values < 250, and monthly when 250 <= values < 1000.
    """
    low = _to_float(min_amount)
    high = _to_float(max_amount)
    if low is None and high is None:
        return (None, None, "")

    interval_str = _clean_str(interval).lower()
    sample = high or low or 0.0

    # 1. Determine annualization multiplier from explicit interval or magnitude heuristic
    if "hour" in interval_str or interval_str == "hr":
        mult = 2080.0
    elif "month" in interval_str or interval_str == "mo":
        mult = 12.0
    elif "week" in interval_str or interval_str == "wk":
        mult = 52.0
    elif "day" in interval_str or "daily" in interval_str:
        mult = 260.0
    elif "year" in interval_str or "annual" in interval_str or interval_str == "yr":
        mult = 1.0
    else:
        if sample < 250:
            mult = 2080.0
        elif sample < 1000:
            mult = 12.0
        else:
            mult = 1.0

    ann_min = round(low * mult, 2) if low is not None else None
    ann_max = round(high * mult, 2) if high is not None else None

    # 2. Ensure min <= max if both exist and inverted
    if ann_min is not None and ann_max is not None and ann_min > ann_max:
        ann_min, ann_max = ann_max, ann_min

    # 3. Format concise human-readable display string
    def _fmt(val: float) -> str:
        return f"${val / 1000:.0f}k" if val >= 1000 else f"${val:,.0f}"

    if ann_min is not None and ann_max is not None and abs(ann_min - ann_max) >= 1:
        display = f"{_fmt(ann_min)}–{_fmt(ann_max)}"
    else:
        val = ann_max if ann_max is not None else ann_min
        display = _fmt(val) if val is not None else ""

    return (ann_min, ann_max, display)


def assign_role_track(title: Any, is_internship: Any = False) -> str:
    """Assign a normalized role track extending `pipeline_enrichment.py` tracks.

    Contract:
    - Returns one of: `'Internship'`, `'Data / Analytics Eng'`, `'Data / BI Analytics'`,
      `'Data Science / ML'`, `'Product / BizOps'`, `'Software / Security'`, `'Other'`.
    - Never raises on missing/NaN titles.

    Limits: precedence checks Internship → Data Eng → Data Science → Data/BI →
    Product/BizOps → Software/Security so hybrid titles land in the most specific track.
    """
    t = _clean_str(title)
    intern_flag = bool(is_internship) if not (isinstance(is_internship, float) and pd.isna(is_internship)) else False
    if intern_flag or (t and INTERNSHIP_PATTERN.search(t)):
        return "Internship"
    if not t:
        return "Other"

    t_low = t.lower()
    if DATA_ENG_PATTERN.search(t_low):
        return "Data / Analytics Eng"
    if DATA_SCI_PATTERN.search(t_low):
        return "Data Science / ML"
    if DATA_BI_PATTERN.search(t_low) or re.search(TRACK_A_PATTERN, t_low):
        return "Data / BI Analytics"
    if re.search(TRACK_C_PATTERN, t_low):
        return "Product / BizOps"
    if re.search(SECURITY_PATTERN, t_low) or re.search(DEVELOPER_PATTERN, t_low) or re.search(TRACK_B_PATTERN, t_low):
        return "Software / Security"
    return "Other"


def is_staffing_or_ai_training(company: Any) -> bool:
    """Return True when employer matches known staffing or AI-training agencies."""
    text = _clean_str(company)
    return bool(text and STAFFING_AI_TRAINING_RE.search(text))


def build_recruiter_search_url(company_group: str) -> str:
    """Generate a deterministic LinkedIn People search URL for company recruiters/HMs.

    Contract:
    - Returns an HTTPS LinkedIn People search URL for non-empty companies; `''` for Unknown.
    """
    name = _clean_str(company_group)
    if not name or name.lower() == "unknown":
        return ""
    query = quote_plus(f'"{name}" (recruiter OR "talent acquisition" OR "hiring manager")')
    return f"https://www.linkedin.com/search/results/people/?keywords={query}"


def _normalize_title_key(title: Any) -> str:
    """Fold title to lowercase alphanumeric tokens for cross-site dedup matching."""
    text = _clean_str(title).lower()
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _is_direct_apply(apply_type: str, segment: str = "") -> bool:
    """Return True if apply_type or segment represents a direct ATS / Company Site apply."""
    at = _clean_str(apply_type).lower()
    seg = _clean_str(segment).lower()
    if any(tok in at for tok in EASY_APPLY_TOKENS):
        return False
    if any(tok in at for tok in DIRECT_APPLY_TOKENS):
        return True
    return "ats direct" in seg or "campus new grad" in seg


def _row_metadata_score(row: pd.Series) -> int:
    """Score a row so Direct ATS / Indeed / metadata-rich rows win cross-site dedup.

    Contract:
    - Higher score wins during deduplication. Direct ATS / Indeed / Company Site rows
      outrank LinkedIn Easy Apply rows even when titles and companies match.
    """
    score = 0
    apply_type = _clean_str(row.get("apply_type")).lower()
    site = _clean_str(row.get("site")).lower()

    if any(tok in apply_type for tok in DIRECT_APPLY_TOKENS):
        score += 12
    elif site == "indeed":
        score += 8
    elif site == "ats":
        score += 10
    elif "easy apply" in apply_type:
        score += 0
    else:
        score += 3

    if _clean_str(row.get("job_url_direct")):
        score += 4
    if _to_float(row.get("min_amount")) or _to_float(row.get("max_amount")):
        score += 4
    if _clean_str(row.get("date_posted")) or _clean_str(row.get("posted_at")):
        score += 3
    if _clean_str(row.get("emails")):
        score += 3
    for col in ("company_industry", "company_revenue", "company_num_employees"):
        if _clean_str(row.get(col)):
            score += 1
    return score


# ───────────────────────── Delta State Persistence ─────────────────────────


def load_seen_funnel_state(state_path: Path | None) -> dict[str, Any]:
    """Load persisted funnel seen-ID state from JSON.

    Contract:
    - Returns `{"ids": {...}, "keys": {...}}` on success or fresh empty structure if
      missing/corrupt/None. Never raises on unreadable JSON.
    """
    empty: dict[str, Any] = {"ids": {}, "keys": {}, "last_run": None}
    if state_path is None or not state_path.exists():
        return empty
    try:
        payload = json.loads(state_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            return empty
        return {
            "ids": payload.get("ids", {}) if isinstance(payload.get("ids"), dict) else {},
            "keys": payload.get("keys", {}) if isinstance(payload.get("keys"), dict) else {},
            "last_run": payload.get("last_run"),
        }
    except (OSError, json.JSONDecodeError):
        return empty


def save_seen_funnel_state(state_path: Path | None, state: dict[str, Any]) -> None:
    """Persist updated funnel seen-ID state to JSON.

    Contract:
    - Creates parent directories if missing and writes indented UTF-8 JSON.
    - No-op when `state_path` is None.
    """
    if state_path is None:
        return
    state_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "last_run": datetime.now().isoformat(timespec="seconds"),
        "ids": state.get("ids", {}),
        "keys": state.get("keys", {}),
    }
    state_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


# ───────────────────────── Cross-Site Deduplication ─────────────────────────


def _merge_duplicate_group(group: pd.DataFrame) -> pd.Series:
    """Collapse a duplicate group into its richest row while backfilling blanks."""
    ordered = group.sort_values(by="_meta_score", ascending=False)
    best = ordered.iloc[0].copy()

    # 1. Backfill any missing metadata fields from secondary duplicates
    if len(ordered) > 1:
        for col in METADATA_BACKFILL_COLUMNS:
            if col in ordered.columns and not _clean_str(best.get(col)):
                for _, cand in ordered.iloc[1:].iterrows():
                    val = _clean_str(cand.get(col))
                    if val:
                        best[col] = cand[col]
                        break

    # 2. Preserve union of segments, sites, and boolean freshness/new flags
    segments = sorted({_clean_str(v) for v in ordered.get("segment", []) if _clean_str(v)})
    sites = sorted({_clean_str(v) for v in ordered.get("site", []) if _clean_str(v)})
    best["segments"] = ", ".join(segments)
    best["sources"] = ", ".join(sites)
    best["is_new"] = bool(ordered["is_new"].any()) if "is_new" in ordered.columns else False
    best["is_fresh_post"] = (
        bool(ordered["is_fresh_post"].any()) if "is_fresh_post" in ordered.columns else False
    )
    return best


def deduplicate_funnel_rows(df: pd.DataFrame) -> pd.DataFrame:
    """Deduplicate jobs across sites and segments, preferring metadata-rich direct rows.

    Contract:
    - Returns a deduplicated DataFrame with merged `segments` and `sources`.
    - Pass 1 collapses identical canonical URLs; Pass 2 collapses matching
      `(company_group, normalized_title, city)` keys so Indeed/Company Site rows
      override sparser LinkedIn Easy Apply rows.
    """
    if df.empty:
        return df.copy()

    work = df.copy()
    work["_meta_score"] = work.apply(_row_metadata_score, axis=1)

    # 1. Pass 1: Collapse identical canonical URLs (preferring job_url_direct)
    work["_url_key"] = work.apply(
        lambda r: _canonical_job_url(r.get("job_url_direct")) or _canonical_job_url(r.get("job_url")),
        axis=1,
    )
    with_url = work[work["_url_key"] != ""]
    without_url = work[work["_url_key"] == ""]
    url_merged = (
        [_merge_duplicate_group(grp) for _, grp in with_url.groupby("_url_key", sort=False)]
        if not with_url.empty
        else []
    )
    pass1 = pd.DataFrame(url_merged) if url_merged else pd.DataFrame(columns=work.columns)
    if not without_url.empty:
        pass1 = pd.concat([pass1, without_url], ignore_index=True)

    # 2. Pass 2: Collapse cross-site duplicates on (company_group, normalized_title, city)
    pass1["_entity_key"] = pass1.apply(
        lambda r: f"{_clean_str(r.get('company_group')).lower()}::"
        f"{_normalize_title_key(r.get('title'))}::"
        f"{_clean_str(r.get('city')).lower()}",
        axis=1,
    )
    entity_merged = [_merge_duplicate_group(grp) for _, grp in pass1.groupby("_entity_key", sort=False)]
    result = pd.DataFrame(entity_merged).reset_index(drop=True)
    return result.drop(columns=["_meta_score", "_url_key", "_entity_key"], errors="ignore")


# ───────────────────────── Main Funnel Pipeline ─────────────────────────


def _compute_dedup_key(row: pd.Series) -> str:
    """Return stable deterministic dedup key for state tracking and Sheets upsert."""
    company = _clean_str(row.get("company_group")).lower()
    title = _normalize_title_key(row.get("title"))
    city = _clean_str(row.get("city")).lower()
    return f"{company}::{title}::{city}"


def _preferred_apply_url(row: pd.Series) -> str:
    """Prefer direct company/ATS URL over aggregator URL."""
    direct = _clean_str(row.get("job_url_direct"))
    if direct.startswith(("http://", "https://")):
        return direct
    fallback = _clean_str(row.get("job_url"))
    return fallback if fallback.startswith(("http://", "https://")) else ""


def _build_company_clusters(active_df: pd.DataFrame) -> pd.DataFrame:
    """Aggregate matching jobs by `company_group` to surface hiring clusters."""
    cols = [
        "Company",
        "Open Roles",
        "Tier 1 Count",
        "Role Tracks",
        "Salary Range",
        "Sample Roles",
        "Careers Link",
        "Recruiter Search",
    ]
    if active_df.empty:
        return pd.DataFrame(columns=cols)

    rows = []
    for company, grp in active_df.groupby("company_group", sort=False):
        open_roles = len(grp)
        tier1_count = int((grp["tier"] == "Tier 1").sum())
        tracks = ", ".join(sorted({t for t in grp["role_track"] if t}))

        mins = [v for v in grp["annual_min"].dropna().tolist() if v > 0]
        maxs = [v for v in grp["annual_max"].dropna().tolist() if v > 0]
        if mins or maxs:
            low = min(mins) if mins else min(maxs)
            high = max(maxs) if maxs else max(mins)
            sal_range = f"${low / 1000:.0f}k–${high / 1000:.0f}k" if high > low else f"${high / 1000:.0f}k"
        else:
            sal_range = ""

        sample_titles = list(dict.fromkeys(_clean_str(t) for t in grp["title"] if _clean_str(t)))[:4]
        sample_roles = " | ".join(sample_titles)
        careers = next((_clean_str(u) for u in grp["careers_url"] if _clean_str(u)), "")
        recruiter = build_recruiter_search_url(str(company))

        rows.append(
            {
                "Company": company,
                "Open Roles": open_roles,
                "Tier 1 Count": tier1_count,
                "Role Tracks": tracks,
                "Salary Range": sal_range,
                "Sample Roles": sample_roles,
                "Careers Link": careers,
                "Recruiter Search": recruiter,
            }
        )

    clusters = pd.DataFrame(rows)
    return clusters.sort_values(
        by=["Open Roles", "Tier 1 Count", "Company"], ascending=[False, False, True]
    ).reset_index(drop=True)


def _build_role_tracks_summary(active_df: pd.DataFrame) -> pd.DataFrame:
    """Summarize active funnel counts and annualized salary ranges by role track."""
    cols = ["Role Track", "Tier 1", "Tier 2", "Total", "Median Salary", "Salary Range"]
    if active_df.empty:
        return pd.DataFrame(columns=cols)

    rows = []
    for track, grp in active_df.groupby("role_track", sort=False):
        t1 = int((grp["tier"] == "Tier 1").sum())
        t2 = int((grp["tier"] == "Tier 2").sum())
        midpoints = []
        for lo, hi in zip(grp["annual_min"], grp["annual_max"]):
            if pd.notna(lo) and pd.notna(hi):
                midpoints.append((float(lo) + float(hi)) / 2.0)
            elif pd.notna(hi):
                midpoints.append(float(hi))
            elif pd.notna(lo):
                midpoints.append(float(lo))
        if midpoints:
            med = float(pd.Series(midpoints).median())
            lo_all = min(midpoints)
            hi_all = max(midpoints)
            median_str = f"${med / 1000:.0f}k"
            range_str = f"${lo_all / 1000:.0f}k–${hi_all / 1000:.0f}k" if hi_all > lo_all else median_str
        else:
            median_str = ""
            range_str = ""

        rows.append(
            {
                "Role Track": track,
                "Tier 1": t1,
                "Tier 2": t2,
                "Total": len(grp),
                "Median Salary": median_str,
                "Salary Range": range_str,
            }
        )

    return pd.DataFrame(rows).sort_values(
        by=["Tier 1", "Total", "Role Track"], ascending=[False, False, True]
    ).reset_index(drop=True)


def _format_tier_table(df: pd.DataFrame) -> pd.DataFrame:
    """Project enriched funnel DataFrame into the compact 11-column Excel view."""
    compact_cols = [
        "Tier",
        "Role Track",
        "Company",
        "Title",
        "Location",
        "Apply Type",
        "Posted",
        "Annualized Salary",
        "Apply Link",
        "Recruiter Search",
        "Outreach / Emails",
        "is_new",
        "is_fresh_post",
        "dedup_key",
    ]
    if df.empty:
        return pd.DataFrame(columns=compact_cols)

    out = pd.DataFrame(
        {
            "Tier": df["tier"],
            "Role Track": df["role_track"],
            "Company": df["company_group"],
            "Title": df["title"],
            "Location": df["location_display"],
            "Apply Type": df["apply_type"],
            "Posted": df["posted_display"],
            "Annualized Salary": df["salary_display"],
            "Apply Link": df["apply_url"],
            "Recruiter Search": df["recruiter_search_url"],
            "Outreach / Emails": df["emails_clean"],
            "is_new": df["is_new"],
            "is_fresh_post": df["is_fresh_post"],
            "dedup_key": df["dedup_key"],
        }
    )
    return out.reset_index(drop=True)


def _build_outreach_leads(active_df: pd.DataFrame) -> pd.DataFrame:
    """Extract active roles that have at least one scraped contact email."""
    cols = [
        "Tier",
        "Company",
        "Title",
        "Role Track",
        "Location",
        "Annualized Salary",
        "Emails",
        "Apply Link",
        "Recruiter Search",
    ]
    if active_df.empty:
        return pd.DataFrame(columns=cols)

    leads = active_df[active_df["emails_clean"] != ""].copy()
    if leads.empty:
        return pd.DataFrame(columns=cols)

    return pd.DataFrame(
        {
            "Tier": leads["tier"],
            "Company": leads["company_group"],
            "Title": leads["title"],
            "Role Track": leads["role_track"],
            "Location": leads["location_display"],
            "Annualized Salary": leads["salary_display"],
            "Emails": leads["emails_clean"],
            "Apply Link": leads["apply_url"],
            "Recruiter Search": leads["recruiter_search_url"],
        }
    ).reset_index(drop=True)


def process_funnel(
    segment_frames: dict[str, pd.DataFrame],
    *,
    seen_state_path: Path | None = None,
    reference_date: date | None = None,
) -> FunnelResult:
    """Run deterministic dedup, normalization, filtering, tiering, and lead generation.

    Contract:
    - Accepts `{segment_name: enriched_DataFrame}` from `run_pipeline.py`.
    - Returns `FunnelResult` with compact Excel tables, TUI feed records, and stats.
    - Updates `seen_state_path` when supplied so future runs accurately flag net-new roles.

    Limits: relies on `apply_type`, `is_new`, and `is_fresh_post` populated by
    `enrich_dataframe`, falling back safely when any optional column is absent.
    """
    today = reference_date or date.today()
    today_iso = today.isoformat()
    fresh_cutoff = today - timedelta(days=FRESH_POST_DAYS)

    # 1. Combine non-empty segment frames and attach segment provenance
    pieces: list[pd.DataFrame] = []
    for segment_name, frame in segment_frames.items():
        if frame is None or frame.empty:
            continue
        copy = frame.copy()
        copy["segment"] = segment_name
        pieces.append(copy)

    if not pieces:
        empty_tier = _format_tier_table(pd.DataFrame())
        return FunnelResult(
            tier1_df=empty_tier,
            tier2_df=empty_tier.copy(),
            all_active_df=empty_tier.copy(),
            company_clusters_df=_build_company_clusters(pd.DataFrame()),
            role_tracks_df=_build_role_tracks_summary(pd.DataFrame()),
            outreach_leads_df=_build_outreach_leads(pd.DataFrame()),
            excluded_df=pd.DataFrame(columns=["Reason", "Company", "Title", "Location", "Apply Type", "Apply Link"]),
            tui_feed_records=[],
            summary_stats={
                "tier1_count": 0,
                "tier2_count": 0,
                "net_new_tier1": 0,
                "outreach_leads": 0,
                "excluded_count": 0,
                "company_clusters": 0,
                "top_companies": [],
            },
        )

    combined = pd.concat(pieces, ignore_index=True, sort=False)

    # 2. Normalize company, location, salary, and role track before cross-site dedup
    combined["company_group"] = combined.get("company", pd.Series("", index=combined.index)).map(normalize_company)
    loc_tuples = [
        normalize_location(loc, rem)
        for loc, rem in zip(
            combined.get("location", pd.Series("", index=combined.index)),
            combined.get("is_remote", pd.Series(False, index=combined.index)),
        )
    ]
    combined["city"] = [t[0] for t in loc_tuples]
    combined["state"] = [t[1] for t in loc_tuples]
    combined["location_display"] = [t[2] for t in loc_tuples]

    # 3. Deduplicate across sites, keeping richest Direct ATS / Indeed metadata
    deduped = deduplicate_funnel_rows(combined)

    # 4. Compute annualized salary, role track, freshness, and deterministic lead links
    sal_tuples = [
        annualize_salary(lo, hi, iv)
        for lo, hi, iv in zip(
            deduped.get("min_amount", pd.Series(None, index=deduped.index)),
            deduped.get("max_amount", pd.Series(None, index=deduped.index)),
            deduped.get("interval", pd.Series("", index=deduped.index)),
        )
    ]
    deduped["annual_min"] = [t[0] for t in sal_tuples]
    deduped["annual_max"] = [t[1] for t in sal_tuples]
    deduped["salary_display"] = [t[2] for t in sal_tuples]

    deduped["role_track"] = [
        assign_role_track(title, intern)
        for title, intern in zip(
            deduped.get("title", pd.Series("", index=deduped.index)),
            deduped.get("is_internship", pd.Series(False, index=deduped.index)),
        )
    ]
    deduped["apply_url"] = deduped.apply(_preferred_apply_url, axis=1)
    deduped["recruiter_search_url"] = deduped["company_group"].map(build_recruiter_search_url)
    deduped["careers_url"] = deduped.apply(
        lambda r: _clean_str(r.get("company_url_direct")) or _clean_str(r.get("company_url")),
        axis=1,
    )
    deduped["emails_clean"] = deduped.get("emails", pd.Series("", index=deduped.index)).map(_clean_str)
    deduped["dedup_key"] = deduped.apply(_compute_dedup_key, axis=1)

    parsed_dates = deduped.get("date_posted", pd.Series(None, index=deduped.index)).map(parse_posted_date)
    deduped["posted_date_obj"] = parsed_dates
    deduped["posted_display"] = parsed_dates.map(lambda d: d.isoformat() if d else "")
    deduped["is_fresh_post"] = [
        bool(existing) or bool(d and d >= fresh_cutoff)
        for existing, d in zip(
            deduped.get("is_fresh_post", pd.Series(False, index=deduped.index)),
            parsed_dates,
        )
    ]

    # 5. Apply persistent delta tracking (net-new since previous funnel run)
    seen_state = load_seen_funnel_state(seen_state_path)
    seen_ids: dict[str, str] = dict(seen_state["ids"])
    seen_keys: dict[str, str] = dict(seen_state["keys"])
    has_prior_funnel = bool(seen_ids or seen_keys)

    net_new_flags: list[bool] = []
    first_seen_values: list[str] = []
    for _, row in deduped.iterrows():
        job_id = _clean_str(row.get("id"))
        dkey = str(row["dedup_key"])
        already_seen = (bool(job_id) and job_id in seen_ids) or (dkey in seen_keys)
        is_net_new = (not already_seen) if has_prior_funnel else bool(row.get("is_new", True))
        net_new_flags.append(is_net_new)
        first_seen = seen_keys.get(dkey) or (seen_ids.get(job_id) if job_id else None) or today_iso
        first_seen_values.append(first_seen)
        if job_id and job_id not in seen_ids:
            seen_ids[job_id] = first_seen
        if dkey not in seen_keys:
            seen_keys[dkey] = first_seen

    deduped["is_net_new"] = net_new_flags
    deduped["is_new"] = [
        bool(orig) or bool(nn)
        for orig, nn in zip(deduped.get("is_new", pd.Series(False, index=deduped.index)), net_new_flags)
    ]
    deduped["first_seen_at"] = first_seen_values

    if seen_state_path is not None:
        save_seen_funnel_state(seen_state_path, {"ids": seen_ids, "keys": seen_keys})

    # 6. Partition into Excluded (Staffing/AI-Training or Seniority) vs Active Funnel
    exclusion_reasons: list[str] = []
    for _, row in deduped.iterrows():
        if is_staffing_or_ai_training(row.get("company")) or is_staffing_or_ai_training(row.get("company_group")):
            exclusion_reasons.append("Staffing / AI-Training")
        elif is_senior_or_high_yoe(row.get("title", ""), row.get("description", "")):
            exclusion_reasons.append("Seniority (Senior/Staff/4+ YoE)")
        else:
            exclusion_reasons.append("")

    deduped["exclusion_reason"] = exclusion_reasons
    excluded_raw = deduped[deduped["exclusion_reason"] != ""].copy()
    eligible = deduped[(deduped["exclusion_reason"] == "") & (deduped["role_track"] != "Other")].copy()

    # 7. Assign Funnel Tiers (Tier 1 = Fresh + Direct ATS/Company Site; Tier 2 = Easy Apply / Secondary)
    tiers: list[str] = []
    for _, row in eligible.iterrows():
        direct = _is_direct_apply(row.get("apply_type", ""), row.get("segments", ""))
        fresh = bool(row.get("is_fresh_post", False))
        if direct and fresh and row["role_track"] != "Internship":
            tiers.append("Tier 1")
        else:
            tiers.append("Tier 2")
    eligible["tier"] = tiers

    # Sort by Tier asc, posted date desc, annualized max salary desc, company asc
    eligible["_date_sort"] = pd.to_datetime(eligible["posted_display"], errors="coerce")
    eligible["_sal_sort"] = pd.to_numeric(eligible["annual_max"], errors="coerce").fillna(-1.0)
    eligible = eligible.sort_values(
        by=["tier", "_date_sort", "_sal_sort", "company_group"],
        ascending=[True, False, False, True],
        na_position="last",
    ).drop(columns=["_date_sort", "_sal_sort"])

    tier1_raw = eligible[eligible["tier"] == "Tier 1"].copy()
    tier2_raw = eligible[eligible["tier"] == "Tier 2"].copy()

    # 8. Build compact views and TUI feed payload
    tier1_table = _format_tier_table(tier1_raw)
    tier2_table = _format_tier_table(tier2_raw)
    all_active_table = _format_tier_table(eligible)
    company_clusters = _build_company_clusters(eligible)
    role_tracks = _build_role_tracks_summary(eligible)
    outreach_leads = _build_outreach_leads(eligible)

    excluded_table = (
        pd.DataFrame(
            {
                "Reason": excluded_raw["exclusion_reason"],
                "Company": excluded_raw["company_group"],
                "Title": excluded_raw["title"],
                "Location": excluded_raw["location_display"],
                "Apply Type": excluded_raw.get("apply_type", ""),
                "Apply Link": excluded_raw["apply_url"],
            }
        ).reset_index(drop=True)
        if not excluded_raw.empty
        else pd.DataFrame(columns=["Reason", "Company", "Title", "Location", "Apply Type", "Apply Link"])
    )

    tui_records: list[dict[str, Any]] = []
    for _, row in eligible.iterrows():
        tui_records.append(
            {
                "dedup_key": row["dedup_key"],
                "id": _clean_str(row.get("id")),
                "tier": row["tier"],
                "role_track": row["role_track"],
                "company": row["company_group"],
                "company_raw": _clean_str(row.get("company")),
                "title": _clean_str(row.get("title")),
                "location": row["location_display"],
                "city": row["city"],
                "state": row["state"],
                "apply_type": _clean_str(row.get("apply_type")),
                "date_posted": row["posted_display"],
                "first_seen_at": row["first_seen_at"],
                "is_new": bool(row["is_new"]),
                "is_fresh_post": bool(row["is_fresh_post"]),
                "annual_min": None if pd.isna(row["annual_min"]) else float(row["annual_min"]),
                "annual_max": None if pd.isna(row["annual_max"]) else float(row["annual_max"]),
                "salary_display": row["salary_display"],
                "apply_url": row["apply_url"],
                "recruiter_search_url": row["recruiter_search_url"],
                "careers_url": row["careers_url"],
                "emails": row["emails_clean"],
                "company_industry": _clean_str(row.get("company_industry")),
                "company_num_employees": _clean_str(row.get("company_num_employees")),
                "company_revenue": _clean_str(row.get("company_revenue")),
                "sources": _clean_str(row.get("sources")),
                "segments": _clean_str(row.get("segments")),
            }
        )

    top_companies = [
        f"{row['Company']} ({row['Open Roles']})"
        for _, row in company_clusters.head(5).iterrows()
    ]
    summary_stats = {
        "tier1_count": len(tier1_raw),
        "tier2_count": len(tier2_raw),
        "net_new_tier1": int(tier1_raw["is_net_new"].sum()) if not tier1_raw.empty else 0,
        "outreach_leads": len(outreach_leads),
        "excluded_count": len(excluded_raw),
        "company_clusters": int((company_clusters["Open Roles"] >= 2).sum()) if not company_clusters.empty else 0,
        "top_companies": top_companies,
    }

    return FunnelResult(
        tier1_df=tier1_table,
        tier2_df=tier2_table,
        all_active_df=all_active_table,
        company_clusters_df=company_clusters,
        role_tracks_df=role_tracks,
        outreach_leads_df=outreach_leads,
        excluded_df=excluded_table,
        tui_feed_records=tui_records,
        summary_stats=summary_stats,
    )


def export_tui_feed(
    records: list[dict[str, Any]],
    output_path: Path,
    latest_path: Path | None = None,
) -> Path:
    """Write normalized Tier 1 & Tier 2 feed records to JSON.

    Contract:
    - Creates parent directories and writes UTF-8 JSON with `generated_at`, `count`,
      and `jobs` list.
    - When `latest_path` is provided, writes an identical copy there for CLI default lookup.
    """
    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "count": len(records),
        "jobs": records,
    }
    serialized = json.dumps(payload, indent=2)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(serialized, encoding="utf-8")
    if latest_path is not None:
        latest_path.parent.mkdir(parents=True, exist_ok=True)
        latest_path.write_text(serialized, encoding="utf-8")
    return output_path
