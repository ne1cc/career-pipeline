"""Dossier navigation: grouped index, per-job notes, learning log, and the CLI entry points."""
from __future__ import annotations

from typer.testing import CliRunner

from cockpit import cli, db, intel, macos
from cockpit.models import FunnelState as S
from tests.test_cockpit_pipeline import db_path, frame  # noqa: F401  (fixtures)


def _saved_jobs(con, root, *companies: str) -> dict[str, str]:
    """Seed one saved job per company, each with a folder holding a dossier file."""
    db.upsert_jobspy_frame(con, frame(*({"company": c, "job_url": f"https://l/{c}"} for c in companies)))
    keys = dict(con.execute("SELECT company, dedup_key FROM jobs_master").fetchall())
    for company, key in keys.items():
        folder = root / f"{company} - Engineer"
        folder.mkdir(parents=True)
        (folder / "05_interview_dossier.md").write_text(f"# {company}\n", "utf-8")
        db.toggle_saved(con, key, intel_path=str(folder / "05_interview_dossier.md"), folder_path=str(folder))
    return keys


def test_index_groups_jobs_by_stage_and_links_their_files(db_path, tmp_path):
    with db.session(db_path) as con:
        k = _saved_jobs(con, tmp_path, "Fresh", "Sent", "Talking", "Gone")
        db.transition_job_state(con, k["Sent"], S.APPLIED, "test")
        db.transition_job_state(con, k["Talking"], S.PHONE_SCREEN, "test")
        db.transition_job_state(con, k["Gone"], S.REJECTED, "test")
        intel.rebuild_index(con, tmp_path)
    text = (tmp_path / "index.md").read_text("utf-8")
    order = [text.index(h) for h in ("## Interviewing (1)", "## Applied (1)", "## To apply (1)", "## Closed (1)")]
    assert order == sorted(order)
    section = text[text.index("## Applied"):text.index("## To apply")]
    assert "| Sent |" in section and "Fresh" not in section
    assert "[Dossier](<Sent - Engineer/05_interview_dossier.md>)" in section
    assert "[Notes](<Sent - Engineer/09_notes.md>)" in section
    assert "[Fit]" not in section  # only files that exist are linked


def test_notes_survive_regeneration_and_feed_the_learning_log(db_path, tmp_path):
    with db.session(db_path) as con:
        _saved_jobs(con, tmp_path, "Acme", "Blank")
        intel.rebuild_index(con, tmp_path)
        notes = tmp_path / "Acme - Engineer" / "09_notes.md"
        notes.write_text(notes.read_text("utf-8") + "\nThey ask about idempotent pipelines.\n", "utf-8")
        intel.rebuild_index(con, tmp_path)  # a second pass must not reset the notes
    assert "idempotent pipelines" in notes.read_text("utf-8")
    assert intel.build_learning_log(tmp_path) == 1  # the untouched template in Blank is not an entry
    log = (tmp_path / "learning-log.md").read_text("utf-8")
    assert "## Acme - Engineer" in log and "idempotent pipelines" in log and "Blank" not in log


def test_cockpit_md_opens_the_index_and_notes_opens_the_log(db_path, tmp_path, monkeypatch):
    opened: list[tuple[str, str | None]] = []
    monkeypatch.setattr(macos, "open_file", lambda path, app=None: opened.append((str(path), app)))
    monkeypatch.setattr(macos, "open_markdown", lambda path, background=True: opened.append((str(path), "markdown")))
    runner = CliRunner()
    # No index yet: markdown viewer cannot open a folder, so file manager gets it.
    assert runner.invoke(cli.app, ["md", "--export-dir", str(tmp_path)]).exit_code == 0
    with db.session(db_path) as con:
        _saved_jobs(con, tmp_path, "Acme")
        intel.rebuild_index(con, tmp_path)
    assert runner.invoke(cli.app, ["md", "--export-dir", str(tmp_path)]).exit_code == 0
    assert runner.invoke(cli.app, ["notes", "--export-dir", str(tmp_path)]).exit_code == 0
    assert opened == [
        (str(tmp_path), None),
        (str(tmp_path / "index.md"), "markdown"),
        (str(tmp_path / "learning-log.md"), "markdown"),
    ]


def test_open_markdown_uses_marktext_on_linux_and_falls_back_to_open_file(tmp_path, monkeypatch):
    import importlib

    importlib.reload(macos)  # bypass conftest's autouse no-op for this unit test
    md = tmp_path / "dossier.md"
    md.write_text("# Hi\n", "utf-8")
    launched: list[list[str]] = []
    fallback: list[str] = []

    monkeypatch.setattr("cockpit.macos.sys.platform", "linux")
    monkeypatch.setattr("cockpit.macos.shutil.which", lambda name: "/usr/bin/marktext" if name == "marktext" else None)
    monkeypatch.setattr("cockpit.macos.Path.is_file", lambda self: str(self) == "/usr/bin/marktext")
    monkeypatch.setattr("cockpit.macos.subprocess.Popen", lambda cmd, **kw: launched.append(cmd))
    monkeypatch.setattr(macos, "open_file", lambda p, app=None: fallback.append(str(p)))

    macos.open_markdown(md)
    assert launched == [["/usr/bin/marktext", str(md)]]
    assert fallback == []

    # When marktext is missing, falls back to open_file (xdg-open)
    monkeypatch.setattr("cockpit.macos.shutil.which", lambda name: None)
    monkeypatch.setattr("cockpit.macos.Path.is_file", lambda self: False)
    macos.open_markdown(md)
    assert fallback == [str(md)]
