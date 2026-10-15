from __future__ import annotations

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


def _has(text: str, keyword: str) -> bool:
    # Token-boundary match so "i" or "go" don't fire inside other words.
    return re.search(rf"(?<![a-z0-9]){re.escape(keyword)}(?![a-z0-9])", text) is not None


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


def score_job(title: str, description: str, rules: Mapping[str, Any]) -> Scored:
    t, d = title.lower(), description.lower()
    best: tuple[float, ResumeArchetype, list[str]] | None = None
    for arch in ResumeArchetype:  # enum order = tie-break order
        total, hits = 0.0, []
        for kw, w in rules["weights"].get(arch.value, {}).items():
            in_title = _has(t, kw)
            if in_title or _has(d, kw):
                total += w * (2 if in_title else 1)
                hits.append(kw)
        if best is None or total > best[0]:
            best = (total, arch, hits)
    total, arch, hits = best  # type: ignore[misc]
    if any(_has(t, k) for k in rules["penalties"]["title_contains"]):
        total -= rules["penalties"]["points"]
    if any(_has(t, k) for k in rules["boosts"]["title_contains"]):
        total += rules["boosts"]["points"]
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
    scored = score_job(title, description, rules)
    posted = row.get("date_posted")
    return JobRow(
        dedup_key=dedup_key(c_norm, norm_title(title)),
        jobspy_id=_text(row.get("id")),
        site=_text(row.get("site")),
        company=company,
        company_norm=c_norm,
        title=title,
        location=_text(row.get("location")),
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
