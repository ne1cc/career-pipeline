import pandas as pd
import pytest
from pipeline_enrichment import (
    matches_fresher_track,
    is_senior_or_high_yoe,
    calculate_fresher_priority_score,
    deduplicate_action_items,
    build_action_items_sheet,
)


def test_matches_fresher_track():
    assert matches_fresher_track("Junior Data Analyst")
    assert matches_fresher_track("Analytics Engineer")
    assert matches_fresher_track("Full Stack Analytics Engineer")
    assert matches_fresher_track("Associate Product Manager")
    assert matches_fresher_track("BizOps Analyst")
    assert matches_fresher_track("Associate Software Engineer")
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

