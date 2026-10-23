import json
from pathlib import Path
import sys
import time

import pytest

import run_parallel


@pytest.fixture
def runner(monkeypatch, tmp_path):
    results = tmp_path / "results"
    results.mkdir()
    monkeypatch.setattr(run_parallel, "BASE_DIR", tmp_path)
    monkeypatch.setattr(run_parallel, "JOBSPY_DIR", tmp_path)
    monkeypatch.setenv("JOBSPY_PYTHON", sys.executable)
    monkeypatch.setenv("JOBSPY_RESULTS_DIR", str(results))
    monkeypatch.setenv("JOBSPY_LOGS_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("JOBSPY_STAGGER_SEC", "0")
    monkeypatch.setenv("JOBSPY_RETRY_COOLDOWN_SEC", "0")
    monkeypatch.setenv("JOBSPY_DISABLE_RETRY", "1")
    monkeypatch.setattr(run_parallel.signal, "signal", lambda *args: None)
    sleep = time.sleep
    monkeypatch.setattr(run_parallel.time, "sleep", lambda seconds: sleep(0.01))

    def configure(sources):
        scripts = []
        for name, source in sources.items():
            script = tmp_path / name
            script.write_text(source)
            scripts.append(script)
        monkeypatch.setattr(run_parallel, "SCRIPTS", scripts)
        monkeypatch.setattr(
            run_parallel,
            "EXPECTED_OUTPUTS",
            {name: f"{Path(name).stem}_jobs.csv" for name in sources},
        )
        return results

    return configure


WRITE_ROWS = """from pathlib import Path
import os
out = Path(os.environ["JOBSPY_RESULTS_DIR"]) / (Path(__file__).stem + "_jobs.csv")
out.write_text("job_url,title\\n" + "https://example.test/1,Data Analyst\\n" * 6)
"""


def test_runner_rejects_stale_csv_after_empty_exit_zero(runner, tmp_path):
    results = runner({"empty.py": "print('No jobs found')\n"})
    old = "job_url,title\n" + "https://old.test/1,Old Job\n" * 6
    output = results / "empty_jobs.csv"
    output.write_text(old)

    with pytest.raises(SystemExit) as exc:
        run_parallel.main()
    assert exc.value.code == 1
    state = json.loads((tmp_path / ".pipeline_state/pipeline_run.json").read_text())
    assert state["tasks"]["empty.py"]["status"] == "failed"
    assert state["tasks"]["empty.py"]["row_count"] == 0
    assert not output.exists()
    assert any(p.read_text() == old for p in results.rglob("empty_jobs.csv"))


def test_runner_preserves_usable_output_with_failed_peer(runner, tmp_path):
    results = runner({"good.py": WRITE_ROWS, "bad.py": "raise SystemExit(1)\n"})
    run_parallel.main()
    state = json.loads((tmp_path / ".pipeline_state/pipeline_run.json").read_text())
    assert state["tasks"]["good.py"]["status"] == "success"
    assert state["tasks"]["bad.py"]["status"] == "failed"
    assert run_parallel.count_csv_rows(results / "good_jobs.csv") == 6


def test_failed_retry_cannot_replace_better_current_run_output(
    runner, monkeypatch, tmp_path
):
    partial = """from pathlib import Path
import os
counter = Path(__file__).with_suffix(".count")
attempt = int(counter.read_text()) + 1 if counter.exists() else 1
counter.write_text(str(attempt))
out = Path(os.environ["JOBSPY_RESULTS_DIR"]) / "partial_jobs.csv"
rows = 2 if attempt == 1 else 1
out.write_text("job_url,title\\n" + "https://example.test/1,Data Analyst\\n" * rows)
raise SystemExit(1)
"""
    results = runner({"good.py": WRITE_ROWS, "partial.py": partial})
    monkeypatch.setenv("JOBSPY_DISABLE_RETRY", "0")
    run_parallel.main()

    state = json.loads((tmp_path / ".pipeline_state/pipeline_run.json").read_text())
    task = state["tasks"]["partial.py"]
    assert task["status"] == "failed"
    assert task["retried"] is True
    assert task["row_count"] == 2
    assert run_parallel.count_csv_rows(results / "partial_jobs.csv") == 2


@pytest.mark.parametrize("error", [OSError("cannot spawn"), SystemExit(1)])
def test_interrupted_retry_restores_current_run_output(runner, monkeypatch, error):
    results = runner({"partial.py": WRITE_ROWS.replace("* 6", "* 2")})
    monkeypatch.setenv("JOBSPY_DISABLE_RETRY", "0")
    spawn = run_parallel.spawn_scraper_process

    def spawn_or_interrupt(*args, **kwargs):
        if kwargs.get("is_retry"):
            raise error
        return spawn(*args, **kwargs)

    monkeypatch.setattr(run_parallel, "spawn_scraper_process", spawn_or_interrupt)
    with pytest.raises(type(error)):
        run_parallel.main()
    assert run_parallel.count_csv_rows(results / "partial_jobs.csv") == 2


def test_retry_child_is_stopped_before_restoring_output(runner, monkeypatch):
    worker = (
        WRITE_ROWS.replace("* 6", "* 2")
        + """
import signal, time
counter = Path(__file__).with_suffix(".count")
attempt = int(counter.read_text()) + 1 if counter.exists() else 1
counter.write_text(str(attempt))
if attempt > 1:
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    Path(__file__).with_suffix(".ready").touch()
    while True:
        time.sleep(1)
"""
    )
    results = runner({"partial.py": worker})
    monkeypatch.setenv("JOBSPY_DISABLE_RETRY", "0")
    spawn = run_parallel.spawn_scraper_process
    children = []

    class InterruptedWait:
        def __init__(self, process):
            self.process = process
            self.interrupted = False

        def __getattr__(self, name):
            return getattr(self.process, name)

        def wait(self, timeout=None):
            if not self.interrupted:
                self.interrupted = True
                raise OSError("wait interrupted")
            return self.process.wait(timeout=timeout)

    def spawn_or_interrupt(*args, **kwargs):
        process, path, log = spawn(*args, **kwargs)
        if kwargs.get("is_retry"):
            children.append(process)
            ready = args[0].with_suffix(".ready")
            deadline = time.monotonic() + 5
            while not ready.exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            assert ready.exists()
            return InterruptedWait(process), path, log
        return process, path, log

    monkeypatch.setattr(run_parallel, "spawn_scraper_process", spawn_or_interrupt)
    try:
        with pytest.raises(OSError, match="wait interrupted"):
            run_parallel.main()
        assert children[0].poll() is not None
        assert run_parallel.count_csv_rows(results / "partial_jobs.csv") == 2
    finally:
        for process in children:
            if process.poll() is None:
                process.kill()
            process.wait()


@pytest.mark.parametrize("retry_rows", [2, 3])
def test_partial_retry_cannot_downgrade_successful_first_attempt(
    runner, monkeypatch, tmp_path, retry_rows
):
    worker = """from pathlib import Path
import os
counter = Path(__file__).with_suffix(".count")
attempt = int(counter.read_text()) + 1 if counter.exists() else 1
counter.write_text(str(attempt))
rows = 2 if attempt == 1 else RETRY_ROWS
title = "First" if attempt == 1 else "Retry"
out = Path(os.environ["JOBSPY_RESULTS_DIR"]) / "partial_jobs.csv"
out.write_text("job_url,title\\n" + f"https://example.test/1,{title}\\n" * rows)
if attempt > 1:
    raise SystemExit(1)
""".replace(
        "RETRY_ROWS", str(retry_rows)
    )
    results = runner({"partial.py": worker})
    monkeypatch.setenv("JOBSPY_DISABLE_RETRY", "0")
    run_parallel.main()
    task = json.loads((tmp_path / ".pipeline_state/pipeline_run.json").read_text())[
        "tasks"
    ]["partial.py"]
    assert task["status"] == "success"
    assert task["row_count"] == 2
    assert task["retry_exit_code"] == 1
    assert "First" in (results / "partial_jobs.csv").read_text()
