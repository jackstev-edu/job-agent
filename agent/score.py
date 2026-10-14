"""
score.py — how the shadow gate's calls compare with what you actually did.

WHAT THIS DOES: Scores each logged gate run against its recorded outcome,
per portal: a match, a false submit, a false stop, unknown, or still
pending. It keeps each portal's consecutive-match streak, and lists every
mismatch with the gate's reasons and what you did. Pure: it reads rows
it's given and changes nothing.
WHY IT MATTERS: This is the evidence for ever trusting the gate on a portal,
so it's strict. A would_submit only counts as right when you submitted with
zero edits and the form was read at the moment you submitted. Anything
uncertain is unknown, never a match. Only the content verdict is scored;
enablement never is.
"""

from __future__ import annotations

import json
from collections import OrderedDict

MATCH, FALSE_SUBMIT, FALSE_STOP, UNKNOWN, PENDING = (
    "match", "false_submit", "false_stop", "unknown", "pending")


def _edits(row: dict) -> list[dict] | None:
    try:
        edits = json.loads(row["edits_json"]) if row.get("edits_json") is not None else None
    except ValueError:
        return None
    return edits if isinstance(edits, list) else None


def score_run(row: dict) -> str:
    """
    One run, scored:
      would_submit -> false_submit if you changed anything that could be read
                      (whether or not you submitted, and even if other fields
                      couldn't be read), or if you didn't submit; match only
                      if you submitted, with zero edits, read at submit time
                      ("exact").
      would_stop   -> match if you changed something or didn't submit;
                      false_stop if you submitted with zero edits, read at
                      submit time.
    Edits are checked before your answer: an edit is evidence on its own, so
    an unknown answer can't hide a false submit. Unreadable fields aren't
    edits, but they stop zero edits being claimed.
    """
    if not row.get("outcome_at"):
        return PENDING
    edits = _edits(row)
    real = [e for e in edits or [] if e.get("kind") != "unreadable"]
    unreadable = any(e.get("kind") == "unreadable" for e in edits or [])
    zero_proven = (edits is not None and not edits and not unreadable
                   and row.get("capture") == "exact")
    submitted = row.get("human_submitted")
    verdict = row.get("verdict")
    if verdict == "would_submit":
        if real or submitted == "n":
            return FALSE_SUBMIT
        if submitted != "y":
            return UNKNOWN
        return MATCH if zero_proven else UNKNOWN
    if verdict == "would_stop":
        if real or submitted == "n":
            return MATCH
        if submitted != "y":
            return UNKNOWN
        return FALSE_STOP if zero_proven else UNKNOWN
    return UNKNOWN


def by_portal(rows: list[dict]) -> "OrderedDict[str, dict]":
    """
    Per-portal counts and the current streak, oldest run first. A match adds
    one to the streak; a false submit resets it to 0; a false stop is a
    mismatch but, by the rule as given, neither adds nor resets; unknown and
    pending runs don't count either way.
    """
    out: "OrderedDict[str, dict]" = OrderedDict()
    for row in sorted(rows, key=lambda r: (r.get("created_at") or "", r.get("id") or 0)):
        portal = row.get("portal") or "(unknown portal)"
        s = out.setdefault(portal, {"runs": 0, MATCH: 0, FALSE_SUBMIT: 0, FALSE_STOP: 0,
                                    UNKNOWN: 0, PENDING: 0, "streak": 0, "mismatches": []})
        result = score_run(row)
        s["runs"] += 1
        s[result] += 1
        if result == MATCH:
            s["streak"] += 1
        elif result == FALSE_SUBMIT:
            s["streak"] = 0
        if result in (FALSE_SUBMIT, FALSE_STOP):
            s["mismatches"].append({"row": row, "result": result})
    for s in out.values():
        s["scored"] = s[MATCH] + s[FALSE_SUBMIT] + s[FALSE_STOP]
    return out


def describe_outcome(row: dict) -> str:
    """What you did, in words: submitted or not, and which fields you changed."""
    did = {"y": "you submitted", "n": "you didn't submit"}.get(row.get("human_submitted"),
                                                                  "unknown whether you submitted")
    edits = _edits(row)
    if edits is None:
        return f"{did}; the form wasn't read"
    real = [e for e in edits if e.get("kind") != "unreadable"]
    if not real:
        return f"{did}, with no changes"
    shown = ", ".join(f"{e.get('label', '?')} ({e.get('kind')})" for e in real[:6])
    more = f" and {len(real) - 6} more" if len(real) > 6 else ""
    return f"{did}, changing {shown}{more}"
