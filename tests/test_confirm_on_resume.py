"""
test_confirm_on_resume.py — the strict confirmation check on every Continue (Jack, 2026-11-02, item 9).

WHAT THIS DOES: Runs the wizard in GUI mode on a mock site and parks it
with no fill in hand ("a page with no fields, no Next and no Submit"),
then moves the tab to a thank-you page and presses Continue: the job ends
as submitted by Jack, read-only, nothing pressed. Three shapes: a
/Success address whose only fillable control is the site's header search
box; a /thanks address with the generic sentence and a feedback box in
the page body (the address rule alone); and the negative, the generic
sentence beside a box on an ordinary address, which is never marked
submitted.
WHY IT MATTERS: Intuit's Avature /Success page parked "no fields, no
Next and no Submit" on every Continue, because a park with no fill never
ran the confirmation check.

Run from anywhere:  .venv/Scripts/python.exe tests/test_confirm_on_resume.py
"""
import io
from contextlib import redirect_stdout
from unittest import mock

from _fill_run import _headless, classify_by_label, map_from_profile
from _harness import Checks, run_as_script
from agent import ledger, wizard
from test_pick_up import _gui
from test_wizard import HOST, _routes

EMPTY = """<!doctype html><html><head><title>Apply - Example Co</title></head><body>
<h1>Your application</h1><p>We are getting your application ready.</p></body></html>"""
SUCCESS_SEARCH = """<!doctype html><html><head><title>Example Co</title></head><body>
<header><input type="search" name="q" aria-label="Search jobs"></header>
<h1>Thank you for applying!</h1><p>We will be in touch.</p></body></html>"""
THANKS_FEEDBACK = """<!doctype html><html><head><title>Example Co</title></head><body>
<h1>Thank you for applying</h1><form><label for="fb">How was your experience?</label>
<input id="fb" name="feedback"></form></body></html>"""
ORDINARY = """<!doctype html><html><head><title>Example Co</title></head><body>
<h1>Thank you for applying so far</h1><p>One more step.</p><form><label for="ref">Reference name</label>
<input id="ref" name="reference_name" required><button type="button">Next</button></form></body></html>"""


def _park_then(target_path, target_html):
    site = {"/apply": EMPTY, target_path: target_html}
    with _headless(_routes(site)) as ctx:
        ctx.add_init_script(ledger.ACTIVITY_INIT_SCRIPT)
        page = ctx.new_page()
        page.goto(f"{HOST}/apply")
        with mock.patch("agent.classify.classify_fields", classify_by_label), \
                mock.patch("agent.mapper.map_fields", map_from_profile), \
                mock.patch.object(wizard, "FORM_WAIT_S", 1), redirect_stdout(io.StringIO()):
            w = _gui(page, "/apply", auto_submit=lambda: False)
            first = w.run()
            parked = (first.status == wizard.WAITING and w.pending.get("kind") == "parked"
                      and w.pending.get("fill") is None)
            page.goto(f"{HOST}{target_path}")
            clicks_before = page.evaluate("() => (window.__clicks || []).length")
            out = w.continue_from({})
            return first, parked, out, clicks_before, page.evaluate("() => (window.__clicks || []).length")


def test_a_park_with_no_fill_continued_on_a_success_page_is_marked_submitted_by_jack():
    # Fails on 5ba4a07: a park with no fill in hand rescanned the page and
    # parked again ("a page with no fields, no Next and no Submit").
    check = Checks()
    first, parked, out, before, after = _park_then("/apply/Success", SUCCESS_SEARCH)
    check(parked, f"the empty page parks with no fill in hand ({first.status}: {first.reason})")
    check(out.status == "submitted" and out.submitted_by == "jack",
          f"Continue on the /Success page: submitted by Jack ({out.status}, {out.submitted_by}, {out.reason})")
    check(out.evidence.get("generic_text") and out.evidence.get("no_form_outside_chrome") is True
          and out.evidence.get("success_address") is True,
          f"the sentence, no fillable control outside the header, and the address ({out.evidence})")
    check(any(e.get("kind") == "confirmation" and e.get("handled") == "jack" for e in out.events),
          "the confirmation event is Jack's")
    check(after == before, "nothing pressed: read-only")
    check.done()


def test_the_address_rule_alone_confirms_beside_a_feedback_box():
    # Fails on 5ba4a07: no address rule, and a park with no fill ran no check.
    check = Checks()
    first, parked, out, before, after = _park_then("/careers/thanks", THANKS_FEEDBACK)
    check(parked, "the empty page parks with no fill in hand")
    check(out.status == "submitted" and out.submitted_by == "jack"
          and out.evidence.get("no_form_outside_chrome") is False and out.evidence.get("success_address") is True,
          f"a feedback box in the body, a /thanks segment: submitted by Jack ({out.status}, {out.evidence})")
    check.done()


def test_the_generic_sentence_beside_a_box_on_an_ordinary_address_is_never_marked_submitted():
    # A guard of the strict rule: passes on 5ba4a07 too (no check ran at all).
    # Mutation check: with `(no_form or by_address)` dropped from
    # Wizard._confirmed_strict's by_sentence, this fails (marked submitted).
    check = Checks()
    first, parked, out, before, after = _park_then("/apply/step4", ORDINARY)
    check(parked, "the empty page parks with no fill in hand")
    check(out.status != "submitted" and out.submitted_by is None,
          f"the sentence beside a box on /apply/step4: never marked submitted ({out.status}, {out.reason})")
    check.done()


if __name__ == "__main__":
    run_as_script(globals())
