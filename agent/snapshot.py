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

from . import config

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
_DUMP_JS = r"""
(limits) => {
  const SKIP = 'header, [role=banner], nav, [data-automation-id*="account" i], [data-automation-id*="profile" i],'
             + ' [data-automation-id="header"], [data-automation-id="navigationHeader"], [data-automation-id="candidateHome"]';
  const norm = t => String(t || '').replace(/\s+/g, ' ').trim().slice(0, limits.text);
  const visible = el => {
    const r = el.getBoundingClientRect(), s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
  };
  const tagOf = el => {
    let t = el.tagName.toLowerCase();
    if (el.id) t += '#' + el.id.slice(0, 60);
    const aid = el.getAttribute('data-automation-id');
    if (aid) t += '[data-automation-id=' + aid.slice(0, 60) + ']';
    const role = el.getAttribute('role');
    if (role) t += '[role=' + role + ']';
    return t;
  };
  const chainOf = el => {
    const out = [];
    for (let p = el.parentElement; p && p !== document.documentElement; p = p.parentElement) out.push(tagOf(p));
    return out.slice(0, limits.chain);
  };
  const byId = id => document.getElementById(id);
  // Anything that holds what was typed or chosen: never part of a label's
  // text, never dumped as text itself.
  const HOLDS_VALUE = 'input, textarea, select, button, [contenteditable], [role=combobox], [role=textbox],'
                    + ' [role=listbox], [role=option], [aria-haspopup]';
  const holdsValue = el => el.matches(HOLDS_VALUE);
  // A label's own words: a clone with every value-holding descendant removed.
  const cleanText = node => {
    if (!node) return '';
    const c = node.cloneNode(true);
    c.querySelectorAll(HOLDS_VALUE).forEach(e => e.remove());
    return norm(c.textContent);
  };
  const labelOf = el => {
    const al = el.getAttribute('aria-label'); if (al && !holdsValue(el)) return norm(al);
    const lb = el.getAttribute('aria-labelledby');
    if (lb) { const t = norm(lb.split(/\s+/).map(i => cleanText(byId(i))).join(' ')); if (t) return t; }
    if (el.id) { const l = document.querySelector('label[for="' + CSS.escape(el.id) + '"]'); if (l) return cleanText(l); }
    const w = el.closest('label'); if (w) return cleanText(w);
    let p = el.parentElement;
    for (let i = 0; i < 4 && p; i++, p = p.parentElement) {
      const n = p.querySelector(':scope > legend, :scope > label, :scope > .label'); if (n) return cleanText(n);
    }
    return '';
  };
  // Elements whose text is page wording, not a value. A button that is a
  // dropdown (aria-haspopup, role=combobox) shows its committed choice as
  // its text and often in aria-label: neither is dumped (a chosen gender,
  // country or state is a value).
  const TEXTUAL = new Set(['label', 'legend', 'a', 'option', 'h1', 'h2', 'h3', 'h4', 'summary', 'th']);
  const isChooser = el => el.hasAttribute('aria-haspopup') || ['combobox', 'listbox', 'option', 'textbox'].includes(el.getAttribute('role') || '')
                      || (el.tagName === 'BUTTON' && !!el.closest('[data-automation-id^="formField"]'));
  const plainButton = el => el.tagName === 'BUTTON' && !isChooser(el);
  const sel = 'input, select, textarea, button, [role], [data-automation-id], label, legend, fieldset, a[href],'
            + ' [aria-haspopup], [contenteditable], h1, h2, h3, h4';
  const out = [];
  const els = [...document.querySelectorAll(sel)];
  for (let i = 0; i < els.length && out.length < limits.elements; i++) {
    const el = els[i];
    if (el.closest(SKIP)) continue;
    const tag = el.tagName.toLowerCase();
    const chooser = isChooser(el);
    const aria = {};
    for (const a of el.attributes) {
      if (!a.name.startsWith('aria-') || ['aria-valuenow', 'aria-valuetext'].includes(a.name)) continue;
      if (a.name === 'aria-label' && (chooser || holdsValue(el))) continue;   // may carry the choice or the value
      aria[a.name] = norm(a.value);
    }
    const entry = {
      i: out.length, tag, type: el.getAttribute('type') || null, role: el.getAttribute('role') || null,
      automation_id: el.getAttribute('data-automation-id') || null,
      name: el.getAttribute('name') || null, id: el.id || null,
      label: ['input', 'select', 'textarea'].includes(tag) || el.getAttribute('role') || chooser || el.hasAttribute('contenteditable') ? labelOf(el) : '',
      text: TEXTUAL.has(tag) ? cleanText(el) : plainButton(el) ? cleanText(el) : '',
      chooser,
      required: el.required === true || el.getAttribute('aria-required') === 'true' || el.hasAttribute('required'),
      disabled: el.disabled === true || el.getAttribute('aria-disabled') === 'true',
      readonly: el.readOnly === true || el.getAttribute('readonly') !== null,
      visible: visible(el), aria,
      classes: [...el.classList].slice(0, 4), chain: chainOf(el),
    };
    if (tag === 'select') entry.options = [...el.options].slice(0, limits.options).map(o => norm(o.text));
    if (tag === 'input' && (el.getAttribute('type') || '').toLowerCase() === 'file') entry.accept = el.getAttribute('accept') || null;
    out.push(entry);
  }
  return {title: norm(document.title), elements: out, total: els.length,
          frames: document.querySelectorAll('iframe, frame').length,
          shadow_hosts: [...document.querySelectorAll('*')].filter(e => e.shadowRoot).length};
}
"""

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


def dump(page: "Page", profile_data: Any) -> dict:
    """The structure of the page the tab shows, stripped of profile values."""
    got = page.evaluate(_DUMP_JS, LIMITS)
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
    texts, digits = profile_values(profile_data)
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
