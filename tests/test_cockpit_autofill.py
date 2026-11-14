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
        "Country", "Preferred Name",
        "Have you previously been employed at Acme for any length of time?", "Why Acme?",
    ]  # fmt: skip
    assert rep.unmatched == [
        "Are you willing to relocate?"
    ]  # optional, rule fits no option
    assert page.evaluate("window.__submits") == 0
    # needs-you fields are outlined orange so the owner can see them
    assert "orange" in page.locator("#q11").locator(
        "xpath=ancestor::*[contains(@class,'text-input-wrapper')]"
    ).get_attribute("style")


def test_greenhouse_ambiguous_choice_left_alone(open_fixture, pdf):
    page = open_fixture("greenhouse.html")
    rep = _run(page, "greenhouse", pdf)
    assert rep.unmatched == [
        "Are you willing to relocate?"
    ]  # "Yes" fits two options → untouched; optional, so not in needs_you
    assert "Are you willing to relocate?" not in rep.needs_you
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
        "When did you begin your current degree program? Please answer in MM/YYYY format (e.g. 08/2022)",
        "What degree are you currently pursuing?",
        "Why Acme?",
    ]
    assert page.evaluate("window.__submits") == 0


# ───────────────────────── rules that need more than typing ─────────────────────────


def _r(pattern: str, answer) -> AnswerRule:
    return AnswerRule(re.compile(pattern, re.IGNORECASE), answer)


def _checked(page, name):
    return page.eval_on_selector_all(
        f"[name='{name}']:checked", "es => es.map(e => e.value)"
    )


def test_list_answer_ticks_each_exact_option(open_fixture, pdf):
    page = open_fixture("lever.html")
    rep = _run(
        page, "lever", pdf,
        answers=[_r("degrees have you already", ["Associate degree", "Bachelor's degree"])],
    )  # fmt: skip
    # curly apostrophe on the page, straight one in the rule
    assert _checked(page, "cards[g][field0]") == [
        "Associate degree",
        "Bachelor’s degree",
    ]
    assert _checked(page, "cards[h][field0]") == []  # another checkbox group: untouched
    assert [q for _, q in rep.rules_fired] == [
        "Which degrees have you already completed, if any? Select all that apply."
    ]
    assert page.evaluate("window.__submits") == 0


def test_list_with_one_unmatched_item_leaves_the_whole_question(open_fixture, pdf):
    page = open_fixture("lever.html")
    rep = _run(
        page, "lever", pdf,
        answers=[_r("degrees have you already", ["Bachelor's degree", "PhD"])],
    )  # fmt: skip
    assert _checked(page, "cards[g][field0]") == []  # no partial ticking
    assert rep.unmatched == [  # optional question: reported apart from needs_you
        "Which degrees have you already completed, if any? Select all that apply."
    ]
    assert rep.rules_fired == []


def test_list_answer_is_ignored_on_a_radio_group_and_on_text(open_fixture, pdf):
    page = open_fixture("lever.html")
    rep = _run(
        page, "lever", pdf,
        answers=[_r("what degree", ["Associate degree", "Other"]), _r("begin your", ["09/2023"])],
    )  # fmt: skip
    assert _checked(page, "cards[f][field0]") == []
    assert page.locator("[name='cards[e][field0]']").input_value() == ""
    assert "What degree are you currently pursuing?" in rep.needs_you


def test_single_exact_checkbox_option(open_fixture, pdf):
    page = open_fixture("lever.html")
    _run(page, "lever", pdf, answers=[_r("student groups", "Hackathons")])
    assert _checked(page, "cards[h][field0]") == ["Hackathons"]


def test_curly_apostrophe_option_matches_straight_answer(open_fixture, pdf):
    page = open_fixture("lever.html")
    rep = _run(page, "lever", pdf, answers=[_r("what degree", "Bachelor's degree")])
    assert _checked(page, "cards[f][field0]") == ["Bachelor’s degree"]
    assert "What degree are you currently pursuing?" not in rep.needs_you


def test_legal_name_second_box_takes_rule_and_contact_name_keeps_profile(
    open_fixture, pdf
):
    page = open_fixture("lever.html")
    rep = _run(page, "lever", pdf, answers=[_r("legal name", "Jane Q. Public")])
    assert _val(page, "[name=name]") == "Jane Developer"  # "Full name" → profile
    assert (
        _val(page, "[name='cards[b][field0]']") == "Jane Q. Public"
    )  # extra box → rule
    assert "Please state your full legal name" not in rep.needs_you


@pytest.mark.parametrize(
    ("label", "name", "expected"),
    [
        ("Preferred Name", "preferred_first_name", None),
        ("Preferred first name", "first_name", None),
        ("Nickname", "", None),
        ("Name Pronunciation", "", None),
        ("First Name", "", "first_name"),
        ("", "last_name", "last_name"),
        (
            "Legal Name",
            "",
            "full_name",
        ),  # the one name box on a form is the contact name
    ],
)
def test_contact_key_does_not_swallow_preferred_name(label, name, expected):
    q = {"label": label, "name": name, "id": "", "ac": ""}
    assert autofill._contact_key(q, set()) == expected


def test_preferred_name_goes_to_rule_not_contact(open_fixture, pdf):
    page = open_fixture("greenhouse.html")
    _run(page, "greenhouse", pdf, answers=[_r("preferred name", "Janie")])
    assert _val(page, "#q3") == "Janie" and _val(page, "#first_name") == "Jane"


def test_country_combobox_through_a_rule(open_fixture, pdf):
    page = open_fixture("greenhouse.html")
    rep = _run(page, "greenhouse", pdf, answers=[_r("^country$", "United States")])
    shown = (
        page.locator("#country")
        .locator("xpath=ancestor::*[contains(@class,'select-shell')]")
        .locator(".select__single-value")
    )
    assert shown.inner_text() == "United States"
    assert "Country" not in rep.needs_you and "Country" in rep.filled
    assert page.evaluate("window.__submits") == 0


def test_plain_text_date_through_a_rule(open_fixture, pdf):
    page = open_fixture("lever.html")
    rep = _run(page, "lever", pdf, answers=[_r("begin your current degree", "09/2023")])
    assert _val(page, "[name='cards[e][field0]']") == "09/2023"
    assert not any(q.startswith("When did you begin") for q in rep.needs_you)


def test_date_picker_input_keeps_a_parseable_date_and_reports_an_unparseable_one(
    open_fixture, pdf
):
    page = open_fixture("ashby.html")
    rep = _run(page, "ashby", pdf, answers=[_r("when can you start", "01/04/2027")])
    assert _val(page, ".ashby-application-form-input-date") == "01/04/2027"
    assert "When can you start a new role?" not in rep.needs_you
    # the widget drops text it cannot parse on blur → the question is reported, not claimed
    page = open_fixture("ashby.html")
    rep = _run(
        page, "ashby", pdf, answers=[_r("when can you start", "after my notice")]
    )
    assert _val(page, ".ashby-application-form-input-date") == ""
    assert "When can you start a new role?" in rep.needs_you
    assert page.evaluate("window.__submits") == 0


def test_greenhouse_required_resume_is_flagged_only_when_missing(open_fixture, pdf):
    page = open_fixture("greenhouse.html")
    rep = fill_page(page, _target("greenhouse"), JANE, None, None, None, _rules())
    assert "Resume/CV" in rep.needs_you and "Cover Letter" not in rep.needs_you
    assert "orange" in page.locator("#resume").locator(
        "xpath=ancestor::*[@role='group'][1]"
    ).get_attribute("style")
    page = open_fixture("greenhouse.html")
    assert "Resume/CV" not in _run(page, "greenhouse", pdf).needs_you


def test_needs_you_holds_required_only(open_fixture, pdf):
    page = open_fixture("greenhouse.html")
    rep = _run(page, "greenhouse", pdf)
    for optional in (
        "LinkedIn Profile",
        "Current Company",
        "Are you willing to relocate?",
    ):
        assert optional not in rep.needs_you
    assert rep.unmatched == [
        "Are you willing to relocate?"
    ]  # optional but a rule matched


def test_list_rules_load_and_bad_lists_are_skipped(tmp_path):
    f = tmp_path / "a.yml"
    f.write_text(
        "- {match: a, answer: [x, y]}\n- {match: b, answer: []}\n"
        "- {match: c, answer: [x, {k: 1}]}\n- {match: d, answer: [Yes, 3]}\n"
    )
    assert [(r.pattern.pattern, r.answer) for r in load_answers(f)] == [
        ("a", ["x", "y"]),
        ("d", ["Yes", "3"]),
    ]


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


EXAMPLE = Path(__file__).parent.parent / "config/career_ops/config/answers.example.yml"


def _first_rule(label: str) -> AnswerRule | None:
    return next((r for r in load_answers(EXAMPLE) if r.pattern.search(label)), None)


def test_example_answers_file_parses():
    rules = load_answers(EXAMPLE)
    assert len(rules) >= 15 and rules[0].answer == "Yes"
    assert rules[1].pattern.search("Do you now or in the future require sponsorship?")
    assert not rules[1].pattern.search("If yes, what type of sponsorship?")
    assert any(isinstance(r.answer, list) for r in rules)  # the tick-all example
    # every rule in the file loaded (a typo in a regex would silently drop it)
    assert len(rules) == len(
        re.findall(r"^- match:", EXAMPLE.read_text(), re.MULTILINE)
    )


@pytest.mark.parametrize(
    ("label", "answer"),
    [
        ("Country", "United States"),
        ("Which U.S. State or Canadian Province do you reside in?", "California"),
        ("Please state your full legal name:", "Jane Q. Public"),
        ("Preferred Name (if applicable)", "Jane"),
        ("Pronouns", "they/them"),
        ("How did you hear about us?", "LinkedIn"),
        ("How did you first learn about Affirm as an employer?", "LinkedIn"),
        ("Have you previously been employed at Affirm for any length of time?", "No"),
        ("Current school or university:", "Example State University"),
        ("What degree are you currently pursuing?", "Bachelor's degree"),
        ("What is your field of study?", "Computer Science"),
        ("If you are enrolled or graduated within 24 months, what is your current or final GPA?", "3.7"),
        ("When did you begin your current degree program? Please answer in MM/YYYY format (e.g. 08/2022)", "09/2023"),
        ("When do you expect to complete your current degree program? Please answer in MM/YYYY format (e.g. 08/2022)", "05/2027"),
        ("When can you start a new role?", "01/04/2027"),
        ("Are you willing to relocate?", "Yes"),
        ("Are you able to work from our US office three days per week?", "Yes"),
        ("Are you at least 18 years of age?", "Yes"),
        ("Are you subject to a non-compete agreement?", "No"),
        ("Which degrees have you already completed, if any? Select all that apply.", ["Bachelor's degree"]),
    ],
)  # fmt: skip
def test_example_rules_catch_the_common_questions(label, answer):
    rule = _first_rule(label)
    assert rule is not None and rule.answer == answer


@pytest.mark.parametrize(
    "label",
    [
        "Optional Comments on GPA",
        "If you selected Other, please specify below:",
        "Name Pronunciation",
        "Which of our office(s) would you be willing to work from/relocate to? Select all that apply",
        "Do you require relocation assistance?",
        "Have you ever worked in a startup?",
        "Do you have at least 18 months of experience?",
        "Do you currently live in the United States?",
        "Tell us about your two strongest technical accomplishments from school",
        "Do you have 3 years of experience completing projects?",
        "Why do you want to work at Acme?",
        "Salary expectations",
        "Gender",
    ],
)
def test_example_rules_do_not_catch_follow_ups_or_company_questions(label):
    assert _first_rule(label) is None


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
