from __future__ import annotations

import email
import email.policy
import imaplib
import os
import re
from datetime import date, timedelta
from pathlib import Path

from cockpit import macos
from cockpit.db import DB_PATH, POSITIVE, session, transition_job_state, write_tmux_cache
from cockpit.models import FunnelState
from cockpit.normalizer import norm_company

ATS_DOMAINS = (
    "greenhouse.io",
    "greenhouse-mail.io",
    "lever.co",
    "ashbyhq.com",
    "myworkday.com",
    "hackerrank.com",
    "codesignal.com",
)
# Checked in order: a rejection wins over everything, then the furthest stage wins, so
# "technical phone screen" is a tech round and "final interview" is not a phone screen.
_RULES = (
    (FunnelState.REJECTED, re.compile(r"unfortunately|not (be )?moving forward|other candidates|will not be proceeding|decided not to", re.I)),
    (FunnelState.OFFER, re.compile(r"offer letter|job offer|offer of employment|pleased to offer|extend(ing)? (you )?an offer|your offer", re.I)),
    (FunnelState.FINAL_ROUND, re.compile(r"final (round|interview|stage|loop)|on-?site|super ?day", re.I)),
    (FunnelState.TECH_ROUND, re.compile(r"(technical|tech|coding) (interview|round|screen|phone screen)|live coding|system design|pair programming", re.I)),
    (FunnelState.OA_RECEIVED, re.compile(r"assessment|hackerrank|codesignal|coding challenge|take-home", re.I)),
    (FunnelState.PHONE_SCREEN, re.compile(r"phone screen|schedule (a|your) (call|interview)|interview|speak with|chat with", re.I)),
)
# A job only ever moves up this ladder from mail; a lower-stage email never pulls it back.
_RANK = {s: i for i, s in enumerate((FunnelState.APPLIED.value, *POSITIVE))}


def classify(subject: str) -> FunnelState | None:
    return next((state for state, rx in _RULES if rx.search(subject)), None)


def sync_gmail_statuses(
    db_path: Path = DB_PATH, user: str | None = None, days: int = 14
) -> list[tuple[str, str, str]]:
    """Advance APPLIED jobs from ATS mail. Returns (company, new_status, subject) per change."""
    user = user or os.getenv("GMAIL_USER")
    password = macos.keychain_password()
    if not (user and password):
        raise RuntimeError("Need GMAIL_USER and a Gmail app password (run `cockpit keychain-setup`).")

    with session(db_path) as con:
        open_jobs = con.execute(
            "SELECT dedup_key, company_norm, status FROM jobs_master "
f"WHERE status IN ({', '.join(repr(s) for s in _RANK)}) ORDER BY applied_at DESC NULLS LAST"
        ).fetchall()
    jobs: dict[str, tuple[str, str]] = {}
    for key, company, status in open_jobs:
        jobs.setdefault(company, (key, status))  # latest application wins per company

    messages: list[str] = []  # "<from display> <subject>" — connection is closed before any DB write
    since = (date.today() - timedelta(days=days)).strftime("%d-%b-%Y")
    with imaplib.IMAP4_SSL("imap.gmail.com") as imap:
        imap.login(user, password)
        imap.select("INBOX", readonly=True)
        for domain in ATS_DOMAINS:
            _, found = imap.search(None, "SINCE", since, "FROM", domain)
            for num in found[0].split():
                _, data = imap.fetch(num, "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT)])")
                msg = email.message_from_bytes(data[0][1], policy=email.policy.default)
                messages.append(f"{msg['From'] or ''}\x00{msg['Subject'] or ''}")

    changes: list[tuple[str, str, str]] = []
    for raw in messages:
        sender, subject = raw.split("\x00", 1)
        state = classify(subject)
        if state is None:
            continue
        haystack = f" {norm_company(sender + ' ' + subject)} "
        for company, (key, current) in jobs.items():
            if f" {company} " not in haystack:
                continue
            if state is not FunnelState.REJECTED and _RANK[state.value] <= _RANK[current]:
                break
            with session(db_path) as con:
                transition_job_state(con, key, state, "imap", subject[:200])
            jobs[company] = (key, state.value)
            changes.append((company, state.value, subject))
            if state is not FunnelState.REJECTED:
                macos.notify(f"{state.value}: {company}")
            break
    with session(db_path) as con:
        write_tmux_cache(con)
    return changes
