from __future__ import annotations

import asyncio
import hashlib
import plistlib
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import duckdb
import pandas as pd
import pytest
from rich.style import Style
from rich.text import Text
from textual.widgets import DataTable, Static

from cockpit import db, intel, macos
from cockpit.cli import render_plist
from cockpit.models import FunnelState
from cockpit.normalizer import highlight_jd, linkedin_referral_url, load_rules, norm_company, render_outreach, score_job
from cockpit.tui import FILTERS, JobCockpitApp


@pytest.fixture
def db_path(tmp_path, monkeypatch) -> Path:
    monkeypatch.setattr(db, "TMUX_CACHE", tmp_path / "tmux.cache")
    return tmp_path / "funnel.duckdb"


@pytest.fixture
def calls(monkeypatch) -> list[dict]:
    """Capture every subprocess.run the macOS layer makes instead of touching pbcopy/open."""
    recorded: list[dict] = []

    def fake_run(cmd, **kw):
        recorded.append({"cmd": cmd, "input": kw.get("input")})
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(macos.subprocess, "run", fake_run)
    return recorded


def frame(*rows: dict) -> pd.DataFrame:
    base = {"id": "x", "site": "linkedin", "title": "Software Engineer I", "company": "Stripe",
            "location": "Seattle, WA", "job_url": "https://l/1", "job_url_direct": None,
            "description": "We use FastAPI and Docker.", "date_posted": "2026-10-01"}
    return pd.DataFrame([{**base, **r} for r in rows])


def test_cross_board_dedup(db_path):
    df = frame({"company": "Stripe, Inc.", "site": "linkedin"}, {"company": "Stripe", "site": "indeed"})
    assert norm_company("Stripe, Inc.") == norm_company("Stripe") == "stripe"
    with db.session(db_path) as con:
        stats = db.upsert_jobspy_frame(con, df)
        assert con.execute("SELECT count(DISTINCT dedup_key), count(*) FROM jobs_master").fetchone() == (1, 1)
    assert stats["inserted"] == 1


def test_upsert_jobspy_frame_many_null_descriptions(db_path):
    # Polars infers pl.Null when the first 100+ rows lack descriptions; explicit schema prevents ComputeError.
    rows = [
        {"company": f"Company {i}", "title": f"Engineer {i}", "job_url": f"https://example.com/{i}", "description": None}
        for i in range(105)
    ]
    rows.append({
        "company": "Deloitte",
        "title": "Software Engineer II",
        "job_url": "https://example.com/deloitte",
        "description": "Help public-sector organizations operate reliable...",
    })
    df = pd.DataFrame(rows)
    with db.session(db_path) as con:
        stats = db.upsert_jobspy_frame(con, df)
        assert stats["inserted"] == 106


def test_direct_url_wins(db_path):
    df = frame({"job_url_direct": "https://ats/direct"}, {"company": "Acme", "job_url_direct": ""})
    with db.session(db_path) as con:
        db.upsert_jobspy_frame(con, df)
        urls = dict(con.execute("SELECT company, apply_url FROM jobs_master").fetchall())
    assert urls == {"Stripe": "https://ats/direct", "Acme": "https://l/1"}


def test_reingest_preserves_applied(db_path):
    df = frame({})
    with db.session(db_path) as con:
        db.upsert_jobspy_frame(con, df)
        key = con.execute("SELECT dedup_key FROM jobs_master").fetchone()[0]
        db.transition_job_state(con, key, FunnelState.APPLIED, "test")
        again = db.upsert_jobspy_frame(con, df)
        status, applied_at = con.execute("SELECT status, applied_at IS NOT NULL FROM jobs_master").fetchone()
    assert again["inserted"] == 0 and status == "APPLIED" and applied_at


def test_transition_writes_audit_row(db_path):
    with db.session(db_path) as con:
        db.upsert_jobspy_frame(con, frame({}))
        key = con.execute("SELECT dedup_key FROM jobs_master").fetchone()[0]
        db.transition_job_state(con, key, FunnelState.SKIPPED, "test", "meh")
        row = con.execute("SELECT from_status, to_status, triggered_by, notes FROM state_transitions").fetchone()
    assert row == ("QUEUED", "SKIPPED", "test", "meh")


def test_scoring_penalty_and_boost():
    rules = load_rules()
    junior = score_job("Data Engineer I", "dbt and polars", rules)
    senior = score_job("Senior Data Engineer", "dbt and polars", rules)
    assert junior.archetype.value == "data_eng"
    assert junior.score - senior.score == rules["boosts"]["points"] + rules["penalties"]["points"]
    assert "i" not in score_job("Rustacean Developer", "", rules).matched  # no substring matches


def test_pbcopy_gets_absolute_posix_path(monkeypatch, tmp_path, calls):
    monkeypatch.setenv("HOME", str(tmp_path))
    path = macos.copy_resume_path("~/resumes/data_eng_lakehouse_v1.pdf")
    expected = f"{tmp_path.resolve()}/resumes/data_eng_lakehouse_v1.pdf"
    assert path == expected and path.startswith("/") and "~" not in path
    assert calls == [{"cmd": ["/usr/bin/pbcopy"], "input": expected.encode()}]


def test_open_background_flag(calls):
    macos.open_url("https://x")
    macos.open_url("https://x", background=True)
    assert [c["cmd"] for c in calls] == [["/usr/bin/open", "https://x"], ["/usr/bin/open", "-g", "https://x"]]


def test_notify_macos(calls, monkeypatch):
    monkeypatch.setattr(macos.sys, "platform", "darwin")
    macos.notify_macos(title="Test Title", message='Test "Quoted" Message')
    assert calls[-1]["cmd"] == [
        "/usr/bin/osascript",
        "-e",
        'display notification "Test \\"Quoted\\" Message" with title "Test Title"',
    ]


def test_tmux_cache_updates_on_transition(db_path):
    with db.session(db_path) as con:
        db.upsert_jobspy_frame(con, frame({}))
        assert db.TMUX_CACHE.read_text() == "#[fg=green]Jobs: 0/15#[default] | Q:1 (72h:1) | H:0\n"
        key = con.execute("SELECT dedup_key FROM jobs_master").fetchone()[0]
        db.transition_job_state(con, key, FunnelState.APPLIED, "test")
    assert db.TMUX_CACHE.read_text() == "#[fg=green]Jobs: 1/15#[default] | Q:0 (72h:0) | H:0\n"


def test_y_copies_outreach_to_clipboard(db_path, calls):
    with db.session(db_path) as con:
        db.upsert_jobspy_frame(con, frame({}))
        job = db.fetch_jobs(con)[0]
    expected = render_outreach(job, load_rules())

    async def drive() -> None:
        app = JobCockpitApp(db_path)
        async with app.run_test() as pilot:
            await pilot.press("y")

    asyncio.run(drive())
    pb = [c for c in calls if c["cmd"] == ["/usr/bin/pbcopy"]]
    assert pb and pb[-1]["input"].decode() == expected
    assert "Stripe" in expected and "Software Engineer I" in expected and "{" not in expected


def test_install_agent_plist_is_valid_xml_with_absolute_paths():
    xml = render_plist(Path("/Users/alex/Github/ne1cc/career-pipeline"), "/Users/alex/.local/bin/uv")
    ET.fromstring(xml)  # well-formed
    plist = plistlib.loads(xml.encode())
    cmd = plist["ProgramArguments"][-1]
    assert plist["Label"] == "com.jobcockpit.sync"
    assert plist["StartCalendarInterval"] == [{"Hour": 8, "Minute": 30}, {"Hour": 13, "Minute": 30}]
    assert plist["WorkingDirectory"].startswith("/") and "/Users/alex/.local/bin/uv run --no-project" in cmd and "python -m cockpit.cli ingest" in cmd
    assert plist["StandardOutPath"] == "/tmp/jobcockpit.out.log"


def test_title_decides_archetype_over_default():
    rules = load_rules()
    assert score_job("Software Engineer I", "marketing analytics dashboards", rules).archetype.value == "swe_backend"
    assert score_job("Analytics Engineer, Associate", "", rules).archetype.value == "data_eng"
    assert score_job("Business Analyst I", "", rules).archetype.value == "analytics_msba"


def test_location_boost_tiers():
    rules = load_rules()
    base = score_job("Data Analyst", "", rules).score
    assert score_job("Data Analyst", "", rules, "Seattle, WA, US").score == base + 10
    assert score_job("Data Analyst", "", rules, "", is_remote=True).score == base + 10
    assert score_job("Data Analyst", "", rules, "Los Angeles, CA, US").score == base + 5
    assert score_job("Data Analyst", "", rules, "Washington, DC, US").score == base  # DC is not WA


def test_min_score_hides_queued_noise_and_rescore_updates(db_path):
    df = frame({}, {"company": "Acme", "title": "Senior Staff Engineer"})
    with db.session(db_path) as con:
        db.upsert_jobspy_frame(con, df)
        assert [j["company"] for j in db.fetch_jobs(con, "QUEUED", floor=10)] == ["Stripe"]
        assert db.scoreboard(con)["queued"] == 1
        con.execute("UPDATE jobs_master SET priority_score = 0, resume_archetype = 'analytics_msba'")
        assert db.rescore_queued(con) == 2
        stripe = [j for j in db.fetch_jobs(con) if j["company"] == "Stripe"][0]
    assert stripe["resume_archetype"] == "swe_backend" and stripe["priority_score"] > 10


def test_segments_are_kept_and_filterable(db_path):
    df = frame(
        {"segment": "seattle", "location": "Seattle, WA, US"},
        {"segment": "seattle_entry_level", "location": "Seattle, WA, US"},  # same job, second sheet
        {"segment": "remote", "company": "Acme", "location": "Austin, TX, US"},
    )
    with db.session(db_path) as con:
        db.upsert_jobspy_frame(con, df)
        db.upsert_jobspy_frame(con, df)  # re-ingest must not duplicate segment rows
        assert db.list_segments(con) == ["remote", "seattle", "seattle_entry_level"]
        assert con.execute("SELECT count(*) FROM job_segments").fetchone()[0] == 3
        names = lambda **kw: sorted(j["company"] for j in db.fetch_jobs(con, **kw))  # noqa: E731
        assert names(segment="seattle_entry_level") == ["Stripe"]
        assert names(segment="remote") == ["Acme"]
        assert names(location="wa") == ["Stripe"]
        assert names(location="tx") == ["Acme"]


# --- Saved jobs, intel pipeline, TUI upgrades -------------------------------------------------

SUMMARY_OUTPUT = """## Block A — Role Summary
Backend role on the payments team.
---SCORE_SUMMARY---
COMPANY: Stripe
ROLE: Software Engineer I
SCORE: 4.2
ARCHETYPE: Backend
LEGITIMACY: High Confidence
---END_SUMMARY---
"""


@pytest.fixture
def career_ops(tmp_path, monkeypatch) -> Path:
    """Minimal career-ops tree. modes/_profile.md is deliberately absent, as on the real machine."""
    for var in ("CAREER_OPS_ROOT", "CAREER_OPS_DATA_DIR"):
        monkeypatch.delenv(var, raising=False)
    root = tmp_path / "career-ops"
    (root / "modes").mkdir(parents=True)
    (root / "config").mkdir()
    (root / "modes" / "oferta.md").write_text("# Mode: job — Full A-G Evaluation")
    (root / "modes" / "_shared.md").write_text("shared rules")
    (root / "cv.md").write_text("# Alex Sun\nPython, DuckDB")
    (root / "config" / "profile.yml").write_text("name: Alex\nspend_tier: economy\n")
    return root


class FakeGemini:
    def __init__(self, output: str = SUMMARY_OUTPUT, error: Exception | None = None) -> None:
        self.output, self.error, self.calls = output, error, []

    def __call__(self, system: str, user: str) -> str:
        self.calls.append((system, user))
        if self.error:
            raise self.error
        return self.output


def _key(con) -> str:
    return con.execute("SELECT dedup_key FROM jobs_master").fetchone()[0]


def test_toggle_saved_round_trip_and_saved_filter(db_path):
    df = frame({}, {"company": "Acme", "title": "Senior Staff Engineer"})  # Acme scores below min_score
    with db.session(db_path) as con:
        db.upsert_jobspy_frame(con, df)
        acme = con.execute("SELECT dedup_key FROM jobs_master WHERE company = 'Acme'").fetchone()[0]
        assert db.toggle_saved(con, acme) is True
        assert [j["company"] for j in db.fetch_jobs(con, "SAVED", floor=10)] == ["Acme"]
        assert "Acme" in [j["company"] for j in db.fetch_jobs(con, "QUEUED", floor=10)]  # saved beats min_score
        assert db.scoreboard(con)["saved"] == 1
        row = con.execute("SELECT intel_status, saved_at IS NOT NULL FROM jobs_master WHERE dedup_key = ?", [acme]).fetchone()
        assert row == ("pending", True)
        assert db.toggle_saved(con, acme) is False
        assert db.fetch_jobs(con, "SAVED") == []


def test_schema_migrates_existing_db(db_path):
    with db.session(db_path) as con:
        db.upsert_jobspy_frame(con, frame({}))
        for col in ("is_saved", "saved_at", "fit_score", "intel_status", "intel_path", "intel_error",
                    "intel_hash", "intel_updated_at"):
            con.execute(f"ALTER TABLE jobs_master DROP COLUMN {col}")
    with db.session(db_path) as con:  # reopening must add the columns back without touching the row
        assert con.execute("SELECT company, is_saved FROM jobs_master").fetchone() == ("Stripe", False)


def test_connect_waits_for_a_held_lock(db_path):
    db.connect(db_path).close()
    code = (f"import duckdb, time; c = duckdb.connect({str(db_path)!r}); print('locked', flush=True); "
            "time.sleep(0.8); c.close()")
    child = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True)
    try:
        assert child.stdout.readline().strip() == "locked"
        start = time.monotonic()
        db.connect(db_path).close()  # would raise IOException immediately without the retry
        assert time.monotonic() - start > 0.3
    finally:
        child.wait(timeout=10)


def test_claim_is_exclusive_and_stale_running_requeues(db_path):
    with db.session(db_path) as con:
        db.upsert_jobspy_frame(con, frame({}))
        db.toggle_saved(con, _key(con))
        assert [j["company"] for j in db.claim_intel(con, 2)] == ["Stripe"]
        assert db.claim_intel(con, 2) == []  # already running
        assert db.requeue_stale_intel(con, minutes=15) == 0
        con.execute("UPDATE jobs_master SET intel_updated_at = CURRENT_TIMESTAMP - INTERVAL 20 MINUTE")
        assert db.requeue_stale_intel(con, minutes=15) == 1
        assert len(db.claim_intel(con, 2)) == 1


def test_parse_summary():
    s = intel.parse_summary(SUMMARY_OUTPUT)
    assert (s.company, s.role, s.score, s.archetype, s.legitimacy) == (
        "Stripe", "Software Engineer I", 4.2, "Backend", "High Confidence")
    with pytest.raises(ValueError):
        intel.parse_summary("no summary block here")


def test_load_context_reads_career_ops_in_place(career_ops):
    ctx = intel.load_context(career_ops)
    assert ctx.model in ("sonnet", "gemini-3.5-flash-lite")
    assert intel.load_context(career_ops, provider="gemini").model == "gemini-3.5-flash-lite"
    assert intel.load_context(career_ops, model="gemini-2.5-pro").model == "gemini-2.5-pro"
    (career_ops / "cv.md").write_text("# Alex Sun\nPython, DuckDB, Rust")
    assert intel.load_context(career_ops).hash != ctx.hash  # CV edits invalidate the cache
    (career_ops / "cv.md").unlink()
    with pytest.raises(intel.IntelUnavailable):
        intel.load_context(career_ops)


def test_load_context_requires_detailed_dossier_without_losing_career_ops(career_ops):
    prompt = intel.load_context(career_ops).prompt
    for heading in (
        "1. The Unspoken Translation (Deconstructing the JD)",
        "2. Industry “Taste” & Production Reality",
        "3. Resume-to-Role Gap Analysis & Positioning",
        "4. Curated “No-Fluff” Study & Resource Guide",
        "5. The Technical Interview Playbook",
    ):
        assert heading in prompt

    assert "Blocks A through G" in prompt
    assert "# Mode: job — Full A-G Evaluation" in prompt
    assert "---SCORE_SUMMARY---" in prompt and "---END_SUMMARY---" in prompt
    assert "JD facts" in prompt and "hypotheses" in prompt
    assert "candidate experience" in prompt and "Do not invent" in prompt
    assert "untrusted" in prompt.lower()
    assert "search queries" in prompt and "URLs" in prompt


def test_context_hash_covers_rendered_prompt_and_selected_model(career_ops):
    ctx = intel.load_context(career_ops, model="gemini-2.5-pro")
    assert ctx.hash == hashlib.sha256((ctx.prompt + ctx.model).encode("utf-8")).hexdigest()[:16]

    other_model = intel.load_context(career_ops, model="gemini-2.5-flash")
    assert other_model.prompt == ctx.prompt
    assert other_model.hash != ctx.hash


def test_process_batch_writes_dossier_and_caches(db_path, career_ops, tmp_path):
    out, fake = tmp_path / "dossiers", FakeGemini()
    ctx = intel.load_context(career_ops)
    with db.session(db_path) as con:
        db.upsert_jobspy_frame(con, frame({}))
        key = _key(con)
        db.toggle_saved(con, key)
    assert intel.process_batch(db_path, ctx, fake, out) == [key]
    assert fake.calls[0][0] == ctx.prompt
    assert "We use FastAPI and Docker." in fake.calls[0][1] and "UNTRUSTED INPUT" in fake.calls[0][1]
    with db.session(db_path) as con:
        fit, status, path = con.execute("SELECT fit_score, intel_status, intel_path FROM jobs_master").fetchone()
    text = Path(path).read_text()
    assert (fit, status) == (4.2, "done") and Path(path).parent.parent == out
    assert Path(path).name == "05_interview_dossier.md"
    assert (Path(path).parent / "00_job_posting.md").exists()
    assert "fit_score: 4.2" in text and "Block A — Role Summary" in text and "We use FastAPI" in text
    assert "SCORE_SUMMARY" not in text
    assert "Stripe" in (out / "index.md").read_text()
    with db.session(db_path) as con:  # unsave + resave a finished job: no second Gemini call
        db.toggle_saved(con, key)
        db.toggle_saved(con, key)
    assert intel.process_batch(db_path, ctx, fake, out) == [] and len(fake.calls) == 1


def test_process_batch_records_failures(db_path, career_ops, tmp_path):
    ctx = intel.load_context(career_ops)
    with db.session(db_path) as con:
        db.upsert_jobspy_frame(con, frame({}))
        db.toggle_saved(con, _key(con))
    intel.process_batch(db_path, ctx, FakeGemini(output="garbage"), tmp_path)
    with db.session(db_path) as con:
        status, error, path = con.execute("SELECT intel_status, intel_error, intel_path FROM jobs_master").fetchone()
    assert status == "failed" and "SCORE_SUMMARY" in error
    assert "SCORE_SUMMARY" in Path(path).read_text()  # the dossier still exists and says what went wrong


def test_undo_restores_previous_state(db_path):
    with db.session(db_path) as con:
        db.upsert_jobspy_frame(con, frame({}))
        key = _key(con)
        snap = db.transition_job_state(con, key, FunnelState.APPLIED, "test")
        assert snap["status"] == "QUEUED" and snap["applied_at"] is None
        db.restore_job_state(con, key, snap)
        status, applied = con.execute("SELECT status, applied_at FROM jobs_master").fetchone()
        last = con.execute("SELECT to_status, triggered_by FROM state_transitions ORDER BY transition_id DESC").fetchone()
    assert (status, applied) == ("QUEUED", None) and last == ("QUEUED", "undo")


def test_search_query_and_notes(db_path):
    df = frame({}, {"company": "Acme", "title": "Data Analyst", "description": "Snowflake and Looker"})
    with db.session(db_path) as con:
        db.upsert_jobspy_frame(con, df)
        names = lambda q: sorted(j["company"] for j in db.fetch_jobs(con, query=q))  # noqa: E731
        assert names("acme") == ["Acme"] and names("looker") == ["Acme"] and names("software") == ["Stripe"]
        key = con.execute("SELECT dedup_key FROM jobs_master WHERE company = 'Acme'").fetchone()[0]
        db.set_job_note(con, key, "ping Jane")
        assert con.execute("SELECT notes FROM jobs_master WHERE dedup_key = ?", [key]).fetchone()[0] == "ping Jane"


def test_highlight_jd_escapes_then_colours():
    rules = load_rules()
    out = highlight_jd("Senior role [remote] using FastAPI.", ["fastapi"], rules)
    assert "[bold green]FastAPI[/bold green]" in out and "[bold red]Senior[/bold red]" in out
    assert "\\[remote]" in out  # literal brackets from the JD can't be read as markup

    out_disq = highlight_jd("Requires TS/SCI clearance and 5+ years of experience.", [], rules)
    assert "[bold red]TS/SCI[/bold red]" in out_disq
    assert "[bold red]5+ years of experience[/bold red]" in out_disq


def test_linkedin_referral_url():
    assert linkedin_referral_url("AT&T Inc") == "https://www.linkedin.com/search/results/people/?keywords=AT%26T%20Inc"


def _app(db_path, tmp_path, career_ops=None, fake=None) -> JobCockpitApp:
    return JobCockpitApp(db_path, export_dir=tmp_path / "dossiers", career_ops_root=career_ops, generate=fake)


def test_b_saves_greys_row_and_fills_fit(db_path, career_ops, tmp_path, calls):
    with db.session(db_path) as con:
        db.upsert_jobspy_frame(con, frame({}))
        f2 = frame({"company": "Acme Corp", "title": "Data Engineer", "link": "https://example.com/job-2"})
        db.upsert_jobspy_frame(con, f2)
    fake = FakeGemini()

    async def drive() -> None:
        app = _app(db_path, tmp_path, career_ops, fake)
        async with app.run_test() as pilot:
            await pilot.press("b")
            await app.workers.wait_for_complete()
            await pilot.pause()
            table = app.query_one(DataTable)
            row = table.get_row_at(0)
            assert "grey23" in str(table._get_row_style(0, Style()).bgcolor)
            assert "★" in (row[1].plain if isinstance(row[1], Text) else str(row[1]))
            assert "4.2" in [c.plain if isinstance(c, Text) else str(c) for c in row]

            # When cursor moves off row 0, verify the entire row line is highlighted uniformly
            await pilot.press("down")
            await pilot.pause()
            lines = table.render_lines(table.region)
            row_0_segs = [s for s in lines[0] if s.text]
            assert len(row_0_segs) > 0
            for seg in row_0_segs:
                assert "grey23" in str(seg.style.bgcolor) or "58" in str(seg.style.bgcolor)

            # In SAVED view, saved jobs must NOT be highlighted (cycle QUEUED -> 72H -> SAVED)
            await pilot.press("f")
            await pilot.press("f")
            await pilot.pause()
            assert "grey23" not in str(table._get_row_style(0, Style()).bgcolor)

    asyncio.run(drive())
    assert len(fake.calls) == 1 and list((tmp_path / "dossiers").glob("Software Engineer I - * - Seattle, WA - */05_interview_dossier.md"))


def test_cockpit_table_highlight_cursor_has_black_text(db_path, tmp_path):
    with db.session(db_path) as con:
        db.upsert_jobspy_frame(con, frame({"company": "Acme Corp", "title": "Software Engineer"}))

    async def drive() -> None:
        app = _app(db_path, tmp_path)
        async with app.run_test() as pilot:
            table = app.query_one(DataTable)
            style = table.get_component_rich_style("datatable--cursor")
            assert str(style.color) == "#000000" or getattr(style.color, "triplet", None) == (0, 0, 0)
            lines = table._render_cell(0, 2, table._get_row_style(0, table.rich_style), 20, cursor=True)
            rendered_texts = [s for s in lines[0] if s.text.strip()]
            assert any(
                s.text == "Acme Corp" and (str(s.style.color) == "#000000" or getattr(s.style.color, "triplet", None) == (0, 0, 0))
                for s in rendered_texts
            )

    asyncio.run(drive())


def test_dossier_path_format(tmp_path):
    job = {
        "title": "Senior Data / Platform Engineer",
        "company": "Acme: Corp",
        "location": "Seattle, WA",
        "saved_at": "2026-10-19 14:30:00",
    }
    p = intel.dossier_path(job, tmp_path)
    assert p.parent.name == "Senior Data Platform Engineer - Acme Corp - Seattle, WA - 2026-10-19"
    assert p.name == "05_interview_dossier.md"


def test_jd_pane_scrolls_with_keys(db_path, tmp_path, calls):
    long_jd = "\n".join(f"Responsibility line {i}" for i in range(400))
    with db.session(db_path) as con:
        db.upsert_jobspy_frame(con, frame({"description": long_jd}))

    async def drive() -> None:
        app = _app(db_path, tmp_path)
        async with app.run_test() as pilot:
            pane = app.query_one("#detail-pane")
            await pilot.press("J")
            await pilot.pause()
            assert pane.scroll_y == 3
            await pilot.press("d")
            await pilot.pause()
            assert pane.scroll_y > 3
            await pilot.press("u")
            await pilot.pause()
            assert pane.scroll_y == 3
            await pilot.press("K")
            await pilot.pause()
            assert pane.scroll_y == 0

    asyncio.run(drive())


def test_z_undoes_skip(db_path, tmp_path, calls):
    with db.session(db_path) as con:
        db.upsert_jobspy_frame(con, frame({}))

    async def drive() -> None:
        app = _app(db_path, tmp_path)
        async with app.run_test() as pilot:
            await pilot.press("s")
            assert app.rows == []
            await pilot.press("z")
            assert [r["status"] for r in app.rows] == ["QUEUED"]

    asyncio.run(drive())


def test_question_mark_searches_and_r_i_open(db_path, tmp_path, calls):
    dossier = tmp_path / "acme.md"
    dossier.write_text("# Acme")
    with db.session(db_path) as con:
        db.upsert_jobspy_frame(con, frame({}, {"company": "Acme", "title": "Data Engineer I"}))
        acme = con.execute("SELECT dedup_key FROM jobs_master WHERE company = 'Acme'").fetchone()[0]
        con.execute("UPDATE jobs_master SET intel_path = ? WHERE dedup_key = ?", [str(dossier), acme])

    async def drive() -> None:
        app = _app(db_path, tmp_path)
        async with app.run_test() as pilot:
            await pilot.press("question_mark", *"acme", "enter")
            assert [r["company"] for r in app.rows] == ["Acme"]
            await pilot.press("r", "i")

    asyncio.run(drive())
    opened = [c["cmd"] for c in calls if c["cmd"][0] == "/usr/bin/open"]
    assert ["/usr/bin/open", linkedin_referral_url("Acme")] in opened
    assert ["/usr/bin/open", str(dossier)] in opened


def test_cli_keychain_setup(calls):
    import os
    from typer.testing import CliRunner
    from cockpit.cli import app as cli_app

    runner = CliRunner()
    res = runner.invoke(cli_app, ["keychain-setup", "--service", "gemini", "--password", "secret123"])
    assert res.exit_code == 0
    cmd = calls[-1]["cmd"]
    assert cmd == ["/usr/bin/security", "add-generic-password", "-a", os.environ.get("USER", ""), "-s", "jobcockpit-gemini", "-U", "-w", "secret123"]


def test_cli_intel(db_path, career_ops, tmp_path, monkeypatch):
    from typer.testing import CliRunner
    from cockpit.cli import app as cli_app

    with db.session(db_path) as con:
        db.upsert_jobspy_frame(con, frame({}))
        key = con.execute("SELECT dedup_key FROM jobs_master").fetchone()[0]
        db.toggle_saved(con, key)

    monkeypatch.setattr(db, "DB_PATH", db_path)
    fake = FakeGemini()
    monkeypatch.setattr(intel, "create_gemini_client", lambda model_name: fake)
    monkeypatch.setattr(intel, "create_claude_cli_client", lambda *a, **kw: fake)
    monkeypatch.setattr(intel, "create_llm_client", lambda *a, **kw: fake)

    runner = CliRunner()
    res = runner.invoke(cli_app, ["intel", "--career-ops-root", str(career_ops), "--export-dir", str(tmp_path / "dossiers")])
    assert res.exit_code == 0
    assert "Intel pipeline finished: processed 1 job(s)" in res.stdout
    with db.session(db_path) as con:
        fit, status = con.execute("SELECT fit_score, intel_status FROM jobs_master WHERE dedup_key = ?", [key]).fetchone()
    assert fit == 4.2 and status == "done"



def test_column_sorting_by_click(db_path, tmp_path, calls):
    df = pd.DataFrame([
        {
            "id": "1",
            "site": "linkedin",
            "title": "Software Engineer I",
            "company": "Zebra Technologies",
            "location": "Seattle, WA",
            "job_url": "https://linkedin.com/jobs/view/1",
            "job_url_direct": None,
            "description": "Python, FastAPI",
            "date_posted": "2026-10-15",
        },
        {
            "id": "2",
            "site": "indeed",
            "title": "Data Platform Engineer",
            "company": "Alpha Corp",
            "location": "Remote",
            "job_url": "https://indeed.com/viewjob?jk=2",
            "job_url_direct": None,
            "description": "Python, DuckDB",
            "date_posted": "2026-10-16",
        },
        {
            "id": "3",
            "site": "zip_recruiter",
            "title": "Backend Architect",
            "company": "Beta Systems",
            "location": "New York, NY",
            "job_url": "https://ziprecruiter.com/jobs/3",
            "job_url_direct": None,
            "description": "FastAPI, PostgreSQL",
            "date_posted": "2026-10-14",
        },
    ])
    with db.session(db_path) as con:
        db.upsert_jobspy_frame(con, df)
        con.execute("UPDATE jobs_master SET priority_score = 95.0, fit_score = 3.5 WHERE company = 'Zebra Technologies'")
        con.execute("UPDATE jobs_master SET priority_score = 75.0, fit_score = 4.8 WHERE company = 'Alpha Corp'")
        con.execute("UPDATE jobs_master SET priority_score = 85.0, fit_score = NULL WHERE company = 'Beta Systems'")

    async def drive() -> None:
        app = _app(db_path, tmp_path)
        async with app.run_test() as pilot:
            table = app.query_one(DataTable)
            # Default order is priority_score DESC: Zebra (95), Beta (85), Alpha (75)
            assert [r["company"] for r in app.rows] == ["Zebra Technologies", "Beta Systems", "Alpha Corp"]

            # 1. Click 'Company' header: Ascending (A -> Z)
            table.post_message(DataTable.HeaderSelected(table, table.columns["company"].key, 2, Text("Company")))
            await pilot.pause()
            assert [r["company"] for r in app.rows] == ["Alpha Corp", "Beta Systems", "Zebra Technologies"]
            assert "▲" in str(table.columns["company"].label)
            assert "sort:company↑" in str(app.query_one("#score", Static).content)

            # 2. Click 'Company' header again: Descending (Z -> A)
            table.post_message(DataTable.HeaderSelected(table, table.columns["company"].key, 2, table.columns["company"].label))
            await pilot.pause()
            assert [r["company"] for r in app.rows] == ["Zebra Technologies", "Beta Systems", "Alpha Corp"]
            assert "▼" in str(table.columns["company"].label)
            assert "sort:company↓" in str(app.query_one("#score", Static).content)

            # 3. Click 'Company' header 3rd time: Reset to default order
            table.post_message(DataTable.HeaderSelected(table, table.columns["company"].key, 2, table.columns["company"].label))
            await pilot.pause()
            assert [r["company"] for r in app.rows] == ["Zebra Technologies", "Beta Systems", "Alpha Corp"]
            assert "▲" not in str(table.columns["company"].label) and "▼" not in str(table.columns["company"].label)
            assert "sort:" not in str(app.query_one("#score", Static).content)

            # 4. Click 'Score' header: 1st click Descending (highest score first)
            table.post_message(DataTable.HeaderSelected(table, table.columns["score"].key, 1, Text("Score")))
            await pilot.pause()
            assert [r["priority_score"] for r in app.rows] == [95.0, 85.0, 75.0]
            assert "▼" in str(table.columns["score"].label)

            # 5. Click 'Score' header again: Ascending (lowest score first)
            table.post_message(DataTable.HeaderSelected(table, table.columns["score"].key, 1, table.columns["score"].label))
            await pilot.pause()
            assert [r["priority_score"] for r in app.rows] == [75.0, 85.0, 95.0]
            assert "▲" in str(table.columns["score"].label)

            # 6. Click 'Fit' header: 1st click Descending (None at bottom)
            table.post_message(DataTable.HeaderSelected(table, table.columns["fit"].key, 0, Text("Fit")))
            await pilot.pause()
            assert [r["company"] for r in app.rows] == ["Alpha Corp", "Zebra Technologies", "Beta Systems"]
            assert [r["fit_score"] for r in app.rows] == [4.8, 3.5, None]
            assert "▼" in str(table.columns["fit"].label)

            # 7. Click 'Fit' header again: Ascending (None still at bottom)
            table.post_message(DataTable.HeaderSelected(table, table.columns["fit"].key, 0, table.columns["fit"].label))
            await pilot.pause()
            assert [r["company"] for r in app.rows] == ["Zebra Technologies", "Alpha Corp", "Beta Systems"]
            assert [r["fit_score"] for r in app.rows] == [3.5, 4.8, None]
            assert "▲" in str(table.columns["fit"].label)

            # 8. Selection preservation across sorts:
            table.move_cursor(row=2)
            await pilot.pause()
            assert app._job()["company"] == "Beta Systems"

            # Sort by company ascending (Beta Systems moves to row index 1)
            table.post_message(DataTable.HeaderSelected(table, table.columns["company"].key, 2, Text("Company")))
            await pilot.pause()
            assert table.cursor_row == 1
            assert app._job()["company"] == "Beta Systems"

    asyncio.run(drive())


def test_resolve_db_path(tmp_path, monkeypatch):
    p = tmp_path / "custom.duckdb"
    assert db.resolve_db_path(p) == p.resolve()

    monkeypatch.setenv("COCKPIT_DB_PATH", str(tmp_path / "env.duckdb"))
    assert db.resolve_db_path() == (tmp_path / "env.duckdb").resolve()
    monkeypatch.delenv("COCKPIT_DB_PATH")

    wt_dir = tmp_path / "wt"
    wt_dir.mkdir()
    main_repo = tmp_path / "main"
    main_db = main_repo / "data" / "job_funnel.duckdb"
    main_db.parent.mkdir(parents=True)
    main_db.touch()
    git_file = wt_dir / ".git"
    git_file.write_text(f"gitdir: {main_repo}/.git/worktrees/wt\n")
    monkeypatch.setattr(db, "REPO_ROOT", wt_dir)
    assert db.resolve_db_path() == main_db.resolve()


def test_fetch_jobs_saved_view_bypasses_segment_and_location(db_path):
    df = pd.DataFrame([
        {"id": "1", "site": "linkedin", "title": "Data Analyst", "company": "RevenueCat",
         "location": "Americas", "job_url": "https://1", "job_url_direct": None,
         "description": "SQL and Python", "date_posted": "2026-10-19", "segment": "ats_direct"},
        {"id": "2", "site": "indeed", "title": "Software Engineer", "company": "Seattle Co",
         "location": "Seattle, WA", "job_url": "https://2", "job_url_direct": None,
         "description": "Go and Docker", "date_posted": "2026-10-19", "segment": "seattle"},
    ])
    with db.session(db_path) as con:
        db.upsert_jobspy_frame(con, df)
        rc_key = con.execute("SELECT dedup_key FROM jobs_master WHERE company = 'RevenueCat'").fetchone()[0]
        db.toggle_saved(con, rc_key)

        assert len(db.fetch_jobs(con, status="QUEUED", segment="seattle")) == 1
        assert len(db.fetch_jobs(con, status="QUEUED", location="americas")) == 1

        saved_with_seattle_seg = db.fetch_jobs(con, status="SAVED", segment="seattle")
        assert len(saved_with_seattle_seg) == 1
        assert saved_with_seattle_seg[0]["company"] == "RevenueCat"

        saved_with_wa_loc = db.fetch_jobs(con, status="SAVED", location="wa")
        assert len(saved_with_wa_loc) == 1
        assert saved_with_wa_loc[0]["company"] == "RevenueCat"

        assert len(db.fetch_jobs(con, status="SAVED", query="revenue")) == 1
        assert len(db.fetch_jobs(con, status="SAVED", query="nonexistent")) == 0


def test_reconcile_saved_jobs_from_dossiers(db_path, tmp_path):
    export_dir = tmp_path / "dossiers"
    export_dir.mkdir()

    with db.session(db_path) as con:
        db.upsert_jobspy_frame(con, frame({"company": "Vola Dynamics", "title": "Legal Assoc"}))
        key = con.execute("SELECT dedup_key FROM jobs_master WHERE company = 'Vola Dynamics'").fetchone()[0]
        assert con.execute("SELECT is_saved, fit_score FROM jobs_master WHERE dedup_key = ?", [key]).fetchone() == (False, None)

    dossier_file = export_dir / "Legal Assoc - Remote - 2026-10-19.md"
    dossier_file.write_text(f"""---
dedup_key: {key}
company: "Vola Dynamics"
title: "Legal Assoc"
date_evaluated: 2026-10-19
fit_score: 3.4
legitimacy: High Confidence
status: QUEUED
---
# Evaluation content
""", "utf-8")

    with db.session(db_path) as con:
        reconciled = intel.reconcile_saved_jobs(con, export_dir)
        assert reconciled == 1
        row = con.execute("SELECT is_saved, fit_score, intel_status, intel_path FROM jobs_master WHERE dedup_key = ?", [key]).fetchone()
        assert row[0] is True
        assert row[1] == 3.4
        assert row[2] == "done"
        expected_dossier = export_dir / "Legal Assoc - Remote - 2026-10-19" / "05_interview_dossier.md"
        assert row[3] == str(expected_dossier)
        assert expected_dossier.exists()
        assert not dossier_file.exists()

        index_text = (export_dir / "index.md").read_text("utf-8")
        assert "Vola Dynamics" in index_text
        assert "3.4" in index_text

        assert intel.reconcile_saved_jobs(con, export_dir) == 0


def test_toggle_saved_records_intel_path_immediately(db_path, tmp_path):
    with db.session(db_path) as con:
        db.upsert_jobspy_frame(con, frame({}))
        key = con.execute("SELECT dedup_key FROM jobs_master").fetchone()[0]
        dossier_path = str(tmp_path / "stripe.md")
        now_saved = db.toggle_saved(con, key, intel_path=dossier_path)
        assert now_saved is True
        path_in_db = con.execute("SELECT intel_path FROM jobs_master WHERE dedup_key = ?", [key]).fetchone()[0]
        assert path_in_db == dossier_path


def test_fetch_jobs_recent_72h_filter(db_path):
    with db.session(db_path) as con:
        # One recent job (today) and one older job (5 days ago)
        db.upsert_jobspy_frame(con, frame(
            {"company": "RecentCo", "title": "Data Engineer", "date_posted": "2026-10-21"},
            {"company": "OldCo", "title": "Data Engineer", "date_posted": "2026-10-01"},
        ))
        # Backdate OldCo's discovered_at to 5 days ago
        con.execute(
            "UPDATE jobs_master SET discovered_at = CURRENT_TIMESTAMP - INTERVAL 5 DAY WHERE company = 'OldCo'"
        )

        board = db.scoreboard(con)
        assert board["queued"] == 2
        assert board["queued_72h"] == 1

        all_queued = db.fetch_jobs(con, status="QUEUED")
        assert len(all_queued) == 2

        recent_72h = db.fetch_jobs(con, status="72H")
        assert len(recent_72h) == 1
        assert recent_72h[0]["company"] == "RecentCo"

        recent_hours = db.fetch_jobs(con, status="QUEUED", hours=72)
        assert len(recent_hours) == 1
        assert recent_hours[0]["company"] == "RecentCo"


def test_tui_72h_filter_toggle_and_cycle(db_path):
    with db.session(db_path) as con:
        db.upsert_jobspy_frame(con, frame(
            {"company": "RecentCo", "title": "Data Engineer", "date_posted": "2026-10-21"},
        ))

    async def drive() -> None:
        app = JobCockpitApp(db_path)
        async with app.run_test() as pilot:
            assert app.filter_idx == 0
            assert FILTERS[app.filter_idx] == "QUEUED"

            # 't' toggles to 72H
            await pilot.press("t")
            assert FILTERS[app.filter_idx] == "72H"

            # 't' toggles back to QUEUED
            await pilot.press("t")
            assert FILTERS[app.filter_idx] == "QUEUED"

            # 'f' cycles to 72H -> SAVED -> REFERRAL_HOLD -> APPLIED -> None -> QUEUED
            await pilot.press("f")
            assert FILTERS[app.filter_idx] == "72H"
            await pilot.press("f")
            assert FILTERS[app.filter_idx] == "SAVED"

    asyncio.run(drive())


def test_cli_sprint_recent_flag(db_path, monkeypatch):
    from typer.testing import CliRunner
    from cockpit.cli import app as cli_app

    with db.session(db_path) as con:
        db.upsert_jobspy_frame(con, frame(
            {"company": "RecentCo", "title": "Data Engineer", "date_posted": "2026-10-21"},
            {"company": "OldCo", "title": "Data Engineer", "date_posted": "2026-10-01"},
        ))
        con.execute(
            "UPDATE jobs_master SET discovered_at = CURRENT_TIMESTAMP - INTERVAL 5 DAY WHERE company = 'OldCo'"
        )

    runner = CliRunner()
    monkeypatch.setattr("typer.prompt", lambda *a, **kw: "q")
    monkeypatch.setattr(macos, "copy_resume_path", lambda *a, **kw: None)
    monkeypatch.setattr(macos, "open_url", lambda *a, **kw: None)
    monkeypatch.setattr(db, "DB_PATH", db_path)

    res = runner.invoke(cli_app, ["sprint", "--recent"])
    assert res.exit_code == 0
    assert "RecentCo" in res.stdout
    assert "OldCo" not in res.stdout


def test_extract_required_yoe():
    from cockpit.normalizer import extract_required_yoe

    # Direct statements & ranges
    assert extract_required_yoe("Requires 4+ years of experience in data engineering") == 4
    assert extract_required_yoe("Minimum 5 years of relevant experience") == 5
    assert extract_required_yoe("At least 6 yrs experience with Python") == 6
    assert extract_required_yoe("4-6 years of experience required") == 4
    assert extract_required_yoe("Requiring 5 to 7 years of work experience") == 5
    assert extract_required_yoe("Four (4) years of experience with SQL") == 4
    assert extract_required_yoe("Minimum of five years of industry experience") == 5

    # False-positive exclusions: company history & age/licenses
    assert extract_required_yoe("We have been in business for over 10 years and founded 20 years ago") is None
    assert extract_required_yoe("Must be at least 18 years of age and hold a driver license for 5 years") is None

    # Multi-tier qualification: preserves the lower required tier
    assert extract_required_yoe("Bachelor with 4+ years of experience, or Master degree with 2+ years of experience") == 2
    assert extract_required_yoe("Master degree with 5+ years of experience, or Bachelor with 7+ years of experience") == 5


def test_relevance_gate_and_disqualifiers():
    rules = load_rules()

    # 1. Non-tech roles in Seattle must NOT receive early-career (+15) or location (+10) boosts
    wh = score_job("Warehouse Associate", "", rules, location="Seattle, WA")
    assert wh.score <= 0.0

    mh = score_job("Material Handler I", "", rules, location="Seattle, WA")
    assert mh.score <= 0.0

    # 2. Legitimate tech junior roles DO receive boosts
    junior_de = score_job("Data Engineer I", "", rules, location="Seattle, WA")
    assert junior_de.score >= 25.0  # +15 boost + 10 location

    # 3. Seniority and Roman numerals are hit with -100 penalty
    sr_ai = score_job("Sr AI Engineer", "dbt and python", rules, location="Seattle, WA")
    assert sr_ai.score < 10.0

    swe_iii = score_job("Software Engineer III", "python and docker", rules, location="Seattle, WA")
    assert swe_iii.score < 10.0

    phd_role = score_job("AI Research Scientist (PhD)", "python", rules, location="Seattle, WA")
    assert phd_role.score < 10.0

    # 4. Contextual exemptions: Associate Solutions Architect and Product Manager Intern are exempted
    assoc_arch = score_job("Associate Solutions Architect", "cloud", rules, location="Seattle, WA")
    assert assoc_arch.score >= 10.0

    pm_intern = score_job("Product Manager Intern", "analytics", rules, location="Seattle, WA")
    assert pm_intern.score >= 10.0

    # 5. JD clearance and high-YOE disqualifiers apply -100 penalty
    cleared = score_job("Software Engineer I", "Active TS/SCI clearance required", rules, location="Seattle, WA")
    assert cleared.score < 10.0

    high_yoe = score_job("Data Analyst", "Requires minimum of 5 years of experience", rules, location="Seattle, WA")
    assert high_yoe.score < 10.0


def test_create_claude_cli_client_calls_subprocess(monkeypatch):
    recorded_cmds = []

    def fake_subprocess_run(cmd, **kwargs):
        recorded_cmds.append({"cmd": cmd, "input": kwargs.get("input")})
        return subprocess.CompletedProcess(cmd, 0, stdout="Claude output", stderr="")

    monkeypatch.setattr(intel.subprocess, "run", fake_subprocess_run)
    monkeypatch.setattr(intel.Path, "exists", lambda self: True)

    client = intel.create_claude_cli_client(model_name="sonnet", binary_path="/opt/homebrew/bin/claude")
    out = client("System prompt here", "User prompt here")
    assert out == "Claude output"
    assert len(recorded_cmds) == 1
    assert recorded_cmds[0]["cmd"] == [
        "/opt/homebrew/bin/claude",
        "-p",
        "--model",
        "sonnet",
        "--system-prompt",
        "System prompt here",
        "--tools",
        "",
        "--no-session-persistence",
    ]
    assert recorded_cmds[0]["input"] == "User prompt here"


FULL_PACKAGE_OUTPUT = """---SECTION:FIT_ASSESSMENT---
## Requirement-to-Evidence Matrix
| Requirement | Evidence | Verdict |
| --- | --- | :---: |
| Python & SQL | local-lakehouse-engine (51 tests) | FULL |
| DuckDB / dbt | clinical-trial-intelligence (cti-dashboard.fly.dev) | FULL |

## Project Selection Rationale
Selected clinical-trial-intelligence, local-lakehouse-engine, and olist-lakehouse as strongest matches.

## Strategic Verdict
PRIORITIZE: Exceptional technical alignment with candidate portfolio.

---SECTION:TAILORED_RESUME---
# Alex Sun
Seattle, WA | suna16@uw.edu | https://github.com/ne1cc

## Technical Skills
- Python, SQL, DuckDB, dbt, Polars

## Selected Projects
### Clinical Trial Feasibility & Site Congestion Intelligence
- Built DuckDB and dbt Medallion marts processing 400,000+ studies (cti-dashboard.fly.dev).

---SECTION:COVER_LETTER---
Dear Hiring Team,

I am writing to express my enthusiastic interest in the Software Engineer role at Stripe.
My background in building contract-first lakehouses (github.com/ne1cc/local-lakehouse-engine)
and clinical analytics platforms (cti-dashboard.fly.dev) directly maps to your requirements.

I welcome the opportunity to discuss how my hands-on systems experience can contribute to Stripe.

---SECTION:CHANGELOG_AUDIT---
- Promoted clinical-trial-intelligence to top project.
- Verified 100% adherence to verified facts; zero hallucinations.

---SECTION:INTERVIEW_DOSSIER---
## Block A — Role Summary
Stripe payments backend role.

---SECTION:OUTREACH_PITCH---
### LinkedIn Connection Note
Hi Alex — noticed your team's work on Stripe payments infrastructure. Built a contract-first lakehouse with 51 tests: github.com/ne1cc/local-lakehouse-engine. Would love to connect.

---SECTION:INTERVIEW_DRILLS---
### 1. STAR+T Story: Data Ingestion Resilience
- Situation: External API payloads corrupted downstream tables.
- Task: Enforce schema contracts.
- Action: Implemented Polars dead-letter quarantine.
- Result: 100% bad payload isolation.
- Tradeoff (+T): Slight latency overhead for contract validation.

---SCORE_SUMMARY---
COMPANY: Stripe
ROLE: Software Engineer I
SCORE: 4.8
ARCHETYPE: swe_backend
LEGITIMACY: High Confidence
FIT_CATEGORY: PRIORITIZE
---END_SUMMARY---
"""


def test_full_package_generation_and_exports(db_path, career_ops, tmp_path):
    out_dir = tmp_path / "dossiers"
    ctx = intel.load_context(career_ops)

    with db.session(db_path) as con:
        db.upsert_jobspy_frame(con, frame({}))
        key = _key(con)
        db.toggle_saved(con, key)

    fake = FakeGemini(output=FULL_PACKAGE_OUTPUT)
    processed = intel.process_batch(db_path, ctx, fake, export_dir=out_dir)
    assert processed == [key]

    job_folders = [d for d in out_dir.iterdir() if d.is_dir()]
    assert len(job_folders) == 1
    folder = job_folders[0]

    # Verify all 8 markdown files exist
    assert (folder / "00_job_posting.md").exists()
    assert (folder / "01_fit_assessment.md").exists()
    assert (folder / "02_tailored_resume.md").exists()
    assert (folder / "03_cover_letter.md").exists()
    assert (folder / "04_changelog_and_audit.md").exists()
    assert (folder / "05_interview_dossier.md").exists()
    assert (folder / "06_outreach_and_pitch.md").exists()
    assert (folder / "07_interview_drills_and_star.md").exists()

    # Verify exports folder
    exports = folder / "exports"
    assert exports.exists()
    assert list(exports.glob("Alex_Sun_Resume_*.docx"))
    assert list(exports.glob("Alex_Sun_Resume_*.pdf"))
    assert list(exports.glob("Alex_Sun_Cover_Letter_*.docx"))
    assert list(exports.glob("Alex_Sun_Cover_Letter_*.pdf"))

    # Verify DuckDB records
    with db.session(db_path) as con:
        row = con.execute(
            "SELECT fit_score, fit_category, folder_path, tailored_resume_path, cover_letter_path FROM jobs_master WHERE dedup_key = ?",
            [key],
        ).fetchone()
        fit_score, fit_cat, f_path, res_path, cl_path = row
        assert fit_score == 4.8
        assert fit_cat == "PRIORITIZE"
        assert f_path == str(folder)
        assert res_path and Path(res_path).exists()
        assert cl_path and Path(cl_path).exists()


def test_tui_cover_letter_and_tailored_staging(db_path, career_ops, tmp_path, calls):
    with db.session(db_path) as con:
        db.upsert_jobspy_frame(con, frame({}))

    fake = FakeGemini(output=FULL_PACKAGE_OUTPUT)

    async def drive() -> None:
        app = _app(db_path, tmp_path, career_ops, fake)
        async with app.run_test() as pilot:
            # Press 'b' to save and kick intel worker
            await pilot.press("b")
            await pilot.pause()
            # Wait for background intel worker to complete in DuckDB
            for _ in range(50):
                with db.session(db_path) as con:
                    st = con.execute("SELECT intel_status FROM jobs_master").fetchone()
                    if st and st[0] == "done":
                        break
                await asyncio.sleep(0.1)
                await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.pause()

            # Press 'c' to copy cover letter
            await pilot.press("c")
            await pilot.pause()

            # Press 'o' to stage tailored resume
            await pilot.press("o")
            await pilot.pause()

            # Press 'y' to copy tailored outreach note
            await pilot.press("y")
            await pilot.pause()

    asyncio.run(drive())

    pb_inputs = [c["input"].decode() for c in calls if c["cmd"] == ["/usr/bin/pbcopy"] and c.get("input")]
    # Verify tailored cover letter pdf or md path was copied
    assert any("Cover_Letter" in inp for inp in pb_inputs)
    # Verify tailored resume pdf path was copied
    assert any("Resume" in inp and inp.endswith(".pdf") for inp in pb_inputs)
    # Verify tailored outreach was copied
    assert any("Stripe payments infrastructure" in inp for inp in pb_inputs)


def test_cli_dossier_and_folder_commands(tmp_path, monkeypatch):
    from typer.testing import CliRunner
    from cockpit.cli import app as cli_app

    export_dir = tmp_path / "artifacts"
    export_dir.mkdir()
    job_dir = export_dir / "Stripe - Software Engineer I"
    job_dir.mkdir()
    dossier_md = job_dir / "05_interview_dossier.md"
    dossier_md.write_text("# Stripe Dossier\nFit: 4.8", encoding="utf-8")
    (job_dir / "00_job_posting.md").write_text("# Posting", encoding="utf-8")

    opened = []
    monkeypatch.setattr(macos, "open_file", lambda p: opened.append(Path(p)))

    runner = CliRunner()

    # 1. List dossiers table
    res_list = runner.invoke(cli_app, ["dossier", "--export-dir", str(export_dir)])
    assert res_list.exit_code == 0
    assert "Saved Job Dossiers" in res_list.stdout
    assert "Stripe - Software Engineer I" in res_list.stdout

    # 2. Cat dossier
    res_cat = runner.invoke(cli_app, ["dossier", "stripe", "--cat", "--export-dir", str(export_dir)])
    assert res_cat.exit_code == 0
    assert "Stripe Dossier" in res_cat.stdout

    # 3. Open dossier
    res_open = runner.invoke(cli_app, ["dossier", "stripe", "--export-dir", str(export_dir)])
    assert res_open.exit_code == 0
    assert opened[-1] == dossier_md

    # 4. Open folder root
    res_folder_root = runner.invoke(cli_app, ["folder", "--export-dir", str(export_dir)])
    assert res_folder_root.exit_code == 0
    assert opened[-1] == export_dir

    # 5. Open specific job folder
    res_folder_job = runner.invoke(cli_app, ["folder", "stripe", "--export-dir", str(export_dir)])
    assert res_folder_job.exit_code == 0
    assert opened[-1] == job_dir

    # 6. Artifacts alias
    res_artifacts = runner.invoke(cli_app, ["artifacts", "stripe", "--export-dir", str(export_dir)])
    assert res_artifacts.exit_code == 0
    assert opened[-1] == job_dir

    # 7. Edit in nvim via cockpit edit
    subp_runs = []
    monkeypatch.setattr("subprocess.run", lambda args, **kw: subp_runs.append(args))
    res_edit = runner.invoke(cli_app, ["edit", "stripe", "--export-dir", str(export_dir)])
    assert res_edit.exit_code == 0
    assert len(subp_runs) == 1
    assert str(dossier_md) in subp_runs[-1]

    # 8. Dossier with --nvim / -v
    res_dossier_v = runner.invoke(cli_app, ["dossier", "stripe", "-v", "--export-dir", str(export_dir)])
    assert res_dossier_v.exit_code == 0
    assert len(subp_runs) == 2
    assert str(dossier_md) in subp_runs[-1]

    # 9. Edit root when no query given
    res_edit_root = runner.invoke(cli_app, ["edit", "--export-dir", str(export_dir)])
    assert res_edit_root.exit_code == 0
    assert len(subp_runs) == 3
    assert str(export_dir) in subp_runs[-1]


def test_tui_e_edits_dossier_in_editor(db_path, tmp_path, monkeypatch):
    dossier = tmp_path / "acme.md"
    dossier.write_text("# Acme Dossier")
    with db.session(db_path) as con:
        db.upsert_jobspy_frame(con, frame({}, {"company": "Acme", "title": "Data Engineer I"}))
        acme = con.execute("SELECT dedup_key FROM jobs_master WHERE company = 'Acme'").fetchone()[0]
        con.execute("UPDATE jobs_master SET intel_path = ? WHERE dedup_key = ?", [str(dossier), acme])

    editor_runs = []
    monkeypatch.setattr("subprocess.run", lambda args, **kw: editor_runs.append(args))

    async def drive() -> None:
        app = _app(db_path, tmp_path)
        async with app.run_test() as pilot:
            await pilot.press("question_mark", *"acme", "enter")
            assert [r["company"] for r in app.rows] == ["Acme"]
            await pilot.press("e")

    asyncio.run(drive())
    assert len(editor_runs) == 1
    assert str(dossier) in editor_runs[0]


def test_vendored_career_ops_resolution_and_optional_prompts(tmp_path, monkeypatch):
    for var in ("CAREER_OPS_ROOT", "CAREER_OPS_DATA_DIR"):
        monkeypatch.delenv(var, raising=False)

    # Default resolution points to REPO_ROOT / "config" / "career_ops"
    resolved = intel.resolve_career_ops_root()
    assert resolved == (intel.REPO_ROOT / "config" / "career_ops").resolve()
    assert (resolved / "modes" / "oferta.md").exists()
    assert (resolved / "modes" / "_shared.md").exists()
    assert (resolved / "modes" / "_writing.md").exists()
    assert (resolved / "modes" / "heuristics" / "recruiter-side.md").exists()

    # Optional files in career_ops are injected into prompt when present
    co = tmp_path / "co"
    (co / "modes" / "heuristics").mkdir(parents=True)
    (co / "config").mkdir()
    (co / "modes" / "oferta.md").write_text("oferta body")
    (co / "modes" / "_shared.md").write_text("shared body")
    (co / "modes" / "_writing.md").write_text("NO_EM_DASH_RULE")
    (co / "modes" / "heuristics" / "recruiter-side.md").write_text("SIX_SECOND_CLARITY_GATE")
    (co / "voice-dna.md").write_text("VOICE_DNA_MARKER")
    (co / "cv.md").write_text("# Jane Smith\nPython, SQL")
    ctx = intel.load_context(co)
    assert "NO_EM_DASH_RULE" in ctx.prompt
    assert "SIX_SECOND_CLARITY_GATE" in ctx.prompt
    assert "VOICE_DNA_MARKER" in ctx.prompt


def test_dynamic_candidate_name_in_exports(tmp_path):
    custom_output = FULL_PACKAGE_OUTPUT.replace("# Alex Sun", "# Jane Doe")
    folder = tmp_path / "pkg"
    job = {
        "dedup_key": "abc12345",
        "company": "Databricks",
        "title": "Data Engineer",
        "location": "San Francisco, CA",
        "apply_url": "https://databricks.com/jobs/1",
        "priority_score": 25.0,
        "status": "QUEUED",
    }
    summary = intel.parse_summary(custom_output)
    info = intel.write_application_package(job, folder, custom_output, summary=summary)
    exports = folder / "exports"
    assert list(exports.glob("Jane_Doe_Resume_Databricks_Data_Engineer.docx"))
    assert list(exports.glob("Jane_Doe_Resume_Databricks_Data_Engineer.pdf"))
    assert list(exports.glob("Jane_Doe_Cover_Letter_Databricks_Data_Engineer.docx"))
    assert list(exports.glob("Jane_Doe_Cover_Letter_Databricks_Data_Engineer.pdf"))
    assert Path(info["tailored_resume_path"]).exists()


def test_onboard_seed_import_and_cli_commands(tmp_path, monkeypatch):
    import shutil
    from typer.testing import CliRunner
    from cockpit import onboard
    from cockpit.cli import app as cli_app
    from cockpit.exporters import render_docx, render_pdf

    # Create isolated repo structure in tmp_path
    fake_repo = tmp_path / "repo"
    shutil.copytree(intel.REPO_ROOT / "config" / "templates", fake_repo / "config" / "templates")
    shutil.copytree(intel.REPO_ROOT / "config" / "career_ops", fake_repo / "config" / "career_ops")
    shutil.copy2(intel.REPO_ROOT / "pipeline_config.example.json", fake_repo / "pipeline_config.example.json")

    seeded = onboard.seed_user_layer(fake_repo)
    assert (fake_repo / "config" / "career_ops" / "cv.md").exists()
    assert (fake_repo / "config" / "career_ops" / "config" / "profile.yml").exists()
    assert (fake_repo / "config" / "evidence" / "01_master_career_history.yaml").exists()
    assert len(seeded) >= 6

    # Test importing a .docx and .pdf resume
    sample_res_dir = tmp_path / "my_resumes"
    sample_res_dir.mkdir()
    sample_md = "# Jordan Rivera\nSeattle, WA | jordan@rivera.dev | +1-206-555-0199 | github.com/jrivera\n\n## Experience\n- Built distributed Rust and Python services."
    docx_file = sample_res_dir / "jordan_resume.docx"
    pdf_file = sample_res_dir / "jordan_resume.pdf"
    render_docx(sample_md, docx_file, title="Jordan Rivera Resume")
    render_pdf(sample_md, pdf_file, title="Jordan Rivera Resume")

    assert "Jordan Rivera" in onboard.extract_resume_text(docx_file)
    assert "Jordan Rivera" in onboard.extract_resume_text(pdf_file)

    import_info = onboard.import_user_resumes(docx_file, repo_root=fake_repo)
    assert import_info["heuristics"]["name"] == "Jordan Rivera"
    assert import_info["heuristics"]["email"] == "jordan@rivera.dev"
    assert (fake_repo / "resumes" / "swe_backend_v1.pdf").exists()
    assert (fake_repo / "resumes" / "data_eng_lakehouse_v1.pdf").exists()
    assert (fake_repo / "resumes" / "analytics_msba_v1.pdf").exists()

    cfg_paths = onboard.write_user_profile_configs(
        repo_root=fake_repo,
        name="Jordan Rivera",
        email="jordan@rivera.dev",
        location="Seattle, WA",
        primary_roles=["Backend Engineer"],
        primary_locations=["seattle", "remote"],
    )
    assert "Jordan Rivera" in cfg_paths["profile_yml"].read_text("utf-8")
    assert "Jordan Rivera" in cfg_paths["local_rules_toml"].read_text("utf-8")

    # Verify load_rules merges scoring_rules.local.toml
    merged_rules = load_rules(
        path=intel.REPO_ROOT / "config" / "scoring_rules.toml",
        local_path=cfg_paths["local_rules_toml"],
    )
    assert merged_rules["outreach"]["candidate_name"] == "Jordan Rivera"
    outreach_msg = render_outreach(
        {"company": "Stripe", "title": "Backend Engineer", "resume_archetype": "swe_backend"},
        merged_rules,
    )
    assert "Jordan Rivera" in outreach_msg

    # Verify CLI doctor command
    runner = CliRunner()
    res_doc = runner.invoke(cli_app, ["doctor"])
    assert res_doc.exit_code == 0
    assert "Career Pipeline & Cockpit Healthcheck" in res_doc.stdout


def test_macos_helpers_survive_missing_binaries_on_linux(monkeypatch):
    def raise_fnf(*args, **kwargs):
        raise FileNotFoundError("No such file or directory")

    monkeypatch.setattr(macos.subprocess, "run", raise_fnf)
    monkeypatch.setenv("GEMINI_API_KEY", "test-linux-key")
    assert macos.gemini_api_key() == "test-linux-key"
    macos.copy_to_clipboard("hello")
    macos.open_url("https://example.com")
    macos.open_file("/tmp/test.txt")


def test_default_results_dir_prefers_max_csv_count_then_latest_date(tmp_path, monkeypatch):
    from cockpit import cli
    fake_root = tmp_path / "repo"
    results = fake_root / "jobspy" / "scraped jobs" / "results"
    monkeypatch.setattr(cli, "REPO_ROOT", fake_root)

    # Empty results
    res = cli._default_results_dir()
    assert res is not None

    # Setup 2026_10 with multiple days:
    # 2026_10_26 has 4 CSVs
    # 2026_10_27 has 2 CSVs (in-flight run)
    # 2026_10_25 has 4 CSVs
    d25 = results / "2026_10" / "2026_10_25"
    d26 = results / "2026_10" / "2026_10_26"
    d27 = results / "2026_10" / "2026_10_27"
    for d in (d25, d26, d27):
        d.mkdir(parents=True, exist_ok=True)

    for i in range(4):
        (d25 / f"seg_{i}_jobs.csv").write_text("a,b\n1,2")
        (d26 / f"seg_{i}_jobs.csv").write_text("a,b\n1,2")
    for i in range(2):
        (d27 / f"seg_{i}_jobs.csv").write_text("a,b\n1,2")

    # Should select d26 (tied with d25 on 4 CSVs, but d26 is newer date than d25; beats d27 which only has 2)
    selected = cli._default_results_dir()
    assert selected == d26

    # When d27 finishes and gets 5 CSVs, it becomes the top candidate
    for i in range(2, 5):
        (d27 / f"seg_{i}_jobs.csv").write_text("a,b\n1,2")
    selected_after_finish = cli._default_results_dir()
    assert selected_after_finish == d27


def test_expand_inputs_supports_date_and_directories(tmp_path, monkeypatch):
    from cockpit import cli
    fake_root = tmp_path / "repo"
    results = fake_root / "jobspy" / "scraped jobs" / "results"
    monkeypatch.setattr(cli, "REPO_ROOT", fake_root)

    d27 = results / "2026_10" / "2026_10_27"
    d27.mkdir(parents=True, exist_ok=True)
    f1 = d27 / "ats_direct_jobs.csv"
    f2 = d27 / "tech_portals_jobs.csv"
    f1.write_text("id,title\n1,Software Engineer")
    f2.write_text("id,title\n2,Data Engineer")

    # Resolve by --date "10/27"
    res_date = cli._expand_inputs(None, date_str="10/27")
    assert res_date == [f1, f2]

    # Resolve by positional argument "10/27"
    res_pos = cli._expand_inputs([Path("10/27")])
    assert res_pos == [f1, f2]

    # Resolve by explicit directory
    res_dir = cli._expand_inputs([d27])
    assert res_dir == [f1, f2]

    # Resolve by YYYY_MM_DD
    res_full = cli._expand_inputs(None, date_str="2026_10_27")
    assert res_full == [f1, f2]


def test_list_candidate_result_dirs_and_select(tmp_path, monkeypatch):
    from cockpit import cli
    fake_root = tmp_path / "repo"
    results = fake_root / "jobspy" / "scraped jobs" / "results"
    monkeypatch.setattr(cli, "REPO_ROOT", fake_root)

    # Populate two dates: 2026_10_21 with 3 CSVs and 2026_10_27 with 2 CSVs
    d21 = results / "2026_10" / "2026_10_21"
    d27 = results / "2026_10" / "2026_10_27"
    for d in (d21, d27):
        d.mkdir(parents=True, exist_ok=True)

    for i in range(3):
        (d21 / f"seg_{i}_jobs.csv").write_text("id\n1")
    for i in range(2):
        (d27 / f"seg_{i}_jobs.csv").write_text("id\n2")

    candidates = cli._list_candidate_result_dirs()
    # Sorted by date descending
    assert [c[0] for c in candidates] == ["2026_10_27", "2026_10_21"]
    assert [c[1] for c in candidates] == [2, 3]

    # Test fuzzy scoring logic
    assert cli._fuzzy_score("10/27", "2026_10_27 2 csv") > 0
    assert cli._fuzzy_score("3 csv", "2026_10_21 3 csv") > 0

    # Mock selection via --select
    monkeypatch.setattr(cli, "_interactive_fuzzy_select_dir", lambda: d21)
    selected_files = cli._expand_inputs(None, select=True)
    assert len(selected_files) == 3
    assert selected_files == sorted(d21.glob("*_jobs.csv"))






