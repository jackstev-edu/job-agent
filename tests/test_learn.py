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
from pathlib import Path

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


# The shape of the first real corrections log (one Greenhouse application),
# anonymised: same types, categories and kinds, made-up labels and values.
# Every row was a false correction. Four sensitive react-select dropdowns and
# one ordinary one read back blank because version 1 read the search box, and
# a phone number differed only by the page's input mask. Repeated on a second
# posting here so they'd recur; real ones would once more applications exist.
def _v1_greenhouse_rows(url):
    return [
        _row(url, "Country", "personal_fact", "overwrote", "Exampleland", ""),
        {**_row(url, "Phone", "personal_fact", "overwrote", "+1 555 010 0199", "(555) 010-0199"),
         "type": "tel"},
        _row(url, "Will you require sponsorship?", "work_authorization", "overwrote", None, None,
             profile_path="eligibility.requires_sponsorship_future"),
        _row(url, "Gender", "eeo", "overwrote", None, None, profile_path="eeo.gender"),
        _row(url, "Are you a veteran?", "eeo", "overwrote", None, None,
             profile_path="eeo.veteran_status"),
        _row(url, "Disability status", "eeo", "overwrote", None, None,
             profile_path="eeo.disability_status"),
    ]


def test_false_corrections_from_real_data_are_set_aside():
    from agent import learn
    check = Checks()
    rows = []
    for url in (A, B):
        for r in _v1_greenhouse_rows(url):
            if r["label"] != "Phone":
                r["type"] = "combobox"
            rows.append(r)
    # One genuine correction on the same two postings, so we can see real
    # signal still gets through the filter.
    rows += [_row(u, "GitHub URL", "personal_fact", "overwrote", "github.com/old",
                  "https://github.com/new") for u in (A, B)]
    result = learn.analyse(rows, {"links": {"github": "github.com/old"}})
    text = learn.render(rows, 0, result, Path("corrections.jsonl"))
    print(text)
    check(result["set_aside"] == {
        "custom dropdown logged before its value could be read back": 10,
        "same answer, formatted differently": 2},
        f"all 12 false rows set aside, by reason (got {result['set_aside']})")
    names = [e["name"] for e in result["by_line"] + result["by_question"]]
    check(names == ["links.github"], f"only the genuine correction is reported (got {names})")
    check("-> ''" not in text and "set `" not in text,
          "never suggests setting a profile line to blank")
    check(not any(n.startswith(("eeo.", "eligibility.")) for n in names),
          "no EEO or eligibility flags from dropdowns you never changed")
    check("Set aside 12" in text, "the report says how many were set aside and why")

    # Version 2 rows are re-checked on their values instead.
    v2 = [{**_row(u, "Country", "personal_fact", "overwrote", "Exampleland", ""),
           "type": "combobox", "log_version": 2} for u in (A, B)]
    v2_ok = [{**_row(u, "Country", "personal_fact", "overwrote", "Exampleland", "Otherland"),
              "type": "combobox", "log_version": 2} for u in (A, B)]
    check(learn.analyse(v2, None)["set_aside"] == {"custom dropdown read back blank": 2},
          "a v2 dropdown that reads back blank is still set aside")
    check(learn.analyse(v2_ok, None)["by_question"][0]["name"] == "Country",
          "a v2 dropdown with a real new value is kept")
    check.done()


def test_logger_skips_formatting_and_unreadable_dropdowns():
    import apply
    from agent import config, extract

    check = Checks()
    fields = [{"ref": "f0", "label": "Phone", "type": "tel"},
              {"ref": "f1", "label": "Country", "type": "combobox"},
              {"ref": "f2", "label": "Gender", "type": "combobox"},
              {"ref": "f3", "label": "City", "type": "text"}]
    plan = {"f0": {"value": "+1 555 010 0199"}, "f1": {"value": "Exampleland"},
            "f2": {"value": "Decline to self-identify", "profile_path": "eeo.gender"},
            "f3": {"value": "Springfield"}}
    categories = {"f0": "personal_fact", "f1": "personal_fact", "f2": "eeo",
                  "f3": "personal_fact"}
    final = {"f0": "(555) 010-0199", "f1": "", "f2": "", "f3": "Shelbyville"}

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
    check([r["label"] for r in rows] == ["City"],
          "only the real change is logged: not the reformatted phone, not the blank dropdowns")
    check(all(r.get("log_version") == 2 for r in rows), "rows carry log_version 2")
    check.done()


if __name__ == "__main__":
    run_as_script(globals())
