"""
test_auto_submit.py — the auto-submit switch: OFF parks ready, ON submits as before.

WHAT THIS DOES: Runs the wizard on the mock portals with the switch
read through a callable (Jack, 2026-10-29). OFF on a page with a Submit
control and no Next: nothing is pressed or ticked, the job parks as
"ready" with the full-record verdict, the tab stays open, one gate_runs
row says so (agent_submitted 0), and a single-page form with a gate stop
gives exactly one pause, of kind ready, carrying the reasons. A switch
flipped OFF after the job started submits nothing. ON: submits as on
f147f5c, with submitted_by "agent". The GUI's /auto-submit endpoint
refuses a missing token, a foreign Origin, a wrong Host and a non-bool
body, and every State starts OFF. Continue on a ready row after Jack
pressed Submit himself marks the job submitted by him and updates the
park's own gate_runs row (capture "confirmation", no second row);
Continue after he moved the tab to another form fills that form.
WHY IT MATTERS: With the switch OFF no code path may press Submit; that
is what lets Jack run a dozen real links with every application ending
in his hands.

Run from anywhere:  .venv/Scripts/python.exe tests/test_auto_submit.py
"""
import http.client
import io
import json
from argparse import Namespace
from contextlib import redirect_stdout
from unittest import mock

from _fill_run import PROFILE, _headless, classify_by_label, map_from_profile
from _harness import TEST_HOME, Checks, run_as_script
from agent import gui, ledger, pause, tracker, wizard
from test_resume import SINGLE, _drive, _gui_site
from test_wizard import HOST, _profile, _routes, _run, _site

OFF, ON = (lambda: False), (lambda: True)

# The single-page form with a required City the profile can't answer: a
# gate stop on the page that is also the final page.
SINGLE_STOP = SINGLE.replace('<button type="submit">Submit application</button>',
                             '<label for="city">City</label><input id="city" name="city" required>'
                             '<button type="submit">Submit application</button>')


def _loop_once(state, waiting, ctx, profile_path, patches=()):
    """One pass of the GUI queue loop with the model stubbed, quietly."""
    import apply
    args = Namespace(profile=str(profile_path), resume=None, dry=False)
    quiet = pause.Pauser(notify_fn=lambda t, body, echo=print: None, echo=lambda *x, **k: None)
    with mock.patch.object(apply.classify, "classify_fields", classify_by_label), \
            mock.patch.object(apply.mapper, "map_fields", map_from_profile), \
            mock.patch.object(apply.report, "open_in_browser", lambda path: None), \
            redirect_stdout(io.StringIO()):
        apply.gui_loop(ctx, state, apply.Profile.load(profile_path), args, quiet,
                       idle_limit_s=1.0, waiting=waiting)


def _gui_run(start, name):
    """
    A GUI-mode queue with the switch OFF, started on one link; returns
    what a test needs. Each test starts its own address (the tracker and
    gate_runs are keyed by URL and shared across the test process).
    """
    profile_path = TEST_HOME / f"auto-submit-{name}.yaml"
    profile_path.write_text(PROFILE, encoding="utf-8")
    state = gui.State()
    [job_id] = state.start([f"{HOST}{start}"], dry=False)
    return profile_path, state, job_id, {}


def test_switch_off_parks_ready_with_nothing_pressed_and_a_gate_runs_row():
    # Fails on 62f6488: the single page was submitted by the agent (status
    # submitted, "Submit application" pressed, agent_submitted 1).
    check = Checks()
    profile_path, state, a, waiting = _gui_run("/single", "ready")
    url = f"{HOST}/single"
    with _headless(_routes(_gui_site())) as ctx:
        ctx.add_init_script(ledger.ACTIVITY_INIT_SCRIPT)
        _loop_once(state, waiting, ctx, profile_path)
        job = state.snapshot()["jobs"][0]
        tabs = [p for p in ctx.pages if p.url.endswith("/single")]
        clicks = tabs[0].evaluate("window.__clicks") if tabs else None
        first_name = tabs[0].input_value("#fn") if tabs else None
    check(state.auto_submit is False and state.snapshot()["auto_submit"] is False, "a new State starts OFF")
    check(job["status"] == "needs_you" and job["pause"] and job["pause"]["kind"] == "ready",
          f"the job parks as ready ({job['status']}, {job['pause'] and job['pause']['kind']})")
    check(wizard.READY_FILLED in job["pause"]["message"] and "the gate would submit" in job["pause"]["message"],
          f"the row carries the wording and the verdict ({job['pause']['message'][:120]})")
    check(len(tabs) == 1 and clicks == [] and first_name == "Test" and a in waiting,
          f"the tab is open, filled, with nothing pressed ({len(tabs)}, {clicks}, {first_name!r})")
    rows = [r for r in tracker.gate_runs() if r["application_url"] == url]
    check(len(rows) == 1 and rows[0]["verdict"] == "would_submit" and rows[0]["agent_submitted"] == 0
          and rows[0]["submitted_by"] is None,
          f"one gate_runs row: the verdict, not agent-submitted ({[(r['verdict'], r['agent_submitted']) for r in rows]})")
    app = tracker.find(url)
    check(app and app["status"] == "needs_you" and wizard.READY_FILLED in (app["notes"] or ""),
          f"the tracker row waits as needs-you with the ready reason ({app and app['status']})")
    check.done()


def test_switch_off_ticks_no_consent_box_on_the_final_page():
    # Fails on 62f6488: the required terms box on page three was ticked
    # and Submit pressed.
    check = Checks()

    def jack_types_the_city(page, state, job_id):
        page.fill("#city", "Springfield")
    out, pauses, url, clicks, w = _drive(_gui_site(), "/apply", [jack_types_the_city, lambda *a: None],
                                         max_pauses=3, auto_submit=OFF)
    kinds = [p["pause"]["kind"] for p in pauses]
    check(kinds == ["gate", "ready", "ready"], f"the city stop, then ready, then still ready ({kinds})")
    check(out.status == wizard.WAITING and url.endswith("/apply/3") and clicks == ["Next", "Next"],
          f"left on the final page with nothing pressed there ({out.status}, {url}, {clicks})")
    check("planned to tick 1 consent box" in pauses[1]["pause"]["message"],
          f"the row says the gate planned a tick Jack must make himself ({pauses[1]['pause']['message'][:160]})")
    check(all(not f["approvable"] and f["value"] is None for f in pauses[1]["pause"]["fields"]),
          "a ready row offers no approve tick")
    check.done()


def test_switch_flipped_off_after_the_job_started_submits_nothing():
    # Fails on 62f6488 (no switch: the run submitted). The switch reads ON
    # until Jack flips it at the page-two pause; the final page then parks.
    check = Checks()
    flag = {"on": True}

    def jack_types_the_city_and_flips_the_switch(page, body):
        if "City" in body:
            page.fill("#city", "Springfield")
            flag["on"] = False
    out, jack, clicks, ticks, url = _run(_site(), "/apply", jack_types_the_city_and_flips_the_switch,
                                         auto_submit=lambda: flag["on"])
    check(out.status == "needs_you" and out.reason.startswith("ready: " + wizard.READY_FILLED),
          f"the final page parked as ready ({out.status}, {out.reason[:100]})")
    check(clicks == ["Next", "Next"] and url.endswith("/apply/3") and dict(ticks).get("tos") is not True,
          f"Next twice, no Submit, no tick ({clicks}, {url}, {ticks})")
    check(out.submitted_by is None and out.gate_result and out.gate_result["content_verdict"] == "would_submit",
          "the verdict is recorded; nobody submitted")
    check(len(jack.bodies) == 2 and "press Submit yourself" in jack.bodies[1],
          f"the terminal saw the city pause and the ready pause ({jack.bodies})")
    check.done()


def test_switch_on_submits_as_before():
    # On 62f6488 the kwarg did not exist; the submitted_by mark is new.
    check = Checks()

    def jack_types_the_city(page, body):
        if "City" in body:
            page.fill("#city", "Springfield")
    out, jack, clicks, ticks, url = _run(_site(), "/apply", jack_types_the_city, auto_submit=ON)
    check(out.status == "submitted" and url.endswith("/thanks") and clicks == ["Next", "Next", "Submit application"],
          f"ON: filled, stepped, submitted ({out.status}, {url}, {clicks})")
    check(out.submitted_by == "agent", f"the agent's press is marked as its own ({out.submitted_by})")
    check.done()


def test_off_a_single_page_with_a_gate_stop_gives_one_pause_of_kind_ready():
    # Jack's addition (2026-10-29): one stop per job. Fails on 62f6488: the
    # first pause was the per-page gate stop (kind "gate").
    check = Checks()
    site = _gui_site(); site["/stop"] = SINGLE_STOP
    out, pauses, url, clicks, w = _drive(site, "/stop", [lambda *a: None], max_pauses=1, auto_submit=OFF)
    kinds = [p["pause"]["kind"] for p in pauses]
    check(kinds == ["ready"], f"exactly one pause, of kind ready ({kinds})")
    msg = pauses[0]["pause"]["message"]
    check("the gate would stop" in msg and "City" in msg and "required, but empty" in msg,
          f"it carries the gate's reasons ({msg[:160]})")
    check(any(f["label"] == "City" for f in pauses[0]["pause"]["fields"]), "the stopped field is listed")
    check(clicks == [] and url.endswith("/stop"), f"nothing pressed ({clicks}, {url})")
    check.done()


def _req(server, method, path, body=None, host=None, origin=None, token="ok"):
    conn = http.client.HTTPConnection("127.0.0.1", server.port, timeout=30)
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
    return resp.status, parsed


def test_the_endpoint_refuses_a_missing_token_foreign_origin_or_wrong_host():
    # Fails on 62f6488: /auto-submit was "no such action" (404) for every call.
    check = Checks()
    state = gui.State()
    server = gui.Server(state).start()
    try:
        status, body = _req(server, "POST", "/auto-submit", {"on": True}, token=None)
        check(status == 403 and state.auto_submit is False, f"no token: refused ({status})")
        status, body = _req(server, "POST", "/auto-submit", {"on": True}, token="wrong")
        check(status == 403 and state.auto_submit is False, f"wrong token: refused ({status})")
        status, body = _req(server, "POST", "/auto-submit", {"on": True}, origin="http://evil.example")
        check(status == 403 and state.auto_submit is False, f"foreign Origin: refused ({status})")
        status, body = _req(server, "POST", "/auto-submit", {"on": True}, host="evil.example")
        check(status == 403 and state.auto_submit is False, f"wrong Host: refused ({status})")
        status, body = _req(server, "POST", "/auto-submit", {"on": "yes"})
        check(status == 400 and state.auto_submit is False, f"a non-bool body: refused ({status})")
        status, body = _req(server, "POST", "/auto-submit", {"on": True},
                            origin=f"http://127.0.0.1:{server.port}")
        check(status == 200 and body.get("auto_submit") is True and state.auto_submit_on() is True,
              f"the page's own request turns it ON ({status}, {body})")
        status, body = _req(server, "GET", "/state")
        check(status == 200 and body.get("auto_submit") is True, "/state shows the switch")
        status, body = _req(server, "POST", "/auto-submit", {"on": False})
        check(status == 200 and state.auto_submit_on() is False, "and OFF again")
        status, body = _req(server, "GET", "/")
        check(status == 200 and "Turn auto-submit ON" in body and "Auto-submit: OFF" in body,
              "the page carries the toggle and its OFF wording")
    finally:
        server.stop()
    check.done()


def test_continue_after_a_confirmation_marks_submitted_by_jack():
    # Fails on 62f6488 (no ready park: the agent submitted). Jack presses
    # Submit in the tab himself; Continue sees the confirmation.
    check = Checks()
    profile_path, state, a, waiting = _gui_run("/single-confirm", "confirm")
    url = f"{HOST}/single-confirm"
    site = _gui_site(); site["/single-confirm"] = SINGLE
    with _headless(_routes(site)) as ctx:
        ctx.add_init_script(ledger.ACTIVITY_INIT_SCRIPT)
        _loop_once(state, waiting, ctx, profile_path)
        job = state.snapshot()["jobs"][0]
        check(job["pause"] and job["pause"]["kind"] == "ready", "parked ready first")
        tab = next(p for p in ctx.pages if p.url.endswith("/single-confirm"))
        tab.click("button[type=submit]")                  # Jack's own press
        tab.wait_for_url("**/thanks")
        state.request_continue(a)
        _loop_once(state, waiting, ctx, profile_path)
        job = state.snapshot()["jobs"][0]
    check(job["status"] == "submitted" and "confirmation seen" in job["reason"] and not waiting,
          f"Continue marked it submitted by Jack ({job['status']}, {job['reason']})")
    app = tracker.find(url)
    check(app and app["status"] == "submitted" and app["agent_submitted"] == 0 and app["submitted_at"],
          f"the tracker row: submitted, not by the agent, dated ({app and (app['status'], app['agent_submitted'])})")
    rows = [r for r in tracker.gate_runs() if r["application_url"] == url]
    check(len(rows) == 1, f"the ready park's own row, no second one ({len(rows)})")
    row = rows[0] if rows else {}
    check(row.get("submitted_by") == "jack" and row.get("capture") == "confirmation"
          and row.get("agent_submitted") == 0 and row.get("human_submitted") == "unknown"
          and "jack_submit" in (row.get("success_signal") or ""),
          f"recorded as confirmation seen, submitted by Jack, not his answer ({dict((k, row.get(k)) for k in ('submitted_by', 'capture', 'agent_submitted', 'human_submitted'))})")
    check.done()


def test_continue_after_the_tab_moved_to_a_new_form_fills_it():
    # Fails on 62f6488 (no ready park). Jack moves the ready tab to another
    # form himself; Continue fills it, presses Next and stops on page two.
    check = Checks()
    profile_path, state, a, waiting = _gui_run("/single-moved", "moved")
    site = _gui_site(); site["/single-moved"] = SINGLE
    with _headless(_routes(site)) as ctx:
        ctx.add_init_script(ledger.ACTIVITY_INIT_SCRIPT)
        _loop_once(state, waiting, ctx, profile_path)
        tab = next(p for p in ctx.pages if p.url.endswith("/single-moved"))
        tab.goto(f"{HOST}/apply")                        # Jack moves on
        state.request_continue(a)
        _loop_once(state, waiting, ctx, profile_path)
        job = state.snapshot()["jobs"][0]
        where, clicks = tab.url, tab.evaluate("window.__clicks")
        kept = waiting.get(a)
        held_log = [line for line in (kept[0].out.log if kept else []) if "boxes holding a value are held" in line]
        check(job["status"] == "needs_you" and job["pause"] and job["pause"]["kind"] == "gate"
              and "City" in job["pause"]["message"],
              f"the new form was filled and stopped on its own page two ({job['status']}, {job['pause'] and job['pause']['kind']})")
        check(where.endswith("/apply/2") and clicks == ["Next"] and held_log,
              f"page one filled under the hold, Next pressed, no Submit ({where}, {clicks}, {held_log})")
        check(kept is not None and kept[0].jack_submits is True, "the job stays Jack's to submit")
        # Jack answers the city; the run reaches page three, another final
        # page: it parks ready again (never submits), and the ready park he
        # ended is his, not a stop on the full record.
        tab.fill("#city", "Springfield")
        state.request_continue(a)
        _loop_once(state, waiting, ctx, profile_path)
        job = state.snapshot()["jobs"][0]
        where, clicks = tab.url, tab.evaluate("window.__clicks")
    msg = job["pause"]["message"] if job["pause"] else ""
    check(job["pause"] and job["pause"]["kind"] == "ready" and where.endswith("/apply/3")
          and clicks == ["Next", "Next"] and "the gate would submit" in msg,
          f"the second final page parks ready with a clean verdict ({job['pause'] and job['pause']['kind']}, {where}, {clicks}, {msg[:160]})")
    check("run: ready" not in msg, f"the ready park Jack ended is not a stop ({msg[:200]})")
    rows = [r for r in tracker.gate_runs() if r["application_url"] == f"{HOST}/single-moved"]
    check(len(rows) == 2 and [r["verdict"] for r in rows] == ["would_submit", "would_submit"]
          and all(r["agent_submitted"] == 0 for r in rows),
          f"one gate_runs row per ready park, neither agent-submitted ({[(r['verdict'], r['agent_submitted']) for r in rows]})")
    check.done()


def test_a_ready_row_never_auto_submits_even_once_the_switch_is_on():
    # Fails on 62f6488 (no switch). Parked ready with the switch OFF; the
    # switch is turned ON before Continue; the job stays ready and nothing
    # is pressed.
    check = Checks()
    flag = {"on": False}

    def jack_turns_it_on(page, state, job_id):
        flag["on"] = True
    out, pauses, url, clicks, w = _drive(_gui_site(), "/single", [jack_turns_it_on], max_pauses=2,
                                         auto_submit=lambda: flag["on"])
    kinds = [p["pause"]["kind"] for p in pauses]
    check(kinds == ["ready", "ready"] and clicks == [] and url.endswith("/single"),
          f"still ready after the switch went ON; nothing pressed ({kinds}, {clicks}, {url})")
    check.done()


# A final page whose own prose carries a confirmation sentence while the
# form is still open and unsubmitted.
PROSE = SINGLE.replace('<form id="f">', '<p>Thank you for applying! Press Submit to finish.</p><form id="f">')


def test_a_confirmation_sentence_in_the_prose_of_the_open_form_never_counts_once_other_jobs_ran():
    # Fix of cacfd8a's BLOCK. Job A parks ready on a form whose prose says
    # "Thank you for applying"; job B runs next, so A's refs are stale;
    # Continue on A with its page unchanged must leave it ready. On
    # cacfd8a the empty ref list read as "form gone" and A was marked
    # submitted by Jack.
    check = Checks()
    profile_path = TEST_HOME / "auto-submit-prose.yaml"
    profile_path.write_text(PROFILE, encoding="utf-8")
    state = gui.State()
    a, b = state.start([f"{HOST}/prose", f"{HOST}/single-b"], dry=False)
    waiting: dict = {}
    site = _gui_site(); site["/prose"], site["/single-b"] = PROSE, SINGLE
    with _headless(_routes(site)) as ctx:
        ctx.add_init_script(ledger.ACTIVITY_INIT_SCRIPT)
        _loop_once(state, waiting, ctx, profile_path)
        jobs = {j["id"]: j for j in state.snapshot()["jobs"]}
        check(all(jobs[j]["pause"] and jobs[j]["pause"]["kind"] == "ready" for j in (a, b)),
              f"both parked ready, B's scan after A's ({[(jobs[j]['status'], jobs[j]['pause'] and jobs[j]['pause']['kind']) for j in (a, b)]})")
        state.request_continue(a)
        _loop_once(state, waiting, ctx, profile_path)
        jobs = {j["id"]: j for j in state.snapshot()["jobs"]}
        tab = next(p for p in ctx.pages if p.url.endswith("/prose"))
        clicks, where, still_kept = tab.evaluate("window.__clicks"), tab.url, a in waiting
        # Now Jack really presses Submit: the confirmation still counts
        # with stale refs, since the page's structure changed since the park.
        tab.click("button[type=submit]")
        tab.wait_for_url("**/thanks")
        state.request_continue(a)
        _loop_once(state, waiting, ctx, profile_path)
        after = {j["id"]: j for j in state.snapshot()["jobs"]}
    check(jobs[a]["status"] == "needs_you" and jobs[a]["pause"] and jobs[a]["pause"]["kind"] == "ready"
          and still_kept and clicks == [] and where.endswith("/prose"),
          f"A stays ready: the prose is not a confirmation ({jobs[a]['status']}, {jobs[a]['reason'][:80]})")
    check(after[a]["status"] == "submitted" and "confirmation seen" in after[a]["reason"] and a not in waiting,
          f"after Jack's own press the confirmation counts ({after[a]['status']}, {after[a]['reason']})")
    row = tracker.find(f"{HOST}/prose")
    check(row and row["status"] == "submitted" and row["agent_submitted"] == 0, "logged as submitted, not by the agent")
    check.done()


def test_an_empty_ref_list_is_never_form_gone():
    # Fix of cacfd8a's BLOCK, at the probe itself: with no current refs
    # the form is unknown, never gone, so the generic sentence alone
    # confirms nothing. On cacfd8a `[].some(...)` made it "gone".
    from _harness import mock_page
    from agent import outcome, submit as submit_mod
    check = Checks()
    page_path = TEST_HOME / "prose.html"
    page_path.write_text(PROSE.replace("{CLICKS_JS}", ""), encoding="utf-8")
    with mock_page(page_path) as page:
        confirmed, evidence = submit_mod.confirmation(page, portal="generic", portal_method="default",
                                                      start_url=page.url, refs=[])
        check(confirmed is False and evidence["generic_text"] is True and evidence["form_gone"] is False,
              f"the sentence is seen, the form is not gone, nothing is confirmed ({evidence})")
        det = outcome.detect_success(page, portal="greenhouse", portal_method="host", start_url=page.url, refs=[])
        check(det["signals"].get("form_gone") is False and det["result"] != "match",
              f"the portal signature's form_gone signal is False on no refs ({det})")
        confirmed, evidence = submit_mod.confirmation(page, portal="generic", portal_method="default",
                                                      start_url=page.url, refs=[], form_gone=True)
        check(confirmed is True and evidence["form_gone"] is True,
              "a caller that knows the form is gone says so, and the sentence then confirms")
    check.done()


if __name__ == "__main__":
    run_as_script(globals())
