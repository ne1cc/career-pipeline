"""Rolling Master Google Sheet sync for the Application Funnel.

Intent: upsert net-new Tier 1 & Tier 2 funnel rows into a persistent Google Sheet
while preserving user-managed tracking columns (`Status`, `Notes`, `Applied Date`)
and auto-archiving untouched `New` rows older than 14 days.

Contract:
- `sync_funnel_to_google_sheets(funnel, config, ...)` returns the Sheet URL string on
  success, or `None` when Google Sheets is not configured or fails soft.
- Never raises on missing credentials or transient Google API errors (fail-soft so
  local `.xlsx` generation and email delivery always succeed).
- `read_shortlisted_from_sheet(config, ...)` returns rows with `Status == 'Shortlist'`
  for on-demand AI Dossier generation (`cockpit dossier --source sheet`).

Limits: uses `gspread` Service Account JSON auth (configured via `pipeline_config.json`
or `GOOGLE_SHEETS_CREDENTIALS` / `GOOGLE_SHEETS_ID` env vars). Batch updates stay under
standard Sheets API quotas for <2,000 active rows.
"""

from __future__ import annotations

import os
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from funnel_processor import FunnelResult

STATUS_OPTIONS = ("New", "Shortlist", "Applied", "Outreach Sent", "Interview", "Skipped")
ARCHIVE_AGE_DAYS = 14

TIER1_SHEET_HEADERS = [
    "Status",
    "Notes",
    "Applied Date",
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
    "First Seen",
    "dedup_key",
]


def resolve_gsheets_config(config: dict[str, Any] | None = None, base_dir: Path | None = None) -> tuple[Path | None, str]:
    """Resolve Service Account credentials path and spreadsheet ID from config or env.

    Contract:
    - Returns `(creds_path, spreadsheet_id)`. Returns `(None, '')` when disabled or
      incomplete.
    """
    cfg = config or {}
    env_creds = os.environ.get("GOOGLE_SHEETS_CREDENTIALS", "").strip()
    env_sheet_id = os.environ.get("GOOGLE_SHEETS_ID", "").strip()

    enabled = cfg.get("google_sheets_enabled", bool(env_creds and env_sheet_id))
    if not enabled:
        return (None, "")

    creds_raw = env_creds or str(cfg.get("google_sheets_credentials_path", "") or "").strip()
    sheet_id = env_sheet_id or str(cfg.get("google_sheets_spreadsheet_id", "") or "").strip()
    if not creds_raw or not sheet_id:
        return (None, "")

    creds_path = Path(creds_raw)
    if not creds_path.is_absolute() and base_dir is not None:
        creds_path = base_dir / creds_path
    if not creds_path.exists():
        return (None, "")
    return (creds_path, sheet_id)


def _hyperlink_formula(url: str, label: str) -> str:
    """Wrap an HTTP(S) URL in a Sheets `=HYPERLINK(...)` formula."""
    clean = str(url or "").strip()
    if not clean.startswith(("http://", "https://")):
        return ""
    safe_url = clean.replace('"', '""')
    safe_label = label.replace('"', '""')
    return f'=HYPERLINK("{safe_url}", "{safe_label}")'


def _ensure_worksheet(spreadsheet: Any, title: str, rows: int = 200, cols: int = 20) -> Any:
    """Return existing worksheet by title, creating it if absent."""
    try:
        return spreadsheet.worksheet(title)
    except Exception:
        return spreadsheet.add_worksheet(title=title, rows=rows, cols=cols)


def merge_rolling_rows(
    existing_records: list[dict[str, Any]],
    incoming_jobs: list[dict[str, Any]],
    *,
    reference_date: date | None = None,
    archive_days: int = ARCHIVE_AGE_DAYS,
) -> tuple[list[list[str]], list[list[str]]]:
    """Pure merge logic for rolling upsert + 14-day archival of untouched `New` rows.

    Contract:
    - Returns `(active_rows_with_header, archived_rows_without_header)` formatted as
      2D string lists ready for `gspread.Worksheet.update`.
    - Net-new rows are placed at the top with `Status='New'`.
    - Existing rows keep their user-edited `Status`, `Notes`, and `Applied Date`.
    - Existing rows with `Status == 'New'` and `First Seen` older than `archive_days`
      are moved to `archived_rows`.
    """
    today = reference_date or date.today()
    today_iso = today.isoformat()
    cutoff = today - timedelta(days=archive_days)

    # 1. Index existing rows by dedup_key so manual Status/Notes are never clobbered
    existing_by_key: dict[str, dict[str, Any]] = {}
    for rec in existing_records:
        key = str(rec.get("dedup_key", "") or "").strip()
        if key:
            existing_by_key[key] = rec

    new_rows: list[list[str]] = []
    updated_existing_rows: list[list[str]] = []
    seen_incoming_keys: set[str] = set()

    # 2. Process incoming Tier 1 & Tier 2 records
    for job in incoming_jobs:
        dkey = str(job.get("dedup_key", "") or "").strip()
        if not dkey or dkey in seen_incoming_keys:
            continue
        seen_incoming_keys.add(dkey)

        prior = existing_by_key.get(dkey)
        status = str(prior.get("Status", "New") or "New").strip() if prior else "New"
        notes = str(prior.get("Notes", "") or "").strip() if prior else ""
        applied_date = str(prior.get("Applied Date", "") or "").strip() if prior else ""
        first_seen = (
            str(prior.get("First Seen", "") or "").strip()
            if prior and prior.get("First Seen")
            else str(job.get("first_seen_at", today_iso) or today_iso)
        )

        row_values = [
            status,
            notes,
            applied_date,
            str(job.get("tier", "") or ""),
            str(job.get("role_track", "") or ""),
            str(job.get("company", "") or ""),
            str(job.get("title", "") or ""),
            str(job.get("location", "") or ""),
            str(job.get("apply_type", "") or ""),
            str(job.get("date_posted", "") or ""),
            str(job.get("salary_display", "") or ""),
            _hyperlink_formula(str(job.get("apply_url", "") or ""), "Apply →"),
            _hyperlink_formula(str(job.get("recruiter_search_url", "") or ""), "Find Recruiters →"),
            str(job.get("emails", "") or ""),
            first_seen,
            dkey,
        ]
        if prior is None:
            new_rows.append(row_values)
        else:
            updated_existing_rows.append(row_values)

    # 3. Retain or archive older existing rows not present in today's scrape
    kept_older_rows: list[list[str]] = []
    archived_rows: list[list[str]] = []
    for dkey, rec in existing_by_key.items():
        if dkey in seen_incoming_keys:
            continue
        status = str(rec.get("Status", "New") or "New").strip()
        first_seen_str = str(rec.get("First Seen", "") or "").strip()
        row_values = [str(rec.get(h, "") or "") for h in TIER1_SHEET_HEADERS]

        should_archive = False
        if status == "New" and first_seen_str:
            try:
                seen_dt = date.fromisoformat(first_seen_str[:10])
                should_archive = seen_dt < cutoff
            except ValueError:
                should_archive = False

        if should_archive:
            archived_rows.append(row_values)
        else:
            kept_older_rows.append(row_values)

    active_matrix = [TIER1_SHEET_HEADERS, *new_rows, *updated_existing_rows, *kept_older_rows]
    return active_matrix, archived_rows


def _df_to_sheet_matrix(df: pd.DataFrame, link_cols: dict[str, str] | None = None) -> list[list[str]]:
    """Convert a DataFrame into a 2D list with optional Sheets HYPERLINK formulas."""
    if df.empty:
        return [list(df.columns)]
    link_map = link_cols or {}
    rows: list[list[str]] = [list(df.columns)]
    for _, row in df.iterrows():
        out_row: list[str] = []
        for col in df.columns:
            val = "" if pd.isna(row[col]) else str(row[col]).strip()
            if col in link_map and val.startswith(("http://", "https://")):
                out_row.append(_hyperlink_formula(val, link_map[col]))
            else:
                out_row.append(val)
        rows.append(out_row)
    return rows


def sync_funnel_to_google_sheets(
    funnel: FunnelResult,
    config: dict[str, Any] | None = None,
    *,
    base_dir: Path | None = None,
    reference_date: date | None = None,
) -> str | None:
    """Upsert daily funnel views into the rolling Google Sheet (fail-soft).

    Contract:
    - Returns the spreadsheet HTTPS URL on success.
    - Returns `None` without raising if credentials/ID are missing or API calls fail.
    """
    creds_path, sheet_id = resolve_gsheets_config(config, base_dir=base_dir)
    if creds_path is None or not sheet_id:
        print(" - Google Sheets sync skipped (not configured in pipeline_config.json / env).")
        return None

    try:
        import gspread

        client = gspread.service_account(filename=str(creds_path))
        spreadsheet = client.open_by_key(sheet_id)

        # 1. Rolling upsert on "Tier 1 Action" (preserves Status/Notes/Applied Date)
        ws_action = _ensure_worksheet(spreadsheet, "Tier 1 Action", rows=500, cols=len(TIER1_SHEET_HEADERS))
        existing_records = ws_action.get_all_records() if ws_action.row_count > 1 else []
        active_matrix, archived_rows = merge_rolling_rows(
            existing_records,
            funnel.tui_feed_records,
            reference_date=reference_date,
        )
        ws_action.clear()
        ws_action.update(range_name="A1", values=active_matrix, value_input_option="USER_ENTERED")
        ws_action.freeze(rows=1)

        # 2. Append aged-out untouched rows into "Archive"
        if archived_rows:
            ws_archive = _ensure_worksheet(spreadsheet, "Archive", rows=500, cols=len(TIER1_SHEET_HEADERS))
            if not ws_archive.get_all_values():
                ws_archive.update(range_name="A1", values=[TIER1_SHEET_HEADERS], value_input_option="USER_ENTERED")
            ws_archive.append_rows(archived_rows, value_input_option="USER_ENTERED")

        # 3. Refresh executive snapshot tabs (Company Clusters, Role Tracks, Direct Outreach Leads)
        snapshot_specs = [
            (
                "Company Clusters",
                funnel.company_clusters_df,
                {"Careers Link": "Careers →", "Recruiter Search": "Find Recruiters →"},
            ),
            ("Role Tracks", funnel.role_tracks_df, {}),
            (
                "Direct Outreach Leads",
                funnel.outreach_leads_df,
                {"Apply Link": "Apply →", "Recruiter Search": "Find Recruiters →"},
            ),
        ]
        for tab_name, df, link_cols in snapshot_specs:
            ws = _ensure_worksheet(spreadsheet, tab_name, rows=max(len(df) + 10, 50), cols=max(len(df.columns), 8))
            ws.clear()
            ws.update(
                range_name="A1",
                values=_df_to_sheet_matrix(df, link_cols=link_cols),
                value_input_option="USER_ENTERED",
            )
            ws.freeze(rows=1)

        sheet_url = f"https://docs.google.com/spreadsheets/d/{sheet_id}"
        print(f" - Google Sheets synced successfully: {sheet_url}")
        return sheet_url
    except Exception as exc:
        print(f"[!] Warning: Google Sheets sync skipped due to error: {exc}")
        return None


def read_shortlisted_from_sheet(
    config: dict[str, Any] | None = None,
    *,
    base_dir: Path | None = None,
    status_filter: str = "Shortlist",
) -> list[dict[str, Any]]:
    """Read rows marked with `status_filter` (default 'Shortlist') from the Google Sheet.

    Contract:
    - Returns a list of normalized job dicts matching `status_filter`.
    - Returns `[]` when Google Sheets is unconfigured or unreachable.
    """
    creds_path, sheet_id = resolve_gsheets_config(config, base_dir=base_dir)
    if creds_path is None or not sheet_id:
        return []
    try:
        import gspread

        client = gspread.service_account(filename=str(creds_path))
        spreadsheet = client.open_by_key(sheet_id)
        ws = spreadsheet.worksheet("Tier 1 Action")
        records = ws.get_all_records()
        target = status_filter.strip().lower()
        return [
            {
                "dedup_key": str(r.get("dedup_key", "") or ""),
                "tier": str(r.get("Tier", "") or ""),
                "role_track": str(r.get("Role Track", "") or ""),
                "company": str(r.get("Company", "") or ""),
                "title": str(r.get("Title", "") or ""),
                "location": str(r.get("Location", "") or ""),
                "apply_type": str(r.get("Apply Type", "") or ""),
                "date_posted": str(r.get("Posted", "") or ""),
                "salary_display": str(r.get("Annualized Salary", "") or ""),
                "apply_url": str(r.get("Apply Link", "") or ""),
                "recruiter_search_url": str(r.get("Recruiter Search", "") or ""),
                "emails": str(r.get("Outreach / Emails", "") or ""),
                "notes": str(r.get("Notes", "") or ""),
            }
            for r in records
            if str(r.get("Status", "") or "").strip().lower() == target
        ]
    except Exception:
        return []
