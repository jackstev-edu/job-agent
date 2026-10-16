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
    "verification (named otp_code)": ("""
      <label for="c">Code</label><input id="c" name="otp_code">""", pages.VERIFICATION),
    "verification (id otpCode)": ("""
      <label for="otpCode">Code</label><input id="otpCode">""", pages.VERIFICATION),
    "sign-in (password shown as text, the hint kept)": ("""
      <label for="em">Email</label><input id="em" type="email">
      <label for="p1">Password</label>
      <input id="p1" type="text" autocomplete="current-password" value="BROWSER">""",
                                                        pages.SIGN_IN),
    "sign-in (no-hint box shown as text)": ("""
      <label for="em">Email</label><input id="em" type="email">
      <label for="p1">Password</label><input id="p1" type="text" name="password" value="BROWSER">""",
                                            pages.SIGN_IN),
    "account (two no-hint boxes shown as text)": ("""
      <label for="p1">Password</label><input id="p1" type="text" name="password">
      <label for="p2">Confirm password</label><input id="p2" type="text" name="confirmPassword">""",
                                                  pages.ACCOUNT),
    "sign-in (hidden password box)": ("""
      <label for="em">Email</label><input id="em" type="email">
      <input id="p1" type="password" style="display:none">""", pages.SIGN_IN),
    "an application page": ("""
      <label for="fn">First name</label><input id="fn" name="first_name">
      <label for="ph">Phone</label><input id="ph" name="phone">""", None),
}


def _write(name: str, html: str) -> Path:
    path = TEST_HOME / name
    path.write_text(html, encoding="utf-8")
    return path


HOST = "https://jobs.example.com"


def _served(html: str):
    """A mock page at a real-looking host (a local file has no host: off_host)."""
    routes = {f"{HOST}/**": lambda route: route.fulfill(content_type="text/html", body=html)}
    return mock_page(_write("pages_blank.html", "<!doctype html><html></html>"), routes)


def test_each_page_kind_is_recognised_without_touching_it():
    check = Checks()
    for i, (what, (body, want)) in enumerate(KINDS.items()):
        html = f"<!doctype html><html><body><form>{body}</form>{FOCUS_JS}</body></html>"
        with _served(html) as page:
            page.goto(f"{HOST}/apply/{i}")
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
        check(got == want and allowed == want, f"{what}: {got} / {allowed}")
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
    check(pages.host_family("https://acme.ne.jp/") != pages.host_family("https://evil.ne.jp/"),
          "an unknown registry level (ne.jp) keeps the whole host, never a shared family")
    check(pages.host_family("http://10.0.3.4/") != pages.host_family("http://192.168.3.4/"),
          "an IP address is its own family")

    class FakePage:
        def __init__(self, url):
            self.url = url
    check(pages.fill_allowed(FakePage("https://evil.example.com/form"), start) == pages.OFF_HOST,
          "a page on another host family: off_host, fill nothing")
    for start_url in ("", None, "about:blank"):
        check(pages.fill_allowed(FakePage("https://boards.greenhouse.io/a"), start_url)
              == pages.OFF_HOST, f"no start host ({start_url!r}): off_host")
    check(pages.fill_allowed(FakePage("file:///C:/form.html"), "file:///C:/form.html")
          == pages.OFF_HOST, "a local file has no host: off_host")
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
    check(pages.is_linkedin("https://lnkd.in/abc") and pages.is_linkedin("https://www.linkedin.cn/x"),
          "LinkedIn's short links and its .cn site are refused too")
    check.done()


def test_a_credential_page_stays_one_for_the_run():
    check = Checks()
    html = f"""<!doctype html><html><body><form>
      <label for="em">Email</label><input id="em" type="email">
      <label for="p1">Pass</label><input id="p1" type="password"></form>
      <script>window.toggle = () => {{ const b = document.getElementById('p1');
        b.type = 'text'; b.removeAttribute('id'); b.setAttribute('aria-label', 'Show'); }};</script>
      </body></html>"""
    seen: dict = {}
    with _served(html) as page:
        page.goto(f"{HOST}/login")
        first = pages.fill_allowed(page, f"{HOST}/apply", seen)
        page.evaluate("window.toggle()")               # now no password box, no hint, no words
        check(pages.page_kind(page) is None, "after the toggle the page no longer looks like one "
                                             "(so the next check means something)")
        later = pages.fill_allowed(page, f"{HOST}/apply", seen)
    check(first == later == pages.SIGN_IN, f"remembered as a sign-in page for the run ({first}, {later})")

    class FakePage:
        url = "https://jobs.example.com/x"
    check(pages.fill_allowed(FakePage(), "http://[::1") == pages.OFF_HOST,
          "a malformed start address: off_host, not an error")
    check.done()


def test_a_page_that_cant_be_read_fills_nothing():
    check = Checks()

    class Broken:
        url = f"{HOST}/apply"

        def __init__(self, result):
            self.result = result

        def evaluate(self, *a):
            if isinstance(self.result, Exception):
                raise self.result
            return self.result
    for result in (RuntimeError("gone"), None, {"pw": "0", "newPw": False, "code": False}):
        check(pages.page_kind(Broken(result)) == pages.ACCOUNT,
              f"unreadable or malformed ({type(result).__name__}): treated as an account page")
    check.done()


if __name__ == "__main__":
    run_as_script(globals())
