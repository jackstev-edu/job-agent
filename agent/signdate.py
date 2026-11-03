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


def plan_for(field: dict, fields: list[dict], today: datetime.date | None = None) -> dict | None:
    """
    The plan decision decision B gives this field, or None: the box sits in
    a signature block (the scan's `signature_block`, a container that also
    holds a Name / signature box) on a page holding an EEO-guarded
    question, names one date part, and its widget states its format.
    Never the model; never a secret box.
    """
    if not field.get("signature_block") or field.get("secret") or (field.get("type") or "").lower() == "password":
        return None
    if not self_identification_page(fields):
        return None
    value = value_for(field, today)
    if value is None:
        return None
    return {"label": field.get("label"), "options": field.get("options"), "action": "fill", "value": value,
            "source": SOURCE, "fit": "exact", "profile_path": None, "clock_part": date_part(field.get("label"))}


def verify(field: dict, decision: dict | None, fields: list[dict], today: datetime.date | None = None) -> bool:
    """The gate's recomputation: the same plan from the record's own field, equal to the decision's value."""
    if not decision or decision.get("source") != SOURCE:
        return False
    plan = plan_for(field, fields, today)
    return plan is not None and str(plan["value"]) == str(decision.get("value"))
