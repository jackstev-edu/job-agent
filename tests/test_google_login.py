"""
test_google_login.py — the Google sign-in tab is opened and left to Jack.

WHAT THIS DOES: Runs `apply.py google-login` and the GUI's Google button
against a mock served at accounts.google.com, with every Playwright method
that could read from or act on a page (evaluate, evaluate_handle, fill,
click, type, press, keyboard, mouse, on Page, Frame, Locator) patched at
the class level for the whole call: a tab opens on that exact address, the
command waits for Enter, and none of those methods is called; the mock's
own click and key counters stay at zero. Also pins is_google to an exact
https host (a look-alike host, userinfo, a trailing dot and the words in a
path are all refused), and is_google_host to the host on any scheme (the
refusal side).
WHY IT MATTERS: The agent never touches Google's own login page (Jack,
2026-10-19); this is the one path that opens it.

Run from anywhere:  .venv/Scripts/python.exe tests/test_google_login.py
"""
import io
from argparse import Namespace
from contextlib import ExitStack, redirect_stdout
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


class _Watch:
    """Every page-touching Playwright method, patched at the class for the whole call."""

    NAMES = (("Page", "evaluate"), ("Page", "evaluate_handle"), ("Page", "fill"), ("Page", "click"),
             ("Page", "type"), ("Page", "press"), ("Page", "check"), ("Page", "select_option"),
             ("Page", "input_value"), ("Page", "content"), ("Page", "inner_text"),
             ("Frame", "evaluate"), ("Frame", "evaluate_handle"), ("Frame", "fill"), ("Frame", "click"),
             ("Frame", "type"), ("Frame", "press"), ("Frame", "content"),
             ("Locator", "fill"), ("Locator", "click"), ("Locator", "type"), ("Locator", "press"),
             ("Locator", "check"), ("Locator", "evaluate"), ("Locator", "input_value"),
             ("Keyboard", "type"), ("Keyboard", "press"), ("Keyboard", "insert_text"), ("Keyboard", "down"),
             ("Mouse", "click"), ("Mouse", "down"), ("ElementHandle", "evaluate"), ("ElementHandle", "fill"),
             ("ElementHandle", "click"))

    def __init__(self):
        import playwright.sync_api as pw
        self.calls: list[str] = []
        self.originals = {}
        self._patches = []
        for cls_name, meth in self.NAMES:
            cls = getattr(pw, cls_name)
            orig = getattr(cls, meth)
            self.originals[(cls_name, meth)] = orig
            self._patches.append(mock.patch.object(cls, meth, self._spy(f"{cls_name}.{meth}", orig)))

    def _spy(self, name, orig):
        def f(this, *a, **k):
            self.calls.append(name)
            return orig(this, *a, **k)
        return f

    def __enter__(self):
        for p in self._patches:
            p.start()
        return self

    def __exit__(self, *exc):
        for p in self._patches:
            p.stop()

    def read(self, page, expr):
        """The test's own read of the mock, through the saved original."""
        return self.originals[("Frame", "evaluate")](page.main_frame, expr)


def test_google_login_opens_the_tab_and_touches_nothing():
    import apply
    check = Checks()
    seen: dict = {}
    watch = _Watch()

    def enter(*a):
        page = page_box["page"]
        seen["url"] = page.url
        seen["counters"] = watch.read(page, "[window.__clicks, window.__keys]")
        seen["typed"] = watch.read(page, "document.getElementById('id').value")
        seen["calls_at_enter"] = list(watch.calls)
        return ""
    page_box: dict = {}
    real_open = google.open_login_tab

    def remember(ctx):
        page_box["page"] = real_open(ctx)
        return page_box["page"]
    out = io.StringIO()
    with ExitStack() as stack:
        stack.enter_context(mock.patch.object(apply.browser_mod, "browser", lambda: _headless(_routes())))
        stack.enter_context(mock.patch.object(google, "open_login_tab", remember))
        stack.enter_context(mock.patch.object(apply, "input", enter, create=True))
        stack.enter_context(redirect_stdout(out))
        stack.enter_context(watch)
        apply.cmd_google_login(Namespace())
    check(seen.get("url") == "https://accounts.google.com/", f"the tab is on Google's sign-in address ({seen.get('url')})")
    check(seen.get("calls_at_enter") == [] and watch.calls == [],
          f"no page-touching Playwright method was called, before or after Enter ({watch.calls})")
    check(seen.get("counters") == [0, 0] and seen.get("typed") == "",
          f"nothing clicked or typed on the page ({seen.get('counters')}, {seen.get('typed')!r})")
    check("Sign in there yourself" in out.getvalue(), "it tells Jack to sign in by hand")
    check.done()


def test_the_gui_button_opens_the_same_tab():
    import apply
    check = Checks()
    state = gui.State()
    state.request_google_login()
    watch = _Watch()
    with _headless(_routes()) as ctx, redirect_stdout(io.StringIO()):
        with watch:
            apply.gui_loop(ctx, state, None, Namespace(profile=None, resume=None, dry=False, force=False),
                           None, idle_limit_s=0.5)
            urls = [p.url for p in ctx.pages]
        counters = ctx.pages[-1].evaluate("[window.__clicks, window.__keys]")
    check(urls[-1] == "https://accounts.google.com/" and counters == [0, 0],
          f"a tab on Google's sign-in address, untouched ({urls}, {counters})")
    check(watch.calls == [], f"the queue loop called no page-touching method for it ({watch.calls})")
    check("sign in there yourself" in state.snapshot()["notice"], "the page is told what to do")
    check.done()


def test_is_google_is_exact():
    check = Checks()
    for url, want in (("https://accounts.google.com/signin/oauth", True),
                      ("https://accounts.google.com", True),
                      ("https://ACCOUNTS.google.com/", True),
                      ("http://accounts.google.com/", False),
                      ("https://accounts.google.com.example/", False),
                      ("https://accounts.google.com@evil.example/", False),
                      ("https://accounts.google.com./", False),
                      ("https://evil.example/accounts.google.com/", False),
                      ("https://www.google.com/", False), ("", False), (None, False)):
        check(google.is_google(url) is want, f"{url!r} -> {want}")
    for url, want in (("https://accounts.google.com/", True), ("http://accounts.google.com/x", True),
                      ("https://accounts.google.com./", True), ("HTTPS://ACCOUNTS.GOOGLE.COM./", True),
                      ("https://accounts.google.com@evil.example/", False),
                      ("https://accounts.google.com.example/", False), ("https://www.google.com/", False),
                      ("", False), (None, False)):
        check(google.is_google_host(url) is want, f"host {url!r} -> {want}")
    check.done()


if __name__ == "__main__":
    run_as_script(globals())
