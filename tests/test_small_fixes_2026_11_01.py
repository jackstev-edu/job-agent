"""
test_small_fixes_2026_11_01.py — the three small fixes of item 6 (2026-11-01).

WHAT THIS DOES: (a) the override watch's poll makes no page call on
Google's host or a job board; (b) the chooser's file-chooser answer
counts only once set_files went through, and a chooser that refuses the
file parks with a reason that says so instead of logging an upload;
(c) the terminal's "moved" pause picks up from the page shown when Jack
ends it with Enter, as the GUI's Continue does: a thank-you page ends
the job as submitted by Jack; a pause that ends because the page moved
on by itself picks nothing up: the hold, the loop, and the thank-you
page parks for him (decision 3 of the 2026-11-01 fold round).
WHY IT MATTERS: Three reviewer findings of the 2026-10-31 round that
Jack asked to fold (2c42879's 4, 9ccf258's 2, 0b81c4e's 1), and Jack's
decision 3 on the report of the 2026-11-01 round.

Run from anywhere:  .venv/Scripts/python.exe tests/test_small_fixes_2026_11_01.py
"""
import io
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from _fill_run import _headless, classify_by_label, map_from_profile
from _harness import TEST_HOME, Checks, run_as_script
from agent import chooser, corrections, ledger, pause, wizard
from test_pick_up import CITY_OPTIONAL, STEPS, _heading, _routes
from test_wizard import HOST, _profile


def test_the_watch_polls_nothing_on_googles_host_or_a_job_board():
    # Fails on 23999d0: poll evaluated the presence and snapshot scripts on every host.
    check = Checks()
    from agent import extract
    gen = extract._current.get("gen") or "g1"
    extract._current["gen"] = gen
    # A field the snapshot policy lets the watch read: current, not secret,
    # a stable identity, not on a credential step, its signature known.
    fields = [{"ref": f"{gen}-f1", "type": "text", "label": "City", "name": "city", "stable_identity": True,
               "credential_step": False, "sigs": {f"{gen}-f1": ["input", "text", "city"]}}]
    with mock.patch.object(corrections._Hooked, "arm", lambda self, page, fields: None):
        assert corrections.Watch(mock.Mock(url="https://x.example/"), fields).items, "the field must be readable"
        for what, url, calls in (("Google's sign-in site", "https://accounts.google.com/signin/v2", 0),
                                 ("Google on another path", "https://accounts.google.com/o/oauth2/consent", 0),
                                 ("LinkedIn", "https://www.linkedin.com/jobs/view/1", 0),
                                 ("Handshake", "https://app.joinhandshake.com/jobs/2", 0),
                                 ("the application's site", "https://jobs.example-co.com/apply", 1)):
            page = mock.Mock()
            page.url = url
            page.evaluate.return_value = False       # the form is gone: nothing to read anyway
            watch = corrections.Watch(page, fields)
            watch.poll(page, force=True)
            check(page.evaluate.call_count == calls, f"{what}: {page.evaluate.call_count} page call(s), wanted {calls}")
        page = mock.Mock()
        type(page).url = mock.PropertyMock(side_effect=RuntimeError("closed"))
        watch = corrections.Watch(page, fields)
        watch.poll(page, force=True)
        check(page.evaluate.call_count == 0, "an unreadable address: no page call")
    check.done()


def test_a_chooser_that_refuses_the_file_is_not_an_upload():
    # Fails on 23999d0: `answered` was set before set_files, so a refused file
    # was logged as "put resume.pdf in the file chooser it opened".
    check = Checks()
    resume = TEST_HOME / "small-fixes-resume.pdf"
    resume.write_bytes(b"%PDF-1.4 test")
    answer = chooser._Answer(resume)
    bad = mock.Mock()
    bad.set_files.side_effect = RuntimeError("not accepted")
    answer(bad)
    check(answer.answered is False and answer.failed is True and answer.why == "RuntimeError",
          f"a refused set_files: not answered, failed with the error's type only ({answer.answered}, {answer.failed})")
    good = mock.Mock()
    answer(good)
    check(answer.answered is False and good.set_files.call_count == 0, "once failed, no second try on another chooser")
    fresh = chooser._Answer(resume)
    fresh(good)
    check(fresh.answered is True and fresh.failed is False and good.set_files.call_args[0] == (str(resume),),
          "a chooser that takes the file is answered once")
    fresh(good)
    check(good.set_files.call_count == 1, "and only once")
    # Through choose(): the control opens a chooser that refuses the file;
    # the park names the control and says to upload by hand, and no
    # upload is logged.
    html = TEST_HOME / "small-fixes-chooser.html"
    html.write_text("""<!doctype html><html><body><h1>How would you like to apply?</h1>
      <button type="button" onclick="document.getElementById('f').click()">From Device</button>
      <button type="button">Without Resume</button>
      <input id="f" type="file" accept=".txt" style="display:none">
      <p>Or <a href="#">apply with LinkedIn</a></p></body></html>""", encoding="utf-8")
    log = []
    from _harness import mock_page
    with mock_page(html) as page:
        with mock.patch.object(chooser._Answer, "__call__",
                               lambda self, fc: (setattr(self, "failed", True), setattr(self, "why", "Refused"))):
            try:
                chooser.choose(page, resume, log)
                parked = None
            except ValueError as e:
                parked = str(e)
    check(parked is not None and "'From Device'" in parked and "didn't take small-fixes-resume.pdf" in parked
          and "upload it yourself" in parked, f"the park says the chooser refused the file ({parked})")
    check(not any("put" in line for line in log), f"no upload logged ({log})")
    check.done()


class _JackFixesThenSubmits(pause.Pauser):
    """The terminal: at the moved pause Jack arms Next, presses it, and submits the last step himself."""
    deferring = False

    def __init__(self, page):
        super().__init__(notify_fn=lambda *a, **k: None)
        self.page, self.seen = page, []

    def wait(self, title, body, *, prompt, hidden=False, until=None, wants_code=False):
        self.seen.append(body)
        self.page.evaluate("() => { window.__armed = true; }")
        self.page.click("#n2")
        self.page.click("button[type=submit]")
        return None, pause.ANSWERED


def test_the_terminals_moved_pause_picks_up_the_page_shown():
    # Fails on 23999d0: the terminal held and rescanned the thank-you page,
    # parking it "no fields, no Next and no Submit" (needs_you) instead of
    # ending it as submitted by Jack, as Continue does in the GUI. Since
    # 24d5a45's successor (decision 3 of the 2026-11-01 fold round) this is
    # the Enter ending only: the pauser below returns pause.ANSWERED.
    check = Checks()
    html = STEPS.replace("CITY", CITY_OPTIONAL).replace("PROSE", "").replace("EXTRA", "")
    # Step 2's Next does nothing until Jack arms it: the agent's press
    # leaves the page where it is, which is the terminal's moved pause.
    html = html.replace('id="n2" onclick="show(3)"', 'id="n2" onclick="if (window.__armed) show(3)"')
    assert 'window.__armed' in html
    with _headless(_routes({"/steps": html})) as ctx:
        ctx.add_init_script(ledger.ACTIVITY_INIT_SCRIPT)
        page = ctx.new_page()
        page.goto(f"{HOST}/steps")
        pauser = _JackFixesThenSubmits(page)
        with mock.patch("agent.classify.classify_fields", classify_by_label), \
                mock.patch("agent.mapper.map_fields", map_from_profile), \
                mock.patch.object(wizard, "FORM_WAIT_S", 1), redirect_stdout(io.StringIO()):
            w = wizard.Wizard(page, _profile(), url=f"{HOST}/steps", portal="generic", portal_method="default",
                              company="Example Co", role="Engineer", resume=None, posting_text="",
                              pauser=pauser, echo=lambda *a, **k: None, auto_submit=lambda: False)
            out = w.run()
        heading = _heading(page)
    check(len(pauser.seen) == 1 and "didn't move on" in pauser.seen[0], f"one pause, the moved one ({pauser.seen})")
    check(heading == "Done", "Jack's own press and Submit left the tab on the thank-you page")
    check(out.status == "submitted" and out.submitted_by == "jack" and out.reason == wizard.READY_SUBMITTED,
          f"the terminal picked up the page shown: submitted by Jack ({out.status}, {out.submitted_by}, {out.reason})")
    check(any("picked up from the page shown" in l for l in out.log), f"the log says so ({out.log[-4:]})")
    check.done()


class _PageMovesByItself(_JackFixesThenSubmits):
    """The terminal: the page moves on (and lands on the thank-you page) while the wait watches it; no Enter."""

    def wait(self, title, body, *, prompt, hidden=False, until=None, wants_code=False):
        super().wait(title, body, prompt=prompt, hidden=hidden, until=until, wants_code=wants_code)
        return None, pause.FINISHED


def test_the_terminals_moved_pause_that_ends_without_enter_picks_nothing_up():
    # Fails on 24d5a45: the pick-up ran on every ending of the moved pause, so
    # a page that moved on by itself ended the job as submitted by Jack.
    # Decision 3 of the 2026-11-01 fold round: only Enter picks up; a page
    # that moved by itself takes the pre-db933fc path (the hold, the loop,
    # and the thank-you page parks for him).
    check = Checks()
    html = STEPS.replace("CITY", CITY_OPTIONAL).replace("PROSE", "").replace("EXTRA", "")
    html = html.replace('id="n2" onclick="show(3)"', 'id="n2" onclick="if (window.__armed) show(3)"')
    assert 'window.__armed' in html
    with _headless(_routes({"/steps": html})) as ctx:
        ctx.add_init_script(ledger.ACTIVITY_INIT_SCRIPT)
        page = ctx.new_page()
        page.goto(f"{HOST}/steps")
        pauser = _PageMovesByItself(page)
        with mock.patch("agent.classify.classify_fields", classify_by_label), \
                mock.patch("agent.mapper.map_fields", map_from_profile), \
                mock.patch.object(wizard, "FORM_WAIT_S", 1), redirect_stdout(io.StringIO()):
            w = wizard.Wizard(page, _profile(), url=f"{HOST}/steps", portal="generic", portal_method="default",
                              company="Example Co", role="Engineer", resume=None, posting_text="",
                              pauser=pauser, echo=lambda *a, **k: None, auto_submit=lambda: False)
            out = w.run()
        heading = _heading(page)
    check(len(pauser.seen) == 1 and "didn't move on" in pauser.seen[0], f"one pause, the moved one ({pauser.seen})")
    check(heading == "Done", "the tab is on the thank-you page")
    check(out.status != "submitted" and out.submitted_by != "jack" and out.reason != wizard.READY_SUBMITTED,
          f"nothing counted as Jack's: not submitted by him ({out.status}, {out.submitted_by}, {out.reason})")
    check(out.status == "needs_you" and "no fields, no Next and no Submit" in (out.reason or ""),
          f"the thank-you page parks for him, as before db933fc ({out.status}, {out.reason})")
    check(not any("picked up from the page shown" in l for l in out.log)
          and any("moved on by itself" in l and "held" in l for l in out.log),
          f"the log says the page moved by itself under the hold, no pick-up ({out.log[-4:]})")
    check(not any(e.get("kind") == "confirmation" for e in out.events), "no confirmation event")
    check.done()


if __name__ == "__main__":
    run_as_script(globals())
