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
from agent.ledger import _touch_tuple_of as _touch_tuple

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


def test_open_fields_leaves_out_what_you_cleared_and_what_you_touched():
    check = Checks()
    with mock_page(_write("ledger_open_cleared.html", FORM)) as page:
        led = ledger.FieldLedger()
        fields, by = _scan(page)
        _fill(page, led, fields, _plan(by, City="Springfield"))
        page.fill("#ci", "")                            # you clear what the agent put there
        fields, by = _scan(page)
        check(by["City"]["ref"] not in led.open_fields(page, fields)
              and led.entries[ledger.field_identity(by["City"])]["owner"] == "jack",
              "cleared after the agent filled it: not open, and yours")

    with mock_page(_write("ledger_open_touched.html", FORM)) as page:
        led = ledger.FieldLedger()
        ledger.ActivityGuard(echo=lambda *a: None).install(page)
        page.type("#ci", "Shelbyville")                 # you type...
        page.fill("#ci", "")                            # ...and clear it
        fields, by = _scan(page)
        check(by["City"]["ref"] not in led.open_fields(page, fields)
              and led.entries[ledger.field_identity(by["City"])]["owner"] == "jack",
              "typed in and cleared by you: not open, and yours for the run")
        check(led.fillable(page, fields, _plan(by, City="Springfield")) == {},
              "  ...and never filled")
    check.done()


def _quiet_guard(page):
    guard = ledger.ActivityGuard(echo=lambda *a: None)
    guard.install(page)
    return guard


def test_what_you_type_while_the_agent_fills_is_still_yours():
    check = Checks()
    with mock_page(_write("ledger_while_agent.html", FORM)) as page:
        led = ledger.FieldLedger()
        guard = _quiet_guard(page)
        with guard.filling(page):
            page.type("#ci", "Shelbyville")             # you, during the agent's fill
            page.fill("#ci", "")
        fields, by = _scan(page)
        check(by["City"]["ref"] not in led.open_fields(page, fields),
              "typed in and cleared during a fill: not open")
    check.done()


PICKER = """<!doctype html><html><body><form>
  <label for="co">Country</label>
  <div class="co-control"><span id="disp"></span>
    <input id="co" name="country" role="combobox" aria-autocomplete="list">
    <button type="button" class="clear" style="width:20px;height:20px">x</button></div>
  <ul class="menu"><li class="opt" style="height:20px">Canada</li></ul>
</form><script>
  document.querySelector('.opt').addEventListener('mousedown', e => {
    e.preventDefault();
    document.getElementById('disp').innerHTML = '<span class="co-single-value">Canada</span>'; });
  document.querySelector('.clear').addEventListener('mousedown', e => {
    e.preventDefault(); document.getElementById('disp').innerHTML = ''; });
</script></body></html>"""


def test_a_dropdown_you_picked_with_the_mouse_and_cleared_is_yours():
    check = Checks()
    with mock_page(_write("ledger_picker.html", PICKER)) as page:
        led = ledger.FieldLedger()
        _quiet_guard(page)
        page.click(".opt")                              # you pick a row...
        page.click(".clear")                            # ...and clear it with the X
        fields, by = _scan(page)
        check(by["Country"]["ref"] not in led.open_fields(page, fields),
              "picked by mouse and cleared: not open (its control was touched)")
    check.done()


UNTRACKED_PAIR = """<!doctype html><html><body><form>
  <div id="w"><input id="a" placeholder="Employer"></div>
  <input id="b" placeholder="Employer">
</form><script>
  window.rerender = () => {
    document.getElementById('w').innerHTML = '<input id="a" placeholder="Employer">'; };
</script></body></html>"""


def test_an_untracked_field_you_touched_stays_out_after_a_rerender():
    check = Checks()
    with mock_page(_write("ledger_untracked_touch.html", UNTRACKED_PAIR)) as page:
        led = ledger.FieldLedger()
        _quiet_guard(page)
        page.type("#a", "Acme")                         # you type...
        page.fill("#a", "")                             # ...and clear it
        fields = extract.extract_fields(page, extract.ReadGuard())
        check(all(ledger.field_identity(f) is None for f in fields),
              "both boxes are untracked (so the checks below mean something)")
        check(led.open_fields(page, fields) == set(), "seen touched: nothing of that kind is open")
        page.evaluate("window.rerender()")              # the mark goes with the element
        fields = extract.extract_fields(page, extract.ReadGuard())
        check(led.open_fields(page, fields) == set(),
              "  ...and still nothing after the page re-renders the box")
    check.done()


CHIPS = """<!doctype html><html><body><form>
  <label for="sk">Skills</label>
  <div class="sk-control"><span class="sk-multi-value">Python</span>
    <input id="sk" name="skills" role="combobox" aria-autocomplete="list"></div>
</form><script>
  document.getElementById('sk').addEventListener('keydown', e => {
    if (e.key === 'Backspace' && !e.target.value) document.querySelector('.sk-multi-value')?.remove();
  });
</script></body></html>"""


def test_a_chip_you_remove_with_backspace_while_the_agent_fills_is_yours():
    check = Checks()
    with mock_page(_write("ledger_chip_backspace.html", CHIPS)) as page:
        led = ledger.FieldLedger()
        guard = _quiet_guard(page)
        with guard.filling(page):
            page.focus("#sk")
            page.keyboard.press("Backspace")            # you, during the agent's fill
        check(page.locator(".sk-multi-value").count() == 0,
              "the chip is gone, the box is empty (so the next check means something)")
        fields, by = _scan(page)
        check(by["Skills"]["ref"] not in led.open_fields(page, fields),
              "removed with Backspace during a fill: not open")
    check.done()


def test_a_field_the_agent_fills_under_the_listener_stays_the_agents():
    check = Checks()
    with mock_page(_write("ledger_agent_listener.html", FORM)) as page:
        led = ledger.FieldLedger()
        guard = _quiet_guard(page)
        fields, by = _scan(page)
        with guard.filling(page):                       # the agent fills, as follow mode does
            plan = led.fillable(page, fields, _plan(by, City="Springfield"))
            rows = filler.run_plan(page, fields, plan, contained=True)
            led.filled(page, fields, plan, rows)
        led.poll(page)
        key = ledger.field_identity(by["City"])
        fields, by = _scan(page)
        check(by["City"]["ref"] not in led.open_fields(page, fields)
              and led.entries[key]["owner"] == "agent" and led.left_out[by["City"]["ref"]] == "agent",
              "filled by the agent: left out as the agent's, not yours")
        page.evaluate("window.rerender()")
        fields, by = _scan(page)
        check(by["City"]["ref"] in led.open_fields(page, fields)
              and set(led.fillable(page, fields, _plan(by, City="Springfield"))) == {by["City"]["ref"]},
              "  ...and refilled after a re-render, as before")
    check.done()


ABANDON = """<!doctype html><html><body><form>
  <label for="co">Country</label>
  <input id="co" name="country" role="combobox" aria-autocomplete="list">
  <ul class="menu"><li class="option">Canada</li><li class="option">Mexico</li></ul>
</form></body></html>"""


def test_a_dropdown_the_agent_typed_into_and_abandoned_is_yours():
    check = Checks()
    with mock_page(_write("ledger_abandon.html", ABANDON)) as page:
        led = ledger.FieldLedger()
        guard = _quiet_guard(page)
        fields, by = _scan(page)
        with guard.filling(page):
            ok, detail = filler.fill_one(page, by["Country"], "Zzzland", contained=True)
        check(not ok and page.input_value("#co") == "",
              f"the agent typed, found no match and abandoned it ({detail})")
        fields, by = _scan(page)
        check(by["Country"]["ref"] not in led.open_fields(page, fields),
              "  ...and it's yours: not open (refusal only)")
    check.done()


SHOWS_OWN_TEXT = """<!doctype html><html><body><form>
  <span id="sl">State</span>
  <button type="button" role="combobox" aria-labelledby="sl" id="st"
          style="width:120px;height:24px">Ohio</button>
  <div data-automation-id="formField-country">
    <label for="wc">Country</label>
    <div id="wc" role="combobox" tabindex="0" style="width:120px;height:24px"></div>
    <div data-automation-id="selectedItem">Canada</div></div>
</form></body></html>"""


def test_a_button_combobox_or_workday_item_showing_text_isnt_open():
    check = Checks()
    with mock_page(_write("ledger_own_text.html", SHOWS_OWN_TEXT)) as page:
        led = ledger.FieldLedger()
        fields, by = _scan(page)
        state, country = by.get("State"), by.get("Country")
        check(state is not None and country is not None, f"both scanned ({sorted(by)})")
        open_refs = led.open_fields(page, fields)
        check(state is not None and state["ref"] not in open_refs,
              "a button combobox reading 'Ohio' isn't open")
        check(country is not None and country["ref"] not in open_refs,
              "a Workday field showing a selectedItem isn't open")
    check.done()


def test_a_label_click_marks_its_control():
    check = Checks()
    with mock_page(_write("ledger_label_click.html", FORM)) as page:
        led = ledger.FieldLedger()
        _quiet_guard(page)
        page.click("label[for=ci]")                     # you click City's label
        fields, by = _scan(page)
        check(by["City"]["ref"] not in led.open_fields(page, fields),
              "clicking the label marks City: not open")
    check.done()


TAGLESS = """<!doctype html><html><body><form>
  <div id="w"><span id="cl">Country</span>
    <div role="combobox" aria-labelledby="cl" tabindex="0" style="width:120px;height:24px"></div></div>
</form><script>
  window.remount = () => {
    document.getElementById('w').innerHTML = '<span id="cl">Country</span>' +
      '<div role="combobox" aria-labelledby="cl" tabindex="0" style="width:120px;height:24px"></div>'; };
</script></body></html>"""


def test_a_tagless_dropdown_you_touched_stays_out_after_a_remount():
    check = Checks()
    with mock_page(_write("ledger_tagless.html", TAGLESS)) as page:
        led = ledger.FieldLedger()
        _quiet_guard(page)
        fields = extract.extract_fields(page, extract.ReadGuard())
        box = next(f for f in fields if f.get("type") == "combobox")
        check(not box.get("tag") and extract.secret_keys_of(box) == {("anon", "")},
              f"a second-pass dropdown is known by the tagless key alone ({box.get('tag')!r})")
        page.click("[role=combobox]")                   # you open it
        page.evaluate("window.remount()")               # the page re-mounts it: the mark is gone
        fields = extract.extract_fields(page, extract.ReadGuard())
        box = next(f for f in fields if f.get("type") == "combobox")
        check(page.evaluate("() => !document.querySelector('[data-ja-touched]')"),
              "no element carries a touch mark any more (so the next check means something)")
        check(box["ref"] not in led.open_fields(page, fields),
              "touched, then re-mounted: still out of model input")
    check.done()


# Two boxes labelled "City": one the agent fills, one with nothing else to
# know it by, so the label is its only key.
TWO_CITIES = """<!doctype html><html><body><form>
  <label for="a">City</label><input id="a" name="city_home">
  <div id="w"><label>City<input></label></div>
</form><script>
  window.remount = () => { document.getElementById('w').innerHTML = '<label>City<input></label>'; };
</script></body></html>"""


def test_typing_during_a_fill_isnt_cancelled_by_another_field_the_agent_filled():
    check = Checks()
    with mock_page(_write("ledger_two_cities.html", TWO_CITIES)) as page:
        led = ledger.FieldLedger()
        guard = _quiet_guard(page)
        fields, _ = _scan(page)
        home = next(f for f in fields if f.get("dom_name") == "city_home")
        yours = next(f for f in fields if not f.get("dom_name"))
        check(extract.secret_keys_of(yours) <= extract.secret_keys_of(home),
              "your box's only key, its label, is one of the agent's box's keys")
        with guard.filling(page):
            page.type("#w input", "Shelbyville")        # you, in the other City box
            plan = led.fillable(page, fields, {home["ref"]: {"value": "Springfield",
                                                             "source": "model"}})
            rows = filler.run_plan(page, fields, plan, contained=True)
            led.filled(page, fields, plan, rows)        # the agent fills the named one
            # The page re-renders while the agent is still filling: your box
            # comes back empty, with no mark on it any more.
            page.evaluate("window.remount()")
        fields, _ = _scan(page)
        yours = next(f for f in fields if not f.get("dom_name"))
        check(page.input_value("#a") == "Springfield" and page.input_value("#w input") == "",
              "the agent filled the named City box; yours came back empty")
        check(yours["ref"] not in led.open_fields(page, fields),
              "your typing in the other City box isn't cancelled by it: still out")
    check.done()


SHOUTS = """<!doctype html><html><body><form>
  <div id="w1"><label for="ci">City</label><input id="ci" name="city"></div>
  <div id="w2"><label for="zp">Zip</label><input id="zp" name="zip" value="99999"></div>
</form><script>
  // A mask that reshapes what anyone types, the agent included.
  document.getElementById('ci').addEventListener('input', e => {
    e.target.value = e.target.value.replace(/ /g, '-'); });
</script></body></html>"""


def test_left_out_says_which_of_you_a_field_belongs_to():
    check = Checks()
    with mock_page(_write("ledger_left_out.html", SHOUTS)) as page:
        led = ledger.FieldLedger()
        guard = _quiet_guard(page)
        fields, by = _scan(page)
        with guard.filling(page):                       # contained, as follow mode fills
            plan = led.fillable(page, fields, _plan(by, City="San Jose"))
            rows = filler.run_plan(page, fields, plan, contained=True)
            led.filled(page, fields, plan, rows)
        led.poll(page)                                  # the mask reshaped it: no longer "ours"
        check(page.input_value("#ci") == "San-Jose",
              f"the page reshaped what the agent typed ({page.input_value('#ci')})")
        led.open_fields(page, fields)
        check(led.left_out.get(by["City"]["ref"]) == "agent",
              f"the agent filled City, whatever the page did to it: 'agent' ({led.left_out})")
        check(led.left_out.get(by["Zip"]["ref"]) == "value",
              f"Zip, prefilled by the page: 'value' ({led.left_out})")
    check.done()


def test_a_field_you_typed_in_and_cleared_reads_as_touched():
    check = Checks()
    with mock_page(_write("ledger_left_out_touched.html", FORM)) as page:
        led = ledger.FieldLedger()
        _quiet_guard(page)
        page.type("#ci", "Shelbyville")                 # you type and clear City
        page.fill("#ci", "")
        fields, by = _scan(page)
        led.open_fields(page, fields)
        check(led.left_out.get(by["City"]["ref"]) == "touched",
              f"City, typed in and cleared by you: 'touched' ({led.left_out})")
    check.done()


# Two boxes with nothing to tell them apart: same tag, no name, no id,
# the same label.
TWINS = """<!doctype html><html><body><form>
  <div id="w1"><label>City<input></label></div>
  <div id="w2"><label>City<input></label></div>
</form><script>
  window.remount = () => {
    document.getElementById('w2').innerHTML = '<label>City<input></label>'; };
</script></body></html>"""


def test_your_typing_survives_the_agent_filling_an_identical_field():
    check = Checks()
    with mock_page(_write("ledger_twins.html", TWINS)) as page:
        led = ledger.FieldLedger()
        guard = _quiet_guard(page)
        fields, _ = _scan(page)
        first, second = fields[0], fields[1]
        check(_touch_tuple(first) == _touch_tuple(second),
              "the two boxes are identical on every part of their identity")
        with guard.filling(page):
            page.type("#w2 input", "Shelbyville")       # you, in the second box
            plan = led.fillable(page, fields, {first["ref"]: {"value": "Springfield",
                                                              "source": "model"}})
            rows = filler.run_plan(page, fields, plan, contained=True)
            led.filled(page, fields, plan, rows)        # the agent fills the first
            page.evaluate("window.remount()")           # yours is re-mounted, empty
        fields, _ = _scan(page)
        yours = fields[1]
        check(page.input_value("#w1 input") == "Springfield"
              and page.input_value("#w2 input") == "",
              "the agent filled the first; yours came back empty")
        check(yours["ref"] not in led.open_fields(page, fields),
              "your typing still counts, though the agent filled its twin")
    check.done()


def test_a_field_the_agent_filled_that_you_then_change_is_yours():
    check = Checks()
    with mock_page(_write("ledger_agent_then_you.html", FORM)) as page:
        led = ledger.FieldLedger()
        guard = _quiet_guard(page)
        fields, by = _scan(page)
        with guard.filling(page):
            plan = led.fillable(page, fields, _plan(by, City="Springfield"))
            rows = filler.run_plan(page, fields, plan, contained=True)
            led.filled(page, fields, plan, rows)
        page.fill("#ci", "Shelbyville")                 # you, afterwards
        fields, by = _scan(page)
        led.open_fields(page, fields)
        check(led.left_out.get(by["City"]["ref"]) == "value",
              f"the agent filled it, you changed it: yours, and flagged ({led.left_out})")
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


def test_a_row_click_still_marks_the_focused_dropdown_but_a_click_marks_no_other_focused_box():
    # The wizard's review mark (REVIEW_ATTR, fix of cc17666's BLOCK): a
    # mouse pick of a row of the dropdown's own list (named by
    # aria-controls, as a real combobox names it; Jack, 2026-10-27) while
    # its combobox input holds the
    # focus marks the input; a click elsewhere while a plain box holds the
    # focus marks only what was clicked. Follow mode's TOUCH_ATTR still
    # marks the focused box. The first half passes on cc17666 too; the
    # second fails there (no REVIEW_ATTR at all).
    from playwright.sync_api import sync_playwright
    check = Checks()
    html = TEST_HOME / "pointer-rule.html"
    html.write_text('''<!doctype html><html><body>
<input id="plain" value="x"><input id="other" value="y">
<label for="cb">Pick</label><input id="cb" role="combobox" aria-controls="menu" aria-expanded="true" autocomplete="off">
<ul id="menu" role="listbox"><li id="row" role="option">Canada</li></ul></body></html>''', encoding="utf-8")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            ctx = browser.new_context(); ctx.add_init_script(ledger.ACTIVITY_INIT_SCRIPT)
            page = ctx.new_page(); page.goto(html.resolve().as_uri())
            T = ledger.REVIEW_ATTR
            page.focus("#cb"); page.locator("#row").click()
            marked_after_pick = page.evaluate("[...document.querySelectorAll('[%s]')].map(e => e.id)" % T)
            page.evaluate("() => { for (const e of document.querySelectorAll('[%s]')) e.removeAttribute('%s'); }" % (T, T))
            page.focus("#plain"); page.locator("#other").click()
            marked_after_click = page.evaluate("[...document.querySelectorAll('[%s]')].map(e => e.id)" % T)
        finally:
            browser.close()
    check("cb" in marked_after_pick, f"the focused combobox is marked by a row pick ({marked_after_pick})")
    check("plain" not in marked_after_click and "other" in marked_after_click,
          f"a click elsewhere marks the clicked box only, not the focused plain box ({marked_after_click})")
    check.done()


def test_the_review_mark_takes_a_focused_dropdown_only_from_a_click_on_it_or_its_row():
    # Jack's decision 6 of 2026-10-27, one case per branch of the picker
    # test: a click ELSEWHERE puts REVIEW_ATTR on no focused element at
    # all (a page that moved the focus to a dropdown after the baseline
    # must not have it count); a click on a ROW ([role=option]) puts it
    # on the focused element only for an explicit combobox role, a live
    # aria-autocomplete, a select, a listbox, an option or a box inside a
    # [role=combobox]; never on Bootstrap's form-control box, an input
    # inside an Ant Design-style "...-control" wrapper, or one with
    # aria-autocomplete="none"; and only when the row is the dropdown's
    # OWN (inside it, or in the list it names by aria-controls): a row of
    # another list marks nothing (fold of 97acecf's review). TOUCH_ATTR
    # (follow mode) takes the focused element in every case. On f1493d5
    # the click elsewhere marked every dropdown-style element; on 97acecf
    # the other list's row did.
    from playwright.sync_api import sync_playwright
    check = Checks()
    html = TEST_HOME / "pointer-rule-branches.html"
    html.write_text('''<!doctype html><html><body>
<input id="fc" class="form-control" aria-controls="rows"><div class="ant-form-item-control"><input id="ant" aria-controls="rows"></div>
<input id="acnone" aria-autocomplete="none" aria-controls="rows"><input id="aclist" aria-autocomplete="list" aria-controls="rows">
<input id="role" role="combobox" aria-controls="rows"><select id="sel" aria-controls="rows"><option>A</option></select>
<div role="combobox" aria-controls="rows"><input id="inner"></div>
<div id="lb" role="listbox" tabindex="0" aria-owns="rows">list</div><div id="op" role="option" tabindex="0" aria-controls="rows">row</div>
<button id="away" type="button">elsewhere</button>
<ul id="rows" role="listbox"><li id="row" role="option">Canada</li></ul>
<ul id="others" role="listbox"><li id="otherrow" role="option">Chile</li></ul>
<script>for (const id of ['row', 'otherrow']) document.getElementById(id).addEventListener('mousedown', e => e.preventDefault());</script>
</body></html>''', encoding="utf-8")
    on_row = {"fc": False, "ant": False, "acnone": False, "aclist": True, "role": True, "sel": True,
              "inner": True, "lb": True, "op": True}
    got: dict = {}
    got_row: dict = {}
    got_other: dict = {}
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            ctx = browser.new_context(); ctx.add_init_script(ledger.ACTIVITY_INIT_SCRIPT)
            page = ctx.new_page(); page.goto(html.resolve().as_uri())
            clear = ("([a, b]) => { for (const e of document.querySelectorAll('*')) "
                     "{ e.removeAttribute(a); e.removeAttribute(b); } }")
            read = ("([id, r, t]) => { const e = document.getElementById(id); "
                    "return [e.hasAttribute(r), e.hasAttribute(t)]; }")

            def click(sel):
                page.mouse.move(0, 0)
                box = page.locator(sel).bounding_box()
                page.mouse.move(box["x"] + 5, box["y"] + 5); page.mouse.down(); page.mouse.up()
            for el_id in on_row:
                page.evaluate(clear, [ledger.REVIEW_ATTR, ledger.TOUCH_ATTR])
                page.focus(f"#{el_id}")
                page.dispatch_event("#away", "pointerdown")      # untrusted: must mark nothing
                untrusted = page.evaluate("id => document.getElementById(id).hasAttribute('%s')"
                                          % ledger.REVIEW_ATTR, el_id)
                click("#away")
                got[el_id] = page.evaluate(read, [el_id, ledger.REVIEW_ATTR, ledger.TOUCH_ATTR]) + [untrusted]
                page.evaluate(clear, [ledger.REVIEW_ATTR, ledger.TOUCH_ATTR])
                page.focus(f"#{el_id}")
                click("#row")                                    # the row keeps the focus where it was
                focused = page.evaluate("document.activeElement && document.activeElement.id")
                got_row[el_id] = page.evaluate(read, [el_id, ledger.REVIEW_ATTR, ledger.TOUCH_ATTR]) + [focused]
                page.evaluate(clear, [ledger.REVIEW_ATTR, ledger.TOUCH_ATTR])
                page.focus(f"#{el_id}")
                click("#otherrow")                               # another list's row: not its own
                focused = page.evaluate("document.activeElement && document.activeElement.id")
                got_other[el_id] = page.evaluate(read, [el_id, ledger.REVIEW_ATTR, ledger.TOUCH_ATTR]) + [focused]
        finally:
            browser.close()
    for el_id, review in on_row.items():
        check(got[el_id][0] is False, f"#{el_id}: a click elsewhere never puts the review mark on it ({got[el_id]})")
        check(got[el_id][1] is True, f"#{el_id}: follow mode's touch mark still taken ({got[el_id]})")
        check(got[el_id][2] is False, f"#{el_id}: an untrusted event marks nothing ({got[el_id]})")
        check(got_row[el_id][2] == el_id, f"#{el_id}: the row click left the focus on it ({got_row[el_id]})")
        check(got_row[el_id][0] is review, f"#{el_id}: a click on its own row: review mark {review} ({got_row[el_id]})")
        check(got_row[el_id][1] is True, f"#{el_id}: follow mode's touch mark on a row click ({got_row[el_id]})")
        check(got_other[el_id][2] == el_id and got_other[el_id][0] is False and got_other[el_id][1] is True,
              f"#{el_id}: a click on another list's row marks it for the wizard never, for follow mode still ({got_other[el_id]})")
    check.done()


DATE_PORTAL = """<!doctype html><html><body><form>
  <div id="w"><label for="dt">Start date</label><input id="dt" name="start_date"></div>
</form>
<div id="portal"><button type="button" id="pick">1 March</button></div>
<script>
  // A datepicker in a portal outside the field: its button keeps the focus
  // in the box (mousedown default prevented) and sets the value by script.
  const pick = document.getElementById('pick');
  pick.addEventListener('mousedown', e => e.preventDefault());
  pick.addEventListener('click', () => { document.getElementById('dt').value = '2026-03-01'; });
  window.remount = () => { document.getElementById('w').innerHTML =
    '<label for="dt">Start date</label><input id="dt" name="start_date">'; };
</script></body></html>"""


def test_a_box_you_changed_with_the_mouse_through_a_portal_stays_out_after_a_remount():
    # Fix of cc17666's BLOCK (follow mode's negative): the box holds the
    # focus, you pick a day in a datepicker whose list sits in a portal
    # outside the field, the page sets the value by script (no trusted
    # event on the box), then the form re-mounts it empty. The focused
    # box was marked and recorded at your click, so it stays out of model
    # input. On cc17666 it came back as open.
    check = Checks()
    with mock_page(_write("ledger_date_portal.html", DATE_PORTAL)) as page:
        led = ledger.FieldLedger()
        _quiet_guard(page)
        page.focus("#dt")
        page.click("#pick")
        focused = page.evaluate("document.activeElement && document.activeElement.id")
        held = page.input_value("#dt")
        page.evaluate("window.remount()")
        fields = extract.extract_fields(page, extract.ReadGuard())
        box = next(f for f in fields if f.get("name") == "start_date")
        check(focused == "dt" and held == "2026-03-01",
              f"the pick kept the focus in the box and set its value ({focused}, {held})")
        check(page.evaluate("() => !document.querySelector('[data-ja-touched]#dt')"),
              "the re-mounted box carries no mark (so the next check means something)")
        check(box["ref"] not in led.open_fields(page, fields),
              "changed with the mouse, then re-mounted: still out of model input")
    check.done()


if __name__ == "__main__":
    run_as_script(globals())
