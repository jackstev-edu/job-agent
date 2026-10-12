"""
_harness.py — shared plumbing for the tests in this folder.

WHAT THIS DOES: Soft checks that print ok/FAIL as they go and fail the test at
the end, a helper that opens a mock form in headless Chromium, and a runner so
every test file still works as a plain script as well as under pytest.
WHY IT MATTERS: Run by hand, a test reads as a narrative of what was checked;
under pytest, each test_ function is still one clear pass or fail.
"""
from __future__ import annotations

import sys
from contextlib import contextmanager
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))


class Checks:
    """Collects every failure in a test instead of stopping at the first."""

    def __init__(self):
        self.failures: list[str] = []

    def __call__(self, cond, what: str) -> None:
        print(("  ok   " if cond else "  FAIL ") + what)
        if not cond:
            self.failures.append(what)

    def done(self) -> None:
        assert not self.failures, "failed: " + "; ".join(self.failures)


@contextmanager
def mock_page(html: Path, route=None):
    """A fresh headless page on a local mock form. `route` sees every request."""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            if route:
                page.route("**/*", route)
            page.goto(html.resolve().as_uri())
            yield page
        finally:
            browser.close()


def run_as_script(namespace: dict) -> None:
    """Run a module's test_ functions in order, the way pytest would."""
    tests = [fn for name, fn in namespace.items()
             if name.startswith("test_") and callable(fn)]
    failed = []
    for fn in tests:
        print(f"\n-- {fn.__name__}")
        try:
            fn()
        except AssertionError:
            failed.append(fn.__name__)
    print(f"\n{'ALL PASSED' if not failed else f'{len(failed)} FAILED: ' + ', '.join(failed)}")
    sys.exit(1 if failed else 0)
