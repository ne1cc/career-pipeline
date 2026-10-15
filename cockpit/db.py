from __future__ import annotations

import os
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

DB_PATH = REPO_ROOT / "data" / "job_funnel.duckdb"
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
CREATE TABLE IF NOT EXISTS state_transitions (
    transition_id INTEGER PRIMARY KEY DEFAULT nextval('transition_seq'),
    dedup_key VARCHAR NOT NULL,
    from_status VARCHAR,
    to_status VARCHAR NOT NULL,
    triggered_by VARCHAR NOT NULL,
    notes VARCHAR,
    transitioned_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);
"""

_INSERT_COLS = (
    "dedup_key, jobspy_id, site, company, company_norm, title, location, apply_url, "
    "description, date_posted, priority_score, resume_archetype, resume_path"
)
# TIMESTAMPTZ values come back as text so the driver never needs pytz.
_TS_AS_TEXT = (
    "* EXCLUDE (discovered_at, applied_at, last_updated_at), "
    "discovered_at::VARCHAR AS discovered_at, applied_at::VARCHAR AS applied_at, "
    "last_updated_at::VARCHAR AS last_updated_at"
)


def connect(path: Path = DB_PATH) -> duckdb.DuckDBPyConnection:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(path))
    con.execute(SCHEMA)
    return con


@contextmanager
def session(path: Path = DB_PATH) -> Iterator[duckdb.DuckDBPyConnection]:
    """Short-lived connection: open, use, close. Never hold one across an idle wait."""
    con = connect(path)
    try:
        yield con
    finally:
        con.close()


def scoreboard(con: duckdb.DuckDBPyConnection) -> dict[str, int]:
    midnight = datetime.now(PACIFIC).replace(hour=0, minute=0, second=0, microsecond=0)
    one = lambda sql, p=(): con.execute(sql, list(p)).fetchone()[0]  # noqa: E731
    return {
        "applied_today": one(
            "SELECT count(*) FROM jobs_master WHERE applied_at >= CAST(? AS TIMESTAMPTZ)", [midnight.isoformat()]
        ),
        "queued": one("SELECT count(*) FROM jobs_master WHERE status = 'QUEUED'"),
        "hold": one("SELECT count(*) FROM jobs_master WHERE status = 'REFERRAL_HOLD'"),
        "positive": one(f"SELECT count(DISTINCT dedup_key) FROM ({_positive_keys_sql()})"),
    }


def tmux_line(board: dict[str, int]) -> str:
    return (
        f"#[fg=green]Jobs: {board['applied_today']}/{DAILY_GOAL}#[default]"
        f" | Q:{board['queued']} | H:{board['hold']}"
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
) -> dict[str, int]:
    import polars as pl

    rules = rules or load_rules()
    frame = df if isinstance(df, pl.DataFrame) else pl.from_pandas(df)
    rows = [j.model_dump(mode="json") for r in frame.iter_rows(named=True) if (j := normalize_row(r, rules))]
    stats = {"seen": frame.height, "invalid": frame.height - len(rows), "inserted": 0, "duplicates": 0}
    if rows:
        # Best-scored copy wins when one batch holds the same job from several boards.
        batch = (
            pl.DataFrame(rows)
            .sort("priority_score", descending=True, maintain_order=True)
            .unique(subset="dedup_key", keep="first", maintain_order=True)
        )
        before = con.execute("SELECT count(*) FROM jobs_master").fetchone()[0]
        con.register("incoming", batch.to_arrow())
        try:
            con.execute(
                f"INSERT INTO jobs_master ({_INSERT_COLS}) SELECT {_INSERT_COLS} FROM incoming "
                "ON CONFLICT (dedup_key) DO NOTHING"
            )
        finally:
            con.unregister("incoming")
        stats["inserted"] = con.execute("SELECT count(*) FROM jobs_master").fetchone()[0] - before
        stats["duplicates"] = len(rows) - stats["inserted"]
    write_tmux_cache(con)
    return stats


def transition_job_state(
    con: duckdb.DuckDBPyConnection,
    dedup_key: str,
    new_status: FunnelState,
    triggered_by: str,
    notes: str | None = None,
) -> None:
    con.begin()
    try:
        row = con.execute("SELECT status FROM jobs_master WHERE dedup_key = ?", [dedup_key]).fetchone()
        if row is None:
            raise KeyError(dedup_key)
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


def fetch_jobs(con: duckdb.DuckDBPyConnection, status: str | None = None) -> list[dict[str, Any]]:
    where, params = ("WHERE status = ?", [status]) if status else ("", [])
    cur = con.execute(
        f"SELECT {_TS_AS_TEXT} FROM jobs_master {where} ORDER BY priority_score DESC, discovered_at", params
    )
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


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
