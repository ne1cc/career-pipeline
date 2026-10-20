import pandas as pd
import pytest
from pipeline_enrichment import (
    matches_fresher_track,
    matches_developer_security_track,
    is_senior_or_high_yoe,
    calculate_fresher_priority_score,
    calculate_developer_security_priority_score,
    deduplicate_action_items,
    build_action_items_sheet,
    build_developer_security_sheet,
    build_last_72_hours_sheet,
)


def test_matches_fresher_track():
    assert matches_fresher_track("Junior Data Analyst")
    assert matches_fresher_track("Analytics Engineer")
    assert matches_fresher_track("Full Stack Analytics Engineer")
    assert matches_fresher_track("Associate Product Manager")
    assert matches_fresher_track("BizOps Analyst")
    assert matches_fresher_track("Associate Software Engineer")
    assert matches_fresher_track("Strategy and Operations Analyst")
    assert matches_fresher_track("Software Engineer")
    assert not matches_fresher_track("Registered Nurse")
    assert not matches_fresher_track("Truck Driver")


def test_is_senior_or_high_yoe():
    # Senior title
    assert is_senior_or_high_yoe("Senior Data Analyst", "Great job 2 years exp")
    assert is_senior_or_high_yoe("Lead Analytics Engineer", "Entry level role")
    assert is_senior_or_high_yoe("Principal Product Manager", "")
    assert is_senior_or_high_yoe("Engineering Manager", "")
    assert is_senior_or_high_yoe("Data Analyst III", "")

    # High YOE description
    assert is_senior_or_high_yoe("Data Analyst", "Must have 6+ years of experience in SQL")
    assert is_senior_or_high_yoe("Business Analyst", "Requires 10+ yrs of data modeling")
    assert is_senior_or_high_yoe("Data Analyst", "Requires 5-7 years of experience")
    assert is_senior_or_high_yoe("Data Analyst", "Requires 6-10 yrs experience")
    assert is_senior_or_high_yoe("Data Analyst", "5 to 7 years in analytics")
    assert is_senior_or_high_yoe("Data Analyst", "5–7 years of experience")

    # Safe fresher roles
    assert not is_senior_or_high_yoe("Data Analyst", "Requires 1-2 years experience or new graduate")
    assert not is_senior_or_high_yoe("Associate Product Manager", "0-3 years experience")
    assert not is_senior_or_high_yoe("Analytics Engineer", "Bachelor's or Master's degree in quantitative field")


def test_priority_scoring():
    high_priority_row = pd.Series({
        "title": "New Grad Analytics Engineer",
        "is_new": True,
        "is_fresh_post": True,
        "apply_type": "Greenhouse Direct",
    })
    score = calculate_fresher_priority_score(high_priority_row)
    # is_new (5) + is_fresh (3) + explicit entry (3) + direct ATS (2) + Track A (2) = 15
    assert score >= 14


def test_data_and_software_tracks_receive_equal_base_priority():
    base = {"is_new": False, "is_fresh_post": False, "apply_type": "Company Site"}
    analytics = calculate_fresher_priority_score(pd.Series({**base, "title": "Data Analyst"}))
    software = calculate_fresher_priority_score(pd.Series({**base, "title": "Software Engineer"}))
    assert analytics == software


def test_last_72_hours_uses_timestamp_and_keeps_distinct_requisitions():
    from datetime import datetime, timezone

    now = datetime(2026, 10, 19, 12, tzinfo=timezone.utc)
    df = pd.DataFrame([
        {"title": "Data Analyst", "company": "Acme", "job_url": "https://jobs.example/1", "date_posted": "2026-10-19", "posted_at": "2026-10-19T10:00:00Z", "site": "Dice"},
        {"title": "Data Analyst", "company": "Acme", "job_url": "https://jobs.example/1", "date_posted": "2026-10-19", "posted_at": "2026-10-19T10:00:00Z", "site": "WWR"},
        {"title": "Data Analyst", "company": "Acme", "job_url": "https://jobs.example/2", "date_posted": "2026-10-19", "posted_at": "2026-10-19T10:00:00Z", "site": "ATS"},
        {"title": "Backend Engineer", "company": "Acme", "job_url": "https://jobs.example/3", "date_posted": "2026-10-16", "posted_at": "2026-10-16T11:59:59Z", "site": "ATS"},
        {"title": "Security Analyst", "company": "Acme", "job_url": "https://jobs.example/4", "date_posted": "2026-10-19", "posted_at": "2026-10-19T12:00:01Z", "site": "ATS"},
        {"title": "Junior Data Analyst", "company": "Acme", "job_url": "https://jobs.example/5", "date_posted": "2026-10-17", "site": "ATS"},
        {"title": "Junior Data Analyst", "company": "Acme", "job_url": "https://jobs.example/6", "date_posted": "", "site": "ATS"},
        {"title": "Data Engineer", "company": "Acme", "job_url": "https://jobs.example/7", "date_posted": "2026-10-16", "site": "ATS"},
    ])

    result = build_last_72_hours_sheet({"source": df}, reference_now=now)

    assert set(result["job_url"]) == {"https://jobs.example/1", "https://jobs.example/2"}
    duplicate = result[result["job_url"] == "https://jobs.example/1"].iloc[0]
    assert set(duplicate["sources"].split(", ")) == {"Dice", "WWR"}


def test_last_72_hours_includes_headers_when_no_jobs_match():
    result = build_last_72_hours_sheet({})
    assert result.empty
    assert "job_url" in result.columns
    assert "posted_at" in result.columns


def test_last_72_hours_preserves_distinct_requisition_query_ids():
    from datetime import datetime, timezone

    now = datetime(2026, 10, 19, 12, tzinfo=timezone.utc)
    df = pd.DataFrame([
        {"title": "Data Analyst", "company": "Acme", "job_url": f"https://jobs.example/apply?jobId={job_id}", "posted_at": "2026-10-19T10:00:00Z", "site": "ATS"}
        for job_id in ("123", "456")
    ])
    result = build_last_72_hours_sheet({"source": df}, reference_now=now)
    assert len(result) == 2


def test_action_items_filters_seniors_and_ranks():
    df = pd.DataFrame([
        {
            "title": "Senior Data Analyst",
            "description": "5+ years",
            "is_new": True,
            "is_fresh_post": True,
            "apply_type": "Company Site",
            "job_url": "http://1",
            "company": "A",
        },
        {
            "title": "Junior Data Analyst",
            "description": "0-1 years",
            "is_new": True,
            "is_fresh_post": True,
            "apply_type": "Greenhouse Direct",
            "job_url": "http://2",
            "company": "B",
        },
    ])
    result = build_action_items_sheet({"Test Segment": df})
    assert len(result) == 1
    assert result.iloc[0]["title"] == "Junior Data Analyst"


def test_action_items_handles_duplicate_indices():
    df = pd.DataFrame([
        {
            "title": "Senior Data Analyst",
            "description": "5+ years",
            "is_new": True,
            "is_fresh_post": True,
            "apply_type": "Company Site",
            "job_url": "http://1",
            "company": "A",
        },
        {
            "title": "Junior Data Analyst",
            "description": "0-1 years",
            "is_new": True,
            "is_fresh_post": True,
            "apply_type": "Greenhouse Direct",
            "job_url": "http://2",
            "company": "B",
        },
    ], index=[0, 0])
    result = build_action_items_sheet({"Test Segment": df})
    assert len(result) == 1
    assert result.iloc[0]["title"] == "Junior Data Analyst"


def test_deduplicate_action_items():
    df = pd.DataFrame([
        {"title": "Data Analyst", "company": "Acme Corp", "_score": 5, "job_url": "http://1"},
        {"title": "data analyst", "company": "Acme Corp.", "_score": 10, "job_url": "http://2"},
    ])
    deduped = deduplicate_action_items(df)
    assert len(deduped) == 1
    assert deduped.iloc[0]["_score"] == 10
    assert deduped.iloc[0]["job_url"] == "http://2"


def test_empty_action_items_sheet():
    res_empty_dict = build_action_items_sheet({})
    assert res_empty_dict.empty
    assert "segment" in res_empty_dict.columns

    res_empty_df = build_action_items_sheet({"Empty": pd.DataFrame()})
    assert res_empty_df.empty
    assert "segment" in res_empty_df.columns
    assert deduplicate_action_items(pd.DataFrame()).empty


def test_matches_developer_security_track():
    # Developer positive matches
    assert matches_developer_security_track("Junior Software Engineer")
    assert matches_developer_security_track("Associate Software Engineer")
    assert matches_developer_security_track("Software Developer")
    assert matches_developer_security_track("Full Stack Developer")
    assert matches_developer_security_track("Frontend Engineer")
    assert matches_developer_security_track("Backend Developer")
    assert matches_developer_security_track("Cloud Engineer")
    assert matches_developer_security_track("DevOps Engineer")
    assert matches_developer_security_track("Site Reliability Engineer")
    assert matches_developer_security_track("Embedded Software Engineer")
    assert matches_developer_security_track("Software Engineer I")

    # Security positive matches
    assert matches_developer_security_track("Cybersecurity Analyst")
    assert matches_developer_security_track("Information Security Analyst")
    assert matches_developer_security_track("Junior Security Engineer")
    assert matches_developer_security_track("SOC Analyst")
    assert matches_developer_security_track("Application Security Engineer")
    assert matches_developer_security_track("Cloud Security Engineer")

    # Exclusions (Senior / Management / Non-technical)
    assert not matches_developer_security_track("Senior Software Engineer")
    assert not matches_developer_security_track("Lead Cloud Architect")
    assert not matches_developer_security_track("Principal Security Engineer")
    assert not matches_developer_security_track("Engineering Manager")
    assert not matches_developer_security_track("Director of Security")
    assert not matches_developer_security_track("Business Development Representative")
    assert not matches_developer_security_track("Sales Development Representative")
    assert not matches_developer_security_track("Software Engineer", "Must have 7+ years of experience")
    assert not matches_developer_security_track("Registered Nurse")
    assert not matches_developer_security_track("Truck Driver")


def test_developer_security_scoring_and_sheet():
    df = pd.DataFrame([
        {
            "title": "Senior Security Engineer",
            "description": "5+ years",
            "is_new": True,
            "is_fresh_post": True,
            "apply_type": "Company Site",
            "job_url": "http://1",
            "company": "Corp A",
        },
        {
            "title": "Junior Software Engineer",
            "description": "0-1 years",
            "is_new": True,
            "is_fresh_post": True,
            "apply_type": "Greenhouse Direct",
            "job_url": "http://2",
            "company": "Corp B",
        },
        {
            "title": "Cybersecurity Analyst",
            "description": "Bachelor's degree or internship experience",
            "is_new": False,
            "is_fresh_post": True,
            "apply_type": "Lever Direct",
            "job_url": "http://3",
            "company": "Corp C",
        },
        {
            "title": "Sales Development Representative",
            "description": "Sales",
            "is_new": True,
            "is_fresh_post": True,
            "apply_type": "Company Site",
            "job_url": "http://4",
            "company": "Corp D",
        },
    ])
    result = build_developer_security_sheet({"Remote": df})
    # Senior & Sales Development should be filtered out
    assert len(result) == 2
    titles = result["title"].tolist()
    assert "Junior Software Engineer" in titles
    assert "Cybersecurity Analyst" in titles
    assert "Senior Security Engineer" not in titles
    assert "Sales Development Representative" not in titles

    # Junior Software Engineer should rank first due to is_new (5) + is_fresh (3) + junior tag (4)
    assert result.iloc[0]["title"] == "Junior Software Engineer"


def test_empty_developer_security_sheet():
    res_empty_dict = build_developer_security_sheet({})
    assert res_empty_dict.empty
    assert "segment" in res_empty_dict.columns

    res_empty_df = build_developer_security_sheet({"Empty": pd.DataFrame()})
    assert res_empty_df.empty
    assert "segment" in res_empty_df.columns


