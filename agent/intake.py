"""
intake.py — from a link Jack gives to the page the application form is on.

WHAT THIS DOES: Opens the link, finds the posting's Apply control, clicks
it (following a new tab or a redirect to the company's site) and says which
portal the form is on. On LinkedIn or Handshake it follows only an Apply
that leaves the site: Easy Apply and Handshake's own applications are
refused and parked. Nothing here reads a field value or fills anything.
WHY IT MATTERS: The agent acts only on links Jack gives it and never finds
postings of its own, so this is the one place a navigation starts, and the
one place Apply is clicked. Apply is the one control it clicks: a control
that reads like Submit, Easy Apply or anything else is never pressed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field as dataclass_field
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from . import adapters, browser as browser_mod, pages

if TYPE_CHECKING:
    from playwright.sync_api import BrowserContext, Page

# What an Apply control may read as, after whitespace is collapsed. Exact,
# whole-text matches only: "Apply now" opens a form on a posting; the same
# words on a final page would send it, which is why intake runs only on the
# link Jack gave, before anything has been filled.
APPLY_WORDS = re.compile(
    r"^(apply|apply now|apply here|apply online|apply today|apply for (this |the )?"
    r"(job|position|role|opening)|apply to (this |the )?(job|position|role|opening)"
    r"|apply for job|apply to job|apply manually|apply with (a |my )?(resume|cv)"
    r"|i'?m interested|i am interested|start (your |my |the )?application"
    r"|begin (your |my |the )?application|apply externally|apply on company (web)?site"
    r"|continue to (the )?(company|employer) (web)?site|continue to apply"
    r"|go to (the )?(company|employer) (web)?site)$", re.I)

# Never pressed by intake, whatever else matches.
REFUSED_WORDS = re.compile(r"easy apply|submit|send|sign|agree|accept|withdraw", re.I)

# Workday's chooser after Apply: the agent fills by hand, never from an
# autofill it can't check.
MANUAL_WORDS = re.compile(r"^apply manually$", re.I)

JOB_BOARDS = {"linkedin": ("linkedin.com", "linkedin.cn", "lnkd.in"),
              "handshake": ("joinhandshake.com", "handshake.com")}

APPLY_ATTR = "data-ja-apply"


def job_board(url: str) -> str | None:
    """"linkedin" or "handshake" when the URL is on one of those sites, else None."""
    host = (urlparse(url or "").hostname or "").lower().rstrip(".")
    for name, hosts in JOB_BOARDS.items():
        if any(host == h or host.endswith("." + h) for h in hosts):
            return name
    return None


# Finds the Apply controls on the page: visible links, buttons, role=button
# elements and submit / button inputs whose whole text (or aria-label, or
# value) reads as Apply and not as anything refused. Marks the one to click.
# Returns only counts and words, never page text beyond the matched control's
# own normalised text (which is one of APPLY_WORDS by construction).
_FIND_APPLY_JS = r"""
([applyRx, refusedRx, manualRx, attr]) => {
  const APPLY = new RegExp(applyRx, 'i'), REFUSED = new RegExp(refusedRx, 'i'),
        MANUAL = new RegExp(manualRx, 'i');
  const norm = t => String(t || '').replace(/\s+/g, ' ').trim();
  const visible = el => {
    const r = el.getBoundingClientRect(), s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
  };
  document.querySelectorAll('[' + attr + ']').forEach(e => e.removeAttribute(attr));
  // The text Jack sees: innerText, or an input's value; an aria-label only
  // for a control with no visible text at all (an icon button).
  const texts = el => { const seen = norm(el.innerText || el.value);
                        return [seen || norm(el.getAttribute('aria-label'))].filter(Boolean); };
  const all = [...document.querySelectorAll(
      'a, button, [role=button], input[type=submit], input[type=button]')].filter(visible);
  const hits = [], manual = [];
  for (const el of all) {
    const ts = texts(el);
    if (!ts.length || ts.some(t => REFUSED.test(t))) continue;
    if (ts.some(t => MANUAL.test(t))) manual.push(el);
    else if (ts.some(t => APPLY.test(t))) hits.push(el);
  }
  // An Apply for the posting itself: links all pointing one way, or buttons.
  // Top-and-bottom pairs are common; a page offering different targets is
  // ambiguous and gets no click.
  const pick = list => {
    if (!list.length) return null;
    const hrefs = new Set(list.map(e => e.tagName === 'A' ? (e.href || '') : ''));
    if (hrefs.size > 1) return null;
    const words = new Set(list.map(e => norm(e.innerText || e.value || e.getAttribute('aria-label')).toLowerCase()));
    if (words.size > 1) return null;
    return list[0];
  };
  const chosen = pick(manual) || pick(hits);
  if (chosen) chosen.setAttribute(attr, '1');
  return {found: hits.length, manual: manual.length, chosen: !!chosen,
          tag: chosen ? chosen.tagName.toLowerCase() : '',
          target: chosen && chosen.tagName === 'A' ? (chosen.getAttribute('target') || '') : '',
          word: chosen ? norm(chosen.innerText || chosen.value || chosen.getAttribute('aria-label')) : ''};
}
"""

# A form worth filling: two or more visible fields, or a file box (a lone
# email box is a newsletter sign-up, not the application).
_HAS_FIELDS_JS = r"""
() => {
  const boxes = [...document.querySelectorAll('input, select, textarea')].filter(el => {
    const t = (el.type || '').toLowerCase();
    if (['hidden', 'submit', 'button', 'reset', 'image', 'search'].includes(t)) return false;
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  });
  return boxes.length >= 2 || boxes.some(el => (el.type || '').toLowerCase() === 'file');
}
"""


@dataclass
class Landing:
    """Where intake left things: the page the form is on, or why it stopped."""
    page: "Page | None"
    url: str
    portal: str = "generic"
    portal_method: str = "default"
    parked: str | None = None                 # the needs-you reason, if parked
    steps: list[str] = dataclass_field(default_factory=list)   # what was done, in words


def find_apply(page: "Page") -> dict:
    """Mark the Apply control, if there is exactly one way to apply. Clicks nothing."""
    try:
        got = page.evaluate(_FIND_APPLY_JS, [APPLY_WORDS.pattern, REFUSED_WORDS.pattern,
                                             MANUAL_WORDS.pattern, APPLY_ATTR])
    except Exception:
        return {"found": 0, "manual": 0, "chosen": False, "tag": "", "target": "", "word": ""}
    if not isinstance(got, dict):
        return {"found": 0, "manual": 0, "chosen": False, "tag": "", "target": "", "word": ""}
    return got


def has_form_fields(page: "Page") -> bool:
    try:
        return bool(page.evaluate(_HAS_FIELDS_JS))
    except Exception:
        return False


def _click_marked(ctx: "BrowserContext", page: "Page", wait_ms: int = 3000) -> "Page":
    """
    Click the marked Apply control. If it opens a new tab, that tab is the
    page from here on; otherwise the same page, once it has settled.
    """
    before = page.url
    locator = page.locator(f"[{APPLY_ATTR}='1']").first
    locator.scroll_into_view_if_needed(timeout=4000)
    try:
        with ctx.expect_page(timeout=wait_ms) as opened:
            locator.click(timeout=4000)
        new_page = opened.value
        new_page.wait_for_load_state("domcontentloaded", timeout=30_000)
        browser_mod.settle(new_page)
        return new_page
    except Exception:
        pass
    # No new tab: give a navigation or an in-page form a moment.
    try:
        page.wait_for_url(lambda u: u != before, timeout=wait_ms)
    except Exception:
        pass
    browser_mod.settle(page)
    return page


def open_link(ctx: "BrowserContext", url: str, max_clicks: int = 3) -> Landing:
    """
    Open Jack's link and get to the form. Up to `max_clicks` Apply clicks
    (posting -> company site -> Workday's "Apply Manually"), each one on a
    control that reads as Apply. Parks, with the reason, anything it won't
    do: an Easy Apply or Handshake-internal application, a posting with no
    single Apply and no form, a page that never leaves a job board.
    """
    landing = Landing(page=None, url=url)
    try:
        page = browser_mod.open_page(ctx, url)
    except Exception as e:
        landing.parked = f"the link didn't open ({type(e).__name__})"
        return landing
    landing.page = page
    last_click: tuple[str, str] | None = None
    for _ in range(max_clicks):
        board = job_board(page.url)
        if not board and has_form_fields(page):
            break                          # the form is here: nothing to click
        found = find_apply(page)
        if found.get("chosen"):
            here = (page.url, str(found.get("word") or "").lower())
            if here == last_click:
                # The same control on the same address again: the click
                # went nowhere the agent can follow.
                landing.parked = (f"{board}: Apply stays on the job board" if board
                                  else "Apply didn't lead anywhere; check the page")
                return landing
            last_click = here
            landing.steps.append(f"clicked '{found.get('word')}' on {urlparse(page.url).hostname}")
            try:
                page = _click_marked(ctx, page)
            except Exception as e:
                landing.parked = f"couldn't click Apply ({type(e).__name__})"
                return landing
            landing.page = page
            continue                       # a chooser, a board's way out, or the form
        if board:
            landing.parked = (f"{board}: no Apply that leaves the site "
                              f"(Easy Apply / built-in applications are refused)")
            return landing
        if found.get("found") == 0:
            break                          # nothing to click; the page is what it is
        landing.parked = "more than one way to apply on the posting; pick one yourself"
        return landing
    if job_board(page.url):
        landing.parked = f"{job_board(page.url)}: the application stays on the job board"
        return landing
    if pages.is_linkedin(page.url):
        landing.parked = "linkedin: refused"
        return landing
    landing.portal, _, landing.portal_method = adapters.detect_with_method(page.url, page)
    landing.url = page.url
    return landing
