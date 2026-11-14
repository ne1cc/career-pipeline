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
            # Full rows (every field) sorted role A→Z, then title: same titles sit together.
            assert app.sort_col == "role" and "sort:role" in app._score_base
            assert [r["title"] for r in app.rows] == ["Data Analyst", "Data Engineer", "Data Engineer II (Remote)"]
            assert all({"company", "location", "priority_score"} <= r.keys() for r in app.rows)

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

    def fake_create(provider=None, model_name=None, agy_task=None, cancel=None):
        made.append((provider, model_name))
        return lambda s, u: f"{provider}:{model_name}"

    monkeypatch.setattr(intel, "create_llm_client", fake_create)
    app = _app(db_path, tmp_path)
    assert app._titles_generator()("s", "u") == "agy:gemini-4-hydrogen-medium"
    assert made == [("agy", "gemini-4-hydrogen-medium"), ("claude-cli", "sonnet")]


def test_cancel_scope_kills_running_cli_and_blocks_new_calls():
    import threading
    import time

    import pytest

    from cockpit import intel

    scope, result = intel.CancelScope(), {}

    def call():
        try:
            scope.run(["sleep", "30"])
        except intel.Cancelled:
            result["cancelled"] = True

    t = threading.Thread(target=call)
    start = time.monotonic()
    t.start()
    time.sleep(0.3)
    scope.cancel()
    t.join(5)
    assert result == {"cancelled": True} and time.monotonic() - start < 5
    with pytest.raises(intel.Cancelled):
        scope.run(["echo", "never"])


def test_group_titles_stop_skips_unsent_batches():
    import threading

    stop, calls = threading.Event(), []

    def gen(system, user):
        calls.append(user)
        stop.set()  # cancel during the first call
        return ""

    titles.MAX_WORKERS, saved = 1, titles.MAX_WORKERS
    try:
        titles.group_titles(Counter({f"t{i}": 1 for i in range(6)}), gen, batch_size=2, stop=stop)
    finally:
        titles.MAX_WORKERS = saved
    assert len(calls) == 1


def test_tui_escape_and_T_cancel_grouping(db_path, tmp_path):  # noqa: F811
    import threading

    with db.session(db_path) as con:
        db.upsert_jobspy_frame(con, frame({"company": "Co", "title": "Data Engineer", "link": "https://e.com/1"}))
    release = threading.Event()

    def slow(system, user):
        release.wait(5)
        return "Data Engineer | target | 1"

    async def drive() -> None:
        app = _app(db_path, tmp_path, None, slow)
        async with app.run_test() as pilot:
            for cancel_key in ("escape", "T"):
                await pilot.press("T")
                await pilot.pause()
                scope = app._titles_cancel
                assert scope is not None
                await pilot.press(cancel_key)
                await pilot.pause()
                assert scope.cancelled and app._titles_cancel is None
                release.set()
                await app.workers.wait_for_complete()
                await pilot.pause()
                assert not isinstance(app.screen, TitlesModal) and app._titles_cache is None
                release.clear()
            # Esc with nothing running is a no-op.
            assert app.check_action("cancel_titles", ()) is False

    asyncio.run(drive())


def test_group_titles_known_titles_skip_the_ai_and_roles_round_trip(tmp_path):
    counts = Counter({"data engineer": 3, "nurse": 1})
    path, key = tmp_path / "title_roles.json", titles.roles_key("t", "e")
    first = titles.group_titles(counts, lambda s, u: "Data Engineer | target | 1")  # nurse -> Unsorted
    titles.save_roles(path, key, first, {})
    known = titles.load_roles(path, key)
    assert known == {"data engineer": ("Data Engineer", "target")}  # Unsorted is retried, not saved
    assert titles.load_roles(path, titles.roles_key("other", "e")) == {}  # tiers changed -> regroup
    assert titles.load_roles(tmp_path / "missing.json", key) == {}

    sent = []
    groups = titles.group_titles(counts, lambda s, u: sent.append(u) or "Nurse | out | 1", known=known)
    assert sent == ["1. nurse"]  # only the unknown title goes to the AI
    assert {(g.role, g.tier, g.count) for g in groups} == {("Data Engineer", "target", 3), ("Nurse", "out", 1)}

    def boom(s, u):
        raise AssertionError("AI must not be called when every title is known")

    all_known = titles.group_titles(Counter({"data engineer": 3}), boom, known=known)
    assert [(g.role, g.count) for g in all_known] == [("Data Engineer", 3)]


def test_tui_titles_persist_across_restart_and_regroup(db_path, tmp_path):  # noqa: F811
    with db.session(db_path) as con:
        for i, t in enumerate(["Data Engineer", "Data Analyst"]):
            db.upsert_jobspy_frame(con, frame({"company": f"Co{i}", "title": t, "link": f"https://e.com/{i}"}))
    calls = []

    def fake(system, user):
        calls.append(user)
        return "\n".join(f"{line.split('. ', 1)[1].title()} | target | {line.split('.')[0]}" for line in user.splitlines())

    async def open_titles() -> set[tuple[str, int]]:
        app = _app(db_path, tmp_path, None, fake)
        async with app.run_test() as pilot:
            await pilot.press("T")
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert isinstance(app.screen, TitlesModal)
            return {(g.role, g.count) for g in app.screen.groups}

    async def drive() -> None:
        expected = {("Data Engineer", 1), ("Data Analyst", 1)}
        assert await open_titles() == expected and len(calls) == 1
        # A fresh app (restart) reads the saved roles: same breakdown, no AI call.
        assert await open_titles() == expected and len(calls) == 1
        # `r` regroups this view through the AI again.
        app = _app(db_path, tmp_path, None, fake)
        async with app.run_test() as pilot:
            await pilot.press("T")
            await app.workers.wait_for_complete()
            await pilot.pause()
            await pilot.press("r")
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert len(calls) == 2 and isinstance(app.screen, TitlesModal)

    asyncio.run(drive())
