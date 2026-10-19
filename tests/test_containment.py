"""
test_containment.py — in follow mode, no agent keystroke lands anywhere but the field being filled.

WHAT THIS DOES: A page moves the focus to another box (as you clicking it
would) while the agent fills a dropdown: in contained mode the agent stops
at the next keystroke, leaves the dropdown empty, and the other box gets
nothing. A text box is set on the element itself, with no keystrokes, even
when the focus jumps away the moment it arrives. A dropdown that won't
take the focus is skipped. And without containment (every non-follow fill)
filling is exactly what it was: typed, key by key. Also what a set counts
as: a box that reformats the same answer is filled, and one left showing
something else is kept but marked unconfirmed, never reported as done.
WHY IT MATTERS: In follow mode you may be typing in the same page. A stray
keystroke from the agent could change one of your answers. And an answer
the page reshaped past recognition must never be reported as filled.

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


# A phone box that reformats what anyone puts in it, as real ones do.
PHONE = """<!doctype html><html><body><form>
  <label for="ph">Phone</label><input id="ph" name="phone" type="tel">
</form><script>
  document.getElementById('ph').addEventListener('input', e => {
    const d = e.target.value.replace(/\\D/g, '').slice(-10);
    if (d.length === 10) e.target.value = '(' + d.slice(0,3) + ') ' + d.slice(3,6) + '-' + d.slice(6);
  });
</script></body></html>"""

# A box that keeps only part of what it's given.
CLIPPED = """<!doctype html><html><body><form>
  <label for="ci">City</label><input id="ci" name="city">
</form><script>
  document.getElementById('ci').addEventListener('input', e => {
    e.target.value = e.target.value.slice(0, 4); });
</script></body></html>"""

# A box that collapses runs of spaces.
SQUEEZED = """<!doctype html><html><body><form>
  <label for="na">Full name</label><input id="na" name="full_name">
</form><script>
  document.getElementById('na').addEventListener('input', e => {
    e.target.value = e.target.value.replace(/\\s+/g, ' '); });
</script></body></html>"""


def _set(page, label, value):
    fields = extract.extract_fields(page, extract.ReadGuard())
    f = next(f for f in fields if (f.get("label") or "").startswith(label))
    return filler.fill_one(page, f, value, contained=True)


# A box that keeps the national digits and stamps its own dialling code on.
RECODED = """<!doctype html><html><body><form>
  <label for="ph">Phone</label><input id="ph" name="phone" type="tel">
</form><script>
  document.getElementById('ph').addEventListener('input', e => {
    const d = e.target.value.replace(/\\D/g, '').slice(-10);
    if (d.length === 10) e.target.value = '+91 ' + d; });
</script></body></html>"""

PLAIN_BOX = """<!doctype html><html><body><form>
  <label for="ci">City</label><input id="ci" name="city">
</form></body></html>"""


def test_only_the_same_text_counts_as_filled():
    check = Checks()
    with mock_page(_write("contained_squeeze.html", SQUEEZED)) as page:
        ok, detail = _set(page, "Full name", "Test  Person")
        value = page.input_value("#na")
    check(ok and filler.UNCONFIRMED not in detail and value == "Test Person",
          f"a box collapsing the spaces says the same thing: done ({detail}, {value})")

    with mock_page(_write("contained_plain.html", PLAIN_BOX)) as page:
        ok, detail = _set(page, "City", "Springfield")
        value = page.input_value("#ci")
    check(ok and detail == "set" and value == "Springfield",
          f"a box that takes it as written: done ({detail}, {value})")
    check.done()


def test_a_box_that_reformats_the_answer_is_kept_and_unconfirmed():
    check = Checks()
    cases = {
        "a phone box reformatting the same digits": (PHONE, "Phone", "#ph",
                                                     "+1 555 824 9000", "(555) 824-9000"),
        "a box stamping its own dialling code on": (RECODED, "Phone", "#ph",
                                                    "555 824 9000", "+91 5558249000"),
        "a box that cut it short": (CLIPPED, "City", "#ci", "Springfield", "Spri"),
    }
    for what, (html, label, sel, answer, shows) in cases.items():
        with mock_page(_write("contained_reformat.html", html)) as page:
            ok, detail = _set(page, label, answer)
            value = page.input_value(sel)
        check(ok and filler.UNCONFIRMED in detail and value == shows,
              f"{what}: kept, unconfirmed, never done ({detail}, {value})")
    check.done()


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


TOGGLED = """<!doctype html><html><body><form>
  <label for="ci">City</label><input id="ci" name="city">
</form></body></html>"""


def test_a_box_that_became_a_password_box_is_never_set():
    check = Checks()
    with mock_page(_write("toggled.html", TOGGLED)) as page:
        city = _field(page, "City")
        # After the scan the page turns the box into a password box, and the
        # browser's password manager fills it.
        page.evaluate("""() => { const el = document.getElementById('ci');
                                 el.type = 'password'; el.value = 'BROWSER-FILLED'; }""")
        ok, detail = filler.fill_one(page, city, "Springfield", contained=True)
        still = page.evaluate("() => document.getElementById('ci').value === 'BROWSER-FILLED'")
    check(not ok and still, f"refused, and the browser-filled value is untouched ({detail})")
    check.done()


PLAIN = f"""<!doctype html><html><body><form>
  <label for="ci">City</label><input id="ci" name="city">
</form><script>{KEYS_JS}</script></body></html>"""


# A box that throws away whatever is typed into it.
CLEARING = """<!doctype html><html><body><form>
  <label for="ci">City</label><input id="ci" name="city">
</form><script>
  document.getElementById('ci').addEventListener('input', e => { e.target.value = ''; });
</script></body></html>"""


def test_a_normal_fill_reads_the_value_back():
    # Must-fix (d): the uncontained path used to return "typed" without
    # looking. Now it reads the box back like the contained path does.
    check = Checks()

    def typed(html, label, value):
        with mock_page(_write("uncontained_readback.html", html)) as page:
            fields = extract.extract_fields(page, extract.ReadGuard())
            f = next(f for f in fields if (f.get("label") or "").startswith(label))
            ok, detail = filler.fill_one(page, f, value)
            keys = page.evaluate("window.__keys") if "__keys" in html else None
        return ok, detail, keys

    ok, detail, _ = typed(PLAIN_BOX, "City", "Springfield")
    check(ok and detail == "typed", f"a plain box: typed ({detail})")
    ok, detail, _ = typed(PHONE, "Phone", "+1 555 824 9000")
    check(ok and filler.UNCONFIRMED in detail,
          f"a box that reformats the number: kept, unconfirmed ({detail})")
    ok, detail, _ = typed(CLIPPED, "City", "Springfield")
    check(ok and filler.UNCONFIRMED in detail,
          f"a box that cut it short: kept, unconfirmed ({detail})")
    ok, detail, _ = typed(SQUEEZED, "Full name", "Test  Person")
    check(ok and detail == "typed (the box reformatted it)" and filler.UNCONFIRMED not in detail,
          f"a box collapsing spaces says the same thing: done ({detail})")
    ok, detail, _ = typed(CLEARING, "City", "Springfield")
    check(not ok and "didn't take" in detail, f"a box that empties itself: not filled ({detail})")
    rows = None
    with mock_page(_write("uncontained_readback.html", PHONE)) as page:
        fields = extract.extract_fields(page, extract.ReadGuard())
        rows = filler.run_plan(page, fields, {fields[0]["ref"]: {"value": "+1 555 824 9000"}})
    check(rows and rows[0]["filled"] and rows[0]["unconfirmed"],
          "run_plan carries the unconfirmed mark, so the gate stops on it")
    check.done()


# A box that turns itself into a password box as soon as anything is typed.
TURNS_SECRET = """<!doctype html><html><body><form>
  <label for="ci">City</label><input id="ci" name="city">
</form><script>
  document.getElementById('ci').addEventListener('input', e => { e.target.type = 'password'; }, {once: true});
</script></body></html>"""


def test_a_box_that_turns_secret_after_typing_is_never_read_back():
    check = Checks()
    with mock_page(_write("turns_secret.html", TURNS_SECRET)) as page:
        fields = extract.extract_fields(page, extract.ReadGuard())
        f = next(f for f in fields if (f.get("label") or "").startswith("City"))
        seen, real = [], page.evaluate

        def spy(*a, **k):
            out = real(*a, **k)
            seen.append(out)
            return out
        page.evaluate = spy
        ok, detail = filler.fill_one(page, f, "Springfield")
        page.evaluate = real
    check(ok and "couldn't be read back" in detail and filler.UNCONFIRMED in detail,
          f"typed, then unreadable: unconfirmed, never claimed ({detail})")
    check("Springfield" not in str(seen) and "Springfield" not in detail,
          "no value word came back from the page or into the detail")
    check.done()


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
