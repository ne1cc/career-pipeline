"""Unit tests for Application Funnel processor, Excel formatter, Google Sheets sync, apply-batch, and dossier."""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import openpyxl
import pandas as pd
from docx import Document

from cockpit import db
from cockpit.apply_batch import filter_pending_jobs, is_autofill_supported, load_feed_jobs, run_apply_batch
from cockpit.dossier import generate_dossier
from cockpit.models import FunnelState
from excel_formatter import format_funnel_worksheet, hide_worksheet, prepare_display_df
from funnel_processor import (
    annualize_salary,
    assign_role_track,
    build_recruiter_search_url,
    export_tui_feed,
    is_staffing_or_ai_training,
    normalize_company,
    normalize_location,
    process_funnel,
)
from gsheets_sync import TIER1_SHEET_HEADERS, merge_rolling_rows, sync_funnel_to_google_sheets


def test_normalization_and_salary_annualization():
    assert normalize_company("Amazon Web Services (AWS)") == "Amazon"
    assert normalize_company("Amazon.com, Inc.") == "Amazon"
    assert normalize_company("Stripe, Inc.") == "Stripe"
    assert normalize_company("") == "Unknown"

    assert normalize_location("Seattle, WA, US", False) == ("Seattle", "WA", "Seattle, WA")
    assert normalize_location("Remote", True) == ("Remote", "", "Remote")
    assert normalize_location("Austin, TX", True) == ("Austin", "TX", "Austin, TX (Remote)")

    assert annualize_salary(50, 60, "hourly") == (104000.0, 124800.0, "$104k–$125k")
    assert annualize_salary(8000, 10000, "monthly") == (96000.0, 120000.0, "$96k–$120k")
    assert annualize_salary(110000, 130000, "yearly") == (110000.0, 130000.0, "$110k–$130k")
    assert annualize_salary(None, None, "") == (None, None, "")

    assert assign_role_track("Analytics Engineer") == "Data / Analytics Eng"
    assert assign_role_track("Junior Data Analyst") == "Data / BI Analytics"
    assert assign_role_track("Data Scientist I") == "Data Science / ML"
    assert assign_role_track("Data Analyst Intern") == "Internship"

    assert is_staffing_or_ai_training("Alignerr") is True
    assert is_staffing_or_ai_training("Robert Half") is True
    assert is_staffing_or_ai_training("Datadog") is False
    assert "linkedin.com/search/results/people/" in build_recruiter_search_url("Datadog")


def test_cross_site_dedup_prefers_indeed_direct_over_linkedin_easy_apply(tmp_path):
    today = date(2026, 11, 26)
    df = pd.DataFrame(
        [
            {
                "id": "li-100",
                "site": "linkedin",
                "apply_type": "LinkedIn Easy Apply",
                "is_new": True,
                "is_fresh_post": True,
                "title": "Junior Data Analyst",
                "company": "Amazon.com",
                "location": "Seattle, WA",
                "date_posted": "",
                "min_amount": None,
                "max_amount": None,
                "interval": "",
                "emails": "",
                "job_url": "https://www.linkedin.com/jobs/view/100",
                "job_url_direct": "",
            },
            {
                "id": "in-200",
                "site": "indeed",
                "apply_type": "Company Site",
                "is_new": True,
                "is_fresh_post": True,
                "title": "Junior Data Analyst",
                "company": "Amazon Web Services (AWS)",
                "location": "Seattle, WA",
                "date_posted": "2026-11-26",
                "min_amount": 45,
                "max_amount": 55,
                "interval": "hourly",
                "emails": "recruiting@amazon.com",
                "company_industry": "Tech",
                "job_url": "https://www.indeed.com/viewjob?jk=200",
                "job_url_direct": "https://boards.greenhouse.io/amazon/jobs/200",
            },
            {
                "id": "in-300",
                "site": "indeed",
                "apply_type": "Company Site",
                "is_new": True,
                "is_fresh_post": True,
                "title": "AI Data Trainer",
                "company": "Alignerr",
                "location": "Remote",
                "date_posted": "2026-11-26",
                "job_url": "https://alignerr.com/jobs/300",
            },
            {
                "id": "in-400",
                "site": "indeed",
                "apply_type": "Company Site",
                "is_new": True,
                "is_fresh_post": True,
                "title": "Principal Data Architect",
                "company": "Stripe",
                "location": "Seattle, WA",
                "date_posted": "2026-11-26",
                "job_url": "https://stripe.com/jobs/400",
            },
        ]
    )

    state_file = tmp_path / "seen_funnel_ids.json"
    result = process_funnel({"Seattle Jobs": df}, seen_state_path=state_file, reference_date=today)

    # Cross-site duplicate collapsed into 1 Tier 1 row with Indeed/Company Site metadata preserved
    assert len(result.tier1_df) == 1
    row = result.tier1_df.iloc[0]
    assert row["Company"] == "Amazon"
    assert row["Apply Type"] == "Company Site"
    assert row["Annualized Salary"] == "$94k–$114k"
    assert row["Apply Link"] == "https://boards.greenhouse.io/amazon/jobs/200"
    assert row["Outreach / Emails"] == "recruiting@amazon.com"

    # Outreach leads & excluded tabs populated accurately
    assert len(result.outreach_leads_df) == 1
    assert len(result.excluded_df) == 2
    assert set(result.excluded_df["Reason"]) == {"Staffing / AI-Training", "Seniority (Senior/Staff/4+ YoE)"}

    # Second run with same state marks net_new_tier1 == 0
    second = process_funnel({"Seattle Jobs": df}, seen_state_path=state_file, reference_date=today)
    assert second.summary_stats["net_new_tier1"] == 0


def test_excel_formatter_hyperlinks_autofilter_and_hidden_tabs(tmp_path):
    df = pd.DataFrame(
        [
            {
                "Tier": "Tier 1",
                "Role Track": "Data / BI Analytics",
                "Company": "Datadog",
                "Title": "Data Analyst",
                "Location": "Seattle, WA",
                "Apply Type": "Greenhouse Direct",
                "Posted": "2026-11-26",
                "Annualized Salary": "$100k–$120k",
                "Apply Link": "https://boards.greenhouse.io/datadog/jobs/1",
                "Recruiter Search": "https://www.linkedin.com/search/results/people/?keywords=Datadog",
                "Outreach / Emails": "talent@datadoghq.com",
                "is_new": True,
                "is_fresh_post": True,
                "dedup_key": "datadog::data analyst::seattle",
            }
        ]
    )
    display_df, flags = prepare_display_df(df)
    assert "dedup_key" not in display_df.columns
    assert flags == [(True, True)]

    out_xlsx = tmp_path / "funnel.xlsx"
    with pd.ExcelWriter(out_xlsx, engine="openpyxl") as writer:
        display_df.to_excel(writer, sheet_name="Funnel - Tier 1 Action", index=False)
        format_funnel_worksheet(writer.sheets["Funnel - Tier 1 Action"], display_df, highlight_flags=flags)
        display_df.to_excel(writer, sheet_name="Raw Hidden", index=False)
        hide_worksheet(writer.sheets["Raw Hidden"])

    wb = openpyxl.load_workbook(out_xlsx)
    ws = wb["Funnel - Tier 1 Action"]
    assert ws.freeze_panes == "A2"
    assert ws.auto_filter.ref is not None
    # Column 9 = Apply Link -> display text "Apply →" with hyperlink preserved
    assert ws.cell(row=2, column=9).value == "Apply →"
    assert ws.cell(row=2, column=9).hyperlink.target == "https://boards.greenhouse.io/datadog/jobs/1"
    assert ws.cell(row=2, column=11).hyperlink.target == "mailto:talent@datadoghq.com"
    assert wb["Raw Hidden"].sheet_state == "hidden"


def test_gsheets_rolling_merge_preserves_status_and_archives_stale():
    today = date(2026, 11, 26)
    old_date = (today - timedelta(days=20)).isoformat()
    recent_date = (today - timedelta(days=3)).isoformat()

    existing = [
        {
            "Status": "Shortlist",
            "Notes": "Strong SQL fit",
            "Applied Date": "",
            "Tier": "Tier 1",
            "Role Track": "Data / BI Analytics",
            "Company": "Datadog",
            "Title": "Data Analyst",
            "Location": "Seattle, WA",
            "Apply Type": "Greenhouse Direct",
            "Posted": recent_date,
            "Annualized Salary": "$100k",
            "Apply Link": "https://boards.greenhouse.io/datadog/jobs/1",
            "Recruiter Search": "",
            "Outreach / Emails": "",
            "First Seen": recent_date,
            "dedup_key": "datadog::data analyst::seattle",
        },
        {
            "Status": "New",
            "Notes": "",
            "Applied Date": "",
            "Tier": "Tier 1",
            "Role Track": "Data / BI Analytics",
            "Company": "OldCo",
            "Title": "BI Analyst",
            "Location": "Seattle, WA",
            "Apply Type": "Company Site",
            "Posted": old_date,
            "Annualized Salary": "",
            "Apply Link": "https://oldco.com/1",
            "Recruiter Search": "",
            "Outreach / Emails": "",
            "First Seen": old_date,
            "dedup_key": "oldco::bi analyst::seattle",
        },
    ]
    incoming = [
        {
            "dedup_key": "stripe::analytics engineer::seattle",
            "tier": "Tier 1",
            "role_track": "Data / Analytics Eng",
            "company": "Stripe",
            "title": "Analytics Engineer",
            "location": "Seattle, WA",
            "apply_type": "Greenhouse Direct",
            "date_posted": today.isoformat(),
            "salary_display": "$130k",
            "apply_url": "https://boards.greenhouse.io/stripe/jobs/9",
            "recruiter_search_url": "https://linkedin.com/search",
            "emails": "",
            "first_seen_at": today.isoformat(),
        },
        {
            "dedup_key": "datadog::data analyst::seattle",
            "tier": "Tier 1",
            "role_track": "Data / BI Analytics",
            "company": "Datadog",
            "title": "Data Analyst",
            "location": "Seattle, WA",
            "apply_type": "Greenhouse Direct",
            "date_posted": recent_date,
            "salary_display": "$100k",
            "apply_url": "https://boards.greenhouse.io/datadog/jobs/1",
            "recruiter_search_url": "",
            "emails": "",
            "first_seen_at": today.isoformat(),
        },
    ]

    active, archived = merge_rolling_rows(existing, incoming, reference_date=today)
    assert active[0] == TIER1_SHEET_HEADERS
    # Net-new Stripe row lands at top (row 1), Datadog preserves Shortlist + Notes (row 2), OldCo archived
    assert active[1][5] == "Stripe"
    assert active[1][0] == "New"
    assert active[2][5] == "Datadog"
    assert active[2][0] == "Shortlist"
    assert active[2][1] == "Strong SQL fit"
    assert len(archived) == 1
    assert archived[0][5] == "OldCo"

    # Unconfigured sync fails soft and returns None
    dummy_funnel = process_funnel({})
    assert sync_funnel_to_google_sheets(dummy_funnel, {"google_sheets_enabled": False}) is None


def test_apply_batch_and_dossier_end_to_end(tmp_path):
    feed_path = tmp_path / "tui_feed_2026_11_26.json"
    records = [
        {
            "dedup_key": "stripe::analytics engineer::seattle",
            "tier": "Tier 1",
            "role_track": "Data / Analytics Eng",
            "company": "Stripe",
            "title": "Analytics Engineer",
            "location": "Seattle, WA",
            "apply_type": "Greenhouse Direct",
            "date_posted": "2026-11-26",
            "annual_min": 120000.0,
            "annual_max": 145000.0,
            "salary_display": "$120k–$145k",
            "apply_url": "https://boards.greenhouse.io/stripe/jobs/12345",
            "recruiter_search_url": "https://www.linkedin.com/search/results/people/?keywords=Stripe",
            "emails": "hiring@stripe.com",
            "company_industry": "Fintech",
            "company_num_employees": "5000+",
            "company_revenue": "$10B+",
        },
        {
            "dedup_key": "workdayco::data analyst::seattle",
            "tier": "Tier 1",
            "role_track": "Data / BI Analytics",
            "company": "WorkdayCo",
            "title": "Data Analyst",
            "location": "Seattle, WA",
            "apply_type": "Workday Direct",
            "date_posted": "2026-11-26",
            "annual_min": 95000.0,
            "annual_max": 110000.0,
            "salary_display": "$95k–$110k",
            "apply_url": "https://workdayco.wd1.myworkdayjobs.com/en-US/jobs/1",
            "recruiter_search_url": "https://www.linkedin.com/search/results/people/?keywords=WorkdayCo",
            "emails": "",
        },
    ]
    export_tui_feed(records, feed_path)

    loaded = load_feed_jobs(feed_path, tier="Tier 1", limit=10)
    assert len(loaded) == 2
    assert is_autofill_supported(loaded[0]["apply_url"]) is True
    assert is_autofill_supported(loaded[1]["apply_url"]) is False

    # Seed DuckDB and exercise hybrid apply-batch (Playwright autofill on job 1, browser open on job 2)
    test_db = tmp_path / "test_funnel.duckdb"
    with db.session(test_db) as con:
        assert len(filter_pending_jobs(loaded, con)) == 2

    responses = iter(["f", "a", "o", "b"])
    autofilled: list[str] = []
    opened: list[str] = []
    counts = run_apply_batch(
        feed_path=feed_path,
        tier="Tier 1",
        limit=10,
        prompt_fn=lambda _: next(responses),
        autofill_fn=lambda url, _res: autofilled.append(url),
        open_url_fn=lambda url: opened.append(url),
        db_path=test_db,
    )
    assert counts["applied"] == 1
    assert counts["saved"] == 1
    assert autofilled == ["https://boards.greenhouse.io/stripe/jobs/12345"]
    assert opened == ["https://workdayco.wd1.myworkdayjobs.com/en-US/jobs/1"]

    # Generate consolidated Dossier (.md + .docx) from Tier 1 feed deterministically
    md_path, docx_path = generate_dossier(
        source="tier1",
        out_dir=tmp_path / "dossiers",
        use_llm=False,
        feed_path=feed_path,
        report_date=date(2026, 11, 26),
    )
    assert md_path.exists()
    assert docx_path.exists()
    md_content = md_path.read_text(encoding="utf-8")
    assert "Executive Market Snapshot" in md_content
    assert "Stripe" in md_content
    assert "hiring@stripe.com" in md_content
    doc = Document(str(docx_path))
    full_text = "\n".join(p.text for p in doc.paragraphs)
    assert "Job Application Dossier" in full_text
    assert "Stripe" in full_text
