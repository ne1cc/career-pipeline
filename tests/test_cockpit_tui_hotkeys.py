"""Tests for Cockpit TUI hotkeys and popup toggle dismiss.

Intent:
- Ensure JobCockpitApp binds alt+shift+j and alt+j to quit action so the tmux popup
  can be dismissed by re-pressing the popup summon shortcut.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
import pandas as pd
import pytest

from cockpit import db
from cockpit.tui import JobCockpitApp


@pytest.fixture
def db_path(tmp_path, monkeypatch) -> Path:
    monkeypatch.setattr(db, "TMUX_CACHE", tmp_path / "tmux.cache")
    path = tmp_path / "funnel.duckdb"
    rows = [
        {
            "id": "1",
            "site": "linkedin",
            "title": "SWE",
            "company": "Acme",
            "location": "Remote",
            "job_url": "https://example.com/1",
            "job_url_direct": None,
            "description": "Python",
            "date_posted": "2026-10-01",
        }
    ]
    with db.session(path) as con:
        db.upsert_jobspy_frame(con, pd.DataFrame(rows))
    return path


def test_tui_quits_on_alt_shift_j(db_path: Path, tmp_path: Path) -> None:
    """Verify pressing Alt+Shift+J exits JobCockpitApp cleanly."""
    app = JobCockpitApp(db_path, export_dir=tmp_path / "dossiers")

    async def drive() -> None:
        async with app.run_test() as pilot:
            await pilot.press("alt+shift+j")

    asyncio.run(drive())
    assert app.return_code == 0


def test_tui_quits_on_alt_j(db_path: Path, tmp_path: Path) -> None:
    """Verify pressing Alt+J exits JobCockpitApp cleanly."""
    app = JobCockpitApp(db_path, export_dir=tmp_path / "dossiers")

    async def drive() -> None:
        async with app.run_test() as pilot:
            await pilot.press("alt+j")

    asyncio.run(drive())
    assert app.return_code == 0
