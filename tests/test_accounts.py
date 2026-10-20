"""
test_accounts.py — sign-up, sign-in, forgot-password and verification, unattended.

WHAT THIS DOES: Serves a mock portal (sign-in, register, verify, reset and
application pages, with its state in sessionStorage) and drives
agent.accounts through it with a scripted pauser: a sign-up types the
profile email and name, a generated password (a sentinel, by patching the
generator) into both new-password boxes, ticks the terms box and not the
marketing one, presses Create account, then answers the verification code;
a sign-in with a saved password just signs in; one without goes through
Create account; one with no account link goes through Forgot password and
the pasted password; a verification that gets no code is parked; a page
whose only button reads Submit application is never pressed. Everything
printed, every file in the test home, every tracker row and the result
objects are searched for the sentinels and must not hold them, and the
Anthropic client must never be constructed.
WHY IT MATTERS: This is the only module that types a password, under the
owner decisions of 2026-10-18, and the proof that a password reaches no
log, file, DB row or model.

Run from anywhere:  .venv/Scripts/python.exe tests/test_accounts.py
"""
import io
import json
from contextlib import redirect_stdout
from unittest import mock

from _harness import TEST_HOME, Checks, mock_page, run_as_script
from agent import accounts, pages, pause, tracker

HOST = "https://careers.example-co.com"
GENERATED = "GEN-PW-SENTINEL-7Qx!"
PASTED = "PASTED-PW-SENTINEL-3Zq!"
CODE = "246810"
EMAIL = "test@example.invalid"

STATE_JS = """<script>
  const S = key => sessionStorage.getItem(key) || '';
  const set = (k, v) => sessionStorage.setItem(k, v);
  window.__clicks = JSON.parse(sessionStorage.getItem('clicks') || '[]');
  document.addEventListener('click', e => {
    const b = e.target.closest('button, a, input[type=submit]');
    if (b) { window.__clicks.push((b.innerText || b.value || '').trim());
             sessionStorage.setItem('clicks', JSON.stringify(window.__clicks)); }
  }, true);
</script>"""

SIGN_IN = STATE_JS + """<h1>Sign in to continue your application</h1>
<form id="f"><label for="em">Email Address</label><input id="em" type="email" autocomplete="username">
<label for="pw">Password</label><input id="pw" type="password" autocomplete="current-password">
<button type="submit">Sign In</button></form>
<a href="/forgot">Forgot password?</a> CREATE_LINK
<script>
  if (sessionStorage.getItem('saved') === '1') {            // Chrome's stand-in
    document.getElementById('pw').value = sessionStorage.getItem('pw') || '';
  }
  document.getElementById('f').addEventListener('submit', e => {
    e.preventDefault();
    const ok = document.getElementById('pw').value && document.getElementById('pw').value === sessionStorage.getItem('pw')
            && document.getElementById('em').value === sessionStorage.getItem('email');
    sessionStorage.setItem('signin_tries', String(1 + Number(sessionStorage.getItem('signin_tries') || 0)));
    if (ok) location.href = '/form'; else document.getElementById('f').insertAdjacentHTML('beforeend', '<p>Wrong</p>');
  });
</script>"""

REGISTER = STATE_JS + """<h1>Create your account</h1>
<form id="f"><label for="em">Email</label><input id="em" type="email">
<label for="fn">First Name</label><input id="fn" name="firstName">
<label for="ln">Last Name</label><input id="ln" name="lastName">
<label for="p1">Create Password</label><input id="p1" type="password" autocomplete="new-password">
<label for="p2">Verify New Password</label><input id="p2" type="password" autocomplete="new-password">
<input type="checkbox" id="tos" required><label for="tos">I have read and agree to the Terms of Use and Privacy Policy</label>
<input type="checkbox" id="mkt"><label for="mkt">I agree to receive marketing emails and job alerts</label>
<button type="submit">Create Account</button></form>
<script>
  document.getElementById('f').addEventListener('submit', e => {
    e.preventDefault();
    const p1 = document.getElementById('p1').value, p2 = document.getElementById('p2').value;
    const rec = {email: document.getElementById('em').value, first: document.getElementById('fn').value,
                 last: document.getElementById('ln').value, p1, p2,
                 tos: document.getElementById('tos').checked, mkt: document.getElementById('mkt').checked};
    sessionStorage.setItem('created', JSON.stringify(rec));
    if (!rec.tos || p1 !== p2 || p1.length < 12) {
      document.getElementById('f').insertAdjacentHTML('beforeend', '<p>Fix the form</p>'); return; }
    if (sessionStorage.getItem('email') === rec.email && sessionStorage.getItem('exists') === '1') {
      location.href = '/apply'; return;                    // already registered: back to sign-in
    }
    sessionStorage.setItem('email', rec.email); sessionStorage.setItem('pw', p1);
    location.href = '/verify';
  });
</script>"""

VERIFY = STATE_JS + """<h1>Check your email</h1><p>Enter the verification code we sent.</p>
<form id="f"><label for="vc">Verification code</label><input id="vc" name="verificationCode">
<button type="submit">Verify</button></form>
<script>
  document.getElementById('f').addEventListener('submit', e => {
    e.preventDefault();
    if (document.getElementById('vc').value === 'CODE') location.href = '/form';
    else document.getElementById('f').insertAdjacentHTML('beforeend', '<p>Wrong code</p>');
  });
</script>""".replace("CODE", CODE)

FORGOT = STATE_JS + """<h1>Reset your password</h1>
<form id="f"><label for="em">Email</label><input id="em" type="email">
<button type="submit">Send reset link</button></form>
<script>
  document.getElementById('f').addEventListener('submit', e => {
    e.preventDefault(); sessionStorage.setItem('reset_for', document.getElementById('em').value);
    location.href = '/sent'; });
</script>"""
SENT = STATE_JS + "<h1>Check your email</h1><p>We sent a link.</p><a href='/apply'>Back to sign in</a>"
FORM = STATE_JS + """<h1>Application</h1><form><label for="city">City</label><input id="city" name="city">
<button type="submit">Submit application</button></form>"""
SUBMIT_ONLY_WALL = STATE_JS + """<h1>Sign in</h1><form><label for="em">Email</label><input id="em" type="email">
<label for="pw">Password</label><input id="pw" type="password"><button type="submit">Submit application</button></form>"""
# A wall whose buttons read as the wizard's words: bare Submit, Continue, Next.
BARE_WALL = STATE_JS + """<h1>Sign in</h1><form><label for="em">Email</label><input id="em" type="email">
<label for="pw">Password</label><input id="pw" type="password">
<button type="submit">Submit</button><button type="button">Continue</button><button type="button">Next</button></form>"""
# A reset link that leads to an ordinary form, not a reset page.
FORGOT_TO_FORM = STATE_JS + """<h1>Application</h1><form><label for="em">Email</label><input id="em" type="email">
<label for="city">City</label><input id="city" name="city"><button type="submit">Submit</button></form>"""
# A register page whose password boxes allow only 16 characters.
SHORT_REGISTER = REGISTER.replace('id="p1" type="password"', 'id="p1" type="password" maxlength="16"')


BLANK = TEST_HOME / "accounts_blank.html"
BLANK.write_text("<!doctype html><html><body></body></html>", encoding="utf-8")


def _site(create_link=True, exists=False):
    html = lambda body: f"<!doctype html><html><head><meta charset='utf-8'></head><body>{body}</body></html>"
    link = "<a href='/register'>Create account</a>" if create_link else ""
    return {"/apply": html(SIGN_IN.replace("CREATE_LINK", link)), "/register": html(REGISTER),
            "/verify": html(VERIFY), "/forgot": html(FORGOT), "/sent": html(SENT), "/form": html(FORM),
            "/wall": html(SUBMIT_ONLY_WALL), "/bare": html(BARE_WALL),
            "/short": html(SHORT_REGISTER), "/forgot-form": html(FORGOT_TO_FORM),
            "/apply-forgot-form": html(SIGN_IN.replace("CREATE_LINK", "").replace("/forgot", "/forgot-form"))}


def _routes(site):
    def handle(route):
        path = route.request.url.split("example-co.com", 1)[-1].split("?")[0]
        route.fulfill(status=200, content_type="text/html", body=site.get(path, "<p>404</p>"))
    return {f"{HOST}/**": handle}


class _Profile:
    def __init__(self, **data):
        self.data = {"personal.email": EMAIL, "personal.first_name": "Test",
                     "personal.last_name": "Person", **data}

    def get(self, path, default=None):
        return self.data.get(path, default)


class _Pauser(pause.Pauser):
    """Answers each pause from a script, without waiting; records what it was told."""

    def __init__(self, answers):
        super().__init__(notify_fn=lambda t, b, echo=print: None, echo=lambda *a, **k: None)
        self.answers = list(answers)
        self.asked: list[tuple[str, bool]] = []

    def wait(self, title, body, *, prompt, hidden=False, until=None, wants_code=False):
        self.asked.append((body, hidden))
        if not self.answers:
            return None, pause.TIMED_OUT
        answer = self.answers.pop(0)
        if answer is None:
            return None, pause.TIMED_OUT
        return answer, pause.ANSWERED


def _run(page, kind, prof, pauser):
    """Drive accounts.handle with every print captured and the Anthropic client forbidden."""
    out = io.StringIO()
    with mock.patch("anthropic.Anthropic", side_effect=AssertionError("a model client was built")), \
            mock.patch.object(accounts, "generate_password", lambda length=24: GENERATED), \
            redirect_stdout(out):
        res = accounts.handle(page, kind, prof, pauser)
    return res, out.getvalue()


def _nowhere(check, what: str, *texts):
    """The sentinels are in none of: the given texts, any file in the test home, any tracker row."""
    blob = "\n".join(texts)
    for root in (TEST_HOME,):
        for f in root.rglob("*"):
            if f.is_file() and f.suffix in (".html", ".jsonl", ".txt", ".yaml", ".log", ".json"):
                try:
                    blob += f.read_text(encoding="utf-8", errors="ignore")
                except Exception:
                    pass
    blob += json.dumps(tracker.all_applications(), default=str)
    blob += json.dumps(tracker.gate_runs(), default=str)
    check(GENERATED not in blob and PASTED not in blob, f"no password anywhere ({what})")


def _session(page, key):
    return page.evaluate(f"sessionStorage.getItem('{key}')")


def test_sign_up_types_the_generated_password_once_and_answers_the_code():
    check = Checks()
    with mock_page(BLANK, _routes(_site())) as page:
        page.goto(f"{HOST}/register")
        check(pages.page_kind(page) == pages.ACCOUNT, "the register page reads as an account page")
        pauser = _Pauser([CODE])
        res, printed = _run(page, pages.ACCOUNT, _Profile(), pauser)
        created = json.loads(_session(page, "created") or "{}")
        check(res.status == accounts.CONTINUE and page.url.endswith("/form"),
              f"created, verified, on the form ({res.status}, {res.reason}, {page.url})")
        check(created.get("email") == EMAIL and created.get("first") == "Test"
              and created.get("last") == "Person", f"email and name from the profile ({created})")
        check(created.get("p1") == GENERATED and created.get("p2") == GENERATED,
              "the generated password went into both new-password boxes (the page saw it)")
        check(created.get("tos") is True and created.get("mkt") is False,
              f"the terms box ticked, the marketing box not ({created.get('tos')}, {created.get('mkt')})")
        check(pauser.asked and not pauser.asked[0][1] and "verification code" in pauser.asked[0][0].lower(),
              "the verification pause asked for a code, not hidden")
        clicks = page.evaluate("window.__clicks")
        check(clicks == ["Create Account", "Verify"], f"only Create Account and Verify pressed ({clicks})")
        kinds = [e["kind"] for e in res.events]
        check("account_created" in kinds and "verification" in kinds, f"events logged ({kinds})")
        _nowhere(check, "sign-up", printed, json.dumps(res.__dict__, default=str),
                 json.dumps(pauser.asked), json.dumps(pauser.log))
    check.done()


def test_a_saved_password_signs_in_with_no_pause():
    check = Checks()
    with mock_page(BLANK, _routes(_site())) as page:
        page.goto(f"{HOST}/apply")
        page.evaluate(f"sessionStorage.setItem('email', '{EMAIL}'); sessionStorage.setItem('pw', 'Saved-1234567890!'); sessionStorage.setItem('saved', '1')")
        page.reload()
        pauser = _Pauser([])
        res, printed = _run(page, pages.SIGN_IN, _Profile(), pauser)
        check(res.status == accounts.CONTINUE and page.url.endswith("/form") and not pauser.asked,
              f"signed in, no pause ({res.status}, {res.reason}, {page.url}, {pauser.asked})")
        check(page.evaluate("window.__clicks") == ["Sign In"], "only Sign In pressed")
        check("Saved-1234567890!" not in printed + json.dumps(res.__dict__, default=str),
              "the saved password was never read or printed")
    check.done()


def test_no_account_yet_goes_through_create_account():
    check = Checks()
    with mock_page(BLANK, _routes(_site())) as page:
        page.goto(f"{HOST}/apply")
        pauser = _Pauser([CODE])
        res, printed = _run(page, pages.SIGN_IN, _Profile(), pauser)
        check(res.status == accounts.CONTINUE and page.url.endswith("/form"),
              f"Sign In failed, Create account, sign-up, code, form ({res.status}, {res.reason}, {page.url})")
        clicks = page.evaluate("window.__clicks")
        check(clicks == ["Sign In", "Create account", "Create Account", "Verify"],
              f"the presses, in order ({clicks})")
        _nowhere(check, "create-account route", printed, json.dumps(res.__dict__, default=str))
    check.done()


def test_an_existing_account_goes_through_forgot_password_and_the_pasted_password():
    check = Checks()
    with mock_page(BLANK, _routes(_site(create_link=False))) as page:
        page.goto(f"{HOST}/apply")
        # The account exists with a password Chrome never saved; Jack will
        # reset it to PASTED and paste that.
        page.evaluate(f"sessionStorage.setItem('email', '{EMAIL}'); sessionStorage.setItem('pw', '{PASTED}')")
        pauser = _Pauser([PASTED])
        res, printed = _run(page, pages.SIGN_IN, _Profile(), pauser)
        check(res.status == accounts.CONTINUE and page.url.endswith("/form"),
              f"reset requested, password pasted, signed in ({res.status}, {res.reason}, {page.url})")
        check(_session(page, "reset_for") == EMAIL, "the reset was requested for the profile email")
        check(pauser.asked and pauser.asked[0][1] is True and "reset" in pauser.asked[0][0].lower(),
              "the pause asked for the password, hidden")
        clicks = page.evaluate("window.__clicks")
        check(clicks == ["Sign In", "Forgot password?", "Send reset link", "Sign In"],
              f"the presses, in order ({clicks})")
        check(_session(page, "signin_tries") == "2", "the pasted password was typed once, on one sign-in")
        _nowhere(check, "forgot-password route", printed, json.dumps(res.__dict__, default=str),
                 json.dumps(pauser.asked), json.dumps(pauser.log))
    check.done()


def test_an_unanswered_reset_pause_on_a_page_of_no_kind_is_parked():
    # Jack, 2026-10-20: the "sent" page after a reset request has no
    # password box, so it reads as no kind; an unanswered pause there is
    # not Jack signing in.
    check = Checks()
    with mock_page(BLANK, _routes(_site(create_link=False))) as page:
        page.goto(f"{HOST}/apply")
        pauser = _Pauser([None])
        res, printed = _run(page, pages.SIGN_IN, _Profile(), pauser)
        check(page.url.endswith("/sent") and pages.page_kind(page) is None,
              f"the reset was requested and the page is the 'sent' one, of no kind ({page.url})")
        check(res.status == accounts.PARKED and "no password pasted" in res.reason,
              f"parked, not read as signed in ({res.status}, {res.reason})")
        check(not any(e.get("detail") == "signed in by Jack" for e in res.events),
              f"no event says Jack signed in ({res.events})")
    check.done()


def test_no_code_in_time_is_parked_and_submit_is_never_pressed():
    check = Checks()
    with mock_page(BLANK, _routes(_site())) as page:
        page.goto(f"{HOST}/verify")
        res, printed = _run(page, pages.VERIFICATION, _Profile(), _Pauser([None]))
        check(res.status == accounts.PARKED and "verification" in res.reason and "timed_out" in res.reason,
              f"no code: parked with the reason ({res.status}, {res.reason})")
        page.goto(f"{HOST}/wall")
        check(pages.page_kind(page) == pages.SIGN_IN, "the wall reads as a sign-in page")
        res, printed = _run(page, pages.SIGN_IN, _Profile(), _Pauser([None]))
        check(res.status == accounts.PARKED and page.evaluate("window.__clicks") == [],
              f"a wall whose only button reads Submit application: nothing pressed, parked "
              f"({res.status}, {res.reason})")
    check.done()


def test_bare_submit_next_and_continue_are_never_pressed_by_accounts():
    # BLOCK fix: a wall whose only buttons read Submit / Continue / Next gets
    # no press at all (those words belong to the wizard and submit.py), and
    # a Forgot link leading to an ordinary form presses nothing there.
    check = Checks()
    with mock_page(BLANK, _routes(_site())) as page:
        page.goto(f"{HOST}/bare")
        check(pages.page_kind(page) == pages.SIGN_IN, "the bare wall reads as a sign-in page")
        res, printed = _run(page, pages.SIGN_IN, _Profile(), _Pauser([None]))
        check(res.status == accounts.PARKED and page.evaluate("window.__clicks") == [],
              f"Submit, Continue and Next: none pressed; parked ({res.status}, {res.reason})")
        for word in ("Submit", "Continue", "Next", " submit ", "Proceed", "Done"):
            check(bool(accounts.NEVER_WORDS.search(word)), f"'{word.strip()}' is never a button for accounts")
        for rx in (accounts.SIGN_IN_WORDS, accounts.CREATE_WORDS, accounts.RESET_SEND_WORDS,
                   accounts.VERIFY_WORDS):
            check(not any(rx.match(w) for w in ("Submit", "Continue", "Next", "Go", "Proceed", "Enter")),
                  f"no step list contains a wizard word ({rx.pattern[:30]}...)")
        page.goto(f"{HOST}/apply-forgot-form")
        page.evaluate("sessionStorage.setItem('clicks', '[]')")
        page.reload()
        res, printed = _run(page, pages.SIGN_IN, _Profile(), _Pauser([PASTED]))
        clicks = page.evaluate("window.__clicks")
        check(res.status == accounts.PARKED and "reset page" in res.reason
              and clicks == ["Sign In", "Forgot password?"],
              f"a Forgot link that leads to an ordinary form: nothing pressed there ({res.reason}, {clicks})")
        check(page.evaluate("document.getElementById('city').value") == "",
              "  ...and nothing typed into the form's other box")
    check.done()


def test_a_password_box_shorter_than_the_policy_parks():
    check = Checks()
    with mock_page(BLANK, _routes(_site())) as page:
        page.goto(f"{HOST}/short")
        res, printed = _run(page, pages.ACCOUNT, _Profile(), _Pauser([CODE]))
        check(res.status == accounts.PARKED and "16 characters" in res.reason
              and page.evaluate("window.__clicks") == [],
              f"maxlength 16 is below the 20-character policy: parked, nothing pressed ({res.reason})")
        check(json.loads(_session(page, "created") or "{}") == {}, "nothing was typed into the form")
        _nowhere(check, "short box", printed, json.dumps(res.__dict__, default=str))
    check.done()


def test_the_generator_policy():
    check = Checks()
    seen = {accounts.generate_password() for _ in range(5)}
    pw = next(iter(seen))
    check(len(seen) == 5 and len(pw) == accounts.PASSWORD_LENGTH, "24 characters, never the same twice")
    for name, cls in (("upper", str.isupper), ("lower", str.islower), ("digit", str.isdigit)):
        check(any(cls(c) for c in pw), f"has a {name} case character")
    check(any(c in "!@#$%^&*-_=+?" for c in pw), "has a symbol")
    check(len(accounts.generate_password(22)) == 22 and len(accounts.generate_password(4)) == 20
          and accounts.PASSWORD_MIN == 20, "a maxlength shortens it, never below the owner's 20")
    check.done()


if __name__ == "__main__":
    run_as_script(globals())
