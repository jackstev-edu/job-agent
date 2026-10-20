"""
test_remap.py — the re-scan on resume carries nothing to the wrong box.

WHAT THIS DOES: Drives `Wizard._remap` and `Wizard._after_pause` on a
small mock page after another scan made the page's refs stale: two old
fields of one identity never both land on one new field (neither is
paired, and the review one of them had is dropped, not inherited); a pair whose
old-scan flags (raw_stop here) differ from the new scan's is refused; and
a ticked field whose value the resume snapshot could not read is not
counted as "still holding" the shown value, even when that value was "".
An unpaired field also loses its fill-time read-back, result, review,
census box and approval, so the gate judges its box as unknown, not as
read before (the gate's own verdict is checked: not filled, and required
but empty); a re-scan that fails leaves every field unpaired.
WHY IT MATTERS: The refs are the only link between a review Jack gave and
the box it was for; a loose match could waive the gate's stops on a field
he never saw (reviewer on a05bc54).

Run from anywhere:  .venv/Scripts/python.exe tests/test_remap.py
"""
from unittest import mock

from _fill_run import _headless
from _harness import Checks, run_as_script
from agent import extract, gate, pause, pipeline, shadow, wizard
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


ONE_REQUIRED = ONE.replace('<input name="extra">', '<input name="extra" required>')


def _filled_fill(w):
    """A fill whose two fields were planned, filled and read back, as a real step records them."""
    fields = extract.extract_fields(w.page, w.guard)
    plan = {f["ref"]: {"value": "kept" if f.get("name") == "extra" else "Test", "source": "profile",
                       "confidence": "high"} for f in fields}
    cats = {f["ref"]: "personal_fact" for f in fields}
    rows = [{"filled": True, "detail": "", "unconfirmed": False} for _ in plan]
    step = shadow.step_entry(fields, cats, plan, [], rows)
    fill = pipeline.PageFill(fields=fields, categories=cats, plan=plan, flags=[], rows=rows, step=step)
    fill.snapshot = {ref: d["value"] for ref, d in plan.items()}
    w.snapshots = dict(fill.snapshot)
    w.results = dict(step["results"])
    return fill


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
        check(w.human_reviewed == [], f"the review is dropped with the unpaired field ({w.human_reviewed})")
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


def test_an_unpaired_field_loses_its_read_back_result_and_review():
    check = Checks()
    with _headless(_routes(ONE_REQUIRED)) as ctx:
        page = ctx.new_page(); page.goto(f"{HOST}/form")
        w = _wizard(page); fill = _filled_fill(w)
        extra = next(f for f in fill.fields if f.get("name") == "extra")
        fn = next(f for f in fill.fields if f.get("name") == "first_name")
        old_extra, old_fn = extra["ref"], fn["ref"]
        w.human_reviewed = [old_extra, old_fn]
        w.census = {old_extra: {"ref": old_extra, "checked": False}, old_fn: {"ref": old_fn, "checked": False}}
        pending = {"marked_before": {old_extra, old_fn}}
        info = {"approved": {old_extra: "kept", old_fn: "Test"}}
        # A second "Extra" box appears: the old one now has two candidates.
        page.evaluate("document.querySelector('input[name=extra]').closest('label')"
                      ".insertAdjacentHTML('afterend', '<label>Extra <input name=\"extra\"></label>')")
        _stale(ctx)
        w._remap(fill, pending, info)
        new_fn = fn["ref"]
        check(extra["ref"] == old_extra and extract.is_current(new_fn) and new_fn != old_fn,
              "the extra box is unpaired; first name is remapped")
        check(old_extra not in w.snapshots and old_extra not in fill.snapshot,
              f"its fill-time read-back is gone ({w.snapshots}, {fill.snapshot})")
        check(fill.step["results"][old_extra] == {"filled": False} and w.results[old_extra] == {"filled": False},
              f"its result says not filled ({fill.step['results']})")
        check(w.human_reviewed == [new_fn], f"its review is gone; first name's moved ({w.human_reviewed})")
        check(old_extra not in w.census and new_fn in w.census, f"its census box is gone; first name's moved ({w.census})")
        check(pending["marked_before"] == {old_extra, new_fn},
              f"the stale mark stays in 'before' (it can only shrink what counts as touched) ({pending})")
        check(info["approved"] == {new_fn: "Test"}, f"its approval is gone; first name's moved ({info})")
        check(w.snapshots.get(new_fn) == "Test" and fill.step["results"][new_fn]["filled"] is True,
              "first name's read-back and result moved with it")
        verdict = gate.decide(w._page_record(fill, []), w.prof)
        reasons = " | ".join(verdict.get("content_reasons") or [])
        check(verdict.get("content_verdict") == gate.WOULD_STOP and "'Extra': filling it failed" in reasons
              and "'Extra': required, but empty" in reasons,
              f"the gate stops on the unpaired box: not filled, and required but empty ({reasons})")
    check.done()


def test_a_failed_re_scan_leaves_every_field_unpaired():
    check = Checks()
    with _headless(_routes(ONE)) as ctx:
        page = ctx.new_page(); page.goto(f"{HOST}/form")
        w = _wizard(page); fill = _filled_fill(w)
        refs = [f["ref"] for f in fill.fields]
        w.human_reviewed = list(refs)
        _stale(ctx)
        with mock.patch.object(extract, "extract_fields", side_effect=RuntimeError("the page went away")):
            w._remap(fill, {}, {})
        check([f["ref"] for f in fill.fields] == refs and not any(extract.is_current(r) for r in refs),
              "every field keeps its stale ref")
        check(not w.snapshots and not fill.snapshot and w.human_reviewed == []
              and all(fill.step["results"][r] == {"filled": False} for r in refs),
              f"and every read-back, review and result is gone ({w.snapshots}, {w.human_reviewed}, {fill.step['results']})")
    check.done()


if __name__ == "__main__":
    run_as_script(globals())
