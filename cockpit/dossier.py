"""On-demand consolidated AI Dossier generator (.md -> .docx) for shortlisted roles.

Intent: compile starred/shortlisted jobs (from Cockpit DuckDB `is_saved=TRUE`, Rolling
Google Sheet `Status == 'Shortlist'`, or today's Tier 1 feed) into a single executive
briefing document with a Market Snapshot and grouped Company & Role Briefs.

Contract:
- `generate_dossier(source, out_dir, ...)` writes `Job_Dossier_YYYY_MM_DD.md` and
  `Job_Dossier_YYYY_MM_DD.docx` and returns `(md_path, docx_path)`.
- Uses Cockpit's LLM client (`cockpit.intel.create_llm_client`) when `use_llm=True` and
  an LLM provider is available; falls back cleanly to deterministic Fit & Strategy notes
  when offline or `--no-llm` is passed.

Limits: relies on `python-docx` for `.docx` export. Does not overwrite per-job 8-artifact
folders produced by `cockpit intel`.
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any, Callable

from docx import Document
from docx.shared import Pt, RGBColor

from cockpit import db
from cockpit.apply_batch import find_latest_feed, select_resume_for_track
from cockpit.normalizer import REPO_ROOT
from funnel_processor import build_recruiter_search_url, normalize_company
from gsheets_sync import read_shortlisted_from_sheet


def collect_dossier_jobs(
    source: str = "saved",
    *,
    db_path: Path | None = None,
    feed_path: Path | None = None,
    config: dict[str, Any] | None = None,
    repo_root: Path = REPO_ROOT,
) -> list[dict[str, Any]]:
    """Gather shortlisted job records from DuckDB, Google Sheets, or Tier 1 JSON feed.

    Contract:
    - `source == 'saved'`: reads `is_saved = TRUE` rows from DuckDB `jobs_master`.
    - `source == 'sheet'`: reads `Status == 'Shortlist'` rows from the Rolling Google Sheet.
    - `source == 'tier1'`: reads `Tier 1` rows from `tui_feed_YYYY_MM_DD.json`.
    - Returns normalized list of job dicts (may be empty).
    """
    src = source.strip().lower()

    if src == "sheet":
        return read_shortlisted_from_sheet(config, base_dir=repo_root)

    if src == "tier1":
        path = feed_path or find_latest_feed(repo_root)
        if path is None or not path.exists():
            return []
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        jobs = payload.get("jobs", []) if isinstance(payload, dict) else []
        return [j for j in jobs if str(j.get("tier", "")).strip().lower() == "tier 1"]

    # Default: 'saved' from Cockpit DuckDB
    with db.session(db_path) as con:
        rows = con.execute(
            """
            SELECT dedup_key, company, company_norm, title, location, apply_url,
                   date_posted, resume_archetype, notes
            FROM jobs_master
            WHERE is_saved = TRUE AND COALESCE(dismissed, FALSE) = FALSE
            ORDER BY saved_at DESC NULLS LAST, priority_score DESC NULLS LAST
            """
        ).fetchall()

    results: list[dict[str, Any]] = []
    for r in rows:
        company = normalize_company(r[2] or r[1] or "")
        results.append(
            {
                "dedup_key": str(r[0] or ""),
                "tier": "Saved",
                "role_track": str(r[7] or "Data / BI Analytics"),
                "company": company,
                "title": str(r[3] or ""),
                "location": str(r[4] or ""),
                "apply_type": "",
                "date_posted": str(r[6] or "")[:10],
                "salary_display": "",
                "apply_url": str(r[5] or ""),
                "recruiter_search_url": build_recruiter_search_url(company),
                "emails": "",
                "company_industry": "",
                "company_num_employees": "",
                "company_revenue": "",
                "notes": str(r[8] or ""),
            }
        )
    return results


def build_market_snapshot(jobs: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute Executive Market Snapshot metrics across shortlisted jobs."""
    company_counts: dict[str, int] = defaultdict(int)
    track_salaries: dict[str, list[float]] = defaultdict(list)
    track_counts: dict[str, int] = defaultdict(int)

    for job in jobs:
        company = str(job.get("company") or "Unknown")
        track = str(job.get("role_track") or "Other")
        company_counts[company] += 1
        track_counts[track] += 1
        for key in ("annual_min", "annual_max"):
            val = job.get(key)
            if isinstance(val, (int, float)) and val > 0:
                track_salaries[track].append(float(val))

    top_companies = sorted(company_counts.items(), key=lambda kv: (-kv[1], kv[0]))[:5]
    salary_by_track: dict[str, str] = {}
    for track, vals in sorted(track_salaries.items()):
        lo, hi = min(vals), max(vals)
        salary_by_track[track] = f"${lo / 1000:.0f}k–${hi / 1000:.0f}k" if hi > lo else f"${hi / 1000:.0f}k"

    return {
        "total_roles": len(jobs),
        "total_companies": len(company_counts),
        "outreach_ready": sum(1 for j in jobs if str(j.get("emails") or "").strip()),
        "top_companies": top_companies,
        "track_counts": dict(sorted(track_counts.items(), key=lambda kv: (-kv[1], kv[0]))),
        "salary_by_track": salary_by_track,
    }


def _deterministic_fit_note(company: str, roles: list[dict[str, Any]]) -> str:
    """Produce a concise, deterministic Fit & Strategy Note when LLM is disabled/offline."""
    tracks = sorted({str(r.get("role_track") or "Data / BI Analytics") for r in roles})
    primary_track = tracks[0]
    archetype, _ = select_resume_for_track(primary_track)
    emails = sorted({str(r.get("emails") or "").strip() for r in roles if str(r.get("emails") or "").strip()})

    lines = [
        f"- **Recommended Resume Track**: `{archetype}` (aligned to {', '.join(tracks)}).",
        f"- **Entry-Level Fit Check**: Confirm required YoE ≤ 2–3 yrs and emphasize SQL, Python, BI/dashboard impact, and measurable project outcomes.",
    ]
    if emails:
        lines.append(
            f"- **Direct Outreach Callout**: Contact email found (`{', '.join(emails)}`). "
            f"Send a concise 4-sentence note referencing your top relevant project and the exact role title at {company}."
        )
    else:
        lines.append(
            f"- **Outreach Move**: Use the LinkedIn Recruiter Search link to message 1–2 early-career recruiters or hiring managers at {company} within 24 hours of applying."
        )
    return "\n".join(lines)


def generate_fit_note(
    company: str,
    roles: list[dict[str, Any]],
    *,
    use_llm: bool = True,
    llm_client: Callable[[str, str], str] | None = None,
) -> str:
    """Generate a Fit & Strategy Note via LLM (with deterministic fallback)."""
    if not use_llm:
        return _deterministic_fit_note(company, roles)

    client = llm_client
    if client is None:
        try:
            from cockpit.intel import create_llm_client

            client = create_llm_client()
        except Exception:
            client = None

    if client is None:
        return _deterministic_fit_note(company, roles)

    role_summary = "\n".join(
        f"- {r.get('title')} ({r.get('role_track')}, {r.get('location')}, salary={r.get('salary_display') or 'N/A'}, emails={r.get('emails') or 'none'})"
        for r in roles
    )
    system_prompt = (
        "You are a concise career strategist helping an early-career candidate land their first data/analytics/engineering role. "
        "Return 3 short markdown bullets: Recommended Resume Track, Seniority/Skill Fit Check, and Direct Outreach Strategy."
    )
    user_prompt = f"Company: {company}\nMatching Roles:\n{role_summary}"
    try:
        response = client(system_prompt, user_prompt)
        if response and response.strip():
            return response.strip()
    except Exception:
        pass
    return _deterministic_fit_note(company, roles)


def render_dossier_markdown(
    jobs: list[dict[str, Any]],
    *,
    use_llm: bool = True,
    llm_client: Callable[[str, str], str] | None = None,
    report_date: date | None = None,
) -> str:
    """Render the full Executive Market Snapshot + Company & Role Briefs in Markdown."""
    today_str = (report_date or date.today()).isoformat()
    snapshot = build_market_snapshot(jobs)

    lines: list[str] = [
        f"# Job Application Dossier — {today_str}",
        "",
        "## Section 1 — Executive Market Snapshot",
        "",
        f"- **Shortlisted Roles**: {snapshot['total_roles']} across {snapshot['total_companies']} companies",
        f"- **Direct Email Outreach Leads**: {snapshot['outreach_ready']}",
    ]
    if snapshot["top_companies"]:
        top_str = ", ".join(f"{co} ({cnt})" for co, cnt in snapshot["top_companies"])
        lines.append(f"- **Top Hiring Companies**: {top_str}")
    if snapshot["track_counts"]:
        track_str = ", ".join(f"{t}: {c}" for t, c in snapshot["track_counts"].items())
        lines.append(f"- **Roles by Track**: {track_str}")
    if snapshot["salary_by_track"]:
        sal_str = "; ".join(f"{t}: {rng}" for t, rng in snapshot["salary_by_track"].items())
        lines.append(f"- **Observed Annualized Salary Ranges**: {sal_str}")

    lines.extend(["", "## Section 2 — Company & Role Briefs", ""])

    if not jobs:
        lines.append("_No shortlisted roles available for this dossier._")
        return "\n".join(lines) + "\n"

    # Group roles by normalized company name
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for job in jobs:
        grouped[str(job.get("company") or "Unknown")].append(job)

    for company, roles in sorted(grouped.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        lines.append(f"### {company} ({len(roles)} role{'s' if len(roles) != 1 else ''})")
        meta_parts = []
        first = roles[0]
        if first.get("company_industry"):
            meta_parts.append(f"Industry: {first['company_industry']}")
        if first.get("company_num_employees"):
            meta_parts.append(f"Size: {first['company_num_employees']}")
        if first.get("company_revenue"):
            meta_parts.append(f"Revenue: {first['company_revenue']}")
        if meta_parts:
            lines.append(f"_{' | '.join(meta_parts)}_")
        lines.append("")

        for role in roles:
            sal = f" | Salary: {role['salary_display']}" if role.get("salary_display") else ""
            posted = f" | Posted: {role['date_posted']}" if role.get("date_posted") else ""
            lines.append(f"- **{role.get('title', 'Unknown')}** ({role.get('role_track', '')} — {role.get('location', '')}{sal}{posted})")
            if role.get("apply_url"):
                lines.append(f"  - Apply URL: {role['apply_url']}")
            if role.get("recruiter_search_url"):
                lines.append(f"  - Recruiter Search: {role['recruiter_search_url']}")
            if role.get("emails"):
                lines.append(f"  - Outreach Email: {role['emails']}")

        lines.append("")
        lines.append("**Fit & Strategy Note**")
        lines.append(generate_fit_note(company, roles, use_llm=use_llm, llm_client=llm_client))
        lines.append("")

    return "\n".join(lines).strip() + "\n"


def write_dossier_docx(md_text: str, output_path: Path) -> Path:
    """Convert structured Dossier Markdown into a clean Word `.docx` document.

    Contract:
    - Creates parent directories and writes a styled `.docx` file to `output_path`.
    - Handles `#`, `##`, `###`, bullet lists (`- ` / `  - `), italic metadata lines,
      and bold callout headers cleanly without external `pandoc` dependency.
    """
    doc = Document()

    normal_style = doc.styles["Normal"]
    normal_style.font.name = "Calibri"
    normal_style.font.size = Pt(10.5)

    for raw_line in md_text.splitlines():
        line = raw_line.rstrip()
        if not line:
            continue
        if line.startswith("# "):
            heading = doc.add_heading(line[2:].strip(), level=0)
            for run in heading.runs:
                run.font.color.rgb = RGBColor(0x1E, 0x29, 0x3B)
        elif line.startswith("## "):
            doc.add_heading(line[3:].strip(), level=1)
        elif line.startswith("### "):
            doc.add_heading(line[4:].strip(), level=2)
        elif line.startswith("  - "):
            p = doc.add_paragraph(line[4:].strip(), style="List Bullet 2")
            p.paragraph_format.space_after = Pt(2)
        elif line.startswith("- "):
            p = doc.add_paragraph(style="List Bullet")
            _add_inline_markdown(p, line[2:].strip())
            p.paragraph_format.space_after = Pt(3)
        elif line.startswith("_") and line.endswith("_"):
            p = doc.add_paragraph()
            run = p.add_run(line.strip("_"))
            run.italic = True
            run.font.color.rgb = RGBColor(0x47, 0x55, 0x69)
        else:
            p = doc.add_paragraph()
            _add_inline_markdown(p, line)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(output_path))
    return output_path


def _add_inline_markdown(paragraph: Any, text: str) -> None:
    """Render simple `**bold**` segments inside a `python-docx` paragraph."""
    parts = text.split("**")
    for idx, chunk in enumerate(parts):
        if not chunk:
            continue
        run = paragraph.add_run(chunk)
        if idx % 2 == 1:
            run.bold = True


def generate_dossier(
    source: str = "saved",
    out_dir: Path | None = None,
    *,
    use_llm: bool = True,
    llm_client: Callable[[str, str], str] | None = None,
    db_path: Path | None = None,
    feed_path: Path | None = None,
    config: dict[str, Any] | None = None,
    report_date: date | None = None,
    repo_root: Path = REPO_ROOT,
) -> tuple[Path, Path]:
    """Generate both `Job_Dossier_YYYY_MM_DD.md` and `.docx` for shortlisted roles.

    Contract:
    - Returns `(md_path, docx_path)`.
    - Writes files into `out_dir` (defaults to `~/Documents/jobspy-cockpit/dossiers`).
    """
    today = report_date or date.today()
    stamp = today.strftime("%Y_%m_%d")
    target_dir = out_dir or (Path.home() / "Documents" / "jobspy-cockpit" / "dossiers")
    target_dir.mkdir(parents=True, exist_ok=True)

    jobs = collect_dossier_jobs(
        source=source,
        db_path=db_path,
        feed_path=feed_path,
        config=config,
        repo_root=repo_root,
    )
    md_text = render_dossier_markdown(jobs, use_llm=use_llm, llm_client=llm_client, report_date=today)

    md_path = target_dir / f"Job_Dossier_{stamp}.md"
    docx_path = target_dir / f"Job_Dossier_{stamp}.docx"
    md_path.write_text(md_text, encoding="utf-8")
    write_dossier_docx(md_text, docx_path)
    return md_path, docx_path
