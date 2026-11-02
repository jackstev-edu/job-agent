"""
test_checkbox_groups.py — EEO checkbox groups as one choice field (Jack, 2026-11-02, item 5).

WHAT THIS DOES: On a generic self-identification mock (a fieldset asking
about a disability with three checkboxes whose labels are answer
statements), fills the page through pipeline.fill_page with
eeo.disability_status false, a decline line and true, and shows that
exactly the matched box is ticked and the rest left unticked, that the
gate passes the group's boxes in place of the census consent rule, and
that a second box ticked by hand stops it; that a lone checkbox whose
label is an answer statement goes to Jack; and (Jack's guard) that a lone
checkbox whose label is a question keeps today's bool fit.
WHY IT MATTERS: Jack's Workday run (2026-11-02) planned all three boxes
False from one bool line, unticked "No, I do not have a disability" with
the rest, and the census then stopped on each box. The statements are
the options; the line answers one of them.

Run from anywhere:  .venv/Scripts/python.exe tests/test_checkbox_groups.py
"""
import io
from contextlib import redirect_stdout
from unittest import mock

from _fill_run import PROFILE, map_from_profile
from _harness import REPO, TEST_HOME, Checks, mock_page, run_as_script
from agent import extract, filler, gate, pipeline, sensitive
from agent.profile import Profile
import yaml

FIXTURE = REPO / "tests" / "checkbox_group.html"
URL = "https://careers.example-co.com/apply/self-identification"
LONE_STATEMENT = '<input type="checkbox" id="lone" name="lone_disability"><label for="lone">I have a disability</label>'
LONE_QUESTION = '<input type="checkbox" id="q" name="has_disability"><label for="q">Do you have a disability?</label>'


def _classify(fields):
    return {f["ref"]: ("eeo" if "disabilit" in (f.get("label") or "").lower() else "personal_fact") for f in fields}


def _prof(name, disability):
    text = PROFILE.replace("eeo:\n  gender: Decline to self-identify\n", f"eeo:\n  disability_status: {disability}\n")
    assert "disability_status" in text
    p = TEST_HOME / f"groups-{name}.yaml"
    p.write_text(text, encoding="utf-8")
    return Profile(yaml.safe_load(text), p)


def _page(lone=""):
    p = TEST_HOME / f"checkbox-group-{abs(hash(lone))}.html"
    p.write_text(FIXTURE.read_text(encoding="utf-8").replace("LONE", lone), encoding="utf-8")
    return p


def _fill(page, prof):
    with mock.patch("agent.classify.classify_fields", _classify), \
            mock.patch("agent.mapper.map_fields", map_from_profile), redirect_stdout(io.StringIO()):
        return pipeline.fill_page(page, prof, guard=extract.ReadGuard(), resume=None, posting_text="")


def _ticks(page):
    return page.evaluate("() => Object.fromEntries([...document.querySelectorAll('input[type=checkbox]')].map(b => [b.id, b.checked]))")


def _gate(page, fill, prof):
    record = pipeline.build_record(page, URL, "generic", "default", "", [fill.step], fill.events, dry_run=False)
    return gate.decide(record, prof)


def test_the_scan_makes_the_three_statement_boxes_one_choice_field():
    # Fails on 2c3f0b8: three lone checkbox fields.
    check = Checks()
    with mock_page(_page(LONE_STATEMENT + LONE_QUESTION)) as page:
        fields = extract.extract_fields(page)
        groups = [f for f in fields if f.get("widget") == "checkboxes"]
        lone = [f for f in fields if f.get("type") == "checkbox"]
        check(len(groups) == 1 and groups[0]["type"] == "radio" and len(groups[0]["options"]) == 3
              and groups[0]["options"][1].startswith("No, I do not have")
              and groups[0]["label"].startswith("Do you have a disability?") and len(groups[0]["option_refs"]) == 3,
              f"one group with the statements as options and the legend as its question ({groups})")
        check({f["label"] for f in lone} == {"I have a disability", "Do you have a disability?"},
              f"the two boxes outside the fieldset stay lone checkboxes ({[f['label'] for f in lone]})")
        check(sensitive.is_statement_label("Yes, I have a disability") and sensitive.is_statement_label("I do not want to answer")
              and sensitive.is_statement_label("No, I do not have a disability") and sensitive.is_statement_label("Prefer not to say")
              and not sensitive.is_statement_label("Do you have a disability?") and not sensitive.is_statement_label("Disability status")
              and not sensitive.is_statement_label("Is this your current employer?"),
              "a statement opens with Yes / No / I or declines; a question does not")
    check.done()


def test_false_ticks_the_no_statement_only_and_the_gate_passes_the_group():
    # Fails on 2c3f0b8: the bool False unticked all three, the "No" box included,
    # and the census stopped on each.
    check = Checks()
    prof = _prof("false", "false")
    with mock_page(_page()) as page:
        fill = _fill(page, prof)
        group = next(f for f in fill.fields if f.get("widget") == "checkboxes")
        decision = fill.plan.get(group["ref"])
        check(decision and decision["source"] == "profile" and decision["fit"] == "bool"
              and decision["value"].startswith("No, I do not have"), f"the line fits the No statement, strictly ({decision})")
        check(_ticks(page) == {"y": False, "n": True, "d": False}, f"exactly the No box is ticked ({_ticks(page)})")
        check(fill.snapshot.get(group["ref"]) == decision["value"], "the read-back gives the chosen statement")
        result = _gate(page, fill, prof)
        check(result["content_verdict"] == gate.WOULD_SUBMIT, f"the gate passes the group's boxes ({result['content_reasons']})")
        page.check("#y")                                  # a second box ticked by hand
        record = pipeline.build_record(page, URL, "generic", "default", "", [fill.step], fill.events, dry_run=False)
        stopped = gate.decide(record, prof)
        check(stopped["content_verdict"] == gate.WOULD_STOP
              and any("ticked differently from the group's planned answer" in r for r in stopped["content_reasons"]),
              f"two boxes ticked: the gate stops ({stopped['content_reasons']})")
        page.uncheck("#y"); page.uncheck("#n")            # none ticked
        record = pipeline.build_record(page, URL, "generic", "default", "", [fill.step], fill.events, dry_run=False)
        stopped = gate.decide(record, prof)
        check(stopped["content_verdict"] == gate.WOULD_STOP, f"the planned box unticked: the gate stops ({stopped['content_reasons']})")
    check.done()


def test_a_decline_line_ticks_the_decline_statement_only_and_true_ticks_yes_only():
    # Fails on 2c3f0b8: a decline value is not a bool, so no box was planned at all.
    check = Checks()
    with mock_page(_page()) as page:
        prof = _prof("decline", "Prefer not to say")
        fill = _fill(page, prof)
        group = next(f for f in fill.fields if f.get("widget") == "checkboxes")
        decision = fill.plan.get(group["ref"])
        check(decision and decision["fit"] == "decline" and decision["value"] == "I do not want to answer"
              and _ticks(page) == {"y": False, "n": False, "d": True},
              f"the decline line ticks 'I do not want to answer' only ({decision}, {_ticks(page)})")
        check(_gate(page, fill, prof)["content_verdict"] == gate.WOULD_SUBMIT, "the gate passes")
    with mock_page(_page()) as page:
        prof = _prof("true", "true")
        fill = _fill(page, prof)
        group = next(f for f in fill.fields if f.get("widget") == "checkboxes")
        decision = fill.plan.get(group["ref"])
        check(decision and decision["fit"] == "bool" and decision["value"].startswith("Yes, I have")
              and _ticks(page) == {"y": True, "n": False, "d": False},
              f"true ticks the Yes statement only ({decision}, {_ticks(page)})")
        check(_gate(page, fill, prof)["content_verdict"] == gate.WOULD_SUBMIT, "the gate passes")
    check(sensitive._fit_group(False, ["No", "No, never", "Yes"]) == (None, None)
          and sensitive._fit_group("Maybe", ["Yes", "No"]) == (None, None)
          and sensitive._fit_group(True, ["Yes, I do", "No, I don't"]) == ("Yes, I do", "bool"),
          "two No statements, or a value no statement equals, fit nothing; never a substring guess")
    check.done()


def test_a_lone_statement_checkbox_goes_to_jack_and_a_lone_question_box_keeps_the_bool_fit():
    # Fails on 2c3f0b8 for the statement box: fit_with_kind returned the bool
    # for any checkbox, so "I have a disability" was planned False from the
    # line. The question box is Jack's guard (2026-11-02): it passes on
    # 2c3f0b8 too, and keeps passing.
    check = Checks()
    prof = _prof("lone", "true")
    with mock_page(_page(LONE_STATEMENT + LONE_QUESTION)) as page:
        fill = _fill(page, prof)
        by = {f["label"]: f for f in fill.fields}
        statement, question = by["I have a disability"], by["Do you have a disability?"]
        flag = next((fl for fl in fill.flags if fl.get("ref") == statement["ref"]), None)
        check(statement["ref"] not in fill.plan and flag and flag.get("source") == "human",
              f"the lone statement box is Jack's ({flag and flag.get('reason')})")
        q = fill.plan.get(question["ref"])
        check(q and q["fit"] == "bool" and q["value"] is True and q["source"] == "profile",
              f"the lone question box keeps today's bool fit from the line ({q})")
        ticks = _ticks(page)
        check(ticks["q"] is True and ticks["lone"] is False and ticks["y"] is True and ticks["n"] is False,
              f"the question box ticked from true, the statement box untouched, the group's Yes ticked ({ticks})")
        check(sensitive.fit_with_kind(True, {"type": "checkbox", "label": "I have a disability"}) == (None, None)
              and sensitive.fit_with_kind(True, {"type": "checkbox", "label": "Do you have a disability?"}) == (True, "bool")
              and sensitive.fit_with_kind("Prefer not to say", {"type": "checkbox", "label": "Do you have a disability?"}) == (None, None),
              "fit_with_kind: a statement label fits nothing, a question label takes a real bool only")
    check.done()


def test_the_filler_refuses_a_group_whose_box_changed_its_question():
    check = Checks()
    with mock_page(_page()) as page:
        fields = extract.extract_fields(page)
        group = next(f for f in fields if f.get("widget") == "checkboxes")
        # One box relabelled to a background-check consent since the scan.
        page.evaluate("() => { document.querySelector('label[for=n]').textContent = 'I consent to a background check'; }")
        worked, detail = filler.fill_one(page, group, group["options"][1])
        check(worked is False and detail.startswith("refused") and _ticks(page) == {"y": False, "n": False, "d": False},
              f"a box that reads differently now refuses the whole group, nothing ticked ({detail})")
        check(filler.fill_one(page, group, "Something else")[0] is False, "a value that is no statement is refused")
    check.done()


if __name__ == "__main__":
    run_as_script(globals())
