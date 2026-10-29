"""
test_chooser.py — "choose how to apply" pages take one allowed control or park.

WHAT THIS DOES: Serves mock chooser pages built from the real ones
(Avature's Without Resume / From Device / Copy & Paste, as Wayfair's
page showed on 2026-10-20; Workday's Autofill with Resume / Apply
Manually / Use My Last Application; an Ashby-style page with a form and
a LinkedIn autofill button) and drives agent.chooser and the wizard:
with a resume on file the resume goes into Avature's own file input and
the form follows; with none, Without Resume is pressed; Workday's Apply
Manually is pressed; LinkedIn, Indeed, Copy & Paste and Use My Last
Application are never pressed; a chooser with only those parks with the
options shown; a page with a form is not a chooser. No native file
dialog is involved: the file goes through the input, or the chooser
Playwright answers.
WHY IT MATTERS: Jack's first GUI run parked Wayfair's step 1 as "a page
with no fields, no Next and no Submit"; it was this chooser.

Run from anywhere:  .venv/Scripts/python.exe tests/test_chooser.py
"""
import contextlib
import io
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from _fill_run import classify_by_label, map_from_profile
from _harness import TEST_HOME, Checks, mock_page, run_as_script
from agent import chooser, pause, wizard
from test_wizard import _profile

HOST = "https://acme.avature.net"
CLICKS_JS = """<script>
  window.__clicks = JSON.parse(sessionStorage.getItem('clicks') || '[]');
  document.addEventListener('click', e => {
    const b = e.target.closest('a, button, [role=button], input, label');
    if (b) { window.__clicks.push((b.innerText || b.value || b.id || '').trim());
             sessionStorage.setItem('clicks', JSON.stringify(window.__clicks)); }
  }, true);
  window.__files = [];
</script>"""

AVATURE = f"""<!doctype html><html><head><title>Applying to Engineer | Acme</title></head><body>{CLICKS_JS}
<h1>Choose an option to apply</h1>
<a id="methodButton--later" href="/careers/Application?folderId=1">Without Resume</a>
<a id="methodButton--file" href="#" onclick="document.getElementById('resumeFile').click(); return false;">From Device</a>
<a id="methodButton--paste" href="#" onclick="location.href='/paste'; return false;">Copy &amp; Paste</a>
<a href="#" onclick="location.href='/linkedin'; return false;">Apply with LinkedIn</a>
<input type="file" id="resumeFile" style="display:none" onchange="window.__files.push(this.files[0].name); location.href='/careers/Application?folderId=1&parsed=1'">
</body></html>"""

# Jack's 2026-10-29 run: the portal's hidden, pre-existing file input ignores
# a file set on it; From Device opens its own chooser through a second
# input, and only that input's change event moves the page to step 2.
PICKER = f"""<!doctype html><html><head><title>Applying to Engineer | Acme</title></head><body>{CLICKS_JS}
<h1>Choose an option to apply</h1>
<a id="methodButton--later" href="/careers/Application?folderId=1">Without Resume</a>
<a id="methodButton--file" href="#" onclick="const i = document.createElement('input'); i.type = 'file'; i.style.display = 'none';
   i.onchange = () => {{ window.__files.push('picker:' + i.files[0].name); location.href = '/careers/Application?folderId=1&picked=1'; }};
   document.body.appendChild(i); i.click(); return false;">From Device</a>
<a id="methodButton--paste" href="#" onclick="location.href='/paste'; return false;">Copy &amp; Paste</a>
<input type="file" id="resumeFile" style="display:none" onchange="window.__files.push('ignored:' + this.files[0].name)">
</body></html>"""

# Item 2 (Jack, 2026-10-29): a chooser whose From Device takes the file
# and leaves the page as it was, and one that shows step 2 at the SAME
# address (fields appear, the URL unchanged).
STUCK = PICKER.replace("location.href = '/careers/Application?folderId=1&picked=1';", "")
INLINE = PICKER.replace(
    "location.href = '/careers/Application?folderId=1&picked=1';",
    "document.body.appendChild(document.getElementById('step2').content.cloneNode(true));").replace(
    "</body></html>",
    """<template id="step2"><form id="f" onsubmit="event.preventDefault(); location.href = '/thanks'">
<label for="fn">First Name</label><input id="fn" name="first_name" required>
<label for="em">Email</label><input id="em" name="email" type="email" required>
<button type="submit">Submit application</button></form></template></body></html>""")

WORKDAY = f"""<!doctype html><html><head><title>Start Your Application - Acme</title></head><body>{CLICKS_JS}
<h2>Start Your Application</h2>
<a href="/apply/autofill">Autofill with Resume</a>
<a href="/apply/applyManually">Apply Manually</a>
<a href="/apply/last">Use My Last Application</a>
</body></html>"""

ASHBY = f"""<!doctype html><html><head><title>Engineer - Acme</title></head><body>{CLICKS_JS}
<button type="button" onclick="location.href='/linkedin'">Autofill with LinkedIn</button>
<button type="button" onclick="location.href='/indeed'">Apply with Indeed</button>
<form><label for="fn">First Name</label><input id="fn" name="first_name">
<label for="em">Email</label><input id="em" name="email" type="email">
<button type="submit">Submit application</button></form></body></html>"""

ONLY_REFUSED = f"""<!doctype html><html><head><title>Apply - Acme</title></head><body>{CLICKS_JS}
<h1>Choose an option to apply</h1>
<a href="/linkedin">Apply with LinkedIn</a><a href="/indeed">Apply with Indeed</a>
<a href="#" onclick="location.href='/paste'">Copy &amp; Paste</a><a href="/last">Use My Last Application</a>
</body></html>"""

FORM = f"""<!doctype html><html><head><title>Engineer - Acme</title></head><body>{CLICKS_JS}
<form id="f"><label for="fn">First Name</label><input id="fn" name="first_name" required>
<label for="em">Email</label><input id="em" name="email" type="email" required>
<button type="submit">Submit application</button></form>
<script>document.getElementById('f').addEventListener('submit', e => {{ e.preventDefault(); location.href = '/thanks'; }});</script>
</body></html>"""
THANKS = "<!doctype html><html><head><title>Acme</title></head><body><h1>Thank you for applying, Test!</h1></body></html>"


def _routes():
    site = {"/careers": AVATURE, "/careers/Application": FORM, "/workday": WORKDAY, "/ashby": ASHBY,
            "/refused": ONLY_REFUSED, "/thanks": THANKS, "/picker": PICKER, "/stuck": STUCK, "/inline": INLINE}

    def handle(route):
        path = route.request.url.split("avature.net", 1)[-1].split("?")[0]
        route.fulfill(status=200, content_type="text/html", body=site.get(path, "<p>404</p>"))
    return {f"{HOST}/**": handle}


BLANK = TEST_HOME / "chooser_blank.html"
BLANK.write_text("<!doctype html><html><body></body></html>", encoding="utf-8")
RESUME = TEST_HOME / "chooser_resume.pdf"
RESUME.write_bytes(b"%PDF-1.4\n% a stand-in resume\n")


def test_picking_follows_the_allowlist_and_never_a_refused_control():
    check = Checks()
    av = [(0, "Without Resume"), (1, "From Device"), (2, "Copy & Paste"), (3, "Apply with LinkedIn")]
    check(chooser.pick(av, RESUME) == ("from_device", 1, "From Device"), "with a resume: From Device")
    check(chooser.pick(av, None) == ("without_resume", 0, "Without Resume"), "without one: Without Resume")
    check(chooser.pick(av, TEST_HOME / "missing.pdf") == ("without_resume", 0, "Without Resume"),
          "a resume path that doesn't exist counts as none")
    wd = [(0, "Autofill with Resume"), (1, "Apply Manually"), (2, "Use My Last Application")]
    check(chooser.pick(wd, RESUME) == ("apply_manually", 1, "Apply Manually"), "Workday: Apply Manually, even with a resume")
    refused = [(0, "Apply with LinkedIn"), (1, "Apply with Indeed"), (2, "Copy & Paste"), (3, "Use My Last Application"),
               (4, "Autofill with Resume"), (5, "Import from LinkedIn")]
    check(chooser.pick(refused, RESUME) == (None, None, None), "only refused options: nothing picked")
    check(chooser.pick([(0, "Apply Manually"), (1, "Apply Manually")], RESUME) == (None, None, None),
          "two controls with the allowed label: nothing picked")
    check(chooser.pick([(0, "Upload Resume from LinkedIn")], RESUME) == (None, None, None),
          "an allowed-looking label naming LinkedIn is refused")
    check(chooser.is_chooser(av) and chooser.is_chooser(wd) and chooser.is_chooser(refused)
          and not chooser.is_chooser([(0, "Submit application"), (1, "Privacy Policy")]),
          "a chooser is a page with any way-to-apply control; a form page isn't")
    check.done()


def test_avatures_from_device_puts_the_resume_in_the_chooser_the_control_opens():
    # Since 2026-10-29 the allowed control is clicked first and the chooser
    # it opens takes the file (here, through the page's own input, which
    # the control clicks); the input is set directly only when the click
    # opens no chooser.
    check = Checks()
    with mock_page(BLANK, _routes()) as page:
        page.goto(f"{HOST}/careers?folderId=1")
        log: list = []
        kind = chooser.choose(page, RESUME, log).kind
        check(kind == "from_device" and page.url.endswith("?folderId=1&parsed=1"),
              f"From Device: the resume went in and the form page followed ({kind}, {page.url})")
        check(log == [f"chose 'From Device' and put {RESUME.name} in the file chooser it opened"], f"the log ({log})")
        page.goto(f"{HOST}/careers?folderId=1")
        # The second entry is the page's own programmatic click on its input,
        # made by the control's handler; the agent clicked the control only.
        check(page.evaluate("window.__clicks") == ["From Device", "resumeFile"],
              f"the From Device control was clicked, nothing else ({page.evaluate('window.__clicks')})")
    check.done()


def test_a_portal_that_ignores_its_hidden_input_takes_the_file_through_the_controls_chooser():
    # Fails on 59e7533: the file went into the hidden pre-existing input,
    # which ignores it, and step 2 never came (the wait below times out).
    check = Checks()
    with mock_page(BLANK, _routes()) as page:
        page.goto(f"{HOST}/picker?folderId=1")
        log: list = []
        kind = chooser.choose(page, RESUME, log).kind
        reached = True
        try:
            page.wait_for_url("**/careers/Application?folderId=1&picked=1", timeout=5000)
        except Exception:
            reached = False
        check(kind == "from_device" and reached, f"the job reached step 2 ({kind}, {page.url})")
        check(log == [f"chose 'From Device' and put {RESUME.name} in the file chooser it opened"], f"the log ({log})")
        page.goto(f"{HOST}/picker?folderId=1")
        # The empty entry is the page's own click on the input it created.
        check(page.evaluate("window.__clicks") == ["From Device", ""],
              f"only the From Device control was clicked ({page.evaluate('window.__clicks')})")
    check.done()


def test_a_control_that_opens_no_chooser_falls_back_to_the_pages_own_input():
    # The fallback route, kept for a portal whose control is a plain label
    # for its input: the click opens nothing, the page stays, the input
    # gets the file. A click that opens nothing on a page that moved, or
    # with no input, puts the file nowhere.
    check = Checks()
    with mock_page(BLANK, _routes()) as page:
        page.goto(f"{HOST}/careers?folderId=1")
        page.evaluate("() => { document.getElementById('methodButton--file').onclick = () => false; }")
        log: list = []
        with mock.patch.object(chooser, "FILE_CHOOSER_WAIT_MS", 300):
            kind = chooser.choose(page, RESUME, log).kind
        check(kind == "from_device" and page.url.endswith("?folderId=1&parsed=1"),
              f"no chooser opened: the page's own input took the file and step 2 followed ({kind}, {page.url})")
        check(log == [f"chose 'From Device' and put {RESUME.name} in the page's file input (the control opened no chooser)"],
              f"the log says which route carried it ({log})")
        page.goto(f"{HOST}/careers?folderId=1")
        page.evaluate("() => { document.getElementById('methodButton--file').onclick = "
                      "() => { location.href = '/thanks'; return false; }; }")
        raised = None
        try:
            with mock.patch.object(chooser, "FILE_CHOOSER_WAIT_MS", 300):
                chooser.choose(page, RESUME, log)
        except ValueError as e:
            raised = str(e)
        check(raised is not None and "opened no file chooser" in raised,
              f"a click that navigated instead is not followed by a set on whatever page came ({raised})")
    check.done()


def test_without_a_resume_without_resume_is_pressed_and_refused_options_never():
    check = Checks()
    with mock_page(BLANK, _routes()) as page:
        page.goto(f"{HOST}/careers?folderId=1")
        log: list = []
        kind = chooser.choose(page, None, log).kind
        check(kind == "without_resume" and page.url.endswith("/careers/Application?folderId=1"),
              f"Without Resume pressed, the form page followed ({kind}, {page.url})")
        page.goto(f"{HOST}/careers?folderId=1")
        check(page.evaluate("window.__clicks") == ["Without Resume"], f"only that link ({page.evaluate('window.__clicks')})")
        page.goto(f"{HOST}/refused")
        raised = None
        try:
            chooser.choose(page, RESUME, log)
        except ValueError as e:
            raised = str(e)
        check(raised is not None and "none of which the agent takes" in raised and "'Apply with LinkedIn'" in raised
              and "'Copy & Paste'" in raised and "'Use My Last Application'" in raised,
              f"only refused options: parked with the options shown ({raised})")
        check(page.url.endswith("/refused") and page.evaluate("window.__clicks") == ["Without Resume"],
              "nothing on that page was pressed")
    check.done()


def test_workdays_apply_manually_is_pressed_and_an_ashby_form_page_is_not_a_chooser():
    check = Checks()
    with mock_page(BLANK, _routes()) as page:
        page.goto(f"{HOST}/workday")
        log: list = []
        kind = chooser.choose(page, RESUME, log).kind
        check(kind == "apply_manually" and page.url.endswith("/apply/applyManually"),
              f"Apply Manually, never Autofill or the last application ({kind}, {page.url})")
        page.goto(f"{HOST}/ashby")
        page.evaluate("sessionStorage.setItem('clicks', '[]')"); page.reload()
        controls = chooser.options_shown(page)
        check(chooser.pick(controls, RESUME) == (None, None, None)
              and page.evaluate("window.__clicks") == [], "Ashby's LinkedIn / Indeed buttons are never picked")
    check.done()


def test_the_wizard_takes_the_chooser_and_goes_on_to_the_form():
    check = Checks()
    with mock_page(BLANK, _routes()) as page:
        page.goto(f"{HOST}/careers?folderId=1")
        pauser = pause.GuiPauser(notify_fn=lambda t, b, echo=print: None, echo=lambda *a, **k: None)
        w = wizard.Wizard(page, _profile(), url=f"{HOST}/careers?folderId=1", portal="generic", portal_method="default",
                          company="Acme", role="Engineer", resume=None, posting_text="", pauser=pauser,
                          echo=lambda *a, **k: None, auto_submit=lambda: True)   # the ON path, as on f147f5c
        with mock.patch("agent.classify.classify_fields", classify_by_label), \
                mock.patch("agent.mapper.map_fields", map_from_profile), redirect_stdout(io.StringIO()):
            out = w.run()
        check("chose 'Without Resume'" in out.log and any(e["kind"] == "apply_method" for e in out.events),
              f"the wizard chose Without Resume (no resume on file) ({out.log[:3]})")
        check(out.status == "submitted" and page.url.endswith("/thanks"),
              f"and went on to fill and submit the form ({out.status}, {out.reason}, {page.url})")
        page.goto(f"{HOST}/refused")
        w2 = wizard.Wizard(page, _profile(), url=f"{HOST}/refused", portal="generic", portal_method="default",
                           company="Acme", role="Engineer", resume=None, posting_text="", pauser=pauser,
                           echo=lambda *a, **k: None)
        with mock.patch("agent.classify.classify_fields", classify_by_label), \
                mock.patch("agent.mapper.map_fields", map_from_profile), redirect_stdout(io.StringIO()):
            out = w2.run()
        # In GUI mode a park waits in its tab with Continue (Jack, 2026-10-26:
        # item 2 of that day), the options still listed in the reason.
        check(out.status == wizard.WAITING and "none of which the agent takes" in out.reason
              and "'Apply with Indeed'" in out.reason and w2.pending and w2.pending.get("kind") == "parked",
              f"a chooser with only refused options waits, with them listed ({out.status}, {out.reason})")
    check.done()


def _gui_wizard(page, url, resume):
    pauser = pause.GuiPauser(notify_fn=lambda t, b, echo=print: None, echo=lambda *a, **k: None)
    return wizard.Wizard(page, _profile(), url=url, portal="generic", portal_method="default",
                         company="Acme", role="Engineer", resume=resume, posting_text="", pauser=pauser,
                         echo=lambda *a, **k: None, auto_submit=lambda: True)


def _quick(stack):
    """The usual patches, with the form wait and the time cap shortened so a loop that never parks ends."""
    stack.enter_context(mock.patch("agent.classify.classify_fields", classify_by_label))
    stack.enter_context(mock.patch("agent.mapper.map_fields", map_from_profile))
    stack.enter_context(mock.patch.object(wizard, "FORM_WAIT_S", 1))
    stack.enter_context(mock.patch.object(wizard, "TIME_CAP_S", 20))
    stack.enter_context(redirect_stdout(io.StringIO()))


def test_a_chooser_that_never_advances_parks_after_one_choice():
    # Fails on 59e7533: the loop chose again every pass until the (here
    # shortened) time cap, and the park names the cap, not the choice.
    check = Checks()
    with mock_page(BLANK, _routes()) as page:
        page.goto(f"{HOST}/stuck?folderId=1")
        w = _gui_wizard(page, f"{HOST}/stuck?folderId=1", RESUME)
        with contextlib.ExitStack() as stack:
            _quick(stack)
            out = w.run()
        check(out.status == wizard.WAITING and out.reason == "chose 'From Device' but the page didn't move on"
              and (w.pending or {}).get("kind") == "parked",
              f"parked with Continue after one choice ({out.status}, {out.reason})")
        check(page.evaluate("window.__files") == [f"picker:{RESUME.name}"],
              f"set_files once ({page.evaluate('window.__files')})")
        check(page.evaluate("window.__clicks") == ["From Device", ""]
              and sum(1 for e in out.events if e["kind"] == "apply_method") == 1,
              f"one choice, one event ({page.evaluate('window.__clicks')})")
        check(f"chose 'From Device' and put {RESUME.name} in the file chooser it opened" in out.log, f"the log ({out.log})")
    check.done()


def test_continue_on_an_unchanged_page_parks_again_with_no_second_choice():
    # Fails on 59e7533 at the first park (as the test above): there is no
    # first park within the cap, so no Continue to make.
    check = Checks()
    with mock_page(BLANK, _routes()) as page:
        page.goto(f"{HOST}/stuck?folderId=1")
        w = _gui_wizard(page, f"{HOST}/stuck?folderId=1", RESUME)
        with contextlib.ExitStack() as stack:
            _quick(stack)
            out = w.run()
            check(out.reason == "chose 'From Device' but the page didn't move on", f"the first park ({out.reason})")
            out = w.continue_from({})
        check(out.status == wizard.WAITING and (w.pending or {}).get("kind") == "parked"
              and out.reason == "chose 'From Device' earlier and the page is unchanged since; "
                                "choose a way to apply yourself in the tab",
              f"Continue on the unchanged page parks again ({out.status}, {out.reason})")
        check(page.evaluate("window.__files") == [f"picker:{RESUME.name}"]
              and page.evaluate("window.__clicks") == ["From Device", ""]
              and sum(1 for e in out.events if e["kind"] == "apply_method") == 1,
              f"no second choice: one set_files, one click, one event ({page.evaluate('window.__files')})")
        check("resumed after a parked pause" in out.log, f"the resume is logged ({out.log[-3:]})")
    check.done()


def test_a_choice_that_shows_step_2_at_the_same_address_counts_as_moved_on():
    # Jack's addition (2026-10-29): fields appear, the URL is unchanged;
    # that is progress, not a park. Fails on 59e7533: the file went into
    # the ignoring input, step 2 never appeared, and the shortened cap
    # parked the job.
    check = Checks()
    with mock_page(BLANK, _routes()) as page:
        page.goto(f"{HOST}/inline?folderId=1")
        w = _gui_wizard(page, f"{HOST}/inline?folderId=1", RESUME)
        with contextlib.ExitStack() as stack:
            _quick(stack)
            out = w.run()
        check("didn't move on" not in out.reason and "unchanged since" not in out.reason
              and f"chose 'From Device' and put {RESUME.name} in the file chooser it opened" in out.log,
              f"the choice counted as moved on ({out.status}, {out.reason})")
        check(out.pages_filled == 1 and any(r.get("label") == "First Name" for r in out.rows),
              f"and the step-2 form at the same address was filled ({out.pages_filled}, {out.status}, {out.reason})")
    check.done()


if __name__ == "__main__":
    run_as_script(globals())
