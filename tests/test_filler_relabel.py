"""
test_filler_relabel.py — a checkbox that now asks a different question is never ticked.

WHAT THIS DOES: Scans checkboxes, then relabels them the way a page could
between the scan and the click: one legal question into another of the
same kind (authorized -> sponsorship, veteran -> disability), a legal one
into an ordinary one, a box that keeps its own aria-label while the nearby
label turns into terms, a terms label split by a zero-width space, a name
changed to a camelCase legal word. None may be ticked; an unchanged
authorization box with a true profile line still is. A dropdown row inside
a typeless button is refused. And a neighbouring label picked up by the
nearby fallback sends a box to you (an accepted false positive).
WHY IT MATTERS: The policy answered the question the scan saw. A tick on a
different question puts that answer somewhere it was never checked.

Run from anywhere:  .venv/Scripts/python.exe tests/test_filler_relabel.py
"""
from pathlib import Path

from _harness import TEST_HOME, Checks, mock_page, run_as_script
from agent import extract, filler

ZWSP = chr(0x200B)

BOXES = """<!doctype html><html><body><form>
  <div><label><input type="checkbox" name="c1" id="c1">
    <span id="t1">I am legally authorized to work in the US</span></label></div>
  <div><label><input type="checkbox" name="c2" id="c2">
    <span id="t2">I am a protected veteran</span></label></div>
  <div><label><input type="checkbox" name="c3" id="c3">
    <span id="t3">I am authorized to work in the US</span></label></div>
  <div><label><input type="checkbox" name="c4" id="c4">
    <span id="t4">I am authorized to work in the US</span></label></div>
  <div><div class="label">I agree to the Terms and Conditions</div>
    <input type="checkbox" aria-label="Email me news" name="c5" id="c5"></div>
  <div><label><input type="checkbox" name="c6" id="c6">
    <span id="t6">Email me news</span></label></div>
  <div><label><input type="checkbox" name="news" id="c7">
    <span id="t7">Send me offers</span></label></div>
  <div><label><input type="checkbox" name="c8" id="c8">
    <span id="t8">I am a protected veteran</span></label></div>
  <div><label><input type="checkbox" name="c9" id="c9">
    <span id="t9">I will require sponsorship</span></label></div>
  <div><label><input type="checkbox" name="offers" id="c10">
    <span id="t10">Send me offers</span></label></div>
</form></body></html>"""


def _write(name: str, html: str) -> Path:
    path = TEST_HOME / name
    path.write_text(html, encoding="utf-8")
    return path


def test_a_box_relabelled_after_the_scan_is_not_ticked():
    check = Checks()
    with mock_page(_write("relabel.html", BOXES)) as page:
        fields = {f.get("dom_id"): f for f in extract.extract_fields(page, extract.ReadGuard())}
        page.evaluate(f"""() => {{
          const t = (id, s) => {{ document.getElementById(id).textContent = s; }};
          t('t1', 'I will require visa sponsorship');
          t('t2', 'I have a disability');
          t('t3', 'Subscribe to marketing');
          t('t6', 'Terms{ZWSP}and Conditions');
          document.getElementById('c7').setAttribute('name', 'requiresSponsorship');
          t('t8', 'I am a protected veteran and I have a disability');
          t('t9', 'I will require sponsorship and am authorized to work in the US');
          document.getElementById('c10').setAttribute('name', 'backgroundCheck');
        }}""")
        results = {i: filler.fill_one(page, fields[i], True) for i in
                   ("c1", "c2", "c3", "c4", "c5", "c6", "c7", "c8", "c9", "c10")}
        ticked = {i: page.is_checked(f"#{i}") for i in results}
    for i, (ok, detail) in results.items():
        print(f"  {i}: {ok} {detail} ticked={ticked[i]}")
    # (box, what happened to it, which refusal must fire)
    cases = {
        "c1": ("'legally authorized' relabelled to 'visa sponsorship' (same kind)",
               "different question"),
        "c2": ("'protected veteran' relabelled to 'I have a disability' (same kind)",
               "different question"),
        "c3": ("an authorization box relabelled to 'Subscribe to marketing'",
               "different question"),
        "c5": ("its own aria-label kept, the nearby .label now terms", "terms or consent"),
        "c6": ("relabelled to 'Terms<zwsp>and Conditions' (only the spaced reading matches)",
               "terms or consent"),
        "c7": ("name changed to 'requiresSponsorship' after the scan", "different question"),
        "c8": ("'protected veteran' relabelled to '... and I have a disability' (a line added "
               "further down the table)", "different question"),
        "c9": ("'require sponsorship' relabelled to '... and am authorized to work'",
               "different question"),
        "c10": ("name changed to 'backgroundCheck' after the scan", "terms or consent"),
    }
    for i, (what, branch) in cases.items():
        check(not results[i][0] and branch in results[i][1] and not ticked[i],
              f"{what}: refused ({branch}), not ticked")
    check(results["c4"][0] and ticked["c4"],
          "an unchanged authorization box (the policy's true line) is still ticked")
    check.done()


ROW_IN_BUTTON = """<!doctype html><html><body><form>
  <label for="co">Country</label>
  <input id="co" name="country" role="combobox" aria-autocomplete="list">
  <div class="menu"><button><div class="option">Canada</div></button>
    <button><div class="option">Mexico</div></button></div>
</form><script>
  window.__submits = 0;
  document.addEventListener('submit', e => { e.preventDefault(); window.__submits++; }, true);
</script></body></html>"""


def test_a_row_inside_a_typeless_button_is_refused():
    check = Checks()
    with mock_page(_write("row_in_button.html", ROW_IN_BUTTON)) as page:
        fields = extract.extract_fields(page, extract.ReadGuard())
        country = next(f for f in fields if f["label"] == "Country")
        country["options"] = ["Canada", "Mexico"]
        ok, detail = filler.fill_one(page, country, "Canada")
        submits, value = page.evaluate("window.__submits"), page.input_value("#co")
    print(f"  -> {ok} {detail}")
    check(not ok and "could submit" in detail and submits == 0 and value == "",
          "a row sitting inside a typeless button: refused, left empty, nothing sent")
    check.done()


NEIGHBOURS = """<!doctype html><html><body><form>
  <label><input type="checkbox" name="v" id="v"> I am a protected veteran</label>
  <label><input type="checkbox" name="a" id="a"> I am authorized to work in the US</label>
</form></body></html>"""


def test_a_neighbouring_label_sends_a_box_to_you():
    # The accepted false positive: both labels are direct children of the
    # form, so the always-on nearby fallback reads the form's first label
    # ("protected veteran") for the authorization box too.
    check = Checks()
    with mock_page(_write("neighbours.html", NEIGHBOURS)) as page:
        fields = {f.get("dom_id"): f for f in extract.extract_fields(page, extract.ReadGuard())}
        ok, detail = filler.fill_one(page, fields["a"], True)
        ticked = page.is_checked("#a")
    print(f"  -> {ok} {detail}")
    check(not ok and "different question" in detail and not ticked,
          "the authorization box comes to you, unticked (a false positive, kept)")
    check.done()


def test_a_box_touching_two_profile_lines_comes_to_you_at_the_scan():
    from agent import sensitive

    class _Profile:
        def get(self, path):
            return {"eeo.veteran_status": False, "eeo.disability_status": False}.get(path)
    check = Checks()
    field = {"label": "I am a protected veteran and I have a disability", "type": "checkbox"}
    check(len(sensitive.all_profile_lines(field["label"])) == 2,
          "the label matches two profile lines (so the next check means something)")
    v = sensitive.decide(field, "eeo", _Profile())
    check(v["action"] == "human" and "more than one profile line" in v.get("reason", ""),
          f"a combined veteran + disability box comes to you ({v['action']})")
    check.done()


if __name__ == "__main__":
    run_as_script(globals())
