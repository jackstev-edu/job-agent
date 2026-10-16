"""
test_follow_steps.py — telling a new step from a re-render, without reading page text.

WHAT THIS DOES: Feeds StepWatcher synthetic shape reads (unsettled pages, a
new address, a follow-up question revealed, a step revisited, a re-render
after the agent filled), then drives a mock wizard in a real page: step 2
arrives by a change of address, step 3 by swapping the form in place, then a
validation-style re-render re-mounts step 3's fields, and a page still
rendering never counts as settled. The test, standing in for you, does
every click; the watcher only reads.
WHY IT MATTERS: Follow-along mode fills each step as it appears. Acting on a
page mid-render, or treating a re-render as a new step, would fill the
wrong fields or fill twice.

Run from anywhere:  .venv/Scripts/python.exe tests/test_follow_steps.py
"""
import time

from _harness import Checks, mock_page, run_as_script, TEST_HOME
from agent import extract, follow

F1 = (("input", "text", "first", "", "", "", ""), ("input", "email", "email", "", "", "", ""))
F2 = (("input", "text", "city", "", "", "", ""), ("select", "select-one", "country", "", "", "", ""))


def shape(url="a.example/apply", fields=F1, quiet=1000, stamped=0):
    return {"url": url, "fields": tuple(sorted(fields)), "count": len(fields),
            "stamped": stamped, "quiet_ms": quiet}


def test_step_watcher_rules():
    check = Checks()
    w = follow.StepWatcher()
    check(w.observe(shape()) is None, "one read is never enough")
    check(w.observe(shape(quiet=100)) is None, "two reads agree, but the page moved 100 ms ago")
    check(w.observe(shape()) == ("new_step", 1), "settled: step 1")
    check(w.observe(shape()) is None, "the same settled step reports nothing more")
    check(w.observe(shape(stamped=0)) is None, "fields unstamped before the agent filled: not a re-render")
    w.filled()
    check(w.observe(shape(stamped=2)) is None, "after filling, all fields stamped: nothing")
    check(w.observe(shape(stamped=0)) == ("rerender", 1), "stamps gone from the same fields: rerender")
    check(w.observe(shape(stamped=0)) is None, "  ...reported once")
    w.filled()
    check(w.observe(shape(stamped=1)) == ("rerender", 1), "  ...and again after the next fill")

    grown = F1 + (("input", "text", "followup", "", "", "", ""),)
    check(w.observe(shape(fields=grown)) is None and w.observe(shape(fields=grown)) == ("changed", 1),
          "same address, a follow-up field revealed: the same step, changed")
    check(w.observe(shape(url="a.example/apply/2", fields=F2)) is None
          and w.observe(shape(url="a.example/apply/2", fields=F2)) == ("new_step", 2),
          "a new address with new fields: step 2")
    check(w.observe(shape(fields=F2)) is None and w.observe(shape(fields=F2)) == ("new_step", 3),
          "same address as step 1 but no fields in common: a new step")
    check(w.observe(shape()) is None and w.observe(shape()) == ("revisit", 1),
          "back on step 1's address and fields: a revisit, numbered 1")

    w = follow.StepWatcher()
    w.observe(shape()); w.observe(shape(fields=F2))
    check(w.observe(shape(fields=F1)) is None, "fields still changing between reads: not settled")
    check(w.observe(None) is None and w.observe(shape()) is None,
          "an unreadable read (mid-navigation) resets the count")
    check(w.observe(shape(fields=())) is None and w.observe(shape(fields=())) is None,
          "a page with no fields is never a step")
    check(follow.url_key("https://A.example/apply?x=1#step2") == "a.example/apply?x=1",
          "the address ignores the #fragment and the host's case")
    check.done()


WIZARD = """<!doctype html><html><body>
<div id="app"></div>
<button id="next" type="button">Next</button>
<script>
  const STEPS = {
    1: '<label for="fn">First name</label><input id="fn" name="first">'
     + '<label for="em">Email</label><input id="em" name="email" type="email">',
    2: '<label for="ci">City</label><input id="ci" name="city">'
     + '<label for="co">Country</label><select id="co" name="country"><option>A</option></select>',
    3: '<label for="yr">Years of experience</label><input id="yr" name="years" type="number">'
     + '<label for="ab">About you</label><textarea id="ab" name="about"></textarea>',
  };
  const step = Number(new URLSearchParams(location.search).get('step') || 1);
  const render = n => { document.getElementById('app').innerHTML = STEPS[n]; window.__step = n; };
  render(step);
  // Kept across the navigation to step 2, so every click on Next is counted.
  document.getElementById('next').addEventListener('click', () => {
    sessionStorage.clicks = Number(sessionStorage.clicks || 0) + 1;
    if (window.__step === 1) location.search = '?step=2';          // a new address
    else render(3);                                                // swapped in place
  });
  // A validation error: React re-mounts the same fields as fresh nodes.
  window.rerender = () => render(window.__step);
  // A page still rendering: nodes keep arriving for a while.
  window.churn = ms => {
    const end = performance.now() + ms;
    const t = setInterval(() => {
      const s = document.createElement('span'); document.body.appendChild(s); s.remove();
      if (performance.now() > end) clearInterval(t);
    }, 50);
  };
</script></body></html>"""


def _routes():
    def handle(route):
        route.fulfill(status=200, content_type="text/html", body=WIZARD)
    return {"https://jobs.example.test/**": handle}


def _until_event(page, w, timeout_s=8.0, poll_s=0.3):
    end = time.monotonic() + timeout_s
    while time.monotonic() < end:
        ev = w.observe(follow.read_shape(page))
        if ev:
            return ev
        page.wait_for_timeout(int(poll_s * 1000))
    return None


def test_steps_on_a_real_page():
    check = Checks()
    blank = TEST_HOME / "blank.html"
    blank.write_text("<!doctype html><p>start</p>", encoding="utf-8")
    with mock_page(blank, _routes()) as page:
        page.goto("https://jobs.example.test/apply")
        w = follow.StepWatcher()
        check(_until_event(page, w) == ("new_step", 1), "step 1 seen once settled")
        extract.extract_fields(page)          # what filling does first: stamp the fields
        w.filled()
        check(_until_event(page, w, timeout_s=1.5) is None, "nothing more while you're on step 1")

        page.click("#next")                    # you
        check(_until_event(page, w) == ("new_step", 2), "a new address: step 2")
        extract.extract_fields(page); w.filled()
        page.click("#next")                    # you
        ev = _until_event(page, w)
        check(ev == ("new_step", 3) and page.url.endswith("?step=2"),
              f"fields swapped in place, same address: step 3 ({ev})")
        extract.extract_fields(page); w.filled()

        page.evaluate("window.rerender()")
        check(_until_event(page, w) == ("rerender", 3), "the same fields re-mounted: a re-render of step 3")
        extract.extract_fields(page); w.filled()          # the agent handles it

        page.evaluate("window.churn(2500)")
        page.evaluate("window.rerender()")
        started = time.monotonic()
        ev = _until_event(page, w, timeout_s=8)
        waited = time.monotonic() - started
        check(ev == ("rerender", 3) and waited >= 2.0,
              f"a page still rendering isn't settled: waited {waited:.1f}s for it to stop")
        check(page.evaluate("Number(sessionStorage.clicks)") == 2, "only your two clicks on Next; the watcher clicked nothing")
    check.done()


if __name__ == "__main__":
    run_as_script(globals())
