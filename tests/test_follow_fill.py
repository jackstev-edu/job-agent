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


ONE_PAGE_WITH_NEXT = SINGLE.replace("</form>", '<button type="button" id="nx">Next</button></form>')


def _gate(url):
    run = [r for r in tracker.gate_runs() if r["application_url"] == url][-1]
    return run["verdict"], json.loads(run["reasons_json"])


def test_a_next_control_on_the_only_page_seen_still_stops_the_gate():
    check = Checks()

    def act(page, state):
        state["n"] = state.get("n", 0) + (page.input_value("#fn") != "")
        return state["n"] > 3                          # Enter while still on page 1
    text, records, error, you = _follow(ONE_PAGE_WITH_NEXT, act, path="/nextonly")
    verdict, reasons = _gate(f"{HOST}/nextonly")
    check(error is None and verdict == "would_stop"
          and any("multi_step" in r for r in reasons),
          f"one page seen, but a Next control on it: would_stop with multi_step ({reasons})")
    check.done()


UPLOAD_THEN_HELP = f"""<!doctype html><html><head><title>Engineer - Example Co</title></head><body>
  <form>
    <section id="s1"><label for="cv">Resume</label><input type="file" id="cv" name="resume">
      <label for="fn">First Name</label><input id="fn" name="first_name">
      <button type="button" id="n1">Next</button></section>
  </form>
<script>
  document.getElementById('n1').onclick = () => {{
    document.getElementById('s1').remove();
    document.querySelector('form').insertAdjacentHTML('beforeend',
      '<section id="s2"><label for="ph">Phone</label>' +
      '<input id="ph" name="phone" aria-describedby="ph-h">' +
      '<div id="ph-h">HELP-SENTINEL-4471 is not a valid phone number</div></section>'); }};
</script>{CLICKS_JS}</body></html>"""


def _helps_sent(sent, label):
    """The help text every model call was given for the field labelled `label`."""
    out = []
    for s in sent:
        for f in json.loads(s):
            if f.get("label") == label:
                out.append(f.get("help_text") or "")
    return out


def test_an_empty_fields_help_text_after_the_upload_is_classified_with_it():
    import apply
    from _fill_run import PROFILE, classify_by_label, map_from_profile

    check = Checks()
    (TEST_HOME / "follow-help-resume.pdf").write_bytes(b"%PDF-1.4 test resume\n")
    profile = PROFILE.replace("resume_default: missing.pdf",
                              "resume_default: follow-help-resume.pdf")
    sent = []

    def classify(fields):
        sent.append(json.dumps(fields))
        return classify_by_label(fields)

    def map_fields(fields, prof, page_text=""):
        sent.append(json.dumps(fields))
        return map_from_profile(fields, prof, page_text)

    def act(page, state):
        if page.is_visible("#fn") and page.input_value("#fn"):
            page.click("#n1")
            return False
        if page.is_visible("#ph"):
            state["n"] = state.get("n", 0) + 1
            return state["n"] > 25
        return False
    text, records, error, you = _follow(
        UPLOAD_THEN_HELP, act, path="/uploadhelp", profile_yaml=profile,
        patches=((apply.classify, "classify_fields", classify),
                 (apply.mapper, "map_fields", map_fields)))
    rec = records[-1] if records else {}
    reasons = [fl.get("reason", "") for fl in rec.get("flags", [])
               if fl.get("label") == "Phone"]
    check(error is None and any("HELP-SENTINEL" in json.dumps(f) for f in rec.get("fields", [])),
          f"page 2's help text was scanned ({error})")
    helps = _helps_sent(sent, "Phone")
    check(helps and all("HELP-SENTINEL-4471" in h for h in helps),
          f"empty Phone went to the model with its static help text ({helps})")
    check(not any("held back" in r for r in reasons), f"and isn't held back for you ({reasons})")
    check.done()


RESUME_ON_PAGE_TWO = f"""<!doctype html><html><head><title>Engineer - Example Co</title></head><body>
  <form>
    <section id="s1"><label for="fn">First Name</label><input id="fn" name="first_name">
      <button type="button" id="n1">Next</button></section>
  </form>
<script>
  document.getElementById('n1').onclick = () => {{
    document.getElementById('s1').remove();
    document.querySelector('form').insertAdjacentHTML('beforeend',
      '<section id="s2"><label for="cv">Resume</label><input type="file" id="cv" name="resume">' +
      '<label for="ph">Phone</label><input id="ph" name="phone" aria-describedby="ph-h">' +
      '<div id="ph-h">HELP-SENTINEL-5582 is not a valid phone number</div></section>'); }};
</script>{CLICKS_JS}</body></html>"""


def _spies():
    from _fill_run import classify_by_label, map_from_profile
    sent = []

    def classify(fields):
        sent.append(json.dumps(fields))
        return classify_by_label(fields)

    def map_fields(fields, prof, page_text=""):
        sent.append(json.dumps(fields))
        return map_from_profile(fields, prof, page_text)
    return sent, classify, map_fields


def test_help_text_next_to_a_resume_box_on_a_later_page_goes_over_after_the_upload():
    import apply
    from _fill_run import PROFILE

    check = Checks()
    (TEST_HOME / "follow-p2-resume.pdf").write_bytes(b"%PDF-1.4 test resume\n")
    profile = PROFILE.replace("resume_default: missing.pdf", "resume_default: follow-p2-resume.pdf")
    sent, classify, map_fields = _spies()

    def act(page, state):
        if page.is_visible("#fn") and page.input_value("#fn"):
            page.click("#n1")
            return False
        if page.is_visible("#ph"):
            state["n"] = state.get("n", 0) + 1
            state["files"] = page.evaluate("document.getElementById('cv').files.length")
            return state["n"] > 25
        return False
    text, records, error, you = _follow(
        RESUME_ON_PAGE_TWO, act, path="/resumep2", profile_yaml=profile,
        patches=((apply.classify, "classify_fields", classify),
                 (apply.mapper, "map_fields", map_fields)))
    check(error is None and you.state.get("files") == 1,
          f"the resume was uploaded on page 2 (so the checks below mean something) ({error})")
    helps = _helps_sent(sent, "Phone")
    check(helps and all("HELP-SENTINEL-5582" in h for h in helps),
          f"empty Phone, scanned after the upload, went over with its help text ({helps})")
    rec = records[-1] if records else {}
    reasons = [fl.get("reason", "") for fl in rec.get("flags", []) if fl.get("label") == "Phone"]
    check(not any("held back" in r for r in reasons), f"and isn't held back for you ({reasons})")
    check.done()


ECHO = f"""<!doctype html><html><head><title>Engineer - Example Co</title></head><body>
  <form><label for="em">Email</label><input id="em" name="email" aria-describedby="em-h">
    <div id="em-h">We'll use this to contact you</div>
    <label for="ci">City</label><input id="ci" name="city"></form>
<script>
  // The page echoes what you type into its validation text.
  document.getElementById('em').addEventListener('input', e => {{
    document.getElementById('em-h').textContent =
      "'" + e.target.value + "' ECHO-SENTINEL-7714 is not a valid email"; }});
</script>{CLICKS_JS}</body></html>"""


def test_text_you_typed_before_the_first_scan_never_reaches_a_model():
    import apply

    check = Checks()
    sent, classify, map_fields = _spies()

    def act(page, state):
        if not state.get("typed"):
            page.fill("#em", "jo@x")                  # you, before the agent's first scan
            state["typed"] = True
            return False
        state["em"] = page.input_value("#em")
        state["n"] = state.get("n", 0) + 1
        return state["n"] > 25
    text, records, error, you = _follow(
        ECHO, act, path="/echo",
        patches=((apply.classify, "classify_fields", classify),
                 (apply.mapper, "map_fields", map_fields)))
    rec = records[-1] if records else {}
    check(error is None and any("ECHO-SENTINEL" in json.dumps(f) for f in rec.get("fields", [])),
          f"the scan did see the echoed help text (so the next check means something) ({error})")
    check(all("ECHO-SENTINEL" not in s for s in sent) and sent,
          "it reached no classify or mapper call")
    check(not _helps_sent(sent, "Email") and _helps_sent(sent, "City"),
          "the field you typed in is in no model input; the empty City is")
    check(you.state.get("em") == "jo@x", "  ...and what you typed is still there")
    check.done()


def test_a_rescan_of_page_one_after_a_failed_fill_keeps_your_answer_from_the_model():
    import apply

    check = Checks()
    sent, classify_ok, map_fields = _spies()
    calls = {"n": 0}

    def classify(fields):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("you clicked mid-fill")   # nothing recorded for page 1
        return classify_ok(fields)

    def act(page, state):
        if calls["n"] >= 1 and not state.get("revealed"):
            # Your answer reveals a follow-up question, which you answer;
            # its help text echoes what you typed.
            page.evaluate("""() => document.querySelector('form').insertAdjacentHTML('beforeend',
                '<label for="x">Extra</label><input id="x" name="extra" aria-describedby="xh">' +
                '<div id="xh">help</div>')""")
            page.fill("#x", "my answer")
            page.evaluate("""() => document.getElementById('xh').textContent =
                "'my answer' HELP-SENTINEL-6693" """)
            state["revealed"] = True
        if state.get("revealed"):
            state["n"] = state.get("n", 0) + 1
            return state["n"] > 25
        return False
    text, records, error, you = _follow(
        SINGLE, act, path="/rescan",
        patches=((apply.classify, "classify_fields", classify),
                 (apply.mapper, "map_fields", map_fields)))
    rec = records[-1] if records else {}
    check(error is None and calls["n"] >= 2 and any(
        "HELP-SENTINEL" in json.dumps(f) for f in rec.get("fields", [])),
          f"page 1 was rescanned with the revealed field (so the next check means something) ({error})")
    check(all("HELP-SENTINEL" not in s and "my answer" not in s for s in sent),
          "the echo of your answer reached no model call")
    check(not _helps_sent(sent, "Extra"), "the field you answered is in no model input")
    check.done()


def test_the_final_read_is_marked_when_it_isnt_the_filled_form():
    import apply

    check = Checks()

    def never(page, state):
        return False
    text, records, error, you = _follow(SINGLE, never, path="/offform",
                                        patches=((apply, "FOLLOW_IDLE_S", 1),))
    rec = records[-1] if records else {}
    check(any(e.get("kind") == "final_read_off_form" for e in rec.get("events", [])),
          "ended by idle time: the final read is marked as not vouching for the form")
    verdict, reasons = _gate(f"{HOST}/offform")
    check(verdict == "would_stop" and any("final_read_off_form" in r for r in reasons),
          "  ...and the gate stops on it")

    def enter_when_filled(page, state):
        state["n"] = state.get("n", 0) + (page.input_value("#fn") != "")
        return state["n"] > 3
    text, records, error, you = _follow(SINGLE, enter_when_filled, path="/onform")
    rec = records[-1] if records else {}
    check(not any(e.get("kind") == "final_read_off_form" for e in rec.get("events", [])),
          "Enter on the form the agent filled: no mark")
    check.done()


def test_a_failed_page_fill_is_logged_and_the_run_still_ends_properly():
    import apply

    check = Checks()
    asked = []

    def boom(fields, prof, page_text=""):
        raise RuntimeError("the page changed under us")

    def ask(prompt=""):
        asked.append(prompt)
        return "n"

    def act(page, state):
        state["n"] = state.get("n", 0) + 1
        return state["n"] > 30
    text, records, error, you = _follow(SINGLE, act, path="/boom",
                                        patches=((apply.mapper, "map_fields", boom),
                                                 (apply, "input", ask)))
    rec = records[-1] if records else {}
    check(error is None and any(e.get("kind") == "fill_step_failed" for e in rec.get("events", [])),
          f"a fill_step_failed event is logged ({error})")
    check(any("Did you submit" in a for a in asked), "  ...and you're still asked whether you submitted")
    check.done()


def test_follow_and_dry_run_together_are_refused():
    from _fill_run import run_fill

    check = Checks()
    text, records, error = run_fill(None, dry_run=True, follow=True, url=f"{HOST}/dry",
                                    routes=_route(SINGLE))
    check(error is None and "don't go together" in text and not records,
          "refused before the browser opens")
    check.done()


PREFILLED = f"""<!doctype html><html><head><title>Engineer - Example Co</title></head><body>
  <form><label for="ci">City</label>
    <input id="ci" name="home_city" value="PREFILL-SENTINEL-3307" aria-describedby="ci-h">
    <div id="ci-h">Saved from your draft: PREFILL-SENTINEL-3307</div>
    <label for="fn">First Name</label><input id="fn" name="first_name"></form>
{CLICKS_JS}</body></html>"""


def test_a_field_already_holding_a_value_is_in_no_model_input():
    import apply

    check = Checks()
    sent, classify, map_fields = _spies()

    def act(page, state):
        state["city"] = page.input_value("#ci")
        state["n"] = state.get("n", 0) + (page.input_value("#fn") != "")
        return state["n"] > 3
    text, records, error, you = _follow(
        PREFILLED, act, path="/prefilled",
        patches=((apply.classify, "classify_fields", classify),
                 (apply.mapper, "map_fields", map_fields)))
    check(error is None and _helps_sent(sent, "First Name"),
          f"the empty field went to the model (so the next check means something) ({error})")
    check(all("PREFILL-SENTINEL" not in s and "home_city" not in s for s in sent),
          "the prefilled field (label, name, help, value) is in no classify or mapper call")
    check(you.state.get("city") == "PREFILL-SENTINEL-3307", "  ...and it's left as it was")
    check.done()


def test_a_normal_fill_still_classifies_every_field_with_its_help():
    import apply
    from _fill_run import run_fill

    check = Checks()
    sent, classify, map_fields = _spies()
    text, records, error = run_fill(
        None, dry_run=True, url=f"{HOST}/normal", routes=_route(PREFILLED),
        patches=((apply.classify, "classify_fields", classify),
                 (apply.mapper, "map_fields", map_fields)))
    check(error is None and any("PREFILL-SENTINEL" in h for h in _helps_sent(sent, "City")),
          f"a normal fill's first scan sends every field, with its help, as before ({error})")
    check.done()


POSTING = f"""<!doctype html><html><head><title>Engineer - Example Co</title></head><body>
  <h1>Engineer</h1>
  <label for="alert">Get job alerts by email</label><input id="alert" name="alert_email">
  <form id="app" style="display:none"><label for="fn">First Name</label>
    <input id="fn" name="first_name"></form>
  <button type="button" id="apply" onclick="document.getElementById('app').style.display=''">Apply</button>
{CLICKS_JS}</body></html>"""


def test_nothing_is_filled_before_you_open_the_form():
    import apply

    check = Checks()
    seen_at_prompt = []
    pages_ref = []
    real_open = apply.browser_mod.open_page

    def open_page(ctx, u):
        page = real_open(ctx, u)
        pages_ref.append(page)
        return page

    def ask(prompt=""):
        page = pages_ref[-1]
        if not seen_at_prompt:
            # The Apply prompt: what the page holds before you press Enter.
            seen_at_prompt.append((page.input_value("#alert"), page.evaluate("window.__keys")))
            page.click("#apply")                      # you open the form
            return ""
        return "n"

    def act(page, state):
        state["n"] = state.get("n", 0) + (page.is_visible("#fn") and page.input_value("#fn") != "")
        return state["n"] > 3
    text, records, error, you = _follow(
        POSTING, act, path="/posting",
        patches=((apply.browser_mod, "open_page", open_page), (apply, "input", ask)))
    check(error is None and "Click Apply if the form isn't open, then press Enter." in text,
          f"follow mode asks you to open the form first ({error})")
    check(seen_at_prompt and seen_at_prompt[0] == ("", 0),
          f"nothing was filled or typed before you pressed Enter ({seen_at_prompt})")
    check.done()


def test_an_ending_other_than_enter_lets_the_watcher_take_its_line_first():
    import apply

    check = Checks()
    order = []
    real_ask = apply._ask_submitted

    class _Waits(_You):
        def wait(self):
            order.append("watcher took its line")

    def ask():
        order.append("y/n asked")
        return real_ask()

    def never(page, state):
        return False
    pages = []
    real_open = apply.browser_mod.open_page

    def open_page(ctx, u):
        page = real_open(ctx, u)
        pages.append(page)
        return page
    text, records, error, you = _follow(
        SINGLE, never, path="/race",
        patches=((apply, "FOLLOW_IDLE_S", 1), (apply, "_ask_submitted", ask),
                 (apply, "_EnterWatcher", _Waits(pages, never)),
                 (apply.browser_mod, "open_page", open_page)))
    check(error is None and "Press Enter to continue." in text,
          f"ended by idle time: you're asked to press Enter ({error})")
    check(order == ["watcher took its line", "y/n asked"],
          f"the watcher takes its line before the y/n question ({order})")
    check.done()


def _files_page(boxes: str) -> str:
    return (f"<!doctype html><html><head><title>Engineer - Example Co</title></head><body>"
            f"<form>{boxes}<label for='fn'>First Name</label><input id='fn' name='first_name'>"
            f"</form>{CLICKS_JS}</body></html>")


def test_the_resume_box_rules():
    from _fill_run import PROFILE

    check = Checks()
    resume = TEST_HOME / "rules-resume.pdf"
    resume.write_bytes(b"%PDF-1.4 test resume\n")
    own = TEST_HOME / "jacks-own.pdf"
    own.write_bytes(b"%PDF-1.4 your own\n")
    profile = PROFILE.replace("resume_default: missing.pdf", "resume_default: rules-resume.pdf")
    files_js = "() => [...document.querySelectorAll('input[type=file]')].map(b => [...b.files].map(f => f.name))"
    cases = {
        "two boxes both say resume: no upload": (
            "<label for='a'>Resume</label><input type='file' id='a' name='resume'>"
            "<label for='b'>CV</label><input type='file' id='b' name='cv'>", [[], []], False),
        "no box says resume: no upload": (
            "<label for='a'>Attachments</label><input type='file' id='a' name='attachments'>",
            [[]], False),
        "a camelCase name behind a generic label: uploaded": (
            "<label for='a'>Attach</label><input type='file' id='a' name='resumeUpload'>",
            [["rules-resume.pdf"]], False),
        "the box already holds your file: left alone": (
            "<label for='a'>Resume</label><input type='file' id='a' name='resume'>",
            [["jacks-own.pdf"]], True),
    }
    for i, (what, (boxes, want, yours_first)) in enumerate(cases.items()):
        def act(page, state, yours_first=yours_first):
            if yours_first and not state.get("yours"):
                page.set_input_files("#a", str(own))  # you, before the agent's scan
                state["yours"] = True
            state["files"] = page.evaluate(files_js)
            state["n"] = state.get("n", 0) + (page.input_value("#fn") != "")
            return state["n"] > 3
        text, records, error, you = _follow(_files_page(boxes), act, path=f"/rules{i}",
                                            profile_yaml=profile)
        check(error is None and you.state.get("files") == want,
              f"{what} ({you.state.get('files')})")
    check.done()


START_THEN_SIGN_IN = {
    "/start": SINGLE,
    "/signin": f"""<!doctype html><html><body><form>
      <label for="em">Email</label><input id="em" type="email">
      <label for="pw">Password</label><input id="pw" type="password"></form>{CLICKS_JS}</body></html>""",
}


def test_the_idle_ending_works_on_a_refused_page_after_you_navigate():
    import apply
    from _fill_run import run_fill

    check = Checks()

    def serve(route):
        path = "/" + route.request.url.split("/", 3)[3].split("?")[0]
        route.fulfill(content_type="text/html", body=START_THEN_SIGN_IN.get(path, SINGLE))

    def act(page, state):
        if not state.get("moved") and page.input_value("#fn"):
            page.goto(f"{HOST}/signin")                # you, to a sign-in page
            state["moved"] = True
        return False
    pages = []
    real_open = apply.browser_mod.open_page

    def open_page(ctx, u):
        page = real_open(ctx, u)
        pages.append(page)
        return page
    you = _You(pages, act, limit=200)
    text, records, error = run_fill(
        None, dry_run=False, follow=True, url=f"{HOST}/start", routes={f"{HOST}/**": serve},
        patches=(*_speed(apply), (apply.browser_mod, "open_page", open_page),
                 (apply, "_EnterWatcher", you), (apply, "FOLLOW_IDLE_S", 2)))
    check(error is None and you.state.get("moved"), f"you moved to the sign-in page ({error})")
    check("No new page and no input from you" in text,
          "the idle ending fired there (the guard was installed on the refused page)")
    check.done()


if __name__ == "__main__":
    run_as_script(globals())
