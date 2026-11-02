"""
test_clobbering.py — the page scripts survive a form whose controls clobber its properties.

WHAT THIS DOES: Opens a generic form whose inputs are named id, name,
action, method, type, tagName, getAttribute, hasAttribute, matches,
closest, parentElement, classList and attributes (a <form> answers each
of those properties with the control of that name, not the attribute or
the method), and runs the page scripts on it: snapshot.dump, the extract
scan and page_text, pages.page_kind, follow.read_shape, submit's finder,
accounts' box scans and the activity listener's touch marks. Each runs
without error and reports the real attributes.
WHY IT MATTERS: Jack's live test of 2026-11-01 (evening): apply.py
snapshot crashed on a posting page ("el.id.slice is not a function" in
tagOf / chainOf) because the form there holds a field named "id". Item 1
of that round: every injected script reads id, name, type, action,
method and tagName through the prototypes' own getters, called on the
element, never through el.id and the like.

Run from anywhere:  .venv/Scripts/python.exe tests/test_clobbering.py
"""
import json

from _harness import REPO, Checks, mock_page, run_as_script
from agent import accounts, extract, follow, ledger, pages, snapshot, submit

FIXTURE = REPO / "tests" / "clobbered_form.html"
CLOBBERED = ["id", "name", "action", "method", "type", "tagName", "getAttribute", "hasAttribute", "matches",
             "closest", "parentElement", "classList", "attributes"]


def test_snapshot_dump_survives_a_clobbered_form():
    # Fails on 1e4aac9: Page.evaluate: TypeError: el.id.slice is not a function (tagOf, chainOf).
    check = Checks()
    with mock_page(FIXTURE) as page:
        got = snapshot.dump(page, {"personal": {"first_name": "Nobody"}})
    names = [e.get("name") for e in got["elements"] if e.get("tag") == "input"]
    check(all(n in names for n in CLOBBERED), f"every clobbering input is dumped by its real name ({names})")
    by_name = {e.get("name"): e for e in got["elements"] if e.get("tag") == "input"}
    check(by_name["id"]["id"] == "f-id" and by_name["id"]["label"] == "Employee id (if any)"
          and by_name["tagName"]["type"] == "text", "an input's own id, label and type are read")
    chain = by_name["id"]["chain"]
    check(any(c.startswith("form#apply-form") and "data-automation-id=applyForm" in c and "[role=form]" in c for c in chain),
          f"the form appears in the chain by its real id, automation id and role ({chain})")
    check(by_name["resume"]["type"] == "file" and got["frames"] == 0, "the file box and the frame count")
    check.done()


def test_the_scan_and_page_text_survive_a_clobbered_form():
    # Fails on 1e4aac9: page_text walked a text node up to the form and called
    # el.closest on it (the input named "closest" answers instead).
    check = Checks()
    with mock_page(FIXTURE) as page:
        fields = extract.extract_fields(page)
        text = extract.page_text(page)
        census = extract.checkbox_census(page)
        frames = extract.frames(page) if hasattr(extract, "frames") else []
    names = {f.get("dom_name") or f.get("name") for f in fields}
    check(all(n in names for n in CLOBBERED), f"every clobbering input is scanned by its real name ({sorted(names)})")
    by = {(f.get("dom_name") or f.get("name")): f for f in fields}
    check(by["name"]["label"] == "Full name" and by["name"]["required"] is True and by["name"]["type"] == "text"
          and by["name"]["dom_id"] == "f-name", f"label, required, type and id read as written ({by['name']})")
    check(by["resume"]["type"] == "file" and by["notes"]["type"] == "textarea" and by["available"]["type"] == "checkbox",
          "the file box, the textarea and the checkbox keep their kinds")
    check("Some words about the role." in text and "Software Engineer" in text, f"page text is read ({text[:80]!r})")
    check(len(census) == 1 and census[0].get("label_inner", "").startswith("Available"), f"the checkbox census ({census})")
    check(frames == [], "no frames")
    check.done()


def test_page_kind_reads_it_as_a_plain_page():
    # Passes on 1e4aac9 too: _KIND_JS reads inputs only, which a form can't
    # clobber. A guard that the rewritten script still reads the fixture as
    # a plain page, not an account page (the strictest, "unreadable" answer).
    check = Checks()
    with mock_page(FIXTURE) as page:
        kind = pages.page_kind(page)
    check(kind is None, f"a plain page ({kind})")
    check.done()


def test_shape_submit_finder_account_scans_and_touch_marks_survive():
    # Guards: these scripts read inputs and buttons, which a form can't
    # clobber, so they pass on 1e4aac9 too; the check is that the rewritten
    # scripts still read the fixture as before. (Playwright's own click
    # walks the form with element.getAttribute and throws here, so the box
    # is focused in the page and typed into with the keyboard.)
    check = Checks()
    with mock_page(FIXTURE) as page:
        shape = follow.read_shape(page)
        found = submit.find_submit(page)
        pw = page.evaluate(accounts._PASSWORD_BOXES_JS, accounts._PW_ATTR)
        texts = page.evaluate(accounts._TEXT_BOXES_JS, accounts._PW_ATTR)
        fields = extract.extract_fields(page)
        ref = next(f["ref"] for f in fields if (f.get("dom_name") or f.get("name")) == "id")
        page.evaluate(ledger.ACTIVITY_INIT_SCRIPT)
        page.evaluate("() => document.getElementById('f-id').focus()")
        page.keyboard.type("x")
        marked = page.evaluate(ledger.TOUCHED_JS, [[ref], ledger.REVIEW_ATTR])
    dumped = json.dumps(shape)
    check(shape is not None and all(n in dumped for n in CLOBBERED), f"the shape names every input ({dumped[:200]})")
    check(found.get("chosen") is True and "submit" in (found.get("word") or "").lower(),
          f"the Submit button is found by its text ({found})")
    check(pw == [] and isinstance(texts, list) and len(texts) >= 13, f"no password box; the text boxes are listed ({len(texts)})")
    check(marked == [ref], f"Jack's keystroke marks the clobbered-name box ({marked})")
    check.done()


if __name__ == "__main__":
    run_as_script(globals())
