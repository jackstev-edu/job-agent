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
import io
import json
import os
import subprocess
import sys
from argparse import Namespace
from contextlib import contextmanager, redirect_stdout
from pathlib import Path

from _harness import REPO, TEST_HOME, Checks, run_as_script
from agent import config, gate, score, tracker
from agent.profile import Profile

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
        # An edit is checked before your answer, so it can't hide behind an unknown one.
        (row("would_submit", "unknown", EDIT), "false_submit", "unknown answer, but you edited"),
        (row("would_submit", "y", UNREAD + EDIT), "false_submit",
         "a field unreadable, but another one edited"),
        (row("would_submit", "unknown", UNREAD + EDIT), "false_submit",
         "unknown answer, unreadable field, and an edit"),
        (row("would_submit", "y", UNREAD, capture="polled"), "unknown",
         "a field unreadable, no edit"),
        (row("would_submit", "unknown", UNREAD), "unknown", "unknown answer, unreadable, no edit"),
        (row("would_stop", "unknown", EDIT), "match", "stop, unknown answer, but you edited"),
        (row("would_stop", "unknown", []), "unknown", "stop, unknown answer, no edit"),
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

    # Every false_submit case resets the streak, including the ones an unknown
    # answer or an unreadable field used to hide.
    built = [row("would_submit", "y", [], when="2026-10-01"),
             row("would_stop", "n", [], when="2026-10-02")]
    for bad, what in [
            (row("would_submit", "unknown", EDIT, when="2026-10-03"), "unknown answer + edit"),
            (row("would_submit", "y", UNREAD + EDIT, when="2026-10-03"), "unreadable + edit"),
            (row("would_submit", "unknown", UNREAD + EDIT, when="2026-10-03"),
             "unknown answer + unreadable + edit"),
            (row("would_submit", "y", EDIT, when="2026-10-03"), "submitted + edit"),
            (row("would_submit", "n", [], when="2026-10-03"), "not submitted")]:
        s = score.by_portal(built + [bad])["greenhouse"]
        check(s["streak"] == 0 and s["false_submit"] == 1,
              f"streak of 2 reset by a false submit: {what} (streak {s['streak']})")
    check.done()


def test_streak_counts_its_matched_would_submits():
    check = Checks()
    stops_only = [row("would_stop", "n", [], when=f"2026-10-0{i}") for i in range(1, 5)]
    s = score.by_portal(stops_only)["greenhouse"]
    check(s["streak"] == 4 and s["streak_submits"] == 0,
          f"a streak built only from would_stop matches has 0 matched would_submits "
          f"(streak {s['streak']}, would_submits {s['streak_submits']})")
    mixed = [row("would_submit", "y", [], when="2026-10-01"),
             row("would_stop", "n", [], when="2026-10-02"),
             row("would_submit", "y", [], when="2026-10-03"),
             row("would_stop", "y", [], when="2026-10-04"),                 # false stop
             row("would_submit", "y", [], capture="polled", when="2026-10-05")]  # unknown
    s = score.by_portal(mixed)["greenhouse"]
    check(s["streak"] == 3 and s["streak_submits"] == 2,
          "only matched would_submits count; a false stop and an unknown add nothing")
    s = score.by_portal(mixed + [row("would_submit", "y", EDIT, when="2026-10-06"),
                                 row("would_stop", "n", [], when="2026-10-07")])["greenhouse"]
    check(s["streak"] == 1 and s["streak_submits"] == 0,
          "a false submit resets both; the would_stop match after it adds no would_submit")
    check.done()


@contextmanager
def home_with_log():
    home = TEST_HOME / "report-home"
    home.mkdir(exist_ok=True)
    (home / "applications.db").unlink(missing_ok=True)
    (home / "profile.yaml").write_text(
        "autosubmit_portals: [greenhouse, ashby]\ndaily_submit_cap: 2\n", encoding="utf-8")
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

        def log(verdict, reasons, submitted, edits, capture, portal="greenhouse",
                method="host"):
            rid = tracker.record_gate_run(f"https://{portal}.example/job", portal=portal,
                                          portal_method=method,
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
        # On the allowlist, but found by the page's HTML rather than its host.
        log("would_stop", ["run: 2 wizard steps"], "n", [], "polled", portal="ashby",
            method="html")
        out = _report(home)
    print(out)
    check("greenhouse  enablement now: on autosubmit_portals · cap remaining: 2" in out,
          "greenhouse's current enablement comes from the profile")
    check("lever  enablement now: not on autosubmit_portals" in out, "lever's too")
    check("ashby  enablement now: not enabled: detected by html, not by its host · "
          "cap remaining: 2" in out,
          "an allowlisted portal detected by html is not enabled (the gate's host rule)")
    check("runs 3 · scored 3 · matches 1 · false submits 1 · false stops 1 · unknown 0 · "
          "pending 0 · consecutive matches 0, of which matched would_submits 0" in out,
          "greenhouse counts and streak")
    check("runs 1 · scored 1 · matches 1" in out
          and "consecutive matches 1, of which matched would_submits 0" in out,
          "lever: a streak of one would_stop match shows 0 matched would_submits")
    check("ready" not in out.lower(), "no readiness threshold or verdict is shown")
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


def test_report_and_gate_share_the_enablement_rule():
    import apply
    check = Checks()
    for portals in ([], ["greenhouse"]):
        for cap in (None, 0, 2):
            prof = Profile({"autosubmit_portals": portals, "daily_submit_cap": cap},
                           Path("p.yaml"))
            for method in ("host", "html", "url", ""):
                for today in (0, 1, 5, None):
                    v = gate.decide({"portal": "greenhouse", "portal_method": method,
                                     "fields": []}, prof, agent_submissions_today=today)
                    line = apply._enablement_now(prof, "greenhouse", method, today)
                    want_on = v["enablement"]["portal_allowlisted"]
                    want_cap = gate.cap_text(v["enablement"], v["enablement_reasons"])
                    ok = (line.startswith("on autosubmit_portals") == want_on
                          and line.endswith(f"cap remaining: {want_cap}"))
                    if not ok:
                        check(False, f"{portals} cap={cap} method={method!r} today={today}: "
                                     f"report says {line!r}, gate says {v['enablement']}")
    check(True, "report and gate agree on every portal / method / cap / count combination")
    prof = Profile({"autosubmit_portals": ["greenhouse"], "daily_submit_cap": 2}, Path("p.yaml"))
    check(not gate.decide({"portal": "greenhouse", "portal_method": "html", "fields": []},
                          prof)["enablement"]["portal_allowlisted"]
          and apply._enablement_now(prof, "greenhouse", "html", 0).startswith("not enabled"),
          "both refuse an allowlisted portal that wasn't detected by its host")
    check.done()


def test_cap_count_error_is_unknown():
    import apply
    check = Checks()
    prof = Profile({"autosubmit_portals": ["greenhouse"], "daily_submit_cap": 2}, Path("p.yaml"))
    for bad in (None, True, -1, "3"):
        ena, reasons = gate.enablement("greenhouse", "host", prof, bad)
        check(ena["cap_remaining"] is None and gate.cap_text(ena, reasons) == "unknown",
              f"an uncounted day ({bad!r}) -> cap_remaining None, shown as unknown")

    def boom():
        raise RuntimeError("database is locked")
    real = tracker.agent_submissions_today
    with home_with_log() as home:
        rid = tracker.record_gate_run("https://greenhouse.example/job", portal="greenhouse",
                                      portal_method="host",
                                      result={"content_verdict": "would_stop",
                                              "content_reasons": ["x"], "gate_version": 1},
                                      record_json={})
        tracker.set_gate_outcome(rid, human_submitted="n", edits=[], capture="polled")
        tracker.agent_submissions_today = boom
        try:
            out = io.StringIO()
            with redirect_stdout(out):
                apply.cmd_shadow_report(Namespace(profile=str(home / "profile.yaml")))
            report = out.getvalue()
            out = io.StringIO()
            with redirect_stdout(out):
                run_id, result = apply._shadow_gate(
                    {"portal": "greenhouse", "portal_method": "host", "fields": [],
                     "url": "https://greenhouse.example/job"},
                    Profile.load(home / "profile.yaml"), "https://greenhouse.example/job",
                    "greenhouse", "host", dry_run=False)
            printed = out.getvalue()
            logged = [r for r in tracker.gate_runs() if r["id"] == run_id]
        finally:
            tracker.agent_submissions_today = real
    print(report)
    check("enablement now: on autosubmit_portals · cap remaining: unknown" in report,
          "shadow-report: the count raised -> cap remaining: unknown")
    check("cap remaining: 2" not in report and "cap remaining: 0" not in report,
          "  ...never the full cap, never none left")
    check(result["enablement"]["cap_remaining"] is None and "cap remaining: unknown" in printed,
          "_shadow_gate: the count raised -> cap_remaining None, printed as unknown")
    check(logged and json.loads(logged[0]["enablement_json"])["cap_remaining"] is None,
          "  ...and logged as cap_remaining null")
    check.done()


if __name__ == "__main__":
    run_as_script(globals())
