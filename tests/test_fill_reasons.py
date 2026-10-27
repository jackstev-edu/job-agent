"""
test_fill_reasons.py — a failed fill carries a fixed reason category, never a value.

WHAT THIS DOES: Checks filler.reason_for on every fixed phrase fill_one
returns and exception_reason on Playwright-shaped messages; then, on a
mock form, makes four fills fail for four different reasons (a box made
read-only, a box removed from the page, a box under an overlay, a select
whose option isn't offered) and reads the categories off run_plan's rows,
the record's results and the redacted gate_runs field, with the plan's
values appearing in none of the detail or reason strings. The gate's own
"filling it failed (<reason>)" line is covered in test_gate.py.
WHY IT MATTERS: Jack's real Workday run reported "filling it failed" on
every text box with nothing to say why (2026-10-20); the category is what
the next adapter is built from, and it must never carry what was typed.

Run from anywhere:  .venv/Scripts/python.exe tests/test_fill_reasons.py
"""
import json

from _harness import TEST_HOME, Checks, mock_page, run_as_script
from agent import extract, filler, report as report_mod, shadow

FORM = TEST_HOME / "fill_reasons.html"
FORM.write_text("""<!doctype html><html><head><meta charset="utf-8"></head><body>
<form id="f" onsubmit="return false">
<label for="fn">First Name</label><input id="fn" name="firstName">
<label for="ln">Last Name</label><input id="ln" name="lastName">
<label for="city">City</label><input id="city" name="city">
<label for="st">State</label><select id="st" name="state"><option value="">Select</option>
<option>Ohio</option><option>Texas</option></select>
<label for="zip">Postal Code</label><input id="zip" name="postalCode">
<button type="submit">Submit</button></form>
<div id="overlay" style="display:none;position:fixed;background:rgba(0,0,0,.02);z-index:9"></div>
</body></html>""", encoding="utf-8")

SENTINELS = {"firstName": "SENTINEL-NAME-Q1", "lastName": "SENTINEL-LAST-Q2", "city": "SENTINEL-CITY-Q3",
             "state": "SENTINEL-STATE-Q4", "postalCode": "SENTINEL-ZIP-Q5"}


class _Err(Exception):
    pass


class TimeoutError(Exception):          # noqa: A001 — the name Playwright uses
    pass


def test_every_fixed_phrase_and_exception_shape_has_a_category():
    check = Checks()
    cases = {
        "skipped — no value": "skipped_no_value",
        "refused — this tool does not enter passwords": "password_refused",
        "refused — this box now reads like terms or consent; it's yours": "click_check_refused:terms",
        "refused — this box now reads as a different question; it's yours": "click_check_refused:question_changed",
        "refused — this box could submit the form; it's yours": "click_check_refused:submits",
        "refused — this dropdown could submit the form; it's yours": "click_check_refused:submits",
        "refused — the row for 'X' could submit the form; left empty for you": "click_check_refused:submits",
        "refused — that button is no longer inside its question": "click_check_refused:outside_group",
        "refused — 'I agree' could submit or agree to something": "click_check_refused:submits_or_agrees",
        "file uploads are handled separately": "file_separate",
        "the box didn't take the value; left for you": "not_taken",
        "can't keep keystrokes in this dropdown; left for you": "focus_lost",
        "the focus moved away while typing; left empty for you": "focus_lost",
        "the form's search found no match for 'X'": "no_option_match",
        "couldn't click the row for 'X'; left empty for you": "click_failed",
        "the dropdown didn't keep 'X'; left empty for you": "not_kept",
        "clicked 'X' but the form didn't register it": "not_registered",
        "'X' is not one of the radio options": "no_option_match",
        "'X' is not one of the options": "no_option_match",
        "'X' — no match — this form offers none of them": "no_option_match",
        "'X' — no exact match among the 3 row(s) the search offered; left blank": "no_option_match",
        "'X' — ambiguous — 2 options start with that: ['Xa', 'Xb']": "ambiguous_option",
        "obscured (TimeoutError)": "obscured", "not_visible (TimeoutError)": "not_visible",
        "not_editable (Error)": "not_editable", "detached (Error)": "detached",
        "timeout (TimeoutError)": "timeout", "no_option_match (Error)": "no_option_match",
        "exception:ValueError (ValueError)": "exception:ValueError",
        "something new the filler says": "unknown",
    }
    for detail, want in cases.items():
        got = filler.reason_for(False, detail)
        check(got == want, f"{detail[:50]!r} -> {want} ({got})")
    check(filler.reason_for(True, "typed") is None and filler.reason_for(True, "selected") is None,
          "a clean fill has no reason")
    check(filler.reason_for(True, "typed, but the box shows something else" + filler.UNCONFIRMED) == "read_back_mismatch"
          and filler.reason_for(True, "typed, but the box couldn't be read back" + filler.UNCONFIRMED) == "read_back_unreadable",
          "an unconfirmed fill says which kind")
    shapes = {
        TimeoutError("Locator.click: Timeout 4000ms exceeded.\nCall log:\n  - <div id=\"overlay\"></div> intercepts pointer events"): "obscured",
        TimeoutError("Locator.click: Timeout 4000ms exceeded.\nCall log:\n  - element is not visible"): "not_visible",
        _Err("Locator.fill: Element is not an <input>, <textarea> or [contenteditable] element"): "not_editable",
        _Err("Locator.fill: Element is read-only"): "not_editable",
        _Err("Element is not attached to the DOM"): "detached",
        TimeoutError("Locator.click: Timeout 4000ms exceeded.\nCall log:\n  - waiting for locator('[data-ja-ref=\"g1-f1\"]')"): "detached",
        TimeoutError("Locator.click: Timeout 4000ms exceeded.\nCall log:\n  - waiting for locator('x')\n  - locator resolved to <input>\n  - element is not visible"): "not_visible",
        _Err("Locator.select_option: Error: did not find some options"): "no_option_match",
        TimeoutError("Locator.type: Timeout 25000ms exceeded."): "timeout",
        _Err("Timeout 3000ms exceeded"): "timeout",
        ValueError("something else entirely"): "exception:ValueError",
    }
    for exc, want in shapes.items():
        got = filler.exception_reason(exc)
        check(got == want, f"{str(exc)[:50]!r} -> {want} ({got})")
    check.done()


TYPE_FLIP = TEST_HOME / "fill_reasons_flip.html"
TYPE_FLIP.write_text("""<!doctype html><html><head><meta charset="utf-8"></head><body>
<form id="f" onsubmit="return false">
<label for="city">City</label><input id="city" name="city">
<button type="submit">Submit</button></form>
<script>
  window.__hits = {click: 0, key: 0};
  const c = document.getElementById('city');
  c.addEventListener('click', () => { window.__hits.click++; if (window.__flipOnClick) c.type = 'checkbox'; });
  c.addEventListener('keydown', () => { window.__hits.key++; });
</script></body></html>""", encoding="utf-8")


def test_a_box_that_turned_into_a_checkbox_takes_no_keystrokes():
    # Fold of 103a876's review: fill("") used to be the only check that the
    # scanned box still took text. A re-render that turns it into a checkbox
    # (React reuses the node) must not get the keystrokes: refused as
    # not_editable, the box left unticked. The click runs after the check,
    # so not even that reaches it. On cc17666 the box was clicked and typed
    # into, and reported as set (unconfirmed).
    check = Checks()
    with mock_page(TYPE_FLIP) as page:
        fields = extract.extract_fields(page)
        city = next(f for f in fields if f.get("name") == "city")
        page.evaluate("document.getElementById('city').type = 'checkbox'")
        ok, detail = filler.fill_one(page, city, "Boston")
        hits = page.evaluate("window.__hits")
        ticked = page.evaluate("document.getElementById('city').checked")
    check(ok is False and filler.reason_for(ok, detail) == "not_editable",
          f"refused as not_editable ({ok}, {detail!r})")
    check(hits == {"click": 0, "key": 0} and ticked is False,
          f"the box was neither clicked nor typed into ({hits}, ticked={ticked})")
    check.done()


def test_a_box_its_own_click_turns_into_a_checkbox_takes_no_keystrokes():
    # Fold of 75ee295's review: the check also runs after the click, so a
    # box whose click handler re-renders it into a checkbox gets no
    # keystroke. On 75ee295 it was typed into and reported as set.
    check = Checks()
    with mock_page(TYPE_FLIP) as page:
        fields = extract.extract_fields(page)
        city = next(f for f in fields if f.get("name") == "city")
        page.evaluate("window.__flipOnClick = true")
        ok, detail = filler.fill_one(page, city, "Boston")
        hits = page.evaluate("window.__hits")
    check(ok is False and filler.reason_for(ok, detail) == "not_editable",
          f"refused as not_editable ({ok}, {detail!r})")
    check(hits == {"click": 1, "key": 0}, f"clicked once, no keystroke ({hits})")
    check.done()


def test_four_failed_fills_on_a_page_carry_categories_and_no_values():
    check = Checks()
    with mock_page(FORM) as page:
        fields = extract.extract_fields(page)
        by_name = {f.get("name"): f for f in fields}
        check(set(SENTINELS) <= set(by_name), f"the five boxes were scanned ({sorted(by_name)})")
        # The failures are arranged after the scan, as a page would.
        page.evaluate("document.getElementById('fn').readOnly = true")
        page.evaluate("document.getElementById('ln').remove()")
        page.evaluate("""() => { const r = document.getElementById('city').getBoundingClientRect();
            const o = document.getElementById('overlay');
            o.style.left = r.left + 'px'; o.style.top = r.top + 'px'; o.style.width = r.width + 'px';
            o.style.height = r.height + 'px'; o.style.display = 'block'; }""")
        plan = {by_name[n]["ref"]: {"value": v, "source": "profile"} for n, v in SENTINELS.items()}
        rows = filler.run_plan(page, fields, plan)
        got = {r["label"]: (r["filled"], r.get("reason"), r["detail"]) for r in rows}
        check(got["First Name"][0] is False and got["First Name"][1] == "not_editable",
              f"a read-only box: not_editable ({got['First Name']})")
        check(got["Last Name"][0] is False and got["Last Name"][1] == "detached",
              f"a removed box: detached ({got['Last Name']})")
        check(got["City"][0] is False and got["City"][1] == "obscured",
              f"a box under an overlay: obscured ({got['City']})")
        check(got["State"][0] is False and got["State"][1] == "no_option_match",
              f"a select without the option: no_option_match ({got['State']})")
        check(got["Postal Code"][0] is True and got["Postal Code"][1] is None,
              f"the box that worked has no reason ({got['Postal Code']})")
        texts = " ".join(f"{r['detail']} {r.get('reason')}" for r in rows)
        check(not any(v in texts for v in SENTINELS.values()),
              f"no plan value in any detail or reason ({texts[:200]})")
        check(all(r.get("reason") is None or r["reason"].replace(":", "_").replace(".", "").isidentifier()
                  for r in rows), "every reason is a bare category")
        # The record and its redacted form carry the category, nothing more.
        step = shadow.step_entry(fields, {r: "personal_fact" for r in plan}, plan, [], rows, None)
        res = step["results"]
        check(res[by_name["city"]["ref"]]["reason"] == "obscured"
              and res[by_name["postalCode"]["ref"]]["reason"] is None, f"the step's results carry it ({res})")
        check(not any(v in json.dumps(res) for v in SENTINELS.values()), "and no value")
        record = {"record_version": 1, "url": "https://careers.example.invalid/j", "final_url": "", "portal": "generic",
                  "portal_method": "default", "detected_by": "default", "fields": step["fields"],
                  "categories": step["categories"], "plan": step["plan"], "flags": [], "results": res,
                  "checkboxes": [], "events": [], "steps": []}
        red = shadow.redacted(record, {"content_verdict": "would_stop", "content_reasons": [], "trace": []})
        reasons = {f["label"]: f.get("fill_reason") for f in red["fields"]}
        check(reasons.get("City") == "obscured" and reasons.get("Postal Code") is None,
              f"the redacted field carries fill_reason ({reasons})")
        check(not any(v in json.dumps(red) for v in SENTINELS.values()), "the redacted record holds no value")
        html = report_mod.build(url="https://careers.example.invalid/j", ats_name="generic", report_rows=rows,
                                flags=[]).read_text(encoding="utf-8")
        check("Could not be filled (obscured)" in html and "Could not be filled (not_editable)" in html,
              "the review sheet names the category")
    check.done()


if __name__ == "__main__":
    run_as_script(globals())
