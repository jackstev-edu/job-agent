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


SOFT = "­"

# One or more per pattern family: (label, options or None).
GUARDED = [
    ("Are you legally authorized to work in the US?", None),
    ("Do you have the right to work in the UK?", None),
    ("Will you require visa sponsorship?", None),
    ("Current work visa", None),
    ("Do you hold an H-1B?", None),
    ("H1B status", None),
    ("H‑1B status", None),            # non-breaking hyphen
    ("H–1B holder", None),            # en dash
    ("Will we need to file a petition for you?", None),
    ("Country of citizenship", None),
    ("Are you a permanent resident?", None),
    (f"Spon{SOFT}sorship required?", None),
    ("Which applies to you?", ["I am a U.S. citizen", "I will require sponsorship"]),
    ("Gender", None), ("Sex", None), ("Race", None), ("Ethnicity", None),
    ("Race for reporting purposes", None), ("Race for EEO-1 / statistical purposes", None),
    # A false positive, kept: a guard is never narrowed to let a project
    # name through.
    ("Race to the finish", None),
    ("Are you Hispanic or Latino?", None), ("Latinx", None),
    ("Are you a protected veteran?", None), ("Veteran status", None),
    ("Do you have a disability?", None), ("I am disabled", None),
    ("Sexual orientation", None), ("Do you identify as transgender?", None),
    ("Pronouns", None),
    ("Desired salary", None), ("Pay expectations", None), ("Hourly wage", None),
    ("Total compensation", None), ("Expected remuneration", None),
    ("Expected earnings", None), ("Desired income", None), ("OTE", None),
    ("Current CTC", None), ("Desired hourly rate", None),
]

NOT_GUARDED = ["PayPal email", "Start date", "Embrace change", "Sussex", "Shipping address",
               "Previous employer", "Years of experience", "Desired start date",
               "Portfolio URL"]


def test_every_pattern_family_matches():
    check = Checks()
    for label, options in GUARDED:
        field = {"label": label, **({"options": options} if options else {})}
        check(sensitive.legal_guard(field) is not None,
              f"guarded: {ascii(label)}" + (f" with options {options}" if options else ""))
    check.done()


def test_ordinary_questions_are_left_alone():
    check = Checks()
    for label in NOT_GUARDED:
        check(sensitive.legal_guard({"label": label}) is None, f"not guarded: {label!r}")
    check.done()


CHECKBOXES = """<!doctype html><html><body><form>
  <label><input type="checkbox" name="vet" id="vet"> I am a protected veteran</label>
  <fieldset><legend>Race</legend>
    <label><input type="checkbox" name="race[]" value="w" id="r1"> White</label>
    <label><input type="checkbox" name="race[]" value="a" id="r2"> Asian</label>
    <label><input type="checkbox" name="race[]" value="b" id="r3"> Black</label></fieldset>
  <label><input type="checkbox" name="auth" id="auth"> I am authorized to work in the United States</label>
  <label><input type="checkbox" name="reloc" id="reloc"> I am willing to relocate if the pay is right</label>
</form></body></html>"""

CHECKBOX_PROFILE_LINES = ("  willing_to_relocate: true\n"
                          "eeo:\n  gender: Decline to self-identify\n"
                          "  veteran_status: Prefer not to say\n"
                          "  race_ethnicity: Prefer not to say\n")


def test_checkboxes_take_only_a_real_true_or_false_from_the_same_area():
    import apply
    from _fill_run import PROFILE, run_fill

    check = Checks()
    form = TEST_HOME / "legal_checkboxes.html"
    form.write_text(CHECKBOXES, encoding="utf-8")
    profile = PROFILE.replace("eeo:\n  gender: Decline to self-identify\n",
                              CHECKBOX_PROFILE_LINES)
    pages, ticked = [], []
    real_open = apply.browser_mod.open_page

    def open_page(ctx, url):
        page = real_open(ctx, url)
        pages.append(page)
        return page

    def you(*a):
        ticked.append({i: pages[-1].is_checked(f"#{i}")
                       for i in ("vet", "r1", "r2", "r3", "auth", "reloc")})
        return "n"
    text, records, error = run_fill(
        form, dry_run=False, profile_yaml=profile,
        classify=lambda fields: {f["ref"]: "personal_fact" for f in fields},
        patches=((apply.browser_mod, "open_page", open_page), (apply, "input", you)))
    print(text[-900:])
    check(error is None and ticked, f"the run finished ({error})")
    final = ticked[-1] if ticked else {}
    check(final.get("vet") is False,
          "'I am a protected veteran' with veteran_status 'Prefer not to say': not ticked")
    check(not any(final.get(i) for i in ("r1", "r2", "r3")),
          "a race[] list with race_ethnicity 'Prefer not to say': no box ticked")
    check(final.get("auth") is True,
          "a true authorized_to_work_us line and a work-authorization box: ticked")
    check(final.get("reloc") is False,
          "a pay question whose closest line is eligibility.willing_to_relocate: not ticked")
    rec = records[-1] if records else {}
    by_label = {f.get("label"): f["ref"] for f in rec.get("fields", [])}
    flagged = {fl["ref"] for fl in rec.get("flags") or []}
    held = {by_label.get(k) for k in ("I am a protected veteran", "White", "Asian", "Black",
                                      "I am willing to relocate if the pay is right")}
    check(None not in held and held <= flagged, "  ...and every one of those came to you")
    check.done()


def test_the_gate_stops_on_a_verbatim_fit_for_a_guarded_question():
    import test_gate as tg

    check = Checks()
    rec = copy.deepcopy(tg.base())
    rec["fields"].append({"ref": "f9", "type": "text", "label": "Wage expectations"})
    rec["categories"]["f9"] = "compensation"
    rec["plan"]["f9"] = {"value": "x", "source": "profile",
                         "profile_path": "compensation.desired_base_min", "fit": "verbatim"}
    rec["results"]["f9"] = {"filled": True, "detail": "typed"}
    rec["snapshot"]["f9"] = "x"
    tg.stops(check, rec, "matched the options loosely (verbatim)",
             "a guarded question answered verbatim from its profile line")
    check.done()


class _Profile:
    def __init__(self, data):
        self.data = data

    def get(self, path):
        return self.data.get(path)


def test_a_typed_box_or_a_two_area_box_comes_to_you():
    check = Checks()
    prof = _Profile({"eligibility.authorized_to_work_us": True,
                     "eeo.veteran_status": False})
    v = sensitive.decide({"label": "Are you authorized to work in the US?", "type": "text"},
                         "personal_fact", prof)
    check(v["action"] == "human",
          f"a guarded box to type in, with a true/false line in the same area: yours ({v['action']})")
    two = {"label": "I am authorized to work in the US and am not a protected veteran",
           "type": "checkbox"}
    check(sorted(set(sensitive.legal_guard_kinds(two))) == ["eeo", "work_authorization"],
          "the two-area box matches both areas (so the next check means something)")
    v = sensitive.decide(two, "personal_fact", prof)
    check(v["action"] == "human" and "more than one kind" in v.get("reason", ""),
          f"a checkbox asking two legal things at once: yours, not ticked ({v['action']})")
    one = {"label": "I am authorized to work in the US", "type": "checkbox"}
    check(sensitive.decide(one, "personal_fact", prof)["action"] == "fill",
          "the one-area box is still ticked from its line (so the refusal above is the two areas')")
    check.done()


def _gate_record(label, category, plan):
    import test_gate as tg

    rec = copy.deepcopy(tg.base())
    rec["fields"].append({"ref": "f9", "type": "text", "label": label})
    rec["categories"]["f9"] = category
    rec["plan"]["f9"] = plan
    rec["results"]["f9"] = {"filled": True, "detail": "typed"}
    rec["snapshot"]["f9"] = plan.get("value")
    return rec


def test_the_gate_holds_a_guarded_field_to_the_policy():
    import test_gate as tg

    check = Checks()
    rec = _gate_record("Wage expectations", "personal_fact",
                       {"value": "x", "source": "profile", "fit": "verbatim",
                        "profile_path": "compensation.desired_base_min"})
    tg.stops(check, rec, "matched the options loosely (verbatim)",
             "classifier said personal_fact, a verbatim fit: the guard alone stops it")
    rec = _gate_record("Are you a protected veteran?", "personal_fact",
                       {"value": "No", "source": "profile", "fit": "exact",
                        "profile_path": "eligibility.authorized_to_work_us"})
    tg.stops(check, rec, "which isn't a eeo profile line",
             "an EEO question answered from an eligibility line: stops")
    rec = _gate_record("Are you a protected veteran?", "personal_fact",
                       {"value": "No", "source": "profile", "fit": "exact",
                        "profile_path": "eeo.veteran_status"})
    check(tg.run(rec)["content_verdict"] == "would_submit",
          "the same answer from its own eeo line passes (so the stops above mean something)")
    check.done()


if __name__ == "__main__":
    run_as_script(globals())
