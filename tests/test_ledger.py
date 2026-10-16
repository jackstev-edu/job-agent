"""
test_ledger.py — follow mode never overwrites you and never types while you do.

WHAT THIS DOES: One rule per test, on a mock form with real typing. Only an
empty field is filled, and a field that had a value first is yours; a value
you change after the agent filled is yours from then on; after a re-render
a field is refilled only when it's empty, its element was replaced, and the
last look showed the agent's value (you clearing the same element makes it
yours); the activity guard waits for quiet and says "hands off — filling",
and the agent's own typing doesn't count as yours; the classification cache
keeps a category across a re-render but not across a changed label.
WHY IT MATTERS: You and the agent work on the same page in follow mode.
Your answers always win.

Run from anywhere:  .venv/Scripts/python.exe tests/test_ledger.py
"""
from pathlib import Path

from _harness import TEST_HOME, Checks, mock_page, run_as_script
from agent import extract, filler, ledger

FORM = """<!doctype html><html><body><form>
  <div id="w1"><label for="ci">City</label><input id="ci" name="city"></div>
  <div id="w2"><label for="zp">Zip</label><input id="zp" name="zip" value="99999"></div>
</form><script>
  window.rerender = () => {
    document.getElementById('w1').innerHTML =
      '<label for="ci">City</label><input id="ci" name="city">';
  };
</script></body></html>"""


def _write(name: str, html: str) -> Path:
    path = TEST_HOME / name
    path.write_text(html, encoding="utf-8")
    return path


def _scan(page):
    fields = extract.extract_fields(page, extract.ReadGuard())
    return fields, {f["label"]: f for f in fields}


def _plan(by, **values):
    return {by[label]["ref"]: {"value": v, "source": "model"} for label, v in values.items()}


def _fill(page, led, fields, plan):
    allowed = led.fillable(page, fields, plan)
    rows = filler.run_plan(page, fields, allowed)
    led.filled(page, fields, allowed, rows)
    return allowed


def test_only_an_empty_field_is_filled_and_a_prefilled_one_is_yours():
    check = Checks()
    with mock_page(_write("ledger_empty.html", FORM)) as page:
        led = ledger.FieldLedger()
        fields, by = _scan(page)
        allowed = _fill(page, led, fields, _plan(by, City="Springfield", Zip="00000"))
        check(set(allowed) == {by["City"]["ref"]}, "only the empty City is filled")
        check(page.input_value("#zp") == "99999", "the prefilled Zip is left as it was")
        check(led.entries[ledger.field_identity(by["Zip"])]["owner"] == "jack",
              "  ...and is yours from then on")
    check.done()


def test_a_value_you_change_is_yours_for_good():
    check = Checks()
    with mock_page(_write("ledger_change.html", FORM)) as page:
        led = ledger.FieldLedger()
        fields, by = _scan(page)
        _fill(page, led, fields, _plan(by, City="Springfield"))
        page.fill("#ci", "Shelbyville")                 # you
        led.poll(page)
        key = ledger.field_identity(by["City"])
        check(led.entries[key]["owner"] == "jack", "your edit makes City yours")
        page.fill("#ci", "")                            # you clear it later
        fields, by = _scan(page)
        check(led.fillable(page, fields, _plan(by, City="Springfield")) == {},
              "  ...and it's never filled again, even empty")
    check.done()


def test_a_rerender_refills_only_a_replaced_empty_element_that_showed_ours():
    check = Checks()
    with mock_page(_write("ledger_rerender.html", FORM)) as page:
        led = ledger.FieldLedger()
        fields, by = _scan(page)
        _fill(page, led, fields, _plan(by, City="Springfield"))
        led.poll(page)                                  # showed ours
        page.evaluate("window.rerender()")              # a framework re-render
        fields, by = _scan(page)
        allowed = _fill(page, led, fields, _plan(by, City="Springfield"))
        check(set(allowed) == {by["City"]["ref"]} and page.input_value("#ci") == "Springfield",
              "replaced, empty, last look showed ours: refilled")

    with mock_page(_write("ledger_cleared.html", FORM)) as page:
        led = ledger.FieldLedger()
        fields, by = _scan(page)
        _fill(page, led, fields, _plan(by, City="Springfield"))
        page.fill("#ci", "")                            # you clear the same element
        fields, by = _scan(page)
        check(led.fillable(page, fields, _plan(by, City="Springfield")) == {}
              and led.entries[ledger.field_identity(by["City"])]["owner"] == "jack",
              "the same element emptied by you: yours, not refilled")

    with mock_page(_write("ledger_changed_then_rerender.html", FORM)) as page:
        led = ledger.FieldLedger()
        fields, by = _scan(page)
        _fill(page, led, fields, _plan(by, City="Springfield"))
        page.fill("#ci", "Shelbyville")                 # you
        led.poll(page)                                  # last look: not ours
        page.evaluate("window.rerender()")
        fields, by = _scan(page)
        check(led.fillable(page, fields, _plan(by, City="Springfield")) == {},
              "replaced and empty, but the last look showed your value: not refilled")
    check.done()


def test_the_activity_guard_waits_for_quiet_and_ignores_its_own_typing():
    check = Checks()
    said = []
    with mock_page(_write("ledger_guard.html", FORM)) as page:
        guard = ledger.ActivityGuard(quiet_ms=600, echo=said.append)
        guard.install(page)
        page.click("#zp")
        page.keyboard.type("1")                         # you, just now
        check(guard.quiet_for(page) < 600, "right after your typing, it isn't quiet")
        check(guard.wait_quiet(page, timeout_s=5) and guard.quiet_for(page) >= 600,
              "it waits until you've been idle long enough")
        with guard.filling(page):
            page.click("#ci")
            page.keyboard.type("abc")                   # the agent
        check(guard.quiet_for(page) >= 600, "the agent's own typing doesn't count as yours")
    check(said == ["  hands off — filling"], f"it says so while filling ({said})")
    check.done()


def test_the_classification_cache_follows_the_label():
    check = Checks()
    cache = ledger.ClassificationCache()
    city = {"ref": "g1-f0", "type": "text", "tag": "input", "dom_name": "city",
            "explicit_label": "City", "label": "City", "help_text": ""}
    cache.remember([city], {"g1-f0": "personal_fact"})
    again = {**city, "ref": "g2-f0"}
    known, todo = cache.split([again])
    check(known == {"g2-f0": "personal_fact"} and not todo,
          "a re-rendered field with the same label keeps its category")
    relabelled = {**city, "ref": "g3-f0", "label": "Have you ever been convicted?"}
    known, todo = cache.split([relabelled])
    check(not known and todo == [relabelled], "a changed label is classified again")
    check.done()


if __name__ == "__main__":
    run_as_script(globals())
