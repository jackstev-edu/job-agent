"""
test_google_login.py — the Google sign-in tab is opened and left to Jack.

WHAT THIS DOES: Runs `apply.py google-login` and the GUI's Google button
against a mock served at accounts.google.com: a tab opens on that exact
address, the command waits for Enter, and the agent evaluates nothing,
types nothing and presses nothing on the page (the mock records clicks and
keys; page.evaluate is spied). Also pins is_google to an exact https host.
WHY IT MATTERS: The agent never touches Google's own login page (Jack,
2026-10-19); this is the one path that opens it.

Run from anywhere:  .venv/Scripts/python.exe tests/test_google_login.py
"""
import io
from argparse import Namespace
from contextlib import redirect_stdout
from unittest import mock

from _fill_run import _headless
from _harness import Checks, run_as_script
from agent import google, gui

MOCK = """<!doctype html><html><head><title>Sign in - Google Accounts</title></head><body>
<h1>Sign in</h1><form><label for="id">Email or phone</label><input id="id" type="email">
<button type="submit">Next</button></form>
<script>window.__clicks = 0; window.__keys = 0;
  document.addEventListener('click', () => { window.__clicks++; }, true);
  document.addEventListener('keydown', () => { window.__keys++; }, true);</script></body></html>"""


def _routes():
    return {"https://accounts.google.com/**": lambda route: route.fulfill(content_type="text/html", body=MOCK)}


def test_google_login_opens_the_tab_and_touches_nothing():
    import apply
    check = Checks()
    seen = {"pages": [], "evaluate": 0}
    real_open = google.open_login_tab

    def spy_open(ctx):
        page = real_open(ctx)
        real_eval = page.evaluate

        def eval_spy(*a, **k):
            seen["evaluate"] += 1
            return real_eval(*a, **k)
        page.evaluate = eval_spy
        seen["pages"].append(page)
        return page

    def enter(*a):
        page = seen["pages"][-1]
        seen["url"] = page.url
        # Read the mock's counters through the frame, not the spied page.evaluate,
        # so the spy's count stays the agent's own.
        seen["counters"] = page.context.pages[-1].main_frame.evaluate("[window.__clicks, window.__keys]")
        seen["typed"] = page.context.pages[-1].main_frame.evaluate("document.getElementById('id').value")
        return ""
    out = io.StringIO()
    with mock.patch.object(apply.browser_mod, "browser", lambda: _headless(_routes())), \
            mock.patch.object(google, "open_login_tab", spy_open), \
            mock.patch.object(apply, "input", enter, create=True), redirect_stdout(out):
        apply.cmd_google_login(Namespace())
    check(seen.get("url") == "https://accounts.google.com/", f"the tab is on Google's sign-in address ({seen.get('url')})")
    check(seen["evaluate"] == 0, "the agent evaluated nothing on the page")
    check(seen.get("counters") == [0, 0] and seen.get("typed") == "",
          f"nothing clicked or typed on the page ({seen.get('counters')}, {seen.get('typed')!r})")
    check("Sign in there yourself" in out.getvalue(), "it tells Jack to sign in by hand")
    check.done()


def test_the_gui_button_opens_the_same_tab():
    import apply
    check = Checks()
    state = gui.State()
    state.request_google_login()
    with _headless(_routes()) as ctx, redirect_stdout(io.StringIO()):
        apply.gui_loop(ctx, state, None, Namespace(profile=None, resume=None, dry=False, force=False),
                       None, idle_limit_s=0.5)
        urls = [p.url for p in ctx.pages]
        counters = ctx.pages[-1].evaluate("[window.__clicks, window.__keys]")
    check(urls[-1] == "https://accounts.google.com/" and counters == [0, 0],
          f"a tab on Google's sign-in address, untouched ({urls}, {counters})")
    check("sign in there yourself" in state.snapshot()["notice"], "the page is told what to do")
    check.done()


def test_is_google_is_exact():
    check = Checks()
    for url, want in (("https://accounts.google.com/signin/oauth", True),
                      ("https://accounts.google.com", True),
                      ("http://accounts.google.com/", False),
                      ("https://accounts.google.com.example/", False),
                      ("https://evil.example/accounts.google.com/", False),
                      ("https://www.google.com/", False), ("", False), (None, False)):
        check(google.is_google(url) is want, f"{url!r} -> {want}")
    check.done()


if __name__ == "__main__":
    run_as_script(globals())
