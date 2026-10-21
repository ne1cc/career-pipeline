from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

EXPECTED_OUTPUTS: dict[str, str] = {
    "search_jobs_remote.py": "all_city_remote_jobs.csv",
    "search_jobs_seattle.py": "seattle_jobs.csv",
    "search_jobs_seattle_entry_level.py": "seattle_entry_level_jobs.csv",
    "search_jobs_west.py": "west_coast_jobs.csv",
    "search_jobs_seattle_msba.py": "seattle_msba_jobs.csv",
    "search_jobs_msba_west.py": "west_coast_msba_jobs.csv",
    "search_jobs_ats.py": "ats_direct_jobs.csv",
    "search_jobs_portals.py": "tech_portals_jobs.csv",
}

SCRAPER_NAMES: list[str] = list(EXPECTED_OUTPUTS.keys())

PHASES = ["SCRAPING", "EXCEL_BUILD", "EMAIL", "INGEST"]


def get_state_file(repo_root: Path) -> Path:
    return repo_root / ".pipeline_state" / "pipeline_run.json"


def update_pipeline_state(repo_root: Path, **updates: Any) -> dict:
    """Atomically read, merge updates, and persist pipeline execution state."""
    state_file = get_state_file(repo_root)
    state_file.parent.mkdir(parents=True, exist_ok=True)

    current: dict[str, Any] = {}
    if state_file.exists():
        try:
            current = json.loads(state_file.read_text(encoding="utf-8"))
        except Exception:
            current = {}

    current.update(updates)
    current["updated_at"] = datetime.now().isoformat()

    tmp_file = state_file.with_suffix(".tmp")
    tmp_file.write_text(json.dumps(current, indent=2), encoding="utf-8")
    tmp_file.replace(state_file)
    return current


def is_pid_alive(pid: int | None) -> bool:
    """Check if process with given PID is currently alive."""
    if pid is None or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def tail_file(path: Path | str | None, max_lines: int = 1) -> str:
    """Read the latest non-empty lines from a file efficiently."""
    if not path:
        return ""
    p = Path(path)
    if not p.is_file():
        return ""

    try:
        size = p.stat().st_size
        if size == 0:
            return ""

        # Read last up to 8KB to find the last non-empty line
        read_bytes = min(size, 8192)
        with open(p, "rb") as f:
            f.seek(size - read_bytes)
            content = f.read().decode("utf-8", errors="replace")

        lines = [line.strip() for line in content.splitlines() if line.strip()]
        if not lines:
            return ""
        return " | ".join(lines[-max_lines:])
    except Exception:
        return ""


def format_duration(seconds: float | None) -> str:
    if seconds is None or seconds < 0:
        return "—"
    sec = int(seconds)
    hours, remainder = divmod(sec, 3600)
    minutes, s = divmod(remainder, 60)
    if hours > 0:
        return f"{hours:02d}h {minutes:02d}m {s:02d}s"
    if minutes > 0:
        return f"{minutes:02d}m {s:02d}s"
    return f"{s:02d}s"


def format_bytes(num_bytes: int | None) -> str:
    if num_bytes is None:
        return "—"
    if num_bytes < 1024:
        return f"{num_bytes} B"
    if num_bytes < 1024 * 1024:
        return f"{num_bytes / 1024:.1f} KB"
    return f"{num_bytes / (1024 * 1024):.2f} MB"


def get_pipeline_state(repo_root: Path) -> dict[str, Any]:
    """Retrieve full pipeline state, cross-referencing process tables and disk files."""
    state_file = get_state_file(repo_root)
    state: dict[str, Any] = {}
    if state_file.exists():
        try:
            state = json.loads(state_file.read_text(encoding="utf-8"))
        except Exception:
            state = {}

    month_str = datetime.now().strftime("%Y_%m")
    day_str = datetime.now().strftime("%Y_%m_%d")
    date_results_dir = repo_root / "jobspy" / "scraped jobs" / "results" / month_str / day_str
    month_results_dir = repo_root / "jobspy" / "scraped jobs" / "results" / month_str
    results_dir = date_results_dir if date_results_dir.exists() else month_results_dir

    date_logs_dir = repo_root / "jobspy" / "scraped jobs" / "logs" / month_str / day_str
    month_logs_dir = repo_root / "jobspy" / "scraped jobs" / "logs" / month_str
    logs_dir = date_logs_dir if date_logs_dir.exists() else month_logs_dir

    master_pid = state.get("pid")
    is_master_running = is_pid_alive(master_pid)
    phase = state.get("phase", "IDLE")

    tasks_state = state.get("tasks", {})
    normalized_tasks: dict[str, dict[str, Any]] = {}

    for name in SCRAPER_NAMES:
        t_data = dict(tasks_state.get(name, {}))
        task_pid = t_data.get("pid")
        task_status = t_data.get("status", "pending")

        log_path = Path(t_data.get("log_path") or (logs_dir / f"{Path(name).stem}.log"))
        csv_path = Path(t_data.get("output_csv") or (results_dir / EXPECTED_OUTPUTS[name]))
        if not csv_path.exists() and (month_results_dir / EXPECTED_OUTPUTS[name]).exists():
            csv_path = month_results_dir / EXPECTED_OUTPUTS[name]

        # Check PID liveness
        if task_status == "running":
            if not is_pid_alive(task_pid):
                if csv_path.exists():
                    task_status = "success"
                else:
                    task_status = "failed"
                t_data["status"] = task_status

        # Calculate elapsed or duration
        duration_sec = t_data.get("duration_sec")
        if duration_sec is None:
            started_at = t_data.get("started_at")
            if started_at:
                try:
                    start_dt = datetime.fromisoformat(started_at)
                    if task_status == "running":
                        duration_sec = (datetime.now() - start_dt).total_seconds()
                    elif t_data.get("finished_at"):
                        end_dt = datetime.fromisoformat(t_data["finished_at"])
                        duration_sec = (end_dt - start_dt).total_seconds()
                except Exception:
                    pass

        csv_size = csv_path.stat().st_size if csv_path.is_file() else None
        last_log = tail_file(log_path, max_lines=1)

        normalized_tasks[name] = {
            "status": task_status,
            "pid": task_pid,
            "started_at": t_data.get("started_at"),
            "finished_at": t_data.get("finished_at"),
            "duration_sec": duration_sec,
            "log_path": str(log_path),
            "csv_path": str(csv_path),
            "csv_size": csv_size,
            "latest_log": last_log,
        }

    # Determine overall status
    is_any_task_running = any(t["status"] == "running" for t in normalized_tasks.values())
    is_pipeline_active = is_master_running or is_any_task_running

    if not is_pipeline_active:
        if phase in PHASES:
            phase = "FAILED" if any(t["status"] == "failed" for t in normalized_tasks.values()) else "COMPLETED"

    # Overall duration
    overall_duration = None
    started_at = state.get("started_at")
    if started_at:
        try:
            start_dt = datetime.fromisoformat(started_at)
            finished_at = state.get("finished_at")
            if finished_at and not is_pipeline_active:
                end_dt = datetime.fromisoformat(finished_at)
                overall_duration = (end_dt - start_dt).total_seconds()
            else:
                overall_duration = (datetime.now() - start_dt).total_seconds()
        except Exception:
            pass

    return {
        "pid": master_pid,
        "is_active": is_pipeline_active,
        "phase": phase,
        "started_at": started_at,
        "finished_at": state.get("finished_at"),
        "duration_sec": overall_duration,
        "tasks": normalized_tasks,
        "excel_path": state.get("excel_path"),
        "error": state.get("error"),
    }


def render_phase_pipeline(current_phase: str, is_active: bool):
    from rich.text import Text

    badges = []
    labels = [
        ("SCRAPING", "1. Scrapers"),
        ("EXCEL_BUILD", "2. Excel Build"),
        ("EMAIL", "3. Email Delivery"),
        ("INGEST", "4. Cockpit Ingest"),
    ]

    phase_indices = {name: i for i, (name, _) in enumerate(labels)}
    curr_idx = phase_indices.get(current_phase, -1)

    for i, (name, label) in enumerate(labels):
        if not is_active:
            if current_phase == "COMPLETED":
                icon, style = "✔", "bold green"
            elif current_phase == "FAILED":
                icon, style = ("✔", "green") if i < curr_idx else ("✖", "bold red") if i == curr_idx else ("○", "dim")
            else:
                icon, style = "○", "dim"
        else:
            if i < curr_idx:
                icon, style = "✔", "green"
            elif i == curr_idx:
                icon, style = "⟳", "bold yellow"
            else:
                icon, style = "○", "dim"

        badges.append(Text(f"[{icon} {label}]", style=style))

    res = Text("  ")
    for idx, badge in enumerate(badges):
        res.append_text(badge)
        if idx < len(badges) - 1:
            res.append(" ➔ ", style="bold bright_black")
    return res


def render_pipeline_dashboard(state: dict[str, Any], is_live: bool = True):
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text

    phase = state.get("phase", "IDLE")
    is_active = state.get("is_active", False)
    started_at = state.get("started_at", "—")
    duration = format_duration(state.get("duration_sec"))
    master_pid = state.get("pid") or "—"

    # Status styling
    if is_active:
        status_text = Text(f"● ACTIVE ({phase})", style="bold yellow")
    elif phase == "COMPLETED":
        status_text = Text("✔ COMPLETED", style="bold green")
    elif phase == "FAILED":
        status_text = Text("✖ FAILED", style="bold red")
    else:
        status_text = Text("○ IDLE", style="dim")

    # Header Panel
    header_table = Table.grid(expand=True, padding=(0, 2))
    header_table.add_column(justify="left")
    header_table.add_column(justify="right")

    left_info = Text.assemble(
        ("Pipeline Status: ", "bold"),
        status_text,
        ("  |  Started: ", "bold bright_black"),
        (str(started_at)[:19].replace("T", " ") if started_at else "—", "cyan"),
        ("  |  PID: ", "bold bright_black"),
        (str(master_pid), "magenta"),
    )

    right_info = Text.assemble(
        ("Elapsed: ", "bold"),
        (duration, "bold bright_cyan"),
    )
    header_table.add_row(left_info, right_info)

    pipeline_badges = render_phase_pipeline(phase, is_active)

    # Scraper Tasks Table
    table = Table(box=None, header_style="bold bright_white", expand=True)
    table.add_column("Scraper Task", style="cyan", no_wrap=True)
    table.add_column("Status", justify="center", no_wrap=True)
    table.add_column("PID", justify="right", style="magenta", no_wrap=True)
    table.add_column("Elapsed", justify="right", style="bright_cyan", no_wrap=True)
    table.add_column("Output Size", justify="right", no_wrap=True)
    table.add_column("Latest Log Activity", style="dim", ratio=1)

    tasks = state.get("tasks", {})
    for name in SCRAPER_NAMES:
        t = tasks.get(name, {})
        status = t.get("status", "pending")
        pid_str = str(t.get("pid") or "—")
        task_duration = format_duration(t.get("duration_sec"))
        csv_size = format_bytes(t.get("csv_size"))
        latest_log = t.get("latest_log") or "—"

        if status == "running":
            status_cell = Text("⟳ Running", style="bold yellow")
        elif status == "success":
            status_cell = Text("✔ Done", style="bold green")
        elif status == "failed":
            status_cell = Text("✖ Failed", style="bold red")
        else:
            status_cell = Text("○ Pending", style="dim")

        table.add_row(
            name,
            status_cell,
            pid_str,
            task_duration,
            csv_size,
            latest_log,
        )

    # Footer instructions
    if is_live and is_active:
        footer = Text("Press Ctrl+C to exit monitor (pipeline continues running in background)", style="dim")
    elif is_active:
        footer = Text("Pipeline running. Run 'cockpit pipeline' to watch live.", style="yellow")
    else:
        footer = Text("Run 'cockpit pipeline run' to launch a new pipeline run.", style="dim")

    layout_table = Table.grid(padding=1, expand=True)
    layout_table.add_row(Panel(header_table, title="[bold cyan]🚀 JobSpy Pipeline Monitor[/bold cyan]", border_style="cyan"))
    layout_table.add_row(Panel(pipeline_badges, title="[bold]Pipeline Phase Tracker[/bold]", border_style="bright_black"))
    layout_table.add_row(Panel(table, title="[bold]Parallel Scraper Tasks (8)[/bold]", border_style="bright_black"))
    layout_table.add_row(footer)

    return layout_table


def resolve_pipeline_python(repo_root: Path) -> str:
    """Resolve the Python executable that has scraper dependencies installed."""
    env_override = os.environ.get("JOBSPY_PYTHON")
    if env_override and Path(env_override).is_file() and os.access(env_override, os.X_OK):
        return env_override

    candidates = [
        repo_root / ".venv" / "bin" / "python",
        Path.home() / ".local" / "venvs" / "jobspy-custom-3.12" / "bin" / "python",
        Path(sys.executable),
    ]

    for candidate in candidates:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            try:
                res = subprocess.run(
                    [str(candidate), "-c", "import bs4, ats_scrapers"],
                    capture_output=True,
                    timeout=3,
                )
                if res.returncode == 0:
                    return str(candidate)
            except Exception:
                pass

    for candidate in candidates:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)

    return sys.executable


def launch_pipeline(repo_root: Path) -> subprocess.Popen:
    """Launch run_pipeline.py as a detached background process with output logged."""
    runner = repo_root / "run_pipeline.py"
    log_file = repo_root / "pipeline_cron.log"
    out = open(log_file, "a", encoding="utf-8")

    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    existing_pythonpath = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = f"{repo_root}:{existing_pythonpath}" if existing_pythonpath else str(repo_root)

    py_bin = resolve_pipeline_python(repo_root)
    env["JOBSPY_PYTHON"] = py_bin

    p = subprocess.Popen(
        [py_bin, str(runner)],
        stdout=out,
        stderr=subprocess.STDOUT,
        cwd=str(repo_root),
        env=env,
        start_new_session=True,
    )
    return p


def monitor_loop(repo_root: Path, once: bool = False, refresh_interval: float = 1.0) -> None:
    """Live monitor loop for terminal viewing."""
    from rich.console import Console
    from rich.live import Live

    console = Console()

    if once:
        state = get_pipeline_state(repo_root)
        console.print(render_pipeline_dashboard(state, is_live=False))
        return

    try:
        with Live(console=console, refresh_per_second=int(1.0 / refresh_interval) or 1, screen=False) as live:
            while True:
                state = get_pipeline_state(repo_root)
                live.update(render_pipeline_dashboard(state, is_live=True))
                if not state.get("is_active"):
                    # Pipeline completed or failed
                    time.sleep(refresh_interval)
                    break
                time.sleep(refresh_interval)
    except KeyboardInterrupt:
        console.print("\n[dim]Detached from monitor. Pipeline continues running in the background.[/dim]")
