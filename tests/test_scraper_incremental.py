import csv
from pathlib import Path
import pandas as pd
import pytest

from jobspy.search_jobs_remote import flush_jobs_to_csv as flush_remote
from jobspy.search_jobs_seattle import flush_jobs_to_csv as flush_seattle
from jobspy.search_jobs_seattle_msba import flush_jobs_to_csv as flush_msba, filter_msba_dataframe


def test_flush_jobs_to_csv_empty(tmp_path: Path):
    out = tmp_path / "test.csv"
    assert flush_remote([], out) == 0
    assert not out.exists()


def test_flush_jobs_to_csv_atomic_write_and_dedup(tmp_path: Path):
    out = tmp_path / "test_jobs.csv"
    
    # Wave 1
    df1 = pd.DataFrame([
        {"title": "Software Engineer", "company": "Acme", "job_url": "https://example.com/1"},
        {"title": "Data Analyst", "company": "Beta", "job_url": "https://example.com/2"},
    ])
    count1 = flush_remote([df1], out)
    assert count1 == 2
    assert out.is_file()

    # Read and verify content
    read1 = pd.read_csv(out)
    assert len(read1) == 2
    # Verify sorted alphabetically by title
    assert list(read1["title"]) == ["Data Analyst", "Software Engineer"]

    # Wave 2: contains duplicate job_url and new row
    df2 = pd.DataFrame([
        {"title": "Data Analyst", "company": "Beta Updated", "job_url": "https://example.com/2"},
        {"title": "BI Developer", "company": "Gamma", "job_url": "https://example.com/3"},
    ])
    count2 = flush_remote([df1, df2], out)
    assert count2 == 3
    
    read2 = pd.read_csv(out)
    assert len(read2) == 3
    assert list(read2["title"]) == ["BI Developer", "Data Analyst", "Software Engineer"]


def test_msba_filter_and_flush(tmp_path: Path):
    out = tmp_path / "seattle_msba.csv"
    
    raw = pd.DataFrame([
        {
            "title": "Junior Business Analyst",
            "description": "Looking for entry level graduate with MS in Business Analytics or related.",
            "company": "Tech Corp",
            "job_url": "https://example.com/msba1",
            "date_posted": None,
        },
        {
            "title": "Senior Director of Engineering",
            "description": "Requires 15+ years of experience leading global teams.",
            "company": "Huge Corp",
            "job_url": "https://example.com/senior1",
            "date_posted": None,
        },
    ])
    filtered = filter_msba_dataframe(raw)
    assert len(filtered) == 1
    assert filtered.iloc[0]["title"] == "Junior Business Analyst"
    
    count = flush_msba([filtered], out)
    assert count == 1
    assert out.is_file()
