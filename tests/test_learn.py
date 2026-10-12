"""
test_learn.py — `apply.py learn` suggests from recurring corrections and changes nothing.

WHAT THIS DOES: Seeds a temp JOB_AGENT_HOME with a profile and a corrections
log, runs `apply.py learn` as you would, and checks each recurring group gets
the right kind of suggestion. Eligibility, EEO and compensation corrections
must be flagged only, even a mis-classified one. One-offs must be ignored.
Afterwards no file has changed. Also checks that `fill`'s logger records the
profile line's name, but never a sensitive value.
WHY IT MATTERS: learn reads what you typed on real applications. It may point
at a profile line, but it must never write one or nudge a legal answer.

Run from anywhere:  .venv/Scripts/python.exe tests/test_learn.py
"""
import hashlib
import json
import os
import subprocess
import sys

from _harness import REPO, TEST_HOME, Checks, run_as_script

PROFILE = """\
personal:
  first_name: Jack
  last_name: Example
links:
  github: github.com/old
eligibility:
  authorized_to_work_us: true
eeo:
  gender: Prefer not to say
compensation:
  desired_base_min: 90000
"""

A, B, C = (f"https://example.invalid/job/{x}" for x in "abc")


def _row(url, label, category, kind, agent, final, **extra):
    return {"url": url, "label": label, "type": "text", "category": category,
            "kind": kind, "agent_value": agent, "final_value": final, **extra}


ROWS = [
    # Same wrong profile value fixed on two postings -> a profile edit.
    _row(A, "GitHub URL", "personal_fact", "overwrote", "github.com/old", "https://github.com/new"),
    _row(B, "GitHub profile", "personal_fact", "overwrote", "github.com/old", "https://github.com/new"),
    # Same blank filled the same way three times; not in the profile -> addition.
    *[_row(u, "Middle name", "personal_fact", "filled_blank", None, "Q.") for u in (A, B, C)],
    # Typed a value the profile already holds -> alias.
    *[_row(u, "Preferred first name *", "personal_fact", "filled_blank", None, "Jack") for u in (A, B)],
    # Sensitive, routed correctly: no values logged -> flagged only.
    *[_row(u, "Are you legally authorized to work in the US?", "work_authorization",
           "overwrote", None, None, profile_path="eligibility.authorized_to_work_us") for u in (A, B)],
    # Sensitive but MIS-classified as ordinary, so its values were logged.
    # Must still be flagged only, and its values never offered back.
    *[_row(u, "What is your gender?", "personal_fact", "overwrote", "Female", "Male") for u in (A, B)],
    *[_row(u, "Desired salary", "compensation", "filled_blank", None, None) for u in (A, B)],
    # Different answers each time -> no single suggestion.
    _row(A, "Pronouns", "preference", "filled_blank", None, "he/him"),
    _row(B, "Pronouns", "preference", "filled_blank", None, "He/Him/His"),
    # Answered by hand, values never logged -> nothing to suggest.
    *[_row(u, "How did you hear about us?", "per_company", "filled_blank", None, None) for u in (A, B)],
    # One posting only -> ignored.
    _row(A, "Favourite colour", "personal_fact", "filled_blank", None, "Teal"),
    # Two rows, but the same posting -> still not recurring.
    _row(C, "Fax number", "personal_fact", "filled_blank", None, "555"),
    _row(C, "Fax number", "personal_fact", "filled_blank", None, "555"),
]


def _digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _snapshot(*paths):
    return {str(p): _digest(p) for p in paths}


def test_learn_report():
    check = Checks()
    home = TEST_HOME / "learn-home"
    home.mkdir(exist_ok=True)
    (home / "profile.yaml").write_text(PROFILE, encoding="utf-8")
    log = home / "corrections.jsonl"
    log.write_text("\n".join(json.dumps(r) for r in ROWS) + "\nnot json at all\n",
                   encoding="utf-8")

    watched = [home / "profile.yaml", log] + sorted((REPO / "agent").glob("*.py"))
    before, files_before = _snapshot(*watched), sorted(home.rglob("*"))

    env = {k: v for k, v in os.environ.items() if k != "JOB_AGENT_PROFILE"}
    env.update(JOB_AGENT_HOME=str(home), PYTHONIOENCODING="utf-8")
    run = subprocess.run([sys.executable, str(REPO / "apply.py"), "learn"], cwd=REPO,
                         env=env, capture_output=True, encoding="utf-8", timeout=60)
    out = run.stdout
    print(out, run.stderr)
    lines = out.splitlines()

    def after(start):
        """The suggestion line under the group whose heading starts with `start`."""
        i = next((i for i, l in enumerate(lines) if l.strip().startswith(start)), None)
        return lines[i + 1].strip() if i is not None and i + 1 < len(lines) else ""

    check(run.returncode == 0, f"learn exits 0 (got {run.returncode})")
    check("1 unreadable line(s) skipped" in out, "malformed line counted, not fatal")
    check("suggested profile edit: `links.github`: 'github.com/old' -> "
          "'https://github.com/new'" in after("`links.github`"),
          "wrong profile value fixed twice -> profile edit for that line")
    check(after("'Middle name'").startswith("suggested profile addition")
          and "'Q.'" in after("'Middle name'"), "recurring blank -> profile addition")
    check("suggested alias" in after("'Preferred first name'")
          and "`personal.first_name`" in after("'Preferred first name'"),
          "value already in profile -> alias to that line")
    for start in ("`eligibility.authorized_to_work_us`", "`eeo.gender`",
                  "`compensation.desired_base_min`"):
        check(after(start).startswith("flagged only"), f"{start} flagged only")
    check("Male" not in out and "Female" not in out,
          "a mis-classified EEO answer's values are never echoed back")
    check("no single suggestion" in after("'Pronouns'"), "inconsistent answers -> no suggestion")
    check("nothing to suggest" in after("'How did you hear"),
          "hand-answered, unlogged values -> nothing to suggest")
    check("Favourite colour" not in out and "Fax number" not in out,
          "one-posting corrections ignored")

    check(_snapshot(*watched) == before, "profile, log and agent code unchanged")
    check(sorted(home.rglob("*")) == files_before, "no new files in JOB_AGENT_HOME")
    check.done()


def test_fill_logs_profile_line_not_sensitive_values():
    import apply
    from agent import config, extract

    check = Checks()
    fields = [{"ref": "f0", "label": "Are you legally authorized to work in the US?",
               "type": "radio"},
              {"ref": "f1", "label": "Middle name", "type": "text"},
              {"ref": "f2", "label": "Password", "type": "password"}]
    plan = {"f0": {"value": "Yes", "profile_path": "eligibility.authorized_to_work_us"},
            "f1": {"value": None}}
    categories = {"f0": "work_authorization", "f1": "personal_fact", "f2": "credential"}
    final = {"f0": "No", "f1": "Q.", "f2": "hunter2"}

    log = config.STATE_DIR / "corrections.jsonl"
    log.unlink(missing_ok=True)
    saved = extract.snapshot
    extract.snapshot = lambda page, fs: final
    try:
        apply._log_corrections(None, A, fields, plan, categories)
    finally:
        extract.snapshot = saved
    rows = [json.loads(l) for l in log.read_text(encoding="utf-8").splitlines()]
    log.unlink()
    for r in rows:
        print(f"  {r}")
    by_label = {r["label"]: r for r in rows}
    auth = by_label.get("Are you legally authorized to work in the US?", {})
    check(auth.get("profile_path") == "eligibility.authorized_to_work_us",
          "the profile line's name is logged")
    check(auth.get("agent_value") is None and auth.get("final_value") is None,
          "a work-authorization answer's values are not")
    check(by_label.get("Middle name", {}).get("final_value") == "Q.",
          "an ordinary field's value is logged")
    check("Password" not in by_label and "hunter2" not in json.dumps(rows),
          "password fields never logged")
    check(all(r.get("logged_at") for r in rows), "every row timestamped")
    check.done()


if __name__ == "__main__":
    run_as_script(globals())
