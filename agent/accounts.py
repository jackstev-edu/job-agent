"""
accounts.py — sign-up, sign-in, forgot-password and verification pages.

WHAT THIS DOES: On a page pages.page_kind calls an account, sign-in or
verification page, does what the owner decisions allow: types the profile
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

import re
import secrets
import string
from dataclasses import dataclass, field as dataclass_field
from typing import TYPE_CHECKING

from . import browser as browser_mod, gate, pages, pause as pause_mod

if TYPE_CHECKING:
    from playwright.sync_api import Page

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
NEVER_WORDS = re.compile(r"^\s*(submit|continue|next|proceed|go|enter|done|finish)\s*$"
                         r"|submit (my |your |the )?application|apply|withdraw|delete|google"
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


def _press(page: "Page", words: re.Pattern, kind_now: str | None, what: str) -> str | None:
    """
    Press the one visible control reading as `words`, if the page is still
    of the kind this step is for. Returns the word pressed, or None.
    """
    if pages.page_kind(page) != kind_now:
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


def _type_secret(page: "Page", key: str, value: str) -> bool:
    """
    Type a password into one located password box. Nothing is read back,
    nothing is logged, and the value isn't kept here. True if the keystrokes
    were sent. Only ever called from this module.
    """
    try:
        box = _box(page, _PW_ATTR, key)
        box.click(timeout=4000)
        box.fill("", timeout=4000)
        box.type(value, delay=8, timeout=20_000)
        return True
    except Exception:
        return False


def _fill_text(page: "Page", attr: str, key: str, value: str) -> bool:
    try:
        box = _box(page, attr, key)
        box.click(timeout=4000)
        box.fill(value, timeout=4000)
        return True
    except Exception:
        return False


def _fill_identity(page: "Page", prof, steps: list[str]) -> None:
    """The email (and a first / last name if boxes are labelled so) from the profile."""
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
    typed = all(_type_secret(page, b["key"], password) for b in new_boxes)
    del password
    if not typed:
        return Result(PARKED, "couldn't type into the new-password boxes", res.steps)
    res.steps.append(f"typed a generated password into {len(new_boxes)} box(es)")
    _tick_consent(page, res.steps)
    pressed = _press(page, CREATE_WORDS, pages.ACCOUNT, "Create account")
    if pressed is None:
        return Result(PARKED, "no single Create account button to press", res.steps)
    res.steps.append(f"pressed '{pressed}'")
    res.events.append({"kind": "account_created", "detail": "by the agent", "handled": "agent"})
    kind = pages.page_kind(page)
    if kind == pages.ACCOUNT:
        _, how = pauser.wait("job-agent needs you", "The sign-up page didn't go through. Finish "
                             "creating the account in the browser, then press Enter here.",
                             prompt="Press Enter when the account exists: ",
                             until=lambda: pages.page_kind(page) != pages.ACCOUNT)
        if pages.page_kind(page) == pages.ACCOUNT:
            return Result(PARKED, f"the account page didn't accept the sign-up ({how})", res.steps,
                          res.events)
        res.events.append({"kind": "account_page", "detail": "finished by Jack", "handled": "jack"})
    # A verification page usually follows a sign-up: handled in the same step.
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
    pressed = _press(page, FORGOT_WORDS, pages.SIGN_IN, "Forgot password")
    if pressed is None:
        return Result(PARKED, "sign-in failed and there is no Forgot password link", res.steps,
                      res.events)
    res.steps.append(f"pressed '{pressed}'")
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
        kind = pages.page_kind(page)
        if how == pause_mod.FINISHED or kind not in (pages.SIGN_IN, pages.ACCOUNT):
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
    ok = _sign_in_with(page, prof, password, res)
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


def _sign_in_with(page: "Page", prof, password: str | None, res: Result) -> bool:
    """Email, the password if given (once), Sign in. True if the sign-in page went away."""
    _fill_identity(page, prof, res.steps)
    if password is not None:
        try:
            boxes = page.evaluate(_PASSWORD_BOXES_JS, _PW_ATTR) or []
        except Exception:
            boxes = []
        current = [b for b in boxes if isinstance(b, dict) and b.get("role") == "current"]
        if len(current) != 1 or not _type_secret(page, current[0]["key"], password):
            return False
        res.steps.append("typed the password you set, once")
    pressed = _press(page, SIGN_IN_WORDS, pages.SIGN_IN, "Sign in")
    if pressed is None:
        return False
    res.steps.append(f"pressed '{pressed}'")
    return pages.page_kind(page) != pages.SIGN_IN


def sign_in(page: "Page", prof, pauser: pause_mod.Pauser) -> Result:
    """
    A sign-in page: the email, Sign in (a saved password may be filled);
    then a Create account link if there is one; then Forgot password.
    """
    res = Result(CONTINUE)
    if pages.page_kind(page) != pages.SIGN_IN:
        return res
    res.events.append({"kind": "login_wall", "detail": "handled by the agent", "handled": "agent"})
    if _sign_in_with(page, prof, None, res):
        res.steps.append("signed in (a saved password)")
        return res
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
    return forgot_password(page, prof, pauser, res)


def verify(page: "Page", prof, pauser: pause_mod.Pauser) -> Result:
    """A verification page: pause for the code (typed in, then Verify) or for Jack to finish."""
    res = Result(CONTINUE)
    if pages.page_kind(page) != pages.VERIFICATION:
        return res
    res.events.append({"kind": "verification", "detail": "paused for Jack", "handled": "jack"})
    code, how = pauser.wait(
        "job-agent needs you",
        f"A verification code or link was sent to {_email(prof)}. Paste the code in the "
        f"terminal, or finish the step in the browser.",
        prompt="Verification code: ",
        until=lambda: pages.page_kind(page) != pages.VERIFICATION)
    if code is not None and code.strip():
        try:
            n = page.evaluate(_CODE_BOX_JS, _CODE_ATTR)
        except Exception:
            n = 0
        if n == 1 and _fill_text(page, _CODE_ATTR, "1", code.strip()):
            res.steps.append("typed the code you pasted")
            pressed = _press(page, VERIFY_WORDS, pages.VERIFICATION, "Verify")
            if pressed is not None:
                res.steps.append(f"pressed '{pressed}'")
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
