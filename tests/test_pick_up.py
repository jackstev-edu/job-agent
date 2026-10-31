"""
test_pick_up.py — Continue picks up from whatever page the tab shows.

WHAT THIS DOES: Serves a mock application whose three steps all live at
the SAME address (the form is swapped in place, as Avature's is) and
runs the wizard in GUI mode. A gate pause on step 2 is ended by Jack
fixing the page and pressing the page's own Next to step 3 before he
presses Continue: the resume drops step 2's fill instead of re-gating
it, scans step 3 afresh and goes on to Submit. The same after a final
pause and a parked pause when the tab shows the thank-you page: the
job is marked submitted by Jack. A box Jack typed into on step 3 before
Continue is held, never typed over. The unchanged page still remaps
and re-gates (a guard). A step 3 whose prose says "your application has
been received" beside its boxes is filled, never marked submitted (the
strict check: the generic sentence needs a page with no fillable field).
WHY IT MATTERS: Jack's Avature run (2026-10-29) re-gated step 2's old
record on every Continue while the tab showed step 3, and he had to
finish by hand. Decision A: "when I click continue it should pick up
from whatever page I'm on."

Run from anywhere:  .venv/Scripts/python.exe tests/test_pick_up.py
"""
import contextlib
import io
from contextlib import redirect_stdout
from unittest import mock

from _fill_run import _headless, classify_by_label, map_from_profile
from _harness import Checks, run_as_script
from agent import ledger, pause, submit as submit_mod, wizard
from test_wizard import CLICKS_JS, HOST, _profile, _routes

# Three steps at one address: each Next swaps the next form in; Submit
# swaps in the thank-you prose. PROSE, CITY and EXTRA are the variants.
STEPS = f"""<!doctype html><html><head><title>Engineer - Example Co</title></head><body>
<h1 id="h">Step 1 of 3</h1><div id="app"></div>
<template id="s1"><form><label for="fn">First Name</label><input id="fn" name="first_name" required>
<label for="em">Email</label><input id="em" name="email" type="email" required>
<button type="button" onclick="show(2)">Next</button></form></template>
<template id="s2"><form>CITY
<button type="button" id="n2" onclick="show(3)">Next</button></form></template>
<template id="s3"><form id="f3">PROSE<label for="em2">Email</label><input id="em2" name="email2">
<label for="pc">Postal code</label><input id="pc" name="postal_code">
EXTRA<button type="submit">Submit application</button></form></template>
<template id="done"><p>Thank you for applying to Example Co!</p></template>
<script>
function show(n) {{
  const app = document.getElementById('app');
  app.replaceChildren(document.getElementById(n === 'done' ? 'done' : 's' + n).content.cloneNode(true));
  document.getElementById('h').textContent = n === 'done' ? 'Done' : 'Step ' + n + ' of 3';
  const f = app.querySelector('form');
  if (f) f.addEventListener('submit', e => {{ e.preventDefault(); show('done'); }});
}}
show(1);
</script>{CLICKS_JS}</body></html>"""

CITY_REQUIRED = '<label for="city">City</label><input id="city" name="city" required>'
CITY_OPTIONAL = CITY_REQUIRED.replace(" required", "")
# A City the agent leaves empty (optional) and a Next that stays disabled until it is typed.
CITY_GATES_NEXT = ('<label for="city">City</label><input id="city" name="city" '
                   'oninput="document.getElementById(\'n2\').disabled = !this.value">')
REFERENCE = '<label for="ref">Reference name</label><input id="ref" name="reference_name" required>'
RECEIVED = "<p>Your application has been received by our team; one more step.</p>"


def _page(city=CITY_REQUIRED, prose="", extra="", next_disabled=False):
    html = STEPS.replace("CITY", city).replace("PROSE", prose).replace("EXTRA", extra)
    return html.replace('id="n2"', 'id="n2" disabled') if next_disabled else html


def _gui(page, start, auto_submit=lambda: True):
    pauser = pause.GuiPauser(notify_fn=lambda t, b, echo=print: None, echo=lambda *a, **k: None)
    return wizard.Wizard(page, _profile(), url=f"{HOST}{start}", portal="generic", portal_method="default",
                         company="Example Co", role="Engineer", resume=None, posting_text="",
                         pauser=pauser, echo=lambda *a, **k: None, auto_submit=auto_submit)


@contextlib.contextmanager
def _run(html, start="/steps", auto_submit=lambda: True):
    """A GUI-mode wizard on the mock, the model stubbed; yields (page, wizard)."""
    with _headless(_routes({start: html})) as ctx:
        ctx.add_init_script(ledger.ACTIVITY_INIT_SCRIPT)
        page = ctx.new_page()
        page.goto(f"{HOST}{start}")
        with mock.patch("agent.classify.classify_fields", classify_by_label), \
                mock.patch("agent.mapper.map_fields", map_from_profile), \
                mock.patch.object(wizard, "FORM_WAIT_S", 1), redirect_stdout(io.StringIO()):
            yield page, _gui(page, start, auto_submit)


def _heading(page):
    return page.evaluate("document.getElementById('h').textContent")


def test_continue_after_a_gate_pause_picks_up_step_3_not_step_2s_record():
    # Fails on 07b3c62: step 2's record is remapped and re-gated ("required,
    # but empty" on City) on every Continue while the tab shows step 3.
    # Jack's own flow: the switch OFF, so step 3 parks ready and he presses
    # Submit himself; the dropped page's fill-time verdict stands in the
    # full record (Jack, 2026-10-31), so the ready row says the gate would
    # stop on City.
    check = Checks()
    with _run(_page(), auto_submit=lambda: False) as (page, w):
        out = w.run()
        check(out.status == wizard.WAITING and w.pending["kind"] == "gate" and _heading(page) == "Step 2 of 3",
              f"the gate pauses on step 2 ({out.status}, {out.reason})")
        page.fill("#city", "Springfield")              # Jack fixes the page by hand
        page.click("#n2")                              # and presses the page's own Next
        check(_heading(page) == "Step 3 of 3", "the tab shows step 3 before Continue")
        out = w.continue_from({})
        check(out.status == wizard.WAITING and w.pending["kind"] == "ready" and _heading(page) == "Step 3 of 3",
              f"Continue filled step 3 and parked it ready ({out.status}, {out.reason})")
        check(out.pages_filled == 3 and page.input_value("#em2") == "test@example.invalid",
              f"step 3 was scanned and filled afresh ({out.pages_filled}, {page.input_value('#em2')!r})")
        check("the page changed since the gate pause: picked up from the page shown" in out.log
              and not any(l.startswith("re-scanned after other jobs ran") for l in out.log)
              and not any(l.startswith("gate stopped") for l in out.log[out.log.index("resumed after a gate pause"):]),
              f"step 2's record was neither remapped nor re-gated ({out.log})")
        check("the gate would stop" in out.reason and "'City'" in out.reason,
              f"the ready row carries the dropped page's fill-time verdict ({out.reason[:160]})")
        page.click("button[type=submit]")              # Jack's own Submit
        out = w.continue_from({})
        check(out.status == "submitted" and out.submitted_by == "jack",
              f"and Continue after his press marks it submitted by him ({out.status}, {out.submitted_by})")
    check.done()


def test_continue_after_a_final_pause_on_the_thank_you_page_marks_submitted_by_jack():
    # Fails on 07b3c62: the final pause resumes at Next / Submit on the
    # thank-you page and parks "no Next or Submit control found".
    check = Checks()
    real = submit_mod.submit_application
    calls = {"n": 0}

    def stops_once(page_, record, prof, **kw):
        # A stand-in for a stop only the full record shows (as
        # tests/test_resume_final.py uses): the first call stops without
        # pressing anything; it is never called again here.
        calls["n"] += 1
        return submit_mod.Submission("needs_you", "the gate stopped: run: a stop only the full record shows",
                                     {"content_reasons": ["run: a stop only the full record shows"]},
                                     steps=kw.get("steps_log"))
    with _run(_page(city=CITY_OPTIONAL)) as (page, w), \
            mock.patch.object(submit_mod, "submit_application", stops_once):
        out = w.run()
        check(out.status == wizard.WAITING and w.pending["kind"] == "final" and _heading(page) == "Step 3 of 3",
              f"the final page pauses ({out.status}, {out.reason})")
        page.click("button[type=submit]")              # Jack's own Submit
        check(_heading(page) == "Done", "the tab shows the thank-you page before Continue")
        clicks_before = page.evaluate("window.__clicks")
        out = w.continue_from({})
        check(out.status == "submitted" and out.submitted_by == "jack" and out.reason == wizard.READY_SUBMITTED,
              f"Continue marked it submitted by Jack ({out.status}, {out.submitted_by}, {out.reason})")
        check(page.evaluate("window.__clicks") == clicks_before, "the agent pressed nothing")
        check(out.record is not None and out.gate_result is not None and out.evidence.get("generic_text") is True,
              "the full record and its verdict go with the confirmation for gate_runs")
        check(any(e["kind"] == "confirmation" and e["handled"] == "jack" for e in out.events)
              and calls["n"] == 1,
              "the confirmation event is Jack's and nothing was gated for a press again")
    check.done()


def test_continue_after_a_parked_pause_on_the_thank_you_page_marks_submitted_by_jack():
    # Fails on 07b3c62: the park's resume rescans the thank-you page and
    # parks again, "a page with no fields, no Next and no Submit".
    check = Checks()
    with _run(_page(city=CITY_GATES_NEXT, next_disabled=True)) as (page, w):
        out = w.run()
        check(out.status == wizard.WAITING and w.pending["kind"] == "parked" and "disabled" in out.reason
              and w.pending.get("fill") is not None,
              f"step 2 parks on its disabled Next with the fill in hand ({out.status}, {out.reason})")
        page.fill("#city", "Springfield")              # enables Next
        page.click("#n2")
        page.click("button[type=submit]")              # Jack submits step 3 himself
        check(_heading(page) == "Done", "the tab shows the thank-you page before Continue")
        out = w.continue_from({})
        check(out.status == "submitted" and out.submitted_by == "jack",
              f"Continue marked it submitted by Jack ({out.status}, {out.submitted_by}, {out.reason})")
    check.done()


def test_an_unchanged_page_is_still_remapped_and_re_gated():
    # A guard: on 07b3c62 this already passes. Mutation check: with
    # Wizard._changed_since forced True, the fill is dropped, City is
    # held by the fresh scan and the gate stops again (waiting, not
    # submitted).
    check = Checks()
    with _run(_page()) as (page, w):
        out = w.run()
        page.fill("#city", "Springfield")              # Jack answers in place; the page stays
        out = w.continue_from({})
        check(out.status == "submitted" and out.submitted_by == "agent" and out.pages_filled == 3,
              f"the same page is judged again and the run goes on ({out.status}, {out.reason})")
        check("1 field(s) touched by Jack" in " ".join(e.get("detail", "") for e in out.events),
              "City counted as his touch at the resume")
        check("picked up from the page shown" not in " ".join(out.log), "nothing was dropped")
    check.done()


def test_a_box_jack_typed_into_on_step_3_before_continue_is_held():
    # Fails on 07b3c62: step 3 is never scanned (step 2 is re-gated).
    check = Checks()
    with _run(_page()) as (page, w):
        out = w.run()
        page.fill("#city", "Springfield")
        page.click("#n2")
        page.fill("#pc", "99999")                      # his own value on step 3, before Continue
        out = w.continue_from({})
        check(page.input_value("#pc") == "99999", f"the box keeps his value ({page.input_value('#pc')!r})")
        check(any(l.startswith("left as it is (holds a value") and "Postal code" in l for l in out.log),
              f"the fresh scan held it ({[l for l in out.log if 'left as' in l]})")
        check(out.pages_filled == 3 and out.status == wizard.WAITING and w.pending["kind"] == "gate",
              f"the gate judges the held box for him ({out.status}, {out.reason})")
    check.done()


def test_a_step_whose_prose_says_received_beside_its_boxes_is_filled_not_marked_submitted():
    # Jack's addition (2026-10-31). Mutation check: with the no-fields
    # condition removed from Wizard._confirmed_strict, Continue marks the
    # job submitted by Jack on step 3 and fills nothing.
    check = Checks()
    with _run(_page(prose=RECEIVED), auto_submit=lambda: False) as (page, w):
        out = w.run()
        page.fill("#city", "Springfield")
        page.click("#n2")
        check(_heading(page) == "Step 3 of 3" and "has been received" in page.inner_text("body"),
              "step 3 shows the sentence and its boxes")
        out = w.continue_from({})
        check(out.status == wizard.WAITING and w.pending["kind"] == "ready" and out.submitted_by is None
              and out.pages_filled == 3 and page.input_value("#em2") == "test@example.invalid"
              and _heading(page) == "Step 3 of 3",
              f"step 3 was filled and parked ready, not marked submitted ({out.status}, "
              f"{out.submitted_by}, {out.pages_filled})")
    check.done()


if __name__ == "__main__":
    run_as_script(globals())
