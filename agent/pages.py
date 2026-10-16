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

# Counts only. A password box is what every reader in extract.py refuses: a
# type=password box, a password autocomplete hint (a show-password toggle
# leaves type=text but keeps the hint) or the sticky data-ja-secret mark.
# A one-time-code box is the autocomplete hint, or a box whose name, id,
# aria-label or placeholder (camelCase split) says one-time / OTP / 2FA /
# MFA / two-factor / passcode / a verification, security, auth, SMS,
# confirmation or access code. Hidden boxes count too: a page can show
# them later.
_KIND_JS = r"""
() => {
  const pw = document.querySelectorAll(
      'input[type=password i], [autocomplete*="password" i], [data-ja-secret]').length;
  const newPw = !!document.querySelector('[autocomplete*="new-password" i]');
  const split = t => t.replace(/([a-z])([A-Z])/g, '$1 $2').replace(/[_-]+/g, ' ');
  const codeRx = /one\s?time|(?<![a-z])otp(?![a-z])|(?<![a-z])2fa(?![a-z])|(?<![a-z])mfa(?![a-z])|two\s?factor|passcode|(verification|verify|security|auth\w*|sms|confirmation|access)\s*code/i;
  const code = !!document.querySelector('[autocomplete="one-time-code" i]')
    || [...document.querySelectorAll('input')].some(el =>
         codeRx.test(split([el.name, el.id, el.getAttribute('aria-label'),
                            el.getAttribute('placeholder')].filter(Boolean).join(' '))));
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
    # Anything but the exact shape the script returns (say a page that
    # patched the DOM methods) counts as an account page too.
    if (not isinstance(k, dict) or not isinstance(k.get("pw"), int)
            or not isinstance(k.get("newPw"), bool) or not isinstance(k.get("code"), bool)):
        return ACCOUNT
    pw = k["pw"]
    if pw >= 2 or k.get("newPw"):
        return ACCOUNT
    if pw == 1:
        return SIGN_IN
    if k.get("code"):
        return VERIFICATION
    return None


# Second-level labels under which a registrable domain takes three labels
# (example.co.uk).
_SECOND_LEVEL = {"co", "com", "net", "org", "gov", "ac", "edu"}


def host_family(url: str) -> str:
    """
    The registrable domain a URL's host belongs to (job-boards.greenhouse.io
    -> greenhouse.io; careers.acme.co.uk -> acme.co.uk). When it can't be
    told for sure, the whole host: an IP address, or a two-letter country
    domain whose second label is short (three letters or fewer, the shape
    of a registry level like ne.jp or nhs.uk) and not in _SECOND_LEVEL.
    That can only make the family narrower, which means filling less.
    """
    host = (urlparse(url or "").hostname or "").lower().rstrip(".")
    labels = host.split(".")
    if not host or ":" in host or all(lab.isdigit() for lab in labels):
        return host
    if len(labels) >= 3 and len(labels[-1]) == 2:
        if labels[-2] in _SECOND_LEVEL:
            return ".".join(labels[-3:])
        if len(labels[-2]) <= 3:
            return host
    return ".".join(labels[-2:])


def is_linkedin(url: str) -> bool:
    host = (urlparse(url or "").hostname or "").lower().rstrip(".")
    return (host in ("linkedin.com", "linkedin.cn", "lnkd.in")
            or host.endswith((".linkedin.com", ".linkedin.cn", ".lnkd.in")))


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
    # No host on either side (a missing start, about:blank, a local file)
    # is never "the same site".
    if not host_family(url) or not host_family(start_url):
        return OFF_HOST
    if host_family(url) != host_family(start_url):
        return OFF_HOST
    return page_kind(page)
