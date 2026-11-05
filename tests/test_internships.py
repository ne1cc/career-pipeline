from __future__ import annotations

import asyncio
from datetime import date

import pandas as pd
import pytest
import requests

from cockpit import db, internships
from cockpit.internships import (
    check_url_open,
    classify,
    internship_flags,
    parse_jobright,
    parse_simplify,
)
from cockpit.tui import FILTERS, JobCockpitApp


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "TMUX_CACHE", tmp_path / "tmux.cache")
    return tmp_path / "funnel.duckdb"


def frame(*rows: dict) -> pd.DataFrame:
    base = {"id": "x", "site": "linkedin", "title": "Software Engineer Intern", "company": "Stripe",
            "location": "Seattle, WA", "job_url": "https://l/1", "job_url_direct": None,
            "description": "We use FastAPI and Docker.", "date_posted": "2026-10-01"}
    return pd.DataFrame([{**base, **r} for r in rows])


# --- classifier ---------------------------------------------------------------

@pytest.mark.parametrize("title", [
    "Data Analyst Intern", "Summer 2027 Software Engineering Internship", "Data Science Co-op",
    "Summer Analyst - Risk", "Business Intelligence Interns",
])
def test_flags_detect_internship_titles(title):
    assert internship_flags(title)["is_internship"]


@pytest.mark.parametrize("title", ["Internal Tools Engineer", "International Analyst", "Senior Data Analyst"])
def test_flags_ignore_non_intern_titles(title):
    assert not internship_flags(title)["is_internship"]


def test_flags_use_job_type_when_title_has_no_intern_word():
    assert internship_flags("Analyst", job_type="internship")["is_internship"]
    assert not internship_flags("Analyst", job_type="fulltime")["is_internship"]


def test_flags_description_alone_does_not_make_an_internship():
    # Full-time JDs often say "you will mentor our interns".
    assert not internship_flags("Data Analyst", "You will mentor our interns.")["is_internship"]


def test_flags_season_and_year():
    f = internship_flags("Data Intern", "Summer 2027 program in Seattle")
    assert f["mentions_2027"] and not f["wrong_season"]
    assert internship_flags("Data Intern", "Fall 2027 term")["wrong_season"]
    assert internship_flags("Data Intern", "Summer 2026 cohort")["wrong_season"]
    # A posting that offers summer 2027 and also fall 2027 is still usable.
    assert not internship_flags("Data Intern", "Summer 2027 or Fall 2027")["wrong_season"]


def test_flags_return_to_school():
    assert internship_flags("Data Intern", "Must plan to return to school in fall.")["needs_return_to_school"]
    assert internship_flags("Data Intern", "Open to rising seniors")["needs_return_to_school"]
    assert not internship_flags("Data Intern", "Build dashboards")["needs_return_to_school"]


# --- undergraduate-only detection ---------------------------------------------

@pytest.mark.parametrize("description", [
    "Currently pursuing a Bachelor's degree in Computer Science.",
    "Must be enrolled in an undergraduate program.",
    "Open to rising juniors and seniors.",
    "We are looking for undergraduate students.",
    "Candidates should be working toward a bachelor's degree.",
])
def test_undergrad_only_text_signals(description):
    assert internship_flags("Data Intern", description)["undergrad_only"]


@pytest.mark.parametrize("description", [
    "Currently pursuing a Bachelor's or Master's degree.",
    "Undergraduate and graduate students are encouraged to apply.",
    "Pursuing a bachelor's degree; MBA candidates welcome.",
    "Open to PhD and undergraduate students.",
    "Enrolled in a graduate program such as MSBA or MSPA.",
    "Pursuing an M.S. or bachelor's degree.",
])
def test_undergrad_text_with_graduate_signal_is_kept(description):
    assert not internship_flags("Data Intern", description)["undergrad_only"]


@pytest.mark.parametrize("description", [
    "Bachelor's degree required. Strong SQL.",   # a master's student holds one too
    "Build dashboards with MS Excel and SQL.",   # "MS" here is Microsoft
    "Currently enrolled in a degree program.",   # no explicit level -> keep
    "Report to a Senior Data Analyst.",          # "senior" is a job level, not a class year
    "",
])
def test_no_explicit_undergrad_signal_is_kept(description):
    assert not internship_flags("Data Intern", description)["undergrad_only"]


def test_undergrad_title_signal():
    assert internship_flags("Undergraduate Data Intern", "")["undergrad_only"]


def test_undergrad_only_ignores_non_internships():
    assert not internship_flags("Data Analyst", "Pursuing a Bachelor's degree")["undergrad_only"]


@pytest.mark.parametrize("degrees, expected", [
    ("Bachelor's", True),
    ("Associate's, Bachelor's", True),
    ("Bachelor's, Certificate", True),
    ("Bachelor's, Master's", False),
    ("Master's", False),
    ("PhD", False),
    ("MBA", False),
    ("", False),
    (None, False),
])
def test_undergrad_only_from_structured_degrees(degrees, expected):
    assert internship_flags("Data Intern", "", degrees=degrees)["undergrad_only"] is expected


def test_structured_degrees_override_text():
    # The board lists Master's explicitly, so a stray "undergraduate" mention does not hide it.
    assert not internship_flags("Data Intern", "Undergraduate students", degrees="Bachelor's, Master's")["undergrad_only"]
    # And an explicit Bachelor's-only list wins even when the text never says so.
    assert internship_flags("Data Intern", "Build dashboards", degrees="Bachelor's")["undergrad_only"]


def test_flags_tolerate_missing_values():
    f = internship_flags(None, None)
    assert f == {"is_internship": False, "mentions_2027": False, "wrong_season": False,
                 "needs_return_to_school": False, "undergrad_only": False}
    assert not internship_flags(float("nan"), float("nan"))["is_internship"]


def test_classify_keeps_only_internships_and_flags_nothing_dropped_for_season():
    df = frame(
        {"title": "Data Intern", "job_url": "https://l/1", "description": "Fall 2027"},
        {"title": "Senior Data Analyst", "job_url": "https://l/2"},
    )
    out = classify(df)
    assert list(out["job_url"]) == ["https://l/1"]  # wrong-season rows stay, per design


def test_classify_handles_empty_frame():
    assert classify(pd.DataFrame()).empty


# --- Simplify -----------------------------------------------------------------

def _listing(**kw):
    base = {"source": "Simplify", "category": "AI/ML/Data", "company_name": "Acme", "id": "id-1",
            "title": "Data Analyst Intern", "active": True, "terms": ["Summer 2027"],
            "date_updated": 1793748649, "date_posted": 1793748580, "url": "https://acme.example/jobs/1",
            "locations": ["Seattle, WA", "Remote"], "company_url": "https://simplify.jobs/c/Acme",
            "is_visible": True, "sponsorship": "Other", "degrees": ["Master's"]}
    return {**base, **kw}


def test_parse_simplify_keeps_active_visible_matching_term_only():
    df = parse_simplify([
        _listing(id="keep"),
        _listing(id="closed", active=False),
        _listing(id="hidden", is_visible=False),
        _listing(id="fall", terms=["Fall 2027"]),
        _listing(id="multi", terms=["Fall 2027", "Summer 2027"], url="https://acme.example/jobs/2"),
    ])
    assert sorted(df["id"]) == ["keep", "multi"]


def test_parse_simplify_maps_to_ingest_columns():
    row = parse_simplify([_listing()]).iloc[0]
    assert row["site"] == "simplify"
    assert row["job_url"] == row["job_url_direct"] == "https://acme.example/jobs/1"
    assert row["company"] == "Acme" and row["title"] == "Data Analyst Intern"
    assert row["location"] == "Seattle, WA; Remote"
    assert bool(row["is_remote"]) is True
    assert row["date_posted"] == "2026-11-03"
    assert "Summer 2027" in row["description"]
    assert row["degrees"] == "Master's"


def test_parse_simplify_degrees_feed_the_undergrad_flag():
    df = parse_simplify([_listing(id="a", degrees=["Bachelor's"]), _listing(id="b", degrees=["Bachelor's", "Master's"], url="https://acme.example/jobs/2")])
    by_id = {r["id"]: r for _, r in df.iterrows()}
    assert internship_flags(by_id["a"]["title"], by_id["a"]["description"], degrees=by_id["a"]["degrees"])["undergrad_only"]
    assert not internship_flags(by_id["b"]["title"], by_id["b"]["description"], degrees=by_id["b"]["degrees"])["undergrad_only"]


def test_parse_simplify_empty_input_has_columns():
    df = parse_simplify([])
    assert df.empty and "job_url" in df.columns


# --- Jobright -----------------------------------------------------------------

JOBRIGHT_MD = """
| Company | Job Title | Location | Work Model | Date Posted |
| ----- | --------- |  --------- | ---- | ------- |
| **[Signet Jewelers](http://www.signetjewelers.com)** | **[Data Analytics Intern](https://jobright.ai/jobs/info/aaa?utm_source=git)** | Akron, OH, United States | Hybrid | Nov 03 |
| **[Jomboy](https://jomboymedia.com)** | **[Business Intelligence Intern - Spring 2027](https://jobright.ai/jobs/info/bbb?utm_source=git)** | Remote, United States | Remote | Dec 30 |
not a table row
"""


def test_parse_jobright_rows():
    df = parse_jobright(JOBRIGHT_MD, today=date(2026, 11, 3))
    assert list(df["company"]) == ["Signet Jewelers", "Jomboy"]
    first = df.iloc[0]
    assert first["title"] == "Data Analytics Intern"
    assert first["job_url"].startswith("https://jobright.ai/jobs/info/aaa")
    assert first["location"] == "Akron, OH, United States" and not first["is_remote"]
    assert first["date_posted"] == "2026-11-03"
    assert bool(df.iloc[1]["is_remote"]) is True
    # "Dec 30" cannot be in the future relative to Nov 3, so it belongs to last year.
    assert df.iloc[1]["date_posted"] == "2025-12-30"


def test_parse_jobright_no_table():
    assert parse_jobright("nothing here").empty


# --- open/closed check --------------------------------------------------------

class FakeResponse:
    def __init__(self, status=200, text=""):
        self.status_code, self.text = status, text


class FakeSession:
    def __init__(self, response=None, error=None):
        self.response, self.error, self.urls = response, error, []

    def get(self, url, **kwargs):
        self.urls.append(url)
        if self.error:
            raise self.error
        return self.response


@pytest.mark.parametrize("status", [404, 410])
def test_check_url_open_gone_is_closed(status):
    assert check_url_open("https://x/1", FakeSession(FakeResponse(status))) is False


@pytest.mark.parametrize("body", [
    "Sorry, this job is no longer available.",
    "No longer accepting applications",
    "The position has been filled",
    "This job has expired",
])
def test_check_url_open_closed_phrases(body):
    assert check_url_open("https://x/1", FakeSession(FakeResponse(200, body))) is False


def test_check_url_open_live_page_is_open():
    assert check_url_open("https://x/1", FakeSession(FakeResponse(200, "Apply now for Data Intern"))) is True


@pytest.mark.parametrize("response", [FakeResponse(403), FakeResponse(429), FakeResponse(503)])
def test_check_url_open_blocked_is_unknown_never_closed(response):
    assert check_url_open("https://x/1", FakeSession(response)) is None


def test_check_url_open_network_error_is_unknown():
    assert check_url_open("https://x/1", FakeSession(error=requests.ConnectionError("boom"))) is None
    assert check_url_open("https://x/1", FakeSession(error=requests.Timeout("slow"))) is None


# --- ingest plumbing ----------------------------------------------------------

def test_ingest_stores_flags_computed_from_title_and_description(db_path):
    df = frame(
        {"title": "Data Intern", "company": "A", "description": "Summer 2027. Plan to return to school.", "job_url": "https://l/a"},
        {"title": "Data Analyst", "company": "B", "job_url": "https://l/b"},
    )
    with db.session(db_path) as con:
        db.upsert_jobspy_frame(con, df)
        rows = {r[0]: r[1:] for r in con.execute(
            "SELECT company, is_internship, mentions_2027, wrong_season, needs_return_to_school, undergrad_only FROM jobs_master"
        ).fetchall()}
    assert rows["A"] == (True, True, False, True, False)
    assert rows["B"] == (False, False, False, False, False)


def test_ingest_uses_degrees_column_for_undergrad_only(db_path):
    df = frame(
        {"title": "Data Intern", "company": "BachOnly", "job_url": "https://l/a", "degrees": "Bachelor's"},
        {"title": "Data Intern", "company": "WithMasters", "job_url": "https://l/b", "degrees": "Bachelor's, Master's"},
        {"title": "Data Intern", "company": "NoDegrees", "job_url": "https://l/c", "degrees": None},
    )
    with db.session(db_path) as con:
        db.upsert_jobspy_frame(con, df)
        got = dict(con.execute("SELECT company, undergrad_only FROM jobs_master").fetchall())
    assert got == {"BachOnly": True, "WithMasters": False, "NoDegrees": False}


def test_reingest_backfills_flags_but_keeps_state(db_path):
    with db.session(db_path) as con:
        db.upsert_jobspy_frame(con, frame({"title": "Data Intern", "company": "A"}))
        con.execute("UPDATE jobs_master SET is_internship = NULL, status = 'APPLIED', is_open = FALSE")
        db.upsert_jobspy_frame(con, frame({"title": "Data Intern", "company": "A"}))
        assert con.execute("SELECT is_internship, status, is_open FROM jobs_master").fetchone() == (True, "APPLIED", False)


def test_migration_is_idempotent(db_path):
    with db.session(db_path):
        pass
    with db.session(db_path) as con:
        cols = {r[0] for r in con.execute("DESCRIBE jobs_master").fetchall()}
    assert {"is_internship", "mentions_2027", "wrong_season", "needs_return_to_school", "undergrad_only", "is_open", "last_verified_at"} <= cols


def test_fetch_jobs_intern_view_hides_non_interns_and_known_closed(db_path):
    df = frame(
        {"title": "Software Engineer Intern", "company": "OpenCo", "job_url": "https://l/1"},
        {"title": "Software Engineer Intern", "company": "ClosedCo", "job_url": "https://l/2"},
        {"title": "Software Engineer I", "company": "FullTimeCo", "job_url": "https://l/3"},
    )
    with db.session(db_path) as con:
        db.upsert_jobspy_frame(con, df)
        con.execute("UPDATE jobs_master SET is_open = FALSE WHERE company = 'ClosedCo'")
        rows = db.fetch_jobs(con, "INTERN")
    assert [r["company"] for r in rows] == ["OpenCo"]


def test_fetch_jobs_intern_view_hides_undergrad_only_but_other_views_keep_them(db_path):
    df = frame(
        {"title": "Software Engineer Intern", "company": "GradOk", "job_url": "https://l/1",
         "description": "Pursuing a Bachelor's or Master's degree. FastAPI."},
        {"title": "Software Engineer Intern", "company": "UndergradOnly", "job_url": "https://l/2",
         "description": "Open to rising seniors pursuing a bachelor's degree. FastAPI."},
    )
    with db.session(db_path) as con:
        db.upsert_jobspy_frame(con, df)
        assert [r["company"] for r in db.fetch_jobs(con, "INTERN")] == ["GradOk"]
        assert sorted(r["company"] for r in db.fetch_jobs(con, "QUEUED")) == ["GradOk", "UndergradOnly"]
        assert sorted(r["company"] for r in db.fetch_jobs(con, None)) == ["GradOk", "UndergradOnly"]


def test_fetch_jobs_returns_verification_timestamp_as_text(db_path):
    with db.session(db_path) as con:
        db.upsert_jobspy_frame(con, frame({}))
        con.execute("UPDATE jobs_master SET last_verified_at = CURRENT_TIMESTAMP")
        row = db.fetch_jobs(con, None)[0]
    assert isinstance(row["last_verified_at"], str)


# --- TUI ----------------------------------------------------------------------

def test_tui_has_intern_filter(db_path):
    assert "INTERN" in FILTERS
    with db.session(db_path) as con:
        db.upsert_jobspy_frame(con, frame(
            {"title": "Software Engineer Intern", "company": "InternCo", "job_url": "https://l/1"},
            {"title": "Software Engineer I", "company": "FullTimeCo", "job_url": "https://l/2"},
        ))

    async def drive() -> None:
        app = JobCockpitApp(db_path)
        async with app.run_test() as pilot:
            while FILTERS[app.filter_idx] != "INTERN":
                await pilot.press("f")
            assert [r["company"] for r in app.rows] == ["InternCo"]

    asyncio.run(drive())


# --- verify-open command ------------------------------------------------------

def _seed_for_verify(db_path):
    with db.session(db_path) as con:
        db.upsert_jobspy_frame(con, frame(
            {"title": "Software Engineer Intern", "company": "GoneCo", "job_url": "https://gone/1"},
            {"title": "Software Engineer Intern", "company": "LiveCo", "job_url": "https://live/1"},
            {"title": "Software Engineer Intern", "company": "BlockedCo", "job_url": "https://blocked/1"},
            {"title": "Software Engineer I", "company": "NotAnIntern", "job_url": "https://other/1"},
        ))


def _run_verify(monkeypatch, db_path, *args):
    from typer.testing import CliRunner
    from cockpit.cli import app as cli_app

    monkeypatch.setattr(db, "DB_PATH", db_path)
    results = {"https://gone/1": False, "https://live/1": True, "https://blocked/1": None}
    seen = []

    def fake_check(url, session=None):
        seen.append(url)
        return results[url]

    monkeypatch.setattr(internships, "check_url_open", fake_check)
    monkeypatch.setattr(internships, "HOST_DELAY_S", 0)
    res = CliRunner().invoke(cli_app, ["verify-open", *args])
    return res, seen


def test_verify_open_updates_internship_rows_only(db_path, monkeypatch):
    _seed_for_verify(db_path)
    res, seen = _run_verify(monkeypatch, db_path)
    assert res.exit_code == 0, res.output
    assert "https://other/1" not in seen
    with db.session(db_path) as con:
        state = {r[0]: r[1:] for r in con.execute(
            "SELECT company, is_open, last_verified_at IS NOT NULL FROM jobs_master").fetchall()}
    assert state["GoneCo"] == (False, True)
    assert state["LiveCo"] == (True, True)
    assert state["BlockedCo"] == (None, True)  # unknown stays unknown but is stamped
    assert state["NotAnIntern"] == (None, False)


def test_verify_open_dry_run_writes_nothing(db_path, monkeypatch):
    _seed_for_verify(db_path)
    res, _ = _run_verify(monkeypatch, db_path, "--dry-run")
    assert res.exit_code == 0, res.output
    with db.session(db_path) as con:
        assert con.execute("SELECT count(*) FROM jobs_master WHERE is_open IS NOT NULL OR last_verified_at IS NOT NULL").fetchone()[0] == 0


def test_verify_open_respects_limit(db_path, monkeypatch):
    _seed_for_verify(db_path)
    _, seen = _run_verify(monkeypatch, db_path, "--limit", "2")
    assert len(seen) == 2
