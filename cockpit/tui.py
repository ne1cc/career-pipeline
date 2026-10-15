from __future__ import annotations

from pathlib import Path
from typing import Any

from rich.markup import escape
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import DataTable, Footer, Input, Label, Static

from cockpit import macos
from cockpit.db import (
    DAILY_GOAL,
    DB_PATH,
    expire_stale_referral_holds,
    fetch_jobs,
    min_score,
    scoreboard,
    session,
    set_resume_archetype,
    transition_job_state,
)
from cockpit.imap_sync import sync_gmail_statuses
from cockpit.models import FunnelState
from cockpit.normalizer import load_rules, render_outreach, resolve_resume, score_job

FILTERS: list[str | None] = ["QUEUED", "REFERRAL_HOLD", "APPLIED", None]


class NoteModal(ModalScreen[str | None]):
    BINDINGS = [("escape", "cancel", "Cancel")]
    DEFAULT_CSS = "NoteModal { align: center middle; } #note-box { width: 60; height: auto; border: round $accent; padding: 1 2; background: $surface; }"

    def compose(self) -> ComposeResult:
        with Vertical(id="note-box"):
            yield Label("Referral contact / note (Enter to save, Esc to cancel)")
            yield Input(id="note")

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value)

    def action_cancel(self) -> None:
        self.dismiss(None)


class JobCockpitApp(App[None]):
    CSS = """
    #score { height: 1; padding: 0 1; background: $boost; }
    #table { width: 65%; }
    #detail-pane { width: 35%; border-left: solid $primary; padding: 0 1; }
    """
    BINDINGS = [
        Binding("j", "down", "Down"),
        Binding("k", "up", "Up"),
        Binding("o", "stage(False)", "Open"),
        Binding("O", "stage(True)", "Open bg"),
        Binding("p", "preview", "Preview"),
        Binding("a", "mark('APPLIED')", "Applied"),
        Binding("s", "mark('SKIPPED')", "Skip"),
        Binding("h", "hold", "Hold"),
        Binding("1", "archetype('swe_backend')", "swe"),
        Binding("2", "archetype('data_eng')", "data"),
        Binding("3", "archetype('analytics_msba')", "analytics"),
        Binding("y", "yank", "Outreach"),
        Binding("f", "cycle_filter", "Filter"),
        Binding("R", "sync", "Sync mail"),
        Binding("q", "quit", "Quit"),
    ]

    def __init__(self, db_path: Path = DB_PATH) -> None:
        super().__init__()
        self.db_path = db_path
        self.rules = load_rules()
        self.rows: list[dict[str, Any]] = []
        self.filter_idx = 0

    def compose(self) -> ComposeResult:
        yield Static(id="score")
        with Horizontal():
            yield DataTable(id="table", cursor_type="row", zebra_stripes=True, cell_padding=1)
            with VerticalScroll(id="detail-pane"):
                yield Static(id="detail")
        yield Footer()

    # Every DB touch below opens and closes its own connection.
    def on_mount(self) -> None:
        table = self.query_one(DataTable)
        # Fixed widths: auto-sizing let the longest company name starve the Title column.
        for label, width in (("Score", 5), ("Company", 20), ("Title", 38), ("Location", 14),
                             ("Site", 8), ("Archetype", 14), ("Status", 8)):
            table.add_column(label, width=width)
        with session(self.db_path) as con:
            expire_stale_referral_holds(con)
        self._reload()

    def _reload(self, row: int | None = None) -> None:
        with session(self.db_path) as con:
            self.rows = fetch_jobs(con, FILTERS[self.filter_idx], min_score())
            b = scoreboard(con)
        table = self.query_one(DataTable)
        table.clear()
        for r in self.rows:
            table.add_row(
                f"{r['priority_score']:.0f}", r["company"], r["title"], r["location"] or "",
                r["site"] or "", r["resume_archetype"], r["status"], key=r["dedup_key"],
            )
        if self.rows and row is not None:
            table.move_cursor(row=min(row, len(self.rows) - 1))
        label = FILTERS[self.filter_idx] or "ALL"
        self.query_one("#score", Static).update(
            f"Applied Today: {b['applied_today']}/{DAILY_GOAL} | Queued: {b['queued']} | "
            f"Referral Hold: {b['hold']} | Positive Signals: {b['positive']}   [filter: {label}]"
        )
        self._show(table.cursor_row)

    def _job(self) -> dict[str, Any] | None:
        i = self.query_one(DataTable).cursor_row
        return self.rows[i] if 0 <= i < len(self.rows) else None

    def _show(self, index: int) -> None:
        detail = self.query_one("#detail", Static)
        if not 0 <= index < len(self.rows):
            detail.update("No jobs in this view.")
            return
        r = self.rows[index]
        matched = score_job(r["title"], r["description"] or "", self.rules, r["location"] or "", bool(r["is_remote"])).matched
        detail.update(
            f"[b]{escape(r['title'])}[/b]\n{escape(r['company'])} — {escape(r['location'] or '')}\n\n"
            f"[b]Matched:[/b] {escape(', '.join(matched) or '—')}\n"
            f"[b]URL:[/b] {escape(r['apply_url'])}\n"
            f"[b]Resume:[/b] {escape(macos.posix_path(r['resume_path']))}\n\n"
            f"{escape((r['description'] or '')[:1200])}"
        )

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        self._show(event.cursor_row)

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        self.action_stage(False)

    def action_down(self) -> None:
        self.query_one(DataTable).action_cursor_down()

    def action_up(self) -> None:
        self.query_one(DataTable).action_cursor_up()

    def action_stage(self, background: bool) -> None:
        if job := self._job():
            macos.copy_resume_path(job["resume_path"])
            macos.open_url(job["apply_url"], background=background)
            self.notify("Resume path on clipboard — Cmd+Shift+G, Cmd+V, Enter")

    def action_preview(self) -> None:
        if job := self._job():
            macos.quicklook(job["resume_path"])

    def action_yank(self) -> None:
        if job := self._job():
            macos.copy_to_clipboard(render_outreach(job, self.rules))
            self.notify(f"Outreach copied for {job['company']}")

    def _mark(self, key: str, status: FunnelState, note: str | None = None) -> None:
        row = self.query_one(DataTable).cursor_row
        with session(self.db_path) as con:
            transition_job_state(con, key, status, "tui", note)
        still_listed = FILTERS[self.filter_idx] in (None, status.value)
        self._reload(row + 1 if still_listed else row)  # advance past the row we just handled

    def action_mark(self, status: str) -> None:
        if job := self._job():
            self._mark(job["dedup_key"], FunnelState(status))

    def action_hold(self) -> None:
        job = self._job()
        if not job:
            return

        def done(note: str | None) -> None:
            if note is not None:
                self._mark(job["dedup_key"], FunnelState.REFERRAL_HOLD, note or None)

        self.push_screen(NoteModal(), done)

    def action_archetype(self, name: str) -> None:
        if job := self._job():
            path = resolve_resume(self.rules["resumes"][name])
            with session(self.db_path) as con:
                set_resume_archetype(con, job["dedup_key"], name, path)
            macos.copy_resume_path(path)
            self._reload(self.query_one(DataTable).cursor_row)

    def action_cycle_filter(self) -> None:
        self.filter_idx = (self.filter_idx + 1) % len(FILTERS)
        self._reload(0)

    def action_sync(self) -> None:
        self.notify("Syncing Gmail…")
        self.run_worker(self._sync, thread=True, exclusive=True)

    def _sync(self) -> None:
        try:
            changes = sync_gmail_statuses(self.db_path)
        except Exception as e:  # surface IMAP/credential errors in the UI instead of dying silently
            self.call_from_thread(self.notify, str(e), severity="error")
            return
        self.call_from_thread(self.notify, f"Mail sync: {len(changes)} status change(s)")
        self.call_from_thread(self._reload, self.query_one(DataTable).cursor_row)
