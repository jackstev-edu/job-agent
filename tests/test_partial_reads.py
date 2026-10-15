"""
test_partial_reads.py — a group read in part is unreadable, and the submit hook goes quiet.

WHAT THIS DOES: A radio group some of whose options the page refuses to read
(a clearance question whose "Secret" and "Top Secret" options match the
password words, while "None" is read) must be left out of the snapshot, the
capture marked "inexact", and the run left unscored, except that a false
submit still counts. Then the real submit hook is installed on a page, a
password box is added after the scan, and the next submit must hand Python
an empty payload.
WHY IT MATTERS: The refused option may be the chosen one, so reading the
rest as "nothing chosen" could claim zero edits that weren't zero. And once
a password box is on the page, nothing on it is read, even by the hook.

Run from anywhere:  .venv/Scripts/python.exe tests/test_partial_reads.py
"""
from pathlib import Path

from _harness import TEST_HOME, Checks, mock_page, run_as_script
from agent import extract, outcome, score

CLEARANCE = {"ref": "g1-f0", "type": "radio", "label": "Security clearance level",
             "options": ["None", "Secret", "Top Secret"],
             "option_refs": ["g1-f0", "g1-f1", "g1-f2"]}


def _write(name: str, html: str) -> Path:
    path = TEST_HOME / name
    path.write_text(html, encoding="utf-8")
    return path


def test_a_partly_read_group_is_left_out_and_unscored():
    check = Checks()
    fields = [CLEARANCE]
    partial = {"g1-f0": False}                       # "None" read, the other two refused
    whole = {"g1-f0": False, "g1-f1": True, "g1-f2": False}
    check(extract.snapshot_from_raw(fields, partial) == {},
          "None read, Secret and Top Secret refused: the group is left out, not 'nothing chosen'")
    check(extract.partly_read(fields, partial), "  ...and the read is partial")
    check(extract.snapshot_from_raw(fields, whole) == {"g1-f0": "Secret"}
          and not extract.partly_read(fields, whole), "every option read: the group is recorded")
    check(extract.snapshot_from_raw(fields, {}) == {} and not extract.partly_read(fields, {}),
          "no option read: left out, unreadable, not partial")
    edits = outcome.compute_edits({"fields": fields, "plan": {}}, {"consent_actions": {}},
                                  extract.snapshot_from_raw(fields, partial))
    check(edits == [{"ref": "g1-f0", "label": "Security clearance level", "kind": "unreadable"}],
          "compute_edits calls it unreadable, so zero edits can't be claimed")

    row = lambda **k: {"outcome_at": "x", "capture": "inexact", "human_submitted": "y",
                       "edits_json": "[]", **k}
    city = '[{"ref": "t", "label": "City", "kind": "changed"}]'
    check(score.score_run(row(verdict="would_stop")) == score.UNKNOWN,
          "inexact, would_stop, submitted with no edits: unknown, not a false stop")
    check(score.score_run(row(verdict="would_stop", edits_json=city)) == score.UNKNOWN,
          "inexact, would_stop, an edit: unknown, not a match")
    check(score.score_run(row(verdict="would_submit")) == score.UNKNOWN,
          "inexact, would_submit, no edits: unknown, not a match")
    check(score.score_run(row(verdict="would_submit", edits_json=city)) == score.FALSE_SUBMIT
          and score.score_run(row(verdict="would_submit", human_submitted="n"))
          == score.FALSE_SUBMIT, "inexact, would_submit, an edit or not sent: still a false submit")
    check(score.score_run(row(verdict="would_stop", capture="exact")) == score.FALSE_STOP,
          "the same run read exactly is scored (so the checks above mean something)")
    check.done()


CLEARANCE_FORM = """<!doctype html><html><body><form>
  <label for="ci">City</label><input id="ci" name="city">
  <fieldset><legend>Security clearance level</legend>
    <label><input type="radio" name="clearance" value="n"> None</label>
    <label><input type="radio" name="clearance" value="s" checked> Secret</label>
    <label><input type="radio" name="clearance" value="t"> Top Secret</label></fieldset>
</form></body></html>"""


def test_a_clearance_radio_read_in_part_on_a_page():
    check = Checks()
    with mock_page(_write("partial_clearance.html", CLEARANCE_FORM)) as page:
        fields = extract.extract_fields(page, extract.ReadGuard())
        group = next(f for f in fields if f["type"] == "radio")
        raw = page.evaluate(extract._SNAPSHOT_JS, extract.read_items(fields))
        check(group["option_refs"][0] in raw and group["option_refs"][1] not in raw
              and group["option_refs"][2] not in raw,
              "the page reads None and refuses Secret and Top Secret")
        check(group["ref"] not in extract.snapshot(page, fields),
              "the snapshot leaves the group out (Secret is the one chosen)")
        polls = iter(range(3))
        watched = outcome.watch(page, record={"fields": fields},
                                stop_requested=lambda: next(polls, None) is None, poll_ms=50)
    check(watched["capture"] == "inexact", f"the watcher's capture is inexact ({watched['capture']})")
    check(group["ref"] not in (watched["final"] or {}) and (watched["final"] or {}).get(
        next(f["ref"] for f in fields if f.get("dom_name") == "city")) == "",
          "the city box is read; the group isn't")
    check.done()


HOOK_FORM = """<!doctype html><html><body>
  <form id="f" onsubmit="event.preventDefault()">
    <label for="fn">First name</label><input id="fn" name="first_name" value="Test">
    <label for="em">Email</label><input id="em" name="email" value="t@example.invalid">
    <button id="go" type="submit">Send</button>
  </form>
<script>
  window.addPassword = () => document.getElementById('f').insertAdjacentHTML('afterbegin',
    '<label for="pw">Account</label><input id="pw" type="password" value="HOOK-PW-SENTINEL-4410">');
</script></body></html>"""


def test_submit_hook_payload_is_empty_once_a_password_box_appears():
    check = Checks()
    with mock_page(_write("partial_hook.html", HOOK_FORM)) as page:
        fields = extract.extract_fields(page, extract.ReadGuard())
        got: list = []
        page.expose_binding("hookUnderTest", lambda source, raw: got.append(raw))
        page.evaluate(outcome._HOOK_JS, ["hookUnderTest", extract.read_items(fields)])
        page.click("#go")
        page.wait_for_timeout(200)
        before = list(got)
        got.clear()
        page.evaluate("window.addPassword()")
        page.click("#go")
        page.wait_for_timeout(200)
    print(f"  before {before}, after {got}")
    check(before and all(isinstance(p, dict) and "Test" in p.values() for p in before),
          "before the password box, a submit hands back the form (so the next check means something)")
    check(got and all(p == {} for p in got),
          "after a password box is added past the scan, every hook payload is {}")
    check("HOOK-PW-SENTINEL-4410" not in repr(got), "the password never reached Python")
    check.done()


if __name__ == "__main__":
    run_as_script(globals())
