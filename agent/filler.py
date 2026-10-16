"""
filler.py — typing the answers in.

WHAT THIS DOES: Takes the plan (which answer goes in which box) and actually
enters it into the page, typing character by character because many forms
only register input they see as real keystrokes.

WHY IT MATTERS: This file contains the hard stop. It has no code path that
clicks Submit, presses Enter, or types a line break outside a textarea — not
as an option, not behind a flag. That's deliberate.
"""

from __future__ import annotations

import re
from pathlib import Path

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from playwright.sync_api import Page

from . import extract, matching, sensitive
from .extract import REF_ATTR

# Buttons this tool will never click, whatever it's asked. Matched against
# the button's visible text.
FORBIDDEN_BUTTONS = re.compile(
    r"^\s*(submit|submit application|send application|apply now|finish|"
    r"complete application|confirm and submit|sign and submit|i agree|"
    r"accept|agree and continue)\s*$",
    re.I,
)


def _one_line(text: str) -> str:
    """Line breaks as spaces: typed into anything but a textarea, a line break is Enter."""
    return re.sub(r"[\r\n]+", " ", text)


# A checkbox's label as it reads right now: aria-label, aria-labelledby,
# its <label>s, a wrapping label, the scan's nearby legend / label / .label
# fallback (always: four levels up, first one with text, even when the box
# has a label of its own; a neighbour's label it picks up only sends the box
# to you), aria-describedby and the placeholder. The name (or id, as the
# scan takes it) comes back on its own, so it's split into words like the
# scan's. Local only: never sent anywhere.
_CHECKBOX_LABEL_JS = r"""
el => {
  const parts = [el.getAttribute('aria-label'),
                 ...(el.getAttribute('aria-labelledby') || '').split(/\s+/)
                   .map(id => document.getElementById(id)?.innerText),
                 ...[...(el.labels || [])].map(l => l.innerText),
                 el.closest('label')?.innerText];
  let parent = el.parentElement;
  for (let i = 0; i < 4 && parent; i++, parent = parent.parentElement) {
    const n = parent.querySelector(':scope > legend, :scope > label, :scope > .label');
    if (n && n.innerText.trim()) { parts.push(n.innerText); break; }
  }
  parts.push(...(el.getAttribute('aria-describedby') || '').split(/\s+/)
               .map(id => document.getElementById(id)?.innerText),
             el.getAttribute('placeholder'));
  return {label: parts.filter(Boolean).join(' '), name: el.getAttribute('name') || el.id || ''};
}
"""


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

        if field_type == "radio" and field.get("widget") == "buttons":
            return _press_option_button(page, field, str(value))

        if field_type == "radio":
            options = field.get("options", [])
            option_refs = field.get("option_refs", [])
            if str(value) in options:
                _element(page, option_refs[options.index(str(value))]).check(timeout=4000)
                return True, "selected"
            return False, f"'{value}' is not one of the radio options"

        if field_type == "checkbox":
            # The label as it reads now, on every reading of the text. A box
            # that now matches HARD_STOPS, or whose PROFILE_PATHS lines or
            # LEGAL_GUARD kinds differ at all from the scan's question text
            # (a line or kind added, removed or changed), is a different
            # question from the one the policy answered: never ticked or
            # unticked.
            now = element.evaluate(_CHECKBOX_LABEL_JS) or {}
            label, name = str(now.get("label") or ""), str(now.get("name") or "")
            if sensitive.stop_reason(f"{label} {name}"):
                return False, "refused — this box now reads like terms or consent; it's yours"
            if (set(sensitive.profile_lines(f"{label} {name}"))
                    != set(sensitive.profile_lines(sensitive._question_text(field)))
                    or set(sensitive.legal_guard_kinds({"label": label, "name": name}))
                    != set(sensitive.legal_guard_kinds(field))):
                return False, "refused — this box now reads as a different question; it's yours"
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

        # Ordinary text, email, tel, number, date, textarea. Outside a
        # textarea a typed line break is Enter, which can submit the form.
        text = str(value) if field_type == "textarea" else _one_line(str(value))
        # Checked at click time: the box, or a button around it, may have
        # become something that submits the form since the scan.
        if element.evaluate(extract._SUBMITS_JS):
            return False, "refused — this box could submit the form; it's yours"
        element.click(timeout=4000)
        element.fill("", timeout=4000)               # clear anything pre-filled
        element.type(text, delay=12, timeout=25_000)
        return True, "typed"

    except Exception as e:
        return False, f"{type(e).__name__}: {str(e)[:110]}"


# Appended to the outcome of anything chosen by judgement rather than copied —
# a broader heading on a field-of-study menu, a name matched loosely by a
# search box. run_plan turns it into a review flag so it reaches the sheet.
CONFIRM = " — confirm this one"


# Marks the one option row we intend to click, so Playwright can click it with
# real mouse events — react-select and Workday listen for mousedown, and a
# JavaScript .click() on the row does nothing on either. A row that is, sits
# in, or holds a button or input that could submit the form is refused
# ("submits"), never marked: clicking it could send the application.
_MARK_OPTION_JS = r"""
(wanted) => {
  const SUBMITS = """ + extract._SUBMITS_JS + r""";
  const sel = '[role=option], [role=listbox] li, [role=listbox] [data-value],'
            + 'ul[class*=menu i] li, li[class*=option i], div[class*=option i]';
  document.querySelectorAll('[data-ja-opt]').forEach(el => el.removeAttribute('data-ja-opt'));
  const hits = [...document.querySelectorAll(sel)]
      .filter(el => el.getBoundingClientRect().height > 0);
  const leaves = hits.filter(el => !hits.some(o => o !== el && el.contains(o)));
  const row = leaves.find(el => el.innerText.trim() === wanted);
  if (!row) return false;
  if (SUBMITS(row) || [...row.querySelectorAll('button, input')].some(SUBMITS)) return 'submits';
  row.setAttribute('data-ja-opt', '1');
  return true;
}
"""

# After a row is clicked (and the menu closed), does the dropdown show that
# choice? Compared in the page against the agent's own value; only true or
# false comes back. A secret box is never read and counts as not showing it.
_SHOWS_CHOICE_JS = ("""
(el, choice) => {
""" + extract._SECRET_JS + r"""
  if (isSecret(el, '')) return false;
  const norm = t => String(t || '').replace(/\s+/g, ' ').trim();
  const shown = [];
  if ('value' in el && el.tagName !== 'BUTTON') shown.push(el.value);
  const box = el.closest('[class*="control"], [role=combobox]') || el;
  for (const d of box.querySelectorAll('[class*="single-value"], [class*="singleValue"]'))
    shown.push(d.innerText);
  if (!('value' in el)) shown.push(el.innerText);
  return shown.some(t => norm(t) === norm(choice));
}
""")


def _fill_combobox(page: "Page", element, field: dict, value: str) -> tuple[bool, str]:
    """
    Custom dropdown: open it, type, then CLICK the row that matches.

    WHY NEVER ENTER: Enter takes whichever row the widget has highlighted,
    which on a list still loading is simply the first one to arrive, and in
    a box that doesn't catch it, Enter submits the form. A school search
    would quietly commit a different university than the one in your
    profile, or send the application. If no row on screen can be clicked,
    this clears the box and reports a failure so the field reaches you.
    """
    # Checked at click time, not trusted from the scan: a typeless <button>
    # in a form is a submit button, and opening this one would click it.
    if element.evaluate(extract._SUBMITS_JS):
        return False, "refused — this dropdown could submit the form; it's yours"
    element.click(timeout=4000)
    page.wait_for_timeout(250)
    page.keyboard.type(_one_line(value), delay=45)

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
        marked = page.evaluate(_MARK_OPTION_JS, choice)
        if marked == "submits":
            _abandon_combobox(page, element)
            return False, f"refused — the row for '{choice}' could submit the form; left empty for you"
        if marked:
            page.locator("[data-ja-opt='1']").first.click(timeout=3000)
    except Exception:
        marked = False
    if not marked:
        _abandon_combobox(page, element)
        return False, f"couldn't click the row for '{choice}'; left empty for you"
    page.wait_for_timeout(300)
    # A menu still up after the click is closed, never committed with Enter.
    if extract.read_open_options(page):
        extract.close_dropdown(page)
    # Some widgets undo a pending choice on Escape. Claim it only if it shows.
    try:
        kept = element.evaluate(_SHOWS_CHOICE_JS, choice)
    except Exception:
        kept = False
    if not kept:
        _abandon_combobox(page, element)
        return False, f"the dropdown didn't keep '{choice}'; left empty for you"

    if how in ("exact", "normalized"):
        return True, f"selected '{choice}'"
    return True, f"selected '{choice}' — {how}{CONFIRM}"


_BUTTON_FACTS_JS = r"""
(el, group) => ({
  text: (el.innerText || '').trim(),
  inGroup: el.tagName === 'BUTTON' && !!el.parentElement?.closest(group),
  // A <button> with no type attribute IS type=submit, and Ashby's have none.
  // That only matters with a form to submit, so the test is both together.
  submits: !!el.form && ['submit', 'image'].includes((el.type || '').toLowerCase()),
  pressed: el.getAttribute('aria-pressed'),
})
"""


def _press_option_button(page: "Page", field: dict, value: str) -> tuple[bool, str]:
    """
    Answer a button-pair question by clicking the matching button.

    WHY SO MANY CHECKS: this is the one fill path that clicks a <button>, the
    same kind of element as Submit. So the button must still sit inside the
    group we scanned, must not be able to submit a form, and must not read
    like Submit or I agree — checked again here, at click time, not trusted
    from the scan. And a pressed button is left alone: clicking it again can
    toggle the answer off.
    """
    options = field.get("options", [])
    option_refs = field.get("option_refs", [])
    if value not in options:
        return False, f"'{value}' is not one of the options"

    button = _element(page, option_refs[options.index(value)])
    facts = button.evaluate(_BUTTON_FACTS_JS, extract.BUTTON_GROUP)
    if not facts["inGroup"]:
        return False, "refused — that button is no longer inside its question"
    if facts["submits"] or is_forbidden_button(facts["text"]):
        return False, f"refused — '{facts['text']}' could submit or agree to something"
    if facts["pressed"] == "true":
        return True, "already selected"

    button.click(timeout=4000)
    page.wait_for_timeout(150)
    if facts["pressed"] is not None and button.get_attribute("aria-pressed") != "true":
        return False, f"clicked '{value}' but the form didn't register it"
    return True, "selected"


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
    """Checked before a button-pair answer is clicked."""
    return bool(FORBIDDEN_BUTTONS.match((text or "").strip()))
