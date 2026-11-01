"""
gui.py — the one-page local GUI: paste links, watch rows, press Continue.

WHAT THIS DOES: Serves a single HTML page on 127.0.0.1 (random port) from
a thread, and keeps one in-memory State the queue loop and the page share:
per job the company, role, portal, status, reason, and when a job needs
Jack, the pause's kind, each stopped field's label and reason and, only
for a field the gate's own sensitive test doesn't flag, that isn't a
checkbox, a consent or a per-company question, and that the agent filled,
the page's read-back value with an approve tick. Links, approvals, codes,
Continue and Close requests go from the page into State and nowhere else.
The auto-submit switch (Jack, 2026-10-29) lives here too: OFF at every
start, never persisted, shown at the top of the page in words with a
warning while ON, and read by the wizard at the final page.
WHY IT MATTERS: The page shows untrusted text (labels from job pages), so
it renders every string as a text node under a strict CSP, binds only to
the loopback address, checks Host and Origin on every request and a
per-session token on every change. Nothing shown here is logged, stored,
or sent to a model; legal, EEO, pay and password fields never reach it.
"""

from __future__ import annotations

import json
import secrets
import threading
import time
from dataclasses import dataclass, field as dataclass_field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

from . import approve, gate, joblog, memory, sensitive

QUEUED, WORKING, NEEDS_YOU, SUBMITTED, FAILED, DRY, DONE, CLOSED = (
    "queued", "working", "needs_you", "submitted", "failed", "dry", "done", "closed")

# Categories whose fields never get an approve tick, beyond what the gate's
# sensitive test already excludes (Jack, 2026-10-19).
NEVER_APPROVABLE_CATEGORIES = {"consent", "per_company"}


def approvable(field: dict, category: str | None, decision: dict | None,
               result: dict | None) -> bool:
    """
    May this stopped field be offered for approval in the GUI? Only when
    the gate's own sensitive test doesn't flag it (category, a PROFILE_PATHS
    line, a legal stop kind, LEGAL_GUARD, the page's raw flags), it isn't a
    checkbox, isn't a consent or per-company question, and the agent
    filled it with a value. Everything else is shown by label and reason
    only, with no value.
    """
    if (field.get("type") or "").lower() in ("checkbox", "password", "file"):
        return False
    if category in NEVER_APPROVABLE_CATEGORIES or category in gate.SENSITIVE_CATEGORIES:
        return False
    text = sensitive._question_text(field)
    if (sensitive.sensitive_line(text, gate.SENSITIVE_PREFIXES) is not None
            or "legal" in sensitive.stop_kinds(text) or "per_company" in sensitive.stop_kinds(text)
            or sensitive.legal_guard(field) is not None
            or field.get("raw_stop") or field.get("raw_legal") or field.get("secret")):
        return False
    if not decision or not has_value(decision.get("value")) or decision.get("source") == "human":
        return False
    return bool((result or {}).get("filled"))


def has_value(value) -> bool:
    """Is there something to approve: a planned or read-back value that isn't None, False or blank?"""
    return value is not None and value is not False and str(value).strip() != ""


@dataclass
class Job:
    id: str
    url: str
    dry: bool = False
    company: str = ""
    role: str = ""
    portal: str = ""
    status: str = QUEUED
    reason: str = ""
    final_url: str = ""
    form_found: bool | None = None
    progress: str = ""                 # the newest job-log line while the job runs (Jack, 2026-10-29)
    pause: dict | None = None          # {"kind", "message", "fields": [...], "wants_code": bool}
    approved: set = dataclass_field(default_factory=set)
    code: str | None = None
    continue_requested: bool = False

    def view(self) -> dict:
        # The landing line (Jack, 2026-10-29): "form with fields" is a dry
        # run's reading at intake; a real run never reads it (the form may
        # render later), so its line names the address only.
        landing = f"landed on {self.final_url}" if self.final_url else ""
        if landing and self.form_found is not None:
            landing += f" / form with fields: {'yes' if self.form_found else 'no'}"
        pause = None
        if self.pause:
            pause = {"kind": self.pause.get("kind"), "message": self.pause.get("message", ""),
                     "wants_code": bool(self.pause.get("wants_code")),
                     "fields": [{"ref": f["ref"], "label": f.get("label", ""),
                                 "reason": f.get("reason", ""),
                                 "approvable": bool(f.get("approvable")),
                                 "value": f.get("value") if f.get("approvable") else None,
                                 "approved": f["ref"] in self.approved}
                                for f in self.pause.get("fields", [])]}
        return {"id": self.id, "url": self.url, "dry": self.dry, "company": self.company,
                "role": self.role, "portal": self.portal, "status": self.status,
                "short_status": self.short_status(), "summary": self.summary(),
                "reason": self.reason, "final_url": self.final_url,
                "form_found": self.form_found, "progress": self.progress, "landing": landing,
                "pause": pause,
                "code_given": self.code is not None}

    # One word for the chip and one line under it (Jack, 2026-11-01,
    # decision 6: "Statuses are too long... Simplify it").
    SHORT = {QUEUED: "Queued", WORKING: "Working", NEEDS_YOU: "Needs you", SUBMITTED: "Submitted",
             FAILED: "Failed", DRY: "Dry run", DONE: "Done", CLOSED: "Closed", "skipped": "Skipped"}

    def is_ready(self) -> bool:
        return self.status == NEEDS_YOU and (self.pause or {}).get("kind") == "ready"

    def short_status(self) -> str:
        if self.is_ready():
            return "Ready"
        return self.SHORT.get(self.status, str(self.status or "").replace("_", " ").capitalize())

    def summary(self) -> str:
        """The one line a row shows: a count of what needs Jack, the newest step, or the reason."""
        def short(text, n=140):
            text = " ".join(str(text or "").split())
            return text if len(text) <= n else text[:n - 1] + "…"
        if self.is_ready():
            return "Filled — review and press Submit yourself"
        if self.status == NEEDS_YOU:
            fields = (self.pause or {}).get("fields") or []
            if fields:
                n = len(fields)
                return f"{n} field{'s' if n != 1 else ''} need{'' if n != 1 else 's'} you"
            return short((self.pause or {}).get("message") or self.reason or "needs you")
        if self.status == WORKING:
            return short(self.progress or "working")
        if self.status == QUEUED:
            return "waiting in the queue"
        if self.status == DRY:
            return short(self.reason or "dry run: nothing filled")
        if self.status == SUBMITTED:
            return short(self.reason or "submitted")
        return short(self.reason or self.status)


class State:
    """What the page and the queue loop share. Every access takes the lock."""

    def __init__(self):
        self.lock = threading.Lock()
        self.jobs: list[Job] = []
        self.close_browser = False
        self.google_login = False
        self.browser_open = False
        self.notice = ""
        # The auto-submit switch (Jack, 2026-10-29): OFF at every start,
        # never persisted; the wizard reads it at the final page through
        # auto_submit_on, never captured at job start.
        self.auto_submit = False
        # profile.yaml, for Save to profile (Jack, 2026-11-01): `profile_path`
        # is set by cmd_gui; without it nothing can be saved. A save marks
        # `profile_changed` so the queue loop reloads before the next job
        # (the wizard reloads before its next page by the file's stamp).
        self.profile_path = None
        self.profile_changed = False
        # Where the jobs' log files are (Jack, 2026-11-01); tests point it
        # at a temp folder. "View log" reads one job's file by its id.
        self.log_root = None
        # The learned store the Memory page reads and edits; None is the
        # default file under the state directory.
        self.memory_path = None

    def job_log(self, job_id: str) -> dict:
        """The lines of one job's log file, as text, for "View log"; only a job this session started."""
        with self.lock:
            job = self._job(job_id)
        if job is None:
            return {"ok": False, "error": "no such job"}
        lines = joblog.read(job.id, root=self.log_root)
        return {"ok": True, "lines": lines or [], "empty": lines is None}

    # -- Save to profile (Jack, 2026-11-01, decision 5) ------------------------
    def save_to_profile(self, entry_id: str) -> dict:
        """
        Jack's click: one profile candidate from learned memory written to
        its ordinary profile line through approve.apply_profile_line (a
        quoted string, comments kept, a backup, the last five kept), under
        approve's one lock from the check of the line's current value to
        the write. Never a legal, EEO, pay, DoD or per-company line, never
        a choice mapping or a plain answer (they name no line to write).
        The candidate is dropped from memory once the profile holds it.
        """
        with self.lock:
            path = self.profile_path
        if path is None:
            return {"ok": False, "error": "no profile to write"}
        store = memory.Learned.load(self.memory_path)
        entry = store.get(str(entry_id)[:64])
        if entry is None:
            return {"ok": False, "error": "no such entry"}
        if entry.get("legal") or entry.get("kind") != memory.CANDIDATE or not entry.get("line"):
            return {"ok": False, "error": "only a profile candidate can be saved; this entry is yours to edit by hand"}
        line, value = str(entry["line"]), str(entry.get("answer") or "")
        try:
            with approve.LOCK:
                from .profile import Profile
                current = Profile.load(path).get(line)
                held = "" if current is None or isinstance(current, (bool, list, dict)) else str(current).strip()
                if held != str(entry.get("line_value") or "").strip():
                    return {"ok": False, "error": f"`{line}` changed since this was learned; nothing written"}
                backup = approve.apply_profile_line(path, line, value, approve.profile_stamp(path))
        except approve.Changed as e:
            return {"ok": False, "error": str(e)}
        except approve.Refused as e:
            return {"ok": False, "error": str(e)}
        except Exception as e:
            return {"ok": False, "error": f"couldn't write it ({type(e).__name__})"}
        store.delete(entry["id"])
        done = f"`{line}` written; backup {backup.name}"
        with self.lock:
            self.profile_changed = True
            self.notice = done
        return {"ok": True, "done": done}

    # -- the Memory page (Jack, 2026-11-01, decision 3) -------------------------
    GROUPS = ("choice", "candidate", "legal", "portal")

    def memory_view(self) -> dict:
        """
        Every learned entry, grouped for the page: choice mappings, profile
        candidates, legal & EEO answers, portal answers. Shown to Jack's own
        browser only (local, token, Host and Origin checked); a legal entry
        shows its answer here so he can tune or delete it, which the jobs
        view never does.
        """
        try:
            store = memory.Learned.load(self.memory_path)
        except Exception:
            return {"ok": False, "error": "learned memory couldn't be read", "entries": []}
        out = []
        for e in store.entries:
            kind = e.get("kind")
            if e.get("legal"):
                group = "legal"
            elif kind == memory.CHOICE:
                group = "choice"
            elif kind == memory.CANDIDATE:
                group = "candidate"
            else:
                group = "portal"
            out.append({"id": e.get("id"), "kind": kind, "group": group,
                        "question": str(e.get("question") or ""), "type": str(e.get("type") or ""),
                        "options": [str(o) for o in (e.get("options") or [])][:60],
                        "answer": str(e.get("answer") or ""), "line": e.get("line"),
                        "line_value": e.get("line_value") if group == "candidate" else None,
                        "scope": "everywhere" if e.get("scope") == memory.ANY else str(e.get("scope") or ""),
                        "host": str(e.get("host") or ""), "portal": str(e.get("portal") or ""),
                        "first_seen": str(e.get("first_seen") or ""), "last_seen": str(e.get("last_seen") or ""),
                        "seen": int(e.get("seen") or 0), "uses": int(e.get("uses") or 0),
                        "posting": str(e.get("posting") or ""),
                        "can_save": group == "candidate" and bool(e.get("line"))})
        return {"ok": True, "entries": out, "groups": list(self.GROUPS)}

    def memory_edit(self, entry_id: str, answer: str) -> dict:
        """Jack tunes an answer: the entry keeps its question and kind; the next job routes on the new value."""
        if not isinstance(answer, str) or not answer.strip() or len(answer) > 4000:
            return {"ok": False, "error": "an answer to keep"}
        try:
            store = memory.Learned.load(self.memory_path)
            ok = store.edit(str(entry_id)[:64], answer)
        except Exception as e:
            return {"ok": False, "error": f"couldn't edit it ({type(e).__name__})"}
        return {"ok": ok} if ok else {"ok": False, "error": "no such entry"}

    def memory_delete(self, entry_id: str) -> dict:
        """Jack forgets an entry: gone from the file, never reused."""
        try:
            store = memory.Learned.load(self.memory_path)
            ok = store.delete(str(entry_id)[:64])
        except Exception as e:
            return {"ok": False, "error": f"couldn't delete it ({type(e).__name__})"}
        return {"ok": ok} if ok else {"ok": False, "error": "no such entry"}

    def take_profile_changed(self) -> bool:
        """Did a Save to profile write the profile since the loop last asked? Cleared on read."""
        with self.lock:
            changed, self.profile_changed = self.profile_changed, False
            return changed

    def set_auto_submit(self, on: bool) -> bool:
        with self.lock:
            self.auto_submit = on is True
            return self.auto_submit

    def auto_submit_on(self) -> bool:
        with self.lock:
            return self.auto_submit is True

    # -- from the page --------------------------------------------------------
    def start(self, links: list[str], dry: bool) -> list[str]:
        with self.lock:
            ids = []
            known = {j.url for j in self.jobs if j.status in (QUEUED, WORKING, NEEDS_YOU)}
            for link in links:
                link = link.strip()
                if not link or link in known:
                    continue
                job = Job(id=secrets.token_hex(6), url=link, dry=dry)
                self.jobs.append(job)
                ids.append(job.id)
                known.add(link)
            return ids

    def approve(self, job_id: str, ref: str, on: bool) -> bool:
        with self.lock:
            job = self._job(job_id)
            if job is None or not job.pause:
                return False
            if not any(f["ref"] == ref and f.get("approvable") for f in job.pause.get("fields", [])):
                return False
            (job.approved.add if on else job.approved.discard)(ref)
            return True

    def give_code(self, job_id: str, code: str) -> bool:
        with self.lock:
            job = self._job(job_id)
            if job is None or not job.pause or not job.pause.get("wants_code"):
                return False
            job.code = code.strip()[:64]
            return True

    def request_continue(self, job_id: str) -> bool:
        with self.lock:
            job = self._job(job_id)
            if job is None or job.status != NEEDS_YOU:
                return False
            if (job.pause or {}).get("kind") == "closed":
                return False            # its tab is gone: Close is the only action
            job.continue_requested = True
            return True

    def close_job(self, job_id: str) -> bool:
        """Jack closes a job whose tab is gone (pause kind "closed"): nothing more is done for it."""
        with self.lock:
            job = self._job(job_id)
            if job is None or job.status != NEEDS_YOU or (job.pause or {}).get("kind") != "closed":
                return False
            job.status, job.pause, job.continue_requested = CLOSED, None, False
            job.reason = f"{job.reason} (closed by Jack)" if job.reason else "closed by Jack"
            return True

    def closed_jobs(self) -> list[str]:
        with self.lock:
            return [j.id for j in self.jobs if j.status == CLOSED]

    def request_close(self, confirm: bool) -> dict:
        with self.lock:
            waiting = [j.id for j in self.jobs if j.status == NEEDS_YOU]
            if waiting and not confirm:
                return {"ok": False, "waiting": len(waiting)}
            self.close_browser = True
            return {"ok": True, "waiting": len(waiting)}

    def request_google_login(self) -> None:
        with self.lock:
            self.google_login = True

    # -- from the queue loop --------------------------------------------------
    def next_queued(self) -> Job | None:
        with self.lock:
            for job in self.jobs:
                if job.status == QUEUED:
                    job.status = WORKING
                    return job
            return None

    def update(self, job_id: str, **fields) -> None:
        with self.lock:
            job = self._job(job_id)
            if job is not None:
                for k, v in fields.items():
                    setattr(job, k, v)

    def set_pause(self, job_id: str, kind: str, message: str, fields: list[dict] | None = None,
                  wants_code: bool = False) -> None:
        """
        A job needs Jack. `fields` is the stopped fields in the GUI's shape:
        {"ref", "label", "reason", "value", "raw"}; `raw` carries the gate's
        own field, category, decision and result, and State decides
        approvability from it with `approvable` here, never from a flag the
        caller passes. A field without `raw` is never approvable, and a
        value is kept only for an approvable field.
        """
        with self.lock:
            job = self._job(job_id)
            if job is None:
                return
            shaped = []
            for f in fields or []:
                raw = f.get("raw") if isinstance(f.get("raw"), dict) else None
                # A field with no planned value, or no read-back value to
                # show, is never offered: nothing to approve, and "null" is
                # never drawn (Jack, 2026-10-29 evening).
                ok = (bool(raw) and approvable(raw.get("field") or {}, raw.get("category"),
                                               raw.get("decision"), raw.get("result"))
                      and has_value(f.get("value")))
                shaped.append({"ref": f.get("ref"), "label": f.get("label", ""),
                               "reason": f.get("reason", ""), "approvable": ok,
                               "value": f.get("value") if ok else None})
            job.status = NEEDS_YOU
            job.reason = message
            job.pause = {"kind": kind, "message": message, "wants_code": wants_code,
                         "fields": shaped}
            job.approved = set()
            job.code = None
            job.continue_requested = False

    def take_resume(self, job_id: str) -> dict | None:
        """The approvals and code for a job Jack asked to continue, handed over once."""
        with self.lock:
            job = self._job(job_id)
            if job is None or not job.continue_requested:
                return None
            shown = {f["ref"]: f.get("value") for f in (job.pause or {}).get("fields", [])
                     if f.get("approvable")}
            out = {"approved": {r: shown.get(r) for r in job.approved}, "code": job.code}
            job.continue_requested, job.code, job.approved = False, None, set()
            job.pause, job.status = None, WORKING
            return out

    def resumable(self) -> list[str]:
        """Jobs Jack pressed Continue on, in order."""
        with self.lock:
            return [j.id for j in self.jobs if j.status == NEEDS_YOU and j.continue_requested]

    def waiting_for_jack(self) -> list[str]:
        with self.lock:
            return [j.id for j in self.jobs if j.status == NEEDS_YOU]

    def snapshot(self) -> dict:
        with self.lock:
            return {"jobs": [j.view() for j in self.jobs], "browser_open": self.browser_open,
                    "notice": self.notice, "auto_submit": self.auto_submit is True}

    def _job(self, job_id: str) -> Job | None:
        return next((j for j in self.jobs if j.id == job_id), None)


# --- the page ---------------------------------------------------------------

_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>job-agent</title>
<style nonce="%(nonce)s">
:root { --ink: #16202b; --muted: #5b6875; --line: #d5dae0; --paper: #fff; --bg: #f4f6f8; }
body { font: 15px/1.5 system-ui, sans-serif; margin: 0; color: var(--ink); background: var(--bg); }
main { max-width: 1100px; margin: 0 auto; padding: 1.2rem 1.2rem 3rem; }
h1 { font-size: 1.3em; margin: .4rem 0 .8rem; }
textarea { width: 100%%; height: 4.5rem; font: inherit; padding: .5rem; border: 1px solid var(--line); border-radius: .4rem; box-sizing: border-box; }
button { font: inherit; padding: .35rem .9rem; border: 1px solid #b9c2cc; border-radius: .4rem; background: #fff; cursor: pointer; }
button:hover { background: #eef1f4; }
button.primary { background: #16202b; color: #fff; border-color: #16202b; }
#switch { display: flex; gap: 1rem; align-items: center; flex-wrap: wrap; padding: .9rem 1rem; margin: .5rem 0 1rem;
          border: 3px solid #5b6875; border-radius: .5rem; background: #e9ecef; font-size: 1.25em; font-weight: 700; }
#switch.on { border-color: #9b2226; background: #fde2e4; color: #6a0f14; }
#switch button { font-size: 1em; font-weight: 700; padding: .5rem 1.2rem; }
#warning { flex-basis: 100%%; font-size: .8em; font-weight: 400; color: #6a0f14; }
.row-actions { display: flex; gap: .5rem; align-items: center; flex-wrap: wrap; margin-top: .4rem; }
.muted { color: var(--muted); font-size: .9em; }
#tabs { display: flex; gap: .4rem; margin: .2rem 0 1rem; }
#tabs button { padding: .4rem 1.1rem; border: 1px solid var(--line); border-bottom: none; background: #e9ecef; border-radius: .4rem .4rem 0 0; }
#tabs button.active { background: #fff; font-weight: 700; }
#jobs-list { margin-top: 1rem; display: flex; flex-direction: column; gap: .5rem; }
.job { background: var(--paper); border: 1px solid var(--line); border-radius: .5rem; }
.job .head { display: grid; grid-template-columns: 1.4fr 1.6fr .8fr auto; gap: .8rem; align-items: center; padding: .6rem .9rem; cursor: pointer; }
.job .head .name { font-weight: 600; }
.job .head .sum { grid-column: 1 / -1; color: var(--muted); font-size: .92em; margin-top: -.2rem; }
.job.failed { border-color: #c0343a; background: #fff0f0; }
.job.failed .head .sum { color: #9b2226; }
.job.needs_you { border-color: #d9951e; }
.job.ready { border-color: #2f8a57; }
.chip { display: inline-block; padding: .1rem .7rem; border-radius: 1rem; font-size: .8em; font-weight: 700; white-space: nowrap; background: #e9ecef; color: #3c4854; }
.chip.working { background: #dfe9ff; color: #1b3f8f; }
.chip.needs_you { background: #ffe9c4; color: #7a4a00; }
.chip.ready { background: #d8f2e2; color: #1f5e3a; }
.chip.submitted { background: #d8f2e2; color: #1f5e3a; }
.chip.failed { background: #f8d2d4; color: #8a1c21; }
.chip.skipped, .chip.closed, .chip.dry, .chip.queued { background: #e9ecef; color: #3c4854; }
.job .body { border-top: 1px solid var(--line); padding: .6rem .9rem .8rem; }
.job .body h3 { font-size: .95em; margin: .6rem 0 .2rem; }
.job .body ul { margin: .2rem 0; padding-left: 1.2rem; }
.job .body li { margin: .15rem 0; }
.url { color: var(--muted); font-size: .85em; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 100%%; }
.joblog { margin: .3rem 0 0; padding: .4rem .6rem; background: #f1f3f5; border: 1px solid var(--line); font: 12px/1.4 ui-monospace, monospace;
          white-space: pre-wrap; max-height: 16rem; overflow: auto; }
#toast { position: fixed; right: 1rem; bottom: 1rem; max-width: 28rem; background: #8a1c21; color: #fff; padding: .7rem 1rem; border-radius: .5rem;
         box-shadow: 0 4px 16px rgba(0,0,0,.25); cursor: pointer; z-index: 9; }
#memory-search { width: 100%%; font: inherit; padding: .4rem .6rem; margin: .4rem 0 .8rem; box-sizing: border-box; }
.mgroup { margin: 1rem 0; }
.mgroup h2 { font-size: 1.05em; margin: .4rem 0; }
.mentry { background: #fff; border: 1px solid var(--line); border-radius: .4rem; padding: .5rem .8rem; margin: .4rem 0; }
.mentry .q { font-weight: 600; }
.mentry input.answer { font: inherit; width: 24rem; max-width: 100%%; padding: .2rem .4rem; }
.mentry .meta { color: var(--muted); font-size: .85em; }
.mentry .legal { color: #6a0f14; font-size: .85em; }
</style></head>
<body><main>
<h1>job-agent</h1>
<div id="switch" class="off">
  <button id="toggle">Turn auto-submit ON</button>
  <span id="switch-text">Auto-submit: OFF — you press Submit</span>
  <div id="warning" hidden>While ON the agent presses Submit when the gate passes. The MUST FIX BEFORE AUTO-SUBMIT items in TODO.md are still open.</div>
</div>
<div id="tabs"><button id="tab-jobs" class="active">Jobs</button><button id="tab-memory">Memory</button></div>
<section id="jobs">
<textarea id="links" placeholder="Paste job links, one per line"></textarea>
<div class="row-actions">
  <button id="start" class="primary">Start</button>
  <label class="muted"><input type="checkbox" id="dry"> dry run (open, click Apply, report; fill nothing)</label>
  <button id="google">Google login</button>
  <button id="close">Close browser</button>
  <span id="notice" class="muted"></span>
</div>
<p class="muted">Click a row to see what it couldn't fill, what it learned and its log. A row that needs you chimes; a red row broke.</p>
<div id="jobs-list"></div>
</section>
<section id="memory" hidden>
<p class="muted">What the agent learned from your own entries, reused only on the exact same question. Edit an answer, delete anything wrong; a profile candidate is written to profile.yaml only when you press Save to profile. Nothing here leaves this machine.</p>
<input id="memory-search" placeholder="search questions, answers, hosts" autocomplete="off">
<div id="memory-groups"></div>
</section>
</main>
<script nonce="%(nonce)s">
(() => {
  const TOKEN = %(token)s;
  const seenNeedsYou = new Set(), seenFailed = new Set(), open = new Set();
  const el = (tag, text, cls) => { const e = document.createElement(tag); if (text !== undefined) e.textContent = String(text); if (cls) e.className = cls; return e; };
  const api = (path, body) => fetch(path, {method: 'POST', headers: {'Content-Type': 'application/json', 'X-Token': TOKEN},
                                            body: JSON.stringify(body || {})}).then(r => r.json()).catch(() => ({ok: false}));
  const chime = (hz) => { try { const c = new AudioContext(); const o = c.createOscillator(); const g = c.createGain();
      o.frequency.value = hz || 880; g.gain.value = 0.08; o.connect(g); g.connect(c.destination); o.start(); o.stop(c.currentTime + 0.35); } catch (e) {} };
  const toast = text => { const old = document.getElementById('toast'); if (old) old.remove();
      const t = el('div', text); t.id = 'toast'; t.addEventListener('click', () => t.remove()); document.body.appendChild(t); };
  const shortUrl = u => { try { const x = new URL(u); const path = x.pathname.length > 28 ? x.pathname.slice(0, 27) + '…' : x.pathname; return x.hostname + path; } catch (e) { return String(u).slice(0, 60); } };
  const chipClass = j => (j.short_status || '').toLowerCase().replace(' ', '_');
  const drawBody = (j, body) => {
    body.replaceChildren();
    const u = el('div', shortUrl(j.url), 'url'); u.title = j.url; body.appendChild(u);
    if (j.landing) body.appendChild(el('div', j.landing, 'muted'));
    if (j.reason && j.reason !== j.summary) body.appendChild(el('div', j.reason));
    if (j.pause) {
      body.appendChild(el('h3', j.pause.fields.length ? "Fields it couldn't fill" : 'What it needs'));
      if (!j.pause.fields.length) body.appendChild(el('div', j.pause.message));
      else {
        const ul = el('ul');
        for (const f of j.pause.fields) {
          const li = el('li');
          li.appendChild(el('span', f.label + ' — ' + f.reason));
          if (f.approvable && f.value !== null && f.value !== undefined && String(f.value).trim() !== '') {
            const lab = el('label'); const cb = document.createElement('input'); cb.type = 'checkbox'; cb.checked = f.approved;
            cb.addEventListener('change', () => api('/approve', {job: j.id, ref: f.ref, on: cb.checked}).then(refresh));
            lab.appendChild(cb); lab.appendChild(el('span', ' approve "' + f.value + '"'));
            li.appendChild(el('span', ' ')); li.appendChild(lab);
          }
          ul.appendChild(li);
        }
        body.appendChild(ul);
      }
      const actions = el('div', undefined, 'row-actions');
      if (j.pause.wants_code) {
        const inp = document.createElement('input'); inp.placeholder = 'verification code'; inp.autocomplete = 'off';
        const send = el('button', 'Send code');
        send.addEventListener('click', () => api('/code', {job: j.id, code: inp.value}).then(refresh));
        actions.appendChild(inp); actions.appendChild(send);
        if (j.code_given) actions.appendChild(el('span', 'code received', 'muted'));
      }
      if (j.pause.kind === 'closed') {
        const close = el('button', 'Close this job');
        close.addEventListener('click', () => api('/close-job', {job: j.id}).then(refresh));
        actions.appendChild(close);
      } else {
        const cont = el('button', 'Continue', 'primary');
        cont.addEventListener('click', () => api('/continue', {job: j.id}).then(refresh));
        actions.appendChild(cont);
      }
      body.appendChild(actions);
    }
    const learnedH = el('h3', 'What it learned'); const learned = el('ul'); learned.appendChild(el('li', 'loading…', 'muted'));
    body.appendChild(learnedH); body.appendChild(learned);
    body.appendChild(el('h3', 'Log'));
    const logBox = el('div', undefined, 'joblog'); logBox.appendChild(el('div', 'loading…', 'muted')); body.appendChild(logBox);
    fetch('/log?job=' + encodeURIComponent(j.id), {headers: {'X-Token': TOKEN}}).then(r => r.json()).then(r => {
      const lines = (r && r.ok && r.lines) ? r.lines : [];
      logBox.replaceChildren(); learned.replaceChildren();
      if (!lines.length) logBox.appendChild(el('div', r && r.empty ? 'no log yet' : '(empty)', 'muted'));
      for (const line of lines) logBox.appendChild(el('div', line));
      const got = lines.filter(l => l.startsWith('learned '));
      if (!got.length) learned.appendChild(el('li', 'nothing yet', 'muted'));
      for (const l of got) learned.appendChild(el('li', l));
    }).catch(() => {});
  };
  const render = state => {
    const on = state.auto_submit === true;
    const sw = document.getElementById('switch'); sw.className = on ? 'on' : 'off';
    document.getElementById('toggle').textContent = on ? 'Turn auto-submit OFF' : 'Turn auto-submit ON';
    document.getElementById('switch-text').textContent = on
        ? 'Auto-submit: ON — the agent submits when the gate passes' : 'Auto-submit: OFF — you press Submit';
    document.getElementById('warning').hidden = !on;
    const list = document.getElementById('jobs-list'); list.replaceChildren();
    for (const j of state.jobs) {
      const cls = chipClass(j);
      const card = el('div', undefined, 'job ' + cls); card.dataset.job = j.id;
      const head = el('div', undefined, 'head');
      head.appendChild(el('span', j.company || shortUrl(j.url), 'name'));
      head.appendChild(el('span', j.role || ''));
      head.appendChild(el('span', j.portal || '', 'muted'));
      head.appendChild(el('span', j.short_status + (j.dry && j.short_status !== 'Dry run' ? ' (dry)' : ''), 'chip ' + cls));
      head.appendChild(el('div', j.summary || '', 'sum'));
      const body = el('div', undefined, 'body'); body.hidden = !open.has(j.id);
      head.addEventListener('click', ev => {
        if (ev.target.closest('button, input, label')) return;
        if (open.has(j.id)) { open.delete(j.id); body.hidden = true; } else { open.add(j.id); drawBody(j, body); body.hidden = false; }
      });
      if (!body.hidden) drawBody(j, body);
      card.appendChild(head); card.appendChild(body); list.appendChild(card);
      if (j.pause) { if (!seenNeedsYou.has(j.id)) { seenNeedsYou.add(j.id); chime(880); } } else seenNeedsYou.delete(j.id);
      if (j.status === 'failed') { if (!seenFailed.has(j.id)) { seenFailed.add(j.id); chime(440);
          toast((j.company || shortUrl(j.url)) + ' broke: ' + (j.summary || j.reason || 'failed') + ' (click to dismiss)'); } }
      else seenFailed.delete(j.id);
    }
    document.getElementById('notice').textContent = (state.browser_open ? 'browser open' : 'browser closed') + (state.notice ? ' · ' + state.notice : '');
  };
  const refresh = () => fetch('/state', {headers: {'X-Token': TOKEN}}).then(r => r.json()).then(render).catch(() => {});
  // --- the Memory tab: text nodes only; every change goes through the token-checked routes ---
  const GROUPS = [['choice', 'Choice mappings', 'an option you picked for a profile line on this exact question'],
                  ['candidate', 'Profile candidates', 'typed text that differs from your profile; Save to profile writes it'],
                  ['legal', 'Legal & EEO', 'your own answers, reused only on the identical question; edit by hand here or delete'],
                  ['portal', 'Portal answers', 'answers with no profile line; free text and per-company ones at the same host only']];
  let memoryEntries = [];
  const drawMemory = () => {
    const q = (document.getElementById('memory-search').value || '').toLowerCase();
    const box = document.getElementById('memory-groups'); box.replaceChildren();
    const hit = e => !q || [e.question, e.answer, e.host, e.portal, e.line || ''].some(s => String(s).toLowerCase().includes(q));
    let shown = 0;
    for (const [key, title, blurb] of GROUPS) {
      const entries = memoryEntries.filter(e => e.group === key && hit(e));
      const g = el('div', undefined, 'mgroup');
      g.appendChild(el('h2', title + ' (' + entries.length + ')'));
      g.appendChild(el('div', blurb, 'muted'));
      for (const e of entries) {
        shown += 1;
        const d = el('div', undefined, 'mentry');
        d.appendChild(el('div', e.question, 'q'));
        const row = el('div', undefined, 'row-actions');
        const inp = document.createElement('input'); inp.className = 'answer'; inp.value = e.answer; inp.autocomplete = 'off';
        const save = el('button', 'Save edit');
        save.addEventListener('click', () => api('/memory/edit', {id: e.id, answer: inp.value}).then(loadMemory));
        const del = el('button', 'Delete');
        del.addEventListener('click', () => { if (confirm('Forget this answer?')) api('/memory/delete', {id: e.id}).then(loadMemory); });
        row.appendChild(inp); row.appendChild(save); row.appendChild(del);
        if (e.can_save) {
          const sp = el('button', 'Save to profile');
          sp.addEventListener('click', () => { if (confirm('Write "' + inp.value + '" to ' + e.line + ' in profile.yaml?')) api('/memory/save', {id: e.id}).then(r => { if (r && r.error) alert(r.error); loadMemory(); refresh(); }); });
          row.appendChild(sp);
        }
        d.appendChild(row);
        const meta = [e.type || 'field', e.options.length ? e.options.length + ' options' : 'typed',
                      e.scope === 'everywhere' ? 'any site' : 'only at ' + e.scope,
                      'seen ' + e.seen + ', used ' + e.uses, 'last ' + e.last_seen];
        if (e.line) meta.push((e.group === 'candidate' ? 'profile line ' + e.line + ' now holds "' + (e.line_value || '') + '"' : 'profile line ' + e.line));
        d.appendChild(el('div', meta.join(' · '), 'meta'));
        if (e.group === 'legal') d.appendChild(el('div', 'A legal, EEO or pay answer: reused only when the question and its options are identical. Never sent to a model.', 'legal'));
        g.appendChild(d);
      }
      box.appendChild(g);
    }
    if (!shown) box.appendChild(el('p', memoryEntries.length ? 'nothing matches' : 'nothing learned yet', 'muted'));
  };
  const loadMemory = () => fetch('/memory', {headers: {'X-Token': TOKEN}}).then(r => r.json()).then(r => { memoryEntries = (r && r.ok && r.entries) ? r.entries : []; drawMemory(); }).catch(() => {});
  const showTab = which => {
    document.getElementById('jobs').hidden = which !== 'jobs';
    document.getElementById('memory').hidden = which !== 'memory';
    document.getElementById('tab-jobs').className = which === 'jobs' ? 'active' : '';
    document.getElementById('tab-memory').className = which === 'memory' ? 'active' : '';
    if (which === 'memory') loadMemory();
  };
  document.getElementById('tab-jobs').addEventListener('click', () => showTab('jobs'));
  document.getElementById('tab-memory').addEventListener('click', () => showTab('memory'));
  document.getElementById('memory-search').addEventListener('input', drawMemory);
  document.getElementById('start').addEventListener('click', () => {
    const links = document.getElementById('links').value.split(/\\r?\\n/).map(s => s.trim()).filter(Boolean);
    api('/start', {links, dry: document.getElementById('dry').checked}).then(() => { document.getElementById('links').value = ''; refresh(); });
  });
  document.getElementById('google').addEventListener('click', () => api('/google-login').then(refresh));
  document.getElementById('toggle').addEventListener('click', () => {
    const on = document.getElementById('switch').className === 'on';
    if (!on && !confirm('Turn auto-submit ON? The agent will press Submit itself whenever the gate passes.')) return;
    api('/auto-submit', {on: !on}).then(refresh);
  });
  document.getElementById('close').addEventListener('click', () => api('/close-browser', {confirm: false}).then(r => {
    if (r && r.ok === false && r.waiting) { if (confirm(r.waiting + ' job(s) are waiting for you. Close anyway?')) api('/close-browser', {confirm: true}).then(refresh); }
    else refresh(); }));
  refresh(); setInterval(refresh, 2000);
})();
</script>
</body></html>
"""


class Server:
    """The loopback server: Host, Origin and token checks on every request."""

    def __init__(self, state: State, host: str = "127.0.0.1", port: int = 0):
        self.state = state
        self.token = secrets.token_urlsafe(24)
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):          # nothing from here reaches a log
                pass

            # Host must be exactly ours; an Origin, if sent, must be ours too;
            # no CORS headers are ever sent, so a foreign page gets nothing.
            def _allowed(self, needs_token: bool) -> bool:
                if self.headers.get("Host", "") not in server.hosts:
                    self._deny(HTTPStatus.FORBIDDEN, "bad host"); return False
                origin = self.headers.get("Origin")
                if origin is not None and origin not in server.origins:
                    self._deny(HTTPStatus.FORBIDDEN, "bad origin"); return False
                if needs_token and not secrets.compare_digest(
                        self.headers.get("X-Token", ""), server.token):
                    self._deny(HTTPStatus.FORBIDDEN, "bad token"); return False
                return True

            def _deny(self, status, why):
                body = json.dumps({"ok": False, "error": why}).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers(); self.wfile.write(body)

            def _json(self, obj, status=HTTPStatus.OK):
                body = json.dumps(obj).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers(); self.wfile.write(body)

            def do_GET(self):
                path = urlparse(self.path).path
                if path == "/":
                    if not self._allowed(False):
                        return
                    nonce = secrets.token_urlsafe(16)
                    page = (_PAGE % {"nonce": nonce, "token": json.dumps(server.token)}).encode()
                    self.send_response(HTTPStatus.OK)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Security-Policy",
                                     f"default-src 'none'; script-src 'nonce-{nonce}'; "
                                     f"style-src 'nonce-{nonce}'; connect-src 'self'; "
                                     f"img-src 'none'; base-uri 'none'; form-action 'none'")
                    self.send_header("Referrer-Policy", "no-referrer")
                    self.send_header("X-Content-Type-Options", "nosniff")
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("Content-Length", str(len(page)))
                    self.end_headers(); self.wfile.write(page)
                    return
                if path == "/state":
                    if self._allowed(True):
                        self._json(server.state.snapshot())
                    return
                if path == "/log":
                    if self._allowed(True):
                        job_id = parse_qs(urlparse(self.path).query).get("job", [""])[0]
                        self._json(server.state.job_log(str(job_id)[:64]))
                    return
                if path == "/memory":
                    if self._allowed(True):
                        self._json(server.state.memory_view())
                    return
                self._deny(HTTPStatus.NOT_FOUND, "no such page")

            def do_POST(self):
                path = urlparse(self.path).path
                if not self._allowed(True):
                    return
                try:
                    length = max(0, min(int(self.headers.get("Content-Length") or 0), 1_000_000))
                    body = json.loads(self.rfile.read(length) or b"{}")
                    if not isinstance(body, dict):
                        raise ValueError
                except Exception:
                    self._deny(HTTPStatus.BAD_REQUEST, "bad body"); return
                st = server.state
                if path == "/start":
                    links = body.get("links")
                    if not isinstance(links, list) or not all(isinstance(x, str) for x in links):
                        self._deny(HTTPStatus.BAD_REQUEST, "links must be a list"); return
                    ids = st.start([x for x in links if x.startswith(("http://", "https://"))][:200],
                                   bool(body.get("dry")))
                    self._json({"ok": True, "started": ids}); return
                if path == "/approve":
                    ok = st.approve(str(body.get("job", "")), str(body.get("ref", "")),
                                    bool(body.get("on", True)))
                    self._json({"ok": ok}); return
                if path == "/code":
                    ok = st.give_code(str(body.get("job", "")), str(body.get("code", "")))
                    self._json({"ok": ok}); return
                if path == "/continue":
                    self._json({"ok": st.request_continue(str(body.get("job", "")))}); return
                if path == "/close-job":
                    self._json({"ok": st.close_job(str(body.get("job", "")))}); return
                if path == "/close-browser":
                    self._json(st.request_close(bool(body.get("confirm")))); return
                if path == "/google-login":
                    st.request_google_login(); self._json({"ok": True}); return
                if path == "/auto-submit":
                    if not isinstance(body.get("on"), bool):
                        self._deny(HTTPStatus.BAD_REQUEST, "on must be true or false"); return
                    self._json({"ok": True, "auto_submit": st.set_auto_submit(body["on"])}); return
                if path == "/memory/edit":
                    self._json(st.memory_edit(str(body.get("id", "")), body.get("answer"))); return
                if path == "/memory/delete":
                    self._json(st.memory_delete(str(body.get("id", "")))); return
                if path == "/memory/save":
                    self._json(st.save_to_profile(str(body.get("id", "")))); return
                self._deny(HTTPStatus.NOT_FOUND, "no such action")

        self.httpd = ThreadingHTTPServer((host, port), Handler)
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]
        self.hosts = {f"127.0.0.1:{self.port}", f"localhost:{self.port}"}
        self.origins = {f"http://127.0.0.1:{self.port}", f"http://localhost:{self.port}"}
        self.url = f"http://127.0.0.1:{self.port}/"
        self._thread: threading.Thread | None = None

    def start(self) -> "Server":
        self._thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        try:
            self.httpd.shutdown()
            self.httpd.server_close()
        except Exception:
            pass
