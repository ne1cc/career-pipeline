import pytest
from pathlib import Path
from cockpit.prefill import (
    detect_ats,
    AtsTarget,
    CandidateProfile,
    read_candidate_profile,
    build_prefill_fields,
    format_prefill_table,
    application_files,
)


def test_detect_ats_greenhouse():
    url = "https://boards.greenhouse.io/stripe/jobs/5678901"
    target = detect_ats(url)
    assert target is not None
    assert target.ats == "greenhouse"
    assert target.company_slug == "stripe"
    assert target.job_id == "5678901"


def test_detect_ats_ashby():
    url = "https://jobs.ashbyhq.com/linear/abc-123-def"
    target = detect_ats(url)
    assert target is not None
    assert target.ats == "ashby"
    assert target.company_slug == "linear"
    assert target.job_id == "abc-123-def"


def test_detect_ats_lever():
    url = "https://jobs.lever.co/figma/xyz-987"
    target = detect_ats(url)
    assert target is not None
    assert target.ats == "lever"
    assert target.company_slug == "figma"
    assert target.job_id == "xyz-987"


def test_detect_ats_generic_fallback():
    url = "https://careers.google.com/jobs/results/12345"
    target = detect_ats(url)
    assert target is not None
    assert target.ats == "generic"
    assert target.company_slug == "careers.google.com"


def test_detect_ats_invalid():
    assert detect_ats("not-a-valid-url") is None
    assert detect_ats("") is None


def test_read_candidate_profile(tmp_path: Path):
    cfg_dir = tmp_path / "config" / "career_ops" / "config"
    cfg_dir.mkdir(parents=True)
    profile_yml = cfg_dir / "profile.yml"
    profile_yml.write_text(
        """
full_name: "Jane Developer"
email: "jane@example.com"
phone: "+1 (555) 019-2834"
location: "San Francisco, CA"
linkedin: "https://linkedin.com/in/janedev"
portfolio_url: "https://janedev.com"
github: "https://github.com/janedev"
""",
        "utf-8",
    )

    prof = read_candidate_profile(tmp_path)
    assert prof.first_name == "Jane"
    assert prof.last_name == "Developer"
    assert prof.email == "jane@example.com"
    assert prof.phone == "+1 (555) 019-2834"
    assert prof.linkedin == "https://linkedin.com/in/janedev"


def test_build_prefill_fields_greenhouse(tmp_path: Path):
    prof = CandidateProfile(
        first_name="Jane",
        last_name="Developer",
        email="jane@example.com",
        phone="+15551234567",
        linkedin="https://linkedin.com/in/janedev",
        portfolio_url="https://janedev.com",
    )
    target = AtsTarget(
        ats="greenhouse",
        company_slug="stripe",
        job_id="123",
        original_url="https://boards.greenhouse.io/stripe/jobs/123",
    )

    pdf = tmp_path / "Jane_Developer_Resume.pdf"
    pdf.write_bytes(b"%PDF-1.4 mock")

    fields = build_prefill_fields(target, prof, resume_file=pdf)
    field_dict = dict(fields)

    assert field_dict["first_name"] == "Jane"
    assert field_dict["last_name"] == "Developer"
    assert field_dict["email"] == "jane@example.com"
    assert field_dict["phone"] == "+15551234567"
    assert "Jane_Developer_Resume.pdf" in field_dict["resume"]
    assert field_dict["linkedin_profile"] == "https://linkedin.com/in/janedev"
    assert field_dict["website"] == "https://janedev.com"


def test_format_prefill_table():
    prof = CandidateProfile(
        first_name="Alex",
        last_name="Smith",
        email="alex@test.com",
    )
    target = AtsTarget(
        ats="lever",
        company_slug="acme",
        job_id="456",
        original_url="https://jobs.lever.co/acme/456",
    )
    fields = build_prefill_fields(target, prof)
    rendered = format_prefill_table(target, fields)
    assert "Lever" in rendered
    assert "acme" in rendered
    assert "alex@test.com" in rendered


def test_build_prefill_url():
    from cockpit.prefill import build_prefill_url

    prof = CandidateProfile(
        first_name="Jane",
        last_name="Developer",
        email="jane@example.com",
        phone="+15551234567",
        linkedin="https://linkedin.com/in/janedev",
        portfolio_url="https://janedev.com",
    )

    # 1. Lever: should append /apply and URL-encoded query params
    lever_target = AtsTarget("lever", "figma", "xyz", "https://jobs.lever.co/figma/xyz")
    lever_url = build_prefill_url(lever_target, prof)
    assert "jobs.lever.co/figma/xyz/apply?" in lever_url
    assert "name=Jane+Developer" in lever_url
    assert "email=jane%40example.com" in lever_url
    assert "urls%5BLinkedIn%5D=https%3A%2F%2Flinkedin.com%2Fin%2Fjanedev" in lever_url

    # 2. Greenhouse
    gh_target = AtsTarget("greenhouse", "stripe", "123", "https://boards.greenhouse.io/stripe/jobs/123")
    gh_url = build_prefill_url(gh_target, prof)
    assert "first_name=Jane" in gh_url
    assert "last_name=Developer" in gh_url
    assert "email=jane%40example.com" in gh_url

    # 3. Ashby
    ashby_target = AtsTarget("ashby", "linear", "456", "https://jobs.ashbyhq.com/linear/456")
    ashby_url = build_prefill_url(ashby_target, prof)
    assert "firstName=Jane" in ashby_url
    assert "lastName=Developer" in ashby_url


def test_read_candidate_profile_nested(tmp_path: Path):
    cfg_dir = tmp_path / "config" / "career_ops" / "config"
    cfg_dir.mkdir(parents=True)
    (cfg_dir / "profile.yml").write_text(
        """
candidate:
  full_name: "Jane Developer"
  email: "jane@example.com"
  phone: "+1 (555) 019-2834"
  location: "Springfield, ST"
  linkedin: "https://linkedin.com/in/janedev"
  portfolio_url: "https://janedev.com"
  github: "https://github.com/janedev"
location:
  country: "Exampleland"
  city: "Elsewhere"
  authorized_in: ["Exampleland"]
""",
        "utf-8",
    )

    prof = read_candidate_profile(tmp_path)
    assert prof.full_name == "Jane Developer"
    assert prof.email == "jane@example.com"
    assert prof.phone == "+1 (555) 019-2834"
    assert prof.github == "https://github.com/janedev"
    assert prof.portfolio_url == "https://janedev.com"
    assert prof.location == "Springfield, ST"


def test_read_candidate_profile_evidence_fallback(tmp_path: Path):
    cfg_dir = tmp_path / "config" / "career_ops" / "config"
    cfg_dir.mkdir(parents=True)
    (cfg_dir / "profile.yml").write_text("target_roles:\n  primary: [Dev]\n", "utf-8")
    ev_dir = tmp_path / "config" / "evidence"
    ev_dir.mkdir(parents=True)
    (ev_dir / "01_master_career_history.yaml").write_text(
        'candidate:\n  full_name: "Jane Developer"\n  email: "jane@example.com"\n', "utf-8"
    )
    prof = read_candidate_profile(tmp_path)
    assert prof.full_name == "Jane Developer"
    assert prof.email == "jane@example.com"


def _touch(*paths: Path) -> None:
    for p in paths:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("x", "utf-8")


def test_application_files_picks_resume_not_cover(tmp_path: Path):
    base = tmp_path / "base.pdf"
    ex = tmp_path / "job" / "exports"
    _touch(base, ex / "Jane_Cover_Letter_Acme_Dev.pdf", ex / "Jane_Resume_Acme_Dev.pdf")
    files = application_files({"resume_path": str(base)}, tmp_path / "job")
    assert files.resume == ex / "Jane_Resume_Acme_Dev.pdf"
    assert files.tailored is True
    assert files.cover_pdf == ex / "Jane_Cover_Letter_Acme_Dev.pdf"
    assert files.cover_text is None


def test_application_files_tailored_path_wins_and_cover_text(tmp_path: Path):
    tailored = tmp_path / "tailored.pdf"
    folder = tmp_path / "job"
    _touch(tailored, folder / "exports" / "Jane_Resume_Acme_Dev.pdf", folder / "03_cover_letter.md")
    files = application_files({"tailored_resume_path": str(tailored), "resume_path": "nope.pdf"}, folder)
    assert files.resume == tailored
    assert files.tailored is True
    assert files.cover_text == folder / "03_cover_letter.md"


def test_application_files_falls_back_to_base_resume(tmp_path: Path):
    base = tmp_path / "base.pdf"
    _touch(base)
    for folder in (None, tmp_path / "missing"):
        files = application_files({"resume_path": str(base)}, folder)
        assert files.resume == base
        assert files.tailored is False
        assert files.cover_pdf is None and files.cover_text is None
