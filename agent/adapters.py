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

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from playwright.sync_api import Page

from . import browser as browser_mod
from .filler import is_forbidden_button


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
        "Modern single-page app, but well built and uses real form elements.",
        quirks=["Dropdowns are custom comboboxes, not <select> elements.",
                "The form is sometimes behind an 'Apply' button."],
    ),
    "workday": ATS(
        "Workday", "hard",
        "Multi-step wizard. Account required per employer. Where most of your "
        "manual effort will go.",
        multi_step=True, needs_account=True,
        quirks=["Element IDs regenerate between renders — key off data-automation-id.",
                "Each step is a separate page; Continue between them.",
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
    for pattern, key in _URL_SIGNATURES:
        if re.search(pattern, url, re.I):
            return key, CATALOG[key]

    # Many companies proxy the ATS behind their own domain, so check the HTML.
    if page is not None:
        try:
            html = page.content()[:200_000].lower()
        except Exception:
            html = ""
        for pattern, key in _URL_SIGNATURES:
            if re.search(pattern, html):
                return key, CATALOG[key]
        if "data-automation-id" in html:
            return "workday", CATALOG["workday"]

    return "generic", CATALOG["generic"]


def prepare(page: "Page", key: str) -> None:
    """Do whatever this system needs before the form is readable."""
    if key in ("ashby", "greenhouse", "smartrecruiters"):
        _click_if_visible(page, "a#apply_button, "
                                "button:has-text('Apply for this Job'), "
                                "button:has-text('Apply for this job'), "
                                "a:has-text('Apply Now'), button:has-text('Apply')")
    elif key == "workday":
        _click_if_visible(page, "a[data-automation-id='adventureButton'], "
                                "button:has-text('Apply Manually')")
        browser_mod.settle(page, 2500)
    browser_mod.settle(page, 700)


def _click_if_visible(page: "Page", selector: str) -> None:
    """Click a button only if it exists, is visible, and isn't forbidden."""
    try:
        button = page.locator(selector).first
        if not button.is_visible(timeout=1500):
            return
        if is_forbidden_button(button.inner_text()):
            return
        button.click()
        browser_mod.settle(page, 1500)
    except Exception:
        pass


def next_step(page: "Page", key: str) -> bool:
    """
    Advance a multi-step wizard by one page.

    Refuses to click anything that looks like a final submit. Returns True if
    it moved on, False if it couldn't find a safe Continue button.
    """
    selector = {
        "workday": "button[data-automation-id='bottom-navigation-next-button'], "
                   "button:has-text('Save and Continue'), button:has-text('Continue')",
        "icims": "input[value='Continue'], button:has-text('Continue')",
        "taleo": "a:has-text('Save and Continue'), button:has-text('Next')",
    }.get(key, "button:has-text('Continue'), button:has-text('Next')")

    try:
        button = page.locator(selector).first
        if not button.is_visible(timeout=2500):
            return False
        if is_forbidden_button(button.inner_text()):
            return False
        button.click()
        browser_mod.settle(page, 2000)
        return True
    except Exception:
        return False
