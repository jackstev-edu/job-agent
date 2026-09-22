"""
matching.py — picking the right option out of a dropdown.

WHAT THIS DOES: Given a value from your profile and the list of choices a form
actually offers, decides which choice you meant — or decides that none of them
match and says so.

WHY IT MATTERS: Dropdowns are where a small wording difference turns into a
wrong fact on a signed application. "Carnegie Mellon University" and "Carnegie
Mellon University - Africa" are different schools. This file refuses an
ambiguous match rather than taking the first one, which is what pressing Enter
on an open dropdown would do.
"""

from __future__ import annotations

import re

# Punctuation and noise that never changes which option is meant.
_TRIM = re.compile(r"[‘’'\"().,/\\-]+")
_SPACE = re.compile(r"\s+")

# Wordings that mean the same thing in a dropdown of academic and job fields.
_EQUIVALENT = {
    "and": "&",
    "bachelors": "bachelor",
    "masters": "master",
    "univ": "university",
}


def normalize(text: str) -> str:
    """Case, punctuation and spacing removed — the parts nobody means."""
    out = _TRIM.sub(" ", str(text or "")).lower()
    out = _SPACE.sub(" ", out).strip()
    words = [_EQUIVALENT.get(w, w) for w in out.split()]
    return " ".join(words)


def best_match(value, options: list[str], strict: bool = False) -> tuple[str | None, str]:
    """
    Returns (chosen_option, how_it_was_chosen).

    `how` is one of "exact", "normalized", "prefix", "contains", or a reason
    beginning with "no match" / "ambiguous". Callers use it to decide whether
    the answer needs your eyes: anything past "normalized" is a judgement call,
    not a copy.

    `strict` drops the weakest test, the substring one. Every degree ending in
    "Engineering" contains the word "Engineering", so on a field-of-study menu
    that test quietly turns every discipline into the same broad heading.
    """
    if value is None or not options:
        return None, "no match — nothing to compare"

    raw = str(value).strip()
    for option in options:
        if option.strip() == raw:
            return option, "exact"

    target = normalize(raw)
    if not target:
        return None, "no match — the value is blank once punctuation is removed"

    normalized = [(o, normalize(o)) for o in options]

    hits = [o for o, n in normalized if n == target]
    if len(hits) == 1:
        return hits[0], "normalized"
    if len(hits) > 1:
        return hits[0], "normalized"          # identical once normalized

    # A prefix match is how a type-to-search box behaves, but only trust it
    # when exactly one option starts that way. Two candidates means the form
    # is distinguishing between things we can't tell apart.
    hits = [o for o, n in normalized if n.startswith(target) or target.startswith(n)]
    if len(hits) == 1:
        return hits[0], "prefix"
    if len(hits) > 1:
        return None, f"ambiguous — {len(hits)} options start with that: {hits[:4]}"

    if not strict:
        hits = [o for o, n in normalized if target in n or n in target]
        if len(hits) == 1:
            return hits[0], "contains"
        if len(hits) > 1:
            return None, f"ambiguous — {len(hits)} options contain that: {hits[:4]}"

    return None, "no match — this form offers none of them"


# ---------------------------------------------------------------------------
#  Fields of study
#
#  Dropdowns of academic subjects are short lists of broad headings. Real
#  degrees are neither. "Artificial Intelligence Engineering" is not on any
#  menu, and neither is a double major written as one string — both matched
#  nothing and left the field blank twice.
#
#  Each entry reads: if the degree mentions the key, these headings describe
#  it, nearest first. Nothing here is a synonym in the strict sense, so every
#  match made this way is flagged for your review rather than filled quietly.
# ---------------------------------------------------------------------------
_STUDY_LABEL = re.compile(
    r"field of study|major|discipline|area of study|course of study|"
    r"concentration|degree field|program of study|subject", re.I)

FIELD_OF_STUDY_ALIASES: list[tuple[str, tuple[str, ...]]] = [
    ("artificial intelligence", ("Artificial Intelligence", "Machine Learning",
                                 "Computer Science", "Computer Engineering",
                                 "Data Science", "Engineering")),
    ("machine learning", ("Machine Learning", "Artificial Intelligence",
                          "Computer Science", "Data Science", "Engineering")),
    ("data science", ("Data Science", "Statistics", "Computer Science",
                      "Mathematics", "Engineering")),
    ("mechatronics", ("Mechatronics", "Robotics", "Mechanical Engineering",
                      "Electrical Engineering", "Engineering")),
    ("robotics", ("Robotics", "Mechanical Engineering", "Electrical Engineering",
                  "Computer Engineering", "Engineering")),
    ("mechanical engineering", ("Mechanical Engineering", "Engineering")),
    ("electrical engineering", ("Electrical Engineering",
                                "Electrical and Electronics Engineering",
                                "Engineering")),
    ("computer engineering", ("Computer Engineering", "Computer Science",
                              "Electrical Engineering", "Engineering")),
    ("computer science", ("Computer Science", "Computer Engineering",
                          "Information Technology", "Engineering")),
    ("software", ("Software Engineering", "Computer Science",
                  "Computer Engineering", "Engineering")),
    ("aerospace", ("Aerospace Engineering", "Mechanical Engineering", "Engineering")),
    ("civil engineering", ("Civil Engineering", "Engineering")),
    ("chemical engineering", ("Chemical Engineering", "Engineering")),
    ("industrial engineering", ("Industrial Engineering", "Systems Engineering",
                                "Engineering")),
    ("systems engineering", ("Systems Engineering", "Industrial Engineering",
                             "Engineering")),
    ("engineering", ("Engineering",)),
]

# Double majors and joint degrees arrive as one string.
_COMPONENT_SPLIT = re.compile(r"\s*(?:;|,|/|\band\b|&|\bwith\b|\bplus\b)\s*", re.I)

_OTHER_OPTION = re.compile(r"^other\b.{0,30}$|^not listed\b.{0,20}$", re.I)


def looks_like_field_of_study(label: str | None) -> bool:
    return bool(_STUDY_LABEL.search(label or ""))


def best_match_for_field(label: str | None, value, options: list[str]) -> tuple[str | None, str]:
    """
    best_match, plus the extra latitude a field-of-study dropdown needs.

    Order matters: a real match is always preferred, then one half of a double
    major, then a broader heading that honestly describes the degree, and only
    then "Other". Everything past a plain match is a judgement call and the
    caller is expected to flag it.
    """
    if not looks_like_field_of_study(label):
        return best_match(value, options)

    # Strict first: a real match, or one that differs only in punctuation.
    choice, how = best_match(value, options, strict=True)
    if choice is not None or not options:
        return choice, how

    raw = str(value or "").strip()

    # A double major arrives as one string. Try each half on its own, longest
    # first — "Mechatronics Engineering" says more than "Robotics".
    parts = [p for p in _COMPONENT_SPLIT.split(raw) if len(p.strip()) > 2]
    if len(parts) > 1:
        for part in sorted(parts, key=len, reverse=True):
            found, _ = best_match(part, options, strict=True)
            if found is not None:
                return found, f"one half of the degree ('{part.strip()}')"

    # A broader heading that still describes the degree truthfully.
    target = normalize(raw)
    for key, headings in FIELD_OF_STUDY_ALIASES:
        if key not in target:
            continue
        for heading in headings:
            found, _ = best_match(heading, options, strict=True)
            if found is not None:
                return found, f"nearest heading offered ('{found}')"

    # Only now the weak substring test, and then the form's own escape hatch.
    found, weak = best_match(value, options)
    if found is not None:
        return found, weak

    other = next((o for o in options if _OTHER_OPTION.match(o.strip())), None)
    if other is not None:
        return other, "the form's own 'Other'"

    return None, how
