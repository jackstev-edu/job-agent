"""
wizard.py — an application, page by page, until it is submitted or parked.

WHAT THIS DOES: Runs the loop the owner decisions allow: on each page, a
CAPTCHA pauses for Jack; an account, sign-in or verification page goes to
accounts.py; any other page on the application's own site is filled by the
shared pipeline, read back and judged by the gate; a stop pauses for Jack
and is judged again once he has answered in the browser (an answer counts
only with a trusted input event on that field); then Next is pressed and
the page must change; a page with Submit and no Next is the final page,
which submit.py handles. Caps: 25 pages, 60 minutes.
WHY IT MATTERS: This is the one place Next is pressed and the order of
every check is decided. Nothing here submits; submit.py does, after the
gate.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field as dataclass_field
from typing import TYPE_CHECKING

from . import (accounts, browser as browser_mod, extract, follow, gate, ledger, pages,
               pause as pause_mod, pipeline, submit as submit_mod)

if TYPE_CHECKING:
    from playwright.sync_api import Page

STEP_CAP = 25
TIME_CAP_S = 60 * 60

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

# Which of these refs carry a trusted-input mark from the activity listener
# on their own element (TOUCH_ATTR, set only for isTrusted events while the
# agent isn't filling). The mark on a wrapper doesn't count here: in follow
# mode over-marking keeps a field from the model, but here a mark waives a
# stop, so only the field's own element says anything. Refs only.
_TOUCHED_JS = r"""
([refs, attr]) => refs.filter(r => {
  const el = document.querySelector(`[data-ja-ref="${CSS.escape(r)}"]`);
  return !!el && el.hasAttribute(attr);
})
"""


@dataclass
class Outcome:
    status: str                              # "submitted" | "needs_you" | "failed"
    reason: str = ""
    steps: list[dict] = dataclass_field(default_factory=list)      # shadow step entries
    events: list[dict] = dataclass_field(default_factory=list)
    rows: list[dict] = dataclass_field(default_factory=list)
    flags: list[dict] = dataclass_field(default_factory=list)
    record: dict | None = None
    gate_result: dict | None = None
    evidence: dict = dataclass_field(default_factory=dict)
    log: list[str] = dataclass_field(default_factory=list)
    pages_filled: int = 0


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


class Wizard:
    def __init__(self, page: "Page", prof, *, url: str, portal: str, portal_method: str,
                 company: str, role: str, resume, posting_text: str, pauser, echo=print,
                 clock=time.monotonic, agent_submissions_today=None, reload_profile=None):
        self.page, self.prof, self.url = page, prof, url
        self.portal, self.portal_method = portal, portal_method
        self.company, self.role, self.resume = company, role, resume
        self.posting_text, self.pauser, self.echo, self.clock = posting_text, pauser, echo, clock
        self.agent_submissions_today = agent_submissions_today
        self.reload_profile = reload_profile
        self.guard = extract.ReadGuard()
        # The activity listener's agent flag: while the filler works, its
        # own (trusted, Playwright-sent) input is marked as the agent's, not
        # as Jack's, so a touch mark can only come from him.
        self.activity = ledger.ActivityGuard(quiet_ms=0, echo=lambda *a, **k: None)
        self.out = Outcome("needs_you")
        self.snapshots: dict = {}               # every page's read-back, by ref
        self.census: dict = {}                  # every page's checkboxes after filling, by ref
        self.results: dict = {}
        self.human_reviewed: list[str] = []
        self.uploaded: dict | None = {}
        self.resume_done = False

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
            rec["human_reviewed"] = list(self.human_reviewed)
            rec["wizard"] = wizard
        return rec

    def _page_record(self, fill: pipeline.PageFill, events: list[dict]) -> dict:
        return self._record([fill.step], events, wizard=True)

    def _parked(self, reason: str) -> Outcome:
        self.out.status, self.out.reason = "needs_you", reason
        return self.out

    # -- pauses ----------------------------------------------------------------
    def _captcha(self) -> bool:
        found = browser_mod.detect_captcha(self.page)
        if not found:
            return True
        ev = {"kind": "captcha", "detail": found, "handled": None}
        self.out.events.append(ev)
        _, how = self.pauser.wait("job-agent needs you",
                                  f"{found} on the page. Solve it in the browser.",
                                  prompt="Press Enter when it's solved: ",
                                  until=lambda: not browser_mod.detect_captcha(self.page))
        if browser_mod.detect_captcha(self.page):
            return False
        ev["handled"] = "jack"
        return True

    def _marked(self, refs: list[str]) -> set[str]:
        try:
            return set(self.page.evaluate(_TOUCHED_JS, [refs, ledger.TOUCH_ATTR]) or [])
        except Exception:
            return set()

    def _gate_pause(self, fill: pipeline.PageFill, result: dict) -> None:
        """
        The gate stopped on this page: pause, telling Jack the reasons. Only
        a pause he ended with Enter (ANSWERED) counts; a timeout or a busy
        terminal waives nothing. Then this page counts as reviewed by him
        for: a field with a touch mark that is new since before the pause
        (a trusted input of his, the filler's own input being marked as the
        agent's); a field that isn't a checkbox and reads empty (left so by
        him); a field the filler filled that still reads the planned value.
        A field that was empty before and holds a value now with no new
        touch mark is the page's doing, not his, and stays stopped. A
        checkbox counts only when he touched it.
        """
        reasons = "; ".join(_clean(r) for r in result["content_reasons"][:8])
        before = dict(fill.snapshot)
        refs = [f["ref"] for f in fill.fields if f.get("type") != "file"]
        kinds = {f["ref"]: (f.get("type") or "").lower() for f in fill.fields}
        marked_before = self._marked(refs)
        _, how = self.pauser.wait("job-agent needs you",
                                  f"This page needs you: {reasons}. Answer in the browser, then "
                                  f"press Enter.", prompt="Press Enter when you've answered: ")
        if how != pause_mod.ANSWERED:
            self.out.events.append({"kind": "gate_pause", "detail": how, "handled": None})
            return
        if self.reload_profile is not None:
            try:
                self.prof = self.reload_profile()      # Jack may have edited profile.yaml
            except Exception:
                pass
        try:
            now = extract.snapshot(self.page, fill.fields)
        except Exception:
            now = {}
        touched = self._marked(refs) - marked_before
        results = fill.step.get("results") or {}
        reviewed: list[str] = []
        for ref in refs:
            is_now = _present(now.get(ref))
            planned = (fill.plan.get(ref) or {}).get("value")
            agents_own = (ref in fill.plan and bool((results.get(ref) or {}).get("filled"))
                          and planned is not None and _same(now.get(ref), planned))
            if ref in touched:
                reviewed.append(ref)
            elif kinds.get(ref) == "checkbox":
                continue
            elif not is_now or agents_own:
                reviewed.append(ref)
        for ref in reviewed:
            if ref not in self.human_reviewed:
                self.human_reviewed.append(ref)
        fill.snapshot = now
        self.snapshots.update(now)
        try:
            boxes = extract.checkbox_census(self.page)
        except Exception:
            boxes = []
        if isinstance(boxes, list):
            self.census.update({b["ref"]: b for b in boxes if isinstance(b, dict) and b.get("ref")})
        self.out.events.append({"kind": "gate_pause",
                                "detail": f"{len(touched)} field(s) touched by Jack",
                                "handled": "jack"})

    # -- the loop --------------------------------------------------------------
    def run(self) -> Outcome:
        page = self.page
        start = self.clock()
        shape = follow.read_shape(page)
        while True:
            if self.clock() - start > TIME_CAP_S:
                return self._parked("over the 60-minute cap for one application")
            if self.out.pages_filled >= STEP_CAP:
                return self._parked(f"over the {STEP_CAP}-page cap")
            if page.is_closed():
                return self._parked("the tab closed")
            if not self._captcha():
                return self._parked("a CAPTCHA wasn't solved in time")
            verdict = pages.fill_allowed(page, self.url, None)
            if verdict in (pages.LINKEDIN, pages.OFF_HOST):
                return self._parked(f"{verdict.replace('_', ' ')}: the page left the application's site")
            if verdict in (pages.ACCOUNT, pages.SIGN_IN, pages.VERIFICATION):
                res = accounts.handle(page, verdict, self.prof, self.pauser)
                self.out.events += res.events
                self.out.log += res.steps
                if res.status == accounts.PARKED:
                    return self._parked(res.reason)
                browser_mod.settle(page, 800)
                continue
            with self.activity.filling(page):
                fill = pipeline.fill_page(page, self.prof, guard=self.guard,
                                          resume=None if self.resume_done else self.resume,
                                          posting_text=self.posting_text, portal=self.portal)
            page_events: list[dict] = []
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
                self.results.update(fill.step.get("results") or {})
                result = gate.decide(self._page_record(fill, page_events), self.prof,
                                     agent_submissions_today=self.agent_submissions_today)
                if result["content_verdict"] != gate.WOULD_SUBMIT:
                    self._gate_pause(fill, result)
                    result = gate.decide(self._page_record(fill, page_events), self.prof,
                                         agent_submissions_today=self.agent_submissions_today)
                    if result["content_verdict"] != gate.WOULD_SUBMIT:
                        self.out.gate_result = result
                        return self._parked("this page needs you: "
                                            + "; ".join(_clean(r) for r in result["content_reasons"][:6]))
            nxt = find_next(page, self.portal)
            sub = submit_mod.find_submit(page)
            if nxt.get("chosen"):
                if nxt.get("disabled"):
                    return self._parked(f"the '{nxt.get('word')}' control is disabled")
                before = follow.read_shape(page)
                try:
                    page.locator(f"[{NEXT_ATTR}='1']").first.click(timeout=5000)
                except Exception as e:
                    return self._parked(f"couldn't press '{nxt.get('word')}' ({type(e).__name__})")
                self.out.log.append(f"pressed '{nxt.get('word')}'")
                self.out.events.append({"kind": "next", "detail": nxt.get("word"), "handled": "agent"})
                if not self._moved(before):
                    _, how = self.pauser.wait("job-agent needs you",
                                              f"The page didn't move on after '{nxt.get('word')}'. "
                                              f"Fix what it's asking for in the browser, then press Enter.",
                                              prompt="Press Enter when it has moved on: ",
                                              until=lambda: self._moved(before))
                    if not self._moved(before):
                        return self._parked(f"the page didn't move on after '{nxt.get('word')}' ({how})")
                continue
            if sub.get("chosen"):
                if fill is None and self.out.pages_filled == 0:
                    return self._parked("a Submit control with nothing filled; check the page")
                record = self._record(self.out.steps, self.out.events, wizard=True)
                submission = submit_mod.submit_application(
                    self.page, record, self.prof,
                    build_record=lambda: self._record(self.out.steps, self.out.events, wizard=True),
                    steps_log=self.out.log, agent_submissions_today=self.agent_submissions_today)
                self.out.record, self.out.gate_result = record, submission.gate_result
                self.out.evidence = submission.evidence
                if submission.status == "needs_you" and not submission.clicked:
                    # The gate stopped on the whole application: Jack's turn.
                    self._final_pause(submission)
                    record = self._record(self.out.steps, self.out.events, wizard=True)
                    submission = submit_mod.submit_application(
                        self.page, record, self.prof,
                        build_record=lambda: self._record(self.out.steps, self.out.events, wizard=True),
                        steps_log=self.out.log, agent_submissions_today=self.agent_submissions_today)
                    self.out.record, self.out.gate_result = record, submission.gate_result
                    self.out.evidence = submission.evidence
                self.out.status, self.out.reason = submission.status, submission.reason
                return self.out
            if fill is None:
                return self._parked("a page with no fields, no Next and no Submit")
            return self._parked("no Next or Submit control found after filling the page")

    def _final_pause(self, submission) -> None:
        self.out.events.append({"kind": "gate_pause", "detail": "final page", "handled": None})
        _, how = self.pauser.wait("job-agent needs you",
                                  f"Before submitting: {_clean(submission.reason)}. Fix it in the "
                                  f"browser, then press Enter.",
                                  prompt="Press Enter when it's ready to submit: ")
        self.out.events[-1]["handled"] = "jack" if how == pause_mod.ANSWERED else None
        if how == pause_mod.ANSWERED and self.reload_profile is not None:
            try:
                self.prof = self.reload_profile()
            except Exception:
                pass

    def _moved(self, before) -> bool:
        """Has the page changed since `before` (address or field structure)?"""
        browser_mod.settle(self.page, 600)
        now = follow.read_shape(self.page)
        if now is None or before is None:
            return now is not before
        return (now["url"], now["fields"]) != (before["url"], before["fields"])


def _same(a, b) -> bool:
    """The same answer as the filler left: equal once whitespace is collapsed."""
    norm = lambda v: re.sub(r"\s+", " ", str("" if v in (None, False) else v)).strip()
    return norm(a) == norm(b)


def _present(value) -> bool:
    if value is None or value is False:
        return False
    if isinstance(value, (list, tuple)):
        return any(_present(v) for v in value)
    return bool(str(value).strip())


def run_application(page: "Page", prof, **kw) -> Outcome:
    return Wizard(page, prof, **kw).run()
