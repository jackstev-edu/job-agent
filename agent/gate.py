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

import re
import unicodedata
from typing import Any

from . import sensitive

# 2: census checkboxes need a <label> or aria-label; a hidden ticked box stops.
# 3: an unread tick counts as ticked; a button pair's hidden box is exempt only
#    when its tick matches the group's planned answer.
# 4: that exemption needs the group's only box, hidden, unticked before the
#    fill and ticked after it, for a planned "Yes". Every other box in a group
#    meets the ordinary label and allowlist checks.
# 5: a pair planned "No" is exempt when its only box stays unticked; both
#    exemptions need the same element before and after, and every box with
#    the group_ref counts, grouped or not.
GATE_VERSION = 5

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


def _same_text(a: Any, b: Any) -> bool:
    return re.sub(r"\s+", " ", str(a or "")).strip() == re.sub(r"\s+", " ", str(b or "")).strip()


def _question_text(f: dict) -> str:
    # The policy's own text, normaliser included, so the gate reads a
    # question exactly as sensitive.decide did.
    return sensitive._question_text(f)


def _stop_kinds(f: dict) -> set[str]:
    text = _question_text(f)
    return {kind for rx, kind in sensitive.STOP_KINDS if rx.search(text)}


def _sensitive_line(f: dict) -> str | None:
    text = _question_text(f)
    return next((p for rx, p in sensitive._PATHS
                 if rx.search(text) and p.startswith(SENSITIVE_PREFIXES)), None)


def _is_required(f: dict) -> bool:
    return bool(f.get("required")) or str(f.get("label") or "").rstrip().endswith("*")


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
                "enablement_reasons": ["gate error"], "trace": [], "consent_actions": {}}


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
                           or "legal" in kinds or sensitive.legal_guard(f) is not None)

        # 5. per-company questions are never answered from the profile yet.
        if category == "per_company" or "per_company" in kinds:
            content.append(f"{name}: {PER_COMPANY_REASON}")
            continue

        # An allowlisted consent checkbox: your exact entry in consent_allowlist
        # answers it, the way a profile line answers an eligibility question.
        # It's waived only when it was handed to you as consent (by category,
        # or by the legal backstop on a consent/legal-declaration box). Any
        # other reason it came to you still stops. Nothing is ticked: the
        # action is recorded for scoring only.
        if f.get("type") == "checkbox" and ref in flags:
            idx = _allowlist_index(census_by_ref.get(ref), allow)
            legal_hit = "legal" in kinds
            consent_handoff = (category == "consent"
                               or (legal_hit and category == "legal_declaration"))
            if (idx is not None and consent_handoff and not kinds - {"legal"}
                    and line is None and sensitive.legal_guard(f) is None):
                if legal_hit or category in SENSITIVE_CATEGORIES:
                    trace.append({"ref": ref, "label": label[:120], "category": category,
                                  "profile_path": None, "source": "consent_allowlist",
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
            if not decision or entry["source"] != "profile" or not entry["profile_path"]:
                content.append(f"{name}: legal/eligibility/EEO/pay answer not traced to a "
                               f"profile line (left for you)")
                continue
            guards = sensitive.legal_guard_kinds(f)
            if guards and not str(entry["profile_path"]).startswith(
                    tuple(sensitive.LEGAL_AREAS[g] for g in guards)):
                content.append(f"{name}: answered from {entry['profile_path']}, which isn't "
                               f"a {'/'.join(guards)} profile line")
            fits = sensitive.LEGAL_FITS if guards else STRICT_FITS
            if entry["fit"] not in fits:
                content.append(f"{name}: profile answer matched the options loosely "
                               f"({entry['fit']}), not exactly")

        # 3. nothing flagged, unanswered, null, failed or low-confidence.
        if ref in flags:
            reason = (flags[ref].get("reason") or "") if not sensitive_field else ""
            if not sensitive_field:
                content.append(f"{name}: left for you" + (f" ({reason[:100]})" if reason else ""))
            continue
        if decision is None:
            content.append(f"{name}: not answered")
            continue
        if decision.get("value") is None:
            content.append(f"{name}: left blank")
            continue
        if results is not None:
            res = results.get(ref)
            if not res or not res.get("filled"):
                content.append(f"{name}: filling it failed")
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
        elif normalize_label(box.get("label_inner")) not in allow:
            content.append(f"{name}: not on consent_allowlist")

    # 9. nothing unexpected along the way.
    for ev in record.get("events") or []:
        if ev.get("kind") in INFORMATIONAL_EVENTS:
            continue
        detail = f" ({ev.get('detail')})" if ev.get("detail") else ""
        content.append(f"run: {ev.get('kind', 'unknown event')}{detail}")
    if (record.get("steps") or 0) > 1:
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
            "consent_actions": consent_actions}
