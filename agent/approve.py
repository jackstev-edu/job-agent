"""
approve.py — the one click that applies a suggestion Jack approved.

WHAT THIS DOES: Writes what the GUI's Apply button asks for (decision B,
Jack, 2026-10-29 evening: "suggest, I approve"): one profile line to a
new value, with profile.yaml's comments and order kept (ruamel.yaml's
round-trip loader), after a timestamped backup beside it, refusing when
the file changed since the suggestion was listed; a label -> profile-line
alias into aliases.yaml beside the profile; and a dismissed suggestion's
id into suggestions-dismissed.json so it never comes back for that value.
Every write goes to a temporary file first and is swapped in atomically.
WHY IT MATTERS: Nothing learned is reused until Jack applies it, and a
legal, eligibility, EEO or pay line is never written by a click: those
suggestions are "edit this line by hand" and this module refuses them.
An alias can only point at a non-sensitive line, and the router consults
it only for a field both layers already let the model answer.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import time
from pathlib import Path
from typing import Any

import yaml

from . import learn, sensitive

# Lines a click never writes, and an alias never points at.
SENSITIVE_PREFIXES = learn.SENSITIVE_PREFIXES + ("per_company.",)
# The only sections a click may write into (learn's own fact sections).
WRITABLE_SECTIONS = learn._FACT_SECTIONS

ALIASES_FILE = "aliases.yaml"
DISMISSED_FILE = "suggestions-dismissed.json"


def alias_key(label: Any) -> str:
    """
    The key an alias is matched on: NFKC, casefold, whitespace collapsed,
    one trailing required '*' dropped. Nothing else (the allowed
    normalisation; fold of 5737dfd's review).
    """
    import unicodedata
    text = unicodedata.normalize("NFKC", str(label or "")).casefold()
    text = re.sub(r"\s+", " ", text).strip()
    if text.endswith("*"):
        text = text[:-1].rstrip()
    return text


class Refused(Exception):
    """The write is not allowed: a sensitive line, an unknown section, a malformed path."""


class Changed(Exception):
    """profile.yaml changed since the suggestion was listed: nothing written."""


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
    """A line a click may write or an alias may point at: a known fact section, never a sensitive one, never a PROFILE_PATHS target."""
    parts = _parts(line)
    if line.startswith(SENSITIVE_PREFIXES) or parts[0][0] not in WRITABLE_SECTIONS:
        raise Refused(f"`{line}` is yours to edit by hand; a click never writes it")
    if any(line == target for _, target in sensitive.PROFILE_PATHS):
        raise Refused(f"`{line}` answers a legal, EEO or pay question; a click never writes it")
    return parts


def _set(node: Any, parts: list[tuple[str, int | None]], value: Any) -> None:
    """Set the value at the dotted path inside a ruamel round-trip tree, creating missing dict keys only."""
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
                seq[idx] = value
                return
            node = seq[idx]
            continue
        if last:
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


def apply_profile_line(path: Path, line: str, value: str, expected_stamp: str) -> Path:
    """
    Write `line` = `value` into profile.yaml at `path`, comments and order
    kept, after a timestamped backup beside it. Refuses a sensitive line
    (Refused) and a file that changed since `expected_stamp` was taken
    (Changed). Returns the backup's path.
    """
    from ruamel.yaml import YAML
    path = Path(path)
    parts = check_line(line)
    if not isinstance(value, str) or not value.strip():
        raise Refused("nothing to write: the suggested value is empty")
    if profile_stamp(path) != expected_stamp or not expected_stamp:
        raise Changed("profile.yaml changed since the suggestion was listed; it is listed again, nothing written")
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
    _set(data, parts, value)
    backup = path.with_name(f"{path.name}.bak-{time.strftime('%Y%m%d-%H%M%S')}")
    backup.write_text(text, encoding="utf-8")
    from io import StringIO
    out = StringIO()
    rt.dump(data, out)
    # ruamel writes "- " for an empty list item where the template has "-":
    # trailing blanks mean nothing in YAML, so every line's are dropped and
    # the only line that differs is the one written.
    text_out = "\n".join(line.rstrip() for line in out.getvalue().splitlines()) + "\n"
    _atomic_write(path, text_out)
    return backup


# --- aliases: a question's wording -> the non-sensitive profile line that answers it ---

def aliases_path(profile_path: Path) -> Path:
    return Path(profile_path).parent / ALIASES_FILE


def load_aliases(profile_path: Path) -> dict[str, str]:
    """
    The aliases beside the profile, normalised label -> line. A line
    that fails check_line (hand-edited to a sensitive one, say) is left
    out here too: the file can only add mappings to ordinary lines.
    """
    at = aliases_path(profile_path)
    try:
        raw = yaml.safe_load(at.read_text(encoding="utf-8")) if at.exists() else {}
    except (OSError, yaml.YAMLError):
        return {}
    out: dict[str, str] = {}
    for label, line in (raw or {}).items() if isinstance(raw, dict) else []:
        if not isinstance(label, str) or not isinstance(line, str):
            continue
        try:
            check_line(line)
        except Refused:
            continue
        out[alias_key(label)] = line
    return out


def add_alias(profile_path: Path, label: str, line: str) -> Path:
    """Record that questions worded `label` are answered by `line`. Refuses a sensitive or unknown line."""
    check_line(line)
    key = alias_key(label)
    if not key:
        raise Refused("an alias needs the question's wording")
    at = aliases_path(profile_path)
    current = {}
    if at.exists():
        try:
            current = yaml.safe_load(at.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError:
            current = {}
    if not isinstance(current, dict):
        current = {}
    current[key] = line
    header = ("# Aliases Jack approved in the GUI: a question's wording -> the profile\n"
              "# line that answers it. Only ordinary lines; eligibility, EEO, pay and\n"
              "# per-company lines are never accepted here.\n")
    _atomic_write(at, header + yaml.safe_dump(current, allow_unicode=True, sort_keys=True))
    return at


# --- dismissed suggestions ----------------------------------------------------

def dismissed_path(profile_path: Path) -> Path:
    return Path(profile_path).parent / DISMISSED_FILE


def load_dismissed(profile_path: Path) -> set[str]:
    at = dismissed_path(profile_path)
    try:
        raw = json.loads(at.read_text(encoding="utf-8")) if at.exists() else []
    except (OSError, ValueError):
        return set()
    return {str(x) for x in raw} if isinstance(raw, list) else set()


def dismiss(profile_path: Path, suggestion_id: str) -> None:
    ids = load_dismissed(profile_path) | {str(suggestion_id)}
    _atomic_write(dismissed_path(profile_path), json.dumps(sorted(ids), indent=0) + "\n")
