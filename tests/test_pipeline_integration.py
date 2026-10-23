import json
import subprocess
from pathlib import Path

import pytest
import run_parallel
import run_pipeline


@pytest.fixture(autouse=True)
def isolate_pipeline(monkeypatch, tmp_path):
    monkeypatch.setattr(run_pipeline, "BASE_DIR", tmp_path)
    monkeypatch.setattr(run_pipeline, "CONFIG_FILE", tmp_path / "pipeline_config.json")
    monkeypatch.setattr(run_pipeline, "notify_macos", lambda **kwargs: None)
    monkeypatch.setattr(run_pipeline, "audit_pipeline_health", lambda *args: {"health": "HEALTHY"})
    monkeypatch.setenv("JOBSPY_RESULTS_DIR", str(tmp_path / "results"))
    monkeypatch.setenv("JOBSPY_LOGS_DIR", str(tmp_path / "logs"))


@pytest.fixture
def mocked_pipeline(monkeypatch, tmp_path):
    monkeypatch.setattr(run_pipeline, "load_config", lambda: {})
    monkeypatch.setattr(run_pipeline, "validate_config", lambda cfg: False)
    monkeypatch.setattr(run_pipeline, "run_scrapers", lambda: None)
    monkeypatch.setattr(
        run_pipeline,
        "build_excel_workbook",
        lambda *args: (tmp_path / "report.xlsx", {}, False),
    )
    monkeypatch.setattr(run_pipeline, "send_email_with_attachment", lambda *args: False)
    monkeypatch.setattr(run_pipeline, "run_cockpit_ingest", lambda *args: True)


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
        raise RuntimeError("email failed")

    def fake_ingest(results_dir=None):
        called.append("cockpit_ingest")
        return True

    monkeypatch.setattr(run_pipeline, "send_email_with_attachment", fake_send_email)
    monkeypatch.setattr(run_pipeline, "run_cockpit_ingest", fake_ingest)

    with pytest.raises(RuntimeError, match="email failed"):
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


def test_pipeline_gracefully_skips_unconfigured_email(monkeypatch, tmp_path):
    cfg_file = tmp_path / "pipeline_config.json"
    monkeypatch.setattr(run_pipeline, "CONFIG_FILE", cfg_file)

    # First run creates default config with email_enabled=False without sys.exit
    cfg = run_pipeline.load_config()
    assert cfg_file.exists()
    assert cfg["email_enabled"] is False
    assert run_pipeline.validate_config(cfg) is False
    assert run_pipeline.send_email_with_attachment(cfg, tmp_path / "dummy.xlsx", {}, False) is False


def test_ingest_failure_records_failed_state(mocked_pipeline, monkeypatch, tmp_path):
    monkeypatch.setattr(run_pipeline, "run_cockpit_ingest", lambda *args: False)
    with pytest.raises(RuntimeError, match="[Ii]ngest"):
        run_pipeline.main()
    state = json.loads((tmp_path / ".pipeline_state/pipeline_run.json").read_text())
    assert state["phase"] == "FAILED"
    assert state["finished_at"]
    assert "ingest" in state["error"].lower()


def test_new_run_clears_previous_state(mocked_pipeline, monkeypatch, tmp_path):
    from cockpit.pipeline_monitor import update_pipeline_state

    update_pipeline_state(
        tmp_path,
        tasks={"old": {"status": "failed"}},
        finished_at="yesterday",
        excel_path="old.xlsx",
        health="CRITICAL",
        anomalies=[{"message": "old failure"}],
    )
    observed = []

    def stop_after_start():
        observed.append(
            json.loads((tmp_path / ".pipeline_state/pipeline_run.json").read_text())
        )
        raise RuntimeError("scraper failed")

    monkeypatch.setattr(run_pipeline, "run_scrapers", stop_after_start)
    with pytest.raises(RuntimeError, match="scraper failed"):
        run_pipeline.main()
    assert observed[0]["tasks"] == {}
    assert observed[0]["finished_at"] is None
    assert observed[0]["excel_path"] is None
    assert observed[0]["health"] == "HEALTHY"
    assert observed[0]["anomalies"] == []


def test_scraper_wrapper_raises_handled_error(monkeypatch, tmp_path):
    (tmp_path / "run_parallel.py").touch()
    monkeypatch.setattr(
        run_pipeline.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args, 1),
    )
    with pytest.raises(RuntimeError, match="return code 1"):
        run_pipeline.run_scrapers()


def test_email_wrapper_raises_handled_error(tmp_path):
    config = {
        "sender_email": "sender@example.com",
        "sender_password": "test",
        "receiver_email": "receiver@example.com",
        "smtp_server": "unused",
        "smtp_port": 587,
    }
    with pytest.raises(RuntimeError, match="email"):
        run_pipeline.send_email_with_attachment(
            config, tmp_path / "missing.xlsx", {}, False
        )


def test_both_stage_failures_remain_visible(mocked_pipeline, monkeypatch, tmp_path):
    def fail_email(*args):
        raise RuntimeError("email unavailable")

    monkeypatch.setattr(run_pipeline, "send_email_with_attachment", fail_email)
    monkeypatch.setattr(run_pipeline, "run_cockpit_ingest", lambda *args: False)
    with pytest.raises(RuntimeError, match="email unavailable"):
        run_pipeline.main()
    state = json.loads((tmp_path / ".pipeline_state/pipeline_run.json").read_text())
    assert state["phase"] == "FAILED"
    assert "email unavailable" in state["error"]
    assert "ingest" in state["error"].lower()


def test_empty_explicit_ingest_never_falls_back_to_old_inputs(monkeypatch, tmp_path):
    def unexpected_subprocess(*args, **kwargs):
        pytest.fail("Empty explicit results must not invoke default-input ingestion")

    monkeypatch.setattr(run_pipeline.subprocess, "run", unexpected_subprocess)
    assert run_pipeline.run_cockpit_ingest(tmp_path) is False


def test_empty_workbook_raises_handled_error(tmp_path):
    with pytest.raises(RuntimeError, match="workbook|CSV|data"):
        run_pipeline.build_excel_workbook(
            tmp_path, run_pipeline.EXPECTED_OUTPUTS, state_file=tmp_path / "urls.json"
        )


@pytest.mark.parametrize("failure", ["scrapers", "workbook", "ingest", "partial"])
def test_main_records_failed_outcome(monkeypatch, tmp_path, failure):
    from cockpit.pipeline_monitor import get_state_file
    import json

    ingested = []
    monkeypatch.setattr(run_pipeline, "load_config", lambda: {})
    monkeypatch.setattr(run_pipeline, "validate_config", lambda cfg: False)

    def scrape():
        if failure == "scrapers":
            raise SystemExit(1)

    def workbook(*args):
        if failure == "workbook":
            raise SystemExit(1)
        return tmp_path / "report.xlsx", {}, False

    def ingest(*args):
        ingested.append(True)
        return failure != "ingest"

    monkeypatch.setattr(run_pipeline, "run_scrapers", scrape)
    monkeypatch.setattr(run_pipeline, "build_excel_workbook", workbook)
    monkeypatch.setattr(run_pipeline, "send_email_with_attachment", lambda *args: False)
    monkeypatch.setattr(run_pipeline, "run_cockpit_ingest", ingest)
    if failure == "partial":
        monkeypatch.setattr(
            run_pipeline,
            "audit_pipeline_health",
            lambda *args: {
                "health": "CRITICAL",
                "anomalies": [{"severity": "CRITICAL", "message": "worker failed"}],
            },
        )

    with pytest.raises((SystemExit, RuntimeError)):
        run_pipeline.main()
    state = json.loads(get_state_file(tmp_path).read_text())
    assert state["phase"] == "FAILED"
    assert state["error"]
    assert state["finished_at"]
    if failure in {"ingest", "partial"}:
        assert ingested == [True]
