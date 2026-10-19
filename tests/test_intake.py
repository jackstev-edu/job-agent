"""
test_intake.py — from Jack's link to the form, clicking only Apply.

WHAT THIS DOES: Serves mock postings at real-looking hosts and runs
intake.open_link on each: a posting with a top-and-bottom Apply pair, one
whose Apply opens a new tab on the company's site, a Greenhouse-style page
with the form already on it, Workday's "Apply Manually" chooser, LinkedIn
with only Easy Apply (parked) and with an external Apply (followed),
Handshake's built-in application (parked), a posting offering two
different ways to apply (parked), and a page whose only button is Submit
(never pressed). Every click on every page is recorded and must read as
Apply.
WHY IT MATTERS: Apply is the one control intake presses, and the agent acts
only on links Jack gives it. Easy Apply and Handshake's own applications are
refused by decision.

Run from anywhere:  .venv/Scripts/python.exe tests/test_intake.py
"""
from contextlib import contextmanager

from _harness import Checks, run_as_script
from agent import intake

COMPANY = "https://jobs.example-co.com"
GREENHOUSE = "https://job-boards.greenhouse.io"
LINKEDIN = "https://www.linkedin.com"
HANDSHAKE = "https://app.joinhandshake.com"

CLICKS_JS = """<script>
  window.__clicks = [];
  document.addEventListener('click', e => {
    const b = e.target.closest('a, button, [role=button], input');
    if (b) window.__clicks.push((b.innerText || b.value || '').trim());
  }, true);
</script>"""

FORM = f"""<!doctype html><html><head><title>Engineer - Example Co</title></head><body>
  <h1>Engineer</h1><form><label for="fn">First Name</label><input id="fn" name="first_name">
  <button type="submit">Submit application</button></form>{CLICKS_JS}</body></html>"""

PAIR = f"""<!doctype html><html><body><h1>Engineer</h1>
  <a href="/jobs/1/apply">Apply now</a><p>Long description.</p>
  <a href="/jobs/1/apply">Apply now</a>{CLICKS_JS}</body></html>"""

NEW_TAB = f"""<!doctype html><html><body><h1>Engineer</h1>
  <button onclick="window.open('{GREENHOUSE}/acme/jobs/7', '_blank')">Apply</button>
  <button type="button">Share</button>{CLICKS_JS}</body></html>"""

TWO_WAYS = f"""<!doctype html><html><body><h1>Engineer</h1>
  <a href="/jobs/1/apply">Apply now</a><a href="/jobs/2/apply">Apply now</a>{CLICKS_JS}</body></html>"""

SUBMIT_ONLY = f"""<!doctype html><html><body><h1>Engineer</h1>
  <button type="submit">Submit</button><button>Easy Apply</button>{CLICKS_JS}</body></html>"""

WORKDAY_POSTING = f"""<!doctype html><html><body><h1>Engineer</h1>
  <a href="/en-US/acme/job/1/apply">Apply</a>{CLICKS_JS}</body></html>"""
WORKDAY_CHOOSER = f"""<!doctype html><html><body><h2>Start Your Application</h2>
  <a href="/en-US/acme/job/1/apply/autofill">Autofill with Resume</a>
  <a href="/en-US/acme/job/1/apply/manual">Apply Manually</a>
  <a href="/en-US/acme/job/1/apply/last">Use My Last Application</a>{CLICKS_JS}</body></html>"""

LINKEDIN_EASY = f"""<!doctype html><html><body><h1>Engineer at Acme</h1>
  <button>Easy Apply</button><button>Save</button>{CLICKS_JS}</body></html>"""
LINKEDIN_EXTERNAL = f"""<!doctype html><html><body><h1>Engineer at Acme</h1>
  <button onclick="window.open('{COMPANY}/jobs/1/apply', '_blank')">Apply</button>
  <button>Save</button>{CLICKS_JS}</body></html>"""
HANDSHAKE_INTERNAL = f"""<!doctype html><html><body><h1>Engineer</h1>
  <button onclick="document.getElementById('m').style.display='block'">Apply</button>
  <div id="m" style="display:none"><form><label for="fn">First Name</label>
  <input id="fn" name="first_name"><button type="submit">Submit Application</button></form></div>
  {CLICKS_JS}</body></html>"""


def _serve(ctx, site: dict):
    """site: {host: {path: html}}. Anything else is a 404 page."""
    def handler(route):
        url = route.request.url
        host = url.split("//", 1)[1].split("/", 1)[0]
        path = "/" + url.split("//", 1)[1].split("/", 1)[1].split("?")[0] if "/" in url.split("//", 1)[1] else "/"
        html = site.get(f"https://{host}", {}).get(path)
        route.fulfill(status=200 if html else 404, content_type="text/html",
                      body=html or "<p>404</p>")
    ctx.route("**/*", handler)


@contextmanager
def _context():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            yield browser.new_context()
        finally:
            browser.close()


def _clicks(landing, *pages):
    out = []
    for page in pages:
        try:
            out += page.evaluate("window.__clicks || []")
        except Exception:
            pass
    return out


def test_a_top_and_bottom_apply_pair_is_one_apply():
    check = Checks()
    with _context() as ctx:
        _serve(ctx, {COMPANY: {"/jobs/1": PAIR, "/jobs/1/apply": FORM}})
        first = ctx.new_page()
        landing = intake.open_link(ctx, f"{COMPANY}/jobs/1")
        check(landing.parked is None and landing.page.url == f"{COMPANY}/jobs/1/apply",
              f"landed on the form page ({landing.parked}, {landing.page and landing.page.url})")
        check(landing.steps == [f"clicked 'Apply now' on jobs.example-co.com"],
              f"one Apply click, recorded ({landing.steps})")
        check(landing.portal == "generic", "a company site detects as generic")
    check.done()


def test_an_apply_that_opens_a_new_tab_is_followed():
    check = Checks()
    with _context() as ctx:
        _serve(ctx, {COMPANY: {"/jobs/1": NEW_TAB}, GREENHOUSE: {"/acme/jobs/7": FORM}})
        landing = intake.open_link(ctx, f"{COMPANY}/jobs/1")
        posting = ctx.pages[0]
        check(landing.parked is None and landing.page is not posting
              and landing.page.url == f"{GREENHOUSE}/acme/jobs/7",
              f"the new tab is the page from here on ({landing.page and landing.page.url})")
        check(landing.portal == "greenhouse" and landing.portal_method == "host",
              f"the portal is read off the landing host ({landing.portal}, {landing.portal_method})")
        check(_clicks(landing, posting) == ["Apply"], f"only Apply was clicked ({_clicks(landing, posting)})")
    check.done()


def test_a_form_already_on_the_posting_needs_no_click():
    check = Checks()
    with _context() as ctx:
        _serve(ctx, {GREENHOUSE: {"/acme/jobs/7": FORM}})
        landing = intake.open_link(ctx, f"{GREENHOUSE}/acme/jobs/7")
        check(landing.parked is None and landing.steps == [] and landing.page.url.endswith("/jobs/7"),
              f"the page itself is the form; nothing clicked ({landing.steps})")
        check(_clicks(landing, landing.page) == [], "Submit application was not pressed")
    check.done()


def test_workdays_chooser_takes_apply_manually():
    check = Checks()
    host = "https://acme.wd5.myworkdayjobs.com"
    with _context() as ctx:
        _serve(ctx, {host: {"/en-US/acme/job/1": WORKDAY_POSTING,
                            "/en-US/acme/job/1/apply": WORKDAY_CHOOSER,
                            "/en-US/acme/job/1/apply/manual": FORM}})
        landing = intake.open_link(ctx, f"{host}/en-US/acme/job/1")
        check(landing.parked is None and landing.page.url.endswith("/apply/manual"),
              f"Apply, then Apply Manually ({landing.page and landing.page.url}, {landing.parked})")
        check(landing.portal == "workday" and [s.split("'")[1] for s in landing.steps]
              == ["Apply", "Apply Manually"], f"the two clicks, in order ({landing.steps})")
    check.done()


def test_linkedin_easy_apply_is_parked_and_an_external_apply_is_followed():
    check = Checks()
    with _context() as ctx:
        _serve(ctx, {LINKEDIN: {"/jobs/view/1": LINKEDIN_EASY, "/jobs/view/2": LINKEDIN_EXTERNAL},
                     COMPANY: {"/jobs/1/apply": FORM}})
        landing = intake.open_link(ctx, f"{LINKEDIN}/jobs/view/1")
        check(landing.parked and "linkedin" in landing.parked and "Easy Apply" in landing.parked,
              f"Easy Apply only: parked with the reason ({landing.parked})")
        check(_clicks(landing, landing.page) == [], "nothing on the LinkedIn page was clicked")
        landing = intake.open_link(ctx, f"{LINKEDIN}/jobs/view/2")
        check(landing.parked is None and landing.page.url == f"{COMPANY}/jobs/1/apply",
              f"an Apply that leaves LinkedIn is followed ({landing.parked}, "
              f"{landing.page and landing.page.url})")
    check.done()


def test_handshakes_built_in_application_is_parked():
    check = Checks()
    with _context() as ctx:
        _serve(ctx, {HANDSHAKE: {"/jobs/9": HANDSHAKE_INTERNAL}})
        landing = intake.open_link(ctx, f"{HANDSHAKE}/jobs/9")
        check(landing.parked and "handshake" in landing.parked,
              f"the application stays on Handshake: parked ({landing.parked})")
        check(_clicks(landing, landing.page) == ["Apply"],
              f"Apply pressed once, then parked: the same control on the same address isn't "
              f"pressed again ({_clicks(landing, landing.page)})")
    check.done()


def test_two_different_ways_to_apply_are_left_to_jack():
    check = Checks()
    with _context() as ctx:
        _serve(ctx, {COMPANY: {"/jobs/1": TWO_WAYS}})
        landing = intake.open_link(ctx, f"{COMPANY}/jobs/1")
        check(landing.parked and "more than one way" in landing.parked, f"parked ({landing.parked})")
        check(_clicks(landing, landing.page) == [], "neither was clicked")
    check.done()


def test_submit_and_easy_apply_are_never_pressed():
    check = Checks()
    with _context() as ctx:
        _serve(ctx, {COMPANY: {"/jobs/1": SUBMIT_ONLY}})
        landing = intake.open_link(ctx, f"{COMPANY}/jobs/1")
        check(landing.parked is None and landing.steps == [],
              f"nothing to apply with: the page is left as is ({landing.parked}, {landing.steps})")
        check(_clicks(landing, landing.page) == [], "Submit and Easy Apply were not pressed")
    found = intake.find_apply.__doc__
    for word, ok in (("Apply now", True), ("Apply for this job", True), ("I'm interested", True),
                     ("Easy Apply", False), ("Submit application", False), ("Apply and submit", False),
                     ("Submit", False), ("Sign in to apply", False)):
        hit = bool(intake.APPLY_WORDS.match(word)) and not intake.REFUSED_WORDS.search(word)
        check(hit == ok, f"'{word}' {'is' if ok else 'is not'} an Apply word")
    check.done()


if __name__ == "__main__":
    run_as_script(globals())
