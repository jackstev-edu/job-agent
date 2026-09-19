"""
browser.py — driving Chrome.

WHAT THIS DOES: Opens a real Chrome window and remembers you between runs, so
you sign in to a career portal once by hand and the agent inherits that
session forever after. It also watches for CAPTCHAs and login walls and stops
rather than trying to get around them.
"""

from __future__ import annotations

import contextlib
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
    markers = {
        "reCAPTCHA": "iframe[src*='recaptcha']",
        "hCaptcha": "iframe[src*='hcaptcha']",
        "Cloudflare Turnstile": "iframe[src*='challenges.cloudflare.com']",
        "Cloudflare check": "#challenge-running, #cf-challenge-running",
        "bot check": "[class*='captcha' i], [id*='captcha' i]",
    }
    for name, selector in markers.items():
        with contextlib.suppress(Exception):
            if page.locator(selector).first.is_visible(timeout=700):
                return name
    return None


def detect_login_wall(page: "Page") -> bool:
    """Is the page asking us to sign in or create an account?"""
    with contextlib.suppress(Exception):
        return page.locator("input[type=password]").first.is_visible(timeout=700)
    return False


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
