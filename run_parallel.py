#!/usr/bin/env python3
import csv
import os
import sys
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
            reader = csv.reader(f)
            header = next(reader, None)
            if header is None:
                return 0
            return sum(1 for _ in reader)
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

    p = subprocess.Popen(
        [py_bin, str(script)],
        stdout=lf,
        stderr=subprocess.STDOUT,
        text=True,
        cwd=str(base_dir),
        env=env,
    )
    return p, log_file_path, lf


def main():
    # 1. Define paths
    base_dir = BASE_DIR
    jobspy_dir = JOBSPY_DIR
    scripts = SCRIPTS

    # Verify all scripts exist
    for script in scripts:
        if not script.exists():
            print(f"Error: Script not found: {script}")
            sys.exit(1)

    # 2. Setup logs directory
    timestamp_month = datetime.now().strftime("%Y_%m")
    timestamp_day = datetime.now().strftime("%Y_%m_%d")
    env_logs = os.environ.get("JOBSPY_LOGS_DIR")
    logs_dir = Path(env_logs) if env_logs else (jobspy_dir / "scraped jobs" / "logs" / timestamp_month / timestamp_day)
    logs_dir.mkdir(parents=True, exist_ok=True)

    # Setup results directory (so we can check output files later)
    env_results = os.environ.get("JOBSPY_RESULTS_DIR")
    results_dir = Path(env_results) if env_results else (jobspy_dir / "scraped jobs" / "results" / timestamp_month / timestamp_day)
    results_dir.mkdir(parents=True, exist_ok=True)

    print("=" * 80)
    print(f"Career Pipeline Parallel Runner - Started at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Logs directory: {logs_dir}")
    print(f"Results directory: {results_dir}")
    print("=" * 80)

    processes = []
    start_times = {}
    log_files = {}
    task_states = {}
    active_retry: dict[str, Any] = {"process": None, "name": None, "log_file": None}

    # 3. Handle termination signal to clean up subprocesses nicely
    def signal_handler(sig, frame):
        print("\n\n[!] Interruption detected! Terminating all running scrape processes...")
        # Clean up active retry process if one is running
        if active_retry["process"] is not None and active_retry["process"].poll() is None:
            name = active_retry["name"]
            p = active_retry["process"]
            print(f" -> Terminating retry task {name} (PID: {p.pid})...")
            p.terminate()
            if name in task_states:
                task_states[name]["status"] = "failed"
                task_states[name]["exit_code"] = -15
            if active_retry.get("log_file"):
                active_retry["log_file"].close()

        for p_info in processes:
            p = p_info["process"]
            name = p_info["name"]
            if p.poll() is None:
                print(f" -> Terminating {name} (PID: {p.pid})...")
                p.terminate()
                if name in task_states:
                    task_states[name]["status"] = "failed"
                    task_states[name]["exit_code"] = -15
        time.sleep(1)
        for p_info in processes:
            p = p_info["process"]
            name = p_info["name"]
            if p.poll() is None:
                print(f" -> Killing {name} (PID: {p.pid})...")
                p.kill()
                if name in task_states:
                    task_states[name]["status"] = "failed"
                    task_states[name]["exit_code"] = -9
        for lf in log_files.values():
            lf.close()
        update_pipeline_state(base_dir, phase="FAILED", error="Interrupted by signal", tasks=task_states)
        print("[!] Cleanup complete. Exiting.")
        sys.exit(1)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # Resolve Python executable for scrapers
    py_bin = os.environ.get("JOBSPY_PYTHON")
    if not py_bin:
        candidates = [
            base_dir / ".venv" / "bin" / "python",
            Path.home() / ".local" / "venvs" / "jobspy-custom-3.12" / "bin" / "python",
            Path(sys.executable),
        ]
        for cand in candidates:
            if cand.is_file() and os.access(cand, os.X_OK):
                try:
                    res = subprocess.run([str(cand), "-c", "import bs4, ats_scrapers"], capture_output=True, timeout=3)
                    if res.returncode == 0:
                        py_bin = str(cand)
                        break
                except Exception:
                    pass
        if not py_bin:
            py_bin = sys.executable

    # Preflight check on scraper dependencies
    try:
        chk = subprocess.run([py_bin, "-c", "import bs4, ats_scrapers"], capture_output=True, text=True, timeout=5)
        if chk.returncode != 0:
            print(f"[!] Warning: Python interpreter '{py_bin}' failed scraper dependency check:")
            print(f"    {chk.stderr.strip() or chk.stdout.strip()}")
    except Exception as exc:
        print(f"[!] Preflight check warning: {exc}")

    # 4. Start all scripts in parallel (Wave 1 with launch staggering)
    stagger_seconds = float(os.environ.get("JOBSPY_STAGGER_SEC", "2.5"))
    ordered_scripts = [s for s in scripts if s.name in IMMEDIATE_SCRAPERS] + [
        s for s in scripts if s.name not in IMMEDIATE_SCRAPERS
    ]

    launched_board_scrapers = 0
    for script in ordered_scripts:
        script_name = script.name
        is_immediate = script_name in IMMEDIATE_SCRAPERS

        if not is_immediate:
            if launched_board_scrapers > 0 and stagger_seconds > 0:
                time.sleep(stagger_seconds)
            launched_board_scrapers += 1

        print(f"[START] Spawning {script_name}...")
        p, log_file_path, lf = spawn_scraper_process(
            script, py_bin, base_dir, results_dir, logs_dir, is_retry=False
        )
        log_files[script_name] = lf

        processes.append({
            "name": script_name,
            "process": p,
            "path": script,
            "log_path": log_file_path,
            "log_file": lf,
        })
        start_times[script_name] = time.time()
        task_states[script_name] = {
            "status": "running",
            "pid": p.pid,
            "started_at": datetime.now().isoformat(),
            "log_path": str(log_file_path),
            "output_csv": str(results_dir / EXPECTED_OUTPUTS.get(script_name, "")),
            "retried": False,
        }

    update_pipeline_state(base_dir, phase="SCRAPING", tasks=task_states)

    print("\nAll processes launched. Monitoring status (polling every 5 seconds)...\n")
    print("-" * 80)

    # 5. Monitor processes for Wave 1
    completed_count = 0
    total_count = len(processes)

    while completed_count < total_count:
        time.sleep(5)

        for p_info in processes:
            if p_info.get("completed", False):
                continue

            p = p_info["process"]
            name = p_info["name"]
            exit_code = p.poll()

            if exit_code is not None:
                p_info["completed"] = True
                completed_count += 1

                p_info["log_file"].close()

                duration = time.time() - start_times[name]
                minutes, seconds = divmod(duration, 60)
                duration_str = f"{int(minutes)}m {seconds:.1f}s"

                csv_path = results_dir / EXPECTED_OUTPUTS.get(name, "")
                row_count = count_csv_rows(csv_path)
                status_str = "SUCCESS" if exit_code == 0 else f"FAILED (Exit Code: {exit_code})"

                task_states[name].update({
                    "status": "success" if exit_code == 0 else "failed",
                    "exit_code": exit_code,
                    "finished_at": datetime.now().isoformat(),
                    "duration_sec": duration,
                    "row_count": row_count,
                    "retried": False,
                })
                update_pipeline_state(base_dir, tasks=task_states)

                print(f"[FINISHED] {name}")
                print(f"           Status:    {status_str}")
                print(f"           Duration:  {duration_str}")
                print(f"           Rows:      {row_count}")
                print(f"           Log:       {p_info['log_path']}")
                print("-" * 80)

    print("\n" + "=" * 80)
    print("Wave 1 parallel scrape tasks have completed.")
    print("=" * 80)

    # 6. Wave 2 Automatic One-Shot Retry
    min_expected_rows = int(os.environ.get("JOBSPY_MIN_ROWS", "5"))
    disable_retry = os.environ.get("JOBSPY_DISABLE_RETRY") == "1"

    retry_names = [
        name for name, state in task_states.items()
        if not disable_retry and (state.get("exit_code") != 0 or state.get("row_count", 0) < min_expected_rows)
    ]

    if retry_names:
        retry_cooldown_sec = float(os.environ.get("JOBSPY_RETRY_COOLDOWN_SEC", "15.0"))
        print(f"\n[!] Wave 1 detected {len(retry_names)} failed or low-yield task(s): {', '.join(retry_names)}")
        print(f"    Entering Wave 2 retry cooldown ({retry_cooldown_sec:.1f}s)...")
        if retry_cooldown_sec > 0:
            time.sleep(retry_cooldown_sec)

        for script_name in retry_names:
            script = next((s for s in scripts if s.name == script_name), None)
            if not script:
                continue

            print(f"[RETRY START] Re-running {script_name}...")
            p, log_file_path, lf = spawn_scraper_process(
                script, py_bin, base_dir, results_dir, logs_dir, is_retry=True
            )
            active_retry["process"] = p
            active_retry["name"] = script_name
            active_retry["log_file"] = lf

            retry_start = time.time()
            task_states[script_name]["status"] = "running"
            update_pipeline_state(base_dir, tasks=task_states)

            p.wait()
            lf.close()
            active_retry["process"] = None
            active_retry["name"] = None
            active_retry["log_file"] = None

            retry_exit = p.returncode
            retry_dur = time.time() - retry_start
            csv_path = results_dir / EXPECTED_OUTPUTS.get(script_name, "")
            new_rows = count_csv_rows(csv_path)
            prev_dur = task_states[script_name].get("duration_sec", 0.0) or 0.0

            task_states[script_name].update({
                "status": "success" if retry_exit == 0 else "failed",
                "exit_code": retry_exit,
                "retry_exit_code": retry_exit,
                "row_count": new_rows,
                "retried": True,
                "duration_sec": prev_dur + retry_dur,
                "finished_at": datetime.now().isoformat(),
            })
            update_pipeline_state(base_dir, tasks=task_states)

            status_str = "SUCCESS" if retry_exit == 0 else f"FAILED (Exit Code: {retry_exit})"
            print(f"[RETRY FINISHED] {script_name}")
            print(f"                 Status: {status_str}")
            print(f"                 Rows:   {new_rows}")
            print("-" * 80)

    print("\n" + "=" * 80)
    print("All scrape tasks have completed.")
    print("=" * 80)

    # 7. Show summary of generated results files
    print("\nDiscrete Results Summary:")
    expected_outputs = EXPECTED_OUTPUTS

    for script_name, csv_name in expected_outputs.items():
        csv_path = results_dir / csv_name
        if csv_path.exists():
            size_mb = csv_path.stat().st_size / (1024 * 1024)
            rows = task_states.get(script_name, {}).get("row_count", count_csv_rows(csv_path))
            retried_str = " [RETRIED]" if task_states.get(script_name, {}).get("retried") else ""
            print(f" - {csv_name}: SAVED ({size_mb:.2f} MB, {rows} rows){retried_str}")
            print(f"   Path: {csv_path}")
        else:
            print(f" - {csv_name}: NOT FOUND (Likely failed or no jobs found)")

    failed_count = sum(1 for s in task_states.values() if s.get("exit_code") != 0)
    if failed_count == len(scripts):
        print(f"\n[!] All {failed_count} scraping tasks failed! Aborting pipeline.")
        sys.exit(1)

    print("\nDone!")


if __name__ == "__main__":
    main()
