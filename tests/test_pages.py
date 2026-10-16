"""
test_pages.py — follow mode fills nothing on account, sign-in, verification, other-site or LinkedIn pages.

WHAT THIS DOES: Mock pages of each kind: two password boxes or a
new-password box (account), exactly one password box (sign-in), a
one-time-code box (verification), and an ordinary application page. Each
must be recognised from its structure alone, with nothing focused and no
value read. Host families: a portal's subdomains belong together, another
site doesn't, and LinkedIn is refused outright.
WHY IT MATTERS: On those pages you type, Chrome fills passwords and you do
SSO and codes; the agent fills nothing there.

Run from anywhere:  .venv/Scripts/python.exe tests/test_pages.py
"""
from pathlib import Path

from _harness import TEST_HOME, Checks, mock_page, run_as_script
from agent import pages

FOCUS_JS = """<script>window.__focused = [];
  document.addEventListener('focusin', e => window.__focused.push(e.target.id || e.target.tagName));
</script>"""

KINDS = {
    "account (two password boxes)": ("""
      <label for="em">Email</label><input id="em" type="email">
      <label for="p1">Password</label><input id="p1" type="password" value="BROWSER">
      <label for="p2">Confirm</label><input id="p2" type="password">""", pages.ACCOUNT),
    "account (new-password box)": ("""
      <label for="p1">Choose a password</label>
      <input id="p1" type="password" autocomplete="new-password">""", pages.ACCOUNT),
    "sign-in (one password box)": ("""
      <label for="em">Email</label><input id="em" type="email">
      <label for="p1">Password</label><input id="p1" type="password" value="BROWSER">""",
                                   pages.SIGN_IN),
    "verification (one-time-code hint)": ("""
      <label for="c">Code</label><input id="c" autocomplete="one-time-code">""",
                                          pages.VERIFICATION),
    "verification (named otp)": ("""
      <label for="c">Enter the code we sent</label><input id="c" name="otp">""",
                                 pages.VERIFICATION),
    "an application page": ("""
      <label for="fn">First name</label><input id="fn" name="first_name">
      <label for="ph">Phone</label><input id="ph" name="phone">""", None),
}


def _write(name: str, html: str) -> Path:
    path = TEST_HOME / name
    path.write_text(html, encoding="utf-8")
    return path


def test_each_page_kind_is_recognised_without_touching_it():
    check = Checks()
    for i, (what, (body, want)) in enumerate(KINDS.items()):
        html = f"<!doctype html><html><body><form>{body}</form>{FOCUS_JS}</body></html>"
        with mock_page(_write(f"kind_{i}.html", html)) as page:
            seen, real = [], page.evaluate

            def spy(*a, **k):
                out = real(*a, **k)
                seen.append(out)
                return out
            page.evaluate = spy
            got = pages.page_kind(page)
            allowed = pages.fill_allowed(page, page.url)
            page.evaluate = real
            focused = page.evaluate("window.__focused")
        check(got == want and allowed == want, f"{what}: {got}")
        check(focused == [] and "BROWSER" not in repr(seen),
              f"  ...nothing focused and no value read ({focused})")
    check.done()


def test_only_the_applications_host_family_is_filled():
    check = Checks()
    start = "https://job-boards.greenhouse.io/acme/jobs/1"
    same = ["https://boards.greenhouse.io/acme/jobs/1#app", "https://greenhouse.io/x"]
    other = ["https://evil.example.com/form", "https://greenhouse.io.example.com/"]
    check(all(pages.host_family(u) == pages.host_family(start) for u in same),
          "a portal's own subdomains are one family")
    check(all(pages.host_family(u) != pages.host_family(start) for u in other),
          "another site, even one with the portal's name in it, isn't")
    check(pages.host_family("https://careers.acme.co.uk/a") == "acme.co.uk"
          and pages.host_family("https://evil.co.uk/a") != "acme.co.uk",
          "a two-part suffix (co.uk) keeps the company's name")

    class FakePage:
        def __init__(self, url):
            self.url = url
    check(pages.fill_allowed(FakePage("https://evil.example.com/form"), start) == pages.OFF_HOST,
          "a page on another host family: off_host, fill nothing")
    check.done()


def test_linkedin_is_refused_outright():
    check = Checks()

    class FakePage:
        def __init__(self, url):
            self.url = url
    for url in ("https://www.linkedin.com/jobs/view/1", "https://linkedin.com/jobs/apply/2"):
        check(pages.fill_allowed(FakePage(url), url) == pages.LINKEDIN, f"refused: {url}")
    check(pages.fill_allowed(FakePage("https://job-boards.greenhouse.io/a"),
                             "https://www.linkedin.com/jobs/view/1") == pages.LINKEDIN,
          "started from LinkedIn: refused everywhere")
    check(not pages.is_linkedin("https://notlinkedin.com/x"), "a look-alike name isn't LinkedIn")
    check.done()


if __name__ == "__main__":
    run_as_script(globals())
