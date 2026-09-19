"""
sensitive.py — the guardrail. Still the most important file here.

WHAT THIS DOES: Takes each field plus the category the classifier assigned it,
and decides one of three things — fill it from a named line in profile.yaml,
let the model fill it, or hand it to you. It also runs a regex backstop, and
if either layer says a field is sensitive, it's sensitive.

WHY IT MATTERS: The classifier reads phrasing the regexes would miss; the
regexes catch anything the classifier fumbles. Neither can quietly downgrade
the other, so a field has to get past both to be auto-filled. The actual
answers to legal questions still come only from lines you wrote yourself.
"""

from __future__ import annotations

import re
from typing import Any

from .classify import action_for

# ---------------------------------------------------------------------------
#  Which line of profile.yaml answers which question.
#
#  Only consulted for fields the classifier routed to "profile". If nothing
#  here matches, the field goes to you rather than being guessed at — so a
#  gap in this table costs you a manual answer, never a wrong one.
# ---------------------------------------------------------------------------
PROFILE_PATHS: list[tuple[str, str]] = [
    # Order matters: "now or in the future" is a different question from "now".
    # "commence an immigration case" is Bain's wording for sponsorship; other
    # portals say "petition", "work permit" or "immigration support". Kept
    # broad because the classifier has already told us this is a work
    # authorization question before we get here.
    (r"now\s+or\s+in\s+the\s+future|will\s+you\s+in\s+the\s+future"
     r"|future.{0,30}(sponsor|immigration|petition|work\s+permit)"
     r"|(sponsor\w*|immigration|petition).{0,30}(future|later|any\s+time)",
     "eligibility.requires_sponsorship_future"),
    (r"(requir\w*|need\w*|seek\w*).{0,40}(sponsor|immigration|petition|work\s+permit)"
     r"|sponsor\w*.{0,25}(now|currently|to\s+begin)",
     "eligibility.requires_sponsorship_now"),
    (r"legally\s+(authoriz|entitl|permitt)|authoriz\w*\s+to\s+work|right\s+to\s+work"
     r"|eligible\s+to\s+work",
     "eligibility.authorized_to_work_us"),
    (r"visa\s+(status|type)|immigration\s+status|citizenship\s+status"
     r"|work\s+authoriz\w*\s+(type|status|category)",
     "eligibility.work_authorization_type"),
    (r"u\.?\s?s\.?\s+person|export\s+control|\bitar\b|\bear\b\s+regulation|deemed\s+export",
     "eligibility.export_control_us_person"),
    (r"security\s+clearance|\bdod\b\s+clearance|(secret|ts/sci)\s+clearance",
     "eligibility.security_clearance"),

    (r"hispanic|latino|latinx", "eeo.hispanic_latino"),
    (r"\brace\b|ethnic(ity)?", "eeo.race_ethnicity"),
    (r"veteran|protected\s+vet|military\s+service|uniformed\s+service", "eeo.veteran_status"),
    (r"disabilit|\bdisabled\b|section\s+503", "eeo.disability_status"),
    (r"\bgender\b|\bsex\b(?!ual\s+orientation)|gender\s+identity", "eeo.gender"),

    (r"willing\s+to\s+relocat|able\s+to\s+relocat|open\s+to\s+relocat",
     "eligibility.willing_to_relocate"),
    (r"driver'?s?\s+licen[cs]e", "eligibility.has_drivers_license"),
    (r"willing\s+to\s+travel|percent\w*\s+of\s+travel|travel\s+requirement",
     "eligibility.willing_to_travel_pct"),

    (r"salary|compensation|desired\s+pay|expected\s+(pay|rate)|hourly\s+rate"
     r"|pay\s+expectation",
     "compensation.desired_base_min"),
]

# ---------------------------------------------------------------------------
#  The backstop. Deliberately broad — a false positive costs you ten seconds.
#  If any of these match, the field goes to you no matter what the classifier
#  decided. This layer does not depend on a model being available, correct,
#  or even reachable.
# ---------------------------------------------------------------------------
HARD_STOPS: list[tuple[str, str]] = [
    (r"password|passcode|\bpin\b|security\s+question|secret\s+answer"
     r"|verification\s+code|one-?time\s+code|\b2fa\b|authenticator",
     "Credential field. This tool never types passwords."),

    (r"convict|felon|misdemean|criminal|arrest|background\s+check"
     r"|drug\s+(test|screen)|substance\s+(abuse|test)",
     "Legal declaration about your record. Never auto-filled."),

    (r"electronic\s+signature|\bi\s+certify|attest|acknowledg"
     r"|terms\s+(and|&)\s+conditions|privacy\s+(policy|notice)"
     r"|(agree|accept|consent|read).{0,40}(privacy|terms|policy)",
     "You sign and consent to this, not a script."),

    (r"(previously|ever|before|prior)\s+(\w+\s+){0,2}(work|appl|employ)"
     r"|former\s+employee|current(ly)?\s+employ\w*\s+(by|at|with)"
     r"|prior\s+employment",
     "Depends on the specific company — only you know."),

    (r"referr?ed\s+by|how\s+did\s+you\s+(hear|learn|find)|referral\s+source"
     r"|source\s+of\s+application",
     "Answer by hand — a real name here changes who reads your application."),

    (r"relative|family\s+member.{0,30}employ|conflict\s+of\s+interest"
     r"|non-?compete|restrictive\s+covenant",
     "Per-company relationship or contract question."),
]

_PATHS = [(re.compile(p, re.I), path) for p, path in PROFILE_PATHS]
_STOPS = [(re.compile(p, re.I), why) for p, why in HARD_STOPS]


def _question_text(field: dict) -> str:
    """Everything visible about the question — some portals hide it in help text."""
    return " ".join(str(field.get(k) or "") for k in
                    ("label", "help_text", "placeholder", "name"))


def decide(field: dict, category: str, profile) -> dict:
    """
    Returns a decision with one of four actions:
      "fill"  — value taken verbatim from a named profile line
      "model" — ordinary field, hand to the mapper
      "draft" — mapper may write it, always flagged for review
      "human" — never filled; handed to you with a reason
    """
    text = _question_text(field)
    base = {"label": field.get("label"), "options": field.get("options"),
            "category": category}

    # -- layer 1: the backstop overrides everything --------------------------
    for rx, why in _STOPS:
        if rx.search(text):
            return {**base, "action": "human", "value": None,
                    "source": "human", "reason": why}

    # -- layer 2: a password input is refused on type alone, however labelled -
    if (field.get("type") or "").lower() == "password":
        return {**base, "action": "human", "value": None, "source": "human",
                "reason": "Credential field. This tool never types passwords."}

    action = action_for(category)

    if action == "human":
        return {**base, "action": "human", "value": None, "source": "human",
                "reason": f"Classified as {category.replace('_', ' ')} — "
                          f"this one is yours to answer."}

    # -- layer 3: answers that must come from a named line in profile.yaml ---
    if action == "profile":
        path = next((p for rx, p in _PATHS if rx.search(text)), None)
        if path is None:
            return {**base, "action": "human", "value": None, "source": "human",
                    "reason": f"This is a {category.replace('_', ' ')} question, but "
                              f"I can't tell which profile line answers it. "
                              f"Answer it yourself."}
        raw = profile.get(path)
        if raw is None:
            return {**base, "action": "human", "value": None, "source": "human",
                    "reason": f"`{path}` is blank in profile.yaml. Fill it in and "
                              f"this stops being manual."}
        value = fit_to_widget(raw, field)
        if value is None:
            return {**base, "action": "human", "value": None, "source": "human",
                    "reason": f"Your answer is '{raw}' but this form offers "
                              f"{field.get('options')}. Pick one yourself."}
        return {**base, "action": "fill", "value": value, "source": "profile",
                "profile_path": path}

    # -- ordinary fields -----------------------------------------------------
    return {**base, "action": action, "value": None, "source": "model"}


def fit_to_widget(raw: Any, field: dict):
    """
    Shape a profile value to whatever widget the form actually uses. A yes/no
    in your file might arrive as radio buttons, a dropdown with oddly-worded
    options, or a checkbox. Returns None rather than forcing a bad match.
    """
    options = field.get("options") or []
    ftype = (field.get("type") or "").lower()

    if ftype == "checkbox":
        return bool(raw)

    if not options:
        return ("Yes" if raw else "No") if isinstance(raw, bool) else str(raw)

    if isinstance(raw, bool):
        wanted = (("yes", "true", "y", "i am authorized", "authorized")
                  if raw else ("no", "false", "n", "not authorized"))
        for opt in options:
            if opt.strip().lower() in wanted:
                return opt
        for opt in options:
            low = opt.strip().lower()
            if (raw and low.startswith("yes")) or (not raw and low.startswith("no")):
                return opt
        return None

    target = str(raw).strip().lower()
    for opt in options:
        if opt.strip().lower() == target:
            return opt
    for opt in options:
        low = opt.strip().lower()
        if target and (target in low or low in target):
            return opt

    if any(k in target for k in ("decline", "wish", "prefer not", "not disclose")):
        for opt in options:
            if any(k in opt.lower() for k in ("decline", "not wish", "prefer not",
                                              "not disclose", "choose not", "not answer")):
                return opt
    return None
