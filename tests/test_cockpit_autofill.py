"""Tests for cockpit.autofill: form filling against trimmed fixtures, answers file, worker.

Browser tests use headless Chromium on `tests/fixtures/autofill/*.html` (trimmed from live
Greenhouse / Ashby / Lever forms, employer replaced by "Acme") and skip when Chromium cannot
launch. Every fixture counts clicks on its submit button and submit events in
`window.__submits`; the fill must leave it at 0. All data is a made-up person.
"""

from __future__ import annotations

import re
import threading
from pathlib import Path

import pytest
from pypdf import PdfWriter

from cockpit import autofill
from cockpit.autofill import (
    AnswerRule,
    AutofillWorker,
    FillReport,
    fill_page,
    form_url,
    load_answers,
)
from cockpit.prefill import AtsTarget, CandidateProfile

FIX = Path(__file__).parent / "fixtures" / "autofill"
JANE = CandidateProfile(
    first_name="Jane", last_name="Developer", email="jane@example.com", phone="+1 555 010 0199",
    location="Seattle, WA", linkedin="https://linkedin.com/in/jane-developer",
    portfolio_url="https://jane.example.com", github="https://github.com/jane-developer",
)  # fmt: skip


def _rules() -> list[AnswerRule]:
    return [AnswerRule(re.compile(m, re.IGNORECASE), a) for m, a in [
        (r"authorized to work", "Yes"),
        (r"require.{0,40}sponsorship", "No"),
        (r"how did you .*(hear|learn)", "Company website"),
        (r"relocate", "Yes"),
        (r"gender", "Decline to self-identify"),
    ]]  # fmt: skip


@pytest.fixture(scope="module")
def browser():
    sync_api = pytest.importorskip("playwright.sync_api")
    with sync_api.sync_playwright() as p:
        try:
            b = p.chromium.launch(headless=True)
        except Exception as e:  # noqa: BLE001 - Chromium not installed / cannot start
            pytest.skip(f"Chromium cannot launch: {e}")
        yield b
        b.close()


@pytest.fixture
def pdf(tmp_path: Path) -> Path:
    path = tmp_path / "Jane_Developer_Resume.pdf"
    w = PdfWriter()
    w.add_blank_page(width=612, height=792)
    with path.open("wb") as f:
        w.write(f)
    return path


@pytest.fixture
def open_fixture(browser):
    pages = []

    def _open(name: str):
        page = browser.new_page()
        pages.append(page)
        page.goto((FIX / name).as_uri())
        return page

    yield _open
    for p in pages:
        p.close()


def _target(ats: str) -> AtsTarget:
    return AtsTarget(ats, "acme", "1", f"https://example.test/{ats}")


def _run(page, ats, pdf, profile=JANE, cover_pdf=None, cover_text=None, answers=None):
    return fill_page(
        page,
        _target(ats),
        profile,
        pdf,
        cover_pdf,
        cover_text,
        _rules() if answers is None else answers,
    )


def _val(page, sel):
    return page.locator(sel).input_value()


def _file(page, sel):
    return page.eval_on_selector(sel, "e => e.files.length ? e.files[0].name : null")


# ───────────────────────── Greenhouse ─────────────────────────


def test_greenhouse_fill(open_fixture, pdf):
    page = open_fixture("greenhouse.html")
    rep = _run(
        page, "greenhouse", pdf, cover_pdf=pdf, cover_text="ignored when a PDF is used"
    )
    assert rep.blocked is None
    assert (
        _val(page, "#first_name"),
        _val(page, "#last_name"),
        _val(page, "#email"),
    ) == ("Jane", "Developer", "jane@example.com")
    assert _val(page, "#phone") == "+1 555 010 0199"
    assert (
        _val(page, "#q1") == JANE.linkedin
        and _val(page, "#q9") == JANE.github
        and _val(page, "#q10") == JANE.portfolio_url
    )
    assert _file(page, "#resume") == pdf.name and rep.resume == pdf.name
    assert _file(page, "#cover_letter") == pdf.name and rep.cover == pdf.name
    shown = {
        i: page.locator(f"#{i}")
        .locator("xpath=ancestor::*[contains(@class,'select-shell')]")
        .locator(".select__single-value")
        for i in ("q4", "q5", "q6")
    }
    assert [shown[i].inner_text() for i in ("q4", "q5", "q6")] == [
        "Yes",
        "No",
        "Company website",
    ]
    assert [(r, q) for r, q in rep.rules_fired if q.startswith("Are you legally")] == [
        (
            "authorized to work",
            "Are you legally authorized to work in the United States?",
        )
    ]
    assert len(rep.rules_fired) == 3
    assert rep.needs_you == [
        "Country", "Preferred Name", "Are you willing to relocate?",
        "Have you previously been employed at Acme for any length of time?", "Why Acme?",
    ]  # fmt: skip
    assert page.evaluate("window.__submits") == 0
    # needs-you fields are outlined orange so the owner can see them
    assert "orange" in page.locator("#q11").locator(
        "xpath=ancestor::*[contains(@class,'text-input-wrapper')]"
    ).get_attribute("style")


def test_greenhouse_ambiguous_choice_left_alone(open_fixture, pdf):
    page = open_fixture("greenhouse.html")
    rep = _run(page, "greenhouse", pdf)
    assert (
        "Are you willing to relocate?" in rep.needs_you
    )  # "Yes" fits two options → untouched, even though optional
    assert all(q != "Are you willing to relocate?" for _, q in rep.rules_fired)
    assert (
        page.locator("#q7")
        .locator("xpath=ancestor::*[contains(@class,'select-shell')]")
        .locator(".select__single-value")
        .count()
        == 0
    )
    assert page.locator("[role=listbox]").count() == 0  # menu closed again


def test_existing_value_not_overwritten_and_empty_profile_skipped(open_fixture, pdf):
    page = open_fixture("greenhouse.html")
    page.fill("#first_name", "Existing")
    rep = _run(
        page,
        "greenhouse",
        pdf,
        profile=CandidateProfile(
            first_name="Jane", last_name="Developer", email="jane@example.com"
        ),
    )
    assert _val(page, "#first_name") == "Existing" and "First Name" not in rep.filled
    assert (
        _val(page, "#q9") == "" and _val(page, "#phone") == ""
    )  # empty profile values: skipped
    assert (
        "GitHub" not in rep.needs_you and "GitHub" not in rep.filled
    )  # optional → not nagged
    assert "Phone" in rep.needs_you  # required and the profile has none
    assert page.evaluate("window.__submits") == 0


# ───────────────────────── Ashby ─────────────────────────


def test_ashby_fill(open_fixture, pdf):
    page = open_fixture("ashby.html")
    rep = _run(page, "ashby", pdf, cover_text="Dear Acme, hello.")
    assert rep.blocked is None
    assert (
        _val(page, "#_systemfield_name") == "Jane Developer"
        and _val(page, "#_systemfield_email") == "jane@example.com"
    )
    assert _val(page, "#phone") == "+1 555 010 0199" and _val(page, "#pref") == ""
    assert (
        _val(page, "[role=combobox]") == "Seattle, Washington, United States"
    )  # typeahead, single city match
    assert _file(page, "#_systemfield_resume") == pdf.name and rep.resume == pdf.name
    assert (
        page.evaluate("document.querySelector('input[type=file]').files.length") == 0
    )  # the "autofill from resume" box is not used
    pressed = page.eval_on_selector_all(
        "button[data-option][aria-pressed=true]",
        "bs => bs.map(b => b.closest('.ashby-application-form-field-entry').dataset.fieldPath + ':' + b.dataset.option)",
    )
    assert pressed == ["auth:yes", "spons:no"]
    assert page.locator("#gender-r2").is_checked()
    assert _val(page, "#cover") == "Dear Acme, hello." and rep.cover == "pasted"
    assert {r for r, _ in rep.rules_fired} == {
        "authorized to work",
        "require.{0,40}sponsorship",
        "gender",
    }
    assert rep.needs_you == [
        "When can you start a new role?",
        "Are you able to work from our US office three days per week?",
        "Applicant Arbitration Agreement Acknowledgement",
    ]
    assert not page.locator(
        "#arb-c0"
    ).is_checked()  # nothing answers it → left to the owner
    assert page.evaluate("window.__submits") == 0


# ───────────────────────── Lever ─────────────────────────


def test_lever_fill(open_fixture, pdf):
    page = open_fixture("lever.html")
    rep = _run(
        page, "lever", pdf, cover_pdf=pdf, cover_text="no cover field on this form"
    )
    assert rep.blocked is None
    assert (
        _val(page, "[name=name]") == "Jane Developer"
        and _val(page, "[name=email]") == "jane@example.com"
    )
    assert (
        _val(page, "[name=phone]") == "+1 555 010 0199"
        and _val(page, "[name=location]") == "Seattle, WA"
    )
    assert (
        _val(page, "[name='urls[LinkedIn]']") == JANE.linkedin
        and _val(page, "[name='urls[GitHub]']") == JANE.github
    )
    assert _val(page, "[name='urls[Portfolio]']") == JANE.portfolio_url
    assert (
        _val(page, "[name='urls[Twitter]']") == ""
        and _val(page, "[name='urls[Other]']") == ""
        and _val(page, "[name=org]") == ""
    )
    assert _file(page, "[name=resume]") == pdf.name
    assert (
        page.eval_on_selector("[name='cards[a][field0]']:checked", "e => e.value")
        == "Yes"
    )
    assert (
        page.eval_on_selector("[name='cards[a][field1]']:checked", "e => e.value")
        == "No"
    )
    assert (
        page.locator("[name='cards[a][field2]']").input_value() == ""
    )  # "what type of sponsorship?" is not the sponsorship rule
    assert rep.cover is None  # no cover-letter field on a Lever form
    # "How did you hear" has no "Company website" option: rule matched, nothing fits → reported
    assert page.locator("[name='cards[c][field0]']:checked").count() == 0
    assert len(rep.rules_fired) == 2
    assert rep.needs_you == [
        "Please state your full legal name",
        "How did you hear about us?",
        "Why Acme?",
    ]
    assert page.evaluate("window.__submits") == 0


# ───────────────────────── blocked pages ─────────────────────────


def test_challenge_page_is_captcha(open_fixture, pdf):
    page = open_fixture("challenge.html")
    rep = _run(page, "greenhouse", pdf)
    assert rep.blocked == "captcha" and not rep.filled
    assert "captcha" in rep.summary()


def test_hidden_recaptcha_on_a_real_form_is_not_a_block(open_fixture, pdf):
    page = open_fixture("greenhouse.html")
    page.evaluate(
        "document.body.insertAdjacentHTML('beforeend', '<iframe src=\"https://www.google.com/recaptcha/api2/bframe\" style=\"visibility:hidden;width:400px;height:500px\"></iframe>')"
    )
    assert _run(page, "greenhouse", pdf).blocked is None


def test_login_wall(browser, pdf):
    page = browser.new_page()
    page.set_content("<html><body><form><input type=password></form></body></html>")
    assert _run(page, "lever", pdf).blocked == "login"
    page.close()


def test_page_without_form_is_no_form(browser, pdf, monkeypatch):
    monkeypatch.setattr(autofill, "WAIT_MS", 400)
    page = browser.new_page()
    page.set_content("<html><body><h1>This position has been filled</h1></body></html>")
    rep = _run(page, "ashby", pdf)
    assert rep.blocked == "no-form"
    page.close()


def test_unsupported_ats_reports_unsupported(browser, pdf):
    page = browser.new_page()
    page.set_content("<html></html>")
    rep = fill_page(
        page,
        AtsTarget("generic", "x.com", "1", "https://x.com/jobs/1"),
        JANE,
        pdf,
        None,
        None,
        [],
    )
    assert rep.blocked == "unsupported"
    page.close()


# ───────────────────────── answers file ─────────────────────────


def test_load_answers_missing_file(tmp_path):
    assert load_answers(tmp_path / "nope.yml") == []


def test_load_answers_skips_bad_entries_and_keeps_valid(tmp_path):
    f = tmp_path / "answers.yml"
    f.write_text("""
- match: "authorized to work"
  answer: Yes
- match: "(unclosed"
  answer: "x"
- match: "no answer here"
- "just a string"
- match: "Sponsorship"
  answer: "No"
- match: "empty"
  answer: ""
""")
    rules = load_answers(f)
    assert [(r.pattern.pattern, r.answer) for r in rules] == [
        ("authorized to work", "Yes"),
        ("Sponsorship", "No"),
    ]  # bare Yes → "Yes"
    assert rules[0].pattern.search(
        "ARE YOU AUTHORIZED TO WORK HERE?"
    )  # case-insensitive


def test_load_answers_not_a_list_or_not_yaml(tmp_path):
    f = tmp_path / "a.yml"
    f.write_text("match: x\nanswer: y\n")
    assert load_answers(f) == []
    f.write_text("- [unterminated")
    assert load_answers(f) == []


def test_example_answers_file_parses():
    rules = load_answers(
        Path(__file__).parent.parent / "config/career_ops/config/answers.example.yml"
    )
    assert len(rules) == 3 and rules[0].answer == "Yes"
    assert rules[1].pattern.search("Do you now or in the future require sponsorship?")
    assert not rules[1].pattern.search("If yes, what type of sponsorship?")


# ───────────────────────── form_url / target_for ─────────────────────────


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (
            "https://job-boards.greenhouse.io/acme/jobs/123",
            "https://job-boards.greenhouse.io/acme/jobs/123",
        ),
        (
            "https://boards.greenhouse.io/acme/jobs/123?gh_src=abc",
            "https://boards.greenhouse.io/acme/jobs/123?gh_src=abc",
        ),
        (
            "https://jobs.lever.co/acme/abc-123",
            "https://jobs.lever.co/acme/abc-123/apply",
        ),
        (
            "https://jobs.lever.co/acme/abc-123/",
            "https://jobs.lever.co/acme/abc-123/apply",
        ),
        (
            "https://jobs.lever.co/acme/abc-123/apply",
            "https://jobs.lever.co/acme/abc-123/apply",
        ),
        (
            "https://jobs.lever.co/acme/abc-123?lever-source=Site",
            "https://jobs.lever.co/acme/abc-123/apply?lever-source=Site",
        ),
        (
            "https://jobs.lever.co/acme/abc-123/apply?lever-source=Site",
            "https://jobs.lever.co/acme/abc-123/apply?lever-source=Site",
        ),
        (
            "https://jobs.ashbyhq.com/acme/abc-123",
            "https://jobs.ashbyhq.com/acme/abc-123/application",
        ),
        (
            "https://jobs.ashbyhq.com/acme/abc-123/application",
            "https://jobs.ashbyhq.com/acme/abc-123/application",
        ),
        (
            "https://jobs.ashbyhq.com/acme/abc-123?utm_source=x",
            "https://jobs.ashbyhq.com/acme/abc-123/application?utm_source=x",
        ),
    ],
)
def test_form_url(url, expected):
    target = autofill.target_for(url)
    assert target is not None and target.ats in autofill.SUPPORTED
    assert form_url(target) == expected


def test_target_for_greenhouse_embed_and_unsupported():
    t = autofill.target_for("https://boards.greenhouse.io/embed/job_app?token=8368989")
    assert (
        t is not None
        and t.ats == "greenhouse"
        and form_url(t) == "https://boards.greenhouse.io/embed/job_app?token=8368989"
    )
    g = autofill.target_for("https://careers.example.com/jobs/42")
    assert g is not None and g.ats == "generic"
    assert autofill.target_for("not a url") is None


# ───────────────────────── report ─────────────────────────


def test_summary_lines():
    r = FillReport(
        "lever", "u", filled=["a"] * 9, needs_you=["Why Acme?", "B", "C", "D"]
    )
    assert r.summary() == 'Filled 9 fields · 4 need you: "Why Acme?", "B", "C", +1 more'
    assert FillReport("lever", "u", filled=["a"]).summary() == "Filled 1 fields"
    assert "unsupported" in FillReport("generic", "u", blocked="unsupported").summary()


# ───────────────────────── worker ─────────────────────────
# These use real headless Chromium on a file:// fixture with `target_for` / `form_url` patched,
# because `detect_ats` rejects file:// URLs (no host).


def _worker_job(worker, url, results, *, profile=JANE):
    done = threading.Event()
    worker.submit(
        url, profile, None, None, None, [], lambda r: (results.append(r), done.set())
    )
    return done


def test_worker_fills_in_order_survives_errors_and_relaunches(
    browser, tmp_path, monkeypatch
):
    gh = (FIX / "greenhouse.html").as_uri()
    monkeypatch.setattr(
        autofill,
        "target_for",
        lambda u: _target("greenhouse") if u == "gh" else autofill.detect_ats(u),
    )
    monkeypatch.setattr(autofill, "form_url", lambda t: gh)
    real_fill = autofill.fill_page
    calls = []

    def flaky(page, *a):
        calls.append(1)
        if len(calls) == 2:
            raise RuntimeError("boom")
        return real_fill(page, *a)

    monkeypatch.setattr(autofill, "fill_page", flaky)
    idle = AutofillWorker(tmp_path / "idle", headless=True)
    idle.close()  # nothing started: safe, and safe twice
    idle.close()
    w = AutofillWorker(tmp_path / "profile", headless=True)
    out: list[FillReport] = []
    try:
        # 1. a good job: filled, and the tab is left open
        assert _worker_job(w, "gh", out).wait(60)
        assert out[0].blocked is None and "First Name" in out[0].filled
        assert [p.url for p in w._ctx.pages] == [gh]
        # 2. an exception while filling becomes a report; the thread lives on
        assert _worker_job(w, "gh", out).wait(60)
        assert out[1].blocked == "error: boom"
        # 3. an unsupported URL still opens a tab and says so
        other = (FIX / "challenge.html").as_uri()
        assert _worker_job(w, other, out).wait(60)
        assert out[2].blocked == "unsupported" and other in [
            p.url for p in w._ctx.pages
        ]
        # 4. the owner quits the browser: the next job starts a fresh one
        closed = threading.Event()
        w.submit(
            other, JANE, None, None, None, [], lambda r: (w._ctx.close(), closed.set())
        )
        assert closed.wait(60)
        assert _worker_job(w, "gh", out).wait(60)
        assert out[3].blocked is None
    finally:
        w.close()
        w.close()  # twice: safe
    assert not w._thread.is_alive()


# ───────────────────────── static no-submit guard ─────────────────────────


def test_source_never_submits():
    """Blunt check on the source text: no submit call, Enter press, or submit-selector click."""
    src = (Path(autofill.__file__)).read_text("utf-8")
    code = "\n".join(
        line for line in src.splitlines() if not line.lstrip().startswith("#")
    )
    code = re.sub(r'"""[\s\S]*?"""', "", code)  # docstrings explain the rules in prose
    assert not re.search(
        r"\.submit\(|\.requestSubmit|\.press\(\s*[\"'](Enter|Return)", code
    )
    assert not re.search(
        r"type\s*=\s*[\"']?submit[\"']?\]?[\"']?\s*\)?\s*\.click", code
    )
    assert not re.search(r"\.(dispatch_event|check|tap|dblclick)\(", code)
    clicks = [line.strip() for line in code.splitlines() if ".click(" in line]
    assert clicks == [
        "loc.click(timeout=ACTION_MS)",
        "opts.nth(k).click(timeout=ACTION_MS)",
    ]
    assert "e.type === 'submit'" in code  # `_click` refuses submit-type controls
