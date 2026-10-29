"""
test_submit.py — the Submit step on its own: the control, the ticks, the gate, the evidence.

WHAT THIS DOES: On mock final pages, checks that find_submit marks only a
control that reads as Submit (never Next, never two different ones);
that tick_allowed ticks a would_tick box only when its label, re-read on
the element, passes the consent policy or the exact allowlist, and never a
marketing box, a relabelled box or one with fine print; that
submit_application presses nothing when the gate says would_stop, and
presses Submit exactly once when it says would_submit; and that the
confirmation keeps the matched sentence, the address and the title, and
nothing else of the page.
WHY IT MATTERS: submit.py is the only module that presses Submit, and only
after gate.decide. Every refusal here is a refusal to send an application.

Run from anywhere:  .venv/Scripts/python.exe tests/test_submit.py
"""
import json
from pathlib import Path

from _harness import TEST_HOME, Checks, mock_page, run_as_script
from agent import extract, gate, submit

CLICKS_JS = """<script>window.__clicks = [];
  document.addEventListener('click', e => { const b = e.target.closest('button, a, input[type=submit]');
    if (b) window.__clicks.push((b.innerText || b.value || '').trim()); }, true);</script>"""

FINAL = f"""<!doctype html><html><head><title>Review - Example Co</title></head><body>
<form id="f"><label for="fn">First Name</label><input id="fn" name="first_name" value="Test">
<input type="checkbox" id="tos" required><label for="tos">I agree to the Terms of Service and Privacy Policy</label>
<input type="checkbox" id="news"><label for="news">Email me job alerts</label>
<input type="checkbox" id="fine" aria-describedby="fp"><label for="fine">I agree to the terms</label>
<p id="fp">and to binding arbitration</p>
<button type="button">Back</button>
<button type="submit" id="go">Submit application</button></form>
<script>document.getElementById('f').addEventListener('submit', e => {{ e.preventDefault();
  document.body.innerHTML = '<h1>Thank you for applying, Test Person!</h1><p>Reference 12345.</p>'; }});</script>
{CLICKS_JS}</body></html>"""

TWO_SUBMITS = f"""<!doctype html><html><body><form>
<button type="submit">Submit application</button><button type="submit">Apply now</button>
</form>{CLICKS_JS}</body></html>"""
NEXT_ONLY = f"""<!doctype html><html><body><form><button type="submit">Continue</button></form>{CLICKS_JS}</body></html>"""


class _Profile:
    def __init__(self, allow=()):
        self._allow = list(allow)

    def consent_allowlist(self):
        return self._allow

    def canned_answers(self):
        return {}

    def autosubmit_portals(self):
        return []

    def daily_submit_cap(self):
        return None


def _write(name, html):
    p = TEST_HOME / name
    p.write_text(html, encoding="utf-8")
    return p


def test_find_submit_marks_only_a_submit_control():
    check = Checks()
    with mock_page(_write("final.html", FINAL)) as page:
        found = submit.find_submit(page)
        check(found.get("chosen") and found.get("word") == "Submit application",
              f"the one Submit control is marked ({found})")
    with mock_page(_write("two.html", TWO_SUBMITS)) as page:
        check(not submit.find_submit(page).get("chosen"), "two different submit words: nothing marked")
    with mock_page(_write("next.html", NEXT_ONLY)) as page:
        check(not submit.find_submit(page).get("chosen"), "Continue is not a Submit")
    check.done()


def _record(page):
    fields = extract.extract_fields(page)
    by = {f["label"]: f for f in fields}
    rec = {"url": "https://x.example/apply", "portal": "generic", "portal_method": "default",
           "fields": fields, "categories": {by["First Name"]["ref"]: "personal_fact"},
           "plan": {by["First Name"]["ref"]: {"value": "Test", "source": "model", "confidence": "high"}},
           "flags": [], "results": {by["First Name"]["ref"]: {"filled": True, "unconfirmed": False}},
           "snapshot": extract.snapshot(page, fields), "census": extract.checkbox_census(page),
           "frames": [], "events": [], "errors": [], "steps": 1, "wizard": True}
    return rec, by


def test_tick_allowed_ticks_only_what_the_policy_allows():
    check = Checks()
    with mock_page(_write("final2.html", FINAL)) as page:
        rec, by = _record(page)
        actions = {by["I agree to the Terms of Service and Privacy Policy"]["ref"]: "would_tick",
                   by["Email me job alerts"]["ref"]: "would_tick",
                   by["I agree to the terms"]["ref"]: "would_tick"}
        steps = []
        ticked = submit.tick_allowed(page, {"consent_actions": actions}, _Profile(), steps)
        states = dict(page.evaluate("[...document.querySelectorAll('input[type=checkbox]')].map(b => [b.id, b.checked])"))
        check(ticked == [by["I agree to the Terms of Service and Privacy Policy"]["ref"]]
              and states == {"tos": True, "news": False, "fine": False},
              f"only the terms box, even though the gate's actions named three ({states})")
        # A box relabelled since the scan is not ticked either.
        page.evaluate("document.querySelector('label[for=tos]').textContent = 'Email me job alerts'")
        page.uncheck("#tos")
        ticked = submit.tick_allowed(page, {"consent_actions": actions}, _Profile(), [])
        check(ticked == [] and not page.is_checked("#tos"), "a box relabelled into marketing is not ticked")
    check.done()


def test_submit_presses_nothing_on_would_stop_and_once_on_would_submit():
    check = Checks()
    with mock_page(_write("final3.html", FINAL)) as page:
        rec, by = _record(page)
        # As it stands the gate stops: the terms box is required and unticked,
        # and the newsletter / fine-print boxes aren't allowed to be ticked.
        rec["flags"] = [{"ref": by["I agree to the Terms of Service and Privacy Policy"]["ref"],
                         "category": "consent", "reason": "consent"}]
        rec["categories"][by["I agree to the Terms of Service and Privacy Policy"]["ref"]] = "consent"
        sub = submit.submit_application(page, rec, _Profile(), build_record=lambda: _record(page)[0],
                                        auto_submit=lambda: True)
        clicks = page.evaluate("window.__clicks")
        print(sub.status, sub.reason)
        check(sub.status != "submitted" and not sub.clicked and clicks == [],
              f"would_stop: nothing pressed ({sub.status}; {sub.reason})")
        # Now a record the gate accepts: the two unallowed boxes gone from the page.
        page.evaluate("for (const id of ['news', 'fine', 'fp']) document.getElementById(id).remove();"
                      "document.querySelector('label[for=news]').remove();"
                      "document.querySelector('label[for=fine]').remove()")
        rec, by = _record(page)
        tos = by["I agree to the Terms of Service and Privacy Policy"]["ref"]
        rec["flags"] = [{"ref": tos, "category": "consent", "reason": "consent"}]
        rec["categories"][tos] = "consent"
        verdict = gate.decide(rec, _Profile())
        check(verdict["content_verdict"] == "would_submit" and verdict["consent_actions"].get(tos) == "would_tick",
              f"the gate plans the terms tick and says would_submit ({verdict['content_reasons']})")

        def rebuild():
            # A rebuild rescans, so the refs are new: find the box by label again.
            r, by2 = _record(page)
            t = by2["I agree to the Terms of Service and Privacy Policy"]["ref"]
            r["flags"], r["categories"][t] = [{"ref": t, "category": "consent", "reason": "consent"}], "consent"
            return r
        sub = submit.submit_application(page, rec, _Profile(), build_record=rebuild, auto_submit=lambda: True)
        clicks = page.evaluate("window.__clicks")
        check(sub.status == "submitted" and sub.clicked and clicks == ["Submit application"],
              f"would_submit: the terms box ticked, Submit pressed once ({sub.status}, {sub.reason}, {clicks})")
        check("Thank you for applying" in sub.evidence.get("snippet", "")
              and "12345" not in json.dumps(sub.evidence) and sub.evidence.get("title") == "Review - Example Co",
              f"evidence: the matched sentence, the title, not the rest of the page ({sub.evidence})")
    check.done()


if __name__ == "__main__":
    run_as_script(globals())
