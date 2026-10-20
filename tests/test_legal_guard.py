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
from agent import sensitive, template

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
        rec["results"]["f9"] = {"filled": True, "detail": "typed", "unconfirmed": False}
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
    ("H" + chr(0x2212) + "1B status", None),    # minus sign
    ("H" + chr(0x2043) + "1B status", None),    # hyphen bullet
] + [
    # One per class of invisible character now stripped: a direction mark,
    # a bidi embedding and an isolate, the Mongolian vowel separator, a tag
    # character, a variation selector, the combining grapheme joiner and an
    # invisible operator.
    (f"Spon{c}sorship required?", None)
    for c in (chr(0x200E), chr(0x202A), chr(0x2066), chr(0x180E), chr(0xE0041),
              chr(0xFE0F), chr(0x034F), chr(0x2062))
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


# Each field in its own wrapper, as most portals lay them out. With every
# label a direct child of the form, filler's click-time label check (which
# always reads the nearby legend / label fallback) picks up the form's first
# label for each box, so a box that was answered correctly comes to you
# instead: a false positive, accepted rather than narrowing that check
# (pinned in tests/test_filler_relabel.py).
CHECKBOXES = """<!doctype html><html><body><form>
  <div><label><input type="checkbox" name="vet" id="vet"> I am a protected veteran</label></div>
  <fieldset><legend>Race</legend>
    <label><input type="checkbox" name="race[]" value="w" id="r1"> White</label>
    <label><input type="checkbox" name="race[]" value="a" id="r2"> Asian</label>
    <label><input type="checkbox" name="race[]" value="b" id="r3"> Black</label></fieldset>
  <div><label><input type="checkbox" name="auth" id="auth"> I am authorized to work in the United States</label></div>
  <div><label><input type="checkbox" name="reloc" id="reloc"> I am willing to relocate if the pay is right</label></div>
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
    rec["results"]["f9"] = {"filled": True, "detail": "typed", "unconfirmed": False}
    rec["snapshot"]["f9"] = "x"
    tg.stops(check, rec, "matched the options loosely (verbatim)",
             "a guarded question answered verbatim from its profile line")
    check.done()


class _Profile:
    def __init__(self, data):
        self.data = data

    def get(self, path):
        return self.data.get(path)


def test_which_sponsorship_question_it_is_decides_which_line_answers_it():
    check = Checks()
    prof = _Profile({"eligibility.requires_sponsorship_now": False,
                     "eligibility.requires_sponsorship_future": True})

    def ask(label):
        return sensitive.decide({"label": label, "type": "select",
                                 "options": ["Yes", "No"]}, "work_authorization", prof)

    # Asks about one time only, but matches both lines: the word it uses decides.
    only_future = "Will you at any time in the future require visa sponsorship?"
    check(sensitive.all_profile_lines(only_future) ==
          {"eligibility.requires_sponsorship_now", "eligibility.requires_sponsorship_future"},
          "the future-only wording matches both lines (so the next check means something)")
    v = ask(only_future)
    check(v["action"] == "fill"
          and v.get("profile_path") == "eligibility.requires_sponsorship_future"
          and v["value"] == "Yes",
          f"it says future and not now: answered from the future line ({v.get('profile_path')}, "
          f"{v.get('value')!r})")

    # One portal's wording asks about both times at once: still yours.
    both = "Will you now or in the future require sponsorship?"
    v = ask(both)
    check(v["action"] == "human" and "more than one profile line" in v.get("reason", ""),
          f"'now or in the future' asks about both: still yours ({v['action']})")

    # That portal's other wording, and a third line in the mix, are unchanged.
    v = ask("Do you require sponsorship to begin employment with us?")
    check(v["action"] == "fill"
          and v.get("profile_path") == "eligibility.requires_sponsorship_now"
          and v["value"] == "No",
          f"'to begin employment' matches the now line alone, as before ({v.get('profile_path')})")
    v = ask("Will you now or in the future require sponsorship for employment visa status?")
    check(v["action"] == "human" and "more than one profile line" in v.get("reason", ""),
          f"a third line in the question keeps it yours ({v['action']})")

    # The rule itself, on each combination of the two words.
    pair = {"eligibility.requires_sponsorship_now", "eligibility.requires_sponsorship_future"}
    for text, want in (("sponsorship in the future", "eligibility.requires_sponsorship_future"),
                       ("sponsorship to begin now", "eligibility.requires_sponsorship_now"),
                       ("sponsorship now or in the future", None),
                       ("sponsorship", None)):
        check(sensitive.sponsorship_line(text, pair) == want,
              f"'{text}' -> {want or 'yours'}")
    check(sensitive.sponsorship_line("sponsorship in the future", pair | {"eeo.gender"}) is None,
          "and it decides nothing when the question touches another line too")
    check.done()


def test_a_typed_box_or_a_two_area_box_comes_to_you():
    check = Checks()
    prof = _Profile({"eligibility.authorized_to_work_us": True,
                     "eeo.veteran_status": False})
    v = sensitive.decide({"label": "Are you authorized to work in the US?", "type": "text"},
                         "personal_fact", prof)
    check(v["action"] == "human" and "box to type in" in (v.get("reason") or ""),
          f"a guarded box to type in, with a true/false line in the same area: yours "
          f"({v['action']}, {v.get('reason')})")
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


def test_an_identifier_naming_a_sibling_line_does_not_make_the_question_ambiguous():
    # Greenhouse's EEO select, as its board page serves it (scanned 2026-10-20):
    # the label names the Hispanic/Latino line, the field name adds the
    # race/ethnicity one. The question's line answers it.
    check = Checks()
    gh = {"label": "Are you Hispanic/Latino?", "help_text": "Select...", "name": "hispanic_ethnicity",
          "type": "combobox", "options": ["Yes", "No", "Decline To Self Identify"]}
    check(sorted(sensitive.all_profile_lines(sensitive._question_text(gh))) == ["eeo.hispanic_latino", "eeo.race_ethnicity"],
          "the name adds the race/ethnicity line (so the next checks mean something)")
    v = sensitive.decide(gh, "eeo", _Profile({"eeo.hispanic_latino": "Prefer not to say"}))
    check(v["action"] == "fill" and v["value"] == "Decline To Self Identify" and v["fit"] == "decline"
          and v["profile_path"] == "eeo.hispanic_latino",
          f"answered from the Hispanic/Latino line with the decline fit ({v['action']}, {v.get('value')}, {v.get('reason')})")
    v = sensitive.decide(gh, "personal_fact", _Profile({"eeo.hispanic_latino": "No"}))
    check(v["action"] == "fill" and v["value"] == "No" and v["fit"] == "exact",
          f"whatever the classifier said, and an exact fit ({v['action']}, {v.get('value')})")
    v = sensitive.decide(gh, "eeo", _Profile({}))
    check(v["action"] == "human" and "blank in profile.yaml" in v["reason"], "blank line: still yours")
    both = {**gh, "label": "Race / Ethnicity: are you Hispanic or Latino?"}
    v = sensitive.decide(both, "eeo", _Profile({"eeo.hispanic_latino": "No"}))
    check(v["action"] == "human" and "more than one profile line" in v["reason"],
          f"a question naming two lines itself is still yours ({v['action']})")
    other_area = {**gh, "name": "salary_hispanic"}
    v = sensitive.decide(other_area, "eeo", _Profile({"eeo.hispanic_latino": "No"}))
    check(v["action"] == "human" and "more than one" in v["reason"],
          f"an identifier from another area keeps it yours ({v['action']}, {v.get('reason')})")
    itar = {**gh, "name": "itar-hispanic"}      # another area's line (export control), no second guard kind
    check(sorted(sensitive.all_profile_lines(sensitive._question_text(itar)))
          == ["eeo.hispanic_latino", "eligibility.export_control_us_person"], "the identifier adds an eligibility line")
    v = sensitive.decide(itar, "eeo", _Profile({"eeo.hispanic_latino": "No"}))
    check(v["action"] == "human" and "more than one profile line" in v["reason"],
          f"an identifier naming a line from another area keeps it yours ({v['action']}, {v.get('reason')})")
    # The fit itself: a decline value takes the decline option, never "No" through "not".
    check(sensitive.fit_with_kind("Prefer not to say", gh) == ("Decline To Self Identify", "decline")
          and sensitive.fit_with_kind("I don't wish to answer", gh) == ("Decline To Self Identify", "decline")
          and sensitive.fit_with_kind("Decline to self-identify.", gh) == ("Decline To Self Identify", "decline")
          and sensitive.fit_with_kind("No", gh) == ("No", "exact"),
          f"a decline value fits the decline option ({sensitive.fit_with_kind('Prefer not to say', gh)})")
    # Only a value that is a decline as a whole: one that merely contains a
    # decline phrase keeps the old (loose, so Jack's) reading.
    tg = {"label": "Do you identify as transgender?", "type": "combobox", "options": ["Yes", "No", "I don't wish to answer"]}
    v = sensitive.decide(tg, "eeo", _Profile({"eeo.transgender": "I do not identify as transgender"}))
    check(sensitive.fit_with_kind("I do not identify as transgender", tg)[1] != "decline" and v["action"] == "human",
          f"a value containing a decline phrase is not a decline ({sensitive.fit_with_kind('I do not identify as transgender', tg)}, {v['action']})")
    export = {"label": "To conform to U.S. Government space technology export regulations, including the "
                       "International Traffic in Arms Regulations (ITAR) you must be a U.S. citizen, lawful "
                       "permanent resident of the U.S., protected individual as defined by 8 U.S.C. 1324b(a)(3), "
                       "or eligible to obtain the required authorizations from the U.S. Department of State. Do you meet this requirement?",
              "help_text": "Select...", "name": "question_9175759005", "type": "combobox", "options": ["Yes", "No"]}
    v = sensitive.decide(export, "work_authorization", _Profile({"eligibility.export_control_us_person": True}))
    check(v["action"] == "fill" and v["value"] == "Yes" and v["profile_path"] == "eligibility.export_control_us_person",
          f"the export-control question is answered from its line once it is filled ({v['action']}, {v.get('reason')})")
    v = sensitive.decide(export, "work_authorization", _Profile({}))
    check(v["action"] == "human" and "`eligibility.export_control_us_person` is blank" in v["reason"],
          f"blank, it names the line to fill ({v.get('reason')})")
    check.done()


def test_every_department_of_defense_question_is_jacks():
    # Jack, 2026-10-20 (second set): the DoD employment line is gone from
    # PROFILE_PATHS and the template; the "dod" guard kind stays and
    # names no line, so every DoD question is his, with any lines filled.
    check = Checks()
    check("eligibility.dod_employment_past_two_years" not in {path for _, path in sensitive.PROFILE_PATHS}
          and "dod_employment" not in template.BLANK_PROFILE,
          "the DoD row is in neither PROFILE_PATHS nor the template")
    check("dod" in {kind for _, kind in sensitive.LEGAL_GUARD} and sensitive.LEGAL_AREAS["dod"] == frozenset(),
          f"the dod guard kind stays and answers from no line ({sensitive.LEGAL_AREAS.get('dod')})")
    prof = _Profile({"eligibility.dod_employment_past_two_years": False, "eligibility.security_clearance": "None",
                     "eligibility.export_control_us_person": True})
    labels = (
        # The line's own question, in the wordings the row used to answer.
        "Have you been employed by the U.S. Department of Defense (civilian or military) in the past two years?",
        "Employed by DoD in the last 2 years?",
        "Within the past two years, were you employed with the Department of Defense?",
        # And every other DoD question.
        "Are you willing to work at a Department of Defense facility?", "Have you ever worked for the DoD?",
        "Have you worked on DoD contracts?", "Are you a current DoD contractor?",
        "Are you interested in working for the Department of Defense?", "Were you employed by the DoD at any time?",
        "Have you been employed by a DoD contractor in the past two years?",
        "Are you currently a DoD employee?", "Do you currently hold a DoD security clearance?",
        "Are you a U.S. person under DoD export rules?")
    for label in labels:
        for extra in ({"type": "radio", "options": ["Yes", "No"]}, {"type": "text", "options": []},
                      {"type": "checkbox", "options": []}):
            field = {"label": label, **extra}
            check("dod" in sensitive.legal_guard_kinds(field), f"{label!r}: the guard sees it")
            for category in ("personal_fact", "work_authorization", "free_text", "per_company"):
                v = sensitive.decide(field, category, prof)
                check(v["action"] == "human" and v["value"] is None,
                      f"{label!r} as {category} ({extra['type']}): Jack's ({v['action']}, {v.get('reason')})")
    # Help text naming the DoD beside another question is Jack's too.
    v = sensitive.decide({"label": "Which site would you work at?", "type": "radio", "options": ["Yes", "No"],
                          "help_text": "Staff employed by the DoD in the past two years must say so."},
                         "personal_fact", prof)
    check(v["action"] == "human", f"DoD in the help text: Jack's ({v['action']}, {v.get('reason')})")
    v = sensitive.decide({"label": "Do you currently hold a DoD security clearance?", "type": "radio",
                          "options": ["Yes", "No"]}, "personal_fact", prof)
    check(v["action"] == "human" and "more than one kind" in v["reason"] and "dod" in v["reason"],
          f"a DoD clearance question reads as two kinds of legal question and stays Jack's ({v.get('reason')})")
    check.done()


def test_a_security_clearance_question_is_answered_only_from_the_clearance_lines():
    # Jack, 2026-10-20 (second set): a "clearance" guard kind. With the
    # lines blank, every clearance question is his; filled, only an exact
    # option or a yes / no from a real true / false answers it; never the
    # model, whatever the classifier called the field.
    check = Checks()
    check(sensitive.LEGAL_AREAS["clearance"] == {"eligibility.security_clearance", "eligibility.clearance_eligible"},
          "the kind answers from the two clearance lines only")
    blank = _Profile({})
    labels = ("Do you hold a Secret clearance?", "Are you eligible to obtain a clearance?",
              "What is your current security clearance level?", "Do you have an active Top Secret clearance?",
              "Clearance eligibility", "Are you eligible for a security clearance?",
              "This role requires TS/SCI. Do you have it?", "Have you ever had a clearance revoked?",
              "Are you willing to obtain a clearance?")
    for label in labels:
        for extra in ({"type": "radio", "options": ["Yes", "No"]}, {"type": "text", "options": []},
                      {"type": "checkbox", "options": []}, {"type": "select", "options": ["None", "Secret", "Top Secret"]}):
            field = {"label": label, **extra}
            check("clearance" in sensitive.legal_guard_kinds(field), f"{label!r}: the guard sees it")
            for category in ("personal_fact", "work_authorization", "free_text", "per_company", "eeo"):
                v = sensitive.decide(field, category, blank)
                check(v["action"] == "human" and v["value"] is None and v["source"] != "model",
                      f"{label!r} as {category} ({extra['type']}), lines blank: Jack's ({v['action']}, {v.get('reason')})")
    # The level held: only an option equal to the profile value.
    held = _Profile({"eligibility.security_clearance": "Secret"})
    level = {"label": "What is your current security clearance level?", "type": "select",
             "options": ["None", "Secret", "Top Secret"]}
    v = sensitive.decide(level, "personal_fact", held)
    check(v["action"] == "fill" and v["value"] == "Secret" and v["fit"] == "exact"
          and v["profile_path"] == "eligibility.security_clearance", f"the level is picked exactly ({v})")
    v = sensitive.decide({"label": "Do you hold a Secret clearance?", "type": "radio", "options": ["Yes", "No"]},
                         "personal_fact", held)
    check(v["action"] == "human" and "Pick one yourself" in v["reason"],
          f"a level is not a yes / no: a hold-a-Secret question is Jack's even with the level filled ({v.get('reason')})")
    v = sensitive.decide({"label": "What is your current security clearance level?", "type": "text", "options": []},
                         "personal_fact", held)
    check(v["action"] == "human" and "box to type in" in v["reason"], f"a typed box is Jack's ({v.get('reason')})")
    v = sensitive.decide({**level, "options": ["None", "Confidential", "Top Secret"]}, "personal_fact", held)
    check(v["action"] == "human", f"no option equals the value: Jack's ({v.get('reason')})")
    # Eligibility: a yes / no from a real true / false, from its own line.
    eligible = _Profile({"eligibility.clearance_eligible": True})
    v = sensitive.decide({"label": "Are you eligible to obtain a clearance?", "type": "radio", "options": ["Yes", "No"]},
                         "personal_fact", eligible)
    check(v["action"] == "fill" and v["value"] == "Yes" and v["fit"] == "bool"
          and v["profile_path"] == "eligibility.clearance_eligible", f"eligibility from its line ({v})")
    v = sensitive.decide({"label": "Are you eligible to obtain a clearance?", "type": "radio", "options": ["Yes", "No"]},
                         "personal_fact", _Profile({"eligibility.clearance_eligible": "Not sure"}))
    check(v["action"] == "human" and "Pick one yourself" in v["reason"],
          f"a value that is neither an option nor a real true / false: Jack's ({v.get('reason')})")
    both = _Profile({"eligibility.security_clearance": "Secret", "eligibility.clearance_eligible": True})
    v = sensitive.decide({"label": "Are you eligible to obtain a security clearance?", "type": "radio",
                          "options": ["Yes", "No"]}, "personal_fact", both)
    check(v["action"] == "human" and "more than one profile line" in v["reason"],
          f"a question naming both lines is Jack's ({v.get('reason')})")
    v = sensitive.decide({"label": "Have you ever had a clearance revoked?", "type": "radio", "options": ["Yes", "No"]},
                         "personal_fact", both)
    check(v["action"] == "human" and "can't tell which profile line" in v["reason"],
          f"a clearance question no line answers is Jack's ({v.get('reason')})")
    v = sensitive.decide({"label": "Do you currently hold a DoD security clearance?", "type": "radio",
                          "options": ["Yes", "No"]}, "personal_fact", both)
    check(v["action"] == "human" and "more than one kind" in v["reason"], f"a DoD clearance: two kinds, Jack's ({v.get('reason')})")
    # BLOCK fix: willingness, ability, acceptance or ineligibility is not
    # the eligibility line, even with it filled; each is Jack's.
    for label in ("Are you willing to obtain a clearance?", "Are you able to obtain a clearance?",
                  "This role requires you to obtain a clearance. Do you accept?",
                  "Are you ineligible to obtain a clearance?", "Would you be willing to obtain a security clearance?",
                  "Can you obtain a Secret clearance?"):
        v = sensitive.decide({"label": label, "type": "radio", "options": ["Yes", "No"]}, "personal_fact", eligible)
        check(v["action"] == "human" and v["value"] is None and "clearance_eligible" not in str(v.get("profile_path")),
              f"{label!r} with the eligibility line filled: Jack's ({v['action']}, {v.get('reason')})")
    # FLAG fold: a level select under a "willing / able to obtain" wording is
    # not the level held, even with it filled; the plain level question still is.
    for label in ("What level of security clearance are you willing to obtain?",
                  "Would you be willing to obtain a security clearance?",
                  "Which security clearance are you able to obtain?",
                  "This role requires a security clearance. Which can you get?"):
        v = sensitive.decide({"label": label, "type": "select", "options": ["None", "Secret", "Top Secret"]},
                             "personal_fact", both)
        check(v["action"] == "human" and v["value"] is None and "obtaining a clearance" in v["reason"],
              f"{label!r} as a level select with the level filled: Jack's ({v['action']}, {v.get('reason')})")
    v = sensitive.decide(level, "personal_fact", both)
    check(v["action"] == "fill" and v["value"] == "Secret", "the plain level question still fills from the level held")
    # The name and the options say it too, and the wider vocabulary.
    for field in ({"label": "Security clearance", "name": "clearance_willing_to_obtain"},
                  {"label": "Security clearance", "name": "clearanceWillingToObtain"},
                  {"label": "Security clearance", "options": ["None", "Willing to obtain Secret", "Top Secret"]},
                  {"label": "Which security clearance could you be granted?"},
                  {"label": "What level of security clearance would you pursue?"},
                  {"label": "Which security clearance do you qualify for?"},
                  {"label": "What security clearance can you receive?"},
                  {"label": "Security clearance level", "help_text": "Tell us what you could seek or apply for."}):
        v = sensitive.decide({"type": "select", "options": ["None", "Secret", "Top Secret"], **field}, "personal_fact", both)
        check(v["action"] == "human" and v["value"] is None and "obtaining a clearance" in v["reason"],
              f"{field!r}: asks about obtaining one, Jack's ({v['action']}, {v.get('reason')})")
    # The page-side copy of the guard (extract.py's RAW_LEGAL) carries the kind too.
    check(any("clearance" in pat for pat in sensitive.RAW_PATTERNS["legal"]), "the raw legal patterns carry the clearance kind")
    check.done()


def _gate_record(label, category, plan):
    import test_gate as tg

    rec = copy.deepcopy(tg.base())
    rec["fields"].append({"ref": "f9", "type": "text", "label": label})
    rec["categories"]["f9"] = category
    rec["plan"]["f9"] = plan
    rec["results"]["f9"] = {"filled": True, "detail": "typed", "unconfirmed": False}
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


RELOCATE_SPONSOR = "Are you willing to relocate if we sponsor your visa?"


def test_a_work_authorization_question_is_never_answered_from_a_relocation_line():
    import test_gate as tg

    check = Checks()
    prof = _Profile({"eligibility.willing_to_relocate": True,
                     "eligibility.authorized_to_work_us": True})
    v = sensitive.decide({"label": RELOCATE_SPONSOR, "type": "radio", "options": ["Yes", "No"]},
                         "personal_fact", prof)
    check(v["action"] == "human" and "eligibility.willing_to_relocate" in (v.get("reason") or ""),
          f"guarded as work authorization, closest line willing_to_relocate: yours ({v['action']})")
    v = sensitive.decide({"label": "Are you authorized to work in the US?", "type": "radio",
                          "options": ["Yes", "No"]}, "personal_fact", prof)
    check(v["action"] == "fill", "a real work-authorization line still answers its own question")
    for plan, needle, what in (
            ({"value": "Yes", "source": "profile", "fit": "bool",
              "profile_path": "eligibility.willing_to_relocate"},
             "which isn't a work_authorization profile line", "answered from the relocation line"),
            ({"value": "Yes", "source": "model", "confidence": "high", "needs_review": False,
              "review_reasons": []}, "not traced to a profile line", "answered by the model")):
        tg.stops(check, _gate_record(RELOCATE_SPONSOR, "personal_fact", plan), needle,
                 f"the gate, {what}")
    check.done()


def test_the_deciding_word_must_sit_in_the_sponsorship_sentence():
    # Must-fix (b): a "future" or "now" word outside the sentence that asks
    # about sponsorship decides nothing. Before this, the future line
    # answered the first question below.
    check = Checks()
    prof = _Profile({"eligibility.requires_sponsorship_now": False,
                     "eligibility.requires_sponsorship_future": True})
    pair = {"eligibility.requires_sponsorship_now", "eligibility.requires_sponsorship_future"}

    def ask(label):
        return sensitive.decide({"label": label, "type": "select",
                                 "options": ["Yes", "No"]}, "work_authorization", prof)

    outside = "Will you require sponsorship? Think about the future."
    check(sensitive.all_profile_lines(outside) == pair,
          "the wording matches both lines (so the next check means something)")
    check(sensitive.sponsorship_line(outside, pair) is None,
          "'future' in another sentence decides nothing")
    v = ask(outside)
    check(v["action"] == "human" and "more than one profile line" in v.get("reason", ""),
          f"  ...so the question is yours ({v['action']})")

    inside = "Will you require sponsorship in the future?"
    check(sensitive.sponsorship_line(inside, pair) == "eligibility.requires_sponsorship_future",
          "the same word inside the sponsorship sentence still decides")
    # A word of the other kind anywhere still blocks, as before.
    check(sensitive.sponsorship_line("Will you require sponsorship in the future? "
                                     "Answer now.", pair) is None,
          "a now-word in another sentence still blocks a future decision")
    check(sensitive.sponsorship_line("Do you need sponsorship to begin? "
                                     "We may relocate you later.", pair) is None,
          "a future-word in another sentence still blocks a now decision")
    # Sentence breaks a page can use: a line break with no punctuation before
    # it (innerText's block break) ends the sentence too, even though one
    # reading of the text collapses it to a space.
    check(sensitive.sponsorship_line("Will you require sponsorship\nLater, "
                                     "we will ask about relocation", pair) is None,
          "a bare line break ends the sentence too")
    check(sensitive.sponsorship_line("Will you require sponsorship\uff1f Think about the future.",
                                     pair) is None,
          "a fullwidth question mark (NFKC: '?') ends the sentence too")
    check(sensitive.sponsorship_line("Will you require spon­sorship in the future",
                                     pair) == "eligibility.requires_sponsorship_future",
          "an invisible character inside the word still can't hide the clause")
    check.done()


if __name__ == "__main__":
    run_as_script(globals())
