#!/usr/bin/env python3
"""Open job-feed ingest.

Pulls fresh postings from the public GitHub-hosted feeds listed in AIHawk's
docs/data-sources.md (https://github.com/feder-cr/Jobs_Applier_AI_Agent), then
applies the same role / target-location / recency filters as the tech portals
scraper and writes `open_feeds_jobs.csv` for Cockpit ingest.

New sources are added only when ≥74% of their postings are US; the location
filter drops the rest. riley has only a scrape time (identical for every row), so
only its entry/intern rows are kept. Skipped on purpose: jobhive (2.2 GB parquet), the HuggingFace datasets (parquet; scholarweave
rate-limits and has no company names), and Simplify internships (covered by
search_jobs_internships.py). Headcount's `pay` is dropped: COLUMNS has no salary field.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import io
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import requests

from jobspy.search_jobs_portals import COLUMNS, normalize_portal_job, parse_posted_at

GH = "https://raw.githubusercontent.com"
UA = {"User-Agent": "Mozilla/5.0 (compatible; CareerPipelineFeedBot/1.0)"}


def _join(*parts) -> str:
    return ", ".join(str(p) for p in parts if p)


# Each adapter turns one raw record into the args normalize_portal_job needs:
# (id, title, company, url, location, posted_at). Remote is folded into the
# location text because normalize_portal_job infers it from the word "remote".
def _simplify(r):
    if not (r.get("active") and r.get("is_visible", True)):
        return None
    return (r.get("id"), r.get("title"), r.get("company_name"), r.get("url"),
            _join(*(r.get("locations") or [])), r.get("date_posted"))


def _open_jobs_data(r):
    return (r.get("jobId"), r.get("title"), r.get("company"), r.get("applyUrl"),
            _join(*(r.get("locations") or []), "Remote" if r.get("isRemote") else ""), r.get("postedAt"))


def _ats_feed(r):
    return (r.get("uid"), r.get("title"), r.get("company"), r.get("url"),
            _join(r.get("location"), r.get("workplace")), r.get("posted_at"))


def _applyguy(r):
    return (r.get("id"), r.get("title"), r.get("company"), r.get("listingUrl") or r.get("url"),
            r.get("location"), r.get("posted"))


def _headcount(r):
    # `locations` is "City, ST; City, ST"; `remote` is Remote / Hybrid / On-site / blank.
    return (r.get("job_id"), r.get("title"), r.get("company"), r.get("apply_url"),
            _join(r.get("locations"), "Remote" if r.get("remote") == "Remote" else ""), r.get("posted"))


def _riley(r):
    # shortcut: scraped_at is the same for every row (no posting date), so recency can't
    # filter; keep entry/intern only to avoid flooding the DB with senior roles of unknown age.
    if r.get("skill_level") not in ("entry", "intern"):
        return None
    return (r.get("url"), r.get("title"), r.get("company"), r.get("url"),
            _join(r.get("location"), "Remote" if r.get("remote") else ""), r.get("scraped_at"))


def _json_list(resp):
    return resp.json()


def _json_jobs(resp):
    return resp.json().get("jobs", [])


def _gzip_json_list(resp):
    return json.loads(gzip.decompress(resp.content))


def _csv_rows(resp):
    return csv.DictReader(io.StringIO(resp.content.decode("utf-8", errors="replace")))


# Priority order = dedupe order (first source to carry a URL wins).
SOURCES = [
    ("simplify-new-grad", f"{GH}/SimplifyJobs/New-Grad-Positions/dev/.github/scripts/listings.json", _json_list, _simplify),
    ("applyguy", f"{GH}/ApplyGuy/2027-New-Grad-Jobs/main/data/new-grad-jobs.json", _json_jobs, _applyguy),
    ("vanshb03-new-grad", f"{GH}/vanshb03/New-Grad-2027/dev/.github/scripts/listings.json", _json_list, _simplify),
    ("headcount-software-interns", f"{GH}/HeadcountLabs/software-engineering-internships/main/data/jobs.csv", _csv_rows, _headcount),
    ("headcount-engineering-interns", f"{GH}/HeadcountLabs/engineering-internships/main/data/jobs.csv", _csv_rows, _headcount),
    ("fecklesspleb", f"{GH}/fecklesspleb/ats-new-jobs-feed/main/data/jobs.csv", _csv_rows, _ats_feed),
    # ConorsCode/open-jobs-data mirrors this feed (0 new rows in testing), so it is omitted.
    ("lookin4jobs", f"{GH}/lookin4jobs/open-jobs-data/main/data/jobs.json", _json_list, _open_jobs_data),
    ("riley", "https://github.com/Riley-Dorrington/job-board-aggregator/releases/latest/download/all_jobs.json.gz", _gzip_json_list, _riley),
]


def parse_source(name, records, adapter, term="open feeds", now=None, hours_old=336):
    """Normalize + filter one feed's records; returns kept job dicts."""
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=max(1, hours_old))
    kept = []
    for rec in records:
        args = adapter(rec)
        if not args:
            continue
        job_id, title, company, url, location, posted_at = args
        job = normalize_portal_job(name, job_id, title, company, url, location, posted_at, term)
        # normalize_portal_job returns None when posted_at is unparseable
        if job and cutoff <= parse_posted_at(posted_at) <= now:
            job["first_seen_at"] = now.isoformat()
            kept.append(job)
    return kept


def collect(hours_old=336, smoke_test=False):
    """Fetch every source; one failing feed never blocks the others."""
    kept, failures = {}, 0
    for name, url, load, adapter in SOURCES:
        try:
            resp = requests.get(url, headers=UA, timeout=120)
            resp.raise_for_status()
            records = list(load(resp))
            if smoke_test:
                records = records[:500]
            jobs = parse_source(name, records, adapter, hours_old=hours_old)
        except Exception as exc:  # network, bad JSON, schema drift
            failures += 1
            print(f"  ! {name}: {exc}")
            continue
        new = [j for j in jobs if j["job_url"] not in kept]
        kept.update({j["job_url"]: j for j in new})
        print(f"  > {name}: {len(records)} records -> {len(jobs)} matching, {len(new)} new")
    return list(kept.values()), failures


def main():
    parser = argparse.ArgumentParser(description="Ingest fresh jobs from open GitHub job feeds")
    parser.add_argument("--smoke-test", action="store_true", help="Only read the first 500 records of each feed")
    parser.add_argument("--hours-old", type=int, default=336, help="Keep postings from the last N hours (default: 336)")
    args = parser.parse_args()

    now = datetime.now()
    default_dir = Path(__file__).parent / "scraped jobs" / "results" / now.strftime("%Y_%m") / now.strftime("%Y_%m_%d")
    results_dir = Path(os.environ.get("JOBSPY_RESULTS_DIR", default_dir))
    results_dir.mkdir(parents=True, exist_ok=True)
    out_csv = results_dir / "open_feeds_jobs.csv"

    jobs, failures = collect(hours_old=args.hours_old, smoke_test=args.smoke_test)
    if failures == len(SOURCES):
        sys.exit("All feeds failed; keeping any previous CSV")
    jobs.sort(key=lambda j: j.get("posted_at") or j["date_posted"], reverse=True)

    tmp = out_csv.with_suffix(".csv.tmp")
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS, quoting=csv.QUOTE_NONNUMERIC, escapechar="\\")
        writer.writeheader()
        writer.writerows({k: j.get(k, "") for k in COLUMNS} for j in jobs)
    os.replace(tmp, out_csv)
    print(f"DONE: {len(jobs)} jobs written to {out_csv} ({failures}/{len(SOURCES)} feeds failed)")


if __name__ == "__main__":
    main()
