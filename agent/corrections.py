"""
corrections.py — what Jack changed after the agent filled a page, recorded locally.

WHAT THIS DOES: Builds the rows of corrections.jsonl (the shape `fill` has
logged since log version 2: url, portal, label, type, category, kind, the
profile line's name, a timestamp; a value only for a field both layers
agreed was ordinary, for anything sensitive only THAT he answered) and
keeps a watch on a page the wizard paused on (decision A, Jack,
2026-10-29 evening): the page is read through extract.snapshot's policy
(default deny, nothing at all while a password box is in the document)
every OVERRIDE_POLL_S while the main thread is free, and outcome.Capture's
hook hands over the read at the moment Jack presses the page's own Next
or Submit even while another job is being filled. The last good read is
kept; when the pause ends (Continue, the page moving on, a confirmation)
it is diffed against what the agent planned and filled, and each field
Jack changed, filled where the agent left it blank, or filled after a
failed fill becomes one row. Formatting-only differences are skipped as
the `fill` logger skips them. Local only: no model, no network.
WHY IT MATTERS: `apply.py learn` reports from what is recorded here, and
the same changes feed learned memory (memory.py, Jack, 2026-11-01),
where his own answers are kept for the identical question. A row here
never carries a password (never read) or a legal, EEO or pay value.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import TYPE_CHECKING

from . import classify, config, extract, learn, ledger, memory, outcome, sensitive, tracker

if TYPE_CHECKING:
    from playwright.sync_api import Page

OVERRIDE_POLL_S = 2.0

FILLED_BLANK, OVERWROTE, FILLED_AFTER_FAILURE = "filled_blank", "overwrote", "filled_after_failure"


def path() -> Path:
    return config.STATE_DIR / "corrections.jsonl"


def _norm(v) -> str:
    return "" if v in (None, False) else str(v).strip()


def _safe(field: dict, category: str) -> bool:
    """
    May this field's values be kept? Only when both layers agreed it was
    ordinary: the classifier's category routes to the model, and neither
    the stop backstop nor the legal guard matches its wording. The same
    test that let the model near it in the first place.
    """
    return (classify.action_for(category) in ("autofill", "draft")
            and sensitive.stop_reason(sensitive._question_text(field)) is None
            and sensitive.legal_guard(field) is None)


def _line_for(field: dict, decision: dict | None) -> str | None:
    """The profile line the question belongs to: the one that filled it, else the one its wording maps to. A name, never a value."""
    return ((decision or {}).get("profile_path")
            or next(iter(sensitive.profile_lines(sensitive._question_text(field))), None))


def changes(fields: list[dict], plan: dict, final: dict, *, results: dict | None = None,
            baseline: dict | None = None, source: str = "fill",
            categories: dict | None = None) -> tuple[list[dict], list[str]]:
    """
    The changes a read of the form (`final`, by ref) implies, with the
    real values, and the labels of fields the read left out. One dict per
    change: field, kind (filled_blank, filled_after_failure, overwrote),
    agent (what the agent held), now (what the box holds), decision,
    category. The one diff behind rows_for (corrections.jsonl, values
    dropped for anything sensitive) and memory.learn (Jack's own answers,
    kept there and nowhere else).
    """
    out: list[dict] = []
    unread: list[str] = []
    for f in fields:
        if f.get("type") in ("file", "password") or extract.is_secret(f):
            continue
        ref = f["ref"]
        label = f.get("label") or f.get("name") or "(unlabelled)"
        if ref not in final:
            unread.append(str(label)[:80])       # not read back; logging it as cleared would be false
            continue
        decision = plan.get(ref) or {}
        planned = decision.get("value")
        result = (results or {}).get(ref)
        landed = result is None or bool(result.get("filled"))
        if baseline is not None and ref in baseline:
            agent = baseline[ref]
        else:
            agent = planned if landed else None
        now = final.get(ref)
        if _norm(agent) == _norm(now):
            continue
        # Judged on the real values, before sensitive ones are dropped: a
        # reformatted phone number or a dropdown that reads back blank isn't
        # a correction, and logging it teaches learn the wrong thing.
        if (learn.same_answer(agent, now, f.get("type"))
                or learn.unreadable(agent, now, f.get("type"))):
            continue
        if _norm(now) == "" and source != "fill":
            continue                            # the brief's three kinds all hold a value now
        if _norm(planned) and not landed and _norm(agent) == "":
            kind = FILLED_AFTER_FAILURE
        elif _norm(agent) == "":
            kind = FILLED_BLANK
        else:
            kind = OVERWROTE
        out.append({"field": f, "kind": kind, "agent": agent, "now": now, "decision": decision,
                    "category": (categories or {}).get(ref, "unknown")})
    return out, unread


def rows_for(url: str, fields: list[dict], plan: dict, categories: dict, final: dict, *,
             results: dict | None = None, baseline: dict | None = None, source: str = "fill",
             portal: str = "") -> tuple[list[dict], list[str]]:
    """
    The correction rows a read of the form (`final`, by ref) implies, and
    the labels of fields the read left out (nothing is recorded for them).
    What the agent left in a box is `baseline` (its fill-time read-back)
    when known, else the plan's value when the fill went through, else
    nothing. A box that holds something else now is a correction: kind
    filled_blank (the agent planned nothing), filled_after_failure (it
    planned a value the fill didn't land) or overwrote.
    """
    found, unread = changes(fields, plan, final, results=results, baseline=baseline,
                            source=source, categories=categories)
    rows: list[dict] = []
    for ch in found:
        f, cat = ch["field"], ch["category"]
        safe = _safe(f, cat)
        rows.append({
            "url": url, "portal": portal, "source": source,
            "label": f.get("label"), "type": f.get("type"), "category": cat, "kind": ch["kind"],
            "agent_value": ch["agent"] if safe else None,
            "final_value": ch["now"] if safe else None,
            "profile_path": _line_for(f, ch["decision"]),
            "logged_at": tracker._timestamp(),
            "log_version": learn.LOG_VERSION,
        })
    return rows, unread


def learn_from(changes_: list[dict], *, url: str, portal: str, prof, log: list | None = None,
               fields: list[dict] | None = None) -> list[dict]:
    """
    Jack's own entries into learned memory (Jack, 2026-11-01): one entry
    per change memory accepts; the job log names each by question and
    kind, never by value. `fields` is the whole page, so a key two of
    its fields share is never learned (Jack, 2026-11-02). Never raises.
    """
    try:
        learned = memory.learn(changes_, url=url, portal=portal, prof=prof, fields=fields)
    except memory.Unreadable as e:
        if log is not None:
            log.append(f"learned.yaml didn't parse ({e}): nothing learned or saved over it; fix or delete the file")
        return []
    except Exception as e:
        if log is not None:
            log.append(f"couldn't learn from your entries ({type(e).__name__})")
        return []
    if learned and log is not None:
        log.append(f"learned {len(learned)} answer(s) of yours for the same question next time: "
                   + ", ".join(memory.describe(e) for e in learned[:6]))
    return learned


def append(rows: list[dict], at: Path | None = None) -> Path:
    """Write the rows, one JSON object per line, to corrections.jsonl (or `at`)."""
    at = at or path()
    at.parent.mkdir(parents=True, exist_ok=True)
    with at.open("a", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, default=str) + "\n")
    return at


class _Hooked(outcome.Capture):
    """
    outcome.Capture with the time of its newest read, so the watch can pick
    the later of hook and poll, and with the touch marks the read carried:
    the fields (by their own and their options' refs) that held Jack's
    mark at his press go into the watch's `marks` (fold of ab9cb55's
    finding 2: an edit within one poll of his own Next press is his).
    """

    at: float | None = None

    def __init__(self, by_field: dict[str, list[str]] | None = None, marks: set | None = None):
        super().__init__()
        self.by_field = by_field or {}
        self.marks = marks if marks is not None else set()

    def _keep(self, snap: dict, kind: str) -> None:
        super()._keep(snap, kind)
        self.at = time.monotonic()

    def _on_submit(self, source, raw=None, how=None, marks=None) -> None:
        super()._on_submit(source, raw, how, marks)
        if isinstance(marks, list):
            hit = {r for r in marks[:_MAX_MARKS] if isinstance(r, str)}
            self.marks |= {ref for ref, refs in self.by_field.items() if any(r in hit for r in refs)}


_MAX_MARKS = 2000


class Watch:
    """
    The read-only watch on one paused page. `fields` are the page's
    fields as filled (the refs the hook and the poll read; copied, so a
    later remap doesn't move them). Both routes read only what
    extract.readable_refs allows, and _SNAPSHOT_JS reads nothing while a
    password box is in the document.
    """

    def __init__(self, page: "Page", fields: list[dict]):
        self.fields = [dict(f) for f in fields]
        self.items = extract.read_items(self.fields)
        self.refs = [r for f in self.fields for r in (f.get("option_refs") or [f.get("ref")]) if r]
        # Each field's ref -> the refs a touch of it can land on (its own
        # and its options'); `marks` is every field seen carrying Jack's
        # touch mark at any poll of the pause (the union: a mark gone
        # with a re-render still happened). memory.learn takes only a
        # change whose field is in it (fix of fe30a9a's BLOCK).
        self.by_field = {f["ref"]: [f["ref"], *(r for r in (f.get("option_refs") or []) if r != f["ref"])]
                         for f in self.fields if f.get("type") != "file" and f.get("ref")}
        self.marks: set[str] = set()
        self.hook = _Hooked(self.by_field, self.marks)
        self.hook.arm(page, self.fields,
                      marks=(ledger.REVIEW_ATTR, [r for refs in self.by_field.values() for r in refs]))
        self.last: dict | None = None
        self.at: float | None = None
        self.polled_at: float = 0.0
        self.reads = 0

    def poll(self, page: "Page", *, force: bool = False) -> None:
        """
        One read, if the form is still there; rate-limited to OVERRIDE_POLL_S
        unless forced. Nothing is evaluated on Google's host or a job board
        (the tab left the application's site: fold of 2c42879's finding 4).
        """
        now = time.monotonic()
        if not force and now - self.polled_at < OVERRIDE_POLL_S:
            return
        self.polled_at = now
        if not self.items:
            return
        if not self.on_application_site(page):
            return
        try:
            if not page.evaluate(outcome._PRESENT_JS, self.refs):
                return
            raw = page.evaluate(extract._SNAPSHOT_JS, self.items)
        except Exception:
            return
        if not isinstance(raw, dict):
            return
        self.last, self.at, self.reads = extract.snapshot_from_raw(self.fields, raw), time.monotonic(), self.reads + 1
        self.read_marks(page)

    @staticmethod
    def on_application_site(page: "Page") -> bool:
        """False on Google's host or a job board (LinkedIn, Handshake), or when the address can't be read: no page call then."""
        from . import google, intake
        try:
            url = page.url
        except Exception:
            return False
        return not google.is_google_host(url) and intake.job_board(url) is None

    def read_marks(self, page: "Page") -> None:
        """Which fields carry Jack's touch mark now, added to `marks`; a read that fails adds nothing."""
        try:
            hit = set(page.evaluate(ledger.TOUCHED_JS,
                                    [[r for refs in self.by_field.values() for r in refs], ledger.REVIEW_ATTR]) or [])
        except Exception:
            return
        self.marks |= {ref for ref, refs in self.by_field.items() if any(r in hit for r in refs)}

    def read(self) -> dict | None:
        """The last good read: the newer of the poll's and the hook's, or None if there was none."""
        hooked = self.hook.final if self.hook.final is not None else None
        if hooked is not None and (self.at is None or (self.hook.at or 0) >= self.at):
            return hooked
        return self.last


def log_overrides(watch: Watch | None, fill, *, url: str, portal: str, log: list, prof=None,
                  touched: set | None = None) -> list[dict]:
    """
    The pause on `fill`'s page ended: diff the watch's last read against
    what the agent planned and filled, write the rows, learn his entries
    into memory (Jack, 2026-11-01), say in the job log what was recorded
    and what couldn't be. Only a change on a field in `touched` (the
    fields that gained his touch mark under the pause, by the wizard's
    reading) is learned: a value the page set itself is not his. None
    learns nothing. Never raises.
    """
    if watch is None or fill is None:
        return []
    final = watch.read()
    if final is None:
        log.append("no readable read of the page under the pause: no correction recorded")
        return []
    try:
        step = getattr(fill, "step", None) or {}
        plan, categories = getattr(fill, "plan", None) or {}, getattr(fill, "categories", None) or {}
        rows, unread = rows_for(url, watch.fields, plan, categories, final,
                                results=step.get("results"), baseline=getattr(fill, "snapshot", None) or {},
                                source="override", portal=portal)
        if rows:
            append(rows)
            log.append(f"recorded {len(rows)} correction(s) of yours: "
                       + ", ".join(f"'{str(r.get('label') or '')[:40]}' ({r['kind']})" for r in rows[:6]))
        if unread:
            log.append(f"no readable last read of {len(unread)} field(s) under the pause, nothing recorded for them: "
                       + ", ".join(f"'{u[:40]}'" for u in unread[:5]))
        found, _ = changes(watch.fields, plan, final, results=step.get("results"),
                           baseline=getattr(fill, "snapshot", None) or {}, source="override",
                           categories=categories)
        his = [ch for ch in found if touched and ch["field"].get("ref") in touched]
        if len(his) < len(found):
            log.append(f"{len(found) - len(his)} change(s) under the pause carried no touch mark of yours: "
                       "recorded, not learned")
        learn_from(his, url=url, portal=portal, prof=prof, log=log, fields=watch.fields)
        return rows
    except Exception as e:
        log.append(f"couldn't record corrections ({type(e).__name__})")
        return []
