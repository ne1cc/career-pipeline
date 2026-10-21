from __future__ import annotations

import os
import plistlib
import re
import shutil
import subprocess
import time
from datetime import datetime
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


def _default_results_dir() -> Path:
    now = datetime.now()
    month_str = now.strftime("%Y_%m")
    day_str = now.strftime("%Y_%m_%d")
    today_dir = REPO_ROOT / "jobspy" / "scraped jobs" / "results" / month_str / day_str
    if today_dir.exists() and any(today_dir.glob("*_jobs.csv")):
        return today_dir
    month_dir = REPO_ROOT / "jobspy" / "scraped jobs" / "results" / month_str
    if month_dir.exists():
        subdirs = sorted([d for d in month_dir.iterdir() if d.is_dir() and re.match(r"^\d{4}_\d{2}_\d{2}$", d.name)], reverse=True)
        for sd in subdirs:
            if any(sd.glob("*_jobs.csv")):
                return sd
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
    inputs: Annotated[Optional[list[Path]], typer.Argument(help="JobSpy CSVs or Excel workbooks; default: results dir or ~/Downloads/Job_Report_*.xlsx")] = None,
    sheet: Annotated[Optional[str], typer.Option(help="Specific sheet name for Excel files, or 'all' for all raw sheets (default: 'Action Items' if present)")] = None,
) -> None:
    """Dedupe, score and upsert scraped jobs into DuckDB."""
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

    paths = inputs or _find_default_inputs()
    if not paths:
        raise typer.BadParameter(f"No CSVs found in {_default_results_dir()} and no Job_Report_*.xlsx found in ~/Downloads; pass paths explicitly.")

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
        load_task = progress.add_task("[cyan]Loading input files...", total=len(paths))
        loaded_frames = []
        for p in paths:
            progress.update(load_task, description=f"[cyan]Loading {p.name}...")
            loaded_frames.append(_load_input_frame(p, sheet=sheet))
            progress.advance(load_task)
        frame = pd.concat(loaded_frames, ignore_index=True)
        progress.update(load_task, description=f"[green]✔ Loaded {len(paths)} file(s) ({len(frame):,} rows)")

        score_task = progress.add_task("[yellow]Scoring and deduping postings...", total=len(frame))

        def on_score_progress(current: int, total: int) -> None:
            progress.update(score_task, completed=current)

        with db.session() as con:
            stats = db.upsert_jobspy_frame(con, frame, progress_callback=on_score_progress)
            progress.update(score_task, completed=len(frame), description="[green]✔ Scored and deduped postings")

            db_task = progress.add_task("[magenta]Reconciling holds & saved jobs...", total=None)
            expired = db.expire_stale_referral_holds(con)
            intel_module.reconcile_saved_jobs(con)
            progress.update(db_task, description="[green]✔ Reconciled holds and saved jobs", completed=1, total=1)

    console.print(
        f"[bold green]✔ Ingest complete:[/bold green] [cyan]{len(paths)} file(s)[/cyan] "
        f"({', '.join(p.name for p in paths)}) {stats} | stale holds flagged: {expired}"
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
    provider: Annotated[Optional[str], typer.Option(help="LLM provider: 'claude-cli' (default) or 'gemini'")] = None,
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


if __name__ == "__main__":
    app()
