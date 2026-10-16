"""
test_text_variants.py — no reading of a question can lose a stop, a guard or a redaction.

WHAT THIS DOES: Puts invisible characters (a zero-width space, a direction
mark, an invisible operator, a soft hyphen) between or inside the words
that make a question sensitive. Deleting them joins words, keeping them
splits them, so every check is made on each reading (text_variants): the
text as written, NFKC, invisibles deleted, invisibles as spaces. Each
question must still stop, still be guarded, still have its census state
withheld from gate_runs; and a label whose readings point at two different
profile lines must come to you.
WHY IT MATTERS: A field must clear both the classifier and the local
backstop. Normalising text for the backstop must only ever add matches.

Run from anywhere:  .venv/Scripts/python.exe tests/test_text_variants.py
"""
import copy

from _harness import Checks, run_as_script
from agent import sensitive, shadow

ZWSP, LRM, INV_TIMES, SOFT = chr(0x200B), chr(0x200E), chr(0x2062), chr(0x00AD)


class _Profile:
    def __init__(self, data):
        self.data = data

    def get(self, path):
        return self.data.get(path)


def _decide(label, category="personal_fact", profile=None, **field):
    return sensitive.decide({"label": label, "type": "text", **field}, category,
                            profile or _Profile({}))


def test_a_word_boundary_made_by_an_invisible_still_stops():
    check = Checks()
    for label in (f"Security{ZWSP}PIN", f"Hereby{ZWSP}I certify"):
        v = _decide(label)
        check(v["action"] == "human" and v.get("reason"),
              f"{ascii(label)}: comes to you ({v['action']}, {v.get('reason')})")
    check.done()


def test_an_invisible_inside_a_word_still_stops():
    check = Checks()
    for label in (f"Have you been con{LRM}victed?", f"Have you been con{SOFT}victed?"):
        v = _decide(label)
        check(v["action"] == "human" and "record" in (v.get("reason") or ""),
              f"{ascii(label)}: HARD_STOPS sends it to you ({v['action']})")
    check.done()


def test_a_word_split_by_one_invisible_and_bounded_by_another_still_stops():
    # Needs one invisible deleted (inside PIN) and the other kept (the
    # boundary before it) at once: only the previous normaliser's reading
    # gives that, so text_variants keeps it. It stopped before this commit
    # too; this pins that it still does.
    check = Checks()
    label = f"Security{LRM}P{SOFT}IN"
    v = _decide(label)
    check(v["action"] == "human", f"{ascii(label)}: comes to you ({v['action']})")
    check.done()


def test_an_invisible_between_words_is_still_guarded():
    check = Checks()
    label = f"Visa{INV_TIMES}required"
    check(sensitive.legal_guard({"label": label}) == "work_authorization",
          f"{ascii(label)}: the legal guard still matches")
    check.done()


def _gate_record(label, plan):
    import test_gate as tg

    rec = copy.deepcopy(tg.base())
    rec["fields"].append({"ref": "f9", "type": "text", "label": label})
    rec["categories"]["f9"] = "personal_fact"
    rec["plan"]["f9"] = plan
    rec["results"]["f9"] = {"filled": True, "detail": "typed"}
    rec["snapshot"]["f9"] = plan.get("value")
    return rec


def _stops(rec, needle):
    import test_gate as tg

    v = tg.run(rec)
    return v["content_verdict"] == "would_stop" and any(needle in r for r in v["content_reasons"])


def test_the_gate_stops_on_a_split_eeo_or_two_area_question():
    check = Checks()
    model = {"value": "Female", "source": "model", "confidence": "high",
             "needs_review": False, "review_reasons": []}
    check(_stops(_gate_record(f"Your{ZWSP}gender", model), "not traced to a profile line"),
          "'Your<zwsp>gender' answered by the model: the gate stops")
    two = {"value": True, "source": "profile", "fit": "bool",
           "profile_path": "eligibility.authorized_to_work_us"}
    check(_stops(_gate_record("I am authorized to work in the US and am not a protected veteran",
                              two), "more than one kind of legal question"),
          "a two-area question answered from one line: the gate stops")
    check.done()


def test_a_split_census_label_keeps_its_state_out_of_gate_runs():
    check = Checks()
    for label in (f"I identify as{ZWSP}disabled", f"I am dis{SOFT}abled"):
        box = {"ref": "c9", "checked": True, "visible": True, "label_source": "for",
               "label_inner": label, "label_content": label, "described": "",
               "in_button_group": False, "group_ref": None}
        out = shadow._redacted_box(box, {})
        check("checked" not in out and out.get("checked_withheld") is True,
              f"{ascii(label)}: its ticked state is withheld")
    plain = shadow._redacted_box({"ref": "c8", "checked": True, "label_inner": "Text me updates",
                                  "label_content": "Text me updates"}, {})
    check(plain.get("checked") is True,
          "an ordinary box's state is still kept (so the checks above mean something)")
    check.done()


def test_readings_that_disagree_about_the_line_come_to_you():
    check = Checks()
    label = f"sex{ZWSP}ual orientation"
    lines = sensitive.profile_lines(label)
    check(len(lines) == 2, f"the readings point at two lines ({lines})")
    v = _decide(label, "eeo", _Profile({"eeo.gender": "Decline to self-identify",
                                       "eeo.sexual_orientation": "Prefer not to say"}),
                type="select", options=["Decline to self-identify", "Prefer not to say"])
    check(v["action"] == "human" and "more than one profile line" in (v.get("reason") or ""),
          f"so the field comes to you ({v['action']}, {v.get('reason')})")
    check.done()


if __name__ == "__main__":
    run_as_script(globals())
