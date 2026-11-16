"""Stages are exclusive tabs; 72h / new-grad / intern are filters that stack on any tab."""
import asyncio

from cockpit import db
from cockpit.tui import TABS, JobCockpitApp
from tests.test_cockpit_pipeline import db_path, frame  # noqa: F401 (db_path is a fixture)


def test_tab_counts_follow_filters_and_stages_do_not_overlap(db_path):
    with db.session(db_path) as con:
        db.upsert_jobspy_frame(con, frame(
            {"title": "New Grad Software Engineer", "company": "GradCo", "job_url": "https://l/1"},
            {"title": "Senior Engineer", "company": "SeniorCo", "job_url": "https://l/2"},
            {"title": "Software Engineer Intern", "company": "InternCo", "job_url": "https://l/3"},
        ))
        keys = {r["company"]: r["dedup_key"] for r in db.fetch_jobs(con)}
        db.record_open(con, keys["SeniorCo"])
        db.toggle_saved(con, keys["GradCo"])
        counts = db.count_tabs(con)
        assert (counts["NEW"], counts["VIEWED"], counts["SAVED"], counts[None]) == (1, 1, 1, 3)
        # A job is New, Viewed or Saved, never two at once.
        assert [r["company"] for r in db.fetch_jobs(con, "NEW")] == ["InternCo"]
        assert db.count_tabs(con, new_grad=True)[None] == 1
        assert db.count_tabs(con, intern=True)["NEW"] == 1


def test_new_grad_key_narrows_the_current_tab(db_path):
    with db.session(db_path) as con:
        db.upsert_jobspy_frame(con, frame(
            {"title": "New Grad Software Engineer", "company": "GradCo", "job_url": "https://l/1"},
            {"title": "Software Engineer", "company": "PlainCo", "job_url": "https://l/2"},
        ))

    async def drive() -> None:
        app = JobCockpitApp(db_path)
        async with app.run_test() as pilot:
            assert len(app.rows) == 2
            await pilot.press("N")
            assert [r["company"] for r in app.rows] == ["GradCo"]
            assert TABS[app.tab_idx] == "NEW"
            assert app._tab_counts["NEW"] == 1 and "new-grad" in str(app.query_one("#tabs").render())
            await pilot.press("N")
            assert len(app.rows) == 2

    asyncio.run(drive())
