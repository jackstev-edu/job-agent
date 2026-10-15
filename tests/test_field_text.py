"""
test_field_text.py — a field's label and help text never carry a value.

WHAT THIS DOES: Builds a mock form where every place the scan takes a label
or help text from (aria-labelledby, label[for], a wrapping <label>, a nearby
legend or .label, aria-describedby) also holds a value display with a
sentinel in it: a combobox whose aria-labelledby names its own value, a
react-select chosen value, an editable region, a listbox. The scan's field
list, a superset of what the classifier and mapper are sent, must hold the
question text and not the sentinel.
WHY IT MATTERS: Labels and help text go to the model. No value read from the
page may ever reach the model or the network.

Run from anywhere:  .venv/Scripts/python.exe tests/test_field_text.py
"""
import json
from pathlib import Path

from _harness import TEST_HOME, Checks, mock_page, run_as_script
from agent import extract

SENTINEL = "FIELD-TEXT-SENTINEL-5521"

FORM = f"""<!doctype html><html><body><form>
  <span id="q1">Preferred location</span>
  <div role="combobox" tabindex="0" aria-labelledby="q1 v1" aria-describedby="h1 v1"
       style="display:block;width:200px;height:24px"><span id="v1">{SENTINEL}-own</span></div>
  <span id="h1">Pick one city</span>

  <span id="q2">Degree</span>
  <div class="select__control"><input id="cb2" role="combobox" aria-labelledby="q2 cb2 sv2"
       aria-describedby="h2 d2 d3"><div class="select__single-value" id="sv2">{SENTINEL}-shown</div></div>
  <span id="h2">Highest completed</span>
  <div id="d2">Currently <span contenteditable="true">{SENTINEL}-edit</span></div>
  <div id="d3" role="listbox">{SENTINEL}-list</div>

  <label for="sd">Start date <span role="textbox">{SENTINEL}-for</span></label>
  <input id="sd" name="start">

  <label>Bio notes <div contenteditable="true">{SENTINEL}-wrap</div><input name="bio"></label>

  <div><div class="label">Team <span class="x-singleValue">{SENTINEL}-near</span></div>
       <input name="team"></div>

  <fieldset><legend>Shift <span class="select__single-value">{SENTINEL}-legend</span></legend>
    <label><input type="radio" name="shift" value="d"> Day</label>
    <label><input type="radio" name="shift" value="n"> Night</label></fieldset>
</form></body></html>"""


def _write(name: str, html: str) -> Path:
    path = TEST_HOME / name
    path.write_text(html, encoding="utf-8")
    return path


def test_label_and_help_never_carry_a_value():
    check = Checks()
    with mock_page(_write("field_text.html", FORM)) as page:
        shown = page.evaluate("""() => ['v1', 'sv2', 'd2', 'd3'].map(
            id => document.getElementById(id).innerText).join(' ')""")
        check(shown.count(SENTINEL) == 4,
              "the sentinels are really rendered there (so the checks below mean something)")
        fields = extract.extract_fields(page, extract.ReadGuard())
    print(json.dumps([{k: f.get(k) for k in ("type", "label", "help_text", "options")}
                      for f in fields], indent=1))
    by_label = {f["label"]: f for f in fields}
    check({"Preferred location", "Degree", "Start date", "Bio notes", "Team", "Shift"}
          <= set(by_label), f"each question keeps its own text as its label ({sorted(by_label)})")
    check(by_label.get("Preferred location", {}).get("type") == "combobox"
          and by_label.get("Degree", {}).get("type") == "combobox",
          "both comboboxes were scanned")
    check(by_label.get("Degree", {}).get("help_text") == "Highest completed Currently",
          "describedby keeps its words and drops the editable region and the listbox")
    check(by_label.get("Shift", {}).get("options") == ["Day", "Night"],
          "the radio group's options are its labels")
    check(all(SENTINEL not in str(f.get("label")) and SENTINEL not in str(f.get("help_text"))
              for f in fields), "no label or help text holds the sentinel")
    check(SENTINEL not in json.dumps(fields), "nor does anything else the scan returned")
    check.done()


if __name__ == "__main__":
    run_as_script(globals())
