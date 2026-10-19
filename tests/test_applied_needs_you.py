"""
test_applied_needs_you.py — `apply.py applied` and `apply.py needs-you`.

WHAT THIS DOES: Logs a submitted application (by the agent, with evidence),
a parked one with a reason and a failed one, then checks `applied` lists
all three with company, role, URL, date, portal and status, marks the
agent's submission, and `needs-you` lists only the parked and failed ones
with their reasons, control characters stripped. Also checks the evidence
column and agent_submitted flag land in the tracker row.
WHY IT MATTERS: These two commands are how Jack sees what the unattended
run did and what it left for him.

Run from anywhere:  .venv/Scripts/python.exe tests/test_applied_needs_you.py
"""
import io
import re
from argparse import Namespace
from contextlib import redirect_stdout

from _harness import Checks, run_as_script
from agent import tracker


def _out(fn):
    buf = io.StringIO()
    with redirect_stdout(buf):
        fn(Namespace())
    return re.sub(r"\x1b\[[0-9;]*m", "", buf.getvalue())


def test_applied_and_needs_you_list_what_run_logged():
    import apply
    check = Checks()
    sent, parked, failed = ("https://a.example/jobs/1", "https://b.example/jobs/2",
                            "https://c.example/jobs/3")
    tracker.record(sent, company="Acme", role="Engineer", ats="Greenhouse", status="submitted",
                   notes="confirmation seen")
    tracker.set_evidence(sent, {"url": sent + "/confirmation", "title": "Thanks",
                                "snippet": "Thank you for applying"}, agent_submitted=True)
    tracker.record(parked, company="Bolt", role="Analyst", ats="Workday", status="needs_you",
                   notes="verification: still waiting (timed_out)\x1b[31m")
    tracker.record(failed, company="Cog", role="Intern", ats="Unknown or custom", status="failed",
                   notes="TimeoutError: the link didn't open")
    row = tracker.find(sent)
    check(row["agent_submitted"] == 1 and "Thank you for applying" in (row["evidence_json"] or "")
          and row["submitted_at"], "the agent's submission carries the evidence and a submitted_at")

    text = _out(apply.cmd_applied)
    print(text)
    for needle in ("Acme", "Engineer", "Greenhouse", "submitted", "(agent)", sent,
                   "Bolt", "needs_you", parked, "Cog", "failed", failed, " application(s)"):
        check(needle in text, f"applied lists: {needle}")
    text = _out(apply.cmd_needs_you)
    print(text)
    check("Acme" not in text and "Bolt" in text and "Cog" in text and " waiting" in text,
          "needs-you lists the parked and failed ones only")
    check("verification: still waiting" in text and "\x1b[31m" not in text,
          "the reason is shown with control characters stripped")
    check.done()


if __name__ == "__main__":
    run_as_script(globals())
