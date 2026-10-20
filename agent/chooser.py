"""
chooser.py — "choose how to apply" pages, by an allowlist of control labels.

WHAT THIS DOES: When a page offers ways to apply instead of a form
(Avature: Without Resume / From Device / Copy & Paste; Workday: Autofill
with Resume / Apply Manually / Use My Last Application), picks by an
allowlist of whole control labels, in order: Workday's "Apply Manually";
Avature's "From Device" (the profile's resume goes in through the page's
own file input, never a native dialog), falling back to "Without Resume"
when no resume is on file. LinkedIn, Indeed and other third-party
autofill, "Copy & Paste" and "Use My Last Application" are never chosen.
With none of the allowed labels on the page, the job is parked with the
options shown. A page that already has a form is not a chooser.
WHY IT MATTERS: Jack's first GUI run parked Wayfair's Avature step 1 as
"a page with no fields, no Next and no Submit"; it was this chooser.
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
    Act on a chooser page: upload the resume through the page's file input
    (or the file chooser the control opens, which Playwright answers in
    place of any native dialog), or press the allowed control. Returns the
    kind used, or None when the page is not a chooser. Raises ValueError,
    with the options shown, when it is one and none is allowed.
    """
    controls = options_shown(page)
    if not is_chooser(controls):
        return None
    kind, index, text = pick(controls, resume)
    if kind is None:
        shown = ", ".join(f"'{t}'" for _, t in controls[:8])
        raise ValueError(f"a choice of ways to apply, none of which the agent takes: {shown}")
    if kind == "from_device":
        done = _upload(page, index, Path(resume))
        if not done:
            raise ValueError("the From Device option gave no file input to put the resume in")
        log.append(f"chose '{text}' and put {Path(resume).name} in the page's file input")
    else:
        if not page.evaluate(_MARK_JS, [index, _ATTR]):
            raise ValueError(f"couldn't mark the '{text}' control")
        page.locator(f"[{_ATTR}='1']").first.click(timeout=4000)
        log.append(f"chose '{text}'")
    browser_mod.settle(page, 1500)
    return kind


def _upload(page: "Page", index: int, resume: Path) -> bool:
    """The page's own file input first; else the chooser the control opens."""
    try:
        inputs = page.locator("input[type=file]")
        if inputs.count() >= 1:
            inputs.first.set_input_files(str(resume), timeout=15_000)
            return True
    except Exception:
        pass
    try:
        if not page.evaluate(_MARK_JS, [index, _ATTR]):
            return False
        with page.expect_file_chooser(timeout=4000) as fc:
            page.locator(f"[{_ATTR}='1']").first.click(timeout=4000)
        fc.value.set_files(str(resume))
        return True
    except Exception:
        return False
