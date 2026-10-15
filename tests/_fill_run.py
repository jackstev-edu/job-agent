"""
_fill_run.py — run the real `apply.py fill` against a mock form, offline.

WHAT THIS DOES: Calls cmd_fill with only the outside world stubbed: no API
calls (the classifier and mapper are given answers), a headless browser, a
profile written to the test's temp home, scripted answers at the terminal
prompts, and no review sheet opened in your real browser. Every record the
run builds for the submit gate is collected.
WHY IT MATTERS: The gate's inputs come from the whole pipeline (scan, policy,
fill, read-back), so its tests have to run that pipeline, not a copy of it.
"""
from __future__ import annotations

import io
import re
from argparse import Namespace
from contextlib import ExitStack, contextmanager, redirect_stdout
from pathlib import Path
from unittest import mock

from _harness import TEST_HOME

# A profile with known values for the mock form's questions. Nothing real.
PROFILE = """\
personal:
  first_name: Test
  last_name: Person
  email: test@example.invalid
  phone: "555 010 0199"
  address_line_1: 1 Example Street
  city: Springfield
  state: XX
  postal_code: "00000"
eligibility:
  work_authorization_type: US Citizen
  authorized_to_work_us: true
eeo:
  gender: Decline to self-identify
voice:
  canned_answers:
    why_this_company: I like the mission.
documents:
  resume_default: missing.pdf
"""


def classify_by_label(fields):
    """Stand-in classifier: the category each mock question really is."""
    out = {}
    for f in fields:
        label = (f.get("label") or "").lower()
        if "authorized" in label:
            out[f["ref"]] = "work_authorization"
        elif "gender" in label:
            out[f["ref"]] = "eeo"
        elif "why do you want" in label:
            out[f["ref"]] = "free_text"
        elif "hear about" in label:
            out[f["ref"]] = "per_company"
        elif f.get("type") == "checkbox":
            out[f["ref"]] = "consent"
        else:
            out[f["ref"]] = "personal_fact"
    return out


def map_from_profile(fields, profile, page_text=""):
    """Stand-in mapper: copies the facts a careful model would, verbatim."""
    out = {}
    for f in fields:
        label = (f.get("label") or "").lower()
        if "first name" in label:
            value = profile.get("personal.first_name")
        elif "email" in label:
            value = profile.get("personal.email")
        elif "why do you want" in label:
            value = profile.get("voice.canned_answers.why_this_company")
        else:
            value = None
        item = {"ref": f["ref"], "value": value, "confidence": "high" if value else "low",
                "needs_review": f.get("_draft", False), "source": "model"}
        from agent import mapper
        out[f["ref"]] = mapper._double_check(item, f)
    return out


@contextmanager
def _headless():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            yield browser.new_context()
        finally:
            browser.close()


class EnterAlreadyPressed:
    """Stands in for the terminal: you pressed Enter as soon as the watch began."""

    def pressed(self):
        return True

    def wait(self):
        pass


def run_fill(form: Path, *, dry_run: bool, answers=("n",), profile_yaml: str = PROFILE,
             classify=classify_by_label, map_fields=map_from_profile, patches=(),
             cover_letter: bool = False):
    """
    Run cmd_fill on `form`. Returns (printed text, records built, error or None).
    `answers` are what you'd type at each terminal prompt, in order; by
    default just "n" at "Did you submit it?".
    """
    import apply
    from agent import shadow

    profile_path = TEST_HOME / "fill-profile.yaml"
    profile_path.write_text(profile_yaml, encoding="utf-8")
    replies = iter(answers)
    records: list[dict] = []
    real_build = shadow.build_record

    def keep(*a, **k):
        rec = real_build(*a, **k)
        records.append(rec)
        return rec

    args = Namespace(profile=str(profile_path), url=form.resolve().as_uri(), force=True,
                     dry_run=dry_run, resume=None, cover_letter=cover_letter,
                     max_steps=6)
    out, error = io.StringIO(), None
    with ExitStack() as stack:
        for target, name, value in (
                (apply.config, "check_api_key", lambda: ("ok", "")),
                (apply.browser_mod, "browser", _headless),
                (apply.classify, "classify_fields", classify),
                (apply.mapper, "map_fields", map_fields),
                (apply.report, "open_in_browser", lambda path: None),
                (apply.shadow, "build_record", keep),
                (apply, "input", lambda *a: next(replies)),
                (apply, "_EnterWatcher", EnterAlreadyPressed),
                *patches):
            stack.enter_context(mock.patch.object(target, name, value, create=True))
        try:
            with redirect_stdout(out):
                apply.cmd_fill(args)
        except (AssertionError, StopIteration) as e:
            error = f"{type(e).__name__}: {e}"
    return re.sub(r"\x1b\[[0-9;]*m", "", out.getvalue()), records, error
