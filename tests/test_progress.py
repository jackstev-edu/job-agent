"""
test_progress.py — a running job's row and the terminal show each log line as it lands.

WHAT THIS DOES: Runs one real (non-dry) job through the GUI queue loop
on a mock single-page site and, at the moment the wizard hands its
ready park to the GUI (the row still "working", the job not yet ended),
reads the GUI state and the terminal: the row already carries the
newest job-log line, and the terminal has already printed it. Afterwards
every line of the job's log appears in the terminal exactly once,
exactly as the log holds it.
WHY IT MATTERS: Jack's 2026-10-29 Avature run looped for an hour with
the row on a bare "working" and nothing in the terminal, because job
lines printed only when the job finished.

Run from anywhere:  .venv/Scripts/python.exe tests/test_progress.py
"""
import io
from argparse import Namespace
from contextlib import redirect_stdout
from unittest import mock

from _fill_run import PROFILE, _headless, classify_by_label, map_from_profile
from _harness import TEST_HOME, Checks, run_as_script
from agent import gui, ledger, pause
from test_resume import _gui_site
from test_wizard import HOST, _routes


def _quiet():
    return pause.Pauser(notify_fn=lambda t, body, echo=print: None, echo=lambda *x, **k: None)


def test_a_running_jobs_row_shows_its_latest_log_line_before_the_job_ends():
    # Fails on 6d95f6e: the Wizard takes no on_log, the row has no
    # progress, and the lines print only once the job has parked.
    import apply
    check = Checks()
    profile_path = TEST_HOME / "progress-profile.yaml"
    profile_path.write_text(PROFILE, encoding="utf-8")
    state = gui.State()
    [job_id] = state.start([f"{HOST}/single-p"], dry=False)
    site = _gui_site()
    site["/single-p"] = site["/single"]
    seen: list = []
    real_set_pause = state.set_pause
    captured = io.StringIO()

    def observe(job, *a, **k):
        # The wizard is handing its pause to the GUI: the row is still
        # working and the job has not returned.
        row = next(j for j in state.snapshot()["jobs"] if j["id"] == job_id)
        seen.append((row["status"], row.get("progress"), captured.getvalue()))
        return real_set_pause(job, *a, **k)
    waiting: dict = {}
    args = Namespace(profile=str(profile_path), resume=None, dry=False)
    with _headless(_routes(site)) as ctx:
        ctx.add_init_script(ledger.ACTIVITY_INIT_SCRIPT)
        with mock.patch.object(apply.classify, "classify_fields", classify_by_label), \
                mock.patch.object(apply.mapper, "map_fields", map_from_profile), \
                mock.patch.object(apply.report, "open_in_browser", lambda path: None), \
                mock.patch.object(state, "set_pause", observe), \
                redirect_stdout(captured):
            apply.gui_loop(ctx, state, apply.Profile.load(profile_path), args, _quiet(),
                           idle_limit_s=1.0, waiting=waiting)
    printed = captured.getvalue()
    row = next(j for j in state.snapshot()["jobs"] if j["id"] == job_id)
    wiz = waiting[job_id][0] if job_id in waiting else None
    log = list(wiz.out.log) if wiz is not None else []
    check(row["status"] == "needs_you" and (row["pause"] or {}).get("kind") == "ready" and len(log) >= 2,
          f"the job ran to its ready park with lines in its log ({row['status']}, {len(log)})")
    status, progress, printed_then = seen[0] if seen else ("", "", "")
    check(status == "working" and bool(progress) and progress == log[-1] if log else False,
          f"while the row was still working it showed the newest log line ({status!r}, {progress!r})")
    check(all(f"{apply.DIM}  {line}{apply.OFF}\n" in printed_then for line in log),
          f"and the terminal had printed every line by then ({len(log)} lines, {len(printed_then)} chars)")
    once = [line for line in log if printed.count(f"{apply.DIM}  {line}{apply.OFF}\n") == 1]
    check(len(once) == len(log),
          f"every log line was printed exactly once, as the log holds it ({len(once)} of {len(log)})")
    check.done()


if __name__ == "__main__":
    run_as_script(globals())
