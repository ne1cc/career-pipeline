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
from collections.abc import Mapping
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
    """Read the applicant's contact fields from the career-ops profile, else the evidence file.

    Contract:
    - Returns a CandidateProfile; any field not found is "" (never raises on bad YAML).
    - profile.yml may nest contacts under `candidate:` (real layout) or be flat (legacy);
      `location` is always a plain string, taken from the candidate block only.
    - The evidence YAML is consulted when profile.yml yields neither a name nor an email.

    Limits: the top-level `location:` mapping (country/city/authorization) is deliberately
    ignored here; it describes work authorization, not a form-fill address.
    """
    root = repo_root or Path(__file__).resolve().parent.parent

    def load(path: Path) -> dict[str, Any]:
        """Return the contact mapping from a YAML file, unwrapping `candidate:` when it is a mapping."""
        if not (path.exists() and yaml):
            return {}
        try:
            loaded = yaml.safe_load(path.read_text("utf-8"))
        except Exception:
            return {}
        if not isinstance(loaded, dict):
            return {}
        nested = loaded.get("candidate")
        return nested if isinstance(nested, dict) else loaded

    # 1. Priority 1: config/career_ops/config/profile.yml (example file when the real one is absent)
    profile_yml = root / "config" / "career_ops" / "config" / "profile.yml"
    if not profile_yml.exists():
        profile_yml = root / "config" / "career_ops" / "config" / "profile.example.yml"
    data = load(profile_yml)

    # 2. No name and no email → fall back to config/evidence/01_master_career_history.yaml
    if not (data.get("full_name") or data.get("name") or data.get("email")):
        ev_yaml = root / "config" / "evidence" / "01_master_career_history.yaml"
        if not ev_yaml.exists():
            ev_yaml = root / "config" / "templates" / "evidence" / "01_master_career_history.yaml"
        data = load(ev_yaml) or data

    location = data.get("location")
    location = location.strip() if isinstance(location, str) else ""
    full_name = str(data.get("full_name") or data.get("name") or "").strip()
    parts = full_name.split() if full_name else []
    first_name = parts[0] if parts else ""
    last_name = " ".join(parts[1:]) if len(parts) > 1 else ""

    return CandidateProfile(
        first_name=first_name,
        last_name=last_name,
        email=str(data.get("email") or "").strip(),
        phone=str(data.get("phone") or "").strip(),
        location=location,
        linkedin=str(data.get("linkedin") or "").strip(),
        portfolio_url=str(data.get("portfolio_url") or data.get("portfolio") or "").strip(),
        github=str(data.get("github") or "").strip(),
    )


@dataclass(frozen=True)
class ApplicationFiles:
    resume: Path | None  # PDF to upload
    tailored: bool  # True when `resume` is the job-specific one, not the base resume
    cover_pdf: Path | None  # cover letter PDF to upload, if any
    cover_text: Path | None  # cover letter markdown/text, for pasting into a text box


def application_files(job: Mapping[str, Any], folder: Path | None) -> ApplicationFiles:
    """Pick the resume and cover letter files to use when applying to one job.

    Contract:
    - resume: `tailored_resume_path` if a file; else first sorted `exports/*Resume*.pdf`;
      else the base `resume_path` if a file; else None (`tailored` is True only for the first two).
    - cover_pdf: `cover_letter_path` if an existing .pdf; else first sorted `exports/*Cover_Letter*.pdf`.
    - cover_text: `03_cover_letter.md`, else `03_tailored_cover_letter.md`, else `cover_letter_path`
      if an existing non-PDF file.
    - Never raises; `folder` may be None or missing, in which case only the job's own paths count.

    Limits: globs are filename-based (`Resume` / `Cover_Letter`), so the export naming scheme
    is load-bearing. Takes the folder as an argument to avoid importing cockpit.intel.
    """

    def existing(value: Any) -> Path | None:
        return Path(value) if value and Path(value).is_file() else None

    def first_export(pattern: str) -> Path | None:
        return next(iter(sorted((folder / "exports").glob(pattern))), None) if folder else None

    # 1. Resume: job-specific file beats exported PDF beats base resume
    tailored = existing(job.get("tailored_resume_path")) or first_export("*Resume*.pdf")
    resume = tailored or existing(job.get("resume_path"))

    # 2. Cover PDF: explicit path (only if it is a PDF) beats exported PDF
    cover_path = existing(job.get("cover_letter_path"))
    cover_pdf = (cover_path if cover_path and cover_path.suffix.lower() == ".pdf" else None) or first_export(
        "*Cover_Letter*.pdf"
    )

    # 3. Cover text: folder markdown beats a non-PDF explicit path
    cover_text = next(
        (p for n in ("03_cover_letter.md", "03_tailored_cover_letter.md") if folder and (p := folder / n).is_file()),
        None,
    ) or (cover_path if cover_path and cover_path.suffix.lower() != ".pdf" else None)

    return ApplicationFiles(resume, tailored is not None, cover_pdf, cover_text)


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


def build_prefill_url(target: AtsTarget, profile: CandidateProfile) -> str:
    """Construct an apply URL with query parameters pre-populated for supported ATS platforms."""
    parsed = urllib.parse.urlparse(target.original_url)
    existing_params = dict(urllib.parse.parse_qsl(parsed.query))
    query_params: dict[str, str] = {}

    if target.ats == "lever":
        # Ensure path points to /apply for Lever forms
        path = parsed.path
        if not path.endswith("/apply") and not path.endswith("/apply/"):
            path = path.rstrip("/") + "/apply"
        parsed = parsed._replace(path=path)

        if profile.full_name:
            query_params["name"] = profile.full_name
        if profile.email:
            query_params["email"] = profile.email
        if profile.phone:
            query_params["phone"] = profile.phone
        if profile.linkedin:
            query_params["urls[LinkedIn]"] = profile.linkedin
        if profile.portfolio_url:
            query_params["urls[Portfolio]"] = profile.portfolio_url
        if profile.github:
            query_params["urls[GitHub]"] = profile.github

    elif target.ats == "greenhouse":
        if profile.first_name:
            query_params["first_name"] = profile.first_name
        if profile.last_name:
            query_params["last_name"] = profile.last_name
        if profile.email:
            query_params["email"] = profile.email
        if profile.phone:
            query_params["phone"] = profile.phone
        if profile.linkedin:
            query_params["linkedin_profile"] = profile.linkedin
        if profile.portfolio_url:
            query_params["website"] = profile.portfolio_url

    elif target.ats == "ashby":
        if profile.first_name:
            query_params["firstName"] = profile.first_name
        if profile.last_name:
            query_params["lastName"] = profile.last_name
        if profile.email:
            query_params["email"] = profile.email
        if profile.phone:
            query_params["phone"] = profile.phone
        if profile.linkedin:
            query_params["linkedInUrl"] = profile.linkedin

    else:
        if profile.full_name:
            query_params["name"] = profile.full_name
        if profile.email:
            query_params["email"] = profile.email
        if profile.phone:
            query_params["phone"] = profile.phone

    # Merge existing and new query parameters (new params take precedence if not empty)
    merged_params = {**existing_params, **{k: v for k, v in query_params.items() if v}}
    new_query = urllib.parse.urlencode(merged_params)
    return urllib.parse.urlunparse(parsed._replace(query=new_query))


def format_prefill_table(
    target: AtsTarget,
    fields: list[tuple[str, str]],
    resume_file: Path | None = None,
    cover_file: Path | None = None,
    prefill_url: str | None = None,
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
    display_url = prefill_url or target.original_url
    console.print(f"\n  [bold green]Open Apply URL:[/bold green] {display_url}")

    return console.file.getvalue()  # type: ignore
