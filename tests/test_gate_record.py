"""
test_gate_record.py — a fill run leaves the gate a complete, honest record.

WHAT THIS DOES: Runs the real `fill` (and `fill --dry-run`) on
tests/gate_form.html and checks the record built for the submit gate: every
field, how each was answered and fitted, what was actually typed, every
checkbox's state, and the run's events. Also checks that a failure to read
the page is recorded as an error rather than an empty but tidy record.
WHY IT MATTERS: The gate is a pure function of this record. Whatever the
record leaves out, the gate can't stop on.

Run from anywhere:  .venv/Scripts/python.exe tests/test_gate_record.py
"""
from pathlib import Path

from _fill_run import run_fill
from _harness import Checks, run_as_script
from agent import shadow

FORM = Path(__file__).parent / "gate_form.html"


def _field(rec, start):
    return next(f for f in rec["fields"] if (f.get("label") or "").startswith(start))


def test_fill_builds_a_record():
    check = Checks()
    text, records, error = run_fill(FORM, dry_run=False)
    print(text[-600:])
    check(error is None, f"the run finished ({error})")
    check(len(records) == 1, f"one record built (got {len(records)})")
    rec = records[0] if records else {}
    if not rec:
        check.done()
    check(rec["dry_run"] is False and rec["steps"] == 1 and rec["errors"] == [],
          "one step, no errors")
    check(rec["portal"] == "generic" and rec["portal_method"] == "default",
          "a file:// form is generic, detected by default")
    plan = rec["plan"]
    auth = _field(rec, "Are you legally authorized")
    gender = _field(rec, "Gender")
    why = _field(rec, "Why do you want")
    hear = _field(rec, "How did you hear")
    first = _field(rec, "First Name")
    check(plan[auth["ref"]]["profile_path"] == "eligibility.authorized_to_work_us"
          and plan[auth["ref"]]["fit"] == "bool", "authorization: profile line and fit kind")
    check(plan[gender["ref"]]["profile_path"] == "eeo.gender"
          and plan[gender["ref"]]["fit"] == "exact", "gender: profile line and fit kind")
    check(plan[why["ref"]].get("draft") is True
          and plan[why["ref"]]["review_reasons"] == ["draft"], "free text: a draft, flagged as one")
    check(any(f["ref"] == hear["ref"] for f in rec["flags"]), "referral question flagged, with its ref")
    check(rec["results"][first["ref"]]["filled"] is True, "results keyed by ref")
    check(rec["snapshot"].get(first["ref"]) == "Test", "the read-back sees what was typed")
    labels = {c["label_content"]: c["checked"] for c in rec["census"]}
    check(labels == {"I agree to receive SMS updates": False,
                     "I agree to resolve disputes through binding arbitration": False},
          "both checkboxes counted, neither ticked")
    check(rec["events"] == [], "no CAPTCHA, login wall or extra step")
    check.done()


def test_dry_run_builds_a_record_without_results():
    check = Checks()
    text, records, error = run_fill(FORM, dry_run=True, answers=())
    check(error is None, f"the dry run finished ({error})")
    rec = records[0] if records else {}
    check(rec.get("dry_run") is True and rec.get("results") is None,
          "a dry run's record says so, and has no fill results")
    check(len(rec.get("plan") or {}) >= 4, "it still has the plan")
    check.done()


def test_unreadable_page_is_an_error_not_an_empty_record():
    check = Checks()

    class Broken:
        @property
        def url(self):
            raise RuntimeError("page gone")

        def evaluate(self, *a, **k):
            raise RuntimeError("page gone")

    rec = shadow.build_record(Broken(), url="https://example.invalid/", portal="greenhouse",
                              portal_method="host", company="", steps=[], events=[],
                              dry_run=False)
    print(f"  errors: {rec['errors']}")
    check(len(rec["errors"]) == 4, "checkboxes, iframes, values and address each recorded as unread")
    check(shadow.failed_record("u", "p", "boom")["errors"] == ["boom"],
          "a record that couldn't be built at all still carries the reason")
    check.done()


if __name__ == "__main__":
    run_as_script(globals())
