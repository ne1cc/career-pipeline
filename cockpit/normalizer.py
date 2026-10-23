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
LOCAL_RULES_PATH = REPO_ROOT / "config" / "scoring_rules.local.toml"

_COMPANY_SUFFIX = re.compile(r"\b(inc|incorporated|llc|ltd|corp|corporation|co|company)\b")


def _deep_merge(base: dict[str, Any], override: Mapping[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for k, v in override.items():
        if isinstance(v, Mapping) and isinstance(merged.get(k), Mapping):
            merged[k] = _deep_merge(dict(merged[k]), v)
        else:
            merged[k] = v
    return merged


def load_rules(path: Path = RULES_PATH, local_path: Path | None = None) -> dict[str, Any]:
    with path.open("rb") as f:
        rules = tomllib.load(f)
    override_file = local_path if local_path is not None else (LOCAL_RULES_PATH if path == RULES_PATH else None)
    if override_file is not None and override_file.exists():
        with override_file.open("rb") as lf:
            rules = _deep_merge(rules, tomllib.load(lf))
    return rules


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
    if not p.is_absolute():
        p = REPO_ROOT / p
    if p.exists():
        return str(p)
    repo_fallback = REPO_ROOT / "resumes" / p.name
    if repo_fallback.exists():
        return str(repo_fallback)
    home_fallback = Path.home() / "resumes" / p.name
    if home_fallback.exists():
        return str(home_fallback)
    return str(p)


@dataclass(frozen=True)
class Scored:
    score: float
    archetype: ResumeArchetype
    resume_path: str
    matched: list[str] = field(default_factory=list)
    required_yoe: int | None = None
    disqualified_reason: str | None = None


_TITLE_ORDER = (ResumeArchetype.DATA_ENG, ResumeArchetype.ANALYTICS_MSBA, ResumeArchetype.SWE_BACKEND)

_WRITTEN_NUMBERS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
}

_HISTORY_PREFIX = re.compile(
    r"(?i)\b(?:for over|founded|celebrating|established|in business|industry leader|history of|serving|trusted for)\b.{0,45}$"
)

_AGE_LICENSE_SUFFIX = re.compile(
    r"(?i)^(?:.{0,35}\b(?:of age|years old|driver'?s license|driving|retain|warranty|records)\b)"
)

_YOE_PATTERNS = [
    re.compile(r"(?i)\b(\d{1,2})\s*(?:to|-|–)\s*\d{1,2}\+?\s*(?:years?|yrs?)(?:\s+of)?(?:\s+[\w,/&-]+){0,4}\s+(?:experience|exp\b|background|track record)"),
    re.compile(r"(?i)\b(\d{1,2})\+?\s*(?:years?|yrs?)(?:\s+of)?(?:\s+[\w,/&-]+){0,4}\s+(?:experience|exp\b|background|track record)"),
    re.compile(r"(?i)\b(?:minimum|min\.?|at least|requires?|requiring)\s+(?:of\s+)?(\d{1,2})\+?\s*(?:years?|yrs?)(?:\s+of)?(?:\s+[\w,/&-]+){0,4}(?:experience|exp\b)?"),
    re.compile(r"(?i)\b(?:experience|work experience|professional experience)\s*:\s*(\d{1,2})\+?\s*(?:years?|yrs?)"),
]


def extract_required_yoe(description: str) -> int | None:
    """Extract candidate minimum required years of experience from job description.

    Contract:
    - Returns lowest required qualification tier if multiple degrees are cited (e.g. 2 yrs MS / 4 yrs BS).
    - Returns None if no qualification requirements or only company history/age boilerplate matched.
    """
    if not description:
        return None
    d = description.lower()
    if "year" not in d and "yr" not in d:
        return None

    # Pre-normalize written numbers: e.g. "four (4) years" -> "4 years", "five years" -> "5 years"
    d = re.sub(r"(?i)\b(one|two|three|four|five|six|seven|eight|nine|ten)\s*\(\d+\)", r"\1", d)
    d = re.sub(
        r"(?i)\b(one|two|three|four|five|six|seven|eight|nine|ten)\+?\s*(years?|yrs?)",
        lambda m: f"{_WRITTEN_NUMBERS[m.group(1).lower()]} {m.group(2)}",
        d,
    )

    candidates: list[int] = []
    for pat in _YOE_PATTERNS:
        for m in pat.finditer(d):
            val = int(m.group(1))
            if val > 15:
                continue
            start, end = m.start(), m.end()
            pre = d[max(0, start - 45):start]
            post = d[end:min(len(d), end + 35)]
            if _HISTORY_PREFIX.search(pre):
                continue
            if _AGE_LICENSE_SUFFIX.search(post):
                continue
            candidates.append(val)

    return min(candidates) if candidates else None


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
        total_kw, hits = 0.0, []
        for kw, w in rules["weights"].get(arch.value, {}).items():
            in_title = _has(t, kw)
            if in_title or _has(d, kw):
                total_kw += w * (2 if in_title else 1)
                hits.append(kw)
        totals[arch] = (total_kw, hits)
    from_title = next(
        (a for a in _TITLE_ORDER if any(_has(t, k) for k in rules["title_archetype"].get(a.value, []))), None
    )
    # max() keeps the first archetype on ties, so enum order is the tie-break.
    arch = from_title or max(ResumeArchetype, key=lambda a: totals[a][0])
    base_kw_score, hits = totals[arch]

    # 1. Relevance Gate: only relevant titles or technical keyword matches earn boosts
    is_relevant = (from_title is not None) or (base_kw_score > 0.0)
    total = base_kw_score
    if is_relevant:
        if any(_has(t, k) for k in rules.get("boosts", {}).get("title_contains", [])):
            total += rules["boosts"]["points"]
        total += _location_points(location, is_remote, rules)
    else:
        total = 0.0

    # 2. Title Disqualifiers (-100 penalty)
    disqualified_reason: str | None = None
    penalty_pts = rules.get("penalties", {}).get("points", 100.0)
    disq_pts = rules.get("disqualifiers", {}).get("penalty", 100.0)

    if any(_has(t, k) for k in rules.get("penalties", {}).get("title_contains", [])):
        total -= penalty_pts
        disqualified_reason = "senior_title"
    elif any(_has(t, k) for k in rules.get("disqualifiers", {}).get("title_exclude", [])):
        total -= disq_pts
        disqualified_reason = "title_excluded"
    elif _has(t, "architect") and not any(_has(t, k) for k in rules.get("penalties", {}).get("architect_exempt", [])):
        total -= penalty_pts
        disqualified_reason = "architect"
    elif _has(t, "manager") and not any(_has(t, k) for k in rules.get("penalties", {}).get("manager_exempt", [])):
        total -= penalty_pts
        disqualified_reason = "manager"

    # 3. JD Body Disqualifiers & YOE Check
    req_yoe = None
    max_allowed_yoe = rules.get("disqualifiers", {}).get("max_yoe", 4)
    if d and ("year" in d or "yr" in d):
        req_yoe = extract_required_yoe(d)
        if req_yoe is not None and req_yoe >= max_allowed_yoe:
            total -= disq_pts
            disqualified_reason = disqualified_reason or f"yoe_{req_yoe}"

    if not disqualified_reason and d:
        for phrase in rules.get("disqualifiers", {}).get("description_contains", []):
            if phrase in d and _has(d, phrase):
                total -= disq_pts
                disqualified_reason = f"phrase_{phrase}"
                break

    return Scored(
        total,
        arch,
        resolve_resume(rules["resumes"][arch.value]),
        hits,
        required_yoe=req_yoe,
        disqualified_reason=disqualified_reason,
    )


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
    candidate_name = rules.get("outreach", {}).get("candidate_name", "a candidate")
    return rules["outreach"]["template"].format(
        company=job["company"],
        title=job["title"],
        resume_archetype=job["resume_archetype"],
        candidate_name=candidate_name,
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
    red_flags = (
        list(rules.get("penalties", {}).get("title_contains", []))
        + list(rules.get("disqualifiers", {}).get("description_contains", []))
    )
    for p in red_flags:
        if p in text.lower():
            pattern = re.compile(rf"(?i)(?<![a-z0-9])({re.escape(p)})(?![a-z0-9])")
            safe = pattern.sub(r"[bold red]\1[/bold red]", safe)

    # Highlight disqualifying YOE requirements (4+ years) in bold red
    yoe_highlight = re.compile(
        r"(?i)\b([4-9]|\d{2})\+?\s*(?:to|-|–)?\s*\d{0,2}\s*(?:years?|yrs?)(?:\s+of)?(?:\s+[\w,/&-]+){0,4}\s+(?:experience|exp\b)?"
    )
    safe = yoe_highlight.sub(r"[bold red]\g<0>[/bold red]", safe)

    return safe


def linkedin_referral_url(company: str) -> str:
    import urllib.parse

    query = urllib.parse.quote(company)
    return f"https://www.linkedin.com/search/results/people/?keywords={query}"

