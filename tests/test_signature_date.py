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
        check(_values(page) == {"nm": "", "mo": "11", "dy": "2", "yr": "2026", "dob": ""},
              f"the selects hold today's parts; Name and Date of birth are empty ({_values(page)})")
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
        check(_values(page) == {"nm": "", "mo": "11", "dy": "02", "yr": "2026", "dob": ""},
              f"MM / DD / YYYY placeholders: 2 / 2 / 4 digits ({_values(page)})")
    with mock_page(_page("numeric", NUMERIC)) as page:
        fill = _fill(page, prof, [])
        check(_values(page) == {"nm": "", "mo": "11", "dy": "02", "yr": "2026", "dob": ""},
              f"inputmode=numeric: 2 / 2 / 4 digits ({_values(page)})")
    with mock_page(_page("one", ONE_DATE)) as page:
        fill = _fill(page, prof, [])
        check(_values(page) == {"nm": "", "dt": "11/02/2026", "dob": ""}, f"one Date box in its placeholder's format ({_values(page)})")
    with mock_page(_page("bare", BARE_DATE)) as page:
        fill = _fill(page, prof, [])
        by = {f["name"]: f for f in fill.fields}
        flag = next((fl for fl in fill.flags if fl.get("ref") == by["sig_date"]["ref"]), None)
        check(_values(page) == {"nm": "", "dt": "", "dob": ""} and flag and flag.get("source") == "human",
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
    ]
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
        check(v["content_verdict"] == gate.WOULD_STOP, "the gate: the raw legal flag stops a clock fill")
        v = gate_run(record(dict(month), category="credential"))
        check(v["content_verdict"] == gate.WOULD_STOP, "the gate: a credential category stops a clock fill")
        v = gate_run(record(dict(month, name="disability_signature_month")))
        check(v["content_verdict"] == gate.WOULD_SUBMIT, f"the gate: a name holding 'disability' is not the box's own text ({v['content_reasons']})")
    # The positive Jack asked for: a plain Date box on the disability
    # self-identification form, its name carrying the form's word, fills.
    with mock_page(_page("named", NAMED_DATE)) as page:
        fill = _fill(page, prof, [])
        check(_values(page).get("dt") == "11/02/2026", f"a plain Date box on the self-identification form still fills ({_values(page)})")
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
