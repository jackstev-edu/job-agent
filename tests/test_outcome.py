"""
test_outcome.py — watching what you do after a fill, without touching anything.

WHAT THIS DOES: Checks the edit diff (including the would_tick / leave
baselines for allowlisted consent boxes, both ways), how the confirmation
page and your answer combine into y / n / unknown, each portal's success
signature on pages served at real-looking hosts, and the watcher end to end,
file boxes included (compared by file names and count, never contents).
In the end-to-end runs a scripted "you" edits a field, types into a password
box and flips it to plain text, then submits. Every value Python reads is
recorded and searched for the password. Also checks `fill` stores the
outcome against its gate verdict.
WHY IT MATTERS: Scores are only worth having if the outcome is right or
honestly unknown, and a password must never be read, even to diff it.

Run from anywhere:  .venv/Scripts/python.exe tests/test_outcome.py
"""
import json
from pathlib import Path

from _fill_run import PROFILE, run_fill
from _harness import TEST_HOME, Checks, mock_page, run_as_script
from agent import extract, outcome, tracker

HERE = Path(__file__).parent
PASSWORD = "PW-SENTINEL-8812"
APP_URL = "https://job-boards.greenhouse.io/acme/jobs/1"

APP_HTML = """<!doctype html><html><body>
<form id="app" onsubmit="event.preventDefault(); if (window.__blockSubmit) { return; }
      location.href = '/acme/jobs/1/confirmation';">
  <label for="fn">First Name *</label><input id="fn" required value="">
  <label for="em">Email</label><input id="em" type="email" value="">
  <label for="pw">Create a password</label><input id="pw" type="password">
  <button type="button" id="show" onclick="const p = document.getElementById('pw');
          p.type = p.type === 'password' ? 'text' : 'password';">Show</button>
  <label for="np">Confirm</label><input id="np" type="text" autocomplete="new-password">
  <input type="checkbox" id="sms"><label for="sms">I agree to receive SMS updates</label>
  <button type="submit" id="go">Submit Application</button>
</form>
<p>Thank you for applying is what we'll say afterwards.</p>
</body></html>"""

THANKS_HTML = "<!doctype html><html><body><h1>Thank you for applying to Acme, Test!</h1></body></html>"


def _routes(pages: dict):
    def handle(route):
        path = route.request.url.split("greenhouse.io", 1)[-1].split("?")[0]
        route.fulfill(status=200, content_type="text/html", body=pages.get(path, "<p>404</p>"))
    return {"https://job-boards.greenhouse.io/**": handle}


def _spy(page) -> list:
    """Record everything page.evaluate hands back to Python."""
    seen, real = [], page.evaluate

    def spy(*a, **k):
        out = real(*a, **k)
        seen.append(out)
        return out
    page.evaluate = spy
    return seen


def _record(page, *, portal="greenhouse", method="host"):
    fields = extract.extract_fields(page)
    by = lambda start: next(f for f in fields if (f.get("label") or "").startswith(start))
    plan = {by("First Name")["ref"]: {"value": "Test"}, by("Email")["ref"]: {"value": "t@x.invalid"}}
    return {"url": APP_URL, "final_url": APP_URL, "portal": portal, "portal_method": method,
            "fields": fields, "plan": plan}, by


def test_edits_and_consent_baselines():
    check = Checks()
    rec = {"fields": [{"ref": "t", "type": "text", "label": "City"},
                      {"ref": "b", "type": "text", "label": "Middle"},
                      {"ref": "w", "type": "checkbox", "label": "I certify"},
                      {"ref": "l", "type": "checkbox", "label": "SMS"},
                      {"ref": "p", "type": "password", "label": "Password"},
                      {"ref": "c", "type": "combobox", "label": "Country"}],
           "plan": {"t": {"value": "Springfield"}, "c": {"value": "Exampleland"}}}
    gate = {"consent_actions": {"w": "would_tick", "l": "leave"}}
    kinds = lambda final: {e["ref"]: e["kind"] for e in outcome.compute_edits(rec, gate, final)}

    same = {"t": "springfield ", "b": "", "w": True, "l": False, "c": "Exampleland"}
    check(kinds(same) == {}, "agent's answers kept, would_tick ticked, leave left -> no edits")
    check(kinds({**same, "w": False}) == {"w": "unticked"},
          "would_tick box left unticked -> an edit")
    check(kinds({**same, "l": True}) == {"l": "ticked"}, "leave box ticked -> an edit")
    check(kinds({**same, "t": "Shelbyville", "b": "Q."}) == {"t": "changed", "b": "filled_blank"},
          "a changed answer and a filled blank")
    check(kinds({**same, "t": ""}) == {"t": "cleared"}, "a cleared answer")
    check(kinds({**same, "c": ""}) == {"c": "unreadable"}, "a dropdown read back blank is unreadable")
    missing = dict(same); del missing["b"]
    check(kinds(missing) == {"b": "unreadable"}, "a field missing from the read is unreadable")
    check("p" not in kinds({**same, "p": "x"}), "password fields are never diffed")
    check(outcome.compute_edits(rec, gate, None) is None, "never read -> edits unknown (None)")
    check(not any("Springfield" in json.dumps(e) for e in
                  outcome.compute_edits(rec, gate, {**same, "t": "Shelbyville"})),
          "edits name fields, never values")
    check.done()


def test_file_box_edits():
    # A file box is compared by the names (and so the count) of its files
    # against what the agent uploaded. Contents never come into it.
    check = Checks()
    rec = {"fields": [{"ref": "cv", "type": "file", "label": "Resume"},
                      {"ref": "cl", "type": "file", "label": "Cover letter"},
                      {"ref": "t", "type": "text", "label": "City"}],
           "plan": {"t": {"value": "Springfield"}}, "uploads": {"cv": ["resume.pdf"]}}
    gate = {"consent_actions": {}}
    kinds = lambda final, r=rec: {e["ref"]: e["kind"]
                                  for e in outcome.compute_edits(r, gate, final)}
    same = {"cv": ["resume.pdf"], "cl": [], "t": "Springfield"}
    check(kinds(same) == {}, "resume unchanged, nothing added -> no edit")
    check(kinds({**same, "cv": ["other.pdf"]}) == {"cv": "file_replaced"},
          "resume replaced -> an edit")
    check(kinds({**same, "cv": ["resume.pdf", "extra.pdf"]}) == {"cv": "file_replaced"},
          "same name plus another file: the count differs -> an edit")
    check(kinds({**same, "cv": []}) == {"cv": "file_removed"}, "resume removed -> an edit")
    check(kinds({**same, "cl": ["letter.pdf"]}) == {"cl": "file_added"},
          "a file put in a box the agent left empty -> an edit")
    check(kinds({**same, "cv": "C:\\fakepath\\resume.pdf"}) == {"cv": "unreadable"},
          "a read that isn't a list of names is unreadable, never a match")
    missing = dict(same); del missing["cv"]
    check(kinds(missing) == {"cv": "unreadable"}, "a file box missing from the read is unreadable")
    unknown = {**rec, "uploads": None}
    check(kinds(same, unknown) == {"cv": "unreadable", "cl": "unreadable"},
          "no upload baseline -> every file box unreadable, so zero edits can't be claimed")
    no_key = {k: v for k, v in rec.items() if k != "uploads"}
    check(kinds(same, no_key) == {"cv": "unreadable", "cl": "unreadable"},
          "a record without the baseline at all is treated the same")
    edits = outcome.compute_edits(rec, gate, {**same, "cv": ["Secret Name CV.pdf"]})
    check("Secret Name" not in json.dumps(edits), "edits name the box, never the file")
    check.done()


def test_hook_payload_is_checked():
    # A page can call the hook itself. Only our snapshot's shape is accepted.
    check = Checks()
    refs = {"f0", "f1"}
    check(outcome._accept({"f0": "Tess", "f1": True}, refs) == {"f0": "Tess", "f1": True},
          "known refs with short plain values are accepted")
    for junk, what in (("x", "a string"), ({}, "an empty dict"), ({"f9": "x"}, "an unknown ref"),
                       ({"f0": {"nested": 1}}, "a nested value"), ({"f0": 3}, "a number"),
                       ({"f0": "x" * 6000}, "an over-long value"), (None, "nothing"),
                       ({"f0": ["a.pdf", 3]}, "a file list with a non-name in it"),
                       ({"f0": [["a.pdf"]]}, "a nested file list"),
                       ({"f0": ["x"] * 51}, "too many file names"),
                       ({"f0": ["x" * 1001]}, "an over-long file name")):
        check(outcome._accept(junk, refs) is None, f"rejects {what}")
    check(outcome._accept({"f0": ["resume.pdf"], "f1": []}, refs)
          == {"f0": ["resume.pdf"], "f1": []}, "a file box's list of names is accepted")
    check.done()


def test_outcome_combination():
    check = Checks()
    hs = outcome.human_submitted
    check(hs(detected="match", answer="y", signature_verified=False) == "y", "seen + you say y -> y")
    check(hs(detected="none", answer="y", signature_verified=False) == "y", "not seen + y -> y")
    check(hs(detected="none", answer="n", signature_verified=False) == "n", "not seen + n -> n")
    check(hs(detected="match", answer="n", signature_verified=True) == "unknown",
          "seen but you say n -> unknown, never a guess")
    check(hs(detected="match", answer=None, signature_verified=False) == "unknown",
          "seen, no answer, signature unconfirmed -> unknown")
    check(hs(detected="match", answer=None, signature_verified=True) == "y",
          "seen, no answer, signature confirmed before -> y")
    check(hs(detected="partial", answer=None, signature_verified=True) == "unknown",
          "partial signals -> unknown")
    runs = [{"portal": "greenhouse", "success_signal": json.dumps({"detected": "match", "answer": "y"})}]
    check(outcome.signature_verified("greenhouse", runs) and not outcome.signature_verified("lever", runs),
          "a signature is confirmed per portal, by a past match you said y to")
    check(not outcome.signature_verified("greenhouse", [{"portal": "greenhouse", "success_signal": "x"}]),
          "unreadable history confirms nothing")
    check.done()


def test_success_signatures():
    check = Checks()
    pages = {"/acme/jobs/1": APP_HTML, "/acme/jobs/1/confirmation": THANKS_HTML,
             "/acme/jobs/2/confirmation": "<p>Something went wrong</p>"}
    with mock_page(HERE / "gate_form.html", _routes(pages)) as page:
        page.goto(APP_URL)
        rec, _ = _record(page)
        # Whether the form is still there reads no value, so it uses every ref
        # (as the watcher does): this page has a password box, so nothing on
        # it is readable.
        refs = [r for f in rec["fields"] for r in (f.get("option_refs") or [f["ref"]])]
        d = lambda **k: outcome.detect_success(page, refs=refs, start_url=APP_URL,
                                               **{"portal": "greenhouse", "portal_method": "host", **k})
        before = d()
        check(before["result"] == "partial" and before["signals"]["text"] and not before["signals"]["form_gone"],
              "the form says 'Thank you for applying' before submit: partial, not a match")
        check(d(portal_method="html")["result"] == "unsupported", "an HTML-detected portal never matches")
        check(d(portal="generic")["result"] == "unsupported", "a portal with no signature never matches")
        page.goto("https://job-boards.greenhouse.io/acme/jobs/1/confirmation")
        after = d()
        check(after["result"] == "match" and after["url_shape"] == "job-boards.greenhouse.io/acme/jobs/<n>/confirmation",
              "confirmation URL + text + form gone -> match; URL kept as a shape")
        check("Test" not in json.dumps(after), "the heading's text (your name) isn't kept")
        page.goto("https://job-boards.greenhouse.io/acme/jobs/2/confirmation")
        check(d()["result"] == "partial", "confirmation URL but no confirmation text -> partial")
    check.done()


def test_watch_submit_with_a_password_present():
    check = Checks()
    pages = {"/acme/jobs/1": APP_HTML, "/acme/jobs/1/confirmation": THANKS_HTML}
    with mock_page(HERE / "gate_form.html", _routes(pages)) as page:
        page.goto(APP_URL)
        rec, by = _record(page)
        page.fill("#fn", "Test"); page.fill("#em", "t@x.invalid")       # as the agent left it
        seen = _spy(page)
        # The submit-time read arrives through the hook's binding, not
        # evaluate: record what it's handed too.
        real_accept = outcome._accept
        hooked: list = []
        outcome._accept = lambda raw, refs: (seen.append(raw), hooked.append(raw),
                                             real_accept(raw, refs))[2]
        steps = iter([
            lambda: page.fill("#fn", "Tess"),                            # you edit a field
            lambda: page.fill("#pw", PASSWORD),                          # you type a password
            lambda: page.click("#show"),                                 # ...and show it as text
            lambda: page.fill("#np", PASSWORD),                          # ...and confirm it
            lambda: page.click("#go"),                                   # you submit
        ])

        def you():
            step = next(steps, None)
            if step:
                step()
            return False
        try:
            watched = outcome.watch(page, record=rec, stop_requested=you, poll_ms=100)
        finally:
            outcome._accept = real_accept
    print(f"  ended {watched['ended']}, capture {watched['capture']}, "
          f"detected {watched['detection']['result']}, final {watched['final']}")
    check(watched["ended"] == "success" and watched["detection"]["result"] == "match",
          "the confirmation page ended the watch")
    # A page with a password box is a credential step: nothing on it is read,
    # your edit included, so no zero-edit claim can be made.
    check(not watched["final"], "nothing on the page was read, next to a password box")
    check(hooked and all(p == {} for p in hooked),
          "the hook fired at submit, and handed back nothing (so the next check means something)")
    check(watched["capture"] != "exact", "  ...so there's no exact submit-time read")
    check(PASSWORD not in json.dumps(seen, default=str), "the password never reached Python, "
          "even shown as text, by evaluate or by the hook")
    edits = outcome.compute_edits(rec, {"consent_actions": {}}, watched["final"])
    kinds = {e["label"]: e["kind"] for e in edits}
    check(kinds == {"First Name *": "unreadable", "Email": "unreadable", "Confirm": "unreadable",
                    "I agree to receive SMS updates": "unreadable"}
          and PASSWORD not in json.dumps(edits),
          "every field next to the password box is unreadable, so zero edits can't be claimed")
    check.done()


FILE_SENTINEL = "FILE-CONTENT-SENTINEL-5531"
# The resume form without its password boxes: a page with one is a credential
# step, and nothing on it would be read.
FILE_HTML = (APP_HTML
             .replace('<label for="pw">Create a password</label><input id="pw" type="password">',
                      '<label for="cv">Resume</label><input id="cv" type="file">')
             .replace('<label for="np">Confirm</label><input id="np" type="text" '
                      'autocomplete="new-password">', ''))


def _resume_files() -> tuple[Path, Path]:
    d = TEST_HOME / "uploads"
    d.mkdir(exist_ok=True)
    mine, other = d / "resume.pdf", d / "other.pdf"
    mine.write_text(f"agent upload {FILE_SENTINEL}", encoding="utf-8")
    other.write_text(f"your replacement {FILE_SENTINEL}", encoding="utf-8")
    return mine, other


def _watch_file_form(you_replace: bool):
    """The agent uploads the resume, rescans, then a scripted you submits."""
    mine, other = _resume_files()
    pages = {"/acme/jobs/1": FILE_HTML, "/acme/jobs/1/confirmation": THANKS_HTML}
    with mock_page(HERE / "gate_form.html", _routes(pages)) as page:
        page.goto(APP_URL)
        first = extract.extract_fields(page)
        cv = next(f for f in first if f.get("type") == "file")
        box = page.query_selector(f"[{extract.REF_ATTR}='{cv['ref']}']")
        page.set_input_files(f"[{extract.REF_ATTR}='{cv['ref']}']", str(mine))   # as the agent
        rec, by = _record(page)                                                 # the rescan
        ref = outcome.upload_ref(box, rec["fields"])
        rec["uploads"] = {ref: [mine.name]} if ref else None
        page.fill("#fn", "Test"); page.fill("#em", "t@x.invalid")
        seen = _spy(page)
        real_accept = outcome._accept
        outcome._accept = lambda raw, refs: (seen.append(raw), real_accept(raw, refs))[1]
        steps = iter(([lambda: page.set_input_files("#cv", str(other))] if you_replace else [])
                     + [lambda: page.click("#go")])

        def you():
            step = next(steps, None)
            if step:
                step()
            return False
        try:
            watched = outcome.watch(page, record=rec, stop_requested=you, poll_ms=100)
        finally:
            outcome._accept = real_accept
    return rec, by, ref, watched, seen


def test_watch_file_box_replaced_and_unchanged():
    check = Checks()
    for replace in (True, False):
        rec, by, ref, watched, seen = _watch_file_form(replace)
        what = "replaced" if replace else "unchanged"
        print(f"  {what}: ended {watched['ended']}, capture {watched['capture']}, "
              f"final {watched['final']}")
        check(ref == by("Resume")["ref"], f"{what}: the rescan's ref for the uploaded box found")
        check(watched["ended"] == "success" and watched["capture"] == "exact",
              f"{what}: read at the moment of submitting")
        check(watched["final"].get(ref) == (["other.pdf"] if replace else ["resume.pdf"]),
              f"{what}: the box read back as its file names")
        kinds = {e["label"]: e["kind"] for e in
                 outcome.compute_edits(rec, {"consent_actions": {}}, watched["final"])}
        check(kinds.get("Resume") == ("file_replaced" if replace else None),
              f"{what}: resume {'replaced -> an edit' if replace else 'unchanged -> no edit'}")
        check(any(isinstance(x, dict) and ref in x for x in seen),
              f"{what}: a read holding the file box was recorded (so the next check means something)")
        check(FILE_SENTINEL not in json.dumps(seen, default=str),
              f"{what}: no file contents reached Python")
        check(PASSWORD not in json.dumps(seen, default=str),
              f"{what}: the password never reached Python")
    check.done()


def test_upload_ref_needs_a_unique_live_box():
    check = Checks()
    pages = {"/acme/jobs/1": FILE_HTML}
    with mock_page(HERE / "gate_form.html", _routes(pages)) as page:
        page.goto(APP_URL)
        fields = extract.extract_fields(page)
        cv = next(f for f in fields if f.get("type") == "file")
        box = page.query_selector("#cv")
        check(outcome.upload_ref(box, fields) == cv["ref"], "a live box holding its own ref")
        check(outcome.upload_ref(None, fields) is None, "no handle -> unknown")
        text_only = [f for f in fields if f.get("type") != "file"]
        check(outcome.upload_ref(box, text_only) is None, "a ref that isn't a file field -> unknown")
        page.evaluate("r => document.getElementById('fn').setAttribute('data-ja-ref', r)", cv["ref"])
        check(outcome.upload_ref(box, fields) is None,
              "a stale ref another element also carries -> unknown")
        page.evaluate("() => document.getElementById('cv').remove()")
        check(outcome.upload_ref(box, fields) is None, "a box gone from the page -> unknown")
    check.done()


def test_watch_ambiguous_endings():
    check = Checks()
    pages = {"/acme/jobs/1": APP_HTML}
    with mock_page(HERE / "gate_form.html", _routes(pages)) as page:
        page.goto(APP_URL)
        rec, _ = _record(page)
        page.evaluate("window.__blockSubmit = true")                     # a validation error
        clicks = iter([lambda: page.click("#go")])
        polls = {"n": 0}

        def you():
            polls["n"] += 1
            step = next(clicks, None)
            if step:
                step()
            return polls["n"] >= 4
        w = outcome.watch(page, record=rec, stop_requested=you, poll_ms=50)
        check(w["ended"] == "enter" and w["detection"]["result"] != "match",
              "a blocked submit keeps watching; Enter ends it with no match")
        t = {"now": 0.0}

        def clock():
            t["now"] += 1000
            return t["now"]
        w = outcome.watch(page, record=rec, stop_requested=lambda: False, poll_ms=10,
                          timeout_s=1500, clock=clock)
        check(w["ended"] == "timeout", "nothing happens -> timeout")
        w = outcome.watch(page, record=rec, stop_requested=lambda: page.close() or False, poll_ms=10)
        check(w["ended"] == "tab_closed", "the tab closes -> tab_closed")
    check.done()


def test_fill_records_the_outcome():
    check = Checks()
    form = TEST_HOME / "gate_clean_outcome.html"
    form.write_text((HERE / "gate_clean.html").read_text(encoding="utf-8")
                    .replace("GENDER_OPTION", "Decline to self-identify"), encoding="utf-8")
    profile = PROFILE + """\
consent_allowlist:
  - I agree to receive SMS updates
"""
    text, records, error = run_fill(form, dry_run=False, answers=("y",), profile_yaml=profile)
    print(text[text.find("Shadow gate"):][:600])
    check(error is None, f"the run finished ({error})")
    check("I'm only watching this tab" in text and "won't click anything" in text,
          "it says it's only watching")
    row = [r for r in tracker.gate_runs() if r["application_url"] == form.resolve().as_uri()][-1]
    sig = json.loads(row["success_signal"])
    check(row["human_submitted"] == "y" and json.loads(row["edits_json"]) == []
          and row["capture"] == "polled", "your 'y' and zero edits stored against the verdict")
    check(sig["detected"] == "unsupported" and sig["answer"] == "y",
          "a file:// form has no signature; your answer decided")
    check(tracker.find(form.resolve().as_uri())["status"] == "submitted",
          "the application is marked submitted, as before")
    check("would_submit" == row["verdict"], "verdict and outcome sit on the same row")
    check.done()


if __name__ == "__main__":
    run_as_script(globals())
