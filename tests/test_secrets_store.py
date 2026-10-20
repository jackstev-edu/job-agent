"""
test_secrets_store.py — a stored portal password goes to the keyring and nowhere else.

WHAT THIS DOES: On the in-memory keyring tests/_harness.py installs (the
real Windows Credential Manager is never touched: checked here), drives
the account flows of test_accounts.py's mock portal: a sign-up stores the
generated password under the portal's host; the next sign-in types the
stored one once and pauses for nothing; a stored password that fails is
forgotten, not retried, and the forgot-password flow stores the pasted
one; `apply.py passwords` prints hosts only. Every sentinel is then shown
to be in no file under the test home, no tracker row, no gate_runs row,
no step, event, printed line or pause text, and no model input (the
Anthropic client is forbidden for the whole run).
WHY IT MATTERS: Jack's decision of 2026-10-20: a password may live in
Credential Manager and nowhere else, and is never read from a page.

Run from anywhere:  .venv/Scripts/python.exe tests/test_secrets_store.py
"""
import io
import json
from argparse import Namespace
from contextlib import redirect_stdout

from unittest import mock

import keyring

from _harness import FAKE_KEYRING, Checks, mock_page, run_as_script
from agent import accounts, pages, secrets_store
from test_accounts import (BLANK, EMAIL, GENERATED, HOST, PASTED, _nowhere, _Pauser, _Profile, _routes, _run,
                           _session, _site)

PORTAL_HOST = "careers.example-co.com"
WRONG = "WRONG-STORED-SENTINEL-9Kp!"


def _vault_values() -> str:
    return json.dumps(sorted(FAKE_KEYRING.store.items()))


def test_the_store_keeps_hosts_and_values_apart_and_runs_on_the_fake_keyring():
    try:
        _test_the_store_keeps_hosts_and_values_apart_and_runs_on_the_fake_keyring()
    finally:
        secrets_store.forget_all()   # never leave a sentinel for the next test


def _test_the_store_keeps_hosts_and_values_apart_and_runs_on_the_fake_keyring():
    check = Checks()
    check(keyring.get_keyring() is FAKE_KEYRING, "the tests run on the in-memory keyring, never the real vault")
    secrets_store.forget_all()
    secrets_store.save("Careers.Example-Co.com.", GENERATED)
    secrets_store.save("tenant.myworkdayjobs.com", PASTED)
    check(secrets_store.hosts() == ["careers.example-co.com", "tenant.myworkdayjobs.com"],
          f"hosts are normalised and listed ({secrets_store.hosts()})")
    check(secrets_store.get("careers.example-co.com") == GENERATED and secrets_store.get("nowhere.invalid") is None,
          "get by host")
    index = FAKE_KEYRING.store.get((secrets_store.SERVICE, secrets_store._INDEX), "")
    check(GENERATED not in index and PASTED not in index, "the index entry holds hosts, no values")
    for bad in ("", "https://x.invalid/", "a b", secrets_store._INDEX):
        try:
            secrets_store.save(bad, "x" * 20)
            check(False, f"{bad!r} is refused as a key")
        except ValueError:
            check(True, f"{bad!r} is refused as a key")
    check(secrets_store.forget("careers.example-co.com") is True and secrets_store.forget("careers.example-co.com") is False
          and secrets_store.hosts() == ["tenant.myworkdayjobs.com"], "forget one")
    out = io.StringIO()
    with redirect_stdout(out):
        accounts_cmd = __import__("apply").cmd_passwords
        accounts_cmd(Namespace(forget=None, forget_all=False))
    printed = out.getvalue()
    check("tenant.myworkdayjobs.com" in printed and PASTED not in printed and GENERATED not in printed,
          f"`apply.py passwords` prints the host, never a value ({printed.strip().splitlines()[1:2]})")
    with redirect_stdout(out):
        accounts_cmd(Namespace(forget=None, forget_all=True))
    check(secrets_store.hosts() == [] and secrets_store.get("tenant.myworkdayjobs.com") is None
          and (secrets_store.SERVICE, secrets_store._INDEX) not in FAKE_KEYRING.store, "forget-all empties the vault")
    check.done()


def test_sign_up_stores_the_generated_password_and_the_next_sign_in_types_it_once():
    try:
        _test_sign_up_stores_the_generated_password_and_the_next_sign_in_types_it_once()
    finally:
        secrets_store.forget_all()   # never leave a sentinel for the next test


def _test_sign_up_stores_the_generated_password_and_the_next_sign_in_types_it_once():
    check = Checks()
    secrets_store.forget_all()
    with mock_page(BLANK, _routes(_site())) as page:
        page.goto(f"{HOST}/register")
        pauser = _Pauser(["246810"])
        res, printed = _run(page, pages.ACCOUNT, _Profile(), pauser)
        check(res.status == accounts.CONTINUE and page.url.endswith("/form"), f"created and on the form ({res.status}, {res.reason})")
        check(secrets_store.get(PORTAL_HOST) == GENERATED and secrets_store.hosts() == [PORTAL_HOST],
              f"the generated password is stored under the portal's host ({secrets_store.hosts()})")
        check(any("stored the password for careers.example-co.com" in s for s in res.steps), f"the step says so ({res.steps[-3:]})")
        _nowhere(check, "sign-up with the store", printed, json.dumps(res.__dict__, default=str),
                 json.dumps(pauser.asked), json.dumps(pauser.log))
        # A new sign-in page on the same host, with nothing saved in the browser.
        page.goto(f"{HOST}/apply")
        page.evaluate("sessionStorage.setItem('saved', '0'); sessionStorage.setItem('signin_tries', '0'); sessionStorage.setItem('clicks', '[]')")
        page.reload()
        pauser2 = _Pauser([])
        res2, printed2 = _run(page, pages.SIGN_IN, _Profile(), pauser2)
        check(res2.status == accounts.CONTINUE and page.url.endswith("/form") and not pauser2.asked,
              f"signed in with the stored password, no pause ({res2.status}, {res2.reason}, {page.url})")
        check(page.evaluate("window.__clicks") == ["Sign In"] and _session(page, "signin_tries") == "1",
              f"typed once, one press ({page.evaluate('window.__clicks')}, {_session(page, 'signin_tries')})")
        check(any(e.get("detail") == "signed in with the stored password" for e in res2.events)
              and any("typed the stored password, once" in s for s in res2.steps), f"the event and step ({res2.steps})")
        _nowhere(check, "stored sign-in", printed2, json.dumps(res2.__dict__, default=str), json.dumps(pauser2.asked))
    check(GENERATED not in printed + printed2, "nothing printed holds it")
    check.done()


def test_a_stored_password_that_fails_is_forgotten_not_retried_and_the_pasted_one_is_stored():
    try:
        _test_a_stored_password_that_fails_is_forgotten_not_retried_and_the_pasted_one_is_stored()
    finally:
        secrets_store.forget_all()   # never leave a sentinel for the next test


def _test_a_stored_password_that_fails_is_forgotten_not_retried_and_the_pasted_one_is_stored():
    check = Checks()
    secrets_store.forget_all()
    secrets_store.save(PORTAL_HOST, WRONG)
    with mock_page(BLANK, _routes(_site(create_link=False))) as page:
        page.goto(f"{HOST}/apply")
        page.evaluate(f"sessionStorage.setItem('email', '{EMAIL}'); sessionStorage.setItem('pw', '{PASTED}')")
        pauser = _Pauser([PASTED])
        res, printed = _run(page, pages.SIGN_IN, _Profile(), pauser)
        check(res.status == accounts.CONTINUE and page.url.endswith("/form"),
              f"the wrong stored one failed, reset, pasted, signed in ({res.status}, {res.reason}, {page.url})")
        clicks = page.evaluate("window.__clicks")
        check(clicks == ["Sign In", "Forgot password?", "Send reset link", "Sign In"],
              f"one press with the stored password, then the reset, then one with the pasted one ({clicks})")
        check(_session(page, "signin_tries") == "2", "the stored password was tried once, the pasted once")
        check(any("didn't sign in; forgotten, not retried" in s for s in res.steps), f"the step says it was forgotten ({res.steps})")
        check(secrets_store.get(PORTAL_HOST) == PASTED and secrets_store.hosts() == [PORTAL_HOST],
              "the pasted password replaced it in the store")
        check(WRONG not in printed + json.dumps(res.__dict__, default=str) + json.dumps(pauser.asked) + json.dumps(pauser.log),
              "the wrong stored password is in no step, event, pause or printed line")
        _nowhere(check, "wrong stored then reset", printed, json.dumps(res.__dict__, default=str),
                 json.dumps(pauser.asked), json.dumps(pauser.log))
        # The vault holds the pasted password and nothing says what it is anywhere else.
        check(PASTED in _vault_values() and WRONG not in _vault_values(), "the vault holds only the pasted one")
    check.done()


def test_nothing_is_stored_when_jack_finishes_the_sign_up_by_hand_or_on_googles_host():
    try:
        _test_nothing_is_stored_when_jack_finishes_the_sign_up_by_hand_or_on_googles_host()
    finally:
        secrets_store.forget_all()   # never leave a sentinel for the next test


def _test_nothing_is_stored_when_jack_finishes_the_sign_up_by_hand_or_on_googles_host():
    check = Checks()
    secrets_store.forget_all()
    # The account exists: the portal bounces the sign-up to its sign-in page.
    # The generated password opened nothing and must not replace the stored one.
    secrets_store.save(PORTAL_HOST, PASTED)
    with mock_page(BLANK, _routes(_site(exists=True))) as page:
        page.goto(f"{HOST}/register")
        page.evaluate(f"sessionStorage.setItem('email', '{EMAIL}'); sessionStorage.setItem('exists', '1')")
        res, printed = _run(page, pages.ACCOUNT, _Profile(), _Pauser([]))
        check(page.url.endswith("/apply") and pages.page_kind(page) == pages.SIGN_IN,
              f"the sign-up bounced to the sign-in page ({page.url}, {res.status}, {res.reason})")
        check(secrets_store.get(PORTAL_HOST) == PASTED and GENERATED not in _vault_values(),
              "the stored password is untouched; the generated one was not stored")
        _nowhere(check, "bounced sign-up", printed, json.dumps(res.__dict__, default=str))
    secrets_store.forget_all()
    # Jack finishes the sign-up by hand: the press leaves the page an account
    # page, the pause is answered, and the password in use is not known.
    with mock_page(BLANK, _routes(_site())) as page:
        page.goto(f"{HOST}/register")
        with mock.patch.object(accounts, "_press", lambda page_, words, kind, what: "Create Account"):
            res, printed = _run(page, pages.ACCOUNT, _Profile(), _Pauser([""]))
        check(pages.page_kind(page) == pages.ACCOUNT and page.evaluate("window.__clicks") == [],
              f"nothing was pressed and the page stayed an account page ({page.url})")
        check(secrets_store.hosts() == [] and GENERATED not in _vault_values(),
              f"nothing stored when the agent's press didn't move the page ({res.status}, {res.reason})")
        _nowhere(check, "hand-finished sign-up", printed, json.dumps(res.__dict__, default=str))
    check(accounts._host(type("P", (), {"url": "https://accounts.google.com/signin"})()) is None
          and accounts._host(type("P", (), {"url": "https://Tenant.MyWorkdayJobs.com/en-US/x"})()) == "tenant.myworkdayjobs.com",
          "no key on Google's host; the tenant host otherwise")
    steps: list = []
    accounts._remember(None, GENERATED, steps)
    accounts._remember("x.invalid", None, steps)
    check(secrets_store.hosts() == [] and steps == [], "nothing stored without a host or a password")
    check.done()


if __name__ == "__main__":
    run_as_script(globals())
