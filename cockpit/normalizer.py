from __future__ import annotations

import functools
import hashlib
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from cockpit.models import JobRow, ResumeArchetype

REPO_ROOT = Path(__file__).resolve().parent.parent
RULES_PATH = REPO_ROOT / "config" / "scoring_rules.toml"

_COMPANY_SUFFIX = re.compile(r"\b(inc|incorporated|llc|ltd|corp|corporation|co|company)\b")


def load_rules(path: Path = RULES_PATH) -> dict[str, Any]:
    with path.open("rb") as f:
        return tomllib.load(f)


def _squash(text: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9 ]+", " ", text.lower()).split())


def norm_company(name: str) -> str:
    return " ".join(_COMPANY_SUFFIX.sub(" ", _squash(name)).split())


def norm_title(title: str) -> str:
    return _squash(title)


def dedup_key(company_norm: str, title_norm: str) -> str:
    return hashlib.sha256(f"{company_norm}|{title_norm}".encode()).hexdigest()[:16]


@functools.lru_cache(maxsize=2048)
def _compile_boundary_pattern(keyword: str) -> re.Pattern:
    return re.compile(rf"(?<![a-z0-9]){re.escape(keyword)}(?![a-z0-9])")


def _has(text: str, keyword: str) -> bool:
    # Token-boundary match so "i" or "go" don't fire inside other words.
    if keyword not in text:
        return False
    return _compile_boundary_pattern(keyword).search(text) is not None


def resolve_resume(path_str: str) -> str:
    p = Path(path_str).expanduser()
    fallback = REPO_ROOT / "resumes" / p.name
    return str(fallback if not p.exists() and fallback.exists() else p)


@dataclass(frozen=True)
class Scored:
    score: float
    archetype: ResumeArchetype
    resume_path: str
    matched: list[str] = field(default_factory=list)


_TITLE_ORDER = (ResumeArchetype.DATA_ENG, ResumeArchetype.ANALYTICS_MSBA, ResumeArchetype.SWE_BACKEND)


def _location_points(location: str, is_remote: bool, rules: Mapping[str, Any]) -> float:
    loc, cfg = location.lower(), rules["location"]
    if is_remote or any(_has(loc, k) for k in cfg["primary"]):
        return cfg["primary_points"]
    if any(_has(loc, k) for k in cfg["secondary"]):
        return cfg["secondary_points"]
    return 0.0


def score_job(
    title: str, description: str, rules: Mapping[str, Any], location: str = "", is_remote: bool = False
) -> Scored:
    t, d = title.lower(), description.lower()
    totals: dict[ResumeArchetype, tuple[float, list[str]]] = {}
    for arch in ResumeArchetype:
        total, hits = 0.0, []
        for kw, w in rules["weights"].get(arch.value, {}).items():
            in_title = _has(t, kw)
            if in_title or _has(d, kw):
                total += w * (2 if in_title else 1)
                hits.append(kw)
        totals[arch] = (total, hits)
    from_title = next(
        (a for a in _TITLE_ORDER if any(_has(t, k) for k in rules["title_archetype"].get(a.value, []))), None
    )
    # max() keeps the first archetype on ties, so enum order is the tie-break.
    arch = from_title or max(ResumeArchetype, key=lambda a: totals[a][0])
    total, hits = totals[arch]
    if any(_has(t, k) for k in rules["penalties"]["title_contains"]):
        total -= rules["penalties"]["points"]
    if any(_has(t, k) for k in rules["boosts"]["title_contains"]):
        total += rules["boosts"]["points"]
    total += _location_points(location, is_remote, rules)
    return Scored(total, arch, resolve_resume(rules["resumes"][arch.value]), hits)


def _text(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def normalize_row(row: Mapping[str, Any], rules: Mapping[str, Any]) -> JobRow | None:
    """Return None when company, title or an apply URL is missing."""
    company, title = _text(row.get("company")), _text(row.get("title"))
    apply_url = _text(row.get("job_url_direct")) or _text(row.get("job_url"))
    if not (company and title and apply_url):
        return None
    c_norm = norm_company(company)
    description = _text(row.get("description")) or ""
    location = _text(row.get("location"))
    is_remote = row.get("is_remote") in (True, "True", "true", 1)
    scored = score_job(title, description, rules, location or "", is_remote)
    posted = row.get("date_posted")
    return JobRow(
        dedup_key=dedup_key(c_norm, norm_title(title)),
        jobspy_id=_text(row.get("id")),
        site=_text(row.get("site")),
        company=company,
        company_norm=c_norm,
        title=title,
        location=location,
        is_remote=is_remote,
        apply_url=apply_url,
        description=description or None,
        date_posted=None if posted is None or posted != posted else str(posted),
        priority_score=scored.score,
        resume_archetype=scored.archetype,
        resume_path=scored.resume_path,
    )


def render_outreach(job: Mapping[str, Any], rules: Mapping[str, Any]) -> str:
    return rules["outreach"]["template"].format(
        company=job["company"], title=job["title"], resume_archetype=job["resume_archetype"]
    )


def highlight_jd(text: str, matched: list[str], rules: Mapping[str, Any]) -> str:
    """Escapes Rich markup from text and applies [bold green] to matched keywords and [bold red] to penalties."""
    from rich.markup import escape

    safe = escape(text)
    # Highlight matched keywords in bold green
    for kw in matched:
        pattern = re.compile(rf"(?i)\b({re.escape(kw)})\b")
        safe = pattern.sub(r"[bold green]\1[/bold green]", safe)

    # Highlight penalties / red flags in bold red
    penalties = rules.get("penalties", {}).get("title_contains", [])
    for p in penalties:
        pattern = re.compile(rf"(?i)\b({re.escape(p)})\b")
        safe = pattern.sub(r"[bold red]\1[/bold red]", safe)

    return safe


def linkedin_referral_url(company: str) -> str:
    import urllib.parse

    query = urllib.parse.quote(company)
    return f"https://www.linkedin.com/search/results/people/?keywords={query}"

