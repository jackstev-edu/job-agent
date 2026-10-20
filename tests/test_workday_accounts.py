"""
test_workday_accounts.py — Workday's account pages, by automation id, through the overlay.

WHAT THIS DOES: Serves a mock of Workday's apply flow built from its
structure (data-automation-id on every control; a click_filter overlay
over the Sign In and Create Account buttons that takes the click and
submits; a verification page after sign-up) and drives agent.accounts:
on the sign-in page the email is typed, Sign In is pressed through the
overlay, and with no account the Create Account link leads to the form,
where the email, a generated password (twice) and the consent box are
filled and Create Account is pressed through the overlay; the code from
the pause is typed and the application page follows. Pressing the button
itself does nothing on this mock, so only the overlay path can pass. The
control listing names only known ids, and nothing is pressed by id off
the page kind its step is for.
WHY IT MATTERS: On Jack's first GUI run two Workday tenants parked at
"no single Create account button to press": the text-matched button was
found but the overlay took the click (Workday's own click path).

Run from anywhere:  .venv/Scripts/python.exe tests/test_workday_accounts.py
"""
import json

from _harness import Checks, mock_page, run_as_script
from agent import accounts, pages
from test_accounts import BLANK, CODE, EMAIL, GENERATED, PASTED, _Pauser, _Profile, _nowhere, _run

HOST = "https://acme.wd5.myworkdayjobs.com"

STATE_JS = """<script>
  window.__clicks = JSON.parse(sessionStorage.getItem('clicks') || '[]');
  document.addEventListener('click', e => {
    const b = e.target.closest('[data-automation-id], button, a');
    if (b) { window.__clicks.push(b.getAttribute('data-automation-id') || (b.innerText || '').trim());
             sessionStorage.setItem('clicks', JSON.stringify(window.__clicks)); }
  }, true);
</script>
<style>.wrap { position: relative; display: inline-block; }
  [data-automation-id=click_filter] { position: absolute; inset: 0; z-index: 2; }</style>"""

SIGN_IN = STATE_JS + """<h1>Sign In</h1><div data-automation-id="signInContent"><form id="f">
<label for="input-1">Email Address</label><input id="input-1" type="text" data-automation-id="email">
<label for="input-2">Password</label><input id="input-2" type="password" data-automation-id="password">
<div class="wrap"><button type="button" data-automation-id="signInSubmitButton" onclick="return false">Sign In</button>
<div data-automation-id="click_filter" onclick="signIn()"></div></div></form>
<button type="button" data-automation-id="forgotPasswordLink" onclick="location.href='/forgot'">Forgot Password?</button>
<p>Don't have an account? <button type="button" data-automation-id="createAccountLink"
   onclick="location.href='/create'">Create Account</button></p></div>
<script>function signIn() {
  const ok = document.getElementById('input-2').value && document.getElementById('input-2').value === sessionStorage.getItem('pw')
          && document.getElementById('input-1').value === sessionStorage.getItem('email');
  sessionStorage.setItem('signin_tries', String(1 + Number(sessionStorage.getItem('signin_tries') || 0)));
  if (ok) location.href = '/apply-form'; else document.getElementById('f').insertAdjacentHTML('beforeend', '<p>Wrong</p>'); }
</script>"""

CREATE = STATE_JS + """<h1>Create Account</h1><div data-automation-id="createAccountContent"><form id="f">
<label for="input-3">Email Address</label><input id="input-3" type="text" data-automation-id="email">
<label for="input-4">Password</label><input id="input-4" type="password" data-automation-id="password">
<label for="input-5">Verify New Password</label><input id="input-5" type="password" data-automation-id="verifyPassword">
<input type="checkbox" id="input-6" data-automation-id="createAccountCheckbox" required>
<label for="input-6">I have read and consent to the Privacy Statement and the Terms of Use</label>
<div class="wrap"><button type="button" data-automation-id="createAccountSubmitButton" onclick="return false">Create Account</button>
<div data-automation-id="click_filter" onclick="create()"></div></div></form>
<p>Already have an account? <button type="button" data-automation-id="signInLink" onclick="location.href='/apply'">Sign In</button></p></div>
<script>function create() {
  const p1 = document.getElementById('input-4').value, p2 = document.getElementById('input-5').value;
  const rec = {email: document.getElementById('input-3').value, p1, p2, tos: document.getElementById('input-6').checked};
  sessionStorage.setItem('created', JSON.stringify(rec));
  if (!rec.tos || p1 !== p2 || p1.length < 12) { document.getElementById('f').insertAdjacentHTML('beforeend', '<p>Fix</p>'); return; }
  sessionStorage.setItem('email', rec.email); sessionStorage.setItem('pw', p1); location.href = '/verify'; }
</script>"""

VERIFY = STATE_JS + """<h1>Verify Your Email</h1><p>Enter the verification code we sent.</p>
<form id="f"><label for="input-7">Verification Code</label><input id="input-7" data-automation-id="verificationCode" name="verificationCode">
<button type="button" data-automation-id="verifyButton" onclick="verify()">Verify</button></form>
<script>function verify() { if (document.getElementById('input-7').value === 'CODE') location.href = '/apply-form'; }</script>""".replace("CODE", CODE)

FORGOT = STATE_JS + """<h1>Reset your password</h1><form id="f"><label for="input-8">Email Address</label>
<input id="input-8" type="text" data-automation-id="email"><button type="button" onclick="sent()">Send reset link</button></form>
<script>function sent() { sessionStorage.setItem('reset_for', document.getElementById('input-8').value); location.href = '/sent'; }</script>"""
SENT = STATE_JS + "<h1>Check your email</h1><p>We sent a link.</p><a href='/apply'>Back to sign in</a>"
# A create-account page whose submit reads like Submit application, and one
# whose overlay covers the whole form (two controls under it).
CREATE_SUBMIT_TEXT = CREATE.replace('>Create Account</button>', '>Submit application</button>')
CREATE_WIDE = CREATE.replace('<div class="wrap"><button type="button" data-automation-id="createAccountSubmitButton" onclick="return false">Create Account</button>\n<div data-automation-id="click_filter" onclick="create()"></div></div>',
                             '<div class="wrap" style="display:block"><button type="button" data-automation-id="deleteAccountButton" onclick="return false" style="display:block;width:200px;height:120px">Delete account</button>'
                             '<button type="button" data-automation-id="createAccountSubmitButton" onclick="return false" style="display:block;width:200px;height:40px">Create Account</button>'
                             '<div data-automation-id="click_filter" onclick="wide(event)"></div></div>'
                             '<script>function wide(e) { const under = document.elementsFromPoint(e.clientX, e.clientY).find(x => x.tagName === \'BUTTON\');'
                             ' sessionStorage.setItem(\'under\', under ? under.getAttribute(\'data-automation-id\') : \'none\');'
                             ' if (under && under.getAttribute(\'data-automation-id\') === \'createAccountSubmitButton\') create(); }</script>')
assert "wide(event)" in CREATE_WIDE
APPLY_FORM = STATE_JS + """<h1>My Information</h1><form><label for="fn">First Name</label><input id="fn" name="firstName">
<label for="ln">Last Name</label><input id="ln" name="lastName">
<button type="button" data-automation-id="bottom-navigation-next-button">Save and Continue</button></form>"""


def _site():
    html = lambda body: f"<!doctype html><html><head><meta charset='utf-8'></head><body>{body}</body></html>"
    return {"/apply": html(SIGN_IN), "/create": html(CREATE), "/verify": html(VERIFY), "/apply-form": html(APPLY_FORM),
            "/forgot": html(FORGOT), "/sent": html(SENT), "/create-submit-text": html(CREATE_SUBMIT_TEXT),
            "/create-wide": html(CREATE_WIDE)}


def _routes(site):
    def handle(route):
        path = route.request.url.split("myworkdayjobs.com", 1)[-1].split("?")[0]
        route.fulfill(status=200, content_type="text/html", body=site.get(path, "<p>404</p>"))
    return {f"{HOST}/**": handle}


def _session(page, key):
    return page.evaluate(f"sessionStorage.getItem('{key}')")


def test_workdays_controls_are_listed_by_known_id_only():
    check = Checks()
    with mock_page(BLANK, _routes(_site())) as page:
        page.goto(f"{HOST}/apply")
        page.evaluate("document.body.insertAdjacentHTML('beforeend', '<div data-automation-id=\"evil<script>\">x</div>')")
        check(accounts.workday_controls(page) == {"email", "password", "sign_in", "create_link", "forgot_link", "overlay"},
              f"the sign-in page's known controls, nothing else ({accounts.workday_controls(page)})")
        page.goto(f"{HOST}/create")
        check(accounts.workday_controls(page) == {"email", "password", "verify", "consent", "create", "sign_in_link", "overlay"},
              f"the create-account page's known controls ({accounts.workday_controls(page)})")
        page.goto(f"{HOST}/apply-form")
        check(accounts.workday_controls(page) == set(), "an application page carries none of them")
    check.done()


def test_no_account_yet_creates_one_through_the_overlay_and_answers_the_code():
    check = Checks()
    with mock_page(BLANK, _routes(_site())) as page:
        page.goto(f"{HOST}/apply")
        check(pages.page_kind(page) == pages.SIGN_IN, "Workday's sign-in page reads as a sign-in page")
        pauser = _Pauser([CODE])
        res, printed = _run(page, pages.SIGN_IN, _Profile(), pauser)
        check(res.status == accounts.CONTINUE and page.url.endswith("/apply-form"),
              f"sign in failed, Create Account, the form, the code, the application ({res.status}, {res.reason}, {page.url})")
        clicks = page.evaluate("window.__clicks")
        presses = [c for c in clicks if c in ("click_filter", "createAccountLink", "verifyButton",
                                              "signInSubmitButton", "createAccountSubmitButton", "forgotPasswordLink")]
        check(presses == ["click_filter", "createAccountLink", "click_filter", "verifyButton"],
              f"both submits went through the overlay, the link by its id, the buttons themselves never ({clicks})")
        created = json.loads(_session(page, "created") or "{}")
        check(created.get("email") == EMAIL and created.get("p1") == GENERATED and created.get("p2") == GENERATED
              and created.get("tos") is True, f"the form got the email, the generated password twice and the consent tick")
        check("pressed 'Create Account (Workday)'" in res.steps and "pressed 'Sign In (Workday)'" in res.steps,
              f"the steps name the Workday presses ({res.steps})")
        _nowhere(check, "workday sign-up", printed, json.dumps(res.__dict__, default=str),
                 json.dumps(pauser.asked), json.dumps(pauser.log))
    check.done()


def test_a_saved_password_signs_in_through_the_overlay():
    check = Checks()
    with mock_page(BLANK, _routes(_site())) as page:
        page.goto(f"{HOST}/apply")
        page.evaluate(f"sessionStorage.setItem('email', '{EMAIL}'); sessionStorage.setItem('pw', 'Saved-1234567890!')")
        page.reload()
        page.evaluate("document.getElementById('input-2').value = sessionStorage.getItem('pw')")   # Chrome's stand-in
        pauser = _Pauser([])
        res, printed = _run(page, pages.SIGN_IN, _Profile(), pauser)
        check(res.status == accounts.CONTINUE and page.url.endswith("/apply-form") and not pauser.asked,
              f"signed in, no pause ({res.status}, {res.reason}, {page.url})")
        clicks = page.evaluate("window.__clicks")
        check([c for c in clicks if c not in ("email", "password")] == ["click_filter"],
              f"the only press was the overlay over Sign In ({clicks})")
        check("Saved-1234567890!" not in printed + json.dumps(res.__dict__, default=str), "the saved password was never read")
        _nowhere(check, "workday sign-in", printed, json.dumps(res.__dict__, default=str))
    check.done()


def test_the_button_itself_is_never_the_click_and_nothing_is_pressed_off_its_page_kind():
    check = Checks()
    with mock_page(BLANK, _routes(_site())) as page:
        page.goto(f"{HOST}/create")
        # The create submit is pressed only on an account page: here the page
        # is one, but the key must match the step.
        check(accounts._press_workday(page, "create", pages.SIGN_IN) is False and page.evaluate("window.__clicks") == [],
              "the create submit is not pressed when the step is for a sign-in page")
        check(accounts._press_workday(page, "overlay", pages.ACCOUNT) is False
              and accounts._press_workday(page, "email", pages.ACCOUNT) is False,
              "only the four links / submits are pressable by id")
        page.goto(f"{HOST}/apply")
        # Without the overlay, the control itself is the click target.
        page.evaluate("document.querySelector('[data-automation-id=click_filter]').remove();"
                      "document.querySelector('[data-automation-id=signInSubmitButton]').onclick = () => signIn()")
        check(accounts._press_workday(page, "sign_in", pages.SIGN_IN) is True
              and page.evaluate("window.__clicks") == ["signInSubmitButton"], "with no overlay the button is clicked")
    check.done()


def test_an_existing_account_goes_through_workdays_forgot_link():
    check = Checks()
    with mock_page(BLANK, _routes(_site())) as page:
        page.goto(f"{HOST}/apply")
        page.evaluate(f"sessionStorage.setItem('email', '{EMAIL}'); sessionStorage.setItem('pw', '{PASTED}')")
        page.reload()
        # The sign-in page has no Create Account link on this tenant: remove it.
        page.evaluate("document.querySelector('[data-automation-id=createAccountLink]').remove()")
        pauser = _Pauser([PASTED])
        res, printed = _run(page, pages.SIGN_IN, _Profile(), pauser)
        check(res.status == accounts.CONTINUE and page.url.endswith("/apply-form"),
              f"Sign In through the overlay, Forgot Password by id, the reset, the pasted password, signed in "
              f"({res.status}, {res.reason}, {page.url})")
        check(_session(page, "reset_for") == EMAIL and "pressed 'Forgot Password (Workday)'" in res.steps,
              f"the Forgot link was pressed by its id and the reset requested for the profile email ({res.steps})")
        _nowhere(check, "workday forgot", printed, json.dumps(res.__dict__, default=str),
                 json.dumps(pauser.asked), json.dumps(pauser.log))
    check.done()


def test_the_id_press_refuses_duplicates_submit_wording_google_and_lands_on_the_control():
    check = Checks()
    check(accounts.WORKDAY_PRESSABLE == {"create", "sign_in", "create_link", "forgot_link"},
          "exactly the two submits and the two links are pressable by id")
    with mock_page(BLANK, _routes(_site())) as page:
        page.goto(f"{HOST}/create")
        page.evaluate("document.querySelector('form').insertAdjacentHTML('beforeend', "
                      "'<button type=\"button\" data-automation-id=\"createAccountSubmitButton\">Create Account</button>')")
        check(accounts._press_workday(page, "create", pages.ACCOUNT) is False and page.evaluate("window.__clicks") == [],
              "two visible controls with the id: nothing pressed")
        page.goto(f"{HOST}/create-submit-text")
        check(accounts._press_workday(page, "create", pages.ACCOUNT) is False and page.evaluate("window.__clicks") == [],
              "a control with the id that reads 'Submit application' is never pressed")
        page.goto(f"{HOST}/create")
        # An overlay that doesn't cover the control's centre is not its overlay: the control is clicked.
        page.evaluate("const o = document.querySelector('[data-automation-id=click_filter]'); o.style.inset = 'auto'; "
                      "o.style.left = '-500px'; o.style.width = '10px'; o.style.height = '10px';"
                      "document.querySelector('[data-automation-id=createAccountSubmitButton]').onclick = () => create()")
        page.evaluate("document.getElementById('input-6').checked = true; document.getElementById('input-4').value = 'Abcdefghijkl1!'; document.getElementById('input-5').value = 'Abcdefghijkl1!'")
        check(accounts._press_workday(page, "create", pages.ACCOUNT) is True
              and page.evaluate("window.__clicks") == ["createAccountSubmitButton"], "an overlay elsewhere: the control itself is clicked")
        page.goto(f"{HOST}/create-wide")
        page.evaluate("document.getElementById('input-6').checked = true; document.getElementById('input-4').value = 'Abcdefghijkl1!'; document.getElementById('input-5').value = 'Abcdefghijkl1!'")
        ok = accounts._press_workday(page, "create", pages.ACCOUNT)
        check(ok is True and _session(page, "under") == "createAccountSubmitButton"
              and _session(page, "under") != "deleteAccountButton",
              f"under a form-wide overlay (its centre on Delete) the click lands on the control's own point ({ok}, {_session(page, 'under')})")
    check.done()


def test_a_password_box_carrying_a_pressable_id_is_never_read_or_pressed():
    # BLOCK fix: the wording check read any input's value; a text or password
    # box with the id is now neither read nor pressed.
    check = Checks()
    with mock_page(BLANK, _routes(_site())) as page:
        page.goto(f"{HOST}/create")
        # Put the pressable id on the password box and instrument its value getter.
        page.evaluate("""() => {
          const pw = document.getElementById('input-4');
          document.querySelector('[data-automation-id=createAccountSubmitButton]').removeAttribute('data-automation-id');
          pw.setAttribute('data-automation-id', 'createAccountSubmitButton');
          window.__valueReads = 0;
          const proto = Object.getPrototypeOf(pw);
          const desc = Object.getOwnPropertyDescriptor(proto, 'value');
          Object.defineProperty(pw, 'value', {get() { window.__valueReads++; return desc.get.call(this); },
                                              set(v) { desc.set.call(this, v); }, configurable: true});
        }""")
        check(accounts._press_workday(page, "create", pages.ACCOUNT) is False,
              "a password box with the id is not pressed")
        check(page.evaluate("window.__valueReads") == 0 and page.evaluate("window.__clicks") == [],
              f"its value was never read and nothing was clicked ({page.evaluate('window.__valueReads')})")
        # A submit input with the id is pressable and its value is its wording.
        page.goto(f"{HOST}/create")
        page.evaluate("""() => {
          const b = document.querySelector('[data-automation-id=createAccountSubmitButton]');
          const i = document.createElement('input'); i.type = 'submit'; i.value = 'Submit application';
          i.setAttribute('data-automation-id', 'createAccountSubmitButton'); b.replaceWith(i); }""")
        check(accounts._press_workday(page, "create", pages.ACCOUNT) is False and page.evaluate("window.__clicks") == [],
              "a submit input reading 'Submit application' is refused on its wording")
    check.done()


def test_nothing_is_pressed_by_id_on_googles_host():
    check = Checks()
    html = f"<!doctype html><html><head><meta charset='utf-8'></head><body>{SIGN_IN}</body></html>"
    with mock_page(BLANK, {"https://accounts.google.com/**": lambda route: route.fulfill(content_type="text/html", body=html)}) as page:
        page.goto("https://accounts.google.com/signin")
        check(pages.page_kind(page) == pages.SIGN_IN, "the page reads as a sign-in page (so the refusal below is the host's)")
        check(accounts._press_workday(page, "sign_in", pages.SIGN_IN) is False
              and accounts._press_workday(page, "create_link", pages.SIGN_IN) is False
              and page.evaluate("window.__clicks") == [], "on Google's host nothing is pressed by id")
    check.done()


if __name__ == "__main__":
    run_as_script(globals())
