#!/usr/bin/env python3
import csv
import math
import tempfile
import os
import time
import signal
from datetime import datetime
from pathlib import Path
import subprocess
from typing import Any

from cockpit.pipeline_monitor import update_pipeline_state

try:
    csv.field_size_limit(10 * 1024 * 1024)
except Exception:
    pass

BASE_DIR = Path(__file__).parent.resolve()
JOBSPY_DIR = BASE_DIR / "jobspy"

SCRIPTS = [
    JOBSPY_DIR / "search_jobs_remote.py",
    JOBSPY_DIR / "search_jobs_seattle.py",
    JOBSPY_DIR / "search_jobs_seattle_entry_level.py",
    JOBSPY_DIR / "search_jobs_west.py",
    JOBSPY_DIR / "search_jobs_seattle_msba.py",
    JOBSPY_DIR / "search_jobs_msba_west.py",
    JOBSPY_DIR / "search_jobs_ats.py",
    JOBSPY_DIR / "search_jobs_portals.py",
]

EXPECTED_OUTPUTS = {
    "search_jobs_remote.py": "all_city_remote_jobs.csv",
    "search_jobs_seattle.py": "seattle_jobs.csv",
    "search_jobs_seattle_entry_level.py": "seattle_entry_level_jobs.csv",
    "search_jobs_west.py": "west_coast_jobs.csv",
    "search_jobs_seattle_msba.py": "seattle_msba_jobs.csv",
    "search_jobs_msba_west.py": "west_coast_msba_jobs.csv",
    "search_jobs_ats.py": "ats_direct_jobs.csv",
    "search_jobs_portals.py": "tech_portals_jobs.csv",
}

IMMEDIATE_SCRAPERS = {"search_jobs_ats.py", "search_jobs_portals.py"}


def count_csv_rows(csv_path: Path) -> int:
    """Return the number of data rows in a CSV file (excluding header)."""
    if not csv_path.is_file():
        return 0
    try:
        with open(csv_path, "r", encoding="utf-8", errors="replace") as f:
            reader = csv.reader(f, strict=True)
            header = next(reader, None)
            if header is None:
                return 0
            rows = 0
            for row in reader:
                if len(row) != len(header):
                    return 0
                rows += 1
            return rows
    except Exception:
        return 0


def spawn_scraper_process(
    script: Path,
    py_bin: str,
    base_dir: Path,
    results_dir: Path,
    logs_dir: Path,
    is_retry: bool = False,
) -> tuple[subprocess.Popen, Path, Any]:
    script_name = script.name
    log_file_path = logs_dir / f"{script.stem}.log"
    mode = "a" if is_retry else "w"
    lf = open(log_file_path, mode, encoding="utf-8")
    if is_retry:
        lf.write(f"\n\n=== RETRY ATTEMPT 1 ({datetime.now().strftime('%Y-%m-%d %H:%M:%S')}) ===\n\n")
        lf.flush()

    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    env["JOBSPY_PYTHON"] = str(py_bin)
    env["JOBSPY_RESULTS_DIR"] = str(results_dir)
    env["JOBSPY_LOGS_DIR"] = str(logs_dir)
    existing_pythonpath = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = f"{base_dir}:{existing_pythonpath}" if existing_pythonpath else str(base_dir)
    if "/home/linuxbrew/.linuxbrew/opt/openblas/lib" not in env.get("LD_LIBRARY_PATH", ""):
        existing_ld = env.get("LD_LIBRARY_PATH", "")
        env["LD_LIBRARY_PATH"] = f"/home/linuxbrew/.linuxbrew/opt/openblas/lib:{existing_ld}" if existing_ld else "/home/linuxbrew/.linuxbrew/opt/openblas/lib"

    try:
        p = subprocess.Popen(
            [py_bin, str(script)],
            stdout=lf,
            stderr=subprocess.STDOUT,
            text=True,
            cwd=str(base_dir),
            env=env,
        )
    except BaseException:
        lf.close()
        raise
    return p, log_file_path, lf


def stop_process(process: subprocess.Popen) -> None:
    """Terminate and reap a child, escalating when it ignores termination."""
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=3)


def main():
    """Publish fresh CSVs from bounded scraper attempts and one-shot retries.

    Contract:
    - Returns when at least one task produced valid current output.
    - Exits nonzero when no task produced output or execution was interrupted.
    - Propagates launch and filesystem errors after cleaning up children.
    Limits: Search scripts save at completion; timed-out in-memory rows are lost.
    """
    # 1. Validate the deadline before moving prior output or launching children.
    task_timeout = float(os.environ.get("JOBSPY_TASK_TIMEOUT_SEC", "7200"))
    if not math.isfinite(task_timeout) or task_timeout <= 0:
        raise ValueError("JOBSPY_TASK_TIMEOUT_SEC must be a positive finite number")
    for script in SCRIPTS:
        if not script.is_file():
            raise FileNotFoundError(f"Scraper script not found: {script}")

    month = datetime.now().strftime("%Y_%m")
    day = datetime.now().strftime("%Y_%m_%d")
    results_dir = Path(
        os.environ.get("JOBSPY_RESULTS_DIR")
        or JOBSPY_DIR / "scraped jobs" / "results" / month / day
    )
    logs_dir = Path(
        os.environ.get("JOBSPY_LOGS_DIR")
        or JOBSPY_DIR / "scraped jobs" / "logs" / month / day
    )
    results_dir.mkdir(parents=True, exist_ok=True)
    logs_dir.mkdir(parents=True, exist_ok=True)

    py_bin = os.environ.get("JOBSPY_PYTHON")
    if not py_bin:
        from cockpit.pipeline_monitor import resolve_pipeline_python

        py_bin = resolve_pipeline_python(BASE_DIR)

    # 2. Preserve prior daily CSVs outside the paths consumed by report/ingest.
    run_dir = Path(tempfile.mkdtemp(prefix=".run-", dir=results_dir))
    archive_dir = run_dir / "previous"
    for name in EXPECTED_OUTPUTS.values():
        previous = results_dir / name
        if previous.is_file():
            archive_dir.mkdir(exist_ok=True)
            previous.replace(archive_dir / name)

    print(f"Career Pipeline Parallel Runner - Started at {datetime.now().isoformat()}")
    print(f"Results directory: {results_dir}")
    print(f"Attempt directory: {run_dir}")
    print(f"Per-attempt deadline: {task_timeout:g}s")
    task_states = {
        script.name: {
            "status": "pending",
            "row_count": 0,
            "output_csv": str(results_dir / EXPECTED_OUTPUTS[script.name]),
        }
        for script in SCRIPTS
    }
    update_pipeline_state(BASE_DIR, phase="SCRAPING", tasks=task_states)
    attempts = []

    def start_attempt(script: Path, retry: bool = False) -> dict:
        name = script.name
        attempt_dir = run_dir / script.stem / ("retry" if retry else "first")
        attempt_dir.mkdir(parents=True)
        process, log_path, log_file = spawn_scraper_process(
            script, py_bin, BASE_DIR, attempt_dir, logs_dir, is_retry=retry
        )
        previous_duration = task_states[name].get("duration_sec", 0) or 0
        attempt = {
            "process": process,
            "name": name,
            "log_file": log_file,
            "csv": attempt_dir / EXPECTED_OUTPUTS[name],
            "start": time.monotonic(),
            "retry": retry,
            "previous_duration": previous_duration,
            "finished": False,
        }
        attempts.append(attempt)
        now = datetime.now().isoformat()
        task_states[name].update(
            {
                "status": "running",
                "pid": process.pid,
                "log_path": str(log_path),
                "attempt_started_at": now,
                "duration_sec": None,
                "retried": retry,
            }
        )
        if not retry:
            task_states[name]["started_at"] = now
        else:
            task_states[name]["retry_started_at"] = now
        update_pipeline_state(BASE_DIR, tasks=task_states)
        print(f"[{'RETRY' if retry else 'START'}] {name} (PID: {process.pid})")
        return attempt

    def finish_attempt(attempt: dict, timed_out: bool = False) -> None:
        process = attempt["process"]
        name = attempt["name"]
        task = task_states[name]
        if timed_out:
            stop_process(process)
        attempt["log_file"].close()
        attempt["finished"] = True
        rows = count_csv_rows(attempt["csv"])
        if rows:
            with attempt["csv"].open(encoding="utf-8", newline="") as stream:
                if "job_url" not in next(csv.reader(stream), []):
                    rows = 0
        error = None
        if timed_out:
            error = f"Scraper timed out after {task_timeout:g}s"
        elif process.returncode != 0:
            error = f"Scraper exited with code {process.returncode}"
        elif not rows:
            error = "Expected CSV is missing, empty, or invalid"

        # 3. Preserve partial rows without downgrading an already complete result.
        previous_rows = task.get("row_count", 0)
        previous_complete = previous_rows > 0 and task.get("error") is None
        if (
            rows > 0
            and not timed_out
            and rows >= previous_rows
            and (process.returncode == 0 or not previous_complete)
        ):
            attempt["csv"].replace(results_dir / EXPECTED_OUTPUTS[name])
            task.update(row_count=rows, error=error)
        elif not previous_rows:
            task["error"] = error
        selected_valid = task.get("row_count", 0) > 0 and task.get("error") is None
        task.update(
            {
                "status": "success" if selected_valid else "failed",
                "exit_code": 0 if selected_valid else (process.returncode or 1),
                "finished_at": datetime.now().isoformat(),
                "duration_sec": attempt["previous_duration"]
                + time.monotonic()
                - attempt["start"],
            }
        )
        if attempt["retry"]:
            task.update(retry_exit_code=process.returncode, retry_error=error)
        update_pipeline_state(BASE_DIR, tasks=task_states)
        print(f"[FINISHED] {name}: {task['status']}, {task['row_count']} selected rows")

    def interrupted(signum, frame):
        raise SystemExit(f"Interrupted by signal {signum}")

    previous_handlers = {
        sig: signal.signal(sig, interrupted) for sig in (signal.SIGINT, signal.SIGTERM)
    }
    try:
        # 4. Stagger board launches; each process owns its own monotonic deadline.
        ordered = [s for s in SCRIPTS if s.name in IMMEDIATE_SCRAPERS] + [
            s for s in SCRIPTS if s.name not in IMMEDIATE_SCRAPERS
        ]
        stagger = float(os.environ.get("JOBSPY_STAGGER_SEC", "2.5"))
        board_count = 0
        for script in ordered:
            if script.name not in IMMEDIATE_SCRAPERS:
                if board_count and stagger > 0:
                    time.sleep(stagger)
                board_count += 1
            start_attempt(script)

        first_wave = list(attempts)
        while any(not a["finished"] for a in first_wave):
            for attempt in first_wave:
                if attempt["finished"]:
                    continue
                if attempt["process"].poll() is not None:
                    finish_attempt(attempt)
                elif time.monotonic() - attempt["start"] >= task_timeout:
                    finish_attempt(attempt, timed_out=True)
            if any(not a["finished"] for a in first_wave):
                time.sleep(min(5, task_timeout))

        # 5. Retry failed/low-yield tasks once, retaining usable first-wave output.
        min_rows = int(os.environ.get("JOBSPY_MIN_ROWS", "5"))
        retry_scripts = [
            s
            for s in SCRIPTS
            if task_states[s.name]["status"] == "failed"
            or task_states[s.name]["row_count"] < min_rows
        ]
        if os.environ.get("JOBSPY_DISABLE_RETRY") != "1" and retry_scripts:
            cooldown = float(os.environ.get("JOBSPY_RETRY_COOLDOWN_SEC", "15"))
            if cooldown > 0:
                time.sleep(cooldown)
            for script in retry_scripts:
                attempt = start_attempt(script, retry=True)
                try:
                    attempt["process"].wait(timeout=task_timeout)
                except subprocess.TimeoutExpired:
                    finish_attempt(attempt, timed_out=True)
                else:
                    finish_attempt(attempt)

        if not any(t.get("row_count", 0) > 0 for t in task_states.values()):
            raise SystemExit(1)
        print("All scrape tasks finished; current outputs published for health audit.")
    except BaseException as exc:
        for attempt in attempts:
            stop_process(attempt["process"])
            task = task_states[attempt["name"]]
            if task["status"] == "running":
                task.update(
                    status="failed",
                    exit_code=attempt["process"].returncode,
                    error="Scraping interrupted",
                )
        update_pipeline_state(
            BASE_DIR, phase="FAILED", error=str(exc), tasks=task_states
        )
        raise
    finally:
        for attempt in attempts:
            stop_process(attempt["process"])
            attempt["log_file"].close()
        for sig, handler in previous_handlers.items():
            signal.signal(sig, handler)


if __name__ == "__main__":
    main()
