from __future__ import annotations

import os
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterator
from zoneinfo import ZoneInfo

import duckdb

from cockpit.models import FunnelState
from cockpit.normalizer import REPO_ROOT, load_rules, normalize_row

if TYPE_CHECKING:
    import pandas as pd
    import polars as pl

def resolve_db_path(given: Path | str | None = None) -> Path:
    """Resolve the canonical DuckDB database path.

    Order of precedence:
    1. Explicit argument `given`
    2. COCKPIT_DB_PATH or JOBSPY_DB_PATH environment variable
    3. If running inside a git worktree, the shared main repository's data/job_funnel.duckdb
    4. REPO_ROOT / "data" / "job_funnel.duckdb"
    """
    if given:
        return Path(given).expanduser().resolve()
    env = os.environ.get("COCKPIT_DB_PATH") or os.environ.get("JOBSPY_DB_PATH")
    if env:
        return Path(env).expanduser().resolve()

    git_file = REPO_ROOT / ".git"
    if git_file.is_file():
        try:
            content = git_file.read_text("utf-8").strip()
            if content.startswith("gitdir:"):
                gitdir_raw = content.split("gitdir:", 1)[1].strip()
                gitdir_path = Path(gitdir_raw)
                if not gitdir_path.is_absolute():
                    gitdir_path = (REPO_ROOT / gitdir_path).resolve()
                if "worktrees" in gitdir_path.parts:
                    wt_idx = gitdir_path.parts.index("worktrees")
                    common_git = Path(*gitdir_path.parts[:wt_idx])
                    main_repo = common_git.parent
                    main_db = main_repo / "data" / "job_funnel.duckdb"
                    if main_db.exists() or not (REPO_ROOT / "data" / "job_funnel.duckdb").exists():
                        return main_db.resolve()
        except Exception:
            pass

    return (REPO_ROOT / "data" / "job_funnel.duckdb").resolve()


DB_PATH = resolve_db_path()
TMUX_CACHE = Path("/tmp/jobcockpit_tmux.cache")
DAILY_GOAL = 15
PACIFIC = ZoneInfo("America/Los_Angeles")
POSITIVE = (FunnelState.OA_RECEIVED.value, FunnelState.PHONE_SCREEN.value)

SCHEMA = """
CREATE SEQUENCE IF NOT EXISTS transition_seq;
CREATE TABLE IF NOT EXISTS jobs_master (
    dedup_key VARCHAR PRIMARY KEY,
    jobspy_id VARCHAR,
    site VARCHAR,
    company VARCHAR NOT NULL,
    company_norm VARCHAR NOT NULL,
    title VARCHAR NOT NULL,
    location VARCHAR,
    is_remote BOOLEAN DEFAULT FALSE,
    apply_url VARCHAR NOT NULL,
    description VARCHAR,
    date_posted VARCHAR,
    discovered_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    priority_score DOUBLE NOT NULL,
    resume_archetype VARCHAR NOT NULL,
    resume_path VARCHAR NOT NULL,
    status VARCHAR NOT NULL DEFAULT 'QUEUED',
    applied_at TIMESTAMPTZ,
    last_updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    notes VARCHAR
);
CREATE TABLE IF NOT EXISTS job_segments (
    dedup_key VARCHAR NOT NULL,
    segment VARCHAR NOT NULL,
    PRIMARY KEY (dedup_key, segment)
);
CREATE TABLE IF NOT EXISTS state_transitions (
    transition_id INTEGER PRIMARY KEY DEFAULT nextval('transition_seq'),
    dedup_key VARCHAR NOT NULL,
    from_status VARCHAR,
    to_status VARCHAR NOT NULL,
    triggered_by VARCHAR NOT NULL,
    notes VARCHAR,
    transitioned_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);
ALTER TABLE jobs_master ADD COLUMN IF NOT EXISTS is_remote BOOLEAN DEFAULT FALSE;
ALTER TABLE jobs_master ADD COLUMN IF NOT EXISTS is_saved BOOLEAN DEFAULT FALSE;
ALTER TABLE jobs_master ADD COLUMN IF NOT EXISTS saved_at TIMESTAMPTZ;
ALTER TABLE jobs_master ADD COLUMN IF NOT EXISTS fit_score DOUBLE;
ALTER TABLE jobs_master ADD COLUMN IF NOT EXISTS intel_status VARCHAR;
ALTER TABLE jobs_master ADD COLUMN IF NOT EXISTS intel_path VARCHAR;
ALTER TABLE jobs_master ADD COLUMN IF NOT EXISTS intel_error VARCHAR;
ALTER TABLE jobs_master ADD COLUMN IF NOT EXISTS intel_hash VARCHAR;
ALTER TABLE jobs_master ADD COLUMN IF NOT EXISTS intel_updated_at TIMESTAMPTZ;
ALTER TABLE jobs_master ADD COLUMN IF NOT EXISTS folder_path VARCHAR;
ALTER TABLE jobs_master ADD COLUMN IF NOT EXISTS fit_category VARCHAR;
ALTER TABLE jobs_master ADD COLUMN IF NOT EXISTS tailored_resume_path VARCHAR;
ALTER TABLE jobs_master ADD COLUMN IF NOT EXISTS cover_letter_path VARCHAR;
"""

_INSERT_COLS = (
    "dedup_key, jobspy_id, site, company, company_norm, title, location, is_remote, apply_url, "
    "description, date_posted, priority_score, resume_archetype, resume_path"
)
# TIMESTAMPTZ values come back as text so the driver never needs pytz.
_TS_COLS = ("discovered_at", "applied_at", "last_updated_at", "saved_at", "intel_updated_at")
_TS_AS_TEXT = f"* EXCLUDE ({', '.join(_TS_COLS)}), " + ", ".join(f"{c}::VARCHAR AS {c}" for c in _TS_COLS)
LOCK_RETRIES, LOCK_WAIT_S = 10, 0.2


def connect(path: Path | None = None) -> duckdb.DuckDBPyConnection:
    """Waits up to LOCK_RETRIES x LOCK_WAIT_S for another process's short session to finish."""
    path = path or DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    for attempt in range(LOCK_RETRIES + 1):
        try:
            con = duckdb.connect(str(path))
            break
        except duckdb.IOException as e:
            if "lock" not in str(e).lower() or attempt == LOCK_RETRIES:
                raise
            time.sleep(LOCK_WAIT_S)
    con.execute(SCHEMA)
    return con


@contextmanager
def session(path: Path | None = None) -> Iterator[duckdb.DuckDBPyConnection]:
    """Short-lived connection: open, use, close. Never hold one across an idle wait."""
    con = connect(path)
    try:
        yield con
    finally:
        con.close()


def min_score() -> float:
    return load_rules()["display"]["min_score"]


def scoreboard(con: duckdb.DuckDBPyConnection) -> dict[str, int]:
    midnight = datetime.now(PACIFIC).replace(hour=0, minute=0, second=0, microsecond=0)
    one = lambda sql, p=(): con.execute(sql, list(p)).fetchone()[0]  # noqa: E731
    return {
        "applied_today": one(
            "SELECT count(*) FROM jobs_master WHERE applied_at >= CAST(? AS TIMESTAMPTZ)", [midnight.isoformat()]
        ),
        "queued": one(
            "SELECT count(*) FROM jobs_master WHERE status = 'QUEUED' AND priority_score >= ?", [min_score()]
        ),
        "queued_72h": one(
            "SELECT count(*) FROM jobs_master WHERE status = 'QUEUED' AND priority_score >= ? "
            "AND (epoch(discovered_at) >= epoch(now()) - 72 * 3600 "
            "OR (date_posted IS NOT NULL AND try_cast(date_posted as DATE) >= CURRENT_DATE - INTERVAL 3 DAY))",
            [min_score()],
        ),
        "hold": one("SELECT count(*) FROM jobs_master WHERE status = 'REFERRAL_HOLD'"),
        "saved": one("SELECT count(*) FROM jobs_master WHERE is_saved"),
        "positive": one(f"SELECT count(DISTINCT dedup_key) FROM ({_positive_keys_sql()})"),
    }


def tmux_line(board: dict[str, int]) -> str:
    recent = f" (72h:{board.get('queued_72h', 0)})" if "queued_72h" in board else ""
    return (
        f"#[fg=green]Jobs: {board['applied_today']}/{DAILY_GOAL}#[default]"
        f" | Q:{board['queued']}{recent}"
        f" | H:{board['hold']}"
    )


def write_tmux_cache(con: duckdb.DuckDBPyConnection) -> None:
    """Atomic replace so `cockpit tmux-status` never reads a half-written file."""
    tmp = TMUX_CACHE.with_name(TMUX_CACHE.name + f".{os.getpid()}.tmp")
    tmp.write_text(tmux_line(scoreboard(con)) + "\n")
    os.replace(tmp, TMUX_CACHE)


def upsert_jobspy_frame(
    con: duckdb.DuckDBPyConnection,
    df: pl.DataFrame | pd.DataFrame,
    rules: dict[str, Any] | None = None,
    progress_callback: Any = None,
) -> dict[str, int]:
    import polars as pl

    rules = rules or load_rules()
    if isinstance(df, pl.DataFrame):
        records = df.iter_rows(named=True)
        total = df.height
    else:
        records = df.to_dict(orient="records")
        total = len(df)

    rows, pairs = [], set()  # pairs: one job can sit in several scraper sheets
    for idx, r in enumerate(records):
        if progress_callback and idx % 250 == 0:
            progress_callback(idx, total)
        if (j := normalize_row(r, rules)) is None:
            continue
        rows.append(j.model_dump(mode="json"))
        if r.get("segment"):
            pairs.add((j.dedup_key, r["segment"]))
    if progress_callback:
        progress_callback(total, total)
    stats = {"seen": total, "invalid": total - len(rows), "inserted": 0, "duplicates": 0}
    if rows:
        # Best-scored copy wins when one batch holds the same job from several boards.
        # Explicit schema prevents polars from inferring pl.Null when the first 100+ rows have None.
        schema = {
            "dedup_key": pl.String,
            "jobspy_id": pl.String,
            "site": pl.String,
            "company": pl.String,
            "company_norm": pl.String,
            "title": pl.String,
            "location": pl.String,
            "is_remote": pl.Boolean,
            "apply_url": pl.String,
            "description": pl.String,
            "date_posted": pl.String,
            "priority_score": pl.Float64,
            "resume_archetype": pl.String,
            "resume_path": pl.String,
        }
        batch = (
            pl.DataFrame(rows, schema=schema)
            .sort("priority_score", descending=True, maintain_order=True)
            .unique(subset="dedup_key", keep="first", maintain_order=True)
        )
        before = con.execute("SELECT count(*) FROM jobs_master").fetchall()[0][0]
        con.register("incoming", batch.to_arrow())
        try:
            con.execute(
                f"INSERT INTO jobs_master ({_INSERT_COLS}) SELECT {_INSERT_COLS} FROM incoming "
                "ON CONFLICT (dedup_key) DO NOTHING"
            )
        finally:
            con.unregister("incoming")
        stats["inserted"] = con.execute("SELECT count(*) FROM jobs_master").fetchall()[0][0] - before
        if pairs:
            con.register("seg_in", pl.DataFrame(sorted(pairs), schema=["dedup_key", "segment"], orient="row").to_arrow())
            try:
                con.execute("INSERT INTO job_segments SELECT dedup_key, segment FROM seg_in ON CONFLICT DO NOTHING")
            finally:
                con.unregister("seg_in")
        stats["duplicates"] = len(rows) - stats["inserted"]
    write_tmux_cache(con)
    return stats


def transition_job_state(
    con: duckdb.DuckDBPyConnection,
    dedup_key: str,
    new_status: FunnelState,
    triggered_by: str,
    notes: str | None = None,
) -> dict[str, Any]:
    con.begin()
    try:
        row = con.execute("SELECT status, applied_at, notes FROM jobs_master WHERE dedup_key = ?", [dedup_key]).fetchone()
        if row is None:
            raise KeyError(dedup_key)
        prev_snapshot = {"status": row[0], "applied_at": row[1], "notes": row[2]}
        con.execute(
            """UPDATE jobs_master SET status = ?, last_updated_at = CURRENT_TIMESTAMP,
                   applied_at = CASE WHEN ? = 'APPLIED' THEN CURRENT_TIMESTAMP ELSE applied_at END,
                   notes = COALESCE(CAST(? AS VARCHAR), notes)
               WHERE dedup_key = ?""",
            [new_status.value, new_status.value, notes, dedup_key],
        )
        con.execute(
            "INSERT INTO state_transitions (dedup_key, from_status, to_status, triggered_by, notes) "
            "VALUES (?, ?, ?, ?, ?)",
            [dedup_key, row[0], new_status.value, triggered_by, notes],
        )
        con.commit()
    except Exception:
        con.rollback()
        raise
    write_tmux_cache(con)
    return prev_snapshot


def restore_job_state(
    con: duckdb.DuckDBPyConnection,
    dedup_key: str,
    snapshot: dict[str, Any],
) -> None:
    """Restores a previous status, applied_at, and notes (used by undo), recording an audit row."""
    con.begin()
    try:
        row = con.execute("SELECT status FROM jobs_master WHERE dedup_key = ?", [dedup_key]).fetchone()
        if row is None:
            raise KeyError(dedup_key)
        current_status = row[0]
        con.execute(
            """UPDATE jobs_master SET status = ?, last_updated_at = CURRENT_TIMESTAMP,
                   applied_at = ?, notes = ?
               WHERE dedup_key = ?""",
            [snapshot["status"], snapshot.get("applied_at"), snapshot.get("notes"), dedup_key],
        )
        con.execute(
            "INSERT INTO state_transitions (dedup_key, from_status, to_status, triggered_by, notes) "
            "VALUES (?, ?, ?, 'undo', ?)",
            [dedup_key, current_status, snapshot["status"], snapshot.get("notes")],
        )
        con.commit()
    except Exception:
        con.rollback()
        raise
    write_tmux_cache(con)


def toggle_saved(
    con: duckdb.DuckDBPyConnection,
    dedup_key: str,
    intel_path: str | None = None,
    folder_path: str | None = None,
) -> bool:
    """Toggle is_saved. When true, sets saved_at and marks intel_status='pending' if not already done."""
    con.begin()
    try:
        row = con.execute("SELECT is_saved, intel_status FROM jobs_master WHERE dedup_key = ?", [dedup_key]).fetchone()
        if row is None:
            raise KeyError(dedup_key)
        now_saved = not bool(row[0])
        intel_status = row[1]
        if now_saved:
            new_intel = "pending" if intel_status != "done" else "done"
            con.execute(
                """UPDATE jobs_master SET is_saved = TRUE, saved_at = CURRENT_TIMESTAMP,
                       last_updated_at = CURRENT_TIMESTAMP, intel_status = ?,
                       intel_path = COALESCE(?, intel_path),
                       folder_path = COALESCE(?, folder_path)
                   WHERE dedup_key = ?""",
                [new_intel, intel_path, folder_path, dedup_key],
            )
        else:
            con.execute(
                """UPDATE jobs_master SET is_saved = FALSE, last_updated_at = CURRENT_TIMESTAMP
                   WHERE dedup_key = ?""",
                [dedup_key],
            )
        con.commit()
    except Exception:
        con.rollback()
        raise
    write_tmux_cache(con)
    return now_saved


def set_job_note(con: duckdb.DuckDBPyConnection, dedup_key: str, note: str | None) -> None:
    con.execute(
        "UPDATE jobs_master SET notes = ?, last_updated_at = CURRENT_TIMESTAMP WHERE dedup_key = ?",
        [note, dedup_key],
    )


def claim_intel(con: duckdb.DuckDBPyConnection, limit: int = 2) -> list[dict[str, Any]]:
    """Atomically claim pending jobs for intel processing, marking them running."""
    con.begin()
    try:
        keys = [
            r[0]
            for r in con.execute(
                """SELECT dedup_key FROM jobs_master
                   WHERE is_saved = TRUE AND intel_status = 'pending'
                   ORDER BY priority_score DESC
                   LIMIT ?""",
                [limit],
            ).fetchall()
        ]
        if not keys:
            con.commit()
            return []
        con.execute(
            f"""UPDATE jobs_master SET intel_status = 'running', intel_updated_at = CURRENT_TIMESTAMP
                WHERE dedup_key IN ({', '.join('?' for _ in keys)})""",
            keys,
        )
        cur = con.execute(
            f"SELECT {_TS_AS_TEXT} FROM jobs_master WHERE dedup_key IN ({', '.join('?' for _ in keys)})",
            keys,
        )
        cols = [d[0] for d in cur.description]
        jobs = [dict(zip(cols, r)) for r in cur.fetchall()]
        con.commit()
        return jobs
    except Exception:
        con.rollback()
        raise


def finish_intel(
    con: duckdb.DuckDBPyConnection,
    dedup_key: str,
    status: str,
    fit_score: float | None = None,
    path: str | None = None,
    error: str | None = None,
    intel_hash: str | None = None,
    folder_path: str | None = None,
    fit_category: str | None = None,
    tailored_resume_path: str | None = None,
    cover_letter_path: str | None = None,
) -> None:
    con.execute(
        """UPDATE jobs_master SET
               intel_status = ?,
               fit_score = ?,
               intel_path = ?,
               intel_error = ?,
               intel_hash = ?,
               folder_path = COALESCE(?, folder_path),
               fit_category = COALESCE(?, fit_category),
               tailored_resume_path = COALESCE(?, tailored_resume_path),
               cover_letter_path = COALESCE(?, cover_letter_path),
               intel_updated_at = CURRENT_TIMESTAMP
           WHERE dedup_key = ?""",
        [
            status,
            fit_score,
            path,
            error,
            intel_hash,
            folder_path,
            fit_category,
            tailored_resume_path,
            cover_letter_path,
            dedup_key,
        ],
    )


def requeue_stale_intel(con: duckdb.DuckDBPyConnection, minutes: int = 15) -> int:
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat()
    rows = con.execute(
        """SELECT dedup_key FROM jobs_master
           WHERE intel_status = 'running' AND intel_updated_at < CAST(? AS TIMESTAMPTZ)""",
        [cutoff],
    ).fetchall()
    if rows:
        keys = [r[0] for r in rows]
        con.execute(
            f"UPDATE jobs_master SET intel_status = 'pending' WHERE dedup_key IN ({', '.join('?' for _ in keys)})",
            keys,
        )
    return len(rows)


def expire_stale_referral_holds(con: duckdb.DuckDBPyConnection, hours: int = 24) -> int:
    """Move holds older than `hours` to REFERRAL_STALE so they surface before the ATS queue closes."""
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    keys = [
        r[0]
        for r in con.execute(
            "SELECT dedup_key FROM jobs_master WHERE status = 'REFERRAL_HOLD' "
            "AND last_updated_at < CAST(? AS TIMESTAMPTZ)",
            [cutoff],
        ).fetchall()
    ]
    for key in keys:
        transition_job_state(con, key, FunnelState.REFERRAL_STALE, "system", f"hold older than {hours}h")
    return len(keys)


def set_resume_archetype(con: duckdb.DuckDBPyConnection, dedup_key: str, archetype: str, resume_path: str) -> None:
    con.execute(
        "UPDATE jobs_master SET resume_archetype = ?, resume_path = ?, last_updated_at = CURRENT_TIMESTAMP "
        "WHERE dedup_key = ?",
        [archetype, resume_path, dedup_key],
    )


def fetch_jobs(
    con: duckdb.DuckDBPyConnection,
    status: str | None = None,
    floor: float | None = None,
    segment: str | None = None,
    location: str | None = None,
    query: str | None = None,
    hours: int | None = None,
) -> list[dict[str, Any]]:
    """`floor` hides QUEUED jobs below that score; jobs in any other state or saved jobs always show."""
    clauses, params = [], []
    order_by = "priority_score DESC, discovered_at"
    if status == "SAVED":
        clauses.append("is_saved = TRUE")
        order_by = "fit_score DESC NULLS LAST, priority_score DESC, discovered_at"
    elif status in ("72H", "QUEUED_72H"):
        clauses.append("status = 'QUEUED'")
        hours = hours or 72
    elif status:
        clauses.append("status = ?")
        params.append(status)
    if hours is not None:
        d = max(1, (hours + 23) // 24)
        clauses.append(
            "(epoch(discovered_at) >= epoch(now()) - ? * 3600 "
            "OR (date_posted IS NOT NULL AND try_cast(date_posted as DATE) >= CURRENT_DATE - (? * INTERVAL 1 DAY)))"
        )
        params.extend([hours, d])
    if floor is not None and status != "SAVED":
        clauses.append("(status <> 'QUEUED' OR is_saved OR priority_score >= ?)")
        params.append(floor)
    if segment and status != "SAVED":
        clauses.append("dedup_key IN (SELECT dedup_key FROM job_segments WHERE segment = ?)")
        params.append(segment)
    if location and status != "SAVED":
        clauses.append("(lower(COALESCE(location, '')) LIKE ? OR (is_remote AND ? = 'remote'))")
        params += [f"%{location.lower()}%", location.lower()]
    if query:
        q = f"%{query.lower()}%"
        clauses.append("(lower(company) LIKE ? OR lower(title) LIKE ? OR lower(COALESCE(description, '')) LIKE ?)")
        params += [q, q, q]
    where = "WHERE " + " AND ".join(clauses) if clauses else ""
    cur = con.execute(
        f"SELECT {_TS_AS_TEXT} FROM jobs_master {where} ORDER BY {order_by}", params
    )
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def list_segments(con: duckdb.DuckDBPyConnection) -> list[str]:
    return [r[0] for r in con.execute("SELECT DISTINCT segment FROM job_segments ORDER BY 1").fetchall()]


def rescore_queued(
    con: duckdb.DuckDBPyConnection,
    rules: dict[str, Any] | None = None,
    progress_callback: Any = None,
    with_audit: bool = False,
) -> int | tuple[int, dict[str, int]]:
    """Recompute score, archetype and resume for QUEUED jobs only. Resets manual 1/2/3 overrides."""
    import polars as pl

    from cockpit.normalizer import load_rules, score_job

    rules = rules or load_rules()
    rows = con.execute(
        "SELECT dedup_key, title, description, location, is_remote FROM jobs_master WHERE status = 'QUEUED'"
    ).fetchall()
    if not rows:
        return (0, {}) if with_audit else 0

    total_rows = len(rows)
    scored = []
    audit_counts: dict[str, int] = {}

    for idx, (key, title, desc, loc, remote) in enumerate(rows):
        if progress_callback and idx % 250 == 0:
            progress_callback(idx, total_rows)
        s = score_job(title, desc or "", rules, loc or "", bool(remote))
        scored.append((key, s.score, s.archetype.value, s.resume_path))
        if s.disqualified_reason:
            audit_counts[s.disqualified_reason] = audit_counts.get(s.disqualified_reason, 0) + 1

    if progress_callback:
        progress_callback(total_rows, total_rows)

    batch = pl.DataFrame(scored, schema=["dedup_key", "score", "arch", "path"], orient="row")
    con.register("rescored", batch.to_arrow())
    try:
        con.execute(
            "UPDATE jobs_master SET priority_score = r.score, resume_archetype = r.arch, resume_path = r.path "
            "FROM rescored r WHERE jobs_master.dedup_key = r.dedup_key"
        )
    finally:
        con.unregister("rescored")
    write_tmux_cache(con)
    return (len(scored), audit_counts) if with_audit else len(scored)


def _positive_keys_sql() -> str:
    names = ", ".join(f"'{s}'" for s in POSITIVE)
    return f"SELECT dedup_key FROM state_transitions WHERE to_status IN ({names})"


def funnel_by(con: duckdb.DuckDBPyConnection, column: str) -> list[tuple[Any, ...]]:
    """(group, total, applied, positive, median apply latency in hours) per `column`."""
    if column not in {"resume_archetype", "site"}:
        raise ValueError(column)
    return con.execute(
        f"""SELECT COALESCE({column}, '?') AS grp, count(*) AS total,
                   count(*) FILTER (WHERE applied_at IS NOT NULL) AS applied,
                   count(*) FILTER (WHERE dedup_key IN ({_positive_keys_sql()})) AS positive,
                   median(date_diff('minute', discovered_at, applied_at)) / 60.0 AS latency_h
            FROM jobs_master GROUP BY 1 ORDER BY total DESC"""
    ).fetchall()
