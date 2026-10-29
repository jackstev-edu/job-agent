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
from urllib.parse import urlparse

from . import gate, sensitive

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
    if not decision or decision.get("value") is None or decision.get("source") == "human":
        return False
    return bool((result or {}).get("filled"))


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
                "reason": self.reason, "final_url": self.final_url,
                "form_found": self.form_found, "progress": self.progress, "landing": landing,
                "pause": pause,
                "code_given": self.code is not None}


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
                ok = bool(raw) and approvable(raw.get("field") or {}, raw.get("category"),
                                              raw.get("decision"), raw.get("result"))
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
body { font: 15px/1.5 system-ui, sans-serif; margin: 1.5rem; color: #16202b; background: #f7f8f9; }
textarea { width: 100%%; height: 6rem; font: inherit; }
table { width: 100%%; border-collapse: collapse; background: #fff; margin-top: 1rem; }
th, td { text-align: left; padding: .4rem .6rem; border-bottom: 1px solid #d5dae0; vertical-align: top; }
tr.needs_you { background: #fff4e0; }
tr.ready { background: #e3f3e8; }
tr.ready td.status { color: #1f5e3a; font-weight: 700; }
tr.submitted td.status { color: #2f6b4f; font-weight: 600; }
tr.failed td.status { color: #9b2226; }
#switch { display: flex; gap: 1rem; align-items: center; flex-wrap: wrap; padding: .9rem 1rem; margin: .5rem 0 1rem;
          border: 3px solid #5b6875; border-radius: .5rem; background: #e9ecef; font-size: 1.25em; font-weight: 700; }
#switch.on { border-color: #9b2226; background: #fde2e4; color: #6a0f14; }
#switch button { font-size: 1em; font-weight: 700; padding: .5rem 1.2rem; }
#warning { flex-basis: 100%%; font-size: .8em; font-weight: 400; color: #6a0f14; }
.pause { margin: .4rem 0 0; padding: .5rem; border-left: 3px solid #a65a00; background: #fff; }
.pause li { margin: .2rem 0; }
button { font: inherit; padding: .3rem .8rem; }
.row-actions { display: flex; gap: .5rem; align-items: center; flex-wrap: wrap; margin-top: .4rem; }
.muted { color: #5b6875; font-size: .9em; }
</style></head>
<body>
<h1>job-agent</h1>
<div id="switch" class="off">
  <button id="toggle">Turn auto-submit ON</button>
  <span id="switch-text">Auto-submit: OFF — you press Submit</span>
  <div id="warning" hidden>While ON the agent presses Submit when the gate passes. The MUST FIX BEFORE AUTO-SUBMIT items in TODO.md are still open.</div>
</div>
<p class="muted">Paste links, one per line. The agent fills in its own browser; rows that need you turn amber and chime; a green READY row is filled and waits for you to press Submit.</p>
<textarea id="links" placeholder="https://..."></textarea>
<div class="row-actions">
  <button id="start">Start</button>
  <label><input type="checkbox" id="dry"> dry run (intake only: open, click Apply, report; fill nothing)</label>
  <button id="google">Google login</button>
  <button id="close">Close browser</button>
  <span id="notice" class="muted"></span>
</div>
<table><thead><tr><th>Company</th><th>Role</th><th>Portal</th><th>Status</th><th>Details</th></tr></thead>
<tbody id="rows"></tbody></table>
<script nonce="%(nonce)s">
(() => {
  const TOKEN = %(token)s;
  const seenNeedsYou = new Set();
  const el = (tag, text, cls) => { const e = document.createElement(tag); if (text !== undefined) e.textContent = String(text); if (cls) e.className = cls; return e; };
  const api = (path, body) => fetch(path, {method: 'POST', headers: {'Content-Type': 'application/json', 'X-Token': TOKEN},
                                            body: JSON.stringify(body || {})}).then(r => r.json()).catch(() => ({ok: false}));
  const chime = () => { try { const c = new AudioContext(); const o = c.createOscillator(); const g = c.createGain();
      o.frequency.value = 880; g.gain.value = 0.08; o.connect(g); g.connect(c.destination); o.start(); o.stop(c.currentTime + 0.35); } catch (e) {} };
  const render = state => {
    const on = state.auto_submit === true;
    const sw = document.getElementById('switch'); sw.className = on ? 'on' : 'off';
    document.getElementById('toggle').textContent = on ? 'Turn auto-submit OFF' : 'Turn auto-submit ON';
    document.getElementById('switch-text').textContent = on
        ? 'Auto-submit: ON — the agent submits when the gate passes' : 'Auto-submit: OFF — you press Submit';
    document.getElementById('warning').hidden = !on;
    const rows = document.getElementById('rows'); rows.replaceChildren();
    for (const j of state.jobs) {
      const ready = !!(j.pause && j.pause.kind === 'ready');
      const tr = el('tr', undefined, ready ? 'ready' : j.status);
      tr.appendChild(el('td', j.company || ''));
      tr.appendChild(el('td', j.role || ''));
      tr.appendChild(el('td', j.portal || ''));
      const working = j.status === 'working' && j.progress ? j.progress : j.status.replace('_', ' ');
      const st = el('td', (ready ? 'READY — press Submit yourself' : working) + (j.dry ? ' (dry)' : ''), 'status');
      tr.appendChild(st);
      const td = el('td');
      td.appendChild(el('div', j.url, 'muted'));
      if (j.reason) td.appendChild(el('div', j.reason));
      if (j.landing) td.appendChild(el('div', j.landing, 'muted'));
      if (j.pause) {
        const box = el('div', undefined, 'pause');
        box.appendChild(el('div', j.pause.message));
        if (j.pause.fields.length) {
          const ul = el('ul');
          for (const f of j.pause.fields) {
            const li = el('li');
            li.appendChild(el('span', f.label + ': ' + f.reason));
            if (f.approvable) {
              const lab = el('label'); const cb = document.createElement('input'); cb.type = 'checkbox'; cb.checked = f.approved;
              cb.addEventListener('change', () => api('/approve', {job: j.id, ref: f.ref, on: cb.checked}).then(refresh));
              lab.appendChild(cb); lab.appendChild(el('span', ' approve "' + f.value + '"'));
              li.appendChild(el('span', ' ')); li.appendChild(lab);
            }
            ul.appendChild(li);
          }
          box.appendChild(ul);
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
          const cont = el('button', 'Continue');
          cont.addEventListener('click', () => api('/continue', {job: j.id}).then(refresh));
          actions.appendChild(cont);
        }
        box.appendChild(actions);
        td.appendChild(box);
        if (!seenNeedsYou.has(j.id)) { seenNeedsYou.add(j.id); chime(); }
      } else { seenNeedsYou.delete(j.id); }
      tr.appendChild(td); rows.appendChild(tr);
    }
    document.getElementById('notice').textContent = (state.browser_open ? 'browser open' : 'browser closed') + (state.notice ? ' · ' + state.notice : '');
  };
  const refresh = () => fetch('/state', {headers: {'X-Token': TOKEN}}).then(r => r.json()).then(render).catch(() => {});
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
