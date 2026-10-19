from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from rich.markup import escape
from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import DataTable, Footer, Input, Label, Static

from cockpit import intel, macos
from cockpit.db import (
    DAILY_GOAL,
    DB_PATH,
    expire_stale_referral_holds,
    fetch_jobs,
    finish_intel,
    list_segments,
    min_score,
    restore_job_state,
    scoreboard,
    session,
    set_job_note,
    set_resume_archetype,
    toggle_saved,
    transition_job_state,
)
from cockpit.imap_sync import sync_gmail_statuses
from cockpit.models import FunnelState
from cockpit.normalizer import highlight_jd, linkedin_referral_url, load_rules, render_outreach, resolve_resume, score_job

FILTERS: list[str | None] = ["QUEUED", "SAVED", "REFERRAL_HOLD", "APPLIED", None]


class NoteModal(ModalScreen[str | None]):
    BINDINGS = [("escape", "cancel", "Cancel")]
    DEFAULT_CSS = "NoteModal { align: center middle; } #note-box { width: 60; height: auto; border: round $accent; padding: 1 2; background: $surface; }"

    def __init__(self, prompt: str = "Referral contact / note") -> None:
        super().__init__()
        self.prompt = prompt

    def compose(self) -> ComposeResult:
        with Vertical(id="note-box"):
            yield Label(f"{self.prompt} (Enter to save, Esc to cancel)")
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
        Binding("J", "scroll_jd(3)", "JD ↓"),
        Binding("shift+j", "scroll_jd(3)", "JD ↓", show=False),
        Binding("K", "scroll_jd(-3)", "JD ↑"),
        Binding("shift+k", "scroll_jd(-3)", "JD ↑", show=False),
        Binding("d", "page_jd(0.5)", "JD ½PgDn"),
        Binding("ctrl+d", "page_jd(0.5)", "JD ½PgDn", show=False),
        Binding("u", "page_jd(-0.5)", "JD ½PgUp"),
        Binding("ctrl+u", "page_jd(-0.5)", "JD ½PgUp", show=False),
        Binding("b", "toggle_save", "Save/MD"),
        Binding("i", "open_dossier", "Dossier"),
        Binding("I", "rerun_intel", "Re-eval"),
        Binding("z", "undo", "Undo"),
        Binding("n", "edit_note", "Note"),
        Binding("question_mark", "search", "Search"),
        Binding("r", "referrals", "Referrals"),
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
        Binding("g", "segment", "Sheet"),
        Binding("slash", "location", "Location"),
        Binding("R", "sync", "Sync mail"),
        Binding("q", "quit", "Quit"),
    ]

    def __init__(
        self,
        db_path: Path = DB_PATH,
        export_dir: Path | None = None,
        career_ops_root: Path | None = None,
        model: str | None = None,
        generate: Callable[[str, str], str] | None = None,
    ) -> None:
        super().__init__()
        self.db_path = db_path
        self.rules = load_rules()
        intel_cfg = self.rules.get("intel", {})
        self.export_dir = Path(export_dir or intel_cfg.get("export_dir", intel.DEFAULT_EXPORT_DIR)).expanduser()
        self.career_ops_root = Path(career_ops_root or intel_cfg.get("career_ops_root", intel.DEFAULT_CAREER_OPS)).expanduser()
        self.model = model or intel_cfg.get("model")
        self.generate = generate
        self.rows: list[dict[str, Any]] = []
        self.filter_idx = 0
        self.segment: str | None = None
        self.loc_query: str | None = None
        self.search_query: str | None = None
        self.undo_stack: list[tuple[str, dict[str, Any]]] = []

    def compose(self) -> ComposeResult:
        yield Static(id="score")
        with Horizontal():
            yield DataTable(id="table", cursor_type="row", zebra_stripes=True, cell_padding=1)
            with VerticalScroll(id="detail-pane"):
                yield Static(id="detail")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one(DataTable)
        for label, width in (
            ("Fit", 5),
            ("Score", 6),
            ("Company", 18),
            ("Title", 34),
            ("Location", 14),
            ("Site", 8),
            ("Archetype", 14),
            ("Status", 8),
        ):
            table.add_column(label, width=width)
        with session(self.db_path) as con:
            expire_stale_referral_holds(con)
        self._reload()
        self._kick_intel()

    def _reload(self, row: int | None = None) -> None:
        with session(self.db_path) as con:
            self.rows = fetch_jobs(
                con,
                FILTERS[self.filter_idx],
                min_score(),
                self.segment,
                self.loc_query,
                query=self.search_query,
            )
            b = scoreboard(con)
        table = self.query_one(DataTable)
        table.clear()
        for r in self.rows:
            is_saved = bool(r.get("is_saved"))
            # Format Fit column: "…" if pending/running, "4.2" if done, "!" if failed
            fit_val = r.get("fit_score")
            intel_st = r.get("intel_status")
            if fit_val is not None:
                fit_str = f"{fit_val:.1f}"
            elif intel_st in ("pending", "running"):
                fit_str = "…"
            elif intel_st == "failed":
                fit_str = "!"
            else:
                fit_str = "—"

            score_str = f"★ {r['priority_score']:.0f}" if is_saved else f"{r['priority_score']:.0f}"

            def c(val: Any) -> Text | str:
                s = str(val or "")
                if is_saved:
                    return Text(s, style="on grey23")
                return s

            table.add_row(
                c(fit_str),
                c(score_str),
                c(r["company"]),
                c(r["title"]),
                c(r["location"] or ""),
                c(r["site"] or ""),
                c(r["resume_archetype"]),
                c(r["status"]),
                key=r["dedup_key"],
            )
        if self.rows and row is not None:
            table.move_cursor(row=min(row, len(self.rows) - 1))
        label = FILTERS[self.filter_idx] or "ALL"
        self.query_one("#score", Static).update(
            f"Applied: {b['applied_today']}/{DAILY_GOAL} | Q:{b['queued']} | "
            f"Saved:{b.get('saved', 0)} | Hold:{b['hold']} | Pos:{b['positive']}   "
            f"[{label} | sheet:{self.segment or 'all'} | loc:{self.loc_query or 'any'} | q:{self.search_query or 'none'}]"
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
        highlighted_desc = highlight_jd(r["description"] or "", matched, self.rules)

        notes_line = f"\n[b]Notes:[/b] {escape(r['notes'])}\n" if r.get("notes") else ""
        fit_line = f" · [b]Fit:[/b] {r['fit_score']:.1f}/5.0" if r.get("fit_score") is not None else ""
        detail.update(
            f"[b]{escape(r['title'])}[/b]\n{escape(r['company'])} — {escape(r['location'] or '')}{fit_line}\n"
            f"{notes_line}\n"
            f"[b]Matched:[/b] {escape(', '.join(matched) or '—')}\n"
            f"[b]URL:[/b] {escape(r['apply_url'])}\n"
            f"[b]Resume:[/b] {escape(macos.posix_path(r['resume_path']))}\n\n"
            f"{highlighted_desc}"
        )

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        self._show(event.cursor_row)
        self.query_one("#detail-pane", VerticalScroll).scroll_home(animate=False)

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        self.action_stage(False)

    def action_down(self) -> None:
        self.query_one(DataTable).action_cursor_down()

    def action_up(self) -> None:
        self.query_one(DataTable).action_cursor_up()

    def action_scroll_jd(self, delta: int) -> None:
        pane = self.query_one("#detail-pane", VerticalScroll)
        pane.scroll_relative(y=delta, animate=False)

    def action_page_jd(self, fraction: float) -> None:
        pane = self.query_one("#detail-pane", VerticalScroll)
        magnitude = max(1, int(pane.size.height * abs(fraction)))
        lines = magnitude if fraction > 0 else -magnitude
        pane.scroll_relative(y=lines, animate=False)

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

    def action_referrals(self) -> None:
        if job := self._job():
            macos.open_url(linkedin_referral_url(job["company"]))

    def action_open_dossier(self) -> None:
        if job := self._job():
            path_str = job.get("intel_path")
            p = Path(path_str) if path_str else intel.dossier_path(job, self.export_dir)
            if p.exists():
                macos.open_file(p)
            else:
                self.notify("Dossier not found on disk yet.", severity="warning")

    def action_toggle_save(self) -> None:
        if job := self._job():
            row = self.query_one(DataTable).cursor_row
            key = job["dedup_key"]
            with session(self.db_path) as con:
                now_saved = toggle_saved(con, key)
            if now_saved:
                # Write initial stub dossier immediately
                out_file = intel.dossier_path(job, self.export_dir)
                self.export_dir.mkdir(parents=True, exist_ok=True)
                if not out_file.exists():
                    out_file.write_text(intel.render_dossier(job), "utf-8")
                self.notify(f"Saved job to {out_file.name}")
                self._kick_intel()
            else:
                self.notify(f"Unsaved {job['company']}")
            self._reload(row)

    def action_rerun_intel(self) -> None:
        if job := self._job():
            key = job["dedup_key"]
            with session(self.db_path) as con:
                con.execute("UPDATE jobs_master SET intel_status = 'pending', fit_score = NULL WHERE dedup_key = ?", [key])
            self.notify(f"Re-queued intel for {job['company']}")
            self._reload(self.query_one(DataTable).cursor_row)
            self._kick_intel()

    def action_edit_note(self) -> None:
        job = self._job()
        if not job:
            return

        def done(note: str | None) -> None:
            if note is not None:
                with session(self.db_path) as con:
                    set_job_note(con, job["dedup_key"], note.strip() or None)
                self._reload(self.query_one(DataTable).cursor_row)

        self.push_screen(NoteModal(f"Edit note for {job['company']} (blank clears)"), done)

    def _mark(self, key: str, status: FunnelState, note: str | None = None) -> None:
        row = self.query_one(DataTable).cursor_row
        with session(self.db_path) as con:
            prev = transition_job_state(con, key, status, "tui", note)
        self.undo_stack.append((key, prev))
        still_listed = FILTERS[self.filter_idx] in (None, status.value)
        self._reload(row + 1 if still_listed else row)

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

    def action_undo(self) -> None:
        if not self.undo_stack:
            self.notify("Nothing to undo.", severity="warning")
            return
        key, snapshot = self.undo_stack.pop()
        with session(self.db_path) as con:
            restore_job_state(con, key, snapshot)
        self.notify(f"Restored status to {snapshot['status']}")
        self._reload(self.query_one(DataTable).cursor_row)

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

    def action_segment(self) -> None:
        with session(self.db_path) as con:
            options: list[str | None] = [None, *list_segments(con)]
        at = options.index(self.segment) if self.segment in options else 0
        self.segment = options[(at + 1) % len(options)]
        self._reload(0)

    def action_location(self) -> None:
        def done(value: str | None) -> None:
            if value is not None:
                self.loc_query = value.strip().lower() or None
                self._reload(0)

        self.push_screen(NoteModal("Filter by location, e.g. wa, seattle, remote (blank clears)"), done)

    def action_search(self) -> None:
        def done(value: str | None) -> None:
            if value is not None:
                self.search_query = value.strip().lower() or None
                self._reload(0)

        self.push_screen(NoteModal("Search company, title, or keywords (blank clears)"), done)

    def action_sync(self) -> None:
        self.notify("Syncing Gmail…")
        self.run_worker(self._sync, thread=True, exclusive=True)

    def _sync(self) -> None:
        try:
            changes = sync_gmail_statuses(self.db_path)
        except Exception as e:
            self.call_from_thread(self.notify, str(e), severity="error")
            return
        self.call_from_thread(self.notify, f"Mail sync: {len(changes)} status change(s)")
        self.call_from_thread(self._reload, self.query_one(DataTable).cursor_row)

    def _kick_intel(self) -> None:
        self.run_worker(self._run_intel_worker, thread=True, exclusive=True)

    def _run_intel_worker(self) -> None:
        try:
            ctx = intel.load_context(self.career_ops_root, model=self.model)
        except intel.IntelUnavailable as e:
            return
        except Exception as e:
            return

        generator = self.generate
        if generator is None:
            try:
                generator = intel.create_gemini_client(model_name=ctx.model)
            except intel.IntelUnavailable:
                return

        processed = intel.process_batch(self.db_path, ctx, generator, self.export_dir, limit=5)
        if processed:
            self.call_from_thread(self._reload, self.query_one(DataTable).cursor_row)
