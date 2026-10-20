"""
google.py — Google's own pages: recognised by exact host, driven by structure only.

WHAT THIS DOES: Says whether an address is Google's sign-in site (exact
https hostname, nothing looser); opens that site in the agent's own Chrome
profile for Jack to sign in by hand (`apply.py google-login`); and, after a
portal's "Continue with Google" button, walks the pages Google shows under
the 2026-10-19 decisions: on the account chooser it may click the entry
whose email equals the profile email; on the consent screen it may press
Continue / Allow only when every permission line on the page equals,
after NFKC, casefold and collapsed whitespace, an entry of
ALLOWED_PERMISSIONS (name, email address, profile picture: Google's own
wording, transcribed). Any other page (a sign-in box, a code box, a
checkbox, "this browser may not be secure", no permission list, an extra
line) pauses for Jack with nothing typed. It never types anywhere.
WHY IT MATTERS: The profile may hold Jack's Google session only because he
put it there himself; the agent never touches Google's login page, and
page text here can only fail an exact comparison, never pass one.
"""

from __future__ import annotations

import contextlib
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
# the openid / email / profile scopes, transcribed by the main session
# from Jack's 2026-10-19 answer (name, email address, profile picture /
# basic profile). Compared exactly after _norm; anything else pauses.
# Google's one-sentence form ("Google will share your name, email address,
# language preference, and profile picture with X") is not accepted: it
# names language preference, which is not on Jack's list.
ALLOWED_PERMISSIONS = (
    "See your primary Google Account email address",
    "See your personal info, including any personal info you've made publicly available",
    "Associate you with your personal info on Google",
    "Name and profile picture",
    "Email address",
)

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
# could type into (hidden ones too: Google shows them later), every tick
# box, the chooser entries' identifiers, every visible leaf list item on
# the whole page (a permission line anywhere counts), the Continue /
# Allow count and the "not secure" notice.
_SHAPE_JS = r"""
(okRx) => {
  const OK = new RegExp(okRx, 'i');
  const norm = t => String(t || '').replace(/\s+/g, ' ').trim();
  const visible = el => {
    const r = el.getBoundingClientRect(), s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
  };
  const boxes = [...document.querySelectorAll('input, textarea, select')].filter(el =>
    !['hidden', 'submit', 'button', 'reset', 'image', 'checkbox', 'radio'].includes((el.type || '').toLowerCase())).length;
  const ticks = document.querySelectorAll('input[type=checkbox i], input[type=radio i], [role=checkbox], [role=radio], [role=switch]').length;
  const ids = [...document.querySelectorAll('[data-identifier]')].filter(visible)
    .map(el => norm(el.getAttribute('data-identifier')));
  const entries = [...document.querySelectorAll('li')].filter(visible).filter(li => !li.querySelector('li'))
    .map(li => norm(li.innerText));
  const btnText = b => norm(b.innerText || (b.tagName === 'INPUT' ? b.value : '') || b.getAttribute('aria-label'));
  const ok = [...document.querySelectorAll('button, [role=button], input[type=submit], input[type=button]')]
    .filter(visible).filter(b => OK.test(btnText(b))).length;
  const insecure = /browser or app may not be secure|couldn.t sign you in/i.test(norm(document.body.innerText));
  return {boxes, ticks, ids, entries, ok, insecure};
}
"""

# Marks the i-th visible chooser entry (and returns its identifier, for a
# second look before the click), or the single Continue / Allow.
_MARK_JS = r"""
([what, index, attr, okRx]) => {
  const OK = new RegExp(okRx, 'i');
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
      .filter(visible).filter(b => OK.test(btnText(b)));
    if (list.length !== 1) return null;
  }
  const el = list[index];
  if (!el) return null;
  el.setAttribute(attr, '1');
  return what === 'account' ? norm(el.getAttribute('data-identifier')) : 'ok';
}
"""


def classify(page: "Page") -> tuple[str, dict]:
    """
    CHOOSER (account entries, no box to type into), CONSENT (permission
    lines with one Continue / Allow, no box, no tick box), or OTHER: a
    sign-in box, a code box, a "not secure" notice, or anything the
    script can't describe. Default deny.
    """
    try:
        shape = page.evaluate(_SHAPE_JS, OK_WORDS.pattern)
    except Exception:
        return OTHER, {}
    if (not isinstance(shape, dict) or not isinstance(shape.get("boxes"), int)
            or not isinstance(shape.get("ticks"), int) or not isinstance(shape.get("ids"), list)
            or not isinstance(shape.get("entries"), list) or not isinstance(shape.get("ok"), int)):
        return OTHER, {}
    if shape["boxes"] > 0 or shape.get("insecure"):
        return OTHER, shape
    if shape["ids"]:
        return CHOOSER, shape
    if shape["entries"] and shape["ok"] == 1 and shape["ticks"] == 0:
        return CONSENT, shape
    return OTHER, shape


def permissions_ok(entries: list) -> bool:
    """Every listed permission is one of ALLOWED_PERMISSIONS; an empty list fails."""
    allowed = {_norm(a) for a in ALLOWED_PERMISSIONS}
    got = [_norm(e) for e in entries if isinstance(e, str)]
    return bool(got) and len(got) == len(entries) and all(g in allowed for g in got)


def _click_marked(page: "Page") -> bool:
    try:
        page.locator(f"[{_ATTR}='1']").first.click(timeout=4000)
    except Exception:
        return False
    return True


def pick_account(page: "Page", ids: list, email: str) -> bool:
    """
    Click the one chooser entry whose identifier equals the profile email.
    The entry is marked by its place in the shape read, then its
    identifier is read again and must still equal the email before the
    click, so a list that changed in between gets no click.
    """
    want = _norm(email)
    hits = [i for i, v in enumerate(ids) if isinstance(v, str) and want and _norm(v) == want]
    if len(hits) != 1:
        return False
    try:
        marked = page.evaluate(_MARK_JS, ["account", hits[0], _ATTR, OK_WORDS.pattern])
    except Exception:
        return False
    if not isinstance(marked, str) or _norm(marked) != want:
        return False
    return _click_marked(page)


def allow(page: "Page") -> bool:
    """Press the single Continue / Allow on a consent screen already judged."""
    try:
        if page.evaluate(_MARK_JS, ["ok", 0, _ATTR, OK_WORDS.pattern]) != "ok":
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
    STUCK when a pause for Jack didn't get it there. After a pause Jack
    ends with Enter the page is looked at again (he may have signed in and
    left the consent screen showing). Nothing is typed.
    """
    pressed, paused = 0, None
    for _ in range(STEP_CAP):
        if _left(page):
            break
        try:
            browser_mod.settle(page, 800)
        except Exception:
            break                          # closed meanwhile: left
        if _left(page):
            break
        kind, shape = classify(page)
        if kind == CHOOSER:
            if pick_account(page, shape.get("ids") or [], str(prof.get("personal.email") or "")):
                steps.append("chose the profile's account on Google's chooser")
                pressed += 1
                continue
            why = "Google's account chooser doesn't list the profile's account (or lists it twice)."
        elif kind == CONSENT:
            n = len(shape.get("entries") or [])
            if not permissions_ok(shape.get("entries") or []):
                why = (f"Google's permission screen asks for more than name, email address and "
                       f"profile picture ({n} line(s)), or the agent couldn't read the list.")
            elif not allow(page):
                why = "Google's permission screen has no single Continue / Allow the agent could press."
            else:
                steps.append("pressed Continue on Google's permission screen (name, email address, "
                             "profile picture only)")
                pressed += 1
                continue
        else:
            why = "Google is asking for a sign-in, a 2-step check or something else the agent never touches."
        paused = _pause(page, pauser, why)
        if paused not in (pause_mod.ANSWERED, pause_mod.FINISHED):
            break
    return _ended(page, paused, steps, events, pressed)


def _pause(page: "Page", pauser: pause_mod.Pauser, why: str) -> str:
    """Hand the Google window to Jack; in GUI mode this raises Deferred."""
    _, how = pauser.wait("job-agent needs you",
                         f"{why} Finish the Google step yourself in that window, then press Enter.",
                         prompt="Press Enter when Google is done: ", until=lambda: _left(page))
    return how


def _ended(page: "Page", how: str | None, steps: list[str], events: list[dict], pressed: int) -> str:
    if pressed:
        events.append({"kind": "google_sign_in", "detail": f"{pressed} Google page(s) by the agent",
                       "handled": "agent"})
    if not _left(page):
        return STUCK
    if how is not None:
        steps.append("Google's page was finished by you")
        events.append({"kind": "google_sign_in", "detail": f"finished by Jack ({how})",
                       "handled": "jack" if how in (pause_mod.ANSWERED, pause_mod.FINISHED) else None})
    return DONE
