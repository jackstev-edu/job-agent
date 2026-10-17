"""
test_shows_choice.py — a dropdown pick counts only when the page shows it.

WHAT THIS DOES: Picks a row in five kinds of dropdown and checks what
filling reports. Greenhouse's react-select (the choice lands in a
single-value display, the input empties) and a widget that commits to its
own display are kept; a widget with a display that stays empty is
abandoned; a button combobox showing the choice as its text is kept; a
plain input with no separate display is accepted but "unconfirmed", and
the gate stops on it. Also: a flex label whose line break split "I accept
the" from a "Terms" tag still stops (the collapsed-whitespace reading).
WHY IT MATTERS: The agent typed the search text itself, so the box showing
that text proves nothing. A fill nobody can confirm is never treated as
done.

Run from anywhere:  .venv/Scripts/python.exe tests/test_shows_choice.py
"""
import json
from pathlib import Path

from _harness import TEST_HOME, Checks, mock_page, run_as_script
from agent import extract, filler, sensitive

HERE = Path(__file__).parent

# What react-select does when a row is picked, added to the Greenhouse mock:
# a menu on focus, and on mousedown the choice goes into a single-value div
# while the input empties.
REACT_SELECT_JS = """() => {
  const input = document.getElementById('question_1');
  const box = input.closest('.select__value-container');
  const menu = document.createElement('div');
  menu.className = 'select__menu';
  menu.innerHTML = '<div class="select__option">Yes</div><div class="select__option">No</div>';
  menu.style.display = 'none';
  input.closest('.select__control').after(menu);
  input.addEventListener('focus', () => { menu.style.display = 'block'; });
  menu.querySelectorAll('.select__option').forEach(o => o.addEventListener('mousedown', () => {
    box.querySelector('.select__placeholder')?.remove();
    box.querySelector('.select__single-value')?.remove();
    box.insertAdjacentHTML('afterbegin', '<div class="select__single-value">' + o.textContent + '</div>');
    input.value = '';
    menu.style.display = 'none';
  }));
}"""


def _write(name: str, html: str) -> Path:
    path = TEST_HOME / name
    path.write_text(html, encoding="utf-8")
    return path


def _pick(page, label, value):
    fields = extract.extract_fields(page, extract.ReadGuard())
    f = next(f for f in fields if f["label"].startswith(label))
    return filler.fill_one(page, f, value)


def test_greenhouse_react_select_pick_is_kept():
    check = Checks()
    with mock_page(HERE / "greenhouse_select.html") as page:
        page.evaluate(REACT_SELECT_JS)
        ok, detail = _pick(page, "Are you legally authorized", "Yes")
        shown = page.evaluate("() => document.querySelector('.select__single-value')?.textContent")
    print(f"  -> {ok} {detail} shown={shown!r}")
    check(ok and "unconfirmed" not in detail and shown == "Yes",
          "the pick shows in the single-value display: kept and reported filled")
    check.done()


def _display_widget(commits: bool) -> str:
    commit = "display.textContent = o.textContent;" if commits else ""
    return f"""<!doctype html><html><body><form>
  <label for="ci">City</label>
  <div class="city-control"><span class="city-singleValue" id="disp"></span>
    <input id="ci" name="city" role="combobox" aria-autocomplete="list"></div>
  <ul class="menu"><li class="option">Paris</li><li class="option">Rome</li></ul>
</form><script>
  const display = document.getElementById('disp');
  document.querySelectorAll('.option').forEach(o => o.addEventListener('mousedown', () => {{
    {commit}
  }}));
</script></body></html>"""


def test_a_committed_display_decides():
    check = Checks()
    with mock_page(_write("display_commits.html", _display_widget(True))) as page:
        ok, detail = _pick(page, "City", "Paris")
    check(ok and "unconfirmed" not in detail, f"the widget commits to its display: kept ({detail})")
    with mock_page(_write("display_empty.html", _display_widget(False))) as page:
        ok, detail = _pick(page, "City", "Paris")
        value = page.input_value("#ci")
    check(not ok and "didn't keep" in detail and value == "",
          f"its display stays empty, though the box holds the typed text: abandoned ({detail})")
    check.done()


BUTTON_COMBO = """<!doctype html><html><body><form>
  <span id="sl">State</span>
  <button type="button" role="combobox" aria-labelledby="sl" id="st"
          style="width:120px;height:24px">Choose</button>
  <ul class="menu" id="m" style="display:none"><li class="option">Ohio</li><li class="option">Utah</li></ul>
</form><script>
  const b = document.getElementById('st'), m = document.getElementById('m');
  b.addEventListener('click', () => { m.style.display = 'block'; });
  m.querySelectorAll('.option').forEach(o => o.addEventListener('mousedown', () => {
    b.textContent = o.textContent; m.style.display = 'none';
  }));
</script></body></html>"""


def test_a_button_combobox_shows_its_choice_as_text():
    check = Checks()
    with mock_page(_write("button_combo.html", BUTTON_COMBO)) as page:
        ok, detail = _pick(page, "State", "Ohio")
    check(ok and "unconfirmed" not in detail, f"the button reads 'Ohio': kept ({detail})")
    check.done()


NO_DISPLAY = """<!doctype html><html><body><form>
  <label for="co">Country</label>
  <input id="co" name="country" role="combobox" aria-autocomplete="list">
  <ul class="menu"><li class="option">Canada</li><li class="option">Mexico</li></ul>
</form></body></html>"""


def test_a_no_display_input_is_unconfirmed_and_the_gate_stops():
    from _fill_run import map_from_profile, run_fill

    check = Checks()
    with mock_page(_write("no_display.html", NO_DISPLAY)) as page:
        ok, detail = _pick(page, "Country", "Canada")
    check(ok and "unconfirmed" in detail, f"accepted, but marked unconfirmed ({detail})")

    def map_fields(fields, profile, page_text=""):
        out = map_from_profile(fields, profile, page_text)
        for f in fields:
            if f.get("label") == "Country":
                out[f["ref"]] = filler_decision(f)
        return out
    form = _write("no_display_fill.html", NO_DISPLAY)
    text, records, error = run_fill(form, dry_run=False, map_fields=map_fields)
    from agent import tracker
    row = [r for r in tracker.gate_runs() if r["application_url"] == form.resolve().as_uri()][-1]
    reasons = json.loads(row["reasons_json"])
    print(f"  {row['verdict']} {reasons}")
    check(error is None and row["verdict"] == "would_stop"
          and any("unconfirmed" in r for r in reasons),
          "the gate stops on the unconfirmed fill")
    check.done()


PART_DISPLAY = """<!doctype html><html><body><form>
  <label for="co">Country code</label>
  <div class="co-control"><span class="co-singleValue" id="disp"></span>
    <input id="co" name="country_code" role="combobox" aria-autocomplete="list"></div>
  <ul class="menu"><li class="option">United States +1</li><li class="option">Canada +1</li></ul>
</form><script>
  const display = document.getElementById('disp');
  document.querySelectorAll('.option').forEach(o => o.addEventListener('mousedown', () => {
    // The widget shows only the dialling code it kept.
    display.textContent = o.textContent.replace(/^.* /, ''); }));
</script></body></html>"""


def test_a_display_holding_part_of_the_choice_is_unconfirmed_and_the_gate_stops():
    from _fill_run import map_from_profile, run_fill

    check = Checks()
    with mock_page(_write("part_display.html", PART_DISPLAY)) as page:
        ok, detail = _pick(page, "Country code", "United States +1")
        shown = page.inner_text("#disp")
    check(ok and "unconfirmed" in detail and shown == "+1",
          f"'+1' shown for 'United States +1': kept, unconfirmed ({detail}, {shown})")

    def map_fields(fields, profile, page_text=""):
        out = map_from_profile(fields, profile, page_text)
        for f in fields:
            if (f.get("label") or "").startswith("Country code"):
                from agent import mapper
                out[f["ref"]] = mapper._double_check(
                    {"ref": f["ref"], "value": "United States +1", "confidence": "high",
                     "needs_review": False, "source": "model"}, f)
        return out
    form = _write("part_display_fill.html", PART_DISPLAY)
    text, records, error = run_fill(form, dry_run=False, map_fields=map_fields)
    from agent import tracker
    row = [r for r in tracker.gate_runs() if r["application_url"] == form.resolve().as_uri()][-1]
    reasons = json.loads(row["reasons_json"])
    check(error is None and row["verdict"] == "would_stop"
          and any("unconfirmed" in r for r in reasons),
          f"  ...and the gate stops on it ({reasons})")
    check.done()


def test_a_display_showing_a_different_option_is_still_abandoned():
    check = Checks()
    # Picking "No" on a widget whose display then reads "None of the above":
    # the short word is inside the long one, but not as a word of its own.
    html = PART_DISPLAY.replace("United States +1", "No").replace("Canada +1", "Yes") \
                       .replace("Country code", "Sponsorship") \
                       .replace("o.textContent.replace(/^.* /, '')", "'None of the above'")
    with mock_page(_write("other_display.html", html)) as page:
        ok, detail = _pick(page, "Sponsorship", "No")
        value = page.input_value("#co")
    check(not ok and "didn't keep" in detail and value == "",
          f"'None of the above' doesn't confirm 'No': abandoned ({detail}, {value!r})")

    # The same rule in a script with no a-z: 是 ("yes") inside 不是 ("no").
    other = PART_DISPLAY.replace("United States +1", "\u662f").replace("Canada +1", "\u5426") \
                        .replace("Country code", "Eligible") \
                        .replace("o.textContent.replace(/^.* /, '')", "'\u4e0d\u662f'")
    with mock_page(_write("other_display_cjk.html", other)) as page:
        ok, detail = _pick(page, "Eligible", "\u662f")
        value = page.input_value("#co")
    check(not ok and "didn't keep" in detail and value == "",
          f"a display reading the opposite doesn't confirm it either ({ascii(detail)})")
    check.done()


TWO_DISPLAYS = """<!doctype html><html><body><form>
  <label for="co">Country</label>
  <div class="co-control"><span class="co-singleValue" id="d1"></span>
    <span class="co-singleValue" id="d2"></span>
    <input id="co" name="country" role="combobox" aria-autocomplete="list"></div>
  <ul class="menu"><li class="option">Canada</li><li class="option">Mexico</li></ul>
</form><script>
  document.querySelectorAll('.option').forEach(o => o.addEventListener('mousedown', () => {
    // One display takes the choice, the other keeps something else.
    document.getElementById('d1').textContent = o.textContent;
    document.getElementById('d2').textContent = 'Mexico'; }));
</script></body></html>"""


def test_two_displays_that_disagree_are_not_a_kept_choice():
    check = Checks()
    with mock_page(_write("two_displays.html", TWO_DISPLAYS)) as page:
        ok, detail = _pick(page, "Country", "Canada")
        value = page.input_value("#co")
    check(not ok and "didn't keep" in detail and value == "",
          f"one display says Canada, the other Mexico: abandoned ({detail}, {value!r})")
    check.done()


def filler_decision(field):
    from agent import mapper
    return mapper._double_check({"ref": field["ref"], "value": "Canada", "confidence": "high",
                                 "needs_review": False, "source": "model"}, field)


FLEX = """<!doctype html><html><body><form>
  <label for="t" style="display:flex"><span>I accept the</span><span class="tag">Terms</span></label>
  <input id="t" name="t">
</form></body></html>"""


def test_a_flex_label_split_by_a_line_break_still_stops():
    check = Checks()
    with mock_page(_write("flex_label.html", FLEX)) as page:
        f = extract.extract_fields(page, extract.ReadGuard())[0]
    check(f.get("raw_stop") is True, "the page check reads 'I accept the Terms' across the break")
    check(sensitive.decide(f, "personal_fact", None)["action"] == "human", "so it comes to you")
    check.done()


if __name__ == "__main__":
    run_as_script(globals())
