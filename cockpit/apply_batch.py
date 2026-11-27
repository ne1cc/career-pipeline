"""Hybrid daily apply-batch runner for Tier 1 & Tier 2 funnel feeds.

Intent: step through today's prioritized `tui_feed_YYYY_MM_DD.json` queue one role at a
time, invoking Playwright autofill (stopping before Submit) for Greenhouse/Lever/Ashby
and browser-assisted prep (open URL + copy matched resume path) for Workday/Company Site,
recording outcomes back into Cockpit's DuckDB store.

Contract:
- `load_feed_jobs(feed_path, tier, limit)` returns a list of normalized job dicts.
- `filter_pending_jobs(jobs, con)` excludes jobs already marked `APPLIED`, `SKIPPED`, or
  `REJECTED` in DuckDB.
- `run_apply_batch(...)` returns a summary dict `{"applied": int, "saved": int, "skipped": int, "held": int}`.
- Never clicks Submit on any ATS form; all submissions remain human-approved.

Limits: clipboard copy uses macOS `pbcopy` (gracefully skipped on non-macOS). DuckDB
status transitions apply when a matching `dedup_key` or `apply_url` exists in `jobs_master`.
"""

from __future__ import annotations

import json
import subprocess
import sys
import webbrowser
from pathlib import Path
from typing import Any, Callable

from rich.console import Console
from rich.panel import Panel

from cockpit import db
from cockpit.autofill import SUPPORTED, target_for
from cockpit.models import FunnelState
from cockpit.normalizer import REPO_ROOT, load_rules, resolve_resume

TERMINAL_STATUSES = {
    FunnelState.APPLIED.value,
    FunnelState.SKIPPED.value,
    FunnelState.REJECTED.value,
}

TRACK_TO_ARCHETYPE = {
    "Data / BI Analytics": "data_analytics",
    "Data / Analytics Eng": "data_engineering",
    "Data Science / ML": "data_science",
    "Product / BizOps": "product_bizops",
    "Software / Security": "software_security",
}


def find_latest_feed(repo_root: Path = REPO_ROOT) -> Path | None:
    """Locate the most recent `tui_feed_*.json` file or `data/latest_tui_feed.json`."""
    latest = repo_root / "data" / "latest_tui_feed.json"
    if latest.exists():
        return latest
    results_root = repo_root / "jobspy" / "scraped jobs" / "results"
    if not results_root.exists():
        return None
    candidates = sorted(results_root.rglob("tui_feed_*.json"), reverse=True)
    return candidates[0] if candidates else None


def load_feed_jobs(
    feed_path: Path | None = None,
    *,
    tier: str = "Tier 1",
    limit: int = 10,
    repo_root: Path = REPO_ROOT,
) -> list[dict[str, Any]]:
    """Load normalized job dicts from `tui_feed_YYYY_MM_DD.json`, filtered by tier.

    Contract:
    - Returns up to `limit` matching job dicts; returns `[]` when feed is missing or empty.
    - `tier="all"` disables tier filtering.
    """
    path = feed_path or find_latest_feed(repo_root)
    if path is None or not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []

    jobs = payload.get("jobs", []) if isinstance(payload, dict) else payload
    if not isinstance(jobs, list):
        return []

    tier_norm = tier.strip().lower()
    if tier_norm not in {"all", "*"}:
        if tier_norm in {"1", "tier1", "tier 1"}:
            target = "tier 1"
        elif tier_norm in {"2", "tier2", "tier 2"}:
            target = "tier 2"
        else:
            target = tier_norm
        jobs = [j for j in jobs if str(j.get("tier", "")).strip().lower() == target]

    return jobs[: max(limit, 0)]


def filter_pending_jobs(jobs: list[dict[str, Any]], con: Any) -> list[dict[str, Any]]:
    """Remove feed jobs already marked APPLIED, SKIPPED, or REJECTED in DuckDB.

    Contract:
    - Matches against `jobs_master` by `apply_url` or `dedup_key`.
    - Returns filtered list preserving input order; returns original list on DB error.
    """
    if not jobs or con is None:
        return list(jobs)
    try:
        rows = con.execute(
            "SELECT dedup_key, apply_url, status FROM jobs_master WHERE status IN (?, ?, ?)",
            [FunnelState.APPLIED.value, FunnelState.SKIPPED.value, FunnelState.REJECTED.value],
        ).fetchall()
    except Exception:
        return list(jobs)

    done_keys = {str(r[0]) for r in rows if r[0]}
    done_urls = {str(r[1]).strip().rstrip("/") for r in rows if r[1]}

    pending: list[dict[str, Any]] = []
    for job in jobs:
        dkey = str(job.get("dedup_key", "") or "")
        url = str(job.get("apply_url", "") or "").strip().rstrip("/")
        if (dkey and dkey in done_keys) or (url and url in done_urls):
            continue
        pending.append(job)
    return pending


def select_resume_for_track(role_track: str) -> tuple[str, str]:
    """Resolve `(archetype, resume_path)` for a funnel role track using Cockpit rules.

    Contract:
    - Returns `(archetype_name, resolved_path_str)`. Falls back to first configured
      resume or empty path if none are configured.
    """
    archetype = TRACK_TO_ARCHETYPE.get(role_track, "data_analytics")
    try:
        rules = load_rules()
        res_map = rules.get("resumes", {}) if isinstance(rules, dict) else {}
        if archetype in res_map:
            return archetype, str(resolve_resume(res_map[archetype]))
        for key, val in res_map.items():
            return str(key), str(resolve_resume(val))
    except Exception:
        pass
    return archetype, ""


def is_autofill_supported(url: str) -> bool:
    """Return True when URL maps to a Playwright-supported ATS (Greenhouse/Lever/Ashby)."""
    if not url:
        return False
    target = target_for(url)
    return bool(target is not None and target.ats in SUPPORTED)


def _copy_to_clipboard(text: str) -> bool:
    """Copy text to macOS clipboard via `pbcopy`; returns False if unavailable."""
    if not text or sys.platform != "darwin":
        return False
    try:
        subprocess.run(["pbcopy"], input=text.encode("utf-8"), check=True)
        return True
    except Exception:
        return False


def _find_db_dedup_key(con: Any, job: dict[str, Any]) -> str | None:
    """Locate matching `jobs_master.dedup_key` by exact key or apply_url."""
    if con is None:
        return None
    dkey = str(job.get("dedup_key", "") or "")
    url = str(job.get("apply_url", "") or "").strip()
    try:
        if dkey:
            row = con.execute("SELECT dedup_key FROM jobs_master WHERE dedup_key = ?", [dkey]).fetchone()
            if row:
                return str(row[0])
        if url:
            row = con.execute("SELECT dedup_key FROM jobs_master WHERE apply_url = ? LIMIT 1", [url]).fetchone()
            if row:
                return str(row[0])
    except Exception:
        return None
    return None


def _launch_autofill(url: str, resume_path: str) -> None:
    """Invoke `cockpit autofill` CLI subprocess for a supported ATS URL."""
    cmd = [sys.executable, "-m", "cockpit.cli", "autofill", url]
    if resume_path and Path(resume_path).exists():
        cmd.extend(["--resume", resume_path])
    subprocess.run(cmd, cwd=str(REPO_ROOT))


def run_apply_batch(
    *,
    feed_path: Path | None = None,
    tier: str = "Tier 1",
    limit: int = 10,
    console: Console | None = None,
    prompt_fn: Callable[[str], str] | None = None,
    open_url_fn: Callable[[str], Any] | None = None,
    autofill_fn: Callable[[str, str], Any] | None = None,
    db_path: Path | None = None,
) -> dict[str, int]:
    """Step interactively through today's Tier 1 apply queue and log outcomes to DuckDB.

    Contract:
    - Returns summary counts `{"applied", "saved", "skipped", "held", "total"}`.
    - Supports dependency injection (`prompt_fn`, `open_url_fn`, `autofill_fn`) for tests.
    """
    out = console or Console()
    ask = prompt_fn or input
    opener = open_url_fn or webbrowser.open
    filler = autofill_fn or _launch_autofill

    raw_jobs = load_feed_jobs(feed_path, tier=tier, limit=limit)
    if not raw_jobs:
        out.print("[yellow]No matching jobs found in TUI feed. Run the scraper pipeline first.[/yellow]")
        return {"applied": 0, "saved": 0, "skipped": 0, "held": 0, "total": 0}

    counts = {"applied": 0, "saved": 0, "skipped": 0, "held": 0, "total": 0}

    with db.session(db_path) as con:
        pending = filter_pending_jobs(raw_jobs, con)
        counts["total"] = len(pending)
        if not pending:
            out.print("[green]All jobs in this batch are already Applied/Skipped in Cockpit![/green]")
            return counts

        for idx, job in enumerate(pending, start=1):
            url = str(job.get("apply_url", "") or "")
            archetype, resume_path = select_resume_for_track(str(job.get("role_track", "")))
            supported = is_autofill_supported(url)
            mode_badge = "[bold green]Playwright Autofill Ready[/bold green]" if supported else "[yellow]Browser Assist[/yellow]"

            body_lines = [
                f"[bold]{job.get('company', 'Unknown')}[/bold] — {job.get('title', 'Unknown')}",
                f"Track: {job.get('role_track', '')}  |  Location: {job.get('location', '')}  |  Salary: {job.get('salary_display') or 'N/A'}",
                f"Apply Type: {job.get('apply_type', '')}  |  Posted: {job.get('date_posted') or 'N/A'}  |  Mode: {mode_badge}",
                f"Resume ({archetype}): {resume_path or '(not configured)'}",
                f"Apply URL: {url or 'N/A'}",
            ]
            if job.get("recruiter_search_url"):
                body_lines.append(f"Recruiters: {job['recruiter_search_url']}")
            if job.get("emails"):
                body_lines.append(f"Direct Email: {job['emails']}")

            out.print(Panel("\n".join(body_lines), title=f"Batch {idx}/{len(pending)} ({job.get('tier', '')})"))

            # 1. Offer launch action (Playwright prefill for Greenhouse/Lever/Ashby or browser open)
            action_hint = "[f] Autofill  [o] Open Browser  [a] Applied  [h] Hold  [b] Save Dossier  [s] Skip  [q] Quit"
            while True:
                choice = ask(f"{action_hint} > ").strip().lower()
                if choice == "f" and supported:
                    filler(url, resume_path)
                    continue
                if choice == "o" and url:
                    opener(url)
                    if resume_path and _copy_to_clipboard(resume_path):
                        out.print("[dim]Copied matched resume path to clipboard.[/dim]")
                    continue
                if choice in {"a", "h", "b", "s", "q"}:
                    break
                out.print("[dim]Enter one of: f, o, a, h, b, s, q[/dim]")

            if choice == "q":
                break

            # 2. Persist status transition to DuckDB when job is tracked there
            db_key = _find_db_dedup_key(con, job)
            if choice == "a":
                counts["applied"] += 1
                if db_key:
                    db.transition_job_state(con, db_key, FunnelState.APPLIED, triggered_by="apply-batch")
            elif choice == "h":
                counts["held"] += 1
                if db_key:
                    db.transition_job_state(con, db_key, FunnelState.REFERRAL_HOLD, triggered_by="apply-batch")
            elif choice == "b":
                counts["saved"] += 1
                if db_key:
                    db.toggle_saved(con, db_key, saved=True)
            elif choice == "s":
                counts["skipped"] += 1
                if db_key:
                    db.transition_job_state(con, db_key, FunnelState.SKIPPED, triggered_by="apply-batch")

    out.print(
        f"[bold green]Batch complete:[/bold green] "
        f"applied={counts['applied']}, saved={counts['saved']}, held={counts['held']}, skipped={counts['skipped']}"
    )
    return counts
