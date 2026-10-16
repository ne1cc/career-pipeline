from __future__ import annotations

import os
import plistlib
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Annotated, Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from cockpit import db, macos
from cockpit.models import FunnelState
from cockpit.normalizer import REPO_ROOT

app = typer.Typer(no_args_is_help=True, add_completion=False)
console = Console()

AGENT_LABEL = "com.jobcockpit.sync"
AGENT_PLIST = Path.home() / "Library" / "LaunchAgents" / f"{AGENT_LABEL}.plist"


def _default_results_dir() -> Path:
    return REPO_ROOT / "jobspy" / "scraped jobs" / "results" / datetime.now().strftime("%Y_%m")


@app.command()
def ingest(
    csvs: Annotated[Optional[list[Path]], typer.Argument(help="JobSpy CSVs; default: this month's results dir")] = None,
) -> None:
    """Dedupe, score and upsert scraped jobs into DuckDB."""
    import pandas as pd

    paths = csvs or sorted(_default_results_dir().glob("*_jobs.csv"))
    if not paths:
        raise typer.BadParameter(f"No CSVs found in {_default_results_dir()}; pass paths explicitly.")
    # The CSV name is the scraper sheet (seattle_entry_level_jobs.csv -> "seattle_entry_level").
    frame = pd.concat(
        [pd.read_csv(p).assign(segment=p.stem.removesuffix("_jobs")) for p in paths], ignore_index=True
    )
    with db.session() as con:
        stats = db.upsert_jobspy_frame(con, frame)
        expired = db.expire_stale_referral_holds(con)
    console.print(f"[green]{len(paths)} file(s)[/green] {stats} | stale holds flagged: {expired}")


@app.command()
def rescore() -> None:
    """Re-apply scoring_rules.toml to QUEUED jobs (applied/skipped/held are untouched)."""
    with db.session() as con:
        console.print(f"Rescored {db.rescore_queued(con)} queued job(s)")


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
) -> None:
    """Blocking loop: stage resume + open URL, then record the outcome."""
    with db.session() as con:
        jobs = db.fetch_jobs(con, "QUEUED", db.min_score(), segment, location)[:limit]
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
        while choice not in (*outcomes, "q"):
            choice = typer.prompt("[a]pplied [s]kip [h]old [q]uit").strip().lower()[:1]
        if choice == "q":
            break
        note = typer.prompt("contact/note", default="") if choice == "h" else None
        with db.session() as con:
            db.transition_job_state(con, job["dedup_key"], outcomes[choice], "sprint", note or None)


@app.command("sync-mail")
def sync_mail(user: Annotated[Optional[str], typer.Option(help="Gmail address (default $GMAIL_USER)")] = None) -> None:
    """Reconcile APPLIED jobs against ATS emails."""
    from cockpit.imap_sync import sync_gmail_statuses

    changes = sync_gmail_statuses(user=user)
    for company, status, subject in changes:
        console.print(f"{company}: [bold]{status}[/bold] — {subject}")
    console.print(f"{len(changes)} change(s)")


@app.command("keychain-setup")
def keychain_setup(run: Annotated[bool, typer.Option(help="Execute it (security prompts for the password)")] = False) -> None:
    """Store the Gmail app password in the login Keychain."""
    cmd = ["/usr/bin/security", "add-generic-password", "-a", os.environ.get("USER", ""),
           "-s", macos.KEYCHAIN_SERVICE, "-U", "-w"]  # trailing -w with no value => interactive prompt
    console.print("Run: " + " ".join(cmd))
    if run:
        subprocess.run(cmd, check=True)


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


if __name__ == "__main__":
    app()
