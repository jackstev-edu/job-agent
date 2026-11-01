"""
test_jobs_view.py — the redesigned jobs view: one compact row per job, sections on click.

WHAT THIS DOES: Checks /state's short status and one-line summary for
every status a job can be in (queued, working with a progress line,
needs-you with stopped fields, ready, submitted, failed, skipped, dry,
closed); then opens the page in a headless browser: a job that needs
Jack draws a chip and a summary with its sections collapsed, a click on
the row opens the fields it couldn't fill, what it learned and its log
(the log drawn as text), the URL is shortened with the full one on
hover, and a failed job turns its row red and raises the toast.
WHY IT MATTERS: Decision 6 of 2026-11-01: "It's a bit bland and too
much information all at once. Simplify it. Statuses are too long. If
something breaks let me know, and I click to see each section it
couldn't fill in."

Run from anywhere:  .venv/Scripts/python.exe tests/test_jobs_view.py
"""
from _harness import TEST_HOME, Checks, run_as_script
from agent import gui, joblog
from test_gui import _req

URL = "https://jobs.example-co.com/careers/software-engineer-ii/apply?src=li&ref=abcdef"


def _raw(label, value):
    return {"field": {"ref": "f1", "type": "text", "label": label}, "category": "personal_fact",
            "decision": {"value": value, "source": "model"}, "result": {"filled": True}}


def test_state_carries_a_short_status_and_a_one_line_summary():
    # Fails on 580cfd0: /state had no short_status and no summary.
    check = Checks()
    state = gui.State()
    ids = state.start([f"{URL}&n={i}" for i in range(9)], dry=False)
    q, w, n, r, s, f, k, d, c = ids
    state.update(w, status=gui.WORKING, progress="page 2: filled 7 of 9 (" + "x" * 200 + ")")
    state.set_pause(n, "gate", "This page needs you: 'City': required, but empty; 'Phone': left blank", fields=[
        {"ref": "f1", "label": "City", "reason": "required, but empty", "value": None},
        {"ref": "f2", "label": "Phone", "reason": "left blank", "value": None},
        {"ref": "f3", "label": "Gender", "reason": "not traced to a profile line", "value": "SENTINEL-EEO"}])
    state.set_pause(r, "ready", "Filled — review and press Submit yourself; the gate would stop: 'City': required, but empty", fields=[])
    state.update(s, status=gui.SUBMITTED, reason="submitted by the agent")
    state.update(f, status=gui.FAILED, reason="failed: TypeError in run_one")
    state.update(k, status="skipped", reason="already logged: submitted on 2026-10-30; use --force to redo")
    state.update(d, status=gui.DRY, reason="dry run: landed on the form")
    state.set_pause(c, "closed", "its tab is gone", fields=[])
    state.close_job(c)
    server = gui.Server(state).start()
    try:
        status, _, body = _req(server, "GET", "/state")
    finally:
        server.stop()
    jobs = {j["id"]: j for j in body["jobs"]}
    check(status == 200 and all("short_status" in j and "summary" in j for j in jobs.values()), "every row carries both")
    want = {q: ("Queued", "waiting in the queue"), w: ("Working", None), n: ("Needs you", "3 fields need you"),
            r: ("Ready", "Filled — review and press Submit yourself"), s: ("Submitted", "submitted by the agent"),
            f: ("Failed", "failed: TypeError in run_one"), k: ("Skipped", "already logged: submitted on 2026-10-30; use --force to redo"),
            d: ("Dry run", "dry run: landed on the form"), c: ("Closed", "its tab is gone (closed by Jack)")}
    for job_id, (chip, summary) in want.items():
        j = jobs[job_id]
        check(j["short_status"] == chip and (summary is None or j["summary"] == summary),
              f"{chip}: {j['short_status']!r} / {j['summary']!r}")
    check(jobs[w]["summary"].startswith("page 2: filled 7 of 9") and len(jobs[w]["summary"]) <= 140 and jobs[w]["summary"].endswith("…"),
          "a working row shows its newest step, cut to one line")
    check("SENTINEL-EEO" not in str(body), "no legal value anywhere in /state")
    one = gui.State(); [x] = one.start([URL], dry=False)
    one.set_pause(x, "gate", "needs you", fields=[{"ref": "f1", "label": "City", "reason": "required, but empty", "value": None}])
    check(one.snapshot()["jobs"][0]["summary"] == "1 field needs you", "the singular")
    check.done()


def test_the_page_draws_a_chip_and_collapsed_sections_that_open_on_click():
    # Fails on 580cfd0: no chip, no collapsed body; the old table drew everything at once.
    from playwright.sync_api import sync_playwright
    check = Checks()
    state = gui.State()
    root = TEST_HOME / "jobs-view-logs"; root.mkdir(exist_ok=True)
    state.log_root = root
    [job_id] = state.start([URL], dry=False)
    state.update(job_id, company="Example Co", role="Engineer", portal="Greenhouse")
    state.set_pause(job_id, "gate", "This page needs you: 'City': required, but empty", fields=[
        {"ref": "f1", "label": "City", "reason": "required, but empty", "value": None},
        {"ref": "f2", "label": "Nickname", "reason": "needs review (model)", "value": "TP", "raw": _raw("Nickname", "TP")}])
    joblog.path_for(job_id, URL, root=root).write_text(
        "clicked 'Apply'\nlearned 1 answer(s) of yours for the same question next time: 'Nickname' (answer)\n"
        "gate stopped: <b>City</b>: required, but empty\n", encoding="utf-8")
    server = gui.Server(state).start()
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                page = browser.new_page()
                page.goto(server.url)
                page.wait_for_timeout(700)
                chip = page.inner_text(".job .chip")
                summary = page.inner_text(".job .sum")
                hidden = page.evaluate("document.querySelector('.job .body').hidden")
                url_title = page.evaluate("document.querySelector('.job .url') ? document.querySelector('.job .url').title : null")
                page.click(".job .head .name")
                page.wait_for_timeout(700)
                body_hidden = page.evaluate("document.querySelector('.job .body').hidden")
                body_text = page.inner_text(".job .body")
                url_short = page.inner_text(".job .url")
                url_title = page.evaluate("document.querySelector('.job .url').title")
                tags = page.evaluate("document.querySelectorAll('.job .body b, .job .body img, .job .body script').length")
                approve_boxes = page.evaluate("document.querySelectorAll('.job .body input[type=checkbox]').length")
                page.click(".job .head .name")
                page.wait_for_timeout(200)
                closed_again = page.evaluate("document.querySelector('.job .body').hidden")
                big_switch = page.evaluate("getComputedStyle(document.getElementById('switch')).fontSize")
            finally:
                browser.close()
        check(chip == "Needs you" and summary == "2 fields need you" and hidden is True,
              f"the chip and the summary with the sections collapsed ({chip!r}, {summary!r}, {hidden})")
        check(body_hidden is False and "Fields it couldn't fill" in body_text and "City — required, but empty" in body_text
              and "What it learned" in body_text and "'Nickname' (answer)" in body_text and "Log" in body_text
              and "clicked 'Apply'" in body_text, "a click opens the three sections")
        check(approve_boxes == 1 and "approve \"TP\"" in body_text, "the approve tick stays on the approvable field only")
        check(tags == 0 and "<b>City</b>" in body_text, "the log line is text, not markup")
        check(url_short.startswith("jobs.example-co.com/careers/") and len(url_short) < len(URL) and url_title == URL,
              f"the URL is shortened with the full one on hover ({url_short!r})")
        check(closed_again is True, "a second click collapses it")
        check(big_switch and float(big_switch.rstrip("px")) >= 18, f"the auto-submit switch stays large ({big_switch})")
    finally:
        server.stop()
    check.done()


def test_a_failed_job_turns_red_and_raises_the_toast():
    # Fails on 580cfd0: no toast element, no red card.
    from playwright.sync_api import sync_playwright
    check = Checks()
    state = gui.State()
    [job_id] = state.start([URL], dry=False)
    state.update(job_id, company="Example Co")
    server = gui.Server(state).start()
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                page = browser.new_page()
                page.goto(server.url)
                page.wait_for_timeout(600)
                before = page.evaluate("!!document.getElementById('toast')")
                state.update(job_id, status=gui.FAILED, reason="failed: TypeError in run_one", pause=None)
                page.wait_for_timeout(2600)
                toast = page.inner_text("#toast")
                card = page.evaluate("document.querySelector('.job').className")
                color = page.evaluate("getComputedStyle(document.querySelector('.job')).borderColor")
                chip = page.inner_text(".job .chip")
                page.click("#toast")
                page.wait_for_timeout(200)
                after = page.evaluate("!!document.getElementById('toast')")
            finally:
                browser.close()
        check(before is False, "no toast while the job runs")
        check("Example Co broke: failed: TypeError in run_one" in toast and after is False,
              f"the toast names the job and the reason, and a click dismisses it ({toast!r})")
        check("failed" in card.split() and chip == "Failed" and color == "rgb(192, 52, 58)",
              f"the row is red with the Failed chip ({card!r}, {color})")
    finally:
        server.stop()
    check.done()


if __name__ == "__main__":
    run_as_script(globals())
