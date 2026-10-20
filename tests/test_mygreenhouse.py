"""
test_mygreenhouse.py — a MyGreenhouse link: the public board first, then Google, then a pause.

WHAT THIS DOES: Serves mock my.greenhouse.io (a job link that redirects
to the sign-in page, which offers Continue with Google, Send security
code, Apple, LinkedIn and the employer's "Sign in" link), the two public
Greenhouse board hosts and a mock Google, and runs intake.open_link and
accounts.handle: the job-boards host is used when it shows the job; the
boards host when the first is missing or says the job is closed; with
neither, the sign-in page is the landing (not parked), reads as a sign-in
page, and Google sign-in gets to the job; without Google the agent pauses
for the emailed code, typing nothing into the email box and pressing
neither "Send security code" nor "Sign in"; a link that already shows
the job (signed in) needs no board; the link shape is matched exactly.
WHY IT MATTERS: Jack's first real link was a MyGreenhouse one that landed
on a sign-in page with no password box, which structure alone would have
treated as an ordinary form and filled.

Run from anywhere:  .venv/Scripts/python.exe tests/test_mygreenhouse.py
"""
import io
from contextlib import contextmanager, redirect_stdout
from unittest import mock
from urllib.parse import parse_qs, urlparse

from _harness import Checks, run_as_script
from agent import accounts, intake, pages, pause
from test_accounts import _Pauser, _Profile

MG = "https://my.greenhouse.io"
JB = "https://job-boards.greenhouse.io"
BOARDS = "https://boards.greenhouse.io"
APP = "https://app.greenhouse.io"
GOOGLE = "https://accounts.google.com"

CLICKS_JS = """<script>
  window.__clicks = [];
  document.addEventListener('click', e => {
    const b = e.target.closest('a, button, [role=button], [role=link], input, [data-identifier]');
    if (b) window.__clicks.push((b.innerText || b.value || '').trim().split('\\n')[0]);
  }, true);
</script>"""


def _doc(title, body):
    return f"<!doctype html><html><head><meta charset='utf-8'><title>{title}</title></head><body>{CLICKS_JS}{body}</body></html>"


JOB = _doc("Engineer - Acme", """<h1>Engineer</h1><p>Acme</p><form><label for="fn">First Name</label>
<input id="fn" name="first_name"><label for="em">Email</label><input id="em" name="email" type="email">
<button type="submit">Submit application</button></form>""")
CLOSED = _doc("Acme", "<h1>Acme</h1><p>The job you are looking for is no longer open.</p>")


def _sign_in(google=True):
    g = (f'<button type="button" onclick="location.href=&quot;{GOOGLE}/chooser&quot;">Continue with Google</button>'
         if google else "")
    return _doc("Sign in - MyGreenhouse", f"""<h1>Sign in</h1><form id="f">
<input id="email-address" type="email" placeholder="Email"><button type="submit">Send security code</button></form>
{g}<button type="button">Continue with Apple</button><button type="button">Continue with LinkedIn</button>
<a href="{APP}/users/sign_in">Sign in</a>
<script>document.getElementById('f').addEventListener('submit', e => {{ e.preventDefault(); location.href = '/users/sign_in?sent=1'; }});</script>""")


CHOOSER = _doc("Choose an account", f"""<div role="main"><ul>
<li><div role="link" data-identifier="test@example.invalid" onclick="location.href='/consent'">Test Person</div></li></ul></div>""")
CONSENT = _doc("Sign in to MyGreenhouse", f"""<div role="main"><ul><li>See your primary Google Account email address</li></ul>
<button type="button">Cancel</button><button type="button" onclick="location.href='{MG}/jobs/acme/123?signed=1'">Continue</button></div>""")


def _serve(ctx, jb, boards, google=True, abort_second_job_request=False):
    """
    jb / boards: the html each public host serves for /acme/jobs/123, or None
    for a 404. `abort_second_job_request`: the way back to the job link fails.
    """
    seen = {"job": 0}

    def handler(route):
        u = urlparse(route.request.url)
        base = f"{u.scheme}://{u.hostname}"
        q = parse_qs(u.query)
        if base == MG and u.path == "/jobs/acme/123":
            seen["job"] += 1
            if abort_second_job_request and seen["job"] >= 2:
                route.abort(); return
            if q.get("signed"):
                route.fulfill(status=200, content_type="text/html", body=JOB); return
            # The redirect is done in the page: a fulfilled 302 is followed by
            # Chromium without routing the next request, which would hit the network.
            route.fulfill(status=200, content_type="text/html",
                          body="<!doctype html><html><body><script>location.replace('/users/sign_in')</script></body></html>"); return
        if base == MG and u.path == "/users/sign_in":
            route.fulfill(status=200, content_type="text/html", body=_sign_in(google)); return
        if base == JB and u.path == "/acme/jobs/123" and jb:
            route.fulfill(status=200, content_type="text/html", body=jb); return
        if base == BOARDS and u.path == "/acme/jobs/123" and boards:
            route.fulfill(status=200, content_type="text/html", body=boards); return
        if base == GOOGLE:
            body = {"/chooser": CHOOSER, "/consent": CONSENT}.get(u.path)
            route.fulfill(status=200 if body else 404, content_type="text/html", body=body or "<p>404</p>"); return
        route.fulfill(status=404, content_type="text/html", body="<p>404</p>")
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


def _handle(page, pauser):
    out = io.StringIO()
    with mock.patch("anthropic.Anthropic", side_effect=AssertionError("a model client was built")), \
            redirect_stdout(out):
        return accounts.handle(page, pages.SIGN_IN, _Profile(), pauser)


def test_the_public_board_is_used_when_it_shows_the_job():
    check = Checks()
    with _context() as ctx:
        _serve(ctx, jb=JOB, boards=None)
        landing = intake.open_link(ctx, f"{MG}/jobs/acme/123")
        check(landing.parked is None and landing.url == f"{JB}/acme/jobs/123",
              f"the job-boards page is the landing ({landing.parked}, {landing.url})")
        check(landing.steps == ["used the public board job-boards.greenhouse.io for this MyGreenhouse job"],
              f"the step says so ({landing.steps})")
        check(landing.portal == "greenhouse" and landing.portal_method == "host", "portal by host")
        check(landing.page.evaluate("window.__clicks") == [], "nothing clicked")
    check.done()


def test_the_second_board_is_tried_when_the_first_is_missing_or_closed():
    check = Checks()
    for first, label in ((None, "a 404"), (CLOSED, "a closed-job page")):
        with _context() as ctx:
            _serve(ctx, jb=first, boards=JOB)
            landing = intake.open_link(ctx, f"{MG}/jobs/acme/123")
            check(landing.parked is None and landing.url == f"{BOARDS}/acme/jobs/123",
                  f"{label} on job-boards: the boards page is the landing ({landing.parked}, {landing.url})")
            check(landing.steps == ["used the public board boards.greenhouse.io for this MyGreenhouse job"],
                  f"{label}: the step ({landing.steps})")
    check.done()


def test_with_no_public_board_the_sign_in_page_is_the_landing_and_google_signs_in():
    check = Checks()
    with _context() as ctx:
        _serve(ctx, jb=None, boards=CLOSED)
        landing = intake.open_link(ctx, f"{MG}/jobs/acme/123")
        page = landing.page
        check(landing.parked is None and landing.url == f"{MG}/users/sign_in",
              f"the sign-in page is the landing, not parked ({landing.parked}, {landing.url})")
        check(landing.steps == ["no public board shows this MyGreenhouse job; its sign-in page is next"],
              f"the step ({landing.steps})")
        check(pages.page_kind(page) == pages.SIGN_IN and pages.fill_allowed(page, landing.url) == pages.SIGN_IN,
              "it reads as a sign-in page by its address, so nothing is filled there")
        # The email box is read just before the Google button takes the page away
        # (kept in sessionStorage: the job page is on the same origin).
        page.evaluate("window.addEventListener('pagehide', () => sessionStorage.setItem('ja_email_at_leave', "
                      "'[' + document.getElementById('email-address').value + ']'))")
        pauser = _Pauser([])
        res = _handle(page, pauser)
        left = page.evaluate("sessionStorage.getItem('ja_email_at_leave')")
        check(left == "[]", f"the email box was empty when the page left ({left!r})")
        check(res.status == accounts.CONTINUE and page.url == f"{MG}/jobs/acme/123?signed=1",
              f"Continue with Google gets to the job ({res.status}, {res.reason}, {page.url})")
        check("the sign-in page went away after the Google button" in res.steps and not pauser.asked
              and "typed" not in " ".join(res.steps),
              f"nothing typed, no pause ({res.steps})")
    check.done()


def test_without_google_the_code_sign_in_is_left_to_jack():
    check = Checks()
    with _context() as ctx:
        _serve(ctx, jb=None, boards=None, google=False)
        landing = intake.open_link(ctx, f"{MG}/jobs/acme/123")
        page = landing.page
        pauser = _Pauser([None])
        res = _handle(page, pauser)
        check(res.status == accounts.PARKED and "still showing" in res.reason,
              f"parked after the pause ({res.status}, {res.reason})")
        check(len(pauser.asked) == 1 and "security code" in pauser.asked[0][0] and not pauser.asked[0][1],
              f"one plain pause for the emailed code ({pauser.asked})")
        check(page.url == f"{MG}/users/sign_in" and page.evaluate("window.__clicks") == []
              and page.evaluate("document.getElementById('email-address').value") == "",
              f"nothing pressed (not Send security code, Apple, LinkedIn or Sign in) and nothing typed "
              f"({page.url}, {page.evaluate('window.__clicks')})")
        check("forgot" not in " ".join(res.steps).lower() and not any("typed" in s for s in res.steps),
              f"no password flow ({res.steps})")
        gui = pause.GuiPauser(notify_fn=lambda t, b, echo=print: None, echo=lambda *a, **k: None)
        raised = None
        try:
            _handle(page, gui)
        except pause.Deferred as e:
            raised = e
        check(raised is not None and "security code" in raised.body and not raised.hidden,
              "GUI mode: a Deferred, no hidden prompt")
    check.done()


def test_a_board_page_that_only_shares_the_prefix_is_not_the_job():
    check = Checks()
    with _context() as ctx:
        _serve(ctx, jb=JOB, boards=None)
        page = ctx.new_page(); page.goto(f"{JB}/acme/jobs/123")
        check(intake._is_public_job(page, f"{JB}/acme/jobs/123", "acme", "123") is True, "the job page itself")
        # The same job page under other paths (a server-side redirect): only the exact path counts.
        for path, want in (("/acme/jobs/123", True), ("/acme/jobs/123/", True), ("/acme/jobs/1234", False),
                           ("/acme/jobs/123/x", False), ("/x/acme/jobs/123", False), ("/acme/jobs/12", False)):
            page.evaluate(f"history.replaceState(null, '', '{path}')")
            check(intake._is_public_job(page, f"{JB}/acme/jobs/123", "acme", "123") is want,
                  f"path {path!r} -> {want}")
        check(intake._is_public_job(page, f"{BOARDS}/acme/jobs/123", "acme", "123") is False,
              "another board host than the one tried is not it")
    check.done()


def test_a_failed_way_back_to_the_link_parks_with_its_reason():
    check = Checks()
    with _context() as ctx:
        _serve(ctx, jb=None, boards=None, abort_second_job_request=True)
        landing = intake.open_link(ctx, f"{MG}/jobs/acme/123")
        check(landing.parked == "couldn't get back to the MyGreenhouse link after trying the public boards",
              f"parked with the reason ({landing.parked})")
        check(not any("sign-in page is next" in s for s in landing.steps), f"no false step ({landing.steps})")
    check.done()


def test_a_link_that_already_shows_the_job_needs_no_board():
    check = Checks()
    with _context() as ctx:
        _serve(ctx, jb=JOB, boards=JOB)
        landing = intake.open_link(ctx, f"{MG}/jobs/acme/123?signed=1")
        check(landing.parked is None and landing.url == f"{MG}/jobs/acme/123?signed=1" and landing.steps == [],
              f"the job page itself, no board tried ({landing.url}, {landing.steps})")
    check.done()


def test_mygreenhouse_links_are_matched_exactly():
    check = Checks()
    for url, want in ((f"{MG}/jobs/acme/123", ("acme", "123")), (f"{MG}/jobs/acme-co_2/123/", ("acme-co_2", "123")),
                      (f"{MG}/jobs/acme/123?x=1", ("acme", "123")),
                      ("http://my.greenhouse.io/jobs/acme/123", None), ("https://my.greenhouse.io.example/jobs/acme/123", None),
                      (f"{MG}/users/sign_in", None), (f"{MG}/jobs/acme/abc", None), (f"{MG}/jobs/acme", None),
                      (f"{JB}/acme/jobs/123", None), ("", None), (None, None)):
        check(intake.mygreenhouse_job(url) == want, f"{url!r} -> {want}")
    for url, want in ((f"{MG}/users/sign_in", True), (f"{MG}/users/sign_in/", True), (f"{MG}/users/sign_in?x=1", True),
                      ("http://my.greenhouse.io/users/sign_in", False), (f"{MG}/users/sign_in/other", False),
                      ("https://my.greenhouse.io.example/users/sign_in", False), (f"{APP}/users/sign_in", False),
                      (f"{MG}/jobs/acme/123", False)):
        check(pages.is_mygreenhouse_sign_in(url) is want, f"sign-in {url!r} -> {want}")
    check.done()


if __name__ == "__main__":
    run_as_script(globals())
