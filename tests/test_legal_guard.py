"""
test_legal_guard.py — a legal question never reaches the model, whatever the classifier says.

WHAT THIS DOES: Runs the real `fill` with the classifier stubbed to call
every field personal_fact. A sponsorship, an EEO and a salary question must
never appear in the mapper's input: each is answered from a named profile
line with a strict fit, or comes to you. Then the gate, given a record
where an EEO or citizenship question was model-answered, must stop.
WHY IT MATTERS: The model never answers legal questions, and a field must
clear both the classifier and the local backstop before the model sees it.
The backstop can't depend on the classifier getting the category right.

Run from anywhere:  .venv/Scripts/python.exe tests/test_legal_guard.py
"""
import copy
import json
from pathlib import Path

from _harness import TEST_HOME, Checks, run_as_script
from agent import sensitive

FORM = """<!doctype html><html><body><form>
  <label for="fn">First Name</label><input id="fn" name="first_name">
  <label for="sp">Will you require sponsorship?</label>
  <select id="sp" name="sp"><option></option><option>Yes</option><option>No</option></select>
  <label for="ge">Gender</label>
  <select id="ge" name="ge"><option></option><option>Male</option><option>Female</option>
    <option>Decline to self-identify</option></select>
  <label for="sa">Desired salary</label><input id="sa" name="sa">
</form></body></html>"""

LEGAL = ("Will you require sponsorship?", "Gender", "Desired salary")


def test_legal_questions_never_reach_the_mapper():
    from _fill_run import PROFILE, map_from_profile, run_fill

    check = Checks()
    form = TEST_HOME / "legal_guard.html"
    form.write_text(FORM, encoding="utf-8")
    profile = PROFILE.replace("  authorized_to_work_us: true\n",
                              "  authorized_to_work_us: true\n  requires_sponsorship_now: false\n")
    mapped: list = []

    def everything_ordinary(fields):
        return {f["ref"]: "personal_fact" for f in fields}

    def map_fields(fields, prof, page_text=""):
        mapped.extend(f.get("label") for f in fields)
        return map_from_profile(fields, prof, page_text)
    text, records, error = run_fill(form, dry_run=False, profile_yaml=profile,
                                    classify=everything_ordinary, map_fields=map_fields)
    print(text[-700:])
    check(error is None, f"the run finished ({error})")
    check("First Name" in mapped, "an ordinary question still went to the mapper "
                                  "(so the next check means something)")
    check(not set(LEGAL) & set(mapped), f"no legal question reached the mapper ({mapped})")
    rec = records[-1] if records else {}
    by_label = {f["label"]: f["ref"] for f in rec.get("fields", [])}
    plan, flagged = rec.get("plan") or {}, {fl["ref"] for fl in rec.get("flags") or []}
    sp, ge, sa = (plan.get(by_label.get(k)) or {} for k in LEGAL)
    check(sp.get("source") == "profile" and sp.get("value") == "No" and sp.get("fit") == "bool"
          and sp.get("profile_path") == "eligibility.requires_sponsorship_now",
          f"sponsorship: answered from its profile line, a strict fit ({sp})")
    check(ge.get("source") == "profile" and ge.get("value") == "Decline to self-identify"
          and ge.get("fit") == "exact", f"gender: answered from its profile line ({ge})")
    check(by_label.get("Desired salary") in flagged and not sa,
          "salary: no profile line, so it came to you, unanswered")
    check.done()


def test_the_gate_stops_on_a_model_answered_legal_question():
    import test_gate as tg

    check = Checks()
    for label in ("Pronouns", "Country of citizenship", "Wage expectations"):
        check(sensitive.legal_guard({"label": label}) is not None,
              f"'{label}' matches the guard (and no older rule names its profile line)")
        rec = copy.deepcopy(tg.base())
        rec["fields"].append({"ref": "f9", "type": "text", "label": label})
        rec["categories"]["f9"] = "personal_fact"
        rec["plan"]["f9"] = {"value": "x", "source": "model", "confidence": "high",
                             "needs_review": False, "review_reasons": []}
        rec["results"]["f9"] = {"filled": True, "detail": "typed"}
        rec["snapshot"]["f9"] = "x"
        tg.stops(check, rec, "not traced to a profile line", f"'{label}', model-answered")
    check(tg.run(tg.base())["content_verdict"] == "would_submit",
          "the record without it would submit (so the stops above are the guard's)")
    check.done()


if __name__ == "__main__":
    run_as_script(globals())
