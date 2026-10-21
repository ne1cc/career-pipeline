from __future__ import annotations

import os
from datetime import datetime
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
    assert "Career Pipeline Monitor" in res.stdout
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


def test_resolve_pipeline_python(tmp_path, monkeypatch):
    # 1. Tests env var override
    fake_py = tmp_path / "fake_python"
    fake_py.write_text("#!/bin/sh\nexit 0\n")
    fake_py.chmod(0o755)
    monkeypatch.setenv("JOBSPY_PYTHON", str(fake_py))
    assert pipeline_monitor.resolve_pipeline_python(tmp_path) == str(fake_py)

    # 2. Tests fallback when env var not set
    monkeypatch.delenv("JOBSPY_PYTHON", raising=False)
    resolved = pipeline_monitor.resolve_pipeline_python(tmp_path)
    assert resolved is not None
    assert Path(resolved).exists()


def test_count_csv_rows(tmp_path):
    from run_parallel import count_csv_rows

    # Missing file
    assert count_csv_rows(tmp_path / "nonexistent.csv") == 0

    # Empty file
    empty = tmp_path / "empty.csv"
    empty.write_text("")
    assert count_csv_rows(empty) == 0

    # Header only
    hdr = tmp_path / "hdr.csv"
    hdr.write_text("id,title,company\n")
    assert count_csv_rows(hdr) == 0

    # Normal rows
    normal = tmp_path / "normal.csv"
    normal.write_text("id,title,company\n1,dev,co\n2,analyst,co\n")
    assert count_csv_rows(normal) == 2

    # Multiline quoted rows
    multiline = tmp_path / "multiline.csv"
    multiline.write_text('id,title,description\n1,dev,"line1\nline2\nline3"\n2,analyst,"desc\nwith\nbreaks"\n')
    assert count_csv_rows(multiline) == 2


def test_audit_pipeline_health_critical_and_warning(tmp_path):
    repo_root = tmp_path
    results_dir = tmp_path / "results"
    results_dir.mkdir()

    # Pre-populate yield_history with 7 days of high yields
    history_file = pipeline_monitor.get_yield_history_file(repo_root)
    history_file.parent.mkdir(parents=True, exist_ok=True)
    past_runs = [
        {
            "date": f"2026-10-{10+i:02d}",
            "timestamp": f"2026-10-{10+i:02d}T08:00:00",
            "tasks": {name: 100 for name in pipeline_monitor.SCRAPER_NAMES},
            "sites": {"linkedin": 300, "indeed": 200, "ats": 100},
        }
        for i in range(7)
    ]
    pipeline_monitor.save_yield_history(repo_root, {"runs": past_runs})

    # Test 1: Critical when task failed or 0 rows
    task_states_critical = {
        "search_jobs_remote.py": {"status": "failed", "row_count": 0},
        "search_jobs_seattle.py": {"status": "success", "row_count": 100},
    }
    audit1 = pipeline_monitor.audit_pipeline_health(repo_root, results_dir, task_states_critical)
    assert audit1["health"] == "CRITICAL"
    crit_tasks = [a["task"] for a in audit1["anomalies"] if a["severity"] == "CRITICAL"]
    assert "search_jobs_remote.py" in crit_tasks

    # Test 2: Warning when yield < 25% of 7-day median
    # Seattle yielded 10 rows (10 < 0.25 * 100 = 25)
    csv_file = results_dir / "seattle_jobs.csv"
    csv_file.write_text("id,site,title\n1,linkedin,eng\n2,indeed,eng\n3,ats,eng\n")

    task_states_warning = {
        name: {"status": "success", "row_count": 100} for name in pipeline_monitor.SCRAPER_NAMES
    }
    task_states_warning["search_jobs_seattle.py"] = {"status": "success", "row_count": 10}

    audit2 = pipeline_monitor.audit_pipeline_health(repo_root, results_dir, task_states_warning)
    assert audit2["health"] == "WARNING"
    warn_tasks = [a["task"] for a in audit2["anomalies"] if a.get("task")]
    assert "search_jobs_seattle.py" in warn_tasks

    # Test 3: Site warning when key site drops to 0
    csv_file.write_text("id,site,title\n1,other,eng\n2,indeed,eng\n3,ats,eng\n")
    task_states_healthy_tasks = {
        name: {"status": "success", "row_count": 100} for name in pipeline_monitor.SCRAPER_NAMES
    }
    audit3 = pipeline_monitor.audit_pipeline_health(repo_root, results_dir, task_states_healthy_tasks)
    site_warns = [a["site"] for a in audit3["anomalies"] if a.get("site")]
    assert "linkedin" in site_warns


def test_yield_history_retention_and_dedup(tmp_path):
    repo_root = tmp_path
    results_dir = tmp_path / "results"
    results_dir.mkdir()

    # Add 20 daily runs to test retention to 14 days
    past_runs = [
        {
            "date": f"2026-09-{i:02d}",
            "timestamp": f"2026-09-{i:02d}T08:00:00",
            "tasks": {name: 50 for name in pipeline_monitor.SCRAPER_NAMES},
            "sites": {"linkedin": 50},
        }
        for i in range(1, 21)
    ]
    pipeline_monitor.save_yield_history(repo_root, {"runs": past_runs})

    task_states = {
        name: {"status": "success", "row_count": 60} for name in pipeline_monitor.SCRAPER_NAMES
    }
    pipeline_monitor.audit_pipeline_health(repo_root, results_dir, task_states)
    history = pipeline_monitor.load_yield_history(repo_root)
    assert len(history["runs"]) == 14

    # Test same-day dedup keeps higher yield
    today_str = datetime.now().strftime("%Y-%m-%d")
    today_run = next(r for r in history["runs"] if r["date"] == today_str)
    assert today_run["tasks"]["search_jobs_seattle.py"] == 60

    # Run with higher yield for today
    task_states_higher = {
        name: {"status": "success", "row_count": 90} for name in pipeline_monitor.SCRAPER_NAMES
    }
    pipeline_monitor.audit_pipeline_health(repo_root, results_dir, task_states_higher)
    history_after = pipeline_monitor.load_yield_history(repo_root)
    today_run_after = next(r for r in history_after["runs"] if r["date"] == today_str)
    assert today_run_after["tasks"]["search_jobs_seattle.py"] == 90

    # Run with lower yield for today -> should NOT overwrite higher yield
    task_states_lower = {
        name: {"status": "success", "row_count": 20} for name in pipeline_monitor.SCRAPER_NAMES
    }
    pipeline_monitor.audit_pipeline_health(repo_root, results_dir, task_states_lower)
    history_after2 = pipeline_monitor.load_yield_history(repo_root)
    today_run_after2 = next(r for r in history_after2["runs"] if r["date"] == today_str)
    assert today_run_after2["tasks"]["search_jobs_seattle.py"] == 90

