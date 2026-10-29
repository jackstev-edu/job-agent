"""
wizard.py — an application, page by page, until it is submitted or waits for Jack.

WHAT THIS DOES: Runs the loop the owner decisions allow: on each page, a
CAPTCHA pauses for Jack; an account, sign-in or verification page goes to
accounts.py; any other page on the application's own site is given a
bounded wait to render its form (FORM_WAIT_S; Jack, 2026-10-26), then
filled by the shared pipeline, read back and judged by the gate; a stop pauses for Jack
and is judged again once he has acted (a field he touched in the tab, a
value he approved in the GUI that the page still holds); on a page the
wizard will press Next on, a required terms / privacy box the gate
planned as would_tick is ticked through submit.py's own helper and the
page judged again (Jack, 2026-10-23); then Next is pressed and the page
must change; a page with Submit and no Next is the final page, which
submit.py handles while the auto-submit switch reads ON. With the switch
OFF (Jack, 2026-10-29: the default) the final page's per-page gate pause
is skipped, the gate decides on the full record, nothing is ticked or
pressed, and the job parks as "ready" for Jack to press Submit himself;
a job parked ready once never auto-submits again. In the terminal a pause blocks; in
GUI mode it is deferred: the wizard keeps its place, returns "waiting",
and `resume` picks up where it stopped when Jack presses Continue. Caps:
25 pages, 60 minutes of the agent's own time (waiting doesn't count).
WHY IT MATTERS: This is the one place Next is pressed and the order of
every check is decided. Nothing here submits; submit.py does, after the
gate. A job that needs Jack never fails and never closes (Jack,
2026-10-19); in the GUI every park waits in its tab with Continue, which
reads the page again (Jack, 2026-10-26).
"""

from __future__ import annotations

import re
import time
from collections import Counter
from dataclasses import dataclass, field as dataclass_field
from typing import TYPE_CHECKING

from . import (accounts, browser as browser_mod, chooser, extract, follow, gate, google, gui as gui_mod, intake,
               ledger, pages, pause as pause_mod, pipeline, submit as submit_mod)

if TYPE_CHECKING:
    from playwright.sync_api import Page

STEP_CAP = 25
TIME_CAP_S = 60 * 60

# How long a page gets to render its form before the scan concludes "no
# fields" (Jack, 2026-10-26). Workday draws My Information client-side
# after the /apply address has loaded, and after a hand sign-in the tab
# is on that address with the form still to come, so a scan at once
# found nothing and the job parked. The probe is polled every
# FORM_POLL_MS from the moment the page is reached; 20 s covers a slow
# network several times over (the form appears within a few seconds),
# while a page that really has no form (a chooser, a dead end) pays the
# wait once and parks as before. The wait only delays the scan: it
# never changes what the scan finds, and it clicks nothing.
FORM_WAIT_S = 20
FORM_POLL_MS = 500
# A pass of the loop that ends in the wait (the page moved, or drew its
# form after a poll) is made again from the top, every check included;
# this many in a row with no scan means the page keeps changing: park.
FORM_RECHECK_CAP = 3

WAITING = "waiting"

# The ready park's wordings (Jack, 2026-10-29).
READY_FILLED = "Filled — review and press Submit yourself"
READY_NO_CONTROLS = ("no Next or Submit found: if this is the last page, submit it yourself; "
                     "otherwise move on and press Continue")
READY_SUBMITTED = "confirmation seen: submitted by Jack"

# A control that moves to the next page, as whole normalised text. Never
# anything that reads as Submit (outcome.SUBMIT_WORDS), checked in the page.
FORWARD_WORDS = re.compile(
    r"^(next|continue|next step|next page|save (and|&) continue|save (and|&) next"
    r"|save (and|&) proceed|proceed|continue to (the )?next step|continue application"
    r"|review( application)?|go to review|save and review)$", re.I)
NEXT_ATTR = "data-ja-next"

_PORTAL_NEXT = {
    "workday": "button[data-automation-id='bottom-navigation-next-button']",
    "icims": "input[value='Continue']",
}

_FIND_NEXT_JS = r"""
([forwardRx, submitRx, extra, attr]) => {
  const FORWARD = new RegExp(forwardRx, 'i'), SUBMIT = new RegExp(submitRx, 'i');
  const norm = t => String(t || '').replace(/\s+/g, ' ').trim();
  const visible = el => {
    const r = el.getBoundingClientRect(), s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
  };
  document.querySelectorAll('[' + attr + ']').forEach(e => e.removeAttribute(attr));
  // A value is read only off a submit / button input, never off anything
  // else that may carry role=button.
  const isBtnInput = el => el.tagName === 'INPUT' && ['submit', 'button'].includes((el.type || '').toLowerCase());
  const text = el => norm(el.innerText || (isBtnInput(el) ? el.value : '') || el.getAttribute('aria-label'));
  // Anything that so much as mentions Submit is never a Next.
  const saysSubmit = t => SUBMIT.test(t) || /submit/i.test(t);
  let hits = extra ? [...document.querySelectorAll(extra)].filter(visible) : [];
  hits = hits.filter(el => !saysSubmit(text(el)));
  if (!hits.length) hits = [...document.querySelectorAll(
      'button, input[type=submit], input[type=button], [role=button], a')]
      .filter(visible).filter(el => { const t = text(el); return t && FORWARD.test(t) && !saysSubmit(t); });
  const words = new Set(hits.map(el => text(el).toLowerCase()));
  if (!hits.length || words.size > 1) return {found: hits.length, chosen: false, word: ''};
  const chosen = hits.find(el => (el.type || '').toLowerCase() === 'submit') || hits[0];
  chosen.setAttribute(attr, '1');
  return {found: hits.length, chosen: true, word: text(chosen), disabled: !!chosen.disabled};
}
"""

# Is at least one fillable control on the page now: a visible, enabled,
# writable input / select / textarea (the scan's own skips: button-like
# types, disabled, readOnly, the aria-hidden tabindex=-1 validation input)
# or a combobox / listbox the scan enriches. A yes/no that stamps nothing;
# the scan itself still decides what is a field.
_HAS_FIELDS_JS = r"""
() => {
  const vis = el => {
    const r = el.getBoundingClientRect(), s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
  };
  return [...document.querySelectorAll('input, select, textarea, [role=combobox], [role=listbox]')].some(el => {
    const type = (el.type || '').toLowerCase();
    if (['hidden', 'submit', 'button', 'reset', 'image'].includes(type)) return false;
    if (el.disabled || el.readOnly) return false;
    if (el.getAttribute('aria-hidden') === 'true' && el.tabIndex === -1) return false;
    return vis(el);
  });
}
"""


# Which of these refs carry a trusted-input mark from the activity listener
# on their own element (REVIEW_ATTR: set for isTrusted events while the
# agent isn't filling, and for a trusted Backspace or Delete while it is,
# which the agent never presses; the wizard's stricter reading of
# TOUCH_ATTR, see ledger). The mark on a wrapper doesn't count here: in follow
# mode over-marking keeps a field from the model, but here a mark waives a
# stop, so only the field's own element, or an option element of its
# group (Jack, 2026-10-26), says anything. Refs only.
_TOUCHED_JS = ledger.TOUCHED_JS


@dataclass
class Outcome:
    status: str                              # "submitted" | "needs_you" | "failed" | "waiting"
    reason: str = ""
    steps: list[dict] = dataclass_field(default_factory=list)      # shadow step entries
    events: list[dict] = dataclass_field(default_factory=list)
    rows: list[dict] = dataclass_field(default_factory=list)
    flags: list[dict] = dataclass_field(default_factory=list)
    record: dict | None = None
    gate_result: dict | None = None
    evidence: dict = dataclass_field(default_factory=dict)
    log: list[str] = dataclass_field(default_factory=list)
    printed: int = 0                         # how many log lines the terminal has shown
    pages_filled: int = 0
    wizard: object = None                    # the Wizard to resume, while waiting
    pause_kind: str = ""                     # the deferred pause's kind, while waiting
    submitted_by: str | None = None          # "agent" (submit.py pressed Submit) or "jack" (a confirmation seen after a ready park)
    gate_run_id: int | None = None           # the ready park's gate_runs row, once apply.py has written it


def find_next(page: "Page", portal: str) -> dict:
    """Mark the one control that moves forward (never Submit). Clicks nothing."""
    try:
        got = page.evaluate(_FIND_NEXT_JS, [FORWARD_WORDS.pattern, submit_mod.SUBMIT_WORDS.pattern,
                                            _PORTAL_NEXT.get(portal, ""), NEXT_ATTR])
    except Exception:
        return {"found": 0, "chosen": False, "word": ""}
    return got if isinstance(got, dict) else {"found": 0, "chosen": False, "word": ""}


def _clean(text: str) -> str:
    """Page-derived text for the terminal: no control or escape characters."""
    return re.sub(r"[\x00-\x1f\x7f-\x9f]", " ", str(text or ""))[:160]


def _same(a, b) -> bool:
    """The same text once whitespace is collapsed (what filler.py counts as filled)."""
    norm = lambda v: re.sub(r"\s+", " ", str("" if v in (None, False) else v)).strip()
    return norm(a) == norm(b)


class _Parked(Exception):
    """
    A stop the loop can't get past on its own: a refusal, a page it can't
    read or move, a pause that didn't end with Jack's Enter. In the
    terminal the job is parked as needs-you; in the GUI it waits in its
    tab with Continue (Jack, 2026-10-26). `after_fill` is the page's
    PageFill when the stop came after this page was filled, so a resume
    goes back to Next / Submit instead of filling the page again.
    """
    after_fill: "pipeline.PageFill | None" = None
    page_events: list | None = None

    def __init__(self, reason: str, *, cap: str | None = None, closed: bool = False):
        super().__init__(reason)
        self.cap = cap            # "time" / "pages": a Continue grants one more window
        self.closed = closed      # the tab is gone: the row offers Close, not Continue


class Wizard:
    def __init__(self, page: "Page", prof, *, url: str, portal: str, portal_method: str,
                 company: str, role: str, resume, posting_text: str, pauser, echo=print,
                 clock=time.monotonic, agent_submissions_today=None, reload_profile=None,
                 on_pause=None, intake_steps=None, auto_submit=None):
        self.page, self.prof, self.url = page, prof, url
        # The auto-submit switch, read through this callable at the final
        # page and never captured here (Jack, 2026-10-29). None is OFF.
        self.auto_submit = auto_submit
        self.jack_submits = False               # set at the first ready park: never auto-submit after it
        self._skipped_gate: tuple | None = None   # (fill, events) of a page whose gate the ready park took over
        self.portal, self.portal_method = portal, portal_method
        self.company, self.role, self.resume = company, role, resume
        self.posting_text, self.pauser, self.echo, self.clock = posting_text, pauser, echo, clock
        self.agent_submissions_today = agent_submissions_today
        self.reload_profile = reload_profile
        self.on_pause = on_pause                # GUI mode: told what the page should show
        self.guard = extract.ReadGuard()
        # The activity listener's agent flag: while the filler works, its
        # own (trusted, Playwright-sent) input is marked as the agent's, not
        # as Jack's, so a touch mark can only come from him.
        self.activity = ledger.ActivityGuard(quiet_ms=0, echo=lambda *a, **k: None)
        self.out = Outcome("needs_you", wizard=self)
        # What intake pressed on the posting ("clicked '<label>' on <host>"),
        # at the head of the job's own log (Jack, 2026-10-26).
        self.out.log.extend(str(s) for s in (intake_steps or []))
        # run_one printed them as intake returned: not again.
        self.out.printed = len(self.out.log)
        self.snapshots: dict = {}               # every page's read-back, by ref
        self.census: dict = {}                  # every page's checkboxes after filling, by ref
        self.prefill: dict = {}                 # earlier pages' pair boxes before filling, by group
        self.results: dict = {}
        self.human_reviewed: list[str] = []
        self.uploaded: dict | None = {}
        self.resume_done = False
        self.pending: dict | None = None        # where a deferred pause left off
        self._protect_held = False              # the next fill leaves boxes that hold a value alone
        self.resume_info: dict | None = None    # what Jack gave in the GUI for that pause
        self.active_s = 0.0                     # the agent's own time, pauses excluded
        self.step_cap = STEP_CAP                # raised by one window per Continue on a cap park
        self._tick: float | None = None
        # The last way-to-apply choice that left the page as it was: its
        # label and the (address, fields) shape it was made on. The same
        # choice is never made twice on a page of that shape (Jack,
        # 2026-10-29); a page that changed clears it.
        self._last_choice: tuple[str, tuple] | None = None

    # -- records ---------------------------------------------------------------
    def _record(self, steps: list[dict], events: list[dict], *, wizard: bool) -> dict:
        rec = pipeline.build_record(self.page, self.url, self.portal, self.portal_method,
                                    self.company, steps, events, dry_run=False,
                                    uploads=self.uploaded)
        if "fields" in rec:
            # Earlier pages were read back when they were filled; the page
            # now showing can only vouch for its own fields and boxes.
            rec["snapshot"] = {**self.snapshots, **(rec.get("snapshot") or {})}
            now = rec.get("census") or []
            showing = {b.get("ref") for b in now if isinstance(b, dict) and b.get("ref")}
            rec["census"] = [b for r, b in self.census.items() if r not in showing] + list(now)
            # The pre-fill states of earlier pages' button-pair boxes too, so
            # the gate's pair exemption can still find a pair's "before".
            pre = rec.get("prefill_census")
            pre_now = list(pre) if isinstance(pre, list) else []
            groups = {b.get("group_ref") for b in pre_now if isinstance(b, dict)}
            earlier = [b for g, boxes in self.prefill.items() if g not in groups for b in boxes]
            if earlier or isinstance(pre, list):
                rec["prefill_census"] = earlier + pre_now
            rec["human_reviewed"] = list(self.human_reviewed)
            rec["wizard"] = wizard
        return rec

    def _page_record(self, fill: pipeline.PageFill, events: list[dict]) -> dict:
        return self._record([fill.step], events, wizard=True)

    def _parked(self, reason: str) -> Outcome:
        # The terminal's park; a GUI park logs the same line in
        # park_for_continue (fold of f2cd52c's review).
        self.out.log.append(f"parked: {_clean(reason)}")
        self.out.status, self.out.reason = "needs_you", reason
        return self.out

    # -- the auto-submit switch ------------------------------------------------
    def _submit_allowed(self) -> bool:
        """
        May submit.py tick and press on this job, now? Only while the
        switch reads ON at this moment (a callable that is missing or
        raises reads OFF) and the job has never parked ready: a ready
        row never auto-submits, even if the switch is turned ON later
        (Jack, 2026-10-29).
        """
        if self.jack_submits or self.auto_submit is None:
            return False
        try:
            return self.auto_submit() is True
        except Exception:
            return False

    def _parks_ready(self) -> bool:
        """Is this a page the switch sends to the ready park: Submit control, no Next, switch OFF?"""
        if self._submit_allowed():
            return False
        if find_next(self.page, self.portal).get("chosen"):
            return False
        return bool(submit_mod.find_submit(self.page).get("chosen"))

    # -- pauses ----------------------------------------------------------------
    def _pause(self, kind: str, body: str, *, prompt: str, until=None, hidden: bool = False,
               wants_code: bool = False, fields: list[dict] | None = None,
               extra: dict | None = None) -> str:
        """
        One pause. In the terminal: notify, prompt, block; returns how it
        ended. In GUI mode: remember where this is (`pending`), tell the GUI
        what to show, and raise Deferred so the queue moves on; `continue_from`
        re-enters the loop at `pending` when Jack presses Continue.
        """
        if getattr(self.pauser, "deferring", False):
            self.pending = {"kind": kind, **(extra or {})}
            raise pause_mod.Deferred("job-agent needs you", body, prompt=prompt, hidden=hidden,
                                     wants_code=wants_code, until=until,
                                     info={"kind": kind, "message": body, "wants_code": wants_code,
                                           "fields": fields or []})
        _, how = self.pauser.wait("job-agent needs you", body, prompt=prompt, hidden=hidden,
                                  until=until, wants_code=wants_code)
        return how

    def _handled_by_jack(self, kind: str, detail: str | None) -> int:
        """
        Mark every unhandled event of this kind (and detail, if given) as
        handled by Jack: the pause it belongs to ended with his Continue.
        Returns how many were marked.
        """
        n = 0
        for ev in self.out.events:
            if (ev.get("kind") == kind and ev.get("handled") is None
                    and (detail is None or ev.get("detail") == detail)):
                ev["handled"] = "jack"
                n += 1
        return n

    def _captcha(self, fill: "pipeline.PageFill | None" = None,
                 page_events: list[dict] | None = None) -> None:
        found = browser_mod.detect_captcha(self.page)
        if not found:
            return
        ev = {"kind": "captcha", "detail": found, "handled": None}
        self.out.events.append(ev)
        # A fill in hand (a resumed pass) rides along, so solving the
        # CAPTCHA resumes at the gate for that fill, not at a second fill.
        extra = ({"fill": fill, "page_events": list(page_events or []),
                  "shape": follow.read_shape(self.page)} if fill is not None else None)
        self._pause("captcha", f"{found} on the page. Solve it in the browser.",
                    prompt="Press Enter when it's solved: ",
                    until=lambda: browser_mod.page_readable(self.page)
                    and not browser_mod.detect_captcha(self.page), extra=extra)
        if not browser_mod.page_readable(self.page) or browser_mod.detect_captcha(self.page):
            raise _Parked("a CAPTCHA wasn't solved (or the page couldn't be read)")
        ev["handled"] = "jack"

    def _await_form(self, here: str) -> bool:
        """
        Give the page up to FORM_WAIT_S to show a fillable control before
        the scan runs (Jack, 2026-10-26): polled, bounded, no click, no
        navigation. `here` is the address the page checks (CAPTCHA, Google,
        host and page kind) were made on. Returns True only when a fillable
        control is there at the first probe (the checks saw this page) or
        when the wait timed out on a page it could read, at `here`, with no
        fillable control (the scan runs and parks as before). It
        returns False, and the loop makes every check again from the top,
        the moment the address differs from `here` or as soon as the form
        appears after a poll: a page that drew a sign-in dialog or a
        CAPTCHA at the same address while the wait ran is a page the checks
        never saw (fixes of 403c058's and 07113ca's BLOCKs). The next pass
        finds the form at its first probe, so nothing is scanned on an
        unchecked page and nothing spins; FORM_RECHECK_CAP bounds a page
        that keeps changing.
        """
        start = time.monotonic()
        waited = 0
        while True:
            try:
                if self.page.url != here:
                    self.out.log.append("the page moved while the form was awaited: checked again")
                    return False
                found = bool(self.page.evaluate(_HAS_FIELDS_JS))
            except Exception:
                found = None            # unreadable (mid-navigation): never a scan on this probe
            if found:
                if waited:
                    self.out.log.append(f"the form rendered after {time.monotonic() - start:.1f}s: "
                                        f"checked again")
                    return False
                return True
            if time.monotonic() - start >= FORM_WAIT_S:
                if found is None:
                    # The last probe couldn't read the page: the scan must
                    # not run on whatever the tab lands on. Every check
                    # again (fold of 5a19d68's review).
                    self.out.log.append("the page couldn't be read as the form wait ended: checked again")
                    return False
                self.out.log.append(f"no form appeared within {FORM_WAIT_S} s")
                return True
            waited += 1
            try:
                self.page.wait_for_timeout(FORM_POLL_MS)
            except Exception:
                return False

    @staticmethod
    def _mark_refs(fields: list[dict]) -> dict[str, list[str]]:
        """Each non-file field's ref -> the refs a touch on the field can land on: its own and its options'."""
        return {f["ref"]: [f["ref"], *(r for r in (f.get("option_refs") or []) if r != f["ref"])]
                for f in fields if f.get("type") != "file"}

    # The baseline read drops the focus first: the agent's last fill or
    # click leaves its box or option focused, and the listener marks the
    # active element on Jack's first trusted event anywhere on the page,
    # which would read as his touch of the agent's field. Under the agent
    # flag, so a change event the blur fires is the agent's (fix of
    # 8fd4eb8's BLOCK).
    _BLUR_JS = "() => { const el = document.activeElement; if (el && el !== document.body) el.blur(); }"
    _FOCUS_FREE_JS = "() => { const el = document.activeElement; return !el || el === document.body; }"

    def _marked(self, fields: list[dict], *, baseline: bool = False) -> set[str] | None:
        """
        The fields (by ref) carrying a touch mark now: on the field's own
        element or on any option of a radio / button group (Jack's click
        on the second option marks that option, not the group's first,
        which is the group's ref; Jack, 2026-10-26). With `baseline`, the
        read a pause's "before" is taken from, the active element is
        blurred first (see _BLUR_JS). None if the page couldn't be read.
        """
        by_field = self._mark_refs(fields)
        if baseline:
            try:
                with self.activity.filling(self.page, quiet=True):
                    self.page.evaluate(self._BLUR_JS)
                    self.page.wait_for_timeout(150)
                    free = bool(self.page.evaluate(self._FOCUS_FREE_JS))
            except Exception:
                return None
            if not free:
                # The page put the focus back on a box (a validation, a
                # re-render, an autofocus): Jack's first click would mark
                # it as his. No baseline, so this pause counts nothing.
                self.out.log.append("the page keeps the focus on a box: nothing counts as reviewed at this pause")
                return None
        try:
            hit = set(self.page.evaluate(_TOUCHED_JS,
                                         [[r for refs in by_field.values() for r in refs],
                                          ledger.REVIEW_ATTR]) or [])
        except Exception:
            return None
        return {ref for ref, refs in by_field.items() if any(r in hit for r in refs)}

    def _stop_fields(self, fill: pipeline.PageFill | None, result: dict, *,
                     approvable: bool = True) -> list[dict]:
        """
        The GUI's list for a gate stop: each stopped field by label and
        reason, and a read-back value with an approve tick only where
        gui.approvable allows (never a legal, EEO, pay, consent, per-company,
        checkbox, password or unfilled field). With `approvable` False (a
        ready park, where Jack presses Submit himself and no gate judges
        an approval) every field is label and reason only.
        """
        if fill is None:
            return []
        out = []
        results = (getattr(fill, "step", None) or {}).get("results") or {}
        categories = getattr(fill, "categories", None) or {}
        plan = getattr(fill, "plan", None) or {}
        snapshot = getattr(fill, "snapshot", None) or {}
        for f in fill.fields:
            if f.get("type") == "file":
                continue
            label = f.get("label") or f.get("name") or "(unlabelled)"
            hits = [r for r in result.get("content_reasons", [])
                    if r.startswith(f"'{label[:80]}'")]
            if not hits:
                continue
            cat = categories.get(f["ref"], "unknown")
            raw = {"field": f, "category": cat, "decision": plan.get(f["ref"]),
                   "result": results.get(f["ref"])}
            ok = approvable and gui_mod.approvable(**raw)
            out.append({"ref": f["ref"], "label": label[:120],
                        "reason": "; ".join(_clean(h.split(": ", 1)[-1]) for h in hits)[:300],
                        "approvable": ok, "raw": raw if approvable else None,
                        "value": snapshot.get(f["ref"]) if ok else None})
        return out

    def _after_pause(self, fill: pipeline.PageFill, marked_before: set | None,
                     approved: dict) -> None:
        """
        Jack has acted on this page. A field counts as reviewed by him only
        if it carries a touch mark that is new since before the pause (a
        trusted input of his; the filler's own input is marked as the
        agent's, and only the field's own element or an option of its
        group counts), or if he ticked
        it in the GUI and the page still holds exactly the value he was
        shown. Every other field keeps the gate's normal checks: a pause
        proves he was present, not that he checked every field (Jack,
        2026-10-19). Continue alone approves nothing.
        """
        if self.reload_profile is not None:
            try:
                self.prof = self.reload_profile()      # Jack may have edited profile.yaml
            except Exception:
                pass
        try:
            now = extract.snapshot(self.page, fill.fields)
        except Exception:
            now = None                  # unreadable: nothing about values is known
        refs = [f["ref"] for f in fill.fields if f.get("type") != "file"]
        marked_after = self._marked(fill.fields)
        # A read that failed, before or after, can only shrink the waiver:
        # with no trustworthy "before", no mark counts as new.
        touched = (set() if marked_before is None or marked_after is None
                   else marked_after - marked_before)
        ticked: list[str] = []
        if now is not None:
            for ref, shown in (approved or {}).items():
                # "Still holds exactly the value he was shown" needs a read: a ref
                # the snapshot left out (stale, or a group it couldn't read whole)
                # is unknown, never "still empty".
                if ref in refs and ref in now and shown is not None and _same(now[ref], shown):
                    ticked.append(ref)
        for ref in refs:
            if (ref in touched or ref in ticked) and ref not in self.human_reviewed:
                self.human_reviewed.append(ref)
        if now is not None:
            fill.snapshot = now
            self.snapshots.update(now)
        try:
            boxes = extract.checkbox_census(self.page)
        except Exception:
            boxes = []
        if isinstance(boxes, list):
            self.census.update({b["ref"]: b for b in boxes if isinstance(b, dict) and b.get("ref")})
        self.out.events.append({"kind": "gate_pause",
                                "detail": f"{len(touched)} field(s) touched by Jack, "
                                          f"{len(ticked)} approved in the GUI",
                                "handled": "jack"})

    def _gate_page(self, fill: pipeline.PageFill, page_events: list[dict]) -> None:
        """
        Judge this page. On a stop, pause (in GUI mode: defer and come back
        here on resume); in the terminal, one pause, then the page is judged
        again, and a second stop parks the job.
        """
        result = gate.decide(self._page_record(fill, page_events), self.prof,
                             agent_submissions_today=self.agent_submissions_today)
        self._log_waived(result)
        if result["content_verdict"] == gate.WOULD_SUBMIT:
            result = self._tick_required_consent(fill, result, page_events)
            if result["content_verdict"] == gate.WOULD_SUBMIT:
                return
        # Every stop, first or on a resume, in the log with its reasons
        # (Jack, 2026-10-26: a re-stop after Continue logged nothing).
        self.out.log.append("gate stopped: " + "; ".join(_clean(r) for r in result["content_reasons"][:8]))
        self._pause_and_review(fill, result, page_events)
        # Terminal mode from here: GUI mode deferred above.
        result = gate.decide(self._page_record(fill, page_events), self.prof,
                             agent_submissions_today=self.agent_submissions_today)
        if result["content_verdict"] == gate.WOULD_SUBMIT:
            result = self._tick_required_consent(fill, result, page_events)
        if result["content_verdict"] != gate.WOULD_SUBMIT:
            self.out.gate_result = result
            raise _Parked("this page needs you: "
                          + "; ".join(_clean(r) for r in result["content_reasons"][:6]))

    def _tick_required_consent(self, fill: pipeline.PageFill, result: dict,
                               page_events: list[dict]) -> dict:
        """
        Jack's decision of 2026-10-23: a REQUIRED terms / privacy box on a
        page the wizard will press Next on may be ticked under the consent
        policy, through submit.py's own helper (which re-reads the label
        on the element and ticks only a visible native box the policy or
        the exact allowlist allows), after the page's gate said
        would_submit with that tick planned (would_tick: the gate plans it
        only for a required box); then the page is read back and judged
        again. The final page (Submit, no Next) is submit.py's: it ticks
        there itself, after the gate on the full record. Returns the
        verdict to act on; with nothing ticked, `result` unchanged.
        """
        actions = result.get("consent_actions") or {}
        if not any(a == "would_tick" for a in actions.values()):
            return result
        if not find_next(self.page, self.portal).get("chosen"):
            return result
        # Under the activity listener's agent flag, like the filler: the
        # helper's own click must never leave a touch mark that reads as
        # Jack's (a mark new since before a pause counts as his review).
        with self.activity.filling(self.page):
            ticked = submit_mod.tick_allowed(self.page, result, self.prof, self.out.log)
        if ticked:
            ev = {"kind": "consent_tick", "detail": f"{len(ticked)} required terms / privacy box(es)",
                  "handled": "agent"}
            self.out.events.append(ev)
            page_events.append(ev)
        try:
            fill.snapshot = extract.snapshot(self.page, fill.fields)
            self.snapshots.update(fill.snapshot)
        except Exception:
            pass                        # the record's own read-back decides
        try:
            boxes = extract.checkbox_census(self.page)
        except Exception:
            boxes = None
        if isinstance(boxes, list):
            fill.census = boxes
            self.census.update({b["ref"]: b for b in boxes if isinstance(b, dict) and b.get("ref")})
        record = self._page_record(fill, page_events)
        fresh = gate.decide(record, self.prof, agent_submissions_today=self.agent_submissions_today)
        # A planned tick that isn't on the page now stops the page here, in
        # the stop direction: the gate counts a would_tick box as filled,
        # so without this a required box the helper refused (hidden, not
        # one native checkbox, a click that raised) would be left unticked
        # behind a Next press and never stop the full-record gate either.
        # Only a box the fresh census reads as ticked counts as done.
        boxes_now = {b.get("ref"): b for b in record.get("census") or [] if isinstance(b, dict)}
        labels = {f.get("ref"): f.get("label") or f.get("name") or "(unlabelled)" for f in fill.fields}
        missed = [ref for ref, a in (fresh.get("consent_actions") or {}).items()
                  if a == "would_tick" and (boxes_now.get(ref) or {}).get("checked") is not True]
        if missed:
            self.out.log.append("planned consent tick not made: the box isn't read as ticked")
            fresh["content_verdict"] = gate.WOULD_STOP
            fresh["content_reasons"] = list(fresh.get("content_reasons") or []) + [
                f"'{str(labels.get(ref, '(unlabelled)'))[:80]}': required consent box planned but not ticked"
                for ref in missed]
        return fresh

    def _log_waived(self, result: dict) -> None:
        """The optional empties the gate listed instead of stopping on (version 8), once each."""
        for reason in result.get("waived") or []:
            line = f"optional, left empty (listed, not a stop): {_clean(reason)}"
            if line not in self.out.log:
                self.out.log.append(line)

    def _pause_and_review(self, fill: pipeline.PageFill, result: dict,
                          page_events: list[dict] | None = None) -> None:
        """
        The gate stopped on `fill`'s page with `result`: pause, and (in the
        terminal) count what Jack touched once he presses Enter; any other
        ending parks. In GUI mode the pause is deferred and the review
        happens on resume.
        """
        reasons = "; ".join(_clean(r) for r in result["content_reasons"][:8])
        marked_before = self._marked(fill.fields, baseline=True)
        how = self._pause("gate", f"This page needs you: {reasons}. Answer in the browser, "
                                  f"then press Enter.",
                          prompt="Press Enter when you've answered: ",
                          fields=self._stop_fields(fill, result),
                          extra={"fill": fill, "page_events": page_events or [],
                                 "marked_before": marked_before})
        if how != pause_mod.ANSWERED:
            self.out.events.append({"kind": "gate_pause", "detail": how, "handled": None})
            self.out.gate_result = result
            raise _Parked("this page needs you: " + reasons[:300])
        self._after_pause(fill, marked_before, {})

    def _moved(self, before) -> bool:
        """Has the page changed since `before` (address or field structure)?"""
        browser_mod.settle(self.page, 600)
        now = follow.read_shape(self.page)
        if now is None or before is None:
            return now is not before
        return (now["url"], now["fields"]) != (before["url"], before["fields"])

    # -- the loop --------------------------------------------------------------
    def run(self) -> Outcome:
        """Run until submitted, parked, or (GUI mode) waiting for Jack."""
        self._tick = self.clock()
        try:
            return self._loop()
        except _Parked as e:
            if getattr(self.pauser, "deferring", False):
                return self.park_for_continue(str(e), after_fill=e.after_fill,
                                              page_events=e.page_events, cap=e.cap, closed=e.closed)
            self.out.wizard = None
            return self._parked(str(e))
        except pause_mod.Deferred as e:
            # From _pause (pending already set) or from accounts.py (not).
            if self.pending is None:
                self.pending = {"kind": "account", "wants_code": e.wants_code}
            info = e.info or {"kind": self.pending["kind"], "message": e.body,
                              "wants_code": e.wants_code, "fields": []}
            return self._wait(info, e.body)

    def _wait(self, info: dict, reason: str) -> Outcome:
        """Hand a deferred pause to the GUI and return "waiting"; `pending` says where to resume."""
        self.out.status, self.out.reason = WAITING, reason
        self.out.pause_kind = str(info.get("kind") or "")
        self.out.events.append({"kind": "pause", "detail": info["kind"], "handled": None})
        if self.on_pause is not None:
            try:
                self.on_pause(info)
            except Exception:
                pass
        self.active_s += self.clock() - (self._tick or self.clock())
        return self.out

    def park_for_continue(self, reason: str, *, after_fill=None, page_events=None,
                          cap: str | None = None, closed: bool = False) -> Outcome:
        """
        GUI mode (Jack, 2026-10-26): a park is a pause like any other. The
        row shows the reason with Continue; the tab stays open; nothing is
        clicked or navigated. Continue re-enters the loop at the top of
        the page (a fresh scan of what the tab shows now), or, when the
        page had been filled before the stop and still has the shape it
        had at the park, at the gate and then Next / Submit for that same
        fill (a page that changed since is scanned afresh). Also used by
        intake for a link it parked before the wizard started, on the tab
        it left.
        """
        if self._tick is None:
            self._tick = self.clock()
        self.out.log.append(f"parked: {_clean(reason)}")
        if closed:
            # The tab is gone: nothing to resume. The row offers Close, not
            # Continue (Jack, 2026-10-26).
            self.pending = {"kind": "closed", "fill": None, "reason": reason, "page_events": [], "shape": None}
            return self._wait({"kind": "closed", "wants_code": False, "fields": [],
                               "message": f"{reason}. Its tab is gone; close this job."}, reason)
        shape = follow.read_shape(self.page) if after_fill is not None else None
        self.pending = {"kind": "parked", "fill": after_fill, "reason": reason,
                        "page_events": list(page_events or []), "shape": shape, "cap": cap}
        more = (" Continue grants one more window: another 60 minutes of the agent's own time."
                if cap == "time" else
                f" Continue grants one more window: another {STEP_CAP} pages." if cap == "pages" else
                " Fix it in the tab yourself, then press Continue: the page is read again from where "
                "it stopped (no click, no navigation).")
        info = {"kind": "parked", "wants_code": False, "fields": [], "message": f"{reason}.{more}"}
        return self._wait(info, reason)

    def continue_from(self, info: dict | None) -> Outcome:
        """Jack pressed Continue: carry on from the pending pause with what he gave."""
        self.resume_info = dict(info or {})
        if self.out.events and self.out.events[-1].get("kind") == "pause":
            self.out.events[-1]["handled"] = "jack"
        return self.run()

    # -- resuming after other jobs ran -------------------------------------------
    # A matched field keeps its old-scan judgement (secret, raw stop / legal
    # hits, readability, required), so the new scan must agree on each or the
    # pair is refused and the field stays unreadable.
    _REMAP_FLAGS = ("secret", "raw_stop", "raw_legal", "stable_identity", "required")

    @staticmethod
    def _ident(f: dict) -> tuple:
        return ((f.get("type") or "", f.get("tag") or "", f.get("dom_name") or f.get("name") or "",
                 f.get("dom_id") or "", f.get("explicit_label") or "", f.get("label") or "",
                 tuple(f.get("options") or [])))

    def _remap(self, fill: pipeline.PageFill | None, pending: dict, info: dict) -> None:
        """
        Every scan in this process makes earlier refs stale (they then read
        as missing, never wrongly), so after another job ran, this page's
        refs are stale. Re-scan the page (a scan that fails leaves every
        field unpaired) and carry the plan, results, flags,
        read-backs, marks and approvals over to the new refs, by field
        identity (type, tag, name, id, labels, options), one-to-one matches
        only: exactly one old field and exactly one new field with that
        identity, agreeing on every _REMAP_FLAGS flag. A field with no such
        match keeps its stale ref, and its fill-time read-back, result and
        review are dropped: its box is unknown now, so the gate stops on it
        ("filling it failed" / "required, but empty") rather than judging
        it by a value read before, or waiving a review it never had.
        """
        if fill is None or not fill.fields or extract.is_current(fill.fields[0]["ref"]):
            return
        try:
            # The rescan only evaluates the scan script (it stamps refs and
            # reads structure; it opens nothing and sends no input event, so
            # the listener, which marks only trusted input events, sees
            # nothing). Under the agent flag all the same, so a later change
            # to what a rescan does can never leave a mark that reads as
            # Jack's after the pause's baseline (Jack, 2026-10-26).
            with self.activity.filling(self.page):
                new_fields = extract.extract_fields(self.page, self.guard)
        except Exception:
            new_fields = []            # unreadable: every field is unpaired below
        by_key: dict = {}
        for nf in new_fields:
            by_key.setdefault(self._ident(nf), []).append(nf)
        old_count = Counter(self._ident(o) for o in fill.fields)
        pairs: list[tuple[dict, dict]] = []
        mapping: dict[str, str] = {}
        for old in fill.fields:
            key = self._ident(old)
            cands = by_key.get(key, [])
            if len(cands) != 1 or old_count[key] != 1:
                continue
            nf = cands[0]
            if any(old.get(k) != nf.get(k) for k in self._REMAP_FLAGS):
                continue
            pairs.append((old, nf))
            mapping[old["ref"]] = nf["ref"]
            for o, n in zip(old.get("option_refs") or [], nf.get("option_refs") or []):
                mapping[o] = n
        unpaired = {r for f in fill.fields if f not in [o for o, _ in pairs]
                    for r in [f["ref"], *(f.get("option_refs") or [])]}
        m = lambda r: mapping.get(r, r)
        remap_keys = lambda d: {m(k): v for k, v in (d or {}).items() if k not in unpaired}
        remap_box = lambda b: {**b, "ref": m(b.get("ref")) if b.get("ref") else b.get("ref"),
                               "group_ref": m(b.get("group_ref")) if b.get("group_ref") else b.get("group_ref")}
        for old, nf in pairs:
            old["ref"] = nf["ref"]
            if old.get("option_refs"):
                old["option_refs"] = list(nf.get("option_refs") or [])
            old["sigs"] = dict(nf.get("sigs") or {})
            old["credential_step"] = nf.get("credential_step")
        fill.plan = {m(k): v for k, v in (fill.plan or {}).items()}
        fill.categories = {m(k): v for k, v in (fill.categories or {}).items()}
        fill.snapshot = remap_keys(fill.snapshot)
        for fl in fill.flags:
            fl["ref"] = m(fl.get("ref"))
        fill.census = [remap_box(b) for b in fill.census if isinstance(b, dict)]
        step = fill.step
        for sf in step.get("fields") or []:
            sf["ref"] = m(sf.get("ref"))
            if sf.get("option_refs"):
                sf["option_refs"] = [m(r) for r in sf["option_refs"]]
            if sf.get("sigs"):
                sf["sigs"] = {m(k): v for k, v in sf["sigs"].items()}
        step["plan"] = {m(k): v for k, v in (step.get("plan") or {}).items()}
        step["categories"] = {m(k): v for k, v in (step.get("categories") or {}).items()}
        if step.get("results") is not None:
            step["results"] = remap_keys(step.get("results"))
            for r in unpaired:
                step["results"][r] = {"filled": False}     # unknown box: never "filled"
        for fl in step.get("flags") or []:
            fl["ref"] = m(fl.get("ref"))
        if isinstance(step.get("prefill_census"), list):
            step["prefill_census"] = [remap_box(b) for b in step["prefill_census"] if isinstance(b, dict)]
        self.snapshots = remap_keys(self.snapshots)
        self.results = remap_keys(self.results)
        for r in unpaired:
            self.results[r] = {"filled": False}
        self.census = {m(k): remap_box(b) for k, b in self.census.items() if k not in unpaired}
        self.prefill = {m(g): [remap_box(b) for b in boxes] for g, boxes in self.prefill.items()}
        self.human_reviewed = [m(r) for r in self.human_reviewed if r not in unpaired]
        if isinstance(pending.get("marked_before"), set):
            # Kept whole: a stale ref left in "before" can only shrink what
            # counts as touched since (touched = after - before).
            pending["marked_before"] = {m(r) for r in pending["marked_before"]}
        if isinstance(info.get("approved"), dict):
            info["approved"] = remap_keys(info["approved"])
        self.out.log.append(f"re-scanned after other jobs ran: {len(pairs)} of {len(fill.fields)} "
                            f"fields matched")

    def _resume_fill(self, pending: dict, info: dict) -> tuple:
        """
        Where a pause that held a fill resumes. The page was filled before
        the stop: if it is still that page (same shape as at the pause),
        it is judged again (Jack may have changed a box), then the resume
        goes back to Next / Submit for that fill, never a second fill of
        it, its events riding along for a later park. If Jack moved the
        page on, or its shape couldn't be read then or now, the fill is
        not vouched for: the page is scanned afresh, and that scan leaves
        every box that holds a value alone (protect_held: never typed
        over, never sent to a model, never counted as his; the gate
        judges it as any other box). Returns (fill or None, skip_fill,
        carried events).
        """
        fill = pending.get("fill")
        if fill is None:
            return None, False, []
        if pending.get("shape") is None or self._moved(pending["shape"]):
            self.out.log.append("the page changed since the park: scanned afresh")
            self._protect_held = True
            return None, False, []
        # The same page, judged again: no fresh scan follows, so a hold
        # ordered earlier (a CAPTCHA pass) must not reach the next page.
        self._protect_held = False
        self._remap(fill, pending, info)
        carried = list(pending.get("page_events") or [])
        self._gate_or_ready(fill, carried)
        return fill, True, carried

    def _gate_or_ready(self, fill: pipeline.PageFill, page_events: list[dict]) -> None:
        """
        Judge this page, unless the switch sends it to the ready park: on
        a page with a Submit control and no Next, with auto-submit OFF,
        the per-page gate pause is skipped so Jack meets one stop per job
        (the ready park carries the full-record verdict and its reasons;
        Jack, 2026-10-29). Nothing is ticked on that page either way
        (_tick_required_consent ticks only on a page with a Next).
        """
        if self._parks_ready():
            self.out.log.append("auto-submit is off: the final page's verdict goes to the ready park")
            # Remembered so that, should _advance find a Next after all (a
            # page that redrew its controls), the page is judged before any
            # press: no page is ever left by Next without its gate.
            self._skipped_gate = (fill, page_events)
            return
        self._skipped_gate = None
        self._gate_page(fill, page_events)

    def _over_time(self) -> bool:
        return self.active_s + (self.clock() - (self._tick or self.clock())) > TIME_CAP_S

    def _loop(self) -> Outcome:
        page = self.page
        pending, self.pending = self.pending, None
        info, self.resume_info = self.resume_info or {}, None
        fill: pipeline.PageFill | None = None
        skip_fill = False
        carried_events: list[dict] = []   # a resumed fill's own events
        rechecks = 0            # passes in a row that ended in the form wait
        if pending:
            if page.is_closed():
                raise _Parked("the tab closed", closed=True)
            # A resumed page is refused by its address before any pause's
            # own resume runs (a job board, Google's site, another host):
            # nothing is gated, ticked or pressed on a page that left the
            # application's site while Jack held it.
            try:
                came_back_at = page.url
            except Exception:
                raise _Parked("the page couldn't be read")
            if intake.job_board(came_back_at):
                raise _Parked(f"{intake.job_board(came_back_at)}: the application stays on the job board")
            if google.is_google_host(came_back_at):
                raise _Parked("Google's site: the page left the application's site")
            verdict = pages.fill_allowed(page, self.url, None)
            if verdict in (pages.LINKEDIN, pages.OFF_HOST):
                raise _Parked(f"{verdict.replace('_', ' ')}: the page left the application's site")
            kind = pending.get("kind")
            self.out.log.append(f"resumed after a {kind} pause")
            if kind == "account" and pending.get("wants_code") and info.get("code"):
                accounts.enter_code(page, info["code"], self.out.log)
                browser_mod.settle(page, 800)
            elif kind == "moved":
                if not self._moved(pending["before"]):
                    self._pause("moved", pending["body"], prompt="Press Enter when it has moved on: ",
                                until=lambda: self._moved(pending["before"]),
                                extra={"before": pending["before"], "body": pending["body"]})
                # Jack moved the page on himself and may have typed on the
                # page it shows now: the next fill leaves every box that
                # holds a value alone, as after a park (Jack, 2026-10-26).
                self.out.log.append("the page moved on in Jack's hands: boxes holding a value are held")
                self._protect_held = True
            elif kind in ("gate", "final"):
                fill = pending.get("fill")
                self._remap(fill, pending, info)
                if fill is not None:
                    self._after_pause(fill, pending.get("marked_before"), info.get("approved") or {})
                if kind == "gate":
                    self._gate_page(fill, pending.get("page_events") or [])
                else:
                    # Continue ends the final pause the way Enter does in the
                    # terminal: the stop's event becomes Jack's, and the gate
                    # judges the page again before anything is pressed.
                    self._handled_by_jack("gate_pause", "final page")
                skip_fill = True
            elif kind == "parked":
                # A cap park resumed: Jack's Continue grants one more window
                # (Jack, 2026-10-26); the cap itself is unchanged.
                if pending.get("cap") == "time":
                    self.active_s = 0.0
                    self.out.log.append("one more 60-minute window granted")
                elif pending.get("cap") == "pages":
                    self.step_cap += STEP_CAP
                    self.out.log.append(f"one more window of {STEP_CAP} pages granted")
                fill, skip_fill, carried_events = self._resume_fill(pending, info)
            elif kind == "closed":
                raise _Parked("the tab closed", closed=True)
            elif kind == "ready":
                done = self._after_ready(pending)
                if done is not None:
                    return done
                # The tab shows another page: the loop goes on there with
                # boxes holding a value held, as after a "moved" pause.
            elif kind == "captcha":
                # Only once the page can be read and shows no CAPTCHA;
                # otherwise the top of the loop pauses again and the event
                # stays unhandled (an unreadable page reads as "none" to
                # detect_captcha, so readability is checked first).
                if browser_mod.page_readable(page) and not browser_mod.detect_captcha(page):
                    self._handled_by_jack("captcha", None)
                    if pending.get("fill") is not None:
                        fill, skip_fill, carried_events = self._resume_fill(pending, info)
                elif pending.get("fill") is not None:
                    # Still a CAPTCHA: pause again with the fill in hand, so
                    # solving it resumes at the gate for that fill. Unreadable
                    # (no CAPTCHA seen): whatever fill follows leaves boxes
                    # that hold a value alone.
                    self._protect_held = True
                    if browser_mod.detect_captcha(page):
                        self._captcha(pending.get("fill"), pending.get("page_events") or [])
        while True:
            page_events: list[dict] = carried_events if skip_fill else []
            carried_events = []
            if self._over_time():
                over = _Parked("over the 60-minute cap of the agent's own time for one application",
                               cap="time")
                if skip_fill:
                    # A resumed pass with this page's fill in hand: the
                    # resume goes back to its gate and Next, not a refill.
                    over.after_fill, over.page_events = fill, page_events
                raise over
            # The page cap stops before a new page is filled. A resumed pass
            # on a page already filled goes on to its Next, as the pass that
            # filled it would have (the cap is checked before each fill).
            if not skip_fill and self.out.pages_filled >= self.step_cap:
                raise _Parked(f"over the {self.step_cap}-page cap", cap="pages")
            if page.is_closed():
                raise _Parked("the tab closed", closed=True)
            try:
                here = page.url         # the address every check below is made on
            except Exception:
                raise _Parked("the page couldn't be read")
            # On every pass, a resumed one included: a CAPTCHA drawn since
            # pauses (the fill in hand rides along), a job board's page is
            # never gone on with, and a page that left the application's
            # site (another host, LinkedIn, Google's) is never advanced.
            self._captcha(fill if skip_fill else None, page_events)
            board = intake.job_board(here)
            if board:
                raise _Parked(f"{board}: the application stays on the job board")
            on_google = google.is_google_host(here)
            if on_google and skip_fill:
                raise _Parked("Google's site: the page left the application's site")
            verdict = None if on_google else pages.fill_allowed(page, self.url, None)
            if verdict in (pages.LINKEDIN, pages.OFF_HOST):
                raise _Parked(f"{verdict.replace('_', ' ')}: the page left the application's site")
            if not skip_fill:
                if on_google:
                    # A same-tab Google sign-in: walked by structure, or a
                    # pause; never filled, never typed into. Checked by
                    # address before anything else: a link in Google's own
                    # host family must not make this an ordinary page, and
                    # an address the driving side won't take (http, a
                    # trailing dot) is parked, not filled.
                    if not google.is_google(page.url):
                        raise _Parked("Google's site on an address the agent won't drive")
                    res = accounts.on_google(page, self.prof, self.pauser)
                    self.out.events += res.events
                    self.out.log += res.steps
                    if res.status == accounts.PARKED:
                        raise _Parked(res.reason)
                    browser_mod.settle(page, 800)
                    continue
                if verdict in (pages.ACCOUNT, pages.SIGN_IN, pages.VERIFICATION):
                    res = accounts.handle(page, verdict, self.prof, self.pauser)
                    self.out.events += res.events
                    self.out.log += res.steps
                    if res.status == accounts.PARKED:
                        raise _Parked(res.reason)
                    browser_mod.settle(page, 800)
                    continue
                if not self._await_form(here):
                    rechecks += 1
                    if rechecks >= FORM_RECHECK_CAP:
                        raise _Parked("the page kept changing while the form was awaited")
                    continue            # the page moved or drew its form: every check above, again
                rechecks = 0
                # The one fill pass after a fresh scan was ordered leaves
                # boxes that hold a value alone; the order is consumed by
                # a fill, never by a pass that fills nothing (a form wait,
                # an account page), and never carries to a later page.
                protect, self._protect_held = self._protect_held, False
                with self.activity.filling(page):
                    try:
                        fill = pipeline.fill_page(page, self.prof, guard=self.guard,
                                                  resume=None if self.resume_done else self.resume,
                                                  posting_text=self.posting_text, portal=self.portal,
                                                  protect_held=protect)
                    except pipeline.Unreadable as e:
                        raise _Parked(f"the page couldn't be read ({e})")
                if fill is not None:
                    self.out.pages_filled += 1
                    if fill.uploaded:
                        self.resume_done, self.uploaded = True, fill.uploaded
                    elif fill.uploaded is None:
                        self.resume_done, self.uploaded = True, None
                    self.out.steps.append(fill.step)
                    self.out.rows += fill.rows
                    self.out.flags += fill.flags
                    self.out.log += fill.lines
                    page_events = list(fill.events)
                    self.out.events += fill.events
                    self.snapshots.update(fill.snapshot)
                    self.census.update({b["ref"]: b for b in fill.census
                                        if isinstance(b, dict) and b.get("ref")})
                    pre = fill.step.get("prefill_census")
                    if isinstance(pre, list):
                        # Every box of a group, so a second box sharing the
                        # group_ref still counts against the pair exemption.
                        for b in pre:
                            if isinstance(b, dict) and b.get("group_ref"):
                                self.prefill.setdefault(b["group_ref"], []).append(b)
                    self.results.update(fill.step.get("results") or {})
                    self._gate_or_ready(fill, page_events)
            skip_fill = False
            try:
                outcome = self._advance(fill)
            except _Parked as e:
                e.after_fill = fill         # a resume goes back to the gate and Next / Submit, not a refill
                e.page_events = page_events
                raise
            if outcome is not None:
                return outcome

    def _advance(self, fill: pipeline.PageFill | None) -> Outcome | None:
        """Press Next (None: keep looping) or, on the final page, submit (an Outcome)."""
        page = self.page
        nxt = find_next(page, self.portal)
        sub = submit_mod.find_submit(page)
        if nxt.get("chosen"):
            skipped, self._skipped_gate = self._skipped_gate, None
            if skipped is not None and skipped[0] is fill:
                self._gate_page(*skipped)
            if nxt.get("disabled"):
                raise _Parked(f"the '{nxt.get('word')}' control is disabled")
            before = follow.read_shape(page)
            try:
                # Under the agent flag: a press is the agent's input, and
                # must leave no touch mark (the listener marks the active
                # element too, which is the last box the filler typed into).
                with self.activity.filling(page):
                    page.locator(f"[{NEXT_ATTR}='1']").first.click(timeout=5000)
            except Exception as e:
                raise _Parked(f"couldn't press '{nxt.get('word')}' ({type(e).__name__})")
            self.out.log.append(f"pressed '{nxt.get('word')}'")
            self.out.events.append({"kind": "next", "detail": nxt.get("word"), "handled": "agent"})
            if not self._moved(before):
                body = (f"The page didn't move on after '{nxt.get('word')}'. Fix what it's "
                        f"asking for in the browser, then press Enter.")
                how = self._pause("moved", body, prompt="Press Enter when it has moved on: ",
                                  until=lambda: self._moved(before),
                                  extra={"before": before, "body": body})
                if not self._moved(before):
                    raise _Parked(f"the page didn't move on after '{nxt.get('word')}' ({how})")
                # The same hold in the terminal: the page moved on while it
                # was in Jack's hands.
                self.out.log.append("the page moved on in Jack's hands: boxes holding a value are held")
                self._protect_held = True
            return None
        if sub.get("chosen"):
            if fill is None and self.out.pages_filled == 0:
                raise _Parked("a Submit control with nothing filled; check the page")
            if not self._submit_allowed():
                return self._ready(fill, READY_FILLED)
            return self._submit(fill)
        if fill is None:
            # A "choose how to apply" page: one allowed control, or parked
            # with the options shown (never LinkedIn, Indeed, paste or a
            # previous application). A choice that leaves the page as it
            # was (address and fields) parks, and is never made again on a
            # page of that shape: Continue rescans, and only a page Jack
            # changed gets a choice (Jack, 2026-10-29; his Avature run
            # looped on the same upload every pass until the time cap).
            before = follow.read_shape(page)
            key = (before["url"], before["fields"]) if before is not None else None
            if self._last_choice is not None and key is not None and key == self._last_choice[1]:
                raise _Parked(f"chose '{self._last_choice[0]}' earlier and the page is unchanged since; "
                              f"choose a way to apply yourself in the tab")
            try:
                with self.activity.filling(page):
                    choice = chooser.choose(page, self.resume, self.out.log)
            except ValueError as e:
                raise _Parked(str(e))
            if choice is not None:
                # The record's uploads map is keyed by file-box ref; a resume
                # the chooser put in is logged as a step and an event, not
                # claimed as a box's upload. A later resume box still gets it.
                self.out.events.append({"kind": "apply_method", "detail": choice.kind, "handled": "agent"})
                if not self._moved(before):
                    if key is not None:
                        self._last_choice = (choice.label, key)
                    raise _Parked(f"chose '{choice.label}' but the page didn't move on")
                self._last_choice = None
                return None
            raise _Parked("a page with no fields, no Next and no Submit")
        if not self._submit_allowed():
            return self._ready(fill, READY_NO_CONTROLS)
        raise _Parked("no Next or Submit control found after filling the page")

    # -- the ready park (auto-submit OFF) ----------------------------------------
    def _ready(self, fill: pipeline.PageFill | None, why: str) -> Outcome | None:
        """
        Auto-submit is OFF on a page with a Submit control and no Next (or
        with neither after a fill): the gate decides on the full record,
        nothing is ticked or pressed, and the job parks as "ready" with
        the verdict and its reasons for Jack to press Submit himself
        (Jack, 2026-10-29). From here the job never auto-submits. In the
        GUI the park waits with Continue; in the terminal one Enter ends
        it. Returns the Outcome, or None when the tab moved on to another
        page in Jack's hands (the loop goes on there).
        """
        self.jack_submits = True
        record = self._record(self.out.steps, self.out.events, wizard=True)
        result = gate.decide(record, self.prof, agent_submissions_today=self.agent_submissions_today)
        self._log_waived(result)
        self.out.record, self.out.gate_result, self.out.gate_run_id = record, result, None
        self.out.evidence = {}
        if result["content_verdict"] == gate.WOULD_SUBMIT:
            planned = sum(1 for a in (result.get("consent_actions") or {}).values() if a == "would_tick")
            verdict = "the gate would submit" + (
                f" (it planned to tick {planned} consent box(es); tick them yourself)" if planned else "")
        else:
            verdict = "the gate would stop: " + "; ".join(_clean(r) for r in result["content_reasons"][:8])
        self.out.log.append(f"ready: {_clean(why)}; {verdict[:400]}")
        ev = {"kind": "ready", "detail": why, "handled": None}
        self.out.events.append(ev)
        # Field refs of the whole record, for the read-only confirmation check.
        refs = [r for f in record.get("fields") or [] for r in (f.get("option_refs") or [f.get("ref")]) if r]
        pending = {"fill": fill, "why": why, "verdict": verdict, "refs": refs,
                   "shape": follow.read_shape(self.page)}
        body = (f"{why}. {verdict}. When you have pressed Submit, or moved the page on yourself, "
                f"press Continue: the page is read again (no click, no navigation).")
        how = self._pause("ready", body, prompt="Press Enter once you've submitted it (or to leave it): ",
                          until=lambda: self._moved(pending["shape"]),
                          fields=self._stop_fields(fill, result, approvable=False), extra=pending)
        # Terminal mode from here: GUI mode deferred above, and resumes in _after_ready.
        done = self._after_ready(pending, by_jack=how in (pause_mod.ANSWERED, pause_mod.FINISHED))
        if done is not None:
            return done
        # The page moved on in the terminal: the loop goes on in _loop's
        # while, so this pass's _advance returns None.
        return None

    def _form_gone_since(self, before) -> bool:
        """Is the form of the shape `before` gone: the page's structure now shares no field with it? Unreadable either way: not gone."""
        if before is None:
            return False
        now = follow.read_shape(self.page)
        if now is None or not before.get("fields"):
            return False
        return not (set(now["fields"]) & set(before["fields"]))

    def _after_ready(self, pending: dict, *, by_jack: bool = True) -> Outcome | None:
        """
        Jack has acted on a ready park (`by_jack`: Continue in the GUI,
        Enter or the page moving on in the terminal; a pause that ended
        any other way leaves the ready event unhandled, which the gate
        stops on). A confirmation on the page (read-only: the portal's
        signature or the generic sentence with the form gone) marks the
        job submitted by him; a tab showing another page lets the loop
        go on there with boxes holding a value held (None); otherwise it
        stays ready (GUI: waits again with the same verdict; terminal:
        parks as needs-you).
        """
        if by_jack:
            self._handled_by_jack("ready", None)
        # The park's refs go stale the moment another job's scan runs (the
        # normal GUI case), and a stale list must never read as "the form
        # is gone" (fix of cacfd8a's BLOCK). The form counts as gone only
        # when the page's structure, read now, shares no field with the
        # structure read at the park; a page that couldn't be read then
        # or now is never gone. A confirmation needs that, so a
        # confirmation sentence in the prose of the still-open form never
        # marks the job submitted.
        confirmed, evidence = submit_mod.confirmation(
            self.page, portal=self.portal, portal_method=self.portal_method,
            start_url=self.url, refs=[r for r in pending.get("refs") or [] if extract.is_current(r)],
            form_gone=self._form_gone_since(pending.get("shape")))
        if confirmed:
            self.out.evidence = evidence
            self.out.events.append({"kind": "confirmation", "detail": "seen after a ready park", "handled": "jack"})
            self.out.log.append(f"{READY_SUBMITTED} (not his answer; correct it with apply.py status)")
            self.out.status, self.out.reason, self.out.submitted_by = "submitted", READY_SUBMITTED, "jack"
            self.out.wizard = None
            return self.out
        if pending.get("shape") is not None and self._moved(pending["shape"]):
            self.out.log.append("the page moved on in Jack's hands after the ready park: "
                                "boxes holding a value are held")
            self._protect_held = True
            return None
        why, verdict = pending.get("why") or READY_FILLED, pending.get("verdict") or ""
        if getattr(self.pauser, "deferring", False):
            self.out.log.append("still ready: no confirmation seen and the page is unchanged")
            body = (f"{why}. {verdict}. When you have pressed Submit, or moved the page on yourself, "
                    f"press Continue: the page is read again (no click, no navigation).")
            self._pause("ready", body, prompt="", fields=self._stop_fields(pending.get("fill"), self.out.gate_result or {},
                                                                             approvable=False), extra=pending)
        self.out.wizard = None
        return self._parked(f"ready: {why}; {verdict}")

    def _submit(self, fill: pipeline.PageFill | None) -> Outcome:
        record = self._record(self.out.steps, self.out.events, wizard=True)
        # Under the agent flag: the consent tick and the Submit press are
        # the agent's input and leave no touch mark (Jack, 2026-10-26).
        with self.activity.filling(self.page):
            submission = submit_mod.submit_application(
                self.page, record, self.prof,
                build_record=lambda: self._record(self.out.steps, self.out.events, wizard=True),
                steps_log=self.out.log, agent_submissions_today=self.agent_submissions_today,
                auto_submit=self._submit_allowed)
        self.out.record, self.out.gate_result = record, submission.gate_result
        self.out.evidence = submission.evidence
        if submission.status == "needs_you" and not submission.clicked:
            # The gate stopped on the whole application: Jack's turn.
            self.out.log.append(f"gate stopped on the final page: {_clean(submission.reason)}")
            self.out.events.append({"kind": "gate_pause", "detail": "final page", "handled": None})
            marked_before = self._marked(fill.fields, baseline=True) if fill else None
            how = self._pause("final", f"Before submitting: {_clean(submission.reason)}. Fix it "
                                       f"in the browser, then press Enter.",
                              prompt="Press Enter when it's ready to submit: ",
                              fields=self._stop_fields(fill, submission.gate_result or {}),
                              extra={"fill": fill, "marked_before": marked_before})
            self.out.events[-1]["handled"] = "jack" if how == pause_mod.ANSWERED else None
            if how == pause_mod.ANSWERED and fill is not None:
                self._after_pause(fill, marked_before, {})
            record = self._record(self.out.steps, self.out.events, wizard=True)
            with self.activity.filling(self.page):
                submission = submit_mod.submit_application(
                    self.page, record, self.prof,
                    build_record=lambda: self._record(self.out.steps, self.out.events, wizard=True),
                    steps_log=self.out.log, agent_submissions_today=self.agent_submissions_today,
                    auto_submit=self._submit_allowed)
            self.out.record, self.out.gate_result = record, submission.gate_result
            self.out.evidence = submission.evidence
        self.out.status, self.out.reason = submission.status, submission.reason
        if submission.status == "submitted":
            self.out.submitted_by = "agent"
        self.out.wizard = None
        return self.out


def run_application(page: "Page", prof, **kw) -> Outcome:
    return Wizard(page, prof, **kw).run()
