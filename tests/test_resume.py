"""
test_resume.py — a pause that waits in its tab, and Continue from the GUI.

WHAT THIS DOES: Runs the wizard on the three-page mock portal in GUI mode
(a GuiPauser and a gui.State standing in for the page): a gate stop on
City comes back as "waiting" with the row showing City by label and
reason and no approve tick (the filler had no value for it); Jack types
the city in the still-open tab and presses Continue, and the run goes on
to Submit. With medium-confidence answers, the stop lists the agent's
fields with the page's read-back value and a tick: ticking both and
Continue counts them as his and the run goes on; Continue alone approves
nothing and the row waits again; a ticked field whose value the page
changed before Continue is not his and waits again, while the other
counts. A verification page waits with a code box; the code given in the
GUI is typed and the run goes on. The queue loop runs two jobs in one
browser: the first waits in its tab while the second is submitted in
another; Continue then finishes the first. Nothing shown holds a legal,
EEO, pay or password value.
WHY IT MATTERS: Jack's first real run lost a filled application when the
browser closed on a timeout. Now a job that needs him never fails and
never closes (Jack, 2026-10-19), and his Continue is the only thing that
moves it, with only his own touches and ticks counting as review.

Run from anywhere:  .venv/Scripts/python.exe tests/test_resume.py
"""
import io
from argparse import Namespace
from contextlib import redirect_stdout
from unittest import mock

from _fill_run import PROFILE, _headless, classify_by_label, map_from_profile
from _harness import TEST_HOME, Checks, run_as_script
from agent import gui, ledger, pause, tracker, wizard
from test_wizard import CLICKS_JS, HOST, PAGE1, PAGE2, PAGE3, THANKS, _medium_confidence_mapper, _profile, _routes, _site

SINGLE = f"""<!doctype html><html><head><title>Analyst - Example Co</title></head><body>
<form id="f"><label for="fn">First Name</label><input id="fn" name="first_name" required>
  <label for="em">Email</label><input id="em" name="email" type="email" required>
  <button type="submit">Submit application</button></form>
<script>document.getElementById('f').addEventListener('submit', e => {{ e.preventDefault(); location.href = '/thanks'; }});</script>
{CLICKS_JS}</body></html>"""

CODE = "246810"
VERIFY_FIRST = f"""<!doctype html><html><head><title>Engineer - Example Co</title></head><body>
<h1>Check your email</h1><form id="f"><label for="vc">Verification code</label>
<input id="vc" name="verificationCode"><button type="submit">Verify</button></form>
<script>document.getElementById('f').addEventListener('submit', e => {{ e.preventDefault();
  if (document.getElementById('vc').value === '{CODE}') location.href = '/single'; }});</script>
{CLICKS_JS}</body></html>"""


def _gui_site(**kw):
    site = _site(**kw)
    site["/single"] = SINGLE
    site["/verify"] = VERIFY_FIRST
    return site


def _open(site):
    """A headless context with the activity listener, routed to the mock site."""
    from playwright.sync_api import sync_playwright
    p = sync_playwright().start()
    browser = p.chromium.launch(headless=True)
    ctx = browser.new_context()
    ctx.add_init_script(ledger.ACTIVITY_INIT_SCRIPT)
    for pattern, handler in _routes(site).items():
        ctx.route(pattern, handler)
    return p, browser, ctx


def _wizard(page, state, job_id, start, mapper=map_from_profile):
    """A GUI-mode wizard on `page`, reporting its pauses into `state`."""
    pauser = pause.GuiPauser(notify_fn=lambda t, b, echo=print: None, echo=lambda *a, **k: None)
    return wizard.Wizard(page, _profile(), url=f"{HOST}{start}", portal="generic",
                         portal_method="default", company="Example Co", role="Engineer",
                         resume=None, posting_text="", pauser=pauser, echo=lambda *a, **k: None,
                         on_pause=lambda info: state.set_pause(job_id, info["kind"], info["message"],
                                                               info["fields"], wants_code=info["wants_code"]))


def _tick_all(page, state, job_id):
    """The act for any later pause: tick every offered field, type the city if it's empty."""
    for f in state.snapshot()["jobs"][0]["pause"]["fields"]:
        if f["approvable"]:
            state.approve(job_id, f["ref"], True)
    try:
        if page.locator("#city").count() and page.input_value("#city") == "":
            page.fill("#city", "Springfield")
    except Exception:
        pass


def _drive(site, start, acts, mapper=map_from_profile, max_pauses=6):
    """
    Run a GUI-mode wizard on `start`; at each pause call the next act
    (page, state, job_id) and press Continue. Returns (outcome, pauses
    seen as state views, the page, context resources to close).
    """
    p, browser, ctx = _open(site)
    page = ctx.new_page()
    page.goto(f"{HOST}{start}")
    state = gui.State()
    [job_id] = state.start([f"{HOST}{start}"], dry=False)
    w = _wizard(page, state, job_id, start, mapper)
    pauses = []
    with mock.patch("agent.classify.classify_fields", classify_by_label), \
            mock.patch("agent.mapper.map_fields", mapper):
        out = w.run()
        while out.status == wizard.WAITING and len(pauses) < max_pauses:
            pauses.append(state.snapshot()["jobs"][0])
            act = acts[len(pauses) - 1] if len(pauses) - 1 < len(acts) else _tick_all
            act(page, state, job_id)
            state.request_continue(job_id)
            out = w.continue_from(state.take_resume(job_id))
    url = page.url
    clicks = page.evaluate("window.__clicks") if not page.is_closed() else []
    browser.close(); p.stop()
    return out, pauses, url, clicks, w


def _field(view, label):
    return next((f for f in (view["pause"] or {}).get("fields", []) if f["label"] == label), None)


NOTHING = f"""<!doctype html><html><head><title>Engineer - Example Co</title></head><body>
<h1>Step 1</h1><div id="app"><p>Loading…</p></div>{CLICKS_JS}</body></html>"""
NO_BUTTONS = f"""<!doctype html><html><head><title>Engineer - Example Co</title></head><body>
<form><label for="fn">First Name</label><input id="fn" name="first_name" required>
<label for="em">Email</label><input id="em" name="email" type="email" required></form>
{CLICKS_JS}</body></html>"""
SINGLE_FORM = SINGLE.split("<body>", 1)[1].split(CLICKS_JS, 1)[0]


def test_every_park_offers_continue_in_the_gui():
    # Jack, 2026-10-26: a park (a refusal, a page the wizard can't read or
    # move, a stop it can't get past) is a pause like any other in the GUI:
    # the row shows the reason with Continue, the tab stays open, nothing
    # is pressed. On 473ef65 a park returned needs_you with no pause, so
    # the row had no Continue and the job could never resume.
    check = Checks()
    # The mechanism, for every _Parked the loop can raise.
    p, browser, ctx = _open(_gui_site())
    page = ctx.new_page(); page.goto(f"{HOST}/apply")
    state = gui.State(); [job_id] = state.start([f"{HOST}/apply"], dry=False)
    w = _wizard(page, state, job_id, "/apply")
    with mock.patch.object(wizard.Wizard, "_loop", lambda self: (_ for _ in ()).throw(wizard._Parked("the tab closed"))):
        out = w.run()
    view = state.snapshot()["jobs"][0]
    check(out.status == wizard.WAITING and out.wizard is w and out.reason == "the tab closed",
          f"a park comes back as waiting, with the wizard kept ({out.status}, {out.reason})")
    check(view["status"] == "needs_you" and view["pause"] and view["pause"]["kind"] == "parked"
          and view["pause"]["message"].startswith("the tab closed. ")
          and "press Continue" in view["pause"]["message"] and view["pause"]["fields"] == [],
          f"the row carries a parked pause (so Continue is drawn) with the reason ({view['pause']})")
    check(w.pending == {"kind": "parked", "fill": None, "reason": "the tab closed"},
          f"the resume point is the top of the page ({w.pending})")
    browser.close(); p.stop()
    # The real no-fields park: a page with no form, no Next, no Submit.
    site = _gui_site(); site["/nothing"] = NOTHING
    with mock.patch.object(wizard, "FORM_WAIT_S", 1):
        out, pauses, url, clicks, w = _drive(site, "/nothing", [lambda page, state, job_id: None], max_pauses=1)
    check(pauses and pauses[0]["pause"]["kind"] == "parked"
          and pauses[0]["pause"]["message"].startswith("a page with no fields, no Next and no Submit. "),
          f"the no-fields park offers Continue ({pauses and pauses[0]['pause']})")
    check(out.status == wizard.WAITING and clicks == [] and url.endswith("/nothing"),
          f"Continue alone re-scans and parks again: nothing pressed, same page ({out.status}, {clicks}, {url})")
    check(any(e["kind"] == "pause" and e["detail"] == "parked" and e.get("handled") == "jack"
              for e in out.events), f"the first parked pause is Jack's once he pressed Continue ({out.events})")
    check.done()


def test_continue_on_a_no_fields_park_rescans_without_a_click():
    # Jack, 2026-10-26: Continue on a no-fields park re-enters the loop at
    # the top: the page as the tab shows it now, scanned afresh, no click,
    # no navigation. Jack opens the form in the tab himself (here: injected
    # by script); Continue fills and submits it on the same address.
    check = Checks()
    site = _gui_site(); site["/nothing"] = NOTHING

    def jack_opens_the_form(page, state, job_id):
        # The form's markup only (a script set through innerHTML never runs),
        # then the submit handler, attached by hand.
        page.evaluate("(html) => { document.getElementById('app').innerHTML = html; }",
                      SINGLE_FORM.split("<script>", 1)[0])
        page.evaluate("() => document.getElementById('f').addEventListener('submit', "
                      "e => { e.preventDefault(); location.href = '/thanks'; })")
    with mock.patch.object(wizard, "FORM_WAIT_S", 1):
        out, pauses, url, clicks, w = _drive(site, "/nothing", [jack_opens_the_form])
    check(len(pauses) == 1 and pauses[0]["pause"]["kind"] == "parked", f"one parked pause ({pauses})")
    check(out.status == "submitted" and clicks == ["Submit application"] and out.pages_filled == 1,
          f"after Continue the form was scanned, filled and submitted ({out.status}, {out.reason}, {clicks})")
    check("resumed after a parked pause" in out.log, f"the resume is logged ({out.log})")
    check.done()


def test_a_park_after_a_fill_resumes_at_next_not_at_a_second_fill():
    # A page filled and then stuck (no Next, no Submit) resumes at Next /
    # Submit for that same fill once Jack has fixed the page: no second
    # fill of the boxes, one Submit press.
    check = Checks()
    site = _gui_site(); site["/nobuttons"] = NO_BUTTONS

    def jack_adds_the_button(page, state, job_id):
        page.evaluate("""() => { const b = document.createElement('button'); b.type = 'submit';
          b.textContent = 'Submit application'; document.querySelector('form').appendChild(b);
          document.querySelector('form').addEventListener('submit', e => { e.preventDefault(); location.href = '/thanks'; }); }""")
    out, pauses, url, clicks, w = _drive(site, "/nobuttons", [jack_adds_the_button])
    check(len(pauses) == 1 and pauses[0]["pause"]["kind"] == "parked"
          and pauses[0]["pause"]["message"].startswith("no Next or Submit control found after filling the page. "),
          f"the after-fill park offers Continue ({pauses and pauses[0]['pause']})")
    check(out.status == "submitted" and clicks == ["Submit application"] and out.pages_filled == 1
          and len(out.steps) == 1, f"resumed at Submit for the one fill ({out.status}, {out.reason}, {clicks}, {out.pages_filled})")
    check.done()


def test_an_intake_park_waits_in_its_tab_with_continue():
    # A link intake refuses (LinkedIn Easy Apply) is a needs-you row with
    # Continue in GUI mode; Continue starts the wizard on the tab, which
    # refuses the same page again and waits again. On 473ef65 the row had
    # no pause and nothing to resume.
    import apply
    check = Checks()
    profile_path = TEST_HOME / "resume-profile2.yaml"
    profile_path.write_text(PROFILE, encoding="utf-8")
    easy = "https://www.linkedin.com/jobs/view/5"
    state = gui.State()
    [a] = state.start([easy], dry=False)
    waiting: dict = {}
    args = Namespace(profile=str(profile_path), resume=None, dry=False)
    quiet = pause.Pauser(notify_fn=lambda t, body, echo=print: None, echo=lambda *x, **k: None)
    site = _gui_site()
    with _headless(_routes(site)) as ctx:
        ctx.route("https://www.linkedin.com/**", lambda route: route.fulfill(
            status=200, content_type="text/html",
            body="<!doctype html><html><body><h1>Engineer</h1><button>Easy Apply</button></body></html>"))
        with mock.patch.object(apply.classify, "classify_fields", classify_by_label), \
                mock.patch.object(apply.mapper, "map_fields", map_from_profile), \
                redirect_stdout(io.StringIO()):
            apply.gui_loop(ctx, state, apply.Profile.load(profile_path), args, quiet,
                           idle_limit_s=1.0, waiting=waiting)
            job = state.snapshot()["jobs"][0]
            check(job["status"] == "needs_you" and job["pause"] and job["pause"]["kind"] == "parked"
                  and "linkedin" in job["pause"]["message"].lower() and "press Continue" in job["pause"]["message"]
                  and a in waiting,
                  f"the refused link waits with Continue ({job['status']}, {job['pause']})")
            state.request_continue(a)
            apply.gui_loop(ctx, state, apply.Profile.load(profile_path), args, quiet,
                           idle_limit_s=1.0, waiting=waiting)
            job = state.snapshot()["jobs"][0]
            tabs = [p.url for p in ctx.pages if "linkedin" in p.url]
    check(job["status"] == "needs_you" and job["pause"] and job["pause"]["kind"] == "parked"
          and "linkedin" in job["pause"]["message"].lower() and a in waiting and len(tabs) == 1,
          f"Continue read the page again, refused it again, and still waits ({job['status']}, {job['pause']})")
    check.done()


def test_a_stop_waits_in_its_tab_until_jack_types_and_continues():
    check = Checks()

    def jack_types_the_city(page, state, job_id):
        page.fill("#city", "Springfield")              # a trusted input in the tab
    out, pauses, url, clicks, w = _drive(_gui_site(), "/apply", [jack_types_the_city])
    check(len(pauses) == 1 and pauses[0]["status"] == "needs_you" and "City" in pauses[0]["reason"],
          f"the stop came back as a waiting row with its reason ({pauses and pauses[0]['reason']})")
    city = pauses[0] and _field(pauses[0], "City")
    check(city is not None and city["approvable"] is False and city["value"] is None,
          f"City is listed by label and reason, with no tick (the filler had no value) ({city})")
    check(out.status == "submitted" and url.endswith("/thanks")
          and clicks == ["Next", "Next", "Submit application"],
          f"after Continue the run went on to Submit ({out.status}, {out.reason}, {clicks})")
    check.done()


def test_ticked_values_count_only_while_the_page_still_holds_them():
    check = Checks()

    def tick_both(page, state, job_id):
        view = state.snapshot()["jobs"][0]
        for f in view["pause"]["fields"]:
            if f["approvable"]:
                state.approve(job_id, f["ref"], True)

    def jack_types_the_city(page, state, job_id):
        page.fill("#city", "Springfield")
    out, pauses, url, clicks, w = _drive(_gui_site(), "/apply", [tick_both, jack_types_the_city],
                                         mapper=_medium_confidence_mapper)
    first = pauses[0]
    ticks = [f for f in first["pause"]["fields"] if f["approvable"]]
    check(len(pauses) == 3 and "confidence is medium" in first["reason"],
          f"page one stopped on the medium-confidence answers; then the city, then the essay "
          f"({[p['reason'][:40] for p in pauses]})")
    check({f["label"] for f in ticks} == {"First Name", "Email"}
          and {f["value"] for f in ticks} == {"Test", "test@example.invalid"},
          f"both agent-filled fields offered with the page's read-back value ({ticks})")
    check(out.status == "submitted" and len(w.human_reviewed) >= 3,
          f"ticked, Continue: both count as reviewed, the run submits ({out.status}, {out.reason})")
    check(any("2 approved in the GUI" in e.get("detail", "") for e in out.events
              if e["kind"] == "gate_pause"), "the pause event counts the two approvals")
    check.done()


def test_continue_alone_approves_nothing():
    check = Checks()
    reviewed_after_first = []

    def nothing(page, state, job_id):
        pass

    def then_tick(page, state, job_id):
        reviewed_after_first.append(list(state.snapshot()["jobs"][0]["pause"]["fields"]))
        for f in state.snapshot()["jobs"][0]["pause"]["fields"]:
            if f["approvable"]:
                state.approve(job_id, f["ref"], True)

    def jack_types_the_city(page, state, job_id):
        page.fill("#city", "Springfield")
    out, pauses, url, clicks, w = _drive(_gui_site(), "/apply", [nothing, then_tick, jack_types_the_city],
                                         mapper=_medium_confidence_mapper)
    check(len(pauses) == 4 and pauses[1]["status"] == "needs_you"
          and "confidence is medium" in pauses[1]["reason"] and "First Name" in pauses[1]["reason"],
          f"Continue with no tick: the same stop again ({[p['reason'][:40] for p in pauses]})")
    first_pause_events = [e for e in out.events if e["kind"] == "gate_pause"]
    check(first_pause_events and "0 approved" in first_pause_events[0]["detail"]
          and "0 field(s) touched" in first_pause_events[0]["detail"],
          f"nothing was counted as reviewed by that Continue ({first_pause_events[0]})")
    check(out.status == "submitted", f"ticked next time, the run submits ({out.status}, {out.reason})")
    check.done()


def test_a_ticked_field_the_page_changed_is_not_jacks():
    check = Checks()

    def tick_both_then_the_page_changes_the_email(page, state, job_id):
        for f in state.snapshot()["jobs"][0]["pause"]["fields"]:
            if f["approvable"]:
                state.approve(job_id, f["ref"], True)
        page.evaluate("document.getElementById('em').value = 'changed@example.invalid'")

    def tick_again(page, state, job_id):
        for f in state.snapshot()["jobs"][0]["pause"]["fields"]:
            if f["approvable"]:
                state.approve(job_id, f["ref"], True)

    def jack_types_the_city(page, state, job_id):
        page.fill("#city", "Springfield")
    out, pauses, url, clicks, w = _drive(
        _gui_site(), "/apply", [tick_both_then_the_page_changes_the_email, tick_again, jack_types_the_city],
        mapper=_medium_confidence_mapper)
    second = pauses[1] if len(pauses) > 1 else None
    check(second is not None and "Email" in second["reason"] and "First Name" not in second["reason"],
          f"the changed Email still stops; the untouched First Name counted ({second and second['reason'][:90]})")
    email_tick = second and _field(second, "Email")
    check(email_tick is not None and email_tick["value"] == "changed@example.invalid",
          f"the second pause shows what the page holds now ({email_tick})")
    check(out.status == "submitted", f"ticked at its real value, the run submits ({out.status}, {out.reason})")
    check.done()


def test_a_verification_code_from_the_gui_is_typed():
    check = Checks()

    def give_the_code(page, state, job_id):
        check(state.give_code(job_id, CODE), "the row takes a code")
    out, pauses, url, clicks, w = _drive(_gui_site(), "/verify", [give_the_code])
    check(len(pauses) == 1 and pauses[0]["pause"]["wants_code"] is True
          and "verification code" in pauses[0]["reason"].lower(),
          f"the verification page waits with a code box ({pauses and pauses[0]['reason'][:80]})")
    check(out.status == "submitted" and url.endswith("/thanks") and "Verify" in clicks,
          f"the code was typed, Verify pressed, the form filled and submitted ({out.status}, {out.reason}, {clicks})")
    check.done()


def test_the_queue_goes_on_while_a_job_waits():
    import apply
    check = Checks()
    profile_path = TEST_HOME / "resume-profile.yaml"
    profile_path.write_text(PROFILE + "consent_allowlist:\n  - I agree to receive SMS updates\n",
                            encoding="utf-8")
    state = gui.State()
    a, b = state.start([f"{HOST}/apply", f"{HOST}/single"], dry=False)
    waiting: dict = {}
    args = Namespace(profile=str(profile_path), resume=None, dry=False)
    quiet = pause.Pauser(notify_fn=lambda t, body, echo=print: None, echo=lambda *x, **k: None)
    with _headless(_routes(_gui_site())) as ctx:
        ctx.add_init_script(ledger.ACTIVITY_INIT_SCRIPT)
        with mock.patch.object(apply.classify, "classify_fields", classify_by_label), \
                mock.patch.object(apply.mapper, "map_fields", map_from_profile), \
                mock.patch.object(apply.report, "open_in_browser", lambda path: None), \
                redirect_stdout(io.StringIO()):
            apply.gui_loop(ctx, state, apply.Profile.load(profile_path), args, quiet,
                           idle_limit_s=1.0, waiting=waiting)
            jobs = {j["id"]: j for j in state.snapshot()["jobs"]}
            check(jobs[a]["status"] == "needs_you" and "City" in jobs[a]["reason"]
                  and jobs[b]["status"] == "submitted",
                  f"A waits on its city while B was submitted ({jobs[a]['status']}, {jobs[b]['status']})")
            tabs = [p for p in ctx.pages if p.url.endswith("/apply/2")]
            check(len(tabs) == 1 and tabs[0].input_value("#city") == "" and a in waiting,
                  "A's tab is still open on page two, kept to resume")
            tabs[0].fill("#city", "Springfield")
            state.request_continue(a)
            apply.gui_loop(ctx, state, apply.Profile.load(profile_path), args, quiet,
                           idle_limit_s=1.0, waiting=waiting)
            jobs = {j["id"]: j for j in state.snapshot()["jobs"]}
    check(jobs[a]["status"] == "submitted" and not waiting,
          f"Continue finished A in its own tab ({jobs[a]['status']}, {jobs[a]['reason']})")
    rows = {r["url"]: r for r in tracker.all_applications()}
    check(rows[f"{HOST}/apply"]["status"] == "submitted" and rows[f"{HOST}/single"]["status"] == "submitted",
          "both are logged as submitted")
    check.done()


if __name__ == "__main__":
    run_as_script(globals())
