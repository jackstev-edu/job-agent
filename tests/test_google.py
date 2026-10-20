"""
test_google.py — Google sign-in ahead of passwords, by structure, pausing on anything else.

WHAT THIS DOES: Serves a mock portal (sign-in and register pages offering
"Continue with Google") and a mock accounts.google.com (chooser, consent
screens, a sign-in page, a 2-step check, a "not secure" notice), plus a
look-alike host, and drives agent.accounts through them: the Google
control is pressed first, the chooser entry equal to the profile email
(in another case) is clicked, a consent screen listing only the allowed
lines gets Continue, and the portal's form is reached with no password
typed, in the same tab and in a popup. Google's sign-in page, the 2-step
check, the notice, a broader permission list, the one-sentence form, a
line outside the main region, a text box or a second Continue on the
consent screen, and a chooser without the account (or with it twice)
each pause with nothing typed and nothing clicked (in the terminal:
parked; in GUI mode: a Deferred, and Continue resumes the job). The
look-alike host is not Google and gets no click. A link in Google's own
host family never makes Google's pages fillable. Only `google` left
NEVER_WORDS; the other providers are still refused and no step list
matches a "... with Google" text.
WHY IT MATTERS: Jack's 2026-10-19 decisions let the agent press exactly
two things on Google's site and nothing more; these are the proofs.

Run from anywhere:  .venv/Scripts/python.exe tests/test_google.py
"""
import io
from contextlib import redirect_stdout
from unittest import mock
from urllib.parse import parse_qs, urlparse

from _fill_run import _headless, classify_by_label, map_from_profile
from _harness import Checks, run_as_script
from agent import accounts, google, pages, pause, wizard
from test_accounts import EMAIL, GENERATED, _Pauser, _Profile
from test_wizard import _profile

PORTAL = "https://careers.example-co.com"
GOOGLE = "https://accounts.google.com"
LOOKALIKE = "https://accounts.google.com.example"
GOOGLE_CAREERS = "https://careers.google.com"

CLICKS_JS = """<script>
  window.__clicks = [];
  document.addEventListener('click', e => {
    const b = e.target.closest('a, button, [role=button], [role=link], input, [data-identifier]');
    if (b) window.__clicks.push((b.innerText || b.value || '').trim().split('\\n')[0]);
  }, true);
</script>"""


def _doc(title, body):
    return f"<!doctype html><html><head><meta charset='utf-8'><title>{title}</title></head><body>{CLICKS_JS}{body}</body></html>"


def _portal_sign_in(target, popup=False):
    gbtn = (f"<button type='button' onclick=\"window.open('{target}')\">Continue with Google</button>" if popup
            else f"<a href='{target}'>Continue with Google</a>")
    return _doc("Sign in", f"""<h1>Sign in</h1><form id="f"><label for="em">Email Address</label>
<input id="em" type="email" autocomplete="username"><label for="pw">Password</label>
<input id="pw" type="password" autocomplete="current-password"><button type="submit">Sign In</button></form>
{gbtn} <a href="#" id="ms">Sign in with Microsoft</a>
<script>document.getElementById('f').addEventListener('submit', e => {{ e.preventDefault(); location.href = '/form?pw=1'; }});</script>""")


def _portal_register(target):
    return _doc("Create account", f"""<h1>Create your account</h1><form id="f">
<label for="em">Email</label><input id="em" type="email">
<label for="p1">Create Password</label><input id="p1" type="password" autocomplete="new-password">
<label for="p2">Verify New Password</label><input id="p2" type="password" autocomplete="new-password">
<button type="submit">Create Account</button></form><a href='{target}'>Sign up with Google</a>
<script>document.getElementById('f').addEventListener('submit', e => {{ e.preventDefault(); location.href = '/form?pw=1'; }});</script>""")


FORM = _doc("Application", """<h1>Application</h1><form><label for="city">City</label><input id="city" name="city">
<button type="submit">Submit application</button></form>""")

FINISH_JS = f"""<script>function finish() {{
  if (location.search.includes('popup')) {{ window.opener.location.href = '{PORTAL}/form?google=1'; window.close(); }}
  else location.href = '{PORTAL}/form?google=1'; }}</script>"""


def _chooser(match, popup=False, twice=False):
    nxt = "/consent" + ("?popup=1" if popup else "")
    again = (f'<li><div role="link" data-identifier="{match}" onclick="location.href=\'/login\'">Test Person again</div></li>'
             if twice else "")
    return _doc("Choose an account", f"""<div role="main"><h1>Choose an account</h1><p>to continue to Example Co</p><ul>
<li><div role="link" data-identifier="{match}" onclick="location.href='{nxt}'">Test Person<br>{match}</div></li>{again}
<li><div role="link" data-identifier="other@example.invalid" onclick="location.href='/login'">Other Person<br>other@example.invalid</div></li>
<li><div role="link" onclick="location.href='/login'">Use another account</div></li></ul></div>""")


def _consent(lines, share=None, extra="", outside=""):
    items = "".join(f"<li>{ln}</li>" for ln in lines)
    body = f"<ul>{items}</ul>" if lines else f"<p>{share}</p>"
    return _doc("Sign in to Example Co", f"""<div role="main"><h1>Sign in to Example Co</h1>
<p>This will allow Example Co to:</p>{body}{extra}
<button type="button">Cancel</button><button type="button" onclick="finish()">Continue</button></div>{outside}{FINISH_JS}""")


OK_LINES = ["See your primary Google Account email address",
            "See your personal info, including any personal info you've made publicly available"]
BROAD_LINES = OK_LINES + ["See, edit, create, and delete all of your Google Drive files"]
DRIVE = "See, edit, create, and delete all of your Google Drive files"
SHARE_OK = "To continue, Google will share your name, email address, and profile picture with Example Co."
SHARE_LANG = "To continue, Google will share your name, email address, language preference, and profile picture with Example Co."

LOGIN = _doc("Sign in - Google Accounts", """<h1>Sign in</h1><form><label for="id">Email or phone</label>
<input id="id" type="email" name="identifier"><button type="button">Next</button></form>""")
CHALLENGE = _doc("2-Step Verification", """<h1>2-Step Verification</h1><p>Enter the code from your phone.</p>
<form><input type="tel" name="totpPin" aria-label="Enter code"><button type="button">Next</button></form>""")
INSECURE = _doc("Google", """<h1>Couldn't sign you in</h1><p>This browser or app may not be secure.</p>
<button type="button">Try again</button>""")

GOOGLE_PAGES = {
    "/chooser": lambda q: _chooser("Test@Example.invalid", popup="popup" in q),
    "/chooser-nomatch": lambda q: _chooser("nobody@example.invalid"),
    "/chooser-twice": lambda q: _chooser("test@example.invalid", twice=True),
    "/consent": lambda q: _consent(OK_LINES), "/consent-broad": lambda q: _consent(BROAD_LINES),
    "/consent-two": lambda q: _consent(OK_LINES, extra='<button type="button" onclick="finish()">Continue</button>'),
    "/consent-box": lambda q: _consent(OK_LINES, extra='<input type="text" aria-label="Note">'),
    "/consent-outside": lambda q: _consent(OK_LINES, outside=f"<footer><ul><li>{DRIVE}</li></ul></footer>"),
    "/share-ok": lambda q: _consent([], SHARE_OK), "/share-lang": lambda q: _consent([], SHARE_LANG),
    "/login": lambda q: LOGIN, "/challenge": lambda q: CHALLENGE, "/insecure": lambda q: INSECURE}


def _routes():
    def handle(route):
        u = urlparse(route.request.url)
        q = parse_qs(u.query)
        base = f"{u.scheme}://{u.hostname}"
        if base in (PORTAL, GOOGLE_CAREERS):
            g = (q.get("g") or ["chooser"])[0]
            target = f"{LOOKALIKE}/chooser" if g == "lookalike" else f"{GOOGLE}/{g}"
            if u.path == "/apply":
                body = _portal_sign_in(target, popup=g == "popup")
                if g == "popup":
                    body = _portal_sign_in(f"{GOOGLE}/chooser?popup=1", popup=True)
            elif u.path == "/register":
                body = _portal_register(target)
            elif u.path == "/form":
                body = FORM
            else:
                body = "<p>404</p>"
        elif base in (GOOGLE, LOOKALIKE):
            maker = GOOGLE_PAGES.get(u.path)
            body = maker(u.query) if maker else "<p>404</p>"
        else:
            body = "<p>404</p>"
        route.fulfill(status=200, content_type="text/html", body=body)
    return {f"{PORTAL}/**": handle, f"{GOOGLE}/**": handle, f"{LOOKALIKE}/**": handle, f"{GOOGLE_CAREERS}/**": handle}


def _run(page, kind, pauser, prof=None):
    out = io.StringIO()
    with mock.patch("anthropic.Anthropic", side_effect=AssertionError("a model client was built")), \
            mock.patch.object(accounts, "generate_password", lambda length=24: GENERATED), \
            redirect_stdout(out):
        res = accounts.handle(page, kind, prof or _Profile(), pauser)
    return res, out.getvalue()


def _clicks(page):
    return page.evaluate("window.__clicks")


def _gui_pauser():
    return pause.GuiPauser(notify_fn=lambda t, b, echo=print: None, echo=lambda *a, **k: None)


def _wizard(page, url, pauser):
    return wizard.Wizard(page, _profile(), url=url, portal="generic", portal_method="default",
                         company="Example Co", role="Engineer", resume=None, posting_text="",
                         pauser=pauser, echo=lambda *a, **k: None)


def test_google_is_pressed_ahead_of_the_password_flow():
    check = Checks()
    with _headless(_routes()) as ctx:
        page = ctx.new_page(); page.goto(f"{PORTAL}/apply")
        pauser = _Pauser([])
        res, printed = _run(page, pages.SIGN_IN, pauser)
        check(res.status == accounts.CONTINUE and page.url == f"{PORTAL}/form?google=1",
              f"the Google route reaches the form ({res.status}, {res.reason}, {page.url})")
        check(not pauser.asked, "no pause")
        check(res.steps[:3] == ["pressed 'Continue with Google'", "chose the profile's account on Google's chooser",
                                "pressed Continue on Google's permission screen (name, email address, profile picture only)"]
              and res.steps[-1] == "the sign-in page went away after the Google button", f"the steps, in order ({res.steps})")
        check(any(e["kind"] == "google_sign_in" and e["handled"] == "agent" for e in res.events),
              "the event says the agent did it")
        check("typed" not in " ".join(res.steps), "nothing typed anywhere")
    check.done()


def test_a_popup_google_flow_is_followed():
    check = Checks()
    with _headless(_routes()) as ctx:
        page = ctx.new_page(); page.goto(f"{PORTAL}/apply?g=popup")
        pauser = _Pauser([])
        res, printed = _run(page, pages.SIGN_IN, pauser)
        check(res.status == accounts.CONTINUE and page.url == f"{PORTAL}/form?google=1",
              f"the popup route reaches the form ({res.status}, {res.reason}, {page.url})")
        check("chose the profile's account on Google's chooser" in res.steps, "the chooser was walked in the popup")
        check(not pauser.asked, "no pause")
    check.done()


def test_googles_sign_in_page_pauses_with_nothing_typed():
    check = Checks()
    with _headless(_routes()) as ctx:
        page = ctx.new_page(); page.goto(f"{PORTAL}/apply?g=login")
        pauser = _Pauser([None])
        res, printed = _run(page, pages.SIGN_IN, pauser)
        check(res.status == accounts.PARKED and res.reason == accounts.GOOGLE_STUCK,
              f"parked on Google's sign-in page ({res.status}, {res.reason})")
        check(page.url == f"{GOOGLE}/login" and _clicks(page) == []
              and page.evaluate("document.getElementById('id').value") == "",
              f"still on the sign-in page, nothing clicked or typed ({page.url}, {_clicks(page)})")
        check(len(pauser.asked) == 1 and "never touches" in pauser.asked[0][0] and not pauser.asked[0][1],
              f"one pause, plain prompt ({pauser.asked})")
        # Enter pressed while still on the sign-in page: looked at again, then parked.
        page.goto(f"{PORTAL}/apply?g=login")
        pauser = _Pauser(["", ""])
        res, _ = _run(page, pages.SIGN_IN, pauser)
        check(res.status == accounts.PARKED and len(pauser.asked) >= 2 and _clicks(page) == [],
              f"Enter with the page unchanged: looked again, still nothing touched, parked ({len(pauser.asked)})")
        # GUI mode: the pause is deferred, the page untouched.
        page.goto(f"{PORTAL}/apply?g=login")
        raised = None
        try:
            _run(page, pages.SIGN_IN, _gui_pauser())
        except pause.Deferred as e:
            raised = e
        check(raised is not None and "never touches" in raised.body, "GUI mode: a Deferred")
        check(page.url == f"{GOOGLE}/login" and _clicks(page) == []
              and page.evaluate("document.getElementById('id').value") == "", "  ...and nothing touched")
    check.done()


def test_a_two_step_check_and_a_not_secure_notice_pause():
    check = Checks()
    with _headless(_routes()) as ctx:
        for g, box in (("challenge", "input"), ("insecure", None)):
            page = ctx.new_page(); page.goto(f"{PORTAL}/apply?g={g}")
            pauser = _Pauser([None])
            res, _ = _run(page, pages.SIGN_IN, pauser)
            check(res.status == accounts.PARKED and page.url == f"{GOOGLE}/{g}" and _clicks(page) == [],
                  f"{g}: parked, nothing clicked ({res.status}, {page.url}, {_clicks(page)})")
            if box:
                check(page.evaluate(f"document.querySelector('{box}').value") == "", f"{g}: nothing typed")
            page.close()
    check.done()


def test_any_other_permission_screen_pauses_without_a_click():
    check = Checks()
    with _headless(_routes()) as ctx:
        for g, why in (("consent-broad", "more than name"), ("share-lang", "never touches"),
                       ("share-ok", "never touches"), ("consent-outside", "more than name"),
                       ("consent-two", "never touches"), ("consent-box", "never touches")):
            page = ctx.new_page(); page.goto(f"{PORTAL}/apply?g={g}")
            pauser = _Pauser([None])
            res, _ = _run(page, pages.SIGN_IN, pauser)
            check(res.status == accounts.PARKED and page.url == f"{GOOGLE}/{g}" and _clicks(page) == [],
                  f"{g}: parked, Continue not pressed ({res.status}, {page.url}, {_clicks(page)})")
            check(pauser.asked and why in pauser.asked[0][0], f"{g}: the pause says why ({pauser.asked[0][0][:60]})")
            page.close()
    for entries, want in (([], False), (OK_LINES, True), (BROAD_LINES, False),
                          (["See your primary Google Account email address", ""], False),
                          (["See your primary Google Account email address", 3], False),
                          (["see your primary google account email address"], True),
                          (["See  your primary Google Account email address "], True)):
        check(google.permissions_ok(entries) is want, f"permissions_ok({entries}) -> {want}")
    check.done()


def test_a_chooser_without_the_profile_account_or_with_it_twice_pauses():
    check = Checks()
    with _headless(_routes()) as ctx:
        for g in ("chooser-nomatch", "chooser-twice"):
            page = ctx.new_page(); page.goto(f"{PORTAL}/apply?g={g}")
            pauser = _Pauser([None])
            res, _ = _run(page, pages.SIGN_IN, pauser)
            check(res.status == accounts.PARKED and page.url == f"{GOOGLE}/{g}" and _clicks(page) == [],
                  f"{g}: no entry clicked, not even 'Use another account' ({res.status}, {page.url}, {_clicks(page)})")
            check(pauser.asked and "doesn't list the profile's account" in pauser.asked[0][0], f"{g}: the pause says why")
            page.close()
        # The list changing between the shape read and the mark: no click.
        page = ctx.new_page(); page.goto(f"{GOOGLE}/chooser")
        ids = ["test@example.invalid", "other@example.invalid"]       # a stale read: the page has the other order
        page.evaluate("const l = document.querySelectorAll('li'); l[0].parentNode.insertBefore(l[1], l[0])")
        check(google.pick_account(page, ids, EMAIL) is False and _clicks(page) == [],
              f"a stale index that now points at another account gets no click ({_clicks(page)})")
    check.done()


def test_a_look_alike_host_is_not_google():
    check = Checks()
    with _headless(_routes()) as ctx:
        page = ctx.new_page(); page.goto(f"{PORTAL}/apply?g=lookalike")
        pauser = _Pauser([None])
        res, _ = _run(page, pages.SIGN_IN, pauser)
        check(page.url == f"{LOOKALIKE}/chooser" and _clicks(page) == [],
              f"nothing clicked on the look-alike chooser ({page.url}, {_clicks(page)})")
        check(not google.is_google(page.url) and "chose the profile's account" not in " ".join(res.steps),
              "it isn't Google, so no account was chosen")
        check(not pauser.asked and res.status == accounts.CONTINUE,
              f"accounts hands the page back for the wizard's off-host check ({res.status}, {pauser.asked})")
    check.done()


def test_only_google_left_never_words():
    check = Checks()
    for word in ("Sign in with Microsoft", "Continue with Apple", "Log in with Facebook",
                 "Sign in with LinkedIn", "SSO", "Single sign-on", "Submit", "Continue"):
        check(bool(accounts.NEVER_WORDS.search(word)), f"'{word}' is still never pressed by accounts")
    for word in ("Continue with Google", "Sign in with Google", "Sign up with Google", "Log in with Google"):
        check(not accounts.NEVER_WORDS.search(word) and bool(google.BUTTON_WORDS.match(word)),
              f"'{word}' is the Google control")
        for rx in (accounts.SIGN_IN_WORDS, accounts.CREATE_WORDS, accounts.CREATE_LINK_WORDS,
                   accounts.RESET_SEND_WORDS, accounts.VERIFY_WORDS, accounts.FORGOT_WORDS):
            check(not rx.match(word), f"  ...and no other step list matches it ({rx.pattern[:25]}...)")
    for word in ("Google", "Continue with Google Workspace", "Apply with Google", "Sign in with Google Drive"):
        check(not google.BUTTON_WORDS.match(word), f"'{word}' is not the Google control")
    with _headless(_routes()) as ctx:
        page = ctx.new_page(); page.goto(f"{PORTAL}/apply?g=login")
        page.evaluate("document.querySelector('a[href*=google]').remove()")
        res, _ = _run(page, pages.SIGN_IN, _Pauser([None]))
        check("Sign in with Microsoft" not in _clicks(page) and page.url.startswith(PORTAL),
              f"with no Google control, the Microsoft one is never pressed ({_clicks(page)})")
    check.done()


def test_google_first_on_a_sign_up_page():
    check = Checks()
    with _headless(_routes()) as ctx:
        page = ctx.new_page(); page.goto(f"{PORTAL}/register")
        res, printed = _run(page, pages.ACCOUNT, _Pauser([]))
        check(res.status == accounts.CONTINUE and page.url == f"{PORTAL}/form?google=1",
              f"sign-up went through Google ({res.status}, {res.reason}, {page.url})")
        check(res.steps[0] == "pressed 'Sign up with Google'"
              and res.steps[-1] == "the account page went away after the Google button"
              and not any("password" in s for s in res.steps), f"no password generated or typed ({res.steps})")
        check(GENERATED not in printed + str(res.__dict__), "the sentinel is nowhere")
    check.done()


def test_the_wizard_hands_a_same_tab_google_page_to_accounts_and_resumes():
    check = Checks()
    with _headless(_routes()) as ctx:
        page = ctx.new_page(); page.goto(f"{GOOGLE}/login")
        w = _wizard(page, f"{PORTAL}/apply", _gui_pauser())
        with redirect_stdout(io.StringIO()):
            out = w.run()
        check(out.status == wizard.WAITING and "never touches" in out.reason,
              f"a same-tab Google sign-in page waits for Jack instead of parking off-host ({out.status}, {out.reason})")
        check(page.url == f"{GOOGLE}/login" and _clicks(page) == []
              and page.evaluate("document.getElementById('id').value") == "", "nothing touched on it")
        # Jack signs in by hand and the tab comes back to the portal; Continue goes on.
        page.goto(f"{PORTAL}/form?google=1")
        with mock.patch("agent.classify.classify_fields", classify_by_label), \
                mock.patch("agent.mapper.map_fields", map_from_profile), redirect_stdout(io.StringIO()):
            out = w.continue_from({})
        # The job went on: the portal's form was filled and its gate paused on
        # City (a gate pause now, not the Google one).
        check(out.status == wizard.WAITING and "City" in out.reason and "never touches" not in out.reason
              and "resumed after a account pause" in out.log and w.pending and w.pending.get("kind") == "gate",
              f"Continue resumes the job on the portal's form ({out.status}, {out.reason}, {out.log[-3:]})")
    check.done()


def test_a_google_family_start_link_never_makes_googles_pages_fillable():
    check = Checks()
    with _headless(_routes()) as ctx:
        page = ctx.new_page(); page.goto(f"{GOOGLE}/login")
        check(pages.fill_allowed(page, f"{GOOGLE_CAREERS}/jobs/1") == pages.OFF_HOST
              and pages.fill_allowed(page, f"{GOOGLE}/x") == pages.OFF_HOST,
              "fill_allowed refuses Google's site whatever the link's host family")
        # The terminal wizard, started from careers.google.com, on Google's sign-in page.
        w = _wizard(page, f"{GOOGLE_CAREERS}/jobs/1", _Pauser([None]))
        with mock.patch("agent.classify.classify_fields", classify_by_label), \
                mock.patch("agent.mapper.map_fields", map_from_profile), redirect_stdout(io.StringIO()):
            out = w.run()
        check(out.status == "needs_you" and out.reason == accounts.GOOGLE_STUCK and out.pages_filled == 0,
              f"parked after the Google pause, nothing filled ({out.status}, {out.reason}, {out.pages_filled})")
        check(page.url == f"{GOOGLE}/login" and _clicks(page) == []
              and page.evaluate("document.getElementById('id').value") == "",
              f"nothing typed into 'Email or phone', Next never pressed ({_clicks(page)})")
        # The same from the portal in Google's family: the button leads to Google's sign-in page.
        page.goto(f"{GOOGLE_CAREERS}/apply?g=login")
        w = _wizard(page, f"{GOOGLE_CAREERS}/apply", _Pauser([None]))
        with mock.patch("agent.classify.classify_fields", classify_by_label), \
                mock.patch("agent.mapper.map_fields", map_from_profile), redirect_stdout(io.StringIO()):
            out = w.run()
        check(out.status == "needs_you" and page.url == f"{GOOGLE}/login" and _clicks(page) == []
              and page.evaluate("document.getElementById('id').value") == "",
              f"from a careers.google.com sign-in page too ({out.status}, {out.reason}, {page.url})")
    check.done()


if __name__ == "__main__":
    run_as_script(globals())
