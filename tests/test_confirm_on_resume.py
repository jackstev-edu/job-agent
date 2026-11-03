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
from test_wizard import CLICKS_JS, HOST, _routes

# Every fixture records clicks and key presses into window.__clicks, so
# "nothing pressed" is a real assertion (b499329's review, finding 4).
KEYS_JS = """<script>document.addEventListener('keydown', e => { window.__clicks = window.__clicks || []; window.__clicks.push('key:' + e.key); }, true);
document.addEventListener('click', e => { if (e.target.closest('input, select, textarea, label')) { window.__clicks = window.__clicks || []; window.__clicks.push('box:' + (e.target.id || e.target.name || e.target.tagName)); } }, true);</script>"""
EMPTY = f"""<!doctype html><html><head><title>Apply - Example Co</title></head><body>
<h1>Your application</h1><p>We are getting your application ready.</p>{CLICKS_JS}{KEYS_JS}</body></html>"""
SUCCESS_SEARCH = f"""<!doctype html><html><head><title>Example Co</title></head><body>
<header><input type="search" name="q" aria-label="Search jobs"></header>
<h1>Thank you for applying!</h1><p>We will be in touch.</p>{CLICKS_JS}{KEYS_JS}</body></html>"""
THANKS_FEEDBACK = f"""<!doctype html><html><head><title>Example Co</title></head><body>
<h1>Thank you for applying</h1><form><label for="fb">How was your experience?</label>
<input id="fb" name="feedback"></form>{CLICKS_JS}{KEYS_JS}</body></html>"""
ORDINARY = f"""<!doctype html><html><head><title>Example Co</title></head><body>
<h1>Thank you for applying so far</h1><p>One more step.</p><form><label for="ref">Reference name</label>
<input id="ref" name="reference_name" required><button type="button">Next</button></form>{CLICKS_JS}{KEYS_JS}</body></html>"""
# The form inside a <footer> on an ordinary address (b499329's review, the
# BLOCK): the fillable-control probe must still count it.
FOOTER_FORM = f"""<!doctype html><html><head><title>Example Co</title></head><body>
<h1>Thank you for applying so far</h1><footer><form><label for="ref">Reference name</label>
<input id="ref" name="reference_name" required><button type="button">Next</button></form></footer>{CLICKS_JS}{KEYS_JS}</body></html>"""


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
    check(out.evidence.get("generic_text") and out.evidence.get("no_form") is False
          and out.evidence.get("success_address") is True and out.evidence.get("shape_changed") is True,
          f"the sentence, the header's search box still a fillable control, and the address on a page with no paused shape ({out.evidence})")
    check(any(e.get("kind") == "confirmation" and e.get("handled") == "jack" for e in out.events),
          "the confirmation event is Jack's")
    check(after == before == 0, "nothing pressed, nothing typed: read-only (the fixture records clicks and keys)")
    check.done()


def test_the_address_rule_alone_confirms_beside_a_feedback_box():
    # Fails on 5ba4a07: no address rule, and a park with no fill ran no check.
    check = Checks()
    first, parked, out, before, after = _park_then("/careers/thanks", THANKS_FEEDBACK)
    check(parked, "the empty page parks with no fill in hand")
    check(out.status == "submitted" and out.submitted_by == "jack"
          and out.evidence.get("no_form") is False and out.evidence.get("success_address") is True,
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


def test_a_moved_pause_on_the_unchanged_form_at_a_confirmation_address_is_not_confirmed_and_a_footer_form_counts():
    # Fold of ba21953's review, findings 1 and 3. A "didn't move on after
    # Next" pause keeps its shape under "before": on ba21953 the check read
    # no shape, counted the page as changed and the address rule confirmed
    # the unchanged form. The footer case here starts from a pause WITH a
    # shape, so the paused form is gone and only the probe can refuse.
    from test_pick_up import CITY_GATES_NEXT, REFERENCE, _page, _run
    check = Checks()
    # The sentence sits on the paused page itself (the mock's PROSE slot is step 3's).
    prose = "<p>Thank you for applying. Please review your answers before submitting.</p>"
    with _run(_page(city=CITY_GATES_NEXT + prose, next_disabled=True), start="/apply/confirmation", auto_submit=lambda: True) as (page, w):
        out = w.run()
        check(out.status == wizard.WAITING and w.pending.get("shape") is not None,
              f"a pause with the page's shape in hand (Next stayed disabled) ({out.status}: {w.pending.get('kind')})")
        # The GUI's moved pause keeps its shape under "before" (wizard._pause's
        # extra), not "shape": the pending is rebuilt in that form.
        shape = w.pending.pop("shape")
        w.pending.update({"kind": "moved", "before": shape, "body": "the page didn't move on after Next"})
        out = w.continue_from({})
        check(out.status != "submitted" and out.submitted_by is None,
              f"Continue on the unchanged form at /apply/confirmation after a moved pause: not confirmed ({out.status}: {out.reason})")
    with _run(_page(city=REFERENCE), start="/steps", auto_submit=lambda: True) as (page, w):
        out = w.run()
        check(out.status == wizard.WAITING and w.pending["kind"] == "gate", f"the gate pause on the reference box ({out.status})")
        page.set_content(FOOTER_FORM.replace("Thank you for applying so far", "Thank you for applying"))
        page.wait_for_timeout(300)
        out = w.continue_from({})
        check(out.status != "submitted" and out.submitted_by is None,
              f"the paused form gone, the sentence shown, the new form inside a <footer>: the probe still counts it, not confirmed ({out.status}: {out.reason})")
    check.done()


def test_a_pause_whose_shape_could_not_be_read_is_not_confirmed_by_the_address_rule_on_the_pick_up_path():
    # Fix of 7ef5c9b's BLOCK: an unreadable shape at pause time made
    # _changed_since say "changed", and _pick_up then ran the check with
    # changed=True, so the address rule confirmed the unchanged form. On
    # 7ef5c9b this run ends "submitted".
    from test_pick_up import REFERENCE, _page, _run
    check = Checks()
    prose = "<p>Thank you for applying. Please review your answers before submitting.</p>"
    with _run(_page(city=REFERENCE + prose), start="/apply/confirmation", auto_submit=lambda: True) as (page, w):
        out = w.run()
        check(out.status == wizard.WAITING and w.pending["kind"] == "gate", f"the gate pause on the reference box ({out.status})")
        w.pending["shape"] = None                       # the read failed at pause time
        out = w.continue_from({})
        check(out.status != "submitted" and out.submitted_by is None,
              f"an unreadable pause shape on the unchanged form at /apply/confirmation: not confirmed on any path ({out.status}: {out.reason})")
    check.done()


def test_a_form_inside_a_footer_still_counts_and_an_unchanged_form_at_a_confirmation_address_is_not_confirmed():
    # Fix of b499329's BLOCK and its finding 2. On b499329 the footer form
    # was read as "no fillable control" and the page marked submitted; a
    # gate pause on the unchanged form at a /confirmation address was
    # confirmed by the address rule alone.
    from test_pick_up import REFERENCE, _page, _run
    check = Checks()
    first, parked, out, before, after = _park_then("/apply/step4", FOOTER_FORM)
    check(parked, "the empty page parks with no fill in hand")
    check(out.status != "submitted" and out.submitted_by is None and (out.evidence or {}).get("no_form") is not True,
          f"the sentence with the form inside a <footer>: never marked submitted ({out.status}, {out.reason})")
    prose = "<p>Thank you for applying. Please review your answers before submitting.</p>"
    with _run(_page(city=REFERENCE + prose), start="/apply/confirmation", auto_submit=lambda: True) as (page, w):
        out = w.run()
        check(out.status == wizard.WAITING and w.pending["kind"] == "gate", f"the gate pause on the reference box ({out.status}: {out.reason})")
        out = w.continue_from({})
        check(out.status != "submitted" and out.submitted_by is None,
              f"Continue on the unchanged form at /apply/confirmation: not confirmed by the address alone ({out.status}: {out.reason})")
    check.done()


if __name__ == "__main__":
    run_as_script(globals())
