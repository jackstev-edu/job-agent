"""
test_login_after_apply.py — a sign-in page that only appears after Apply is still caught.

WHAT THIS DOES: Runs the real `fill --dry-run` against tests/apply_then_login.html,
a Workday-shaped posting whose sign-in page shows up only once
adapters.prepare() clicks Apply. Checks the saved-login sign-in happens after
that click and the dry run reads the application form behind it, not the
sign-in boxes.
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

    def no_pause(*a):
        raise AssertionError("fill paused for a manual login")

    out = io.StringIO()
    args = Namespace(profile=None, url=FORM.resolve().as_uri(), force=False, dry_run=True,
                     resume=None, cover_letter=False, max_steps=6)
    error = None
    with ExitStack() as stack:
        # Only what needs the network, your profile or your log is stubbed.
        # detect, prepare, the login-wall check and the scan are all real.
        for target, name, value in (
                (apply.config, "check_api_key", lambda: ("ok", "")),
                (apply, "Profile", FakeProfile),
                (apply.tracker, "find", lambda url: None),
                (apply.browser_mod, "browser", headless_browser),
                (apply.classify, "classify_fields", lambda fields: {}),
                (apply, "input", no_pause)):          # shadows the builtin in apply
            stack.enter_context(mock.patch.object(target, name, value, create=True))
        try:
            with redirect_stdout(out):
                apply.cmd_fill(args)
        except AssertionError as e:
            error = str(e)
    text = re.sub(r"\x1b\[[0-9;]*m", "", out.getvalue())
    print(text)
    check(error is None, f"no manual pause ({error or 'none'})")
    check("Workday" in text, "detected as Workday, so prepare() clicks its Apply button")
    check("Signed in with the login saved in this browser profile" in text,
          "signed in after the Apply click")
    check("Phone Number" in text, "the dry run read the application form behind the login")
    check("Email Address" not in text and "Password" not in text,
          "the sign-in boxes were never treated as application questions")
    check("SENTINEL" not in text, "the password never printed")
    check.done()


if __name__ == "__main__":
    run_as_script(globals())
