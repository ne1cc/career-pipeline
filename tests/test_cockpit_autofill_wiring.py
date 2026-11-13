# ruff: noqa: F811  (pytest fixtures imported from the pipeline tests)
"""Wiring of autofill into the cockpit: the `A` key and `cockpit autofill`, with a fake browser worker."""

import asyncio
from typing import ClassVar

from typer.testing import CliRunner

from cockpit import autofill, cli, db, macos
from cockpit.tui import JobCockpitApp
from tests.test_cockpit_pipeline import db_path, frame  # noqa: F401

GREENHOUSE = "https://boards.greenhouse.io/acme/jobs/123"
PROFILE = "candidate:\n  full_name: Pat Example\n  email: pat@example.test\n  phone: '555-0100'\n"
ANSWERS = "- match: authorized to work\n  answer: Yes\n"


class FakeWorker:
    """Records submit() calls; the test fires on_done itself."""

    instances: ClassVar[list["FakeWorker"]] = []

    def __init__(self, profile_dir, headless=False):
        self.profile_dir, self.calls, self.closed = profile_dir, [], 0
        FakeWorker.instances.append(self)

    def submit(self, url, profile, resume, cover_pdf, cover_text, answers, on_done):
        self.calls.append({"url": url, "profile": profile, "resume": resume, "cover_pdf": cover_pdf,
                           "cover_text": cover_text, "answers": answers, "on_done": on_done})

    def close(self):
        self.closed += 1


def _repo(tmp_path, answers=True):
    cfg = tmp_path / "repo" / "config" / "career_ops" / "config"
    cfg.mkdir(parents=True)
    (cfg / "profile.yml").write_text(PROFILE)
    if answers:
        (cfg / "answers.yml").write_text(ANSWERS)
    return tmp_path / "repo"


def _app(db_path, tmp_path, repo):
    FakeWorker.instances = []
    return JobCockpitApp(db_path, export_dir=tmp_path / "dossiers", repo_root=repo,
                         autofill_worker=lambda d: FakeWorker(d))


def _seed(db_path, url=GREENHOUSE, company="Acme"):
    with db.session(db_path) as con:
        db.upsert_jobspy_frame(con, frame({"company": company, "job_url_direct": url, "job_url": url}))


def _notes(app):
    return [n.message for n in app._notifications]


def test_a_on_greenhouse_submits_once_with_profile_resume_answers(db_path, tmp_path):
    _seed(db_path)
    resume = tmp_path / "base.pdf"
    resume.write_bytes(b"%PDF")

    async def drive():
        app = _app(db_path, tmp_path, _repo(tmp_path))
        async with app.run_test() as pilot:
            app.rows[0]["resume_path"] = str(resume)
            await pilot.press("A")
            await pilot.pause()
            (w,) = FakeWorker.instances
            (call,) = w.calls
            assert call["url"] == GREENHOUSE
            assert call["profile"].email == "pat@example.test"
            assert call["resume"] == resume
            assert [r.answer for r in call["answers"]] == ["Yes"]
            assert call["cover_text"] is None
            assert any("Autofill queued: Acme" in m and "base resume" in m for m in _notes(app))

    asyncio.run(drive())


def test_tailored_files_and_cover_text_string(db_path, tmp_path):
    _seed(db_path)
    folder = tmp_path / "folder"
    (folder / "exports").mkdir(parents=True)
    tailored = folder / "exports" / "Pat_Resume.pdf"
    tailored.write_bytes(b"%PDF")
    cover_pdf = folder / "exports" / "Pat_Cover_Letter.pdf"
    cover_pdf.write_bytes(b"%PDF")
    (folder / "03_cover_letter.md").write_text("Dear team")

    async def drive():
        app = _app(db_path, tmp_path, _repo(tmp_path))
        async with app.run_test() as pilot:
            app.rows[0]["folder_path"] = str(folder)
            await pilot.press("A")
            await pilot.pause()
            call = FakeWorker.instances[0].calls[0]
            assert call["resume"] == tailored and call["cover_pdf"] == cover_pdf
            assert call["cover_text"] == "Dear team"
            assert any("tailored resume" in m for m in _notes(app))

    asyncio.run(drive())


def test_a_on_unsupported_site_opens_normally(db_path, tmp_path, monkeypatch):
    opened = []
    monkeypatch.setattr(macos, "open_url", lambda url, **kw: opened.append(url))
    url = "https://www.linkedin.com/jobs/view/1"
    _seed(db_path, url)

    async def drive():
        app = _app(db_path, tmp_path, _repo(tmp_path))
        async with app.run_test() as pilot:
            await pilot.press("A")
            await pilot.pause()
            assert FakeWorker.instances == []
            assert opened == [url]
            assert any("No autofill for this site" in m for m in _notes(app))

    asyncio.run(drive())


def test_on_done_notifications(db_path, tmp_path):
    _seed(db_path)

    async def drive():
        app = _app(db_path, tmp_path, _repo(tmp_path))
        async with app.run_test() as pilot:
            await pilot.press("A")
            await pilot.pause()
            done = FakeWorker.instances[0].calls[0]["on_done"]
            # Fire from a real thread, as the browser worker does.
            for report in (autofill.FillReport("greenhouse", GREENHOUSE, filled=["Email"]),
                           autofill.FillReport("greenhouse", GREENHOUSE, blocked="captcha")):
                await asyncio.to_thread(done, report)
                await pilot.pause()
            notes = {n.message: n.severity for n in app._notifications}
            assert notes["Acme: Filled 1 fields"] == "information"
            assert notes["Acme: stopped at a CAPTCHA, solve it in the browser"] == "warning"
        done(autofill.FillReport("greenhouse", GREENHOUSE, blocked="login"))  # after exit: no raise

    asyncio.run(drive())


def test_missing_answers_hint_once_and_worker_closed_on_exit(db_path, tmp_path):
    _seed(db_path)

    async def drive():
        app = _app(db_path, tmp_path, _repo(tmp_path, answers=False))
        async with app.run_test() as pilot:
            await pilot.press("A")
            await pilot.press("A")
            await pilot.pause()
            assert sum("answers.yml" in m for m in _notes(app)) == 1
            assert len(FakeWorker.instances[0].calls) == 2
            assert FakeWorker.instances[0].calls[0]["answers"] == []
        assert FakeWorker.instances[0].closed == 1

    asyncio.run(drive())


def test_autofill_binding_resolves_without_browser(db_path, tmp_path):
    async def drive():
        app = _app(db_path, tmp_path, _repo(tmp_path))
        async with app.run_test():
            for key in ("A", "shift+a"):
                binding = app._bindings.key_to_bindings[key][0]
                assert binding.action == "autofill"
            assert hasattr(app, "action_autofill")
            assert app.check_action("autofill", ()) is True
        assert FakeWorker.instances == []  # lazy: nothing built until a job is filled

    asyncio.run(drive())


def test_cli_autofill_prints_report_and_closes_worker(tmp_path, monkeypatch):
    class CliWorker(FakeWorker):
        def submit(self, *args):
            super().submit(*args)
            args[-1](autofill.FillReport("greenhouse", GREENHOUSE, filled=["Email"],
                                         needs_you=["Why us?"], rules_fired=[("authorized", "Yes")],
                                         resume="/r/Pat_Resume.pdf"))

    FakeWorker.instances = []
    monkeypatch.setattr(autofill, "AutofillWorker", CliWorker)
    monkeypatch.setattr("cockpit.tui.AUTOFILL_PROFILE_DIR", tmp_path / "profile")
    cover = tmp_path / "cover.md"
    cover.write_text("Dear team")

    result = CliRunner().invoke(cli.app, ["autofill", GREENHOUSE, "--cover", str(cover)], input="\n")

    assert result.exit_code == 0, result.output
    assert "Filled 1 fields" in result.output and "Why us?" in result.output
    assert "authorized -> Yes" in result.output and "Pat_Resume.pdf" in result.output
    assert "Review and submit in the browser" in result.output
    (w,) = FakeWorker.instances
    assert w.calls[0]["cover_text"] == "Dear team" and w.closed == 1
