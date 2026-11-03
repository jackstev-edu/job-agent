"""
test_attestations.py — decisions D and E (Jack, 2026-11-02) and the Memory page's "Always tick this exact wording".

WHAT THIS DOES: Runs gate.attestation_ok over Jack's positive variants
(ticked) and the negatives (each forbidden clause appended to a positive,
an optional box, a LEGAL_GUARD wording, a mixed terms-and-certify
wording: never); shows the gate plans a REQUIRED attestation box as a
consent tick (trace source "attestation") and stops on an optional one;
in a headless browser, that submit.py's tick helper ticks the required
attestation box and not the optional or mixed ones; decision E: a census
box carrying Jack's review mark passes ticked or unticked as he left it,
and a wizard run where Jack ticks a mixed certification box himself under
the gate pause goes on through Submit after Continue; and the offer: a
box Jack ticked that neither class covers is offered once, his click on
the Memory page writes one quoted consent_allowlist line through
approve.py (never a legal, EEO, pay or marketing wording), Dismiss drops
it.
WHY IT MATTERS: Jack's Workday run (2026-11-02) left "I acknowledge that
the information provided in this application is true and complete." for
him, and after he ticked it the census stopped again. Page text can only
fail these checks; HARD_STOPS is unchanged, and the one certify / attest
/ acknowledge entry is read past only inside the attestation check.

Run from anywhere:  .venv/Scripts/python.exe tests/test_attestations.py
"""
import copy
import io
import json
from contextlib import redirect_stdout
from unittest import mock

import yaml

from _fill_run import PROFILE
from _harness import TEST_HOME, Checks, mock_page, run_as_script
from agent import approve, corrections, extract, gate, gui, memory, sensitive, submit, wizard
from agent.profile import Profile
from test_gate import base, box, run as gate_run
from test_gui import _req

ATT = "I acknowledge that the information provided in this application is true and complete."
POSITIVE = [
    "I certify that the information I have provided is true and complete to the best of my knowledge",
    ATT,
    "I certify that the information provided in this application is accurate, and I understand that "
    "misrepresentation may result in dismissal.",
    "By checking this box, I attest that all statements made in this application are true, correct and complete. "
    "I understand that any false statement or omission may result in rejection of my application, withdrawal of "
    "an offer of employment or termination of employment.",
    "I declare that the information given is truthful and complete.",
    "I understand that a false statement, misrepresentation or omission may lead to the rejection of my "
    "application, the withdrawal of an offer or dismissal.",
]
FORBIDDEN = [
    ("authorize", "I authorize the company to verify it"),
    ("consent to", "I consent to the processing of my personal data"),
    ("agree to", "I agree to the code of conduct"),
    ("background check", "I understand a background check will be performed"),
    ("reference check", "I understand reference checks may be made"),
    ("drug check", "I agree to a drug screen"),
    ("credit check", "I consent to a credit check"),
    ("release of information", "I release the company from liability for the release of information"),
    ("arbitration", "I agree to binding arbitration"),
    ("at-will", "I understand my employment is at-will"),
    ("electronic signature", "This is my electronic signature"),
    ("signing", "By signing below I confirm the above"),
    ("contact", "You may contact my previous employers"),
    ("SMS", "I agree to receive SMS updates"),
    ("marketing", "Send me marketing emails"),
    ("newsletter", "Subscribe me to the newsletter"),
    ("non-compete", "I accept the non-compete clause"),
]
LEGAL = [
    ("LEGAL_GUARD eeo", "I certify that the information is true and I am not a veteran"),
    ("LEGAL_GUARD work authorization", "I certify that the information is true and I am legally authorized to work"),
    ("PROFILE_PATHS pay", "I certify that the information is true and my salary expectation is flexible"),
    ("terms and certify (mixed)", "I agree to the terms and certify that my answers are true"),
    ("criminal record", "I certify that I have never been convicted of a felony and the information is true"),
    ("age declaration", "I am at least 18 years of age and certify the information is true"),
    ("not an attestation", "Thank you for applying!"),
    ("empty", ""),
    ("an unknown word", "I certify that the information is true and complete and vibrant"),
    # Fix of c8a18cf's BLOCK 1: clauses built from vocabulary words that
    # are no attestation and no consequence of misrepresentation.
    ("an at-will clause from vocabulary words",
     "I certify that the information provided is true and complete, and I understand that my employment may be "
     "terminated at any time with or without cause or notice."),
    ("a declaration about Jack from vocabulary words",
     "I certify that the information provided in this application is true and complete and that I am not subject "
     "to any disciplinary action."),
    ("a consequence clause with no falsity",
     "I certify that the information is true and complete. My employment may be terminated."),
    # Fix of c8a18cf's BLOCK 2: a token with a digit or in another script is unknown.
    ("a digit token", "I certify that the information provided is true and complete and that I am 18+."),
    ("a digit token, no I am", "I certify that the information provided is true and complete and 18+."),
    ("another script", "I certify that the information is true and complete. Я согласен."),
    ("a non-Latin letter inside a word", "I certify that the informatiοn is true and complete."),   # Greek omicron
    # Second fix of c8a18cf's BLOCK (b9376b3's review): other connectives,
    # declarations from attestation words, a symbol. Each passes on b9376b3.
    ("an at-will clause after 'and' with no new subject",
     "I understand that any misrepresentation may result in dismissal and employment may be terminated at any time."),
    ("an at-will clause first", "I understand that my employment may be terminated at any time and any false statement "
     "will result in dismissal."),
    ("an at-will clause after ', as'", "I understand that any false statement may result in dismissal, as my employment "
     "may be terminated at any time."),
    ("an at-will clause with no join", "I understand that my employment may be terminated at any time regardless of any omission."),
    ("a resume declaration", "I confirm that I have attached my complete resume with all my information."),
    ("a knowledge declaration", "I have complete knowledge of the whole application process including the information I state herein."),
    ("a 'but' clause", "I certify that the information is true and complete but I have nothing to declare."),
    ("an attachment clause", "I certify that the information is true and complete and have attached my resume and cv."),
    ("an employment declaration", "I state that I have been in employment during this process with accurate information."),
    ("a symbol", "I certify that the information is true and complete \U0001F6AB"),
]
MIXED = "I agree to the terms and certify that my answers are true"


def test_the_attestation_class_takes_jacks_variants_and_nothing_else():
    # Fails on ab0a09e: no gate.attestation_ok.
    check = Checks()
    for text in POSITIVE:
        check(gate.attestation_ok(text), f"ticked: {text[:70]!r}")
    for what, clause in FORBIDDEN:
        text = f"{ATT} {clause}."
        check(not gate.attestation_ok(text), f"{what}: never ({clause!r})")
    for what, text in LEGAL:
        check(not gate.attestation_ok(text), f"{what}: never")
    split = ATT.replace("information", "infor­mation")        # a soft hyphen inside a word
    check(not gate.attestation_ok(split), "an invisible character inside a word: the as-written reading fails")
    check(not gate.consent_ok(ATT), "the terms / privacy policy still refuses a certification (unchanged)")
    check(gate.attestation_ok(ATT + " " + ATT), "two attestation sentences pass")
    check(not gate.attestation_ok(ATT * 12), "over-long: never")
    check.done()


def _att_record(label=ATT, required=True, category="legal_declaration"):
    r = base()
    r["fields"].append({"ref": "f9", "type": "checkbox", "label": label, "required": required})
    r["categories"]["f9"] = category
    r["flags"].append({"ref": "f9", "label": label, "category": category, "reason": "You sign and consent to this, not a script."})
    r["census"].append(box(label, ref="f9", required=required, checked=False))
    return r


def test_the_gate_plans_a_required_attestation_box_as_a_consent_tick_and_stops_on_an_optional_one():
    # Fails on ab0a09e: the census stopped "not on consent_allowlist, and not a terms / privacy consent".
    check = Checks()
    v = gate_run(_att_record())
    check(v["content_verdict"] == gate.WOULD_SUBMIT and v["consent_actions"].get("f9") == "would_tick"
          and any(t.get("ref") == "f9" and t.get("source") == "attestation" for t in v["trace"]),
          f"a required attestation box: planned as a tick, traced as an attestation ({v['content_reasons']})")
    v = gate_run(_att_record(required=False))
    check(v["content_verdict"] == gate.WOULD_STOP and "f9" not in v["consent_actions"],
          f"an optional attestation box: never planned, the gate stops ({v['content_reasons']})")
    v = gate_run(_att_record(label=MIXED))
    check(v["content_verdict"] == gate.WOULD_STOP and "f9" not in v["consent_actions"],
          "terms bundled with a certification: Jack's")
    v = gate_run(_att_record(label=LEGAL[0][1]))
    check(v["content_verdict"] == gate.WOULD_STOP and "f9" not in v["consent_actions"], "a LEGAL_GUARD wording: Jack's")
    r = _att_record()
    r["census"][-1]["described"] = "and I authorize a background check"
    check(gate_run(r)["content_verdict"] == gate.WOULD_STOP, "fine print attached: Jack's")
    r = _att_record()
    r["census"][-1]["label_source"] = "aria-label"
    check(gate_run(r)["content_verdict"] == gate.WOULD_STOP, "a label Jack never sees (aria-label): Jack's")
    check.done()


def test_decision_e_a_box_carrying_jacks_review_mark_passes_as_he_left_it():
    # Fails on ab0a09e: the census stopped on the mixed box whatever human_reviewed held.
    check = Checks()
    for ticked in (True, False):
        r = base()
        r["fields"].append({"ref": "c9", "type": "checkbox", "label": MIXED, "required": True})
        r["categories"]["c9"] = "legal_declaration"
        r["flags"].append({"ref": "c9", "label": MIXED, "category": "legal_declaration", "reason": "yours"})
        r["census"].append(box(MIXED, ref="c9", required=True, checked=ticked))
        r["snapshot"]["c9"] = ticked
        stopped = gate_run(copy.deepcopy(r))
        check(stopped["content_verdict"] == gate.WOULD_STOP, f"without his mark the box stops ({ticked})")
        r["human_reviewed"] = ["c9"]
        v = gate_run(copy.deepcopy(r))
        if ticked:
            check(v["content_verdict"] == gate.WOULD_SUBMIT, f"ticked by Jack: passes ({v['content_reasons']})")
        else:
            check(v["content_verdict"] == gate.WOULD_STOP and any("required, but empty" in x for x in v["content_reasons"])
                  and not any("consent_allowlist" in x for x in v["content_reasons"]),
                  f"left unticked by Jack: the census is waived, the required-but-empty check still stands ({v['content_reasons']})")
    r = base()
    r["census"].append(box(MIXED, ref="c9", checked=True, visible=False))
    r["human_reviewed"] = ["c9"]
    check(any("hidden, and ticked" in x for x in gate_run(r)["content_reasons"]),
          "a hidden ticked box stops even with his mark")
    check.done()


TICK_PAGE = f"""<!doctype html><html><body><form id="f">
<input type="checkbox" id="att" name="att" required><label for="att">{ATT}</label>
<input type="checkbox" id="opt" name="opt"><label for="opt">{ATT}</label>
<input type="checkbox" id="mix" name="mix" required><label for="mix">{MIXED}</label>
<button type="submit">Submit application</button></form></body></html>"""


class _Profile:
    def consent_allowlist(self):
        return []


def test_the_tick_helper_ticks_a_required_attestation_box_only():
    # Fails on ab0a09e: tick_allowed ticked nothing but the policy's and the allowlist's.
    check = Checks()
    p = TEST_HOME / "attest-tick.html"
    p.write_text(TICK_PAGE, encoding="utf-8")
    with mock_page(p) as page:
        fields = extract.extract_fields(page)
        by = {f["name"]: f for f in fields}
        actions = {by["att"]["ref"]: "would_tick", by["opt"]["ref"]: "would_tick", by["mix"]["ref"]: "would_tick"}
        steps = []
        ticked = submit.tick_allowed(page, {"consent_actions": actions}, _Profile(), steps)
        states = dict(page.evaluate("[...document.querySelectorAll('input[type=checkbox]')].map(b => [b.id, b.checked])"))
        check(ticked == [by["att"]["ref"]] and states == {"att": True, "opt": False, "mix": False},
              f"only the required attestation box, though the actions named three ({states})")
        page.uncheck("#att")
        page.evaluate("() => { document.getElementById('att').removeAttribute('required'); }")
        ticked = submit.tick_allowed(page, {"consent_actions": actions}, _Profile(), [])
        check(ticked == [] and not page.is_checked("#att"), "the same box no longer required: not ticked")
    check.done()


CERT = (f'<input type="checkbox" id="cert" name="cert" required><label for="cert">{MIXED}</label>')


def test_a_mixed_box_jack_ticks_himself_under_the_pause_passes_on_continue():
    # Fails on ab0a09e: after Continue the census stopped on the box again
    # ("not on consent_allowlist, and not a terms / privacy consent").
    from test_pick_up import _page, _run
    check = Checks()
    with _run(_page(city=CERT), auto_submit=lambda: True) as (page, w):
        out = w.run()
        check(out.status == wizard.WAITING and w.pending["kind"] == "gate", f"the gate pause on step 2 ({out.status}: {out.reason})")
        check(not page.is_checked("#cert"), "the agent did not tick the mixed certification box")
        page.check("#cert")                                   # Jack's own click, under the pause
        page.wait_for_timeout(300)
        out = w.continue_from({})
        check(out.status == "submitted", f"after Continue the job goes on through Submit ({out.status}: {out.reason})")
        check(any("gate_pause" == e.get("kind") and e.get("handled") == "jack" for e in out.events),
              "the pause counts as his")
    check.done()


def _profile_file(name):
    p = TEST_HOME / f"attest-profile-{name}.yaml"
    p.write_text(PROFILE, encoding="utf-8")
    return p


def test_a_box_jack_ticked_is_offered_once_and_his_click_writes_one_allowlist_line():
    # Fails on ab0a09e: no offers, no routes, no approve.add_consent_allowlist_line.
    check = Checks()
    state = gui.State()
    state.memory_path = TEST_HOME / "attest-memory.yaml"
    state.memory_path.unlink(missing_ok=True)
    state.profile_path = _profile_file("offers")
    store = memory.Learned.load(state.memory_path)

    def change(label, now=True, ftype="checkbox", explicit=True):
        f = {"ref": "x", "type": ftype, "label": label}
        if explicit:
            f["explicit_label"] = label
        return {"field": f, "kind": "filled_blank", "agent": None, "now": now, "decision": {}, "category": "consent"}

    log = []
    offered = corrections.offer_ticks([change(MIXED), change("I agree to the Terms of Service and Privacy Policy"),
                                       change(ATT), change("I am not a veteran"), change("Send me job alerts"),
                                       change(MIXED, now=False), change("I accept the handbook", explicit=False),
                                       change("Have you worked at Acme?"), change(MIXED)],
                                      url="https://careers.example-co.com/apply", log=log, store=store)
    check([o["label"] for o in offered] == [MIXED] and len(store.offers) == 1,
          f"one offer: the mixed box; a policy box, an attestation, a legal, a marketing and an unticked box, a "
          f"nearby-text label and a per-company wording are never offered ({[o['label'] for o in store.offers]})")
    check(any("Memory page" in l for l in log), f"the job log says so ({log})")
    status, _, body = _req(state_server := gui.Server(state).start(), "GET", "/memory")
    try:
        check(status == 200 and body["ok"] and [o["label"] for o in body["tick_offers"]] == [MIXED],
              "the Memory page lists the offer")
        oid = body["tick_offers"][0]["id"]
        origin = f"http://127.0.0.1:{state_server.port}"
        status, _, bad = _req(state_server, "POST", "/memory/always-tick", {"id": oid}, origin=origin, token="wrong")
        check(status == 403, "a wrong token is refused")
        status, _, body = _req(state_server, "POST", "/memory/always-tick", {"id": oid}, origin=origin)
        prof = Profile.load(state.profile_path)
        text = state.profile_path.read_text(encoding="utf-8")
        check(status == 200 and body.get("ok") is True and prof.consent_allowlist() == [MIXED]
              and f'- "{MIXED}"' in text and state.take_profile_changed() is True,
              f"his click writes the one quoted line and flags the reload ({body}, {text[-200:]!r})")
        check(approve.backups_of(state.profile_path), "a backup was taken first")
        check(memory.Learned.load(state.memory_path).offers == [], "the offer is gone once the profile holds it")
        status, _, body = _req(state_server, "POST", "/memory/always-tick", {"id": oid}, origin=origin)
        check(body.get("ok") is False, "offered once: a second click finds nothing")
        corrections.offer_ticks([change("I accept the employee handbook")], url="https://x.example/", store=memory.Learned.load(state.memory_path))
        oid2 = memory.Learned.load(state.memory_path).offers[0]["id"]
        status, _, body = _req(state_server, "POST", "/memory/dismiss-tick", {"id": oid2}, origin=origin)
        check(body.get("ok") is True and memory.Learned.load(state.memory_path).offers == [], "Dismiss drops it")
        check(Profile.load(state.profile_path).consent_allowlist() == [MIXED], "a dismissed wording is not written")
    finally:
        state_server.stop()
    for what, label in (("a legal wording", "I am not a veteran"), ("a pay wording", "My salary expectation is flexible"),
                        ("a marketing wording", "Send me job alerts"), ("a per-company wording", "Have you worked at Acme?"),
                        ("a criminal-record wording", "I have never been convicted of a felony")):
        try:
            approve.add_consent_allowlist_line(state.profile_path, label, approve.profile_stamp(state.profile_path))
            check(False, f"{what}: refused")
        except approve.Refused as e:
            check("never allowlisted" in str(e), f"{what}: refused ({e})")
    try:
        approve.add_consent_allowlist_line(state.profile_path, "I accept the employee handbook", "stale")
        check(False, "a stale stamp: refused")
    except approve.Changed:
        check(True, "a stale stamp: refused")
    check(Profile.load(state.profile_path).consent_allowlist() == [MIXED], "nothing else was written")
    check.done()


if __name__ == "__main__":
    run_as_script(globals())
