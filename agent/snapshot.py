"""
snapshot.py — a structure dump of the page the agent's browser is on, with no values in it.

WHAT THIS DOES: Reads the structure of a page (tag, type, role, aria-*,
data-automation-id, name, id, label text, required, visibility, the
container chain) for every control, label and automation-id element,
skipping the header / account area, reading no input value (a dropdown
button's own text and aria-label, which show its choice, and anything a
label wraps that holds a value, are left out too), and then
strips from the whole dump every string that equals or contains a value
from the profile (which `apply.py snapshot` loads at run time; the dump is
written to the repo's gitignored snapshots/ folder). WHY IT MATTERS:
Jack's Workday run failed on every text box (2026-10-20); mocks built
from a real page's structure are how the adapter gets fixed, and a dump
that could carry his name, email or a typed value must never exist.
"""

from __future__ import annotations

import json
import re
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from . import config, extract

if TYPE_CHECKING:
    from playwright.sync_api import Page

SNAPSHOTS_DIR = config.ROOT / "snapshots"
REDACTED = "[profile]"
MIN_VALUE_CHARS = 3            # shorter profile values ("CA", "US") would blank every page
MIN_VALUE_DIGITS = 7           # a phone or postal number, read through its digits
# Every control, label and Workday-tagged element, outside the header /
# account area; each as structure only. No .value, no option values, no
# text of anything but labels, legends, buttons and options (page
# vocabulary, filtered again in Python against the profile).
_DUMP_JS = extract.safe(r"""
(limits) => {
  const SKIP = 'header, [role=banner], nav, [data-automation-id*="account" i], [data-automation-id*="profile" i],'
             + ' [data-automation-id="header"], [data-automation-id="navigationHeader"], [data-automation-id="candidateHome"]';
  const norm = t => String(t || '').replace(/\s+/g, ' ').trim().slice(0, limits.text);
  const visible = el => {
    const r = el.getBoundingClientRect(), s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
  };
  const tagOf = el => {
    let t = __ja.tag(el).toLowerCase();
    if (__ja.id(el)) t += '#' + __ja.id(el).slice(0, 60);
    const aid = __ja.attr(el, 'data-automation-id');
    if (aid) t += '[data-automation-id=' + aid.slice(0, 60) + ']';
    const role = __ja.attr(el, 'role');
    if (role) t += '[role=' + role + ']';
    return t;
  };
  const chainOf = el => {
    const out = [];
    for (let p = __ja.parent(el); p && p !== document.documentElement; p = __ja.parent(p)) out.push(tagOf(p));
    return out.slice(0, limits.chain);
  };
  const byId = id => document.getElementById(id);
  // Anything that holds what was typed or chosen: never part of a label's
  // text, never dumped as text itself.
  const HOLDS_VALUE = 'input, textarea, select, button, [contenteditable], [role=combobox], [role=textbox],'
                    + ' [role=listbox], [role=option], [aria-haspopup]';
  const holdsValue = el => __ja.matches(el, HOLDS_VALUE);
  // A label's own words: a clone with every value-holding descendant removed.
  const cleanText = (node, own) => {
    // A value-holding root (a dropdown named by its own id) has no words of its
    // own to give a label; a plain button asked for its own wording (own) does.
    if (!node || (!own && holdsValue(node))) return '';
    const c = node.cloneNode(true);
    c.querySelectorAll(HOLDS_VALUE).forEach(e => e.remove());
    return norm(c.textContent);
  };
  const labelOf = el => {
    const al = __ja.attr(el, 'aria-label'); if (al && !holdsValue(el)) return norm(al);
    const lb = __ja.attr(el, 'aria-labelledby');
    if (lb) { const t = norm(lb.split(/\s+/).map(i => cleanText(byId(i))).join(' ')); if (t) return t; }
    if (__ja.id(el)) { const l = document.querySelector('label[for="' + CSS.escape(__ja.id(el)) + '"]'); if (l) return cleanText(l); }
    const w = __ja.closest(el, 'label'); if (w) return cleanText(w);
    let p = __ja.parent(el);
    for (let i = 0; i < 4 && p; i++, p = __ja.parent(p)) {
      const n = p.querySelector(':scope > legend, :scope > label, :scope > .label'); if (n) return cleanText(n);
    }
    return '';
  };
  // Elements whose text is page wording, not a value. A button that is a
  // dropdown (aria-haspopup, role=combobox) shows its committed choice as
  // its text and often in aria-label: neither is dumped (a chosen gender,
  // country or state is a value).
  const TEXTUAL = new Set(['label', 'legend', 'a', 'option', 'h1', 'h2', 'h3', 'h4', 'summary', 'th']);
  const isChooser = el => __ja.has(el, 'aria-haspopup') || ['combobox', 'listbox', 'option', 'textbox'].includes(__ja.attr(el, 'role') || '')
                      || (__ja.tag(el) === 'BUTTON' && !!__ja.closest(el, '[data-automation-id^="formField"]'));
  const plainButton = el => __ja.tag(el) === 'BUTTON' && !isChooser(el);
  const sel = 'input, select, textarea, button, [role], [data-automation-id], label, legend, fieldset, a[href],'
            + ' [aria-haspopup], [contenteditable], h1, h2, h3, h4';
  const out = [];
  const els = [...document.querySelectorAll(sel)];
  for (let i = 0; i < els.length && out.length < limits.elements; i++) {
    const el = els[i];
    if (__ja.closest(el, SKIP)) continue;
    const tag = __ja.tag(el).toLowerCase();
    const chooser = isChooser(el);
    const aria = {};
    for (const a of __ja.attrs(el)) {
      // The value-like aria attributes: a number, a text, a chosen / pressed state.
      if (!a.name.startsWith('aria-') || ['aria-valuenow', 'aria-valuetext', 'aria-checked', 'aria-selected', 'aria-pressed'].includes(a.name)) continue;
      if (a.name === 'aria-label' && (chooser || holdsValue(el))) continue;   // may carry the choice or the value
      aria[a.name] = norm(a.value);
    }
    const entry = {
      i: out.length, tag, type: __ja.attr(el, 'type') || null, role: __ja.attr(el, 'role') || null,
      automation_id: __ja.attr(el, 'data-automation-id') || null,
      name: __ja.attr(el, 'name') || null, id: __ja.id(el) || null,
      label: ['input', 'select', 'textarea'].includes(tag) || __ja.attr(el, 'role') || chooser || __ja.has(el, 'contenteditable') ? labelOf(el) : '',
      text: (TEXTUAL.has(tag) && !chooser && !holdsValue(el)) ? cleanText(el) : plainButton(el) ? cleanText(el, true) : '',
      chooser,
      required: el.required === true || __ja.attr(el, 'aria-required') === 'true' || __ja.has(el, 'required'),
      disabled: el.disabled === true || __ja.attr(el, 'aria-disabled') === 'true',
      readonly: el.readOnly === true || __ja.attr(el, 'readonly') !== null,
      visible: visible(el), aria,
      classes: [...__ja.classes(el)].slice(0, 4), chain: chainOf(el),
    };
    if (tag === 'select') entry.options = [...el.options].slice(0, limits.options).map(o => norm(o.text));
    if (tag === 'input' && (__ja.attr(el, 'type') || '').toLowerCase() === 'file') entry.accept = __ja.attr(el, 'accept') || null;
    out.push(entry);
  }
  return {title: norm(document.title), elements: out, total: els.length,
          frames: document.querySelectorAll('iframe, frame').length,
          shadow_hosts: [...document.querySelectorAll('*')].filter(e => e.shadowRoot).length};
}
""")

LIMITS = {"text": 200, "chain": 12, "elements": 1500, "options": 60}


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", str(s))).strip().casefold()


def profile_values(data: Any) -> tuple[set[str], set[str]]:
    """
    Every leaf value of the profile as normalised text (3+ characters), and
    the digits of every value with 7+ digits (a phone, a postal number).
    """
    texts: set[str] = set()
    digits: set[str] = set()

    def walk(node):
        if isinstance(node, dict):
            for v in node.values():
                walk(v)
        elif isinstance(node, (list, tuple, set)):
            for v in node:
                walk(v)
        elif isinstance(node, bool) or node is None:
            return
        else:
            s = _norm(node)
            if len(s) >= MIN_VALUE_CHARS:
                texts.add(s)
            d = re.sub(r"\D", "", s)
            if len(d) >= MIN_VALUE_DIGITS:
                digits.add(d)
    walk(data)
    return texts, digits


def strip_values(obj: Any, texts: set[str], digits: set[str]) -> Any:
    """
    The dump with every string that contains a profile value (or whose
    digits contain a profile number) replaced whole by REDACTED. Keys are
    structure and are not stripped; a value in a key would be a bug upstream.
    """
    if isinstance(obj, str):
        s = _norm(obj)
        if any(v in s for v in texts):
            return REDACTED
        d = re.sub(r"\D", "", s)
        # A profile number inside the string's digits, or the string's own
        # digits (7+) inside a profile number: "824-9000" is part of the phone.
        if d and any(n in d or (len(d) >= MIN_VALUE_DIGITS and d in n) for n in digits):
            return REDACTED
        return obj
    if isinstance(obj, list):
        return [strip_values(v, texts, digits) for v in obj]
    if isinstance(obj, dict):
        return {k: strip_values(v, texts, digits) for k, v in obj.items()}
    return obj


NAVIGATING = "navigating"            # the page moved under the read, twice
_DESTROYED = "Execution context was destroyed"


def dump(page: "Page", profile_data: Any, extra_values: Any = ()) -> dict:
    """
    The structure of the page the tab shows, stripped of profile values
    and of `extra_values` (Jack, 2026-11-03: the learned answers and the
    current fill's typed values, read back or planned, 3+ characters, the
    same way as a profile value; the wizard gathers them). A dropdown
    button's shown value is never dumped by the page script, whatever its
    length. A read that lands on a navigation ("Execution context was
    destroyed") is tried once more after a short wait; a second one, or
    any other failure, gives {"error": <a fixed category>} with no
    elements, never an exception and never the message text (Jack,
    2026-11-02, item 4).
    """
    got = None
    for attempt in (1, 2):
        try:
            got = page.evaluate(_DUMP_JS, LIMITS)
            break
        except Exception as e:
            navigating = _DESTROYED in str(e)
            if attempt == 1 and navigating:
                try:
                    page.wait_for_timeout(500)
                except Exception:
                    pass
                continue
            return {"error": NAVIGATING if navigating else f"unreadable:{type(e).__name__}",
                    "url": "", "title": "", "elements": []}
    if not isinstance(got, dict):
        got = {"title": "", "elements": [], "total": 0}
    parts = urlsplit(page.url)
    out = {
        "url": f"{parts.scheme}://{parts.hostname or ''}{parts.path}",   # no query, no fragment
        "title": got.get("title", ""),
        "total_matched": got.get("total", 0),
        "frames": got.get("frames", 0),               # not dumped: the main frame's light DOM only
        "shadow_hosts": got.get("shadow_hosts", 0),   # open roots not dumped either
        "elements": got.get("elements") or [],
    }
    texts, digits = profile_values([profile_data, list(extra_values or [])])
    out = strip_values(out, texts, digits)
    out["taken"] = datetime.now().isoformat(timespec="seconds")   # after the strip: a year is often a profile value
    return out


def write(snapshot: dict, name: str | None = None, folder: Path | None = None) -> Path:
    """Write the dump under snapshots/ (gitignored) and return its path."""
    folder = folder or SNAPSHOTS_DIR
    folder.mkdir(parents=True, exist_ok=True)
    host = re.sub(r"[^a-z0-9.-]", "", (urlsplit(snapshot.get("url", "")).hostname or "page").lower())
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    base = re.sub(r"[^A-Za-z0-9_-]", "", name or "") or stamp
    path = folder / f"{base}-{host}.json"
    path.write_text(json.dumps(snapshot, indent=1, ensure_ascii=False), encoding="utf-8")
    return path
