"""
test_no_step_clicks.py — the agent never clicks Next, Continue, Apply or a submit-capable dropdown.

WHAT THIS DOES: A real `fill` on a page with Next and Continue buttons must
fill the page, click neither, send nothing, log a multi_step event and say
you carry on yourself. The gate logs would_stop for it, and the dry run's
call stops too. The same holds when the Next is a link or a role=button,
when it appears only after filling, when it sits behind more hidden matches
than the check looks through, and when the check itself errors: anything
unsettled counts as a next page. prepare() clicks nothing at all, Apply
included; fill asks you to click it. A dropdown that is a typeless <button>
inside a form (so a submit button) must never be clicked: not to read its
options, not to fill it. It comes to you instead.
WHY IT MATTERS: No code path clicks Submit, Next, Continue, Sign in, Create
account or SSO. You move between pages yourself.

Run from anywhere:  .venv/Scripts/python.exe tests/test_no_step_clicks.py
"""
import json
from pathlib import Path

from _harness import TEST_HOME, Checks, mock_page, run_as_script
from agent import adapters, extract, tracker

CLICKS_JS = """
  window.__clicks = []; window.__submits = 0;
  document.addEventListener('click', e => {
    const b = e.target.closest('button, a, [role=button]');
    if (b) window.__clicks.push((b.innerText || '').trim());
  }, true);
  document.addEventListener('submit', e => { e.preventDefault(); window.__submits++; }, true);
"""

STEP_FORM = f"""<!doctype html><html><body><h1>Step 1 of 3</h1><form>
  <label for="fn">First Name</label><input id="fn" name="first_name">
  <label for="em">Email</label><input id="em" name="email" type="email">
  <button type="button">Back</button><button type="button">Next</button>
  <button type="submit">Continue</button>
</form><script>{CLICKS_JS}</script></body></html>"""

ONE_PAGE = f"""<!doctype html><html><body><form>
  <label for="fn">First Name</label><input id="fn" name="first_name">
</form><script>{CLICKS_JS}</script></body></html>"""


def _write(name: str, html: str) -> Path:
    path = TEST_HOME / name
    path.write_text(html, encoding="utf-8")
    return path


def _fill(form: Path, *, dry_run: bool, patches=()):
    """A real fill; returns (printed, records, error, what the page showed at each prompt)."""
    import apply
    from _fill_run import run_fill

    pages, at_prompt = [], []
    real_open = apply.browser_mod.open_page

    def open_page(ctx, url):
        page = real_open(ctx, url)
        pages.append(page)
        return page

    def you(*a):
        at_prompt.append((pages[-1].evaluate("window.__clicks"),
                          pages[-1].evaluate("window.__submits")))
        return "n"
    text, records, error = run_fill(form, dry_run=dry_run,
                                    patches=((apply.browser_mod, "open_page", open_page),
                                             (apply, "input", you), *patches))
    return text, records, error, at_prompt


def _untouched(at_prompt) -> bool:
    """At every prompt, nothing had been clicked and nothing sent."""
    return bool(at_prompt) and all(p == ([], 0) for p in at_prompt)


def _gate_row(form: Path) -> tuple[str, list]:
    row = [r for r in tracker.gate_runs() if r["application_url"] == form.resolve().as_uri()][-1]
    return row["verdict"], json.loads(row["reasons_json"])


MULTI = "run: multi_step (a Next or Continue control)"


def test_a_page_with_next_and_continue_is_filled_and_left_there():
    check = Checks()
    form = _write("steps_next.html", STEP_FORM)
    text, records, error, at_prompt = _fill(form, dry_run=False)
    print(text[-900:])
    check(error is None, f"the run finished ({error})")
    check(_untouched(at_prompt), f"nothing was clicked and nothing sent ({at_prompt})")
    rec = records[-1] if records else {}
    check({"kind": "multi_step", "detail": "a Next or Continue control"} in rec.get("events", []),
          "a multi_step event is in the gate's record")
    check(rec.get("steps") == 1 and not any(e.get("kind") == "advanced"
                                            for e in rec.get("events", [])),
          "one page, and no 'advanced' event")
    check("continues on another page" in text and "carry on in the browser yourself" in text,
          "it says you carry on yourself")
    check(rec.get("plan") and all(v.get("value") for v in rec["plan"].values()),
          "the page itself was still filled")
    verdict, reasons = _gate_row(form)
    check(verdict == "would_stop" and MULTI in reasons,
          f"the gate logged would_stop with multi_step as a reason ({verdict})")
    check(reasons.count(MULTI) == 1, "  ...once, though it's looked for before and after filling")
    check.done()


def test_the_dry_run_stops_on_it_too_and_a_single_page_does_not():
    check = Checks()
    text, records, error, _ = _fill(_write("steps_next_dry.html", STEP_FORM), dry_run=True)
    check(error is None and "would_stop" in text and "run: multi_step" in text,
          "the dry run's gate call is would_stop, with multi_step")
    text, records, error, at_prompt = _fill(_write("steps_one.html", ONE_PAGE), dry_run=False)
    check(error is None and records and not any(
        e.get("kind") == "multi_step" for e in records[-1].get("events", [])),
          "a page with no Next or Continue logs no multi_step event")
    check(_untouched(at_prompt), "  ...and nothing was clicked there either")
    check.done()


def _stops_on_multi_step(check, name: str, html: str, patches=()) -> None:
    form = _write(name, html)
    text, records, error, at_prompt = _fill(form, dry_run=False, patches=patches)
    check(error is None, f"{name}: the run finished ({error})")
    check(_untouched(at_prompt), f"{name}: nothing was clicked and nothing sent")
    check(records and {"kind": "multi_step", "detail": "a Next or Continue control"}
          in records[-1].get("events", []), f"{name}: a multi_step event is logged")
    verdict, reasons = _gate_row(form)
    check(verdict == "would_stop" and MULTI in reasons, f"{name}: the gate logged would_stop")


def test_a_next_link_or_role_button_counts():
    check = Checks()
    for name, control in (("steps_a.html", '<a href="#step2">Next</a>'),
                          ("steps_role.html", '<div role="button" tabindex="0">Continue</div>'),
                          ("steps_input.html", '<input type="button" value="Next step">')):
        _stops_on_multi_step(check, name, ONE_PAGE.replace("</form>", control + "</form>"))
    check.done()


def test_a_next_that_appears_only_after_filling_counts():
    check = Checks()
    late = ONE_PAGE.replace("</script>", """
      document.getElementById('fn').addEventListener('input', () => {
        if (!document.getElementById('nx')) document.querySelector('form').insertAdjacentHTML(
          'beforeend', '<button type="button" id="nx">Next</button>');
      });</script>""")
    _stops_on_multi_step(check, "steps_late.html", late)
    check.done()


def test_more_matches_than_it_looks_through_counts():
    check = Checks()
    hidden = '<button type="button" style="display:none">Next</button>' * (
        adapters.NEXT_SCAN_LIMIT + 10)
    _stops_on_multi_step(check, "steps_many.html", ONE_PAGE.replace(
        "</form>", hidden + '<button type="button">Next</button></form>'))
    with mock_page(_write("steps_many_hidden.html",
                          ONE_PAGE.replace("</form>", hidden + "</form>"))) as page:
        check(adapters.has_next_step(page, "generic") is True,
              "only hidden matches, but more than the limit: still a yes, never a truncated no")
    check.done()


def test_an_error_in_the_check_counts():
    from playwright.sync_api import Locator
    check = Checks()
    real_all, raised = Locator.all, []

    def all_(self):
        if "has-text('Next')" in repr(self):
            raised.append(1)
            raise RuntimeError("the page went away")
        return real_all(self)
    _stops_on_multi_step(check, "steps_error.html", ONE_PAGE,
                         patches=((Locator, "all", all_),))
    check(raised, "the check really raised (so the checks above mean something)")
    check.done()


APPLY_PAGE = f"""<!doctype html><html><body><h1>Engineer</h1>
  <button id="li">Apply with LinkedIn</button>
  <a href="#" id="sso">Apply with Google</a>
  <form><button id="ap">Apply</button></form>
  <a href="#" id="apply_button">Apply for this job</a>
  <button data-automation-id="adventureButton">Apply</button>
  <button>Apply Manually</button>
<script>{CLICKS_JS}</script></body></html>"""


def test_prepare_clicks_nothing():
    check = Checks()
    with mock_page(_write("steps_apply.html", APPLY_PAGE)) as page:
        for key in ("greenhouse", "ashby", "smartrecruiters", "workday", "lever", "generic"):
            adapters.prepare(page, key)
        clicks, submits = page.evaluate("window.__clicks"), page.evaluate("window.__submits")
    check(clicks == [] and submits == 0,
          f"prepare() pressed nothing on any portal, Apply included ({clicks}, {submits})")
    text, _, error, at_prompt = _fill(_write("steps_apply_fill.html", APPLY_PAGE.replace(
        "</form>", '<label for="fn">First Name</label><input id="fn" name="first_name"></form>')),
        dry_run=True)
    check(error is None and "Click Apply if the form isn't open, then press Enter." in text,
          "fill asks you to click Apply")
    check(_untouched(at_prompt), f"  ...and clicks nothing itself ({at_prompt})")
    check.done()


DROPDOWN_FORM = f"""<!doctype html><html><body><form>
  <label for="fn">First Name</label><input id="fn" name="first_name">
  <span id="cl">Country</span>
  <button role="combobox" aria-labelledby="cl" style="width:120px;height:24px">Choose</button>
  <span id="sl">State</span>
  <button type="button" role="combobox" aria-labelledby="sl"
          style="width:120px;height:24px">Pick</button>
</form><script>{CLICKS_JS}</script></body></html>"""


def test_a_dropdown_that_could_submit_is_never_clicked():
    check = Checks()
    with mock_page(_write("steps_dropdown_scan.html", DROPDOWN_FORM)) as page:
        fields = extract.enrich_comboboxes(page, extract.extract_fields(page, extract.ReadGuard()))
        clicks = page.evaluate("window.__clicks")
        submits = page.evaluate("window.__submits")
    by = {f["label"]: f for f in fields}
    check(by["Country"].get("submits") is True and not by["State"].get("submits"),
          "the typeless button in the form is marked as submitting; the type=button one isn't")
    check(clicks == ["Pick"] and submits == 0,
          f"reading options opened only the safe one, and nothing was sent ({clicks}, {submits})")

    text, records, error, at_prompt = _fill(_write("steps_dropdown.html", DROPDOWN_FORM),
                                            dry_run=False)
    check(error is None, f"the run finished ({error})")
    check(at_prompt and all("Choose" not in c and n == 0 for c, n in at_prompt),
          f"the full fill never clicked it and sent nothing ({at_prompt})")
    check("This dropdown is a button that could submit the form" in text,
          "it was handed to you, with the reason")
    check.done()


if __name__ == "__main__":
    run_as_script(globals())
