"""
test_gate_tracker.py — the gate's shadow log: schema migration, rows, and no answer values.

WHAT THIS DOES: Migrates a database in the original layout and checks old
rows survive. Logs gate verdicts and outcomes, and counts agent submissions
by local date. Then seeds EEO and eligibility answers as sentinel strings
(in the plan, the read-back, the option lists, a flag's text and a ticked
EEO checkbox) and asserts none of them reach gate_runs, in any column or
anywhere in the database file.
WHY IT MATTERS: gate_runs is kept for scoring. It may say which profile line
answered a question, but never what the answer was.

Run from anywhere:  .venv/Scripts/python.exe tests/test_gate_tracker.py
"""
import json
import sqlite3
import tempfile
from contextlib import contextmanager
from pathlib import Path

from _harness import Checks, run_as_script
from agent import config, gate, shadow, tracker

import test_gate


@contextmanager
def temp_db():
    saved = (config.DATABASE_PATH, config.ensure_dirs)
    with tempfile.TemporaryDirectory() as tmp:
        config.DATABASE_PATH = Path(tmp) / "applications.db"
        config.ensure_dirs = lambda: None
        try:
            yield config.DATABASE_PATH
        finally:
            config.DATABASE_PATH, config.ensure_dirs = saved


def test_migration_from_the_original_layout():
    check = Checks()
    with temp_db() as path:
        conn = sqlite3.connect(path)
        conn.executescript(tracker.SCHEMA)
        conn.execute("INSERT INTO applications (url, company, status, created_at) "
                     "VALUES ('https://example.invalid/old', 'Old', 'submitted', '2026-01-01')")
        conn.commit(); conn.close()

        old = tracker.find("https://example.invalid/old")     # first connect migrates
        conn = sqlite3.connect(path)
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        conn.close()
        check(version == tracker.SCHEMA_VERSION == 1, f"user_version 0 -> 1 (got {version})")
        check({"applications", "gate_runs"} <= tables, "gate_runs added beside applications")
        check(old and old["status"] == "submitted" and old["company"] == "Old",
              "the existing application row is untouched")
        tracker.find("x"); tracker.find("y")
        check(len(tracker.all_applications()) == 1, "migrating again changes nothing")
    check.done()


def test_gate_rows_and_outcomes():
    check = Checks()
    with temp_db():
        result = gate.decide(test_gate.base(), test_gate.profile())
        rid = tracker.record_gate_run("https://example.invalid/1", portal="greenhouse",
                                      portal_method="host", result=result,
                                      record_json={"fields": []})
        row = tracker.gate_runs()[0]
        check(row["id"] == rid and row["verdict"] == "would_submit"
              and row["human_submitted"] == "unknown" and row["agent_submitted"] == 0,
              "a verdict is logged as unknown outcome, not agent-submitted")
        ena = json.loads(row["enablement_json"])
        check(ena == {"portal_allowlisted": True, "cap_remaining": 3, "reasons": []},
              "enablement stored apart from the verdict")
        tracker.set_gate_outcome(rid, human_submitted="y", edits=[], capture="exact",
                                 success_signal="greenhouse:confirmation")
        row = tracker.gate_runs("greenhouse")[0]
        check(row["human_submitted"] == "y" and json.loads(row["edits_json"]) == []
              and row["capture"] == "exact" and row["outcome_at"], "outcome recorded")
        tracker.set_gate_outcome(rid, human_submitted="maybe", edits=None, capture="none")
        check(tracker.gate_runs()[0]["human_submitted"] == "unknown", "anything but y/n is unknown")

        check(tracker.agent_submissions_today() == 0, "no agent submissions")
        with tracker._connect() as conn:
            conn.execute("UPDATE gate_runs SET agent_submitted = 1")
            conn.execute("INSERT INTO gate_runs (application_url, verdict, agent_submitted, "
                         "created_at) VALUES ('u', 'would_submit', 1, '2020-01-01T12:00:00+00:00')")
        check(tracker.agent_submissions_today() == 1, "counts today's, not an old one")
    check.done()


def test_no_answer_values_reach_gate_runs():
    check = Checks()
    eeo, elig, vet = "EEO-SENTINEL-4417", "ELIG-SENTINEL-2280", "VET-SENTINEL-9031"
    rec = test_gate.base()
    rec["fields"][1]["options"] = [elig, "No"]
    rec["plan"]["f1"]["value"] = elig
    rec["snapshot"]["f1"] = elig
    rec["fields"][2]["options"] = ["Male", eeo]           # base() already uses an EEO sentinel
    rec["plan"]["f3"]["value"] = eeo
    rec["snapshot"]["f3"] = eeo
    rec["fields"].append({"ref": "f20", "type": "select", "label": "Veteran status",
                          "options": [vet, "No"]})
    rec["categories"]["f20"] = "eeo"
    rec["flags"] = [{"ref": "f20", "label": "Veteran status", "category": "eeo",
                     "reason": f"Your answer is '{vet}' but this form offers [...]"}]
    rec["census"].append(test_gate.box("I identify as a protected veteran", checked=True))
    result = gate.decide(rec, test_gate.profile())
    stored = shadow.redacted(rec, result)
    with temp_db() as path:
        tracker.record_gate_run(rec["url"], portal="greenhouse", portal_method="host",
                                result=result, record_json=stored)
        rows = tracker.gate_runs()
        dump = json.dumps(rows)
        raw = path.read_bytes()
    print(f"  verdict: {result['content_verdict']}; reasons: {result['content_reasons']}")
    for s in (eeo, elig, vet, test_gate.SENTINEL_EEO):
        check(s not in dump and s.encode() not in raw, f"{s} appears nowhere in gate_runs or the file")
    fields = {f["ref"]: f for f in json.loads(rows[0]["record_json"])["fields"]}
    check(set(fields["f3"]) == {"ref", "label", "category", "profile_path", "source", "fit"}
          and fields["f3"]["profile_path"] == "eeo.gender",
          "a sensitive field is stored as field, profile line, source and fit")
    vet_box = json.loads(rows[0]["record_json"])["census"][-1]
    check("checked" not in vet_box and vet_box["checked_withheld"],
          "a ticked EEO checkbox's state is withheld")
    check.done()


if __name__ == "__main__":
    run_as_script(globals())
