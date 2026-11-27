"""Human-readable openpyxl formatting for Job_Report Application Funnel workbooks.

Intent: style summary funnel worksheets with dark-slate headers, frozen panes,
AutoFilters, clickable display-text hyperlinks, readable widths, and subtle
freshness/new row highlights while hiding raw segment tabs at the back.

Contract:
- `format_funnel_worksheet(ws, df, ...)` mutates `ws` in place and returns None.
- `hide_worksheet(ws)` sets `ws.sheet_state = "hidden"` without losing data.
- Strips internal helper columns (`is_new`, `is_fresh_post`, `dedup_key`) before
  writing if callers pass `prepare_display_df(df)` while preserving row highlight
  metadata via parallel flags.

Limits: openpyxl display-text hyperlinks require valid http(s) URLs; malformed URLs
are left as plain text. Excel sheet names are truncated to 31 characters by openpyxl.
"""

from __future__ import annotations

from typing import Sequence

import pandas as pd
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from pipeline_enrichment import FRESH_FILL, NEW_AND_FRESH_FILL, NEW_FILL

# ───────────────────────── Visual Palette ─────────────────────────

HEADER_FILL = PatternFill(start_color="1E293B", end_color="1E293B", fill_type="solid")
HEADER_FONT = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
BODY_FONT = Font(name="Calibri", size=10, color="0F172A")
LINK_FONT = Font(name="Calibri", size=10, color="1D4ED8", underline="single")
ZEBRA_FILL = PatternFill(start_color="F8FAFC", end_color="F8FAFC", fill_type="solid")

THIN_BORDER_SIDE = Side(style="thin", color="E2E8F0")
CELL_BORDER = Border(
    left=THIN_BORDER_SIDE,
    right=THIN_BORDER_SIDE,
    top=THIN_BORDER_SIDE,
    bottom=THIN_BORDER_SIDE,
)

# Column-specific display labels for compact clickable links
LINK_LABELS: dict[str, str] = {
    "Apply Link": "Apply →",
    "Recruiter Search": "Find Recruiters →",
    "Careers Link": "Careers →",
    "job_url": "Open Listing →",
    "job_url_direct": "Direct Apply →",
}

# Explicit width overrides for compact executive scanning
PREFERRED_WIDTHS: dict[str, int] = {
    "Tier": 10,
    "Role Track": 22,
    "Company": 22,
    "Title": 38,
    "Location": 22,
    "Apply Type": 20,
    "Posted": 13,
    "Annualized Salary": 18,
    "Apply Link": 15,
    "Recruiter Search": 19,
    "Careers Link": 15,
    "Outreach / Emails": 28,
    "Emails": 28,
    "Open Roles": 13,
    "Tier 1 Count": 14,
    "Role Tracks": 30,
    "Salary Range": 18,
    "Sample Roles": 50,
    "Reason": 28,
}

WRAP_COLUMNS = {"Title", "Sample Roles", "Role Tracks", "Outreach / Emails", "Emails"}
INTERNAL_HELPER_COLS = ("is_new", "is_fresh_post", "dedup_key")


def prepare_display_df(df: pd.DataFrame) -> tuple[pd.DataFrame, list[tuple[bool, bool]]]:
    """Separate display columns from internal highlight flags before writing to Excel.

    Contract:
    - Returns `(display_df, highlight_flags)` where `highlight_flags[i]` is
      `(is_new, is_fresh_post)` for row `i`.
    - Never mutates the input DataFrame.
    """
    if df.empty:
        display_cols = [c for c in df.columns if c not in INTERNAL_HELPER_COLS]
        return pd.DataFrame(columns=display_cols), []

    flags: list[tuple[bool, bool]] = []
    has_new = "is_new" in df.columns
    has_fresh = "is_fresh_post" in df.columns
    for _, row in df.iterrows():
        is_new = bool(row["is_new"]) if has_new and pd.notna(row["is_new"]) else False
        is_fresh = bool(row["is_fresh_post"]) if has_fresh and pd.notna(row["is_fresh_post"]) else False
        flags.append((is_new, is_fresh))

    display_cols = [c for c in df.columns if c not in INTERNAL_HELPER_COLS]
    return df[display_cols].fillna("").reset_index(drop=True), flags


def format_funnel_worksheet(
    ws,
    display_df: pd.DataFrame,
    *,
    highlight_flags: Sequence[tuple[bool, bool]] | None = None,
    tab_color: str | None = None,
) -> None:
    """Apply executive readability styling to an openpyxl worksheet.

    Contract:
    - Styles header row (`A1:...`), freezes `A2`, enables AutoFilter, turns URL/email
      columns into clickable display-text hyperlinks, applies row highlights/zebra
      striping, and sets readable column widths.
    - Handles empty DataFrames gracefully (styles header row only).
    """
    if tab_color:
        ws.sheet_properties.tabColor = tab_color

    max_col = ws.max_column or len(display_df.columns)
    max_row = ws.max_row or 1
    if max_col == 0:
        return

    # 1. Style header row and freeze pane below header
    ws.freeze_panes = "A2"
    ws.row_dimensions[1].height = 24
    headers: list[str] = []
    for col_idx in range(1, max_col + 1):
        cell = ws.cell(row=1, column=col_idx)
        headers.append(str(cell.value or ""))
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
        cell.alignment = Alignment(horizontal="left", vertical="center", wrap_text=True)
        cell.border = CELL_BORDER

    # 2. Enable AutoFilter across the full data range
    last_col_letter = get_column_letter(max_col)
    ws.auto_filter.ref = f"A1:{last_col_letter}{max(max_row, 1)}"

    # 3. Style body rows: hyperlinks, mailto links, fonts, borders, and freshness fills
    for row_idx in range(2, max_row + 1):
        data_idx = row_idx - 2
        row_fill = None
        if highlight_flags and data_idx < len(highlight_flags):
            is_new, is_fresh = highlight_flags[data_idx]
            if is_new and is_fresh:
                row_fill = NEW_AND_FRESH_FILL
            elif is_new:
                row_fill = NEW_FILL
            elif is_fresh:
                row_fill = FRESH_FILL
        if row_fill is None and row_idx % 2 == 1:
            row_fill = ZEBRA_FILL

        ws.row_dimensions[row_idx].height = 21
        for col_idx in range(1, max_col + 1):
            col_name = headers[col_idx - 1]
            cell = ws.cell(row=row_idx, column=col_idx)
            val = "" if cell.value is None else str(cell.value).strip()

            cell.font = BODY_FONT
            cell.border = CELL_BORDER
            cell.alignment = Alignment(
                horizontal="left",
                vertical="center",
                wrap_text=col_name in WRAP_COLUMNS,
            )
            if row_fill is not None:
                cell.fill = row_fill

            # Convert URLs into concise display-text hyperlinks
            if val.startswith(("http://", "https://")):
                cell.hyperlink = val
                cell.value = LINK_LABELS.get(col_name, "Open Link →")
                cell.font = LINK_FONT
            elif col_name in {"Emails", "Outreach / Emails"} and "@" in val:
                primary_email = val.split(",")[0].strip()
                cell.hyperlink = f"mailto:{primary_email}"
                cell.font = LINK_FONT

    # 4. Set crisp column widths (prefer curated widths, fallback to content autosize)
    for col_idx in range(1, max_col + 1):
        letter = get_column_letter(col_idx)
        col_name = headers[col_idx - 1]
        if col_name in PREFERRED_WIDTHS:
            ws.column_dimensions[letter].width = PREFERRED_WIDTHS[col_name]
            continue

        max_len = len(col_name)
        for row_idx in range(2, min(max_row + 1, 200)):
            val = ws.cell(row=row_idx, column=col_idx).value
            if val is not None:
                first_line = str(val).split("\n", 1)[0]
                max_len = max(max_len, len(first_line))
        ws.column_dimensions[letter].width = min(max(max_len + 3, 12), 55)


def hide_worksheet(ws) -> None:
    """Hide a raw or legacy worksheet while preserving all data for manual inspection."""
    ws.sheet_state = "hidden"
