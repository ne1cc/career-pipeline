import subprocess
from pathlib import Path

import pytest
import run_parallel
import run_pipeline

def test_parallel_runner_scripts_contain_msba():
    base_dir = Path(run_parallel.__file__).parent.resolve()
    jobspy_dir = base_dir / "jobspy"
    expected_scripts = [
        jobspy_dir / "search_jobs_remote.py",
        jobspy_dir / "search_jobs_seattle.py",
        jobspy_dir / "search_jobs_seattle_entry_level.py",
        jobspy_dir / "search_jobs_west.py",
        jobspy_dir / "search_jobs_seattle_msba.py",
        jobspy_dir / "search_jobs_msba_west.py",
        jobspy_dir / "search_jobs_ats.py",
        jobspy_dir / "search_jobs_portals.py",
    ]
    # We inspect the expected_outputs keys or scripts defined in run_parallel
    for script in expected_scripts:
        assert script.exists(), f"Script missing: {script}"
    assert set(run_parallel.SCRIPTS) == set(expected_scripts)
    assert "search_jobs_seattle_msba.py" in run_parallel.EXPECTED_OUTPUTS
    assert "search_jobs_msba_west.py" in run_parallel.EXPECTED_OUTPUTS
    assert len(run_parallel.EXPECTED_OUTPUTS) == 8

def test_pipeline_expected_outputs_mapping():
    outputs = run_pipeline.EXPECTED_OUTPUTS
    assert "seattle_msba_jobs.csv" in outputs
    assert outputs["seattle_msba_jobs.csv"] == "Seattle MSBA Jobs"
    assert "west_coast_msba_jobs.csv" in outputs
    assert outputs["west_coast_msba_jobs.csv"] == "West Coast MSBA Jobs"
    assert len(outputs) == 8


def test_run_cockpit_ingest_with_csvs(monkeypatch, tmp_path):
    recorded_commands = []

    def fake_run(cmd, cwd=None, **kwargs):
        recorded_commands.append({"cmd": cmd, "cwd": cwd})
        return subprocess.CompletedProcess(cmd, 0, stdout="success", stderr="")

    monkeypatch.setattr(run_pipeline.subprocess, "run", fake_run)

    results_dir = tmp_path / "results"
    results_dir.mkdir()
    f1 = results_dir / "seattle_jobs.csv"
    f2 = results_dir / "west_coast_jobs.csv"
    f1.write_text("dummy")
    f2.write_text("dummy")

    ok = run_pipeline.run_cockpit_ingest(results_dir)
    assert ok is True
    assert len(recorded_commands) == 1
    cmd = recorded_commands[0]["cmd"]
    assert "cockpit.cli" in cmd
    assert "ingest" in cmd
    assert str(f1) in cmd
    assert str(f2) in cmd


def test_run_cockpit_ingest_handles_failure(monkeypatch):
    def fake_run(cmd, cwd=None, **kwargs):
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="error")

    monkeypatch.setattr(run_pipeline.subprocess, "run", fake_run)
    assert run_pipeline.run_cockpit_ingest() is False


def test_main_runs_cockpit_ingest_even_if_email_fails(monkeypatch, tmp_path):
    called = []

    monkeypatch.setattr(run_pipeline, "load_config", lambda: {})
    monkeypatch.setattr(run_pipeline, "validate_config", lambda cfg: None)
    monkeypatch.setattr(run_pipeline, "run_scrapers", lambda: called.append("scrapers"))
    monkeypatch.setattr(
        run_pipeline,
        "build_excel_workbook",
        lambda results_dir, expected_outputs: (tmp_path / "report.xlsx", {}, False),
    )

    def fake_send_email(*args, **kwargs):
        called.append("email_failed")
        raise SystemExit(1)

    def fake_ingest(results_dir=None):
        called.append("cockpit_ingest")
        return True

    monkeypatch.setattr(run_pipeline, "send_email_with_attachment", fake_send_email)
    monkeypatch.setattr(run_pipeline, "run_cockpit_ingest", fake_ingest)

    with pytest.raises(SystemExit):
        run_pipeline.main()

    assert called == ["scrapers", "email_failed", "cockpit_ingest"]


def test_main_creates_date_directory(monkeypatch, tmp_path):
    monkeypatch.setattr(run_pipeline, "BASE_DIR", tmp_path)
    monkeypatch.setattr(run_pipeline, "load_config", lambda: {})
    monkeypatch.setattr(run_pipeline, "validate_config", lambda cfg: None)
    monkeypatch.setattr(run_pipeline, "run_scrapers", lambda: None)
    
    passed_results_dir = []
    def fake_build(results_dir, expected_outputs):
        passed_results_dir.append(results_dir)
        return tmp_path / "report.xlsx", {}, False

    monkeypatch.setattr(run_pipeline, "build_excel_workbook", fake_build)
    monkeypatch.setattr(run_pipeline, "send_email_with_attachment", lambda *a, **k: None)
    monkeypatch.setattr(run_pipeline, "run_cockpit_ingest", lambda d=None: True)

    run_pipeline.main()
    assert len(passed_results_dir) == 1
    # Check that results_dir contains year_month/year_month_day
    r_dir = passed_results_dir[0]
    assert r_dir.exists()
    assert len(r_dir.parts) >= 2
    # The parent part is YYYY_MM, and the leaf part is YYYY_MM_DD
    assert len(r_dir.name.split("_")) == 3
    assert len(r_dir.parent.name.split("_")) == 2
