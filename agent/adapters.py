"""
adapters.py — knowing which system you're looking at.

WHAT THIS DOES: "Job application form" isn't one thing. About six big systems
run most applications, and each behaves differently. This works out which one
you're on and applies what's known about it.

WHY IT MATTERS: Greenhouse is a clean single page and works first try.
Workday is a multi-step wizard that rebuilds its own HTML and makes you
create an account per employer. Treating them identically fails on both.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field as dataclass_field
from urllib.parse import urlparse

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from playwright.sync_api import Page

from . import browser as browser_mod


@dataclass
class ATS:
    name: str
    difficulty: str                  # easy | medium | hard
    summary: str
    multi_step: bool = False
    needs_account: bool = False
    iframe_hint: str = ""            # if the form lives inside an iframe
    quirks: list[str] = dataclass_field(default_factory=list)


CATALOG: dict[str, ATS] = {
    "greenhouse": ATS(
        "Greenhouse", "easy",
        "Single page, stable HTML, honest labels. Your best case.",
        quirks=["Custom questions have generic IDs — the label text is what matters."],
    ),
    "lever": ATS(
        "Lever", "easy",
        "Single page. Readable field names like urls[LinkedIn].",
        quirks=["Uploading a resume triggers an autoparse that overwrites text "
                "fields. This tool uploads first, then fills, for that reason."],
    ),
    "ashby": ATS(
        "Ashby", "easy",
        "Modern single-page app. Mostly real form elements, except Yes/No "
        "questions, which are a pair of buttons over a hidden checkbox.",
        quirks=["Dropdowns are custom comboboxes, not <select> elements.",
                "Yes/No questions are button pairs over a hidden checkbox; "
                "answered by clicking, and a pressed one is left alone (a second "
                "click toggles it off).",
                "Those buttons are typeless, so inside a <form> they'd submit it. "
                "No <form> seen so far; if one appears they're all refused and "
                "the questions come to you.",
                "The form is sometimes behind an 'Apply' button, which you click."],
    ),
    "workday": ATS(
        "Workday", "hard",
        "Multi-step wizard. Account required per employer. Where most of your "
        "manual effort will go.",
        multi_step=True, needs_account=True,
        quirks=["Element IDs regenerate between renders — key off data-automation-id.",
                "Each step is a separate page; you press Continue between them, never the agent.",
                "Date fields are three separate spinners, not one input.",
                "Resume autofill populates work history badly. Expect to correct it."],
    ),
    "icims": ATS(
        "iCIMS", "hard",
        "Older and iframe-heavy. Very common at industrial employers.",
        multi_step=True, iframe_hint="icims",
        quirks=["The form lives inside an iframe."],
    ),
    "taleo": ATS(
        "Taleo (Oracle)", "hard",
        "Legacy enterprise portal. Times out. Common at large manufacturers.",
        multi_step=True, needs_account=True,
        quirks=["Sessions expire quickly — don't leave it half-finished."],
    ),
    "successfactors": ATS(
        "SAP SuccessFactors", "hard", "Legacy enterprise. Behaves like Taleo.",
        multi_step=True, needs_account=True,
    ),
    "smartrecruiters": ATS("SmartRecruiters", "medium", "Reasonably clean modern app."),
    "jobvite": ATS("Jobvite", "medium", "Mixed quality between employers."),
    "bamboohr": ATS("BambooHR", "easy", "Small-company portal, simple forms."),
    "generic": ATS(
        "Unknown or custom", "unknown",
        "No specific knowledge. Falls back to reading the page generically — "
        "check the review sheet carefully.",
    ),
}

_URL_SIGNATURES = [
    (r"greenhouse\.io|boards\.greenhouse|job-boards\.greenhouse", "greenhouse"),
    (r"jobs\.lever\.co|\blever\.co\b", "lever"),
    (r"jobs\.ashbyhq\.com|ashbyhq\.com", "ashby"),
    (r"myworkdayjobs\.com|myworkdaysite\.com|\bworkday\b", "workday"),
    (r"icims\.com", "icims"),
    (r"taleo\.net|oraclecloud\.com/hcm", "taleo"),
    (r"successfactors\.|sapsf\.", "successfactors"),
    (r"smartrecruiters\.com", "smartrecruiters"),
    (r"jobvite\.com", "jobvite"),
    (r"bamboohr\.com", "bamboohr"),
]


def detect(url: str, page = None) -> tuple[str, ATS]:
    """Identify the system from the URL, falling back to the page markup."""
    key, ats, _ = detect_with_method(url, page)
    return key, ats


def detect_with_method(url: str, page = None) -> tuple[str, ATS, str]:
    """
    detect(), plus how: "host", "url", "html" or "default". The submit gate
    trusts only "host", a match on the hostname itself. "url" means the
    pattern turned up elsewhere in the address (`?ref=greenhouse.io`), and the
    HTML fallback reads page text, which is untrusted: any page can mention
    "greenhouse".
    """
    host = (urlparse(url).hostname or "").lower()
    for pattern, key in _URL_SIGNATURES:
        if re.search(pattern, url, re.I):
            return key, CATALOG[key], ("host" if re.search(pattern, host, re.I) else "url")

    # Many companies proxy the ATS behind their own domain, so check the HTML.
    if page is not None:
        try:
            html = page.content()[:200_000].lower()
        except Exception:
            html = ""
        for pattern, key in _URL_SIGNATURES:
            if re.search(pattern, html):
                return key, CATALOG[key], "html"
        if "data-automation-id" in html:
            return "workday", CATALOG["workday"], "html"

    return "generic", CATALOG["generic"], "default"


def prepare(page: "Page", key: str) -> None:
    """
    Let the page settle once you've opened the form. Clicks nothing: on a
    portal you're signed in to, "Apply" can be a one-click submit that no
    check on the button can see, so you press Apply yourself.
    """
    if key == "workday":
        browser_mod.settle(page, 2500)
    browser_mod.settle(page, 700)


# What a "next page" control looks like on each system, on top of the
# generic Next / Continue controls every system is checked for.
_NEXT_STEP = {
    "workday": "button[data-automation-id='bottom-navigation-next-button'], "
               "button:has-text('Save and Continue'), ",
    "icims": "input[value='Continue'], ",
    "taleo": "a:has-text('Save and Continue'), ",
}
_NEXT_GENERIC = ", ".join(
    f"{tag}:has-text('{word}')" for word in ("Next", "Continue")
    for tag in ("button", "a", "[role=button]")) + ", " + ", ".join(
    f"input[type={kind}][value*='{word}' i]" for word in ("Next", "Continue")
    for kind in ("submit", "button"))

# How many matches has_next_step looks through. More than this and it
# answers yes rather than guess from the first few.
NEXT_SCAN_LIMIT = 50


def has_next_step(page: "Page", key: str) -> bool:
    """
    Is there a visible Next or Continue control, so the application goes on
    past this page? Only looked for, never clicked: you move between pages
    yourself. Anything it can't settle (an error, more matches than it
    looks through) is a yes, so the gate stops.
    """
    selector = _NEXT_STEP.get(key, "") + _NEXT_GENERIC
    try:
        found = page.locator(selector).all()
        if len(found) > NEXT_SCAN_LIMIT:
            return True
        return any(el.is_visible() for el in found)
    except Exception:
        return True
