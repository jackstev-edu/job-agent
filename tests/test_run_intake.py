"""
test_run_intake.py — `apply.py run`: links in, each one opened and logged.

WHAT THIS DOES: Runs cmd_run offline on a queue file plus a link on the
command line: a LinkedIn Easy Apply link is parked as needs-you with the
reason, a company posting is followed to its form and logged, a link
already logged past draft is skipped, and the queue reader drops comments,
blanks and duplicates.
WHY IT MATTERS: This is the command Jack runs. Every link must end up in
the tracker with a status and a reason, and nothing is ever discovered
beyond the links given.

Run from anywhere:  .venv/Scripts/python.exe tests/test_run_intake.py
"""
import io
import re
from argparse import Namespace
from contextlib import ExitStack, redirect_stdout
from unittest import mock

from _fill_run import PROFILE, _headless
from _harness import TEST_HOME, Checks, run_as_script
from agent import tracker

COMPANY = "https://jobs.example-co.com"
LINKEDIN = "https://www.linkedin.com"
FORM = """<!doctype html><html><head><title>Engineer - Example Co</title></head><body>
  <form><label for="fn">First Name</label><input id="fn" name="first_name"></form></body></html>"""
POSTING = """<!doctype html><html><body><a href="/jobs/1/apply">Apply now</a></body></html>"""
EASY = """<!doctype html><html><body><h1>Engineer</h1><button>Easy Apply</button></body></html>"""


def _routes():
    site = {f"{COMPANY}/jobs/1": POSTING, f"{COMPANY}/jobs/1/apply": FORM,
            f"{LINKEDIN}/jobs/view/5": EASY}

    def handle(route):
        html = site.get(route.request.url.split("?")[0])
        route.fulfill(status=200 if html else 404, content_type="text/html", body=html or "<p>404</p>")
    return {"**/*": handle}


def _run(urls, queue=None, force=False):
    import apply
    profile_path = TEST_HOME / "run-profile.yaml"
    # A resume that exists, so nothing required is blank and --force isn't needed.
    (TEST_HOME / "run-resume.pdf").write_bytes(b"%PDF-1.4 stub")
    profile_path.write_text(PROFILE.replace("missing.pdf", "run-resume.pdf"), encoding="utf-8")
    args = Namespace(profile=str(profile_path), urls=urls, queue=queue, resume=None, force=force)
    out = io.StringIO()
    with ExitStack() as stack:
        for target, name, value in ((apply.config, "check_api_key", lambda: ("ok", "")),
                                    (apply.browser_mod, "browser", lambda: _headless(_routes()))):
            stack.enter_context(mock.patch.object(target, name, value))
        with redirect_stdout(out):
            apply.cmd_run(args)
    return re.sub(r"\x1b\[[0-9;]*m", "", out.getvalue())


def test_the_queue_reader_keeps_only_links():
    import apply
    check = Checks()
    q = TEST_HOME / "queue.txt"
    q.write_text("# my links\n\nhttps://a.example/1\n  https://b.example/2  \nhttps://a.example/1\n",
                 encoding="utf-8")
    check(apply.read_queue(q) == ["https://a.example/1", "https://b.example/2"],
          "comments, blanks and duplicates dropped, order kept")
    check.done()


def test_run_logs_every_link_with_a_status_and_reason():
    check = Checks()
    q = TEST_HOME / "queue2.txt"
    q.write_text(f"{LINKEDIN}/jobs/view/5\n", encoding="utf-8")
    text = _run([f"{COMPANY}/jobs/1"], queue=str(q))
    print(text)
    easy = tracker.find(f"{LINKEDIN}/jobs/view/5")
    check(easy and easy["status"] == "needs_you" and "Easy Apply" in (easy["notes"] or ""),
          f"the Easy Apply link is parked as needs-you with the reason ({easy and easy['notes']})")
    posting = tracker.find(f"{COMPANY}/jobs/1")
    check(posting and posting["status"] == "needs_you" and posting["company"] == "Example Co"
          and "clicked 'Apply now'" in text,
          f"the company posting was followed to its form and logged ({posting})")
    check("2 link(s)" in text and "needs_you 2" in text, "the batch summary counts both")
    check.done()


def test_a_link_already_sent_is_skipped_without_force():
    check = Checks()
    url = f"{COMPANY}/jobs/1"
    tracker.record(url, status="submitted")
    text = _run([url])
    check("Already logged: status 'submitted'" in text and "skipped 1" in text,
          f"skipped, and said so ({text.strip().splitlines()[-1]})")
    check.done()


if __name__ == "__main__":
    run_as_script(globals())
