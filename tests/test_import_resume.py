from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from cockpit import intel, onboard
from cockpit.cli import app as cli_app
from cockpit.exporters import render_docx, render_pdf


def test_import_user_resumes_full_pipeline(tmp_path, monkeypatch):
    runner = CliRunner()

    # 1. Build an isolated fake repo
    fake_repo = tmp_path / "repo"
    shutil.copytree(intel.REPO_ROOT / "config" / "templates", fake_repo / "config" / "templates")
    shutil.copytree(intel.REPO_ROOT / "config" / "career_ops", fake_repo / "config" / "career_ops")
    shutil.copy2(intel.REPO_ROOT / "pipeline_config.example.json", fake_repo / "pipeline_config.example.json")
    (fake_repo / "resumes").mkdir(parents=True, exist_ok=True)

    # Mock home directory
    fake_home = tmp_path / "home"
    fake_home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(Path, "home", lambda: fake_home)

    # 2. Create sample resume PDF
    sample_dir = tmp_path / "sources"
    sample_dir.mkdir(parents=True, exist_ok=True)
    sample_md = """# Taylor Swift
Seattle, WA · (206) 555-0199 · taylor@example.com · github.com/tswift · linkedin.com/in/tswift
Data Analyst Intern | Python · SQL · DuckDB

EDUCATION
University of Washington — Seattle, WA
M.S. in Business Analytics

EXPERIENCE
Acme Analytics — Seattle, WA
Data Analyst Intern (09/2026 – Present)
• Built automated pipelines with Python, DuckDB, and SQL.
"""
    sample_pdf = sample_dir / "Taylor_Swift_Resume.pdf"
    render_pdf(sample_md, sample_pdf, title="Taylor Swift Resume")
    assert sample_pdf.exists()

    # 3. Create dummy DuckDB with a QUEUED job to test database sync
    db_dir = fake_repo / "data"
    db_dir.mkdir(parents=True, exist_ok=True)
    db_path = db_dir / "job_funnel.duckdb"
    import duckdb

    with duckdb.connect(str(db_path)) as con:
        con.execute(
            """
            CREATE TABLE jobs_master (
                dedup_key VARCHAR PRIMARY KEY,
                status VARCHAR,
                resume_archetype VARCHAR,
                resume_path VARCHAR
            );
            INSERT INTO jobs_master VALUES ('test_key_1', 'QUEUED', 'analytics_msba', 'old/path/resume.pdf');
            """
        )

    # 4. Run import_user_resumes
    res = onboard.import_user_resumes(
        sample_pdf,
        repo_root=fake_repo,
        sync_home=True,
        update_duckdb=True,
        configure_rules=True,
    )

    assert "Taylor Swift" in res["heuristics"]["name"]
    assert res["heuristics"]["email"] == "taylor@example.com"

    # Verify binary staged in resumes/
    staged_pdf = fake_repo / "resumes" / "Taylor_Swift_Resume.pdf"
    assert staged_pdf.exists()
    assert staged_pdf.read_bytes() == sample_pdf.read_bytes()

    # Verify archetype aliases staged and matching authentic binary
    for alias in ["swe_backend_v1.pdf", "data_eng_lakehouse_v1.pdf", "analytics_msba_v1.pdf"]:
        alias_path = fake_repo / "resumes" / alias
        assert alias_path.exists()
        assert alias_path.read_bytes() == sample_pdf.read_bytes()

    # Verify home directory sync
    assert (fake_home / "resumes" / "Taylor_Swift_Resume.pdf").exists()
    assert (fake_home / "resumes" / "swe_backend_v1.pdf").exists()

    # Verify cv.md & profile.yml
    cv_file = fake_repo / "config" / "career_ops" / "cv.md"
    assert cv_file.exists()
    assert "Taylor Swift" in cv_file.read_text("utf-8")

    profile_file = fake_repo / "config" / "career_ops" / "config" / "profile.yml"
    assert profile_file.exists()
    assert "Taylor Swift" in profile_file.read_text("utf-8")

    # Verify scoring_rules.local.toml
    local_rules = fake_repo / "config" / "scoring_rules.local.toml"
    assert local_rules.exists()
    rules_text = local_rules.read_text("utf-8")
    assert "[resumes]" in rules_text
    assert 'analytics_msba = "resumes/Taylor_Swift_Resume.pdf"' in rules_text

    # Verify DuckDB was updated
    with duckdb.connect(str(db_path)) as con:
        row = con.execute("SELECT resume_path FROM jobs_master WHERE dedup_key = 'test_key_1'").fetchone()
        assert str(staged_pdf) in row[0]

    # 5. Test CLI command `cockpit import-resume`
    cli_result = runner.invoke(
        cli_app,
        ["import-resume", str(sample_pdf), "--name", "Taylor A. Swift"],
        env={"COCKPIT_REPO_ROOT": str(fake_repo)},
    )
    assert cli_result.exit_code == 0
    assert "Taylor A. Swift" in (fake_repo / "config" / "career_ops" / "config" / "profile.yml").read_text("utf-8")

    # 6. Test importing a .docx resume
    docx_file = sample_dir / "Taylor_Swift_Resume.docx"
    render_docx(sample_md, docx_file, title="Taylor Swift Resume")
    res_docx = onboard.import_user_resumes(
        docx_file,
        repo_root=fake_repo,
        sync_home=False,
        update_duckdb=False,
    )
    assert (fake_repo / "resumes" / "Taylor_Swift_Resume.docx").exists()
    assert res_docx["primary_resume"].endswith("Taylor_Swift_Resume.docx")
