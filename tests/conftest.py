"""
conftest.py — pytest's first import in this folder, and the browser marker.

WHAT THIS DOES: Imports _harness before any test module, so JOB_AGENT_HOME
already points at a throwaway folder when the first agent module loads;
then marks every test that launches Playwright's Chromium `browser`, read
off the test's own source in its file and the helpers it calls (in its
own module or imported from another test module, followed to a
fixpoint), so `-m "not browser"` runs the rest in under a minute. The
source is read from the test files themselves, never through
inspect.getsource (pytest's assertion rewriter can leave that unable to
find a module's source), and a test whose source can't be read counts
as a browser test.
WHY IT MATTERS: Under pytest every test shares one process, and config reads
JOB_AGENT_HOME only once; whichever test imported it first would otherwise
decide whether the rest write into your real ~/.job-agent. And Jack's
test-run rule (2026-10-20, second set) is: per commit, the new or changed
test files plus the non-browser tests; the full suite once before each
push. The split needs one marker, kept here, not a list to maintain.
"""
import ast
import re
import sys
from pathlib import Path

import pytest

import _harness  # noqa: F401

TESTS_DIR = Path(__file__).resolve().parent

# A name whose presence in a function's source means a browser is launched:
# the harness's page factories and the shared fill runner.
BROWSER_TOKENS = ("mock_page", "sync_playwright", "_headless", "run_fill", "_fill_run",
                  "launch_persistent", "chromium.launch")
_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_defs_cache: dict[str, dict[str, str]] = {}
_launches_cache: dict[tuple[str, str], bool] = {}


def _defs(modname: str) -> dict[str, str]:
    """The source of each top-level function and class of a test module, by name, read from its file."""
    if modname in _defs_cache:
        return _defs_cache[modname]
    out: dict[str, str] = {}
    try:
        text = (TESTS_DIR / f"{modname}.py").read_text(encoding="utf-8")
        for node in ast.parse(text).body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                out[node.name] = ast.get_source_segment(text, node) or ""
    except (OSError, SyntaxError, ValueError):
        out = {}
    _defs_cache[modname] = out
    return out


def _launches(modname: str, name: str, seen: set) -> bool | None:
    """
    Does the function or class `name` of test module `modname` launch a
    browser: by naming a token in its own source, or by naming (as a
    whole word) a function or class, of its module or imported into it
    from another test module, that does? None when its source can't be
    read. A cycle counts as no for the inner visit only; nothing negative
    is cached, so a cycle can't hide a token reached another way.
    """
    key = (modname, name)
    if key in _launches_cache:
        return _launches_cache[key]
    if key in seen:
        return False
    seen.add(key)
    src = _defs(modname).get(name)
    if src is None:
        return None
    if any(t in src for t in BROWSER_TOKENS):
        _launches_cache[key] = True
        return True
    module = sys.modules.get(modname)
    globs = vars(module) if module is not None else {}
    for word in set(_WORD.findall(src)):
        if word == name:
            continue
        obj = globs.get(word)
        if obj is None or not callable(obj):
            continue
        callee_mod = getattr(obj, "__module__", None) or ""
        callee_name = getattr(obj, "__name__", None) or word
        if callee_mod == modname:
            hit = _launches(modname, word, seen)
        elif (TESTS_DIR / f"{callee_mod}.py").is_file():
            hit = _launches(callee_mod, callee_name, seen)
        else:
            continue
        if hit:
            _launches_cache[key] = True
            return True
    return False


def pytest_collection_modifyitems(config, items):
    for item in items:
        func = getattr(item, "function", None)
        if func is None:
            continue
        modname = getattr(item.module, "__name__", None) or Path(str(item.path)).stem
        hit = _launches(modname, getattr(func, "__name__", item.name), set())
        if hit is None or hit:
            item.add_marker(pytest.mark.browser)
