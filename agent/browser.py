"""
browser.py — driving Chrome.

WHAT THIS DOES: Opens a real Chrome window and remembers you between runs, so
you sign in to a career portal once by hand and the agent inherits that
session forever after. It also watches for CAPTCHAs and login walls, and on a
login wall lets the browser's own password manager sign you in, or stops.

WHY IT MATTERS: The agent never reads, types, generates or stores a password.
A login saved in Chrome's password manager, inside the agent's own profile,
may fill the form; the agent only checks that the boxes are filled and
presses a plainly named sign-in button.
"""

from __future__ import annotations

import contextlib
import re
from typing import TYPE_CHECKING

from . import config

if TYPE_CHECKING:                      # for type hints only, never at runtime
    from playwright.sync_api import Page


@contextlib.contextmanager
def browser():
    """
    Open Chrome with a persistent profile.

    "Persistent" means cookies and logins are saved to a folder on your disk
    (~/.job-agent/chrome-profile) instead of vanishing when the window closes.
    This is why you only ever log in to Workday once per employer.
    """
    # Lazy import: see the note in mapper.py.
    from playwright.sync_api import sync_playwright

    config.ensure_dirs()
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            user_data_dir=str(config.CHROME_PROFILE_DIR),
            headless=config.HEADLESS,
            slow_mo=config.SLOW_MO,
            viewport={"width": 1440, "height": 1000},
            # Hides the "Chrome is being controlled by automated software"
            # banner. This is cosmetic — we are not trying to defeat bot
            # detection, and if a site blocks us we stop.
            args=["--disable-blink-features=AutomationControlled"],
        )
        try:
            yield ctx
        finally:
            ctx.close()


def open_page(ctx, url: str) -> "Page":
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    page.goto(url, wait_until="domcontentloaded", timeout=60_000)
    settle(page)
    return page


def settle(page: "Page", extra_ms: int = 1200) -> None:
    """
    Wait for the page to stop moving.

    Application portals render the form *after* the page loads, so "loaded"
    and "the form exists" are two different moments. Never rely on a fixed
    sleep alone — wait for the network to go quiet first.
    """
    with contextlib.suppress(Exception):
        page.wait_for_load_state("networkidle", timeout=15_000)
    page.wait_for_timeout(extra_ms)


def detect_captcha(page: "Page") -> str | None:
    """
    Spot a CAPTCHA or bot check and name it.

    We detect these to hand you the keyboard, not to solve them. Solving them
    is a losing arms race and the fastest way to get an account banned.
    """
    if _recaptcha_challenge(page):
        return "reCAPTCHA"
    markers = {
        "hCaptcha": "iframe[src*='hcaptcha']",
        "Cloudflare Turnstile": "iframe[src*='challenges.cloudflare.com']",
        "Cloudflare check": "#challenge-running, #cf-challenge-running",
        # reCAPTCHA is excluded here only because _recaptcha_challenge has
        # already judged it; its badge wrapper is .grecaptcha-badge, which
        # would otherwise trip this on every Greenhouse page.
        "bot check": "[class*='captcha' i]:not([class*='recaptcha' i]), "
                     "[id*='captcha' i]:not([id*='recaptcha' i])",
    }
    for name, selector in markers.items():
        if _any_visible(page, selector):
            return name
    return None


def _any_visible(page: "Page", selector: str) -> bool:
    # Every match, not .first: a hidden first match (a response textarea, a
    # pre-rendered container) used to hide a visible challenge behind it.
    with contextlib.suppress(Exception):
        return any(el.is_visible() for el in page.locator(selector).all()[:25])
    return False


def _recaptcha_challenge(page: "Page") -> bool:
    """
    Is reCAPTCHA actually asking for something right now?

    Invisible reCAPTCHA (Greenhouse loads it on every posting) keeps its anchor
    iframe on screen as the corner badge, with size=invisible in its src, so
    that iframe alone means nothing. A real challenge shows up as the bframe
    iframe becoming visible, or, for the checkbox kind, as an anchor without
    size=invisible. Anything else visible from reCAPTCHA counts too: only the
    badge has been shown to be harmless, so only the badge is excused.
    """
    with contextlib.suppress(Exception):
        for frame in page.locator("iframe[src*='recaptcha']").all()[:25]:
            if not frame.is_visible():
                continue
            src = frame.get_attribute("src") or ""
            if "/anchor" in src and "size=invisible" in src:
                continue
            return True
    return False


def detect_login_wall(page: "Page") -> bool:
    """Is the page asking us to sign in or create an account?"""
    with contextlib.suppress(Exception):
        return page.locator("input[type=password]").first.is_visible(timeout=700)
    return False


# The only buttons a saved login may press, matched against the whole label.
# Whole-label on purpose: "Continue with Google" would sign a Google account
# in, and "Sign in and apply" does more than sign in.
SIGN_IN_BUTTON = re.compile(r"^(sign\s?in|log\s?in|login|continue)$", re.I)

SIGN_IN_ATTR = "data-ja-signin"

# Reads the login form's SHAPE, never its contents. "Filled" is judged by
# Chrome's :autofill state or, failing that, by length alone, and only
# booleans and the button's label ever come back to Python.
_LOGIN_STATE_JS = r"""
(args) => {
  const [ATTR, BUTTON_RX] = args;
  const buttonRx = new RegExp(BUTTON_RX, 'i');
  const visible = el => {
    const r = el.getBoundingClientRect(), s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
  };
  const filled = el => {
    for (const sel of [':autofill', ':-webkit-autofill']) {
      try { if (el.matches(sel)) return true; } catch (e) {}
    }
    return el.value.length > 0;
  };
  const no = why => ({ready: false, final: true, why});

  const passwords = [...document.querySelectorAll('input[type=password]')].filter(visible);
  if (passwords.length === 0) return no('no password box on the page');
  // Two password boxes is "choose one and confirm it": account creation.
  if (passwords.length > 1) return no('more than one password box, which looks like account creation');
  const pw = passwords[0];
  if (/new-password/i.test(pw.getAttribute('autocomplete') || ''))
    return no('the password box is for a new password, which is account creation');

  const scope = pw.form || document;
  const texty = [...scope.querySelectorAll('input')].filter(el => el !== pw && visible(el)
      && ['', 'text', 'email', 'tel'].includes((el.getAttribute('type') || '').toLowerCase()));
  const user = texty.find(el => /username|email/i.test(el.getAttribute('autocomplete') || ''))
            || texty.find(el => (el.getAttribute('type') || '').toLowerCase() === 'email')
            || texty.find(el => /user|email|login/i.test(`${el.name} ${el.id}`))
            || texty.find(el => el.compareDocumentPosition(pw) & Node.DOCUMENT_POSITION_FOLLOWING);
  if (!user) return no('no username box next to the password box');

  const label = b => ((b.innerText || b.value || b.getAttribute('aria-label') || '')
      .replace(/\s+/g, ' ').replace(/[\s\u2192\u203a>]+$/, '').trim());
  const buttons = [...scope.querySelectorAll(
      'button, input[type=submit], input[type=button], [role=button]')]
      .filter(b => visible(b) && !b.disabled && buttonRx.test(label(b)));
  document.querySelectorAll(`[${ATTR}]`).forEach(b => b.removeAttribute(ATTR));
  if (buttons.length === 0) return no('no button labelled sign in, log in or continue');
  if (buttons.length > 1) return no('more than one sign-in button; not guessing which');
  buttons[0].setAttribute(ATTR, '1');

  const userFilled = filled(user), passwordFilled = filled(pw);
  return {ready: userFilled && passwordFilled, final: false, button: label(buttons[0]),
          why: userFilled && passwordFilled ? '' :
               'the browser did not fill ' + (userFilled ? 'the password' :
               passwordFilled ? 'the username' : 'either box')};
}
"""


def sign_in_with_saved_login(page: "Page", wait_ms: int = 5000) -> tuple[bool, str]:
    """
    Press sign-in if Chrome's password manager has filled the login form.

    Waits up to `wait_ms` for autofill, then clicks only if the username and
    the single password box are both filled and exactly one button reads
    sign in / log in / continue (and passes is_forbidden_button). Types
    nothing and never reads the password. Returns (signed_in, why_not).
    Account creation (two password boxes, a new-password box) is always
    left to you.
    """
    from .filler import is_forbidden_button   # filler imports nothing from here

    waited, state = 0, {}
    while True:
        try:
            state = page.evaluate(_LOGIN_STATE_JS, [SIGN_IN_ATTR, SIGN_IN_BUTTON.pattern])
        except Exception as e:
            return False, f"couldn't read the login form ({str(e)[:60]})"
        if state.get("ready") or state.get("final") or waited >= wait_ms:
            break
        page.wait_for_timeout(250)
        waited += 250
    if not state.get("ready"):
        return False, state.get("why") or "the login form wasn't filled"

    text = state.get("button") or ""
    if not SIGN_IN_BUTTON.match(text) or is_forbidden_button(text):
        return False, f"refused to press '{text}'"
    try:
        page.locator(f"[{SIGN_IN_ATTR}='1']").first.click(timeout=4000)
    except Exception as e:
        return False, f"couldn't press '{text}' ({str(e)[:60]})"
    settle(page)
    if detect_login_wall(page):
        return False, f"pressed '{text}' but the login page is still showing"
    return True, f"pressed '{text}'"


def active_frame(page: "Page", hint: str = ""):
    """
    Some older portals (iCIMS especially) put the whole form inside an iframe.
    Everything else in this tool works on whatever this returns.
    """
    if hint:
        for frame in page.frames:
            if hint in (frame.url or ""):
                return frame
    return page
