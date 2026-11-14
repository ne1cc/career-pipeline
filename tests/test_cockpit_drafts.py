# ruff: noqa: F811  (pytest fixtures imported from the pipeline tests)
"""Drafter, dossier queueing, concurrent dossier batch, and the dossier-first `A` flow."""

import asyncio
import threading
import time

from cockpit import db, drafts, intel
from cockpit import autofill
from cockpit.tui import JobCockpitApp
from tests.test_cockpit_autofill_wiring import FakeWorker, GREENHOUSE, _app, _notes, _repo, _seed
from tests.test_cockpit_pipeline import FakeGemini, career_ops, db_path, frame  # noqa: F401


def _key(db_path):
    with db.session(db_path) as con:
        return con.execute("SELECT dedup_key FROM jobs_master").fetchone()[0]


# ── drafter ──

def test_clean_handles_skip_fences_markdown_and_length():
    assert drafts.clean("SKIP") is None and drafts.clean("") is None and drafts.clean("skip: no facts") is None
    assert drafts.clean("```\n**Hi** there\n```") == "Hi there"
    long = ("Sentence one. " * 200).strip()
    out = drafts.clean(long)
    assert len(out) <= drafts.MAX_CHARS and out.endswith(".")


def test_drafter_sends_documents_and_swallows_errors():
    seen = []
    ok = drafts.make_drafter(lambda p, t: seen.append((p, t)) or "Because I like it.",
                             company="Acme", title="Eng", jd="JD text", dossier="DOSSIER", resume="RESUME")
    assert ok("Why Acme?") == "Because I like it."
    prompt, body = seen[0]
    assert "ONLY facts" in prompt and "RESUME" in body and "DOSSIER" in body and "Why Acme?" in body

    def boom(p, t):
        raise RuntimeError("cli down")

    bad = drafts.make_drafter(boom, company="", title="", jd="", dossier="", resume="")
    assert bad("Q?") is None


# ── queue helpers ──

def test_queue_intel_stars_and_is_idempotent(db_path):
    _seed(db_path, dossier=None)
    key = _key(db_path)
    with db.session(db_path) as con:
        assert db.queue_intel(con, key) == "pending"
        assert db.queue_intel(con, key) == "pending"
        assert con.execute("SELECT is_saved FROM jobs_master").fetchone()[0] is True
        con.execute("UPDATE jobs_master SET intel_status = 'failed'")
        assert db.queue_intel(con, key) == "pending"
        con.execute("UPDATE jobs_master SET intel_status = 'done'")
        assert db.queue_intel(con, key) == "done"
        assert db.get_job(con, key)["intel_status"] == "done"
        assert db.get_job(con, "nope") is None


# ── concurrent batch ──

def test_process_batch_runs_jobs_side_by_side(db_path, career_ops, tmp_path):
    ctx = intel.load_context(career_ops)
    with db.session(db_path) as con:
        db.upsert_jobspy_frame(con, frame({}))
        key = _key(db_path)
    for n in range(3):  # three saved, pending jobs
        with db.session(db_path) as con:
            if n:
                con.execute("INSERT INTO jobs_master SELECT * REPLACE (? AS dedup_key) FROM jobs_master LIMIT 1", [f"k{n}"])
    with db.session(db_path) as con:
        con.execute("UPDATE jobs_master SET is_saved = TRUE, intel_status = 'pending'")
    base = FakeGemini()
    live, peak, lock = [0], [0], threading.Lock()

    def gen(prompt, text):
        with lock:
            live[0] += 1
            peak[0] = max(peak[0], live[0])
        time.sleep(0.2)
        with lock:
            live[0] -= 1
        return base(prompt, text)

    done = intel.process_batch(db_path, ctx, gen, tmp_path / "out", workers=3)
    assert len(done) == 3 and peak[0] >= 2


# ── dossier-first A ──

def test_a_without_dossier_queues_then_opens_when_done(db_path, tmp_path, monkeypatch):
    monkeypatch.setattr(JobCockpitApp, "_kick_intel", lambda self: None)  # never start a real model
    _seed(db_path, dossier=None)
    key = _key(db_path)

    async def drive():
        app = _app(db_path, tmp_path, _repo(tmp_path))
        async with app.run_test() as pilot:
            await pilot.press("A")
            await pilot.pause()
            assert FakeWorker.instances == [] and key in app._autofill_waiting
            assert any("Dossier queued: Acme" in m for m in _notes(app))
            with db.session(db_path) as con:
                assert db.get_job(con, key)["intel_status"] == "pending"
                con.execute("UPDATE jobs_master SET intel_status = 'done'")
            await asyncio.to_thread(app._poll_dossiers_worker)
            await pilot.pause()
            (call,) = FakeWorker.instances[0].calls
            assert call["url"] == GREENHOUSE and key not in app._autofill_waiting

    asyncio.run(drive())


def test_failed_dossier_warns_and_opens_nothing(db_path, tmp_path, monkeypatch):
    monkeypatch.setattr(JobCockpitApp, "_kick_intel", lambda self: None)
    _seed(db_path, dossier=None)

    async def drive():
        app = _app(db_path, tmp_path, _repo(tmp_path))
        async with app.run_test() as pilot:
            await pilot.press("A")
            await pilot.pause()
            with db.session(db_path) as con:
                con.execute("UPDATE jobs_master SET intel_status = 'failed'")
            await asyncio.to_thread(app._poll_dossiers_worker)
            await pilot.pause()
            assert FakeWorker.instances == [] and not app._autofill_waiting
            assert any("dossier failed" in m for m in _notes(app))

    asyncio.run(drive())


def test_done_dossier_passes_a_drafter(db_path, tmp_path):
    dossier = tmp_path / "05_interview_dossier.md"
    dossier.write_text("fit notes")
    _seed(db_path, intel_path=str(dossier))

    async def drive():
        app = _app(db_path, tmp_path, _repo(tmp_path))
        app.generate = lambda p, t: "A draft."
        async with app.run_test() as pilot:
            await pilot.press("A")
            await pilot.pause()
            drafter = FakeWorker.instances[0].calls[0]["drafter"]
            assert drafter is not None and await asyncio.to_thread(drafter, "Why us?") == "A draft."

    asyncio.run(drive())
