"""
test_saved_login.py — a browser-filled login gets one click on "Sign in", and nothing more.

WHAT THIS DOES: Loads tests/login_wall.html, where a page script stands in for
Chrome's password manager, and checks sign_in_with_saved_login presses only a
plain "Sign in" once both boxes are filled. It must leave account creation,
look-alike buttons ("Continue with Google") and unfilled forms to you, and the
password must never come back to Python. fill_one must still refuse a password
box.
WHY IT MATTERS: The agent never reads, types, generates or stores a password.
The browser may fill one, and this is the only click the agent makes on a
login page.

Run from anywhere:  .venv/Scripts/python.exe tests/test_saved_login.py
"""
import time
from pathlib import Path

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


def _variant(page, body: str, fill_js: str = "") -> None:
    """Replace the page with another login form, keeping the click recorder."""
    page.set_content(f"""<!doctype html><body>{body}<script>
      window.__clicks = [];
      document.addEventListener('click', e => {{
        const b = e.target.closest('button'); if (b) window.__clicks.push(b.innerText.trim());
      }}, true);
      {fill_js}</script></body>""")


FILL_ALL = """for (const el of document.querySelectorAll('input'))
                el.value = el.type === 'password' ? 'SENTINEL-PW-9' : 'test@example.invalid';"""


def test_signs_in_when_the_browser_filled_the_form():
    check = Checks()
    with mock_page(FORM) as page:
        check(browser.detect_login_wall(page), "login wall detected")
        seen = _record_evaluate(page)
        ok, detail = browser.sign_in_with_saved_login(page, wait_ms=3000)
        print(f"  -> {ok} {detail}")
        check(ok and detail == "pressed 'Sign in'", "waited for the fill, then pressed Sign in")
        check(page.evaluate("window.__signedIn") == 1, "the login form was sent once")
        check(not browser.detect_login_wall(page), "past the login wall")
        check(SENTINEL not in repr(seen), "the password never came back to Python")
    check.done()


def test_unfilled_form_falls_back_to_you():
    check = Checks()
    with mock_page(FORM) as page:
        page.goto(FORM.resolve().as_uri() + "#nofill")
        page.reload()
        start = time.monotonic()
        ok, detail = browser.sign_in_with_saved_login(page, wait_ms=800)
        took = time.monotonic() - start
        print(f"  -> {ok} {detail} ({took:.1f}s)")
        check(not ok and "did not fill" in detail, "nothing filled -> no sign-in")
        check(took < 3, "gave up after the wait, not later")
        check(page.evaluate("window.__clicks") == [], "nothing was clicked")
    check.done()


def test_account_creation_is_left_to_you():
    check = Checks()
    with mock_page(FORM) as page:
        _variant(page, """<form>
            <input type="email" autocomplete="username">
            <input type="password" autocomplete="new-password">
            <input type="password" autocomplete="new-password">
            <label><input type="checkbox"> I agree to the terms</label>
            <button type="submit">Continue</button></form>""", FILL_ALL)
        ok, detail = browser.sign_in_with_saved_login(page, wait_ms=500)
        print(f"  two password boxes -> {ok} {detail}")
        check(not ok and "account creation" in detail, "password + confirm box refused")
        check(page.evaluate("window.__clicks") == [], "  ...and nothing clicked")
        check(not page.locator("input[type=checkbox]").is_checked(), "  ...terms left unticked")

        _variant(page, """<form><input type="email"><input type="password"
            autocomplete="new-password"><button>Sign in</button></form>""", FILL_ALL)
        ok, detail = browser.sign_in_with_saved_login(page, wait_ms=500)
        print(f"  new-password box -> {ok} {detail}")
        check(not ok and "account creation" in detail, "a new-password box is refused")
        check(page.evaluate("window.__clicks") == [], "  ...and nothing clicked")
    check.done()


def test_only_a_plain_sign_in_button_is_pressed():
    check = Checks()
    with mock_page(FORM) as page:
        cases = {
            "look-alikes only": """<button type="button">Continue with Google</button>
                                   <button>Sign in and apply</button>
                                   <button>Create account</button>""",
            "two candidates": "<button>Log in</button><button>Continue</button>",
        }
        for name, buttons in cases.items():
            _variant(page, f"""<form><input type="email" autocomplete="username">
                <input type="password" autocomplete="current-password">{buttons}</form>""",
                     FILL_ALL)
            ok, detail = browser.sign_in_with_saved_login(page, wait_ms=500)
            print(f"  {name} -> {ok} {detail}")
            check(not ok and page.evaluate("window.__clicks") == [], f"{name}: nothing pressed")

        saved = filler.is_forbidden_button
        filler.is_forbidden_button = lambda text: True
        try:
            page.goto(FORM.resolve().as_uri())
            ok, detail = browser.sign_in_with_saved_login(page, wait_ms=3000)
        finally:
            filler.is_forbidden_button = saved
        print(f"  is_forbidden_button says no -> {ok} {detail}")
        check(not ok and "refused" in detail and page.evaluate("window.__clicks") == [],
              "is_forbidden_button still has the last word")
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
