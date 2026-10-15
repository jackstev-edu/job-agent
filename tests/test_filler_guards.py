"""
test_filler_guards.py — filling never presses Enter, and re-checks what it clicks or ticks.

WHAT THIS DOES: On a form that submits on Enter (a submit button, nothing
catching the key), a custom dropdown is filled with a matching value, a
value with no matching row, and a text answer with a line break in it. No
Enter may be pressed and the form must never submit. A dropdown that turns
into a submit button after the scan must not be clicked, and a checkbox
relabelled into terms after the scan must not be ticked.
WHY IT MATTERS: No code path clicks Submit or accepts terms. Enter in a
form's text box is a submit, and the page can change between the scan and
the click.

Run from anywhere:  .venv/Scripts/python.exe tests/test_filler_guards.py
"""
from pathlib import Path

from _harness import TEST_HOME, Checks, mock_page, run_as_script
from agent import extract, filler

WATCH_JS = """
  window.__submits = 0; window.__enters = 0; window.__clicks = [];
  document.addEventListener('submit', e => { e.preventDefault(); window.__submits++; }, true);
  document.addEventListener('keydown', e => { if (e.key === 'Enter') window.__enters++; }, true);
  document.addEventListener('click', e => {
    const b = e.target.closest('button'); if (b) window.__clicks.push(b.innerText.trim());
  }, true);
"""

# The menu stays on screen after a row is clicked, the case that used to be
# "committed" with Enter.
ENTER_FORM = f"""<!doctype html><html><body><form id="f">
  <label for="co">Country</label>
  <input id="co" name="country" role="combobox" aria-autocomplete="list">
  <ul class="menu"><li class="option">Canada</li><li class="option">Mexico</li></ul>
  <label for="ci">City</label><input id="ci" name="city">
  <button type="submit">Send</button>
</form><script>{WATCH_JS}</script></body></html>"""


def _write(name: str, html: str) -> Path:
    path = TEST_HOME / name
    path.write_text(html, encoding="utf-8")
    return path


def _by_label(fields, label):
    return next(f for f in fields if f.get("label") == label)


def test_an_enter_sensitive_form_never_submits_during_a_fill():
    check = Checks()
    with mock_page(_write("guards_enter.html", ENTER_FORM)) as page:
        fields = extract.enrich_comboboxes(page, extract.extract_fields(page, extract.ReadGuard()))
        country, city = _by_label(fields, "Country"), _by_label(fields, "City")
        check(country["type"] == "combobox" and country.get("options") == ["Canada", "Mexico"],
              "the dropdown was scanned with its two rows")
        page.focus("#ci")
        page.keyboard.press("Enter")
        check(page.evaluate("window.__submits") == 1,
              "Enter in this form really submits it (so the checks below mean something)")
        page.evaluate("window.__submits = 0; window.__enters = 0")

        ok, detail = filler.fill_one(page, country, "Canada")
        print(f"  Canada -> {ok} {detail}")
        check(ok and page.evaluate("window.__enters") == 0 and page.evaluate("window.__submits") == 0,
              "a matching row: clicked, the menu left up is closed, no Enter, nothing sent")

        ok, detail = filler.fill_one(page, country, "Atlantis")
        print(f"  Atlantis -> {ok} {detail}")
        check(not ok and page.input_value("#co") == ""
              and page.evaluate("window.__enters") == 0 and page.evaluate("window.__submits") == 0,
              "no matching row: left empty and reported, no Enter, nothing sent")

        ok, detail = filler.fill_one(page, city, "Spring\nfield\r\n")
        print(f"  city -> {ok} {detail} {page.input_value('#ci')!r}")
        check(ok and page.input_value("#ci").strip() == "Spring field"
              and page.evaluate("window.__enters") == 0 and page.evaluate("window.__submits") == 0,
              "a line break in a text answer is typed as a space, never as Enter")
    check.done()


SUBMIT_LATER = f"""<!doctype html><html><body><form>
  <span id="sl">State</span>
  <button type="button" role="combobox" aria-labelledby="sl" id="st"
          style="width:120px;height:24px">Pick</button>
</form><script>{WATCH_JS}</script></body></html>"""


def test_a_dropdown_that_becomes_a_submit_after_the_scan_is_not_clicked():
    check = Checks()
    with mock_page(_write("guards_submit_later.html", SUBMIT_LATER)) as page:
        fields = extract.extract_fields(page, extract.ReadGuard())
        state = _by_label(fields, "State")
        check(state["type"] == "combobox" and not state.get("submits"),
              "at the scan it's a type=button dropdown")
        page.evaluate("document.getElementById('st').removeAttribute('type')")
        ok, detail = filler.fill_one(page, state, "Ohio")
        print(f"  -> {ok} {detail}")
        check(not ok and "could submit" in detail, "once typeless, the fill refuses it")
        check(page.evaluate("window.__clicks") == [] and page.evaluate("window.__submits") == 0,
              "  ...it was never clicked, and nothing was sent")
    check.done()


RELABEL = """<!doctype html><html><body><form>
  <label><input type="checkbox" name="updates" id="up"> <span id="t">Text me updates</span></label>
  <label><input type="checkbox" name="news" id="nw"> Email me news</label>
</form></body></html>"""


def test_a_checkbox_relabelled_into_terms_after_the_scan_is_not_ticked():
    check = Checks()
    with mock_page(_write("guards_relabel.html", RELABEL)) as page:
        fields = extract.extract_fields(page, extract.ReadGuard())
        updates, news = _by_label(fields, "Text me updates"), _by_label(fields, "Email me news")
        ok, _ = filler.fill_one(page, news, True)
        check(ok and page.is_checked("#nw"), "an ordinary box is still ticked (so the next check means something)")
        page.evaluate("document.getElementById('t').textContent = "
                      "'I agree to the Terms and Conditions'")
        ok, detail = filler.fill_one(page, updates, True)
        print(f"  -> {ok} {detail}")
        check(not ok and "terms or consent" in detail and not page.is_checked("#up"),
              "relabelled to terms after the scan: refused and left unticked")
    check.done()


if __name__ == "__main__":
    run_as_script(globals())
