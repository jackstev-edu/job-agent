"""
test_follow_fill.py — `fill --follow`: you move through the pages, the agent fills each one.

WHAT THIS DOES: Runs the real `fill --follow` on mock applications served at
real-looking hosts, with you simulated: you click Next and Submit, the
agent never clicks anything. A single page fills exactly as a normal fill
does. A three-page wizard gets each page filled as it appears, with one
tracker record, one review sheet and a gate call that stops (more than one
page). A sign-in page in the middle gets nothing, and a start on LinkedIn
fills nothing. Your resume goes only into the box labelled as the resume.
Idle time and a closed tab both end the run and still ask whether you
submitted.
WHY IT MATTERS: Follow mode is the MVP: real multi-page applications, you
clicking every button and Submit, the agent filling only what it should.

Run from anywhere:  .venv/Scripts/python.exe tests/test_follow_fill.py
"""
import json
from pathlib import Path

from _harness import TEST_HOME, Checks, run_as_script
from agent import follow, ledger, tracker

HOST = "https://apply.example.com"

CLICKS_JS = """<script>
  window.__clicks = 0; window.__keys = 0;
  document.addEventListener('click', () => { window.__clicks++; }, true);
  document.addEventListener('keydown', () => { window.__keys++; }, true);
</script>"""


def _route(html: str):
    return {f"{HOST}/**": lambda route: route.fulfill(content_type="text/html", body=html)}


def _speed(apply):
    """Short quiet periods so the tests don't wait seconds per page."""
    return ((ledger, "QUIET_MS", 200), (follow, "QUIET_MS", 200),
            (apply, "FOLLOW_POLL_MS", 150))


class _You:
    """
    Stands in for the terminal's Enter watcher, and for you: on every poll
    it runs `act(page, state)`, which may click as you would, and reports
    Enter once that returns True (or after `limit` polls).
    """

    def __init__(self, pages, act, limit=400):
        self.pages, self.act, self.limit, self.n, self.state = pages, act, limit, 0, {}

    def __call__(self):
        return self

    def pressed(self):
        self.n += 1
        if self.n > self.limit:
            return True
        page = self.pages[-1] if self.pages else None
        try:
            return bool(page is not None and not page.is_closed() and self.act(page, self.state))
        except Exception:
            return True

    def wait(self):
        pass


def _follow(html, act, path="/job", patches=(), answers=("n",), profile_yaml=None, url=None):
    import apply
    from _fill_run import PROFILE, run_fill

    pages = []
    real_open = apply.browser_mod.open_page

    def open_page(ctx, u):
        page = real_open(ctx, u)
        pages.append(page)
        return page
    you = _You(pages, act)
    text, records, error = run_fill(
        None, dry_run=False, follow=True, url=url or f"{HOST}{path}", routes=_route(html),
        answers=answers, profile_yaml=profile_yaml or PROFILE,
        patches=(*_speed(apply), (apply.browser_mod, "open_page", open_page),
                 (apply, "_EnterWatcher", you), *patches))
    return text, records, error, you


SINGLE = f"""<!doctype html><html><head><title>Data Engineer - Example Co</title></head><body>
  <form><label for="fn">First Name</label><input id="fn" name="first_name">
  <label for="em">Email</label><input id="em" name="email">
  <label for="ci">City</label><input id="ci" name="city"></form>{CLICKS_JS}</body></html>"""


def test_a_single_page_fills_as_a_normal_fill_does():
    from _fill_run import run_fill

    check = Checks()
    form = TEST_HOME / "follow_single.html"
    form.write_text(SINGLE, encoding="utf-8")
    _, normal, error1 = run_fill(form, dry_run=False)

    def act(page, state):
        state["values"] = [page.input_value(s) for s in ("#fn", "#em", "#ci")]
        state["clicks"] = page.evaluate("window.__clicks")
        return state["values"][0] != "" and state.get("seen", 0) > 3 or \
            state.__setitem__("seen", state.get("seen", 0) + (state["values"][0] != "")) or False
    text, followed, error2, you = _follow(SINGLE, act, path="/single")
    print(text[-600:])
    check(error1 is None and error2 is None, f"both runs finished ({error1}, {error2})")

    def answers(rec):
        by_ref = {f["ref"]: f["label"] for f in rec.get("fields", [])}
        return {by_ref[r]: d.get("value") for r, d in (rec.get("plan") or {}).items()}
    check(normal and followed and answers(normal[-1]) == answers(followed[-1]),
          f"the same answers planned ({answers(followed[-1]) if followed else None})")
    check(you.state.get("values") == ["Test", "test@example.invalid", ""],
          f"the same fields filled, City left blank ({you.state.get('values')})")
    check(you.state.get("clicks") == 0, "the agent clicked nothing")
    check.done()


WIZARD = f"""<!doctype html><html><head><title>Engineer - Example Co</title></head><body>
  <h1>Engineer</h1><p>Pipelines.</p>
  <form>
    <section id="s1"><label for="fn">First Name</label><input id="fn" name="first_name">
      <button type="button" id="n1">Next</button></section>
    <section id="s2" style="display:none"><label for="em">Email</label><input id="em" name="email">
      <button type="button" id="n2">Next</button></section>
    <section id="s3" style="display:none"><label for="wh">Why do you want to work here?</label>
      <textarea id="wh" name="why"></textarea>
      <button type="button" id="go">Submit Application</button></section>
  </form>
  <div id="thanks" style="display:none">Thanks!</div>
<script>
  const show = id => {{ for (const s of ['s1','s2','s3']) document.getElementById(s).style.display =
                          s === id ? '' : 'none'; }};
  document.getElementById('n1').onclick = () => show('s2');
  document.getElementById('n2').onclick = () => show('s3');
  document.getElementById('go').onclick = () => {{
    document.querySelector('form').style.display = 'none';
    document.getElementById('thanks').style.display = ''; window.__sent = true; }};
</script>{CLICKS_JS}</body></html>"""


def _wizard_you(page, state):
    """You: move on once a page is filled, Submit on the last, then Enter."""
    state.setdefault("you_clicked", 0)
    if page.evaluate("!!window.__sent"):
        state["values"] = state.get("values")
        return True
    for box, button in (("#fn", "#n1"), ("#em", "#n2"), ("#wh", "#go")):
        if page.is_visible(box) and page.input_value(box):
            state.setdefault("values", {})[box] = page.input_value(box)
            state["agent_clicks"] = page.evaluate("window.__clicks") - state["you_clicked"]
            page.click(button)
            state["you_clicked"] += 1
            break
    return False


def test_a_three_page_wizard_is_filled_page_by_page_and_logged_once():
    check = Checks()
    url = f"{HOST}/wizard"
    text, records, error, you = _follow(WIZARD, _wizard_you, path="/wizard")
    print(text[-900:])
    check(error is None, f"the run finished ({error})")
    check(you.state.get("values") == {"#fn": "Test", "#em": "test@example.invalid",
                                       "#wh": "I like the mission."},
          f"each page was filled as it appeared ({you.state.get('values')})")
    check(you.state.get("you_clicked") == 3 and you.state.get("agent_clicks") == 0,
          "you clicked Next, Next and Submit; the agent clicked nothing")
    rows = [r for r in tracker.all_applications() if r["url"] == url]
    rec = records[-1] if records else {}
    check(len(rows) == 1 and rec.get("steps") == 3, f"one record covering 3 pages ({rec.get('steps')})")
    run = [r for r in tracker.gate_runs() if r["application_url"] == url][-1]
    check(run["verdict"] == "would_stop"
          and any("multi_step" in r for r in json.loads(run["reasons_json"])),
          "the gate stops: more than one page")
    check("Review sheet:" in text and text.count("Review sheet:") == 1, "one review sheet")
    check.done()


SIGN_IN_MIDDLE = f"""<!doctype html><html><head><title>Engineer - Example Co</title></head><body>
  <form>
    <section id="s1"><label for="fn">First Name</label><input id="fn" name="first_name">
      <button type="button" id="n1">Next</button></section>
  </form>
<script>
  // The sign-in step only exists once you click Next.
  document.getElementById('n1').onclick = () => {{
    document.getElementById('s1').remove();
    document.querySelector('form').insertAdjacentHTML('beforeend',
      '<section id="s2"><label for="em">Email</label><input id="em" name="email">' +
      '<label for="pw">Password</label><input id="pw" type="password">' +
      '<button type="button" id="n2">Sign in</button></section>'); }};
</script>{CLICKS_JS}</body></html>"""


def _sign_in_you(page, state):
    if page.is_visible("#fn") and page.input_value("#fn"):
        page.click("#n1")
        state["moved"] = page.evaluate("Date.now()")
        return False
    if state.get("moved"):
        state["polls"] = state.get("polls", 0) + 1
        state["email"] = page.input_value("#em")
        return state["polls"] > 25                      # give the agent time to (not) act
    return False


def test_a_sign_in_page_gets_nothing():
    check = Checks()
    text, records, error, you = _follow(SIGN_IN_MIDDLE, _sign_in_you, path="/signin")
    rec = records[-1] if records else {}
    check(error is None and you.state.get("moved"), f"you moved to the sign-in page ({error})")
    check(you.state.get("email") == "", "the agent typed nothing there, not even your email")
    check(any(e.get("kind") == "sign_in_page" for e in rec.get("events", [])),
          "a sign_in_page event is logged")
    check.done()


def test_a_start_on_linkedin_fills_nothing():
    check = Checks()

    def act(page, state):
        state["polls"] = state.get("polls", 0) + 1
        state["fn"] = page.input_value("#fn")
        return state["polls"] > 20
    import apply
    li = "https://www.linkedin.com/jobs/view/1"
    routes = {"https://www.linkedin.com/**":
              lambda route: route.fulfill(content_type="text/html", body=SINGLE)}
    from _fill_run import run_fill
    pages = []
    real_open = apply.browser_mod.open_page

    def open_page(ctx, u):
        page = real_open(ctx, u)
        pages.append(page)
        return page
    you = _You(pages, act)
    text, records, error = run_fill(None, dry_run=False, follow=True, url=li, routes=routes,
                                    patches=(*_speed(apply), (apply.browser_mod, "open_page", open_page),
                                             (apply, "_EnterWatcher", you)))
    rec = records[-1] if records else {}
    check(error is None and you.state.get("fn") == "", f"nothing was filled ({error})")
    check(any(e.get("kind") == "linkedin_refused" for e in rec.get("events", [])),
          "a linkedin_refused event is logged")
    check.done()


TWO_FILES = f"""<!doctype html><html><head><title>Engineer - Example Co</title></head><body>
  <form><label for="cl">Cover letter</label><input type="file" id="cl" name="cover_letter">
  <label for="cv">Resume/CV</label><input type="file" id="cv" name="resume">
  <label for="fn">First Name</label><input id="fn" name="first_name"></form>{CLICKS_JS}</body></html>"""


def test_the_resume_goes_only_into_the_resume_box():
    from _fill_run import PROFILE

    check = Checks()
    (TEST_HOME / "follow-resume.pdf").write_bytes(b"%PDF-1.4 test resume\n")
    profile = PROFILE.replace("resume_default: missing.pdf", "resume_default: follow-resume.pdf")

    def act(page, state):
        state["files"] = page.evaluate(
            "() => [document.getElementById('cl').files.length, document.getElementById('cv').files.length]")
        return state["files"][1] == 1 and page.input_value("#fn") != ""
    text, records, error, you = _follow(TWO_FILES, act, path="/files", profile_yaml=profile)
    check(error is None and you.state.get("files") == [0, 1],
          f"the resume went into Resume/CV, never the first box ({you.state.get('files')})")
    check.done()


def test_idle_and_a_closed_tab_end_the_run_and_still_ask():
    import apply

    check = Checks()
    asked = []

    def ask(prompt=""):
        asked.append(prompt)
        return "n"

    def never(page, state):
        return False
    text, records, error, you = _follow(SINGLE, never, path="/idle",
                                        patches=((apply, "FOLLOW_IDLE_S", 1),
                                                 (apply, "input", ask)))
    check(error is None and "No new page and no input from you" in text
          and any("Did you submit" in a for a in asked), "idle: the run ends and asks y/n")
    asked.clear()

    def close(page, state):
        if page.input_value("#fn"):
            page.close()
        return False
    text, records, error, you = _follow(SINGLE, close, path="/closed",
                                        patches=((apply, "input", ask),))
    check(error is None and "The tab closed." in text and any("Did you submit" in a for a in asked),
          "a closed tab: the run ends and still asks y/n")
    check.done()


if __name__ == "__main__":
    run_as_script(globals())
