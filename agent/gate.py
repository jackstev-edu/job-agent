"""
gate.py — would this application have been safe to submit unattended?

WHAT THIS DOES: A pure, deterministic function of one fill run's record (from
shadow.py) and your profile. It returns a content verdict, would_submit or
would_stop, from what's on the form, and separately whether this portal and
today's cap would even allow it. It makes no model calls, reads no page,
writes nothing, and clicks nothing. Every reason names the field or condition.
WHY IT MATTERS: This is shadow mode. Nothing is ever submitted for you; the
verdict is logged and later scored against what you actually did. So it has
to be strict and explainable: it defaults to stop on anything missing,
unknown or unreadable, and page text only ever fails an exact comparison
with something you wrote, never passes one.
"""

from __future__ import annotations

import json
import re
import unicodedata
from typing import Any

from . import employers, sensitive, signdate

# 2: census checkboxes need a <label> or aria-label; a hidden ticked box stops.
# 3: an unread tick counts as ticked; a button pair's hidden box is exempt only
#    when its tick matches the group's planned answer.
# 4: that exemption needs the group's only box, hidden, unticked before the
#    fill and ticked after it, for a planned "Yes". Every other box in a group
#    meets the ordinary label and allowlist checks.
# 5: a pair planned "No" is exempt when its only box stays unticked; both
#    exemptions need the same element before and after, and every box with
#    the group_ref counts, grouped or not.
# 6: every check reads every reading of the text (as written, NFKC,
#    invisibles deleted / as spaces, the earlier deletion, whitespace
#    collapsed); a LEGAL_GUARD match is a legal field that needs a strict
#    fit (exact, bool, decline) from a profile line in the same area
#    (work authorization from an explicit set of lines), and stops when it
#    spans two or more areas; the page's raw_stop / raw_legal flags make a
#    field legal; a dropdown fill the page couldn't confirm stops.
# 7: the owner decisions of 2026-10-18. A checkbox whose label passes the
#    consent policy (terms / privacy wording only) counts like an
#    allowlisted one; a field in the record's human_reviewed list (set by
#    the wizard after a pause Jack ended; since 2026-10-19 only for a field
#    he touched, a touch mark new since before the pause) is his answer and
#    skips the field-level stops, never the required-but-empty check; an
#    event marked handled by the agent (a
#    sign-in, a Next press) or by Jack (a CAPTCHA, a pause) doesn't stop,
#    a CAPTCHA only when Jack handled it; more than one step is fine when
#    the record says it is a wizard run.
# 8: optional empties (Jack, 2026-10-20, third set; built 2026-10-23). On
#    a field the scan marks optional (required exactly False; a missing
#    key counts as required; no trailing *), that the gate's sensitive
#    test doesn't flag, and that the page reads back empty, these reasons are listed
#    under "waived" instead of stopping: "left blank", "not answered", an
#    unanswered per-company question (not a checkbox: unticked asserts
#    "No"), a field (no checkbox) handed over only because its category
#    is unknown, and an optional checkbox left unticked whose census box
#    the waiver may cover. A census box is waived only on its allowlist
#    reason, only when it is visible, unticked, plainly labelled (a
#    <label> or aria-label, the same rendered as written, no fine print),
#    its label is no question, has no trailing * and its required key is
#    exactly False (no key, or any other value, counts as required; the
#    census reads the attribute, aria-required and a trailing *).
#    Required fields, legal / EEO /
#    pay questions, every other flag, failed, unconfirmed and
#    low-confidence fills still stop. "Required" is page data (the
#    attribute, aria-required, a trailing *), accepted as such.
GATE_VERSION = 8

WOULD_SUBMIT, WOULD_STOP = "would_submit", "would_stop"

# Questions whose answer must be traced to a named profile line.
SENSITIVE_CATEGORIES = {"work_authorization", "eeo", "compensation", "legal_declaration"}
SENSITIVE_PREFIXES = ("eligibility.", "eeo.", "compensation.")

# How a profile value may have been matched to the form's options and still
# count as yours, as written. "bool_prefix" and "substring" are guesses.
STRICT_FITS = {"exact", "bool", "decline", "verbatim"}

# Where a checkbox's label must come from for consent_allowlist to cover it:
# a real <label> element (for= or wrapping) or aria-label. One rule for
# checkbox fields and for every box in the census (custom role=checkbox or
# switch, hidden native boxes). aria-labelledby can point at any text on the
# page and a custom box's own text can be anything, so neither qualifies;
# nor does the scan's nearby-text fallback.
ALLOWLIST_LABEL_SOURCES = {"for", "wrap", "aria-label"}

# Run events that are logged but never stop the run.
INFORMATIONAL_EVENTS = {"hidden_iframe"}

PER_COMPANY_REASON = "per-company question; profile answering not enabled"

# Frames the gate knows are not part of the form (invisible reCAPTCHA).
_KNOWN_FRAME = re.compile(r"^https://www\.(google|recaptcha)\.(com|net)/recaptcha/", re.I)


def normalize_label(text: Any) -> str:
    """NFKC, casefold, collapse whitespace, drop a trailing required '*'. Nothing else."""
    t = unicodedata.normalize("NFKC", str(text or "")).casefold()
    t = re.sub(r"\s+", " ", t).strip()
    return t[:-1].rstrip() if t.endswith("*") else t


def short_label(label: Any) -> bool:
    """Three words or fewer (a word holds a letter or digit): the labels a key needs a section for."""
    return len([w for w in normalize_label(label).split() if re.search(r"[^\W_]", w)]) <= 3


def question_key(field: dict) -> str:
    """
    The question as asked, in the allowed normalisation and nothing
    else: the label, the field type, for a choice field the sorted
    option texts, and for a label of three words or fewer the field's
    section context (the scan's value-free legend / group label /
    heading; Jack, 2026-11-02, item 2: a bare "Year" is two questions
    under two sections). A learned answer (memory.py) is reused on this
    key alone, and the gate recomputes it here from the record's own
    field to trust a "learned" legal answer (Jack, 2026-11-01).
    """
    label = normalize_label(field.get("label"))
    options = sorted(normalize_label(o) for o in (field.get("options") or []) if str(o or "").strip())
    key: list = [label, normalize_label(field.get("type")), options]
    if short_label(label):
        key.append(normalize_label(field.get("section")))
    return json.dumps(key, ensure_ascii=False)


def _same_text(a: Any, b: Any) -> bool:
    return re.sub(r"\s+", " ", str(a or "")).strip() == re.sub(r"\s+", " ", str(b or "")).strip()


def _question_text(f: dict) -> str:
    # The policy's own text, normaliser included, so the gate reads a
    # question exactly as sensitive.decide did.
    return sensitive._question_text(f)


def _stop_kinds(f: dict) -> set[str]:
    return sensitive.stop_kinds(_question_text(f))


def _sensitive_line(f: dict) -> str | None:
    return sensitive.sensitive_line(_question_text(f), SENSITIVE_PREFIXES)


def _is_required(f: dict) -> bool:
    return bool(f.get("required")) or str(f.get("label") or "").rstrip().endswith("*")


def _box_sensitive(box: dict) -> bool:
    """
    Is this census box's label itself a question (a HARD_STOPS kind, the
    per-company one included; a LEGAL_GUARD or PROFILE_PATHS match) on
    any reading of its rendered or written text? Such a box answers by
    being ticked, and unticked it asserts "No", so the optional-empties
    waiver never covers it.
    """
    label = f"{box.get('label_inner') or ''} {box.get('label_content') or ''}"
    return bool(sensitive.stop_kinds(label)
                or sensitive.sensitive_line(label, SENSITIVE_PREFIXES) is not None
                or sensitive.legal_guard({"label": label}) is not None
                or sensitive.all_profile_lines(label))


def _optional_box(box: dict | None) -> bool:
    """
    A census box the optional-empties waiver (version 8) may cover: the
    page marks it optional on every reading (required exactly False, no
    trailing *), it is visible and read unticked, not a button pair's,
    its label comes from a <label> or aria-label and reads the same
    rendered as written with no fine print, and that label is no
    question. A missing box, key or reading fails.
    """
    if not isinstance(box, dict):
        return False
    return (box.get("required") is False and box.get("checked") is False
            and box.get("visible") is True and not box.get("in_button_group")
            and not box.get("group_ref")
            and box.get("label_source") in ALLOWLIST_LABEL_SOURCES
            and _same_text(box.get("label_inner"), box.get("label_content"))
            and not box.get("described") and not _starred(box) and not _box_sensitive(box))


def _starred(box: dict) -> bool:
    """A census label ending in '*' marks the box required, whatever its key says."""
    return any(str(box.get(k) or "").rstrip().endswith("*")
               for k in ("label_inner", "label_content"))


def _present(value: Any) -> bool:
    if value is None or value is False:
        return False
    if isinstance(value, (list, tuple)):        # a file box reads back as its file names
        return any(_present(v) for v in value)
    return bool(str(value).strip())


def _allowlist_index(box: dict | None, allow: list[str]) -> int | None:
    """
    The consent_allowlist entry a checkbox exactly matches, or None. Its label
    must come from a <label> or aria-label, read the same rendered as written
    (nothing hidden with CSS), carry no fine print, and normalise to exactly
    one of your entries. No substring or model matching.
    """
    if not box or box.get("label_source") not in ALLOWLIST_LABEL_SOURCES:
        return None
    if normalize_label(box.get("label_inner")) != normalize_label(box.get("label_content")):
        return None
    if box.get("described"):
        return None
    label = normalize_label(box.get("label_inner"))
    return next((i for i, entry in enumerate(allow) if normalize_label(entry) == label), None)


# --- the consent policy (owner decision, 2026-10-18; docs/PLAN-autonomy.md, section 3)
# A terms-of-service / privacy-policy consent box may be ticked; marketing,
# newsletter, SMS and talent-community opt-ins never; nor a certification,
# a signature or anything else HARD_STOPS calls legal. Judged on every
# reading of the label (sensitive.text_variants): the allow words must be
# found on some reading, and no deny word on any. Page text can't make a
# box anything but a consent box here, and it can only ever lead to a tick
# of a consent box, never to an answer.
CONSENT_AGREE = re.compile(
    r"\b(i\s+)?(agree|accept|consent|acknowledge|have\s+read|read\s+and\s+(agree|accept|understood?))\b",
    re.I)
CONSENT_DOCUMENT = re.compile(
    r"\b(terms(\s+(of\s+(use|service)|and\s+conditions|&\s+conditions))?"
    r"|conditions|privacy\s+(policy|notice|statement)|data\s+(processing|protection|privacy)"
    r"|cookie\s+policy|user\s+agreement)\b", re.I)
CONSENT_MARKETING = re.compile(
    r"newsletter|marketing|promotion|\bsms\b|text\s+messag|talent\s+(community|network|pool)"
    r"|job\s+alert|updates?\s+(about|on|from)|keep\s+me\s+(informed|updated|posted)"
    r"|subscri|communications?\s+(from|about)|contact\s+me|receive\s+(e-?mails?|news|offers)"
    r"|opt[\s-]?in|notify\s+me|future\s+(opportunities|openings|roles|positions)|recruit", re.I)
CONSENT_CERTIFY = re.compile(
    r"certif|attest|signature|\bsign(ed|ing)?\b|true|accurate|correct|complete\b|penalty"
    r"|perjury|background\s+check|drug|criminal|convict|felon|misdemean|arrest|authori[sz]e"
    r"|arbitrat|waive|release|indemnif|non-?compete|reference\s+check|verify|investigat"
    r"|my\s+(answers|responses|information)\s+(are|is)", re.I)
# Words that make a consent label an answer to a question about age or
# anything else a person declares: never ticked.
CONSENT_DECLARE = re.compile(
    r"\b(age|aged|years?\s+old|older|over\s+\d+|at\s+least\s+\d+|\d{2}\+?|eligible|eligib\w*"
    r"|legally|not\s+require|i\s+am\s+(a|an|not)\b|i\s+(do|have|will)\s+not|confirm\s+(that\s+)?i)",
    re.I)
CONSENT_MAX_CHARS = 240


def consent_ok(label: Any) -> bool:
    """
    Does this checkbox label read as agreeing to terms / privacy and nothing
    else? True only when some reading has an agreement word and a document
    word, no reading has a marketing, certification or legal word, and the
    label is short. Nothing about the box's state or fine print is judged
    here (see consent_box_ok).
    """
    text = str(label or "")
    if not text.strip() or len(text) > CONSENT_MAX_CHARS:
        return False
    variants = sensitive.text_variants(text)
    if any(CONSENT_MARKETING.search(v) or CONSENT_CERTIFY.search(v) or CONSENT_DECLARE.search(v)
           for v in variants):
        return False
    if any(sensitive.stop_kinds(v) - {"legal"} for v in variants):
        return False
    # Anything that reads as eligibility, EEO or pay (LEGAL_GUARD, or a
    # PROFILE_PATHS line) is a question, and a tick would answer it.
    if (sensitive.legal_guard({"label": text}) is not None
            or sensitive.sensitive_line(text, SENSITIVE_PREFIXES) is not None
            or sensitive.all_profile_lines(text)):
        return False
    # The one HARD_STOPS legal pattern a consent box is allowed to match is
    # the terms / privacy one; a criminal-record or declaration match says no.
    if any(sensitive.record_stop(v) for v in variants):
        return False
    return any(CONSENT_AGREE.search(v) and CONSENT_DOCUMENT.search(v) for v in variants)


# --- the attestation class (decision D, Jack, 2026-11-02) -------------------
# A REQUIRED checkbox may be ticked, under submit.py's tick helper as the
# consent policy is, when EVERY sentence of its label is one of: (1) an
# attestation that the information Jack provided in this application is
# true / accurate / correct / complete (optionally "to the best of my
# knowledge"); (2) a statement that a false statement, misrepresentation
# or omission may lead to rejection, withdrawal of an offer or dismissal.
# "This can be ticked for similar wording too. It won't always be the
# same": each sentence kind is a fixed pattern (Jack, 2026-11-03): the
# slots are bounded word lists in a fixed order, so "similar wording"
# (another verb, another truth word, a lead-in, "to the best of my
# knowledge") passes and a clause that is no attestation and no
# consequence of misrepresentation (an at-will clause, a declaration
# about Jack, an agreement) does not, whatever connective joins it. Any
# other clause makes it Jack's (ATTEST_FORBIDDEN: authorize, consent to,
# agree to, background / reference / drug / credit check, release of
# information, arbitration, at-will, electronic signature / signing,
# contact / SMS / marketing / newsletter, non-compete); so does any
# LEGAL_GUARD, PROFILE_PATHS or CONSENT_MARKETING match, any HARD_STOPS
# kind but the one certify / attest / acknowledge entry (read past ONLY
# inside this check, for that one tick: Jack's answer on the plan), and
# the criminal-record pattern. Never an optional box. Page text can only
# fail this check.
# The subject of an attestation comes from a closed list naming Jack's
# own answers (Jack, 2026-11-03, the third fix of c8a18cf's BLOCK): the
# information / statements / answers / responses he provided, gave,
# submitted or entered, optionally "in this application"; "my answers";
# "my application". A subject that points at text on the page ("the
# facts set forth below", "the statements contained herein", "the
# following") certifies the page's words, not his, and is his: those
# words are forbidden outright and the subject slot has no place for
# them. "made" names his answers only with "I have" or "in this
# application" beside it.
_LEAD = r"(?:by (?:checking|ticking|selecting|clicking) (?:this|the) box )?"
_INFO = r"(?:information|statements?|answers?|responses?|details|representations?)"
_GIVEN = r"(?:provided|given|supplied|submitted|entered|furnished)"
_WHERE = r" (?:in|on|within|as part of|throughout) (?:this|my|the)(?: employment| job)? application(?: form| process)?"
# The subject slot. Each alternative opens with a different word, so the
# pattern never has two ways to read one text.
_SUBJECT = (r"(?:"
            r"(?:the |this )?" + _INFO +                                 # "the information ..." needs a source or a place
            r"(?:"
            r" (?:i have |i've |that i have )(?:" + _GIVEN + r"|made)(?: by me)?(?:" + _WHERE + r")?"
            r"| " + _GIVEN + r"(?: by me)?(?:" + _WHERE + r")?"
            r"| made(?: by me)?" + _WHERE +
            r"|" + _WHERE +
            r")"
            r"|my " + _INFO +                                               # "my answers ..." names his own
            r"(?:"
            r" (?:i have |i've |that i have )(?:" + _GIVEN + r"|made)(?: by me)?(?:" + _WHERE + r")?"
            r"| " + _GIVEN + r"(?: by me)?(?:" + _WHERE + r")?"
            r"| made(?: by me)?" + _WHERE +
            r"|" + _WHERE +
            r")?"
            r"|my application"
            r")")
_TRUTH = r"(?:true|accurate|correct|complete|truthful|factual|honest)"
_TRUTHS = (r"(?: (?:completely|fully|entirely))? " + _TRUTH
           + r"(?:(?: and| or)?(?: (?:completely|fully|entirely))? " + _TRUTH + r")*")
_BEST = r"(?: (?:to|according to) the best of my (?:knowledge(?: and belief)?|belief))?"
ATTEST_CLASS = re.compile(
    "^" + _LEAD + r"i (?:hereby )?(?:also )?(?:certify|attest|acknowledge|affirm|declare|confirm|state|swear"
    r"|represent|verify|understand)(?: that)?(?: (?:all|each|every|any))?(?: of)? "
    + _SUBJECT + r" (?:is|are|has been|have been|was|were)(?: (?:all|both))?" + _TRUTHS + _BEST + "$")
_FALSE_NP = (r"(?:(?:any|a|the|such) )?(?:(?:material )?(?:misrepresentations?|omissions?(?: of (?:a )?material facts?)?"
             r"|falsifications?|misstatements?)|(?:false|untrue|inaccurate|incorrect|incomplete|misleading|fraudulent"
             r"|dishonest) (?:statements?|information|answers?|representations?|responses?))")
_CONSEQ_NP = (r"(?:(?:the|my|an|a|immediate) )*(?:rejection|refusal|denial|disqualification|withdrawal|rescission"
              r"|revocation|dismissal|discharge|termination|cancellation)(?: of (?:my |the |an |any |this )?"
              r"(?:application|candidacy|offer(?: of employment)?|employment|consideration|job offer))?"
              r"(?: from (?:further )?consideration)?")
CONSEQ_CLASS = re.compile(
    r"^(?:i (?:also )?(?:understand|acknowledge)(?: that)? )?" + _FALSE_NP + r"(?:(?: or| and)? " + _FALSE_NP + r")*"
    r"(?: (?:made )?(?:in|on) (?:this|my|the)(?: employment| job)? application)?"
    r" (?:may|will|can|could|shall|would) (?:result in|lead to|be (?:grounds|cause|sufficient cause|a basis) for"
    r"|constitute grounds for|cause) " + _CONSEQ_NP + r"(?:(?: or| and)? " + _CONSEQ_NP + r")*"
    r"(?: (?:even )?if (?:discovered|found)(?: after (?:hire|hiring|employment))?)?$")
ATTEST_FORBIDDEN = re.compile(
    r"authori[sz]|consent\s+to|agree\s+to|agreement|(background|reference|drug|credit|criminal|employment|"
    r"education|security|medical)\s+(check|screen|investigat|verif|test)|\bchecks?\b|\bscreen|release|arbitrat|"
    r"at[\s-]will|with\s+or\s+without|without\s+(cause|notice)|for\s+any\s+reason|\bam\s+not\b|\bi\s+am\b|"
    r"(terminat|dismiss|discharg)\w*\s+(of\s+\w+\s+)?at\s+any\s+time|at\s+any\s+time\s+(by|regardless)|"
    r"electronic|signature|\bsign(ed|ing|s)?\b|e-?sign|\bcontact|\bsms\b|text\s+messag|marketing|"
    r"newsletter|non-?compete|waive|indemnif|investigat|\bpolicy|\bterms\b|privacy|conditions|employee\s+handbook|"
    r"\bage\b|\byears\s+old|eligib|legally|\bvisa|sponsor|"
    # A subject that points at text on the page, not at Jack's answers
    # (Jack, 2026-11-03): the words themselves are forbidden.
    r"set\s+forth|\bherein\b|\bhereto\b|\bbelow\b|\babove\b|\bthe\s+facts?\b|\bthe\s+following\b|\bcontained\b|"
    r"\bstated\b|\bthis\s+form\b|\bthe\s+form\b|\battached\b|\benclosed\b", re.I)
ATTEST_MAX_CHARS = 600
_SENTENCE_SPLIT = re.compile(r"[.!?;]+")
# A sentence's clauses: cut at an "and" / "or" (a comma before it or not)
# that opens a new statement with "I", "that" or "my"; each clause must be
# one of the two grammars on its own.
_CLAUSE_SPLIT = re.compile(r"(?:,\s*)?\b(?:and|or)\s+(?=(?:i|that|my)\b)", re.I)
# Characters a label of the class may hold: letters (any script: a non-Latin
# letter then fails the grammar), digits (likewise), spaces and plain
# punctuation. An emoji, a symbol or an invisible character refuses. One
# character class, matched whole: linear in the label's length, with no
# nested or overlapping quantifier (Jack, 2026-11-03; the earlier
# `(?:sep+ word*)*` shape backtracked exponentially on a run of
# punctuation before a symbol). `\w` admits the underscore, so that is
# refused apart.
_ALLOWED_CHARS = re.compile(r"[\w\s.,;:!?'\"()\u2019\u2018\u201c\u201d-]*", re.UNICODE)


def _attestation_clause_ok(clause: str) -> bool:
    """One clause, normalised (casefold, commas and quotes out, spaces collapsed, a leading "that" off): one of the two grammars, whole."""
    text = clause.casefold().replace("\u2019", "'").replace("\u2018", "'")
    text = re.sub(r"[,\"()\u201c\u201d:]", " ", text)
    text = re.sub(r"\s+", " ", text).strip(" -'")
    text = re.sub(r"^that ", "", text)
    if not text:
        return False
    return bool(ATTEST_CLASS.fullmatch(text) or CONSEQ_CLASS.fullmatch(text))


def _attestation_sentence_ok(sentence: str) -> bool:
    """One sentence, clause by clause (one of Jack's variants joins both classes in one sentence); an empty piece says nothing."""
    if not sentence.strip():
        return True
    return all(_attestation_clause_ok(c) for c in _CLAUSE_SPLIT.split(sentence))


# The attestation route is OFF (Jack, 2026-11-03): the fourth BLOCK on
# decision D's check (16b6ac3's review: "the statements in this
# application" still passed, a subject that can point at a sentence
# printed on the page) used up the three fix attempts Jack allowed, and
# his recorded consequence is that the check returns False, with E and
# Always tick kept. The grammar below stays as the record of the attempt
# (`_attestation_grammar_ok`, reachable by tests only); no tick, plan or
# offer reads it while this is False. Turning it back on is Jack's call.
ATTESTATION_ROUTE_ON = False


def attestation_ok(label: Any) -> bool:
    """
    Decision D's class, as the gate and submit.py's tick helper read it:
    False for every label while ATTESTATION_ROUTE_ON is False (Jack,
    2026-11-03), so no attestation box is ever planned as a tick or
    ticked. With the route on it would be _attestation_grammar_ok.
    """
    if not ATTESTATION_ROUTE_ON:
        return False
    return _attestation_grammar_ok(label)


def _attestation_grammar_ok(label: Any) -> bool:
    """
    The check behind the route: does every sentence of this label read as
    a truth-and-completeness attestation or a consequence-of-
    misrepresentation statement, by the two fixed grammars, with no
    forbidden clause, no marketing, legal-guard or profile-line match,
    no HARD_STOPS kind but the certify / attest / acknowledge entry and
    no criminal-record match, on any reading of the text? Nothing about
    the box's state, requiredness or fine print is judged here (see
    attestation_box_ok). Known hole (16b6ac3's review): a bare place
    ("the statements in this application") still passes.
    """
    text = str(label or "")
    # The length first, before any pattern runs (Jack, 2026-11-03): over
    # ATTEST_MAX_CHARS is his, whatever it says.
    if not text.strip() or len(text) > ATTEST_MAX_CHARS:
        return False
    variants = sensitive.text_variants(text)
    if any(len(v) > ATTEST_MAX_CHARS for v in variants):
        return False
    if any(CONSENT_MARKETING.search(v) or ATTEST_FORBIDDEN.search(v) for v in variants):
        return False
    if any(sensitive.stop_kinds(v) - {"legal"} for v in variants):
        return False
    if any(sensitive.record_stop(v) for v in variants):
        return False
    if (sensitive.legal_guard({"label": text}) is not None
            or sensitive.sensitive_line(text, SENSITIVE_PREFIXES) is not None
            or sensitive.all_profile_lines(text)):
        return False
    # Every reading must pass sentence by sentence: an invisible character,
    # an emoji or a symbol on any reading refuses, and a non-Latin letter
    # or a digit fails the grammar.
    for v in variants:
        if "_" in v or not _ALLOWED_CHARS.fullmatch(v):
            return False
        if not all(_attestation_sentence_ok(piece) for piece in _SENTENCE_SPLIT.split(v)):
            return False
    return True


# The policy reads only a visible <label> (for= or wrapping): an aria-label
# is text Jack never sees, so it can satisfy only the exact allowlist.
POLICY_LABEL_SOURCES = {"for", "wrap"}


def attestation_box_ok(box: dict | None) -> bool:
    """
    A census box decision D may tick: the page marks it required on some
    reading (the census's required key, from the attribute, aria-required
    or a trailing *), its label comes from a visible <label>, rendered the
    same as written, no fine print attached, and passes attestation_ok.
    """
    if not box or box.get("required") is not True or box.get("label_source") not in POLICY_LABEL_SOURCES:
        return False
    if normalize_label(box.get("label_inner")) != normalize_label(box.get("label_content")):
        return False
    if box.get("described"):
        return False
    return attestation_ok(box.get("label_inner"))


def consent_box_ok(box: dict | None) -> bool:
    """
    A census box the policy may tick: label from a visible <label> (for= or
    wrapping, never aria-label), rendered the same as written, no fine print
    attached, and a label that passes consent_ok.
    """
    if not box or box.get("label_source") not in POLICY_LABEL_SOURCES:
        return False
    if normalize_label(box.get("label_inner")) != normalize_label(box.get("label_content")):
        return False
    if box.get("described"):
        return False
    return consent_ok(box.get("label_inner"))


CAP_COUNT_UNKNOWN = "today's agent submissions couldn't be counted"
NO_CAP = "no daily_submit_cap set"


def enablement(portal: str, method: str, profile,
               agent_submissions_today: int | None) -> tuple[dict, list[str]]:
    """
    Whether this portal and today's cap would allow an unattended submit:
    ({"portal_allowlisted", "cap_remaining"}, reasons). The one rule, used by
    the gate and by shadow-report alike. A portal counts only when it was
    detected by its host. `agent_submissions_today` is None when it couldn't
    be counted, and then cap_remaining is None (unknown), never a full cap.
    """
    reasons: list[str] = []
    allowlisted = method == "host" and portal in profile.autosubmit_portals()
    if method != "host":
        reasons.append(f"portal detected by {method or 'nothing'}, not by its host")
    elif not allowlisted:
        reasons.append(f"portal '{portal}' not on autosubmit_portals")
    cap = profile.daily_submit_cap()
    counted = (isinstance(agent_submissions_today, int)
               and not isinstance(agent_submissions_today, bool)
               and agent_submissions_today >= 0)
    cap_remaining = None
    if not counted:
        reasons.append(CAP_COUNT_UNKNOWN)
    if cap is None:
        reasons.append(NO_CAP)
    elif counted:
        cap_remaining = max(cap - agent_submissions_today, 0)
        if cap_remaining == 0:
            reasons.append("daily_submit_cap reached")
    return {"portal_allowlisted": allowlisted, "cap_remaining": cap_remaining}, reasons


def cap_text(ena: dict, reasons: list[str]) -> str:
    """How cap_remaining reads: a number, "unknown" if the count failed, or "not set"."""
    if CAP_COUNT_UNKNOWN in reasons:
        return "unknown"
    if ena.get("cap_remaining") is None:
        return "not set"
    return str(ena["cap_remaining"])


def _group_boxes(census) -> dict:
    """
    {group_ref: [boxes]} for every census box carrying a group_ref, whether
    or not it says it's in a button group, so nothing sharing the ref is
    left out of the count.
    """
    out: dict = {}
    if not isinstance(census, list):
        return out
    for b in census:
        if isinstance(b, dict) and b.get("group_ref"):
            out.setdefault(b["group_ref"], []).append(b)
    return out


# What a button pair's own box reads as, before and after the fill, for each
# answer it may be exempt under. Ashby mirrors the pressed button into it:
# ticked for "Yes", unticked otherwise (as tests/button_pairs.html imitates
# it; unconfirmed on a live page, see TODO item 21). Any other answer gets
# no exemption.
_PAIR_TICKS = {"yes": (False, True), "no": (False, False)}


def _same_box(a: dict, b: dict) -> bool:
    """The pre-fill and post-fill reads are of the same element, as far as the census can tell."""
    if any(a.get(k) != b.get(k) for k in ("label_source", "label_inner", "label_content")):
        return False
    return a.get("ref") is None or b.get("ref") is None or a.get("ref") == b.get("ref")


def _is_pair_mirror(box: dict, plan: dict, after: dict, before: dict | None) -> bool:
    """
    Is this box an Ashby button pair's own hidden box, in exactly the state the
    agent's planned answer leaves it? Only then is it exempt from the
    hidden-and-ticked stop and from the label and allowlist checks. All must
    hold, each read exactly:
      - it's in a button group with a group_ref, planned with an answer that
        normalises to exactly "yes" or "no";
      - it's the only census box with that group_ref, grouped or not, after
        the fill and in the pre-fill census alike, so a second box can't ride
        along;
      - it's hidden (visible is False). A visible box gets no exemption;
      - the pre-fill and post-fill reads are the same element: same label
        source and text, and the same ref when both have one;
      - "yes": read unticked before the fill (False, not unread) and ticked
        after (True). "no": read unticked (False) both times. No pre-fill
        census for the run means no exemption.
    """
    ref = box.get("group_ref")
    if box.get("in_button_group") is not True or not ref:
        return False
    group = plan.get(ref)
    if not group or group.get("value") is None:
        return False
    ticks = _PAIR_TICKS.get(normalize_label(group["value"]))
    if ticks is None:
        return False
    if len(after.get(ref) or []) != 1 or box.get("visible") is not False:
        return False
    if before is None or len(before.get(ref) or []) != 1:
        return False
    was = before[ref][0]
    if not _same_box(was, box):
        return False
    return was.get("checked") is ticks[0] and box.get("checked") is ticks[1]


def decide(record: dict, profile, *, agent_submissions_today: int | None = 0) -> dict:
    """
    Returns {
      "gate_version", "content_verdict", "content_reasons",
      "enablement": {"portal_allowlisted", "cap_remaining"}, "enablement_reasons",
      "trace": [one entry per legal/eligibility/EEO/pay field: no answer values],
      "consent_actions": {ref: "would_tick" | "leave"} for allowlisted consent boxes
    }. Any exception becomes would_stop with the reason. Nothing is ever ticked:
    a consent action is what the agent would do if it ever submitted for you.
    """
    try:
        return _decide(record, profile, agent_submissions_today)
    except Exception as e:
        return {"gate_version": GATE_VERSION, "content_verdict": WOULD_STOP,
                "content_reasons": [f"gate error: {type(e).__name__}"],
                "enablement": {"portal_allowlisted": False, "cap_remaining": None},
                "enablement_reasons": ["gate error"], "trace": [], "consent_actions": {},
                "waived": []}


def _decide(record: dict, profile, agent_submissions_today: int | None) -> dict:
    content: list[str] = []
    trace: list[dict] = []

    for err in record.get("errors") or []:
        content.append(f"record incomplete: {err}")
    if "fields" not in record:
        content.append("record has no fields")

    fields: list[dict] = record.get("fields") or []
    plan: dict = record.get("plan") or {}
    categories: dict = record.get("categories") or {}
    flags = {fl.get("ref"): fl for fl in (record.get("flags") or [])}
    results = record.get("results")          # None on a dry run
    snapshot: dict = record.get("snapshot") or {}
    dry = bool(record.get("dry_run"))
    canned = {re.sub(r"\s+", " ", v).strip() for v in profile.canned_answers().values()}
    allow = profile.consent_allowlist()
    census_by_ref = {b.get("ref"): b for b in (record.get("census") or []) if b.get("ref")}
    consent_actions: dict[str, str] = {}
    # Fields Jack touched himself after a pause he ended (a touch mark new
    # since before it; the wizard writes this list; a missing key waives
    # nothing).
    reviewed = {r for r in (record.get("human_reviewed") or []) if isinstance(r, str)}
    # The option boxes of a checkbox group that carry his mark themselves
    # (decision E per element, Jack, 2026-11-03; the wizard writes this
    # list; a missing key marks nothing).
    marked = {r for r in (record.get("human_marked") or []) if isinstance(r, str)}
    # Reasons the optional-empties decision lists instead of stopping on.
    waived: list[str] = []

    def reads_empty(ref: str) -> bool:
        # What the page holds, as read back; on a dry run, the plan. A ref
        # the read-back left out is unknown, never empty.
        if dry:
            return not _present((plan.get(ref) or {}).get("value"))
        return ref in snapshot and not _present(snapshot.get(ref))

    for f in fields:
        ref, label = f.get("ref"), f.get("label") or f.get("name") or "(unlabelled)"
        name = f"'{label[:80]}'"
        if f.get("type") == "file":
            continue
        category = categories.get(ref, "unknown")
        decision = plan.get(ref)
        kinds = _stop_kinds(f)
        line = _sensitive_line(f)
        sensitive_field = (category in SENSITIVE_CATEGORIES or line is not None
                           or "legal" in kinds or sensitive.legal_guard(f) is not None
                           or bool(f.get("raw_stop") or f.get("raw_legal")))
        # 8. the waiver's ground: an optional, non-sensitive, non-secret
        # field that holds nothing. "Optional" is the scan's required key
        # being exactly False (a missing key, None, 0 or "" counts as
        # required: the gate defaults to stopping on anything missing)
        # and no trailing * on the label. A checkbox field is optional only
        # when its census box vouches for it too (_optional_box). Every
        # waiver below needs this.
        is_box = (f.get("type") or "").lower() == "checkbox"
        optional_empty = (not sensitive_field and f.get("required") is False
                          and not str(f.get("label") or "").rstrip().endswith("*")
                          and not f.get("secret") and (f.get("type") or "").lower() != "password"
                          and reads_empty(ref)
                          and (not is_box or _optional_box(census_by_ref.get(ref))))

        if ref in reviewed:
            # Jack's own answer: traced as his, never judged on content. The
            # required-but-empty check below still applies to it.
            if sensitive_field:
                trace.append({"ref": ref, "label": label[:120], "category": category,
                              "profile_path": None, "source": "human", "fit": None})
            continue

        # 5. per-company questions are never answered from the profile, with
        # decision C's one exception (Jack, 2026-11-02): a past-employer
        # question the gate itself recomputes from the record's field, the
        # job's address and work_history.past_employers, equal to the fill.
        # The per-company rule as before; decision C's plan joins it by its
        # source only, so a Department of Defense question ("Have you worked
        # for the Department of Defense?") stays the legal question it is
        # (fold round, decision 1).
        if (category == "per_company" or "per_company" in kinds
                or (decision or {}).get("source") == employers.SOURCE):
            if decision and decision.get("source") == employers.SOURCE:
                # The route reads past the per-company stop kind only: a
                # field the gate calls legal / sensitive (a category, a
                # profile line, a legal stop kind, a guard, a raw flag) is
                # never answered from the list (5ba4a07's review, BLOCK 3).
                if sensitive_field:
                    content.append(f"{name}: past-employer answer on a question that reads as legal or sensitive")
                    continue
                if employers.verify(f, decision, record.get("final_url") or record.get("url") or "",
                                    profile.past_employers() if hasattr(profile, "past_employers") else [],
                                    category):
                    trace.append({"ref": ref, "label": label[:120], "category": category,
                                  "profile_path": "work_history.past_employers", "source": employers.SOURCE,
                                  "fit": "exact"})
                    if results is not None:
                        res = results.get(ref)
                        if not res or not res.get("filled"):
                            content.append(f"{name}: filling it failed")
                        elif res.get("unconfirmed", True):
                            content.append(f"{name}: the page couldn't confirm the choice (unconfirmed)")
                    continue
                content.append(f"{name}: past-employer answer not recomputed from your list and the job's address")
                continue
            reason = f"{name}: {PER_COMPANY_REASON}"
            # Not a checkbox: unticked, one asserts "No" to the question.
            if (optional_empty and not is_box
                    and (decision is None or decision.get("value") is None)):
                waived.append(reason)
            else:
                content.append(reason)
            continue

        # Decision B's plan (source "clock") is trusted only when the gate
        # recomputes it from the record's own field, whatever the category:
        # a credential / consent / per-company category, or a box whose own
        # text reads as a legal question, is never the clock's (fold of
        # 2c24303's finding 3; the sensitive branch below checks the same
        # for a box the gate calls legal).
        if (decision or {}).get("source") == signdate.SOURCE and not signdate.verify(f, decision, fields, category=category):
            content.append(f"{name}: signature date not recomputed from the clock (left for you)")
            continue
        # The signature Name box (Jack, 2026-11-03): a plan with source
        # "profile_name" is trusted only when the gate recomputes the same
        # name from the record's own field and the profile's name lines.
        if (decision or {}).get("source") == signdate.NAME_SOURCE:
            if not signdate.verify_name(f, decision, fields, profile, category):
                content.append(f"{name}: signature name not recomputed from your profile's name lines (left for you)")
                continue
            trace.append({"ref": ref, "label": label[:120], "category": category,
                          "profile_path": signdate.NAME_LINES[0], "source": signdate.NAME_SOURCE, "fit": "verbatim"})

        # An allowlisted consent checkbox: your exact entry in consent_allowlist
        # answers it, the way a profile line answers an eligibility question.
        # It's waived only when it was handed to you as consent (by category,
        # or by the legal backstop on a consent/legal-declaration box). Any
        # other reason it came to you still stops. Nothing is ticked: the
        # action is recorded for scoring only.
        if f.get("type") == "checkbox" and ref in flags:
            idx = _allowlist_index(census_by_ref.get(ref), allow)
            # The consent policy (owner decision): terms / privacy wording
            # read from the census box, which is the label on the element.
            policy = idx is None and consent_box_ok(census_by_ref.get(ref))
            # Decision D (Jack, 2026-11-02): a REQUIRED attestation box,
            # judged on the census box's label, planned like a terms box.
            attest = (idx is None and not policy and _is_required(f)
                      and attestation_box_ok(census_by_ref.get(ref)))
            legal_hit = "legal" in kinds
            consent_handoff = (category == "consent"
                               or (legal_hit and category == "legal_declaration"))
            if ((idx is not None or policy or attest) and consent_handoff and not kinds - {"legal"}
                    and line is None and sensitive.legal_guard(f) is None):
                if legal_hit or category in SENSITIVE_CATEGORIES:
                    trace.append({"ref": ref, "label": label[:120], "category": category,
                                  "profile_path": None,
                                  "source": ("attestation" if attest else "consent_policy" if policy
                                             else "consent_allowlist"),
                                  "fit": "exact", "allowlist_index": idx})
                consent_actions[ref] = "would_tick" if _is_required(f) else "leave"
                continue

        # 2. legal / eligibility / EEO / pay: traced to a named profile line.
        # Only names and kinds are kept: never the answer itself.
        if sensitive_field:
            entry = {"ref": ref, "label": label[:120], "category": category,
                     "profile_path": (decision or {}).get("profile_path"),
                     "source": (decision or {}).get("source"),
                     "fit": (decision or {}).get("fit")}
            trace.append(entry)
            # Jack's own recorded answer on the identical question (Jack,
            # 2026-11-01, decision 4): source "learned" counts as traced
            # only when the key the gate recomputes from this field equals
            # the key the answer was learned under. Anything else about the
            # key (a missing one, a different one) is not traced.
            learned_ok = bool(decision and entry["source"] == "learned"
                              and isinstance(decision.get("learned_key"), str)
                              and decision.get("learned_key") == question_key(f))
            traced_to_profile = bool(decision and entry["source"] == "profile" and entry["profile_path"])
            # Decision B (Jack, 2026-11-02): today's date in a self-
            # identification form's signature block, recomputed here from
            # the record's own field (the block, the page's EEO question,
            # the part, the widget's stated format) and equal to the fill.
            clock_ok = signdate.verify(f, decision, fields, category=category)
            if not (traced_to_profile or learned_ok or clock_ok):
                content.append(f"{name}: legal/eligibility/EEO/pay answer not traced to a "
                               f"profile line or to your own answer on this question (left for you)")
                continue
            guards = sensitive.legal_guard_kinds(f)
            if len(set(guards)) > 1:
                content.append(f"{name}: reads as more than one kind of legal question "
                               f"({'/'.join(guards)})")
            if guards and entry["profile_path"] and not sensitive.in_legal_area(entry["profile_path"], guards):
                content.append(f"{name}: answered from {entry['profile_path']}, which isn't "
                               f"a {'/'.join(guards)} profile line")
            fits = sensitive.LEGAL_FITS if guards else STRICT_FITS
            if learned_ok and not f.get("options"):
                # His typed answer in a box with no options, as written
                # (a salary box; reused at the same host only by memory.py).
                fits = fits | {"verbatim"}
            if entry["fit"] not in fits:
                content.append(f"{name}: profile answer matched the options loosely "
                               f"({entry['fit']}), not exactly")

        # 3. nothing flagged, unanswered, null, failed or low-confidence.
        if ref in flags:
            reason = (flags[ref].get("reason") or "") if not sensitive_field else ""
            if not sensitive_field:
                text = f"{name}: left for you" + (f" ({reason[:100]})" if reason else "")
                # 8. Two hand-offs the decision lists instead: an optional
                # checkbox left unticked (optional_empty already needs its
                # census box to vouch for it: optional on every reading,
                # visible, unticked, plainly labelled, no question); and a
                # field that is no checkbox, handed over only because its
                # category is unknown (no stop kind, no raw flag: those
                # made it sensitive above). Any other flag still stops.
                plain = optional_empty and not kinds
                if plain and (is_box or category == "unknown"):
                    waived.append(text)
                else:
                    content.append(text)
            continue
        if decision is None:
            (waived if optional_empty else content).append(f"{name}: not answered")
            continue
        if decision.get("value") is None:
            (waived if optional_empty else content).append(f"{name}: left blank")
            continue
        if results is not None:
            res = results.get(ref)
            if not res or not res.get("filled"):
                # The filler's fixed category (a str of its own making, never
                # a value); anything else in that slot is not repeated.
                why = (res or {}).get("reason")
                why = why if isinstance(why, str) and re.fullmatch(r"[a-z_]+(:[A-Za-z_]+)?", why) else None
                content.append(f"{name}: filling it failed" + (f" ({why})" if why else ""))
            # A results entry with no "unconfirmed" key is unreadable, not
            # confirmed: the gate defaults to stopping on anything missing.
            elif res.get("unconfirmed", True):
                content.append(f"{name}: the page couldn't confirm the choice (unconfirmed)")
        reasons = list(decision.get("review_reasons") or [])
        draft = bool(decision.get("draft"))
        if category == "free_text" or draft:
            # 6. free text passes only as a canned answer, word for word.
            verbatim = re.sub(r"\s+", " ", str(decision.get("value"))).strip() in canned
            if not verbatim:
                content.append(f"{name}: free text isn't one of your canned answers, "
                               f"word for word")
            # The one waiver: the flag every draft gets. It can only matter for
            # a verbatim canned answer (anything else stopped just above), and
            # any other reason on the field still stands.
            reasons = [r for r in reasons if r != "draft"]
        if decision.get("needs_review") and not reasons and not draft:
            reasons = ["unattributed"]
        if reasons:
            content.append(f"{name}: needs review ({', '.join(sorted(set(reasons)))})")
        if decision.get("source") == "model" and decision.get("confidence") != "high":
            content.append(f"{name}: model confidence is "
                           f"{decision.get('confidence') or 'not given'}, not high")

    # 1. every required field filled, as read back from the page.
    for f in fields:
        if not _is_required(f):
            continue
        ref = f.get("ref")
        if consent_actions.get(ref) == "would_tick":
            filled = True        # the gate's plan ticks it; the agent never does
        elif dry:
            filled = _present((plan.get(ref) or {}).get("value"))
        else:
            filled = _present(snapshot.get(ref))
        if not filled:
            content.append(f"'{(f.get('label') or f.get('name') or '(unlabelled)')[:80]}': "
                           f"required, but empty")

    # 4. every checkbox on the page, ticked or not, is one you allowed.
    allow = {normalize_label(a) for a in profile.consent_allowlist()}
    census = record.get("census") or []
    after = _group_boxes(census)
    prefill = record.get("prefill_census")
    before = _group_boxes(prefill) if isinstance(prefill, list) else None
    # The boxes of a checkbox group answering one question (item 5, Jack,
    # 2026-11-02): each is judged against the group's planned answer
    # instead of the consent rule, once the question itself went through
    # 2 and 3 above as a traced choice field.
    grouped = {r: (f, o) for f in fields if f.get("widget") == "checkboxes"
               for r, o in zip(f.get("option_refs") or [], f.get("options") or [])}
    for box in census:
        shown = box.get("label_inner") or box.get("label_content") or "(unlabelled)"
        name = f"checkbox '{shown[:80]}'"
        # The one exemption: a button pair's own hidden box, in exactly the
        # state the agent's planned "Yes" or "No" leaves it (see
        # _is_pair_mirror). The question itself went through 2 and 3 above.
        if _is_pair_mirror(box, plan, after, before):
            continue
        # A box you can't see, already ticked, is something you'd be sending
        # without having seen it. No label clears that, an allowlisted one
        # included. Visibility or tick state not read counts as hidden and
        # ticked.
        if box.get("checked") is not False and box.get("visible") is not True:
            content.append(f"{name}: hidden, and ticked")
        # Decision E (Jack, 2026-11-02): a box carrying his own review mark
        # new since before the pause is his answer, ticked or unticked as
        # he left it; the hidden-and-ticked check above still applies. Per
        # element only (Jack, 2026-11-03): a checkbox group's ref is its
        # first box's, and the wizard marks the group when he touches ANY
        # of its boxes, so a grouped box is never waived here (fix of
        # aa46374's BLOCK: the first box skipped every check on a sibling's
        # touch); it is judged with its group below, where a group he
        # answered passes only with its labels clean and one box ticked.
        if box.get("ref") and box.get("ref") in reviewed and box.get("ref") not in grouped:
            continue
        if box.get("in_button_group"):
            group = plan.get(box.get("group_ref"))
            if not group or group.get("value") is None:
                content.append(f"{name}: button-pair question not answered")
        # Everything not exempt, a box inside a button group included, meets
        # the same label and allowlist checks.
        if box.get("label_source") not in ALLOWLIST_LABEL_SOURCES:
            content.append(f"{name}: label not explicit ({box.get('label_source')})")
        elif not _same_text(box.get("label_inner"), box.get("label_content")):
            content.append(f"{name}: label contains hidden text")
        elif box.get("described"):
            content.append(f"{name}: has fine print attached")
        elif box.get("ref") in grouped:
            # A box of a checkbox group answering one question (item 5), past
            # the label checks above like any other box: judged against the
            # group's planned answer, which must be a strict profile fit (fix
            # of ab0a09e's BLOCK 1: a model-sourced plan vouches for no tick);
            # a group Jack answered himself (human_reviewed) is his, as long
            # as one box of it is ticked at most. Its label must be a visible
            # <label> (the sources that tick) and read like no marketing,
            # certification or stop wording (fold of 2d0c2ee's findings 1-3).
            group, option = grouped[box["ref"]]
            label_text = str(box.get("label_inner") or "")
            ticked_in_group = sum(1 for b in census if isinstance(b, dict) and b.get("ref") in grouped
                                  and grouped[b["ref"]][0] is group and b.get("checked") is True)
            if box.get("label_source") not in POLICY_LABEL_SOURCES:
                content.append(f"{name}: a grouped box needs a visible <label> ({box.get('label_source')})")
            elif any(CONSENT_MARKETING.search(v) or CONSENT_CERTIFY.search(v) or sensitive.stop_reason(v)
                     for v in sensitive.text_variants(label_text)):
                content.append(f"{name}: a grouped box that reads as an opt-in, a certification or a stop wording")
            elif ticked_in_group > 1:
                content.append(f"{name}: more than one box of its group is ticked")
            elif group.get("ref") in reviewed:
                # A group he answered himself: a ticked box must carry his
                # own mark, or equal a strict-fit profile plan; a box the
                # agent or the page ticked is not his answer (fold of
                # 398e6c5's review, finding 1: "with no sibling waived").
                decision = plan.get(group.get("ref")) or {}
                trusted = decision.get("source") == "profile" and decision.get("fit") in sensitive.LEGAL_FITS
                his_tick = box.get("ref") in marked or (
                    trusted and normalize_label(option) == normalize_label(decision.get("value")))
                if box.get("checked") is not False and not his_tick:
                    content.append(f"{name}: ticked in a group you answered, but carries neither your mark "
                                   f"nor a strict profile fit")
            else:
                decision = plan.get(group.get("ref")) or {}
                planned = decision.get("value")
                trusted = decision.get("source") == "profile" and decision.get("fit") in sensitive.LEGAL_FITS
                if planned is None or not trusted:
                    content.append(f"{name}: its checkbox group is not answered from a profile line with a strict fit")
                elif box.get("checked") is not (normalize_label(option) == normalize_label(planned)):
                    content.append(f"{name}: ticked differently from the group's planned answer")
        elif (normalize_label(box.get("label_inner")) not in allow
              and not consent_box_ok(box) and not attestation_box_ok(box)):
            reason = f"{name}: not on consent_allowlist, and not a terms / privacy consent"
            # 8. An optional box left unticked is listed, not stopped on
            # (_optional_box: required exactly False, no trailing *, visible,
            # read unticked, not a pair's, plainly labelled, no question).
            if _optional_box(box):
                waived.append(reason)
            else:
                content.append(reason)

    # 9. nothing unexpected along the way. An event the agent handled under
    # the owner decisions (a sign-in, an account, a Next press) or Jack
    # handled at a pause doesn't stop; a CAPTCHA only when Jack did.
    for ev in record.get("events") or []:
        if ev.get("kind") in INFORMATIONAL_EVENTS:
            continue
        handled = ev.get("handled")
        if handled == "jack" or (handled == "agent" and ev.get("kind") != "captcha"):
            continue
        detail = f" ({ev.get('detail')})" if ev.get("detail") else ""
        content.append(f"run: {ev.get('kind', 'unknown event')}{detail}")
    if (record.get("steps") or 0) > 1 and record.get("wizard") is not True:
        content.append(f"run: {record['steps']} wizard steps")
    for fr in record.get("frames") or []:
        if fr.get("visible") and not _KNOWN_FRAME.match(fr.get("src") or ""):
            content.append(f"run: an embedded frame the gate can't see into "
                           f"({(fr.get('src') or 'no src')[:80]})")

    # Enablement: never part of the verdict, never scored.
    ena, enablement_reasons = enablement(record.get("portal") or "",
                                         record.get("portal_method") or "", profile,
                                         agent_submissions_today)

    # Same reason twice says nothing new.
    content = list(dict.fromkeys(content))
    return {"gate_version": GATE_VERSION,
            "content_verdict": WOULD_STOP if content else WOULD_SUBMIT,
            "content_reasons": content,
            "enablement": ena,
            "enablement_reasons": enablement_reasons,
            "trace": trace,
            "consent_actions": consent_actions,
            "waived": list(dict.fromkeys(waived))}
