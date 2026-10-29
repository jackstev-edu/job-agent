"""
chooser.py — "choose how to apply" pages, by an allowlist of control labels.

WHAT THIS DOES: When a page offers ways to apply instead of a form
(Avature: Without Resume / From Device / Copy & Paste; Workday: Autofill
with Resume / Apply Manually / Use My Last Application), picks by an
allowlist of whole control labels, in order: Workday's "Apply Manually";
Avature's "From Device" (the profile's resume goes into the file chooser
that control opens, which Playwright answers in place of any native
dialog; the page's own pre-existing file input only when the click opens
no chooser), falling back to "Without Resume" when no resume is on file.
LinkedIn, Indeed and other third-party autofill, "Copy & Paste" and "Use
My Last Application" are never chosen. With none of the allowed labels on
the page, the job is parked with the options shown. A page that already
has a form is not a chooser.
WHY IT MATTERS: Jack's first GUI run parked Wayfair's Avature step 1 as
"a page with no fields, no Next and no Submit"; it was this chooser. His
2026-10-29 run then looped on it: the file went into a hidden
pre-existing input the page ignores, and the page never moved on; the
control's own chooser is the one route the portal takes.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING

from . import browser as browser_mod

if TYPE_CHECKING:
    from playwright.sync_api import Page

# Whole control text after whitespace is collapsed, case set aside. In the
# order they are tried. Nothing outside this list is ever chosen.
ALLOWED = (
    ("apply_manually", re.compile(r"^apply manually$", re.I)),
    ("from_device", re.compile(r"^(from device|upload (a |my |your )?(resume|cv)|from (my )?(computer|device))$", re.I)),
    ("without_resume", re.compile(r"^(without (a )?resume|apply without (a )?resume|continue without (a )?resume)$", re.I)),
)
# Never chosen, whatever else they match: third-party autofill, pasting, a
# previous application.
REFUSED = re.compile(r"linkedin|indeed|seek|glassdoor|copy\s*(&|and)\s*paste|paste|last application"
                     r"|previous application|autofill|auto-fill|import", re.I)

_ATTR = "data-ja-choice"
# How long the allowed control gets to open its file chooser after the click.
FILE_CHOOSER_WAIT_MS = 4000

# The visible controls that could be a way to apply, with their whole text.
# Marks nothing; the texts come back for the allowlist compare and, when
# none is allowed, the park message.
_CONTROLS_JS = r"""
(attr) => {
  const norm = t => String(t || '').replace(/\s+/g, ' ').trim();
  const visible = el => {
    const r = el.getBoundingClientRect(), s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
  };
  document.querySelectorAll('[' + attr + ']').forEach(e => e.removeAttribute(attr));
  const els = [...document.querySelectorAll('a, button, [role=button], input[type=submit], input[type=button], label')]
    .filter(visible);
  return els.map((el, i) => {
    const text = norm(el.tagName === 'INPUT' ? el.value : el.innerText) || norm(el.getAttribute('aria-label'));
    return [i, text];
  }).filter(([, t]) => t && t.length <= 60);
}
"""

_MARK_JS = r"""
([index, attr]) => {
  const visible = el => {
    const r = el.getBoundingClientRect(), s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
  };
  document.querySelectorAll('[' + attr + ']').forEach(e => e.removeAttribute(attr));
  const els = [...document.querySelectorAll('a, button, [role=button], input[type=submit], input[type=button], label')]
    .filter(visible);
  const el = els[index];
  if (!el) return false;
  el.setAttribute(attr, '1');
  return true;
}
"""


def options_shown(page: "Page") -> list[tuple[int, str]]:
    """Every visible control's whole text, with its index for marking."""
    try:
        got = page.evaluate(_CONTROLS_JS, _ATTR)
    except Exception:
        return []
    return [(int(i), str(t)) for i, t in got if isinstance(got, list)] if isinstance(got, list) else []


def pick(controls: list[tuple[int, str]], resume: Path | None) -> tuple[str | None, int | None, str | None]:
    """
    (kind, index, text) of the one control to use, or (None, None, None).
    "from_device" needs a resume on file; otherwise "without_resume" is
    taken if offered. A text REFUSED matches is never chosen, even when it
    also matches an allowed pattern.
    """
    has_resume = bool(resume) and Path(resume).exists()
    for kind, rx in ALLOWED:
        if kind == "from_device" and not has_resume:
            continue
        hits = [(i, t) for i, t in controls if rx.match(t) and not REFUSED.search(t)]
        if len(hits) == 1:
            return kind, hits[0][0], hits[0][1]
    return None, None, None


def is_chooser(controls: list[tuple[int, str]]) -> bool:
    """Does the page offer any way-to-apply control at all (allowed or refused)?"""
    return any(rx.match(t) for _, t in controls for _, rx in ALLOWED) or any(
        REFUSED.search(t) and re.search(r"apply|resume|cv|application", t, re.I) for _, t in controls)


def choose(page: "Page", resume: Path | None, log: list[str]) -> str | None:
    """
    Act on a chooser page: upload the resume through the file chooser the
    allowed control opens (Playwright answers it in place of any native
    dialog; the page's own file input only when the click opens none), or
    press the allowed control. Returns the kind used, or None when the
    page is not a chooser. Raises ValueError, with the options shown, when
    it is one and none is allowed.
    """
    controls = options_shown(page)
    if not is_chooser(controls):
        return None
    kind, index, text = pick(controls, resume)
    if kind is None:
        shown = ", ".join(f"'{t}'" for _, t in controls[:8])
        raise ValueError(f"a choice of ways to apply, none of which the agent takes: {shown}")
    if kind == "from_device":
        route = _upload(page, index, Path(resume))
        if route is None:
            raise ValueError(f"the '{text}' option opened no file chooser and the page has no file input")
        log.append(f"chose '{text}' and put {Path(resume).name} in {route}")
    else:
        if not page.evaluate(_MARK_JS, [index, _ATTR]):
            raise ValueError(f"couldn't mark the '{text}' control")
        page.locator(f"[{_ATTR}='1']").first.click(timeout=4000)
        log.append(f"chose '{text}'")
    browser_mod.settle(page, 1500)
    return kind


def _upload(page: "Page", index: int, resume: Path) -> str | None:
    """
    The chooser the control opens first (Jack, 2026-10-29): the allowed
    control is clicked under expect_file_chooser and the chooser is
    answered with the resume. Only when the click went through and opened
    no chooser, on a page still at the same address, does the resume go
    into the page's own pre-existing file input (Avature ignores a file
    set there; a portal whose control is a plain label for that input
    does not). Returns where the file went, for the log, or None.
    """
    if not page.evaluate(_MARK_JS, [index, _ATTR]):
        return None
    try:
        here = page.url
    except Exception:
        return None
    clicked = False
    try:
        with page.expect_file_chooser(timeout=FILE_CHOOSER_WAIT_MS) as fc:
            page.locator(f"[{_ATTR}='1']").first.click(timeout=4000)
            clicked = True
        fc.value.set_files(str(resume))
        return "the file chooser it opened"
    except Exception:
        if not clicked:
            return None
    try:
        # A click that navigated instead may not have committed yet: the
        # page is let settle before its address is compared, so the input
        # of a page being left is never set.
        browser_mod.settle(page, 300)
        if page.url != here:
            return None
        inputs = page.locator("input[type=file]")
        if inputs.count() >= 1:
            inputs.first.set_input_files(str(resume), timeout=15_000)
            return "the page's file input (the control opened no chooser)"
    except Exception:
        pass
    return None
