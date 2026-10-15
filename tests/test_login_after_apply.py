"""
test_login_after_apply.py — a sign-in page that only appears after Apply is still caught.

WHAT THIS DOES: Runs the real `fill --dry-run` against tests/apply_then_login.html,
a Workday-shaped posting whose sign-in page shows up only once
you click Apply. Checks the run asks you to click Apply and never clicks it
itself, then pauses at the sign-in page for you, that the agent never
pressed Sign In (the form wasn't sent before you signed in), and that the dry
run reads the application form behind it, not the sign-in boxes.
WHY IT MATTERS: The login-wall check used to run once, before prepare(), so on
Workday it never saw the sign-in page and the scan read the login form as if
it were the application.

Run from anywhere:  .venv/Scripts/python.exe tests/test_login_after_apply.py
"""
import io
import re
from contextlib import ExitStack, contextmanager, redirect_stdout
from argparse import Namespace
from pathlib import Path
from unittest import mock

from _harness import Checks, run_as_script

FORM = Path(__file__).parent / "apply_then_login.html"


class FakeProfile:
    @classmethod
    def load(cls, *a, **k):
        return cls()

    def audit(self):
        return {"required": [], "recommended": [], "verify": []}

    def resume_path(self, variant=None):
        return None


@contextmanager
def headless_browser():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            yield browser.new_context()
        finally:
            browser.close()


def test_sign_in_page_after_apply_is_caught():
    import apply
    check = Checks()

    pages, at_prompt = [], []
    real_open = apply.browser_mod.open_page

    def open_page(ctx, url):
        page = real_open(ctx, url)
        pages.append(page)
        return page

    def you(*a):
        # You, at each pause: first you click Apply; then, at the sign-in
        # page Chrome has filled, you press Sign In.
        page = pages[-1]
        page.wait_for_timeout(500)
        at_prompt.append({"apply": page.evaluate("window.__applyClicks"),
                          "signed_in": page.evaluate("window.__signedIn")})
        if len(at_prompt) == 1:
            page.click("[data-automation-id=adventureButton]")
        else:
            page.evaluate("document.getElementById('signin').requestSubmit()")
        return ""

    out = io.StringIO()
    args = Namespace(profile=None, url=FORM.resolve().as_uri(), force=False, dry_run=True,
                     resume=None, cover_letter=False)
    error = None
    with ExitStack() as stack:
        # Only what needs the network, your profile or your log is stubbed.
        # detect, prepare, the login-wall check and the scan are all real.
        # Each pause is answered by you().
        for target, name, value in (
                (apply.config, "check_api_key", lambda: ("ok", "")),
                (apply, "Profile", FakeProfile),
                (apply.tracker, "find", lambda url: None),
                (apply.browser_mod, "browser", headless_browser),
                (apply.classify, "classify_fields", lambda fields: {}),
                (apply.browser_mod, "open_page", open_page),
                (apply, "input", you)):               # shadows the builtin in apply
            stack.enter_context(mock.patch.object(target, name, value, create=True))
        try:
            with redirect_stdout(out):
                apply.cmd_fill(args)
        except AssertionError as e:
            error = str(e)
    text = re.sub(r"\x1b\[[0-9;]*m", "", out.getvalue())
    print(text)
    check(error is None, f"the run finished ({error or 'none'})")
    check("Workday" in text and "Click Apply if the form isn't open" in text,
          "detected as Workday, and it asked you to click Apply")
    check(at_prompt == [{"apply": 0, "signed_in": 0}, {"apply": 1, "signed_in": 0}],
          f"it never clicked Apply, then paused at sign-in with the form not sent ({at_prompt})")
    check("This portal wants a login" in text and "Signed in" not in text,
          "it asked you to sign in and never claimed to have done it")
    check("Phone Number" in text, "the dry run read the application form behind the login")
    check("Email Address" not in text and "Password" not in text,
          "the sign-in boxes were never treated as application questions")
    check("SENTINEL" not in text, "the password never printed")
    check.done()


if __name__ == "__main__":
    run_as_script(globals())
