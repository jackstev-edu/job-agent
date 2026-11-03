"""
test_auto_capture.py — the wizard's structure snapshots (Jack, 2026-11-02, item 4).

WHAT THIS DOES: Runs the wizard over a three-page mock in a headless
browser and finds one structure file per page scanned in the snapshots
folder it was given, each named by date, job id, step and host, holding
no input value and none of the profile's strings, and named in the job's
log; shows a page holding a password box writes none, that a dump that
raises leaves the job running with one log line, and that snapshot.dump
retries once on a navigation ("Execution context was destroyed") and
otherwise returns a fixed error category, never the message.
WHY IT MATTERS: The next live run must yield the fixtures the Workday
widgets are built from (the next round), without a value or a password
page ever reaching the repo folder.

Run from anywhere:  .venv/Scripts/python.exe tests/test_auto_capture.py
"""
import io
import json
import re
import shutil
import time
from contextlib import redirect_stdout
from unittest import mock

from _fill_run import _headless, classify_by_label, map_from_profile
from _harness import TEST_HOME, Checks, mock_page, run_as_script
from agent import ledger, pause, snapshot, wizard
from test_pick_up import CITY_OPTIONAL, _page
from test_wizard import HOST, _profile, _routes

JOB = "ab12cd34ef56"


def _wizard(page, folder, start="/steps", auto_submit=lambda: True):
    pauser = pause.GuiPauser(notify_fn=lambda t, b, echo=print: None, echo=lambda *a, **k: None)
    return wizard.Wizard(page, _profile(), url=f"{HOST}{start}", portal="generic", portal_method="default",
                         company="Example Co", role="Engineer", resume=None, posting_text="",
                         pauser=pauser, echo=lambda *a, **k: None, auto_submit=auto_submit,
                         job_id=JOB, snapshot_dir=folder)


def test_a_three_page_run_writes_one_structure_file_per_page_with_no_value_in_it():
    # Fails on abb7fd0: Wizard takes no job_id / snapshot_dir and writes nothing.
    check = Checks()
    folder = TEST_HOME / "snaps-run"
    shutil.rmtree(folder, ignore_errors=True)
    with _headless(_routes({"/steps": _page(city=CITY_OPTIONAL)})) as ctx:
        ctx.add_init_script(ledger.ACTIVITY_INIT_SCRIPT)
        page = ctx.new_page()
        page.goto(f"{HOST}/steps")
        with mock.patch("agent.classify.classify_fields", classify_by_label), \
                mock.patch("agent.mapper.map_fields", map_from_profile), \
                mock.patch.object(wizard, "FORM_WAIT_S", 1), redirect_stdout(io.StringIO()):
            w = _wizard(page, folder)
            out = w.run()
    check(out.status == "submitted", f"the mock run ends submitted ({out.status}: {out.reason})")
    files = sorted(folder.glob("*.json"))
    today = time.strftime("%Y-%m-%d")
    check(len(files) == 3 and [f.name for f in files]
          == [f"{today}-{JOB}-0{n}-scan-apply.example-co.com.json" for n in (1, 2, 3)],
          f"three files, one per page scanned, named by date, job id, step and host ({[f.name for f in files]})")
    written = [line[len("snapshot written: "):] for line in out.log if line.startswith("snapshot written: ")]
    check(written == [f.name for f in files], f"the job log names each file ({written})")
    for f in files:
        text = f.read_text(encoding="utf-8")
        data = json.loads(text)
        check(data.get("elements") and '"value"' not in text and "test@example.invalid" not in text
              and "Springfield" not in text and "Example Street" not in text and '"taken"' in text,
              f"{f.name}: structure only, no input value and no profile string")
        labels = [e.get("label") for e in data["elements"] if e.get("tag") == "input"]
        check(any(labels), f"{f.name}: the inputs carry their labels ({labels})")
    check(not any(l.startswith("snapshot not written") for l in out.log), "no failure logged")
    check.done()


PW_PAGE = """<!doctype html><html><body><h1>Create your account</h1><form>
<label for="em">Email</label><input id="em" type="email">
<label for="pw">Password</label><input id="pw" type="password" autocomplete="new-password">
<button type="submit">Create account</button></form></body></html>"""
PLAIN_PAGE = """<!doctype html><html><body><h1>Step</h1><form>
<label for="fn">First Name</label><input id="fn" name="first_name"></form></body></html>"""


def _write(name, html):
    p = TEST_HOME / name
    p.write_text(html, encoding="utf-8")
    return p


def test_a_password_page_writes_none_and_a_dump_failure_never_stops_the_job():
    # Fails on abb7fd0: no Wizard._capture.
    check = Checks()
    folder = TEST_HOME / "snaps-pw"
    shutil.rmtree(folder, ignore_errors=True)
    with mock_page(_write("capture-pw.html", PW_PAGE)) as page:
        w = _wizard(page, folder)
        w._capture("scan")
        w._capture("pause-account")
        check(not folder.exists() or not list(folder.glob("*.json")), "a page holding a password box: no file")
        check(not any("snapshot" in l for l in w.out.log), "and nothing logged about it")
    with mock_page(_write("capture-plain.html", PLAIN_PAGE)) as page:
        w = _wizard(page, folder)
        with mock.patch.object(snapshot, "dump", side_effect=RuntimeError("boom-SENTINEL")):
            w._capture("scan")
        check(not list(folder.glob("*.json")), "a dump that raises writes nothing")
        check(any(l == "snapshot not written (RuntimeError)" for l in w.out.log)
              and "SENTINEL" not in json.dumps(w.out.log), f"one log line with the type only ({w.out.log})")
        with mock.patch.object(snapshot, "dump", return_value={"error": snapshot.NAVIGATING, "elements": []}):
            w._capture("scan")
        check(any(l == "snapshot not written (navigating)" for l in w.out.log), "a fixed error category is logged as is")
        w._capture("scan")
        files = list(folder.glob("*.json"))
        check(len(files) == 1 and re.fullmatch(rf"\d{{4}}-\d{{2}}-\d{{2}}-{JOB}-01-scan-page\.json", files[0].name),
              f"the plain page is written afterwards, numbered from the files written ({[f.name for f in files]})")
        w._snaps = wizard.SNAPSHOT_CAP
        w._capture("scan")
        check(len(list(folder.glob("*.json"))) == 1, "the per-job cap stops further files")
    check.done()


def test_a_capture_strips_the_jobs_typed_values_and_every_learned_answer():
    # Jack, 2026-11-03: snapshots also strip learned answers and the
    # current fill's typed values. Fails on d9c5b10 (dump took no extra
    # values; the wizard passed none); the unreadable case fails on ba21953.
    from agent import memory
    check = Checks()
    folder = TEST_HOME / "snaps-extra"
    shutil.rmtree(folder, ignore_errors=True)
    prior = memory.path().read_bytes() if memory.path().exists() else None     # put back whatever the shared home held
    store = memory.Learned.load()
    # A label of four words: a shorter one is removed on load (item 2's rule).
    store.entries.append({"id": "t-extra", "kind": "answer", "key": "[\"what is your favourite paint colour\", \"text\", []]",
                          "question": "What is your favourite paint colour", "type": "text", "options": [],
                          "answer": "LEARNED-EXTRA-SENTINEL", "line": None, "legal": False, "scope": "any",
                          "host": "", "portal": "", "category": "personal_fact", "first_seen": "2026-11-03T00:00:00",
                          "last_seen": "2026-11-03T00:00:00", "seen": 1, "uses": 0, "posting": "", "change": "filled_blank"})
    store.save()
    seen: list[list] = []
    real = snapshot.dump

    def spy(page, profile_data, extra_values=()):
        seen.append(list(extra_values))
        return real(page, profile_data, extra_values)

    with _headless(_routes({"/steps": _page(city=CITY_OPTIONAL)})) as ctx:
        ctx.add_init_script(ledger.ACTIVITY_INIT_SCRIPT)
        page = ctx.new_page()
        page.goto(f"{HOST}/steps")
        with mock.patch("agent.classify.classify_fields", classify_by_label), \
                mock.patch("agent.mapper.map_fields", map_from_profile), \
                mock.patch.object(wizard, "FORM_WAIT_S", 1), mock.patch.object(snapshot, "dump", side_effect=spy), \
                redirect_stdout(io.StringIO()):
            w = _wizard(page, folder)
            out = w.run()
    check(out.status == "submitted", f"the mock run ends submitted ({out.status}: {out.reason})")
    check(len(seen) >= 3 and all("LEARNED-EXTRA-SENTINEL" in s for s in seen),
          f"every capture carries the learned answer ({len(seen)} captures)")
    check(any("Test" in s for s in seen[1:]), "a later capture carries the first page's read-back value (the typed first name)")
    # Fold of 99c5a77's review: learned.yaml that didn't parse means the
    # learned answers cannot be stripped, so no file is written.
    try:
        memory.path().write_text("entries: [unclosed", encoding="utf-8")
        folder2 = TEST_HOME / "snaps-unreadable"
        shutil.rmtree(folder2, ignore_errors=True)
        with mock_page(_write("capture-plain-2.html", PLAIN_PAGE)) as page:
            w = _wizard(page, folder2)
            w._capture("scan")
            check(not folder2.exists() or not list(folder2.glob("*.json")), "learned.yaml unreadable: no snapshot file")
            check(any(l == "snapshot not written (the values to strip could not all be read)" for l in w.out.log),
                  f"one log line says why ({[l for l in w.out.log if 'snapshot' in l]})")
    finally:
        # The shared test home goes back to what it held: never an unreadable
        # store, never the planted entry.
        if prior is None:
            memory.path().unlink(missing_ok=True)
        else:
            memory.path().write_bytes(prior)
    check.done()


class _FakePage:
    def __init__(self, raises):
        self.raises, self.calls, self.url = list(raises), 0, "https://x.example/apply?x=1#f"

    def evaluate(self, script, arg=None):
        self.calls += 1
        if self.raises:
            raise self.raises.pop(0)
        return {"title": "T", "elements": [], "total": 0, "frames": 0, "shadow_hosts": 0}

    def wait_for_timeout(self, ms):
        pass


def test_dump_retries_once_on_a_destroyed_context_and_otherwise_returns_a_fixed_error():
    # Fails on abb7fd0: dump raised on the first failure.
    check = Checks()
    destroyed = Exception("Execution context was destroyed, most likely because of a navigation. SECRET-MSG")
    once = _FakePage([destroyed])
    got = snapshot.dump(once, {})
    check(not got.get("error") and once.calls == 2 and got["url"] == "https://x.example/apply",
          "one navigation: retried once and dumped")
    twice = _FakePage([destroyed, destroyed])
    got = snapshot.dump(twice, {})
    check(got.get("error") == snapshot.NAVIGATING and got["elements"] == [] and twice.calls == 2
          and "SECRET-MSG" not in json.dumps(got), "two navigations: a fixed error, no message text")
    other = _FakePage([ValueError("SECRET-MSG")])
    got = snapshot.dump(other, {})
    check(got.get("error") == "unreadable:ValueError" and other.calls == 1 and "SECRET-MSG" not in json.dumps(got),
          "another failure: no retry, the exception type only")
    check.done()


if __name__ == "__main__":
    run_as_script(globals())
