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
    const shown = control ? [...control.querySelectorAll('[class*="single-value"], [class*="singleValue"]')]
        .map(d => d.innerText).filter(t => norm(t)) : [];
    const own = ('value' in el && el.tagName !== 'BUTTON') ? el.value : '';
    empty = !shown.length && !norm(own);
    mine = agentValue !== null && (shown.length ? shown.some(t => norm(t) === norm(agentValue))
                                                 : norm(own) === norm(agentValue));
  } else {
    empty = !norm(el.value);
    mine = agentValue !== null && norm(el.value) === norm(agentValue);
  }
  return {present: true, empty, mine, same, marked};
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


# Notices your input. Installed once per page; the agent's own keystrokes
# and clicks happen while `agent` is set, so they don't count as yours.
_ACTIVITY_JS = r"""
() => {
  if (window.__jaAct) return;
  // Starts as "just now": input before this was installed can't be seen,
  // so a fresh page always waits the full quiet period.
  window.__jaAct = {last: performance.now(), agent: false};
  for (const t of ['keydown', 'input', 'pointerdown', 'mousedown', 'change', 'paste'])
    document.addEventListener(t, e => {
      if (e.isTrusted && !window.__jaAct.agent) window.__jaAct.last = performance.now();
    }, true);
}
"""
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
