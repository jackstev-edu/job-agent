"""
test_joblog.py — every job's log on disk, as it lands (Jack, 2026-11-01).

WHAT THIS DOES: Appends lines to a job's log through the wizard's own
hook and the sink and reads the file back after each one; flushes the
same Outcome through apply._flush_log several times and across a resume
and shows each line is printed once and written once; runs run_one with
intake and the wizard stood in for and finds the file named by the day,
the job id and the link's host holding exactly the log's lines; prunes
a folder of dated files; asks the GUI for a job's log with and without
the token; and opens the page in a headless browser to see a log line
holding markup drawn as text.
WHY IT MATTERS: "Track everything in the current window and record it
for future use": the file is the job's story, the same lines and
nothing more, and the page that shows it must never run what a page
wrote into a log line.

Run from anywhere:  .venv/Scripts/python.exe tests/test_joblog.py
"""
import io
import json
import time
from argparse import Namespace
from contextlib import ExitStack, redirect_stdout
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

from _harness import TEST_HOME, Checks, run_as_script
from agent import joblog, wizard

HOST_URL = "https://jobs.example-co.com/careers/42?x=1"


def _tmp(name: str) -> Path:
    root = TEST_HOME / "logs-" / name
    root.mkdir(parents=True, exist_ok=True)
    return root


def test_lines_reach_the_file_as_they_land():
    check = Checks()
    root = _tmp("land")
    out = wizard.Outcome("needs_you")
    sink = joblog.Sink("ab12cd", HOST_URL, root=root)
    out.log.on_line = lambda: sink.write(out)
    check(not sink.path.exists(), "no file before the first line")
    out.log.append("clicked 'Apply' on jobs.example-co.com")
    check(sink.path.read_text(encoding="utf-8") == "clicked 'Apply' on jobs.example-co.com\n",
          "the first line is in the file as soon as it is appended")
    out.log.extend(["page 1: 3 of 4 filled", "parked: needs you"])
    check(sink.path.read_text(encoding="utf-8").splitlines() == list(out.log),
          "an extend lands too, each line once, in order")
    out.log += ["resumed after a gate pause"]
    check(sink.path.read_text(encoding="utf-8").splitlines() == list(out.log),
          "a += lands too")
    check(sink.path.name == f"{time.strftime('%Y-%m-%d')}-ab12cd-jobs.example-co.com.log",
          f"the name is day, job id and host ({sink.path.name})")
    check.done()


def test_nothing_is_printed_or_written_twice():
    import apply
    check = Checks()
    root = _tmp("twice")
    out = wizard.Outcome("needs_you")
    sink = joblog.Sink("0f0f0f", HOST_URL, root=root)
    buf = io.StringIO()
    with redirect_stdout(buf):
        out.log.append("one")
        apply._flush_log(out, None, sink)
        apply._flush_log(out, None, sink)          # nothing new: nothing written, nothing printed
        out.log.append("two")
        apply._flush_log(out, None, sink)
        # The end of a job (or of a resume): the flush with no new lines.
        apply._flush_log(out, sink=sink)
    printed = [line.strip() for line in buf.getvalue().splitlines() if line.strip()]
    printed = [line.replace("\x1b[2m", "").replace("\x1b[0m", "").strip() for line in printed]
    check(printed == ["one", "two"], f"each line printed once ({printed})")
    check(sink.path.read_text(encoding="utf-8").splitlines() == ["one", "two"],
          "each line written once")
    check(out.printed == 2 and sink.written == 2, "both cursors sit at the end")
    check.done()


class _FakeLanding:
    def __init__(self, page, url, steps):
        self.page, self.url, self.steps = page, url, steps
        self.portal, self.portal_method, self.parked = "generic", "default", None


def test_run_one_names_the_file_by_day_job_id_and_host():
    import apply
    check = Checks()
    page = mock.Mock(url=HOST_URL)
    page.is_closed.return_value = False
    page.evaluate.side_effect = Exception("no page reads here")
    seen = {}

    def fake_run_application(page_, prof, **kw):
        out = wizard.Outcome("needs_you", reason="needs you: a test")
        out.log.extend(str(s) for s in kw.get("intake_steps") or [])
        out.printed = len(out.log)
        out.log.on_line = lambda: kw["on_log"](out)
        out.log.append("page 1: filled 2 of 3")
        seen["mid"] = [f.read_text(encoding="utf-8").splitlines()
                       for f in sorted((TEST_HOME / "logs").glob("*-c0ffee-*.log"))]
        out.log.append("parked: needs you: a test")
        return out

    prof = mock.Mock()
    prof.resume_path.return_value = None
    args = Namespace(profile=str(TEST_HOME / "no-profile.yaml"), resume=None, dry=False, force=True)
    with ExitStack() as stack:
        stack.enter_context(mock.patch.object(apply.intake, "open_link",
                                              lambda ctx, url, new_tab=False: _FakeLanding(page, url, ["clicked 'Apply'"])))
        stack.enter_context(mock.patch.object(apply.extract, "guess_role_and_company", lambda p: ("Role", "Co")))
        stack.enter_context(mock.patch.object(apply.extract, "page_text", lambda p, n: ""))
        stack.enter_context(mock.patch.object(apply.wizard, "run_application", fake_run_application))
        stack.enter_context(mock.patch.object(apply.report, "build", lambda **kw: None))
        stack.enter_context(redirect_stdout(io.StringIO()))
        status, reason = apply.run_one(None, HOST_URL, prof, args, job_id="c0ffee")
    files = sorted((TEST_HOME / "logs").glob("*-c0ffee-*.log"))
    check(len(files) == 1 and files[0].name == f"{date.today().isoformat()}-c0ffee-jobs.example-co.com.log",
          f"one file, named by the day, the GUI's job id and the link's host ({[f.name for f in files]})")
    check(files[0].read_text(encoding="utf-8").splitlines()
          == ["clicked 'Apply'", "page 1: filled 2 of 3", "parked: needs you: a test"],
          "the file holds exactly the log's lines, intake steps first")
    check(seen["mid"] == [["clicked 'Apply'", "page 1: filled 2 of 3"]],
          "the file was written while the job was still running, not at its end")
    check.done()


def test_prune_goes_by_the_date_in_the_name():
    check = Checks()
    root = _tmp("prune")
    today = date(2026, 11, 1)
    old = (today - timedelta(days=61)).isoformat()
    kept = (today - timedelta(days=59)).isoformat()
    edge = (today - timedelta(days=60)).isoformat()
    for name in (f"{old}-aa-x.example.log", f"{kept}-bb-x.example.log", f"{edge}-cc-x.example.log",
                 "notes.txt", "undated-dd-x.example.log"):
        (root / name).write_text("line\n", encoding="utf-8")
    gone = joblog.prune(root=root, today=today)
    left = sorted(p.name for p in root.iterdir())
    check(gone == 1 and f"{old}-aa-x.example.log" not in left, f"the 61-day file went ({gone}, {left})")
    check(f"{kept}-bb-x.example.log" in left and f"{edge}-cc-x.example.log" in left,
          "59- and 60-day files stay")
    check("notes.txt" in left and "undated-dd-x.example.log" in left, "files not named by a day are left alone")
    check(joblog.prune(root=root / "missing", today=today) == 0, "a missing folder prunes nothing")
    check.done()


def test_the_page_serves_a_jobs_log_behind_the_token():
    from test_gui import _req
    from agent import gui
    check = Checks()
    root = _tmp("route")
    state = gui.State()
    state.log_root = root
    server = gui.Server(state).start()
    try:
        [job_id] = state.start([HOST_URL], dry=False)
        joblog.path_for(job_id, HOST_URL, root=root).write_text("one\n<b>two</b>\n", encoding="utf-8")
        status, _, body = _req(server, "GET", f"/log?job={job_id}")
        check(status == 200 and body == {"ok": True, "lines": ["one", "<b>two</b>"], "empty": False},
              f"the job's lines come back as text ({status}, {body})")
        status, _, body = _req(server, "GET", f"/log?job={job_id}", token=None)
        check(status == 403, f"no token: refused ({status})")
        status, _, body = _req(server, "GET", f"/log?job={job_id}", host="evil.example")
        check(status == 403, f"wrong host: refused ({status})")
        status, _, body = _req(server, "GET", f"/log?job={job_id}", origin="http://evil.example")
        check(status == 403, f"foreign origin: refused ({status})")
        status, _, body = _req(server, "GET", "/log?job=zz")
        check(status == 200 and body.get("ok") is False, "a job this session never started has no log here")
        status, _, page = _req(server, "GET", "/", token=None)
        check("/log?job=" in page, "the page reads the job's log into its row")
    finally:
        server.stop()
    check.done()


def test_a_log_line_holding_markup_is_drawn_as_text():
    from playwright.sync_api import sync_playwright
    from agent import gui
    check = Checks()
    root = _tmp("markup")
    state = gui.State()
    state.log_root = root
    server = gui.Server(state).start()
    try:
        [job_id] = state.start([HOST_URL], dry=False)
        evil = '<img src=x onerror="window.__pwned=1"><script>window.__pwned=2</script>Bold <b>line</b>'
        joblog.path_for(job_id, HOST_URL, root=root).write_text(evil + "\n", encoding="utf-8")
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                page = browser.new_page()
                page.goto(server.url)
                page.wait_for_timeout(800)
                page.click(".job .head .name")        # the row's log section opens on click (2026-11-01)
                page.wait_for_timeout(800)
                pwned = page.evaluate("window.__pwned || 0")
                text = page.inner_text("#jobs-list")
                tags = page.evaluate("document.querySelectorAll('#jobs-list img, #jobs-list script, #jobs-list b').length")
            finally:
                browser.close()
        check(pwned == 0 and tags == 0, f"nothing from the log line ran or rendered ({pwned}, {tags})")
        check("Bold <b>line</b>" in text and "<img src=x onerror=" in text, "the line shows as the text it is")
    finally:
        server.stop()
    check.done()


if __name__ == "__main__":
    run_as_script(globals())
