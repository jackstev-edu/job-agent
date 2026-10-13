"""
learn.py — what your corrections keep saying.

WHAT THIS DOES: Reads every correction `fill` has logged, keeps the ones that
recur across postings, groups them by the profile line involved (or, when no
line is known, by the question), and prints a suggested edit for each. It
writes nothing: not to profile.yaml, not to the code, not to the log.

WHY IT MATTERS: The same fix typed by hand on five forms is a profile gap or a
wrong profile line, and that's worth knowing. But a value typed on one form
isn't a general fact about you, and eligibility, EEO and compensation answers
are yours alone, so those are only ever flagged, never suggested.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

from . import config, sensitive

# A fix has to show up on this many different postings before it means
# anything beyond "that one form was odd".
MIN_POSTINGS = 2

# The corrections log's format. Version 2 reads custom dropdowns from what
# they display, and stops logging diffs that are only formatting or an
# unreadable blank. Rows without a version are version 1.
LOG_VERSION = 2

# Answers learn only ever flags. Matched on the classifier's category, on the
# profile line, and on the wording, so a mis-classified field is still caught.
SENSITIVE_CATEGORIES = {"work_authorization", "eeo", "compensation"}
SENSITIVE_PREFIXES = ("eligibility.", "eeo.", "compensation.")

# The profile sections the model is allowed to see, mirroring
# Profile.facts_for_model. Only these are searched for matching values.
_FACT_SECTIONS = ("personal", "links", "education", "experience", "skills",
                  "certifications", "achievements", "voice", "current_status")


def corrections_path() -> Path:
    return config.STATE_DIR / "corrections.jsonl"


def load_rows(path: Path) -> tuple[list[dict], int]:
    """Every logged correction, plus a count of lines that weren't valid JSON."""
    rows, bad = [], 0
    if not path.exists():
        return rows, bad
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            bad += 1
            continue
        if isinstance(row, dict):
            rows.append(row)
        else:
            bad += 1
    return rows, bad


def _norm(value: Any) -> str:
    return re.sub(r"\s+", " ", "" if value in (None, False) else str(value)).strip()


def same_answer(agent: Any, final: Any, field_type: str | None) -> bool:
    """
    The same answer written differently: case and spacing, or for a phone
    number the same digits. A page's input mask reformats a typed phone
    number, and that isn't you correcting it.
    """
    a, f = _norm(agent).casefold(), _norm(final).casefold()
    if a == f:
        return True
    if (field_type or "").lower() == "tel":
        da, df = re.sub(r"\D", "", a), re.sub(r"\D", "", f)
        # A country code added or dropped is still the same number.
        return min(len(da), len(df)) >= 7 and (da.endswith(df) or df.endswith(da))
    return False


def unreadable(agent: Any, final: Any, field_type: str | None) -> bool:
    """
    A custom dropdown that reads back blank after the agent answered it. That's
    far more often a widget hiding its value from us than you clearing it.
    """
    return (field_type or "").lower() == "combobox" and bool(_norm(agent)) and not _norm(final)


def set_aside_reason(row: dict) -> str | None:
    """Why a logged row can't be trusted as a real correction, or None."""
    if int(row.get("log_version") or 1) < 2 and row.get("type") == "combobox":
        # Version 1 read react-select's search box, which is empty once an
        # option is chosen, so these were logged whether or not you changed
        # anything. Sensitive ones have no values to re-check, so all go.
        return "custom dropdown logged before its value could be read back"
    agent, final, ftype = row.get("agent_value"), row.get("final_value"), row.get("type")
    if agent is None and final is None:
        return None                     # a sensitive row; nothing to re-check
    if unreadable(agent, final, ftype):
        return "custom dropdown read back blank"
    if same_answer(agent, final, ftype):
        return "same answer, formatted differently"
    return None


def _norm_label(label: Any) -> str:
    return _norm(label).strip("*:? ").casefold()


def _flatten(node: Any, prefix: str = "") -> dict[str, Any]:
    """{'links.github': 'https://…', 'experience[0].company': 'Acme', …}"""
    out: dict[str, Any] = {}
    if isinstance(node, dict):
        for k, v in node.items():
            out.update(_flatten(v, f"{prefix}.{k}" if prefix else str(k)))
    elif isinstance(node, list):
        for i, v in enumerate(node):
            out.update(_flatten(v, f"{prefix}[{i}]"))
    elif node is not None and _norm(node) and _norm(node).upper() != "TODO":
        out[prefix] = node
    return out


def profile_facts(profile_data: dict | None) -> dict[str, Any]:
    """The profile lines learn may point at. Eligibility, EEO and pay are left out."""
    data = profile_data or {}
    return _flatten({k: data[k] for k in _FACT_SECTIONS if k in data})


def _lines_holding(value: Any, facts: dict[str, Any]) -> list[str]:
    target = _norm(value).casefold()
    if not target:
        return []
    return [path for path, v in facts.items() if _norm(v).casefold() == target]


def _line_for(row: dict, facts: dict[str, Any]) -> tuple[str | None, str]:
    """Which profile line this correction involved, and how we know."""
    if row.get("profile_path"):
        return row["profile_path"], "logged"
    label = row.get("label") or ""
    for rx, path in sensitive._PATHS:
        if rx.search(label):
            return path, "wording"
    held = _lines_holding(row.get("agent_value"), facts)
    if len(held) == 1:
        return held[0], "value"
    return None, ""


def _is_sensitive(row: dict, line: str | None) -> bool:
    return (row.get("category") in SENSITIVE_CATEGORIES
            or (line or "").startswith(SENSITIVE_PREFIXES))


def _postings(rows: list[dict]) -> int:
    return len({r.get("url") or f"row{i}" for i, r in enumerate(rows)})


def _quote(value: Any) -> str:
    text = _norm(value)
    return repr(text if len(text) <= 60 else text[:57] + "...")


def _suggest_for_line(line: str, how: str, rows: list[dict],
                      facts: dict[str, Any]) -> str:
    finals = {_norm(r.get("final_value")) for r in rows}
    if any(r.get("final_value") is None for r in rows):
        return "values weren't logged for this kind of question; nothing to suggest"
    if len(finals) > 1:
        return ("corrected differently each time ("
                + ", ".join(_quote(f) for f in sorted(finals)) + "); no single suggestion")
    final = next(iter(finals))
    current = facts.get(line)
    if current is not None and _norm(current) == final:
        return f"`{line}` already says {_quote(final)}; the form may just format it differently"
    if how == "value" or current is not None:
        return f"suggested profile edit: `{line}`: {_quote(current)} -> {_quote(final)}"
    return f"suggested profile edit: set `{line}` to {_quote(final)}"


def _suggest_for_question(label: str, rows: list[dict], facts: dict[str, Any]) -> str:
    if any(r.get("final_value") is None for r in rows):
        cats = sorted({r.get("category") or "unknown" for r in rows})
        return (f"values aren't logged for {', '.join(cats)} questions; "
                f"nothing to suggest")
    finals = {_norm(r.get("final_value")) for r in rows}
    if len(finals) > 1:
        return ("answered differently each time ("
                + ", ".join(_quote(f) for f in sorted(finals)) + "); no single suggestion")
    final = next(iter(finals))
    held = _lines_holding(final, facts)
    if held:
        lines = ", ".join(f"`{p}`" for p in held)
        return (f"suggested alias: questions worded {label!r} -> {lines}. Your profile "
                f"already holds {_quote(final)}; the model didn't connect them. "
                f"(Ordinary fields have no alias table yet; this is its input.)")
    return (f"suggested profile addition: you typed {_quote(final)} each time. If "
            f"that's true in general, add a line for it to profile.yaml")


def analyse(rows: list[dict], profile_data: dict | None) -> dict:
    """
    Group recurring corrections. Returns {"by_line": [...], "by_question": [...]}.

    Each correction lands in exactly one group: under its profile line when
    one is known, otherwise under its question's wording. Rows that can't be
    trusted as real corrections are counted in "set_aside" and go no further.
    """
    facts = profile_facts(profile_data)
    set_aside: dict[str, int] = defaultdict(int)
    kept = []
    for row in rows:
        reason = set_aside_reason(row)
        if reason:
            set_aside[reason] += 1
        else:
            kept.append(row)
    rows = kept
    by_line: dict[str, list[dict]] = defaultdict(list)
    how_known: dict[str, str] = {}
    by_question: dict[str, list[dict]] = defaultdict(list)
    shown_label: dict[str, str] = {}

    for row in rows:
        line, how = _line_for(row, facts)
        if line:
            by_line[line].append(row)
            how_known.setdefault(line, how)
        else:
            key = _norm_label(row.get("label"))
            if key:
                by_question[key].append(row)
                shown_label.setdefault(key, _norm(row.get("label")).strip("* "))

    def entry(name, group, sensitive_, suggestion):
        kinds = sorted({r.get("kind") or "changed" for r in group})
        return {"name": name, "count": len(group), "postings": _postings(group),
                "kinds": kinds, "sensitive": sensitive_, "suggestion": suggestion}

    line_out, question_out = [], []
    for line, group in sorted(by_line.items()):
        if _postings(group) < MIN_POSTINGS:
            continue
        if any(_is_sensitive(r, line) for r in group):
            line_out.append(entry(line, group, True, None))
        else:
            line_out.append(entry(line, group, False,
                                  _suggest_for_line(line, how_known[line], group, facts)))
    for key, group in sorted(by_question.items()):
        if _postings(group) < MIN_POSTINGS:
            continue
        label = shown_label[key]
        if any(_is_sensitive(r, None) for r in group):
            question_out.append(entry(label, group, True, None))
        else:
            question_out.append(entry(label, group, False,
                                      _suggest_for_question(label, group, facts)))
    return {"by_line": line_out, "by_question": question_out,
            "set_aside": dict(set_aside)}


def render(rows: list[dict], bad: int, result: dict, path: Path) -> str:
    out = [f"Corrections log: {path}",
           f"{len(rows)} correction(s) from {_postings(rows)} posting(s)"
           + (f"; {bad} unreadable line(s) skipped" if bad else "") + "."]
    if not rows:
        out.append("\nNothing logged yet. Corrections are recorded after a real `fill`, "
                   "when you press Enter before submitting.")
        return "\n".join(out)

    set_aside = result.get("set_aside") or {}
    if set_aside:
        out.append(f"\nSet aside {sum(set_aside.values())} that aren't real corrections:")
        for reason, n in sorted(set_aside.items()):
            out.append(f"  {n}: {reason}")

    def section(title, entries, quote_name):
        if not entries:
            return
        out.append(f"\n{title}")
        for e in entries:
            name = f"{e['name']!r}" if quote_name else f"`{e['name']}`"
            out.append(f"  {name} — {e['count']} correction(s) on {e['postings']} "
                       f"posting(s) ({', '.join(e['kinds'])})")
            if e["sensitive"]:
                out.append("      flagged only: eligibility, EEO and compensation answers "
                           "are yours. learn suggests nothing for them.")
            else:
                out.append(f"      {e['suggestion']}")

    section("By profile line", result["by_line"], quote_name=False)
    section("By question (no profile line known)", result["by_question"], quote_name=True)
    if not result["by_line"] and not result["by_question"]:
        out.append(f"\nNothing recurs on {MIN_POSTINGS}+ postings yet.")
    out.append("\nNothing was changed. Edit profile.yaml or the code yourself if a "
               "suggestion holds up.")
    return "\n".join(out)
