from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

import pandas as pd
import pytest
from rich.text import Text
from textual.widgets import DataTable

from cockpit import db, macos
from cockpit.tui import FILTERS, JobCockpitApp

COMPANIES = ["Stripe", "Acme", "Globex", "Initech"]


@pytest.fixture
def db_path(tmp_path, monkeypatch) -> Path:
    monkeypatch.setattr(db, "TMUX_CACHE", tmp_path / "tmux.cache")
    path = tmp_path / "funnel.duckdb"
    rows = [
        {"id": str(i), "site": "linkedin", "title": "Software Engineer I", "company": c,
         "location": "Seattle, WA", "job_url": f"https://example.com/{c.lower()}", "job_url_direct": None,
         "description": "We use FastAPI and Docker.", "date_posted": "2026-10-01"}
        for i, c in enumerate(COMPANIES)
    ]
    with db.session(path) as con:
        db.upsert_jobspy_frame(con, pd.DataFrame(rows))
    return path


@pytest.fixture
def opened(monkeypatch) -> list[tuple[str, bool]]:
    urls: list[tuple[str, bool]] = []
    monkeypatch.setattr(macos, "open_url", lambda url, background=False: urls.append((url, background)))
    monkeypatch.setattr(macos, "copy_resume_path", lambda p: None)
    return urls


def _app(db_path, tmp_path) -> JobCockpitApp:
    return JobCockpitApp(db_path, export_dir=tmp_path / "dossiers")


def _companies(app: JobCockpitApp) -> list[str]:
    return [r["company"] for r in app.rows]


def _flag(db_path, company: str, column: str = "dismissed"):
    with db.session(db_path) as con:
        return con.execute(f"SELECT {column} FROM jobs_master WHERE company = ?", [company]).fetchone()[0]


def test_set_dismissed_round_trip_and_filter(db_path):
    with db.session(db_path) as con:
        keys = [r["dedup_key"] for r in db.fetch_jobs(con)][:2]
        db.set_dismissed(con, keys, True)
        assert {r["dedup_key"] for r in db.fetch_jobs(con, "DISMISSED")} == set(keys)
        assert len(db.fetch_jobs(con, "QUEUED")) == 4  # dismissed rows stay visible in normal views
        db.set_dismissed(con, keys, False)
        assert db.fetch_jobs(con, "DISMISSED") == []


def test_x_dismisses_dims_and_cursor_skips_it(db_path, tmp_path):
    async def drive() -> None:
        app = _app(db_path, tmp_path)
        async with app.run_test() as pilot:
            first, second, third = _companies(app)[:3]
            await pilot.press("j", "x")  # dismiss row 1; cursor advances to row 2
            await pilot.pause()
            table = app.query_one(DataTable)
            assert _flag(db_path, second) is True
            assert table.cursor_row == 2
            cells = table.get_row_at(1)
            assert all(isinstance(c, Text) and "dim" in str(c.style) for c in cells)
            await pilot.press("k")  # row 1 is dismissed → lands on row 0
            await pilot.pause()
            assert table.cursor_row == 0
            await pilot.press("j")  # skips dismissed row 1
            await pilot.pause()
            assert table.cursor_row == 2 and _companies(app)[2] == third and first != third

    asyncio.run(drive())


def test_dismissed_view_allows_undismiss(db_path, tmp_path):
    with db.session(db_path) as con:
        db.set_dismissed(con, [db.fetch_jobs(con)[0]["dedup_key"]], True)

    async def drive() -> None:
        app = _app(db_path, tmp_path)
        async with app.run_test() as pilot:
            while FILTERS[app.filter_idx] != "DISMISSED":
                await pilot.press("f")
            await pilot.pause()
            assert len(app.rows) == 1
            await pilot.press("x")
            await pilot.pause()
            assert app.rows == []

    asyncio.run(drive())
    with db.session(db_path) as con:
        assert con.execute("SELECT count(*) FROM jobs_master WHERE dismissed").fetchone()[0] == 0


def test_space_marks_and_escape_clears(db_path, tmp_path):
    async def drive() -> None:
        app = _app(db_path, tmp_path)
        async with app.run_test() as pilot:
            await pilot.press("space", "space")  # marks rows 0 and 1, cursor advances each time
            await pilot.pause()
            assert app.marked == {r["dedup_key"] for r in app.rows[:2]}
            assert app.query_one(DataTable).cursor_row == 2
            await pilot.press("escape")
            await pilot.pause()
            assert app.marked == set()

    asyncio.run(drive())


def test_batch_open_opens_every_marked_url_in_background(db_path, tmp_path, opened):
    async def drive() -> None:
        app = _app(db_path, tmp_path)
        async with app.run_test() as pilot:
            await pilot.press("space", "j", "space")  # space marks row 0 and advances to row 1; j moves to row 2 and marks it
            await pilot.press("o")
            await pilot.pause()
            assert len(opened) == len(app.marked) == 2
            assert all(bg for _, bg in opened)

    asyncio.run(drive())


def test_batch_skip_and_dismiss_apply_to_all_marked(db_path, tmp_path):
    async def drive() -> None:
        app = _app(db_path, tmp_path)
        async with app.run_test() as pilot:
            a, b = _companies(app)[:2]
            await pilot.press("space", "space", "S")
            await pilot.pause()
            assert {_flag(db_path, c, "status") for c in (a, b)} == {"SKIPPED"}
            assert app.marked == set()
            c, d = _companies(app)[:2]
            app.query_one(DataTable).move_cursor(row=0)  # skipped rows left the view; cursor was clamped to the end
            await pilot.press("space", "space", "x")
            await pilot.pause()
            assert _flag(db_path, c) is True and _flag(db_path, d) is True

    asyncio.run(drive())


def test_batch_reeval_requeues_all_marked(db_path, tmp_path):
    async def drive() -> None:
        app = _app(db_path, tmp_path)
        async with app.run_test() as pilot:
            await pilot.press("space", "space", "I")
            await pilot.pause()

    asyncio.run(drive())
    with db.session(db_path) as con:
        assert con.execute("SELECT count(*) FROM jobs_master WHERE intel_status = 'pending'").fetchone()[0] == 2


def _status(db_path, company):
    with db.session(db_path) as con:
        return con.execute("SELECT is_saved, intel_status FROM jobs_master WHERE company = ?", [company]).fetchone()


def test_G_queues_dossiers_for_marked_rows_only(db_path, monkeypatch):
    kicks = []
    monkeypatch.setattr(JobCockpitApp, "_kick_intel", lambda self: kicks.append(1))

    async def drive():
        app = JobCockpitApp(db_path)
        async with app.run_test() as pilot:
            before = len(kicks)  # the app may kick the worker on start
            await pilot.press("space", "j", "space", "G")
            await pilot.pause()
            assert not app.marked
            assert len(kicks) == before + 1

    asyncio.run(drive())
    saved = [c for c in COMPANIES if _status(db_path, c)[0]]
    assert len(saved) == 2 and all(_status(db_path, c)[1] == "pending" for c in saved)


def test_G_without_marks_uses_cursor_row(db_path, monkeypatch):
    monkeypatch.setattr(JobCockpitApp, "_kick_intel", lambda self: None)

    async def drive():
        async with JobCockpitApp(db_path).run_test() as pilot:
            await pilot.press("G")
            await pilot.pause()

    asyncio.run(drive())
    assert sum(bool(_status(db_path, c)[0]) for c in COMPANIES) == 1


def test_G_big_batch_needs_second_press(db_path, monkeypatch):
    monkeypatch.setattr(JobCockpitApp, "_kick_intel", lambda self: None)
    monkeypatch.setattr("cockpit.tui.BULK_CONFIRM_OVER", 1)

    async def drive():
        async with JobCockpitApp(db_path).run_test() as pilot:
            await pilot.press("space", "j", "space", "G")
            await pilot.pause()
            assert not any(_status(db_path, c)[0] for c in COMPANIES)  # warned only
            await pilot.press("G")
            await pilot.pause()

    asyncio.run(drive())
    assert sum(bool(_status(db_path, c)[0]) for c in COMPANIES) == 2
