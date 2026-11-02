"""
test_read_rule.py — decision A (Jack, 2026-11-02): the credential-page read rule, loosened with limits.

WHAT THIS DOES: On a mock where the sign-up form and the My Information
form live at ONE address (Workday's /apply/applyManually), scans the
sign-up (a credential step: the page is tainted), swaps in My
Information, fills it through pipeline.fill_page and shows the gate no
longer stops "required, but empty": the filled boxes are read, because
this scan finds no password box or hint, no secret field, and no field
sharing an identity (tag, type, name or explicit label) with one scanned
beside the password boxes. Then the limits: a show-password toggle on
the sign-up form (type=text, hints gone, names or only ids left) stays
unreadable; a password box reappearing in My Information re-taints the
page and the in-page read refuses on its own; a My Information variant
carrying the sign-up's email box (same identity) stays unreadable; and
the sticky secret keys hold for the run.
WHY IT MATTERS: Jack's Workday run (2026-11-02) typed every My
Information box and the gate still stopped "required, but empty",
because the sign-up page had tainted the address for the rest of the
run. Decision A is the one approved change to the read rule, exactly as
written; everything a password could reach stays refused.

Run from anywhere:  .venv/Scripts/python.exe tests/test_read_rule.py
"""
import json
from unittest import mock

from _fill_run import classify_by_label, map_from_profile
from _harness import REPO, Checks, mock_page, run_as_script
from agent import extract, gate, pipeline
from test_memory import _prof

FIXTURE = REPO / "tests" / "signup_then_info.html"
URL = "https://careers.example-co.com/apply/applyManually"


def _info_fill(page, guard, prof):
    with mock.patch("agent.classify.classify_fields", classify_by_label), \
            mock.patch("agent.mapper.map_fields", map_from_profile):
        return pipeline.fill_page(page, prof, guard=guard, resume=None, posting_text="")


def _direct(page, fields) -> dict:
    """Ask the page for every ref straight through _SNAPSHOT_JS, as the hook and the watch do."""
    items = [[r, *f["sigs"][r]] for f in fields for r in (f.get("option_refs") or [f["ref"]]) if r in f.get("sigs", {})]
    return page.evaluate(extract._SNAPSHOT_JS, items)


def test_my_information_at_the_sign_up_address_is_read_and_the_gate_no_longer_stops_required_but_empty():
    # Fails on 168606b: every field at a tainted page_key got
    # credential_step True for the rest of the run, the snapshot read
    # nothing, and the gate stopped "'First Name': required, but empty".
    check = Checks()
    prof = _prof("read-rule-profile.yaml")
    with mock_page(FIXTURE) as page:
        guard = extract.ReadGuard()
        signup = extract.extract_fields(page, guard)
        key = extract.page_key(page.url)
        check(signup and all(f.get("credential_step") for f in signup) and key in guard.credential_pages,
              "the sign-up scan: a credential step, the address tainted")
        beside = guard.credential_identities.get(key, set())
        check(("input", "email", "name", "email") in beside and ("input", "email", "label", "email") in beside
              and any(i[2:] == ("name", "password") for i in beside),
              f"the identities beside the password boxes are remembered, by name and by label ({sorted(beside)})")
        page.evaluate("() => show('info')")
        fill = _info_fill(page, guard, prof)
        check(fill is not None and fill.fields and not any(f.get("credential_step") for f in fill.fields),
              "My Information at the same address: no password box, no hint, no shared identity, so it is read")
        check(key in guard.credential_pages, "the address keeps its taint: every later scan there is judged again")
        first = next(f for f in fill.fields if f["label"] == "First Name")
        check(fill.snapshot.get(first["ref"]) == "Test", f"the filled First Name reads back ({fill.snapshot.get(first['ref'])!r})")
        record = pipeline.build_record(page, URL, "generic", "default", "", [fill.step], fill.events, dry_run=False)
        result = gate.decide(record, prof)
        check(not any("required, but empty" in r for r in result["content_reasons"]),
              f"the gate does not stop 'required, but empty' ({result['content_reasons']})")
        check(result["content_verdict"] == gate.WOULD_SUBMIT, f"the page passes ({result['content_reasons']})")
        # The limit (decision A): a password box reappearing re-taints.
        page.evaluate("() => addPassword()")
        check(extract.snapshot(page, fill.fields) == {} and _direct(page, fill.fields) == {},
              "a password box drawn into the page: the in-page read refuses at once")
        again = extract.extract_fields(page, guard)
        check(again and all(f.get("credential_step") for f in again) and extract.readable_refs(again) == [],
              "the next scan is a credential step again")
        page.evaluate("() => document.querySelector('[name=pw3]').remove()")
        once_more = extract.extract_fields(page, guard)
        check(once_more and all(f.get("credential_step") for f in once_more),
              "the box gone again: still a credential step, since My Information's own fields were scanned beside "
              "a password box (decision A's identity condition holds for the run)")
    check.done()


def test_a_show_password_toggle_on_the_sign_up_form_stays_unreadable():
    # Fails on 168606b only in direction (it passed by the old blanket
    # rule); a guard that decision A's loosening doesn't reach these.
    check = Checks()
    with mock_page(FIXTURE) as page:
        guard = extract.ReadGuard()
        extract.extract_fields(page, guard)
        page.evaluate("() => toggle()")
        toggled = extract.extract_fields(page, guard)
        by = {f["label"]: f for f in toggled}
        check(by["Create Password"].get("secret") and by["Verify Password"].get("secret"),
              "type=text with the hints gone: the boxes are still secret by name and sticky key")
        check(all(f.get("credential_step") for f in toggled) and extract.readable_refs(toggled) == []
              and extract.snapshot(page, toggled) == {},
              "a scan holding a secret field keeps the page a credential step: nothing read, not the email either")
        page.evaluate("() => strip()")
        stripped = extract.extract_fields(page, guard)
        by = {f["label"]: f for f in stripped}
        check(by["Create Password"].get("secret") and by["Verify Password"].get("secret"),
              "names gone too: the sticky id keys still make them secret")
        check(all(f.get("credential_step") for f in stripped) and extract.snapshot(page, stripped) == {},
              "still a credential step, nothing read")
        page.fill("#em", "typed@example.invalid")
        check(extract.read_items(stripped) == [] and extract.snapshot(page, stripped) == {},
              "the email the agent typed is not read back either: no ref is even asked for (the hook and the "
              "watch read through read_items)")
    check.done()


def test_a_field_sharing_an_identity_with_a_sign_up_field_keeps_the_page_unreadable():
    # Fails on 168606b in direction only: the old rule read nothing at the
    # address either; this guards decision A's second condition.
    check = Checks()
    with mock_page(FIXTURE) as page:
        guard = extract.ReadGuard()
        extract.extract_fields(page, guard)
        page.evaluate("() => show('info_email')")
        fields = extract.extract_fields(page, guard)
        by = {f["label"]: f for f in fields}
        check(not any(f.get("secret") for f in fields) and "Email" in by,
              "My Information with an email box like the sign-up's: no secret field")
        check(extract.field_identities(by["Email"]) & guard.credential_identities[extract.page_key(page.url)],
              "its identity (input, email, name email) is one scanned beside the password boxes")
        check(all(f.get("credential_step") for f in fields) and extract.readable_refs(fields) == [],
              "so the whole scan stays a credential step: First Name is not read either")
        page.fill("#fn", "Test")
        check(extract.snapshot(page, fields) == {}, "nothing read back")
        page.evaluate("() => show('info')")
        plain = extract.extract_fields(page, guard)
        check(plain and not any(f.get("credential_step") for f in plain),
              "the variant without the shared box is readable")
        # Fold of 49f3d78's finding 1: a renamed box still matches by its label.
        page.evaluate("() => show('info_renamed')")
        renamed = extract.extract_fields(page, guard)
        by = {f["label"]: f for f in renamed}
        check(by["Email"].get("dom_name") == "emailAddress" and all(f.get("credential_step") for f in renamed),
              "an email box renamed since the sign-up scan, labelled Email as before: still a credential step")
    check.done()


def test_a_secret_field_alone_keeps_the_page_a_credential_step():
    # The implementation's one condition stricter than decision A as
    # written: a scan holding a field secret by any layer stays a
    # credential step even with no password box, no hint and no shared
    # identity. Mutation check: deleting the `any(is_secret(f) ...)` term
    # in extract_fields makes this fail (49f3d78's finding 2).
    check = Checks()
    with mock_page(FIXTURE) as page:
        guard = extract.ReadGuard()
        extract.extract_fields(page, guard)
        page.evaluate("() => show('info_pin')")
        fields = extract.extract_fields(page, guard)
        by = {f["label"]: f for f in fields}
        beside = guard.credential_identities[extract.page_key(page.url)]
        check(by["PIN"].get("secret") and not by["First Name"].get("secret"),
              "the PIN box is secret by the password-word layer, First Name is not")
        check(not any(extract.field_identities(f) & beside for f in fields),
              "no field of this scan shares an identity with the sign-up's")
        check(all(f.get("credential_step") for f in fields) and extract.readable_refs(fields) == [],
              "the secret field alone keeps the page a credential step: First Name is not read")
        page.fill("#fn", "Test")
        check(extract.snapshot(page, fields) == {}, "nothing read back")
    check.done()


def test_the_snapshot_js_check_is_unchanged():
    check = Checks()
    check("input[type=password i], [autocomplete*=\"password\" i]" in extract._SNAPSHOT_JS
          and "return out;" in extract._SNAPSHOT_JS and "if (document.querySelector(" in extract._SNAPSHOT_JS,
          "the in-page read still returns nothing while any password box or hint is in the document")
    check(extract.field_identities({"tag": "input", "type": "text"}) == {("input", "text", "anon", "")}
          and extract.field_identities({"tag": "input", "type": "text", "dom_name": "", "explicit_label": "Last  Name"})
          == {("input", "text", "label", "last name")}
          and extract.field_identities({"tag": "input", "type": "text", "dom_name": "ln", "explicit_label": "Last Name"})
          == {("input", "text", "name", "ln"), ("input", "text", "label", "last name")},
          "an anonymous field has the one anonymous identity; a named and labelled one both keys")
    check.done()


if __name__ == "__main__":
    run_as_script(globals())
