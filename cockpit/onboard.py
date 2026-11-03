from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from cockpit import intel
from cockpit.exporters import build_base_resumes
from cockpit.normalizer import REPO_ROOT


def seed_user_layer(
    repo_root: Path = REPO_ROOT,
    overwrite: bool = False,
) -> list[Path]:
    """Seeds gitignored user-layer files from committed system templates when missing.

    Contract:
    - Copies starter templates into `config/evidence/` and `config/career_ops/` only if missing (unless `overwrite=True`).
    - Returns list of created/updated file paths.
    """
    created: list[Path] = []
    co_root = repo_root / "config" / "career_ops"
    ev_src = repo_root / "config" / "templates" / "evidence"
    ev_dst = repo_root / "config" / "evidence"

    # 1. Seed career_ops user layer from .example / .template files
    seed_pairs = [
        (co_root / "cv.example.md", co_root / "cv.md"),
        (co_root / "config" / "profile.example.yml", co_root / "config" / "profile.yml"),
        (co_root / "modes" / "_profile.template.md", co_root / "modes" / "_profile.md"),
        (co_root / "modes" / "_custom.template.md", co_root / "modes" / "_custom.md"),
        (co_root / "voice-dna.template.md", co_root / "voice-dna.md"),
        (repo_root / "pipeline_config.example.json", repo_root / "pipeline_config.json"),
    ]
    for src, dst in seed_pairs:
        if src.exists() and (overwrite or not dst.exists()):
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            created.append(dst)

    # 2. Seed config/evidence/ from config/templates/evidence/
    if ev_src.exists():
        for src_file in sorted(ev_src.rglob("*")):
            if not src_file.is_file():
                continue
            rel = src_file.relative_to(ev_src)
            dst_file = ev_dst / rel
            if overwrite or not dst_file.exists():
                dst_file.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src_file, dst_file)
                created.append(dst_file)

    return created


def extract_resume_text(path: Path) -> str:
    """Extracts plain/markdown text from a .md, .txt, .docx, or .pdf resume file.

    Contract:
    - Returns extracted UTF-8 text string.
    - Raises ValueError on unsupported file extensions or unreadable files.
    """
    p = Path(path).expanduser().resolve()
    if not p.exists():
        raise FileNotFoundError(f"Resume file not found: {p}")

    ext = p.suffix.lower()
    if ext in (".md", ".txt", ".tex"):
        return p.read_text("utf-8", errors="replace").strip()

    if ext == ".docx":
        import docx

        doc = docx.Document(str(p))
        lines: list[str] = []
        for para in doc.paragraphs:
            txt = para.text.strip()
            if txt:
                lines.append(txt)
        for table in doc.tables:
            for row in table.rows:
                row_txt = " | ".join(c.text.strip() for c in row.cells if c.text.strip())
                if row_txt:
                    lines.append(row_txt)
        return "\n".join(lines).strip()

    if ext == ".pdf":
        # 1. Try pypdf first
        try:
            from pypdf import PdfReader

            reader = PdfReader(str(p))
            pages = [page.extract_text() or "" for page in reader.pages]
            joined = "\n".join(t.strip() for t in pages if t.strip()).strip()
            if joined:
                return joined
        except Exception:
            pass

        # 2. Try pdftotext fallback if installed
        try:
            proc = subprocess.run(
                ["pdftotext", "-layout", str(p), "-"],
                capture_output=True,
                text=True,
                timeout=20,
                check=False,
            )
            if proc.returncode == 0 and proc.stdout.strip():
                return proc.stdout.strip()
        except (FileNotFoundError, OSError, subprocess.SubprocessError):
            pass

        raise ValueError(f"Could not extract text from PDF: {p}. Install pypdf or poppler (pdftotext).")

    raise ValueError(f"Unsupported resume format '{ext}'. Use .pdf, .docx, .md, or .txt.")


def parse_contact_heuristics(text: str) -> dict[str, str]:
    """Heuristically extracts candidate name, email, phone, github, and linkedin from resume text."""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    name = "Candidate"
    if lines:
        first = re.sub(r"^[#*_`\s]+|[#*_`\s]+$", "", lines[0]).strip()
        if 1 <= len(first.split()) <= 5 and "@" not in first and "http" not in first.lower():
            name = first

    email_m = re.search(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", text)
    phone_m = re.search(r"(\+?\d[\d\s().-]{7,}\d)", text)
    gh_m = re.search(r"((?:https?://)?(?:www\.)?github\.com/[A-Za-z0-9_.-]+)", text, re.IGNORECASE)
    li_m = re.search(r"((?:https?://)?(?:www\.)?linkedin\.com/in/[A-Za-z0-9_.-]+)", text, re.IGNORECASE)

    return {
        "name": name,
        "email": email_m.group(0) if email_m else "",
        "phone": phone_m.group(1).strip() if phone_m else "",
        "github": gh_m.group(1) if gh_m else "",
        "linkedin": li_m.group(1) if li_m else "",
    }


def write_user_profile_configs(
    *,
    repo_root: Path = REPO_ROOT,
    name: str,
    email: str = "",
    phone: str = "",
    location: str = "Remote",
    linkedin: str = "",
    github: str = "",
    portfolio: str = "",
    primary_roles: list[str] | None = None,
    primary_locations: list[str] | None = None,
    min_comp: str = "$100K",
    target_comp: str = "$120K-160K",
    resume_filename: str | None = None,
) -> dict[str, Path]:
    """Writes personalized profile.yml, 01_master_career_history.yaml header, and scoring_rules.local.toml."""
    seed_user_layer(repo_root=repo_root, overwrite=False)
    co_root = repo_root / "config" / "career_ops"
    ev_dir = repo_root / "config" / "evidence"
    co_root.mkdir(parents=True, exist_ok=True)
    ev_dir.mkdir(parents=True, exist_ok=True)

    roles = primary_roles or ["Software Engineer", "Data Engineer", "Data Analyst"]
    locs = [loc.strip().lower() for loc in (primary_locations or [location, "remote"]) if loc.strip()]

    # 1. Write config/career_ops/config/profile.yml
    profile_yml_path = co_root / "config" / "profile.yml"
    profile_yml_path.parent.mkdir(parents=True, exist_ok=True)
    roles_yaml = "\n".join(f'    - "{r}"' for r in roles)
    profile_yml_content = f"""# Generated by `cockpit onboard`
candidate:
  full_name: "{name}"
  email: "{email}"
  phone: "{phone}"
  location: "{location}"
  linkedin: "{linkedin}"
  portfolio_url: "{portfolio}"
  github: "{github}"

target_roles:
  primary:
{roles_yaml}
  archetypes:
    - name: "Software / Backend Engineer"
      level: "Early-Career / Mid"
      fit: "primary"
    - name: "Data / Analytics Engineer"
      level: "Early-Career / Mid"
      fit: "primary"
    - name: "Product / Business Analyst"
      level: "Early-Career / Mid"
      fit: "secondary"

compensation:
  target_range: "{target_comp}"
  currency: "USD"
  minimum: "{min_comp}"
  location_flexibility: "Flexible / Remote or Hybrid"

location:
  country: "United States"
  city: "{location}"
  authorized_in: ["United States"]
  needs_sponsorship: false

language:
  output: en

spend_tier: economy
"""
    profile_yml_path.write_text(profile_yml_content, "utf-8")

    # 2. Update candidate block in config/evidence/01_master_career_history.yaml
    history_path = ev_dir / "01_master_career_history.yaml"
    if history_path.exists():
        hist_text = history_path.read_text("utf-8")
        hist_text = re.sub(r'(^\s*name:\s*)"[^"]*"', rf'\1"{name}"', hist_text, count=1, flags=re.MULTILINE)
        if email:
            hist_text = re.sub(r'(^\s*email:\s*)"[^"]*"', rf'\1"{email}"', hist_text, count=1, flags=re.MULTILINE)
        if phone:
            hist_text = re.sub(r'(^\s*phone:\s*)"[^"]*"', rf'\1"{phone}"', hist_text, count=1, flags=re.MULTILINE)
        if location:
            hist_text = re.sub(r'(^\s*location:\s*)"[^"]*"', rf'\1"{location}"', hist_text, count=1, flags=re.MULTILINE)
        history_path.write_text(hist_text, "utf-8")

    # 3. Write config/scoring_rules.local.toml for personalized outreach & location boosts
    local_rules_path = repo_root / "config" / "scoring_rules.local.toml"
    locs_toml = ", ".join(f'"{l}"' for l in locs)
    resumes_block = ""
    if resume_filename:
        resumes_block = f"""[resumes]
swe_backend = "resumes/{resume_filename}"
data_eng = "resumes/{resume_filename}"
analytics_msba = "resumes/{resume_filename}"

"""
    local_rules_content = f"""# Personalized overrides generated by `cockpit onboard` (gitignored)
{resumes_block}[location]
primary_points = 10.0
primary = [{locs_toml}]

[outreach]
candidate_name = "{name}"
template = \"\"\"Hi — I'm {name}. I just applied for the {{title}} role at {{company}} and wanted to reach out directly. \\
My background is a strong fit for the work, and I'd welcome a quick 10-minute chat, \\
or a pointer to the right person to talk to.\"\"\"
"""
    local_rules_path.write_text(local_rules_content, "utf-8")

    return {
        "profile_yml": profile_yml_path,
        "history_yaml": history_path,
        "local_rules_toml": local_rules_path,
    }


def import_user_resumes(
    source_path: Path | str,
    repo_root: Path = REPO_ROOT,
    *,
    sync_home: bool = True,
    update_duckdb: bool = True,
    configure_rules: bool = True,
    name: str | None = None,
    email: str | None = None,
    phone: str | None = None,
    location: str | None = None,
    linkedin: str | None = None,
    github: str | None = None,
    primary_roles: list[str] | None = None,
    primary_locations: list[str] | None = None,
) -> dict[str, Any]:
    """Imports one or more user resumes (.pdf, .docx, .md, .txt) into the workspace.

    Contract:
    - Extracts text from all discovered resumes under `source_path`.
    - Preserves authentic binary files in `resumes/` and sets up archetype aliases.
    - Synchronizes to `$HOME/resumes/` if `sync_home` is True.
    - Updates `config/career_ops/cv.md`, `profile.yml`, `01_master_career_history.yaml`, and `scoring_rules.local.toml`.
    - Updates DuckDB `jobs_master.resume_path` for queued jobs if database exists.
    - Runs ATS parseability audit on the primary resume.
    """
    seed_user_layer(repo_root=repo_root, overwrite=False)
    src = Path(source_path).expanduser().resolve()
    if not src.exists():
        raise FileNotFoundError(f"Resume path does not exist: {src}")

    files: list[Path] = []
    if src.is_file():
        files = [src]
    else:
        for ext in ("*.md", "*.pdf", "*.docx", "*.txt"):
            files.extend(sorted(src.glob(ext)))
        files = [f for f in files if not f.name.startswith("~$")]

    if not files:
        raise ValueError(f"No .pdf, .docx, .md, or .txt resume files found in {src}")

    co_root = repo_root / "config" / "career_ops"
    ev_dir = repo_root / "config" / "evidence"
    base_res_dir = ev_dir / "base_resumes"
    resumes_out = repo_root / "resumes"
    base_res_dir.mkdir(parents=True, exist_ok=True)
    resumes_out.mkdir(parents=True, exist_ok=True)

    extracted_docs: list[tuple[Path, str]] = []
    has_binary_source = False
    for f in files:
        txt = extract_resume_text(f)
        if txt:
            extracted_docs.append((f, txt))
            if f.suffix.lower() in (".pdf", ".docx"):
                has_binary_source = True
                dest = resumes_out / f.name
                if f.resolve() != dest.resolve():
                    shutil.copy2(f, dest)

    if not extracted_docs:
        raise ValueError(f"Could not extract text from any resume in {src}")

    # Primary resume populates cv.md and archetype aliases
    primary_file, primary_text = extracted_docs[0]
    heuristics = parse_contact_heuristics(primary_text)
    if name:
        heuristics["name"] = name
    if email:
        heuristics["email"] = email
    if phone:
        heuristics["phone"] = phone
    if linkedin:
        heuristics["linkedin"] = linkedin
    if github:
        heuristics["github"] = github

    # Setup archetype copies/aliases for the primary resume
    default_stems = ["swe_fullstack_ai", "data_analytics_engineering", "analytics_bi_product"]
    default_aliases = ["swe_backend_v1", "data_eng_lakehouse_v1", "analytics_msba_v1"]
    primary_ext = primary_file.suffix.lower()

    if primary_ext in (".pdf", ".docx"):
        for alias_stem in default_aliases:
            alias_target = resumes_out / f"{alias_stem}{primary_ext}"
            src_dest = resumes_out / primary_file.name
            if src_dest.exists() and src_dest.resolve() != alias_target.resolve():
                shutil.copy2(src_dest, alias_target)

    # Mirror to ~/resumes/ if requested
    if sync_home:
        try:
            home_resumes = Path.home() / "resumes"
            home_resumes.mkdir(parents=True, exist_ok=True)
            for f in files:
                if f.suffix.lower() in (".pdf", ".docx"):
                    home_dest = home_resumes / f.name
                    if f.resolve() != home_dest.resolve():
                        shutil.copy2(f, home_dest)
            if primary_ext in (".pdf", ".docx"):
                src_dest = resumes_out / primary_file.name
                for alias_stem in default_aliases:
                    alias_home = home_resumes / f"{alias_stem}{primary_ext}"
                    if src_dest.exists() and src_dest.resolve() != alias_home.resolve():
                        shutil.copy2(src_dest, alias_home)
        except Exception:
            pass

    # Primary resume populates cv.md
    if not primary_text.lstrip().startswith("#"):
        cv_markdown = f"# {heuristics['name']}\n\n{primary_text}\n"
    else:
        cv_markdown = f"{primary_text}\n"

    cv_path = co_root / "cv.md"
    cv_path.parent.mkdir(parents=True, exist_ok=True)
    cv_path.write_text(cv_markdown, "utf-8")

    # Sync to private ~/Github/ne1cc/career-ops/cv.md if repository exists locally
    try:
        co_private = Path.home() / "Github" / "ne1cc" / "career-ops" / "cv.md"
        if co_private.parent.exists():
            co_private.write_text(cv_markdown, "utf-8")
    except Exception:
        pass

    # Populate base_resumes/*.md
    for idx, (orig_file, txt) in enumerate(extracted_docs):
        md_body = txt if txt.lstrip().startswith("#") else f"# {heuristics['name']}\n\n{txt}\n"
        stem = orig_file.stem
        (base_res_dir / f"{stem}.md").write_text(md_body, "utf-8")
        if idx < len(default_stems):
            (base_res_dir / f"{default_stems[idx]}.md").write_text(md_body, "utf-8")

    if len(extracted_docs) == 1:
        for stem_alias in default_stems:
            (base_res_dir / f"{stem_alias}.md").write_text(cv_markdown, "utf-8")

    # Compile base resumes so all standard document formats (.docx and .pdf) are generated
    compiled = build_base_resumes(evidence_dir=ev_dir, output_dir=resumes_out)

    # For authentic binary files, ensure their original high-fidelity bytes are preserved
    # and not superseded by markdown-rendered fallbacks
    for f in files:
        if f.suffix.lower() in (".pdf", ".docx"):
            dest = resumes_out / f.name
            if f.resolve() != dest.resolve():
                shutil.copy2(f, dest)

    if primary_ext in (".pdf", ".docx"):
        src_dest = resumes_out / primary_file.name
        for alias_stem in default_aliases:
            alias_target = resumes_out / f"{alias_stem}{primary_ext}"
            if src_dest.exists() and src_dest.resolve() != alias_target.resolve():
                shutil.copy2(src_dest, alias_target)

    # Configure profile.yml and local scoring rules
    if configure_rules:
        write_user_profile_configs(
            repo_root=repo_root,
            name=heuristics["name"],
            email=heuristics.get("email", ""),
            phone=heuristics.get("phone", ""),
            location=location or "Remote",
            linkedin=heuristics.get("linkedin", ""),
            github=heuristics.get("github", ""),
            primary_roles=primary_roles,
            primary_locations=primary_locations,
            resume_filename=primary_file.name if primary_ext in (".pdf", ".docx") else None,
        )

    # Sync DuckDB queued jobs if database exists
    duckdb_updated = 0
    if update_duckdb:
        db_path = repo_root / "data" / "job_funnel.duckdb"
        if db_path.exists():
            try:
                import duckdb

                target_staged = str((resumes_out / primary_file.name).resolve())
                with duckdb.connect(str(db_path)) as con:
                    con.execute(
                        "UPDATE jobs_master SET resume_path = ? WHERE status = 'QUEUED'",
                        [target_staged],
                    )
                    duckdb_updated = con.execute(
                        "SELECT count(*) FROM jobs_master WHERE resume_path = ?",
                        [target_staged],
                    ).fetchone()[0]
            except Exception:
                pass

    # Audit ATS parseability
    ats_scorecard: dict[str, Any] = {}
    try:
        from cockpit.verify_ats import audit_resume_file

        target_file = resumes_out / primary_file.name
        if target_file.exists():
            ats_scorecard = audit_resume_file(target_file)
    except Exception:
        pass

    return {
        "imported_files": [str(f) for f, _ in extracted_docs],
        "primary_resume": str(resumes_out / primary_file.name),
        "cv_path": str(cv_path),
        "heuristics": heuristics,
        "ats_scorecard": ats_scorecard,
        "duckdb_updated": duckdb_updated,
        "compiled_resumes": [str(p) for p in compiled],
    }


def execute_resume_import(
    resume_path: Path | str,
    *,
    repo_root: Path = REPO_ROOT,
    name: str | None = None,
    email: str | None = None,
    phone: str | None = None,
    location: str | None = None,
    linkedin: str | None = None,
    github: str | None = None,
    roles: list[str] | None = None,
    locations: list[str] | None = None,
    sync_home: bool = True,
    update_duckdb: bool = True,
) -> dict[str, Any]:
    """Turnkey single-resume importer: stages binary, updates configs/cv.md, syncs DuckDB and audits ATS.

    Contract:
    - Preserves authentic binary PDF/DOCX bytes in `resumes/` without destructive re-rendering.
    - Symlinks/copies archetype aliases (`swe_backend_v1.pdf`, `data_eng_lakehouse_v1.pdf`, `analytics_msba_v1.pdf`).
    - Updates `config/career_ops/cv.md`, `config/career_ops/config/profile.yml`, `config/evidence/01_master_career_history.yaml`.
    - Overrides `config/scoring_rules.local.toml` with `[resumes]` mapped to `resumes/<filename>`.
    - Updates DuckDB queued jobs and calculates ATS parseability scorecard.
    - Returns dictionary of import artifacts and verification metrics.
    """
    seed_user_layer(repo_root=repo_root, overwrite=False)
    src = Path(resume_path).expanduser().resolve()
    if not src.exists():
        raise FileNotFoundError(f"Resume file not found: {src}")

    # 1. Read raw binary content & extract text
    orig_bytes = src.read_bytes()
    txt = extract_resume_text(src)
    heuristics = parse_contact_heuristics(txt)

    cand_name = name or heuristics.get("name") or "Candidate"
    cand_email = email or heuristics.get("email") or ""
    cand_phone = phone or heuristics.get("phone") or ""
    cand_loc = location or "Remote"
    cand_li = linkedin or heuristics.get("linkedin") or ""
    cand_gh = github or heuristics.get("github") or ""

    co_root = repo_root / "config" / "career_ops"
    ev_dir = repo_root / "config" / "evidence"
    base_res_dir = ev_dir / "base_resumes"
    resumes_out = repo_root / "resumes"
    base_res_dir.mkdir(parents=True, exist_ok=True)
    resumes_out.mkdir(parents=True, exist_ok=True)

    # 2. Stage authentic binary into resumes/
    dest_file = resumes_out / src.name
    dest_file.write_bytes(orig_bytes)

    # 3. Write config/career_ops/cv.md
    if txt.lstrip().startswith("#"):
        cv_markdown = f"{txt}\n"
    else:
        cv_markdown = f"# {cand_name}\n\n{txt}\n"

    cv_path = co_root / "cv.md"
    cv_path.parent.mkdir(parents=True, exist_ok=True)
    cv_path.write_text(cv_markdown, "utf-8")

    # Sync to private career-ops if repository is present
    career_ops_cv = Path.home() / "Github" / "ne1cc" / "career-ops" / "cv.md"
    if career_ops_cv.parent.exists():
        try:
            career_ops_cv.write_text(cv_markdown, "utf-8")
        except OSError:
            pass

    # 4. Populate base_resumes/*.md
    default_stems = ["swe_fullstack_ai", "data_analytics_engineering", "analytics_bi_product"]
    (base_res_dir / f"{src.stem}.md").write_text(cv_markdown, "utf-8")
    for stem_alias in default_stems:
        (base_res_dir / f"{stem_alias}.md").write_text(cv_markdown, "utf-8")

    # 5. Compile base resumes & restore authentic binary
    compiled = build_base_resumes(evidence_dir=ev_dir, output_dir=resumes_out)
    if src.suffix.lower() == ".pdf":
        dest_file.write_bytes(orig_bytes)
        for alias in ["swe_backend_v1.pdf", "data_eng_lakehouse_v1.pdf", "analytics_msba_v1.pdf"]:
            (resumes_out / alias).write_bytes(orig_bytes)

    # 6. Write profile.yml, 01_master_career_history.yaml, and scoring_rules.local.toml
    cfg_paths = write_user_profile_configs(
        repo_root=repo_root,
        name=cand_name,
        email=cand_email,
        phone=cand_phone,
        location=cand_loc,
        linkedin=cand_li,
        github=cand_gh,
        primary_roles=roles,
        primary_locations=locations,
        resume_filename=src.name,
    )

    # 7. Mirror to ~/resumes/ if requested
    home_synced = False
    if sync_home:
        home_resumes = Path.home() / "resumes"
        try:
            home_resumes.mkdir(parents=True, exist_ok=True)
            (home_resumes / src.name).write_bytes(orig_bytes)
            if src.suffix.lower() == ".pdf":
                for alias in ["swe_backend_v1.pdf", "data_eng_lakehouse_v1.pdf", "analytics_msba_v1.pdf"]:
                    (home_resumes / alias).write_bytes(orig_bytes)
            home_synced = True
        except OSError:
            home_synced = False

    # 8. Update DuckDB jobs_master resume_path if database exists
    duckdb_updated_rows = 0
    if update_duckdb:
        db_path = repo_root / "data" / "job_funnel.duckdb"
        if db_path.exists():
            try:
                import duckdb
                with duckdb.connect(str(db_path)) as con:
                    tbl_exists = con.execute(
                        "SELECT count(*) FROM information_schema.tables WHERE table_name = 'jobs_master'"
                    ).fetchone()[0]
                    if tbl_exists:
                        con.execute(
                            "UPDATE jobs_master SET resume_path = ? WHERE status = 'QUEUED'",
                            [str(dest_file)],
                        )
                        res_count = con.execute(
                            "SELECT count(*) FROM jobs_master WHERE resume_path = ?",
                            [str(dest_file)],
                        ).fetchone()[0]
                        duckdb_updated_rows = res_count
            except Exception:
                pass

    # 9. Audit ATS parseability
    ats_score = 0
    ats_grade = "N/A"
    try:
        from cockpit.verify_ats import audit_resume_file
        audit_res = audit_resume_file(dest_file, min_score=70)
        ats_score = audit_res.score
        ats_grade = audit_res.grade
    except Exception:
        pass

    return {
        "dest_resume": str(dest_file),
        "cv_path": str(cv_path),
        "profile_path": str(cfg_paths["profile_yml"]),
        "local_rules_path": str(cfg_paths["local_rules_toml"]),
        "heuristics": heuristics,
        "ats_score": ats_score,
        "ats_grade": ats_grade,
        "duckdb_updated_rows": duckdb_updated_rows,
        "home_synced": home_synced,
        "compiled_resumes": [str(p) for p in compiled],
    }
