"""
test_save_profile.py — Save to profile is Jack's click, and the only profile writer.

WHAT THIS DOES: Drives approve.apply_profile_line and gui.State's
save_to_profile on a temporary profile: a save writes exactly one line,
as a quoted string, with every comment kept and a backup beside it; a
mapping or list node is never overwritten; backups are capped at five;
two saves racing on one stamp leave one written and one refused under
the lock; aliases.yaml is counted and renamed, nothing learned from it,
and the alias route is gone from the router and the profile; the
Suggestions routes and panel are gone from the GUI; the wizard reloads
the profile before a page when the file changed since the job loaded
it; and State.save_to_profile writes only a profile candidate (never a
legal entry, a choice or a plain answer, never a candidate whose line
moved on), drops it from memory once saved, and flags the reload.
WHY IT MATTERS: Decision 5 of 2026-11-01: profile.yaml is NEVER changed
automatically; only Jack's click writes it, through this one path.

Run from anywhere:  .venv/Scripts/python.exe tests/test_save_profile.py
"""
import threading
import time
from unittest import mock

import yaml

from _harness import TEST_HOME, Checks, run_as_script
from agent import approve, gui, memory, pipeline, template, wizard
from agent.profile import Profile
from test_gui import _req


def _profile(name):
    """A profile from the blank template with a few lines filled: comments intact, nothing personal."""
    path = TEST_HOME / f"save-{name}.yaml"
    text = template.BLANK_PROFILE.replace("  first_name:                  # REQUIRED", "  first_name: Test                   # REQUIRED")
    text = text.replace("  github:\n", "  github: github.com/old\n")
    text = text.replace("  preferred_name:\n", "  preferred_name: Tee\n")
    path.write_text(text, encoding="utf-8")
    for old in approve.backups_of(path):
        old.unlink()
    approve.aliases_path(path).unlink(missing_ok=True)
    return path


def test_save_writes_exactly_one_quoted_line_with_comments_kept_and_a_backup():
    # Fails on fe30a9a: the value was written as a plain scalar (`middle_name: Q.`).
    check = Checks()
    path = _profile("apply")
    before = path.read_text(encoding="utf-8")
    stamp = approve.profile_stamp(path)
    backup = approve.apply_profile_line(path, "personal.middle_name", "Q.", stamp)
    after = path.read_text(encoding="utf-8")
    diff = [(b, a) for b, a in zip(before.splitlines(), after.splitlines()) if b != a]
    check(len(before.splitlines()) == len(after.splitlines()) and len(diff) == 1
          and diff[0][0].strip() == "middle_name:" and diff[0][1].strip() == 'middle_name: "Q."',
          f"exactly one line changed, the value quoted ({diff})")
    check(after.count("#") == before.count("#") and "# REQUIRED" in after and "#  YOUR PROFILE" in after,
          "every comment kept")
    check(backup.exists() and backup.read_text(encoding="utf-8") == before and backup.parent == path.parent
          and backup.name.startswith(path.name + ".bak-"), f"a timestamped backup holds the old file ({backup.name})")
    check(Profile.load(path).get("personal.middle_name") == "Q." and Profile.load(path).get("personal.first_name") == "Test",
          "the new value loads; the rest is as it was")
    # A value that reads like YAML stays the text Jack typed.
    stamp = approve.profile_stamp(path)
    approve.apply_profile_line(path, "personal.preferred_name", "yes", stamp)
    check(Profile.load(path).get("personal.preferred_name") == "yes" and 'preferred_name: "yes"' in path.read_text(encoding="utf-8"),
          "a yes stays the word yes, not a boolean")
    for line in ("eligibility.authorized_to_work_us", "eeo.gender", "compensation.desired_base_min",
                 "per_company.defaults.referral_source", "documents.resume_default", "nonsense"):
        try:
            approve.apply_profile_line(path, line, "x", approve.profile_stamp(path))
            refused = False
        except approve.Refused:
            refused = True
        check(refused, f"a click never writes `{line}`")
    check.done()


def test_a_mapping_or_list_node_is_never_overwritten():
    # Fails on fe30a9a: `personal` (a mapping) was replaced by the string.
    check = Checks()
    path = _profile("nodes")
    before = path.read_text(encoding="utf-8")
    for line in ("personal", "experience", "experience[0]", "voice.canned_answers"):
        try:
            approve.apply_profile_line(path, line, "x", approve.profile_stamp(path))
            refused = False
        except approve.Refused:
            refused = True
        check(refused, f"`{line}` is a section, a list or a whole entry: refused")
    check(path.read_text(encoding="utf-8") == before and not approve.backups_of(path), "nothing written, no backup")
    stamp = approve.profile_stamp(path)
    approve.apply_profile_line(path, "experience[0].company", "Example Co", stamp)
    check(Profile.load(path).get("experience[0].company") == "Example Co", "one line of a list entry can still be written")
    check.done()


def test_backups_keep_the_last_five():
    # Fails on fe30a9a: seven writes left seven backups.
    check = Checks()
    path = _profile("backups")
    kept = []
    for i in range(7):
        kept.append(approve.apply_profile_line(path, "personal.middle_name", f"V{i}", approve.profile_stamp(path)))
    now = approve.backups_of(path)
    check(len(now) == 5 and [p.name for p in now] == [p.name for p in kept[-5:]],
          f"five newest backups stay, the two oldest go ({len(now)})")
    check(Profile.load(path).get("personal.middle_name") == "V6", "the last value is in the file")
    check.done()


def test_one_lock_from_the_check_to_the_write():
    # Fails on fe30a9a: with no lock both writers pass the stamp check and both write.
    check = Checks()
    path = _profile("lock")
    stamp = approve.profile_stamp(path)
    real = approve._atomic_write

    def slow_write(p, text):
        time.sleep(0.4)
        real(p, text)
    outcomes = []

    def save(value):
        try:
            approve.apply_profile_line(path, "personal.middle_name", value, stamp)
            outcomes.append(("written", value))
        except approve.Changed:
            outcomes.append(("changed", value))
    with mock.patch.object(approve, "_atomic_write", slow_write):
        a, b = threading.Thread(target=save, args=("A",)), threading.Thread(target=save, args=("B",))
        a.start(); time.sleep(0.05); b.start(); a.join(); b.join()
    check(sorted(o[0] for o in outcomes) == ["changed", "written"], f"one write, one refusal on the stale stamp ({outcomes})")
    check(Profile.load(path).get("personal.middle_name") == "A", "the first click's value is in the file")
    check.done()


def test_aliases_are_retired_counted_and_never_learned():
    # Fails on fe30a9a: no retire_aliases; Profile.aliases and the router's alias route existed.
    check = Checks()
    path = _profile("retire")
    approve.aliases_path(path).write_text("nickname: personal.preferred_name\ncountry: personal.country\n", encoding="utf-8")
    count = approve.retire_aliases(path)
    retired = approve.aliases_path(path).with_name("aliases.yaml.retired")
    check(count == 2 and retired.exists() and not approve.aliases_path(path).exists()
          and "nickname" in retired.read_text(encoding="utf-8"), f"two entries counted, the file renamed ({count})")
    check(approve.retire_aliases(path) is None, "no file: nothing to retire")
    check(memory.Learned.load().entries == [], "nothing learned from it")
    prof = Profile.load(path)
    check(not hasattr(prof, "aliases") and not hasattr(pipeline, "_by_alias") and not hasattr(approve, "add_alias"),
          "the alias route is gone from the profile, the router and the writer")
    field = {"ref": "g1-f0", "label": "Nickname", "type": "text", "name": "nick"}
    plan, flags, for_model = pipeline.route([field], {"g1-f0": "personal_fact"}, prof, set())
    check("g1-f0" not in plan and any(f["ref"] == "g1-f0" for f in for_model),
          "a question the old alias named goes to the model, not the line")
    check.done()


def test_the_suggestions_routes_and_panel_are_gone():
    # Fails on fe30a9a: the routes answered 200 and /state carried "suggestions".
    check = Checks()
    state = gui.State()
    state.profile_path = _profile("gone")
    server = gui.Server(state).start()
    try:
        for route in ("/suggestion/apply", "/suggestion/dismiss"):
            status, _, body = _req(server, "POST", route, {"id": "x"}, origin=f"http://127.0.0.1:{server.port}")
            check(status == 404, f"{route}: no such action ({status})")
        status, _, body = _req(server, "GET", "/state")
        check(status == 200 and "suggestions" not in body, "/state carries no suggestions")
        status, _, page = _req(server, "GET", "/", token=None)
        check("Suggestions" not in page and "/suggestion/" not in page, "the page has no panel")
        check(not hasattr(state, "refresh_suggestions") and not hasattr(state, "apply_suggestion"), "the state has no suggestion methods")
    finally:
        server.stop()
    check.done()


def test_the_wizard_reloads_the_profile_before_a_page_when_the_file_changed():
    # Fails on fe30a9a: no _reload_if_changed; a running job kept the profile it started with.
    check = Checks()
    path = _profile("reload")
    loads = []

    def reload():
        loads.append(1)
        return Profile.load(path)
    w = wizard.Wizard(None, Profile.load(path), url="https://a.example/1", portal="generic", portal_method="default",
                      company="", role="", resume=None, posting_text="", pauser=None,
                      echo=lambda *a, **k: None, reload_profile=reload)
    check(w._reload_if_changed() is False and not loads, "an unchanged file: no reload")
    approve.apply_profile_line(path, "personal.middle_name", "Q.", approve.profile_stamp(path))
    check(w._reload_if_changed() is True and len(loads) == 1 and w.prof.get("personal.middle_name") == "Q."
          and any("reloaded before this page" in l for l in w.out.log), "a changed file: reloaded once, and the log says so")
    check(w._reload_if_changed() is False and len(loads) == 1, "then unchanged again")
    w2 = wizard.Wizard(None, Profile.load(path), url="https://a.example/1", portal="generic", portal_method="default",
                       company="", role="", resume=None, posting_text="", pauser=None, echo=lambda *a, **k: None)
    check(w2._reload_if_changed() is False, "with no reloader (fill, tests): nothing")
    check.done()


def _entry(store, kind, line=None, legal=False, answer="Tess", line_value="Tee"):
    e = {"id": memory.entry_id("k", kind, memory.ANY, answer), "kind": kind, "key": "k", "question": "Preferred first name",
         "type": "text", "options": [], "answer": answer, "line": line, "legal": legal, "scope": memory.ANY, "host": "",
         "portal": "", "first_seen": "t", "last_seen": "t", "seen": 1, "uses": 0, "posting": ""}
    if line_value is not None:
        e["line_value"] = line_value
    store.entries.append(e)
    store.save()
    return e


def test_save_to_profile_writes_only_a_candidate_and_drops_it():
    # Fails on fe30a9a: no State.save_to_profile.
    check = Checks()
    path = _profile("state")
    state = gui.State()
    state.profile_path = path
    state.memory_path = TEST_HOME / "save-memory.yaml"
    state.memory_path.unlink(missing_ok=True)
    store = memory.Learned.load(state.memory_path)
    cand = _entry(store, memory.CANDIDATE, line="personal.preferred_name")
    legal = _entry(store, memory.ANSWER, line="eeo.gender", legal=True, answer="SENTINEL-EEO", line_value=None)
    choice = _entry(store, memory.CHOICE, line="personal.country", answer="Canada", line_value="CA")
    plain = _entry(store, memory.ANSWER, answer="Portal answer", line_value=None)
    stale = _entry(store, memory.CANDIDATE, line="links.github", answer="github.com/new", line_value="github.com/other")
    before = path.read_text(encoding="utf-8")
    for what, e in (("a legal entry", legal), ("a choice mapping", choice), ("a plain answer", plain), ("a stale candidate", stale)):
        res = state.save_to_profile(e["id"])
        check(res.get("ok") is False and path.read_text(encoding="utf-8") == before, f"{what}: refused, nothing written ({res})")
    check(state.save_to_profile("nope").get("ok") is False and state.take_profile_changed() is False, "an unknown id: nothing")
    res = state.save_to_profile(cand["id"])
    text = path.read_text(encoding="utf-8")
    check(res.get("ok") is True and 'preferred_name: "Tess"' in text and text.count("Tess") == 1,
          f"the candidate is written, quoted, once ({res})")
    check(state.take_profile_changed() is True and state.take_profile_changed() is False, "the reload is flagged once")
    after = memory.Learned.load(state.memory_path)
    check(after.get(cand["id"]) is None and len(after.entries) == 4, "the saved candidate leaves memory; the rest stay")
    check("SENTINEL-EEO" not in text, "no legal value anywhere near the profile")
    check.done()


if __name__ == "__main__":
    run_as_script(globals())
