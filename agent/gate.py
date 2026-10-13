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

GATE_VERSION = 1

WOULD_SUBMIT, WOULD_STOP = "would_submit", "would_stop"

# Questions whose answer must be traced to a named profile line.
SENSITIVE_CATEGORIES = {"work_authorization", "eeo", "compensation", "legal_declaration"}
SENSITIVE_PREFIXES = ("eligibility.", "eeo.", "compensation.")

# How a profile value may have been matched to the form's options and still
# count as yours, as written. "bool_prefix" and "substring" are guesses.
STRICT_FITS = {"exact", "bool", "decline", "verbatim"}

# Where a checkbox's label may come from. The scan's nearby-text fallback can
# pick up the wrong text, so a label it found isn't trusted here.
EXPLICIT_LABEL_SOURCES = {"for", "wrap", "aria-label", "aria-labelledby", "self"}

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
    return " ".join(str(f.get(k) or "") for k in ("label", "help_text", "placeholder", "name"))


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
    return bool(str(value).strip())


def decide(record: dict, profile, *, agent_submissions_today: int = 0) -> dict:
    """
    Returns {
      "gate_version", "content_verdict", "content_reasons",
      "enablement": {"portal_allowlisted", "cap_remaining"}, "enablement_reasons",
      "trace": [one entry per legal/eligibility/EEO/pay field: no answer values]
    }. Any exception becomes would_stop with the reason.
    """
    try:
        return _decide(record, profile, agent_submissions_today)
    except Exception as e:
        return {"gate_version": GATE_VERSION, "content_verdict": WOULD_STOP,
                "content_reasons": [f"gate error: {type(e).__name__}"],
                "enablement": {"portal_allowlisted": False, "cap_remaining": None},
                "enablement_reasons": ["gate error"], "trace": []}


def _decide(record: dict, profile, agent_submissions_today: int) -> dict:
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
                           or "legal" in kinds)

        # 5. per-company questions are never answered from the profile yet.
        if category == "per_company" or "per_company" in kinds:
            content.append(f"{name}: {PER_COMPANY_REASON}")
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
            if entry["fit"] not in STRICT_FITS:
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
        if dry:
            filled = _present((plan.get(ref) or {}).get("value"))
        else:
            filled = _present(snapshot.get(ref))
        if not filled:
            content.append(f"'{(f.get('label') or f.get('name') or '(unlabelled)')[:80]}': "
                           f"required, but empty")

    # 4. every checkbox on the page, ticked or not, is one you allowed.
    allow = {normalize_label(a) for a in profile.consent_allowlist()}
    for box in record.get("census") or []:
        shown = box.get("label_inner") or box.get("label_content") or "(unlabelled)"
        name = f"checkbox '{shown[:80]}'"
        if box.get("in_button_group"):
            # An Ashby button pair's hidden checkbox stores the answer to a
            # question. It's exempt only if that question was planned and
            # answered; the question itself went through 2 and 3 above.
            group = plan.get(box.get("group_ref"))
            if not group or group.get("value") is None:
                content.append(f"{name}: button-pair question not answered")
            continue
        if box.get("label_source") not in EXPLICIT_LABEL_SOURCES:
            content.append(f"{name}: label not explicit ({box.get('label_source')})")
        elif not _same_text(box.get("label_inner"), box.get("label_content")):
            content.append(f"{name}: label contains hidden text")
        elif box.get("described"):
            content.append(f"{name}: has fine print attached")
        elif normalize_label(box.get("label_inner")) not in allow:
            content.append(f"{name}: not on consent_allowlist")

    # 9. nothing unexpected along the way.
    for ev in record.get("events") or []:
        detail = f" ({ev.get('detail')})" if ev.get("detail") else ""
        content.append(f"run: {ev.get('kind', 'unknown event')}{detail}")
    if (record.get("steps") or 0) > 1:
        content.append(f"run: {record['steps']} wizard steps")
    for fr in record.get("frames") or []:
        if fr.get("visible") and not _KNOWN_FRAME.match(fr.get("src") or ""):
            content.append(f"run: an embedded frame the gate can't see into "
                           f"({(fr.get('src') or 'no src')[:80]})")

    # Enablement: never part of the verdict, never scored.
    enablement_reasons: list[str] = []
    portal, method = record.get("portal") or "", record.get("portal_method") or ""
    allowlisted = method == "host" and portal in profile.autosubmit_portals()
    if method != "host":
        enablement_reasons.append(f"portal detected by {method or 'nothing'}, not by its host")
    elif not allowlisted:
        enablement_reasons.append(f"portal '{portal}' not on autosubmit_portals")
    cap = profile.daily_submit_cap()
    if cap is None:
        cap_remaining = None
        enablement_reasons.append("no daily_submit_cap set")
    else:
        cap_remaining = max(cap - int(agent_submissions_today), 0)
        if cap_remaining == 0:
            enablement_reasons.append("daily_submit_cap reached")

    # Same reason twice says nothing new.
    content = list(dict.fromkeys(content))
    return {"gate_version": GATE_VERSION,
            "content_verdict": WOULD_STOP if content else WOULD_SUBMIT,
            "content_reasons": content,
            "enablement": {"portal_allowlisted": allowlisted, "cap_remaining": cap_remaining},
            "enablement_reasons": enablement_reasons,
            "trace": trace}
