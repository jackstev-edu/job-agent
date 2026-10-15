"""
test_saved_login.py — at a login wall the agent pauses for you and clicks nothing.

WHAT THIS DOES: Loads tests/login_wall.html, where a page script stands in for
Chrome's password manager and fills both boxes. The login-wall step, and then
a real `fill`, must pause for you and press nothing: not Sign in, not Create
account, not "Continue with Google". The password must never come back to
Python, the old saved-login sign-in must be gone, and fill_one must still
refuse a password box.
WHY IT MATTERS: No code path clicks Sign in, Create account or SSO. The
browser may fill a saved login; you press the button.

Run from anywhere:  .venv/Scripts/python.exe tests/test_saved_login.py
"""
import json
from pathlib import Path
from unittest import mock

from _harness import Checks, mock_page, run_as_script
from agent import browser, filler, sensitive

FORM = Path(__file__).parent / "login_wall.html"
SENTINEL = "SENTINEL-PW-9"


def _record_evaluate(page) -> list:
    """Keep everything page.evaluate hands back, to prove the password never does."""
    seen, real = [], page.evaluate

    def spy(*a, **k):
        out = real(*a, **k)
        seen.append(out)
        return out
    page.evaluate = spy
    return seen


def test_the_login_wall_step_pauses_and_clicks_nothing():
    import apply
    check = Checks()
    with mock_page(FORM) as page:
        page.wait_for_timeout(800)                      # the stand-in autofill has run
        check(page.evaluate("document.getElementById('pw').value.length") > 0,
              "the browser stand-in filled the password (so the checks below mean something)")
        seen = _record_evaluate(page)
        at_prompt = []

        def you(*a):
            at_prompt.append((page.evaluate("window.__clicks"),
                              page.evaluate("window.__signedIn || 0")))
            return ""
        with mock.patch.object(apply, "input", you, create=True):
            wall = apply._get_past_login_wall(page)
    check(wall is True, "the login wall was seen")
    check(at_prompt == [([], 0)], f"it paused for you once, with nothing clicked or sent ({at_prompt})")
    check(SENTINEL not in json.dumps(seen, default=str), "the password never came back to Python")
    check(not hasattr(browser, "sign_in_with_saved_login") and not hasattr(browser, "SIGN_IN_BUTTON"),
          "the saved-login sign-in is gone from browser.py")
    check.done()


def test_a_real_fill_at_a_login_wall_clicks_nothing():
    import apply
    from _fill_run import run_fill

    check = Checks()
    pages, seen_at_prompts = [], []
    real_open = apply.browser_mod.open_page

    def open_page(ctx, url):
        page = real_open(ctx, url)
        pages.append(page)
        return page
    # A login pause, "Click Apply", two more login pauses, then "Did you submit it?".
    replies = iter(["", "", "", "", "n"])

    def you(*a):
        page = pages[-1]
        seen_at_prompts.append((page.evaluate("window.__clicks"),
                                page.evaluate("window.__signedIn || 0")))
        return next(replies)
    text, records, error = run_fill(FORM, dry_run=False,
                                    patches=((apply.browser_mod, "open_page", open_page),
                                             (apply, "input", you)))
    print(text[-700:])
    check(error is None, f"the run finished ({error})")
    check(len(seen_at_prompts) == 5 and all(p == ([], 0) for p in seen_at_prompts),
          f"at every pause, nothing had been clicked and the form wasn't sent ({seen_at_prompts})")
    check("This portal wants a login" in text and "Signed in" not in text,
          "it asked you to sign in and never claimed to have done it")
    check(SENTINEL not in text and SENTINEL not in json.dumps(records, default=str),
          "the password is in neither the output nor the gate's record")
    check.done()


def test_fill_one_still_refuses_a_password_box():
    check = Checks()
    with mock_page(FORM) as page:
        page.goto(FORM.resolve().as_uri() + "#nofill")
        page.reload()
        page.evaluate("document.getElementById('pw').setAttribute('data-ja-ref', 'pw0')")
        field = {"ref": "pw0", "type": "password", "label": "Password", "name": "password"}
        ok, detail = filler.fill_one(page, field, "hunter2")
        print(f"  fill_one -> {ok} {detail}")
        check(not ok and "refused" in detail, "fill_one refuses a password box")
        check(page.evaluate("document.getElementById('pw').value.length") == 0,
              "  ...and nothing was typed into it")
        check(sensitive.decide(field, "personal_fact", None)["action"] == "human",
              "sensitive.decide hands a password box to you, whatever its category")
    check.done()


if __name__ == "__main__":
    run_as_script(globals())
