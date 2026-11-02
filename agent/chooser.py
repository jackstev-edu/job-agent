"""
chooser.py — "choose how to apply" pages, by an allowlist of control labels.

WHAT THIS DOES: When a page offers ways to apply instead of a form
(Avature: Without Resume / From Device / Copy & Paste; Workday: Autofill
with Resume / Apply Manually / Use My Last Application), picks by an
allowlist of whole control labels, in order: Workday's "Apply Manually";
Avature's "From Device" (the profile's resume goes into the file chooser
that control opens, which a listener registered before the click answers
in place of any native dialog, a late chooser included; the page's own
pre-existing file input only when the click opens no chooser), falling
back to "Without Resume" when no resume is on file.
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

import contextlib
import re
import time
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple

from . import browser as browser_mod, extract

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


class ChooserRefused(Exception):
    """The control's file chooser opened but didn't take the resume."""


class Choice(NamedTuple):
    """What `choose` did: the allowlist kind, and the control's whole label for the log and a park."""
    kind: str
    label: str

# How long the allowed control gets to open its file chooser after the click,
# and how long the page is let settle before the fallback input is used.
FILE_CHOOSER_WAIT_MS = 4000
FALLBACK_SETTLE_MS = 300


class _Answer:
    """
    The one answer to a file chooser the allowed control opens: the resume,
    once. Registered as a filechooser listener before the click and removed
    when _upload is done (Jack, 2026-10-31), so a chooser that opens late
    (a slow handler, a modal first, while the fallback is being weighed) is
    answered by Playwright and never reaches a native dialog.
    """

    def __init__(self, resume: Path):
        self.resume, self.answered, self.failed = resume, False, False

    def __call__(self, chooser) -> None:
        if self.answered or self.failed:
            return
        try:
            chooser.set_files(str(self.resume))
        except Exception as e:
            # The chooser opened and refused the file: not an upload, and
            # the park says so (fold of 9ccf258's finding 2).
            self.failed, self.why = True, type(e).__name__
            return
        self.answered = True


@contextlib.contextmanager
def answering(page: "Page", resume: Path):
    """A filechooser listener that answers once with `resume`, alive for the block."""
    answer = _Answer(resume)
    page.on("filechooser", answer)
    try:
        yield answer
    finally:
        with contextlib.suppress(Exception):
            page.remove_listener("filechooser", answer)

# The visible controls that could be a way to apply, with their whole text.
# Marks nothing; the texts come back for the allowlist compare and, when
# none is allowed, the park message.
_CONTROLS_JS = extract.safe(r"""
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
    const text = norm(__ja.tag(el) === 'INPUT' ? el.value : el.innerText) || norm(__ja.attr(el, 'aria-label'));
    return [i, text];
  }).filter(([, t]) => t && t.length <= 60);
}
""")

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


def choose(page: "Page", resume: Path | None, log: list[str]) -> Choice | None:
    """
    Act on a chooser page: upload the resume through the file chooser the
    allowed control opens (Playwright answers it in place of any native
    dialog; the page's own file input only when the click opens none), or
    press the allowed control. Returns the Choice made (kind and label),
    or None when the page is not a chooser. Raises ValueError, with the options shown, when
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
        try:
            route = _upload(page, index, Path(resume))
        except ChooserRefused as e:
            raise ValueError(f"the '{text}' option's file chooser didn't take {Path(resume).name} ({e}); "
                             f"upload it yourself in the tab")
        if route is None:
            raise ValueError(f"the '{text}' option opened no file chooser and the page has no file input")
        log.append(f"chose '{text}' and put {Path(resume).name} in {route}")
    else:
        if not page.evaluate(_MARK_JS, [index, _ATTR]):
            raise ValueError(f"couldn't mark the '{text}' control")
        page.locator(f"[{_ATTR}='1']").first.click(timeout=4000)
        log.append(f"chose '{text}'")
    browser_mod.settle(page, 1500)
    return Choice(kind, text)


def _upload(page: "Page", index: int, resume: Path) -> str | None:
    """
    The chooser the control opens first (Jack, 2026-10-29): a filechooser
    listener is registered before the click and answers the chooser with
    the resume; it stays registered until this returns (Jack, 2026-10-31),
    so a chooser that opens late, while the fallback is being weighed, is
    answered too, and the fallback input is then left alone. Only when the
    click went through and opened no chooser within the wait, on a page
    still at the same address, does the resume go into the page's own
    pre-existing file input (Avature ignores a file set there; a portal
    whose control is a plain label for that input does not). Returns where
    the file went, for the log, or None.
    """
    if not page.evaluate(_MARK_JS, [index, _ATTR]):
        return None
    try:
        here = page.url
    except Exception:
        return None
    with answering(page, resume) as answer:
        try:
            page.locator(f"[{_ATTR}='1']").first.click(timeout=4000)
        except Exception:
            return None
        # The listener's answer lands while the page is waited on.
        start = time.monotonic()
        while not (answer.answered or answer.failed) and (time.monotonic() - start) * 1000 < FILE_CHOOSER_WAIT_MS:
            try:
                page.wait_for_timeout(100)
            except Exception:
                return None
        if answer.failed:
            raise ChooserRefused(getattr(answer, "why", "") or "refused")
        if answer.answered:
            return "the file chooser it opened"
        try:
            # A click that navigated instead may not have committed yet: the
            # page is let settle before its address is compared, so the input
            # of a page being left is never set.
            browser_mod.settle(page, FALLBACK_SETTLE_MS)
            if answer.failed:
                raise ChooserRefused(getattr(answer, "why", "") or "refused")
            if answer.answered:
                return "the file chooser it opened (late)"
            if page.url != here:
                return None
            inputs = page.locator("input[type=file]")
            if inputs.count() >= 1 and not answer.answered:
                inputs.first.set_input_files(str(resume), timeout=15_000)
                return "the page's file input (the control opened no chooser)"
        except Exception:
            pass
    return None
