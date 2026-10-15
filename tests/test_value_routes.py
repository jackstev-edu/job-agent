"""
test_value_routes.py — the live routes from a page value to the model stay closed.

WHAT THIS DOES: Four routes, each with a sentinel standing in for a value
already on the page. Validation text that echoes an autofilled value after
the resume upload must not reach the classifier or the mapper. Text already
in a dropdown's search box must not reach its options (the menu filters by
it, and a creatable menu offers 'Create "<text>"'). A value shown in a
role=button picker, a chip or a Workday selected item inside a label or help
source must not reach the label or help. And a title the page sets after
Apply must not reach the cover letter.
WHY IT MATTERS: No value read from the page may ever reach the model or the
network.

Run from anywhere:  .venv/Scripts/python.exe tests/test_value_routes.py
"""
import json
from pathlib import Path

from _harness import TEST_HOME, Checks, mock_page, run_as_script
from agent import adapters, extract, tracker

SENTINEL = "VALUE-ROUTE-SENTINEL-6604"


def _write(name: str, html: str) -> Path:
    path = TEST_HOME / name
    path.write_text(html, encoding="utf-8")
    return path


UPLOAD_FORM = f"""<!doctype html><html><body><form>
  <label for="cv">Resume</label><input type="file" id="cv" name="resume">
  <label for="ph">Phone</label><input id="ph" name="phone" aria-describedby="ph-err">
  <div id="ph-err"></div>
  <label for="fn">First Name</label><input id="fn" name="first_name">
</form>
<script>
  // Stands in for a portal's resume autofill, and its validation message.
  document.getElementById('cv').addEventListener('change', () => {{
    document.getElementById('ph').value = '{SENTINEL}-autofill';
    document.getElementById('ph-err').textContent =
      "'{SENTINEL}-autofill' is not a valid phone number";
  }});
</script></body></html>"""


def test_validation_text_after_the_upload_never_reaches_the_model():
    import apply
    from _fill_run import PROFILE, classify_by_label, map_from_profile, run_fill

    check = Checks()
    (TEST_HOME / "route-resume.pdf").write_bytes(b"%PDF-1.4 test resume\n")
    profile = PROFILE.replace("resume_default: missing.pdf", "resume_default: route-resume.pdf")
    scans, classified, mapped = [], [], []
    real_scan = extract.extract_fields

    def scan(page, guard=None):
        out = real_scan(page, guard)
        scans.append(json.dumps(out))
        return out

    def classify(fields):
        classified.append(json.dumps(fields))
        return classify_by_label(fields)

    def map_fields(fields, profile, page_text=""):
        mapped.append(json.dumps(fields))
        return map_from_profile(fields, profile, page_text)
    text, _, error = run_fill(_write("route_upload.html", UPLOAD_FORM), dry_run=False,
                              profile_yaml=profile, classify=classify, map_fields=map_fields,
                              patches=((apply.extract, "extract_fields", scan),))
    print(text[-500:])
    check(error is None and "uploaded route-resume.pdf" in text, f"the run uploaded the resume ({error})")
    check(len(scans) == 2 and SENTINEL not in scans[0] and SENTINEL in scans[1],
          "the post-upload rescan really holds the validation text (so the checks below mean something)")
    check(classified and mapped, "the classifier and the mapper were both reached")
    check(all(SENTINEL not in c for c in classified), "the classifier never got it")
    check(all(SENTINEL not in m for m in mapped), "nor did the mapper")
    check.done()


COMBO_FORM = f"""<!doctype html><html><body><form>
  <label for="sch">School</label>
  <input id="sch" name="school" role="combobox" aria-autocomplete="list">
  <ul id="m1" class="menu" style="display:none"></ul>
  <label for="deg">Degree</label>
  <input id="deg" name="degree" role="combobox" aria-autocomplete="list">
  <ul id="m2" class="menu" style="display:none"></ul>
</form>
<script>
  // A type-to-search, creatable menu: it filters by the box's text and
  // offers to create it.
  const rows = q => ['Alpha University', 'Beta College']
    .filter(r => r.toLowerCase().includes(q.toLowerCase()) || !q)
    .concat(q ? ['Create "' + q + '"'] : []);
  window.show = (box, menu) => {{
    menu.innerHTML = rows(box.value).map(r => '<li class="option">' + r + '</li>').join('');
    menu.style.display = 'block';
  }};
  for (const [b, m] of [['sch', 'm1'], ['deg', 'm2']]) {{
    const box = document.getElementById(b), menu = document.getElementById(m);
    box.addEventListener('focus', () => show(box, menu));
    box.addEventListener('blur', () => {{ menu.style.display = 'none'; }});
    box.addEventListener('keydown', e => {{ if (e.key === 'Escape') menu.style.display = 'none'; }});
  }}
  document.getElementById('sch').value = '{SENTINEL}';
</script></body></html>"""


def test_text_already_in_a_dropdown_never_reaches_its_options():
    check = Checks()
    form = _write("route_combo.html", COMBO_FORM)
    with mock_page(form) as page:
        page.evaluate("show(document.getElementById('sch'), document.getElementById('m1'))")
        check(any(SENTINEL in o for o in extract.read_open_options(page)),
              "opened, the prefilled box's menu would offer the text (so the checks below mean something)")
    with mock_page(form) as page:
        fields = extract.enrich_comboboxes(page, extract.extract_fields(page, extract.ReadGuard()))
    by = {f["label"]: f for f in fields}
    print(json.dumps([{k: f.get(k) for k in ("label", "options", "note")} for f in fields]))
    check(not by["School"].get("options") and "already holds text" in by["School"].get("note", ""),
          "the box holding text isn't opened, and says why")
    check(by["Degree"].get("options") == ["Alpha University", "Beta College"],
          "an empty box still has its options read")
    check(SENTINEL not in json.dumps(fields), "the text is nowhere in the field list")
    check.done()


DISPLAY_FORM = f"""<!doctype html><html><body><form>
  <label for="loc">Location <div role="button" class="picker">{SENTINEL}-picked</div></label>
  <input id="loc" name="loc">
  <label for="sk">Skills</label>
  <input id="sk" name="skills" aria-describedby="skh">
  <div id="skh">Add skills <span class="MuiChip-root MuiChip-filled">{SENTINEL}-chip</span>
    <span class="tag">{SENTINEL}-tag</span></div>
  <div><div class="label">Country <div data-automation-id="selectedItem">{SENTINEL}-wd</div></div>
       <input name="country"></div>
  <label for="lv">Level <span role="slider" aria-valuenow="3">{SENTINEL}-slider</span>
    <span aria-selected="true">{SENTINEL}-row</span></label>
  <input id="lv" name="level">
</form></body></html>"""


def test_value_displays_in_labels_and_help_are_left_out():
    check = Checks()
    with mock_page(_write("route_display.html", DISPLAY_FORM)) as page:
        fields = extract.extract_fields(page, extract.ReadGuard())
    by = {f.get("dom_name"): f for f in fields}
    print(json.dumps([{k: f.get(k) for k in ("label", "help_text")} for f in fields]))
    check(by["loc"]["label"] == "Location" and by["skills"]["help_text"] == "Add skills"
          and by["country"]["label"] == "Country" and by["level"]["label"] == "Level",
          "each label and help keeps its own words")
    check(SENTINEL not in json.dumps(fields),
          "no role=button, chip, tag, Workday selected item, slider or selected row text got in")
    check.done()


POSTING = """<!doctype html><html><head><title>Data Engineer - Example Co</title></head><body>
  <h1>Data Engineer</h1><p>Pipelines.</p>
  <form><label for="fn">First Name</label><input id="fn" name="first_name"></form>
</body></html>"""


def test_a_title_set_after_apply_never_reaches_the_letter():
    import apply
    from _fill_run import run_fill

    check = Checks()
    letters, real_prepare = [], adapters.prepare

    def prepare(page, key):
        real_prepare(page, key)
        page.evaluate(f"document.title = '{SENTINEL} - Review your answers'")

    def draft(profile, job_text, company="", role=""):
        letters.append((role, company))
        return "A draft."
    form = _write("route_title.html", POSTING)
    text, _, error = run_fill(form, dry_run=False,
                              cover_letter=True,
                              patches=((apply.adapters, "prepare", prepare),
                                       (apply.letters, "draft_cover_letter", draft)))
    check(error is None and letters, f"the letter was drafted ({error})")
    check(letters == [("Data Engineer", "Example Co")],
          f"it got the posting's role and company, not the later title ({letters})")
    row = tracker.find(form.resolve().as_uri()) or {}
    check(row and SENTINEL not in json.dumps(row, default=str),
          "nor is it logged as them in the tracker")
    check.done()


if __name__ == "__main__":
    run_as_script(globals())
