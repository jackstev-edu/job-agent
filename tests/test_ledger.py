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


def test_open_fields_are_the_empty_ones_and_a_field_seen_with_a_value_stays_yours():
    check = Checks()
    with mock_page(_write("ledger_open.html", FORM)) as page:
        led = ledger.FieldLedger()
        fields, by = _scan(page)
        check(led.open_fields(page, fields) == {by["City"]["ref"]},
              "only the empty City is open; the prefilled Zip isn't")
        page.fill("#zp", "")                            # you clear the Zip later
        fields, by = _scan(page)
        check(by["Zip"]["ref"] not in led.open_fields(page, fields)
              and led.fillable(page, fields, _plan(by, Zip="00000")) == {},
              "  ...and the Zip, seen with a value, stays yours: not open, never filled")
        _fill(page, led, fields, _plan(by, City="Springfield"))
        fields, by = _scan(page)
        check(by["City"]["ref"] not in led.open_fields(page, fields),
              "City, once the agent filled it, isn't open either")
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


UNTRACKED = """<!doctype html><html><body><form>
  <div id="w"><div class="label">Nickname</div><input id="nick"></div>
  <div><label for="e1">Employer</label><input id="e1" name="employer"></div>
  <div><label for="e2">Employer</label><input id="e2" name="employer"></div>
</form><script>
  window.rerender = () => {
    document.getElementById('w').innerHTML = '<div class="label">Nickname</div><input id="nick">';
  };
</script></body></html>"""


def test_a_field_that_cant_be_tracked_is_never_refilled():
    check = Checks()
    with mock_page(_write("ledger_untracked.html", UNTRACKED)) as page:
        led = ledger.FieldLedger()
        fields = extract.extract_fields(page, extract.ReadGuard())
        nick = next(f for f in fields if f.get("dom_id") == "nick")
        e1 = next(f for f in fields if f.get("dom_id") == "e1")
        check(ledger.field_identity(nick) is None
              and led._keys(fields)[e1["ref"]] is None,
              "no identity (Nickname), and a shared one (two Employer boxes): both untracked")
        plan = {nick["ref"]: {"value": "Sam"}, e1["ref"]: {"value": "Acme"}}
        allowed = led.fillable(page, fields, plan, step=1)
        rows = filler.run_plan(page, fields, allowed)
        led.filled(page, fields, allowed, rows, step=1)
        check(page.input_value("#nick") == "Sam" and page.input_value("#e1") == "Acme",
              "each is filled once while empty")
        page.fill("#nick", "")                          # you clear it, same element
        page.fill("#e1", "")
        fields = extract.extract_fields(page, extract.ReadGuard())
        plan = {f["ref"]: {"value": "x"} for f in fields if f.get("dom_id") in ("nick", "e1")}
        check(led.fillable(page, fields, plan, step=1) == {},
              "cleared by you on elements the agent marked: not refilled")
        check(led.fillable(page, fields, plan, step=2) == {},
              "  ...on another step too: the agent's mark alone refuses it")
        page.evaluate("window.rerender()")              # a fresh, unmarked Nickname box
        fields = extract.extract_fields(page, extract.ReadGuard())
        plan = {f["ref"]: {"value": "x"} for f in fields if f.get("dom_id") == "nick"}
        check(led.fillable(page, fields, plan, step=1) == {},
              "re-rendered on the same step: still not refilled")
    check.done()


def test_untracked_first_fills_and_the_missing_step():
    check = Checks()
    with mock_page(_write("ledger_untracked_steps.html", UNTRACKED)) as page:
        led = ledger.FieldLedger()
        fields = extract.extract_fields(page, extract.ReadGuard())
        nick = next(f for f in fields if f.get("dom_id") == "nick")
        led.filled(page, fields, {}, [], step=1)      # nothing filled yet
        allowed = led.fillable(page, fields, {nick["ref"]: {"value": "Sam"}}, step=2)
        check(set(allowed) == {nick["ref"]}, "an untracked field still gets its first fill on a step")
        rows = filler.run_plan(page, fields, allowed)
        led.filled(page, fields, allowed, rows)          # no step given
        page.evaluate("window.rerender()")
        fields = extract.extract_fields(page, extract.ReadGuard())
        plan = {f["ref"]: {"value": "x"} for f in fields if f.get("dom_id") == "nick"}
        check(led.fillable(page, fields, plan) == {},
              "with no step given, a missing step counts as one: not refilled after a re-render")
    check.done()


SHARED_THEN_UNIQUE = """<!doctype html><html><body><form>
  <div><label for="e1">Employer</label><input id="e1" name="employer"></div>
  <div id="second"><label for="e2">Employer</label><input id="e2" name="employer"></div>
</form></body></html>"""


def test_a_shared_identity_that_becomes_unique_is_never_refilled():
    check = Checks()
    with mock_page(_write("ledger_shared_unique.html", SHARED_THEN_UNIQUE)) as page:
        led = ledger.FieldLedger()
        fields = extract.extract_fields(page, extract.ReadGuard())
        plan = {f["ref"]: {"value": "Acme"} for f in fields}
        allowed = led.fillable(page, fields, plan, step=1)
        rows = filler.run_plan(page, fields, allowed)
        led.filled(page, fields, allowed, rows, step=1)
        page.fill("#e1", "")                          # you clear one
        page.evaluate("document.getElementById('second').remove()")   # and remove the other
        fields = extract.extract_fields(page, extract.ReadGuard())
        e1 = next(f for f in fields if f.get("dom_id") == "e1")
        check(led._keys(fields)[e1["ref"]] is not None, "Employer is now unique, so trackable")
        check(led.fillable(page, fields, {e1["ref"]: {"value": "Acme"}}, step=2) == {},
              "but it was filled while untracked: yours, not refilled")
    check.done()


def test_a_field_whose_mark_failed_is_yours():
    check = Checks()
    with mock_page(_write("ledger_markfail.html", FORM)) as page:
        led = ledger.FieldLedger()
        fields, by = _scan(page)
        plan = _plan(by, City="Springfield")
        allowed = led.fillable(page, fields, plan)
        rows = filler.run_plan(page, fields, allowed)
        real = ledger._elements
        ledger._elements = lambda page, refs: [None]    # marking can't find the element
        try:
            led.filled(page, fields, allowed, rows)
        finally:
            ledger._elements = real
        check(led.entries[ledger.field_identity(by["City"])]["owner"] == "jack",
              "an unmarked fill is recorded as yours")
        page.fill("#ci", "")
        page.evaluate("window.rerender()")
        fields, by = _scan(page)
        check(led.fillable(page, fields, _plan(by, City="Springfield")) == {},
              "  ...so it's never refilled")
    check.done()


def test_a_fresh_guard_waits_the_full_quiet_period():
    check = Checks()
    with mock_page(_write("ledger_fresh_guard.html", FORM)) as page:
        guard = ledger.ActivityGuard(quiet_ms=800, echo=lambda s: None)
        guard.install(page)
        check(guard.quiet_for(page) < 800,
              "just installed, with no input seen yet: not quiet (input before it can't be seen)")
    check.done()


def test_a_misaligned_radio_group_is_never_the_agents():
    check = Checks()
    f = {"type": "radio", "options": ["Yes", "No"], "option_refs": ["g1-f0", "g1-f1", "g1-f2"]}
    check(ledger._option_index(f, "Yes") is None,
          "three option elements, two labels: no option counts as the agent's")
    f["option_refs"] = ["g1-f0", "g1-f1"]
    check(ledger._option_index(f, "No") == 1, "lined up one to one: the index is used")
    check.done()


if __name__ == "__main__":
    run_as_script(globals())
