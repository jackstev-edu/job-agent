"""
test_folds_2026_11_01.py — the four folds of decision 4 of the 2026-11-01 fold round.

WHAT THIS DOES: (1) Jack's own press of the page's Next, with no poll in
between, carries his touch marks in the hook's read, so the boxes he
changed in that instant are learned; (2) a GUI edit or delete and a
learn() that loaded the store before each other lose nothing: each
re-reads the file under the lock before writing; (3) an open job card
fetches its log once, not on every 2 s poll, a code typed into its box
survives the poll, and the log is fetched again when it grew; (4) a
learned.yaml that exists but didn't parse is never saved over: learning
reports it in the job log, the Memory page says so, edit and delete
refuse, and the file's bytes stay what they were.
WHY IT MATTERS: Jack, 2026-11-01 (fold round): "Fold all four: touch
marks in the hook's read; the learn-versus-edit race; no per-poll log
fetch on an open card; refuse to save over a learned.yaml that didn't
parse (keep the old file, report it)."

Run from anywhere:  .venv/Scripts/python.exe tests/test_folds_2026_11_01.py
"""
from pathlib import Path

import yaml

from _fill_run import PROFILE
from _harness import TEST_HOME, Checks, run_as_script
from agent import corrections, gate, gui, joblog, memory, pipeline
from agent.profile import Profile
from test_gui import _req
from test_memory import _change, _prof
from test_overrides import EMAIL2, _heading
from test_pick_up import CITY_REQUIRED, _page, _run

URL = "https://jobs.example-co.com/careers/engineer/apply"
CITY = {"ref": "c1", "type": "text", "label": "City"}
NICK = {"ref": "n1", "type": "text", "label": "Preferred name"}
STATE_Q = {"ref": "s1", "type": "select", "label": "State / Province", "options": ["XX - Example State", "YY - Other"]}


def test_an_edit_in_the_instant_before_jacks_own_press_is_learned_from_the_hooks_marks():
    # Fails on 4fdca04: the watch read its marks only at a poll, so with no
    # poll between Jack's edit and his own press the two changes were
    # recorded in corrections.jsonl but not learned (ab9cb55's finding 2).
    check = Checks()
    memory.path().unlink(missing_ok=True)
    with _run(_page(city=CITY_REQUIRED + EMAIL2), auto_submit=lambda: False) as (page, w):
        out = w.run()
        check(out.status == "waiting" and w.pending.get("watch") is not None, f"the gate pause on step 2 ({out.status})")
        page.fill("#em2", "other@example.invalid")      # Jack changes the agent's answer
        page.fill("#city", "Springfield")               # and fills what it left blank
        page.click("#n2")                               # his press: the hook reads the form, marks and all
        check(_heading(page) == "Step 3 of 3", "the tab shows step 3")
        watch = w.pending.get("watch")
        marked = {f.get("label") for f in watch.fields if f.get("ref") in watch.marks} if watch else set()
        check(watch is not None and watch.reads == 0 and marked == {"City", "Email"},
              f"no poll was made; the hook's read carried both marks ({watch and watch.reads}, {marked})")
        out = w.continue_from({})
    store = memory.Learned.load()
    learned = {e["question"]: e for e in store.entries}
    # Email: no PROFILE_PATHS line names an email box, so his value is an
    # answer for that key (not a candidate for a profile line).
    check(set(learned) == {"City", "Email"} and learned["City"]["answer"] == "Springfield"
          and learned["Email"]["kind"] == memory.ANSWER and learned["Email"]["answer"] == "other@example.invalid",
          f"both boxes learned from his own press ({[(e['question'], e['kind']) for e in store.entries]})")
    check(any(l.startswith("learned 2 answer(s) of yours") for l in out.log), f"the job log says so ({out.log[-4:]})")
    check.done()


def test_a_gui_edit_and_a_learn_that_loaded_before_each_other_lose_nothing():
    # Fails on 4fdca04: each side saved its own loaded copy of the whole
    # list, so the later save dropped the earlier one's entry (580cfd0's
    # finding 2).
    check = Checks()
    prof = _prof("folds-profile.yaml")
    at = TEST_HOME / "learned-race.yaml"
    at.unlink(missing_ok=True)
    [x] = memory.learn([_change(NICK, "Tess")], url=URL, portal="generic", prof=prof, store=memory.Learned.load(at))
    gui_copy = memory.Learned.load(at)                  # the Memory page's copy, loaded first
    [y] = memory.learn([_change(CITY, "Springfield")], url=URL, portal="generic", prof=prof,
                       store=memory.Learned.load(at))   # a job learns meanwhile
    check(gui_copy.edit(x["id"], "Tessa") is True, "the edit on the earlier copy goes through")
    after = memory.Learned.load(at)
    by_q = {e["question"]: e for e in after.entries}
    check(set(by_q) == {"Preferred name", "City"} and by_q["Preferred name"]["answer"] == "Tessa"
          and by_q["City"]["answer"] == "Springfield", f"the edit and the learned entry both survive ({by_q})")
    # The other way round: a delete on an earlier copy, then a learn on a
    # copy loaded before the delete.
    gui_copy = memory.Learned.load(at)
    job_copy = memory.Learned.load(at)
    check(gui_copy.delete(by_q["Preferred name"]["id"]) is True, "the delete goes through")
    memory.learn([_change(STATE_Q, "YY - Other", decision={"profile_path": "personal.state"})],
                 url=URL, portal="generic", prof=prof, store=job_copy)
    after = memory.Learned.load(at)
    check({e["question"] for e in after.entries} == {"City", "State / Province"},
          f"the deleted entry stays deleted, the learned one is there ({[e['question'] for e in after.entries]})")
    check(len(job_copy.entries) == 2, "the learning copy was refreshed from the file first")
    check.done()


def test_an_open_card_fetches_its_log_once_until_it_changes():
    # Fails on 4fdca04: an open card was redrawn, with a fresh GET /log, on
    # every 2 s poll, and a code typed into its box was lost on the redraw
    # (23999d0's finding 1).
    from playwright.sync_api import sync_playwright
    check = Checks()
    state = gui.State()
    root = TEST_HOME / "folds-logs"; root.mkdir(exist_ok=True)
    state.log_root = root
    [job_id] = state.start([URL], dry=False)
    state.update(job_id, company="Example Co", role="Engineer", portal="Greenhouse")
    state.set_pause(job_id, "account", "Paste the code the portal emailed you", fields=[], wants_code=True)
    log_path = joblog.path_for(job_id, URL, root=root)
    log_path.write_text("clicked 'Apply'\nwaiting for the code\n", encoding="utf-8")
    server = gui.Server(state).start()
    fetched = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                page = browser.new_page()
                page.on("request", lambda r: fetched.append(r.url) if "/log?" in r.url else None)
                page.goto(server.url)
                page.wait_for_timeout(700)
                before_open = len(fetched)
                page.click(".job .head .name")
                page.wait_for_timeout(700)
                after_open = len(fetched)
                page.wait_for_timeout(5000)                     # two or three polls with nothing changed
                quiet = len(fetched)
                page.fill(".job .body input[placeholder='verification code']", "123456")
                page.wait_for_timeout(2600)                     # another poll
                kept = page.input_value(".job .body input[placeholder='verification code']")
                still = len(fetched)
                with open(log_path, "a", encoding="utf-8") as fh:
                    fh.write("the code box is on the page\n")
                page.wait_for_timeout(2600)
                grown = len(fetched)
                body = page.inner_text(".job .body")
                approve = page.evaluate("document.querySelectorAll('#jobs-list .job').length")
            finally:
                browser.close()
        check(before_open == 0 and after_open == 1, f"opening the card fetches the log once ({before_open}, {after_open})")
        check(quiet == 1 and still == 1, f"the polls fetch nothing while nothing changed ({quiet}, {still})")
        check(kept == "123456", f"a code typed into the box survives the poll ({kept!r})")
        check(grown == 2 and "the code box is on the page" in body, f"a log that grew is fetched again ({grown})")
        check(approve == 1, "one card")
    finally:
        server.stop()
    check.done()


def test_a_learned_yaml_that_did_not_parse_is_never_saved_over():
    # Fails on 4fdca04: a file that didn't parse loaded as empty and the next
    # save replaced it (fe30a9a's finding 3).
    check = Checks()
    prof = _prof("folds-profile-2.yaml")
    for what, text in (("a YAML syntax error", "version: 1\nentries: [\n  - {kind: answer\n"),
                       ("entries that aren't a list", "version: 1\nentries: 5\n"),
                       ("a bare scalar", "just a line\n")):
        at = TEST_HOME / "learned-broken.yaml"
        at.write_text(text, encoding="utf-8")
        raw = at.read_bytes()
        store = memory.Learned.load(at)
        check(store.entries == [] and store.unreadable, f"{what}: the store loads empty and unreadable ({store.unreadable})")
        raised = None
        try:
            memory.learn([_change(CITY, "Springfield")], url=URL, portal="generic", prof=prof, store=store)
        except memory.Unreadable as e:
            raised = e
        check(raised is not None and at.read_bytes() == raw, f"{what}: learn refuses and the file's bytes stay ({raised})")
        for op, fn in (("edit", lambda: store.edit("abc", "x")), ("delete", lambda: store.delete("abc"))):
            try:
                fn(); refused = None
            except memory.Unreadable as e:
                refused = e
            check(refused is not None and at.read_bytes() == raw, f"{what}: {op} refuses, the file stays")
        decision, _, _ = (lambda plan: (plan[0].get("c1"), plan[1], plan[2]))(
            pipeline.route([CITY], {"c1": "personal_fact"}, prof, set(), learned=store, host="x.example"))
        check(decision is None, f"{what}: nothing reused from it")
    # The job log and the Memory page say so; the default path (memory.path()).
    at = memory.path()
    at.write_text("version: 1\nentries: [\n", encoding="utf-8")
    raw = at.read_bytes()
    log = []
    got = corrections.learn_from([_change(CITY, "Springfield")], url=URL, portal="generic", prof=prof, log=log)
    check(got == [] and at.read_bytes() == raw and len(log) == 1 and "learned.yaml didn't parse" in log[0]
          and "fix or delete" in log[0], f"the job log reports it, nothing saved ({log})")
    state = gui.State()
    state.memory_path = at
    server = gui.Server(state).start()
    try:
        origin = f"http://127.0.0.1:{server.port}"
        status, _, body = _req(server, "GET", "/memory")
        check(status == 200 and body.get("ok") is False and "didn't parse" in body.get("error", "")
              and body.get("entries") == [], f"the Memory page says the file didn't parse ({body})")
        for path in ("/memory/edit", "/memory/delete"):
            status, _, body = _req(server, "POST", path, {"id": "abc", "answer": "x"}, origin=origin)
            check(body.get("ok") is False and "didn't parse" in body.get("error", ""), f"{path} refuses with the reason ({body})")
        check(at.read_bytes() == raw, "the file's bytes stay what they were")
    finally:
        server.stop()
    at.unlink(missing_ok=True)
    check.done()


if __name__ == "__main__":
    run_as_script(globals())
