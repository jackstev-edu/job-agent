"""
accounts.py — sign-up, sign-in, forgot-password and verification pages.

WHAT THIS DOES: On a page pages.page_kind calls an account, sign-in or
verification page, does what the owner decisions allow: presses a "Sign in
/ Continue with Google" control first, if there is one, and lets google.py
walk Google's pages (Jack, 2026-10-19); on Workday presses the Sign In /
Create Account submits and links by their data-automation-id, through the
click_filter overlay that covers them; otherwise types the profile
email (and name) into a sign-up form, generates a password and types it
into the new-password boxes, ticks a terms / privacy box under the consent
policy, presses Create account; on a sign-in page types the email and
presses Sign in (Chrome may fill a saved password), and failing that goes
through Forgot password, pausing for the password Jack sets; on a
verification page pauses for the code. Every password is held in a local
variable, typed once, and dropped; nothing here reads a password box, logs
a password or sends one anywhere.
WHY IT MATTERS: Account walls are where every unattended run used to stop.
This is also the only module that types a password, so the rules about it
can be checked in one place.
"""

from __future__ import annotations

import contextlib
import re
import secrets
import string
from urllib.parse import urlsplit
from dataclasses import dataclass, field as dataclass_field
from typing import TYPE_CHECKING

from . import browser as browser_mod, gate, google, secrets_store, pages, pause as pause_mod

if TYPE_CHECKING:
    from playwright.sync_api import Page

# Workday's account pages are keyed by data-automation-id, not visible text
# (their ids regenerate; the automation ids don't), and Workday covers its
# Sign In / Create Account button with a "click_filter" overlay that takes
# the click. Only these two submits and the two links are ever pressed by
# id, each on the page kind its step is for (sign_in_link is listed, never
# pressed).
WORKDAY_IDS = {
    "email": "email", "password": "password", "verify": "verifyPassword",
    "consent": "createAccountCheckbox", "create": "createAccountSubmitButton",
    "sign_in": "signInSubmitButton", "create_link": "createAccountLink",
    "sign_in_link": "signInLink", "forgot_link": "forgotPasswordLink", "overlay": "click_filter",
}
WORKDAY_PRESSABLE = {"create", "sign_in", "create_link", "forgot_link"}

# Buttons each step may press, as whole normalised text. Never anything that
# reads like Submit application: on these pages there is no application to
# submit, and the page kind is checked again right before every press.
SIGN_IN_WORDS = re.compile(
    r"^(sign ?in|log ?in|login|sign ?in with (email|password)"
    r"|sign ?in to (apply|continue|your account))$", re.I)
CREATE_WORDS = re.compile(
    r"^(create (an |my |your |a new )?account|sign ?up|register|register now"
    r"|create account and continue)$", re.I)
CREATE_LINK_WORDS = re.compile(
    r"^(create (an |my |your |a new )?account|sign ?up|register|register (here|now)|new user"
    r"|create one|create one here|don'?t have an account\??( ?(sign ?up|register|create (an |one)?( account)?))?"
    r"|new to \S+\??( ?(sign ?up|register|create (an )?account))?|i'?m a new user|new candidate)$", re.I)
FORGOT_WORDS = re.compile(
    r"^(forgot(ten)? (your |my )?password\??|forgot\??|reset (your |my |the )?password\??"
    r"|trouble (signing|logging) in\??|can'?t (sign|log) in\??|need help signing in\??)$", re.I)
RESET_SEND_WORDS = re.compile(
    r"^(send|reset|reset password|send (reset |me a |a )?(link|email|code|instructions)"
    r"|email me|request( reset)?|send reset link|recover)$", re.I)
VERIFY_WORDS = re.compile(r"^(verify|verify (code|email|now)|confirm|validate|confirm (code|email))$",
                          re.I)
# Never pressed by this module, whatever list matched: a bare Submit, Next
# or Continue belongs to the wizard and to submit.py, behind the gate.
# `google` left this list on 2026-10-19 (Jack: Sign in with Google is
# preferred); the other identity providers stay refused.
NEVER_WORDS = re.compile(r"^\s*(submit|continue|next|proceed|go|enter|done|finish)\s*$"
                         r"|submit (my |your |the )?application|apply|withdraw|delete"
                         r"|microsoft|apple|facebook|linkedin|sso|single sign", re.I)

EMAIL_BOX = re.compile(r"e-?mail|username|user name|user id|login id", re.I)
FIRST_BOX = re.compile(r"first\s*name|given\s*name|\bfname\b|firstname", re.I)
LAST_BOX = re.compile(r"last\s*name|family\s*name|surname|\blname\b|lastname", re.I)

PASSWORD_LENGTH = 24
PASSWORD_MIN = 20              # the owner decision: 20+ characters
_CLASSES = (string.ascii_uppercase, string.ascii_lowercase, string.digits, "!@#$%^&*-_=+?")

CONTINUE, PARKED = "continue", "parked"


@dataclass
class Result:
    status: str                         # CONTINUE or PARKED
    reason: str = ""
    steps: list[str] = dataclass_field(default_factory=list)
    events: list[dict] = dataclass_field(default_factory=list)


def _host(page: "Page") -> str | None:
    """The portal's host (tenant) a stored password is keyed by; None on Google or with no host."""
    try:
        host = (urlsplit(page.url).hostname or "").lower().rstrip(".")
    except Exception:
        return None
    if not host or google.is_google_host(page.url):
        return None
    return host


def _remember(host: str | None, password: str | None, steps: list[str]) -> None:
    """Store a password that just signed in or created the account (Jack, 2026-10-20)."""
    if not host or not password:
        return
    try:
        secrets_store.save(host, password)
        steps.append(f"stored the password for {host} in Windows Credential Manager")
    except Exception as e:
        steps.append(f"couldn't store the password for {host} ({type(e).__name__})")


def generate_password(length: int = PASSWORD_LENGTH) -> str:
    """
    One fixed policy: `length` characters (never below PASSWORD_MIN, the
    owner's 20), at least one of each class, shuffled with `secrets`. No
    page text is read for rules; a site that rejects it gets a pause, not a
    parse.
    """
    length = max(int(length or 0), PASSWORD_MIN)
    chars = [secrets.choice(c) for c in _CLASSES]
    alphabet = "".join(_CLASSES)
    chars += [secrets.choice(alphabet) for _ in range(length - len(chars))]
    # secrets has no shuffle: pick positions with it.
    out: list[str] = []
    while chars:
        out.append(chars.pop(secrets.randbelow(len(chars))))
    return "".join(out)


# --- finding things on the page (structure and whole-text matches only) -------

_FIND_BUTTON_JS = r"""
([wordsRx, neverRx, attr]) => {
  const WORDS = new RegExp(wordsRx, 'i'), NEVER = new RegExp(neverRx, 'i');
  const norm = t => String(t || '').replace(/\s+/g, ' ').trim();
  const visible = el => {
    const r = el.getBoundingClientRect(), s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
  };
  document.querySelectorAll('[' + attr + ']').forEach(e => e.removeAttribute(attr));
  const isBtnInput = el => el.tagName === 'INPUT' && ['submit', 'button'].includes((el.type || '').toLowerCase());
  const texts = el => [el.innerText, isBtnInput(el) ? el.value : '', el.getAttribute('aria-label'),
                       el.getAttribute('title')].map(norm).filter(Boolean);
  const hits = [...document.querySelectorAll(
      'button, a, [role=button], input[type=submit], input[type=button]')]
      .filter(visible)
      .filter(el => { const ts = texts(el); return ts.length && !ts.some(t => NEVER.test(t))
                                                  && ts.some(t => WORDS.test(t)); });
  // A submit button beats a link; the first of several equals wins only
  // when they all read the same.
  const words = new Set(hits.map(el => norm(el.innerText || (isBtnInput(el) ? el.value : '') || el.getAttribute('aria-label')).toLowerCase()));
  if (!hits.length || words.size > 1) return {found: hits.length, chosen: false, word: ''};
  const chosen = hits.find(el => (el.type || '').toLowerCase() === 'submit') || hits[0];
  chosen.setAttribute(attr, '1');
  return {found: hits.length, chosen: true, word: norm(chosen.innerText || (isBtnInput(chosen) ? chosen.value : '') || chosen.getAttribute('aria-label'))};
}
"""

# Visible text-like boxes with their identity words, so the email and name
# boxes can be picked by label. Values are never read: only attributes and
# label text come back, and only for non-password boxes.
_TEXT_BOXES_JS = r"""
(attr) => {
  const norm = t => String(t || '').replace(/\s+/g, ' ').trim();
  const visible = el => {
    const r = el.getBoundingClientRect(), s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
  };
  document.querySelectorAll('[' + attr + ']').forEach(e => e.removeAttribute(attr));
  const out = [];
  let n = 0;
  for (const el of document.querySelectorAll('input')) {
    const type = (el.type || 'text').toLowerCase();
    if (!['text', 'email', 'tel', ''].includes(type) || !visible(el) || el.disabled || el.readOnly) continue;
    if (/password/i.test(el.getAttribute('autocomplete') || '')) continue;
    const words = norm([el.name, el.id, el.getAttribute('aria-label'), el.getAttribute('placeholder'),
                        el.getAttribute('autocomplete'),
                        ...[...(el.labels || [])].map(l => l.innerText)].filter(Boolean).join(' '));
    const key = 'b' + (n++);
    el.setAttribute(attr, key);
    out.push({key, type, words});
  }
  return out;
}
"""

# The password boxes, by role: "new" (autocomplete new-password, or every
# password box on a page with none marked current), "current" (autocomplete
# current-password, or the single box on a sign-in page). Attributes only.
_PASSWORD_BOXES_JS = r"""
(attr) => {
  const visible = el => {
    const r = el.getBoundingClientRect(), s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
  };
  document.querySelectorAll('[' + attr + ']').forEach(e => e.removeAttribute(attr));
  const boxes = [...document.querySelectorAll('input[type=password i]')].filter(visible);
  const roles = boxes.map(el => /new-password/i.test(el.getAttribute('autocomplete') || '') ? 'new'
                             : /current-password/i.test(el.getAttribute('autocomplete') || '') ? 'current' : '');
  const anyCurrent = roles.includes('current');
  let n = 0;
  return boxes.map((el, i) => {
    const key = 'p' + (n++);
    el.setAttribute(attr, key);
    const role = roles[i] || (anyCurrent ? 'current' : (boxes.length >= 2 ? 'new' : 'current'));
    const max = el.maxLength > 0 ? el.maxLength : null;
    return {key, role, maxlength: max};
  });
}
"""

_CODE_BOX_JS = r"""
(attr) => {
  const split = t => t.replace(/([a-z])([A-Z])/g, '$1 $2').replace(/[_-]+/g, ' ');
  const codeRx = /one\s?time|(?<![a-z])otp(?![a-z])|(?<![a-z])2fa(?![a-z])|(?<![a-z])mfa(?![a-z])|two\s?factor|passcode|(verification|verify|security|auth\w*|sms|confirmation|access)\s*code/i;
  const visible = el => {
    const r = el.getBoundingClientRect(), s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
  };
  document.querySelectorAll('[' + attr + ']').forEach(e => e.removeAttribute(attr));
  const boxes = [...document.querySelectorAll('input')].filter(el => visible(el) &&
      (el.getAttribute('autocomplete') === 'one-time-code' ||
       codeRx.test(split([el.name, el.id, el.getAttribute('aria-label'), el.getAttribute('placeholder')]
                         .filter(Boolean).join(' ')))));
  if (boxes.length !== 1) return boxes.length;
  boxes[0].setAttribute(attr, '1');
  return 1;
}
"""

# One marker attribute per kind of box, so stamping one kind never clears
# another's (each scan clears its own markers first).
_TEXT_ATTR, _PW_ATTR, _CODE_ATTR, _CONSENT_ATTR = (
    "data-ja-acct-text", "data-ja-acct-pw", "data-ja-acct-code", "data-ja-acct-consent")
_BTN_ATTR = "data-ja-acct-btn"

# Consent checkboxes on the page, in the census shape the gate's policy judges.
_CONSENT_BOXES_JS = r"""
(attr) => {
  const norm = t => (t || '').replace(/\s+/g, ' ').trim();
  const visible = el => {
    const r = el.getBoundingClientRect(), s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
  };
  const texts = els => ({inner: els.map(e => e.innerText).join(' '),
                         content: els.map(e => e.textContent).join(' ')});
  const labelOf = el => {
    const aria = el.getAttribute('aria-label');
    if (aria && aria.trim()) return {source: 'aria-label', inner: aria, content: aria};
    if (el.id) {
      const l = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
      if (l) return {source: 'for', ...texts([l])};
    }
    const wrap = el.closest('label');
    if (wrap) return {source: 'wrap', ...texts([wrap])};
    return {source: 'none', inner: '', content: ''};
  };
  const described = el => (el.getAttribute('aria-describedby') || '').split(/\s+/)
      .map(id => document.getElementById(id)?.textContent || '').join(' ');
  let n = 0;
  return [...document.querySelectorAll('input[type=checkbox]')].filter(visible).map(el => {
    const lab = labelOf(el);
    const key = 'c' + (n++);
    el.setAttribute(attr, key);
    return {key, checked: !!el.checked, required: !!el.required, visible: true,
            label_source: lab.source, label_inner: norm(lab.inner), label_content: norm(lab.content),
            described: norm(described(el))};
  });
}
"""


def _box(page: "Page", attr: str, key: str):
    return page.locator(f"[{attr}='{key}']").first


# Which of the known Workday controls the page carries, visible ones only.
# Only names from WORKDAY_IDS come back, never page text.
_WORKDAY_IDS_JS = r"""
(ids) => {
  const visible = el => {
    const r = el.getBoundingClientRect(), s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
  };
  return ids.filter(id => [...document.querySelectorAll('[data-automation-id="' + id + '"]')].some(visible));
}
"""

# Marks the one visible control with this automation id (never one that
# reads like Submit application or another provider, whatever its id)
# and, if a click_filter overlay in the same parent covers its centre,
# that overlay too. Returns {where: 'overlay' | 'control', x, y}, the
# control's own centre as an offset inside the marked element, so the
# click lands on the control's point even under a larger overlay; or null.
_WORKDAY_MARK_JS = r"""
([id, attr, neverRx]) => {
  const NEVER = new RegExp(neverRx, 'i');
  const norm = t => String(t || '').replace(/\s+/g, ' ').trim();
  const visible = el => {
    const r = el.getBoundingClientRect(), s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
  };
  document.querySelectorAll('[' + attr + ']').forEach(e => e.removeAttribute(attr));
  const hits = [...document.querySelectorAll('[data-automation-id="' + id + '"]')].filter(visible);
  if (hits.length !== 1) return null;
  const el = hits[0];
  // An <input> is pressable only as a submit / button input; its value is
  // read for the wording check only then. Any other input with the id
  // (a text or password box) is never read and never pressed.
  const kinds = ['submit', 'button'];
  const isBtnInput = el.tagName === 'INPUT' && kinds.includes((el.type || '').toLowerCase())
    && kinds.includes((el.getAttribute('type') || '').toLowerCase());
  if (el.tagName === 'INPUT' && !isBtnInput) return null;
  const texts = [el.innerText, isBtnInput ? el.value : '', el.getAttribute('aria-label'),
                 el.getAttribute('title')].map(norm).filter(Boolean);
  if (texts.some(t => NEVER.test(t))) return null;
  const b = el.getBoundingClientRect();
  const cx = b.left + b.width / 2, cy = b.top + b.height / 2;
  const covers = o => { const r = o.getBoundingClientRect();
    return r.left <= cx && cx <= r.right && r.top <= cy && cy <= r.bottom; };
  const overlay = [...document.querySelectorAll('[data-automation-id="click_filter"]')]
    .filter(visible).filter(o => el.parentElement && el.parentElement.contains(o)).find(covers);
  const target = overlay || el, r = target.getBoundingClientRect();
  target.setAttribute(attr, '1');
  // Playwright's position is relative to the padding box: the border is taken off.
  return {where: overlay ? 'overlay' : 'control', x: cx - r.left - target.clientLeft, y: cy - r.top - target.clientTop};
}
"""


def workday_controls(page: "Page") -> set[str]:
    """The WORKDAY_IDS keys whose control is visible on the page; empty off Workday."""
    try:
        got = page.evaluate(_WORKDAY_IDS_JS, list(WORKDAY_IDS.values()))
    except Exception:
        return set()
    if not isinstance(got, list):
        return set()
    by_id = {v: k for k, v in WORKDAY_IDS.items()}
    return {by_id[i] for i in got if i in by_id}


def _press_workday(page: "Page", key: str, kind_now: str | None) -> bool:
    """
    Press one of Workday's own account controls by automation id, through
    the click_filter overlay when it covers the control (Workday's own
    click path), if the page is still of the kind this step is for.
    """
    if key not in WORKDAY_PRESSABLE or _on_google(page) or pages.page_kind(page) != kind_now:
        return False
    try:
        mark = page.evaluate(_WORKDAY_MARK_JS, [WORKDAY_IDS[key], _BTN_ATTR, NEVER_WORDS.pattern])
    except Exception:
        return False
    if (not isinstance(mark, dict) or mark.get("where") not in ("overlay", "control")
            or not isinstance(mark.get("x"), (int, float)) or not isinstance(mark.get("y"), (int, float))):
        return False
    try:
        page.locator(f"[{_BTN_ATTR}='1']").first.click(timeout=4000,
                                                      position={"x": float(mark["x"]), "y": float(mark["y"])})
    except Exception:
        return False
    browser_mod.settle(page, 1500)
    return True


def _press(page: "Page", words: re.Pattern, kind_now: str | None, what: str) -> str | None:
    """
    Press the one visible control reading as `words`, if the page is still
    of the kind this step is for. Returns the word pressed, or None.
    """
    if _on_google(page) or pages.page_kind(page) != kind_now:
        return None
    try:
        found = page.evaluate(_FIND_BUTTON_JS, [words.pattern, NEVER_WORDS.pattern, _BTN_ATTR])
    except Exception:
        return None
    if not isinstance(found, dict) or not found.get("chosen"):
        return None
    try:
        page.locator(f"[{_BTN_ATTR}='1']").first.click(timeout=4000)
    except Exception:
        return None
    browser_mod.settle(page, 1500)
    return str(found.get("word") or what)


def _google_first(page: "Page", prof, pauser: pause_mod.Pauser, res: Result,
                  kind_now: str) -> str | None:
    """
    Press the one control reading as Google sign-in, ahead of email and
    password (Jack, 2026-10-19), follow it into the same tab or a popup,
    and let google.drive walk Google's pages by structure. None when there
    is no such control (or it couldn't be pressed); else google.DONE or
    google.STUCK. The caller re-reads the portal page's kind afterwards.
    """
    if _on_google(page) or pages.page_kind(page) != kind_now:
        return None
    try:
        found = page.evaluate(_FIND_BUTTON_JS, [google.BUTTON_WORDS.pattern, NEVER_WORDS.pattern, _BTN_ATTR])
    except Exception:
        return None
    if not isinstance(found, dict) or not found.get("chosen"):
        return None
    gpage, clicked = page, False
    try:
        with page.context.expect_page(timeout=3000) as opened:
            page.locator(f"[{_BTN_ATTR}='1']").first.click(timeout=4000)
            clicked = True
        gpage = opened.value
        gpage.wait_for_load_state("domcontentloaded", timeout=30_000)
    except Exception:
        if not clicked:
            return None                    # the same tab, or a popup that didn't come
    res.steps.append(f"pressed '{found.get('word')}'")
    # A popup Google already sent back may close at any moment: a settle
    # on a closed page raises, and that is not a failure of the job.
    with contextlib.suppress(Exception):
        if not gpage.is_closed():
            browser_mod.settle(gpage, 1000)
    status = google.drive(gpage, prof, pauser, res.steps, res.events)
    with contextlib.suppress(Exception):
        browser_mod.settle(page, 800)
    return status


GOOGLE_STUCK = "Google's page needs you, and the pause didn't get it done"
GOOGLE_FORGOT = "the Forgot password link led to Google's site; nothing is requested or typed there"


def on_google(page: "Page", prof, pauser: pause_mod.Pauser) -> Result:
    """The tab itself is on Google's site (a same-tab sign-in): walk it, or pause."""
    res = Result(CONTINUE)
    if google.drive(page, prof, pauser, res.steps, res.events) == google.STUCK:
        return Result(PARKED, GOOGLE_STUCK, res.steps, res.events)
    return res


def _on_google(page: "Page") -> bool:
    """
    Google's own site, on any address: nothing here types, ticks or presses
    there (Jack, 2026-10-19). A redirect in the middle of a step (a Forgot
    link, a code page, a bounce) lands on this check, not on a box.
    """
    try:
        return google.is_google_host(page.url)
    except Exception:
        return True


def _type_secret(page: "Page", key: str, value: str) -> bool:
    """
    Type a password into one located password box. Nothing is read back,
    nothing is logged, and the value isn't kept here. True if the keystrokes
    were sent. Only ever called from this module.
    """
    if _on_google(page):
        return False
    try:
        box = _box(page, _PW_ATTR, key)
        box.click(timeout=4000)
        box.fill("", timeout=4000)
        box.type(value, delay=8, timeout=20_000)
        return True
    except Exception:
        return False


def _fill_text(page: "Page", attr: str, key: str, value: str) -> bool:
    if _on_google(page):
        return False
    try:
        box = _box(page, attr, key)
        box.click(timeout=4000)
        box.fill(value, timeout=4000)
        return True
    except Exception:
        return False


def _fill_identity(page: "Page", prof, steps: list[str]) -> None:
    """The email (and a first / last name if boxes are labelled so) from the profile."""
    if _on_google(page):
        return
    try:
        boxes = page.evaluate(_TEXT_BOXES_JS, _TEXT_ATTR) or []
    except Exception:
        boxes = []
    wanted = ((EMAIL_BOX, "personal.email", "email"), (FIRST_BOX, "personal.first_name", "first name"),
              (LAST_BOX, "personal.last_name", "last name"))
    for rx, path, what in wanted:
        value = prof.get(path)
        if not value:
            continue
        hits = [b for b in boxes if isinstance(b, dict) and rx.search(str(b.get("words") or ""))]
        if what == "email":
            hits = [b for b in boxes if b.get("type") == "email"] or hits
        if len(hits) == 1 and _fill_text(page, _TEXT_ATTR, hits[0]["key"], str(value)):
            steps.append(f"typed the {what}")


def _tick_consent(page: "Page", steps: list[str]) -> None:
    """Tick a required terms / privacy box the policy allows; never anything else."""
    if _on_google(page):
        return
    try:
        boxes = page.evaluate(_CONSENT_BOXES_JS, _CONSENT_ATTR) or []
    except Exception:
        return
    for b in boxes:
        if not isinstance(b, dict) or b.get("checked") or not b.get("required"):
            continue
        if not gate.consent_box_ok(b):
            continue
        try:
            _box(page, _CONSENT_ATTR, b["key"]).check(timeout=4000)
            steps.append(f"ticked '{b.get('label_inner', '')[:60]}'")
        except Exception:
            pass


def sign_up(page: "Page", prof, pauser: pause_mod.Pauser) -> Result:
    """An account page: email, name, a generated password, consent, Create account."""
    res = Result(CONTINUE)
    if pages.page_kind(page) != pages.ACCOUNT:
        return res
    g = _google_first(page, prof, pauser, res, pages.ACCOUNT)
    if g == google.STUCK or (g is not None and google.is_google_host(page.url)):
        return Result(PARKED, GOOGLE_STUCK, res.steps, res.events)    # or bounced back to Google
    if g is not None and pages.page_kind(page) != pages.ACCOUNT:
        res.steps.append("the account page went away after the Google button")
        return _then_verify(page, prof, pauser, res)
    try:
        pw_boxes = page.evaluate(_PASSWORD_BOXES_JS, _PW_ATTR) or []
    except Exception:
        pw_boxes = []
    new_boxes = [b for b in pw_boxes if isinstance(b, dict) and b.get("role") == "new"]
    if not new_boxes:
        return Result(PARKED, "an account page with no new-password box to create an account in")
    _fill_identity(page, prof, res.steps)
    limits = [int(b["maxlength"]) for b in new_boxes if isinstance(b.get("maxlength"), int)
              and 0 < int(b["maxlength"]) < PASSWORD_LENGTH]
    if limits and min(limits) < PASSWORD_MIN:
        return Result(PARKED, f"the password box allows only {min(limits)} characters; the "
                              f"policy is {PASSWORD_MIN}+", res.steps)
    password = generate_password(min(limits) if limits else PASSWORD_LENGTH)
    host = _host(page)
    typed = all(_type_secret(page, b["key"], password) for b in new_boxes)
    if not typed:
        del password
        return Result(PARKED, "couldn't type into the new-password boxes", res.steps)
    res.steps.append(f"typed a generated password into {len(new_boxes)} box(es)")
    _tick_consent(page, res.steps)
    if "create" in workday_controls(page):
        pressed = "Create Account (Workday)" if _press_workday(page, "create", pages.ACCOUNT) else None
    else:
        pressed = _press(page, CREATE_WORDS, pages.ACCOUNT, "Create account")
    if pressed is None:
        del password
        return Result(PARKED, "no single Create account button to press", res.steps)
    res.steps.append(f"pressed '{pressed}'")
    res.events.append({"kind": "account_created", "detail": "by the agent", "handled": "agent"})
    kind = pages.page_kind(page)
    if kind not in (pages.ACCOUNT, pages.SIGN_IN):
        # The press went through to a verification or a form page: this is
        # the account's password. When the page stays (Jack finishes by
        # hand) or bounces to the sign-in page (the account already exists,
        # with a password this one is not), nothing is stored.
        _remember(host, password, res.steps)
    del password
    if kind == pages.ACCOUNT:
        _, how = pauser.wait("job-agent needs you", "The sign-up page didn't go through. Finish "
                             "creating the account in the browser, then press Enter here.",
                             prompt="Press Enter when the account exists: ",
                             until=lambda: pages.page_kind(page) != pages.ACCOUNT)
        if pages.page_kind(page) == pages.ACCOUNT:
            return Result(PARKED, f"the account page didn't accept the sign-up ({how})", res.steps,
                          res.events)
        res.events.append({"kind": "account_page", "detail": "finished by Jack", "handled": "jack"})
    return _then_verify(page, prof, pauser, res)


def _then_verify(page: "Page", prof, pauser: pause_mod.Pauser, res: Result) -> Result:
    """A verification page usually follows a sign-up: handled in the same step."""
    if pages.page_kind(page) == pages.VERIFICATION:
        verified = verify(page, prof, pauser)
        res.steps += verified.steps
        res.events += verified.events
        if verified.status != CONTINUE:
            return Result(PARKED, verified.reason, res.steps, res.events)
    return res


def _email(prof) -> str:
    return str(prof.get("personal.email") or "")


def forgot_password(page: "Page", prof, pauser: pause_mod.Pauser, res: Result) -> Result:
    """
    Forgot password: the link, the email, Send; pause for the password Jack
    sets; back to the sign-in page; email and the password, once; Sign in.
    """
    if "forgot_link" in workday_controls(page):
        pressed = "Forgot Password (Workday)" if _press_workday(page, "forgot_link", pages.SIGN_IN) else None
    else:
        pressed = _press(page, FORGOT_WORDS, pages.SIGN_IN, "Forgot password")
    if pressed is None:
        return Result(PARKED, "sign-in failed and there is no Forgot password link", res.steps,
                      res.events)
    res.steps.append(f"pressed '{pressed}'")
    if _on_google(page):
        # The link led to Google's own site: nothing is requested or typed
        # there, and no event may say it was.
        return Result(PARKED, GOOGLE_FORGOT, res.steps, res.events)
    if not _is_reset_page(page):
        return Result(PARKED, "the Forgot password link didn't lead to a reset page (one email "
                              "box, no password box)", res.steps, res.events)
    _fill_identity(page, prof, res.steps)
    sent = _press(page, RESET_SEND_WORDS, None, "Send")
    if sent is not None:
        res.steps.append(f"pressed '{sent}'")
    res.events.append({"kind": "password_reset", "detail": "requested by the agent", "handled": "agent"})
    password, how = pauser.wait(
        "job-agent needs you",
        f"A password reset was requested for {_email(prof)}. Set a new password, then paste it "
        f"in the terminal (hidden). Or sign in yourself in the browser.",
        prompt="New password (hidden): ", hidden=True,
        until=lambda: pages.page_kind(page) not in (pages.SIGN_IN, pages.ACCOUNT, None)
        or pages.page_kind(page) is None and False)
    if password is None:
        # Only a pause the page itself ended (the sign-in page went away
        # while Jack was there) counts as Jack signing in. A page of no kind
        # after a pause nobody answered is parked (Jack, 2026-10-20).
        if how == pause_mod.FINISHED:
            res.events.append({"kind": "login_wall", "detail": "signed in by Jack", "handled": "jack"})
            return res
        return Result(PARKED, f"forgot-password reset: no password pasted ({how})", res.steps, res.events)
    # Back to a sign-in page: go back until one shows, or give up.
    for _ in range(4):
        if pages.page_kind(page) == pages.SIGN_IN:
            break
        try:
            page.go_back(timeout=10_000)
            browser_mod.settle(page, 800)
        except Exception:
            break
    if pages.page_kind(page) != pages.SIGN_IN:
        del password
        return Result(PARKED, "couldn't get back to the sign-in page after the reset", res.steps,
                      res.events)
    host = _host(page)
    ok = _sign_in_with(page, prof, password, res)
    if ok:
        _remember(host, password, res.steps)
    del password
    if not ok:
        return Result(PARKED, "the password you pasted didn't sign in", res.steps, res.events)
    return res


def _is_reset_page(page: "Page") -> bool:
    """
    A reset page as this module will act on one: no password or code box
    (page_kind None), and exactly one visible text box, which reads as the
    email box. Anything else is some other page, and nothing is pressed.
    """
    if pages.page_kind(page) is not None:
        return False
    try:
        boxes = page.evaluate(_TEXT_BOXES_JS, _TEXT_ATTR) or []
    except Exception:
        return False
    return (len(boxes) == 1 and isinstance(boxes[0], dict)
            and (boxes[0].get("type") == "email"
                 or bool(EMAIL_BOX.search(str(boxes[0].get("words") or "")))))


def _sign_in_with(page: "Page", prof, password: str | None, res: Result,
                  out: dict | None = None, what: str = "the password you set") -> bool:
    """
    Email, the password if given (once), Sign in. True if the sign-in page
    went away. `out`, if given, records whether the password was typed and
    the button pressed, so a caller can tell a failed attempt from none.
    """
    out = out if out is not None else {}
    _fill_identity(page, prof, res.steps)
    if password is not None:
        try:
            boxes = page.evaluate(_PASSWORD_BOXES_JS, _PW_ATTR) or []
        except Exception:
            boxes = []
        current = [b for b in boxes if isinstance(b, dict) and b.get("role") == "current"]
        if len(current) != 1 or not _type_secret(page, current[0]["key"], password):
            return False
        out["typed"] = True
        res.steps.append(f"typed {what}, once")
    if "sign_in" in workday_controls(page):
        pressed = "Sign In (Workday)" if _press_workday(page, "sign_in", pages.SIGN_IN) else None
    else:
        pressed = _press(page, SIGN_IN_WORDS, pages.SIGN_IN, "Sign in")
    if pressed is None:
        return False
    out["pressed"] = True
    res.steps.append(f"pressed '{pressed}'")
    return pages.page_kind(page) != pages.SIGN_IN


def sign_in(page: "Page", prof, pauser: pause_mod.Pauser) -> Result:
    """
    A sign-in page: Google first, if offered; else the email, Sign in (a
    saved password may be filled); then a Create account link if there is
    one; then Forgot password.
    """
    res = Result(CONTINUE)
    if pages.page_kind(page) != pages.SIGN_IN:
        return res
    res.events.append({"kind": "login_wall", "detail": "handled by the agent", "handled": "agent"})
    g = _google_first(page, prof, pauser, res, pages.SIGN_IN)
    if g == google.STUCK or (g is not None and google.is_google_host(page.url)):
        return Result(PARKED, GOOGLE_STUCK, res.steps, res.events)    # or bounced back to Google
    if g is not None and pages.page_kind(page) != pages.SIGN_IN:
        res.steps.append("the sign-in page went away after the Google button")
        return res
    if pages.is_mygreenhouse_sign_in(page.url):
        # MyGreenhouse signs in by Google or an emailed security code: no
        # password, so no password flow is invented (docs/PLAN-gui.md item 5);
        # its "Sign in" link leads to the employer's login and is never pressed.
        _, how = pauser.wait("job-agent needs you",
                             "MyGreenhouse wants you to sign in: use Continue with Google or the "
                             "security code it emails you, in this tab, then press Enter.",
                             prompt="Press Enter when you're signed in: ",
                             until=lambda: pages.page_kind(page) != pages.SIGN_IN)
        if pages.page_kind(page) == pages.SIGN_IN:
            return Result(PARKED, f"MyGreenhouse's sign-in page is still showing ({how})",
                          res.steps, res.events)
        res.events.append({"kind": "login_wall", "detail": "finished by Jack", "handled": "jack"})
        return res
    # A password stored for this host (Jack, 2026-10-20): typed once; one
    # that fails is forgotten, not retried, and the flow goes on as before.
    host = _host(page)
    stored = secrets_store.get(host) if host else None
    tried_stored = False
    if stored is not None:
        attempt: dict = {}
        ok = _sign_in_with(page, prof, stored, res, attempt, what="the stored password")
        del stored
        if ok:
            res.steps.append("signed in (the stored password)")
            res.events.append({"kind": "login_wall", "detail": "signed in with the stored password",
                               "handled": "agent"})
            return res
        if attempt.get("typed") and attempt.get("pressed"):
            tried_stored = True
            secrets_store.forget(host)
            res.steps.append(f"the stored password for {host} didn't sign in; forgotten, not retried")
    if not tried_stored and _sign_in_with(page, prof, None, res):
        res.steps.append("signed in (a saved password)")
        return res
    if "create_link" in workday_controls(page):
        link = "Create Account (Workday)" if _press_workday(page, "create_link", pages.SIGN_IN) else None
    else:
        link = _press(page, CREATE_LINK_WORDS, pages.SIGN_IN, "Create account")
    if link is not None:
        res.steps.append(f"pressed '{link}'")
        if pages.page_kind(page) == pages.ACCOUNT:
            created = sign_up(page, prof, pauser)
            res.steps += created.steps
            res.events += created.events
            if created.status == CONTINUE and pages.page_kind(page) != pages.SIGN_IN:
                return res
        # Back on, or still on, a sign-in page: the account exists already.
        for _ in range(3):
            if pages.page_kind(page) == pages.SIGN_IN:
                break
            try:
                page.go_back(timeout=10_000)
                browser_mod.settle(page, 800)
            except Exception:
                break
        if pages.page_kind(page) != pages.SIGN_IN:
            return Result(PARKED, "the account page didn't lead anywhere the agent can sign in",
                          res.steps, res.events)
    if getattr(pauser, "deferring", False):
        # GUI mode: no password passes through the GUI, so no reset is
        # requested; Jack signs in in this tab himself (Jack, 2026-10-19).
        pauser.wait("job-agent needs you",
                    "This portal wants a sign-in the agent can't do here. Sign in yourself in "
                    "this tab, then press Continue.", prompt="", hidden=True)
        return res
    return forgot_password(page, prof, pauser, res)


def enter_code(page: "Page", code: str, steps: list[str]) -> bool:
    """
    Type a verification code Jack gave into the one one-time-code box and
    press Verify. The code is typed, never logged; True if it was typed.
    """
    code = str(code or "").strip()
    if not code or _on_google(page) or pages.page_kind(page) != pages.VERIFICATION:
        return False
    try:
        n = page.evaluate(_CODE_BOX_JS, _CODE_ATTR)
    except Exception:
        n = 0
    if n != 1 or not _fill_text(page, _CODE_ATTR, "1", code):
        return False
    steps.append("typed the code you gave")
    pressed = _press(page, VERIFY_WORDS, pages.VERIFICATION, "Verify")
    if pressed is not None:
        steps.append(f"pressed '{pressed}'")
    return True


def verify(page: "Page", prof, pauser: pause_mod.Pauser) -> Result:
    """A verification page: pause for the code (typed in, then Verify) or for Jack to finish."""
    res = Result(CONTINUE)
    if pages.page_kind(page) != pages.VERIFICATION:
        return res
    res.events.append({"kind": "verification", "detail": "paused for Jack", "handled": "jack"})
    code, how = pauser.wait(
        "job-agent needs you",
        f"A verification code or link was sent to {_email(prof)}. Paste the code, or finish "
        f"the step in the browser.",
        prompt="Verification code: ", wants_code=True,
        until=lambda: pages.page_kind(page) != pages.VERIFICATION)
    if code is not None and code.strip():
        enter_code(page, code, res.steps)
    if pages.page_kind(page) == pages.VERIFICATION:
        return Result(PARKED, f"verification: still waiting ({how})", res.steps, res.events)
    return res


def handle(page: "Page", kind: str, prof, pauser: pause_mod.Pauser) -> Result:
    """The step for a page of this kind; CONTINUE when the page has moved on."""
    if kind == pages.ACCOUNT:
        return sign_up(page, prof, pauser)
    if kind == pages.SIGN_IN:
        return sign_in(page, prof, pauser)
    if kind == pages.VERIFICATION:
        return verify(page, prof, pauser)
    return Result(CONTINUE)
