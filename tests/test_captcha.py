"""
test_captcha.py — invisible reCAPTCHA's badge is not a challenge; a real one is.

WHAT THIS DOES: Loads tests/recaptcha.html (the badge, hidden challenge frame
and response box invisible reCAPTCHA puts on every Greenhouse page) and checks
detect_captcha stays quiet, then reveals a challenge and checks it speaks up.
WHY IT MATTERS: Greenhouse paused on every run for a CAPTCHA that wasn't
there. Silencing that must never silence a real one: we hand those to you.

Run from anywhere:  .venv/Scripts/python.exe tests/test_captcha.py
"""
from pathlib import Path

from _harness import Checks, mock_page, run_as_script
from agent import browser

FORM = Path(__file__).parent / "recaptcha.html"

# Google's frames are answered locally, so the test never touches the network.
ROUTES = {"**/recaptcha/**": lambda route: route.fulfill(
    status=200, content_type="text/html", body="<html><body></body></html>")}


def test_badge_alone_is_not_a_challenge():
    check = Checks()
    with mock_page(FORM, ROUTES) as page:
        badge = page.locator("iframe[src*='size=invisible']")
        check(badge.is_visible(), "the badge iframe is visible, as on Greenhouse")
        check(not page.locator("iframe[src*='bframe']").is_visible(),
              "the challenge frame is present but hidden")
        found = browser.detect_captcha(page)
        check(found is None, f"no captcha reported (got {found!r})")
    check.done()


def test_shown_challenge_is_reported():
    check = Checks()
    with mock_page(FORM, ROUTES) as page:
        page.evaluate("window.revealChallenge()")
        found = browser.detect_captcha(page)
        check(found == "reCAPTCHA", f"visible bframe reported as reCAPTCHA (got {found!r})")
    check.done()


def test_checkbox_recaptcha_is_reported():
    check = Checks()
    with mock_page(FORM, ROUTES) as page:
        page.evaluate("window.addCheckbox()")
        found = browser.detect_captcha(page)
        check(found == "reCAPTCHA", f"visible checkbox anchor reported (got {found!r})")
    check.done()


def test_other_captcha_still_caught_beside_the_badge():
    check = Checks()
    with mock_page(FORM, ROUTES) as page:
        page.evaluate("window.addOtherCaptcha()")
        found = browser.detect_captcha(page)
        check(found == "bot check", f"generic captcha box still reported (got {found!r})")
    check.done()


if __name__ == "__main__":
    run_as_script(globals())
