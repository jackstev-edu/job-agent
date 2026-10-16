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
import unicodedata
from typing import Any

from .classify import action_for

# ---------------------------------------------------------------------------
#  Which line of profile.yaml answers which question.
#
#  Consulted for fields the classifier routed to "profile", and for any
#  field the legal backstop (LEGAL_GUARD, below) matches, whatever the
#  classifier said. In the second case the line found must be in the same
#  area as the guard's match (eligibility / eeo / compensation), or the
#  field comes to you. If nothing here matches, the field goes to you rather
#  than being guessed at — so a gap in this table costs you a manual answer,
#  never a wrong one.
# ---------------------------------------------------------------------------
PROFILE_PATHS: list[tuple[str, str]] = [
    # Order matters: "now or in the future" is a different question from "now".
    # "commence an immigration case" is one portal's wording for sponsorship; other
    # portals say "petition", "work permit" or "immigration support". Kept
    # broad. A question reaches these rows only when the classifier routed
    # it to a profile-answered category (work authorization, EEO or pay) or
    # LEGAL_GUARD matched it. With a guard match, an eligibility.* line is
    # used only if the guard matched as work authorization and nothing
    # else. Without one, the classifier's routing is all that keeps these
    # patterns off an unrelated question.
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
    # These four sit ABOVE the gender line on purpose. Forms bundle the
    # questions — "sexual orientation or gender identity" is one field on
    # plenty of portals — and the gender pattern matches that phrase, so a
    # lower-placed orientation rule would never be reached and the answer
    # would be read off the wrong profile line. Any new eeo.* row goes above
    # the gender row too: its gender\s+identity alternative matches bundled
    # "sexual orientation or gender identity" fields and would claim them first.
    (r"sexual\s+orientation|\blgbt\w*|\bqueer\b", "eeo.sexual_orientation"),
    (r"transgender|gender\s+expansive|non-?binary\s+identity", "eeo.transgender"),
    (r"first[\s-]?gener\w*|first\s+in\s+(your|their|the)\s+family",
     "eeo.first_generation"),
    (r"socio-?economic|social\s+mobility|free\s+school\s+meals"
     r"|parent\w*\s+(education|occupation|qualification)"
     r"|low[\s-]income\s+(background|household)",
     "eeo.socioeconomic_background"),
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
# Each entry: (pattern, why it goes to you, kind). The kind is for the submit
# gate, which treats per-company questions differently from legal ones; it
# changes nothing about what is stopped here.
HARD_STOPS: list[tuple[str, str, str]] = [
    (r"password|passcode|\bpin\b|security\s+question|secret\s+answer"
     r"|verification\s+code|one-?time\s+code|\b2fa\b|authenticator",
     "Credential field. This tool never types passwords.", "credential"),

    (r"convict|felon|misdemean|criminal|arrest|background\s+check"
     r"|drug\s+(test|screen)|substance\s+(abuse|test)",
     "Legal declaration about your record. Never auto-filled.", "legal"),

    (r"electronic\s+signature|\bi\s+certify|attest|acknowledg"
     r"|terms\s+(and|&)\s+conditions|privacy\s+(policy|notice)"
     r"|(agree|accept|consent|read).{0,40}(privacy|terms|policy)",
     "You sign and consent to this, not a script.", "legal"),

    (r"(previously|ever|before|prior)\s+(\w+\s+){0,2}(work|appl|employ)"
     r"|former\s+employee|current(ly)?\s+employ\w*\s+(by|at|with)"
     r"|prior\s+employment",
     "Depends on the specific company — only you know.", "per_company"),

    (r"referr?ed\s+by|how\s+did\s+you\s+(hear|learn|find)|referral\s+source"
     r"|source\s+of\s+application",
     "Answer by hand — a real name here changes who reads your application.",
     "per_company"),

    (r"relative|family\s+member.{0,30}employ|conflict\s+of\s+interest"
     r"|non-?compete|restrictive\s+covenant",
     "Per-company relationship or contract question.", "per_company"),
]

# Every way a form writes "I would rather not answer". Matching this as a
# regex rather than a list of substrings is deliberate: Greenhouse ships
# "I don't wish to answer", and a plain `"not wish" in option` test misses it
# on the apostrophe, which silently turned every EEO default into a manual
# field on the most common portal there is.
_DECLINE = re.compile(
    r"decline"
    r"|prefer\s+not"
    r"|do\s*n.?t\s+wish"
    r"|(do\s+not|don.?t|not|choose\s+not|wish\s+not|rather\s+not)\s+(want\s+)?"
    r"(to\s+)?(self[\s-]?)?(wish|say|answer|disclose|specify|identify|state)"
    r"|no\s+answer"
    r"|not\s+(specified|disclosed)",
    re.I,
)

# ---------------------------------------------------------------------------
#  The legal backstop. Eligibility, EEO and pay, matched on the question's
#  own words, whole words only, locally. A field that matches never goes to
#  the model or to draft-and-flag, whatever the classifier said: it's
#  answered from a named profile line with an exact, yes/no or decline fit,
#  or it comes to you. The classifier needs the right words to route these;
#  this layer doesn't need the classifier at all.
# ---------------------------------------------------------------------------
LEGAL_GUARD: list[tuple[str, str]] = [
    (r"\bwork(ing)?\s+authori[sz]\w*|\bauthori[sz]\w*\s+to\s+(work|be\s+employed)"
     r"|\blegally\s+(authori[sz]\w*|entitled|permitted|eligible|able)"
     r"|\beligib\w*\s+to\s+work|\bright\s+to\s+work\b|\bwork\s+permits?\b"
     r"|\bsponsor\w*|\bpetition\w*|\bvisas?\b|\bimmigra\w*|\bcitizen\w*|\bnationality\b"
     r"|\bgreen\s+card\b|\bpermanent\s+residen\w*|\bh[\s\-\u2010-\u2015]?1[\s\-\u2010-\u2015]?b\b",
     "work_authorization"),
    (r"\bgender\w*|\bsex\b|\brace\b"
     r"|\bracial\b|\bethnic\w*|\bhispanic\b"
     r"|\blatin[oax]s?\b|\blatine\b|\bveterans?\b|\bdisab(led|ilit\w*)\b"
     r"|\bsexual\s+orientation\b|\btransgender\b|\bpronouns?\b",
     "eeo"),
    (r"\bsalar(y|ies)\b|\bpay\b|\bwages?\b|\bcompensation\b|\bremuneration\b"
     r"|\b(expected|desired)\s+(rate|pay|salary|wage|compensation|earnings|income)\b"
     r"|\bhourly\s+rate\b|\bote\b|\bctc\b",
     "compensation"),
]

# The fits a legal answer may take: the profile value as one of the form's
# options exactly, a yes / no (from a real true / false profile value), or
# a decline option.
LEGAL_FITS = {"exact", "bool", "decline"}

# Which profile lines answer which kind of guard match.
LEGAL_AREAS = {"work_authorization": "eligibility.", "eeo": "eeo.",
               "compensation": "compensation."}

# Invisible characters a page can put inside a word (soft hyphens, the
# combining grapheme joiner, zero-width spaces and joiners, word joiners
# and invisible operators, byte-order marks). NFKC
# keeps them, and "Spon\u00adsorship" (a soft hyphen) would otherwise slip past.
_INVISIBLE = re.compile("[\\u00ad\\u034f\\u200b-\\u200d\\u2060-\\u2064\\ufeff]")

_PATHS = [(re.compile(p, re.I), path) for p, path in PROFILE_PATHS]
_LEGAL = [(re.compile(p, re.I), kind) for p, kind in LEGAL_GUARD]
_STOPS = [(re.compile(p, re.I), why) for p, why, _ in HARD_STOPS]
STOP_KINDS = [(re.compile(p, re.I), kind) for p, _, kind in HARD_STOPS]


def _question_text(field: dict) -> str:
    """Everything visible about the question — some portals hide it in help text."""
    return " ".join(str(field.get(k) or "") for k in
                    ("label", "help_text", "placeholder", "name"))


def _identifier_words(name: str) -> str:
    """'requiresSponsorship' / 'desired_salary' -> separate words, for the guard."""
    name = re.sub(r"([a-z])([A-Z])", r"\1 \2", name)
    name = re.sub(r"([A-Z])([A-Z][a-z])", r"\1 \2", name)
    return re.sub(r"[_\-.\[\]]+", " ", name)


def legal_guard_kinds(field: dict) -> list[str]:
    """
    Every LEGAL_GUARD kind a field matches ("work_authorization", "eeo",
    "compensation"), in table order. Read on the label, help text,
    placeholder, name and option texts, NFKC-normalised with invisible
    characters removed; the name is split into words first. Local only.
    """
    text = " ".join(
        [str(field.get(k) or "") for k in ("label", "help_text", "placeholder")]
        + [_identifier_words(str(field.get("name") or ""))]
        + [str(o) for o in field.get("options") or []])
    text = _INVISIBLE.sub("", unicodedata.normalize("NFKC", text))
    return [kind for rx, kind in _LEGAL if rx.search(text)]


def legal_guard(field: dict) -> str | None:
    """The first LEGAL_GUARD kind a field matches, or None."""
    kinds = legal_guard_kinds(field)
    return kinds[0] if kinds else None


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

    # -- layer 2: a password input is refused on type alone, however labelled,
    # and so is any box the scan marked secret (toggled to text, hinted or
    # named as a password, or seen as one earlier in the run).
    if (field.get("type") or "").lower() == "password" or field.get("secret"):
        return {**base, "action": "human", "value": None, "source": "human",
                "reason": "Credential field. This tool never types passwords."}

    action = action_for(category)

    if action == "human":
        return {**base, "action": "human", "value": None, "source": "human",
                "reason": f"Classified as {category.replace('_', ' ')} — "
                          f"this one is yours to answer."}

    # -- layer 2b: the legal backstop. Whatever the classifier said, a
    # question that reads as eligibility, EEO or pay is answered only from a
    # profile line, with a strict fit, or by you.
    guards = legal_guard_kinds(field)
    guard = guards[0] if guards else None

    # -- layer 3: answers that must come from a named line in profile.yaml ---
    if action == "profile" or guard:
        kind = (guard or category).replace("_", " ")
        path = next((p for rx, p in _PATHS if rx.search(text)), None)
        if path is None:
            return {**base, "action": "human", "value": None, "source": "human",
                    "reason": f"This is a {kind} question, but "
                              f"I can't tell which profile line answers it. "
                              f"Answer it yourself."}
        if len(set(guards)) > 1:
            return {**base, "action": "human", "value": None, "source": "human",
                    "reason": "This reads as more than one kind of legal question "
                              f"({', '.join(g.replace('_', ' ') for g in guards)}). "
                              "Answer it yourself."}
        if guards and not path.startswith(tuple(LEGAL_AREAS[g] for g in guards)):
            return {**base, "action": "human", "value": None, "source": "human",
                    "reason": f"This reads as a {kind} question, but the closest "
                              f"profile line is `{path}`. Answer it yourself."}
        ftype = (field.get("type") or "").lower()
        if guard and not field.get("options") and ftype != "checkbox":
            return {**base, "action": "human", "value": None, "source": "human",
                    "reason": f"A {kind} question with a box to type in. "
                              f"Answer it yourself."}
        raw = profile.get(path)
        if raw is None:
            return {**base, "action": "human", "value": None, "source": "human",
                    "reason": f"`{path}` is blank in profile.yaml. Fill it in and "
                              f"this stops being manual."}
        value, fit = fit_with_kind(raw, field)
        if value is None or (guard and fit not in LEGAL_FITS):
            return {**base, "action": "human", "value": None, "source": "human",
                    "reason": f"Your answer is '{raw}' but this form offers "
                              f"{field.get('options') or 'a box to type in'}. "
                              f"Pick one yourself."}
        return {**base, "action": "fill", "value": value, "source": "profile",
                "profile_path": path, "fit": fit}

    # -- ordinary fields -----------------------------------------------------
    return {**base, "action": action, "value": None, "source": "model"}


def fit_to_widget(raw: Any, field: dict):
    """
    Shape a profile value to whatever widget the form actually uses. A yes/no
    in your file might arrive as radio buttons, a dropdown with oddly-worded
    options, or a checkbox. Returns None rather than forcing a bad match.
    """
    return fit_with_kind(raw, field)[0]


def fit_with_kind(raw: Any, field: dict) -> tuple[Any, str | None]:
    """
    fit_to_widget, plus HOW the value was fitted. The submit gate trusts only
    the strict kinds for legal answers: "exact", "bool", "decline" and
    "verbatim" (no options, the profile value as written). "bool_prefix" and
    "substring" are looser guesses at which option you meant. A checkbox
    takes only a real true / false: "Prefer not to say" is not a tick.
    """
    options = field.get("options") or []
    ftype = (field.get("type") or "").lower()

    if ftype == "checkbox":
        return (raw, "bool") if isinstance(raw, bool) else (None, None)

    if not options:
        if isinstance(raw, bool):
            return ("Yes" if raw else "No"), "bool"
        return str(raw), "verbatim"

    if isinstance(raw, bool):
        wanted = (("yes", "true", "y", "i am authorized", "authorized")
                  if raw else ("no", "false", "n", "not authorized"))
        for opt in options:
            if opt.strip().lower() in wanted:
                return opt, "bool"
        for opt in options:
            low = opt.strip().lower()
            if (raw and low.startswith("yes")) or (not raw and low.startswith("no")):
                return opt, "bool_prefix"
        return None, None

    target = str(raw).strip().lower()
    for opt in options:
        if opt.strip().lower() == target:
            return opt, "exact"
    for opt in options:
        low = opt.strip().lower()
        if target and (target in low or low in target):
            return opt, "substring"

    if _DECLINE.search(target):
        for opt in options:
            if _DECLINE.search(opt.lower()):
                return opt, "decline"
    return None, None
