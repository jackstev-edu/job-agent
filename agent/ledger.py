"""
ledger.py — in follow mode, never overwrite you, and never type while you do.

WHAT THIS DOES: Keeps a per-field ledger keyed by structure (stable
identity, tag, type) saying whose each field is: the agent's, once it
filled an empty field, or yours, once the field had a value before the
agent touched it or you changed what the agent put there. Only empty
fields that aren't yours are filled; after a re-render a field is refilled
only if it's empty, its element was replaced, and the last look showed the
agent's own value. An activity guard waits for two seconds without input
from you before filling, and says "hands off — filling" while it does.
WHY IT MATTERS: In follow mode you and the agent work on the same page.
Your answers always win, and the agent must never type over you.

Every look at a field runs in the page, after the password test, and
returns only true / false (empty? the agent's value?), never a value.
The one exception is `sweep`, which brings back the identity parts of the
fields you touched (tag, name, id, explicit label) so a touch outlives a
re-mount. No field's value is ever read, and nothing from the page here
reaches a model or the network.
"""

from __future__ import annotations

import itertools
import re
import time
import unicodedata
from contextlib import contextmanager
from typing import TYPE_CHECKING

from . import extract

if TYPE_CHECKING:
    from playwright.sync_api import Page

# The attribute an element the agent filled carries, so a later look can
# tell the same element from a re-rendered replacement. The scan never
# clears it (it clears only data-ja-ref).
MARK_ATTR = "data-ja-filled"
# The attribute the activity listener puts on anything you type in, paste
# into, change or click, so a field you touched never reaches a model, even
# once you've cleared it.
TOUCH_ATTR = "data-ja-touched"
# Put on what gets typed in, pasted into or changed (trusted events only)
# while the agent is filling: that's you, unless the agent then marks the
# field as filled (MARK_ATTR). The agent's own text sets are untrusted.
WHILE_AGENT_ATTR = "data-ja-input-while-agent"

QUIET_MS = 2000


def field_identity(f: dict) -> tuple | None:
    """
    What a field is known by across rescans and re-renders: its kind, tag,
    name attribute and explicit label (a real <label> or aria-label). None
    without a name or an explicit label: an id or a nearby-text label isn't
    an identity.
    """
    name = f.get("dom_name") if "dom_name" in f else (
        f.get("name") if f.get("type") == "radio" else "")
    label = f.get("explicit_label") or ""
    if not (name or label):
        return None
    return (f.get("type"), f.get("tag") or "", name or "", label)


def _touch_tuple(tag: str, dom_name: str, dom_id: str, label: str) -> tuple:
    """One touched element, by its identity parts. The label is normalised
    as secret_keys_of normalises it, so both sides compare the same."""
    return (tag, dom_name, dom_id,
            " ".join(unicodedata.normalize("NFKC", label).split()).casefold())


def _touch_tuple_of(f: dict) -> tuple:
    return _touch_tuple(str(f.get("tag") or ""), str(f.get("dom_name") or ""),
                        str(f.get("dom_id") or ""), str(f.get("explicit_label") or ""))


def _keys_of(whole: tuple) -> set:
    """
    The keys one touched element is matched by: its name, id and label
    each alone (secret_keys_of), the anonymous key for its tag, and the
    tagless catch-all, which is the only key a second-pass field (a div or
    button dropdown, a button group) has. So once you touch anything, no
    field without a tag of its own reaches a model for the rest of the run.
    """
    tag, dom_name, dom_id, label = whole
    keys = extract.secret_keys_of({"tag": tag, "dom_name": dom_name,
                                   "dom_id": dom_id, "explicit_label": label})
    return keys | {("anon", tag), ("anon", "")}


def _norm(text) -> str:
    return " ".join(unicodedata.normalize("NFKC", str(text or "")).split()).casefold()


def _kind(f: dict) -> str:
    t = (f.get("type") or "").lower()
    if t == "radio":
        return "buttons" if f.get("widget") == "buttons" else "radio"
    if t in ("checkbox", "select", "combobox", "file"):
        return t
    return "text"


# One field's state, from its elements: [kind, option elements or the
# element, the agent's value or null, the option index the agent chose].
# Returns {present, empty, mine, same} or {present: false}; "same" is
# whether every element still carries the agent's mark.
_STATE_JS = ("""
(args) => {
""" + extract._SECRET_JS + r"""
  const [kind, els, agentValue, agentIndex, mark] = args;
  if (!els.length || els.some(e => !e || !e.isConnected)) return {present: false};
  if (els.some(e => isSecret(e, ''))) return {present: true, secret: true};
  if (document.querySelector('input[type=password i], [autocomplete*="password" i]'))
    return {present: true, secret: true};
  const norm = t => String(t ?? '').normalize('NFKC').replace(/\s+/g, ' ').trim().toLowerCase();
  const same = !!mark && els.every(e => e.getAttribute('""" + MARK_ATTR + r"""') === mark);
  const marked = els.some(e => e.hasAttribute('""" + MARK_ATTR + r"""'));
  let touched = els.some(e => e.hasAttribute('""" + TOUCH_ATTR + r"""'));
  const whileFilling = els.some(e => e.hasAttribute('""" + WHILE_AGENT_ATTR + r"""')
      && !e.hasAttribute('""" + MARK_ATTR + r"""'));
  const el = els[0];
  let empty, mine = false;
  if (kind === 'radio' || kind === 'buttons') {
    const on = els.map(e => kind === 'radio' ? e.checked : e.getAttribute('aria-pressed') === 'true');
    empty = !on.some(Boolean);
    mine = agentIndex !== null && agentIndex >= 0 && on[agentIndex] && on.filter(Boolean).length === 1;
  } else if (kind === 'checkbox') {
    empty = !el.checked;
    mine = agentValue !== null && el.checked === !!agentValue;
  } else if (kind === 'select') {
    const opt = el.selectedOptions[0];
    empty = !opt || (el.selectedIndex <= 0 && (opt.value === '' || !norm(opt.text)));
    mine = !!opt && agentValue !== null && norm(opt.text) === norm(agentValue);
  } else if (kind === 'file') {
    empty = !(el.files && el.files.length);
    mine = false;
  } else if (kind === 'combobox') {
    const control = el.closest('[class*="control"]') || el.parentElement?.closest('[role=combobox]');
    const shown = control ? [...control.querySelectorAll(
        '[class*="single-value"], [class*="singleValue"], [class*="multi-value"], ' +
        '[class*="multiValue"], [class*="chip" i], [class~="tag" i], [class*="ant-tag"], ' +
        '[class*="ant-select-selection-item"], [class*="select2-selection__choice"], ' +
        '[class*="choices__item"], [class*="search-choice"], [class*="chosen-single"], ' +
        '[class*="selected-item" i], [class*="selectedItem" i], ' +
        '[data-automation-id="selectedItem"]')]
        .map(d => d.innerText).filter(t => norm(t)) : [];
    const own = ('value' in el && el.tagName !== 'BUTTON') ? el.value : '';
    // A button or div combobox shows its choice as its own text, and Workday
    // can show one in its form field, outside any control container: any
    // text there and it isn't empty (a placeholder can't be told apart).
    const ownText = (el.tagName === 'BUTTON' || !('value' in el)) ? el.innerText : '';
    const wd = el.closest('[data-automation-id^="formField"]');
    const wdShown = wd ? [...wd.querySelectorAll('[data-automation-id="selectedItem"]')]
        .map(d => d.innerText).filter(t => norm(t)) : [];
    // You may pick a row with the mouse and clear it with the X: neither
    // touches the input, both touch its control.
    if (control && (control.hasAttribute('""" + TOUCH_ATTR + r"""')
        || control.querySelector('[""" + TOUCH_ATTR + r"""]'))) touched = true;
    empty = !shown.length && !norm(own) && !norm(ownText) && !wdShown.length;
    mine = agentValue !== null && (shown.length ? shown.some(t => norm(t) === norm(agentValue))
                                                 : norm(own) === norm(agentValue));
  } else {
    empty = !norm(el.value);
    mine = agentValue !== null && norm(el.value) === norm(agentValue);
  }
  return {present: true, empty, mine, same, marked, touched, whileFilling};
}
""")

_MARK_JS = ("""
([els, mark]) => els.forEach(e => e && e.setAttribute('""" + MARK_ATTR + """', mark))
""")


def _elements(page: "Page", refs: list[str]):
    """The one element carrying each ref, or None for any that isn't unique."""
    out = []
    for r in refs:
        loc = page.locator(f"[{extract.REF_ATTR}='{r}']")
        out.append(loc.element_handle() if loc.count() == 1 else None)
    return out


def _option_index(f: dict, value) -> int | None:
    """
    Which option element the agent's value is. None (never "the agent's")
    when the options and the option elements don't line up one to one: the
    scan drops an option with no label text, which would shift every index.
    """
    options = f.get("options") or []
    if len(options) != len(f.get("option_refs") or []):
        return None
    return options.index(str(value)) if value is not None and str(value) in options else None


def _refs(f: dict) -> list[str]:
    return list(f.get("option_refs") or [f["ref"]])


class FieldLedger:
    """
    Whose each field is, by structure. Only a field whose identity is
    unique in the scan can be tracked across re-renders. One without (no
    identity, or one shared with another field) is never filled while any
    of its elements carries an agent mark, and once the agent has filled a
    field like that on a step, none is filled on that step again.
    """

    def __init__(self):
        self.entries: dict[tuple, dict] = {}
        self._marks = (f"m{n}" for n in itertools.count(1))
        self._anon_steps: set = set()               # steps where an untracked field was filled
        self._touched_kinds: set = set()            # (type, tag) of untracked fields you touched
        self._touched: set = set()                  # identity keys of what you touched
        self._typed_while_agent: set = set()        # (tag, name, id, label) got input while filling
        self._agent_filled: set = set()             # (tag, name, id, label) the agent filled
        self.left_out: dict[str, str] = {}

    @staticmethod
    def _keys(fields: list[dict]) -> dict[str, tuple | None]:
        """Each field's identity, or None when it has none or shares it in this scan."""
        idents = {f["ref"]: field_identity(f) for f in fields}
        counts: dict = {}
        for k in idents.values():
            if k is not None:
                counts[k] = counts.get(k, 0) + 1
        return {ref: (k if k is not None and counts[k] == 1 else None)
                for ref, k in idents.items()}

    def state(self, page: "Page", f: dict, value=None, mark: str | None = None) -> dict:
        """The field's state now, from the page; {"present": False} if it can't be told."""
        if extract.is_secret(f) or f.get("credential_step") is not False:
            return {"present": True, "secret": True}
        try:
            els = _elements(page, [r for r in _refs(f) if extract.is_current(r)])
            if not els or None in els:
                return {"present": False}
            return page.evaluate(_STATE_JS, [_kind(f), els, None if value is None else value,
                                             _option_index(f, value), mark])
        except Exception:
            return {"present": False}

    def open_fields(self, page: "Page", fields: list[dict]) -> set[str]:
        """
        The refs of the fields that are empty, carry no agent mark, were
        never touched by you (typed in, pasted into, changed or clicked
        since the listener was installed) and aren't yours, read now from
        the page (booleans only, through the same secret-safe check as
        fillable). Only these may reach a model: a field holding any value
        is never filled, and its own label or help can echo that value, or
        what you typed and cleared. A tracked field (one with a unique
        identity) seen with a value, touched by you, or found cleared after
        the agent filled it becomes yours for the run. An untracked one is
        left out while it shows a value or carries a touch mark; once both
        are gone (a re-render), so once one is seen touched, every
        untracked field of the same kind and tag stays out for the run.
        Touches are remembered by identity (name, id, explicit label,
        an anonymous tag key and a tagless catch-all, as secret_keys_of),
        from the listener's record, so a field re-mounted or revisited
        after you touched it matches too, a second-pass field that has no
        tag of its own included. `left_out` says why each other field was
        left out: "agent" (the agent filled it), "value" (holding a value
        the agent didn't put there), "touched" (yours: touched, cleared or
        like one you touched) or "while_filling" (typed into while the
        agent was filling: its own attempt, or yours).
        """
        self.sweep(page)
        touched_keys = self._touched
        filling_keys = self.while_filling_keys()
        keys = self._keys(fields)
        out: set[str] = set()
        self.left_out: dict[str, str] = {}
        for f in fields:
            if f.get("type") == "file":
                continue
            key = keys.get(f["ref"])
            entry = self.entries.get(key) if key else None
            st = self.state(page, f, mark=(entry or {}).get("mark"))
            if not st.get("present") or st.get("secret"):
                continue
            kind = (f.get("type"), f.get("tag") or "")
            holds = not st["empty"]
            fkeys = extract.secret_keys_of(f)
            # Whose it is. A field with nothing to know it by ("", "", "",
            # "") matches every other such field, so it never counts as the
            # agent's: that would drop its flag. Anything you touched is
            # yours even if the agent filled it first.
            whole = _touch_tuple_of(f)
            agents = any(whole) and whole in self._agent_filled
            yours = (st.get("touched") or fkeys & touched_keys
                     or (key is None and kind in self._touched_kinds))
            filling = st.get("whileFilling") or fkeys & filling_keys
            if yours or filling:
                if key is not None:
                    self.entries[key] = {"owner": "jack"}
                else:
                    self._touched_kinds.add(kind)
                self.left_out[f["ref"]] = ("value" if holds and yours else
                                           "touched" if yours else
                                           "agent" if agents else "while_filling")
                continue
            if entry is not None and entry.get("owner") == "jack":
                self.left_out[f["ref"]] = ("agent" if agents else
                                           "value" if holds else "touched")
                continue
            if holds:
                if key is not None and entry is None:
                    self.entries[key] = {"owner": "jack"}
                self.left_out[f["ref"]] = "agent" if agents else "value"
                continue
            if st.get("marked"):
                if key is not None and (entry is None or st.get("same")):
                    self.entries[key] = {"owner": "jack"}
                self.left_out[f["ref"]] = "agent" if agents else "touched"
                continue
            out.add(f["ref"])
        return out

    def sweep(self, page: "Page") -> None:
        """
        Copy the listener's record of what you touched (identity parts of
        form fields: tag, name, id, explicit label; never a value) into
        Python, so it outlasts a re-mount and a new document. Called on
        every poll and before every open check. Never fails the run.
        """
        try:
            got = page.evaluate(_TOUCHED_JS) or {}
        except Exception:
            return
        for name in ("t", "a"):
            for rec in got.get(name) or []:
                if not (isinstance(rec, list) and len(rec) == 4):
                    continue
                whole = _touch_tuple(*(str(x or "") for x in rec))
                if name == "t":
                    self._touched |= _keys_of(whole)
                else:
                    # Kept whole: input during a fill is the agent's own only
                    # if the agent then filled that very field, matched on all
                    # four parts, never on one shared key.
                    self._typed_while_agent.add(whole)

    def while_filling_keys(self) -> set:
        """
        Keys of every field something was typed into while the agent was
        filling. The agent's own typing isn't taken out: two fields with
        the same name, id and label can't be told apart, so taking it out
        dropped your record whenever the agent filled the twin. The
        agent's own field is excluded where it can be told for certain,
        on the element itself (_STATE_JS: marked by the agent, so not
        yours). The cost is a false positive after a re-mount: a field
        the agent filled becomes yours and isn't refilled.
        """
        keys: set = set()
        for whole in self._typed_while_agent:
            keys |= _keys_of(whole)
        return keys

    def fillable(self, page: "Page", fields: list[dict], plan: dict, step=None) -> dict:
        """
        The plan entries the agent may fill now. A field is filled only if
        it's empty and isn't yours; after a re-render, only if it's empty,
        its element was replaced and the agent's value showed at the last
        look. Every other field the agent has seen with a value becomes
        yours. `step` names the step (follow mode passes it), for fields
        that can't be tracked.
        """
        by_ref = {f["ref"]: f for f in fields}
        keys = self._keys(fields)
        out = {}
        for ref, decision in plan.items():
            f = by_ref.get(ref)
            if f is None:
                continue
            key = keys.get(ref)
            entry = self.entries.get(key) if key else None
            st = self.state(page, f, mark=(entry or {}).get("mark"))
            if not st.get("present") or st.get("secret"):
                continue
            if key is None:
                # Untracked: never over an element the agent marked, and
                # never twice on one step.
                # A missing step counts as one step.
                if st.get("marked") or step in self._anon_steps:
                    continue
                if st["empty"]:
                    out[ref] = decision
                continue
            if entry is None:
                # An element the agent marked while it couldn't track the
                # field (say its identity was shared then) is never refilled.
                if not st["empty"] or st.get("marked"):
                    self.entries[key] = {"owner": "jack"}
                    continue
                out[ref] = decision
            elif entry["owner"] == "jack":
                continue
            elif st["empty"] and not st.get("same") and entry.get("last_mine"):
                out[ref] = decision                 # re-rendered while showing ours
            elif st["empty"] and st.get("same"):
                entry["owner"] = "jack"             # you cleared it
            # Anything else (still showing a value, or a replacement whose
            # last look didn't show ours) is left alone.
        return out

    def filled(self, page: "Page", fields: list[dict], plan: dict, rows: list[dict],
               step=None) -> None:
        """
        Record what the agent just filled (run_plan's rows, in plan order)
        and mark it. "The agent's value showed" waits for the next look
        (poll); a field whose mark couldn't be set is recorded as yours, so
        it's never refilled.
        """
        by_ref = {f["ref"]: f for f in fields}
        keys = self._keys(fields)
        refs = [ref for ref in plan if ref in by_ref]
        for ref, row in zip(refs, rows):
            if not row.get("filled"):
                continue
            f = by_ref[ref]
            # Input on it during the fill was the agent's own typing. Kept
            # whole, so it cancels that field's record and no other's.
            self._agent_filled.add(_touch_tuple_of(f))
            mark = next(self._marks)
            try:
                els = _elements(page, _refs(f))
                if None in els:
                    raise ValueError("an element isn't unique")
                page.evaluate(_MARK_JS, [els, mark])
                marked = True
            except Exception:
                marked = False
            key = keys.get(ref)
            if key is None:
                self._anon_steps.add(step)
                # If it becomes trackable later (the field sharing its
                # identity goes away), it's never refilled.
                if field_identity(f) is not None:
                    self.entries[field_identity(f)] = {"owner": "jack"}
            elif not marked:
                self.entries[key] = {"owner": "jack"}
            else:
                self.entries[key] = {"owner": "agent", "value": plan[ref].get("value"),
                                     "mark": mark, "last_mine": False, "field": f}

    def poll(self, page: "Page") -> None:
        """
        Look again at every field the agent filled that's still on the page
        (found by its mark). Showing something other than the agent's value
        makes it yours; showing the agent's value is remembered, so a
        re-render right after can refill it.
        """
        for entry in self.entries.values():
            if entry.get("owner") != "agent":
                continue
            f, mark = entry["field"], entry["mark"]
            try:
                els = [h for h in page.query_selector_all(f"[{MARK_ATTR}='{mark}']")]
                if not els:
                    continue                        # re-rendered away; keep last_mine as it was
                if len(els) != len(_refs(f)):
                    entry["last_mine"] = False      # some options replaced: indices can't be trusted
                    continue
                st = page.evaluate(_STATE_JS, [_kind(f), els, entry["value"],
                                               _option_index(f, entry["value"]), mark])
            except Exception:
                continue
            if not st.get("present") or st.get("secret"):
                continue
            # The same element (it carries the mark), so anything but the
            # agent's value is you: changed or cleared.
            entry["last_mine"] = bool(st["mine"])
            if not st["mine"]:
                entry["owner"] = "jack"


# Notices your input. In follow mode it is on every new document before the
# page's scripts run (an init script) and installed again on every poll; the
# agent's own keystrokes and clicks happen while `agent` is set, so they
# don't count as yours. It also marks what you touched (TOUCH_ATTR): the
# event's target, the scanned field around it, a label's own control, a
# dropdown's control container, and the focused element (a dropdown keeps
# the focus while you pick a row with the mouse). While the agent fills,
# what's typed in, pasted into or changed still gets WHILE_AGENT_ATTR, and
# Backspace or Delete (which the agent never presses) counts as you. Each
# touched form field's identity parts (tag, name, id, explicit label; never
# a value) are also recorded, in window.__jaAct and the tab's
# sessionStorage, so the record outlasts a re-mount and a new document.
_ACTIVITY_JS = r"""
() => {
  if (window.__jaAct) return;
  // Starts as "just now": input before this was installed can't be seen,
  // so a fresh page always waits the full quiet period.
  window.__jaAct = {last: performance.now(), agent: false, t: [], a: []};
""" + extract._TEXT_OF_JS + r"""
  const explicitLabel = el => ((el.getAttribute('aria-label') || '').trim()
      || [...(el.labels || [])].map(l => textOf(l, el)).filter(Boolean).join(' '))
      .slice(0, 300);
  const STORE = '__jaTouched';
  const seen = new Set();
  // Called once when a run starts: a record kept in the tab outlives the
  // run that wrote it, and another run's touches aren't yours now.
  window.__jaAct.reset = () => {
    window.__jaAct.t = []; window.__jaAct.a = []; seen.clear();
    try { sessionStorage.removeItem(STORE); } catch (e) {}
  };
  try {
    const kept = JSON.parse(sessionStorage.getItem(STORE) || '{}');
    for (const list of ['t', 'a'])
      for (const rec of (Array.isArray(kept[list]) ? kept[list] : [])) {
        const k = list + JSON.stringify(rec);
        if (!seen.has(k)) { seen.add(k); window.__jaAct[list].push(rec); }
      }
  } catch (e) {}
  const FORMISH = 'input, select, textarea, [role=combobox], '
      + '[contenteditable]:not([contenteditable="false"])';
  const REF = '[""" + extract.REF_ATTR + r"""]';
  const fieldsOf = el => {
    const out = new Set();
    if (!(el instanceof Element) || el === document.body) return out;
    if (el.matches(FORMISH)) out.add(el);
    const ref = el.closest(REF);
    if (ref) out.add(ref);
    if (el instanceof HTMLLabelElement && el.control) out.add(el.control);
    const control = el.closest('[class*="control"], [role=combobox]');
    if (control) {
      if (control.matches(FORMISH)) out.add(control);
      control.querySelectorAll(FORMISH).forEach(x => out.add(x));
    }
    return out;
  };
  const record = (els, list) => {
    let added = false;
    for (const el of els)
      for (const f of fieldsOf(el)) {
        const rec = [f.tagName.toLowerCase(), typeof f.name === 'string' ? f.name : '',
                     f.id || '', explicitLabel(f)];
        const k = list + JSON.stringify(rec);
        if (seen.has(k)) continue;
        seen.add(k); window.__jaAct[list].push(rec); added = true;
      }
    if (added) try {
      sessionStorage.setItem(STORE, JSON.stringify({t: window.__jaAct.t, a: window.__jaAct.a}));
    } catch (e) {}
  };
  const put = (el, attr) => { if (el instanceof Element) el.setAttribute(attr, ''); };
  const T = '""" + TOUCH_ATTR + r"""';
  const touch = els => {
    for (const el of els) {
      if (!(el instanceof Element) || el === document.body) continue;
      put(el, T);
      put(el.closest(REF), T);
      put(el.closest('[class*="control"], [role=combobox]'), T);
      if (el instanceof HTMLLabelElement) put(el.control, T);
    }
    record(els, 't');
  };
  for (const t of ['keydown', 'input', 'pointerdown', 'mousedown', 'change', 'paste'])
    document.addEventListener(t, e => {
      if (!e.isTrusted) return;
      const path = e.composedPath ? e.composedPath() : [];
      const targets = [e.target, path[0]].filter(el => el instanceof Element);
      if (window.__jaAct.agent) {
        if (t === 'keydown' && (e.key === 'Backspace' || e.key === 'Delete')) {
          touch([...targets, document.activeElement]);
        } else if (['input', 'paste', 'change'].includes(t)) {
          for (const el of targets) {
            put(el, '""" + WHILE_AGENT_ATTR + r"""');
            put(el.closest(REF), '""" + WHILE_AGENT_ATTR + r"""');
          }
          record(targets, 'a');
        }
        return;
      }
      window.__jaAct.last = performance.now();
      touch([...targets, document.activeElement]);
    }, true);
}
"""
# The listener's record of what you touched: identity parts only.
_TOUCHED_JS = "() => window.__jaAct ? {t: window.__jaAct.t || [], a: window.__jaAct.a || []} : null"
_RESET_JS = """() => {
  if (window.__jaAct && window.__jaAct.reset) window.__jaAct.reset();
  try { sessionStorage.removeItem('__jaTouched'); } catch (e) {}
}"""


def clear_touch_record(page: "Page") -> None:
    """Forget what an earlier run on this origin left in the tab. Never fails."""
    try:
        page.evaluate(_RESET_JS)
    except Exception:
        pass
# The same listener as an init script, for every new document.
ACTIVITY_INIT_SCRIPT = "(" + _ACTIVITY_JS.strip() + ")();"
_QUIET_JS = "() => window.__jaAct ? performance.now() - window.__jaAct.last : 0"
_AGENT_JS = "(on) => { if (window.__jaAct) window.__jaAct.agent = on; }"


class ActivityGuard:
    """Fill only after `quiet_ms` without input from you; say so while filling."""

    def __init__(self, quiet_ms: int = QUIET_MS, echo=print, clock=time.monotonic):
        self.quiet_ms = quiet_ms
        self.echo = echo
        self.clock = clock

    def install(self, page: "Page") -> None:
        try:
            page.evaluate(_ACTIVITY_JS)
        except Exception:
            pass

    def quiet_for(self, page: "Page") -> float:
        """Milliseconds since your last input on this page (0 if it can't be told)."""
        try:
            return float(page.evaluate(_QUIET_JS))
        except Exception:
            return 0.0

    def wait_quiet(self, page: "Page", timeout_s: float = 600) -> bool:
        """Wait until you've been idle `quiet_ms`; False if that never happened in time."""
        self.install(page)
        start = self.clock()
        while self.clock() - start < timeout_s:
            if self.quiet_for(page) >= self.quiet_ms:
                return True
            try:
                page.wait_for_timeout(200)
            except Exception:
                return False
        return False

    @contextmanager
    def filling(self, page: "Page"):
        self.echo("  hands off — filling")
        try:
            page.evaluate(_AGENT_JS, True)
        except Exception:
            pass
        try:
            yield
        finally:
            try:
                page.evaluate(_AGENT_JS, False)
            except Exception:
                pass


class ClassificationCache:
    """
    Categories remembered by (stable identity, normalised label, normalised
    help): a re-render keeps them, a changed question doesn't.
    """

    def __init__(self):
        self._cache: dict[tuple, str] = {}

    @staticmethod
    def key(f: dict) -> tuple | None:
        ident = field_identity(f)
        if ident is None:
            return None
        return (ident, _norm(f.get("label")), _norm(f.get("help_text")))

    def split(self, fields: list[dict]) -> tuple[dict[str, str], list[dict]]:
        """(categories known for these fields, the fields still to classify)."""
        known, todo = {}, []
        for f in fields:
            k = self.key(f)
            if k is not None and k in self._cache:
                known[f["ref"]] = self._cache[k]
            else:
                todo.append(f)
        return known, todo

    def remember(self, fields: list[dict], categories: dict[str, str]) -> None:
        for f in fields:
            k = self.key(f)
            if k is not None and f["ref"] in categories:
                self._cache[k] = categories[f["ref"]]
