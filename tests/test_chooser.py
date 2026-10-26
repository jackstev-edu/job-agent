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
            "/refused": ONLY_REFUSED, "/thanks": THANKS}

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


def test_avatures_from_device_puts_the_resume_in_the_pages_file_input():
    check = Checks()
    with mock_page(BLANK, _routes()) as page:
        page.goto(f"{HOST}/careers?folderId=1")
        log: list = []
        kind = chooser.choose(page, RESUME, log)
        check(kind == "from_device" and page.url.endswith("?folderId=1&parsed=1"),
              f"From Device: the resume went in and the form page followed ({kind}, {page.url})")
        check(log == [f"chose 'From Device' and put {RESUME.name} in the page's file input"], f"the log ({log})")
        page.goto(f"{HOST}/careers?folderId=1")
        check(page.evaluate("window.__clicks") == [], "the file input was used directly: no link clicked")
    check.done()


def test_without_a_resume_without_resume_is_pressed_and_refused_options_never():
    check = Checks()
    with mock_page(BLANK, _routes()) as page:
        page.goto(f"{HOST}/careers?folderId=1")
        log: list = []
        kind = chooser.choose(page, None, log)
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
        kind = chooser.choose(page, RESUME, log)
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
                          echo=lambda *a, **k: None)
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


if __name__ == "__main__":
    run_as_script(globals())
