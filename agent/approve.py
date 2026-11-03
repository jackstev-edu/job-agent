"""
approve.py — the one writer of profile.yaml: Jack's "Save to profile" click.

WHAT THIS DOES: Writes one ordinary profile line to a new value, with
profile.yaml's comments and order kept (ruamel.yaml's round-trip loader),
the value as a quoted string, after a timestamped backup beside it (the
last five kept), under one lock from the file check to the atomic
replace, refusing a file that changed since the stamp was taken, a
mapping or list node, and every legal, eligibility, EEO, pay, DoD or
per-company line (Jack, 2026-11-01: "profile.yaml is NEVER changed
automatically... only Jack's click writes the profile"). Also retires
the aliases.yaml of the 2026-10-31 Suggestions panel: counted, renamed
`aliases.yaml.retired`, nothing learned from it (Jack, 2026-11-01).
WHY IT MATTERS: Learning (memory.py) never touches the profile; this
module is the only code path that does, and only on Jack's click.
"""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

from . import learn, sensitive

# Lines a click never writes.
SENSITIVE_PREFIXES = learn.SENSITIVE_PREFIXES + ("per_company.",)
# The only sections a click may write into (learn's own fact sections).
WRITABLE_SECTIONS = learn._FACT_SECTIONS

ALIASES_FILE = "aliases.yaml"
RETIRED_SUFFIX = ".retired"
KEEP_BACKUPS = 5

# One lock from the file check to the write: a second click, or a save
# while the queue loop reads the file, waits here and then sees the new
# stamp (Jack, 2026-11-01; finding 6 of 5737dfd's review).
LOCK = threading.RLock()


class Refused(Exception):
    """The write is not allowed: a sensitive line, an unknown section, a malformed path, a mapping or list node."""


class Changed(Exception):
    """profile.yaml changed since the stamp was taken: nothing written."""


def profile_stamp(path: Path) -> str:
    """A hash of the file as it is now; '' when it can't be read."""
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError:
        return ""


_PART = re.compile(r"^(\w+)(?:\[(\d+)\])?$")


def _parts(line: str) -> list[tuple[str, int | None]]:
    out = []
    for part in str(line or "").split("."):
        m = _PART.match(part)
        if not m:
            raise Refused(f"not a profile line: {line!r}")
        out.append((m.group(1), int(m.group(2)) if m.group(2) is not None else None))
    if not out:
        raise Refused("an empty profile line")
    return out


def check_line(line: str) -> list[tuple[str, int | None]]:
    """A line a click may write: a known fact section, never a sensitive one, never a PROFILE_PATHS target."""
    parts = _parts(line)
    if line.startswith(SENSITIVE_PREFIXES) or parts[0][0] not in WRITABLE_SECTIONS:
        raise Refused(f"`{line}` is yours to edit by hand; a click never writes it")
    if any(line == target for _, target in sensitive.PROFILE_PATHS):
        raise Refused(f"`{line}` answers a legal, EEO or pay question; a click never writes it")
    return parts


def _set(node: Any, parts: list[tuple[str, int | None]], value: Any) -> None:
    """
    Set the value at the dotted path inside a ruamel round-trip tree,
    creating missing dict keys only. A node that is a mapping or a list
    is never overwritten with a scalar (finding 5 of 5737dfd's review).
    """
    from ruamel.yaml.comments import CommentedMap
    for i, (key, idx) in enumerate(parts):
        last = i == len(parts) - 1
        if not isinstance(node, dict):
            raise Refused(f"`{key}` is not under a mapping in profile.yaml")
        if idx is not None:
            seq = node.get(key)
            if not isinstance(seq, list) or idx >= len(seq):
                raise Refused(f"`{key}[{idx}]` is not in profile.yaml")
            if last:
                if isinstance(seq[idx], (dict, list)):
                    raise Refused(f"`{key}[{idx}]` is a whole entry, not one line; a click never overwrites it")
                seq[idx] = value
                return
            node = seq[idx]
            continue
        if last:
            if isinstance(node.get(key), (dict, list)):
                raise Refused(f"`{key}` is a section or a list, not one line; a click never overwrites it")
            node[key] = value
            return
        if node.get(key) is None:
            node[key] = CommentedMap()
        node = node[key]


def _atomic_write(path: Path, text: str) -> None:
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def backups_of(path: Path) -> list[Path]:
    """The timestamped backups beside the profile, oldest first."""
    return sorted(Path(path).parent.glob(Path(path).name + ".bak-*"))


def _prune_backups(path: Path, keep: int = KEEP_BACKUPS) -> None:
    for old in backups_of(path)[:-keep] if keep > 0 else backups_of(path):
        try:
            old.unlink()
        except OSError:
            pass


def apply_profile_line(path: Path, line: str, value: str, expected_stamp: str) -> Path:
    """
    Write `line` = `value` (a quoted string) into profile.yaml at `path`,
    comments and order kept, after a timestamped backup beside it (the
    last KEEP_BACKUPS kept). Refuses a sensitive line, a mapping or list
    node (Refused) and a file that changed since `expected_stamp` was
    taken (Changed). The check and the write happen under LOCK. Returns
    the backup's path.
    """
    from ruamel.yaml import YAML
    from ruamel.yaml.scalarstring import DoubleQuotedScalarString
    path = Path(path)
    parts = check_line(line)
    if not isinstance(value, str) or not value.strip():
        raise Refused("nothing to write: the value is empty")
    if "\n" in value or "\r" in value:
        raise Refused("a profile line holds one line of text")
    with LOCK:
        if profile_stamp(path) != expected_stamp or not expected_stamp:
            raise Changed("profile.yaml changed since this was listed; nothing written")
        text = path.read_text(encoding="utf-8")
        rt = YAML()
        rt.preserve_quotes = True
        rt.width = 4096
        # The template's own layout: list items indented under their key
        # ("  - institution:"), so a round trip moves nothing.
        rt.indent(mapping=2, sequence=4, offset=2)
        data = rt.load(text)
        if data is None:
            raise Refused("profile.yaml is empty")
        # Quoted, so a value that reads like YAML ("yes", "1e3", "# x")
        # stays the text Jack typed (finding 4 of 5737dfd's review).
        _set(data, parts, DoubleQuotedScalarString(value.strip()))
        stamp = time.strftime('%Y%m%d-%H%M%S')
        # A second save within the second gets the next number after the
        # highest present, so the names keep their order through pruning.
        same = [b.name[len(f"{path.name}.bak-{stamp}"):] for b in backups_of(path)
                if b.name.startswith(f"{path.name}.bak-{stamp}")]
        n = max([1] + [int(s[1:]) for s in same if s[1:].isdigit()] + ([1] if "" in same else [0]))
        backup = path.with_name(f"{path.name}.bak-{stamp}" + ("" if not same else f"-{n + 1:02d}"))
        backup.write_text(text, encoding="utf-8")
        from io import StringIO
        out = StringIO()
        rt.dump(data, out)
        # ruamel writes "- " for an empty list item where the template has "-":
        # trailing blanks mean nothing in YAML, so every line's are dropped and
        # the only line that differs is the one written.
        text_out = "\n".join(line_.rstrip() for line_ in out.getvalue().splitlines()) + "\n"
        _atomic_write(path, text_out)
        _prune_backups(path)
    return backup


# --- "Always tick this exact wording" (Jack, 2026-11-02, item 6) ------------

# Wordings "Always tick this exact wording" refuses outright (Jack,
# 2026-11-03): an allowlist line is ticked on every portal that shows it,
# and none of these is a consent to terms. Matched on every reading.
# Every plain form of each (fix of 5da85c0's BLOCK): the bare nouns
# (drug, background, references, credit report, consumer report),
# "checked", "signatures", "undersigned", a spaced or Unicode-dashed
# "non compete" / "at will".
# Second fix (d9c5b10's review): the credit group keeps every alternative
# the first pattern had (screen, investigation, verification, test) and
# gains inquiry / record / information; "not to compete" and "covenant not
# to"; "call or text me", "texts", "texting"; "referees"; "permit" /
# "permission"; "verify my employment / education / identity / history /
# records" in either order. The at-will term keeps its old unanchored
# shape (bounding it was a logged FLAG, not a fold).
_DASH = r"[\s\-‐-―−]"
ALLOWLIST_FORBIDDEN = re.compile(
    r"authori[sz]|\bpermi(t|ts|tted|tting|ssion)\b|\brelease|\bbackground\b|\breferenc\w*|\breferees?\b|\bdrug\w*|"
    r"\bcredit\s+(report|history|check|score|screen|investigat|verif|test|inquir|enquir|record|information)|"
    r"\bconsumer\s+report|(criminal|employment|education|medical|identity)\s+(check|screen|investigat|verif|test)|"
    r"verif\w*\s+(my\s+|your\s+|the\s+)?(employment|education|identity|history|records?|credentials?)|"
    r"\bcheck(s|ed|ing)?\b|\bscreening|arbitrat|at" + _DASH + r"*will|e-?sign|electronic\s+signature|"
    r"signatur|\bsign(s|ed|ing|er|ers)?\b|undersign|\bcontact|\bsms\b|text\s+messag|\btexts?\s+me\b|\btexts\b|"
    r"\btexting\b|\bcall\s+(or\s+\w+\s+)?me\b|marketing|newsletter|"
    r"\bnon" + _DASH + r"*compet|not\s+to\s+compet|covenant\s+not|restrictive\s+covenant",
    re.I)


def allowlist_refusal(label: str) -> str | None:
    """
    Why this wording may never be a consent_allowlist line, or None: it
    reads as a legal, eligibility, EEO or pay question (LEGAL_GUARD or a
    PROFILE_PATHS line), a marketing / opt-in wording, an authorize /
    release / check / arbitration / at-will / signing / contact /
    non-compete wording (ALLOWLIST_FORBIDDEN, Jack, 2026-11-03), a
    per-company or criminal-record question, any HARD_STOPS kind but the
    terms / consent entry, or it is empty / over-long. A line here lets
    a box be ticked on an exact match, so it must never answer a
    question. The server route (gui.State.always_tick, through
    add_consent_allowlist_line) refuses on this, not only the button.
    """
    from . import gate
    text = " ".join(str(label or "").split())
    if not text or len(text) > gate.ATTEST_MAX_CHARS:
        return "an empty or over-long wording"
    variants = sensitive.text_variants(text)
    if any(gate.CONSENT_MARKETING.search(v) for v in variants):
        return "a marketing / communications opt-in"
    if any(ALLOWLIST_FORBIDDEN.search(v) for v in variants):
        return ("an authorize / release / check / arbitration / at-will / signing / contact / marketing / "
                "non-compete wording")
    if any(sensitive.stop_kinds(v) - {"legal"} for v in variants) or any(sensitive.record_stop(v) for v in variants):
        return "a credential, per-company or criminal-record question"
    if any(sensitive.is_past_employer_question(v) for v in variants):
        return "a past-employer question"
    if (sensitive.legal_guard({"label": text}) is not None
            or sensitive.sensitive_line(text, gate.SENSITIVE_PREFIXES) is not None
            or sensitive.all_profile_lines(text)):
        return "an eligibility, EEO or pay question"
    return None


def add_consent_allowlist_line(path: Path, label: str, expected_stamp: str) -> Path:
    """
    Append `label` (a quoted string, as written) to profile.yaml's
    consent_allowlist, comments and order kept, after a timestamped
    backup, under LOCK, refusing a wording allowlist_refusal names, one
    already listed, a file that changed since `expected_stamp` (Changed)
    and a consent_allowlist that is not a list. Jack's click only.
    """
    from ruamel.yaml import YAML
    from ruamel.yaml.comments import CommentedSeq
    from ruamel.yaml.scalarstring import DoubleQuotedScalarString
    path = Path(path)
    label = " ".join(str(label or "").split())
    why = allowlist_refusal(label)
    if why is not None:
        raise Refused(f"never allowlisted: {why}")
    with LOCK:
        if profile_stamp(path) != expected_stamp or not expected_stamp:
            raise Changed("profile.yaml changed since this was listed; nothing written")
        text = path.read_text(encoding="utf-8")
        rt = YAML()
        rt.preserve_quotes = True
        rt.width = 4096
        rt.indent(mapping=2, sequence=4, offset=2)
        data = rt.load(text)
        if not isinstance(data, dict):
            raise Refused("profile.yaml is empty")
        current = data.get("consent_allowlist")
        if current is None:
            current = CommentedSeq()
            data["consent_allowlist"] = current
        if not isinstance(current, list):
            raise Refused("consent_allowlist is not a list in profile.yaml")
        if any(" ".join(str(c).split()).casefold() == label.casefold() for c in current):
            raise Refused("this wording is already on consent_allowlist")
        current.append(DoubleQuotedScalarString(label))
        stamp = time.strftime('%Y%m%d-%H%M%S')
        same = [b.name[len(f"{path.name}.bak-{stamp}"):] for b in backups_of(path)
                if b.name.startswith(f"{path.name}.bak-{stamp}")]
        n = max([1] + [int(s[1:]) for s in same if s[1:].isdigit()] + ([1] if "" in same else [0]))
        backup = path.with_name(f"{path.name}.bak-{stamp}" + ("" if not same else f"-{n + 1:02d}"))
        backup.write_text(text, encoding="utf-8")
        from io import StringIO
        out = StringIO()
        rt.dump(data, out)
        text_out = "\n".join(line_.rstrip() for line_ in out.getvalue().splitlines()) + "\n"
        _atomic_write(path, text_out)
        _prune_backups(path)
    return backup


# --- the retired aliases of the Suggestions panel (2026-10-31) --------------

def aliases_path(profile_path: Path) -> Path:
    return Path(profile_path).parent / ALIASES_FILE


def retire_aliases(profile_path: Path) -> int | None:
    """
    Rename aliases.yaml beside the profile to aliases.yaml.retired and
    say how many entries it held; nothing is learned from it (Jack,
    2026-11-01: "a frozen copy of a line's value would go stale when I
    edit my profile"). None when there was no file.
    """
    import yaml
    at = aliases_path(profile_path)
    if not at.exists():
        return None
    try:
        raw = yaml.safe_load(at.read_text(encoding="utf-8"))
        count = len(raw) if isinstance(raw, dict) else 0
    except (OSError, yaml.YAMLError):
        count = 0
    target = at.with_name(at.name + RETIRED_SUFFIX)
    if target.exists():
        target = at.with_name(f"{at.name}{RETIRED_SUFFIX}-{time.strftime('%Y%m%d-%H%M%S')}")
    os.replace(at, target)
    return count
