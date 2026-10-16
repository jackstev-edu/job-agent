"""
pages.py — in follow mode, which pages the agent may fill at all.

WHAT THIS DOES: Tells, from the page's structure alone, an account page (two
or more password boxes, or a new-password box), a sign-in page (exactly one
password box) and a verification page (a one-time-code box) apart from an
application page; and decides whether a page is on the application's own
host family. LinkedIn is refused outright. Nothing here reads a value or
focuses anything: only counts and yes/no come back from the page.
WHY IT MATTERS: On those pages you type your email and codes, Chrome fills
passwords, and you do SSO; the agent fills nothing and reads nothing, never
focuses a password box and never ticks terms. Filling another site's form,
or anything on LinkedIn, is never what you asked for.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from urllib.parse import urlparse

if TYPE_CHECKING:
    from playwright.sync_api import Page

ACCOUNT, SIGN_IN, VERIFICATION = "account_page", "sign_in_page", "verification_page"
OFF_HOST, LINKEDIN = "off_host", "linkedin_refused"

# Counts only. A one-time-code box is the autocomplete hint, or a box whose
# name, id, aria-label or placeholder says one-time / OTP / verification /
# security code. Hidden boxes count too: a page can show them later.
_KIND_JS = r"""
() => {
  const pw = document.querySelectorAll('input[type=password i]').length;
  const newPw = !!document.querySelector('[autocomplete*="new-password" i]');
  const codeRx = /one[\s_-]?time|\botp\b|verification[\s_-]*code|verify[\s_-]*code|security[\s_-]*code|auth\w*[\s_-]*code/i;
  const code = !!document.querySelector('[autocomplete="one-time-code" i]')
    || [...document.querySelectorAll('input')].some(el =>
         codeRx.test([el.name, el.id, el.getAttribute('aria-label'),
                      el.getAttribute('placeholder')].filter(Boolean).join(' ')));
  return {pw, newPw, code};
}
"""


def page_kind(page: "Page") -> str | None:
    """
    ACCOUNT, SIGN_IN, VERIFICATION, or None for any other page. A page that
    can't be read counts as an account page: the strictest, fills nothing.
    """
    try:
        k = page.evaluate(_KIND_JS)
    except Exception:
        return ACCOUNT
    if not isinstance(k, dict):
        return ACCOUNT
    pw = int(k.get("pw") or 0)
    if pw >= 2 or k.get("newPw"):
        return ACCOUNT
    if pw == 1:
        return SIGN_IN
    if k.get("code"):
        return VERIFICATION
    return None


# Second-level labels under which a registrable domain takes three labels
# (example.co.uk). A short list is enough: a miss only makes the family
# narrower, which means filling less.
_SECOND_LEVEL = {"co", "com", "net", "org", "gov", "ac", "edu"}


def host_family(url: str) -> str:
    """The registrable domain a URL's host belongs to (job-boards.greenhouse.io -> greenhouse.io)."""
    host = (urlparse(url or "").hostname or "").lower().rstrip(".")
    labels = host.split(".")
    if len(labels) >= 3 and labels[-2] in _SECOND_LEVEL and len(labels[-1]) == 2:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def is_linkedin(url: str) -> bool:
    host = (urlparse(url or "").hostname or "").lower().rstrip(".")
    return host == "linkedin.com" or host.endswith(".linkedin.com")


def fill_allowed(page: "Page", start_url: str) -> str | None:
    """
    None if the agent may fill this page; otherwise the event to log and
    the reason it fills nothing: LinkedIn, another host family, or an
    account / sign-in / verification page.
    """
    try:
        url = page.url
    except Exception:
        return OFF_HOST
    if is_linkedin(url) or is_linkedin(start_url):
        return LINKEDIN
    if host_family(url) != host_family(start_url):
        return OFF_HOST
    return page_kind(page)
