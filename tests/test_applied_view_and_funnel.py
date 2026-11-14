"""APPLIED / INTERVIEWS views, funnel metrics, status timeline and mail classification."""
from __future__ import annotations

import asyncio

from textual.widgets import DataTable

from cockpit import db
from cockpit.imap_sync import classify
from cockpit.models import FunnelState as S
from cockpit.tui import FILTERS, FunnelModal, StageMoveModal
from tests.test_cockpit_pipeline import _app, career_ops, db_path, frame  # noqa: F401  (fixtures)


def _seed(con, *companies: str) -> dict[str, str]:
    db.upsert_jobspy_frame(con, frame(*({"company": c, "job_url": f"https://l/{c}"} for c in companies)))
    return dict(con.execute("SELECT company, dedup_key FROM jobs_master").fetchall())


def _move(con, key: str, *states: S) -> None:
    for state in states:
        db.transition_job_state(con, key, state, "test")


def _backdate(con, key: str, days: int) -> None:
    con.execute(f"UPDATE jobs_master SET applied_at = now() - INTERVAL {days} DAY WHERE dedup_key = ?", [key])


def test_applied_orders_newest_first_and_interviews_filters_by_stage(db_path):
    with db.session(db_path) as con:
        k = _seed(con, "Old", "New", "Screened", "Final", "Untouched")
        _move(con, k["Old"], S.APPLIED)
        _backdate(con, k["Old"], 5)
        _move(con, k["New"], S.REFERRAL_HOLD, S.APPLIED)
        _move(con, k["Screened"], S.APPLIED, S.PHONE_SCREEN)
        _move(con, k["Final"], S.APPLIED, S.FINAL_ROUND)
        applied = db.fetch_jobs(con, "APPLIED")
        interviews = db.fetch_jobs(con, "INTERVIEWS")
    assert [r["company"] for r in applied] == ["New", "Old"]
    assert [(r["age_days"], r["had_referral"]) for r in applied] == [(0, True), (5, False)]
    assert {r["company"] for r in interviews} == {"Screened", "Final"}


def test_applied_summary_handles_no_applications_and_no_mature_ones(db_path):
    with db.session(db_path) as con:
        k = _seed(con, "A", "B", "C")
        empty = db.applied_summary(con)
        _move(con, k["A"], S.APPLIED)
        young = db.applied_summary(con)
        _move(con, k["B"], S.REFERRAL_HOLD, S.APPLIED, S.OA_RECEIVED)
        _backdate(con, k["B"], 20)
        _move(con, k["C"], S.APPLIED)
        _backdate(con, k["C"], 30)
        con.execute("UPDATE jobs_master SET tailored_resume_path = '/r.pdf' WHERE dedup_key = ?", [k["C"]])
        full = db.applied_summary(con)
    assert empty == {"total_applied": 0, "applied_7d": 0, "tailored_pct": None, "referral_pct": None,
                     "mature_14d_count": 0, "mature_14d_screens": 0, "mature_screen_rate_pct": None}
    assert (young["total_applied"], young["mature_14d_count"], young["mature_screen_rate_pct"]) == (1, 0, None)
    assert full == {"total_applied": 3, "applied_7d": 1, "tailored_pct": 33.3, "referral_pct": 33.3,
                    "mature_14d_count": 2, "mature_14d_screens": 1, "mature_screen_rate_pct": 50.0}


def test_cohorts_count_later_stages_in_the_week_applied(db_path):
    with db.session(db_path) as con:
        k = _seed(con, "Offer", "Rejected", "Fresh", "Undone")
        _move(con, k["Offer"], S.APPLIED)
        _backdate(con, k["Offer"], 28)  # the stage moves below happen "now", four weeks later
        _move(con, k["Offer"], S.PHONE_SCREEN, S.TECH_ROUND, S.FINAL_ROUND, S.OFFER)
        _move(con, k["Rejected"], S.APPLIED)
        _backdate(con, k["Rejected"], 28)
        _move(con, k["Rejected"], S.REJECTED)
        _move(con, k["Fresh"], S.APPLIED)
        _move(con, k["Undone"], S.APPLIED)
        db.restore_job_state(con, k["Undone"], db.transition_job_state(con, k["Undone"], S.OFFER, "tui"))
        cohorts = db.weekly_funnel_cohorts(con)
    assert len(cohorts) == 2 and cohorts[0]["week"] > cohorts[1]["week"]
    new, old = cohorts
    assert old == {"week": old["week"], "applied": 2, "tailored": 0, "referral": 0, "screen": 1, "tech": 1,
                   "final": 1, "offer": 1, "mature_14d": 2, "screen_pct": 50.0}
    # A mistaken move that was undone is not counted as a reached stage.
    assert (new["applied"], new["screen"], new["offer"], new["mature_14d"], new["screen_pct"]) == (2, 0, 0, 0, None)


def test_transitions_come_back_in_order(db_path):
    with db.session(db_path) as con:
        key = _seed(con, "A")["A"]
        db.transition_job_state(con, key, S.APPLIED, "tui", "sent")
        db.transition_job_state(con, key, S.OA_RECEIVED, "imap")
        steps = db.fetch_job_transitions(con, key)
    assert [(t["from_status"], t["to_status"], t["triggered_by"], t["notes"]) for t in steps] == [
        ("QUEUED", "APPLIED", "tui", "sent"), ("APPLIED", "OA_RECEIVED", "imap", None)]
    assert all(t["transitioned_at"][:4].isdigit() for t in steps)


def test_classify_precedence():
    cases = {
        "Unfortunately we will not extend an offer after your final interview": S.REJECTED,
        "Your offer letter from Acme": S.OFFER,
        "Invitation: onsite interview loop": S.FINAL_ROUND,
        "Final round interview with Acme": S.FINAL_ROUND,
        "Schedule your technical phone screen": S.TECH_ROUND,
        "Acme system design interview": S.TECH_ROUND,
        "Your HackerRank technical assessment": S.OA_RECEIVED,
        "Schedule a call with our recruiter": S.PHONE_SCREEN,
        "Thanks for applying to Acme": None,
    }
    assert {subject: classify(subject) for subject in cases} == cases


def test_tui_applied_and_interviews_views(db_path, tmp_path):
    with db.session(db_path) as con:
        k = _seed(con, "Applied Co", "Interview Co")
        _move(con, k["Applied Co"], S.REFERRAL_HOLD, S.APPLIED)
        _move(con, k["Interview Co"], S.APPLIED)
    app = _app(db_path, tmp_path)

    async def run() -> None:
        async with app.run_test(size=(200, 40)) as pilot:
            table = app.query_one("#table", DataTable)
            app.filter_idx = FILTERS.index("APPLIED")
            app._reload(0)
            await pilot.pause()
            assert [str(c.label) for c in table.columns.values()][:2] == ["Age", "Mode"]
            assert table.get_row_at(0)[:3] == ["0d", "Base", "Interview Co"]
            assert table.get_row_at(1)[:3] == ["0d", "Ref", "Applied Co"]
            assert "Applied: 2 | 7d: 2 | Tailored: 0% | Referral: 50% | Screens: 0 / 0 mature (—)" in app._score_base
            assert "QUEUED → REFERRAL_HOLD  (test)" in app._timeline(app.rows[1])

            await pilot.press("m")
            await pilot.pause()
            assert isinstance(app.screen, StageMoveModal)
            await pilot.press("3")
            await pilot.pause()
            assert [r["company"] for r in app.rows] == ["Applied Co"]  # moved job left the APPLIED view

            await pilot.press("f")
            await pilot.pause()
            assert FILTERS[app.filter_idx] == "INTERVIEWS"
            assert [(r["company"], r["status"]) for r in app.rows] == [("Interview Co", "TECH_ROUND")]

            await pilot.press("V")
            await pilot.pause()
            assert isinstance(app.screen, FunnelModal)
            assert app.screen.query_one("#funnel-table", DataTable).get_row_at(0)[1:6] == ["2", "0", "1", "1", "1"]
            await pilot.press("escape")
            await pilot.pause()
            assert not isinstance(app.screen, FunnelModal)

            await pilot.press("f")  # ALL view: the usual Fit / Score columns are back
            await pilot.pause()
            assert [str(c.label) for c in table.columns.values()][:2] == ["Fit", "Score"]

    asyncio.run(run())
