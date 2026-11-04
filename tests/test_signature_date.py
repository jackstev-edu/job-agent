"""
test_signature_date.py — decision B (Jack, 2026-11-02): today's date in a self-identification form's signature block.

WHAT THIS DOES: On a generic self-identification mock (a disability
question as a checkbox group, then a Signature block with a Name box and
Month / Day / Year widgets, then a Date of birth box elsewhere), fills
the page through pipeline.fill_page under a patched clock and shows the
signature block's selects take today's month, day and year, text boxes
with MM / DD / YYYY placeholders or inputmode="numeric" take 2 / 2 / 4
digits, one Date box takes its placeholder's format, a Date box with no
stated format is left for Jack, the Date of birth box outside the block
is never touched by the clock, no date-part box ever reaches the model,
a page with no EEO question gets no clock fill, and the gate takes a
clock fill only when it recomputes the same value from the record.
WHY IT MATTERS: Jack's Workday run left the signature date for him. It is
his own fact (the computer's clock), never the model's, and only where
the widget states its format.

Run from anywhere:  .venv/Scripts/python.exe tests/test_signature_date.py
"""
import datetime
import io
import re
from contextlib import redirect_stdout
from unittest import mock

import yaml

from _fill_run import PROFILE
from _harness import TEST_HOME, Checks, mock_page, run_as_script
from agent import extract, gate, pipeline, signdate
from agent.profile import Profile
from test_gate import base, run as gate_run

TODAY = datetime.date(2026, 11, 2)
URL = "https://careers.example-co.com/apply/self-identification"

DISABILITY = """<fieldset><legend>Do you have a disability? Please check one of the boxes below.</legend>
<input type="checkbox" id="y" name="d_yes"><label for="y">Yes, I have a disability, or have had one in the past</label>
<input type="checkbox" id="n" name="d_no"><label for="n">No, I do not have a disability and have not had one in the past</label>
<input type="checkbox" id="d" name="d_decline"><label for="d">I do not want to answer</label></fieldset>"""
SELECTS = """<label for="mo">Month</label><select id="mo" name="sig_month"><option value="">Select</option>
<option>01</option><option>02</option><option>10</option><option>11</option><option>12</option></select>
<label for="dy">Day</label><select id="dy" name="sig_day"><option value="">Select</option>
<option>1</option><option>2</option><option>3</option><option>31</option></select>
<label for="yr">Year</label><select id="yr" name="sig_year"><option value="">Select</option>
<option>2025</option><option>2026</option><option>2027</option></select>"""
TEXTS = """<label for="mo">Month</label><input id="mo" name="sig_month" placeholder="MM">
<label for="dy">Day</label><input id="dy" name="sig_day" placeholder="DD">
<label for="yr">Year</label><input id="yr" name="sig_year" placeholder="YYYY">"""
NUMERIC = """<label for="mo">Month</label><input id="mo" name="sig_month" inputmode="numeric">
<label for="dy">Day</label><input id="dy" name="sig_day" inputmode="numeric">
<label for="yr">Year</label><input id="yr" name="sig_year" inputmode="numeric">"""
ONE_DATE = """<label for="dt">Date</label><input id="dt" name="sig_date" placeholder="MM/DD/YYYY">"""
BARE_DATE = """<label for="dt">Signature date</label><input id="dt" name="sig_date">"""

PAGE = """<!doctype html><html><body><h1>Voluntary self-identification (mock)</h1><form id="f">
EEO
<fieldset id="sig"><legend>Signature</legend>
<label for="nm">Your Name</label><input id="nm" name="sig_name">
DATES
</fieldset>
<fieldset><legend>Dates</legend><label for="dob">Date of birth</label><input id="dob" name="dob" placeholder="MM/DD/YYYY"></fieldset>
</form></body></html>"""


def _page(name, dates, eeo=DISABILITY):
    p = TEST_HOME / f"signdate-{name}.html"
    p.write_text(PAGE.replace("EEO", eeo).replace("DATES", dates), encoding="utf-8")
    return p


def _prof():
    text = PROFILE.replace("eeo:\n  gender: Decline to self-identify\n", "eeo:\n  disability_status: false\n")
    p = TEST_HOME / "signdate-profile.yaml"
    p.write_text(text, encoding="utf-8")
    return Profile(yaml.safe_load(text), p)


def _classify(fields):
    # As Jack's run had it: the signature block's date boxes read as legal.
    out = {}
    for f in fields:
        label = (f.get("label") or "").lower()
        out[f["ref"]] = "eeo" if ("disabilit" in label or signdate.date_part(label) in ("month", "day", "year", "date")) else "personal_fact"
    return out


def _fill(page, prof, seen):
    def mapper(fields, profile, page_text=""):
        seen.extend(f.get("label") for f in fields)
        return {}
    with mock.patch("agent.classify.classify_fields", _classify), mock.patch("agent.mapper.map_fields", mapper), \
            mock.patch.object(signdate, "_today", return_value=TODAY), redirect_stdout(io.StringIO()):
        return pipeline.fill_page(page, prof, guard=extract.ReadGuard(), resume=None, posting_text="")


def _values(page):
    return page.evaluate("() => Object.fromEntries([...document.querySelectorAll('#f input:not([type=checkbox]), #f select')].map(e => [e.id, e.value]))")


def _gate(page, fill, prof):
    record = pipeline.build_record(page, URL, "generic", "default", "", [fill.step], fill.events, dry_run=False)
    with mock.patch.object(signdate, "_today", return_value=TODAY):
        return gate.decide(record, prof)


def test_the_signature_blocks_selects_take_todays_date_and_the_date_of_birth_box_does_not():
    # Fails on 2d0c2ee: no signdate, the date boxes were left for Jack.
    check = Checks()
    prof, seen = _prof(), []
    with mock_page(_page("selects", SELECTS)) as page:
        fill = _fill(page, prof, seen)
        by = {f["name"]: f for f in fill.fields}
        check(by["sig_month"].get("signature_block") and by["sig_year"].get("signature_block")
              and not by["dob"].get("signature_block"),
              "the scan marks the block's date boxes, not the Date of birth box in its own fieldset")
        for name, want in (("sig_month", "11"), ("sig_day", "2"), ("sig_year", "2026")):
            d = fill.plan.get(by[name]["ref"])
            check(d and d["source"] == "clock" and d["value"] == want and d["fit"] == "exact",
                  f"{name}: planned from the clock as {want!r} ({d})")
        check(_values(page) == {"nm": "Test Person", "mo": "11", "dy": "2", "yr": "2026", "dob": ""},
              f"the selects hold today's parts; the Name box holds the profile's name; Date of birth is empty ({_values(page)})")
        check(by["dob"]["ref"] not in fill.plan or fill.plan[by["dob"]["ref"]].get("source") != "clock",
              "the Date of birth box outside the block is never dated by the clock")
        check(not any(signdate.date_part(l) for l in seen), f"no date-part box reached the model ({seen})")
        result = _gate(page, fill, prof)
        check(not any("Month" in r or "Day" in r or "Year" in r for r in result["content_reasons"]),
              f"the gate takes the clock fills ({result['content_reasons']})")
        check(any(t.get("source") == "clock" for t in result["trace"]), "traced as the clock's")
    check.done()


def test_text_boxes_take_the_date_only_where_the_widget_states_its_format():
    # Fails on 2d0c2ee: no signdate.
    check = Checks()
    prof = _prof()
    with mock_page(_page("texts", TEXTS)) as page:
        fill = _fill(page, prof, [])
        check(_values(page) == {"nm": "Test Person", "mo": "11", "dy": "02", "yr": "2026", "dob": ""},
              f"MM / DD / YYYY placeholders: 2 / 2 / 4 digits ({_values(page)})")
    with mock_page(_page("numeric", NUMERIC)) as page:
        fill = _fill(page, prof, [])
        check(_values(page) == {"nm": "Test Person", "mo": "11", "dy": "02", "yr": "2026", "dob": ""},
              f"inputmode=numeric: 2 / 2 / 4 digits ({_values(page)})")
    with mock_page(_page("one", ONE_DATE)) as page:
        fill = _fill(page, prof, [])
        check(_values(page) == {"nm": "Test Person", "dt": "11/02/2026", "dob": ""}, f"one Date box in its placeholder's format ({_values(page)})")
    with mock_page(_page("bare", BARE_DATE)) as page:
        fill = _fill(page, prof, [])
        by = {f["name"]: f for f in fill.fields}
        flag = next((fl for fl in fill.flags if fl.get("ref") == by["sig_date"]["ref"]), None)
        check(_values(page) == {"nm": "Test Person", "dt": "", "dob": ""} and flag and flag.get("source") == "human",
              f"a Date box stating no format: left for Jack ({_values(page)}, {flag and flag.get('reason')})")
    with mock_page(_page("no-eeo", SELECTS, eeo="<label for=\"x\">Nickname</label><input id=\"x\" name=\"nick\">")) as page:
        fill = _fill(page, prof, [])
        check(_values(page).get("mo") == "" and not any(d.get("source") == "clock" for d in fill.plan.values()),
              "a page with no EEO question: the signature block's dates are not the clock's")
    check.done()


def test_a_date_box_the_backstop_stops_stays_jacks_and_the_clock_runs_before_the_classifier():
    # Fix of d9461c9's BLOCK: a "Month" box whose help text hits HARD_STOPS
    # was dated from the clock; the classifier's category did not matter.
    # Fails on d9461c9 (the stop was read past).
    check = Checks()
    prof = _prof()
    month = {"ref": "m1", "type": "select", "label": "Month", "options": ["Select", "10", "11", "12"],
             "signature_block": True, "help_text": "By selecting a date you acknowledge the terms above"}
    eeo = {"ref": "e1", "type": "select", "label": "Are you a protected veteran?", "options": ["Yes", "No"]}
    with mock.patch.object(signdate, "_today", return_value=TODAY):
        check(signdate.plan_for(month, [month, eeo]) is None, "a HARD_STOPS phrase in the help text: no clock plan")
        check(signdate.plan_for(dict(month, help_text="", raw_stop=True), [month, eeo]) is None,
              "the page's raw-stop flag: no clock plan")
        plan, flags, for_model = pipeline.route([month, eeo], {"m1": "personal_fact", "e1": "eeo"}, prof, set())
        check("m1" not in plan and any(fl["ref"] == "m1" for fl in flags) and not for_model,
              f"routed: Jack's, never the model ({[fl.get('reason') for fl in flags]})")
        clean = dict(month, help_text="")
        plan, flags, for_model = pipeline.route([clean, eeo], {"m1": "personal_fact", "e1": "eeo"}, prof, set())
        check(plan.get("m1", {}).get("source") == "clock" and not any(f["ref"] == "m1" for f in for_model),
              "the same box with no stop, classified personal_fact: dated from the clock, never handed to the mapper")
    check.done()


NAMED_DATE = """<label for="dt">Date</label><input id="dt" name="disability_signature_date" placeholder="MM/DD/YYYY">"""


def test_a_date_box_whose_own_text_reads_as_a_legal_question_is_jacks_and_its_name_is_not_read():
    # Fix of 2c24303's BLOCK (Jack, 2026-11-03: "a date box whose own label,
    # help or placeholder hits any LEGAL_GUARD kind is mine", the box's own
    # text only). On 2c24303 each negative was dated from the clock.
    check = Checks()
    prof = _prof()
    month = {"ref": "m1", "type": "select", "label": "Month", "options": ["Select", "10", "11", "12"],
             "signature_block": True, "required": True}
    year = {"ref": "y1", "type": "select", "label": "Year", "options": ["Select", "2025", "2026", "2027"],
            "signature_block": True, "required": True}
    eeo = {"ref": "e1", "type": "select", "label": "Are you a protected veteran?", "options": ["Yes", "No"]}
    cases = [
        ("a work-visa year in the help text", dict(year, help_text="Year your work visa expires"), "personal_fact"),
        ("a clearance year in the help text", dict(year, help_text="Year your security clearance was granted"), "personal_fact"),
        ("a Department of Defense wording", dict(year, help_text="Year of your Department of Defense service"), "personal_fact"),
        ("a pay wording in the help text", dict(month, help_text="Month of your salary review"), "personal_fact"),
        ("an EEO date in the help text", dict(month, help_text="Month your disability began"), "personal_fact"),
        ("the page's own raw legal flag", dict(month, raw_legal=True), "personal_fact"),
        ("a credential category", dict(month), "credential"),
        ("a consent category", dict(month), "consent"),
        ("a per-company category", dict(month), "per_company"),
        # Fold of 9e3d5fb's review, finding 3: the two categories it named.
        ("a work_authorization category", dict(month), "work_authorization"),
        ("a compensation category", dict(month), "compensation"),
    ]
    # Fold of 9e3d5fb's review, finding 1: a PROFILE_PATHS line with no
    # LEGAL_GUARD kind in the help text refuses the clock. (sensitive.decide
    # itself routes such a personal_fact box on as before; only the clock
    # fill is refused here.)
    export = dict(year, help_text="Year your export control status was confirmed")
    with mock.patch.object(signdate, "_today", return_value=TODAY):
        check(signdate.plan_for(export, [export, eeo]) is None, "a profile line with no guard kind in the help text: no clock plan")
        plan, flags, for_model = pipeline.route([export, eeo], {"y1": "personal_fact", "e1": "eeo"}, prof, set())
        check(plan.get("y1", {}).get("source") != "clock", "routed: not the clock's")
    with mock.patch.object(signdate, "_today", return_value=TODAY):
        for what, box_, category in cases:
            check(signdate.plan_for(box_, [box_, eeo], category=category) is None, f"{what}: no clock plan")
            plan, flags, for_model = pipeline.route([box_, eeo], {box_["ref"]: category, "e1": "eeo"}, prof, set())
            check(box_["ref"] not in plan and any(fl["ref"] == box_["ref"] for fl in flags) and not for_model,
                  f"{what}: routed to Jack, never the model ({plan}, {[fl.get('reason') for fl in flags]})")
        named = dict(month, name="disability_signature_month")
        check(signdate.plan_for(named, [named, eeo])["value"] == "11",
              "the box's name is not its own text: a name holding 'disability' still takes the clock")
        check(signdate.plan_for(month, [month, eeo], category="eeo")["value"] == "11"
              and signdate.plan_for(month, [month, eeo], category="personal_fact")["value"] == "11",
              "an eeo or personal_fact category (the classifier's two readings of a signature date) still takes the clock")
    # The gate's recomputation refuses the same boxes.
    def record(box_, category="eeo"):
        r = base()
        r["fields"] += [dict(box_), eeo]
        r["categories"].update({box_["ref"]: category, "e1": "eeo"})
        r["plan"]["e1"] = {"value": "No", "source": "profile", "profile_path": "eeo.veteran_status", "fit": "bool"}
        r["results"]["e1"] = {"filled": True, "unconfirmed": False}
        r["snapshot"]["e1"] = "No"
        value = "11" if box_["label"] == "Month" else "2026"
        r["plan"][box_["ref"]] = {"value": value, "source": "clock", "fit": "exact", "profile_path": None}
        r["results"][box_["ref"]] = {"filled": True, "unconfirmed": False}
        r["snapshot"][box_["ref"]] = value
        return r
    with mock.patch.object(signdate, "_today", return_value=TODAY):
        v = gate_run(record(dict(year, help_text="Year your work visa expires")))
        check(v["content_verdict"] == gate.WOULD_STOP and any("not recomputed from the clock" in x for x in v["content_reasons"]),
              f"the gate: a visa year dated from the clock stops ({v['content_reasons']})")
        v = gate_run(record(dict(month, raw_legal=True)))
        check(v["content_verdict"] == gate.WOULD_STOP and any("not recomputed from the clock" in x for x in v["content_reasons"]),
              f"the gate: the raw legal flag stops a clock fill, in the clock branch ({v['content_reasons']})")
        for category in ("credential", "consent", "work_authorization", "compensation"):
            v = gate_run(record(dict(month), category=category))
            check(v["content_verdict"] == gate.WOULD_STOP and any("not recomputed from the clock" in x for x in v["content_reasons"]),
                  f"the gate: a {category} category stops a clock fill, in the clock branch ({v['content_reasons']})")
        v = gate_run(record(dict(year, help_text="Year your export control status was confirmed")))
        check(v["content_verdict"] == gate.WOULD_STOP and any("not recomputed from the clock" in x for x in v["content_reasons"]),
              f"the gate: a profile line with no guard kind in the help text stops a clock fill ({v['content_reasons']})")
        v = gate_run(record(dict(month, name="disability_signature_month")))
        check(v["content_verdict"] == gate.WOULD_SUBMIT, f"the gate: a name holding 'disability' is not the box's own text ({v['content_reasons']})")
    # The positive Jack asked for: a plain Date box on the disability
    # self-identification form, its name carrying the form's word, fills.
    with mock_page(_page("named", NAMED_DATE)) as page:
        fill = _fill(page, prof, [])
        check(_values(page).get("dt") == "11/02/2026", f"a plain Date box on the self-identification form still fills ({_values(page)})")
        result = _gate(page, fill, prof)
        check(not any("'Date'" in r for r in result["content_reasons"]),
              f"...and the gate takes it: a clock-dated box whose name says 'signature' is no box that signs (fix of 5d099c5's review, finding 3) ({result['content_reasons']})")
    with mock_page(_page("sigdate", '<label for="dt">Signature date</label><input id="dt" name="sig_date" placeholder="MM/DD/YYYY">')) as page:
        fill = _fill(page, prof, [])
        result = _gate(page, fill, prof)
        check(_values(page).get("dt") == "11/02/2026" and not any("Signature date" in r for r in result["content_reasons"]),
              f"a 'Signature date' text box with a stated format is dated from the clock and taken by the gate ({_values(page)}, {result['content_reasons']})")
    check.done()


def _name_page(name, label, dates=SELECTS, eeo=DISABILITY):
    p = TEST_HOME / f"signname-{name}.html"
    p.write_text(PAGE.replace("EEO", eeo).replace("DATES", dates).replace("Your Name", label), encoding="utf-8")
    return p


def test_the_signature_name_box_takes_the_profiles_name_lines_and_any_box_that_signs_is_jacks():
    # Jack, 2026-11-03: the Name box of the signature block, on a
    # self-identification form, from the profile's name lines only; any
    # other signature box is his. Fails on 9e3d5fb (the Name box went to
    # the mapper and stayed empty; a "Signature" box went to the mapper).
    check = Checks()
    prof, seen = _prof(), []
    with mock_page(_page("name", SELECTS)) as page:
        fill = _fill(page, prof, seen)
        by = {f["name"]: f for f in fill.fields}
        d = fill.plan.get(by["sig_name"]["ref"])
        check(by["sig_name"].get("signature_name") is True and not by["dob"].get("signature_name"),
              "the scan marks the block's Name box, not a box in another fieldset")
        check(d and d["source"] == signdate.NAME_SOURCE and d["value"] == "Test Person" and d["fit"] == "verbatim",
              f"planned from the profile's name lines ({d})")
        check(_values(page)["nm"] == "Test Person", f"typed ({_values(page)})")
        check("Your Name" not in seen, f"the Name box never reached the model ({seen})")
        result = _gate(page, fill, prof)
        check(not any("Your Name" in r for r in result["content_reasons"])
              and any(t.get("source") == signdate.NAME_SOURCE for t in result["trace"]),
              f"the gate recomputes the name and traces it ({result['content_reasons']})")
    for label in ("Signature", "Electronic signature", "Signature of applicant", "Initials", "Sign here"):
        seen = []
        with mock_page(_name_page(label.replace(" ", "-").lower(), label)) as page:
            fill = _fill(page, prof, seen)
            by = {f["name"]: f for f in fill.fields}
            flag = next((fl for fl in fill.flags if fl.get("ref") == by["sig_name"]["ref"]), None)
            check(by["sig_name"]["ref"] not in fill.plan and flag and flag.get("source") == "human"
                  and "signature box" in (flag.get("reason") or "") and label not in seen and _values(page)["nm"] == "",
                  f"{label!r}: Jack's, never the model, left empty ({flag and flag.get('reason')}, {seen})")
    seen = []
    with mock_page(_page("name-no-eeo", SELECTS, eeo="<label for=\"x\">Nickname</label><input id=\"x\" name=\"nick\">")) as page:
        fill = _fill(page, prof, seen)
        by = {f["name"]: f for f in fill.fields}
        check(fill.plan.get(by["sig_name"]["ref"], {}).get("source") != signdate.NAME_SOURCE and _values(page)["nm"] == "",
              "a page with no EEO question: the Name box is not this route's")
    with mock_page(_page("name-nodate", "<label for=\"c\">City</label><input id=\"c\" name=\"city\">")) as page:
        fill = _fill(page, prof, [])
        by = {f["name"]: f for f in fill.fields}
        check(not by["sig_name"].get("signature_name") and fill.plan.get(by["sig_name"]["ref"], {}).get("source") != signdate.NAME_SOURCE,
              "a Name box with no date-part box beside it is not marked and not this route's")
    check(signdate.name_value(Profile({"personal": {"first_name": "Test"}}, TEST_HOME / "x.yaml")) is None
          and signdate.name_value(Profile({}, TEST_HOME / "x.yaml")) is None,
          "a profile missing a name line gives no value")
    check(not signdate.is_signature_box({"type": "text", "label": "Design experience"})
          and not signdate.is_signature_box({"type": "text", "label": "Assignment"})
          and signdate.is_signature_box({"type": "text", "label": "Signed"})
          and signdate.is_signature_box({"type": "textarea", "label": "E-signature"}),
          "sign / signature / initials as words, not inside Design or Assignment")
    # The gate's recomputation.
    from test_gate import profile as gate_profile
    named_prof = gate_profile(personal={"first_name": "Test", "last_name": "Person"})
    box_ = {"ref": "n1", "type": "text", "label": "Your Name", "signature_name": True, "signature_name_sole": True, "required": True}
    eeo = {"ref": "e1", "type": "select", "label": "Are you a protected veteran?", "options": ["Yes", "No"]}

    def record(field=box_, value="Test Person"):
        r = base()
        r["fields"] += [dict(field), eeo]
        r["categories"].update({"n1": "personal_fact", "e1": "eeo"})
        r["plan"]["e1"] = {"value": "No", "source": "profile", "profile_path": "eeo.veteran_status", "fit": "bool"}
        r["results"]["e1"] = {"filled": True, "unconfirmed": False}
        r["snapshot"]["e1"] = "No"
        r["plan"]["n1"] = {"value": value, "source": signdate.NAME_SOURCE, "fit": "verbatim", "profile_path": "personal.first_name"}
        r["results"]["n1"] = {"filled": True, "unconfirmed": False}
        r["snapshot"]["n1"] = value
        return r

    v = gate_run(record(), named_prof)
    check(v["content_verdict"] == gate.WOULD_SUBMIT, f"the profile's name in the block's Name box: passes ({v['content_reasons']})")
    v = gate_run(record(value="Someone Else"), named_prof)
    check(v["content_verdict"] == gate.WOULD_STOP and any("name lines" in x for x in v["content_reasons"]), "another value: stops")
    check(gate_run(record(field=dict(box_, signature_name=False)), named_prof)["content_verdict"] == gate.WOULD_STOP, "not the block's Name box: stops")
    check(gate_run(record(field=dict(box_, signature_name_sole=False)), named_prof)["content_verdict"] == gate.WOULD_STOP, "marked but not sole: stops")
    check(gate_run(record(field=dict(box_, label="Signature")), named_prof)["content_verdict"] == gate.WOULD_STOP, "a Signature label: stops")
    check(gate_run(record(), gate_profile())["content_verdict"] == gate.WOULD_STOP, "a profile with no name lines: stops")
    check.done()


CERTS = """<fieldset><legend>Certifications</legend>
<label for="cn">Name</label><input id="cn" name="cert_name">
<label for="ci">Issuer</label><input id="ci" name="cert_issuer">
<label for="cy">Year</label><input id="cy" name="cert_year" placeholder="YYYY"></fieldset>"""
# Nested rows: the smallest ancestor of the Name box holds only Name and
# Year; the Issuer sits in a sibling row of the same fieldset (5d099c5's
# review, finding 4).
NESTED_CERTS = """<fieldset><legend>Certifications</legend>
<div class="row"><label for="cn">Name</label><input id="cn" name="cert_name">
<label for="cy">Year</label><input id="cy" name="cert_year" placeholder="YYYY"></div>
<div class="row"><label for="ci">Issuer</label><input id="ci" name="cert_issuer"></div></fieldset>"""
# A plain-div Certifications row with no fieldset at all (item e, Jack,
# 2026-11-03 evening): a Name + Year row is no block.
PLAIN_CERTS = """<div class="row"><label for="cn">Name</label><input id="cn" name="cert_name">
<label for="cy">Year</label><input id="cy" name="cert_year" placeholder="YYYY"></div>"""
# Fold of 0ec23c5's review: a Name beside a "Date (of certification)" box
# (a free parenthetical is no date part); a Name + Year with hidden Month
# and Day dropdowns; a second closed-list name box in the block.
CERT_DATE = """<div class="row"><label for="cn">Name</label><input id="cn" name="cert_name">
<label for="cd">Date (of certification)</label><input id="cd" name="cert_date" placeholder="MM/DD/YYYY"></div>"""
HIDDEN_SELECTS = """<label for="yr">Year</label><select id="yr" name="sig_year"><option value="">Select</option><option>2026</option></select>
<label for="hm" hidden>Month</label><select id="hm" name="sig_month" hidden><option>11</option></select>
<label for="hd" hidden>Day</label><select id="hd" name="sig_day" hidden><option>2</option></select>"""
TWO_NAMES = """<label for="en">Name of the employee</label><input id="en" name="emp_name">"""
# The signature block's dates reduced to a Year: no full date.
YEAR_ONLY = """<label for="yr">Year</label><select id="yr" name="sig_year"><option value="">Select</option>
<option>2025</option><option>2026</option><option>2027</option></select>"""
# A radio pair beside a Name and a Date: something else in the block.
RADIO_BLOCK = """<fieldset><legend>Signature</legend>
<label for="nm">Your Name</label><input id="nm" name="sig_name">
<label><input type="radio" name="agree" value="y"> I agree</label><label><input type="radio" name="agree" value="n"> I do not agree</label>
<label for="dt">Date</label><input id="dt" name="sig_date" placeholder="MM/DD/YYYY"></fieldset>"""
SIG_BLOCK_HTML = '<fieldset id="sig"><legend>Signature</legend>\n<label for="nm">Your Name</label><input id="nm" name="sig_name">\n\n</fieldset>'


def test_a_name_box_the_route_cannot_fill_is_jacks_and_sign_words_anywhere_on_it_refuse():
    # Fix of 0530a70's BLOCKs (Jack's rule: the Name box from the profile's
    # name lines only, never the model; a box that signs never filled).
    # On 0530a70 the Name box with a missing line, or a label the scan
    # missed, went to the mapper; a "Name" box with placeholder "Signature"
    # or name="signature" was filled; a Certifications Name box beside an
    # Issuer box on an EEO page took Jack's name.
    check = Checks()
    full = _prof()
    half_text = PROFILE.replace("  last_name: Person\n", "").replace("eeo:\n  gender: Decline to self-identify\n", "eeo:\n  disability_status: false\n")
    p_half = TEST_HOME / "signdate-half-profile.yaml"
    p_half.write_text(half_text, encoding="utf-8")
    half = Profile(yaml.safe_load(half_text), p_half)
    check(half.get("personal.last_name") in (None, "") and signdate.name_value(half) is None, "the half profile has no last name")
    seen = []
    with mock_page(_page("name", SELECTS)) as page:
        fill = _fill(page, half, seen)
        by = {f["name"]: f for f in fill.fields}
        flag = next((fl for fl in fill.flags if fl.get("ref") == by["sig_name"]["ref"]), None)
        check(by["sig_name"]["ref"] not in fill.plan and flag and flag.get("source") == "human" and "name lines" in flag["reason"]
              and "Your Name" not in seen and _values(page)["nm"] == "",
              f"a profile missing a name line: the Name box is Jack's, never the model, empty ({flag and flag.get('reason')}, {seen})")
    for label, filled in (("Your full name", True), ("Full legal name", True), ("Name of applicant", True), ("Printed name (please print)", True),
                          ("Applicant’s name", True), ("Employee name", True), ("Name of your supervisor", False), ("Signatory name", False),
                          ("Signer", False), ("E-sig", False), ("Initials", False), ("First name", False)):
        seen = []
        with mock_page(_name_page(re.sub(r"[^a-z]+", "-", label.lower()), label)) as page:
            fill = _fill(page, full, seen)
            by = {f["name"]: f for f in fill.fields}
            d = fill.plan.get(by["sig_name"]["ref"])
            if filled:
                check(d and d["source"] == signdate.NAME_SOURCE and _values(page)["nm"] == "Test Person" and label not in seen,
                      f"{label!r}: a name label from the closed list, filled from the lines ({d}, {_values(page)['nm']!r})")
                # Item e (Jack, 2026-11-03 evening): NAME_RX takes the same
                # closed list, so the dates beside such a box are the
                # clock's too ("Full legal name" dated nothing on e38458a).
                check(by["sig_year"].get("signature_block") and _values(page)["mo"] == "11" and _values(page)["dy"] == "2"
                      and _values(page)["yr"] == "2026",
                      f"{label!r}: the block's dates take the clock beside it ({_values(page)})")
            else:
                check((d is None or d.get("source") != signdate.NAME_SOURCE) and label not in seen and _values(page)["nm"] == "",
                      f"{label!r}: not the Name box, never the model, left empty ({d}, {seen})")
    for what, extra in (("placeholder 'Signature'", 'placeholder="Signature"'), ("name='signature'", 'name="signature"'),
                        ("help text about a signature", 'aria-describedby="h"'), ("placeholder 'Sign here'", 'placeholder="Sign here"')):
        seen = []
        html = PAGE.replace("EEO", DISABILITY).replace("DATES", SELECTS).replace('<input id="nm" name="sig_name">',
                                                                                f'<input id="nm" name="sig_name" {extra}>'
                                                                                '<p id="h" hidden>Your typed name is your signature</p>')
        if extra == 'name="signature"':
            html = html.replace('name="sig_name" name="signature"', 'name="signature"')
        p = TEST_HOME / f"signname-{what[:6].replace(' ', '')}.html"
        p.write_text(html, encoding="utf-8")
        with mock_page(p) as page:
            fill = _fill(page, full, seen)
            box_ = next(f for f in fill.fields if f.get("dom_id") == "nm" or f.get("name") in ("sig_name", "signature"))
            d = fill.plan.get(box_["ref"])
            check(d is None and "Your Name" not in seen and _values(page)["nm"] == "",
                  f"a Name box with {what}: a box that signs, Jack's, never the model, empty ({d}, {seen})")
    seen = []
    with mock_page(_page("certs", SELECTS, eeo=DISABILITY + CERTS)) as page:
        fill = _fill(page, full, seen)
        by = {f["name"]: f for f in fill.fields}
        check(not by["cert_name"].get("signature_name_sole") and fill.plan.get(by["cert_name"]["ref"], {}).get("source") != signdate.NAME_SOURCE
              and by["sig_name"].get("signature_name_sole") and fill.plan.get(by["sig_name"]["ref"], {}).get("value") == "Test Person"
              and not by["cert_year"].get("signature_block") and _values(page)["cy"] == "",
              "a Certifications Name box beside an Issuer box is not the sole Name box, nor its Year the clock's; the block's own is")
    seen = []
    with mock_page(_page("nested", SELECTS, eeo=DISABILITY + NESTED_CERTS)) as page:
        fill = _fill(page, full, seen)
        by = {f["name"]: f for f in fill.fields}
        # Item e (Jack, 2026-11-03 evening): a Name + Year row holds no full
        # date, so it is no block at all: the Name box is not marked (an
        # ordinary name box, routed as before item f) and nothing is
        # planned from the name lines; on e38458a it was marked, not sole.
        check(not by["cert_name"].get("signature_name") and not by["cert_name"].get("signature_name_sole")
              and fill.plan.get(by["cert_name"]["ref"], {}).get("source") != signdate.NAME_SOURCE
              and _values(page)["cn"] == "" and by["sig_name"].get("signature_name_sole"),
              f"a nested-row Certifications Name box beside a lone Year is no signature Name box: nothing from the name lines ({_values(page)})")
        check(not by["cert_year"].get("signature_block") and fill.plan.get(by["cert_year"]["ref"], {}).get("source") != "clock"
              and _values(page)["cy"] == "",
              f"the Certifications Year box is not the clock's: no invented year ({_values(page)['cy']!r})")
    # A plain-div Certifications row with no fieldset, on a page holding an
    # EEO question: neither box filled (item e; on e38458a the sole scope
    # fell back to the row, so the Name box took the name and Year dated).
    seen = []
    with mock_page(_page("plain-certs", SELECTS, eeo=DISABILITY + PLAIN_CERTS)) as page:
        fill = _fill(page, full, seen)
        by = {f["name"]: f for f in fill.fields}
        check(not by["cert_name"].get("signature_name") and not by["cert_name"].get("signature_name_sole")
              and not by["cert_year"].get("signature_block")
              and fill.plan.get(by["cert_name"]["ref"], {}).get("source") != signdate.NAME_SOURCE
              and fill.plan.get(by["cert_year"]["ref"], {}).get("source") != "clock"
              and _values(page)["cn"] == "" and _values(page)["cy"] == ""
              and by["sig_name"].get("signature_name_sole") and _values(page)["nm"] == "Test Person" and _values(page)["yr"] == "2026",
              f"a plain-div Name + Year row: neither box filled; the real block beside it still is ({_values(page)})")
    # The signature block itself with only a Year: no full date, no block,
    # for the clock or the Name box.
    seen = []
    with mock_page(_page("year-only", YEAR_ONLY)) as page:
        fill = _fill(page, full, seen)
        by = {f["name"]: f for f in fill.fields}
        check(not by["sig_name"].get("signature_name") and not by["sig_year"].get("signature_block")
              and fill.plan.get(by["sig_name"]["ref"], {}).get("source") != signdate.NAME_SOURCE
              and fill.plan.get(by["sig_year"]["ref"], {}).get("source") != "clock"
              and _values(page)["nm"] == "" and _values(page)["yr"] == "",
              f"a Name + Year signature block is no block: neither the clock nor the name lines ({_values(page)})")
    with mock_page(_page("one-date", ONE_DATE)) as page:
        fill = _fill(page, full, [])
        by = {f["name"]: f for f in fill.fields}
        check(by["sig_name"].get("signature_name_sole") and by["sig_date"].get("signature_block")
              and _values(page)["nm"] == "Test Person" and _values(page)["dt"] == "11/02/2026",
              f"one Date box is a full date: the block stands ({_values(page)})")
    # Fold of 0ec23c5's review (findings 1, 4 and 5; each filled on
    # 0ec23c5): a date part is read on the closed list, so "Date (of
    # certification)" is none and a Name beside it is no signature Name
    # box; hidden Month / Day dropdowns complete nothing; a second
    # closed-list name box in the block leaves both for Jack; a name label
    # holding a letter outside ASCII is no bare name.
    with mock_page(_page("cert-date", SELECTS, eeo=DISABILITY + CERT_DATE)) as page:
        fill = _fill(page, full, [])
        by = {f["name"]: f for f in fill.fields}
        check(not by["cert_name"].get("signature_name_sole") and fill.plan.get(by["cert_name"]["ref"], {}).get("source") != signdate.NAME_SOURCE
              and not by["cert_date"].get("signature_block") and _values(page)["cn"] == "" and _values(page)["cd"] == "",
              f"a 'Date (of certification)' beside a Name is no full date: neither box filled ({_values(page)})")
    with mock_page(_page("hidden-selects", HIDDEN_SELECTS)) as page:
        fill = _fill(page, full, [])
        by = {f["name"]: f for f in fill.fields}
        check(not by["sig_name"].get("signature_name") and not by["sig_year"].get("signature_block")
              and _values(page)["nm"] == "" and _values(page)["yr"] == "",
              f"hidden Month / Day dropdowns complete no date: no block ({_values(page)})")
    seen = []
    with mock_page(_page("two-names", SELECTS + TWO_NAMES)) as page:
        fill = _fill(page, full, seen)
        by = {f["name"]: f for f in fill.fields}
        check(by["sig_name"].get("signature_name") and not by["sig_name"].get("signature_name_sole")
              and by["emp_name"].get("signature_name") and not by["emp_name"].get("signature_name_sole")
              and by["sig_name"]["ref"] not in fill.plan and by["emp_name"]["ref"] not in fill.plan
              and "Your Name" not in seen and "Name of the employee" not in seen
              and _values(page)["nm"] == "" and _values(page)["en"] == "",
              f"two closed-list name boxes in the block: neither is sole, both are Jack's, never the model ({_values(page)}, {seen})")
    seen = []
    with mock_page(_name_page("other-script", "Name 署名")) as page:
        fill = _fill(page, full, seen)
        by = {f["name"]: f for f in fill.fields}
        flag = next((fl for fl in fill.flags if fl.get("ref") == by["sig_name"]["ref"]), None)
        check(by["sig_name"]["ref"] not in fill.plan and flag and flag.get("source") == "human"
              and not any("署名" in str(l) for l in seen) and _values(page)["nm"] == "",
              f"a name label with a word in another script: Jack's, never the model, empty ({flag and flag.get('reason')}, {seen})")
    check(not signdate._name_box({"type": "text", "label": "Name 署名"}) and not signdate._name_box({"type": "text", "label": "Your Nаme"})
          and signdate._name_box({"type": "text", "label": "Your Name"}),
          "the Python side refuses a letter outside ASCII in a name label (a Cyrillic а inside 'Name' too)")
    # A marked Name box with no profile_name plan is Jack's whatever the
    # page (item e): here a page with no EEO question, as a hold pass or
    # follow mode sees one whose EEO question already holds a value. On
    # e38458a it went to the mapper.
    marked = {"ref": "n1", "type": "text", "label": "Your Name", "signature_name": True, "signature_name_sole": True, "required": True}
    other = {"ref": "t1", "type": "text", "label": "City", "required": False}
    plan, flags, for_model = pipeline.route([marked, other], {"n1": "personal_fact", "t1": "personal_fact"}, full, set())
    flag = next((fl for fl in flags if fl.get("ref") == "n1"), None)
    check("n1" not in plan and flag and flag.get("source") == "human" and "name lines" in flag["reason"]
          and not any(f["ref"] == "n1" for f in for_model) and signdate.name_box_is_jacks(marked, [other]),
          f"a marked Name box on a page with no EEO question: flagged, never the mapper ({flag and flag.get('reason')}, {[f['ref'] for f in for_model]})")
    plan, flags, for_model = pipeline.route([marked, other], {"n1": "personal_fact", "t1": "personal_fact"}, half, set())
    check("n1" not in plan and any(fl.get("ref") == "n1" for fl in flags) and not any(f["ref"] == "n1" for f in for_model),
          "...and with a name line missing, likewise")
    html = PAGE.replace("EEO", DISABILITY).replace("DATES", "")
    assert SIG_BLOCK_HTML in html, "the fixture's signature block as expected"
    html = html.replace(SIG_BLOCK_HTML, RADIO_BLOCK)
    p = TEST_HOME / "signname-radio.html"
    p.write_text(html, encoding="utf-8")
    seen = []
    with mock_page(p) as page:
        fill = _fill(page, full, seen)
        by = {f["name"]: f for f in fill.fields}
        flag = next((fl for fl in fill.flags if fl.get("ref") == by["sig_name"]["ref"]), None)
        check("sig_name" in by and by["sig_name"].get("signature_name") and not by["sig_name"].get("signature_name_sole")
              and by["sig_name"]["ref"] not in fill.plan and flag and flag.get("source") == "human" and "Your Name" not in seen
              and _values(page)["nm"] == "",
              f"a radio pair in the block is something else: the Name box is marked but not sole, Jack's, never the model ({flag and flag.get('reason')}, {seen})")
        check(not by["sig_date"].get("signature_block") and fill.plan.get(by["sig_date"]["ref"], {}).get("source") != "clock" and _values(page)["dt"] == "",
              f"...and the Date box beside it is not the clock's either ({_values(page)})")
    # A marked box of another type (a textarea "Full name") the route cannot
    # fill is Jack's, never the model's (fix of 5d099c5's review, BLOCK 1).
    seen = []
    html = PAGE.replace("EEO", DISABILITY).replace("DATES", SELECTS).replace(
        '<label for="nm">Your Name</label><input id="nm" name="sig_name">', '<label for="nm">Full name</label><textarea id="nm" name="sig_name"></textarea>')
    p = TEST_HOME / "signname-textarea.html"
    p.write_text(html, encoding="utf-8")
    with mock_page(p) as page:
        fill = _fill(page, full, seen)
        by = {f["name"]: f for f in fill.fields}
        flag = next((fl for fl in fill.flags if fl.get("ref") == by["sig_name"]["ref"]), None)
        check(by["sig_name"].get("signature_name") and by["sig_name"]["ref"] not in fill.plan and flag and flag.get("source") == "human"
              and "Full name" not in seen and page.evaluate("document.getElementById('nm').value") == "",
              f"a textarea Name box: marked, not filled, Jack's, never the model ({flag and flag.get('reason')}, {seen})")
    # "Middle initial" alone leaves the sign words (item e, Jack, 2026-11-03
    # evening); every other initial wording stays. On e38458a the first
    # three were signature boxes.
    check(not signdate.is_signature_box({"type": "text", "label": "Middle initial"})
          and not signdate.is_signature_box({"type": "text", "label": "Middle Initial:"})
          and not signdate.is_signature_box({"type": "text", "label": "Middle initial (optional)"})
          and signdate.is_signature_box({"type": "text", "label": "Middle initials"})
          and signdate.is_signature_box({"type": "text", "label": "Middle initial", "placeholder": "Initial here"})
          and signdate.is_signature_box({"type": "text", "label": "Initial"})
          and signdate.is_signature_box({"type": "text", "label": "Please initial"})
          and signdate.is_signature_box({"type": "text", "label": "Initial"})
          and signdate.is_signature_box({"type": "text", "label": "Initials"})
          and signdate.is_signature_box({"type": "text", "label": "Name", "placeholder": "Signature"})
          and signdate.is_signature_box({"type": "text", "label": "Name", "name": "applicant_signature"})
          and signdate.is_signature_box({"type": "text", "label": "Signatory"})
          and signdate.is_signature_box({"type": "email", "label": "Signature"})
          and signdate.is_signature_box({"type": "combobox", "label": "Initials"}),
          "sign words on any of the box's own texts; 'initial' as in 0530a70, with 'Middle initial' alone lifted (item e)")
    # The gate: a model fill in the Name box stops; a filled box that signs stops; an eeo category on the Name box passes.
    from test_gate import profile as gate_profile
    named_prof = gate_profile(personal={"first_name": "Test", "last_name": "Person"})
    eeo = {"ref": "e1", "type": "select", "label": "Are you a protected veteran?", "options": ["Yes", "No"]}

    def record(field, plan_entry, category="personal_fact"):
        r = base()
        r["fields"] += [dict(field), eeo]
        r["categories"].update({"n1": category, "e1": "eeo"})
        r["plan"]["e1"] = {"value": "No", "source": "profile", "profile_path": "eeo.veteran_status", "fit": "bool"}
        r["results"]["e1"] = {"filled": True, "unconfirmed": False}
        r["snapshot"]["e1"] = "No"
        r["plan"]["n1"] = plan_entry
        r["results"]["n1"] = {"filled": True, "unconfirmed": False}
        r["snapshot"]["n1"] = plan_entry["value"]
        return r

    name_box = {"ref": "n1", "type": "text", "label": "Your Name", "signature_name": True, "signature_name_sole": True, "required": True}
    v = gate_run(record(name_box, {"value": "Test Person", "source": "model", "confidence": "high"}), named_prof)
    check(v["content_verdict"] == gate.WOULD_STOP and any("name lines only" in x for x in v["content_reasons"]),
          f"the gate: a model fill in the signature Name box stops ({v['content_reasons']})")
    v = gate_run(record(dict(name_box, type="textarea", label="Full name"), {"value": "Test Person", "source": "model", "confidence": "high"}), named_prof)
    check(v["content_verdict"] == gate.WOULD_STOP and any("name lines only" in x for x in v["content_reasons"]),
          f"the gate: a model fill in a marked textarea Name box stops too ({v['content_reasons']})")
    # Whatever the page (item e): the same stop with no EEO question in the record (on e38458a it passed).
    no_eeo = record(name_box, {"value": "Test Person", "source": "model", "confidence": "high"})
    no_eeo["fields"] = [f for f in no_eeo["fields"] if f["ref"] != "e1"]
    for k in ("categories", "plan", "results", "snapshot"):
        no_eeo[k].pop("e1", None)
    v = gate_run(no_eeo, named_prof)
    check(v["content_verdict"] == gate.WOULD_STOP and any("name lines only" in x for x in v["content_reasons"]),
          f"the gate: a model fill in a marked Name box stops on a page with no EEO question too ({v['content_reasons']})")
    v = gate_run(record({"ref": "n1", "type": "text", "label": "Signature", "required": True},
                        {"value": "Test Person", "source": "model", "confidence": "high"}), named_prof)
    check(v["content_verdict"] == gate.WOULD_STOP and any("nothing signs for you" in x for x in v["content_reasons"]),
          f"the gate: a filled box that signs stops, whatever planned it ({v['content_reasons']})")
    v = gate_run(record(name_box, {"value": "Test Person", "source": signdate.NAME_SOURCE, "fit": "verbatim",
                                   "profile_path": "personal.first_name"}, category="eeo"), named_prof)
    check(v["content_verdict"] == gate.WOULD_SUBMIT and sum(1 for t in v["trace"] if t.get("ref") == "n1") == 1,
          f"the gate: the Name box the classifier called eeo passes on the recomputed name, traced once ({v['content_reasons']})")
    check.done()


def test_the_gate_takes_a_clock_fill_only_when_it_recomputes_the_same_value():
    # Fails on 2d0c2ee: a plan with source "clock" was "not traced" and stopped.
    check = Checks()
    month = {"ref": "m1", "type": "select", "label": "Month", "options": ["Select", "10", "11", "12"],
             "signature_block": True, "required": True}
    eeo = {"ref": "e1", "type": "select", "label": "Are you a protected veteran?", "options": ["Yes", "No", "I don't wish to answer"]}

    def record(value="11", block=True, with_eeo=True):
        r = base()
        if not with_eeo:
            # base() holds a Gender select (an EEO question): drop it.
            r["fields"] = [f for f in r["fields"] if f["ref"] != "f3"]
            for key in ("plan", "categories", "snapshot", "results"):
                r[key].pop("f3", None)
        r["fields"].append(dict(month, signature_block=block))
        if with_eeo:
            r["fields"].append(eeo)
            r["categories"]["e1"] = "eeo"
            r["plan"]["e1"] = {"value": "I don't wish to answer", "source": "profile", "profile_path": "eeo.veteran_status", "fit": "decline"}
            r["results"]["e1"] = {"filled": True, "unconfirmed": False}
            r["snapshot"]["e1"] = "I don't wish to answer"
        r["categories"]["m1"] = "eeo"
        r["plan"]["m1"] = {"value": value, "source": "clock", "fit": "exact", "profile_path": None}
        r["results"]["m1"] = {"filled": True, "unconfirmed": False}
        r["snapshot"]["m1"] = value
        return r

    with mock.patch.object(signdate, "_today", return_value=TODAY):
        v = gate_run(record())
        check(v["content_verdict"] == gate.WOULD_SUBMIT, f"today's month from the clock, in a signature block on an EEO page: passes ({v['content_reasons']})")
        v = gate_run(record(value="10"))
        check(v["content_verdict"] == gate.WOULD_STOP and any("not recomputed from the clock" in x for x in v["content_reasons"]),
              f"another month: not the clock's, stops ({v['content_reasons']})")
        v = gate_run(record(block=False))
        check(v["content_verdict"] == gate.WOULD_STOP, "outside a signature block: stops")
        v = gate_run(record(with_eeo=False))
        check(v["content_verdict"] == gate.WOULD_STOP, "no EEO question on the page: stops")
    check(signdate.value_for({"label": "Month", "type": "select", "options": ["11", "November"]}, TODAY) is None,
          "two options equal to the part: nothing chosen")
    check(signdate.date_part("Date of birth") is None and signdate.date_part("Start date") is None
          and signdate.date_part("Signature date") == "date" and signdate.date_part("Today's date") == "date",
          "a date part is the label's one date word with signature / today words only")
    check.done()


if __name__ == "__main__":
    run_as_script(globals())
