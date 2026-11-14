from __future__ import annotations

import os
import plistlib
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Annotated, Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)
from rich.table import Table

from cockpit import db, macos
from cockpit.models import FunnelState
from cockpit.normalizer import REPO_ROOT

app = typer.Typer(no_args_is_help=False, add_completion=False)
console = Console()


@app.callback(invoke_without_command=True)
def main(ctx: typer.Context) -> None:
    """JobCockpit — terminal application engine and intelligence pipeline."""
    if ctx.invoked_subcommand is None:
        tui()


AGENT_LABEL = "com.jobcockpit.sync"
AGENT_PLIST = Path.home() / "Library" / "LaunchAgents" / f"{AGENT_LABEL}.plist"


def _list_candidate_result_dirs() -> list[tuple[str, int, Path, str]]:
    """List all date directories with *_jobs.csv files, sorted by date descending.

    Returns list of tuples: (date_str, csv_count, dir_path, status_tag)
    where status_tag is '[NEW]', '[PARTIAL]', or '[INGESTED]'.
    """
    results_root = REPO_ROOT / "jobspy" / "scraped jobs" / "results"
    if not results_root.exists():
        return []

    month_dirs = sorted(
        [d for d in results_root.iterdir() if d.is_dir() and re.match(r"^\d{4}_\d{2}$", d.name)],
        reverse=True,
    )
    manifest_paths: set[str] = set()
    try:
        with db.session() as con:
            manifest_paths = db.get_ingest_manifest_paths(con)
    except Exception:
        pass

    candidates: list[tuple[str, int, Path, str]] = []
    for md in month_dirs:
        for sd in sorted(md.iterdir(), reverse=True):
            if sd.is_dir() and re.match(r"^\d{4}_\d{2}_\d{2}$", sd.name):
                csv_files = list(sd.glob("*_jobs.csv"))
                if not csv_files:
                    continue
                ingested_count = sum(
                    1
                    for f in csv_files
                    if str(f.resolve()) in manifest_paths
                    or (f.is_relative_to(REPO_ROOT) and str(f.relative_to(REPO_ROOT)) in manifest_paths)
                )
                if ingested_count == 0:
                    status_tag = "[NEW]"
                elif ingested_count == len(csv_files):
                    status_tag = "[INGESTED]"
                else:
                    status_tag = "[PARTIAL]"
                candidates.append((sd.name, len(csv_files), sd, status_tag))

    # Sort primarily by date descending
    candidates.sort(key=lambda item: item[0], reverse=True)
    return candidates


def _fuzzy_score(query: str, target: str) -> int:
    """Compute fuzzy match score between query and target string. Higher is better, -1 if no match."""
    q = query.lower().replace("/", "_").replace("-", "_")
    t = target.lower()
    if not q:
        return 0
    if q in t:
        return 1000 - t.index(q)
    ti = 0
    score = 0
    consecutive = 0
    for ch in q:
        idx = t.find(ch, ti)
        if idx == -1:
            return -1
        if idx == ti:
            consecutive += 1
            score += 10 + (consecutive * 5)
        else:
            consecutive = 0
            score += 5
        ti = idx + 1
    return score


def _curses_fuzzy_select(candidates: list[tuple[str, int, Path, str]]) -> tuple[str, Path | None] | None:
    """Built-in curses interactive fuzzy finder for terminal environments."""
    import curses

    # Options include top action item if any candidates exist
    has_candidates = bool(candidates)

    def _menu(stdscr: curses.window) -> tuple[str, Path | None] | None:
        curses.curs_set(1)
        curses.use_default_colors()
        query = ""
        selected_idx = 0

        while True:
            stdscr.clear()
            max_y, max_x = stdscr.getmaxyx()

            # Build items list: special action entries + candidate directories
            items: list[tuple[str, Path | None, str, int, str]] = []
            if has_candidates:
                # Count uningested files across all candidates
                new_days = sum(1 for c in candidates if c[3] in ("[NEW]", "[PARTIAL]"))
                new_csvs = sum(c[1] for c in candidates if c[3] in ("[NEW]", "[PARTIAL]"))
                all_label = f"⚡ Ingest ALL Uningested Results ({new_days} days, {new_csvs} CSVs)"
                items.append(("all_uningested", None, all_label, new_csvs, "[ACTION]"))

            for c in candidates:
                items.append(("date", c[2], c[0], c[1], c[3]))

            # Filter candidates based on query
            if not query.strip():
                matched = items[:]
            else:
                scored = []
                for item in items:
                    label = f"{item[2]} {item[3]} csv {item[4]}"
                    s = _fuzzy_score(query, label)
                    if s >= 0:
                        scored.append((s, item))
                # Sort by score descending
                scored.sort(key=lambda x: x[0], reverse=True)
                matched = [item for _, item in scored]

            if selected_idx >= len(matched):
                selected_idx = max(0, len(matched) - 1)

            # Header
            header = f"Select Results Directory | Total: {len(candidates)} dirs (↑/↓: navigate, Enter: select, Esc/q: cancel)"
            stdscr.addnstr(0, 0, header[:max_x], max_x, curses.A_BOLD)
            search_line = f"Search: {query}"
            stdscr.addnstr(1, 0, search_line[:max_x], max_x, curses.A_UNDERLINE)

            # Column legend
            legend = "  STATUS      DATE/ACTION                           CSVS"
            stdscr.addnstr(3, 0, legend[:max_x], max_x, curses.A_DIM)

            # Candidate rows
            list_y = 4
            available_rows = max_y - list_y - 1

            start_row = 0
            if selected_idx >= available_rows:
                start_row = selected_idx - available_rows + 1

            for display_i, i in enumerate(range(start_row, min(len(matched), start_row + available_rows))):
                action_type, path, label, count, status = matched[i]
                if action_type == "all_uningested":
                    line_str = f"  {status:<11} {label:<38} {count:>3} CSV(s)"
                else:
                    rel_path = path.relative_to(REPO_ROOT) if path and path.is_relative_to(REPO_ROOT) else (path or "")
                    line_str = f"  {status:<11} {label:<16} {count:>2} CSV(s)   {rel_path}"
                is_selected = (i == selected_idx)
                attr = curses.A_REVERSE | curses.A_BOLD if is_selected else curses.A_NORMAL
                marker = "▶" if is_selected else " "
                stdscr.addnstr(list_y + display_i, 0, f"{marker}{line_str[1:]}"[:max_x], max_x, attr)

            stdscr.refresh()
            stdscr.move(1, min(len(search_line), max_x - 1))

            ch = stdscr.getch()
            if ch in (27,):  # ESC
                return None
            elif ch in (curses.KEY_ENTER, 10, 13):
                if matched and 0 <= selected_idx < len(matched):
                    chosen = matched[selected_idx]
                    return (chosen[0], chosen[1])
                return None
            elif ch in (curses.KEY_UP, 65):  # Up arrow
                selected_idx = max(0, selected_idx - 1)
            elif ch in (curses.KEY_DOWN, 66):  # Down arrow
                if matched:
                    selected_idx = min(len(matched) - 1, selected_idx + 1)
            elif ch in (curses.KEY_BACKSPACE, 127, 8):
                query = query[:-1]
                selected_idx = 0
            elif 32 <= ch <= 126:
                query += chr(ch)
                selected_idx = 0

    return curses.wrapper(_menu)


def _interactive_fuzzy_select_dir() -> tuple[str, Path | None] | None:
    """Prompt user to select a results directory using fzf (if available) or an interactive curses fuzzy menu.

    Returns tuple of (action_type, path):
    - ("all_uningested", None)
    - ("date", Path)
    - None if cancelled
    """
    candidates = _list_candidate_result_dirs()
    if not candidates:
        return None

    new_days = sum(1 for c in candidates if c[3] in ("[NEW]", "[PARTIAL]"))
    new_csvs = sum(c[1] for c in candidates if c[3] in ("[NEW]", "[PARTIAL]"))

    # Check for fzf binary
    fzf_bin = shutil.which("fzf") or (Path("/opt/homebrew/bin/fzf") if Path("/opt/homebrew/bin/fzf").is_file() else None)
    if fzf_bin and sys.stdin.isatty():
        # Lines formatted for fzf: ACTION/STATUS | DATE | CSV COUNT | PATH
        lines = [
            f"[ACTION]    │ ⚡ Ingest ALL Uningested Results ({new_days} days, {new_csvs} CSVs) │ ALL_UNINGESTED",
        ]
        for date_str, count, path, status in candidates:
            lines.append(f"{status:<11} │ {date_str}  │  {count:>2} CSV(s)  │  {path}")

        try:
            proc = subprocess.Popen(
                [
                    str(fzf_bin),
                    "--height=40%",
                    "--reverse",
                    "--prompt=Select results date > ",
                    "--header=Use fuzzy search (e.g. 'new', '10/27', 'all'). Enter to select, Esc to cancel.",
                ],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            stdout, _ = proc.communicate(input="\n".join(lines))
            if proc.returncode == 0 and stdout.strip():
                selected_line = stdout.strip()
                if "ALL_UNINGESTED" in selected_line:
                    return ("all_uningested", None)
                parts = selected_line.split("│")
                if len(parts) >= 4:
                    selected_path = Path(parts[-1].strip())
                    return ("date", selected_path)
        except Exception:
            pass

    # Fallback to curses-based interactive fuzzy finder
    if sys.stdin.isatty():
        try:
            return _curses_fuzzy_select(candidates)
        except Exception:
            pass

    # Fallback if non-interactive: pick the most recent date with maximum CSV count
    best = sorted(candidates, key=lambda item: (item[1], item[0]), reverse=True)
    return ("date", best[0][2]) if best else None


def _resolve_results_date_dir(date_query: str) -> Path:
    """Resolve a user-provided date string (e.g. '10/27', '2026-10-27', '2026_10_27', 'latest') to a results dir."""
    results_root = REPO_ROOT / "jobspy" / "scraped jobs" / "results"
    cleaned = date_query.strip().lower()

    if cleaned in ("latest", "today"):
        return _default_results_dir()

    # Parse month and day components
    # Supports: MM/DD, MM-DD, MM_DD, YYYY/MM/DD, YYYY-MM-DD, YYYY_MM_DD
    m = re.match(r"^(?:(\d{4})[_\-\/])?(\d{1,2})[_\-\/](\d{1,2})$", cleaned)
    if not m:
        raise typer.BadParameter(f"Unrecognized date format '{date_query}'. Use MM/DD (e.g. 10/27) or YYYY_MM_DD.")

    year_str, month_str, day_str = m.group(1), m.group(2).zfill(2), m.group(3).zfill(2)

    # If year is omitted, look in results directory for matching year_month/year_month_day
    if not year_str:
        if results_root.exists():
            candidate_dirs = sorted(
                [d for d in results_root.glob(f"????_{month_str}/????_{month_str}_{day_str}") if d.is_dir()],
                reverse=True,
            )
            if candidate_dirs:
                return candidate_dirs[0]
        year_str = str(datetime.now().year)

    target_dir = results_root / f"{year_str}_{month_str}" / f"{year_str}_{month_str}_{day_str}"
    return target_dir


def _collect_recent_days_inputs(days: int = 5) -> list[Path]:
    """Collect all *_jobs.csv from results directories within the most recent N days."""
    candidates = _list_candidate_result_dirs()
    if not candidates:
        return []

    # Filter directories whose date is within `days` of either now or the latest available directory date
    latest_dt: datetime | None = None
    dir_dates: list[tuple[datetime, Path]] = []
    for c in candidates:
        date_str, path = c[0], c[2]
        try:
            dt = datetime.strptime(date_str, "%Y_%m_%d")
            dir_dates.append((dt, path))
            if latest_dt is None or dt > latest_dt:
                latest_dt = dt
        except ValueError:
            continue

    if not dir_dates or latest_dt is None:
        return []

    # Take directories within `days` of latest date, or take top `days` directories
    cutoff = latest_dt - timedelta(days=days - 1)
    matched_dirs = [path for dt, path in dir_dates if dt >= cutoff]
    if not matched_dirs:
        # Fallback to top N most recent date directories
        matched_dirs = [path for _, path in dir_dates[:days]]

    all_csvs: list[Path] = []
    for d in matched_dirs:
        all_csvs.extend(sorted(d.glob("*_jobs.csv")))
    return all_csvs


def _collect_all_uningested_inputs() -> list[Path]:
    """Collect all *_jobs.csv from candidate directories that are not fully recorded in ingest_manifest."""
    candidates = _list_candidate_result_dirs()
    all_csvs: list[Path] = []
    for date_str, count, path, status in candidates:
        if status in ("[NEW]", "[PARTIAL]"):
            all_csvs.extend(sorted(path.glob("*_jobs.csv")))
    return all_csvs


def _collect_cumulative_date_inputs(target_dir: Path) -> list[Path]:
    """Collect all *_jobs.csv from results date directories up to and including target_dir date."""
    candidates = _list_candidate_result_dirs()
    target_date_str = target_dir.name
    # Filter candidates whose date <= target_date_str
    # Candidates are sorted date descending
    selected_dirs: list[Path] = []
    for date_str, count, path, status in candidates:
        if date_str <= target_date_str:
            selected_dirs.append(path)

    all_csvs: list[Path] = []
    for d in selected_dirs:
        all_csvs.extend(sorted(d.glob("*_jobs.csv")))
    return all_csvs


def _expand_inputs(
    inputs: list[Path] | None,
    date_str: str | None = None,
    select: bool = False,
    days: int | None = None,
    all_uningested: bool = False,
    cumulative: bool = False,
    latest: bool = False,
) -> list[Path]:
    if all_uningested:
        csvs = _collect_all_uningested_inputs()
        if not csvs:
            raise typer.BadParameter("No uningested *_jobs.csv found across results directories.")
        return csvs

    if days is not None:
        csvs = _collect_recent_days_inputs(days=days)
        if not csvs:
            raise typer.BadParameter(f"No *_jobs.csv found in results directories from the last {days} days.")
        return csvs

    if date_str:
        target_dir = _resolve_results_date_dir(date_str)
        if not target_dir.exists():
            raise typer.BadParameter(f"Results directory for date '{date_str}' does not exist: {target_dir}")
        if cumulative:
            csvs = _collect_cumulative_date_inputs(target_dir)
        else:
            csvs = sorted(target_dir.glob("*_jobs.csv"))
        if not csvs:
            raise typer.BadParameter(f"No *_jobs.csv found in date directory: {target_dir}")
        return csvs

    # In interactive mode when no specific target is provided and --latest is not set:
    should_prompt_select = select or (inputs is None and not latest and sys.stdin.isatty())
    if should_prompt_select:
        selection = _interactive_fuzzy_select_dir()
        if not selection:
            raise typer.BadParameter("Selection cancelled or no results directory available.")
        if isinstance(selection, tuple):
            action_type, selected_dir = selection
        else:
            action_type, selected_dir = ("date", selection)

        if action_type == "all_uningested":
            csvs = _collect_all_uningested_inputs()
            if not csvs:
                raise typer.BadParameter("No uningested *_jobs.csv found across results directories.")
            return csvs
        if selected_dir:
            if cumulative:
                csvs = _collect_cumulative_date_inputs(selected_dir)
            else:
                csvs = sorted(selected_dir.glob("*_jobs.csv"))
            if not csvs:
                raise typer.BadParameter(f"No *_jobs.csv found in selected directory: {selected_dir}")
            return csvs

    if not inputs:
        return _find_default_inputs()

    expanded: list[Path] = []
    for p in inputs:
        # Check if the argument is a date specification (e.g. '10/27' or '2026_10_27')
        if not p.exists() and re.match(r"^(?:\d{4}[_\-\/])?\d{1,2}[_\-\/]\d{1,2}$", str(p)):
            expanded.extend(_expand_inputs(None, date_str=str(p), cumulative=cumulative))
        elif p.is_dir():
            csvs = sorted(p.glob("*_jobs.csv"))
            if not csvs:
                raise typer.BadParameter(f"No *_jobs.csv found in directory: {p}")
            expanded.extend(csvs)
        else:
            expanded.append(p)
    return expanded


def _default_results_dir() -> Path:
    now = datetime.now()
    month_str = now.strftime("%Y_%m")
    day_str = now.strftime("%Y_%m_%d")
    today_dir = REPO_ROOT / "jobspy" / "scraped jobs" / "results" / month_str / day_str
    results_root = REPO_ROOT / "jobspy" / "scraped jobs" / "results"

    if not results_root.exists():
        return today_dir

    # Inspect the most recent month directories (current and previous)
    month_dirs = sorted(
        [d for d in results_root.iterdir() if d.is_dir() and re.match(r"^\d{4}_\d{2}$", d.name)],
        reverse=True,
    )
    candidates: list[tuple[int, str, Path]] = []
    for md in month_dirs[:2]:
        for sd in md.iterdir():
            if sd.is_dir() and re.match(r"^\d{4}_\d{2}_\d{2}$", sd.name):
                csv_count = len(list(sd.glob("*_jobs.csv")))
                if csv_count > 0:
                    candidates.append((csv_count, sd.name, sd))

    if candidates:
        # Prefer maximum CSV count first, then most recent directory date
        candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
        return candidates[0][2]

    month_dir = results_root / month_str
    return today_dir if today_dir.exists() else month_dir


def _find_default_inputs() -> list[Path]:
    csvs = sorted(_default_results_dir().glob("*_jobs.csv"))
    if csvs:
        return csvs
    month_csvs = sorted((REPO_ROOT / "jobspy" / "scraped jobs" / "results" / datetime.now().strftime("%Y_%m")).glob("*_jobs.csv"))
    if month_csvs:
        return month_csvs
    downloads = Path.home() / "Downloads"
    if downloads.exists():
        excel_reports = sorted(downloads.glob("Job_Report_*.xlsx"), key=lambda p: p.stat().st_mtime, reverse=True)
        if excel_reports:
            return [excel_reports[0]]
    return []


def _load_input_frame(p: Path, sheet: str | None = None) -> "pd.DataFrame":
    import pandas as pd

    if p.suffix.lower() in (".xlsx", ".xls"):
        xl = pd.ExcelFile(p)
        if sheet and sheet != "all":
            df = pd.read_excel(xl, sheet_name=sheet)
            if "segment" not in df.columns:
                df = df.assign(segment=sheet.lower().replace(" ", "_"))
            return df
        if sheet == "all":
            base_sheets = [s for s in xl.sheet_names if not s.endswith("(Filtered)") and s != "Action Items"]
            return pd.concat(
                [pd.read_excel(xl, sheet_name=s).assign(segment=s.lower().replace(" ", "_")) for s in base_sheets],
                ignore_index=True,
            )
        if "Action Items" in xl.sheet_names:
            return pd.read_excel(xl, sheet_name="Action Items")
        base_sheets = [s for s in xl.sheet_names if not s.endswith("(Filtered)")]
        return pd.concat(
            [pd.read_excel(xl, sheet_name=s).assign(segment=s.lower().replace(" ", "_")) for s in base_sheets],
            ignore_index=True,
        )
    return pd.read_csv(p).assign(segment=p.stem.removesuffix("_jobs"))


@app.command()
def ingest(
    inputs: Annotated[Optional[list[Path]], typer.Argument(help="JobSpy CSVs, directory, date (e.g. 10/27, 2026_10_27), or Excel workbooks; default: latest results dir or ~/Downloads/Job_Report_*.xlsx")] = None,
    select: Annotated[bool, typer.Option("--select", "-s", help="Open an interactive fuzzy finder (fzf) to choose which results date directory to ingest")] = False,
    all_uningested: Annotated[bool, typer.Option("--all-uningested", "-u", help="Ingest all uningested results across all date directories")] = False,
    cumulative: Annotated[bool, typer.Option("--cumulative", "-c", help="Ingest up to the selected date cumulatively across all earlier uningested scan results")] = False,
    latest: Annotated[bool, typer.Option("--latest", "-l", help="Force ingesting latest results directory without prompt even in interactive terminal")] = False,
    days: Annotated[Optional[int], typer.Option("--days", help="Bulk ingest CSVs from results directories across the last N days (default for bulk: 5)")] = None,
    date: Annotated[Optional[str], typer.Option("--date", "-d", help="Ingest from specific date directory under results/ (e.g. '10/27', '2026_10_27', '2026-10-27')")] = None,
    sheet: Annotated[Optional[str], typer.Option(help="Specific sheet name for Excel files, or 'all' for all raw sheets (default: 'Action Items' if present)")] = None,
    status: Annotated[bool, typer.Option("--status", "-S", help="Show most recently ingested suite and full ingest log")] = False,
) -> None:
    """Dedupe, score and upsert scraped jobs into DuckDB (or pass 'status' / --status)."""
    import pandas as pd
    from rich.progress import (
        BarColumn,
        MofNCompleteColumn,
        Progress,
        SpinnerColumn,
        TaskProgressColumn,
        TextColumn,
        TimeElapsedColumn,
        TimeRemainingColumn,
    )
    from cockpit import intel as intel_module
    from cockpit import pipeline_monitor

    if status or (inputs and len(inputs) == 1 and str(inputs[0]) == "status" and not inputs[0].exists()):
        summary = pipeline_monitor.get_ingest_summary(REPO_ROOT, db_path=db.DB_PATH)
        console.print(pipeline_monitor.render_ingest_dashboard(summary))
        return

    paths = _expand_inputs(
        inputs,
        date_str=date,
        select=select,
        days=days,
        all_uningested=all_uningested,
        cumulative=cumulative,
        latest=latest,
    )
    if not paths:
        raise typer.BadParameter(f"No CSVs found in {_default_results_dir()} and no Job_Report_*.xlsx found in ~/Downloads; pass paths explicitly.")

    # Determine common directory or source label for user clarity
    try:
        common_parent = Path(os.path.commonpath([str(p.resolve()) for p in paths]))
        source_label = common_parent.relative_to(REPO_ROOT) if common_parent.is_relative_to(REPO_ROOT) else common_parent
    except Exception:
        source_label = paths[0].parent.name

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
        TimeRemainingColumn(),
        console=console,
    ) as progress:
        load_task = progress.add_task(f"[cyan]Loading input files from {source_label}...", total=len(paths))
        loaded_frames = []
        file_row_counts: dict[Path, int] = {}
        for p in paths:
            progress.update(load_task, description=f"[cyan]Loading {p.name}...")
            df_i = _load_input_frame(p, sheet=sheet)
            file_row_counts[p] = len(df_i)
            loaded_frames.append(df_i)
            progress.advance(load_task)
        frame = pd.concat(loaded_frames, ignore_index=True)
        progress.update(load_task, description=f"[green]✔ Loaded {len(paths)} file(s) ({len(frame):,} rows) from {source_label}")

        score_task = progress.add_task("[yellow]Scoring and deduping postings...", total=len(frame))

        def on_score_progress(current: int, total: int) -> None:
            progress.update(score_task, completed=current)

        with db.session() as con:
            stats = db.upsert_jobspy_frame(con, frame, progress_callback=on_score_progress)
            progress.update(score_task, completed=len(frame), description="[green]✔ Scored and deduped postings")

            # Record successfully ingested files into ingest_manifest and ingest_history
            recorded_files = []
            suite_dates = []
            for p in paths:
                try:
                    rel = str(p.relative_to(REPO_ROOT)) if p.is_relative_to(REPO_ROOT) else str(p.resolve())
                    date_val = p.parent.name if re.match(r"^\d{4}_\d{2}_\d{2}$", p.parent.name) else datetime.now().strftime("%Y_%m_%d")
                    size = p.stat().st_size if p.exists() else 0
                    mtime = p.stat().st_mtime if p.exists() else 0.0
                    rows_i = file_row_counts.get(p, 0)
                    db.record_ingested_file(con, rel, date_val, file_size=size, mtime_epoch=mtime, row_count=rows_i)
                    recorded_files.append({"name": p.name, "path": rel, "size": size, "rows": rows_i})
                    suite_dates.append(date_val)
                except Exception:
                    pass

            db_task = progress.add_task("[magenta]Reconciling holds & saved jobs...", total=None)
            expired = db.expire_stale_referral_holds(con)
            intel_module.reconcile_saved_jobs(con)
            progress.update(db_task, description="[green]✔ Reconciled holds and saved jobs", completed=1, total=1)

    pipeline_monitor.record_ingest_run(
        REPO_ROOT,
        {
            "ingested_at": datetime.now().isoformat(),
            "date_str": suite_dates[-1] if suite_dates else datetime.now().strftime("%Y_%m_%d"),
            "source_label": str(source_label),
            "files": recorded_files,
            "seen": int(stats.get("seen", 0)),
            "inserted": int(stats.get("inserted", 0)),
            "duplicates": int(stats.get("duplicates", 0)),
            "invalid": int(stats.get("invalid", 0)),
            "stale_holds_flagged": int(expired),
        },
    )

    console.print(
        f"[bold green]✔ Ingest complete:[/bold green] [cyan]{len(paths)} file(s)[/cyan] from [yellow]{source_label}[/yellow] "
        f"({', '.join(p.name for p in paths)}) {stats} | stale holds flagged: {expired}"
    )


@app.command("bulk-ingest")
def bulk_ingest(
    days: Annotated[int, typer.Option("--days", "-n", help="Number of recent days of results directories to ingest")] = 5,
    sheet: Annotated[Optional[str], typer.Option(help="Specific sheet name for Excel files, or 'all' for all raw sheets (default: 'Action Items' if present)")] = None,
) -> None:
    """Bulk ingest all scraped CSVs across the last N days (default: 5 days)."""
    ingest(inputs=None, select=False, days=days, date=None, sheet=sheet)


@app.command()
def enrich(
    limit: Annotated[int, typer.Option("--limit", "-l", help="Maximum top-scoring stub postings to enrich")] = 100,
    min_score: Annotated[float, typer.Option("--min-score", "-m", help="Minimum priority_score for enrichment")] = 10.0,
) -> None:
    """Fetch full JDs for top-scoring QUEUED stub postings and re-score them."""
    from cockpit import enrich as enrich_module

    with db.session() as con:
        stats = enrich_module.enrich_queued_jobs(con, limit=limit, min_score=min_score)
    console.print(
        f"[bold green]✔ Enrich complete:[/bold green] checked={stats['checked']} "
        f"enriched={stats['enriched']} dropped_below_{min_score:g}={stats['dropped']}"
    )


@app.command()
def rescore(
    audit: Annotated[bool, typer.Option("--audit", "-a", help="Show breakdown of disqualification reasons")] = False,
) -> None:
    """Re-apply scoring_rules.toml to QUEUED jobs (applied/skipped/held are untouched)."""
    with db.session() as con:
        before = db.scoreboard(con)
        queued_before = before.get("queued", 0)
        recent_before = before.get("queued_72h", 0)

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            MofNCompleteColumn(),
            TimeElapsedColumn(),
            TimeRemainingColumn(),
            console=console,
        ) as progress:
            total_rows = con.execute("SELECT count(*) FROM jobs_master WHERE status = 'QUEUED'").fetchone()[0]
            task = progress.add_task("[yellow]Rescoring queued jobs...", total=total_rows)

            def on_progress(cur: int, tot: int) -> None:
                progress.update(task, completed=cur)

            total_rescored, audit_counts = db.rescore_queued(
                con, progress_callback=on_progress, with_audit=True
            )
            progress.update(task, completed=total_rows, description="[green]✔ Rescoring complete")

        after = db.scoreboard(con)
        queued_after = after.get("queued", 0)
        recent_after = after.get("queued_72h", 0)

    console.print(
        f"[bold green]✔ Rescore complete:[/bold green] rescored [cyan]{total_rescored:,}[/cyan] total queued row(s)"
    )
    console.print(
        f"  Queue >= min_score ({db.min_score():.0f}): [bold red]{queued_before:,}[/bold red] ➔ [bold green]{queued_after:,}[/bold green] "
        f"([yellow]{queued_before - queued_after:+,} noise filtered[/yellow])"
    )
    console.print(
        f"  72h Fresh Queue:           [bold red]{recent_before:,}[/bold red] ➔ [bold green]{recent_after:,}[/bold green] "
        f"([yellow]{recent_before - recent_after:+,} noise filtered[/yellow])"
    )

    if audit and audit_counts:
        table = Table(title="Disqualification Audit Breakdown", show_header=True, header_style="bold magenta")
        table.add_column("Disqualification Reason", style="cyan")
        table.add_column("Count", justify="right", style="green")
        for reason, count in sorted(audit_counts.items(), key=lambda x: x[1], reverse=True):
            table.add_row(reason, f"{count:,}")
        console.print(table)


@app.command()
def tui() -> None:
    """Launch the Textual cockpit."""
    from cockpit.tui import JobCockpitApp

    JobCockpitApp().run()


@app.command()
def sprint(
    limit: int = 15,
    segment: Annotated[Optional[str], typer.Option(help="Only this scraper sheet, e.g. seattle")] = None,
    location: Annotated[Optional[str], typer.Option(help="Location text, e.g. 'wa' or 'remote'")] = None,
    recent: Annotated[bool, typer.Option("--recent", "-r", help="Only jobs from the last 72 hours")] = False,
) -> None:
    """Blocking loop: stage resume + open URL, then record the outcome."""
    from cockpit import intel as intel_module

    export_dir = intel_module.resolve_export_dir()
    status = "72H" if recent else "QUEUED"
    with db.session() as con:
        jobs = db.fetch_jobs(con, status, db.min_score(), segment, location)[:limit]
    outcomes = {"a": FunnelState.APPLIED, "s": FunnelState.SKIPPED, "h": FunnelState.REFERRAL_HOLD}
    for i, job in enumerate(jobs, 1):
        console.print(Panel(
            f"{job['title']} @ {job['company']}\n{job['location'] or ''} · {job['site']} · score {job['priority_score']:.0f}\n"
            f"{job['apply_url']}\nresume: {macos.posix_path(job['resume_path'])}",
            title=f"{i}/{len(jobs)}",
        ))
        macos.copy_resume_path(job["resume_path"])
        macos.open_url(job["apply_url"])
        choice = ""
        while choice not in (*outcomes, "b", "q"):
            choice = typer.prompt("[a]pplied [s]kip [h]old [b]ookmark [q]uit").strip().lower()[:1]
        if choice == "q":
            break
        if choice == "b":
            job_folder = intel_module.job_folder_path(job, export_dir)
            out_file = job_folder / "05_interview_dossier.md"
            with db.session() as con:
                is_saved = db.toggle_saved(con, job["dedup_key"], intel_path=str(out_file), folder_path=str(job_folder))
                if is_saved:
                    job_folder.mkdir(parents=True, exist_ok=True)
                    posting_file = job_folder / "00_job_posting.md"
                    if not posting_file.exists():
                        posting_file.write_text(
                            f"---\ndedup_key: {job['dedup_key']}\ncompany: \"{job.get('company')}\"\ntitle: \"{job.get('title')}\"\n"
                            f"location: \"{job.get('location') or ''}\"\napply_url: \"{job.get('apply_url')}\"\n"
                            f"priority_score: {job.get('priority_score', 0):.0f}\nfit_score: pending\n---\n\n"
                            f"# {job.get('company')} — {job.get('title')}\n\n## Original Job Description\n\n{job.get('description') or 'No description provided.'}\n",
                            "utf-8",
                        )
                    if not out_file.exists():
                        out_file.write_text(intel_module.render_dossier(job), "utf-8")
                    intel_module.rebuild_index(con, export_dir)
            console.print(f"[bold {'green' if is_saved else 'yellow'}]{'Saved' if is_saved else 'Unsaved'}[/bold {'green' if is_saved else 'yellow'}] {job['title']} @ {job['company']}")
            continue
        note = typer.prompt("contact/note", default="") if choice == "h" else None
        with db.session() as con:
            db.transition_job_state(con, job["dedup_key"], outcomes[choice], "sprint", note or None)


@app.command("sync-saved")
def sync_saved(
    export_dir: Annotated[Optional[Path], typer.Option(help="Path to export directory (default: ~/Documents/jobspy-cockpit)")] = None,
) -> None:
    """Reconcile saved jobs and dossiers between DuckDB and export folder."""
    from cockpit import intel as intel_module

    out_dir = export_dir or intel_module.resolve_export_dir()
    with db.session() as con:
        reconciled = intel_module.reconcile_saved_jobs(con, out_dir)
        intel_module.rebuild_index(con, out_dir)
        b = db.scoreboard(con)
    console.print(
        f"[green]Synced saved jobs.[/green] Total saved in database: [bold]{b['saved']}[/bold] "
        f"(reconciled {reconciled} from {out_dir})"
    )


@app.command("verify-open")
def verify_open_cmd(
    limit: Annotated[int, typer.Option("--limit", "-l", help="Maximum postings to verify")] = 50,
    recent: Annotated[bool, typer.Option("--recent", "-r", help="Verify recent/72h queued postings instead of only internships")] = False,
    all_queued: Annotated[bool, typer.Option("--all-queued", "-a", help="Verify all queued postings")] = False,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Check status without writing to DuckDB")] = False,
) -> None:
    """Verify whether apply URLs for queued postings are still active/open."""
    from cockpit import internships as intern_module
    import requests

    where_clause = "WHERE is_internship = TRUE AND (is_open IS NULL OR is_open = TRUE)"
    desc = "internship"
    if recent:
        where_clause = (
            "WHERE status = 'QUEUED' AND (is_open IS NULL OR is_open = TRUE) "
            "AND (epoch(discovered_at) >= epoch(now()) - 72 * 3600 "
            "OR (date_posted IS NOT NULL AND try_cast(date_posted as DATE) >= CURRENT_DATE - INTERVAL 3 DAY))"
        )
        desc = "72h fresh queued"
    elif all_queued:
        where_clause = "WHERE status = 'QUEUED' AND (is_open IS NULL OR is_open = TRUE)"
        desc = "queued"

    with db.session() as con:
        jobs = con.execute(
            f"""SELECT dedup_key, company, title, apply_url
               FROM jobs_master
               {where_clause}
               ORDER BY priority_score DESC
               LIMIT ?""",
            [limit],
        ).fetchall()

    if not jobs:
        console.print(f"[yellow]No active {desc} postings found to verify.[/yellow]")
        return

    console.print(f"[cyan]Verifying {len(jobs)} {desc} postings{' (DRY RUN)' if dry_run else ''}...[/cyan]")
    session = requests.Session()
    counts = {"open": 0, "closed": 0, "unknown": 0}

    for idx, (key, company, title, apply_url) in enumerate(jobs, 1):
        is_open = intern_module.check_url_open(apply_url, session=session)
        if is_open is True:
            counts["open"] += 1
            status_desc = "[green]LIVE[/green]"
        elif is_open is False:
            counts["closed"] += 1
            status_desc = "[red]CLOSED[/red]"
        else:
            counts["unknown"] += 1
            status_desc = "[yellow]UNKNOWN[/yellow]"

        console.print(f"[{idx}/{len(jobs)}] {company} — {title[:35]}: {status_desc}")

        if not dry_run:
            with db.session() as con:
                con.execute(
                    """UPDATE jobs_master
                       SET is_open = ?, last_verified_at = CURRENT_TIMESTAMP
                       WHERE dedup_key = ?""",
                    [is_open, key],
                )

        delay = getattr(intern_module, "HOST_DELAY_S", 0.5)
        if delay > 0 and idx < len(jobs):
            time.sleep(delay)

    console.print(
        f"\n[bold green]Verification complete:[/bold green] "
        f"[green]{counts['open']} live[/green] · [red]{counts['closed']} closed[/red] · [yellow]{counts['unknown']} unknown[/yellow]"
    )


def _list_candidate_dossiers(root: Path) -> list[dict[str, Any]]:
    """List all saved dossier folders with parsed fit score, mtime, and apply URL."""
    if not root.exists():
        return []
    items: list[dict[str, Any]] = []
    for d in sorted(root.iterdir()):
        if not d.is_dir() or d.name.startswith("."):
            continue
        p = d / "05_interview_dossier.md"
        if not p.is_file():
            mds = sorted(d.glob("*dossier*.md"))
            p = mds[0] if mds else None

        fit_score = "—"
        apply_url = ""
        archetype = ""
        if p and p.is_file():
            try:
                txt = p.read_text(encoding="utf-8", errors="replace")
                m_fit = re.search(r"fit_score:\s*([\d\.]+)|Score:\s*([\d\.]+)", txt)
                if m_fit:
                    fit_score = m_fit.group(1) or m_fit.group(2)
                m_url = re.search(r"apply_url:\s*\"([^\"]+)\"|URL:\s*(https?://\S+)", txt)
                if m_url:
                    apply_url = m_url.group(1) or m_url.group(2)
                m_arch = re.search(r"archetype:\s*\"?([^\n\"]+)\"?", txt)
                if m_arch:
                    archetype = m_arch.group(1).strip()
            except Exception:
                pass

        mtime = datetime.fromtimestamp(d.stat().st_mtime).strftime("%Y-%m-%d")
        items.append({
            "folder": d,
            "dossier": p,
            "name": d.name,
            "fit_score": fit_score,
            "mtime": mtime,
            "apply_url": apply_url,
            "archetype": archetype,
        })
    # Sort by mtime descending
    items.sort(key=lambda x: x["folder"].stat().st_mtime, reverse=True)
    return items


def _curses_fuzzy_select_dossier(candidates: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Curses interactive fuzzy finder for dossiers."""
    import curses

    def _menu(stdscr: curses.window) -> dict[str, Any] | None:
        curses.curs_set(1)
        curses.use_default_colors()
        query = ""
        selected_idx = 0

        while True:
            stdscr.clear()
            max_y, max_x = stdscr.getmaxyx()

            if not query.strip():
                matched = candidates[:]
            else:
                scored = []
                for item in candidates:
                    label = f"{item['name']} {item['fit_score']} {item['archetype']} {item['mtime']}"
                    s = _fuzzy_score(query, label)
                    if s >= 0:
                        scored.append((s, item))
                scored.sort(key=lambda x: x[0], reverse=True)
                matched = [item for _, item in scored]

            if selected_idx >= len(matched):
                selected_idx = max(0, len(matched) - 1)

            header = f"Select Job Dossier | Total: {len(candidates)} dossiers (↑/↓: navigate, Enter: select, Esc/q: cancel)"
            stdscr.addnstr(0, 0, header[:max_x], max_x, curses.A_BOLD)
            search_line = f"Search: {query}"
            stdscr.addnstr(1, 0, search_line[:max_x], max_x, curses.A_UNDERLINE)

            legend = "  FIT    DATE         ROLE / COMPANY"
            stdscr.addnstr(3, 0, legend[:max_x], max_x, curses.A_DIM)

            list_y = 4
            available_rows = max_y - list_y - 1

            start_row = 0
            if selected_idx >= available_rows:
                start_row = selected_idx - available_rows + 1

            for display_i, i in enumerate(range(start_row, min(len(matched), start_row + available_rows))):
                item = matched[i]
                fit_str = f"{item['fit_score']:>3}★" if item['fit_score'] != "—" else "   — "
                line_str = f"  {fit_str}  {item['mtime']}   {item['name']}"
                is_selected = (i == selected_idx)
                attr = curses.A_REVERSE | curses.A_BOLD if is_selected else curses.A_NORMAL
                marker = "▶" if is_selected else " "
                stdscr.addnstr(list_y + display_i, 0, f"{marker}{line_str[1:]}"[:max_x], max_x, attr)

            stdscr.refresh()
            stdscr.move(1, min(len(search_line), max_x - 1))

            ch = stdscr.getch()
            if ch in (27,):  # ESC
                return None
            elif ch in (curses.KEY_ENTER, 10, 13):
                if matched and 0 <= selected_idx < len(matched):
                    return matched[selected_idx]
                return None
            elif ch in (curses.KEY_UP, 65):
                selected_idx = max(0, selected_idx - 1)
            elif ch in (curses.KEY_DOWN, 66):
                if matched:
                    selected_idx = min(len(matched) - 1, selected_idx + 1)
            elif ch in (curses.KEY_BACKSPACE, 127, 8):
                query = query[:-1]
                selected_idx = 0
            elif 32 <= ch <= 126:
                query += chr(ch)
                selected_idx = 0

    return curses.wrapper(_menu)


def _interactive_fuzzy_select_dossier(root: Path) -> dict[str, Any] | None:
    """Prompt user to select a dossier using fzf or curses."""
    candidates = _list_candidate_dossiers(root)
    if not candidates:
        return None

    fzf_bin = shutil.which("fzf") or (Path("/opt/homebrew/bin/fzf") if Path("/opt/homebrew/bin/fzf").is_file() else None)
    if fzf_bin and sys.stdin.isatty():
        lines = []
        for c in candidates:
            fit = f"{c['fit_score']:>3}★" if c['fit_score'] != "—" else "   — "
            lines.append(f"{fit}  │  {c['mtime']}  │  {c['name']}  │  {c['folder']}")
        try:
            proc = subprocess.Popen(
                [
                    str(fzf_bin),
                    "--height=40%",
                    "--reverse",
                    "--prompt=Select Job Dossier > ",
                    "--header=Enter: select, Esc: cancel. Search by company, role, or score.",
                ],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            stdout, _ = proc.communicate(input="\n".join(lines))
            if proc.returncode == 0 and stdout.strip():
                parts = stdout.strip().split("│")
                if len(parts) >= 4:
                    chosen_folder = Path(parts[-1].strip())
                    for c in candidates:
                        if c["folder"] == chosen_folder:
                            return c
        except Exception:
            pass

    if sys.stdin.isatty():
        try:
            return _curses_fuzzy_select_dossier(candidates)
        except Exception:
            pass

    return candidates[0] if candidates else None


@app.command("dossier")
def dossier_cmd(
    query: Annotated[Optional[str], typer.Argument(help="Company name, role title, or search term (e.g. 'nintendo', 'stripe')")] = None,
    select: Annotated[bool, typer.Option("--select", "-s", help="Open an interactive fuzzy finder (fzf) to select and action a dossier")] = False,
    cat: Annotated[bool, typer.Option("--cat", "-c", help="Render dossier in terminal with rich markdown formatting")] = False,
    nvim: Annotated[bool, typer.Option("--nvim", "-v", help="Open dossier directly in Neovim")] = False,
    edit: Annotated[bool, typer.Option("--edit", "-e", help="Open dossier in Neovim / $EDITOR")] = False,
    one_markdown: Annotated[bool, typer.Option("--one-markdown", "-m", help="Open dossier directly in One Markdown")] = False,
    open_in_editor: Annotated[bool, typer.Option("--open", "-o", help="Open dossier in default macOS editor/app")] = False,
    latest: Annotated[bool, typer.Option("--latest", "-l", help="Open/view the most recently modified dossier")] = False,
    export_dir: Annotated[Optional[Path], typer.Option(help="Custom artifacts root (default: ~/Documents/jobspy-cockpit)")] = None,
) -> None:
    """View an interview dossier in terminal or open it in default editor / Neovim / One Markdown."""
    from cockpit import intel as intel_module
    from rich.markdown import Markdown

    root = (export_dir or intel_module.resolve_export_dir()).expanduser()
    if not root.exists():
        console.print(f"[yellow]Artifacts directory does not exist yet: {root}[/yellow]")
        raise typer.Exit(1)

    subdirs = [d for d in root.iterdir() if d.is_dir() and not d.name.startswith(".")]

    def get_dossier_file(folder: Path) -> Path | None:
        p = folder / "05_interview_dossier.md"
        if p.is_file():
            return p
        mds = sorted(folder.glob("*dossier*.md"))
        if mds and mds[0].is_file():
            return mds[0]
        other_mds = sorted(folder.glob("*.md"))
        return other_mds[0] if other_mds else None

    use_nvim = nvim or edit
    target_dossier: Path | None = None
    target_folder: Path | None = None
    target_apply_url: str = ""

    if select:
        chosen = _interactive_fuzzy_select_dossier(root)
        if not chosen:
            console.print("[yellow]Selection cancelled.[/yellow]")
            return
        target_folder = chosen["folder"]
        target_dossier = chosen["dossier"]
        target_apply_url = chosen.get("apply_url", "")

        # Interactive quick-action prompt if no specific action flag passed
        if not (cat or use_nvim or open_in_editor or one_markdown):
            console.print(f"\n[bold cyan]Selected:[/bold cyan] [bright_white]{chosen['name']}[/bright_white] "
                          f"([yellow]{chosen['fit_score']}★[/yellow])")
            actions_msg = (
                "[1] View in terminal    [2] Open in One Markdown  [3] Open in Neovim\n"
                "[4] Open Folder/Finder  [5] Open Apply URL        [6] Copy Path  [q] Cancel"
            )
            console.print(Panel(actions_msg, title="Choose Action", border_style="cyan"))
            choice = typer.prompt("Select action", default="2").strip().lower()

            if choice in ("1", "view", "cat"):
                cat = True
            elif choice in ("2", "m", "onemarkdown", "one"):
                one_markdown = True
            elif choice in ("3", "v", "nvim", "edit"):
                use_nvim = True
            elif choice in ("4", "f", "finder", "folder"):
                macos.open_file(target_folder)
                console.print(f"Opened folder in Finder: [cyan]{target_folder}[/cyan]")
                return
            elif choice in ("5", "a", "url", "apply"):
                if target_apply_url:
                    macos.open_url(target_apply_url)
                    console.print(f"Opened apply URL: [cyan]{target_apply_url}[/cyan]")
                else:
                    console.print("[yellow]No apply URL found in dossier.[/yellow]")
                return
            elif choice in ("6", "c", "copy"):
                macos.copy_to_clipboard(str(target_dossier or target_folder))
                console.print(f"Copied path to clipboard: [green]{target_dossier or target_folder}[/green]")
                return
            else:
                return

    elif latest:
        valid_folders = [(d, get_dossier_file(d)) for d in subdirs if get_dossier_file(d)]
        if not valid_folders:
            console.print(f"[yellow]No dossiers found in {root}[/yellow]")
            raise typer.Exit(1)
        valid_folders.sort(key=lambda x: x[1].stat().st_mtime, reverse=True)
        target_folder, target_dossier = valid_folders[0]
    elif query:
        q = query.lower().strip()
        matched = [(d, get_dossier_file(d)) for d in subdirs if q in d.name.lower() and get_dossier_file(d)]
        if not matched:
            console.print(f"[red]No dossier found matching query '{query}' in {root}[/red]")
            console.print("Available dossiers:")
            for d in subdirs:
                if get_dossier_file(d):
                    console.print(f"  • {d.name}")
            raise typer.Exit(1)
        target_folder, target_dossier = matched[0]
    elif use_nvim:
        # No query + nvim -> open the artifacts root folder in Neovim
        editor = os.environ.get("EDITOR") or shutil.which("nvim") or shutil.which("vim") or "nvim"
        subprocess.run([editor, str(root)])
        return
    elif one_markdown:
        # No query + one_markdown -> One Markdown cannot open a folder, so show the root in Finder.
        macos.open_file(root)
        console.print(f"Opened dossiers folder in Finder: [cyan]{root}[/cyan]")
        return
    else:
        valid = [(d, get_dossier_file(d)) for d in subdirs if get_dossier_file(d)]
        if not valid:
            console.print(f"[yellow]No dossiers found in {root}. Save jobs with 'b' in Cockpit TUI to generate dossiers.[/yellow]")
            return

        table = Table(title="[bold cyan]Saved Job Dossiers[/bold cyan]", box=None, header_style="bold bright_white")
        table.add_column("Folder / Role", style="cyan")
        table.add_column("Last Updated", style="bright_cyan", justify="right")
        table.add_column("Artifacts", justify="right", style="green")

        valid.sort(key=lambda x: x[1].stat().st_mtime, reverse=True)
        for folder, d_file in valid:
            mtime = datetime.fromtimestamp(d_file.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
            md_count = len(list(folder.glob("*.md")))
            pdf_count = len(list((folder / "exports").glob("*.pdf"))) if (folder / "exports").exists() else 0
            artifacts_str = f"{md_count} MD" + (f", {pdf_count} PDF" if pdf_count else "")
            table.add_row(folder.name, mtime, artifacts_str)

        console.print(table)
        console.print("\n[dim]Tip: Use 'cockpit dossier -s' (or 'cockpit md <query>') to open in One Markdown.[/dim]")
        return

    if target_dossier:
        if cat:
            console.print(f"[bold cyan]─── Dossier: {target_dossier.parent.name} ───[/bold cyan]\n")
            console.print(Markdown(target_dossier.read_text(encoding="utf-8", errors="replace")))
        elif one_markdown:
            macos.open_file(target_dossier, app="One Markdown")
            console.print(f"Opened dossier in One Markdown: [cyan]{target_dossier}[/cyan]")
        elif use_nvim:
            editor = os.environ.get("EDITOR") or shutil.which("nvim") or shutil.which("vim") or "nvim"
            subprocess.run([editor, str(target_dossier)])
        else:
            macos.open_file(target_dossier)
            console.print(f"Opened dossier: [cyan]{target_dossier}[/cyan]")


@app.command("md")
@app.command("onemarkdown")
def onemarkdown_cmd(
    query: Annotated[Optional[str], typer.Argument(help="Company name, role title, or search term (e.g. 'nintendo', 'stripe')")] = None,
    latest: Annotated[bool, typer.Option("--latest", "-l", help="Open the most recently modified dossier in One Markdown")] = False,
    export_dir: Annotated[Optional[Path], typer.Option(help="Custom artifacts root (default: ~/Documents/jobspy-cockpit)")] = None,
) -> None:
    """Open an interview dossier in One Markdown; with no query, open the dossiers folder in Finder."""
    dossier_cmd(query=query, one_markdown=True, latest=latest, export_dir=export_dir)


@app.command("edit")
@app.command("nvim")
def edit_cmd(
    query: Annotated[Optional[str], typer.Argument(help="Company name, role title, or search term (e.g. 'nintendo', 'stripe')")] = None,
    latest: Annotated[bool, typer.Option("--latest", "-l", help="Open the most recently modified dossier in Neovim")] = False,
    export_dir: Annotated[Optional[Path], typer.Option(help="Custom artifacts root (default: ~/Documents/jobspy-cockpit)")] = None,
) -> None:
    """Open an interview dossier (or artifacts root) directly in Neovim."""
    dossier_cmd(query=query, nvim=True, latest=latest, export_dir=export_dir)


@app.command("folder")
def folder_cmd(
    query: Annotated[Optional[str], typer.Argument(help="Company name or role to open a specific job's folder")] = None,
    export_dir: Annotated[Optional[Path], typer.Option(help="Custom artifacts root (default: ~/Documents/jobspy-cockpit)")] = None,
) -> None:
    """Open the artifacts / dossiers folder in macOS Finder."""
    from cockpit import intel as intel_module

    root = (export_dir or intel_module.resolve_export_dir()).expanduser()
    if not root.exists():
        root.mkdir(parents=True, exist_ok=True)

    if query:
        q = query.lower().strip()
        subdirs = [d for d in root.iterdir() if d.is_dir() and not d.name.startswith(".")]
        matched = [d for d in subdirs if q in d.name.lower()]
        if not matched:
            console.print(f"[red]No folder found matching '{query}' in {root}[/red]")
            console.print("Available folders:")
            for d in subdirs:
                console.print(f"  • {d.name}")
            raise typer.Exit(1)
        target = matched[0]
        macos.open_file(target)
        console.print(f"Opened job folder in Finder: [cyan]{target}[/cyan]")
    else:
        macos.open_file(root)
        console.print(f"Opened artifacts directory in Finder: [cyan]{root}[/cyan]")


@app.command("artifacts")
def artifacts_cmd(
    query: Annotated[Optional[str], typer.Argument(help="Company name or role to open a specific job's folder")] = None,
    export_dir: Annotated[Optional[Path], typer.Option(help="Custom artifacts root (default: ~/Documents/jobspy-cockpit)")] = None,
) -> None:
    """Alias for 'cockpit folder' — open artifacts folder in macOS Finder."""
    folder_cmd(query=query, export_dir=export_dir)


@app.command("sync-mail")
def sync_mail(user: Annotated[Optional[str], typer.Option(help="Gmail address (default $GMAIL_USER)")] = None) -> None:
    """Reconcile APPLIED jobs against ATS emails."""
    from cockpit.imap_sync import sync_gmail_statuses

    changes = sync_gmail_statuses(user=user)
    for company, status, subject in changes:
        console.print(f"{company}: [bold]{status}[/bold] — {subject}")
    console.print(f"{len(changes)} change(s)")


@app.command("keychain-setup")
def keychain_setup(
    service: Annotated[str, typer.Option(help="Keychain service: 'gmail' (default) or 'gemini' (or custom name)")] = "gmail",
    run: Annotated[bool, typer.Option(help="Execute it (security prompts for the password)")] = False,
    password: Annotated[Optional[str], typer.Option(help="Password or API key to store non-interactively")] = None,
) -> None:
    """Store credentials (Gmail app password or Gemini API key) in the login Keychain."""
    if service.lower() in ("gemini", "jobcockpit-gemini"):
        service_name = macos.KEYCHAIN_GEMINI_SERVICE
    elif service.lower() in ("gmail", "mail", "jobcockpit-gmail"):
        service_name = macos.KEYCHAIN_SERVICE
    else:
        service_name = service

    cmd = ["/usr/bin/security", "add-generic-password", "-a", os.environ.get("USER", ""),
           "-s", service_name, "-U"]
    if password is not None:
        cmd.extend(["-w", password])
    else:
        cmd.append("-w")

    display_cmd = cmd.copy()
    if password is not None:
        display_cmd[-1] = "[REDACTED]"
    console.print("Run: " + " ".join(display_cmd))
    if run or password is not None:
        subprocess.run(cmd, check=True)


@app.command()
def intel(
    limit: Annotated[int, typer.Option(help="Maximum jobs to process in this run")] = 20,
    stale_hours: Annotated[int, typer.Option(help="Requeue jobs whose intel is running/pending for longer than N hours")] = 0,
    retry_failed: Annotated[bool, typer.Option("--retry-failed", "-r", help="Requeue previously failed intel jobs")] = False,
    provider: Annotated[Optional[str], typer.Option(help="LLM provider: 'claude-cli' (default), 'agy' (Antigravity), or 'gemini'")] = None,
    model: Annotated[Optional[str], typer.Option(help="Model name: 'sonnet' (default for claude-cli) or Gemini model")] = None,
    career_ops_root: Annotated[Optional[Path], typer.Option(help="Path to career-ops repository")] = None,
    evidence_dir: Annotated[Optional[Path], typer.Option(help="Path to evidence bank directory")] = None,
    export_dir: Annotated[Optional[Path], typer.Option(help="Path to export directory")] = None,
) -> None:
    """Run batch fit-scoring, application packaging, and interview prep for saved jobs."""
    from cockpit import intel as intel_module

    try:
        ctx = intel_module.load_context(career_ops_root, model=model, evidence_dir=evidence_dir, provider=provider)
    except intel_module.IntelUnavailable as e:
        console.print(f"[bold red]Career-ops error:[/bold red] {e}")
        raise typer.Exit(1)

    try:
        generator = intel_module.create_llm_client(provider=ctx.provider, model_name=ctx.model)
    except intel_module.IntelUnavailable as e:
        console.print(f"[bold red]LLM provider error:[/bold red] {e}")
        raise typer.Exit(1)

    out_dir = export_dir or intel_module.resolve_export_dir()
    if stale_hours > 0:
        with db.session() as con:
            requeued = db.requeue_stale_intel(con, minutes=stale_hours * 60)
            if requeued:
                console.print(f"[yellow]Requeued {requeued} stale intel job(s)[/yellow]")

    if retry_failed:
        with db.session() as con:
            con.execute("UPDATE jobs_master SET intel_status = 'pending', intel_error = NULL WHERE is_saved = TRUE AND intel_status = 'failed'")
            console.print("[yellow]Requeued failed intel jobs for retry[/yellow]")

    with db.session() as con:
        reconciled = intel_module.reconcile_saved_jobs(con, out_dir)
        if reconciled:
            console.print(f"[cyan]Reconciled {reconciled} saved job(s) from dossiers on disk[/cyan]")

    console.print(f"Running intel pipeline with model [cyan]{ctx.model}[/cyan]...")
    processed = intel_module.process_batch(
        db.DB_PATH,
        ctx,
        generator,
        export_dir=out_dir,
        limit=limit,
    )
    console.print(f"Intel pipeline finished: processed [bold green]{len(processed)}[/bold green] job(s)")


@app.command()
def stats() -> None:
    """Funnel conversion by resume archetype and board."""
    with db.session() as con:
        for column, title in (("resume_archetype", "By resume archetype"), ("site", "By board")):
            table = Table(title=title)
            for h in ("group", "jobs", "applied", "positive", "apply %", "median latency (h)"):
                table.add_column(h, justify="right" if h != "group" else "left")
            for grp, total, applied, positive, latency in db.funnel_by(con, column):
                rate = f"{100 * positive / applied:.0f}%" if applied else "—"
                table.add_row(str(grp), str(total), str(applied), str(positive), rate,
                              f"{latency:.1f}" if latency is not None else "—")
            console.print(table)


@app.command("tmux-status")
def tmux_status() -> None:
    """Print the cached status line; touches DuckDB only if the cache is missing."""
    if not db.TMUX_CACHE.exists():
        with db.session() as con:
            db.write_tmux_cache(con)
    typer.echo(db.TMUX_CACHE.read_text().rstrip("\n"))


def uv_invocation(repo_root: Path, uv_path: str) -> str:
    """The repo is Poetry-managed, so `uv run cockpit` has no script to find; run the module directly."""
    reqs = repo_root / "cockpit" / "requirements.txt"
    return f"{uv_path} run --no-project --python 3.12 --with-requirements '{reqs}' python -m cockpit.cli"


def render_plist(repo_root: Path, uv_path: str) -> str:
    run = uv_invocation(repo_root, uv_path)
    # `;` not `&&`: a missing CSV folder must not stop mail sync.
    cmd = f"cd '{repo_root}' && {{ {run} ingest; {run} sync-mail; }}"
    return plistlib.dumps({
        "Label": AGENT_LABEL,
        "ProgramArguments": ["/bin/zsh", "-lc", cmd],
        "StartCalendarInterval": [{"Hour": 8, "Minute": 30}, {"Hour": 13, "Minute": 30}],
        "WorkingDirectory": str(repo_root),
        "StandardOutPath": "/tmp/jobcockpit.out.log",
        "StandardErrorPath": "/tmp/jobcockpit.err.log",
    }).decode()


@app.command("install-agent")
def install_agent() -> None:
    """Install and load the 08:30 / 13:30 launchd sync agent."""
    uv = shutil.which("uv")
    if not uv:
        raise typer.Exit("uv not found on PATH")
    AGENT_PLIST.parent.mkdir(parents=True, exist_ok=True)
    AGENT_PLIST.write_text(render_plist(REPO_ROOT, uv))
    domain = f"gui/{os.getuid()}"
    subprocess.run(["launchctl", "bootout", domain, str(AGENT_PLIST)], check=False)  # fine if not loaded yet
    subprocess.run(["launchctl", "bootstrap", domain, str(AGENT_PLIST)], check=True)
    console.print(f"Installed {AGENT_PLIST}")


pipeline_app = typer.Typer(no_args_is_help=False, add_completion=False, help="Inspect, monitor, and run the JobSpy scraping pipeline.")
app.add_typer(pipeline_app, name="pipeline")
app.add_typer(pipeline_app, name="pipelines")


@pipeline_app.callback(invoke_without_command=True)
def pipeline_default(
    ctx: typer.Context,
    run: Annotated[bool, typer.Option("--run", "-r", help="Launch run_pipeline.py and attach monitor")] = False,
    watch: Annotated[bool, typer.Option("--watch", "-w", help="Keep monitor active in terminal")] = False,
    once: Annotated[bool, typer.Option("--once", "-1", help="Print a single snapshot without live refresh")] = False,
) -> None:
    """Monitor, inspect, or run the JobSpy scraping pipeline."""
    if ctx.invoked_subcommand is not None:
        return

    from cockpit import pipeline_monitor

    if run:
        pipeline_run()
        return

    state = pipeline_monitor.get_pipeline_state(REPO_ROOT)
    if (state.get("is_active") and not once) or watch:
        pipeline_monitor.monitor_loop(REPO_ROOT, once=False)
    else:
        pipeline_monitor.monitor_loop(REPO_ROOT, once=True)


@app.command("run", help="Launch scraper pipeline in background and attach live visual monitor.")
@pipeline_app.command("run")
def pipeline_run() -> None:
    """Launch run_pipeline.py in background and attach live visual monitor."""
    from cockpit import pipeline_monitor

    state = pipeline_monitor.get_pipeline_state(REPO_ROOT)
    if state.get("is_active"):
        console.print("[yellow]Pipeline is already running! Attaching to active run...[/yellow]")
    else:
        console.print("[cyan]Launching JobSpy pipeline in background...[/cyan]")
        p = pipeline_monitor.launch_pipeline(REPO_ROOT)
        console.print(f"[dim]Master pipeline process spawned (PID: {p.pid})[/dim]")
        time.sleep(0.5)

    pipeline_monitor.monitor_loop(REPO_ROOT, once=False)


@pipeline_app.command("status")
def pipeline_status(
    watch: Annotated[bool, typer.Option("--watch", "-w", help="Keep monitor active in terminal")] = False,
) -> None:
    """Print current pipeline status table (or stream with --watch)."""
    from cockpit import pipeline_monitor

    pipeline_monitor.monitor_loop(REPO_ROOT, once=not watch)


@pipeline_app.command("logs")
def pipeline_logs(
    target: Annotated[Optional[str], typer.Argument(help="Scraper name (e.g. seattle, ats, remote) or 'pipeline'")] = None,
    lines: Annotated[int, typer.Option("--lines", "-n", help="Number of lines to show")] = 25,
) -> None:
    """View recent log entries for a scraper or the master pipeline."""
    month_str = datetime.now().strftime("%Y_%m")
    day_str = datetime.now().strftime("%Y_%m_%d")
    date_logs_dir = REPO_ROOT / "jobspy" / "scraped jobs" / "logs" / month_str / day_str
    month_logs_dir = REPO_ROOT / "jobspy" / "scraped jobs" / "logs" / month_str
    logs_dir = date_logs_dir if date_logs_dir.exists() else month_logs_dir

    if not target or target.lower() in ("pipeline", "cron", "all"):
        target_file = REPO_ROOT / "pipeline_cron.log"
    else:
        clean = target.lower().removesuffix(".py").removesuffix(".log")
        candidates = list(date_logs_dir.glob(f"*{clean}*.log")) if date_logs_dir.exists() else []
        if not candidates and month_logs_dir.exists():
            candidates = list(month_logs_dir.glob(f"*{clean}*.log"))
        if not candidates:
            console.print(f"[red]No logs found matching '{target}' in {logs_dir}[/red]")
            raise typer.Exit(1)
        target_file = candidates[0]

    if not target_file.is_file():
        console.print(f"[yellow]Log file does not exist yet: {target_file}[/yellow]")
        return

    console.print(f"[bold cyan]─── Log: {target_file.name} (last {lines} lines) ───[/bold cyan]")
    try:
        content = target_file.read_text(encoding="utf-8", errors="replace").splitlines()
        recent = content[-lines:] if len(content) > lines else content
        for l in recent:
            console.print(l)
    except Exception as e:
        console.print(f"[red]Error reading log: {e}[/red]")


@app.command("stop", help="Stop the currently running scraper pipeline, if active.")
@pipeline_app.command("stop")
def pipeline_stop() -> None:
    """Stop the currently running pipeline, if any."""
    from cockpit import pipeline_monitor

    state = pipeline_monitor.get_pipeline_state(REPO_ROOT)
    if not state.get("is_active"):
        console.print("[yellow]No active pipeline is currently running.[/yellow]")
        return
    stopped = pipeline_monitor.stop_pipeline(REPO_ROOT)
    if stopped:
        console.print("[green]Active pipeline process terminated.[/green]")
    else:
        console.print("[yellow]Marked pipeline as stopped (process was no longer alive).[/yellow]")


@app.command("retry", help="Selectively re-run failed scrapers without destroying successful outputs.")
@pipeline_app.command("retry")
def pipeline_retry(
    targets: Annotated[
        Optional[list[str]],
        typer.Argument(help="Specific scrapers to retry (e.g. remote seattle); default: all failed"),
    ] = None,
    watch: Annotated[
        bool,
        typer.Option("--watch/--no-watch", "-w", help="Keep monitor active in terminal"),
    ] = True,
    kill_active: Annotated[
        bool,
        typer.Option("--kill-active", "-k", help="Stop any active pipeline before retrying"),
    ] = False,
) -> None:
    """Selectively re-run failed scrapers without wiping existing successful CSV outputs."""
    from cockpit import pipeline_monitor

    state = pipeline_monitor.get_pipeline_state(REPO_ROOT)
    if state.get("is_active"):
        if not kill_active:
            if not typer.confirm("A pipeline is currently active. Stop it and launch retry?", default=True):
                console.print("[yellow]Aborted retry.[/yellow]")
                raise typer.Exit(0)
        console.print("[yellow]Stopping active pipeline...[/yellow]")
        pipeline_monitor.stop_pipeline(REPO_ROOT)
        time.sleep(1)

    if targets:
        target_list = list(targets)
    else:
        target_list = pipeline_monitor.get_failed_scrapers(REPO_ROOT)
        if not target_list:
            console.print("[green]No failed scrapers found to retry! (All scrapers succeeded or state is clean)[/green]")
            raise typer.Exit(0)

    console.print(f"[cyan]Retrying scrapers: {', '.join(target_list)}[/cyan]")
    p = pipeline_monitor.launch_pipeline(REPO_ROOT, scrapers=target_list)
    console.print(f"[dim]Master pipeline spawned for retry (PID: {p.pid})[/dim]")
    time.sleep(0.5)

    if watch:
        pipeline_monitor.monitor_loop(REPO_ROOT, once=False)
    else:
        console.print("[green]Pipeline retry launched in background. Run 'cockpit pipeline status' to monitor.[/green]")


@app.command("init")
def init_cmd() -> None:
    """Initialize the local DuckDB database and schema."""
    with db.session() as con:
        db.write_tmux_cache(con)
    console.print(f"[green]Initialized DuckDB database at {db.DB_PATH}[/green]")


@app.command("build-resumes")
def build_resumes_cmd(
    evidence_dir: Annotated[Optional[Path], typer.Option(help="Path to evidence bank directory")] = None,
    output_dir: Annotated[Optional[Path], typer.Option(help="Output directory for compiled resumes")] = None,
) -> None:
    """Compile Markdown base resumes into ATS-clean .docx and 1-page .pdf files in resumes/."""
    from cockpit.exporters import build_base_resumes

    generated = build_base_resumes(evidence_dir=evidence_dir, output_dir=output_dir)
    if not generated:
        console.print("[yellow]No Markdown base resumes found to compile.[/yellow]")
        raise typer.Exit(1)
    console.print(f"[bold green]Compiled {len(generated)} resume artifact(s):[/bold green]")
    for p in generated:
        console.print(f"  • {p}")


@app.command("onboard")
def onboard_cmd(
    from_resumes: Annotated[
        Optional[Path],
        typer.Option("--from-resumes", "-r", help="Import existing .pdf, .docx, or .md resume file or directory"),
    ] = None,
    seed_defaults: Annotated[
        bool,
        typer.Option("--seed-defaults", help="Seed starter templates into user-layer config without prompting"),
    ] = False,
    name: Annotated[Optional[str], typer.Option("--name", help="Candidate full name")] = None,
    email: Annotated[Optional[str], typer.Option("--email", help="Candidate email address")] = None,
    phone: Annotated[Optional[str], typer.Option("--phone", help="Candidate phone number")] = None,
    location: Annotated[Optional[str], typer.Option("--location", help="Primary city/state or Remote")] = None,
    linkedin: Annotated[Optional[str], typer.Option("--linkedin", help="LinkedIn profile URL")] = None,
    github: Annotated[Optional[str], typer.Option("--github", help="GitHub profile URL")] = None,
    roles: Annotated[
        Optional[str],
        typer.Option("--roles", help="Comma-separated target roles (e.g. 'Software Engineer,Data Engineer')"),
    ] = None,
    locations: Annotated[
        Optional[str],
        typer.Option("--locations", help="Comma-separated target locations (e.g. 'seattle,remote,san francisco')"),
    ] = None,
) -> None:
    """Set up Career Pipeline with your own resumes, candidate profile, and target role configurations."""
    from cockpit import onboard

    seeded = onboard.seed_user_layer(REPO_ROOT, overwrite=False)
    if seeded:
        console.print(f"[cyan]Seeded {len(seeded)} starter user-layer file(s).[/cyan]")

    if seed_defaults and not from_resumes and not name:
        compiled = onboard.build_base_resumes()
        console.print(f"[green]Default templates seeded and {len(compiled)} resume artifact(s) compiled.[/green]")
        return

    detected: dict[str, str] = {}
    if from_resumes is not None:
        try:
            res = onboard.import_user_resumes(from_resumes, repo_root=REPO_ROOT)
            detected = res.get("heuristics", {})
            console.print(
                f"[bold green]Imported {len(res['imported_files'])} resume(s)[/bold green] -> {res['cv_path']}"
            )
            console.print(f"[green]Compiled {len(res['compiled_resumes'])} resume artifact(s) into resumes/[/green]")
        except Exception as e:
            console.print(f"[bold red]Resume import failed:[/bold red] {e}")
            raise typer.Exit(1)

    interactive = not any([name, email, phone, location, linkedin, github, roles, locations, from_resumes, seed_defaults])
    if interactive:
        console.print("[bold cyan]─── Career Pipeline User Onboarding ───[/bold cyan]")
        resume_input = typer.prompt(
            "Path to your existing resume (.pdf, .docx, .md, or directory) [leave blank to use starter template]",
            default="",
            show_default=False,
        ).strip()
        if resume_input:
            res = onboard.import_user_resumes(Path(resume_input), repo_root=REPO_ROOT)
            detected = res.get("heuristics", {})
            console.print(f"[green]Imported resume(s) from {resume_input}[/green]")

        name = typer.prompt("Full Name", default=detected.get("name") or "Jane Smith")
        email = typer.prompt("Email", default=detected.get("email") or "jane@example.com")
        phone = typer.prompt("Phone", default=detected.get("phone") or "+1-555-0123")
        location = typer.prompt("Primary City/Location", default="Remote")
        linkedin = typer.prompt("LinkedIn URL", default=detected.get("linkedin") or "")
        github = typer.prompt("GitHub URL", default=detected.get("github") or "")
        roles = typer.prompt("Target Roles (comma-separated)", default="Software Engineer,Data Engineer,Data Analyst")
        locations = typer.prompt("Target Locations (comma-separated)", default=f"{location.lower()},remote")

    if name or detected.get("name"):
        final_name = name or detected.get("name") or "Candidate"
        role_list = [r.strip() for r in (roles or "").split(",") if r.strip()] or None
        loc_list = [l.strip() for l in (locations or "").split(",") if l.strip()] or None
        paths = onboard.write_user_profile_configs(
            repo_root=REPO_ROOT,
            name=final_name,
            email=email or detected.get("email", ""),
            phone=phone or detected.get("phone", ""),
            location=location or "Remote",
            linkedin=linkedin or detected.get("linkedin", ""),
            github=github or detected.get("github", ""),
            primary_roles=role_list,
            primary_locations=loc_list,
        )
        console.print(f"[bold green]Updated user configuration for {final_name}:[/bold green]")
        for label, p in paths.items():
            console.print(f"  • {label}: {p}")

    if not from_resumes:
        compiled = onboard.build_base_resumes()
        console.print(f"[green]Compiled {len(compiled)} base resume file(s) in resumes/[/green]")


@app.command("import-resume")
def import_resume_cmd(
    resume_path: Annotated[Path, typer.Argument(help="Path to resume file (.pdf, .docx, .md, .txt)")],
    name: Annotated[Optional[str], typer.Option("--name", help="Candidate full name override")] = None,
    email: Annotated[Optional[str], typer.Option("--email", help="Candidate email override")] = None,
    phone: Annotated[Optional[str], typer.Option("--phone", help="Candidate phone override")] = None,
    location: Annotated[Optional[str], typer.Option("--location", help="Primary city/location override")] = None,
    linkedin: Annotated[Optional[str], typer.Option("--linkedin", help="LinkedIn URL override")] = None,
    github: Annotated[Optional[str], typer.Option("--github", help="GitHub URL override")] = None,
    roles: Annotated[Optional[str], typer.Option("--roles", help="Comma-separated target roles")] = None,
    locations: Annotated[Optional[str], typer.Option("--locations", help="Comma-separated target locations")] = None,
    sync_home: Annotated[bool, typer.Option("--sync-home/--no-sync-home", help="Mirror resume to ~/resumes/")] = True,
    update_duckdb: Annotated[bool, typer.Option("--update-duckdb/--no-update-duckdb", help="Update resume_path in DuckDB queued jobs")] = True,
) -> None:
    """Turnkey resume importer: stages authentic binary, updates configs/cv.md, syncs DuckDB, and audits ATS."""
    from cockpit import onboard

    if not resume_path.exists():
        console.print(f"[bold red]Error:[/bold red] Resume file not found: {resume_path}")
        raise typer.Exit(1)

    role_list = [r.strip() for r in (roles or "").split(",") if r.strip()] or None
    loc_list = [l.strip() for l in (locations or "").split(",") if l.strip()] or None

    active_repo = Path(os.environ.get("COCKPIT_REPO_ROOT", str(REPO_ROOT)))
    try:
        res = onboard.execute_resume_import(
            resume_path,
            repo_root=active_repo,
            name=name,
            email=email,
            phone=phone,
            location=location,
            linkedin=linkedin,
            github=github,
            roles=role_list,
            locations=loc_list,
            sync_home=sync_home,
            update_duckdb=update_duckdb,
        )
    except Exception as e:
        console.print(f"[bold red]Import failed:[/bold red] {e}")
        raise typer.Exit(1)

    table = Table(title="Resume Import & System Staging Report")
    table.add_column("Component", style="bold cyan")
    table.add_column("Status / Path", style="green")

    table.add_row("Staged Resume", res["dest_resume"])
    table.add_row("AI Prompt CV", res["cv_path"])
    table.add_row("Candidate Profile", res["profile_path"])
    table.add_row("Scoring Overrides", res["local_rules_path"])
    table.add_row("DuckDB Queued Jobs Updated", f"{res['duckdb_updated_rows']} jobs")
    table.add_row("ATS Parseability Score", f"{res['ats_score']}/100 (Grade {res['ats_grade']})")
    table.add_row("Home Directory Mirrored", "~/resumes/" if res["home_synced"] else "Skipped")

    console.print(table)
    console.print(f"[bold green]✔ Successfully imported and staged resume:[/bold green] {resume_path.name}")


def _resolve_apply_target(
    raw_target: Optional[str], pdf: Optional[Path], cover: Optional[Path]
) -> tuple[str, Optional[Path], Optional[Path]]:
    """Turn an apply URL or a job key / row index into (apply_url, resume, cover_file).

    Returns `("", pdf, cover)` when nothing resolves. Explicit `pdf` / `cover` win over the
    job folder's files. A DuckDB failure is printed as a notice, not raised.
    """
    from cockpit import prefill as prefill_mod
    from cockpit import intel as intel_module

    apply_url, resume_file, cover_file = "", pdf, cover
    if raw_target and raw_target.startswith(("http://", "https://")):
        return raw_target, resume_file, cover_file
    if raw_target:
        try:
            with db.session() as con:
                row = con.execute("SELECT * FROM jobs_master WHERE dedup_key = ?", [raw_target]).fetchone()
                if not row:
                    try:
                        idx = int(raw_target)
                        rows = con.execute("SELECT * FROM jobs_master ORDER BY priority_score DESC LIMIT 1 OFFSET ?", [idx]).fetchall()
                        if rows:
                            row = rows[0]
                    except ValueError:
                        pass
                if row:
                    cols = [d[0] for d in con.description]
                    job_dict = dict(zip(cols, row))
                    apply_url = job_dict.get("apply_url") or ""
                    export_dir = intel_module.resolve_export_dir()
                    folder = intel_module.job_folder_path(job_dict, export_dir)
                    files = prefill_mod.application_files(job_dict, folder if folder.exists() else None)
                    resume_file = resume_file or files.resume
                    cover_file = cover_file or files.cover_text or files.cover_pdf
        except Exception as e:
            console.print(f"[yellow]DuckDB query notice:[/yellow] {e}")
    return apply_url, resume_file, cover_file


@app.command("prefill")
def prefill_cmd(
    target: Annotated[Optional[str], typer.Argument(help="Apply URL or job dedup_key / row index")] = None,
    job: Annotated[Optional[str], typer.Option("--job", "-j", help="Job dedup_key or row index in DuckDB")] = None,
    pdf: Annotated[Optional[Path], typer.Option("--pdf", "-p", help="Path to resume PDF file")] = None,
    cover: Annotated[Optional[Path], typer.Option("--cover", "-c", help="Path to cover letter file")] = None,
    copy: Annotated[bool, typer.Option("--copy", help="Copy prefill text to system clipboard")] = False,
    open_browser: Annotated[bool, typer.Option("--open", "-o", help="Open apply URL in default browser")] = False,
    as_json: Annotated[bool, typer.Option("--json", help="Output as JSON")] = False,
) -> None:
    """Generate ATS application form prefill mappings for Greenhouse, Ashby, Lever, or generic jobs."""
    import json
    from cockpit import prefill as prefill_mod

    apply_url, resume_file, cover_file = _resolve_apply_target(target or job, pdf, cover)

    if not apply_url:
        console.print("[bold red]Error:[/bold red] Please provide an apply URL or job identifier: `cockpit prefill <url>` or `--job <key>`")
        raise typer.Exit(1)

    ats_target = prefill_mod.detect_ats(apply_url)
    if not ats_target:
        console.print(f"[bold red]Error:[/bold red] Could not parse apply URL: {apply_url}")
        raise typer.Exit(1)

    profile = prefill_mod.read_candidate_profile(REPO_ROOT)
    fields = prefill_mod.build_prefill_fields(ats_target, profile, resume_file=resume_file, cover_file=cover_file)
    prefill_url = prefill_mod.build_prefill_url(ats_target, profile)

    if as_json:
        payload = {
            "ats": ats_target.ats,
            "company_slug": ats_target.company_slug,
            "job_id": ats_target.job_id,
            "apply_url": ats_target.original_url,
            "prefill_url": prefill_url,
            "fields": dict(fields),
            "resume": str(resume_file) if resume_file else None,
            "cover": str(cover_file) if cover_file else None,
        }
        typer.echo(json.dumps(payload, indent=2))
        return

    table_text = prefill_mod.format_prefill_table(
        ats_target, fields, resume_file=resume_file, cover_file=cover_file, prefill_url=prefill_url
    )
    console.print(table_text)

    if copy:
        try:
            macos.copy_to_clipboard(table_text)
            console.print("[green]Copied prefill summary to clipboard![/green]")
        except Exception as e:
            console.print(f"[yellow]Could not copy to clipboard:[/yellow] {e}")

    if open_browser:
        macos.open_url(prefill_url)


@app.command("autofill")
def autofill_cmd(
    target: Annotated[Optional[str], typer.Argument(help="Apply URL or job dedup_key / row index")] = None,
    pdf: Annotated[Optional[Path], typer.Option("--pdf", "-p", help="Resume PDF to upload")] = None,
    cover: Annotated[Optional[Path], typer.Option("--cover", "-c", help="Cover letter file (.md/.txt text or .pdf)")] = None,
    headless: Annotated[bool, typer.Option("--headless", hidden=True)] = False,
) -> None:
    """Open one application form in a separate browser and fill it. Never submits; you review and submit."""
    import threading

    from cockpit import autofill as autofill_mod
    from cockpit import prefill as prefill_mod

    apply_url, resume_file, cover_file = _resolve_apply_target(target, pdf, cover)
    if not apply_url:
        console.print("[bold red]Error:[/bold red] Please provide an apply URL or job identifier: `cockpit autofill <url|key>`")
        raise typer.Exit(1)

    # 1. A cover file is either a PDF to upload or text to paste.
    cover_pdf, cover_text = None, None
    if cover_file:
        if cover_file.suffix.lower() == ".pdf":
            cover_pdf = cover_file
        else:
            cover_text = cover_file.read_text("utf-8")

    # 2. Profile and answers, same locations the TUI uses.
    profile = prefill_mod.read_candidate_profile(REPO_ROOT)
    answers = autofill_mod.load_answers(REPO_ROOT / "config" / "career_ops" / "config" / "answers.yml")

    # 3. One job, then wait: closing the worker closes the browser, so hold it open for the review.
    from cockpit.tui import AUTOFILL_PROFILE_DIR

    AUTOFILL_PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    worker = autofill_mod.AutofillWorker(AUTOFILL_PROFILE_DIR, headless=headless)
    done = threading.Event()
    reports: list = []
    worker.submit(apply_url, profile, resume_file, cover_pdf, cover_text, answers,
                  lambda r: (reports.append(r), done.set()))
    try:
        done.wait()
        report = reports[0]
        console.print(report.summary())
        for label, items in (("Filled", report.filled), ("Needs you", report.needs_you)):
            console.print(f"[bold]{label}:[/bold]")
            for item in items:
                console.print(f"  - {item}")
        console.print("[bold]Rules fired:[/bold]")
        for question, answer in report.rules_fired:
            console.print(f"  - {question} -> {answer}")
        console.print(f"Resume: {report.resume or 'none'}")
        console.print(f"Cover letter: {report.cover or 'none'}")
        console.print("Review and submit in the browser. Press Enter here to close it.")
        input()
    finally:
        worker.close()


@app.command("verify-ats")
def verify_ats_cmd(
    resume_path: Annotated[Optional[Path], typer.Argument(help="Path to resume file (.md, .pdf, .docx, .html)")] = None,
    all_resumes: Annotated[bool, typer.Option("--all", "-a", help="Audit all resumes in resumes/ and config/evidence/base_resumes/")] = False,
    keywords: Annotated[Optional[str], typer.Option("--keywords", "-k", help="Comma-separated keywords to check in text")] = None,
    role: Annotated[Optional[str], typer.Option("--role", "-r", help="Target role title to check in text")] = None,
    min_score: Annotated[int, typer.Option("--min-score", "-m", help="Minimum passing score threshold (0-100)")] = 70,
    as_json: Annotated[bool, typer.Option("--json", help="Output audit results as JSON")] = False,
) -> None:
    """Score a resume's ATS parseability (0-100), letter grade, and keyword coverage."""
    import json
    from cockpit import verify_ats as verify_ats_mod

    kw_list = [k.strip() for k in keywords.split(",") if k.strip()] if keywords else None

    if all_resumes or resume_path is None:
        candidates: list[Path] = []
        res_dir = REPO_ROOT / "resumes"
        if res_dir.exists():
            candidates.extend(sorted(res_dir.glob("*.pdf")))
            candidates.extend(sorted(res_dir.glob("*.docx")))
        base_dir = REPO_ROOT / "config" / "evidence" / "base_resumes"
        if base_dir.exists():
            candidates.extend(sorted(base_dir.glob("*.md")))
        else:
            tmpl_dir = REPO_ROOT / "config" / "templates" / "evidence" / "base_resumes"
            if tmpl_dir.exists():
                candidates.extend(sorted(tmpl_dir.glob("*.md")))

        if not candidates:
            console.print("[yellow]No resumes found in resumes/ or config/evidence/base_resumes/[/yellow]")
            return

        summary_table = Table(title="ATS Resume Parseability Scorecard")
        summary_table.add_column("Resume File", style="bold cyan")
        summary_table.add_column("Score", justify="right")
        summary_table.add_column("Grade", justify="center")
        summary_table.add_column("Status", justify="center")
        summary_table.add_column("Sections")
        summary_table.add_column("Issues")

        all_passed = True
        json_results = []
        for p in candidates:
            try:
                res = verify_ats_mod.audit_resume_file(p, keywords=kw_list, role=role, min_score=min_score)
                if not res.pass_status:
                    all_passed = False
                color = "green" if res.score >= 80 else ("yellow" if res.score >= 70 else "red")
                status = "[green]PASS[/green]" if res.pass_status else "[red]FAIL[/red]"
                issue_summary = f"{len(res.issues)} issue(s)" if res.issues else "None"
                summary_table.add_row(
                    p.name,
                    f"[{color}]{res.score}[/{color}]",
                    f"[{color}]{res.grade}[/{color}]",
                    status,
                    ", ".join(res.found_sections).title(),
                    issue_summary,
                )
                if as_json:
                    json_results.append({
                        "file": p.name,
                        "path": str(p),
                        "score": res.score,
                        "grade": res.grade,
                        "pass": res.pass_status,
                        "sections": res.found_sections,
                        "issues": [{"severity": i.severity, "message": i.message} for i in res.issues],
                    })
            except Exception as e:
                summary_table.add_row(p.name, "ERR", "F", "[red]ERROR[/red]", "", str(e))

        if as_json:
            typer.echo(json.dumps(json_results, indent=2))
        else:
            console.print(summary_table)

        if not all_passed:
            raise typer.Exit(1)
        return

    if not resume_path.exists():
        console.print(f"[bold red]Error:[/bold red] Resume file not found: {resume_path}")
        raise typer.Exit(1)

    result = verify_ats_mod.audit_resume_file(resume_path, keywords=kw_list, role=role, min_score=min_score)

    if as_json:
        payload = {
            "file": resume_path.name,
            "path": str(resume_path),
            "score": result.score,
            "grade": result.grade,
            "pass": result.pass_status,
            "sections": result.found_sections,
            "issues": [{"severity": i.severity, "message": i.message} for i in result.issues],
            "keyword_coverage": {
                "found": result.keyword_coverage.found,
                "total": result.keyword_coverage.total,
                "percent": result.keyword_coverage.percent,
                "missing": result.keyword_coverage.missing,
                "role_found": result.keyword_coverage.role_found,
            } if result.keyword_coverage else None,
        }
        typer.echo(json.dumps(payload, indent=2))
    else:
        report_text = verify_ats_mod.format_ats_report(result, min_score=min_score)
        console.print(report_text)

    if not result.pass_status:
        raise typer.Exit(1)


@app.command("doctor")
def doctor_cmd() -> None:
    """Audit environment, DuckDB, vendored career-ops prompts, user resumes, and LLM readiness."""
    from cockpit import intel as intel_module
    from cockpit.normalizer import load_rules, resolve_resume

    table = Table(title="Career Pipeline & Cockpit Healthcheck")
    table.add_column("Check", style="bold")
    table.add_column("Status")
    table.add_column("Details")

    def add_row(check: str, ok: bool, detail: str, warn_only: bool = False) -> None:
        badge = "[bold green]OK[/bold green]" if ok else ("[bold yellow]WARN[/bold yellow]" if warn_only else "[bold red]FAIL[/bold red]")
        table.add_row(check, badge, detail)

    # 1. DuckDB initialization
    try:
        with db.session() as con:
            db.write_tmux_cache(con)
        add_row("DuckDB Database", db.DB_PATH.exists(), str(db.DB_PATH))
    except Exception as e:
        add_row("DuckDB Database", False, str(e))

    # 2. Vendored career-ops root & required prompt files
    co_root = intel_module.resolve_career_ops_root()
    oferta_ok = (co_root / "modes" / "oferta.md").exists() and (co_root / "modes" / "_shared.md").exists()
    add_row("Career-Ops Prompts", oferta_ok, str(co_root))

    # 3. User CV & Profile
    cv_exists = (co_root / "cv.md").exists()
    prof_exists = (co_root / "config" / "profile.yml").exists()
    add_row(
        "User Profile & CV",
        cv_exists and prof_exists,
        f"cv.md={'yes' if cv_exists else 'no'}, profile.yml={'yes' if prof_exists else 'no'} (run `cockpit onboard`)",
        warn_only=True,
    )

    # 4. Evidence Bank
    ev_dir = intel_module.resolve_evidence_dir()
    ev_ok = (ev_dir / "01_master_career_history.yaml").exists() and (ev_dir / "02_project_evidence_bank.yaml").exists()
    add_row("Evidence Bank", ev_ok, str(ev_dir), warn_only=True)

    # 5. Base Resumes for Archetypes
    rules = load_rules()
    res_map = rules.get("resumes", {})
    resolved_resumes = {k: Path(resolve_resume(v)) for k, v in res_map.items()}
    all_resumes_ok = bool(resolved_resumes) and all(p.exists() for p in resolved_resumes.values())
    missing_res = [k for k, p in resolved_resumes.items() if not p.exists()]
    add_row(
        "Archetype Resumes",
        all_resumes_ok,
        "All archetype PDFs present" if all_resumes_ok else f"Missing: {', '.join(missing_res)} (run `cockpit build-resumes`)",
        warn_only=True,
    )

    # 6. LLM Backend Readiness
    claude_bin = shutil.which("claude") or (
        "/opt/homebrew/bin/claude" if Path("/opt/homebrew/bin/claude").exists() else None
    )
    gemini_key = macos.gemini_api_key()
    llm_ok = bool(claude_bin or gemini_key)
    llm_detail = f"claude={claude_bin or 'not found'}, gemini_key={'set' if gemini_key else 'not set'}"
    add_row("LLM Provider", llm_ok, llm_detail, warn_only=True)

    # 7. Shell & Tmux
    tmux_bin = shutil.which("tmux")
    add_row("Tmux Popup Support", bool(tmux_bin), tmux_bin or "tmux not found on PATH", warn_only=True)

    # 8. ATS Resume Quality
    try:
        from cockpit.verify_ats import audit_resume_file

        sample_res = (
            next((REPO_ROOT / "resumes").glob("*.pdf"), None)
            or next((REPO_ROOT / "config" / "evidence" / "base_resumes").glob("*.md"), None)
            or next((REPO_ROOT / "config" / "templates" / "evidence" / "base_resumes").glob("*.md"), None)
        )
        if sample_res and sample_res.exists():
            ats_res = audit_resume_file(sample_res, min_score=70)
            add_row(
                "ATS Resume Quality",
                ats_res.pass_status,
                f"{sample_res.name}: {ats_res.score}/100 ({ats_res.grade})"
                + (f" ({len(ats_res.issues)} issues)" if ats_res.issues else ""),
                warn_only=True,
            )
        else:
            add_row("ATS Resume Quality", False, "No resumes found to audit (run `cockpit build-resumes`)", warn_only=True)
    except Exception as e:
        add_row("ATS Resume Quality", False, str(e), warn_only=True)

    console.print(table)


if __name__ == "__main__":
    app()
