"""
test_dry_run_and_tracker.py — a null answer prints as a blank; reruns refresh the log.

WHAT THIS DOES: Feeds _print_dry_run a plan with a null value and checks the
line shows a blank and the model's note, not "[ok] None"; then records the
same URL twice in a throwaway database and checks the second write wins for
every column of a draft, but never knocks a submitted (or later) row back
to draft.
WHY IT MATTERS: "[ok] None" read like an answer of "None"; a --force rerun
kept the first run's portal and resume, and would have un-submitted a sent
application in the log.

Run from anywhere:  .venv/Scripts/python.exe tests/test_dry_run_and_tracker.py
"""
import io
import re
import tempfile
from contextlib import contextmanager, redirect_stdout
from pathlib import Path

from _harness import Checks, run_as_script
from agent import config, tracker


def test_null_value_prints_blank_with_note():
    import apply
    check = Checks()
    fields = [{"ref": "f0", "label": "Middle name"}, {"ref": "f1", "label": "City"},
              {"ref": "f2", "label": "Pronouns"}]
    plan = {"f0": {"value": None, "note": "No middle name in your profile."},
            "f1": {"value": "Pittsburgh"},
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
    check(any("[ok]" in l and "Pittsburgh" in l for l in lines), "a real value still prints [ok]")
    pronouns = next(l for l in lines if "Pronouns" in l)
    check("left for you" in lines[lines.index(pronouns) + 1],
          "a null with no note still says why it's blank")
    check.done()


@contextmanager
def temp_log():
    """A throwaway applications.db, never the real log in ~/.job-agent."""
    saved = (config.DATABASE_PATH, config.ensure_dirs)
    with tempfile.TemporaryDirectory() as tmp:
        config.DATABASE_PATH = Path(tmp) / "applications.db"
        config.ensure_dirs = lambda: None
        try:
            yield
        finally:
            config.DATABASE_PATH, config.ensure_dirs = saved


def test_rerun_updates_ats_and_resume():
    check = Checks()
    with temp_log():
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
        check(row["status"] == "draft", "a draft stays a draft")
        check(row["id"] == first["id"] and row["created_at"] == first["created_at"],
              "same row, original created_at kept")
        check(len(tracker.all_applications()) == 1, "still one row for the URL")
    check.done()


def test_rerun_never_downgrades_status():
    check = Checks()
    for status in tracker.PAST_DRAFT:
        with temp_log():
            url = f"https://example.invalid/job/{status}"
            tracker.record(url, company="Acme", role="Engineer", ats="Lever",
                           resume_used="sent.pdf", answers=[{"v": 1}],
                           flags=[{"f": 1}], report_path="a.html")
            tracker.mark_submitted(url)
            if status != "submitted":
                tracker.set_status(url, status)
            before = tracker.find(url)
            # What a --force rerun of `fill` writes: a fresh draft record.
            tracker.record(url, company="Other", role="Other", ats="Greenhouse",
                           resume_used="new.pdf", answers=[{"v": 2}],
                           flags=[{"f": 2}], report_path="b.html")
            row = tracker.find(url)
            print(f"  {status:<9} -> status={row['status']!r} "
                  f"submitted_at={row['submitted_at']!r} report={row['report_path']!r}")
            check(row["status"] == status, f"{status}: status kept")
            check(row["submitted_at"] == before["submitted_at"] and row["submitted_at"],
                  f"{status}: submitted_at kept")
            check(row["answers_json"] == '[{"v": 2}]' and row["flags_json"] == '[{"f": 2}]'
                  and row["report_path"] == "b.html",
                  f"{status}: answers, flags and report refreshed")
            check((row["company"], row["role"], row["ats"], row["resume_used"])
                  == ("Acme", "Engineer", "Lever", "sent.pdf"),
                  f"{status}: what it was sent with is kept")
    check.done()


if __name__ == "__main__":
    run_as_script(globals())
