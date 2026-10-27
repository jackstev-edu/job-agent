"""
test_gui.py — the local page: what it shows, what it refuses, what it never shows.

WHAT THIS DOES: Starts gui.Server on a loopback port with a State and
talks to it over http.client: the page comes with a strict CSP and the
token; /state needs the token; a wrong Host, a foreign Origin, a missing
token and a bad body are refused; /start queues links, /approve ticks
only approvable fields, /code only when a code is wanted, /continue only
a waiting job, /close-browser asks for confirmation while jobs wait. The
approvable test: a legal-looking field the classifier called free_text, a
checkbox, a consent and a per-company field, a password box and a field
the agent didn't fill never carry a value; an ordinary filled field does.
Then a headless browser opens the page with a label holding markup: it is
rendered as text, nothing runs. The queue loop runs a dry job through the
GUI and a run --dry reports the landing without filling anything.
WHY IT MATTERS: The page shows untrusted text and holds the token that
changes state; it must not be reachable from other origins, and it must
never show a legal, EEO, pay or password value.

Run from anywhere:  .venv/Scripts/python.exe tests/test_gui.py
"""
import http.client
import io
import json
import re
from argparse import Namespace
from contextlib import ExitStack, redirect_stdout
from unittest import mock

from _fill_run import PROFILE, _headless, classify_by_label, map_from_profile
from _harness import TEST_HOME, Checks, run_as_script
from agent import gui, tracker

COMPANY = "https://jobs.example-co.com"


def _server():
    state = gui.State()
    server = gui.Server(state).start()
    return state, server


def _req(server, method, path, body=None, host=None, origin=None, token="ok"):
    conn = http.client.HTTPConnection("127.0.0.1", server.port, timeout=5)
    headers = {"Host": host or f"127.0.0.1:{server.port}"}
    if origin is not None:
        headers["Origin"] = origin
    if token == "ok":
        headers["X-Token"] = server.token
    elif token:
        headers["X-Token"] = token
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    conn.request(method, path, body=data, headers=headers)
    resp = conn.getresponse()
    raw = resp.read()
    conn.close()
    try:
        parsed = json.loads(raw)
    except ValueError:
        parsed = raw.decode("utf-8", "replace")
    return resp.status, dict(resp.getheaders()), parsed


def test_close_job_is_a_route_and_a_state_change():
    # Item 4: POST /close-job closes a job whose pause is of kind "closed"
    # and nothing else; the page draws "Close this job" for that kind.
    # On f2cd52c the route did not exist.
    check = Checks()
    state, server = _server()
    try:
        [a, b] = state.start(["https://x.example/1", "https://x.example/2"], dry=False)
        state.set_pause(a, "closed", "the tab closed. Its tab is gone; close this job.")
        state.set_pause(b, "gate", "This page needs you: 'City'")
        status, _, body = _req(server, "POST", "/close-job", {"job": b})
        check(status == 200 and body.get("ok") is False, f"a gate pause can't be closed ({status}, {body})")
        status, _, body = _req(server, "POST", "/continue", {"job": a})
        check(status == 200 and body.get("ok") is False, f"a closed tab can't be continued ({status}, {body})")
        status, _, body = _req(server, "POST", "/close-job", {"job": a})
        jobs = {j["id"]: j for j in state.snapshot()["jobs"]}
        check(status == 200 and body.get("ok") is True and jobs[a]["status"] == "closed" and jobs[a]["pause"] is None,
              f"the closed tab's job is closed ({status}, {body}, {jobs[a]})")
        status, _, page = _req(server, "GET", "/")
        check(status == 200 and "Close this job" in page and "/close-job" in page, "the page draws the Close button")
    finally:
        server.stop()
    check.done()


def test_the_page_and_its_checks():
    check = Checks()
    state, server = _server()
    try:
        status, headers, page = _req(server, "GET", "/", token=None)
        csp = headers.get("Content-Security-Policy", "")
        check(status == 200 and "default-src 'none'" in csp and "script-src 'nonce-" in csp
              and "connect-src 'self'" in csp and "'unsafe-inline'" not in csp,
              f"the page comes with a strict CSP ({csp})")
        check(server.token in page and "textContent" in page and "innerHTML" not in page,
              "the page carries the token and renders only text nodes")
        check("Access-Control-Allow-Origin" not in headers, "no CORS header")
        check(_req(server, "GET", "/state", token=None)[0] == 403, "/state without the token: refused")
        check(_req(server, "GET", "/state", token="wrong")[0] == 403, "/state with a wrong token: refused")
        check(_req(server, "GET", "/state")[0] == 200, "/state with the token: served")
        check(_req(server, "GET", "/state", host="evil.example:80")[0] == 403,
              "a foreign Host (DNS rebinding): refused")
        check(_req(server, "GET", "/", host=f"localhost:{server.port}", token=None)[0] == 200,
              "localhost:<port> is a good Host too")
        check(_req(server, "POST", "/start", {"links": ["https://a.example/1"]},
                   origin="https://evil.example")[0] == 403, "a foreign Origin: refused")
        check(_req(server, "POST", "/start", {"links": ["https://a.example/1"]},
                   token=None)[0] == 403, "a POST without the token: refused")
        check(_req(server, "POST", "/start", {"links": "not a list"})[0] == 400,
              "a bad body: refused")
        status, _, got = _req(server, "POST", "/start", {"links": ["https://a.example/1",
                                                                   "javascript:alert(1)",
                                                                   "https://a.example/1"],
                                                         "dry": True},
                              origin=f"http://127.0.0.1:{server.port}")
        check(status == 200 and len(got["started"]) == 1, f"links queued once each, only http(s) ({got})")
        snap = state.snapshot()
        check(snap["jobs"][0]["status"] == "queued" and snap["jobs"][0]["dry"] is True,
              "the job is queued as a dry run")
        job_id = snap["jobs"][0]["id"]
        check(_req(server, "POST", "/continue", {"job": job_id})[2]["ok"] is False,
              "Continue on a job that isn't waiting: refused")
        check(_req(server, "POST", "/code", {"job": job_id, "code": "1"})[2]["ok"] is False,
              "a code for a job that wants none: refused")
        state.set_pause(job_id, "code", "paste the code", wants_code=True)
        check(_req(server, "POST", "/close-browser", {"confirm": False})[2]
              == {"ok": False, "waiting": 1}, "closing with a waiting job asks first")
        check(_req(server, "POST", "/code", {"job": job_id, "code": " 246810 "})[2]["ok"] is True
              and _req(server, "POST", "/continue", {"job": job_id})[2]["ok"] is True,
              "a code and Continue for the waiting job")
        resume = state.take_resume(job_id)
        check(resume == {"approved": {}, "code": "246810"} and state.take_resume(job_id) is None,
              f"the code is handed over once, stripped ({resume})")
        check(_req(server, "POST", "/close-browser", {"confirm": False})[2]["ok"] is True
              and state.close_browser, "closing with nothing waiting goes through")
        check(_req(server, "GET", "/nothing")[0] == 404 and _req(server, "POST", "/x", {})[0] == 404,
              "unknown paths: 404")
    finally:
        server.stop()
    check.done()


def test_only_ordinary_filled_fields_get_an_approve_tick():
    check = Checks()
    plain = {"ref": "f1", "type": "text", "label": "City"}
    decision = {"value": "Springfield", "source": "model"}
    filled = {"filled": True}
    cases = [
        ("an ordinary filled text field", plain, "personal_fact", decision, filled, True),
        ("a field the classifier called free_text but LEGAL_GUARD caught",
         {"ref": "f2", "type": "text", "label": "Do you require visa sponsorship? Explain"},
         "free_text", decision, filled, False),
        ("an EEO line by PROFILE_PATHS", {"ref": "f3", "type": "select", "label": "Veteran status"},
         "personal_fact", decision, filled, False),
        ("a HARD_STOPS legal pattern", {"ref": "f4", "type": "text", "label": "Have you been convicted"},
         "personal_fact", decision, filled, False),
        ("the page's raw_legal flag", {"ref": "f5", "type": "text", "label": "Box", "raw_legal": True},
         "personal_fact", decision, filled, False),
        ("a checkbox", {"ref": "f6", "type": "checkbox", "label": "Keep me posted"},
         "preference", {"value": True, "source": "model"}, filled, False),
        ("a consent category", {"ref": "f7", "type": "text", "label": "Initials"},
         "consent", decision, filled, False),
        ("a per-company category", {"ref": "f8", "type": "text", "label": "Referrer"},
         "per_company", decision, filled, False),
        ("a password box", {"ref": "f9", "type": "password", "label": "Password"},
         "credential", decision, filled, False),
        ("a secret-marked box", {"ref": "f10", "type": "text", "label": "Token", "secret": True},
         "personal_fact", decision, filled, False),
        ("a field the agent didn't fill", plain, "personal_fact", None, None, False),
        ("a field the fill failed on", plain, "personal_fact", decision, {"filled": False}, False),
        ("a field with no value", plain, "personal_fact", {"value": None}, filled, False),
        ("a human-sourced decision", plain, "personal_fact", {"value": "x", "source": "human"}, filled, False),
    ]
    for what, f, cat, dec, res, want in cases:
        check(gui.approvable(f, cat, dec, res) is want, f"{what}: {'approvable' if want else 'never'}")
    # And through the State: a non-approvable field's value never appears in /state.
    state = gui.State()
    [job_id] = state.start(["https://a.example/2"], dry=False)
    raw_ok = {"field": plain, "category": "personal_fact", "decision": decision, "result": filled}
    raw_eeo = {"field": {"ref": "f3", "type": "select", "label": "Veteran status"},
               "category": "personal_fact", "decision": decision, "result": filled}
    state.set_pause(job_id, "gate", "needs you", fields=[
        {"ref": "f1", "label": "City", "reason": "left blank", "value": "Springfield", "raw": raw_ok},
        {"ref": "f3", "label": "Veteran status", "reason": "not traced", "approvable": True,
         "value": "SENTINEL-EEO-VALUE", "raw": raw_eeo},
        {"ref": "f4", "label": "No raw", "reason": "x", "approvable": True, "value": "SENTINEL-NORAW"}])
    text = json.dumps(state.snapshot())
    check("Springfield" in text and "SENTINEL-EEO-VALUE" not in text and "SENTINEL-NORAW" not in text,
          "State judges approvability itself: the ordinary value is shown, the EEO one and a "
          "field with no raw inputs never are, whatever flag the caller passed")
    check(state.approve(job_id, "f3", True) is False and state.approve(job_id, "f1", True) is True,
          "only an approvable field can be ticked")
    state.request_continue(job_id)
    check(state.take_resume(job_id) == {"approved": {"f1": "Springfield"}, "code": None},
          "Continue hands over the ticked field with the value that was shown")
    check.done()


def test_markup_in_a_label_is_rendered_as_text():
    from playwright.sync_api import sync_playwright
    check = Checks()
    state, server = _server()
    try:
        [job_id] = state.start(["https://a.example/3"], dry=False)
        evil = '<img src=x onerror="window.__pwned=1"><script>window.__pwned=2</script>Bold <b>label</b>'
        raw = {"field": {"ref": "f1", "type": "text", "label": evil}, "category": "personal_fact",
               "decision": {"value": evil, "source": "model"}, "result": {"filled": True}}
        state.set_pause(job_id, "gate", "needs you: " + evil, fields=[
            {"ref": "f1", "label": evil, "reason": "left blank", "value": evil, "raw": raw}])
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                page = browser.new_page()
                page.goto(server.url)
                page.wait_for_timeout(1200)
                pwned = page.evaluate("window.__pwned || 0")
                text = page.inner_text("#rows")
                imgs = page.evaluate("document.querySelectorAll('#rows img, #rows script, #rows b').length")
            finally:
                browser.close()
        check(pwned == 0 and imgs == 0, f"no markup from the label ran or rendered ({pwned}, {imgs})")
        check("<img src=x onerror=" in text and "Bold <b>label</b>" in text,
              "the label shows as the literal text it is")
    finally:
        server.stop()
    check.done()


FORM = """<!doctype html><html><head><title>Engineer - Example Co</title></head><body>
  <form><label for="fn">First Name</label><input id="fn" name="first_name">
  <label for="em">Email</label><input id="em" name="email"><button type="submit">Submit application</button>
  </form><script>window.__clicks = []; document.addEventListener('click', e => {
    const b = e.target.closest('a, button'); if (b) window.__clicks.push(b.innerText.trim()); }, true);</script>
  </body></html>"""
POSTING = """<!doctype html><html><body><a href="/jobs/1/apply">Apply now</a></body></html>"""


def _routes():
    site = {f"{COMPANY}/jobs/1": POSTING, f"{COMPANY}/jobs/1/apply": FORM}

    def handle(route):
        html = site.get(route.request.url.split("?")[0])
        route.fulfill(status=200 if html else 404, content_type="text/html", body=html or "<p>404</p>")
    return {"**/*": handle}


def test_run_dry_reports_the_landing_and_fills_nothing():
    import apply
    check = Checks()
    profile_path = TEST_HOME / "gui-profile.yaml"
    profile_path.write_text(PROFILE, encoding="utf-8")
    args = Namespace(profile=str(profile_path), urls=[f"{COMPANY}/jobs/1"], queue=None, resume=None,
                     force=False, dry=True)
    out = io.StringIO()
    seen = {}
    real_report = apply.dry_report

    def spy(landing, ats, **kw):
        # Read the form's state while the browser is still open: what the
        # run left in the boxes, and what it clicked on the form page.
        seen["values"] = [landing.page.input_value("#fn"), landing.page.input_value("#em")]
        seen["clicks"] = landing.page.evaluate("window.__clicks")
        return real_report(landing, ats, **kw)
    with ExitStack() as stack:
        for target, name, value in ((apply.browser_mod, "browser", lambda: _headless(_routes())),
                                    (apply, "dry_report", spy),
                                    (apply.config, "check_api_key", lambda: ("missing", "no key")),
                                    (apply.classify, "classify_fields",
                                     mock.Mock(side_effect=AssertionError("a dry run must not classify"))),
                                    (apply.mapper, "map_fields",
                                     mock.Mock(side_effect=AssertionError("a dry run must not map")))):
            stack.enter_context(mock.patch.object(target, name, value))
        with redirect_stdout(out):
            apply.cmd_run(args)
    values, clicks = seen.get("values"), seen.get("clicks")
    text = re.sub(r"\x1b\[[0-9;]*m", "", out.getvalue())
    print(text)
    check("dry run: landed on https://jobs.example-co.com/jobs/1/apply" in text
          and "portal Unknown or custom" in text and "form with fields: yes (2 field(s) scanned)" in text
          and "clicked 'Apply now'" in text, "the landing is reported: address, portal, form, clicks")
    check("role 'Engineer' at 'Example Co'" in text, "the role and company read off the page's title are reported")
    check(values == ["", ""] and clicks == [], f"nothing filled, nothing clicked on the form ({values}, {clicks})")
    check(tracker.find(f"{COMPANY}/jobs/1") is None, "a dry run writes nothing to the tracker")
    check("dry 1" in text, "the batch summary counts it as dry")
    check.done()


def test_the_gui_loop_runs_a_dry_job_in_its_own_tab():
    import apply
    check = Checks()
    profile_path = TEST_HOME / "gui-profile2.yaml"
    profile_path.write_text(PROFILE, encoding="utf-8")
    state = gui.State()
    [job_id] = state.start([f"{COMPANY}/jobs/1"], dry=True)
    tabs = []
    with _headless(_routes()) as ctx:
        first = ctx.new_page()
        with mock.patch.object(apply.classify, "classify_fields", classify_by_label), \
                mock.patch.object(apply.mapper, "map_fields", map_from_profile), \
                redirect_stdout(io.StringIO()):
            apply.gui_loop(ctx, state, None, Namespace(profile=str(profile_path), resume=None, dry=False),
                           None, idle_limit_s=1.0)
        tabs = [p.url for p in ctx.pages]
    job = state.snapshot()["jobs"][0]
    check(job["status"] == "dry" and job["form_found"] is True and job["company"] == "Example Co"
          and job["final_url"].endswith("/jobs/1/apply"), f"the row shows the landing ({job})")
    check(len(tabs) == 2 and tabs[0] == "about:blank", f"the job ran in its own tab ({tabs})")
    check.done()


if __name__ == "__main__":
    run_as_script(globals())
