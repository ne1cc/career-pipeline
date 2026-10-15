from __future__ import annotations

import asyncio
import plistlib
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

import pandas as pd
import pytest

from cockpit import db, macos
from cockpit.cli import render_plist
from cockpit.models import FunnelState
from cockpit.normalizer import load_rules, norm_company, render_outreach, score_job
from cockpit.tui import JobCockpitApp


@pytest.fixture
def db_path(tmp_path, monkeypatch) -> Path:
    monkeypatch.setattr(db, "TMUX_CACHE", tmp_path / "tmux.cache")
    return tmp_path / "funnel.duckdb"


@pytest.fixture
def calls(monkeypatch) -> list[dict]:
    """Capture every subprocess.run the macOS layer makes instead of touching pbcopy/open."""
    recorded: list[dict] = []

    def fake_run(cmd, **kw):
        recorded.append({"cmd": cmd, "input": kw.get("input")})
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(macos.subprocess, "run", fake_run)
    return recorded


def frame(*rows: dict) -> pd.DataFrame:
    base = {"id": "x", "site": "linkedin", "title": "Software Engineer I", "company": "Stripe",
            "location": "Seattle, WA", "job_url": "https://l/1", "job_url_direct": None,
            "description": "We use FastAPI and Docker.", "date_posted": "2026-10-01"}
    return pd.DataFrame([{**base, **r} for r in rows])


def test_cross_board_dedup(db_path):
    df = frame({"company": "Stripe, Inc.", "site": "linkedin"}, {"company": "Stripe", "site": "indeed"})
    assert norm_company("Stripe, Inc.") == norm_company("Stripe") == "stripe"
    with db.session(db_path) as con:
        stats = db.upsert_jobspy_frame(con, df)
        assert con.execute("SELECT count(DISTINCT dedup_key), count(*) FROM jobs_master").fetchone() == (1, 1)
    assert stats["inserted"] == 1


def test_direct_url_wins(db_path):
    df = frame({"job_url_direct": "https://ats/direct"}, {"company": "Acme", "job_url_direct": ""})
    with db.session(db_path) as con:
        db.upsert_jobspy_frame(con, df)
        urls = dict(con.execute("SELECT company, apply_url FROM jobs_master").fetchall())
    assert urls == {"Stripe": "https://ats/direct", "Acme": "https://l/1"}


def test_reingest_preserves_applied(db_path):
    df = frame({})
    with db.session(db_path) as con:
        db.upsert_jobspy_frame(con, df)
        key = con.execute("SELECT dedup_key FROM jobs_master").fetchone()[0]
        db.transition_job_state(con, key, FunnelState.APPLIED, "test")
        again = db.upsert_jobspy_frame(con, df)
        status, applied_at = con.execute("SELECT status, applied_at IS NOT NULL FROM jobs_master").fetchone()
    assert again["inserted"] == 0 and status == "APPLIED" and applied_at


def test_transition_writes_audit_row(db_path):
    with db.session(db_path) as con:
        db.upsert_jobspy_frame(con, frame({}))
        key = con.execute("SELECT dedup_key FROM jobs_master").fetchone()[0]
        db.transition_job_state(con, key, FunnelState.SKIPPED, "test", "meh")
        row = con.execute("SELECT from_status, to_status, triggered_by, notes FROM state_transitions").fetchone()
    assert row == ("QUEUED", "SKIPPED", "test", "meh")


def test_scoring_penalty_and_boost():
    rules = load_rules()
    junior = score_job("Data Engineer I", "dbt and polars", rules)
    senior = score_job("Senior Data Engineer", "dbt and polars", rules)
    assert junior.archetype.value == "data_eng"
    assert junior.score - senior.score == 15.0 + 45.0
    assert "i" not in score_job("Rustacean Developer", "", rules).matched  # no substring matches


def test_pbcopy_gets_absolute_posix_path(monkeypatch, tmp_path, calls):
    monkeypatch.setenv("HOME", str(tmp_path))
    path = macos.copy_resume_path("~/resumes/data_eng_lakehouse_v1.pdf")
    expected = f"{tmp_path.resolve()}/resumes/data_eng_lakehouse_v1.pdf"
    assert path == expected and path.startswith("/") and "~" not in path
    assert calls == [{"cmd": ["/usr/bin/pbcopy"], "input": expected.encode()}]


def test_open_background_flag(calls):
    macos.open_url("https://x")
    macos.open_url("https://x", background=True)
    assert [c["cmd"] for c in calls] == [["/usr/bin/open", "https://x"], ["/usr/bin/open", "-g", "https://x"]]


def test_tmux_cache_updates_on_transition(db_path):
    with db.session(db_path) as con:
        db.upsert_jobspy_frame(con, frame({}))
        assert db.TMUX_CACHE.read_text() == "#[fg=green]Jobs: 0/15#[default] | Q:1 | H:0\n"
        key = con.execute("SELECT dedup_key FROM jobs_master").fetchone()[0]
        db.transition_job_state(con, key, FunnelState.APPLIED, "test")
    assert db.TMUX_CACHE.read_text() == "#[fg=green]Jobs: 1/15#[default] | Q:0 | H:0\n"


def test_y_copies_outreach_to_clipboard(db_path, calls):
    with db.session(db_path) as con:
        db.upsert_jobspy_frame(con, frame({}))
        job = db.fetch_jobs(con)[0]
    expected = render_outreach(job, load_rules())

    async def drive() -> None:
        app = JobCockpitApp(db_path)
        async with app.run_test() as pilot:
            await pilot.press("y")

    asyncio.run(drive())
    pb = [c for c in calls if c["cmd"] == ["/usr/bin/pbcopy"]]
    assert pb and pb[-1]["input"].decode() == expected
    assert "Stripe" in expected and "Software Engineer I" in expected and job["resume_archetype"] in expected


def test_install_agent_plist_is_valid_xml_with_absolute_paths():
    xml = render_plist(Path("/Users/alex/Github/ne1cc/Jobspy-Custom"), "/Users/alex/.local/bin/uv")
    ET.fromstring(xml)  # well-formed
    plist = plistlib.loads(xml.encode())
    cmd = plist["ProgramArguments"][-1]
    assert plist["Label"] == "com.jobcockpit.sync"
    assert plist["StartCalendarInterval"] == [{"Hour": 8, "Minute": 30}, {"Hour": 13, "Minute": 30}]
    assert plist["WorkingDirectory"].startswith("/") and "/Users/alex/.local/bin/uv run --no-project" in cmd and "python -m cockpit.cli ingest" in cmd
    assert plist["StandardOutPath"] == "/tmp/jobcockpit.out.log"
