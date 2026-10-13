"""
test_gate_fill.py — `fill` prints and logs the shadow gate's call, and never acts on it.

WHAT THIS DOES: Runs the real `fill` on mock forms. A clean form whose every
answer comes exactly from the profile gets would_submit. The fuller mock,
with a referral question, an arbitration box and no allowlist, gets
would_stop with those reasons named. A dry run prints its verdict but logs
nothing. Then an EEO answer seeded as a sentinel, in the profile and as the
form's option, must not appear in gate_runs.
WHY IT MATTERS: Shadow mode only watches. A would_submit must never press
anything, and the log kept for scoring must never hold an EEO answer.

Run from anywhere:  .venv/Scripts/python.exe tests/test_gate_fill.py
"""
import json
from pathlib import Path

from _fill_run import PROFILE, run_fill
from _harness import TEST_HOME, Checks, run_as_script
from agent import tracker

HERE = Path(__file__).parent
CLEAN_PROFILE = PROFILE + """\
consent_allowlist:
  - I agree to receive SMS updates
autosubmit_portals: [greenhouse]
daily_submit_cap: 2
"""


def _clean_form(option: str) -> Path:
    """gate_clean.html with its decline option spelled as given."""
    path = TEST_HOME / f"gate_clean_{abs(hash(option))}.html"
    html = (HERE / "gate_clean.html").read_text(encoding="utf-8").replace("GENDER_OPTION", option)
    path.write_text(html, encoding="utf-8")
    return path


def _rows_for(form: Path) -> list[dict]:
    url = form.resolve().as_uri()
    return [r for r in tracker.gate_runs() if r["application_url"] == url]


def test_clean_form_would_submit_and_is_logged():
    check = Checks()
    form = _clean_form("Decline to self-identify")
    text, records, error = run_fill(form, dry_run=False, profile_yaml=CLEAN_PROFILE)
    print(text[text.find("Shadow gate"):][:700])
    check(error is None, f"the run finished ({error})")
    check("Shadow gate: would_submit — Submit is still yours." in text,
          "clean form -> would_submit, printed as advice only")
    check("portal allowlisted no" in text and "cap remaining 2" in text,
          "enablement printed apart: a file:// form isn't an allowlisted portal")
    check("allowlisted consent boxes: 0 would be ticked, 1 left; the agent ticked none" in text,
          "the planned consent action is printed, and nothing was ticked")
    check(records and records[0]["snapshot"].get(
        next(f["ref"] for f in records[0]["fields"] if f["type"] == "checkbox")) is False,
        "  ...the opt-in really is unticked")
    rows = _rows_for(form)
    check(len(rows) == 1 and rows[0]["verdict"] == "would_submit"
          and rows[0]["agent_submitted"] == 0, "logged once, as not agent-submitted")
    check("Submit Application" not in json.dumps(rows), "nothing about pressing Submit is logged")
    check.done()


def test_fuller_form_would_stop_with_reasons():
    check = Checks()
    form = HERE / "gate_form.html"
    text, records, error = run_fill(form, dry_run=False)
    shown = text[text.find("Shadow gate"):]
    print(shown[:900])
    check(error is None, f"the run finished ({error})")
    check("Shadow gate: would_stop" in text, "-> would_stop")
    for needle in ("'How did you hear about us?': per-company question; profile answering not enabled",
                   "checkbox 'I agree to resolve disputes through binding arbitration': not on consent_allowlist",
                   "checkbox 'I agree to receive SMS updates': not on consent_allowlist"):
        check(needle in shown, f"names: {needle[:60]}…")
    rows = _rows_for(form)
    check(rows and rows[-1]["verdict"] == "would_stop"
          and any("per-company" in r for r in json.loads(rows[-1]["reasons_json"])),
          "logged with its reasons")
    check.done()


def test_dry_run_prints_but_does_not_log():
    check = Checks()
    form = _clean_form("Decline to self-identify")
    before = len(_rows_for(form))
    text, records, error = run_fill(form, dry_run=True, answers=(), profile_yaml=CLEAN_PROFILE)
    check(error is None, f"the dry run finished ({error})")
    check("Shadow gate: would_submit (dry run, not logged)" in text, "dry run prints its verdict")
    check(len(_rows_for(form)) == before, "and logs nothing")
    check.done()


def test_eeo_answer_never_reaches_gate_runs():
    check = Checks()
    sentinel = "Decline-SENTINEL-6602"
    form = _clean_form(sentinel)
    profile = CLEAN_PROFILE.replace("gender: Decline to self-identify", f"gender: {sentinel}")
    text, records, error = run_fill(form, dry_run=False, profile_yaml=profile)
    check(error is None, f"the run finished ({error})")
    check(records and records[0]["plan"] and any(
        (d.get("profile_path") == "eeo.gender") for d in records[0]["plan"].values()),
        "the EEO answer really was filled from the profile line")
    rows = _rows_for(form)
    check(len(rows) == 1, "one gate run logged")
    check(sentinel not in json.dumps(rows), "the EEO answer appears in no gate_runs column")
    stored = json.loads(rows[0]["record_json"])
    gender = next(f for f in stored["fields"] if f.get("profile_path") == "eeo.gender")
    check(set(gender) == {"ref", "label", "category", "profile_path", "source", "fit"},
          "it's stored as field, profile line, source and fit only")
    check.done()


if __name__ == "__main__":
    run_as_script(globals())
