"""
test_normalize.py — an invisible character inside a word can't hide a question from the patterns.

WHAT THIS DOES: Puts a soft hyphen or a zero-width space inside the word
that names a criminal-record question ("con\\u00advicted", "crimi\\u200bnal"),
with the classifier stubbed to call it an ordinary fact. HARD_STOPS must
still send it to you, and the gate must still stop on it. A soft-hyphenated
sponsorship question must still resolve to its own profile line.
WHY IT MATTERS: The regex backstop is the layer that doesn't depend on the
classifier. Pages put soft hyphens and zero-width characters inside words
for line breaking, and NFKC alone keeps them.

Run from anywhere:  .venv/Scripts/python.exe tests/test_normalize.py
"""
import copy

from _harness import Checks, run_as_script
from agent import sensitive

SOFT, ZWSP = "­", "​"
RECORD_QUESTIONS = [f"Have you ever been con{SOFT}victed?",
                    f"Do you have a crimi{ZWSP}nal record?"]


class _Profile:
    def __init__(self, data):
        self.data = data

    def get(self, path):
        return self.data.get(path)


def test_a_split_criminal_record_question_still_hits_hard_stops():
    check = Checks()
    for label in RECORD_QUESTIONS:
        v = sensitive.decide({"label": label, "type": "text"}, "personal_fact", _Profile({}))
        check(v["action"] == "human" and "record" in v.get("reason", ""),
              f"{ascii(label)}: HARD_STOPS sends it to you ({v['action']}, {v.get('reason')})")
    check.done()


def test_the_gate_stops_on_one_too():
    import test_gate as tg

    check = Checks()
    for label in RECORD_QUESTIONS:
        rec = copy.deepcopy(tg.base())
        rec["fields"].append({"ref": "f9", "type": "text", "label": label})
        rec["categories"]["f9"] = "personal_fact"
        rec["plan"]["f9"] = {"value": "No", "source": "model", "confidence": "high",
                             "needs_review": False, "review_reasons": []}
        rec["results"]["f9"] = {"filled": True, "detail": "typed"}
        rec["snapshot"]["f9"] = "No"
        # Checked here rather than with tg.stops, which prints the reason
        # (and so the zero-width space) raw.
        v = tg.run(rec)
        hit = [r for r in v["content_reasons"] if "not traced to a profile line" in r]
        check(v["content_verdict"] == "would_stop" and hit,
              f"{ascii(label)}, model-answered -> stop ({ascii(hit)})")
    check.done()


def test_a_soft_hyphenated_sponsorship_question_finds_its_line():
    check = Checks()
    field = {"label": f"Do you require spon{SOFT}sorship?", "type": "radio",
             "options": ["Yes", "No"]}
    v = sensitive.decide(field, "work_authorization",
                         _Profile({"eligibility.requires_sponsorship_now": False}))
    check(v["action"] == "fill" and v.get("profile_path") == "eligibility.requires_sponsorship_now"
          and v.get("value") == "No" and v.get("fit") == "bool",
          f"answered from eligibility.requires_sponsorship_now ({v.get('action')}, "
          f"{v.get('profile_path')}, {v.get('value')})")
    check.done()


if __name__ == "__main__":
    run_as_script(globals())
