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
            # Custom dropdown: click to open it, type to filter, take the match.
            element.click(timeout=4000)
            page.wait_for_timeout(350)
            page.keyboard.type(str(value), delay=45)
            page.wait_for_timeout(700)
            page.keyboard.press("Enter")
            return True, "picked from custom dropdown"

        if field_type == "file":
            return False, "file uploads are handled separately"

        # Ordinary text, email, tel, number, date, textarea.
        element.click(timeout=4000)
        element.fill("", timeout=4000)               # clear anything pre-filled
        element.type(str(value), delay=12, timeout=25_000)
        return True, "typed"

    except Exception as e:
        return False, f"{type(e).__name__}: {str(e)[:110]}"


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
            "needs_review": bool(decision.get("needs_review")),
            "source": decision.get("source", "model"),
            "confidence": decision.get("confidence", ""),
            "note": decision.get("note", ""),
            "required": bool(field.get("required")),
        })
    return report


def is_forbidden_button(text: str) -> bool:
    """Used by the step navigator before it clicks anything."""
    return bool(FORBIDDEN_BUTTONS.match((text or "").strip()))
