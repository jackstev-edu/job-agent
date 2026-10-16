"""
test_containment.py — in follow mode, no agent keystroke lands anywhere but the field being filled.

WHAT THIS DOES: A page moves the focus to another box (as you clicking it
would) while the agent fills a dropdown: in contained mode the agent stops
at the next keystroke, leaves the dropdown empty, and the other box gets
nothing. A text box is set on the element itself, with no keystrokes, even
when the focus jumps away the moment it arrives. A dropdown that won't
take the focus is skipped. And without containment (every non-follow fill)
filling is exactly what it was: typed, key by key.
WHY IT MATTERS: In follow mode you may be typing in the same page. A stray
keystroke from the agent could change one of your answers.

Run from anywhere:  .venv/Scripts/python.exe tests/test_containment.py
"""
from pathlib import Path

from _harness import TEST_HOME, Checks, mock_page, run_as_script
from agent import extract, filler

KEYS_JS = """
  window.__keys = {};
  document.addEventListener('keydown', e => {
    const id = e.target.id || e.target.tagName;
    window.__keys[id] = (window.__keys[id] || 0) + 1;
  }, true);
"""

STEAL_WHILE_TYPING = f"""<!doctype html><html><body><form>
  <label for="co">Country</label>
  <input id="co" name="country" role="combobox" aria-autocomplete="list">
  <ul class="menu"><li class="option">Canada</li><li class="option">Mexico</li></ul>
  <label for="other">Your notes</label><input id="other" name="notes">
</form><script>{KEYS_JS}
  // You click into your notes box just after the agent's first keystroke.
  document.getElementById('co').addEventListener('keydown', () => {{
    setTimeout(() => document.getElementById('other').focus(), 0);
  }}, {{once: true}});
</script></body></html>"""


def _write(name: str, html: str) -> Path:
    path = TEST_HOME / name
    path.write_text(html, encoding="utf-8")
    return path


def _field(page, label):
    return next(f for f in extract.extract_fields(page, extract.ReadGuard()) if f["label"] == label)


def test_focus_stolen_mid_type_aborts_and_nothing_lands_elsewhere():
    check = Checks()
    with mock_page(_write("steal_contained.html", STEAL_WHILE_TYPING)) as page:
        country = _field(page, "Country")
        country["options"] = ["Canada", "Mexico"]
        ok, detail = filler.fill_one(page, country, "Canada", contained=True)
        other, value = page.input_value("#other"), page.input_value("#co")
        keys = page.evaluate("window.__keys")
    print(f"  contained -> {ok} {detail}; other={other!r} co={value!r} keys={keys}")
    check(not ok and "focus moved away" in detail and value == "",
          "the agent stops at the next keystroke and leaves the dropdown empty")
    check(other == "" and not keys.get("other"), "  ...and not one keystroke reached your box")

    with mock_page(_write("steal_uncontained.html", STEAL_WHILE_TYPING)) as page:
        country = _field(page, "Country")
        country["options"] = ["Canada", "Mexico"]
        filler.fill_one(page, country, "Canada")
        other = page.input_value("#other")
    check(other != "", f"without containment the same page does get keystrokes in your box "
                       f"({other!r}), so the check above means something")
    check.done()


STEAL_ON_FOCUS = f"""<!doctype html><html><body><form>
  <label for="ci">City</label><input id="ci" name="city">
  <label for="other">Your notes</label><input id="other" name="notes">
</form><script>{KEYS_JS}
  document.getElementById('ci').addEventListener('focus', () => document.getElementById('other').focus());
</script></body></html>"""


def test_a_text_box_is_set_on_the_element_with_no_keystrokes():
    check = Checks()
    with mock_page(_write("set_value.html", STEAL_ON_FOCUS)) as page:
        city = _field(page, "City")
        ok, detail = filler.fill_one(page, city, "Springfield", contained=True)
        values = (page.input_value("#ci"), page.input_value("#other"))
        keys = page.evaluate("window.__keys")
    check(ok and detail == "set" and values == ("Springfield", "") and keys == {},
          f"set on the element, no keystrokes anywhere ({detail}, {values}, {keys})")
    check.done()


NO_FOCUS = f"""<!doctype html><html><body><form>
  <label for="co">Country</label>
  <input id="co" name="country" role="combobox" aria-autocomplete="list">
  <ul class="menu"><li class="option">Canada</li></ul>
  <label for="other">Your notes</label><input id="other" name="notes">
</form><script>{KEYS_JS}
  // A widget that hands the focus elsewhere as soon as it's clicked.
  document.getElementById('co').addEventListener('focus', () => document.getElementById('other').focus());
</script></body></html>"""


def test_a_dropdown_that_wont_hold_the_focus_is_skipped():
    check = Checks()
    with mock_page(_write("no_focus.html", NO_FOCUS)) as page:
        country = _field(page, "Country")
        country["options"] = ["Canada"]
        ok, detail = filler.fill_one(page, country, "Canada", contained=True)
        keys = page.evaluate("window.__keys")
        other = page.input_value("#other")
    check(not ok and "can't keep keystrokes" in detail and not keys and other == "",
          f"skipped before any keystroke ({detail}, {keys})")
    check.done()


PLAIN = f"""<!doctype html><html><body><form>
  <label for="ci">City</label><input id="ci" name="city">
</form><script>{KEYS_JS}</script></body></html>"""


def test_without_containment_filling_is_unchanged():
    check = Checks()
    with mock_page(_write("plain.html", PLAIN)) as page:
        city = _field(page, "City")
        rows = filler.run_plan(page, [city], {city["ref"]: {"value": "Springfield"}})
        keys = page.evaluate("window.__keys")
        value = page.input_value("#ci")
    check(rows[0]["detail"] == "typed" and value == "Springfield"
          and keys.get("ci", 0) >= len("Springfield"),
          f"a non-follow fill still types key by key ({rows[0]['detail']}, {keys})")
    check.done()


if __name__ == "__main__":
    run_as_script(globals())
