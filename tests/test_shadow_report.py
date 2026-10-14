"""
test_shadow_report.py — scoring the gate's calls against yours, and the report.

WHAT THIS DOES: Checks every scoring rule (a would_submit only matches with
zero edits read at submit time; anything uncertain is unknown), the
per-portal streak (a false submit resets it, a false stop doesn't add to it),
and runs `apply.py shadow-report` on a seeded log in a temp JOB_AGENT_HOME:
counts per portal, each portal's current enablement from the profile, and
every mismatch with its reasons and what you did.
WHY IT MATTERS: This report is the case for ever trusting the gate on a
portal. It must never count a guess as a match.

Run from anywhere:  .venv/Scripts/python.exe tests/test_shadow_report.py
"""
import json
import os
import subprocess
import sys
from contextlib import contextmanager

from _harness import REPO, TEST_HOME, Checks, run_as_script
from agent import config, score, tracker

EDIT = [{"ref": "f0", "label": "First Name *", "kind": "changed"}]
UNREAD = [{"ref": "f3", "label": "Confirm", "kind": "unreadable"}]


def row(verdict, submitted, edits, capture="exact", portal="greenhouse", when="2026-10-01",
        outcome=True, reasons=()):
    return {"verdict": verdict, "human_submitted": submitted,
            "edits_json": None if edits is None else json.dumps(edits), "capture": capture,
            "portal": portal, "created_at": when, "outcome_at": when if outcome else None,
            "application_url": f"https://{portal}.example/{when}", "reasons_json": json.dumps(list(reasons))}


def test_scoring_rules():
    check = Checks()
    cases = [
        (row("would_submit", "y", []), "match", "submitted, zero edits, read at submit"),
        (row("would_submit", "y", [], capture="polled"), "unknown", "zero edits but only polled"),
        (row("would_submit", "y", EDIT, capture="polled"), "false_submit", "you changed a field"),
        (row("would_submit", "n", []), "false_submit", "you didn't submit"),
        (row("would_submit", "y", UNREAD), "unknown", "a field couldn't be read"),
        (row("would_submit", "y", None), "unknown", "the form was never read"),
        (row("would_submit", "unknown", []), "unknown", "unknown whether you submitted"),
        (row("would_submit", "y", [], outcome=False), "pending", "no outcome yet"),
        (row("would_stop", "n", []), "match", "stop, and you didn't submit"),
        (row("would_stop", "y", EDIT), "match", "stop, and you changed something"),
        (row("would_stop", "y", []), "false_stop", "stop, but you sent it untouched"),
        (row("would_stop", "y", [], capture="polled"), "unknown", "untouched, but only polled"),
        (row("would_stop", "y", UNREAD), "unknown", "stop, a field unreadable"),
    ]
    for r, want, what in cases:
        got = score.score_run(r)
        check(got == want, f"{r['verdict']}: {what} -> {want} (got {got})")
    check.done()


def test_streaks_per_portal():
    check = Checks()
    seq = [row("would_submit", "y", [], when="2026-10-01"),          # match       1
           row("would_stop", "n", [], when="2026-10-02"),            # match       2
           row("would_stop", "y", [], when="2026-10-03"),            # false stop  2
           row("would_submit", "y", [], capture="polled", when="2026-10-04"),  # unknown 2
           row("would_submit", "y", [], when="2026-10-05"),          # match       3
           row("would_submit", "y", EDIT, when="2026-10-06"),        # false submit 0
           row("would_stop", "y", EDIT, when="2026-10-07"),          # match       1
           row("would_submit", "y", [], portal="lever", when="2026-10-03")]
    s = score.by_portal(list(reversed(seq)))                         # order comes from dates
    gh, lv = s["greenhouse"], s["lever"]
    print(f"  greenhouse: {({k: v for k, v in gh.items() if k != 'mismatches'})}")
    check(gh["streak"] == 1, "a false submit reset the streak; the match after it starts again at 1")
    check((gh["match"], gh["false_submit"], gh["false_stop"], gh["unknown"], gh["scored"])
          == (4, 1, 1, 1, 6), "counts: 4 matches, 1 false submit, 1 false stop, 1 unknown")
    check([m["result"] for m in gh["mismatches"]] == ["false_stop", "false_submit"],
          "both mismatches listed, oldest first")
    check(lv["streak"] == 1 and lv["runs"] == 1, "each portal keeps its own streak")
    upto = score.by_portal(seq[:5])["greenhouse"]
    check(upto["streak"] == 3, "a false stop and an unknown neither add to nor reset the streak")
    check.done()


@contextmanager
def home_with_log():
    home = TEST_HOME / "report-home"
    home.mkdir(exist_ok=True)
    (home / "applications.db").unlink(missing_ok=True)
    (home / "profile.yaml").write_text(
        "autosubmit_portals: [greenhouse]\ndaily_submit_cap: 2\n", encoding="utf-8")
    saved = (config.DATABASE_PATH, config.ensure_dirs)
    config.DATABASE_PATH, config.ensure_dirs = home / "applications.db", (lambda: None)
    try:
        yield home
    finally:
        config.DATABASE_PATH, config.ensure_dirs = saved


def _report(home) -> str:
    env = {k: v for k, v in os.environ.items() if k != "JOB_AGENT_PROFILE"}
    env.update(JOB_AGENT_HOME=str(home), PYTHONIOENCODING="utf-8")
    run = subprocess.run([sys.executable, str(REPO / "apply.py"), "shadow-report"], cwd=REPO,
                         env=env, capture_output=True, encoding="utf-8", timeout=60)
    import re
    return re.sub(r"\x1b\[[0-9;]*m", "", run.stdout + run.stderr)


def test_shadow_report_command():
    check = Checks()
    with home_with_log() as home:
        out = _report(home)
        check("No gate runs logged yet." in out, "an empty log says so")

        def log(verdict, reasons, submitted, edits, capture, portal="greenhouse"):
            rid = tracker.record_gate_run(f"https://{portal}.example/job", portal=portal,
                                          portal_method="host",
                                          result={"content_verdict": verdict,
                                                  "content_reasons": reasons, "gate_version": 1},
                                          record_json={})
            tracker.set_gate_outcome(rid, human_submitted=submitted, edits=edits,
                                     capture=capture, success_signal="{}")
        log("would_submit", [], "y", [], "exact")
        log("would_submit", [], "y", EDIT, "exact")
        log("would_stop", ["'How did you hear about us?': per-company question; "
                           "profile answering not enabled"], "y", [], "exact")
        log("would_stop", ["run: login_wall (before Apply)"], "n", [], "polled", portal="lever")
        out = _report(home)
    print(out)
    check("greenhouse  enablement now: on autosubmit_portals · cap remaining 2" in out,
          "greenhouse's current enablement comes from the profile")
    check("lever  enablement now: not on autosubmit_portals" in out, "lever's too")
    check("runs 3 · scored 3 · matches 1 · false submits 1 · false stops 1 · unknown 0 · "
          "pending 0 · consecutive matches 0" in out, "greenhouse counts and streak")
    check("runs 1 · scored 1 · matches 1" in out and "consecutive matches 1" in out,
          "lever counts and streak")
    check("Mismatches (2)" in out, "both mismatches listed")
    check("would_submit, but it shouldn't have" in out
          and "you submitted, changing First Name * (changed)" in out
          and "the gate found nothing to stop on" in out,
          "the false submit: no reasons, and what you changed")
    check("would_stop, but you sent it untouched" in out
          and "per-company question; profile answering not enabled" in out
          and "you submitted, with no changes" in out, "the false stop: its reason, and what you did")
    check("The agent has submitted nothing" in out, "it says the agent submitted nothing")
    check.done()


if __name__ == "__main__":
    run_as_script(globals())
