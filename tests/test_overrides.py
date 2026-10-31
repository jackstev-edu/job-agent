"""
test_overrides.py — Jack's own changes under a pause are recorded, locally.

WHAT THIS DOES: Runs the wizard in GUI mode on the same-address three-step
mock of tests/test_pick_up.py. At the gate pause on step 2 Jack corrects
two boxes (one the agent filled, one it left blank), and the watch's last
read before he moves the page on (a poll, as the GUI loop makes; or the
hook, when his press of the page's own Next hands the read over with no
poll at all) becomes two rows in corrections.jsonl under a temporary
state directory, with values, in the `fill` logger's shape. A legal
answer he gave logs no value; a password box on the page makes every
read empty so nothing is recorded; a formatting-only difference is
skipped. The row builder is shared with `fill`'s logger.
WHY IT MATTERS: Decision A (Jack, 2026-10-29 evening): "it should record
and learn from my manual override". Nothing here reaches a model or the
network, and nothing is reused until he approves a suggestion.

Run from anywhere:  .venv/Scripts/python.exe tests/test_overrides.py
"""
import json

from _harness import TEST_HOME, Checks, run_as_script
from agent import corrections, wizard
from test_pick_up import CITY_REQUIRED, STEPS, _heading, _page, _run

EMAIL2 = '<label for="em2">Email</label><input id="em2" name="email2">'
PASSWORD = '<label for="pw">Password</label><input id="pw" name="password" type="password">'


def _log_path():
    p = corrections.path()
    assert str(p).startswith(str(TEST_HOME)), "the log must live in the temporary state directory"
    return p


def _rows():
    p = _log_path()
    if not p.exists():
        return []
    return [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]


def _reset():
    _log_path().unlink(missing_ok=True)


def test_two_corrections_before_a_same_address_move_are_logged_from_the_last_poll():
    # Fails on 9ccf258: the wizard never logged a correction (only `fill` did).
    check = Checks()
    _reset()
    with _run(_page(city=CITY_REQUIRED + EMAIL2), auto_submit=lambda: False) as (page, w):
        out = w.run()
        check(out.status == wizard.WAITING and w.pending["kind"] == "gate" and w.pending.get("watch") is not None,
              f"the gate pause on step 2 carries a watch ({out.status})")
        check(page.input_value("#em2") == "test@example.invalid", "the agent filled the second Email box")
        page.fill("#em2", "other@example.invalid")      # Jack changes the agent's answer
        page.fill("#city", "Springfield")               # and fills what it left blank
        w.poll_watch()                                  # the GUI loop's 2 s poll
        page.evaluate("() => show(3)")                  # the page moves on with no press
        check(_heading(page) == "Step 3 of 3", "the tab shows step 3")
        out = w.continue_from({})
    rows = _rows()
    by_label = {r["label"]: r for r in rows}
    check(len(rows) == 2 and set(by_label) == {"Email", "City"}, f"two rows, the two boxes ({[r.get('label') for r in rows]})")
    check(by_label.get("Email", {}).get("kind") == "overwrote" and by_label["Email"].get("final_value") == "other@example.invalid"
          and by_label["Email"].get("agent_value") == "test@example.invalid",
          f"the overwrite carries both values ({by_label.get('Email')})")
    check(by_label.get("City", {}).get("kind") == "filled_blank" and by_label["City"].get("final_value") == "Springfield",
          f"the blank he filled carries his value ({by_label.get('City')})")
    check(all(r.get("log_version") == 2 and r.get("logged_at") and r.get("source") == "override"
              and r.get("portal") == "generic" and "profile_path" in r for r in rows),
          "the fill logger's shape, plus source and portal")
    check(any(l.startswith("recorded 2 correction(s) of yours") for l in out.log), f"the job log says so ({out.log[-4:]})")
    check.done()


def test_jacks_own_press_hands_the_read_over_without_a_poll():
    # Fails on 9ccf258 (no watch at all). The hook on the paused page: no
    # poll is made, Jack presses the page's own Next, and the read taken at
    # that press is the last good one.
    check = Checks()
    _reset()
    with _run(_page(city=CITY_REQUIRED + EMAIL2), auto_submit=lambda: False) as (page, w):
        out = w.run()
        page.fill("#em2", "other@example.invalid")
        page.fill("#city", "Springfield")
        page.click("#n2")                               # his press: the hook reads the form first
        check(_heading(page) == "Step 3 of 3", "the tab shows step 3")
        watch = w.pending.get("watch")
        check(watch is not None and watch.reads == 0 and watch.read() is not None,
              f"no poll was made; the hook's read is there ({watch and watch.reads}, {watch and watch.hook.capture})")
        out = w.continue_from({})
    labels = sorted(r["label"] for r in _rows())
    check(labels == ["City", "Email"], f"both corrections logged from the hook's read ({labels})")
    check.done()


def test_a_legal_field_jack_answered_logs_no_value():
    # Fails on 9ccf258 (nothing logged). A non-browser test of the row
    # builder with a watch's read: the sensitive row records that he
    # answered, never what.
    check = Checks()
    fields = [{"ref": "g1-f0", "label": "Are you legally authorized to work in the US?", "type": "radio",
               "options": ["Yes", "No"], "option_refs": ["g1-f0", "g1-f1"]},
              {"ref": "g1-f2", "label": "Middle name", "type": "text"}]
    plan = {"g1-f0": {"value": "Yes", "profile_path": "eligibility.authorized_to_work_us", "source": "profile"},
            "g1-f2": {"value": None}}
    categories = {"g1-f0": "work_authorization", "g1-f2": "personal_fact"}
    final = {"g1-f0": "No", "g1-f2": "Q."}
    rows, unread = corrections.rows_for("https://a.example/1", fields, plan, categories, final,
                                        results={"g1-f0": {"filled": True}}, source="override", portal="generic")
    by_label = {r["label"]: r for r in rows}
    auth = by_label.get("Are you legally authorized to work in the US?", {})
    check(auth.get("kind") == "overwrote" and auth.get("agent_value") is None and auth.get("final_value") is None
          and auth.get("profile_path") == "eligibility.authorized_to_work_us",
          f"the legal row: that he answered, the line's name, no value ({auth})")
    check(by_label.get("Middle name", {}).get("final_value") == "Q."
          and by_label["Middle name"].get("kind") == "filled_blank", "the ordinary row keeps its value")
    check("No" not in json.dumps([auth]) and not unread, "the legal answer is nowhere in the row")
    # A failed fill he then completed: the plan had a value, the result says it didn't land.
    rows, _ = corrections.rows_for("https://a.example/1",
                                   [{"ref": "g1-f3", "label": "Start date", "type": "text"}],
                                   {"g1-f3": {"value": "2026-11"}}, {"g1-f3": "personal_fact"}, {"g1-f3": "11/2026"},
                                   results={"g1-f3": {"filled": False, "reason": "not_taken"}}, source="override")
    check(rows and rows[0]["kind"] == corrections.FILLED_AFTER_FAILURE and rows[0]["final_value"] == "11/2026"
          and rows[0]["agent_value"] is None,
          f"a value typed after a failed fill is its own kind, with nothing claimed for the agent ({rows})")
    check.done()


def test_a_password_box_is_never_read():
    # Fails on 9ccf258 (no agent.corrections). The watch is armed on a page
    # the agent filled; a password box then appears (a sign-in dialog) and
    # Jack types in it and corrects a box: the snapshot policy reads
    # nothing at all while a password box is in the document, by the poll
    # and by the hook on his press alike, so no row is written and the
    # password never reaches Python; the builder also drops a password
    # field by type.
    check = Checks()
    _reset()
    with _run(_page(city=CITY_REQUIRED + EMAIL2), auto_submit=lambda: False) as (page, w):
        out = w.run()
        check(out.status == wizard.WAITING and w.pending["kind"] == "gate", f"the gate pauses ({out.status})")
        page.evaluate("""() => { const f = document.querySelector('form');
            f.insertAdjacentHTML('afterbegin', '<label for="pw">Password</label><input id="pw" name="password" type="password">'); }""")
        page.fill("#pw", "hunter2-never-logged")
        page.fill("#em2", "other@example.invalid")
        page.fill("#city", "Springfield")
        w.poll_watch()
        watch = w.pending.get("watch")
        check(watch is not None and watch.reads == 1 and watch.last == {},
              f"the poll read nothing with a password box on the page ({watch and watch.last})")
        page.click("#n2")                               # his press: the hook's read is empty too
        check(watch.hook.final in (None, {}), f"the hook read nothing either ({watch.hook.final})")
        out = w.continue_from({})
        text = _log_path().read_text(encoding="utf-8") if _log_path().exists() else ""
    check(text == "", f"no row at all, no password anywhere ({text[:80]!r})")
    check(any("nothing recorded" in l or "no correction recorded" in l for l in out.log),
          f"the job log says nothing was recorded ({[l for l in out.log if 'record' in l]})")
    rows, _ = corrections.rows_for("https://a.example/3", [{"ref": "g1-f0", "label": "Password", "type": "password"}],
                                   {"g1-f0": {"value": None}}, {"g1-f0": "credential"}, {"g1-f0": "hunter2"})
    check(rows == [], "a password field never becomes a row, whatever the read holds")
    check.done()


def test_formatting_only_differences_are_skipped():
    # A guard (the `fill` logger already skipped these): the shared row
    # builder still does. Mutation check: with learn.same_answer forced
    # False the phone row appears.
    check = Checks()
    fields = [{"ref": "g1-f0", "label": "Phone", "type": "tel"},
              {"ref": "g1-f1", "label": "Country", "type": "combobox"},
              {"ref": "g1-f2", "label": "City", "type": "text"}]
    plan = {"g1-f0": {"value": "+1 555 010 0199"}, "g1-f1": {"value": "Exampleland"}, "g1-f2": {"value": "Springfield"}}
    cats = {r: "personal_fact" for r in plan}
    final = {"g1-f0": "(555) 010-0199", "g1-f1": "", "g1-f2": "Shelbyville"}
    rows, _ = corrections.rows_for("https://a.example/2", fields, plan, cats, final, source="override")
    check([r["label"] for r in rows] == ["City"], f"only the real change is a row ({[r['label'] for r in rows]})")
    check.done()


if __name__ == "__main__":
    run_as_script(globals())
