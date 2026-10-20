"""
conftest.py — pytest's first import in this folder, and the browser marker.

WHAT THIS DOES: Imports _harness before any test module, so JOB_AGENT_HOME
already points at a throwaway folder when the first agent module loads;
then marks every test that launches Playwright's Chromium `browser`, read
off the test's own source and the helpers it calls (in its own module or
imported from another test module, followed to a fixpoint), so
`-m "not browser"` runs the rest in under a minute.
WHY IT MATTERS: Under pytest every test shares one process, and config reads
JOB_AGENT_HOME only once; whichever test imported it first would otherwise
decide whether the rest write into your real ~/.job-agent. And Jack's
test-run rule (2026-10-20, second set) is: per commit, the new or changed
test files plus the non-browser tests; the full suite once before each
push. The split needs one marker, kept here, not a list to maintain.
"""
import inspect
import re
import sys

import pytest

import _harness  # noqa: F401

# A name whose presence in a callable's source means a browser is launched:
# the harness's page factories and the shared fill runner.
BROWSER_TOKENS = ("mock_page", "sync_playwright", "_headless", "run_fill", "_fill_run",
                  "launch_persistent", "chromium.launch")
_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_known: dict[int, bool] = {}


def _source(obj) -> str:
    try:
        return inspect.getsource(obj)
    except (OSError, TypeError):
        return ""


def _launches(obj, seen: set | None = None) -> bool:
    """
    Does this function or class launch a browser: by naming a token in
    its own source, or by naming (as a whole word) a function or class
    of its module's globals that does, whichever module that came from?
    An unreadable callee counts as no; a cycle counts as no.
    """
    key = id(obj)
    if key in _known:
        return _known[key]
    seen = seen or set()
    if key in seen:
        return False
    seen.add(key)
    src = _source(obj)
    if any(t in src for t in BROWSER_TOKENS):
        _known[key] = True
        return True
    module = sys.modules.get(getattr(obj, "__module__", "") or "")
    globs = vars(module) if module is not None else {}
    for word in set(_WORD.findall(src)):
        callee = globs.get(word)
        if callee is not obj and (inspect.isfunction(callee) or inspect.isclass(callee)) and _launches(callee, seen):
            _known[key] = True
            return True
    _known[key] = False
    return False


def pytest_collection_modifyitems(config, items):
    for item in items:
        func = getattr(item, "function", None)
        if func is not None and _launches(func):
            item.add_marker(pytest.mark.browser)
