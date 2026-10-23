from datetime import date
from pathlib import Path
import runpy
from threading import Event

import pandas as pd
import pytest

import jobspy
from jobspy.model import JobPost, JobResponse


def test_scrape_jobs_keeps_successful_board_when_peer_raises(monkeypatch):
    succeeded = Event()

    class GoodBoard:
        def __init__(self, **kwargs):
            pass

        def scrape(self, query):
            succeeded.set()
            return JobResponse(
                jobs=[
                    JobPost(
                        title="Data Analyst",
                        company_name="Example",
                        job_url="https://example.test/jobs/1",
                        location=None,
                    )
                ]
            )

    class BrokenBoard:
        def __init__(self, **kwargs):
            pass

        def scrape(self, query):
            assert succeeded.wait(2)
            raise RuntimeError("parser failed")

    monkeypatch.setattr(jobspy, "Indeed", GoodBoard)
    monkeypatch.setattr(jobspy, "LinkedIn", BrokenBoard)
    result = jobspy.scrape_jobs(site_name=["indeed", "linkedin"])

    assert result["job_url"].tolist() == ["https://example.test/jobs/1"]
    assert result.attrs["scrape_errors"] == {"linkedin": "parser failed"}


def test_scrape_jobs_raises_when_every_board_raises(monkeypatch):
    class BrokenBoard:
        def __init__(self, **kwargs):
            pass

        def scrape(self, query):
            raise ValueError("blocked")

    monkeypatch.setattr(jobspy, "Indeed", BrokenBoard)
    monkeypatch.setattr(jobspy, "LinkedIn", BrokenBoard)
    with pytest.raises(RuntimeError, match="All .*boards failed"):
        jobspy.scrape_jobs(site_name=["indeed", "linkedin"])


def test_board_limit_mutation_does_not_change_peer_query(monkeypatch):
    capped = Event()
    observed = []

    class CappingBoard:
        def __init__(self, **kwargs):
            pass

        def scrape(self, query):
            query.results_wanted = 900
            capped.set()
            return JobResponse(jobs=[])

    class ObservingBoard:
        def __init__(self, **kwargs):
            pass

        def scrape(self, query):
            assert capped.wait(2)
            observed.append(query.results_wanted)
            return JobResponse(jobs=[])

    monkeypatch.setattr(jobspy, "Google", CappingBoard)
    monkeypatch.setattr(jobspy, "Indeed", ObservingBoard)
    jobspy.scrape_jobs(site_name=["google", "indeed"], results_wanted=9999)
    assert observed == [9999]


WORKERS = [
    ("search_jobs_remote.py", "all_city_remote_jobs.csv"),
    ("search_jobs_seattle.py", "seattle_jobs.csv"),
    ("search_jobs_seattle_entry_level.py", "seattle_entry_level_jobs.csv"),
    ("search_jobs_west.py", "west_coast_jobs.csv"),
    ("search_jobs_seattle_msba.py", "seattle_msba_jobs.csv"),
    ("search_jobs_msba_west.py", "west_coast_msba_jobs.csv"),
]


@pytest.mark.parametrize("worker,output", WORKERS)
@pytest.mark.parametrize("partial_results", [False, True])
def test_worker_reports_failed_queries_and_keeps_partial_rows(
    monkeypatch,
    tmp_path,
    worker,
    output,
    partial_results,
):
    monkeypatch.setenv("JOBSPY_RESULTS_DIR", str(tmp_path))
    csv_path = tmp_path / output
    csv_path.write_text("previous output\n")

    def scrape(**kwargs):
        if not partial_results:
            raise RuntimeError("blocked")
        frame = pd.DataFrame(
            [
                {
                    "title": "Data Analyst",
                    "company": "Example",
                    "job_url": "https://example.test/jobs/1",
                    "date_posted": date.today().isoformat(),
                    "description": "",
                }
            ]
        )
        frame.attrs["scrape_errors"] = {"linkedin": "blocked"}
        return frame

    monkeypatch.setattr(jobspy, "scrape_jobs", scrape)
    script = Path(__file__).resolve().parents[1] / "jobspy" / worker
    with pytest.raises(SystemExit) as exc:
        runpy.run_path(str(script), run_name="__main__")
    assert exc.value.code == 1
    if partial_results:
        assert pd.read_csv(csv_path)["job_url"].tolist() == [
            "https://example.test/jobs/1"
        ]
    else:
        assert csv_path.read_text() == "previous output\n"
