"""
test_search_dropdown.py — a type-to-search dropdown takes only an exact match.

WHAT THIS DOES: A mock school field in the react-select shape (an input
with role=combobox, rows fetched 400 ms after typing, filtered by the
text, a .single-value display once a row is chosen) is filled through
filler.fill_one: the profile value as written is chosen; so is one that
differs only in case; a value the rows only start with, one that every
row contains, and one a single row contains are all left blank with the
reason, and the box holds no leftover text; a value the search knows
nothing about ("No results") is left blank too. The scan marks the box
search_required because it lists nothing until text is typed.
WHY IT MATTERS: Jack's first GUI run left Greenhouse's school field
blank, and the old matcher could also have taken the first or closest
row of a search; now only a row equal to the profile value counts (Jack,
2026-10-20).

Run from anywhere:  .venv/Scripts/python.exe tests/test_search_dropdown.py
"""
from pathlib import Path

from _harness import TEST_HOME, Checks, mock_page, run_as_script
from agent import extract, filler, matching

SCHOOLS = ["Example University", "Example University of Technology", "University of Example",
           "Example Community College"]

FORM = """<!doctype html><html><body><form>
<label for="school">School</label>
<div class="control"><input id="school" name="school" role="combobox" aria-autocomplete="list" autocomplete="off"></div>
<div id="menu" role="listbox" style="display:none"></div>
<label for="city">City</label><input id="city" name="city">
<button type="submit">Submit application</button></form>
<script>
const SCHOOLS = SCHOOLS_JSON;
const input = document.getElementById('school'), menu = document.getElementById('menu');
let timer;
window.__submits = 0; window.__enters = 0;
document.addEventListener('submit', e => { e.preventDefault(); window.__submits++; }, true);
document.addEventListener('keydown', e => { if (e.key === 'Enter') window.__enters++; }, true);
input.addEventListener('input', () => {
  clearTimeout(timer); menu.style.display = 'none'; menu.innerHTML = '';
  const q = input.value.toLowerCase(); if (!q) return;
  timer = setTimeout(() => {
    const rows = SCHOOLS.filter(s => s.toLowerCase().includes(q));
    menu.innerHTML = rows.length ? rows.map(s => '<div role="option">' + s + '</div>').join('')
                                 : '<div role="option" class="no">No results</div>';
    menu.style.display = 'block'; }, 400); });
menu.addEventListener('mousedown', e => {
  const row = e.target.closest('[role=option]'); if (!row || row.classList.contains('no')) return;
  const control = input.closest('.control');
  control.querySelector('.single-value')?.remove();
  const sv = document.createElement('span'); sv.className = 'single-value'; sv.textContent = row.innerText;
  control.appendChild(sv); input.value = ''; menu.style.display = 'none'; window.__picked = row.innerText; });
input.addEventListener('keydown', e => { if (e.key === 'Escape') menu.style.display = 'none'; });
</script></body></html>""".replace("SCHOOLS_JSON", str(SCHOOLS))


def _page():
    path = TEST_HOME / "search_dropdown.html"
    path.write_text(FORM, encoding="utf-8")
    return path


def _school(page):
    fields = extract.enrich_comboboxes(page, extract.extract_fields(page, extract.ReadGuard()))
    return next(f for f in fields if f.get("label") == "School")


def _state(page):
    return (page.evaluate("window.__picked || null"), page.input_value("#school"),
            page.evaluate("window.__submits"), page.evaluate("window.__enters"))


def test_the_scan_marks_a_search_box_and_the_exact_value_is_chosen():
    check = Checks()
    with mock_page(_page()) as page:
        field = _school(page)
        check(field.get("type") == "combobox" and field.get("search_required") is True and not field.get("options"),
              f"a box that lists nothing until typed into is search_required ({field.get('type')}, {field.get('search_required')})")
        ok, detail = filler.fill_one(page, field, "Example University")
        picked, left, submits, enters = _state(page)
        check(ok and detail == "selected 'Example University'" and picked == "Example University",
              f"the row equal to the value is chosen and shown as kept ({ok}, {detail}, {picked})")
        check(submits == 0 and enters == 0, "no Enter, nothing sent")
    with mock_page(_page()) as page:
        field = _school(page)
        ok, detail = filler.fill_one(page, field, "example university")
        picked = _state(page)[0]
        check(ok and picked == "Example University" and detail == "selected 'Example University'",
              f"a value differing only in case is the same row ({ok}, {detail}, {picked})")
    check.done()


def test_anything_but_an_exact_row_is_left_blank_with_the_reason():
    check = Checks()
    for value, why in (("Example Univ", "the rows only start with it"),
                       ("University", "every row contains it"),
                       ("Technology", "one row contains it"),
                       ("Example", "three rows start with it")):
        with mock_page(_page()) as page:
            field = _school(page)
            ok, detail = filler.fill_one(page, field, value)
            picked, left, submits, enters = _state(page)
            check(not ok and "no exact match" in detail and picked is None and left == "",
                  f"'{value}' ({why}): nothing chosen, the box left blank, the reason given ({ok}, {detail}, {picked}, {left!r})")
            check(submits == 0 and enters == 0, f"'{value}': no Enter, nothing sent")
    with mock_page(_page()) as page:
        field = _school(page)
        ok, detail = filler.fill_one(page, field, "Nowhere College")
        picked, left, submits, enters = _state(page)
        check(not ok and "found no match" in detail and picked is None and left == "",
              f"a value the search doesn't know: left blank ({ok}, {detail}, {left!r})")
    for value, options, want in (("Example University", SCHOOLS, "Example University"),
                                 ("example  university!", SCHOOLS, "Example University"),
                                 ("Example Univ", SCHOOLS, None), ("University", SCHOOLS, None),
                                 ("Technology", SCHOOLS, None), ("", SCHOOLS, None), (None, SCHOOLS, None),
                                 ("Example University", [], None),
                                 ("X", ["X", "x"], "X"), ("x", ["X", "X "], None)):
        got, how = matching.exact_match(value, options)
        check(got == want, f"exact_match({value!r}) -> {want} ({got}, {how})")
    check.done()


if __name__ == "__main__":
    run_as_script(globals())
