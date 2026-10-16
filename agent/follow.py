"""
follow.py — following you through a multi-page application, one step at a time.

WHAT THIS DOES: Watches the agent's own tab and tells a new step of an
application from a re-render of the same one. A step is the page's address
plus a fingerprint of its visible fields, built from structure only (tag,
type, name, id, role, autocomplete); a page counts as settled once nothing
has been added or removed for a moment and two reads in a row agree. It
reads no values and clicks nothing: you move between steps yourself.
WHY IT MATTERS: Follow-along mode fills each step as you reach it and
leaves everything else to you. Mistaking a validation re-render for a new
step, or reacting to a page still mid-render, would fill the wrong fields
or fill twice, so detection is structural and debounced, never driven by
page text like "Step 2 of 4".
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from urllib.parse import urlparse

from . import extract

if TYPE_CHECKING:
    from playwright.sync_api import Page

# A step has settled once nothing was added to or removed from the page for
# this long, and two reads in a row give the same fingerprint.
QUIET_MS = 800

# Below this share of fields in common, a changed field set on the same
# address is a new step rather than the same step growing or shrinking.
SAME_STEP_OVERLAP = 0.5

NEW_STEP, REVISIT, RERENDER, CHANGED = "new_step", "revisit", "rerender", "changed"

# One read of the page's shape. The MutationObserver watches nodes being
# added and removed (re-renders, steps swapped in), not attributes, so the
# agent stamping refs or you typing doesn't count as the page moving. Values
# are never read. A field's label is used only to tell apart fields with no
# name and no id, and only to tell them apart.
_SHAPE_JS = r"""
(GROUP) => {
  if (!window.__jaShape) {
    window.__jaShape = {last: performance.now()};
    try {
      new MutationObserver(() => { window.__jaShape.last = performance.now(); })
        .observe(document, {childList: true, subtree: true});
    } catch (e) {}
  }
  const visible = el => {
    const r = el.getBoundingClientRect(), s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
  };
  const attr = (el, a) => (el.getAttribute(a) || '').slice(0, 80);
  const tie = el => (el.name || el.id) ? '' :
      (el.getAttribute('aria-label') || el.labels?.[0]?.innerText || '')
        .replace(/\s+/g, ' ').trim().slice(0, 80);
  const out = [];
  let stamped = 0;
  document.querySelectorAll(`input, select, textarea, [role=combobox], ${GROUP}`).forEach(el => {
    if (el.matches(GROUP)) {
      if (!visible(el)) return;
      out.push(['group', '', attr(el, 'data-field-path'), el.id || '', '', '', '']);
      if (el.querySelector('[data-ja-ref]')) stamped++;
      return;
    }
    if (el.closest(GROUP)) return;
    const type = (el.type || el.tagName).toLowerCase();
    if (['hidden', 'submit', 'button', 'reset', 'image'].includes(type)) return;
    if (el.disabled || el.readOnly || !visible(el)) return;
    // Skipped by the scan too (react-select's validation input), so never stamped.
    if (el.getAttribute('aria-hidden') === 'true' && el.tabIndex === -1) return;
    out.push([el.tagName.toLowerCase(), type, el.name || '', el.id || '', attr(el, 'role'),
              attr(el, 'autocomplete'), tie(el)]);
    if (el.hasAttribute('data-ja-ref')) stamped++;
  });
  return {url: location.href, fields: out, stamped,
          quiet_ms: Math.round(performance.now() - window.__jaShape.last)};
}
"""


def url_key(url: str) -> str:
    """The address a step is known by: host, path and query, never the #fragment."""
    p = urlparse(url or "")
    return f"{(p.hostname or '').lower()}{p.path or ''}{'?' + p.query if p.query else ''}"


def read_shape(target: "Page") -> dict | None:
    """One read of the page's shape, or None if it can't be read (mid-navigation)."""
    try:
        raw = target.evaluate(_SHAPE_JS, extract.BUTTON_GROUP)
    except Exception:
        return None
    if not isinstance(raw, dict) or not isinstance(raw.get("fields"), list):
        return None
    fields = tuple(sorted(tuple(str(x) for x in f) for f in raw["fields"] if isinstance(f, list)))
    return {"url": url_key(str(raw.get("url") or "")), "fields": fields,
            "count": len(fields), "stamped": int(raw.get("stamped") or 0),
            "quiet_ms": int(raw.get("quiet_ms") or 0)}


def _overlap(a: tuple, b: tuple) -> float:
    if not a and not b:
        return 1.0
    sa, sb = set(a), set(b)
    return len(sa & sb) / max(len(sa), len(sb))


class StepWatcher:
    """
    Turns successive shape reads into step events. Feed it every read; it
    returns None until the page settles on something worth acting on:
      new_step  a step not seen before (a new address, or mostly new fields);
      revisit   back on a step already filled (same address and fields);
      changed   the same step with fields added or removed (a follow-up
                question revealed, say);
      rerender  the same step and fields, but the agent's ref stamps are gone
                from fields still on the page (React re-mounted them, e.g. on
                a validation error).
    Each event comes with the step's number, counted from 1 in the order
    steps were first seen.
    """

    def __init__(self, quiet_ms: int = QUIET_MS):
        self.quiet_ms = quiet_ms
        self._prev: tuple | None = None
        self._streak = 0
        self.current: tuple | None = None          # (url, fields) of the settled step
        self.step_no = 0
        self._steps: dict[tuple, int] = {}         # (url, fields) -> step number
        self._rerender_reported = False
        self._was_stamped = False                  # the agent has filled the current step

    def observe(self, shape: dict | None) -> tuple[str, int] | None:
        if shape is None:
            self._prev, self._streak = None, 0
            return None
        key = (shape["url"], shape["fields"])
        self._streak = self._streak + 1 if key == self._prev else 1
        self._prev = key
        if self._streak < 2 or shape["quiet_ms"] < self.quiet_ms or not shape["fields"]:
            return None
        if key == self.current:
            if 0 < shape["count"] and shape["stamped"] < shape["count"] and self._was_stamped:
                if not self._rerender_reported:
                    self._rerender_reported = True
                    return RERENDER, self._steps[key]
            return None
        previous, self.current = self.current, key
        self._rerender_reported = False
        self._was_stamped = False
        if key in self._steps:
            return REVISIT, self._steps[key]
        if (previous is not None and previous[0] == key[0]
                and _overlap(previous[1], key[1]) >= SAME_STEP_OVERLAP):
            number = self._steps[previous]
            self._steps[key] = number
            return CHANGED, number
        self.step_no += 1
        self._steps[key] = self.step_no
        return NEW_STEP, self.step_no

    def filled(self) -> None:
        """The agent has just filled the current step, stamping its fields."""
        self._was_stamped = True
        self._rerender_reported = False
