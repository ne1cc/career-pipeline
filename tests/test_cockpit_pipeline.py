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
    assert junior.score - senior.score == 15.0 + 45.0
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


def test_tmux_cache_updates_on_transition(db_path):
    with db.session(db_path) as con:
        db.upsert_jobspy_frame(con, frame({}))
        assert db.TMUX_CACHE.read_text() == "#[fg=green]Jobs: 0/15#[default] | Q:1 | H:0\n"
        key = con.execute("SELECT dedup_key FROM jobs_master").fetchone()[0]
        db.transition_job_state(con, key, FunnelState.APPLIED, "test")
    assert db.TMUX_CACHE.read_text() == "#[fg=green]Jobs: 1/15#[default] | Q:0 | H:0\n"


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
    xml = render_plist(Path("/Users/alex/Github/ne1cc/Jobspy-Custom"), "/Users/alex/.local/bin/uv")
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
    assert ctx.model == "gemini-3.5-flash-lite"
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
    assert (fit, status) == (4.2, "done") and Path(path).parent == out
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
    assert len(fake.calls) == 1 and list((tmp_path / "dossiers").glob("Software Engineer I - * - Seattle, WA - *.md"))


def test_dossier_path_format(tmp_path):
    job = {
        "title": "Senior Data / Platform Engineer",
        "company": "Acme: Corp",
        "location": "Seattle, WA",
        "saved_at": "2026-10-19 14:30:00",
    }
    p = intel.dossier_path(job, tmp_path)
    assert p.name == "Senior Data Platform Engineer - Acme Corp - Seattle, WA - 2026-10-19.md"


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
        assert row[3] == str(dossier_file)

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


