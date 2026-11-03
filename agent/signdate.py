"""
signdate.py — today's date into the signature block of a self-identification form.

WHAT THIS DOES: Decision B (Jack, 2026-11-02: "Yes, today's date"). On a
page that holds an EEO-guarded question (a self-identification form), the
date-part boxes of the block that also holds its Name / signature box
(Month / Day / Year, or one Date box) are planned from the computer's
clock, in the widget's own format: the option of a select that equals
the part, ISO for a date input, a text box only where it states its
format (an MM / DD / YYYY placeholder, inputmode="numeric" for a separate
Month / Day / Year box, filled as 2 / 2 / 4 digits, or a dated placeholder
for one Date box). Anything else, and every other date on a legal / EEO
form, is unchanged (Jack's). The gate recomputes the same plan from the
record's own field and trusts the fill only when it equals.
WHY IT MATTERS: Jack's Workday run (2026-11-02) left the signature block's
Month / Day / Year for him as legal answers. The date is his own fact (the
clock), never the model's; a box whose format the widget does not state
is never guessed into.
"""

from __future__ import annotations

import datetime
import re
from typing import Any

from . import sensitive

SOURCE = "clock"
PARTS = ("month", "day", "year", "date")
# Words a date-part label may hold beside its part word.
_EXTRA = frozenset({"signature", "signed", "signing", "today", "todays", "s", "of", "the", "mm", "dd", "yyyy", "yy",
                    "please", "enter", "select", "current", "this", "application", "form"})
_WORD = re.compile(r"[a-z]+")
_MONTHS = ("january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
           "november", "december")
# One Date box's stated formats, by placeholder (normalised: letters and separators only).
_DATE_FORMATS = {"mm/dd/yyyy": "%m/%d/%Y", "dd/mm/yyyy": "%d/%m/%Y", "yyyy-mm-dd": "%Y-%m-%d",
                 "mm-dd-yyyy": "%m-%d-%Y", "dd-mm-yyyy": "%d-%m-%Y", "yyyy/mm/dd": "%Y/%m/%d",
                 "mm.dd.yyyy": "%m.%d.%Y", "dd.mm.yyyy": "%d.%m.%Y"}
_PART_PLACEHOLDERS = {"month": {"mm", "m"}, "day": {"dd", "d"}, "year": {"yyyy", "y"}}
_PART_FORMAT = {"month": "%m", "day": "%d", "year": "%Y"}


def _today() -> datetime.date:
    """The computer's local date; patched by tests."""
    return datetime.date.today()


def _norm(text: Any) -> str:
    return " ".join(str(text or "").split()).casefold()


def date_part(label: Any) -> str | None:
    """Which date part a label names ("Month", "Signature date", "Date (mm/dd/yyyy)"), or None."""
    words = _WORD.findall(_norm(label))
    parts = [w for w in words if w in PARTS]
    if len(parts) != 1 or any(w not in _EXTRA and w not in PARTS for w in words):
        return None
    return parts[0]


def self_identification_page(fields: list[dict]) -> bool:
    """Does the page hold an EEO-guarded question (the legal backstop's own test)?"""
    return any("eeo" in sensitive.legal_guard_kinds(f) for f in fields)


def _select_value(part: str, options: list, today: datetime.date) -> str | None:
    """The one option equal to today's part: a number (padded or not), a month's name (full or three letters), a year."""
    if part == "month":
        wanted = {str(today.month), f"{today.month:02d}", _MONTHS[today.month - 1], _MONTHS[today.month - 1][:3]}
    elif part == "day":
        wanted = {str(today.day), f"{today.day:02d}"}
    elif part == "year":
        wanted = {str(today.year)}
    else:
        return None
    hits = [o for o in options if _norm(o) in wanted]
    return hits[0] if len(hits) == 1 else None


def _text_value(part: str, field: dict, today: datetime.date) -> str | None:
    placeholder = _norm(field.get("placeholder")).replace(" ", "")
    numeric = _norm(field.get("inputmode")) == "numeric"
    if part == "date":
        fmt = _DATE_FORMATS.get(placeholder)
        return today.strftime(fmt) if fmt else None
    if placeholder in _PART_PLACEHOLDERS[part] or (numeric and not placeholder):
        return today.strftime(_PART_FORMAT[part])
    return None


def value_for(field: dict, today: datetime.date | None = None) -> str | None:
    """Today's part for this box, in the widget's own format, or None when the widget states no format."""
    today = today or _today()
    part = date_part(field.get("label"))
    if part is None:
        return None
    ftype = (field.get("type") or "").lower()
    options = [str(o) for o in field.get("options") or []]
    if ftype == "select" and options:
        return _select_value(part, options, today)
    if ftype == "date":
        return today.isoformat() if part == "date" else None
    if ftype in ("text", "tel", "number", ""):
        return _text_value(part, field, today)
    return None


# Classifier categories the clock never fills past (fold of 2c24303's
# finding 3): sensitive.decide hands these to Jack, and the clock ran
# before it. A classifier reading of work authorization or pay on a date
# box is not overridden either (fold of 9e3d5fb's finding 3); an eeo
# reading is the expected one on a self-identification form and stays.
NEVER_CATEGORIES = frozenset({"credential", "consent", "per_company", "work_authorization", "compensation"})


def _own_text(field: dict) -> dict:
    """The box's own words (label, help text, placeholder), not its name and not the surrounding form's (Jack, 2026-11-03)."""
    return {"label": field.get("label"), "help_text": field.get("help_text"), "placeholder": field.get("placeholder")}


def reads_legal(field: dict) -> bool:
    """
    Does the box's own label, help text or placeholder hit any LEGAL_GUARD
    kind or PROFILE_PATHS line on any reading, or did the page's own read
    of its raw label (raw_legal) hit one? Jack, 2026-11-03, on 2c24303's
    BLOCK: such a date box is his, whatever block it sits in ("Year your
    work visa expires", "Month your disability began").
    """
    own = _own_text(field)
    joined = " ".join(str(v or "") for v in own.values())
    return (sensitive.legal_guard(own) is not None or bool(sensitive.all_profile_lines(joined))
            or bool(field.get("raw_legal")))


def plan_for(field: dict, fields: list[dict], today: datetime.date | None = None,
             category: str | None = None) -> dict | None:
    """
    The plan decision decision B gives this field, or None: the box sits in
    a signature block (the scan's `signature_block`, a container that also
    holds a Name / signature box) on a page holding an EEO-guarded
    question, names one date part, its own text reads as no legal
    question, and its widget states its format. Never the model; never a
    secret box; never a credential / consent / per-company category.
    """
    if not field.get("signature_block") or field.get("secret") or (field.get("type") or "").lower() == "password":
        return None
    # The regex backstop and the page's own raw-stop flag as every other
    # route reads them: a box whose label, help, placeholder or name hits
    # HARD_STOPS is Jack's, whatever its date part (fix of d9461c9's BLOCK:
    # the clock read past a stop the profile route would have kept).
    if sensitive.stop_reason(sensitive._question_text(field)) is not None or field.get("raw_stop"):
        return None
    if reads_legal(field) or category in NEVER_CATEGORIES:
        return None
    if not self_identification_page(fields):
        return None
    value = value_for(field, today)
    if value is None:
        return None
    return {"label": field.get("label"), "options": field.get("options"), "action": "fill", "value": value,
            "source": SOURCE, "fit": "exact", "profile_path": None, "clock_part": date_part(field.get("label"))}


def verify(field: dict, decision: dict | None, fields: list[dict], today: datetime.date | None = None,
           category: str | None = None) -> bool:
    """The gate's recomputation: the same plan from the record's own field, equal to the decision's value."""
    if not decision or decision.get("source") != SOURCE:
        return False
    plan = plan_for(field, fields, today, category)
    return plan is not None and str(plan["value"]) == str(decision.get("value"))


# --- the signature Name box (Jack, 2026-11-03) ------------------------------
# The Name box of the block decision B recognises, on a self-identification
# form, is filled from the profile's name lines (first and last), never
# the model; the gate recomputes the value and stops on a mismatch. Any
# other signature box, or a label with sign / signature wording, is Jack's.
NAME_SOURCE = "profile_name"
NAME_LINES = ("personal.first_name", "personal.last_name")
_NAME_LABEL = re.compile(r"^(?:your |full |applicant'?s? |candidate'?s? |printed |legal )?name(?: \(.*\))?\s*:?\s*\*?$")
# A box that signs: never filled by any route (the filler paragraph's
# "signs anything" line in CLAUDE.md).
_SIGN_WORDS = re.compile(r"\bsign(?:ature|atures|ed|ing|s)?\b|\be-?sign\w*|\binitials?\b", re.I)


def is_signature_box(field: dict) -> bool:
    """Does the box's own label read as a signature / initials box on any reading? Such a box is Jack's."""
    if (field.get("type") or "").lower() not in ("text", "textarea", ""):
        return False
    return any(_SIGN_WORDS.search(v) for v in sensitive.text_variants(str(field.get("label") or "")))


def _name_label(label: Any) -> bool:
    """Every reading of the label is a bare name label ("Name", "Your Name", "Full name *"), with no sign wording on any."""
    variants = sensitive.text_variants(str(label or ""))
    if not variants or any(_SIGN_WORDS.search(v) for v in variants):
        return False
    return all(_NAME_LABEL.fullmatch(_norm(v).replace("’", "'")) for v in variants)


def name_value(prof) -> str | None:
    """First and last name from the profile's own lines, both present, joined by one space; else None."""
    try:
        first, last = (" ".join(str(prof.get(k) or "").split()) for k in NAME_LINES)
    except Exception:
        return None
    if not first or not last:
        return None
    return f"{first} {last}"


def name_plan_for(field: dict, fields: list[dict], prof, category: str | None = None) -> dict | None:
    """
    The plan for the signature Name box, or None (the ordinary routes):
    the scan marked it (`signature_name`: a name label in a block that
    also holds a date-part box), the label is a bare name with no sign
    wording, nothing about the box reads as a stop or a legal question,
    the page holds an EEO-guarded question, and the profile holds both
    name lines. Never the model; never a secret box.
    """
    if (not field.get("signature_name") or field.get("secret") or field.get("options")
            or (field.get("type") or "").lower() not in ("text", "")):
        return None
    if sensitive.stop_reason(sensitive._question_text(field)) is not None or field.get("raw_stop") or field.get("raw_legal"):
        return None
    if not _name_label(field.get("label")):
        return None
    if reads_legal(field) or sensitive.legal_guard_kinds(field) or category in NEVER_CATEGORIES:
        return None
    if not self_identification_page(fields):
        return None
    value = name_value(prof)
    if value is None:
        return None
    return {"label": field.get("label"), "options": None, "action": "fill", "value": value,
            "source": NAME_SOURCE, "fit": "verbatim", "profile_path": NAME_LINES[0]}


def verify_name(field: dict, decision: dict | None, fields: list[dict], prof, category: str | None = None) -> bool:
    """The gate's recomputation: the same name from the record's own field and the profile, equal to the fill."""
    if not decision or decision.get("source") != NAME_SOURCE:
        return False
    plan = name_plan_for(field, fields, prof, category)
    return plan is not None and str(plan["value"]) == str(decision.get("value"))
