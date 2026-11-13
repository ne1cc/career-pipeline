"""Title breakdown: exact counts in Python, AI only maps titles to roles."""

import asyncio
from collections import Counter

from cockpit import db, titles
from cockpit.tui import AnswerModal, TitlesModal
from tests.test_cockpit_pipeline import _app, career_ops, db_path, frame  # noqa: F401


def test_clean_title_strips_noise():
    assert titles.clean_title("Data Engineer II (Remote)") == "data engineer ii"
    assert titles.clean_title("Jr. Data Engineer - Seattle, WA") == "jr. data engineer"
    assert titles.clean_title("Backend Engineer, Payments Platform") == "backend engineer"
    assert titles.clean_title("Analyst [Hybrid] Req #12345") == "analyst"
    assert titles.clean_title(None) == ""


def test_parse_groups_ignores_malformed_and_duplicates():
    text = "Here you go:\nData Engineer | target | 1, 2\n**Analyst** | weird | 2,3,99\n```\nnot a line"
    assert titles.parse_groups(text, 3) == {1: ("Data Engineer", "target"), 2: ("Data Engineer", "target"),
                                            3: ("Analyst", "out")}


def test_group_titles_counts_sum_to_jobs_and_unsorted_fallback():
    counts = Counter({"data engineer": 5, "data engineer ii": 3, "chef": 2, "nurse": 1})
    # Titles are sent most-common first: 1 data engineer, 2 data engineer ii, 3 chef, 4 nurse.
    groups = titles.group_titles(counts, lambda s, u: "Data Engineer | target | 1,2\nchef | out | 3")
    by_role = {g.role: g for g in groups}
    assert by_role["Data Engineer"].count == 8
    assert by_role[titles.UNSORTED].titles == {"nurse"}
    assert sum(g.count for g in groups) == sum(counts.values())


def test_group_titles_batches_merge_roles_and_survive_errors():
    counts = Counter({f"t{i}": 1 for i in range(5)})
    calls = []

    def gen(system, user):
        calls.append(user)
        if len(calls) == 2:
            raise RuntimeError("boom")
        n = user.count("\n") + 1
        return f"Data Engineer | target | {','.join(str(i) for i in range(1, n + 1))}"

    groups = titles.group_titles(counts, gen, batch_size=2)
    assert len(calls) == 3
    assert sum(g.count for g in groups) == 5
    assert {g.role for g in groups} == {"Data Engineer", titles.UNSORTED}


def test_tui_titles_view_drilldown_and_guard(db_path, tmp_path):  # noqa: F811
    with db.session(db_path) as con:
        for i, t in enumerate(["Data Engineer", "Data Engineer II (Remote)", "Data Analyst"]):
            db.upsert_jobspy_frame(con, frame({"company": f"Co{i}", "title": t, "link": f"https://e.com/{i}"}))
    prompts = []

    def fake(system, user):
        prompts.append((system, user))
        if "Question:" in user:
            return "Focus on **Data Engineer**."
        return "Data Engineer | target | " + ",".join(
            line.split(".")[0] for line in user.splitlines() if "engineer" in line
        ) + "\nData Analyst | target | " + ",".join(line.split(".")[0] for line in user.splitlines() if "analyst" in line)

    async def drive() -> None:
        app = _app(db_path, tmp_path, None, fake)
        async with app.run_test() as pilot:
            assert len(app.rows) == 3
            await pilot.press("T")
            await app.workers.wait_for_complete()
            await pilot.pause()
            modal = app.screen
            assert isinstance(modal, TitlesModal)
            assert {(g.role, g.count) for g in modal.groups} == {("Data Engineer", 2), ("Data Analyst", 1)}
            # App keys are inert inside the pop-up: `s` must not skip the job behind it.
            assert app.check_action("skip", ()) is False
            await pilot.press("down", "enter")  # first role row (largest = Data Engineer)
            await pilot.pause()
            assert app.title_role.role == "Data Engineer"
            assert sorted(r["title"] for r in app.rows) == ["Data Engineer", "Data Engineer II (Remote)"]
            assert "role:Data Engineer (2)" in app._score_base

            # Reopen: same view (pre-role rows) → cached, no new AI call.
            n = len(prompts)
            await pilot.press("T")
            await pilot.pause()
            assert len(prompts) == n and isinstance(app.screen, TitlesModal)
            await pilot.press("enter")  # "All roles" row clears the drill-down
            await pilot.pause()
            assert app.title_role is None and len(app.rows) == 3

            await pilot.press("T")
            await pilot.pause()
            await pilot.press("a")
            await pilot.pause()
            await pilot.press(*"what fits?", "enter")
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert isinstance(app.screen, AnswerModal)
            assert "Data Engineer | target | 2" in prompts[-1][1]

    asyncio.run(drive())


def test_fallback_chain_sticks_to_fallback_after_primary_fails():
    from cockpit import intel

    calls, switched = [], []

    def agy(s, u):
        calls.append("agy")
        raise RuntimeError("agy down")

    def claude(s, u):
        calls.append("claude")
        return "ok"

    gen = intel.fallback_chain([("agy", agy), ("claude", claude)], lambda a, b, e: switched.append((a, b)))
    assert gen("s", "u") == "ok" and gen("s", "u") == "ok"
    assert calls == ["agy", "claude", "claude"]  # primary tried once, then skipped
    assert switched == [("agy", "claude")]


def test_fallback_chain_treats_blank_reply_as_failure_and_raises_when_all_fail():
    from cockpit import intel

    gen = intel.fallback_chain([("agy", lambda s, u: "  "), ("claude", lambda s, u: "Data Engineer | target | 1")])
    assert gen("s", "u").startswith("Data Engineer")
    dead = intel.fallback_chain([("only", lambda s, u: (_ for _ in ()).throw(RuntimeError("boom")))])
    import pytest

    with pytest.raises(RuntimeError, match="boom"):
        dead("s", "u")


def test_titles_generator_uses_agy_hydrogen_then_claude(db_path, tmp_path, monkeypatch):  # noqa: F811
    from cockpit import intel

    made = []

    def fake_create(provider=None, model_name=None, agy_task=None):
        made.append((provider, model_name))
        return lambda s, u: f"{provider}:{model_name}"

    monkeypatch.setattr(intel, "create_llm_client", fake_create)
    app = _app(db_path, tmp_path)
    assert app._titles_generator()("s", "u") == "agy:gemini-4-hydrogen-medium"
    assert made == [("agy", "gemini-4-hydrogen-medium"), ("claude-cli", "sonnet")]
