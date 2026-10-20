"""
test_remap.py — the re-scan on resume carries nothing to the wrong box.

WHAT THIS DOES: Drives `Wizard._remap` and `Wizard._after_pause` on a
small mock page after another scan made the page's refs stale: two old
fields of one identity never both land on one new field (neither is
paired, so the review one of them had is not inherited); a pair whose
old-scan flags (raw_stop here) differ from the new scan's is refused; and
a ticked field whose value the resume snapshot could not read is not
counted as "still holding" the shown value, even when that value was "".
WHY IT MATTERS: The refs are the only link between a review Jack gave and
the box it was for; a loose match could waive the gate's stops on a field
he never saw (reviewer on a05bc54).

Run from anywhere:  .venv/Scripts/python.exe tests/test_remap.py
"""
from _fill_run import _headless
from _harness import Checks, run_as_script
from agent import extract, pause, pipeline, wizard
from test_wizard import HOST, _profile

TWO = """<!doctype html><html><head><title>Form</title></head><body><form>
<label for="fn">First Name</label><input id="fn" name="first_name">
<label>Extra <input name="extra"></label>
<label>Extra <input name="extra"></label>
<button type="submit">Submit application</button></form></body></html>"""

ONE = """<!doctype html><html><head><title>Form</title></head><body><form>
<label for="fn">First Name</label><input id="fn" name="first_name">
<label>Extra <input name="extra"></label>
<button type="submit">Submit application</button></form></body></html>"""


def _routes(html):
    return {f"{HOST}/**": lambda route: route.fulfill(content_type="text/html", body=html)}


def _wizard(page):
    pauser = pause.GuiPauser(notify_fn=lambda t, b, echo=print: None, echo=lambda *a, **k: None)
    return wizard.Wizard(page, _profile(), url=f"{HOST}/form", portal="generic", portal_method="default",
                         company="Example Co", role="Engineer", resume=None, posting_text="",
                         pauser=pauser, echo=lambda *a, **k: None)


def _fill(w):
    fields = extract.extract_fields(w.page, w.guard)
    step = {"fields": [], "plan": {}, "categories": {}, "flags": []}
    return pipeline.PageFill(fields=fields, categories={}, plan={}, flags=[], rows=[], step=step)


def _stale(ctx):
    """Another job's scan: every earlier ref is now stale."""
    other = ctx.new_page()
    other.set_content("<form><label>Other <input name='other'></label></form>")
    extract.extract_fields(other, extract.ReadGuard())
    other.close()


def test_two_old_fields_of_one_identity_never_share_a_new_field():
    check = Checks()
    with _headless(_routes(TWO)) as ctx:
        page = ctx.new_page(); page.goto(f"{HOST}/form")
        w = _wizard(page); fill = _fill(w)
        extras = [f for f in fill.fields if f.get("name") == "extra"]
        check(len(extras) == 2 and w._ident(extras[0]) == w._ident(extras[1]), "two fields share one identity")
        old_refs = [f["ref"] for f in extras]
        w.human_reviewed = [old_refs[0]]
        page.evaluate("document.querySelectorAll('input[name=extra]')[1].closest('label').remove()")
        _stale(ctx)
        w._remap(fill, {}, {})
        now = [f["ref"] for f in fill.fields if f.get("name") == "extra"]
        fn = next(f for f in fill.fields if f.get("name") == "first_name")
        check(now == old_refs and not any(extract.is_current(r) for r in now),
              f"neither old field was paired; both keep their stale refs ({now})")
        check(w.human_reviewed == [old_refs[0]], f"the review stays on its stale ref ({w.human_reviewed})")
        check(extract.is_current(fn["ref"]), "the one-to-one field was remapped")
    check.done()


def test_a_pair_whose_scan_flags_differ_is_refused():
    check = Checks()
    with _headless(_routes(ONE)) as ctx:
        page = ctx.new_page(); page.goto(f"{HOST}/form")
        w = _wizard(page); fill = _fill(w)
        fn = next(f for f in fill.fields if f.get("name") == "first_name")
        check(not fn.get("raw_stop"), "the field carries no raw stop in the page")
        fn["raw_stop"] = True                    # what an earlier scan had judged
        stale_ref = fn["ref"]
        _stale(ctx)
        w._remap(fill, {}, {})
        check(fn["ref"] == stale_ref and not extract.is_current(fn["ref"]),
              "a field the scans flag differently keeps its stale ref")
    check.done()


def test_a_ticked_value_the_resume_could_not_read_is_not_held():
    check = Checks()
    with _headless(_routes(ONE)) as ctx:
        page = ctx.new_page(); page.goto(f"{HOST}/form")
        w = _wizard(page); fill = _fill(w)
        fn = next(f for f in fill.fields if f.get("name") == "first_name")
        _stale(ctx)                              # the ref can't be read now
        w._after_pause(fill, set(), {fn["ref"]: ""})
        check(fn["ref"] not in w.human_reviewed, "an unread field is not 'still holding' the shown value")
        check(w.out.events[-1]["detail"].endswith("0 approved in the GUI"), "the event counts no approval")
    check.done()


if __name__ == "__main__":
    run_as_script(globals())
