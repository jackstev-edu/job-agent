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
    # A Department of Defense question has no profile line at all (Jack,
    # 2026-10-20, second set): the "dod" guard kind below sends every one
    # to Jack. The row that answered "employed by the DoD in the past two
    # years" was removed that day.
    # A security clearance: eligibility to obtain one, and the level held,
    # each from its own line. The "clearance" guard kind below holds every
    # clearance question to a strict fit from these two lines or sends it
    # to Jack (2026-10-20, second set). A question naming both ("eligible
    # to obtain a security clearance") matches both rows and is Jack's.
    # The row needs the word "eligible" (whole, so "ineligible" is not
    # it): willingness or ability to obtain one, or accepting that a role
    # requires one, is not eligibility and has no line (BLOCK fix).
    (r"\beligib\w*\s+(to\s+(obtain|get|hold|receive|be\s+granted)|for)\s+(a|an)?\s*(\w+\s+)?clearance"
     r"|clearance[\s-]+eligib\w*",
     "eligibility.clearance_eligible"),
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

# A profile VALUE that is itself a decline, as a whole: "Prefer not to say",
# "Decline to self-identify", "I don't wish to answer". Matched whole so a
# value that merely contains a decline phrase ("I do not identify as ...")
# is not read as one.
_DECLINE_VALUE = re.compile(
    r"(i\s+)?(would\s+)?(prefer\s+not\s+to\s+(say|answer|disclose|specify|identify|state|self[\s-]?identify)"
    r"|decline(\s+to\s+(self[\s-]?)?(identify|answer|state|say|disclose|specify))?"
    r"|do\s*n.?t\s+wish\s+to\s+(answer|say|disclose|identify|specify)"
    r"|no\s+answer|not\s+(specified|disclosed)|rather\s+not\s+(say|answer))\.?",
    re.I,
)

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
     r"|\bgreen\s+card\b|\bpermanent\s+residen\w*|\bh[\s\-\u2010-\u2015\u2212\u2043]?1[\s\-\u2010-\u2015\u2212\u2043]?b\b",
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
    # Added 2026-10-20 (Jack): a Department of Defense question is yours,
    # every one; no profile line answers this kind (second set, same day).
    (r"\bdepartment\s+of\s+defen[cs]e\b|\bdod\b", "dod"),
    # Added 2026-10-20 (Jack, second set): a security-clearance question is
    # answered only from the two clearance lines, with a strict fit, or by
    # you. Never by the model, whatever the classifier called it.
    (r"\bclearances?\b|\bts/sci\b|\btop\s+secret\b", "clearance"),
]

# The fits a legal answer may take: the profile value as one of the form's
# options exactly, a yes / no (from a real true / false profile value), or
# a decline option.
LEGAL_FITS = {"exact", "bool", "decline"}

# Which profile lines answer which kind of guard match. Work authorization
# is a fixed list, not the "eligibility." prefix: that prefix also holds
# relocation, driving and travel lines, and "Are you willing to relocate if
# we sponsor your visa?" must not be answered from willing_to_relocate.
LEGAL_AREAS = {
    "work_authorization": frozenset({
        "eligibility.requires_sponsorship_future",
        "eligibility.requires_sponsorship_now",
        "eligibility.authorized_to_work_us",
        "eligibility.work_authorization_type",
        "eligibility.export_control_us_person",
    }),
    "eeo": "eeo.",
    "compensation": "compensation.",
    # No line: a DoD question matching a profile line elsewhere (a clearance
    # row) is still Jack's, and one matching none names no line.
    "dod": frozenset(),
    "clearance": frozenset({"eligibility.security_clearance", "eligibility.clearance_eligible"}),
}


# Lines whose question must be asked by the label itself, not by help text
# beside another question. Only lines added on or after 2026-10-20; none
# at present (the Department of Defense line left the same day).
LABEL_ONLY_LINES: frozenset[str] = frozenset()


def in_legal_area(path: str | None, kinds) -> bool:
    """Is this profile line one that answers a guard match of any of these kinds?"""
    for kind in kinds:
        area = LEGAL_AREAS[kind]
        if (path in area) if isinstance(area, frozenset) else str(path or "").startswith(area):
            return True
    return False

_PATHS = [(re.compile(p, re.I), path) for p, path in PROFILE_PATHS]
_LEGAL = [(re.compile(p, re.I), kind) for p, kind in LEGAL_GUARD]
_STOPS = [(re.compile(p, re.I), why) for p, why, _ in HARD_STOPS]
STOP_KINDS = [(re.compile(p, re.I), kind) for p, _, kind in HARD_STOPS]


def is_invisible(ch: str) -> bool:
    """
    A character a page can put inside or between words without it showing:
    every Unicode format character (category Cf: soft hyphens, zero-width
    spaces and joiners, direction marks, bidi controls, invisible operators,
    tags, byte-order marks), the variation selectors U+FE00-U+FE0F and the
    combining grapheme joiner U+034F.
    """
    return (unicodedata.category(ch) == "Cf" or "\ufe00" <= ch <= "\ufe0f"
            or ch in "\u034f\u00ad")


# The invisible characters the previous normaliser deleted.
_EARLIER_INVISIBLE = re.compile("[\\u00ad\\u034f\\u200b-\\u200d\\u2060-\\u2064\\ufeff]")


# The page's own copy of the stop and guard checks (extract.py, the
# raw-label check) is built from these, so the patterns and the invisible
# characters have one source. INVISIBLE_JS is is_invisible as a JavaScript
# class (\p{Cf} needs the u flag).
INVISIBLE_JS = "[\\p{Cf}\\uFE00-\\uFE0F\\u034F\\u00AD]"
EARLIER_INVISIBLE_JS = _EARLIER_INVISIBLE.pattern
RAW_PATTERNS = {"stops": [p for p, _, _ in HARD_STOPS], "legal": [p for p, _ in LEGAL_GUARD]}


def text_variants(raw: str) -> list[str]:
    """
    Every reading of a page text the pattern tables are matched against:
    the text as written, then NFKC-normalised as is, with invisible
    characters deleted ("con\u00advict" -> "convict"), and with them as spaces
    ("Security\u200bPIN" -> "Security PIN"). A check that means stop,
    sensitive, guard or redact counts a hit on ANY of them, so normalising
    can only add matches, never lose one: the text as written is always
    still there.
    """
    raw = str(raw or "")
    nfkc = unicodedata.normalize("NFKC", raw)
    deleted = "".join(c for c in nfkc if not is_invisible(c))
    spaced = "".join(" " if is_invisible(c) else c for c in nfkc)
    # The reading the previous normaliser gave (NFKC, a narrower set
    # deleted), kept so no text that matched before can stop matching: a
    # word split by one invisible and bounded by another needs exactly it.
    earlier = _EARLIER_INVISIBLE.sub("", nfkc)
    # Whitespace collapsed to single spaces: a page's innerText puts line
    # breaks between block and flex children, and "." in a pattern can't
    # cross one ("I accept the\nTerms").
    collapsed = [re.sub(r"\s+", " ", t) for t in (deleted, spaced)]
    return list(dict.fromkeys([raw, nfkc, deleted, spaced, earlier, *collapsed]))


def _question_text(field: dict) -> str:
    """Everything visible about the question, as written — some portals hide it in help text."""
    return " ".join(str(field.get(k) or "") for k in
                    ("label", "help_text", "placeholder", "name"))


def stop_reason(text: str) -> str | None:
    """The HARD_STOPS reason that any reading of `text` hits, or None."""
    variants = text_variants(text)
    return next((why for rx, why in _STOPS if any(rx.search(v) for v in variants)), None)


# The HARD_STOPS entry about a criminal record, found by its reason rather
# than its position in the table.
_RECORD_STOP = next(re.compile(p, re.I) for p, why, _ in HARD_STOPS if "your record" in why)


def record_stop(text: str) -> bool:
    """Does this one reading of a text match the criminal-record stop?"""
    return bool(_RECORD_STOP.search(str(text or "")))


def stop_kinds(text: str) -> set[str]:
    """Every HARD_STOPS kind any reading of `text` hits."""
    variants = text_variants(text)
    return {kind for rx, kind in STOP_KINDS if any(rx.search(v) for v in variants)}


# The two sponsorship lines are the one pair a question regularly matches
# both of ("will you now or in the future require sponsorship" hits the
# future line, "do you require sponsorship to begin" the now line, and
# plenty of wordings hit both patterns). Jack approved deciding between
# them on which word the question actually uses.
SPONSORSHIP = {"future": "eligibility.requires_sponsorship_future",
               "now": "eligibility.requires_sponsorship_now"}
_SAYS_FUTURE = re.compile(r"future|later|any\s+time", re.I)
_SAYS_NOW = re.compile(r"\bnow\b|currently|\bbegin\b|commenc", re.I)
# The sponsorship clause: the sentence that actually asks about sponsorship.
_SPONSOR_WORD = re.compile(r"sponsor|immigration|petition|work\s+permit|visa", re.I)
_SENTENCE_END = re.compile(r"[.?!;:\r\n]+")


def sponsorship_line(text: str, lines: set[str]) -> str | None:
    """
    Which of the two sponsorship lines a question is asking about, when it
    matches both. "Future" with no "now" word is the future line; a "now"
    word with no "future" is the now line. Both words, or neither, is
    still yours: the question is asking about both times, or we can't tell.
    The deciding word must sit in the same sentence as the sponsorship
    word ("Will you require sponsorship? Think about the future." decides
    nothing), while a word of the other kind anywhere in the text still
    blocks the decision. None when this isn't that pair.
    """
    if lines != set(SPONSORSHIP.values()):
        return None
    variants = text_variants(text)
    future_anywhere = any(_SAYS_FUTURE.search(v) for v in variants)
    now_anywhere = any(_SAYS_NOW.search(v) for v in variants)
    # NFKC first, so a fullwidth question mark ends a sentence like a plain
    # one (NFKC never removes a sentence end, only adds them); each sentence
    # is then read every way, so an invisible character inside a word still
    # can't hide it. Line breaks survive NFKC, so a bare one ends a sentence.
    sentences = _SENTENCE_END.split(unicodedata.normalize("NFKC", str(text or "")))
    clauses = [c for s in sentences for c in text_variants(s) if _SPONSOR_WORD.search(c)]
    future_in_clause = any(_SAYS_FUTURE.search(c) for c in clauses)
    now_in_clause = any(_SAYS_NOW.search(c) for c in clauses)
    if future_in_clause and not now_anywhere:
        return SPONSORSHIP["future"]
    if now_in_clause and not future_anywhere:
        return SPONSORSHIP["now"]
    return None


def profile_lines(text: str) -> list[str]:
    """
    The PROFILE_PATHS line each reading of `text` resolves to (first match
    in table order), distinct, in reading order. More than one means the
    readings disagree about which line answers the question.
    """
    out = []
    for v in text_variants(text):
        path = next((p for rx, p in _PATHS if rx.search(v)), None)
        if path and path not in out:
            out.append(path)
    return out


def all_profile_lines(text: str) -> set[str]:
    """
    Every PROFILE_PATHS line any reading of `text` matches, not just the
    first in table order. Can only add matches: two or more means the
    question touches more than one profile line.
    """
    variants = text_variants(text)
    return {p for rx, p in _PATHS if any(rx.search(v) for v in variants)}


def sensitive_line(text: str, prefixes: tuple[str, ...]) -> str | None:
    """A PROFILE_PATHS line under `prefixes` that any reading of `text` matches."""
    for v in text_variants(text):
        hit = next((p for rx, p in _PATHS if rx.search(v) and p.startswith(prefixes)), None)
        if hit:
            return hit
    return None


def _identifier_words(name: str) -> str:
    """'requiresSponsorship' / 'desired_salary' -> separate words, for the guard."""
    name = re.sub(r"([a-z])([A-Z])", r"\1 \2", name)
    name = re.sub(r"([A-Z])([A-Z][a-z])", r"\1 \2", name)
    return re.sub(r"[_\-.\[\]]+", " ", name)


# A clearance question about getting one rather than holding one, on any
# reading of the question as asked.
_ASKS_TO_OBTAIN = re.compile(r"\b(willing\w*|able|unable|obtain\w*|accept\w*|get|acquir\w*)\b", re.I)


def asked_text(field: dict) -> str:
    """The question as asked (label, help, placeholder), every reading joined."""
    return " ".join(text_variants(" ".join(str(field.get(k) or "") for k in ("label", "help_text", "placeholder"))))


def legal_guard_kinds(field: dict) -> list[str]:
    """
    Every LEGAL_GUARD kind a field matches ("work_authorization", "eeo",
    "compensation"), in table order. Read on the label, help text,
    placeholder, name and option texts, every reading of them
    (text_variants); the name is split into words first. Local only.
    """
    text = " ".join(
        [str(field.get(k) or "") for k in ("label", "help_text", "placeholder")]
        + [_identifier_words(str(field.get("name") or ""))]
        + [str(o) for o in field.get("options") or []])
    variants = text_variants(text)
    return [kind for rx, kind in _LEGAL if any(rx.search(v) for v in variants)]


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

    # -- layer 1: the backstop overrides everything, on any reading ---------
    why = stop_reason(text)
    if why:
        return {**base, "action": "human", "value": None,
                "source": "human", "reason": why}
    # The page's own check of the label and help read whole, before any
    # value display was skipped (a "background check" styled as a tag).
    # Only a true / false came back from the page.
    if field.get("raw_stop"):
        return {**base, "action": "human", "value": None, "source": "human",
                "reason": "Read in full on the page, this question matches a stop "
                          "(terms, consent, a credential or a legal declaration)."}

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
    if field.get("raw_legal") and not guards:
        return {**base, "action": "human", "value": None, "source": "human",
                "reason": "Read in full on the page, this reads as an eligibility, "
                          "EEO or pay question. Answer it yourself."}

    # -- layer 3: answers that must come from a named line in profile.yaml ---
    if action == "profile" or guard:
        kind = (guard or category).replace("_", " ")
        if len(set(guards)) > 1:
            return {**base, "action": "human", "value": None, "source": "human",
                    "reason": "This reads as more than one kind of legal question "
                              f"({', '.join(g.replace('_', ' ') for g in guards)}). "
                              "Answer it yourself."}
        lines = profile_lines(text)
        every = all_profile_lines(text)
        # The sponsorship pair, decided by the word the question uses.
        only = sponsorship_line(text, every | set(lines))
        if only is not None:
            lines, every = [only], {only}
        if len(every) > 1 and len(lines) == 1:
            # The question as asked (label, help, placeholder) names one line
            # and only the field's own identifier names another, in the same
            # area (Greenhouse calls its "Are you Hispanic/Latino?" select
            # hispanic_ethnicity): the question's line answers it. An
            # identifier from another area, or a question naming two lines
            # itself, is still yours (Jack, 2026-10-20).
            asked = all_profile_lines(" ".join(str(field.get(k) or "")
                                               for k in ("label", "help_text", "placeholder")))
            area = lines[0].split(".")[0]
            if asked == {lines[0]} and all(line.split(".")[0] == area for line in every):
                every = {lines[0]}
        if len(lines) > 1 or len(every) > 1:
            return {**base, "action": "human", "value": None, "source": "human",
                    "reason": f"This could be answered by more than one profile line "
                              f"({', '.join(sorted(every | set(lines)))}). Answer it yourself."}
        path = lines[0] if lines else None
        if path is None:
            return {**base, "action": "human", "value": None, "source": "human",
                    "reason": f"This is a {kind} question, but "
                              f"I can't tell which profile line answers it. "
                              f"Answer it yourself."}
        if guards and not in_legal_area(path, guards):
            return {**base, "action": "human", "value": None, "source": "human",
                    "reason": f"This reads as a {kind} question, but the closest "
                              f"profile line is `{path}`. Answer it yourself."}
        if path == "eligibility.security_clearance" and _ASKS_TO_OBTAIN.search(asked_text(field)):
            # The level held answers "what level do you hold", never
            # "what level are you willing / able to obtain" (2026-10-20).
            return {**base, "action": "human", "value": None, "source": "human",
                    "reason": "This asks about obtaining a clearance, not the level held. "
                              "Answer it yourself."}
        if path in LABEL_ONLY_LINES and path not in profile_lines(str(field.get("label") or "")):
            # A line whose question must be the label itself: help text that
            # mentions the same thing beside another question doesn't make
            # that question this one (2026-10-20).
            return {**base, "action": "human", "value": None, "source": "human",
                    "reason": f"This is a {kind} question, but only its help text names "
                              f"`{path}`. Answer it yourself."}
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
    # A value that is itself a decline ("Prefer not to say"), as a whole,
    # takes the form's decline option before any substring guess: "No" sits
    # inside "not", and the substring step used to take it, turning every
    # EEO default into a loose fit the gate refuses (found on Greenhouse's
    # EEO selects, 2026-10-20).
    if _DECLINE_VALUE.fullmatch(target):
        for opt in options:
            if _DECLINE.search(opt.lower()):
                return opt, "decline"
    for opt in options:
        low = opt.strip().lower()
        if target and (target in low or low in target):
            return opt, "substring"
    if _DECLINE.search(target):
        for opt in options:
            if _DECLINE.search(opt.lower()):
                return opt, "decline"
    return None, None
