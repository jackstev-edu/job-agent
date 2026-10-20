"""
google.py — Google's own pages: recognised by exact host, driven by structure only.

WHAT THIS DOES: Says whether an address is Google's sign-in site (exact
https hostname, nothing looser); opens that site in the agent's own Chrome
profile for Jack to sign in by hand (`apply.py google-login`); and, after a
portal's "Continue with Google" button, walks the pages Google shows under
the 2026-10-19 decisions: on the account chooser it may click the entry
whose email equals the profile email; on the consent screen it may press
Continue / Allow only when every permission listed equals, after NFKC,
casefold and collapsed whitespace, an entry of ALLOWED_PERMISSIONS (name,
email address, profile picture: Google's own wording, transcribed). Any
other page (a sign-in box, a 2-step check, "this browser may not be
secure", an unparsed or empty permission list, an extra entry) pauses for
Jack with nothing typed. It never types anywhere.
WHY IT MATTERS: The profile may hold Jack's Google session only because he
put it there himself; the agent never touches Google's login page, and
page text here can only fail an exact comparison, never pass one.
"""

from __future__ import annotations

import re
import unicodedata
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from . import browser as browser_mod, pause as pause_mod

if TYPE_CHECKING:
    from playwright.sync_api import BrowserContext, Page

LOGIN_URL = "https://accounts.google.com/"
HOST = "accounts.google.com"

# A portal's control that starts Google sign-in, as whole normalised text.
BUTTON_WORDS = re.compile(r"^((sign|log) ?in|login|continue|sign ?up|register) with google$", re.I)

# The permission lines a consent screen may show, in Google's wording for
# the openid / email / profile scopes, as Jack approved them on
# 2026-10-19. Compared exactly after _norm; anything else pauses.
ALLOWED_PERMISSIONS = (
    "See your primary Google Account email address",
    "See your personal info, including any personal info you've made publicly available",
    "Associate you with your personal info on Google",
    "Name and profile picture",
    "Email address",
)
# The items of Google's one-sentence form ("To continue, Google will share
# your name, email address, and profile picture with X"), same rule.
ALLOWED_SHARE_ITEMS = ("name", "email address", "profile picture")

OK_WORDS = re.compile(r"^(continue|allow)$", re.I)

CHOOSER, CONSENT, OTHER = "chooser", "consent", "other"
DONE, STUCK = "done", "stuck"
STEP_CAP = 4
_ATTR = "data-ja-google"


def is_google(url: str) -> bool:
    """Exactly accounts.google.com over https: not a look-alike host or a path holding the words."""
    try:
        parts = urlparse(url or "")
    except Exception:
        return False
    return parts.scheme == "https" and (parts.hostname or "").lower() == HOST


def open_login_tab(ctx: "BrowserContext") -> "Page":
    """A new tab on Google's sign-in site, for Jack's hands only."""
    page = ctx.new_page()
    page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=60_000)
    browser_mod.settle(page, 500)
    return page


def _norm(t) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", str(t or ""))).casefold().strip()


# The page's shape, nothing typed and nothing marked: every box the agent
# could type into (hidden ones too: Google shows them later), the chooser
# entries' identifiers, the permission lines (leaf list items in the main
# content), the one-sentence share form, the Continue / Allow count and
# the "not secure" notice.
_SHAPE_JS = r"""
() => {
  const norm = t => String(t || '').replace(/\s+/g, ' ').trim();
  const visible = el => {
    const r = el.getBoundingClientRect(), s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
  };
  const boxes = [...document.querySelectorAll('input, textarea, select')].filter(el =>
    !['hidden', 'submit', 'button', 'reset', 'image', 'checkbox', 'radio'].includes((el.type || '').toLowerCase())).length;
  const ids = [...document.querySelectorAll('[data-identifier]')].filter(visible)
    .map(el => norm(el.getAttribute('data-identifier')));
  const main = document.querySelector('[role=main], main, form') || document.body;
  const entries = [...main.querySelectorAll('li')].filter(visible).filter(li => !li.querySelector('li'))
    .map(li => norm(li.innerText));
  const text = norm(document.body.innerText);
  const share = (text.match(/(?:to continue|by continuing),? google will share your (.+?) with /i) || [])[1] || '';
  const btnText = b => norm(b.innerText || (b.tagName === 'INPUT' ? b.value : '') || b.getAttribute('aria-label'));
  const ok = [...document.querySelectorAll('button, [role=button], input[type=submit], input[type=button]')]
    .filter(visible).filter(b => /^(continue|allow)$/i.test(btnText(b))).length;
  const insecure = /browser or app may not be secure|couldn.t sign you in/i.test(text);
  return {boxes, ids, entries, share, ok, insecure};
}
"""

# Marks the i-th visible chooser entry, or the single Continue / Allow.
_MARK_JS = r"""
([what, index, attr]) => {
  const norm = t => String(t || '').replace(/\s+/g, ' ').trim();
  const visible = el => {
    const r = el.getBoundingClientRect(), s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
  };
  document.querySelectorAll('[' + attr + ']').forEach(e => e.removeAttribute(attr));
  let list;
  if (what === 'account') list = [...document.querySelectorAll('[data-identifier]')].filter(visible);
  else {
    const btnText = b => norm(b.innerText || (b.tagName === 'INPUT' ? b.value : '') || b.getAttribute('aria-label'));
    list = [...document.querySelectorAll('button, [role=button], input[type=submit], input[type=button]')]
      .filter(visible).filter(b => /^(continue|allow)$/i.test(btnText(b)));
    if (list.length !== 1) return false;
  }
  const el = list[index];
  if (!el) return false;
  el.setAttribute(attr, '1');
  return true;
}
"""


def classify(page: "Page") -> tuple[str, dict]:
    """
    CHOOSER (account entries, no box to type into), CONSENT (a permission
    list or share sentence with one Continue / Allow, no box), or OTHER:
    a sign-in box, a code box, a "not secure" notice, or anything the
    script can't describe. Default deny.
    """
    try:
        shape = page.evaluate(_SHAPE_JS)
    except Exception:
        return OTHER, {}
    if (not isinstance(shape, dict) or not isinstance(shape.get("boxes"), int)
            or not isinstance(shape.get("ids"), list) or not isinstance(shape.get("entries"), list)
            or not isinstance(shape.get("ok"), int)):
        return OTHER, {}
    if shape["boxes"] > 0 or shape.get("insecure"):
        return OTHER, shape
    if shape["ids"]:
        return CHOOSER, shape
    if (shape["entries"] or shape.get("share")) and shape["ok"] == 1:
        return CONSENT, shape
    return OTHER, shape


def permissions_ok(entries: list, share) -> bool:
    """
    Every listed permission is one of ALLOWED_PERMISSIONS; with no list,
    every item of the share sentence is one of ALLOWED_SHARE_ITEMS. An
    empty or unparsed list fails.
    """
    allowed = {_norm(a) for a in ALLOWED_PERMISSIONS}
    if entries:
        got = [_norm(e) for e in entries if isinstance(e, str)]
        return bool(got) and len(got) == len(entries) and all(g in allowed for g in got)
    items = [_norm(i) for i in re.split(r",|\band\b", str(share or "")) if _norm(i)]
    return bool(items) and all(i in {_norm(a) for a in ALLOWED_SHARE_ITEMS} for i in items)


def _click_marked(page: "Page") -> bool:
    try:
        page.locator(f"[{_ATTR}='1']").first.click(timeout=4000)
    except Exception:
        return False
    return True


def pick_account(page: "Page", ids: list, email: str) -> bool:
    """Click the one chooser entry whose identifier equals the profile email."""
    want = _norm(email)
    hits = [i for i, v in enumerate(ids) if isinstance(v, str) and want and _norm(v) == want]
    if len(hits) != 1:
        return False
    try:
        if not page.evaluate(_MARK_JS, ["account", hits[0], _ATTR]):
            return False
    except Exception:
        return False
    return _click_marked(page)


def allow(page: "Page") -> bool:
    """Press the single Continue / Allow on a consent screen already judged."""
    try:
        if not page.evaluate(_MARK_JS, ["ok", 0, _ATTR]):
            return False
    except Exception:
        return False
    return _click_marked(page)


def _left(page: "Page") -> bool:
    try:
        return page.is_closed() or not is_google(page.url)
    except Exception:
        return True


def drive(page: "Page", prof, pauser: pause_mod.Pauser, steps: list[str],
          events: list[dict]) -> str:
    """
    Walk Google's pages after a portal's Google button, on `page` (the
    same tab or a popup). DONE once the page has left Google (or closed);
    STUCK when a pause for Jack didn't get it there. Nothing is typed.
    """
    pressed = 0
    for _ in range(STEP_CAP):
        if _left(page):
            break
        browser_mod.settle(page, 800)
        if _left(page):
            break
        kind, shape = classify(page)
        if kind == CHOOSER:
            if not pick_account(page, shape.get("ids") or [], str(prof.get("personal.email") or "")):
                how = _pause(page, pauser, "Google's account chooser doesn't list the profile's "
                                           "account (or lists it twice).")
                return _ended(page, how, steps, events, pressed)
            steps.append("chose the profile's account on Google's chooser")
            pressed += 1
            continue
        if kind == CONSENT:
            n = len(shape.get("entries") or [])
            if not permissions_ok(shape.get("entries") or [], shape.get("share")):
                how = _pause(page, pauser, f"Google's permission screen asks for more than name, "
                                           f"email address and profile picture ({n} line(s)), "
                                           f"or the agent couldn't read the list.")
                return _ended(page, how, steps, events, pressed)
            if not allow(page):
                how = _pause(page, pauser, "Google's permission screen has no single Continue / "
                                           "Allow the agent could press.")
                return _ended(page, how, steps, events, pressed)
            steps.append("pressed Continue on Google's permission screen (name, email address, "
                         "profile picture only)")
            pressed += 1
            continue
        how = _pause(page, pauser, "Google is asking for a sign-in, a 2-step check or something "
                                   "else the agent never touches.")
        return _ended(page, how, steps, events, pressed)
    if pressed:
        events.append({"kind": "google_sign_in", "detail": f"{pressed} Google page(s) by the agent",
                       "handled": "agent"})
    return DONE if _left(page) else STUCK


def _pause(page: "Page", pauser: pause_mod.Pauser, why: str) -> str:
    """Hand the Google window to Jack; in GUI mode this raises Deferred."""
    _, how = pauser.wait("job-agent needs you",
                         f"{why} Finish the Google step yourself in that window, then press Enter.",
                         prompt="Press Enter when Google is done: ", until=lambda: _left(page))
    return how


def _ended(page: "Page", how: str, steps: list[str], events: list[dict], pressed: int) -> str:
    if pressed:
        events.append({"kind": "google_sign_in", "detail": f"{pressed} Google page(s) by the agent",
                       "handled": "agent"})
    if _left(page):
        steps.append("Google's page was finished by you")
        events.append({"kind": "google_sign_in", "detail": f"finished by Jack ({how})",
                       "handled": "jack" if how in (pause_mod.ANSWERED, pause_mod.FINISHED) else None})
        return DONE
    return STUCK
