"""ATS application form prefill generator.

Ported and enhanced from career-ops prepare-application.mjs into native Python.
Detects ATS platforms (Greenhouse, Ashby, Lever, and generic), extracts candidate
profile data from config/career_ops/ or config/evidence/, and formats exact form
field mappings for rapid manual or semi-automated application submission.
"""

from __future__ import annotations

import re
import urllib.parse
from dataclasses import dataclass
from io import StringIO
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore

SAFE_SLUG = re.compile(r"^[a-zA-Z0-9._-]+$")


@dataclass(frozen=True)
class AtsTarget:
    ats: str  # "greenhouse", "ashby", "lever", "generic"
    company_slug: str
    job_id: str
    original_url: str


@dataclass
class CandidateProfile:
    first_name: str = ""
    last_name: str = ""
    email: str = ""
    phone: str = ""
    location: str = ""
    linkedin: str = ""
    portfolio_url: str = ""
    github: str = ""

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip()


def detect_ats(url: str) -> AtsTarget | None:
    """Detect ATS platform, company slug, and job ID from an apply URL."""
    if not url or not isinstance(url, str):
        return None

    clean_url = url.strip()
    try:
        parsed = urllib.parse.urlparse(clean_url)
    except Exception:
        return None

    if not parsed.scheme or not parsed.netloc:
        return None

    host = parsed.netloc.lower()
    path = parsed.path

    # 1. Greenhouse: boards.greenhouse.io/{company}/jobs/{job_id} or greenhouse.io/{company}/jobs/{job_id}
    if "greenhouse.io" in host:
        m = re.match(r"^/([^/]+)/jobs/(\d+)", path)
        if m:
            comp, jid = m.group(1), m.group(2)
            if SAFE_SLUG.match(comp):
                return AtsTarget("greenhouse", comp, jid, clean_url)

    # 2. Ashby: jobs.ashbyhq.com/{company}/{job_id} or ashbyhq.com/{company}/{job_id}
    if "ashbyhq.com" in host:
        m = re.match(r"^/([^/]+)/([^/?#]+)", path)
        if m:
            comp, jid = m.group(1), m.group(2)
            if SAFE_SLUG.match(comp) and SAFE_SLUG.match(jid):
                return AtsTarget("ashby", comp, jid, clean_url)

    # 3. Lever: jobs.lever.co/{company}/{job_id} or jobs.eu.lever.co/{company}/{job_id}
    if "lever.co" in host:
        m = re.match(r"^/([^/]+)/([^/?#]+)", path)
        if m:
            comp, jid = m.group(1), m.group(2)
            if SAFE_SLUG.match(comp) and SAFE_SLUG.match(jid):
                return AtsTarget("lever", comp, jid, clean_url)

    # 4. Generic Fallback
    parts = [p for p in path.strip("/").split("/") if p]
    job_id = parts[-1] if parts else ""
    comp = host
    return AtsTarget("generic", comp, job_id, clean_url)


def read_candidate_profile(repo_root: Path | None = None) -> CandidateProfile:
    """Read candidate profile from config/career_ops/config/profile.yml or config/evidence/."""
    root = repo_root or Path(__file__).resolve().parent.parent

    # Priority 1: config/career_ops/config/profile.yml
    profile_yml = root / "config" / "career_ops" / "config" / "profile.yml"
    if not profile_yml.exists():
        profile_yml = root / "config" / "career_ops" / "config" / "profile.example.yml"

    data: dict[str, Any] = {}
    if profile_yml.exists() and yaml:
        try:
            loaded = yaml.safe_load(profile_yml.read_text("utf-8"))
            if isinstance(loaded, dict):
                data = loaded
        except Exception:
            pass

    # Priority 2: config/evidence/01_master_career_history.yaml
    if not data:
        ev_yaml = root / "config" / "evidence" / "01_master_career_history.yaml"
        if not ev_yaml.exists():
            ev_yaml = root / "config" / "templates" / "evidence" / "01_master_career_history.yaml"
        if ev_yaml.exists() and yaml:
            try:
                loaded = yaml.safe_load(ev_yaml.read_text("utf-8"))
                if isinstance(loaded, dict) and "candidate" in loaded:
                    data = loaded["candidate"]
            except Exception:
                pass

    full_name = str(data.get("full_name") or data.get("name") or "").strip()
    parts = full_name.split() if full_name else []
    first_name = parts[0] if parts else ""
    last_name = " ".join(parts[1:]) if len(parts) > 1 else ""

    return CandidateProfile(
        first_name=first_name,
        last_name=last_name,
        email=str(data.get("email") or "").strip(),
        phone=str(data.get("phone") or "").strip(),
        location=str(data.get("location") or "").strip(),
        linkedin=str(data.get("linkedin") or "").strip(),
        portfolio_url=str(data.get("portfolio_url") or data.get("portfolio") or "").strip(),
        github=str(data.get("github") or "").strip(),
    )


def build_prefill_fields(
    target: AtsTarget,
    profile: CandidateProfile,
    resume_file: Path | None = None,
    cover_file: Path | None = None,
) -> list[tuple[str, str]]:
    """Build ATS-specific field name to value mapping."""
    res_str = f"{resume_file.name}  (attach file)" if resume_file else "(not attached — see resumes/)"

    cover_str = ""
    if cover_file and cover_file.exists():
        try:
            txt = cover_file.read_text("utf-8").strip()
            word_count = len(txt.split())
            preview = txt[:80].replace("\n", " ")
            cover_str = f"{word_count} words — {preview}…"
        except Exception:
            cover_str = cover_file.name

    if target.ats == "greenhouse":
        fields: list[tuple[str, str] | None] = [
            ("first_name", profile.first_name),
            ("last_name", profile.last_name),
            ("email", profile.email),
            ("phone", profile.phone),
            ("resume", res_str),
            ("cover_letter", cover_str) if cover_str else None,
            ("linkedin_profile", profile.linkedin) if profile.linkedin else None,
            ("website", profile.portfolio_url) if profile.portfolio_url else None,
        ]
        return [f for f in fields if f is not None]

    if target.ats == "ashby":
        fields = [
            ("firstName", profile.first_name),
            ("lastName", profile.last_name),
            ("email", profile.email),
            ("phone", profile.phone),
            ("resume", res_str),
            ("coverLetter", cover_str) if cover_str else None,
            ("linkedInUrl", profile.linkedin) if profile.linkedin else None,
        ]
        return [f for f in fields if f is not None]

    if target.ats == "lever":
        fields = [
            ("name", profile.full_name),
            ("email", profile.email),
            ("phone", profile.phone),
            ("resume", res_str),
            ("comments", cover_str) if cover_str else None,
            ("urls[LinkedIn]", profile.linkedin) if profile.linkedin else None,
            ("urls[Portfolio]", profile.portfolio_url) if profile.portfolio_url else None,
        ]
        return [f for f in fields if f is not None]

    # Generic fallback
    fields = [
        ("Full Name", profile.full_name),
        ("Email", profile.email),
        ("Phone", profile.phone),
        ("Location", profile.location),
        ("Resume", res_str),
        ("Cover Letter", cover_str) if cover_str else None,
        ("LinkedIn", profile.linkedin) if profile.linkedin else None,
        ("Portfolio", profile.portfolio_url) if profile.portfolio_url else None,
        ("GitHub", profile.github) if profile.github else None,
    ]
    return [f for f in fields if f is not None]


def format_prefill_table(
    target: AtsTarget,
    fields: list[tuple[str, str]],
    resume_file: Path | None = None,
    cover_file: Path | None = None,
) -> str:
    """Render a Rich table formatted as an ATS prefill summary."""
    console = Console(file=StringIO(), width=90, color_system=None)

    table = Table(title=f"ATS Prefill · {target.ats.title()} · {target.company_slug}", show_header=True)
    table.add_column("Field Name", style="bold cyan", no_wrap=True)
    table.add_column("Prefill Value", style="white")

    for key, val in fields:
        table.add_row(key, val or "[dim](not set)[/dim]")

    console.print(table)
    if resume_file:
        console.print(f"  [bold]PDF File:[/bold] {resume_file.name}")
    if cover_file:
        console.print(f"  [bold]Cover File:[/bold] {cover_file.name}")
    console.print(f"\n  [bold green]Open Apply URL:[/bold green] {target.original_url}")

    return console.file.getvalue()  # type: ignore
