"""
test_filler_clicks.py — every click filling makes is checked at click time.

WHAT THIS DOES: Against forms where a click could send the application or
tick something new, filler must refuse: a dropdown row holding a submit
button, a dropdown sitting inside a typeless button, a text box that
became a submit button after the scan, a checkbox whose nearby label was
relabelled to terms or to a legal question after the scan. Each has a
positive control showing the same widget is still used when it's safe. A
widget that undoes the chosen row on Escape is reported empty, not filled.
WHY IT MATTERS: No code path clicks Submit or ticks a consent/terms box,
and the page can change between the scan and the click.

Run from anywhere:  .venv/Scripts/python.exe tests/test_filler_clicks.py
"""
from pathlib import Path

from _harness import TEST_HOME, Checks, mock_page, run_as_script
from agent import extract, filler

WATCH_JS = """
  window.__submits = 0; window.__enters = 0; window.__clicks = [];
  document.addEventListener('submit', e => { e.preventDefault(); window.__submits++; }, true);
  document.addEventListener('keydown', e => { if (e.key === 'Enter') window.__enters++; }, true);
  document.addEventListener('click', e => {
    const b = e.target.closest('button, [role=combobox], li');
    if (b) window.__clicks.push((b.getAttribute('aria-label') || b.innerText || '').trim());
  }, true);
"""


def _write(name: str, html: str) -> Path:
    path = TEST_HOME / name
    path.write_text(html, encoding="utf-8")
    return path


def _state(page):
    return (page.evaluate("window.__clicks"), page.evaluate("window.__submits"),
            page.evaluate("window.__enters"))


def _row_form(button_type: str) -> str:
    return f"""<!doctype html><html><body><form>
  <label for="co">Country</label>
  <input id="co" name="country" role="combobox" aria-autocomplete="list">
  <ul class="menu"><li class="option"><button {button_type}>Canada</button></li>
    <li class="option"><button {button_type}>Mexico</button></li></ul>
</form><script>{WATCH_JS}</script></body></html>"""


def test_a_row_that_could_submit_is_refused_and_left_empty():
    check = Checks()
    with mock_page(_write("rows_submit.html", _row_form(""))) as page:
        fields = extract.extract_fields(page, extract.ReadGuard())
        country = next(f for f in fields if f["label"] == "Country")
        country["options"] = ["Canada", "Mexico"]
        ok, detail = filler.fill_one(page, country, "Canada")
        clicks, submits, enters = _state(page)
        value = page.input_value("#co")
    print(f"  typeless rows -> {ok} {detail}; clicks {clicks}")
    check(not ok and "left empty for you" in detail and value == "",
          "a row holding a typeless (submit) button: refused, the box left empty")
    check("Canada" not in clicks and submits == 0 and enters == 0,
          "  ...the row was never clicked, nothing was sent, no Enter")
    with mock_page(_write("rows_button.html", _row_form('type="button"'))) as page:
        fields = extract.extract_fields(page, extract.ReadGuard())
        country = next(f for f in fields if f["label"] == "Country")
        country["options"] = ["Canada", "Mexico"]
        ok, detail = filler.fill_one(page, country, "Canada")
        clicks, submits, _ = _state(page)
    print(f"  type=button rows -> {ok} {detail}; clicks {clicks}")
    check(ok and "Canada" in clicks and submits == 0,
          "positive control: the same dropdown with type=button rows IS clicked and filled")
    check.done()


def _wrapped_form(button_type: str) -> str:
    return f"""<!doctype html><html><body><form>
  <button {button_type} id="wrap"><div role="combobox" aria-label="State" tabindex="0"
       style="display:inline-block;width:120px;height:20px">Pick</div></button>
</form><script>{WATCH_JS}</script></body></html>"""


def test_a_dropdown_inside_a_typeless_button_is_not_clicked():
    check = Checks()
    with mock_page(_write("wrapped_submit.html", _wrapped_form(""))) as page:
        fields = extract.extract_fields(page, extract.ReadGuard())
        state = next(f for f in fields if f["label"] == "State")
        check(state.get("submits") is True, "the scan marks it as submitting (the button around it)")
        ok, detail = filler.fill_one(page, state, "Ohio")
        clicks, submits, _ = _state(page)
    print(f"  inside a typeless button -> {ok} {detail}; clicks {clicks}")
    check(not ok and "could submit" in detail and clicks == [] and submits == 0,
          "filling refuses it at click time: never clicked, nothing sent")
    with mock_page(_write("wrapped_button.html", _wrapped_form('type="button"'))) as page:
        fields = extract.extract_fields(page, extract.ReadGuard())
        state = next(f for f in fields if f["label"] == "State")
        filler.fill_one(page, state, "Ohio")
        clicks, submits, _ = _state(page)
    check("State" in clicks and submits == 0,
          f"positive control: inside a type=button button, it IS clicked ({clicks})")
    check.done()


TEXT_BOX = f"""<!doctype html><html><body><form>
  <label for="ci">City</label><input id="ci" name="city">
  <label for="zp">Zip</label><input id="zp" name="zip">
</form><script>{WATCH_JS}</script></body></html>"""


def test_a_text_box_that_became_a_submit_button_is_not_clicked():
    check = Checks()
    with mock_page(_write("text_submit.html", TEXT_BOX)) as page:
        fields = extract.extract_fields(page, extract.ReadGuard())
        city = next(f for f in fields if f["label"] == "City")
        zp = next(f for f in fields if f["label"] == "Zip")
        page.evaluate("document.getElementById('ci').type = 'submit'")
        ok, detail = filler.fill_one(page, city, "Springfield")
        _, submits, _ = _state(page)
        check(not ok and "could submit" in detail and submits == 0,
              f"a text box turned into a submit input after the scan: refused ({detail})")
        ok, _ = filler.fill_one(page, zp, "00000")
        check(ok and page.input_value("#zp") == "00000",
              "positive control: an ordinary text box is still typed into")
    check.done()


CLIMB = """<!doctype html><html><body><form>
  <div><div class="label"><span id="t1">Email me news</span></div>
       <input type="checkbox" name="news" id="news"></div>
  <div><div class="label"><span id="t2">Text me updates</span></div>
       <input type="checkbox" name="updates" id="updates"></div>
  <div><div class="label"><span id="t3">Send me offers</span></div>
       <input type="checkbox" name="offers" id="offers"></div>
</form></body></html>"""


def test_a_climb_up_label_relabelled_after_the_scan_is_not_ticked():
    check = Checks()
    with mock_page(_write("climb.html", CLIMB)) as page:
        fields = extract.extract_fields(page, extract.ReadGuard())
        by = {f["label"]: f for f in fields}
        check(set(by) >= {"Email me news", "Text me updates", "Send me offers"},
              "the scan labels each box from the nearby .label (the climb-up fallback)")
        page.evaluate("document.getElementById('t1').textContent = "
                      "'I agree to the Terms and Conditions'")
        page.evaluate("document.getElementById('t2').textContent = 'I am a protected veteran'")
        ok1, d1 = filler.fill_one(page, by["Email me news"], True)
        ok2, d2 = filler.fill_one(page, by["Text me updates"], True)
        ok3, _ = filler.fill_one(page, by["Send me offers"], True)
        state = {i: page.is_checked(f"#{i}") for i in ("news", "updates", "offers")}
    print(f"  {d1} / {d2} / {state}")
    check(not ok1 and "terms or consent" in d1 and not state["news"],
          "relabelled to terms after the scan: refused, not ticked")
    check(not ok2 and "different question" in d2 and not state["updates"],
          "relabelled to a legal question the scan didn't see: refused, not ticked")
    check(ok3 and state["offers"], "positive control: an unchanged box is still ticked")
    check.done()


# The chosen row is only pending: Escape puts the box back to empty.
ESCAPE_UNDO = f"""<!doctype html><html><body><form>
  <label for="co">Country</label>
  <input id="co" name="country" role="combobox" aria-autocomplete="list">
  <ul class="menu"><li class="option">Canada</li><li class="option">Mexico</li></ul>
</form><script>{WATCH_JS}
  const box = document.getElementById('co');
  document.querySelectorAll('.option').forEach(li =>
    li.addEventListener('mousedown', () => {{ box.value = li.textContent; }}));
  document.addEventListener('keydown', e => {{ if (e.key === 'Escape') box.value = ''; }});
</script></body></html>"""


def test_a_widget_that_undoes_the_choice_on_escape_is_reported_empty():
    check = Checks()
    with mock_page(_write("escape_undo.html", ESCAPE_UNDO)) as page:
        fields = extract.extract_fields(page, extract.ReadGuard())
        country = next(f for f in fields if f["label"] == "Country")
        country["options"] = ["Canada", "Mexico"]
        ok, detail = filler.fill_one(page, country, "Canada")
        value = page.input_value("#co")
        _, submits, enters = _state(page)
    print(f"  -> {ok} {detail} {value!r}")
    check(not ok and "didn't keep" in detail and value == "",
          "the choice was undone by Escape: reported not filled, the box empty")
    check(submits == 0 and enters == 0, "  ...no Enter, nothing sent")
    check.done()


if __name__ == "__main__":
    run_as_script(globals())
