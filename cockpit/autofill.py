"""Application-form autofill: open an employer's form, fill the owner's details, STOP.

Intent: save the owner the typing on Greenhouse, Ashby and Lever forms. The owner reviews
the page and presses Submit themselves.
Contract:
- Nothing here submits. No code path clicks a submit button, presses Enter, or calls a
  form-submit API; clicks are limited to dropdown controls and to radio / checkbox / option
  items inside a question (`_click` also refuses any submit-type control).
- A question is filled only from the profile's contact fields or from a rule in the owner's
  answers file. Everything else is left untouched; required leftovers are listed in
  `FillReport.needs_you` and outlined in orange on the page.
- No CAPTCHA solving, no login automation, no stealth. A challenge or login wall becomes
  `blocked="captcha"` / `"login"`; the tab stays open for the owner.
Limits: selectors target the form markup seen in mid-2026 (React Select on Greenhouse,
Ashby's own components, Lever's server-rendered form) and will drift. Custom dropdowns are
driven by their ARIA roles (`combobox`, `option`), not by class names. Greenhouse's
"Enter manually" cover-letter box needs a button click this module does not make.
"""

from __future__ import annotations

import queue
import re
import threading
import time
import urllib.parse
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from cockpit.prefill import AtsTarget, CandidateProfile, detect_ats

try:
    from playwright.sync_api import Error as PWError
except ImportError:  # pragma: no cover - playwright is in requirements.txt
    PWError = Exception  # type: ignore[assignment,misc]

SUPPORTED = ("greenhouse", "ashby", "lever")

WAIT_MS = (
    8000  # how long to wait for a form (or a challenge) to appear after navigation
)
IDLE_MS = 6000
ACTION_MS = 3000  # bound on any single fill / click / option wait
UPLOAD_SETTLE_MS = 6000  # bound on Lever's "analyzing resume" step
FILL_BUDGET_S = 60.0  # after this, remaining questions are reported, not attempted
OUTLINE = "2px solid orange"
DRAFT_OUTLINE = "2px solid #3b82f6"  # blue: written by the drafter, review before submitting
MAX_DRAFTS = 8  # per form; each one is a model call, made one after another
_QUESTIONISH = re.compile(r"\?|^\s*(why|how|what|describe|tell us|explain|share)\b", re.IGNORECASE)


# ───────────────────────── answers file ─────────────────────────


@dataclass(frozen=True)
class AnswerRule:
    """`answer` is text to type / an option to pick, or a list of options to tick (a
    multi-select checkbox question). A list on any other kind of question is not applied.
    """

    pattern: re.Pattern[str]
    answer: str | list[str]


def _scalar(v: Any) -> str:
    """A YAML scalar as answer text ("" when it is not one); booleans become Yes/No."""
    if isinstance(v, bool):
        return "Yes" if v else "No"
    return str(v).strip() if isinstance(v, (str, int, float)) else ""


def load_answers(path: Path) -> list[AnswerRule]:
    """Read the owner's answers file: a YAML list of `{match: <regex>, answer: <text|list>}`.

    Returns the valid rules in file order; `[]` when the file is missing or unreadable.
    A bad regex or malformed entry is skipped, never fatal. Patterns are case-insensitive.
    YAML turns an unquoted Yes/No into a boolean, so booleans map back to "Yes"/"No".
    """
    try:
        data = yaml.safe_load(Path(path).read_text("utf-8"))
    except (OSError, yaml.YAMLError):
        return []
    rules: list[AnswerRule] = []
    for entry in data if isinstance(data, list) else []:
        if not isinstance(entry, dict):
            continue
        match, answer = entry.get("match"), entry.get("answer")
        if isinstance(answer, list):
            # A list answer is for ticking several boxes; every item must be a usable string.
            items = [_scalar(a) for a in answer]
            answer = items if items and all(items) else None
        else:
            answer = _scalar(answer)
        if not isinstance(match, str) or not match or not answer:
            continue
        try:
            rules.append(AnswerRule(re.compile(match, re.IGNORECASE), answer))
        except re.error:
            continue
    return rules


# ───────────────────────── report ─────────────────────────


@dataclass
class FillReport:
    ats: str
    url: str
    filled: list[str] = field(default_factory=list)
    needs_you: list[str] = field(default_factory=list)  # required and still empty
    unmatched: list[str] = field(
        default_factory=list
    )  # optional; a rule matched, nothing fit
    rules_fired: list[tuple[str, str]] = field(default_factory=list)
    blocked: str | None = None
    resume: str | None = None
    cover: str | None = None
    drafted: list[str] = field(default_factory=list)  # free-text boxes filled by the drafter

    def summary(self) -> str:
        """One line for the status bar: what was filled, what still needs the owner."""
        if self.blocked:
            return f"Not filled ({self.blocked}) · tab left open for you"
        line = f"Filled {len(self.filled)} fields"
        if self.drafted:
            line += f" · {len(self.drafted)} drafted, review them"
        if self.needs_you:
            shown = ", ".join(f'"{_short(x)}"' for x in self.needs_you[:3])
            more = len(self.needs_you) - 3
            line += f" · {len(self.needs_you)} need you: {shown}" + (
                f", +{more} more" if more > 0 else ""
            )
        return line


def _short(text: str, n: int = 40) -> str:
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


# ───────────────────────── per-site table ─────────────────────────


@dataclass(frozen=True)
class Site:
    """Everything that differs between ATSes; the fill logic itself is shared."""

    form: str  # CSS that is present when the application form has rendered
    root: str  # CSS for one question's wrapper
    label: str  # CSS for the question text inside a wrapper ("" = fall back to legend/label)
    resume: str  # CSS for the resume file input
    busy: str = ""  # CSS shown while the site parses an uploaded resume


SITES: dict[str, Site] = {
    # React Select combos are `[role=combobox]` inside `.select__container`; phone and the
    # country picker are separate wrappers inside `fieldset.phone-input`.
    "greenhouse": Site(
        form="form#application-form, form:has(#first_name)",
        root=".text-input-wrapper, .select__container, fieldset:not(.phone-input), [role=group]",
        label="",
        resume="#resume",
    ),
    # Ashby has no <form>; the first file input on the page is the "autofill from resume"
    # widget, so the resume is addressed by its system-field id instead.
    "ashby": Site(
        form=".ashby-application-form-field-entry",
        root=".ashby-application-form-field-entry",
        label=".ashby-application-form-question-title",
        resume="#_systemfield_resume",
    ),
    # Lever parses an uploaded resume server-side and may pre-fill fields, so upload first.
    "lever": Site(
        form="input[name=email]",
        root="li.application-question",
        label=".application-label",
        resume="input[name=resume]",
        busy=".resume-upload-working",
    ),
}

# (key, label regex, name/id/autocomplete regex). Order matters: first match per field wins,
# and each key fills at most one field.
_CONTACT: tuple[tuple[str, str, str], ...] = (
    ("first_name", r"^(legal )?first name\b|^given name", r"first[_-]?name|given-name"),
    (
        "last_name",
        r"^(legal )?last name\b|^family name|^surname",
        r"last[_-]?name|family-name",
    ),
    (
        "full_name",
        r"^((full|legal|your)\s+)+name$|^name$",
        r"^name$|^_systemfield_name$",
    ),
    ("email", r"e-?mail", r"e-?mail"),
    ("phone", r"^(mobile |cell )?(phone|mobile|telephone)\b", r"^phone$|^tel$"),
    (
        "location",
        r"^(current )?(location|city)\b|where are you (currently )?(located|based)",
        r"^location$|_systemfield_location",
    ),
    ("linkedin", r"linkedin", r"urls\[linkedin\]"),
    ("github", r"github", r"urls\[github\]"),
    ("portfolio_url", r"portfolio|personal (web)?site|^website", r"urls\[portfolio\]"),
)
_CONTACT_RE = [
    (k, re.compile(a, re.IGNORECASE), re.compile(b, re.IGNORECASE))
    for k, a, b in _CONTACT
]

_NOT_CONTACT = re.compile(r"preferred|nick ?name|pronounc|pronoun", re.IGNORECASE)
_COVER_FILE = 'input[type=file][id*="cover" i], input[type=file][name*="cover" i]'
_COVER_LABEL = re.compile(r"cover letter", re.IGNORECASE)

# ───────────────────────── URLs ─────────────────────────


def target_for(url: str) -> AtsTarget | None:
    """`detect_ats`, plus the older `boards.greenhouse.io/embed/job_app?token=` form URL.

    Returns None when the URL is unparseable; a "generic" target for unsupported sites.
    """
    t = detect_ats(url)
    if t is not None and t.ats == "generic":
        p = urllib.parse.urlparse(url.strip())
        token = urllib.parse.parse_qs(p.query).get("token", [""])[0]
        if (
            "greenhouse.io" in p.netloc.lower()
            and p.path.startswith("/embed/job_app")
            and token
        ):
            return AtsTarget("greenhouse", "embed", token, t.original_url)
    return t


def form_url(target: AtsTarget) -> str:
    """The URL that shows the form: Lever `/apply`, Ashby `/application`, Greenhouse as is.

    Query string and fragment are kept; a URL already ending in the suffix is unchanged.
    """
    suffix = {"lever": "/apply", "ashby": "/application"}.get(target.ats)
    if not suffix:
        return target.original_url
    p = urllib.parse.urlparse(target.original_url.strip())
    path = p.path.rstrip("/")
    if not path.endswith(suffix):
        path += suffix
    return urllib.parse.urlunparse(p._replace(path=path))


# ───────────────────────── in-page scripts ─────────────────────────

# Shared by extraction and re-verification so "has a value" means the same in both.
_HAS_VALUE_JS = """
const hasValue = (root) => {
  const c = root.querySelector('[data-af-c]');
  if (root.querySelector('input[type=file]')?.files?.length) return true;
  if (root.querySelector('button[data-option][aria-pressed=true]')) return true;
  if (root.querySelector('input[type=radio]:checked, input[type=checkbox]:checked')) return true;
  if (root.querySelector('[class*=single-value], [class*=multi-value]')) return true;
  return !!(c && c.value && c.value.trim() !== '');
};
"""

# One pass over the form: tags each question's wrapper (`data-af`), its main control
# (`data-af-c`) and its option items (`data-af-o`), and returns plain data about them.
_EXTRACT_JS = """(cfg) => {""" + _HAS_VALUE_JS + """
  const vis = (e) => !!(e.offsetWidth || e.offsetHeight || e.getClientRects().length)
                     && getComputedStyle(e).visibility !== 'hidden';
  const txt = (e) => (e ? e.textContent : '').replace(/\\s+/g, ' ').trim();
  const strip = (s) => s.replace(/[\\s*\\u2731]+$/, '').trim();
  const roots = [...document.querySelectorAll(cfg.root)].filter((r) => !r.querySelector(cfg.root));
  const out = [];
  roots.forEach((root) => {
    const q = {label: '', kind: '', options: [], required: false, visible: true, name: '', id: '', ac: ''};
    // 1. Pick the control: the first kind found wins, in this order.
    let ctrl = null, items = [];
    const files = root.querySelector('input[type=file]');
    const combo = root.querySelector('[role=combobox]:not([type=search])');
    const btns = [...root.querySelectorAll('button[data-option]')];
    const radios = [...root.querySelectorAll('input[type=radio]')];
    const checks = [...root.querySelectorAll('input[type=checkbox]')];
    const sel = root.querySelector('select');
    const texts = [...root.querySelectorAll(
      'textarea, input:not([type=hidden]):not([type=radio]):not([type=checkbox]):not([type=file])' +
      ':not([type=search]):not([type=submit]):not([type=button]):not([role=combobox])')]
      .filter((e) => e.getAttribute('aria-hidden') !== 'true' && e.tabIndex !== -1);
    if (combo) { q.kind = 'combobox'; ctrl = combo; }
    else if (btns.length) { q.kind = 'buttons'; items = btns; }
    else if (radios.length) { q.kind = 'radio'; items = radios; }
    else if (checks.length) { q.kind = 'checkbox'; items = checks; }
    else if (sel) { q.kind = 'select'; ctrl = sel; }
    else if (texts.length) { q.kind = 'text'; ctrl = texts[0]; }
    else if (files) { q.kind = 'file'; ctrl = files; }
    else return;
    // 2. Tag the control and option items; read option text from the nearest label.
    if (ctrl) ctrl.setAttribute('data-af-c', out.length);
    items.forEach((el, k) => {
      el.setAttribute('data-af-o', k);
      let t;
      if (el.tagName === 'BUTTON') t = txt(el);
      else {
        const l = (el.id && root.querySelector('label[for="' + CSS.escape(el.id) + '"]')) || el.closest('label');
        t = txt(l) || txt(el.nextElementSibling) || el.value || '';
      }
      q.options.push(t);
    });
    if (q.kind === 'select') {
      q.options = [...sel.options].filter((o) => o.value !== '').map((o) => o.textContent.trim());
    }
    // 3. Label: site selector, else group label / legend, else the first non-option label.
    let labelEl = cfg.label && root.querySelector(cfg.label);
    if (!labelEl) {
      const by = root.getAttribute('aria-labelledby');
      labelEl = (by && document.getElementById(by)) || root.querySelector('legend') ||
        [...root.querySelectorAll('label')].find((l) => {
          const t = l.htmlFor && document.getElementById(l.htmlFor);
          return !(t && (t.type === 'radio' || t.type === 'checkbox')) && !l.classList.contains('visually-hidden');
        });
    }
    const raw = txt(labelEl);
    q.label = strip(raw);
    q.required = /[*\\u2731]\\s*$/.test(raw) || !!root.querySelector('[required], [aria-required=true]') ||
      !!(labelEl && /required/i.test(labelEl.className + ' ' + (labelEl.querySelector('[class*=required]') ? 'required' : '')));
    if (ctrl) {
      q.name = ctrl.getAttribute('name') || ''; q.id = ctrl.id || '';
      q.ac = ctrl.getAttribute('autocomplete') || '';
      q.visible = q.kind === 'file' || vis(ctrl);
      q.long = ctrl.tagName === 'TEXTAREA';
      q.date = ctrl.type === 'date' || /(^|[\\s_-])date([\\s_-]|$)/i.test(ctrl.className) ||
        !!ctrl.closest('.react-datepicker-wrapper');
    }
    root.setAttribute('data-af', out.length);
    q.hasValue = hasValue(root);
    q.i = out.length;
    out.push(q);
  });
  return out;
}"""

# Which marker, if any, is on the page right now: the form, a challenge, or a login wall.
_STATE_JS = """(formSel) => {
  const vis = (e) => !!(e.offsetWidth || e.offsetHeight) && getComputedStyle(e).visibility !== 'hidden';
  if (document.querySelector(formSel)) return 'form';
  if (/just a moment|attention required|verify you are human|security check/i.test(document.title) ||
      document.querySelector('#challenge-form, #cf-challenge-running, .cf-turnstile') ||
      [...document.querySelectorAll(
        'iframe[src*="hcaptcha"], iframe[src*="recaptcha/api2/bframe"], iframe[src*="challenges.cloudflare.com"]')]
        .some((f) => vis(f) && f.offsetWidth > 100 && f.offsetHeight > 100)) return 'captcha';
  if ([...document.querySelectorAll('input[type=password]')].some(vis)) return 'login';
  return null;
}"""

_VALUE_JS = (
    "(i) => { "
    + _HAS_VALUE_JS
    + " const r = document.querySelector(`[data-af='${i}']`); return !!r && hasValue(r); }"
)


# ───────────────────────── small helpers ─────────────────────────


def _norm(text: str) -> str:
    """Lower-case, curly quotes and en/em dashes straightened, spaces collapsed."""
    t = text.translate(
        str.maketrans("\u2018\u2019\u201c\u201d\u2013\u2014", "''\"\"--")
    )
    return re.sub(r"\s+", " ", t).strip().lower()


def _pick(options: list[str], answer: str) -> int | None:
    """Index of the option for `answer`: exact (case-insensitive), else the single option
    containing it as whole words. Returns None for no match or for more than one."""
    options = [_norm(o) for o in options]
    a = _norm(answer)
    exact = [i for i, o in enumerate(options) if o == a]
    if exact:
        return exact[0] if len(exact) == 1 else None
    word = re.compile(rf"(?<!\w){re.escape(a)}(?!\w)", re.IGNORECASE)
    loose = [i for i, o in enumerate(options) if word.search(o)]
    return loose[0] if len(loose) == 1 else None


def _click(loc: Any) -> None:
    """The only click in this module. Refuses submit-type controls, so even a mis-tagged
    element cannot submit a form."""
    submit = loc.evaluate(
        "e => (e.matches('button, input[type=submit], input[type=image]') && e.type === 'submit' && !!e.form)",
        timeout=ACTION_MS,
    )
    if submit:
        raise PWError("refusing to click a submit control")
    loc.click(timeout=ACTION_MS)


def _qloc(page: Any, q: dict) -> Any:
    return page.locator(f'[data-af-c="{q["i"]}"]')


def _item(page: Any, q: dict, k: int) -> Any:
    """Click target for option `k`: its label when there is one (the input is often hidden)."""
    root = page.locator(f'[data-af="{q["i"]}"]')
    el = root.locator(f'[data-af-o="{k}"]')
    if q["kind"] == "buttons":
        return el
    label = el.locator("xpath=ancestor::label[1]")
    if label.count() == 0 and q["id"] == "":
        eid = el.get_attribute("id", timeout=ACTION_MS)
        label = root.locator(f'label[for="{eid}"]') if eid else label
    return label.first if label.count() else el


def _set_choice(page: Any, q: dict, answer: str | list[str]) -> bool:
    """Select the option(s) matching `answer` for radio / checkbox / buttons / select.

    A list ticks several boxes and is only valid on a checkbox group; every item must
    match exactly one option (and no two items the same one), else nothing is touched.
    Returns True only if the page then shows every chosen option as selected.
    """
    wanted = [answer] if isinstance(answer, str) else answer
    if len(wanted) > 1 and q["kind"] != "checkbox":
        return False
    ks = [_pick(q["options"], a) for a in wanted]
    if None in ks or len(set(ks)) != len(ks):
        return False
    if q["kind"] == "select":
        _qloc(page, q).select_option(label=q["options"][ks[0]], timeout=ACTION_MS)
    else:
        for k in ks:
            _click(_item(page, q, k))
    if q["kind"] == "checkbox":
        root = page.locator(f'[data-af="{q["i"]}"]')
        return all(
            root.locator(f'[data-af-o="{k}"]').is_checked(timeout=ACTION_MS) for k in ks
        )
    return bool(page.evaluate(_VALUE_JS, q["i"]))


def _set_combo(page: Any, q: dict, answer: str) -> bool:
    """Open a combobox, choose the matching option, and confirm the page shows a value.

    Preloaded lists (Greenhouse) are read as soon as they open. A typeahead (Ashby location)
    shows nothing until text is typed, so the answer is typed and results awaited. For a
    typeahead only, a single option that starts with the part before the first comma and a
    comma is also accepted ("Seattle, WA" → "Seattle, Washington, United States").
    Returns False, with the menu closed, when no single option fits.
    """
    ctrl = _qloc(page, q)
    listbox = ctrl.get_attribute("aria-controls")
    opts = page.locator(
        f"#{listbox} [role=option]" if listbox else "[role=option]:visible"
    )
    _click(ctrl)
    typed = False
    try:
        opts.first.wait_for(state="visible", timeout=1200)
    except PWError:
        ctrl.press_sequentially(answer, delay=15)
        typed = True
        try:
            opts.first.wait_for(state="visible", timeout=ACTION_MS)
        except PWError:
            return False
    texts = [t.strip() for t in opts.all_inner_texts()]
    k = _pick(texts, answer)
    if k is None and typed:
        city = answer.split(",")[0].strip().lower()
        starts = [
            i for i, t in enumerate(texts) if city and t.lower().startswith(city + ",")
        ]
        k = starts[0] if len(starts) == 1 else None
    if k is None:
        page.keyboard.press("Escape")
        return False
    opts.nth(k).click(timeout=ACTION_MS)
    return bool(page.evaluate(_VALUE_JS, q["i"]))


def _set_text(q: dict, ctrl: Any, value: str) -> bool:
    """Fill a text control and read it back (React can silently reset a controlled input).

    A date-picker input is also blurred: its widget only commits a typed date on blur and
    drops text it cannot parse, so a value that survives the blur counts as set (the widget
    may reformat it, so equality is not required there).
    """
    ctrl.fill(value, timeout=ACTION_MS)
    if q.get("date"):
        ctrl.press("Escape", timeout=ACTION_MS)
        ctrl.blur(timeout=ACTION_MS)
        return ctrl.input_value(timeout=ACTION_MS).strip() != ""
    return ctrl.input_value(timeout=ACTION_MS) == value


def _contact_key(q: dict, taken: set[str]) -> str | None:
    """The contact field this question is: the visible label decides first, the field's
    name / id / autocomplete attribute second. Keys already filled are skipped.

    A preferred name / nickname / pronunciation question is never a contact field, whatever
    its attributes say (a name like `preferred_first_name` would otherwise match first_name);
    it is left to a rule. A second "legal name" box is also left to a rule, because each key
    is used once and the first name field on the page takes the profile name.
    """
    if _NOT_CONTACT.search(q["label"]):
        return None
    for key, by_label, _ in _CONTACT_RE:
        if key not in taken and q["label"] and by_label.search(q["label"]):
            return key
    for key, _, by_name in _CONTACT_RE:
        if key not in taken and any(
            v and by_name.search(v) for v in (q["name"], q["id"], q["ac"])
        ):
            return key
    return None


def _wait_state(page: Any, site: Site) -> str:
    """'form', 'captcha', 'login', or 'no-form' (nothing recognisable within WAIT_MS)."""
    try:
        handle = page.wait_for_function(_STATE_JS, arg=site.form, timeout=WAIT_MS)
        return handle.json_value() or "no-form"
    except PWError:
        return "no-form"


def _upload(page: Any, selector: str, path: Path) -> bool:
    loc = page.locator(selector)
    if loc.count() == 0:
        return False
    loc.first.set_input_files(str(path), timeout=ACTION_MS)
    return True


# ───────────────────────── the fill ─────────────────────────


def fill_page(
    page: Any,
    target: AtsTarget,
    profile: CandidateProfile,
    resume: Path | None,
    cover_pdf: Path | None,
    cover_text: str | None,
    answers: list[AnswerRule],
    drafter: Callable[[str], str | None] | None = None,
) -> FillReport:
    """Fill the form already loaded in `page`. Never navigates, never submits.

    Contract:
    - Returns a FillReport; `blocked` is "unsupported", "captcha", "login" or "no-form"
      when there is no form to fill (nothing is touched then).
    - A field that already has a value is left alone; an empty profile value is skipped.
    - A rule that matches a choice question but fits no single option leaves it and lists
      it under `needs_you`, as do required questions nothing answered.
    - Playwright errors on one question mark that question as needing the owner; the rest
      continue. The whole fill stops trying after FILL_BUDGET_S.
    - With a `drafter`, empty free-text boxes (required, or worded as a question) that no
      rule claimed get a draft after the main pass, at most MAX_DRAFTS, outlined in blue and
      listed in `drafted`. The drafting time is not part of FILL_BUDGET_S.
    """
    rep = FillReport(ats=target.ats, url=page.url)
    site = SITES.get(target.ats)
    if site is None:
        rep.blocked = "unsupported"
        return rep

    # 1. Wait (bounded) for the form; a challenge or login wall with no form is a block.
    state = _wait_state(page, site)
    if state != "form":
        rep.blocked = state
        return rep
    # React forms are server-rendered, then hydrate and reset typed values; let the page
    # go quiet first (bounded; some pages never idle, which is fine).
    _try(lambda: page.wait_for_load_state("networkidle", timeout=IDLE_MS))

    # 2. Upload the resume and cover PDF first: Lever re-fills fields after parsing a resume,
    #    and "don't overwrite" then protects whatever it produced.
    if resume and _try(lambda: _upload(page, site.resume, resume)):
        rep.resume = resume.name
        rep.filled.append("Resume")
        if site.busy:
            _try(
                lambda: page.wait_for_selector(
                    site.busy, state="hidden", timeout=UPLOAD_SETTLE_MS
                )
            )
    if cover_pdf and _try(lambda: _upload(page, _COVER_FILE, cover_pdf)):
        rep.cover = cover_pdf.name
        rep.filled.append("Cover letter")

    # 3. Read every question once, then walk them in page order.
    questions: list[dict] = page.evaluate(
        _EXTRACT_JS, {"root": site.root, "label": site.label}
    )
    values = {
        "first_name": profile.first_name, "last_name": profile.last_name,
        "full_name": profile.full_name, "email": profile.email, "phone": profile.phone,
        "location": profile.location, "linkedin": profile.linkedin,
        "github": profile.github, "portfolio_url": profile.portfolio_url,
    }  # fmt: skip
    taken: set[str] = set()
    deadline = time.monotonic() + FILL_BUDGET_S
    left: list[dict] = []
    longs: list[dict] = []  # empty free-text boxes the drafter may take
    for q in questions:
        label = q["label"] or q["name"] or "(unlabelled question)"
        if q["kind"] == "file":
            uploaded = (label.lower().startswith("resume") and rep.resume) or (
                _COVER_LABEL.search(label) and rep.cover
            )
            if q["required"] and not q["hasValue"] and not uploaded:
                left.append(q)
            continue
        if q["hasValue"] or not q["visible"]:
            continue
        if time.monotonic() > deadline:
            if q["required"]:
                left.append(q)
            continue
        try:
            if _fill_question(page, q, label, values, taken, cover_text, answers, rep):
                continue
        except PWError:
            pass
        if drafter and q["kind"] == "text" and q.get("long") and not q.get("_ruled") and (
            q["required"] or _QUESTIONISH.search(label)
        ):
            longs.append(q)
        if q["required"] or q.get("_ruled"):
            left.append(q)

    # 3b. Draft the free-text boxes; a draft that lands removes the box from `left`.
    for q in longs[:MAX_DRAFTS]:
        label = q["label"] or q["name"] or "(unlabelled question)"
        text = drafter(label)
        if text and _try(lambda q=q, text=text: _set_text(q, _qloc(page, q), text)):
            rep.drafted.append(label)
            if q in left:
                left.remove(q)
            _try(
                lambda q=q: page.locator(f'[data-af="{q["i"]}"]').evaluate(
                    "(e, o) => { e.style.outline = o; e.style.outlineOffset = '2px'; }",
                    DRAFT_OUTLINE,
                    timeout=ACTION_MS,
                )
            )
    for q in longs[MAX_DRAFTS:]:
        if q["required"] and q not in left:
            left.append(q)

    # 4. Report what still needs the owner, and outline it on the page. Only required
    #    questions go in `needs_you`; an optional one matters only when a rule matched it.
    for q in left:
        name = q["label"] or q["name"] or "(unlabelled question)"
        (rep.needs_you if q["required"] else rep.unmatched).append(name)
        _try(
            lambda q=q: page.locator(f'[data-af="{q["i"]}"]').evaluate(
                "(e, o) => { e.style.outline = o; e.style.outlineOffset = '2px'; }",
                OUTLINE,
                timeout=ACTION_MS,
            )
        )
    return rep


def _fill_question(
    page: Any,
    q: dict,
    label: str,
    values: dict[str, str],
    taken: set[str],
    cover_text: str | None,
    answers: list[AnswerRule],
    rep: FillReport,
) -> bool:
    """Fill one question from the profile or a rule. Returns True when it was set.

    Sets `q["_ruled"]` when a rule matched but could not be applied, so the caller reports
    it even if the question is optional.
    """
    ctrl = _qloc(page, q)
    # 1. Contact field → profile value (text boxes; the location typeahead too).
    key = _contact_key(q, taken)
    if key:
        taken.add(key)
        if not values[key]:
            return False
        ok = (
            _set_combo(page, q, values[key])
            if q["kind"] == "combobox"
            else (q["kind"] == "text" and _set_text(q, ctrl, values[key]))
        )
        if ok:
            rep.filled.append(label)
        return bool(ok)
    # 2. Cover-letter text box → pasted cover text (only when the form shows one).
    if (
        cover_text
        and q["kind"] == "text"
        and _COVER_LABEL.search(label)
        and not rep.cover
    ):
        if _set_text(q, ctrl, cover_text):
            rep.cover = "pasted"
            rep.filled.append(label)
            return True
        return False
    # 3. Anything else → first matching rule, else untouched.
    rule = next((r for r in answers if r.pattern.search(label)), None)
    if rule is None:
        return False
    q["_ruled"] = True
    if isinstance(rule.answer, list):
        ok = q["kind"] == "checkbox" and _set_choice(page, q, rule.answer)
    elif q["kind"] == "text":
        ok = _set_text(q, ctrl, rule.answer)
    elif q["kind"] == "combobox":
        ok = _set_combo(page, q, rule.answer)
    else:
        ok = _set_choice(page, q, rule.answer)
    if ok:
        rep.filled.append(label)
        rep.rules_fired.append((rule.pattern.pattern, label))
    return ok


def _try(fn: Callable[[], Any]) -> Any:
    """Run a page action; a Playwright error (timeout, detached element) yields None."""
    try:
        return fn()
    except PWError:
        return None


# ───────────────────────── browser worker ─────────────────────────


class AutofillWorker:
    """Runs fill jobs one at a time on a single thread that owns the browser.

    Intent: Playwright's sync API is bound to the thread that started it, so one daemon
    thread starts Chromium (a persistent profile, so logins and cookies survive) and does
    all page work. `submit` only enqueues.
    Contract:
    - Each job opens a new tab, navigates to the form, fills it, calls `on_done(report)`
      from the worker thread, and leaves the tab open for the owner.
    - An unsupported URL still opens a tab; the report says `blocked="unsupported"`.
    - Any exception becomes `blocked="error: ..."`; the thread survives it.
    - The browser starts on the first job and is relaunched if the owner closed it.
    - `close()` is idempotent and safe when nothing was started.
    Limits: strictly sequential; `on_done` must not block for long.
    """

    def __init__(self, profile_dir: Path, headless: bool = False) -> None:
        self._profile_dir = Path(profile_dir)
        self._headless = headless
        self._jobs: queue.Queue[tuple | None] = queue.Queue()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._closing = False
        self._pw: Any = None
        self._ctx: Any = None
        self._ctx_open = False

    def submit(
        self,
        url: str,
        profile: CandidateProfile,
        resume: Path | None,
        cover_pdf: Path | None,
        cover_text: str | None,
        answers: list[AnswerRule],
        on_done: Callable[[FillReport], None],
        drafter: Callable[[str], str | None] | None = None,
    ) -> None:
        """Queue one job; returns immediately. Starts the worker thread on first use."""
        with self._lock:
            if self._closing:
                return
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(
                    target=self._run, name="autofill", daemon=True
                )
                self._thread.start()
            self._jobs.put(
                (url, profile, resume, cover_pdf, cover_text, answers, drafter, on_done)
            )

    def close(self) -> None:
        """Stop after the current job, close the browser and join the thread. Idempotent."""
        with self._lock:
            thread, self._closing = self._thread, True
            if thread is not None:
                self._jobs.put(None)
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=20)

    # -- worker thread only below this line --

    def _run(self) -> None:
        try:
            while (job := self._jobs.get()) is not None:
                *args, on_done = job
                try:
                    report = self._do(*args)
                except Exception as e:
                    report = FillReport(
                        ats="unknown", url=str(args[0]), blocked=f"error: {e}"
                    )
                try:
                    on_done(report)
                except Exception:
                    pass
        finally:
            self._shutdown_browser()

    def _context(self) -> Any:
        """The live persistent context, launching (or relaunching after the owner quit)."""
        if self._ctx is not None:
            try:
                _ = self._ctx.pages  # raises once the browser process is gone
                if self._ctx_open:
                    return self._ctx
            except Exception:  # noqa: BLE001
                pass
            self._drop_context()
        if self._pw is None:
            from playwright.sync_api import sync_playwright

            self._pw = sync_playwright().start()
        self._ctx = self._pw.chromium.launch_persistent_context(
            user_data_dir=str(self._profile_dir),
            headless=self._headless,
            no_viewport=not self._headless,
        )
        self._ctx_open = True
        self._ctx.on("close", lambda *_: setattr(self, "_ctx_open", False))
        return self._ctx

    def _drop_context(self) -> None:
        try:
            if self._ctx is not None:
                self._ctx.close()
        except Exception:  # noqa: BLE001
            pass
        self._ctx = None

    def _shutdown_browser(self) -> None:
        self._drop_context()
        try:
            if self._pw is not None:
                self._pw.stop()
        except Exception:  # noqa: BLE001
            pass
        self._pw = None

    def _tab(self) -> Any:
        """A tab for the next job: the blank one a fresh browser opens with, else a new one."""
        ctx = self._context()
        blank = [p for p in ctx.pages if p.url == "about:blank"]
        return blank[0] if blank else ctx.new_page()

    def _do(self, url, profile, resume, cover_pdf, cover_text, answers, drafter=None) -> FillReport:
        target = target_for(url)
        supported = target is not None and target.ats in SUPPORTED
        try:
            page = self._tab()
        except Exception:
            self._drop_context()
            page = self._tab()
        go = form_url(target) if supported else url
        try:
            page.goto(go, wait_until="domcontentloaded", timeout=30000)
        except PWError as e:
            return FillReport(
                ats=target.ats if target else "unknown", url=url, blocked=f"error: {e}"
            )
        if not supported:
            return FillReport(
                ats=target.ats if target else "unknown", url=url, blocked="unsupported"
            )
        return fill_page(page, target, profile, resume, cover_pdf, cover_text, answers, drafter)
