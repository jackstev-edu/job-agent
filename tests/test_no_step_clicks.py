"""
test_no_step_clicks.py — the agent never clicks Next, Continue, an SSO Apply or a submit-capable dropdown.

WHAT THIS DOES: A real `fill` on a page with Next and Continue buttons must
fill the page, click neither, send nothing, log a multi_step event and say
you carry on yourself. The gate logs would_stop for it, and the dry run's
call stops too. prepare() must press only a plainly labelled Apply, never
"Apply with LinkedIn" ahead of it. A dropdown that is a typeless <button>
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


def _fill(form: Path, *, dry_run: bool):
    """A real fill; returns (printed, records, error, what the page saw at the last prompt)."""
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
                                             (apply, "input", you)))
    return text, records, error, at_prompt


def test_a_page_with_next_and_continue_is_filled_and_left_there():
    check = Checks()
    form = _write("steps_next.html", STEP_FORM)
    text, records, error, at_prompt = _fill(form, dry_run=False)
    print(text[-900:])
    check(error is None, f"the run finished ({error})")
    check(at_prompt == [([], 0)], f"nothing was clicked and nothing sent ({at_prompt})")
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
    row = [r for r in tracker.gate_runs() if r["application_url"] == form.resolve().as_uri()][-1]
    reasons = json.loads(row["reasons_json"])
    check(row["verdict"] == "would_stop" and "run: multi_step (a Next or Continue control)" in reasons,
          f"the gate logged would_stop with multi_step as a reason ({row['verdict']})")
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
    check(at_prompt == [([], 0)], "  ...and nothing was clicked there either")
    check.done()


APPLY_PAGE = f"""<!doctype html><html><body><h1>Engineer</h1>
  <button id="li">Apply with LinkedIn</button>
  <a href="#" id="sso">Apply with Google</a>
  <button id="ap">Apply</button>
<script>{CLICKS_JS}</script></body></html>"""


def test_prepare_presses_only_a_plain_apply():
    check = Checks()
    with mock_page(_write("steps_apply.html", APPLY_PAGE)) as page:
        adapters.prepare(page, "greenhouse")
        clicks = page.evaluate("window.__clicks")
    check(clicks == ["Apply"], f"only the plain Apply was pressed, not LinkedIn or Google ({clicks})")
    with mock_page(_write("steps_apply_none.html", APPLY_PAGE.replace(
            '<button id="ap">Apply</button>', ""))) as page:
        adapters.prepare(page, "greenhouse")
        clicks = page.evaluate("window.__clicks")
    check(clicks == [], f"with only Easy Apply and SSO buttons, nothing is pressed ({clicks})")
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
    check(at_prompt and "Choose" not in at_prompt[0][0] and at_prompt[0][1] == 0,
          f"the full fill never clicked it and sent nothing ({at_prompt})")
    check("This dropdown is a button that could submit the form" in text,
          "it was handed to you, with the reason")
    check.done()


if __name__ == "__main__":
    run_as_script(globals())
