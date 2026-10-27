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
    check(w.pending and (w.pending["kind"], w.pending["fill"], w.pending["reason"]) == ("parked", None, "the tab closed"),
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


HANDSHAKE_FORM = f"""<!doctype html><html><head><title>Engineer - Example Co</title></head><body>
<h1>Apply on Handshake</h1><form><label for="fn">First Name</label><input id="fn" name="first_name" required>
<button type="submit">Submit application</button></form>{CLICKS_JS}</body></html>"""


def test_a_handshake_park_resumed_is_refused_again_not_filled():
    # Fix of 16e359e's BLOCK: intake refuses Handshake's built-in
    # application, and Continue on that park must meet the same refusal
    # in the loop, not a fill. The tab holds a form and a Submit control;
    # after Continue nothing is typed or pressed and the row waits again
    # with the job-board reason. On 16e359e the resumed loop filled the
    # form and pressed Submit.
    import apply
    check = Checks()
    profile_path = TEST_HOME / "resume-profile3.yaml"
    profile_path.write_text(PROFILE, encoding="utf-8")
    link = "https://app.joinhandshake.com/jobs/77/apply"
    state = gui.State()
    [a] = state.start([link], dry=False)
    waiting: dict = {}
    args = Namespace(profile=str(profile_path), resume=None, dry=False)
    quiet = pause.Pauser(notify_fn=lambda t, body, echo=print: None, echo=lambda *x, **k: None)
    with _headless(_routes(_gui_site())) as ctx:
        ctx.add_init_script(ledger.ACTIVITY_INIT_SCRIPT)
        ctx.route("https://app.joinhandshake.com/**", lambda route: route.fulfill(
            status=200, content_type="text/html", body=HANDSHAKE_FORM))
        with mock.patch.object(apply.classify, "classify_fields", classify_by_label), \
                mock.patch.object(apply.mapper, "map_fields", map_from_profile), \
                redirect_stdout(io.StringIO()):
            apply.gui_loop(ctx, state, apply.Profile.load(profile_path), args, quiet,
                           idle_limit_s=1.0, waiting=waiting)
            job = state.snapshot()["jobs"][0]
            check(job["status"] == "needs_you" and job["pause"] and job["pause"]["kind"] == "parked"
                  and "handshake" in job["pause"]["message"].lower() and a in waiting,
                  f"intake's Handshake park waits with Continue ({job['status']}, {job['pause']})")
            state.request_continue(a)
            apply.gui_loop(ctx, state, apply.Profile.load(profile_path), args, quiet,
                           idle_limit_s=1.0, waiting=waiting)
            job = state.snapshot()["jobs"][0]
            tab = next(p for p in ctx.pages if "joinhandshake" in p.url)
            typed, clicks, url = tab.input_value("#fn"), tab.evaluate("window.__clicks"), tab.url
    check(job["status"] == "needs_you" and job["pause"] and job["pause"]["kind"] == "parked"
          and job["pause"]["message"].startswith("handshake: the application stays on the job board. ")
          and a in waiting,
          f"Continue met the loop's own refusal and waits again ({job['status']}, {job['pause']})")
    check(typed == "" and clicks == [] and url == link,
          f"nothing typed, nothing pressed, same page ({typed!r}, {clicks}, {url})")
    check.done()


def test_a_park_after_a_fill_is_judged_again_before_next_and_rescanned_if_the_page_changed():
    # Fold of 16e359e's review: a resume after a fill runs the gate again
    # on that page (Jack may have changed a box) before Next / Submit, and
    # a page whose shape changed since the park (Jack moved it on himself)
    # is scanned afresh, never advanced on a stale fill. On 16e359e the
    # resume went straight to Next / Submit.
    check = Checks()
    site = _gui_site(); site["/nobuttons"] = NO_BUTTONS

    def jack_empties_first_name(page, state, job_id):
        page.fill("#fn", "")

    def jack_refills_it(page, state, job_id):
        page.fill("#fn", "Test")
    out, pauses, url, clicks, w = _drive(site, "/nobuttons", [jack_empties_first_name, jack_refills_it],
                                          max_pauses=3)
    kinds = [p["pause"]["kind"] for p in pauses]
    check(kinds == ["parked", "gate", "parked"] and clicks == [],
          f"park, then the gate stop on the emptied box, then the park again; nothing pressed ({kinds}, {clicks})")
    check(pauses[1]["pause"]["fields"] and pauses[1]["pause"]["fields"][0]["label"] == "First Name"
          and "required, but empty" in pauses[1]["pause"]["fields"][0]["reason"],
          f"the gate stop names the box Jack emptied ({pauses[1]['pause']})")
    check(out.pages_filled == 1 and len(out.steps) == 1, f"one fill throughout ({out.pages_filled})")

    def jack_moves_to_page_two(page, state, job_id):
        page.goto(f"{HOST}/apply/2")

    def jack_types_the_city(page, state, job_id):
        page.fill("#city", "Springfield")
    out, pauses, url, clicks, w = _drive(site, "/nobuttons", [jack_moves_to_page_two, jack_types_the_city])
    kinds = [p["pause"]["kind"] for p in pauses]
    check(kinds == ["parked", "gate"] and out.status == "submitted" and clicks == ["Next", "Submit application"],
          f"the moved page was scanned afresh and the run went on ({kinds}, {out.status}, {out.reason}, {clicks})")
    check("the page changed since the park: scanned afresh" in out.log and out.pages_filled == 3,
          f"the fresh scan is logged; three pages filled ({out.pages_filled}, {out.log})")
    check.done()


def test_a_resume_on_a_closed_tab_or_an_unknown_shape_is_safe():
    # Fold of 58d7ab7's review: a tab Jack closed during a park resumes
    # into the "tab closed" park, not a crash; a park whose page shape
    # couldn't be read is scanned afresh rather than advanced on the
    # stale fill. On 58d7ab7 the first raised out of continue_from and the
    # second kept the fill.
    check = Checks()
    site = _gui_site(); site["/nobuttons"] = NO_BUTTONS
    p, browser, ctx = _open(site)
    page = ctx.new_page(); page.goto(f"{HOST}/nobuttons")
    state = gui.State(); [job_id] = state.start([f"{HOST}/nobuttons"], dry=False)
    w = _wizard(page, state, job_id, "/nobuttons")
    try:
        with mock.patch("agent.classify.classify_fields", classify_by_label), \
                mock.patch("agent.mapper.map_fields", map_from_profile):
            out = w.run()
            check(out.status == wizard.WAITING and w.pending and w.pending["kind"] == "parked"
                  and w.pending["fill"] is not None, f"parked after the fill ({out.status}, {out.reason})")
            page.close()
            state.request_continue(job_id)
            out = w.continue_from(state.take_resume(job_id))
        check(out.status == wizard.WAITING and out.reason == "the tab closed"
              and state.snapshot()["jobs"][0]["pause"]["kind"] == "parked",
              f"a closed tab parks again, with Continue, no crash ({out.status}, {out.reason})")
    finally:
        browser.close(); p.stop()

    def jack_adds_the_button(page, state, job_id):
        page.evaluate("""() => { const b = document.createElement('button'); b.type = 'submit';
          b.textContent = 'Submit application'; document.querySelector('form').appendChild(b);
          document.querySelector('form').addEventListener('submit', e => { e.preventDefault(); location.href = '/thanks'; }); }""")
    with mock.patch("agent.follow.read_shape", lambda target: None):
        out, pauses, url, clicks, w = _drive(site, "/nobuttons", [jack_adds_the_button], max_pauses=2)
    kinds = [p["pause"]["kind"] for p in pauses]
    # The fresh scan leaves the boxes that hold a value alone and the gate
    # stops for Jack on them (nothing proves who wrote them): no Submit.
    check(kinds == ["parked", "gate"] and "the page changed since the park: scanned afresh" in out.log
          and out.pages_filled == 2 and clicks == [] and _field(pauses[1], "First Name"),
          f"an unreadable shape: scanned afresh, then the gate stops on the held boxes ({kinds}, {out.reason}, {clicks})")
    check.done()


def test_a_fresh_scan_after_a_park_leaves_boxes_that_hold_a_value_alone():
    # Fold of f0f0e0d's review, redone after 8185f17's BLOCK: a page
    # scanned afresh after a park (here its shape couldn't be read) is
    # filled again, and that fill must not type over a box that already
    # holds a value, nor send it to a model, nor count it as Jack's: he may
    # have corrected it, but nothing proves who wrote it, so the gate
    # judges it like any other box and stops for him. Touch marks are not
    # the rule (the filler's own clear leaves one on every box it fills).
    # On 8185f17 the boxes were counted as his.
    check = Checks()
    site = _gui_site(); site["/nobuttons"] = NO_BUTTONS
    classified: dict = {"refs": set()}

    def classify_and_record(fields):
        classified["refs"] |= {f["ref"] for f in fields}
        return classify_by_label(fields)

    def jack_corrects_first_name(page, state, job_id):
        page.fill("#fn", "Jacques")
    with mock.patch("agent.follow.read_shape", lambda target: None):
        p, browser, ctx = _open(site)
        try:
            page = ctx.new_page(); page.goto(f"{HOST}/nobuttons")
            state = gui.State(); [job_id] = state.start([f"{HOST}/nobuttons"], dry=False)
            w = _wizard(page, state, job_id, "/nobuttons")
            with mock.patch("agent.classify.classify_fields", classify_and_record), \
                    mock.patch("agent.mapper.map_fields", map_from_profile):
                out = w.run()
                check(out.status == wizard.WAITING and page.input_value("#fn") == "Test",
                      f"filled, then parked ({out.status}, {out.reason})")
                jack_corrects_first_name(page, state, job_id)
                state.request_continue(job_id)
                out = w.continue_from(state.take_resume(job_id))
            value = page.input_value("#fn")
            new_refs = {f["label"]: f["ref"] for f in out.steps[-1]["fields"]} if out.steps else {}
            view = state.snapshot()["jobs"][0]
        finally:
            browser.close(); p.stop()
    check(out.status == wizard.WAITING and "the page changed since the park: scanned afresh" in out.log,
          f"scanned afresh ({out.status}, {out.reason}, {out.log})")
    check(value == "Jacques", f"Jack's correction was not typed over ({value!r})")
    check(any(line.startswith("left as it is (holds a value, or couldn't be read): 'First Name'") for line in out.log),
          f"the protected box is logged ({out.log})")
    check(out.pages_filled == 2 and w.human_reviewed == [],
          f"the box is not counted as his ({out.pages_filled}, {w.human_reviewed})")
    check(new_refs.get("First Name") not in classified["refs"] and new_refs.get("Email") not in classified["refs"],
          f"the held boxes reached no classifier call ({new_refs}, {classified})")
    check(view["pause"] and view["pause"]["kind"] == "gate" and _field(view, "First Name"),
          f"the gate then stops for him on the held box ({view['pause']})")
    check.done()


# The first box has no name, no aria-label and no <label>: the scan names
# it from the text beside it, but the value read has no stable identity
# for it and leaves it out.
NO_IDENTITY = f"""<!doctype html><html><head><title>Engineer - Example Co</title></head><body>
<form><div><span>First Name</span> <input id="fn" placeholder="First Name" required></div>
<div><label for="em">Email</label> <input id="em" name="email" type="email" required></div></form>
{CLICKS_JS}</body></html>"""


def test_a_box_the_value_read_leaves_out_is_held_not_filled_over():
    # Fix of c5b13bc's BLOCK: on a protected fill, a box the value read
    # leaves out (no name, no aria-label, no <label>: no stable identity)
    # is held like one that holds a value, never filled over, never sent
    # to a model, and flagged with the fixed reason; a box that was read
    # and reads empty is filled. The pipeline is called directly on the
    # page, as the wizard calls it after a fresh scan. On c5b13bc the
    # nameless box counted as empty and "Jacques" was typed over.
    from agent import extract, pipeline
    check = Checks()
    site = _gui_site(); site["/noid"] = NO_IDENTITY
    classified: dict = {"refs": set()}

    def classify_and_record(fields):
        classified["refs"] |= {f["ref"] for f in fields}
        return classify_by_label(fields)
    p, browser, ctx = _open(site)
    try:
        page = ctx.new_page(); page.goto(f"{HOST}/noid")
        page.fill("#fn", "Jacques")                  # Jack's correction, before the fresh fill
        with mock.patch("agent.classify.classify_fields", classify_and_record), \
                mock.patch("agent.mapper.map_fields", map_from_profile):
            fill = pipeline.fill_page(page, _profile(), guard=extract.ReadGuard(), resume=None,
                                      posting_text="", protect_held=True)
        fn, em = page.input_value("#fn"), page.input_value("#em")
    finally:
        browser.close(); p.stop()
    check(fill is not None and fn == "Jacques" and em == "test@example.invalid",
          f"the nameless box kept Jack's value; the readable empty box was filled ({fn!r}, {em!r})")
    fn_ref = fill.fields[0]["ref"] if fill else None
    check(fill and fill.held == [fn_ref] and fn_ref not in classified["refs"] and fn_ref not in fill.plan,
          f"held, no classifier call, no plan ({fill and fill.held}, {classified}, {fn_ref})")
    flag = next((f for f in (fill.flags if fill else []) if f.get("ref") == fn_ref), None)
    check(flag and flag.get("category") == "held" and "left as it is" in flag.get("reason", "")
          and (fill.categories.get(fn_ref) == "held"),
          f"flagged with the fixed reason and the held category ({flag})")
    # And the gate stops on it: a held box is never a pass (its record has
    # no decision, and its read-back is missing, so it is never "empty").
    from agent import gate, shadow
    record = {"url": f"{HOST}/noid", "portal": "generic", "portal_method": "default", "wizard": True, "steps": 1,
              "fields": fill.fields, "categories": fill.categories, "plan": fill.plan, "flags": fill.flags,
              "results": fill.step.get("results"), "snapshot": fill.snapshot, "census": fill.census,
              "frames": [], "events": [], "errors": []}
    verdict = gate.decide(record, _profile())
    check(verdict["content_verdict"] == "would_stop"
          and any(r.startswith("'First Name': left for you") for r in verdict["content_reasons"])
          and not any("First Name" in w for w in verdict.get("waived", [])),
          f"the gate stops on the held box and waives nothing of it ({verdict['content_reasons']}, {verdict.get('waived')})")
    check.done()


def test_the_hold_survives_a_pass_that_fills_nothing():
    # Fix of c5b13bc's second BLOCK: the protection order is consumed only
    # by a fill, so a resumed pass that ends in the form wait (the page
    # mid-render) keeps it for the fill that follows. Jack's "Jacques"
    # survives; on c5b13bc the wait pass consumed the order and the next
    # pass typed over it.
    check = Checks()
    site = _gui_site(); site["/nobuttons"] = NO_BUTTONS
    real_wait = wizard.Wizard._await_form
    waits = {"n": 0}

    def wait_once_then_real(self, here):
        waits["n"] += 1
        if waits["n"] == 2:            # the resumed pass: the form "renders late" once
            self.out.log.append("the form rendered after 0.0s: checked again")
            return False
        return real_wait(self, here)
    with mock.patch("agent.follow.read_shape", lambda target: None), \
            mock.patch.object(wizard.Wizard, "_await_form", wait_once_then_real):
        p, browser, ctx = _open(site)
        try:
            page = ctx.new_page(); page.goto(f"{HOST}/nobuttons")
            state = gui.State(); [job_id] = state.start([f"{HOST}/nobuttons"], dry=False)
            w = _wizard(page, state, job_id, "/nobuttons")
            with mock.patch("agent.classify.classify_fields", classify_by_label), \
                    mock.patch("agent.mapper.map_fields", map_from_profile):
                out = w.run()
                page.fill("#fn", "Jacques")
                state.request_continue(job_id)
                out = w.continue_from(state.take_resume(job_id))
            value = page.input_value("#fn")
        finally:
            browser.close(); p.stop()
    check(out.status == wizard.WAITING and "the page changed since the park: scanned afresh" in out.log
          and waits["n"] >= 3, f"scanned afresh after a wait pass ({out.status}, {out.reason}, {waits})")
    check(value == "Jacques" and out.pages_filled == 2,
          f"Jack's correction survived the fill after the wait pass ({value!r}, {out.pages_filled})")
    check.done()


def test_an_unreadable_value_probe_parks_instead_of_filling():
    # Fold of 8185f17's review: when the fresh scan can't read the page's
    # values, it must not type over anything; the job parks. Jack's
    # correction survives. On 8185f17 the fill ran regardless.
    check = Checks()
    site = _gui_site(); site["/nobuttons"] = NO_BUTTONS

    def boom(page, fields):
        raise RuntimeError("unreadable")
    with mock.patch("agent.follow.read_shape", lambda target: None):
        p, browser, ctx = _open(site)
        try:
            page = ctx.new_page(); page.goto(f"{HOST}/nobuttons")
            state = gui.State(); [job_id] = state.start([f"{HOST}/nobuttons"], dry=False)
            w = _wizard(page, state, job_id, "/nobuttons")
            with mock.patch("agent.classify.classify_fields", classify_by_label), \
                    mock.patch("agent.mapper.map_fields", map_from_profile):
                out = w.run()
                page.fill("#fn", "Jacques")
                state.request_continue(job_id)
                # The page's values become unreadable only for the resumed scan.
                with mock.patch("agent.pipeline.extract.snapshot", boom):
                    out = w.continue_from(state.take_resume(job_id))
            value = page.input_value("#fn")
        finally:
            browser.close(); p.stop()
    check(out.status == wizard.WAITING and out.reason.startswith("the page couldn't be read"),
          f"parked, not filled ({out.status}, {out.reason})")
    check(value == "Jacques" and out.pages_filled == 1, f"nothing typed over ({value!r}, {out.pages_filled})")
    check.done()


def test_a_page_that_left_the_site_during_a_gate_pause_is_refused_on_resume():
    # Fold of f0f0e0d's review, untested until now: a tab Jack navigated
    # off-host during a gate pause is refused on the resumed pass before
    # anything is gated or pressed.
    check = Checks()
    site = _gui_site(); site["https://elsewhere.example/x"] = SINGLE

    def jack_leaves_the_site(page, state, job_id):
        page.goto("https://elsewhere.example/x")
    out, pauses, url, clicks, w = _drive(site, "/apply", [jack_leaves_the_site, lambda *a: None], max_pauses=2)
    kinds = [p["pause"]["kind"] for p in pauses]
    check(kinds == ["gate", "parked"] and "the page left the application's site" in out.reason,
          f"refused on resume ({kinds}, {out.status}, {out.reason})")
    check(clicks == [] and url == "https://elsewhere.example/x" and out.pages_filled == 2,
          f"nothing pressed on the other site, nothing filled there ({clicks}, {url}, {out.pages_filled})")
    check.done()


# Page two with a per-company radio question beside the required City.
WORKED_RADIO = ('<fieldset><legend>Have you previously worked for Example Co?</legend>'
                '<label><input type="radio" name="worked" value="yes">Yes</label>'
                '<label><input type="radio" name="worked" value="no">No</label></fieldset>')
# A radio the agent answers itself (a stand-in mapper picks "Email").
CONTACT_RADIO = ('<fieldset><legend>Preferred contact</legend>'
                 '<label><input type="radio" name="contact" value="email">Email</label>'
                 '<label><input type="radio" name="contact" value="phone">Phone</label></fieldset>')


def _page2_with(extra):
    site = _gui_site()
    site["/apply/2"] = site["/apply/2"].replace("<label for=\"city\">", extra + "<label for=\"city\">")
    return site


def _contact_mapper(fields, profile, page_text=""):
    # Picks "Phone", the SECOND option, so the agent's own click lands on
    # an option element other than the group's ref.
    out = map_from_profile(fields, profile, page_text)
    for f in fields:
        if (f.get("label") or "").startswith("Preferred contact"):
            from agent import mapper
            out[f["ref"]] = mapper._double_check({"ref": f["ref"], "value": "Phone", "confidence": "high",
                                                  "needs_review": False, "source": "model"}, f)
    return out


def test_jacks_click_on_a_groups_second_option_counts_as_his():
    # Item 1 of 2026-10-26's second round: a radio group's ref is its first
    # option's element, and Jack's click on "No" marks the second; the
    # mark probe now reads every option of the group. At the City pause
    # Jack picks "No" on the per-company question and types the city;
    # after Continue the run goes on. On e8021fa the per-company stop
    # came back every time (the mark was on an option the probe never
    # read).
    check = Checks()

    def jack_answers(page, state, job_id):
        page.check("input[name=worked][value=no]")
        page.fill("#city", "Springfield")
    out, pauses, url, clicks, w = _drive(_page2_with(WORKED_RADIO), "/apply", [jack_answers])
    check(len(pauses) == 1 and _field(pauses[0], "Have you previously worked for Example Co?"),
          f"one pause, naming the per-company question ({[p['pause']['message'] for p in pauses]})")
    check(out.status == "submitted" and clicks == ["Next", "Next", "Submit application"],
          f"his click on the second option counted and the run went on ({out.status}, {out.reason}, {clicks})")
    by_label = {f["label"]: f["ref"] for st in out.steps for f in st["fields"]}
    check(by_label.get("Have you previously worked for Example Co?") in out.record.get("human_reviewed", []),
          "the group is his")
    check.done()


def test_the_agents_own_radio_pick_counts_for_nothing():
    # Negative (fix of 8fd4eb8's BLOCK): the agent picks the SECOND option
    # under the agent flag, which leaves that option focused; at the City
    # pause Jack CLICKS the City box (a trusted pointerdown, which the
    # listener answers by marking the active element too) and types. The
    # baseline read blurred the agent's option first, so the group is not
    # his; the city is. On 8fd4eb8 the group was counted as his.
    check = Checks()

    def jack_clicks_and_types_the_city(page, state, job_id):
        page.click("#city")
        page.keyboard.type("Springfield")
    out, pauses, url, clicks, w = _drive(_page2_with(CONTACT_RADIO), "/apply", [jack_clicks_and_types_the_city],
                                          mapper=_contact_mapper)
    by_label = {f["label"]: f["ref"] for st in out.steps for f in st["fields"]}
    reviewed = (out.record or {}).get("human_reviewed", [])
    check(out.status == "submitted", f"the run went on ({out.status}, {out.reason})")
    check(by_label.get("City") in reviewed and by_label.get("Preferred contact") not in reviewed,
          f"the city is his, the agent's own radio pick is not ({reviewed})")
    detail = next(e["detail"] for e in out.events if e["kind"] == "gate_pause")
    check(detail.startswith("1 field(s) touched by Jack"), f"one touch counted ({detail})")
    check.done()


REFOCUS2 = ('<script>document.addEventListener("DOMContentLoaded", () => { const em = document.getElementById("em2"); '
            'if (em) em.addEventListener("blur", () => setTimeout(() => em.focus(), 10)); });</script>')


def test_a_page_that_restores_the_focus_makes_the_pause_count_nothing():
    # Fold of 7efeb21's review: the baseline blurs the agent's last box,
    # but a page that puts the focus back (a validation, a re-render)
    # would have Jack's first click mark it as his. The baseline now
    # checks the focus is free after the blur; if not, nothing counts at
    # that pause, not even the box Jack really touched. On 7efeb21 the
    # refocused email box was counted as his.
    check = Checks()
    site = _gui_site(email_on_2=True)
    site["/apply/2"] = site["/apply/2"].replace("</body>", REFOCUS2 + "</body>")

    def jack_clicks_and_types_the_city(page, state, job_id):
        page.click("#city")
        page.keyboard.type("Springfield")
    out, pauses, url, clicks, w = _drive(site, "/apply", [jack_clicks_and_types_the_city, lambda *a: None],
                                          max_pauses=2)
    kinds = [p["pause"]["kind"] for p in pauses]
    pause_events = [e for e in out.events if e["kind"] == "gate_pause" and e.get("handled") == "jack"]
    check(kinds == ["gate", "gate"] and pause_events
          and all(str(e.get("detail", "")).startswith("0 field(s) touched by Jack") for e in pause_events),
          f"nothing counted at the pauses, the stop stays ({kinds}, {pause_events})")
    check(any("keeps the focus on a box" in line for line in out.log), f"the reason is logged ({out.log})")
    check(clicks == ["Next"], f"nothing pressed after the stop ({clicks})")
    check.done()


REFOCUS_SLOW2 = ('<script>document.addEventListener("DOMContentLoaded", () => { const em = document.getElementById("em2"); '
                 'if (em) em.addEventListener("blur", () => setTimeout(() => em.focus(), 400)); });</script>')


def test_a_refocus_slower_than_the_settle_still_marks_nothing_at_jacks_click():
    # Fold of bb196bf's review: the settle after the blur is a bound, not a
    # close; a page that puts the focus back later than it would still
    # have had Jack's first click mark that box. The listener now marks
    # the focused element on a pointer event only when it is a
    # dropdown-style control, so a plain box the page refocused is never
    # marked by a click elsewhere: the baseline is taken (focus was free
    # at the settle), Jack's click and typing count the City only, the
    # run goes on. On bb196bf the refocused email box counted as his.
    check = Checks()
    site = _gui_site(email_on_2=True)
    site["/apply/2"] = site["/apply/2"].replace("</body>", REFOCUS_SLOW2 + "</body>")

    def jack_waits_then_clicks_and_types_the_city(page, state, job_id):
        page.wait_for_timeout(700)          # the refocus has happened by now
        page.click("#city")
        page.keyboard.type("Springfield")
    out, pauses, url, clicks, w = _drive(site, "/apply", [jack_waits_then_clicks_and_types_the_city])
    by_label: dict = {}
    for st in out.steps:
        for f in st["fields"]:
            by_label.setdefault(f["label"], []).append(f["ref"])
    reviewed = (out.record or {}).get("human_reviewed", [])
    em2 = [r for r in by_label.get("Email", []) if r in {f["ref"] for f in out.steps[1]["fields"]}]
    check(out.status == "submitted", f"the run went on ({out.status}, {out.reason})")
    check(by_label.get("City", [None])[0] in reviewed and em2 and em2[0] not in reviewed,
          f"the city is his, the refocused box the agent filled is not ({reviewed}, {em2})")
    detail = next(e["detail"] for e in out.events if e["kind"] == "gate_pause")
    check(detail.startswith("1 field(s) touched by Jack"), f"one touch counted ({detail})")
    check.done()


def test_jacks_first_click_elsewhere_never_marks_the_box_the_agent_last_filled():
    # Negative, the text-box form of the same route (pre-existing before
    # 8fd4eb8, closed by the same blur): the agent's last fill on page two
    # leaves the second Email box focused; Jack clicks the City box and
    # types. The email box is not his. On e8021fa and 8fd4eb8 it was.
    check = Checks()

    def jack_clicks_and_types_the_city(page, state, job_id):
        page.click("#city")
        page.keyboard.type("Springfield")
    out, pauses, url, clicks, w = _drive(_gui_site(email_on_2=True), "/apply", [jack_clicks_and_types_the_city])
    by_label: dict = {}
    for st in out.steps:
        for f in st["fields"]:
            by_label.setdefault(f["label"], []).append(f["ref"])
    reviewed = (out.record or {}).get("human_reviewed", [])
    em2 = [r for r in by_label.get("Email", []) if r in {f["ref"] for f in out.steps[1]["fields"]}]
    check(out.status == "submitted", f"the run went on ({out.status}, {out.reason})")
    check(by_label.get("City", [None])[0] in reviewed and em2 and em2[0] not in reviewed,
          f"the city is his, the box the agent last filled is not ({reviewed}, {em2})")
    check.done()


def test_a_resume_that_rescans_the_page_counts_nothing_as_his():
    # Negative (Jack's amendment): another job's scan makes this page's refs
    # stale, so the resume rescans the page (extract_fields only; it opens
    # nothing) before the review. Jack pressed Continue without touching
    # anything: the rescan leaves no mark, nothing is his, the stop stays.
    # A guard: it passes on e8021fa too.
    check = Checks()
    from agent import extract
    site = _page2_with(WORKED_RADIO)

    def another_job_scans(page, state, job_id):
        # The same process scanned another page in between (a second tab).
        other = page.context.new_page()
        other.set_content(SINGLE)
        extract.extract_fields(other, extract.ReadGuard())
        other.close()
    out, pauses, url, clicks, w = _drive(site, "/apply", [another_job_scans, lambda *a: None], max_pauses=2)
    kinds = [p["pause"]["kind"] for p in pauses]
    log = out.log
    check(kinds == ["gate", "gate"] and any("re-scanned after other jobs ran" in line for line in log),
          f"the resume rescanned the page and the gate stopped again ({kinds}, {log})")
    pauses_ev = [e for e in out.events if e["kind"] == "gate_pause" and e.get("handled") == "jack"]
    check(pauses_ev and all(str(e.get("detail", "")).startswith("0 field(s) touched by Jack") for e in pauses_ev),
          f"nothing counted as his ({pauses_ev})")
    check(clicks == ["Next"], f"nothing pressed after the stop ({clicks})")
    check.done()


def test_a_box_the_agent_filled_and_jack_corrected_counts_as_his():
    # Item 2: with no mark left by the agent's fill, Jack's correction to a
    # box the agent filled is a mark new since the pause, so the box is his
    # and the run goes on. The second Email box on page two is filled by
    # the agent; at the City pause Jack retypes it. On e8021fa the box
    # already carried the mark from the clear, so his retyping was never
    # new and the box stayed the agent's.
    check = Checks()

    def jack_corrects_the_email_and_types_the_city(page, state, job_id):
        page.fill("#em2", "jack@example.invalid")
        page.fill("#city", "Springfield")
    out, pauses, url, clicks, w = _drive(_gui_site(email_on_2=True), "/apply",
                                          [jack_corrects_the_email_and_types_the_city])
    by_label: dict = {}
    for st in out.steps:
        for f in st["fields"]:
            by_label.setdefault(f["label"], []).append(f["ref"])
    reviewed = (out.record or {}).get("human_reviewed", [])
    em2 = [r for r in by_label.get("Email", []) if r in {f["ref"] for f in out.steps[1]["fields"]}]
    check(out.status == "submitted", f"the run went on ({out.status}, {out.reason})")
    check(em2 and em2[0] in reviewed and by_label.get("City", [None])[0] in reviewed,
          f"the corrected email and the city are his ({reviewed})")
    detail = next(e["detail"] for e in out.events if e["kind"] == "gate_pause")
    check(detail.startswith("2 field(s) touched by Jack"), f"two touches counted ({detail})")
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
