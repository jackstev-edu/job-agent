"""
google.py — Google's own pages: recognised by exact host, never driven.

WHAT THIS DOES: Says whether an address is Google's sign-in site (exact
https hostname, nothing looser), and opens that site in the agent's own
Chrome profile for Jack to sign in by hand (`apply.py google-login`). It
types nothing, reads nothing from the page, and presses nothing there.
Item 4 of docs/PLAN-gui.md adds the account chooser and consent screen,
by structure, under the 2026-10-19 decisions.
WHY IT MATTERS: The profile may hold Jack's Google session only because he
put it there himself; the agent never touches Google's login page, a
2-step challenge or a "this browser may not be secure" page.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from urllib.parse import urlparse

from . import browser as browser_mod

if TYPE_CHECKING:
    from playwright.sync_api import BrowserContext, Page

LOGIN_URL = "https://accounts.google.com/"
HOST = "accounts.google.com"


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
