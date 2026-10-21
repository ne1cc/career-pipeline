from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from typer.testing import CliRunner

from cockpit import pipeline_monitor
from cockpit.cli import app as cli_app


def test_update_pipeline_state_atomic(tmp_path):
    s1 = pipeline_monitor.update_pipeline_state(tmp_path, phase="SCRAPING", pid=123)
    assert s1["phase"] == "SCRAPING"
    assert s1["pid"] == 123
    assert "updated_at" in s1

    # Merge update
    s2 = pipeline_monitor.update_pipeline_state(tmp_path, phase="EXCEL_BUILD", excel_path="/tmp/x.xlsx")
    assert s2["phase"] == "EXCEL_BUILD"
    assert s2["pid"] == 123
    assert s2["excel_path"] == "/tmp/x.xlsx"


def test_is_pid_alive():
    assert pipeline_monitor.is_pid_alive(os.getpid()) is True
    assert pipeline_monitor.is_pid_alive(None) is False
    assert pipeline_monitor.is_pid_alive(-1) is False
    assert pipeline_monitor.is_pid_alive(99999999) is False


def test_tail_file(tmp_path):
    f = tmp_path / "test.log"
    assert pipeline_monitor.tail_file(f) == ""
    assert pipeline_monitor.tail_file(None) == ""

    f.write_text("line 1\n\nline 2\nline 3\n\n", encoding="utf-8")
    assert pipeline_monitor.tail_file(f, max_lines=1) == "line 3"
    assert pipeline_monitor.tail_file(f, max_lines=2) == "line 2 | line 3"


def test_formatting_helpers():
    assert pipeline_monitor.format_duration(None) == "—"
    assert pipeline_monitor.format_duration(45) == "45s"
    assert pipeline_monitor.format_duration(125) == "02m 05s"
    assert pipeline_monitor.format_duration(3665) == "01h 01m 05s"

    assert pipeline_monitor.format_bytes(None) == "—"
    assert pipeline_monitor.format_bytes(500) == "500 B"
    assert pipeline_monitor.format_bytes(2048) == "2.0 KB"
    assert pipeline_monitor.format_bytes(10485760) == "10.00 MB"


def test_get_pipeline_state_parsing(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline_monitor, "is_pid_alive", lambda pid: pid == 100)

    # Setup state
    pipeline_monitor.update_pipeline_state(
        tmp_path,
        pid=100,
        phase="SCRAPING",
        tasks={
            "search_jobs_remote.py": {
                "status": "running",
                "pid": 100,
                "started_at": "2026-10-21T03:00:00",
            },
            "search_jobs_seattle.py": {
                "status": "running",
                "pid": 999,  # dead PID -> should auto-switch to failed or success
                "started_at": "2026-10-21T03:00:00",
            },
        },
    )

    state = pipeline_monitor.get_pipeline_state(tmp_path)
    assert state["is_active"] is True
    assert state["phase"] == "SCRAPING"
    assert state["tasks"]["search_jobs_remote.py"]["status"] == "running"
    # search_jobs_seattle has dead pid and no csv output -> marked failed
    assert state["tasks"]["search_jobs_seattle.py"]["status"] == "failed"


def test_render_pipeline_dashboard_no_crash(tmp_path):
    for phase in ("IDLE", "SCRAPING", "EXCEL_BUILD", "EMAIL", "INGEST", "COMPLETED", "FAILED"):
        state = {
            "pid": 1234,
            "is_active": phase in ("SCRAPING", "EXCEL_BUILD", "EMAIL", "INGEST"),
            "phase": phase,
            "started_at": "2026-10-21T03:00:00",
            "duration_sec": 120.0,
            "tasks": {
                "search_jobs_remote.py": {
                    "status": "success",
                    "pid": None,
                    "duration_sec": 60.0,
                    "csv_size": 10240,
                    "latest_log": "Fetched 50 jobs",
                }
            },
        }
        rendered = pipeline_monitor.render_pipeline_dashboard(state, is_live=True)
        assert rendered is not None


def test_cli_pipeline_status(tmp_path, monkeypatch):
    monkeypatch.setattr("cockpit.cli.REPO_ROOT", tmp_path)
    runner = CliRunner()

    res = runner.invoke(cli_app, ["pipeline", "--once"])
    assert res.exit_code == 0
    assert "JobSpy Pipeline Monitor" in res.stdout
    assert "Parallel Scraper Tasks" in res.stdout


def test_cli_pipeline_logs(tmp_path, monkeypatch):
    monkeypatch.setattr("cockpit.cli.REPO_ROOT", tmp_path)
    cron_log = tmp_path / "pipeline_cron.log"
    cron_log.write_text("cron line 1\ncron line 2\n")

    runner = CliRunner()
    res = runner.invoke(cli_app, ["pipeline", "logs"])
    assert res.exit_code == 0
    assert "pipeline_cron.log" in res.stdout
    assert "cron line 2" in res.stdout

    # Test named scraper log
    logs_dir = tmp_path / "jobspy" / "scraped jobs" / "logs" / "2026_10"
    logs_dir.mkdir(parents=True)
    seattle_log = logs_dir / "search_jobs_seattle.log"
    seattle_log.write_text("seattle scrape line\n")

    res2 = runner.invoke(cli_app, ["pipeline", "logs", "seattle"])
    assert res2.exit_code == 0
    assert "search_jobs_seattle.log" in res2.stdout
    assert "seattle scrape line" in res2.stdout


def test_cli_pipeline_run_launches_process(tmp_path, monkeypatch):
    monkeypatch.setattr("cockpit.cli.REPO_ROOT", tmp_path)
    mock_popen = MagicMock()
    mock_popen.pid = 4321
    monkeypatch.setattr(pipeline_monitor, "launch_pipeline", lambda repo: mock_popen)

    called_monitor = []
    monkeypatch.setattr(pipeline_monitor, "monitor_loop", lambda repo, once: called_monitor.append(once))

    runner = CliRunner()
    res = runner.invoke(cli_app, ["pipeline", "run"])
    assert res.exit_code == 0
    assert "Master pipeline process spawned (PID: 4321)" in res.stdout
    assert called_monitor == [False]
