"""
test_resume_final.py — Continue after a final-page stop or a CAPTCHA can submit.

WHAT THIS DOES: Runs the wizard in GUI mode on the single-page mock
portal. A CAPTCHA on the form pauses the job; Jack solves it in the tab
(the marker goes away) and presses Continue: the pause's event becomes
his, the page is filled, the gate passes and the form is submitted. If
he presses Continue with the CAPTCHA still there, the job waits again
and nothing is submitted. A final-page gate stop (a stand-in for a stop
only the full record shows) pauses the job; Continue with the stop
cleared re-gates and submits; Continue with it still present waits again.
WHY IT MATTERS: In GUI mode these two pauses left their event unhandled
forever (the line that marked it came after the deferred pause), so the
full-record gate stopped on every Continue and the job could never
submit (reviewer on a05bc54; Jack's first GUI run hit the final-page
case on a single-page Greenhouse form).

Run from anywhere:  .venv/Scripts/python.exe tests/test_resume_final.py
"""
from unittest import mock

from _harness import Checks, run_as_script
from agent import submit as submit_mod, wizard
from test_resume import SINGLE, _drive, _gui_site

# The single-page form with a bot check on it (browser.detect_captcha's
# "bot check" marker is a visible element whose class names captcha).
CAPTCHA_FORM = SINGLE.replace('<form id="f">', '<div class="captcha-box" style="width:300px;height:80px">'
                                                 'I am not a robot</div><form id="f">')


def _site_with_captcha():
    site = _gui_site()
    site["/captcha"] = CAPTCHA_FORM
    return site


def test_a_captcha_jack_solved_is_his_on_continue_and_the_form_is_submitted():
    check = Checks()

    def jack_solves_it(page, state, job_id):
        page.evaluate("document.querySelector('.captcha-box').remove()")
    out, pauses, url, clicks, w = _drive(_site_with_captcha(), "/captcha", [jack_solves_it])
    check(pauses and pauses[0]["pause"]["kind"] == "captcha", f"the first pause is the CAPTCHA ({[p['pause']['kind'] for p in pauses]})")
    captcha_events = [e for e in w.out.events if e["kind"] == "captcha"]
    check(captcha_events and all(e["handled"] == "jack" for e in captcha_events),
          f"on Continue the CAPTCHA event is Jack's ({captcha_events})")
    check(out.status == "submitted" and url.endswith("/thanks") and "Submit application" in clicks,
          f"the form is filled, gated and submitted ({out.status}, {out.reason}, {url}, {clicks})")
    check(len(pauses) == 1, f"one pause only ({len(pauses)})")
    check.done()


def test_continue_with_the_captcha_still_there_waits_again_and_submits_nothing():
    check = Checks()
    out, pauses, url, clicks, w = _drive(_site_with_captcha(), "/captcha", [lambda *a: None], max_pauses=2)
    check(out.status == wizard.WAITING and len(pauses) == 2
          and all(p["pause"]["kind"] == "captcha" for p in pauses),
          f"it waits again for the CAPTCHA ({out.status}, {[p['pause']['kind'] for p in pauses]})")
    captcha_events = [e for e in w.out.events if e["kind"] == "captcha"]
    check(captcha_events and all(e["handled"] is None for e in captcha_events),
          f"nothing is marked as Jack's ({captcha_events})")
    check(not url.endswith("/thanks") and "Submit application" not in clicks,
          f"nothing submitted ({url}, {clicks})")
    check.done()


def test_a_page_unreadable_at_continue_does_not_make_the_captcha_jacks():
    # detect_captcha answers "none" on a page it can't read; the resume
    # must not take that for solved.
    from agent import browser as browser_mod
    check = Checks()
    unreadable = {"on": False}
    real = browser_mod.page_readable

    def flaky(page):
        return False if unreadable["on"] else real(page)

    def jack_solves_it_but_the_page_cannot_be_read(page, state, job_id):
        page.evaluate("document.querySelector('.captcha-box').remove()")
        unreadable["on"] = True
    with mock.patch.object(browser_mod, "page_readable", flaky):
        out, pauses, url, clicks, w = _drive(_site_with_captcha(), "/captcha",
                                             [jack_solves_it_but_the_page_cannot_be_read], max_pauses=2)
    captcha_events = [e for e in w.out.events if e["kind"] == "captcha"]
    check(captcha_events and captcha_events[0]["handled"] is None,
          f"the CAPTCHA event stays unhandled when the page couldn't be read at Continue ({captcha_events})")
    check(out.status == wizard.WAITING and "Submit application" not in clicks and not url.endswith("/thanks"),
          f"the job waits again, nothing submitted ({out.status}, {out.reason[:80]}, {clicks})")
    check.done()


def _run_level_stop(stop):
    """
    A stand-in for a stop only the full record shows: the first call of
    submit_application stops without pressing anything while `stop["on"]`
    is set; otherwise the real submit runs.
    """
    real = submit_mod.submit_application

    def wrapped(page, record, prof, **kw):
        if stop["on"]:
            return submit_mod.Submission("needs_you", "the gate stopped: run: a stop only the full record shows",
                                         {"content_reasons": ["run: a stop only the full record shows"]},
                                         steps=kw.get("steps_log"))
        return real(page, record, prof, **kw)
    return wrapped


def test_a_final_page_pause_resumed_with_the_stop_cleared_submits():
    check = Checks()
    stop = {"on": True}

    def jack_clears_it(page, state, job_id):
        stop["on"] = False
    with mock.patch.object(submit_mod, "submit_application", _run_level_stop(stop)):
        out, pauses, url, clicks, w = _drive(_gui_site(), "/single", [jack_clears_it])
    check(pauses and pauses[0]["pause"]["kind"] == "final" and "Before submitting" in pauses[0]["pause"]["message"],
          f"the pause is the final-page one ({[p['pause']['kind'] for p in pauses]}, {pauses[0]['pause']['message'][:60]})")
    finals = [e for e in w.out.events if e["kind"] == "gate_pause" and e["detail"] == "final page"]
    check(finals and all(e["handled"] == "jack" for e in finals), f"on Continue the final-page event is Jack's ({finals})")
    check(out.status == "submitted" and url.endswith("/thanks") and clicks == ["Submit application"],
          f"re-gated and submitted ({out.status}, {out.reason}, {url}, {clicks})")
    check.done()


def test_a_final_page_pause_resumed_with_the_stop_still_present_waits_again():
    check = Checks()
    stop = {"on": True}
    with mock.patch.object(submit_mod, "submit_application", _run_level_stop(stop)):
        out, pauses, url, clicks, w = _drive(_gui_site(), "/single", [lambda *a: None], max_pauses=2)
    check(out.status == wizard.WAITING and len(pauses) == 2 and all(p["pause"]["kind"] == "final" for p in pauses),
          f"it waits again at the final page ({out.status}, {[p['pause']['kind'] for p in pauses]})")
    finals = [e for e in w.out.events if e["kind"] == "gate_pause" and e["detail"] == "final page"]
    # The harness presses Continue after each of its two recorded pauses, so
    # three stops happened: each Continue made the pause before it Jack's,
    # and the newest stop is still open.
    check(len(finals) == 3 and [e["handled"] for e in finals] == ["jack", "jack", None],
          f"each Continue made the pause before it Jack's; the newest stop is open ({finals})")
    check(clicks == [] and not url.endswith("/thanks"), f"nothing submitted ({url}, {clicks})")
    check.done()


if __name__ == "__main__":
    run_as_script(globals())
