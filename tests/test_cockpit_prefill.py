import pytest
from pathlib import Path
from cockpit.prefill import (
    detect_ats,
    AtsTarget,
    CandidateProfile,
    read_candidate_profile,
    build_prefill_fields,
    format_prefill_table,
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
