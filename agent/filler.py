"""
filler.py — typing the answers in.

WHAT THIS DOES: Takes the plan (which answer goes in which box) and actually
enters it into the page, typing character by character because many forms
only register input they see as real keystrokes.

WHY IT MATTERS: This file contains the hard stop. It has no code path that
clicks Submit — not as an option, not behind a flag. That's deliberate.
"""

from __future__ import annotations

import re
from pathlib import Path

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from playwright.sync_api import Page

from . import extract, matching
from .extract import REF_ATTR

# Buttons this tool will never click, whatever it's asked. Matched against
# the button's visible text.
FORBIDDEN_BUTTONS = re.compile(
    r"^\s*(submit|submit application|send application|apply now|finish|"
    r"complete application|confirm and submit|sign and submit|i agree|"
    r"accept|agree and continue)\s*$",
    re.I,
)


def _element(page: "Page", ref: str):
    """Find an element by the reference tag we stamped on it during scanning."""
    return page.locator(f"[{REF_ATTR}='{ref}']").first


def fill_one(page: "Page", field: dict, value) -> tuple[bool, str]:
    """Fill a single field. Returns (did_it_work, what_happened)."""
    if value is None or value == "":
        return False, "skipped — no value"

    # Last line of defence, behind the classifier and the regex backstop.
    # Even if a credential field somehow reaches here, refuse at the keyboard.
    if (field.get("type") or "").lower() == "password":
        return False, "refused — this tool does not enter passwords"

    field_type = field.get("type")
    element = _element(page, field["ref"])

    try:
        element.scroll_into_view_if_needed(timeout=4000)

        if field_type == "radio":
            options = field.get("options", [])
            option_refs = field.get("option_refs", [])
            if str(value) in options:
                _element(page, option_refs[options.index(str(value))]).check(timeout=4000)
                return True, "selected"
            return False, f"'{value}' is not one of the radio options"

        if field_type == "checkbox":
            element.set_checked(bool(value), timeout=4000)
            return True, "ticked" if value else "unticked"

        if field_type == "select":
            try:
                element.select_option(label=str(value), timeout=3000)
            except Exception:
                element.select_option(value=str(value), timeout=3000)
            return True, "selected"

        if field_type == "combobox":
            return _fill_combobox(page, element, field, str(value))

        if field_type == "file":
            return False, "file uploads are handled separately"

        # Ordinary text, email, tel, number, date, textarea.
        element.click(timeout=4000)
        element.fill("", timeout=4000)               # clear anything pre-filled
        element.type(str(value), delay=12, timeout=25_000)
        return True, "typed"

    except Exception as e:
        return False, f"{type(e).__name__}: {str(e)[:110]}"


# Appended to the outcome of anything chosen by judgement rather than copied —
# a broader heading on a field-of-study menu, a name matched loosely by a
# search box. run_plan turns it into a review flag so it reaches the sheet.
CONFIRM = " — confirm this one"


# Marks the one option row we intend to click, so Playwright can click it with
# real mouse events — react-select and Workday listen for mousedown, and a
# JavaScript .click() on the row does nothing on either.
_MARK_OPTION_JS = r"""
(wanted) => {
  const sel = '[role=option], [role=listbox] li, [role=listbox] [data-value],'
            + 'ul[class*=menu i] li, li[class*=option i], div[class*=option i]';
  document.querySelectorAll('[data-ja-opt]').forEach(el => el.removeAttribute('data-ja-opt'));
  const hits = [...document.querySelectorAll(sel)]
      .filter(el => el.getBoundingClientRect().height > 0);
  const leaves = hits.filter(el => !hits.some(o => o !== el && el.contains(o)));
  const row = leaves.find(el => el.innerText.trim() === wanted);
  if (!row) return false;
  row.setAttribute('data-ja-opt', '1');
  return true;
}
"""


def _fill_combobox(page: "Page", element, field: dict, value: str) -> tuple[bool, str]:
    """
    Custom dropdown: open it, type, then CLICK the row that matches.

    WHY NOT JUST PRESS ENTER: Enter takes whichever row the widget has
    highlighted, which on a list still loading is simply the first one to
    arrive. A school search would then quietly commit a different university
    than the one in your profile — a wrong fact on a form you sign. If nothing
    on screen matches what we typed, this clears the box and reports a failure
    so the field reaches you instead.
    """
    element.click(timeout=4000)
    page.wait_for_timeout(250)
    page.keyboard.type(value, delay=45)

    options = extract.wait_for_options(page, 3000)
    if not options:
        if extract.menu_says_no_results(page) or field.get("search_required"):
            # The form searched and came back with nothing. Typing a value it
            # doesn't recognise leaves text in a box that hasn't actually been
            # answered, which reads as filled on screen and is blank underneath.
            _abandon_combobox(page, element)
            return False, f"the form's search found no match for '{value}'"
        # Some boxes take free text. Leave it, but don't claim it was picked.
        return True, f"typed '{value}' — nothing offered to pick from{CONFIRM}"

    choice, how = matching.best_match_for_field(field.get("label"), value, options)
    if choice is None:
        _abandon_combobox(page, element)
        return False, f"'{value}' — {how}"

    try:
        if page.evaluate(_MARK_OPTION_JS, choice):
            page.locator("[data-ja-opt='1']").first.click(timeout=3000)
        else:
            page.keyboard.press("Enter")
    except Exception:
        page.keyboard.press("Enter")
    page.wait_for_timeout(300)

    if extract.read_open_options(page):
        page.keyboard.press("Enter")          # the menu is still up; commit it
        page.wait_for_timeout(200)

    if how in ("exact", "normalized"):
        return True, f"selected '{choice}'"
    return True, f"selected '{choice}' — {how}{CONFIRM}"


def _abandon_combobox(page: "Page", element) -> None:
    """Leave no half-typed text behind in a box we couldn't answer."""
    try:
        element.fill("", timeout=2000)
    except Exception:
        pass
    extract.close_dropdown(page)


def upload_file(page: "Page", ref: str, path: Path) -> tuple[bool, str]:
    if not path or not Path(path).exists():
        return False, f"file not found: {path}"
    try:
        _element(page, ref).set_input_files(str(path), timeout=15_000)
        return True, f"uploaded {Path(path).name}"
    except Exception as e:
        return False, f"upload failed: {str(e)[:110]}"


def run_plan(page: "Page", fields: list[dict], plan: dict) -> list[dict]:
    """
    Execute the whole plan and return a line-by-line report.

    Does not submit. There is no argument that makes it submit.
    """
    by_ref = {f["ref"]: f for f in fields}
    report = []

    for ref, decision in plan.items():
        field = by_ref.get(ref)
        if field is None:
            continue
        worked, detail = fill_one(page, field, decision.get("value"))
        report.append({
            "label": field.get("label") or field.get("name") or "(unlabelled)",
            "value": decision.get("value"),
            "filled": worked,
            "detail": detail,
            "needs_review": bool(decision.get("needs_review")) or CONFIRM in detail,
            "source": decision.get("source", "model"),
            "confidence": decision.get("confidence", ""),
            "note": decision.get("note", ""),
            "required": bool(field.get("required")),
        })
    return report


def is_forbidden_button(text: str) -> bool:
    """Used by the step navigator before it clicks anything."""
    return bool(FORBIDDEN_BUTTONS.match((text or "").strip()))
