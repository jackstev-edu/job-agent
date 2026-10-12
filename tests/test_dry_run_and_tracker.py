"""
test_dry_run_and_tracker.py — a null answer prints as a blank; reruns refresh the log.

WHAT THIS DOES: Feeds _print_dry_run a plan with a null value and checks the
line shows a blank and the model's note, not "[ok] None"; then records the
same URL twice in a throwaway database and checks the second write wins for
every column, ats and resume_used included.
WHY IT MATTERS: "[ok] None" read like an answer of "None", and a --force rerun
kept the first run's portal and resume in the log.

Run from anywhere:  .venv/Scripts/python.exe tests/test_dry_run_and_tracker.py
"""
import io
import re
import tempfile
from contextlib import redirect_stdout
from pathlib import Path

from _harness import Checks, run_as_script
from agent import config, tracker


def test_null_value_prints_blank_with_note():
    import apply
    check = Checks()
    fields = [{"ref": "f0", "label": "Middle name"}, {"ref": "f1", "label": "City"},
              {"ref": "f2", "label": "Pronouns"}]
    plan = {"f0": {"value": None, "note": "No middle name in your profile."},
            "f1": {"value": "Springfield"},
            "f2": {"value": None}}
    out = io.StringIO()
    with redirect_stdout(out):
        apply._print_dry_run(fields, plan, [])
    text = re.sub(r"\x1b\[[0-9;]*m", "", out.getvalue())
    print(text)
    lines = text.splitlines()
    middle = next(l for l in lines if "Middle name" in l)
    check("None" not in text, "no 'None' printed anywhere")
    check("[blank]" in middle and "[ok]" not in middle, "null shown as [blank], not [ok]")
    check(middle.rstrip().endswith("Middle name"), "nothing printed in the value column")
    check(lines[lines.index(middle) + 1].strip() == "No middle name in your profile.",
          "the model's note printed under it")
    check(any("[ok]" in l and "Springfield" in l for l in lines), "a real value still prints [ok]")
    pronouns = next(l for l in lines if "Pronouns" in l)
    check("left for you" in lines[lines.index(pronouns) + 1],
          "a null with no note still says why it's blank")
    check.done()


def test_rerun_updates_ats_and_resume():
    check = Checks()
    saved = (config.DATABASE_PATH, config.ensure_dirs)
    with tempfile.TemporaryDirectory() as tmp:
        # Never the real log in ~/.job-agent.
        config.DATABASE_PATH = Path(tmp) / "applications.db"
        config.ensure_dirs = lambda: None
        try:
            url = "https://example.invalid/job/1"
            tracker.record(url, company="Acme", role="Engineer", ats="Lever",
                           resume_used="old.pdf", report_path="a.html")
            first = tracker.find(url)
            tracker.record(url, company="Acme", role="Engineer", ats="Greenhouse",
                           resume_used="new.pdf", report_path="b.html")
            row = tracker.find(url)
            print(f"  first:  ats={first['ats']!r} resume={first['resume_used']!r}")
            print(f"  rerun:  ats={row['ats']!r} resume={row['resume_used']!r}")
            check(row["ats"] == "Greenhouse", "rerun updated ats")
            check(row["resume_used"] == "new.pdf", "rerun updated resume_used")
            check(row["report_path"] == "b.html", "rerun still updates report_path")
            check(row["id"] == first["id"] and row["created_at"] == first["created_at"],
                  "same row, original created_at kept")
            check(len(tracker.all_applications()) == 1, "still one row for the URL")
        finally:
            config.DATABASE_PATH, config.ensure_dirs = saved
    check.done()


if __name__ == "__main__":
    run_as_script(globals())
