import runpy
import threading
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

import jobspy
from jobspy.indeed import Indeed
from jobspy.model import JobPost, JobResponse, ScraperInput, Site


def adapter(result=None, error=None, mutate=None):
    class FakeAdapter:
        def __init__(self, **kwargs):
            pass

        def scrape(self, query):
            if mutate:
                mutate(query)
            if error:
                raise RuntimeError(error)
            return JobResponse(jobs=result or [])

    return FakeAdapter


def listing():
    return JobPost(
        title="Analyst",
        company_name="Example",
        location=None,
        job_url="https://example.com/job",
        description="Analysis",
    )


def test_one_failed_board_preserves_successful_results(monkeypatch):
    monkeypatch.setattr(jobspy, "LinkedIn", adapter(result=[listing()]))
    monkeypatch.setattr(jobspy, "Google", adapter(error="blocked"))
    result = jobspy.scrape_jobs(site_name=["linkedin", "google"], search_term="Analyst")
    assert result["job_url"].tolist() == ["https://example.com/job"]
    assert result["site"].tolist() == ["linkedin"]


def test_all_board_errors_identify_failed_sources(monkeypatch):
    monkeypatch.setattr(jobspy, "LinkedIn", adapter(error="linkedin blocked"))
    monkeypatch.setattr(jobspy, "Google", adapter(error="google blocked"))
    with pytest.raises(RuntimeError) as error:
        jobspy.scrape_jobs(site_name=["linkedin", "google"])
    assert "linkedin" in str(error.value).lower()
    assert "google" in str(error.value).lower()


def test_board_limit_mutation_is_isolated(monkeypatch):
    mutated = threading.Event()
    observed = []

    def google_query(query):
        query.results_wanted = 900
        mutated.set()

    def linkedin_query(query):
        assert mutated.wait(timeout=2)
        observed.append(query.results_wanted)

    monkeypatch.setattr(jobspy, "Google", adapter(mutate=google_query))
    monkeypatch.setattr(jobspy, "LinkedIn", adapter(mutate=linkedin_query))
    jobspy.scrape_jobs(site_name=["google", "linkedin"], results_wanted=9999)
    assert observed == [9999]


def test_indeed_remote_request_keeps_date_and_remote_constraints():
    scraper = Indeed()
    scraper.scraper_input = ScraperInput(
        site_type=[Site.INDEED], search_term="Analyst", hours_old=48, is_remote=True
    )
    requests = []

    def post(url, **kwargs):
        requests.append(kwargs["json"]["query"])
        return SimpleNamespace(
            ok=True,
            json=lambda: {
                "data": {"jobSearch": {"results": [], "pageInfo": {"nextCursor": None}}}
            },
        )

    scraper.session = SimpleNamespace(post=post)
    scraper.api_country_code = "US"
    scraper._scrape_page(None)
    assert "DSQF7" in requests[0]


def test_remote_script_filters_explicit_nonremote_jobs(monkeypatch, tmp_path):
    queries = []

    def scrape(**kwargs):
        queries.append(kwargs)
        return pd.DataFrame(
            {
                "job_url": [
                    "https://example.com/remote",
                    "https://example.com/onsite",
                    "https://example.com/unknown",
                ],
                "title": ["Remote Analyst", "Onsite Analyst", "Unknown Analyst"],
                "is_remote": [True, False, None],
            }
        )

    monkeypatch.setattr(jobspy, "scrape_jobs", scrape)
    monkeypatch.setenv("JOBSPY_RESULTS_DIR", str(tmp_path))
    script = Path(jobspy.__file__).parent / "search_jobs_remote.py"
    runpy.run_path(str(script), run_name="__main__")
    result = pd.read_csv(tmp_path / "all_city_remote_jobs.csv")
    assert set(result["job_url"]) == {
        "https://example.com/remote",
        "https://example.com/unknown",
    }
    assert all("remote" in query["google_search_term"].lower() for query in queries)


def test_indeed_remote_recency_filters_old_postings():
    from datetime import datetime

    scraper = Indeed()
    scraper.base_url = "https://www.indeed.com"
    scraper.scraper_input = ScraperInput(
        site_type=[Site.INDEED], hours_old=48, is_remote=True
    )

    def raw_job(key, hours_ago):
        return {
            "key": key,
            "title": "Analyst",
            "employer": None,
            "description": {"html": "Remote analysis"},
            "attributes": [],
            "datePublished": int(
                (datetime.now().timestamp() - hours_ago * 3600) * 1000
            ),
            "compensation": {"baseSalary": None, "estimated": None},
            "location": {"countryCode": "US", "formatted": {"long": "Remote"}},
        }

    assert scraper._process_job(raw_job("old", 72)) is None
    assert scraper._process_job(raw_job("new", 1)).id == "in-new"


def test_indeed_remote_scan_continues_after_filtered_page(monkeypatch):
    scraper = Indeed()
    pages = iter([([], "next"), ([listing()], None)])
    monkeypatch.setattr(scraper, "_scrape_page", lambda cursor: next(pages))
    result = scraper.scrape(
        ScraperInput(site_type=[Site.INDEED], hours_old=48, is_remote=True)
    )
    assert len(result.jobs) == 1
