"""
test_crash.py — a crash anywhere in a job leaves a failed row, the tab open and the queue running.

WHAT THIS DOES: Raises an exception whose message carries a sentinel
(standing in for page text a Playwright error can carry) from intake,
from inside the wizard's fill and from a resume, under `run` and the
GUI queue loop, and checks each leaves "failed: <ExceptionType> in
<function>" (type and function name only, never the message) on the
row and in the tracker, that the next job still runs, and that the
crashed job's tab is still open. A tracker that fails before a job
starts fails that job the same way and the loop goes on.
WHY IT MATTERS: Jack pastes a dozen links; one bad page must not take
the GUI down or put page text into the log (Jack, 2026-10-29).

Run from anywhere:  .venv/Scripts/python.exe tests/test_crash.py
"""
import io
from argparse import Namespace
from contextlib import contextmanager, redirect_stdout
from unittest import mock

from _fill_run import PROFILE, _headless, classify_by_label, map_from_profile
from _harness import TEST_HOME, Checks, run_as_script
from agent import gui, intake, ledger, pause, pipeline, tracker, wizard
from test_resume import _gui_site
from test_wizard import HOST, _routes

SENTINEL = "Sentinel-Label-9000 from the page"


class _Boom(RuntimeError):
    pass


def _profile_path(name):
    path = TEST_HOME / f"crash-{name}.yaml"
    path.write_text(PROFILE, encoding="utf-8")
    return path


def _quiet():
    return pause.Pauser(notify_fn=lambda t, body, echo=print: None, echo=lambda *x, **k: None)


def _loop(ctx, state, profile_path, waiting, patches=()):
    import apply
    args = Namespace(profile=str(profile_path), resume=None, dry=False)
    with mock.patch.object(apply.classify, "classify_fields", classify_by_label), \
            mock.patch.object(apply.mapper, "map_fields", map_from_profile), \
            mock.patch.object(apply.report, "open_in_browser", lambda path: None), \
            redirect_stdout(io.StringIO()) as out:
        with _patched(patches):
            apply.gui_loop(ctx, state, apply.Profile.load(profile_path), args, _quiet(),
                           idle_limit_s=1.0, waiting=waiting)
    return out.getvalue()


@contextmanager
def _patched(patches):
    started = [mock.patch.object(*p) for p in patches]
    for p in started:
        p.start()
    try:
        yield
    finally:
        for p in reversed(started):
            p.stop()


def _no_message(check, text: str, what: str) -> None:
    check(SENTINEL not in text and "from the page" not in text, f"no message text in {what} ({text[:160]!r})")


def test_an_intake_crash_fails_the_row_without_the_message_and_the_queue_goes_on():
    # Fails on 17783c9: the row read "_Boom: Sentinel-Label-9000 from the
    # page" (type plus message) under both `run` and the GUI. The raising
    # stand-in lives in this file, outside agent/, so the innermost repo
    # frame named is its caller, run_one.
    import apply
    check = Checks()

    def boom(ctx, url, **kw):
        raise _Boom(SENTINEL)
    # The GUI loop: no browser is needed when intake itself raises.
    profile_path = _profile_path("intake")
    state = gui.State()
    a, b = state.start(["https://x.example/crash-1", "https://x.example/crash-2"], dry=False)
    printed = _loop(None, state, profile_path, {}, patches=[(intake, "open_link", boom)])
    jobs = {j["id"]: j for j in state.snapshot()["jobs"]}
    check(jobs[a]["status"] == "failed" and jobs[a]["reason"] == "failed: _Boom in run_one",
          f"the row: type and function only ({jobs[a]['status']}, {jobs[a]['reason']})")
    check(jobs[b]["status"] == "failed" and jobs[b]["reason"] == "failed: _Boom in run_one",
          f"the queue went on to the second link ({jobs[b]['status']}, {jobs[b]['reason']})")
    for url in ("https://x.example/crash-1", "https://x.example/crash-2"):
        row = tracker.find(url)
        check(row and row["status"] == "failed" and row["notes"] == "failed: _Boom in run_one",
              f"the tracker row for {url[-7:]} ({row and (row['status'], row['notes'])})")
        _no_message(check, (row or {}).get("notes") or "", "the tracker")
    _no_message(check, printed, "the terminal")
    # `run`: the same line, and the batch finishes.
    @contextmanager
    def no_browser():
        yield Namespace(add_init_script=lambda s: None)
    args = Namespace(profile=str(profile_path), resume=None, force=True, dry=False, queue=None,
                     urls=["https://x.example/crash-3"], auto_submit=False)
    with mock.patch.object(apply.browser_mod, "browser", no_browser), \
            mock.patch.object(intake, "open_link", boom), \
            mock.patch.object(apply, "_ready_to_fill", lambda *a, **k: True), \
            redirect_stdout(io.StringIO()) as out:
        apply.cmd_run(args)
    text = out.getvalue()
    row = tracker.find("https://x.example/crash-3")
    check("failed: _Boom in run_one" in text and row and row["notes"] == "failed: _Boom in run_one",
          f"`run` prints and logs the same line ({row and row['notes']})")
    _no_message(check, text + ((row or {}).get("notes") or ""), "run's output and row")
    check.done()


def test_a_wizard_crash_leaves_the_tab_open_and_the_next_job_runs():
    # Fails on 17783c9: the row carried the message. The fill of the first
    # page raises; the second link (a single page) is still run to its
    # ready park.
    check = Checks()
    profile_path = _profile_path("wizard")
    state = gui.State()
    a, b = state.start([f"{HOST}/apply-w", f"{HOST}/single-w"], dry=False)
    site = _gui_site(); site["/apply-w"], site["/single-w"] = site["/apply"], site["/single"]
    real = pipeline.fill_page
    hits = {"n": 0}

    def fill_then_boom(page, prof, **kw):
        hits["n"] += 1
        if hits["n"] == 1:
            raise _Boom(SENTINEL)
        return real(page, prof, **kw)
    waiting: dict = {}
    with _headless(_routes(site)) as ctx:
        ctx.add_init_script(ledger.ACTIVITY_INIT_SCRIPT)
        printed = _loop(ctx, state, profile_path, waiting, patches=[(pipeline, "fill_page", fill_then_boom)])
        jobs = {j["id"]: j for j in state.snapshot()["jobs"]}
        open_tabs = [p.url for p in ctx.pages if not p.is_closed()]
    check(jobs[a]["status"] == "failed" and jobs[a]["reason"] == "failed: _Boom in _loop",
          f"the crashed job's row names the wizard's own frame, no message ({jobs[a]['reason']})")
    check(jobs[b]["status"] == "needs_you" and jobs[b]["pause"] and jobs[b]["pause"]["kind"] == "ready",
          f"the next job ran to its ready park ({jobs[b]['status']}, {jobs[b]['pause'] and jobs[b]['pause']['kind']})")
    check(any(u.endswith("/apply-w") for u in open_tabs), f"the crashed job's tab is still open ({open_tabs})")
    row = tracker.find(f"{HOST}/apply-w")
    check(row and row["status"] == "failed" and row["notes"] == "failed: _Boom in _loop", f"the tracker row ({row and row['notes']})")
    _no_message(check, printed + ((row or {}).get("notes") or ""), "the terminal and the tracker")
    check.done()


def test_a_resume_crash_fails_the_row_and_the_queue_goes_on():
    # Fails on 17783c9: the row carried the message. A job waits on its
    # city; Continue crashes inside the wizard's review; the row fails
    # with the function name only, the tab stays open, and a second job
    # queued meanwhile still runs.
    check = Checks()
    profile_path = _profile_path("resume")
    state = gui.State()
    [a] = state.start([f"{HOST}/apply-r"], dry=False)
    site = _gui_site(); site["/apply-r"], site["/single-r"] = site["/apply"], site["/single"]
    waiting: dict = {}

    def boom(self, *args, **kw):
        raise _Boom(SENTINEL)
    with _headless(_routes(site)) as ctx:
        ctx.add_init_script(ledger.ACTIVITY_INIT_SCRIPT)
        _loop(ctx, state, profile_path, waiting)
        job = state.snapshot()["jobs"][0]
        check(job["status"] == "needs_you" and "City" in job["reason"] and a in waiting, "the job waits on its city")
        [b] = state.start([f"{HOST}/single-r"], dry=False)
        state.request_continue(a)
        printed = _loop(ctx, state, profile_path, waiting, patches=[(wizard.Wizard, "_after_pause", boom)])
        jobs = {j["id"]: j for j in state.snapshot()["jobs"]}
        open_tabs = [p.url for p in ctx.pages if not p.is_closed()]
    check(jobs[a]["status"] == "failed" and jobs[a]["reason"] == "failed: _Boom in _loop" and a not in waiting,
          f"the resumed job failed with the function only ({jobs[a]['status']}, {jobs[a]['reason']})")
    check(jobs[b]["status"] == "needs_you" and jobs[b]["pause"] and jobs[b]["pause"]["kind"] == "ready",
          f"the job queued meanwhile still ran ({jobs[b]['status']})")
    check(any(u.endswith("/apply/2") for u in open_tabs), f"the crashed job's tab is still open ({open_tabs})")
    row = tracker.find(f"{HOST}/apply-r")
    check(row and row["status"] == "failed" and row["notes"] == "failed: _Boom in _loop", f"the tracker row ({row and row['notes']})")
    _no_message(check, printed + ((row or {}).get("notes") or ""), "the terminal and the tracker")
    check.done()


def test_a_tracker_failure_before_a_job_starts_does_not_take_the_loop_down():
    # Fails on 17783c9: tracker.find raising inside gui_loop propagated
    # out of the loop (the GUI died).
    import apply
    check = Checks()
    profile_path = _profile_path("tracker")
    state = gui.State()
    a, b = state.start(["https://x.example/t-1", "https://x.example/t-2"], dry=False)
    calls = {"n": 0}
    real_find = tracker.find

    def flaky_find(url):
        calls["n"] += 1
        if calls["n"] == 1:
            raise _Boom(SENTINEL)
        return real_find(url)

    def fake_run(ctx, url, prof, job_args, pauser, **kw):
        return apply.NEEDS_YOU, "ran"
    args = Namespace(profile=str(profile_path), resume=None, dry=False)
    with mock.patch.object(apply.tracker, "find", flaky_find), redirect_stdout(io.StringIO()):
        apply.gui_loop(None, state, apply.Profile.load(profile_path), args, _quiet(), run=fake_run,
                       idle_limit_s=1.0, waiting={})
    jobs = {j["id"]: j for j in state.snapshot()["jobs"]}
    check(jobs[a]["status"] == "failed" and jobs[a]["reason"] == "failed: _Boom in gui_loop",
          f"the first job failed on the tracker, named ({jobs[a]['reason']})")
    check(jobs[b]["status"] == "needs_you" and jobs[b]["reason"] == "ran", f"the second ran ({jobs[b]['status']})")
    _no_message(check, jobs[a]["reason"], "the row")
    check.done()


def test_failure_line_names_the_innermost_repo_frame_only():
    # A guard for the helper itself (mutation check: with the frame walk
    # dropped it reads "in unknown"; with the message kept it carries the
    # sentinel).
    import apply
    check = Checks()
    try:
        intake.job_board(object())                 # raises inside agent/intake.py
    except Exception as e:
        line, kind = apply.failure_line(e), type(e).__name__
    check(line == f"failed: {kind} in job_board", f"the repo frame is named ({line})")
    try:
        raise _Boom(SENTINEL)
    except Exception as e:
        line = apply.failure_line(e)
    check(line == "failed: _Boom in unknown" and SENTINEL not in line,
          f"no repo frame: 'unknown', never the message ({line})")
    check.done()


if __name__ == "__main__":
    run_as_script(globals())
