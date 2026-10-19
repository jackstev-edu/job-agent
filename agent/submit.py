"""
submit.py — the final page: consent boxes the gate allows, then Submit.

WHAT THIS DOES: On the page the wizard reaches with a Submit control and no
Next, judges the whole application with gate.decide; ticks the consent
boxes the gate planned as would_tick (re-reading each label at tick time
against the consent policy or the profile's exact allowlist); judges it
again on a fresh census; and only if the verdict is would_submit presses
the one control that reads as Submit. Then it reads the confirmation:
the portal's signature or a generic one, keeping only the sentence that
matched, the address and the title.
WHY IT MATTERS: This is the only module that presses Submit, and it can
only do so after the gate says yes on the full record. Nothing else in the
codebase submits.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field as dataclass_field
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from . import browser as browser_mod, extract, gate, outcome

if TYPE_CHECKING:
    from playwright.sync_api import Page

SUBMIT_WORDS = outcome.SUBMIT_WORDS
NEXT_WORDS = outcome.NEXT_WORDS
SUBMIT_ATTR = "data-ja-submit"
TICK_ATTR = "data-ja-tick"

# The generic "we have it" wording, used only when the portal has no
# signature of its own. Only the matched sentence is ever kept.
GENERIC_CONFIRMATION = re.compile(
    r"thank you for (applying|your application|submitting)|thanks for applying"
    r"|application (has been |was )?(received|submitted|sent)|successfully (submitted|applied)"
    r"|we('ve| have) received your application|your application (is|has been) (in|complete)",
    re.I)

_FIND_SUBMIT_JS = r"""
([submitRx, nextRx, attr]) => {
  const SUBMIT = new RegExp(submitRx, 'i'), NEXT = new RegExp(nextRx, 'i');
  const norm = t => String(t || '').replace(/\s+/g, ' ').trim();
  const visible = el => {
    const r = el.getBoundingClientRect(), s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
  };
  document.querySelectorAll('[' + attr + ']').forEach(e => e.removeAttribute(attr));
  const isBtnInput = el => el.tagName === 'INPUT' && ['submit', 'button'].includes((el.type || '').toLowerCase());
  const text = el => norm(el.innerText || (isBtnInput(el) ? el.value : '') || el.getAttribute('aria-label'));
  const hits = [...document.querySelectorAll(
      'button, input[type=submit], input[type=button], [role=button], a')]
      .filter(visible).filter(el => { const t = text(el); return t && SUBMIT.test(t) && !NEXT.test(t); });
  const words = new Set(hits.map(el => text(el).toLowerCase()));
  if (hits.length !== 1 && words.size !== 1) return {found: hits.length, chosen: false, word: ''};
  const chosen = hits[0];
  chosen.setAttribute(attr, '1');
  return {found: hits.length, chosen: true, word: text(chosen), disabled: !!chosen.disabled};
}
"""

# One checkbox, found by its scan ref, with its label as the census reads it.
_BOX_LABEL_JS = r"""
(el) => {
  const norm = t => (t || '').replace(/\s+/g, ' ').trim();
  const texts = els => ({inner: els.map(e => e.innerText).join(' '),
                         content: els.map(e => e.textContent).join(' ')});
  let lab = {source: 'none', inner: '', content: ''};
  const aria = el.getAttribute('aria-label');
  if (aria && aria.trim()) lab = {source: 'aria-label', inner: aria, content: aria};
  else if (el.id && document.querySelector(`label[for="${CSS.escape(el.id)}"]`))
    lab = {source: 'for', ...texts([document.querySelector(`label[for="${CSS.escape(el.id)}"]`)])};
  else if (el.closest('label')) lab = {source: 'wrap', ...texts([el.closest('label')])};
  const described = (el.getAttribute('aria-describedby') || '').split(/\s+/)
      .map(id => document.getElementById(id)?.textContent || '').join(' ');
  const r = el.getBoundingClientRect(), s = getComputedStyle(el);
  return {checked: !!el.checked, label_source: lab.source, label_inner: norm(lab.inner),
          label_content: norm(lab.content), described: norm(described),
          visible: r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none',
          is_checkbox: el.tagName === 'INPUT' && (el.type || '').toLowerCase() === 'checkbox'};
}
"""


@dataclass
class Submission:
    status: str                      # "submitted" | "needs_you" | "failed"
    reason: str = ""
    gate_result: dict | None = None
    evidence: dict = dataclass_field(default_factory=dict)
    steps: list[str] = dataclass_field(default_factory=list)
    clicked: bool = False


def find_submit(page: "Page") -> dict:
    """Mark the one control that reads as Submit (never Next). Clicks nothing."""
    try:
        got = page.evaluate(_FIND_SUBMIT_JS, [SUBMIT_WORDS.pattern, NEXT_WORDS.pattern, SUBMIT_ATTR])
    except Exception:
        return {"found": 0, "chosen": False, "word": ""}
    return got if isinstance(got, dict) else {"found": 0, "chosen": False, "word": ""}


def tick_allowed(page: "Page", result: dict, profile, steps: list[str]) -> list[str]:
    """
    Tick each box the gate planned as would_tick, after re-reading its label
    on the element itself: it must still be a visible native checkbox whose
    label passes the consent policy or exactly matches the profile's
    allowlist. Returns the refs ticked. Anything else is left alone, and the
    gate's second pass then stops on it.
    """
    ticked: list[str] = []
    allow = profile.consent_allowlist()
    for ref, action in (result.get("consent_actions") or {}).items():
        if action != "would_tick" or not extract.is_current(ref):
            continue
        loc = page.locator(f"[{extract.REF_ATTR}='{ref}']")
        try:
            if loc.count() != 1:
                continue
            box = loc.first.evaluate(_BOX_LABEL_JS)
        except Exception:
            continue
        if not isinstance(box, dict) or not box.get("is_checkbox") or not box.get("visible"):
            continue
        if box.get("checked"):
            continue
        exact = gate._allowlist_index(box, allow) is not None
        if not (exact or gate.consent_box_ok(box)):
            continue
        try:
            loc.first.check(timeout=4000)
            ticked.append(ref)
            steps.append(f"ticked '{str(box.get('label_inner') or '')[:60]}'")
        except Exception:
            continue
    return ticked


def _text_snippet(page: "Page") -> tuple[str, bool]:
    """The one sentence the generic confirmation matched, and whether it matched."""
    try:
        text = page.evaluate("() => (document.body ? document.body.innerText : '').slice(0, 20000)")
    except Exception:
        return "", False
    m = GENERIC_CONFIRMATION.search(text or "")
    if not m:
        return "", False
    # The sentence the match sits in: back to the previous sentence end or
    # line break, forward to the next. Nothing else of the page is kept.
    ends = re.compile(r"[.!?\n\r]")
    start = max((e.end() for e in ends.finditer(text, 0, m.start())), default=0)
    nxt = ends.search(text, m.end())
    end = nxt.end() if nxt else min(len(text), m.end() + 80)
    snippet = re.sub(r"\s+", " ", text[start:end]).strip()
    return snippet[:200], True


def _form_gone(page: "Page", refs: list[str]) -> bool:
    try:
        return not page.evaluate(outcome._PRESENT_JS, refs)
    except Exception:
        return False


def confirmation(page: "Page", *, portal: str, portal_method: str, start_url: str,
                 refs: list[str]) -> tuple[bool, dict]:
    """
    Did the submit go through? The portal's signature when it has one
    (outcome.detect_success), else the generic wording with the form gone.
    Returns (confirmed, evidence): address, title, which signals held, and
    only the matched sentence of the page's text.
    """
    det = outcome.detect_success(page, portal=portal, portal_method=portal_method,
                                 start_url=start_url, refs=refs)
    snippet, generic = _text_snippet(page)
    gone = _form_gone(page, refs)
    try:
        url, title = page.url, page.title()
    except Exception:
        url, title = "", ""
    confirmed = det.get("result") == "match" or (generic and gone)
    return confirmed, {"url": url, "title": (title or "")[:200], "portal_signal": det.get("result"),
                       "signals": det.get("signals"), "generic_text": generic, "form_gone": gone,
                       "snippet": snippet, "url_shape": outcome.url_shape(url)}


def submit_application(page: "Page", record: dict, profile, *, build_record, steps_log=None,
                       agent_submissions_today: int | None = None) -> Submission:
    """
    Gate, tick what the gate allows, gate again on a fresh record, Submit.
    `build_record` rebuilds the full record from the page after the ticks,
    so the second verdict is on what is actually there. Never presses
    Submit on anything but would_submit.
    """
    steps: list[str] = steps_log if steps_log is not None else []
    result = gate.decide(record, profile, agent_submissions_today=agent_submissions_today)
    if result["content_verdict"] != gate.WOULD_SUBMIT:
        return Submission("needs_you", "the gate stopped: " + "; ".join(result["content_reasons"][:6]),
                          result, steps=steps)
    ticked = tick_allowed(page, result, profile, steps)
    if ticked:
        record = build_record()
        result = gate.decide(record, profile, agent_submissions_today=agent_submissions_today)
        if result["content_verdict"] != gate.WOULD_SUBMIT:
            return Submission("needs_you", "after ticking consent the gate stopped: "
                              + "; ".join(result["content_reasons"][:6]), result, steps=steps)
    found = find_submit(page)
    if not found.get("chosen"):
        return Submission("needs_you", f"no single Submit control on the final page "
                          f"({found.get('found', 0)} candidates)", result, steps=steps)
    if found.get("disabled"):
        return Submission("needs_you", f"the Submit control '{found.get('word')}' is disabled",
                          result, steps=steps)
    refs = [r for f in record.get("fields") or [] for r in (f.get("option_refs") or [f.get("ref")])
            if extract.is_current(r)]
    try:
        page.locator(f"[{SUBMIT_ATTR}='1']").first.click(timeout=5000)
    except Exception as e:
        return Submission("needs_you", f"couldn't press Submit ({type(e).__name__})", result,
                          steps=steps)
    steps.append(f"pressed '{found.get('word')}'")
    browser_mod.settle(page, 2500)
    confirmed, evidence = confirmation(page, portal=record.get("portal") or "",
                                       portal_method=record.get("portal_method") or "",
                                       start_url=record.get("url") or "", refs=refs)
    if not confirmed:
        # One more moment: confirmations can take a redirect or two.
        browser_mod.settle(page, 4000)
        confirmed, evidence = confirmation(page, portal=record.get("portal") or "",
                                           portal_method=record.get("portal_method") or "",
                                           start_url=record.get("url") or "", refs=refs)
    if confirmed:
        return Submission("submitted", "confirmation seen", result, evidence, steps, clicked=True)
    return Submission("needs_you", "Submit was pressed but no confirmation was seen; check the "
                      "portal", result, evidence, steps, clicked=True)
