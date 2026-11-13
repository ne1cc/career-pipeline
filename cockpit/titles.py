"""AI title breakdown: how many jobs exist per role in the current cockpit view.

Intent: collapse thousands of raw title variants ("Data Engineer II (Remote)", "Jr. Data
Engineer - Seattle") into a short list of roles with exact job counts, tiered by how
realistic each role is for the candidate.
Contract: Python counts; the LLM only maps titles to roles. Every job in the input is
counted exactly once, so the role counts always sum to the number of jobs in the view.
Limits: nothing is stored; each run costs one LLM call per ~600 distinct titles. Role
names from separate batches are merged case-insensitively, so near-synonyms across
batches ("BI Analyst" / "Business Intelligence Analyst") can remain separate rows.
"""

from __future__ import annotations

import re
import threading
from collections import Counter
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

TIERS = ("target", "stretch", "explore", "out")
UNSORTED = "Unsorted"
BATCH_SIZE = 600
MAX_WORKERS = 4

DEFAULT_TARGET = (
    "Data Engineer, Analytics Engineer, Data Analyst, BI / Business Intelligence Analyst, "
    "Business Analyst, Backend / Software Engineer, Platform Engineer, at intern / new-grad / "
    "entry / I-II level"
)
DEFAULT_EXPLORE = (
    "Data Scientist, Machine Learning / AI Engineer, Financial / Pricing Analyst, "
    "Product Manager / APM, Security Engineer: not qualified yet, worth watching for skills to learn"
)

_NOISE = re.compile(
    r"\(.*?\)|\[.*?\]"  # (Remote), [Hybrid]
    r"|\b(?:req|job|requisition)\s*(?:id)?\s*[#:]?\s*\w*\d\w*"  # Req #12345
    r"|\s[-–—|@]\s.*$"  # " - Seattle, WA", " | Payments"
    r"|,.*$"  # ", Payments Platform"
)


@dataclass
class RoleGroup:
    role: str
    tier: str
    count: int = 0
    titles: set[str] = field(default_factory=set)  # cleaned title keys in this role


def clean_title(title: str | None) -> str:
    """Cheap, deterministic title key: lowercase, no location/req-id/team suffixes."""
    t = _NOISE.sub("", (title or "").lower())
    return re.sub(r"\s+", " ", t).strip(" -,.") or (title or "").strip().lower()


def count_titles(rows: Iterable[dict[str, Any]]) -> Counter[str]:
    """Exact job counts per cleaned title."""
    return Counter(clean_title(r.get("title")) for r in rows)


def _prompt(target: str, explore: str) -> str:
    return f"""You group job titles into roles for a job seeker. Reply with ONLY lines in this format:
<Role name> | <tier> | <comma-separated title numbers>

Rules:
- Every title number appears in exactly one line.
- Role names are short canonical job titles in Title Case ("Data Engineer", "Backend Engineer").
  Merge variants, levels I/II, "Jr."/"Associate", team names and locations into one role.
  Keep seniority in the name only for senior+ titles ("Senior Data Engineer", "Staff Engineer").
- tier is one of: target, stretch, explore, out
  - target: {target}
  - stretch: a target-family role that is senior / lead / staff / manager, or asks for 5+ years
  - explore: {explore}
  - out: anything else
- No commentary, no markdown, no headers."""


_LINE = re.compile(r"^\s*(?P<role>[^|]+?)\s*\|\s*(?P<tier>\w+)\s*\|\s*(?P<ids>[\d,\s-]+)\s*$")


def parse_groups(text: str, n: int) -> dict[int, tuple[str, str]]:
    """Map title index (1-based) -> (role, tier). Ignores malformed lines and out-of-range ids;
    an index claimed twice keeps its first role."""
    out: dict[int, tuple[str, str]] = {}
    for line in text.splitlines():
        m = _LINE.match(line.strip().strip("`"))
        if not m:
            continue
        role = m["role"].strip().strip("*-# ").strip()
        tier = m["tier"].lower() if m["tier"].lower() in TIERS else "out"
        for tok in re.findall(r"\d+", m["ids"]):
            i = int(tok)
            if 1 <= i <= n and i not in out:
                out[i] = (role, tier)
    return out


def group_titles(
    counts: Counter[str],
    generate: Callable[[str, str], str],
    target: str = DEFAULT_TARGET,
    explore: str = DEFAULT_EXPLORE,
    batch_size: int = BATCH_SIZE,
    on_batch: Callable[[int, int], None] | None = None,
    stop: Any = None,
) -> list[RoleGroup]:
    """Group cleaned titles into roles with exact counts, largest first.

    Contract: sum(g.count) == sum(counts.values()). Titles the model skips or a batch that
    errors land in the "Unsorted" role instead of disappearing. `stop` (anything with
    is_set(), e.g. threading.Event) skips batches not yet sent; the caller discards the result.
    """
    # 1. Most common titles first, so the first batch covers most jobs.
    titles = [t for t, _ in counts.most_common()]
    batches = [titles[i : i + batch_size] for i in range(0, len(titles), batch_size)]
    system = _prompt(target, explore)
    done = 0
    lock = threading.Lock()

    def run(batch: list[str]) -> dict[int, tuple[str, str]]:
        nonlocal done
        if stop is not None and stop.is_set():
            return {}
        listing = "\n".join(f"{i}. {t}" for i, t in enumerate(batch, 1))
        try:
            result = parse_groups(generate(system, listing), len(batch))
        except Exception:
            result = {}
        with lock:
            done += 1
            finished = done
        if on_batch:
            on_batch(finished, len(batches))
        return result

    # 2. Batches are independent, so run a few at once.
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        mapped = list(pool.map(run, batches))

    # 3. Sum exact counts per role; role names merge case-insensitively across batches.
    groups: dict[str, RoleGroup] = {}
    for batch, mapping in zip(batches, mapped):
        for i, t in enumerate(batch, 1):
            role, tier = mapping.get(i, (UNSORTED, "out"))
            g = groups.setdefault(role.lower(), RoleGroup(role, tier))
            g.count += counts[t]
            g.titles.add(t)
    return sorted(groups.values(), key=lambda g: (-g.count, g.role))


def tier_totals(groups: Iterable[RoleGroup]) -> dict[str, int]:
    totals = dict.fromkeys(TIERS, 0)
    for g in groups:
        totals[g.tier] = totals.get(g.tier, 0) + g.count
    return totals


def summarize(groups: list[RoleGroup], limit: int = 60) -> str:
    """Compact role table for an LLM question: small enough for any provider's prompt."""
    lines = [f"{g.role} | {g.tier} | {g.count}" for g in groups[:limit]]
    rest = groups[limit:]
    if rest:
        lines.append(f"(other {len(rest)} roles) | mixed | {sum(g.count for g in rest)}")
    return "role | tier | jobs\n" + "\n".join(lines)
