"""ATS parseability and keyword coverage validator.

Ported and enhanced from career-ops verify-ats.mjs into native Python.
Provides deterministic 0-100 structural scoring, letter grading, actionable
issue detection, and keyword coverage checks for resumes in Markdown, HTML,
DOCX, or PDF format.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from io import StringIO
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.table import Table

# Email pattern matching standard addresses
EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")
# Bounded phone pattern: checks candidate runs of digits/symbols
PHONE_CANDIDATE_RE = re.compile(r"\+?\(?\d[\d\s().-]{6,23}\d")

# Standard ATS sections mapped to common synonyms (lowercased)
STANDARD_SECTIONS = {
    "experience": ["experience", "work experience", "employment", "work history", "professional experience"],
    "education": ["education", "academic", "degrees", "university", "qualifications"],
    "skills": ["skills", "technical skills", "core competencies", "technologies", "tools", "stack"],
    "projects": ["projects", "personal projects", "portfolio", "selected projects", "key projects"],
    "summary": ["summary", "professional summary", "profile", "about", "objective", "executive summary"],
}

# ATS safe font list
ATS_SAFE_FONTS = {
    "arial", "helvetica", "helvetica neue", "liberation sans", "calibri", "candara",
    "corbel", "segoe ui", "tahoma", "verdana", "trebuchet ms", "times new roman",
    "times", "georgia", "cambria", "garamond", "palatino", "lato", "roboto",
    "open sans", "noto sans", "source sans pro",
}


@dataclass
class AtsIssue:
    severity: str  # "critical", "warning", "info"
    message: str


@dataclass
class KeywordCoverage:
    found: int
    total: int
    percent: float
    missing: list[str] = field(default_factory=list)
    role_found: bool | None = None


@dataclass
class AtsAuditResult:
    score: int
    grade: str
    pass_status: bool
    issues: list[AtsIssue] = field(default_factory=list)
    found_sections: list[str] = field(default_factory=list)
    keyword_coverage: KeywordCoverage | None = None
    file_path: Path | None = None


def calculate_grade(score: int) -> str:
    """Calculate letter grade from 0-100 score."""
    if score >= 90:
        return "A"
    if score >= 80:
        return "B"
    if score >= 70:
        return "C"
    if score >= 60:
        return "D"
    return "F"


def check_keyword_coverage(
    text: str,
    keywords: list[str],
    role: str | None = None,
) -> KeywordCoverage | None:
    """Audit keyword and target role presence in resume text."""
    clean_kws = [k.strip() for k in keywords if k.strip()]
    if not clean_kws and not role:
        return None

    lower_text = text.lower()
    found_count = 0
    missing: list[str] = []

    for kw in clean_kws:
        pattern = r"\b" + re.escape(kw.lower()) + r"\b"
        if re.search(pattern, lower_text):
            found_count += 1
        else:
            missing.append(kw)

    total = len(clean_kws)
    percent = round((found_count / total * 100.0), 1) if total > 0 else 100.0

    role_found = None
    if role:
        role_pattern = r"\b" + re.escape(role.lower()) + r"\b"
        role_found = bool(re.search(role_pattern, lower_text))

    return KeywordCoverage(
        found=found_count,
        total=total,
        percent=percent,
        missing=missing,
        role_found=role_found,
    )


def extract_sections(text: str) -> list[str]:
    """Identify recognized ATS section headings in the text."""
    found: list[str] = []
    lines = text.splitlines()

    for line in lines:
        clean = line.strip().lstrip("#").strip().rstrip(":").lower()
        if not clean or len(clean) > 50:
            continue
        for sec_name, synonyms in STANDARD_SECTIONS.items():
            if sec_name not in found and any(syn == clean or clean.startswith(syn) for syn in synonyms):
                found.append(sec_name)

    return found


def extract_text_from_file(file_path: Path) -> str:
    """Extract plain text from Markdown, HTML, TXT, DOCX, or PDF."""
    ext = file_path.suffix.lower()

    if ext in (".md", ".txt"):
        return file_path.read_text("utf-8", errors="ignore")

    if ext == ".html":
        raw = file_path.read_text("utf-8", errors="ignore")
        # Strip script and style blocks
        cleaned = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", raw, flags=re.DOTALL | re.IGNORECASE)
        # Strip all HTML tags
        return re.sub(r"<[^>]+>", " ", cleaned)

    if ext == ".pdf":
        try:
            import pypdf

            reader = pypdf.PdfReader(str(file_path))
            pages_text = [page.extract_text() or "" for page in reader.pages]
            return "\n".join(pages_text)
        except Exception as e:
            raise RuntimeError(f"Failed to extract text from PDF: {e}")

    if ext == ".docx":
        try:
            import docx

            doc = docx.Document(str(file_path))
            paras = [p.text for p in doc.paragraphs if p.text]
            return "\n".join(paras)
        except Exception as e:
            raise RuntimeError(f"Failed to extract text from DOCX: {e}")

    # Fallback to UTF-8 text read
    return file_path.read_text("utf-8", errors="ignore")


def audit_resume_text(
    text: str,
    keywords: list[str] | None = None,
    role: str | None = None,
    min_score: int = 70,
    file_path: Path | None = None,
) -> AtsAuditResult:
    """Audit resume text for ATS parseability and keyword coverage."""
    issues: list[AtsIssue] = []
    score = 100

    clean_text = text.strip()
    char_count = len(clean_text)

    # 1. Text Volume & Layer Check (weight 15)
    if char_count < 300:
        score -= 15
        issues.append(AtsIssue("critical", f"Text layer too sparse ({char_count} chars; min 300 required for ATS parsing)"))
    elif char_count < 800:
        score -= 7
        issues.append(AtsIssue("warning", f"Low text volume ({char_count} chars; recommended >= 800 for full parsing)"))

    # 2. Recognizable Sections Check (weight 20)
    sections = extract_sections(clean_text)
    essential_sections = ["experience", "education", "skills"]
    missing_essential = [s for s in essential_sections if s not in sections]

    if missing_essential:
        penalty = min(20, len(missing_essential) * 7)
        score -= penalty
        issues.append(AtsIssue("critical", f"Missing standard ATS section headings: {', '.join(missing_essential).title()}"))

    if "summary" not in sections:
        score -= 3
        issues.append(AtsIssue("info", "No dedicated Summary / Profile section found"))

    # 3. Contact Information (weight 15)
    has_email = bool(EMAIL_RE.search(clean_text))
    has_phone = False
    for candidate in PHONE_CANDIDATE_RE.findall(clean_text):
        digits = re.sub(r"\D", "", candidate)
        if len(digits) >= 9:
            has_phone = True
            break

    if not has_email:
        score -= 10
        issues.append(AtsIssue("critical", "No reachable email address detected in text body"))

    if not has_phone:
        score -= 5
        issues.append(AtsIssue("warning", "No reachable phone number (>= 9 digits) detected"))

    # 4. Layout & Table Complexity (weight 20)
    # Check for Markdown tables with 3+ columns or HTML tables
    multi_col_tables = len(re.findall(r"^\|(?:[^|\n]+\|){3,}", clean_text, flags=re.MULTILINE))
    if multi_col_tables > 1:
        score -= 10
        issues.append(AtsIssue("warning", f"Detected {multi_col_tables} multi-column tables; ATS parsers prefer linear text blocks"))

    # 5. Image & Non-text Elements (weight 10)
    markdown_images = len(re.findall(r"!\[.*?\]\(.*?\)", clean_text))
    html_images = len(re.findall(r"<img[^>]*>", clean_text, flags=re.IGNORECASE))
    if (markdown_images + html_images) > 0:
        score -= 10
        issues.append(AtsIssue("critical", "Image tags found; ensure no resume credentials or text are baked into images"))

    # 6. Fonts and Encoding (weight 10)
    # If HTML has font-family styles, verify standard safe fonts
    font_matches = re.findall(r"font-family\s*:\s*([^;}\"']+)", clean_text, flags=re.IGNORECASE)
    for font_str in font_matches:
        fonts = [f.strip().strip("'\"").lower() for f in font_str.split(",")]
        has_safe = any(f in ATS_SAFE_FONTS for f in fonts)
        if not has_safe:
            score -= 5
            issues.append(AtsIssue("warning", f"Non-standard font-family detected: '{font_str.strip()}'; prefer standard ATS-safe fonts"))
            break

    # 7. Hidden Text & Keyword Stuffing (weight 5)
    hidden_css = bool(re.search(r"(display\s*:\s*none|visibility\s*:\s*hidden|font-size\s*:\s*0|opacity\s*:\s*0)", clean_text, flags=re.IGNORECASE))
    if hidden_css:
        score -= 5
        issues.append(AtsIssue("critical", "Hidden text CSS (e.g. font-size:0, display:none) detected; risks ATS spam rejection"))

    # Clamp score to 0..100
    final_score = max(0, min(100, score))
    grade = calculate_grade(final_score)
    has_critical = any(i.severity == "critical" for i in issues)
    pass_status = (final_score >= min_score) and not has_critical

    # 8. Keyword Coverage
    kw_coverage = check_keyword_coverage(clean_text, keywords or [], role=role)

    return AtsAuditResult(
        score=final_score,
        grade=grade,
        pass_status=pass_status,
        issues=issues,
        found_sections=sections,
        keyword_coverage=kw_coverage,
        file_path=file_path,
    )


def audit_resume_file(
    path: Path | str,
    keywords: list[str] | None = None,
    role: str | None = None,
    min_score: int = 70,
) -> AtsAuditResult:
    """Audit a resume file for ATS friendliness and keyword coverage."""
    p = Path(path).resolve()
    if not p.exists():
        raise FileNotFoundError(f"Resume file not found: {p}")

    text = extract_text_from_file(p)
    return audit_resume_text(
        text=text,
        keywords=keywords,
        role=role,
        min_score=min_score,
        file_path=p,
    )


def format_ats_report(result: AtsAuditResult, min_score: int = 70) -> str:
    """Format ATS audit report as a human-readable Rich table and summary."""
    console = Console(file=StringIO(), width=90, color_system=None)
    name = result.file_path.name if result.file_path else "Resume Text"

    status_str = "[bold green]PASS[/bold green]" if result.pass_status else "[bold red]FAIL[/bold red]"
    score_style = "green" if result.score >= 80 else ("yellow" if result.score >= 70 else "red")

    table = Table(title=f"ATS Audit Report · {name}", show_header=True)
    table.add_column("Metric", style="bold cyan")
    table.add_column("Result")

    table.add_row("Status", status_str)
    table.add_row("Score", f"[{score_style}]{result.score} / 100 ({result.grade})[/{score_style}] (threshold: {min_score})")
    table.add_row("Standard Sections", ", ".join(result.found_sections).title() if result.found_sections else "[red]None[/red]")

    console.print(table)

    if result.issues:
        console.print("\n[bold]Issues Detected:[/bold]")
        for issue in result.issues:
            color = "red" if issue.severity == "critical" else ("yellow" if issue.severity == "warning" else "blue")
            console.print(f"  [{color}][{issue.severity.upper()}][/{color}] {issue.message}")

    if result.keyword_coverage:
        kc = result.keyword_coverage
        console.print(f"\n[bold]Keyword Coverage:[/bold] {kc.found} / {kc.total} ({kc.percent}%)")
        if kc.role_found is not None:
            console.print(f"  Target Role Mentioned: {'[green]Yes[/green]' if kc.role_found else '[yellow]No[/yellow]'}")
        if kc.missing:
            console.print(f"  [yellow]Missing Keywords:[/yellow] {', '.join(kc.missing)}")

    return console.file.getvalue()  # type: ignore
