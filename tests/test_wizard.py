"""
test_wizard.py — a three-page application, filled, gated, stepped and submitted.

WHAT THIS DOES: Serves a mock three-page portal at a real-looking host and
runs agent.wizard on it with stand-ins for the model (the classifier and
mapper from tests/_fill_run.py) and a scripted pauser standing in for Jack.
The first page is filled and Next pressed; the second has a required City
the profile can't answer, so the gate stops, the pause fires, "Jack" types
the city (a trusted input), and the page is judged again; the third has a
drafted essay (a canned answer, verbatim), a required terms box the
consent policy allows, and an optional SMS box it never ticks; Submit is
pressed only after the gate says yes, and the thank-you page is read as
the confirmation. Then: a pause that times out parks the job with the
reason and nothing more is pressed; a final page with a pre-ticked
marketing box stops the gate and Submit is never pressed; a page-filled
legal field without a trusted input is not taken as Jack's answer.
WHY IT MATTERS: This is the unattended path end to end: the order of
checks, the one Next press per page, the one Submit press behind the gate.

Run from anywhere:  .venv/Scripts/python.exe tests/test_wizard.py
"""
import json
from pathlib import Path
from unittest import mock

from _fill_run import PROFILE, classify_by_label, map_from_profile
from _harness import TEST_HOME, Checks, run_as_script
from agent import ledger, pause, wizard
from agent.profile import Profile

HOST = "https://apply.example-co.com"
CLICKS_JS = """<script>
  window.__clicks = JSON.parse(sessionStorage.getItem('clicks') || '[]');
  document.addEventListener('click', e => {
    const b = e.target.closest('button, a, input[type=submit]');
    if (b) { window.__clicks.push((b.innerText || b.value || '').trim());
             sessionStorage.setItem('clicks', JSON.stringify(window.__clicks)); }
  }, true);
  window.__ticks = () => [...document.querySelectorAll('input[type=checkbox]')].map(b => [b.id, b.checked]);
</script>"""

PAGE1 = f"""<!doctype html><html><head><title>Engineer - Example Co</title></head><body>
<h1>Step 1 of 3</h1><form>
  <label for="fn">First Name</label><input id="fn" name="first_name" required>
  <label for="em">Email</label><input id="em" name="email" type="email" required>
  <button type="button" onclick="location.href='/apply/2'">Next</button>
</form>{CLICKS_JS}</body></html>"""

PAGE2 = f"""<!doctype html><html><head><title>Engineer - Example Co</title></head><body>
<h1>Step 2 of 3</h1><form>
  <label for="city">City</label><input id="city" name="city" required>
  <input type="checkbox" id="sms"><label for="sms">I agree to receive SMS updates</label>
  <button type="button" onclick="location.href='/apply/3'">Next</button>
</form>{CLICKS_JS}</body></html>"""

PAGE3 = f"""<!doctype html><html><head><title>Engineer - Example Co</title></head><body>
<h1>Step 3 of 3</h1><form id="f">
  <label for="why">Why do you want to work here?</label><textarea id="why" name="why"></textarea>
  <input type="checkbox" id="tos" required><label for="tos">I have read and agree to the Terms of Service and Privacy Policy</label>
  MARKETING
  <button type="submit">Submit application</button>
</form>
<script>document.getElementById('f').addEventListener('submit', e => {{ e.preventDefault();
  if (document.getElementById('tos').checked) location.href = '/thanks'; }});</script>
{CLICKS_JS}</body></html>"""

THANKS = f"""<!doctype html><html><head><title>Example Co</title></head><body>
<h1>Thank you for applying to Example Co, Test!</h1><p>We'll be in touch.</p>{CLICKS_JS}</body></html>"""

LEGAL_PAGE = f"""<!doctype html><html><head><title>Engineer - Example Co</title></head><body>
<form><label for="fn">First Name</label><input id="fn" name="first_name">
<fieldset><legend>Have you ever been convicted of a felony?</legend>
<label><input type="radio" name="felony" value="yes">Yes</label>
<label><input type="radio" name="felony" value="no">No</label></fieldset>
<button type="submit">Submit application</button></form>
<script>window.__autofill = () => {{ document.querySelector('input[name=felony][value=no]').checked = true; }};</script>
{CLICKS_JS}</body></html>"""


# Variants: a second Email box on page two (the filler fills it); the
# required City on page three instead of page two.
EMAIL2 = '<label for="em2">Email</label><input id="em2" name="email2">'
CITY = '<label for="city">City</label><input id="city" name="city" required>'


def _site(marketing="", email_on_2=False, city_on_3=False):
    page2 = PAGE2.replace('<label for="city">City</label><input id="city" name="city" required>',
                          (CITY if not city_on_3 else "") + (EMAIL2 if email_on_2 else ""))
    page3 = PAGE3.replace("MARKETING", marketing + (CITY if city_on_3 else ""))
    return {"/apply": PAGE1, "/apply/2": page2, "/apply/3": page3,
            "/thanks": THANKS, "/legal": LEGAL_PAGE}


def _routes(site):
    def handle(route):
        path = route.request.url.split("example-co.com", 1)[-1].split("?")[0]
        route.fulfill(status=200, content_type="text/html", body=site.get(path, "<p>404</p>"))
    return {f"{HOST}/**": handle}


class _Jack(pause.Pauser):
    """Stands in for Jack at each pause: runs `act(page, body)` and reports how it ended."""

    def __init__(self, page, act=None, how=pause.TIMED_OUT):
        super().__init__(notify_fn=lambda t, b, echo=print: None, echo=lambda *a, **k: None)
        self.page, self.act, self.bodies, self.how = page, act, [], how

    def wait(self, title, body, *, prompt, hidden=False, until=None):
        self.bodies.append(body)
        if self.act is None:
            return None, self.how
        self.act(self.page, body)
        return "", pause.ANSWERED


def _profile():
    path = TEST_HOME / "wizard-profile.yaml"
    # The optional SMS box on page two is one Jack allowed to be left alone.
    path.write_text(PROFILE + "consent_allowlist:\n  - I agree to receive SMS updates\n",
                    encoding="utf-8")
    return Profile.load(path)


def _run(site, start, act=None, portal="generic", method="default", how=pause.TIMED_OUT):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            ctx = browser.new_context()
            ctx.add_init_script(ledger.ACTIVITY_INIT_SCRIPT)
            for pattern, handler in _routes(site).items():
                ctx.route(pattern, handler)
            page = ctx.new_page()
            page.goto(f"{HOST}{start}")
            jack = _Jack(page, act, how)
            with mock.patch("agent.classify.classify_fields", classify_by_label), \
                    mock.patch("agent.mapper.map_fields", map_from_profile):
                out = wizard.run_application(page, _profile(), url=f"{HOST}{start}", portal=portal,
                                             portal_method=method, company="Example Co",
                                             role="Engineer", resume=None, posting_text="",
                                             pauser=jack, echo=lambda *a, **k: None)
            clicks = page.evaluate("window.__clicks") if not page.is_closed() else []
            ticks = page.evaluate("window.__ticks ? window.__ticks() : []") if not page.is_closed() else []
            url = page.url
        finally:
            browser.close()
    return out, jack, clicks, ticks, url


def test_three_pages_filled_stepped_and_submitted():
    check = Checks()

    def jack_types_the_city(page, body):
        if "City" in body:
            page.fill("#city", "Springfield")          # a trusted input event
    out, jack, clicks, ticks, url = _run(_site(), "/apply", jack_types_the_city)
    print(out.status, out.reason, out.log)
    check(out.status == "submitted" and url.endswith("/thanks"),
          f"submitted, on the thank-you page ({out.status}, {out.reason}, {url})")
    check(clicks == ["Next", "Next", "Submit application"], f"one press per page, then Submit ({clicks})")
    check(len(jack.bodies) == 1 and "City" in jack.bodies[0], f"one pause, for the city ({jack.bodies})")
    check(out.pages_filled == 3 and len(out.steps) == 3, "three pages filled and recorded")
    check(out.record and out.record.get("wizard") is True and out.record["steps"] == 3,
          "the final record says it is a wizard run of three steps")
    by_label = {f["label"]: f["ref"] for s in out.steps for f in s["fields"]}
    check(by_label.get("City") in (out.record or {}).get("human_reviewed", []),
          "the city Jack typed counts as his answer")
    snap = (out.record or {}).get("snapshot") or {}
    check(snap.get(by_label.get("First Name")) == "Test" and snap.get(by_label.get("City")) == "Springfield",
          "every page's read-back is in the final record")
    check(out.evidence.get("generic_text") and "Thank you for applying" in out.evidence.get("snippet", ""),
          f"the confirmation sentence is the evidence ({out.evidence})")
    check("Test!" not in json.dumps({k: v for k, v in out.evidence.items() if k != "snippet"}),
          "beyond the matched sentence, no page text is kept")
    kinds = [(e["kind"], e.get("handled")) for e in out.events]
    check(("next", "agent") in kinds and ("gate_pause", "jack") in kinds, f"events with who handled them ({kinds})")
    check(out.gate_result and out.gate_result["content_verdict"] == "would_submit",
          f"the final gate said would_submit ({out.gate_result and out.gate_result['content_reasons']})")
    check.done()


def test_a_timed_out_pause_parks_the_job_and_presses_nothing_more():
    check = Checks()
    out, jack, clicks, ticks, url = _run(_site(), "/apply", act=None)
    check(out.status == "needs_you" and "City" in out.reason, f"parked with the reason ({out.reason})")
    check(clicks == ["Next"], f"Next on page one only; nothing after the stop ({clicks})")
    check(url.endswith("/apply/2"), "left on page two for Jack")
    check.done()


def test_a_preticked_marketing_box_keeps_submit_unpressed():
    check = Checks()
    marketing = ('<input type="checkbox" id="news" checked><label for="news">'
                 'Send me the newsletter and job alerts</label>')

    def jack_types_the_city_only(page, body):
        if "City" in body:
            page.fill("#city", "Springfield")
    out, jack, clicks, ticks, url = _run(_site(marketing), "/apply", jack_types_the_city_only)
    check(out.status == "needs_you" and "Submit application" not in clicks,
          f"the gate stopped on the ticked newsletter box; Submit never pressed ({out.status}, {clicks})")
    check(any("newsletter" in r.lower() for r in (out.gate_result or {}).get("content_reasons", [])),
          f"the reason names the box ({(out.gate_result or {}).get('content_reasons')})")
    check(dict(ticks).get("tos") is not True, "the terms box wasn't ticked either (no submit, no tick)")
    check.done()


def test_a_busy_terminal_pause_waives_nothing():
    # BLOCK fix: only a pause Jack ended with Enter counts. A pause that ends
    # because the terminal is still owed a line (no_terminal) is as
    # unattended as a timeout: nothing reviewed, nothing more pressed.
    check = Checks()
    out, jack, clicks, ticks, url = _run(_site(), "/apply", act=None, how=pause.NO_TERMINAL)
    check(out.status == "needs_you" and "City" in out.reason and clicks == ["Next"],
          f"parked after page two, nothing more pressed ({out.status}, {out.reason}, {clicks})")
    check(not out.record and all(e.get("handled") is None for e in out.events if e["kind"] == "gate_pause"),
          f"no field reviewed, the pause not handled ({[e for e in out.events if e['kind'] == 'gate_pause']})")
    check.done()


def test_a_field_the_filler_typed_and_the_page_changed_is_not_jacks():
    # BLOCK fix: the filler's own (trusted, Playwright-sent) input used to
    # leave the same mark as Jack's. Now the filler works under the agent
    # flag and only a mark new since before the pause counts, so a box the
    # page rewrites during the pause isn't his.
    check = Checks()

    def jack_types_the_city_while_the_page_rewrites_the_email(page, body):
        if "City" in body:
            page.fill("#city", "Springfield")                       # Jack: a trusted input
            page.evaluate("document.getElementById('em2').value = 'rewritten@example.invalid'")
    out, jack, clicks, ticks, url = _run(_site(email_on_2=True), "/apply",
                                         jack_types_the_city_while_the_page_rewrites_the_email)
    by_label = {}
    for st in out.steps:
        for f in st["fields"]:
            by_label.setdefault(f["label"], []).append(f["ref"])
    reviewed = (out.record or {}).get("human_reviewed", [])
    em2 = [r for r in by_label.get("Email", []) if r in {f["ref"] for f in out.steps[1]["fields"]}]
    check(out.status == "submitted", f"the run still submitted ({out.status}, {out.reason})")
    check(by_label.get("City", [None])[0] in reviewed, "the city Jack typed is his")
    check(em2 and em2[0] not in reviewed,
          f"the email the filler typed and the page rewrote is not his ({em2}, {reviewed})")
    detail = next(e["detail"] for e in out.events if e["kind"] == "gate_pause")
    check(detail == "1 field(s) touched by Jack", f"one touch counted, the filler's not ({detail})")
    check.done()


def test_a_pause_on_the_final_page_still_lets_the_terms_box_be_ticked():
    # FLAG fix: a required terms box left unticked at a pause is not
    # "reviewed" (a checkbox counts only when touched), so the gate still
    # plans its tick and the application is submitted.
    check = Checks()

    def jack_types_the_city(page, body):
        if "City" in body:
            page.fill("#city", "Springfield")
    out, jack, clicks, ticks, url = _run(_site(city_on_3=True), "/apply", jack_types_the_city)
    check(out.status == "submitted" and clicks == ["Next", "Next", "Submit application"],
          f"submitted after a pause on the final page ({out.status}, {out.reason}, {clicks})")
    check(len(jack.bodies) == 1 and "City" in jack.bodies[0], f"one pause, for the city ({jack.bodies})")
    check.done()


def test_the_final_pause_and_the_record_merge_on_their_own():
    # The final pause: only ANSWERED counts there too. The record: earlier
    # pages' post-fill census and pre-fill pair boxes travel into it.
    from playwright.sync_api import sync_playwright
    check = Checks()
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.set_content("<form><label for='a'>A</label><input id='a' name='a'></form>")
            w = wizard.Wizard(page, _profile(), url="https://x.example/apply", portal="generic",
                              portal_method="default", company="", role="", resume=None,
                              posting_text="", pauser=_Jack(page, None, pause.NO_TERMINAL),
                              echo=lambda *a, **k: None, reload_profile=lambda: (_ for _ in ()).throw(AssertionError("reloaded")))
            w._final_pause(type("S", (), {"reason": "the gate stopped: x"})())
            check(w.out.events[-1]["kind"] == "gate_pause" and w.out.events[-1]["handled"] is None,
                  "a no_terminal final pause is logged unhandled (so the gate stops) and reloads nothing")
            w.pauser = _Jack(page, lambda pg, body: None)      # Jack answers this time
            w.reload_profile = lambda: _profile()
            w._final_pause(type("S", (), {"reason": "the gate stopped: x"})())
            check(w.out.events[-1]["handled"] == "jack", "an answered final pause is Jack's")
            # The merge: an Ashby pair on an earlier page, answered Yes: its
            # hidden box read unticked before the fill and ticked after. With
            # both states in the final record the gate's pair exemption holds;
            # without the pre-fill state the box is hidden-and-ticked.
            from agent import gate
            box = {"ref": "g1-f9", "checked": True, "visible": False, "label_source": "none",
                   "label_inner": "", "label_content": "", "described": "",
                   "in_button_group": True, "group_ref": "g1-f8"}
            w.census = {"g1-f9": box}
            w.prefill = {"g1-f8": [{**box, "checked": False}]}
            pair = {"ref": "g1-f8", "type": "radio", "widget": "buttons", "label": "Relocate?",
                    "options": ["Yes", "No"], "option_refs": ["g1-f8", "g1-f8b"]}
            w.out.steps = [{"fields": [pair], "categories": {"g1-f8": "preference"},
                            "plan": {"g1-f8": {"value": "Yes", "source": "profile",
                                               "profile_path": "eligibility.willing_to_relocate",
                                               "fit": "bool"}},
                            "flags": [], "results": {"g1-f8": {"filled": True, "unconfirmed": False}},
                            "prefill_census": None}]
            w.snapshots = {"g1-f8": "Yes"}
            rec = w._record(w.out.steps, [], wizard=True)
            refs = [b.get("ref") for b in rec["census"]]
            check("g1-f9" in refs and any(b.get("group_ref") == "g1-f8" for b in rec["prefill_census"]),
                  f"earlier pages' boxes, before and after, are in the final record ({refs})")
            reasons = gate.decide(rec, _profile())["content_reasons"]
            check(not any("hidden, and ticked" in r for r in reasons),
                  f"the pair's Yes on an earlier page passes with its pre-fill state ({reasons})")
            w.prefill = {}
            reasons = gate.decide(w._record(w.out.steps, [], wizard=True), _profile())["content_reasons"]
            check(any("hidden, and ticked" in r for r in reasons),
                  "  ...and stops without it (control)")
            # A failed read of the touch marks before a pause waives nothing,
            # even when Jack's mark is on the field afterwards.
            from agent import extract

            def jack_marks_and_types(pg, body):
                pg.fill("#a", "typed")
                pg.eval_on_selector("#a", "(el, a) => el.setAttribute(a, '1')", ledger.TOUCH_ATTR)
            w2 = wizard.Wizard(page, _profile(), url="https://x.example/apply", portal="generic",
                               portal_method="default", company="", role="", resume=None,
                               posting_text="", pauser=_Jack(page, jack_marks_and_types),
                               echo=lambda *a, **k: None)
            fields = extract.extract_fields(page)
            fill = type("F", (), {})()
            fill.fields, fill.plan, fill.snapshot, fill.flags = fields, {}, {}, []
            fill.step = {"results": {}}
            real = w2._marked
            calls = {"n": 0}

            def flaky(refs):
                calls["n"] += 1
                return None if calls["n"] == 1 else real(refs)
            w2._marked = flaky
            w2._gate_pause(fill, {"content_reasons": ["'A': left blank"]})
            check(page.evaluate(f"document.getElementById('a').hasAttribute('{ledger.TOUCH_ATTR}')"),
                  "the mark is on the field (so the next check means something)")
            check(fields[0]["ref"] not in w2.human_reviewed
                  and w2.out.events[-1]["detail"] == "0 field(s) touched by Jack",
                  f"with the before-read failed, no mark counts as new ({w2.out.events[-1]})")
            # A failed read-back after the pause: only a touch of Jack's counts.
            page.set_content("<form><label for='a'>A</label><input id='a' name='a'>"
                             "<label for='b'>B</label><input id='b' name='b'></form>")
            w3 = wizard.Wizard(page, _profile(), url="https://x.example/apply", portal="generic",
                               portal_method="default", company="", role="", resume=None,
                               posting_text="", pauser=_Jack(page, lambda pg, body: None),
                               echo=lambda *a, **k: None)
            fields = extract.extract_fields(page)
            fill = type("F", (), {})()
            fill.fields, fill.plan, fill.flags = fields, {}, []
            fill.snapshot = {fields[0]["ref"]: "", fields[1]["ref"]: ""}
            fill.step = {"results": {}}
            with mock.patch.object(extract, "snapshot", side_effect=RuntimeError("mid-navigation")):
                w3._gate_pause(fill, {"content_reasons": ["'A': left blank"]})
            check(w3.human_reviewed == [] and fill.snapshot == {fields[0]["ref"]: "", fields[1]["ref"]: ""},
                  f"an unreadable read-back reviews nothing and keeps the old snapshot ({w3.human_reviewed})")
        finally:
            browser.close()
    check.done()


def test_a_page_filled_legal_answer_is_not_jacks():
    check = Checks()

    def the_page_fills_it(page, body):
        page.evaluate("window.__autofill()")          # not a trusted input
    out, jack, clicks, ticks, url = _run(_site(), "/legal", the_page_fills_it)
    check(out.status == "needs_you" and "Submit application" not in clicks,
          f"a legal radio filled by script is not Jack's answer: parked, no Submit ({out.status}, {clicks})")
    check(not (out.record or {}).get("human_reviewed"), "nothing counted as reviewed by Jack")
    check.done()


if __name__ == "__main__":
    run_as_script(globals())
