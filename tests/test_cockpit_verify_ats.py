import pytest
from pathlib import Path
from cockpit.verify_ats import (
    audit_resume_text,
    audit_resume_file,
    check_keyword_coverage,
    AtsAuditResult,
)


CLEAN_RESUME = """# Jane Developer
San Francisco, CA | (555) 019-2834 | jane.developer@example.com | linkedin.com/in/janedev

## Professional Summary
Senior Software Engineer with 7 years of experience building distributed systems, data pipelines,
and high-throughput backend APIs in Python, Go, and SQL.

## Work Experience

### Senior Backend Engineer | Acme Corp | 2021 - Present
- Designed and deployed real-time data streaming pipelines using DuckDB and FastAPI, reducing query latency by 45%.
- Led architectural migration of microservices architecture to Kubernetes and AWS, managing 100M+ events daily.
- Implemented robust CI/CD and automated regression test suites achieving 95% branch coverage.

### Software Engineer | Beta Systems | 2018 - 2021
- Developed RESTful microservices using Python and PostgreSQL serving 500k monthly active users.
- Optimized slow SQL queries with indexing and query plan analysis, reducing p99 database latency from 1.2s to 85ms.
- Mentored junior engineers and conducted peer code reviews across 4 engineering teams.

## Education
- B.S. Computer Science | University of California, Berkeley | 2014 - 2018

## Technical Skills
- Languages: Python, SQL, Go, TypeScript, Bash
- Databases & Storage: DuckDB, PostgreSQL, Redis, Snowflake
- Infrastructure & Tools: Docker, Kubernetes, AWS, Git, Linux
"""


def test_audit_clean_resume():
    res = audit_resume_text(CLEAN_RESUME)
    assert res.score >= 85
    assert res.grade in ("A", "B")
    assert res.pass_status is True
    # Should have found contact info and all standard sections
    assert any("experience" in s.lower() for s in res.found_sections)
    assert any("education" in s.lower() for s in res.found_sections)
    assert any("skills" in s.lower() for s in res.found_sections)


def test_audit_resume_missing_contact():
    resume_no_contact = """# Jane Developer
## Summary
Experienced engineer.

## Work Experience
- Software Engineer at Acme Corp. Worked on Python and databases.

## Education
- B.S. in Computer Science.

## Skills
- Python, SQL.
""" + " " * 300  # padding text length

    res = audit_resume_text(resume_no_contact)
    assert res.score < 85
    issue_msgs = [i.message.lower() for i in res.issues]
    assert any("email" in m or "contact" in m for m in issue_msgs)


def test_audit_resume_too_short():
    short_resume = "Just a quick line."
    res = audit_resume_text(short_resume)
    assert res.score < 50
    assert res.grade in ("F", "D")
    assert any("text" in i.message.lower() for i in res.issues)


def test_check_keyword_coverage():
    coverage = check_keyword_coverage(
        CLEAN_RESUME,
        keywords=["python", "duckdb", "kubernetes", "snowflake", "rust"],
        role="Senior Backend Engineer",
    )
    assert coverage is not None
    assert coverage.total == 5
    assert coverage.found == 4  # python, duckdb, kubernetes, snowflake found; rust missing
    assert "rust" in coverage.missing
    assert coverage.percent == 80.0
    assert coverage.role_found is True


def test_audit_resume_file(tmp_path: Path):
    md_file = tmp_path / "resume.md"
    md_file.write_text(CLEAN_RESUME, "utf-8")

    res = audit_resume_file(
        md_file,
        keywords=["python", "sql"],
        min_score=75,
    )
    assert res.score >= 85
    assert res.pass_status is True
    assert res.keyword_coverage is not None
    assert res.keyword_coverage.percent == 100.0
