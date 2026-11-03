import json
import os
import sys
import time

import pytest

import run_parallel


@pytest.fixture
def runner(monkeypatch, tmp_path):
    results = tmp_path / "results"
    logs = tmp_path / "logs"
    results.mkdir()
    monkeypatch.setattr(run_parallel, "BASE_DIR", tmp_path)
    monkeypatch.setenv("JOBSPY_RESULTS_DIR", str(results))
    monkeypatch.setenv("JOBSPY_LOGS_DIR", str(logs))
    monkeypatch.setenv("JOBSPY_PYTHON", sys.executable)
    monkeypatch.setenv("JOBSPY_STAGGER_SEC", "0")
    monkeypatch.setenv("JOBSPY_RETRY_COOLDOWN_SEC", "0")
    monkeypatch.setenv("JOBSPY_TASK_TIMEOUT_SEC", "5")
    monkeypatch.delenv("JOBSPY_DISABLE_RETRY", raising=False)
    real_sleep = time.sleep
    monkeypatch.setattr(
        run_parallel.time, "sleep", lambda seconds: real_sleep(min(seconds, 0.01))
    )

    def configure(modes):
        script = tmp_path / "search_jobs_remote.py"
        script.write_text(
            """import os, sys, time
from pathlib import Path
counter = Path(__file__).with_suffix(".calls")
n = int(counter.read_text()) if counter.exists() else 0
counter.write_text(str(n + 1))
modes = MODES
mode = modes[min(n, len(modes) - 1)]
if mode == "fail":
    sys.exit(1)
out = Path(os.environ["JOBSPY_RESULTS_DIR"])
out.mkdir(parents=True, exist_ok=True)
if mode == "incremental_slow":
    rows = [f"indeed,Analyst,https://example.com/{i}\\n" for i in range(3)]
    (out / "remote_jobs.csv").write_text("site,title,job_url\\n" + "".join(rows))
    time.sleep(0.4)
    mode = "missing"
elif mode == "slow":
    time.sleep(0.4)
    mode = 6
if mode == "bad":
    (out / "remote_jobs.csv").write_text('site,title,job_url\\nindeed,"unterminated')
elif mode != "missing":
    rows = [f"indeed,Analyst,https://example.com/{i}\\n" for i in range(mode)]
    (out / "remote_jobs.csv").write_text("site,title,job_url\\n" + "".join(rows))
""".replace(
                "MODES", repr(modes)
            )
        )
        monkeypatch.setattr(run_parallel, "SCRIPTS", [script])
        monkeypatch.setattr(
            run_parallel, "EXPECTED_OUTPUTS", {script.name: "remote_jobs.csv"}
        )
        return script

    return tmp_path, results, configure


def state(root):
    return json.loads((root / ".pipeline_state/pipeline_run.json").read_text())


def test_stale_daily_csv_is_archived_and_cannot_make_failed_run_succeed(
    runner, monkeypatch
):
    root, results, configure = runner
    configure(["missing"])
    monkeypatch.setenv("JOBSPY_DISABLE_RETRY", "1")
    previous = "site,title,job_url\nindeed,Old,https://example.com/old\n"
    (results / "remote_jobs.csv").write_text(previous)
    with pytest.raises(SystemExit) as error:
        run_parallel.main()
    assert error.value.code == 1
    assert not (results / "remote_jobs.csv").exists()
    archives = [p for p in results.glob("*/previous/remote_jobs.csv")]
    assert len(archives) == 1
    assert archives[0].read_text() == previous


@pytest.mark.parametrize("mode", ["missing", "bad", 0, "fail"])
def test_no_valid_output_retries_once_and_fails(runner, mode):
    root, results, configure = runner
    script = configure([mode])
    with pytest.raises(SystemExit):
        run_parallel.main()
    assert script.with_suffix(".calls").read_text() == "2"
    task = state(root)["tasks"][script.name]
    assert task["status"] == "failed"
    assert task["row_count"] == 0
    assert not (results / "remote_jobs.csv").exists()


def test_successful_retry_publishes_current_rows(runner):
    root, results, configure = runner
    script = configure(["missing", 6])
    run_parallel.main()
    task = state(root)["tasks"][script.name]
    assert task["status"] == "success"
    assert task["row_count"] == 6
    assert task["retried"] is True
    assert task["retry_exit_code"] == 0
    assert run_parallel.count_csv_rows(results / "remote_jobs.csv") == 6


@pytest.mark.parametrize("retry", ["fail", "missing", 1])
def test_failed_or_worse_retry_preserves_valid_first_wave(runner, retry):
    root, results, configure = runner
    script = configure([2, retry])
    run_parallel.main()
    task = state(root)["tasks"][script.name]
    assert task["status"] == "success"
    assert task["row_count"] == 2
    assert task["retried"] is True
    assert run_parallel.count_csv_rows(results / "remote_jobs.csv") == 2


def test_task_deadline_terminates_child_and_rejects_output(runner, monkeypatch):
    root, results, configure = runner
    script = configure(["slow"])
    monkeypatch.setenv("JOBSPY_TASK_TIMEOUT_SEC", "0.08")
    monkeypatch.setenv("JOBSPY_DISABLE_RETRY", "1")
    with pytest.raises(SystemExit):
        run_parallel.main()
    task = state(root)["tasks"][script.name]
    assert task["status"] == "failed"
    assert "timed out" in task["error"].lower()
    assert not (results / "remote_jobs.csv").exists()
    with pytest.raises(ProcessLookupError):
        os.kill(task["pid"], 0)


@pytest.mark.parametrize("value", ["0", "-1", "nan", "inf", "bad"])
def test_invalid_deadline_fails_before_scrapers_start(runner, monkeypatch, value):
    root, results, configure = runner
    script = configure([6])
    monkeypatch.setenv("JOBSPY_TASK_TIMEOUT_SEC", value)
    with pytest.raises((ValueError, SystemExit)):
        run_parallel.main()
    assert not script.with_suffix(".calls").exists()


def test_launch_failure_reaps_already_running_child(runner, monkeypatch):
    root, results, configure = runner
    first = configure(["slow"])
    second = root / "second.py"
    second.touch()
    monkeypatch.setattr(run_parallel, "SCRIPTS", [first, second])
    monkeypatch.setattr(
        run_parallel,
        "EXPECTED_OUTPUTS",
        {first.name: "remote_jobs.csv", second.name: "second_jobs.csv"},
    )
    real_spawn = run_parallel.spawn_scraper_process
    children = []

    def spawn(script, *args, **kwargs):
        if script == second:
            raise OSError("launch failed")
        result = real_spawn(script, *args, **kwargs)
        children.append(result[0])
        return result

    monkeypatch.setattr(run_parallel, "spawn_scraper_process", spawn)
    with pytest.raises(OSError, match="launch failed"):
        run_parallel.main()
    assert children[0].poll() is not None


def test_retry_timeout_preserves_valid_first_wave(runner, monkeypatch):
    root, results, configure = runner
    script = configure([2, "slow"])
    monkeypatch.setenv("JOBSPY_TASK_TIMEOUT_SEC", "0.08")
    run_parallel.main()
    task = state(root)["tasks"][script.name]
    assert task["status"] == "success"
    assert task["row_count"] == 2
    assert "timed out" in task["retry_error"].lower()
    assert task["retry_exit_code"] != 0
    assert run_parallel.count_csv_rows(results / "remote_jobs.csv") == 2


def test_monitor_tracks_actual_retry_pid(runner, monkeypatch):
    import copy

    root, results, configure = runner
    script = configure(["missing", 6])
    observed = []
    real_update = run_parallel.update_pipeline_state

    def update(*args, **kwargs):
        observed.append(copy.deepcopy(kwargs))
        return real_update(*args, **kwargs)

    monkeypatch.setattr(run_parallel, "update_pipeline_state", update)
    run_parallel.main()
    running = [
        entry["tasks"][script.name]
        for entry in observed
        if entry.get("tasks", {}).get(script.name, {}).get("status") == "running"
    ]
    assert running[0]["pid"] != running[-1]["pid"]
    assert running[-1]["retried"] is True
    assert running[-1]["duration_sec"] is None


def test_timeout_preserves_incremental_rows(runner, monkeypatch):
    root, results, configure = runner
    script = configure(["incremental_slow", "incremental_slow"])
    monkeypatch.setenv("JOBSPY_TASK_TIMEOUT_SEC", "0.1")
    run_parallel.main()
    task = state(root)["tasks"][script.name]
    assert task["row_count"] == 3
    assert "timed out" in task["error"].lower()
    assert run_parallel.count_csv_rows(results / "remote_jobs.csv") == 3


def test_resolve_target_scripts_filters_properly(tmp_path):
    jobspy_dir = tmp_path / "jobspy"
    jobspy_dir.mkdir()
    for name in run_parallel.EXPECTED_OUTPUTS.keys():
        (jobspy_dir / name).touch()

    # Exact filename
    res1 = run_parallel.resolve_target_scripts(tmp_path, ["search_jobs_remote.py"])
    assert len(res1) == 1
    assert res1[0].name == "search_jobs_remote.py"

    # Substring / stem (e.g. "remote", "seattle")
    res2 = run_parallel.resolve_target_scripts(tmp_path, ["remote", "seattle"])
    assert len(res2) >= 2
    names = {s.name for s in res2}
    assert "search_jobs_remote.py" in names
    assert "search_jobs_seattle.py" in names

    # Empty / none defaults to all
    res_all = run_parallel.resolve_target_scripts(tmp_path, [])
    assert len(res_all) == len(run_parallel.EXPECTED_OUTPUTS)


def test_selective_scrapers_preserves_unrelated_output(runner, monkeypatch, tmp_path):
    root, results, configure = runner
    script1 = configure([3])
    script2 = tmp_path / "search_jobs_seattle.py"
    script2.write_text("""import os, sys
from pathlib import Path
out = Path(os.environ["JOBSPY_RESULTS_DIR"])
(out / "seattle_jobs.csv").write_text("site,title,job_url\\nindeed,Dev,https://example.com/s1\\n")
""")
    monkeypatch.setattr(run_parallel, "SCRIPTS", [script1, script2])
    monkeypatch.setattr(
        run_parallel,
        "EXPECTED_OUTPUTS",
        {script1.name: "remote_jobs.csv", script2.name: "seattle_jobs.csv"},
    )
    # Pre-populate seattle_jobs.csv
    (results / "seattle_jobs.csv").write_text("site,title,job_url\nindeed,Dev,https://example.com/s1\n")

    monkeypatch.setenv("JOBSPY_SCRAPERS", "remote")
    run_parallel.main()

    assert run_parallel.count_csv_rows(results / "remote_jobs.csv") == 3
    assert run_parallel.count_csv_rows(results / "seattle_jobs.csv") == 1


