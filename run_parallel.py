#!/usr/bin/env python3
import os
import sys
import time
import signal
from datetime import datetime
from pathlib import Path
import subprocess

from cockpit.pipeline_monitor import update_pipeline_state

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
    logs_dir = jobspy_dir / "scraped jobs" / "logs" / timestamp_month
    logs_dir.mkdir(parents=True, exist_ok=True)
    
    # Setup results directory (so we can check output files later)
    results_dir = jobspy_dir / "scraped jobs" / "results" / timestamp_month
    results_dir.mkdir(parents=True, exist_ok=True)
    
    print("=" * 80)
    print(f"JobSpy Custom Parallel Runner - Started at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Logs directory: {logs_dir}")
    print(f"Results directory: {results_dir}")
    print("=" * 80)
    
    processes = []
    start_times = {}
    log_files = {}
    task_states = {}
    
    # 3. Handle termination signal to clean up subprocesses nicely
    def signal_handler(sig, frame):
        print("\n\n[!] Interruption detected! Terminating all running scrape processes...")
        for p_info in processes:
            p = p_info["process"]
            name = p_info["name"]
            if p.poll() is None:
                print(f" -> Terminating {name} (PID: {p.pid})...")
                p.terminate()
                if name in task_states:
                    task_states[name]["status"] = "failed"
                    task_states[name]["exit_code"] = -15
        # Wait a bit for processes to terminate
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
        # Close log files
        for lf in log_files.values():
            lf.close()
        update_pipeline_state(base_dir, phase="FAILED", error="Interrupted by signal", tasks=task_states)
        print("[!] Cleanup complete. Exiting.")
        sys.exit(1)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    # 4. Start all scripts in parallel
    for script in scripts:
        script_name = script.name
        log_file_path = logs_dir / f"{script.stem}.log"
        print(f"[START] Spawning {script_name}...")
        print(f"        Log file: {log_file_path}")
        
        # Open log file
        lf = open(log_file_path, "w", encoding="utf-8")
        log_files[script_name] = lf
        
        # Start process using the current python executable (sys.executable)
        # to ensure it uses the active virtualenv/poetry environment.
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        existing_pythonpath = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = f"{base_dir}:{existing_pythonpath}" if existing_pythonpath else str(base_dir)
        if "/home/linuxbrew/.linuxbrew/opt/openblas/lib" not in env.get("LD_LIBRARY_PATH", ""):
            existing_ld = env.get("LD_LIBRARY_PATH", "")
            env["LD_LIBRARY_PATH"] = f"/home/linuxbrew/.linuxbrew/opt/openblas/lib:{existing_ld}" if existing_ld else "/home/linuxbrew/.linuxbrew/opt/openblas/lib"
        p = subprocess.Popen(
            [sys.executable, str(script)],
            stdout=lf,
            stderr=subprocess.STDOUT,
            text=True,
            cwd=str(base_dir), # run from the root directory
            env=env
        )
        
        processes.append({
            "name": script_name,
            "process": p,
            "path": script,
            "log_path": log_file_path,
        })
        start_times[script_name] = time.time()
        task_states[script_name] = {
            "status": "running",
            "pid": p.pid,
            "started_at": datetime.now().isoformat(),
            "log_path": str(log_file_path),
            "output_csv": str(results_dir / EXPECTED_OUTPUTS.get(script_name, "")),
        }

    update_pipeline_state(base_dir, phase="SCRAPING", tasks=task_states)
        
    print("\nAll processes launched in parallel. Monitoring status (polling every 5 seconds)...\n")
    print("-" * 80)
    
    # 5. Monitor processes
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
                
                # Close log file
                log_files[name].close()
                
                duration = time.time() - start_times[name]
                minutes, seconds = divmod(duration, 60)
                duration_str = f"{int(minutes)}m {seconds:.1f}s"
                
                status_str = "SUCCESS" if exit_code == 0 else f"FAILED (Exit Code: {exit_code})"
                task_states[name].update({
                    "status": "success" if exit_code == 0 else "failed",
                    "exit_code": exit_code,
                    "finished_at": datetime.now().isoformat(),
                    "duration_sec": duration,
                })
                update_pipeline_state(base_dir, tasks=task_states)

                print(f"[FINISHED] {name}")
                print(f"           Status:   {status_str}")
                print(f"           Duration: {duration_str}")
                print(f"           Log:      {p_info['log_path']}")
                print("-" * 80)
                
    print("\n" + "=" * 80)
    print("All parallel scrape tasks have completed.")
    print("=" * 80)
    
    # 6. Show summary of generated results files
    print("\nDiscrete Results Summary:")
    expected_outputs = EXPECTED_OUTPUTS
    
    for script_name, csv_name in expected_outputs.items():
        csv_path = results_dir / csv_name
        if csv_path.exists():
            size_mb = csv_path.stat().st_size / (1024 * 1024)
            print(f" - {csv_name}: SAVED ({size_mb:.2f} MB)")
            print(f"   Path: {csv_path}")
        else:
            print(f" - {csv_name}: NOT FOUND (Likely failed or no jobs found)")
            
    print("\nDone!")

if __name__ == "__main__":
    main()
