"""
test_always_tick_refusals.py — "Always tick this exact wording" refuses the wordings Jack named (2026-11-03).

WHAT THIS DOES: Runs approve.allowlist_refusal over an authorize, release,
background / reference / drug / credit check, arbitration, at-will,
e-signature / signing, contact / SMS / marketing / newsletter and
non-compete wording (each refused, each with the reason) and over a plain
handbook acceptance (allowed); posts each refused wording straight at the
server route /memory/always-tick with an offer planted in the store, and
shows the route answers ok: false, writes nothing to profile.yaml and
keeps the offer; shows corrections.offer_ticks never offers one of them.
WHY IT MATTERS: A consent_allowlist line is ticked on every portal that
shows it. Jack's click must not be able to allowlist an "I authorize..."
or "I release..." box (2026-11-02 report, decision 5; his go 2026-11-03),
and the refusal has to live in the route, since the button is only a
convenience.

Run from anywhere:  .venv/Scripts/python.exe tests/test_always_tick_refusals.py
"""
import hashlib

from _fill_run import PROFILE
from _harness import TEST_HOME, Checks, run_as_script
from agent import approve, corrections, gui, memory
from agent.profile import Profile
from test_gui import _req

REFUSED = [
    ("authorize", "I authorize Example Co to verify the information in this application"),
    ("release", "I release Example Co and its agents from any liability"),
    ("background check", "I understand that a background check will be conducted"),
    ("reference check", "I agree that reference checks may be carried out"),
    ("drug check", "I agree to a pre-employment drug screen"),
    ("credit check", "I consent to a credit check as part of this application"),
    ("arbitration", "I agree to resolve any dispute by binding arbitration"),
    ("at-will", "I understand that my employment would be at-will"),
    ("e-signature", "I agree that ticking this box is my e-signature"),
    ("signing", "By signing below I accept the above"),
    ("contact", "You may contact my current employer"),
    ("SMS", "I agree to receive SMS messages about my application"),
    ("marketing", "I agree to receive marketing emails"),
    ("newsletter", "Sign me up for the newsletter"),
    ("non-compete", "I accept the non-compete clause"),
]
# Plain forms of the same wordings that 5da85c0's patterns missed (its
# review's BLOCK), each refused by ALLOWLIST_FORBIDDEN itself.
VARIANTS = [
    ("'non compete' with a space", "I accept the non compete clause"),
    ("'non‑compete' with a Unicode hyphen", "I accept the non‑compete clause"),
    ("'at‐will' with a Unicode hyphen", "I understand that my employment is at‐will"),
    ("'at will' with a space", "I understand that my employment is at will"),
    ("a word between drug and testing", "I agree to submit to drug and alcohol testing"),
    ("a credit report", "I consent to Example Co obtaining my credit report"),
    ("a consumer report", "I consent to a consumer report being obtained"),
    ("a credit history", "I consent to a review of my credit history"),
    ("references checked, passive", "I understand that my references may be checked"),
    ("references verified", "Example Co may verify my references"),
    ("background alone", "I agree to a background investigation"),
    ("signatures", "Our signatures below confirm the above"),
    ("undersigned", "I, the undersigned, accept the above"),
    ("an invisible character inside authorize", "I auth­orize Example Co to verify my details"),
    # Second fix (d9c5b10's review): forms the first fix missed, and the
    # three credit alternatives it dropped (restored: refused on 0698a98,
    # not on d9c5b10 / b4623af); the employment / education / medical rows
    # are guards (refused throughout).
    ("a credit investigation (restored)", "I consent to a credit investigation"),
    ("a credit verification (restored)", "I consent to credit verification"),
    ("a credit screen (restored)", "I consent to a credit screen"),
    ("an employment verification (guard)", "I consent to employment verification"),
    ("an education verification (guard)", "I consent to education verification"),
    ("a medical test (guard)", "I agree to a medical test"),
    ("a credit inquiry", "I consent to a credit inquiry"),
    ("'not to compete'", "I agree not to compete with Example Co"),
    ("'a covenant not to compete'", "I accept a covenant not to compete"),
    ("'call or text me'", "Example Co may call or text me"),
    ("'receive texts'", "I agree to receive texts"),
    ("referees", "Example Co may approach my referees"),
    ("permission to obtain records", "I give Example Co permission to obtain my records"),
    ("permit to verify employment history", "I permit Example Co to verify my employment history"),
    ("verify my identity", "Example Co may verify my identity"),
]
FORBIDDEN_REASON = "an authorize / release / check / arbitration / at-will / signing / contact / marketing / non-compete wording"
ALLOWED = "I accept the employee handbook"


def test_each_named_wording_is_refused_with_its_reason_and_a_plain_acceptance_is_not():
    # On 16b6ac3 (before 5da85c0) allowlist_refusal returned None for
    # authorize, release, reference check, credit check, arbitration,
    # at-will, e-signature, signing and "You may contact"; background
    # check and drug screen were already refused as criminal-record stops,
    # non-compete as a per-company stop, SMS / marketing / newsletter as
    # marketing. The VARIANTS each returned None on 5da85c0 (its review's
    # BLOCK) and are refused by ALLOWLIST_FORBIDDEN's own reason.
    check = Checks()
    for what, label in REFUSED:
        why = approve.allowlist_refusal(label)
        check(why is not None, f"{what}: refused ({why})")
    for what, label in VARIANTS:
        why = approve.allowlist_refusal(label)
        check(why == FORBIDDEN_REASON, f"{what}: refused by the forbidden-wording check ({why})")
    check(approve.allowlist_refusal(ALLOWED) is None, "a plain handbook acceptance is still allowed")
    check(approve.allowlist_refusal("I agree to the Terms of Service and Privacy Policy") is None,
          "a terms / privacy wording is still allowed (the policy ticks it anyway)")
    check.done()


def _state(name):
    state = gui.State()
    state.memory_path = TEST_HOME / f"always-tick-{name}.yaml"
    state.memory_path.unlink(missing_ok=True)
    state.profile_path = TEST_HOME / f"always-tick-{name}-profile.yaml"
    state.profile_path.write_text(PROFILE, encoding="utf-8")
    return state


def _plant(store, label):
    """An offer put straight into the store, past add_offer's own refusal: what an older store or a hand edit could hold."""
    offer = {"id": hashlib.sha1(label.casefold().encode()).hexdigest()[:12], "label": label,
             "host": "careers.example-co.com", "posting": "https://careers.example-co.com/apply", "at": "2026-11-03T00:00:00"}
    store.offers.append(offer)
    store.save()
    return offer["id"]


def test_the_server_route_refuses_each_wording_and_writes_nothing():
    # Fails on 5ba4a07: the route wrote the authorize / release / ... lines
    # (allowlist_refusal let them through).
    check = Checks()
    state = _state("route")
    store = memory.Learned.load(state.memory_path)
    ids = {what: _plant(store, label) for what, label in REFUSED + VARIANTS}
    ok_id = _plant(store, ALLOWED)
    server = gui.Server(state).start()
    try:
        origin = f"http://127.0.0.1:{server.port}"
        for what, _label in REFUSED + VARIANTS:
            status, _, body = _req(server, "POST", "/memory/always-tick", {"id": ids[what]}, origin=origin)
            check(status == 200 and body.get("ok") is False and "never allowlisted" in str(body.get("error")),
                  f"{what}: the route refuses ({body})")
        check(Profile.load(state.profile_path).consent_allowlist() == [], "nothing written to consent_allowlist")
        check(not approve.backups_of(state.profile_path), "no backup taken: the file was never touched")
        kept = {o["id"] for o in memory.Learned.load(state.memory_path).offers}
        check(all(ids[w] in kept for w, _ in REFUSED + VARIANTS), "a refused offer stays listed (nothing was written for it)")
        status, _, body = _req(server, "POST", "/memory/always-tick", {"id": ok_id}, origin=origin)
        check(status == 200 and body.get("ok") is True and Profile.load(state.profile_path).consent_allowlist() == [ALLOWED],
              f"the plain acceptance still goes through the same route ({body})")
    finally:
        server.stop()
    check.done()


def test_offer_ticks_never_offers_one_of_them():
    # Fails on 5ba4a07 for the wordings allowlist_refusal let through.
    check = Checks()
    state = _state("offers")
    store = memory.Learned.load(state.memory_path)

    def change(label):
        return {"field": {"ref": "x", "type": "checkbox", "label": label, "explicit_label": label},
                "kind": "filled_blank", "agent": None, "now": True, "decision": {}, "category": "consent"}

    offered = corrections.offer_ticks([change(l) for _, l in REFUSED + VARIANTS] + [change(ALLOWED)],
                                      url="https://careers.example-co.com/apply", log=[], store=store)
    check([o["label"] for o in offered] == [ALLOWED], f"only the plain acceptance is offered ({[o['label'] for o in offered]})")
    check.done()


if __name__ == "__main__":
    run_as_script(globals())
