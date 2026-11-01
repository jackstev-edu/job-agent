"""
memory.py — what Jack entered himself, learned for the same question.

WHAT THIS DOES: Keeps `~/.job-agent/learned.yaml` (Jack, 2026-11-01:
"learn from my entries... what the agent misses and I have to manually
fill"): every box he filled or changed himself under a pause, or after
the agent failed or left it blank, becomes one entry keyed by the
question as asked (the label, the field type and the sorted options, in
the allowed normalisation: gate.question_key). Three kinds: a choice
mapping (a profile line -> the option he picked for this key, the line
unchanged), an answer (no profile line: portal-specific, per-company,
or a legal / EEO / pay answer of his own) and a profile candidate (typed
text that differs in content from an ordinary profile line; offered on
the Memory page with "Save to profile", never written by this module).
pipeline.route reuses an entry only on the exact same key, a choice only
as an option the form offers now, free text and per-company answers only
at the same host, and a legal answer only when the field reads as legal
now and the entry was learned as one. Never learned, never reused: a
file box, a password or anything secret-classified, a credential, a
checkbox of any kind, a consent or legal-declaration field, any
HARD_STOPS match. Local only: nothing here reaches a model or the
network; the model never sees a learned legal answer.
WHY IT MATTERS: The profile fills every portal the same way; what one
portal asks differently is answered the way Jack answered it there, and
only there. profile.yaml is never written by learning.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import yaml

from . import config, extract, gate, learn as learn_mod, sensitive

LEARNED_FILE = "learned.yaml"
CHOICE, ANSWER, CANDIDATE = "choice", "answer", "candidate"
ANY = "any"                                   # an entry reused at every host
LEGAL_CATEGORIES = {"work_authorization", "eeo", "compensation"}
SENSITIVE_PREFIXES = ("eligibility.", "eeo.", "compensation.")
# Categories whose answer is reused at the same host only: what one
# company asks in its own words, or about itself (Jack, 2026-11-01).
HOST_ONLY_CATEGORIES = {"free_text", "per_company"}
# Categories never learned: a credential, a consent, a legal declaration.
NEVER_CATEGORIES = {"credential", "consent", "legal_declaration"}

_LOCK = threading.RLock()


def path() -> Path:
    return config.STATE_DIR / LEARNED_FILE


def host_of(url: str) -> str:
    try:
        return (urlsplit(str(url or "")).hostname or "").lower()
    except ValueError:
        return ""


def _norm(value: Any) -> str:
    return gate.normalize_label(value)


def never_reason(field: dict, category: str) -> str | None:
    """Why this field is never learned from and never answered from memory, or None."""
    ftype = (field.get("type") or "").lower()
    if ftype in ("file", "password", "checkbox") or extract.is_secret(field):
        return f"a {ftype or 'secret'} box is never learned"
    if category in NEVER_CATEGORIES:
        return f"a {category.replace('_', ' ')} field is never learned"
    if sensitive.stop_reason(sensitive._question_text(field)) is not None or field.get("raw_stop"):
        return "a hard-stop question is never learned"
    if {"dod", "clearance"} & set(sensitive.legal_guard_kinds(field)):
        # Every Department of Defense question, and a clearance question
        # outside its two profile lines, is Jack's (2026-10-20, second
        # set); decision 4 of 2026-11-01 did not name them, so memory
        # neither learns nor answers one (fold of fe30a9a's review).
        return "a Department of Defense or clearance question is always yours"
    if not _norm(field.get("label")):
        return "a question with no label has no key"
    return None


def is_legal(field: dict, category: str) -> bool:
    """Does the field read as eligibility, EEO or pay (the gate's own test)? Such an entry is reused only on such a field."""
    return (category in LEGAL_CATEGORIES
            or sensitive.legal_guard(field) is not None
            or bool(field.get("raw_legal"))
            or sensitive.sensitive_line(sensitive._question_text(field), SENSITIVE_PREFIXES) is not None)


def _line_for(field: dict, decision: dict | None) -> str | None:
    """The profile line the question belongs to: the one that filled it, else the one its wording maps to. A name, never a value."""
    return ((decision or {}).get("profile_path")
            or next(iter(sensitive.profile_lines(sensitive._question_text(field))), None))


def _profile_text(prof, line: str | None) -> str | None:
    """The line's value as text, or None when blank, missing, a bool, a list or a mapping."""
    if not line or prof is None:
        return None
    try:
        raw = prof.get(line)
    except Exception:
        return None
    if raw is None or isinstance(raw, (bool, list, dict)):
        return None
    text = str(raw).strip()
    return text or None


def entry_id(key: str, kind: str, scope: str, answer: Any) -> str:
    return hashlib.sha1(f"{key}\x00{kind}\x00{scope}\x00{_norm(answer)}".encode("utf-8")).hexdigest()[:12]


class Learned:
    """The store, loaded once; every write goes through the one lock and an atomic replace."""

    def __init__(self, entries: list[dict], at: Path | None = None):
        self.entries = entries
        self.at = at or path()

    @classmethod
    def load(cls, at: Path | None = None) -> "Learned":
        at = at or path()
        with _LOCK:
            try:
                raw = yaml.safe_load(at.read_text(encoding="utf-8")) if at.exists() else None
            except (OSError, yaml.YAMLError):
                raw = None
        entries = raw.get("entries") if isinstance(raw, dict) else None
        good = [e for e in (entries or []) if isinstance(e, dict) and isinstance(e.get("key"), str)
                and e.get("kind") in (CHOICE, ANSWER, CANDIDATE) and isinstance(e.get("id"), str)]
        return cls(good, at)

    def save(self) -> None:
        with _LOCK:
            self.at.parent.mkdir(parents=True, exist_ok=True)
            text = ("# Learned by job-agent from what you entered yourself (Jack, 2026-11-01).\n"
                    "# Reused only on the exact same question. Edit or delete entries on the\n"
                    "# GUI's Memory page; profile.yaml is never written from here.\n"
                    + yaml.safe_dump({"version": 1, "entries": self.entries}, allow_unicode=True,
                                     sort_keys=False, width=4096))
            fd, tmp = tempfile.mkstemp(prefix=f".{self.at.name}.", suffix=".tmp", dir=str(self.at.parent))
            try:
                with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
                    fh.write(text)
                os.replace(tmp, self.at)
            finally:
                if os.path.exists(tmp):
                    os.unlink(tmp)

    # -- lookups ---------------------------------------------------------------
    def match(self, field: dict, category: str, host: str) -> dict | None:
        """
        The entry for this exact question at this host, or None: the key
        equal, the entry's scope ANY or this host, and the entry learned as
        legal exactly when the field reads as legal now. The latest wins.
        """
        if never_reason(field, category) is not None:
            return None
        key = gate.question_key(field)
        legal = is_legal(field, category)
        found = [e for e in self.entries
                 if e.get("key") == key and bool(e.get("legal")) == legal
                 and (e.get("scope") == ANY or (host and e.get("scope") == host))]
        if not found:
            return None
        return max(found, key=lambda e: (str(e.get("last_seen") or ""), self.entries.index(e)))

    def get(self, entry_id_: str) -> dict | None:
        return next((e for e in self.entries if e.get("id") == entry_id_), None)

    def delete(self, entry_id_: str) -> bool:
        with _LOCK:
            before = len(self.entries)
            self.entries = [e for e in self.entries if e.get("id") != entry_id_]
            if len(self.entries) == before:
                return False
            self.save()
            return True

    def edit(self, entry_id_: str, answer: str) -> bool:
        """Jack tunes an answer; the id changes with it (it names the answer), the key and kind stay."""
        with _LOCK:
            e = self.get(entry_id_)
            if e is None or not isinstance(answer, str) or not answer.strip():
                return False
            e["answer"] = answer.strip()
            e["id"] = entry_id(e["key"], e["kind"], e.get("scope") or ANY, e["answer"])
            e["last_seen"] = _now()
            self.save()
            return True

    def note_use(self, entry_id_: str) -> None:
        with _LOCK:
            e = self.get(entry_id_)
            if e is not None:
                e["uses"] = int(e.get("uses") or 0) + 1
                try:
                    self.save()
                except OSError:
                    pass


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _upsert(store: Learned, entry: dict) -> dict:
    """The same key, kind, scope and answer again: one entry, seen once more; otherwise a new one (both kept)."""
    for e in store.entries:
        if e.get("id") == entry["id"]:
            e["last_seen"] = entry["last_seen"]
            e["seen"] = int(e.get("seen") or 1) + 1
            e["posting"] = entry["posting"]
            return e
    store.entries.append(entry)
    return entry


def learn(changes: list[dict], *, url: str, portal: str, prof, store: Learned | None = None) -> list[dict]:
    """
    One entry per change Jack made (corrections.changes: the field, the
    kind of change, what the agent held, what the box holds now, the
    plan's decision and the category). Returns the entries learned, in
    the store's shape, and saves. Values of a legal field are kept here
    and nowhere else; a field never_reason refuses is skipped.
    """
    store = store or Learned.load()
    host = host_of(url)
    learned: list[dict] = []
    for ch in changes:
        field, category = ch["field"], ch.get("category") or "unknown"
        now = ch.get("now")
        if now in (None, False) or not str(now).strip():
            continue
        if never_reason(field, category) is not None:
            continue
        key = gate.question_key(field)
        options = [str(o) for o in field.get("options") or []]
        answer = str(now).strip()
        if options and not any(_norm(o) == _norm(answer) for o in options):
            continue                         # a choice must be one of the form's own options
        legal = is_legal(field, category)
        line = _line_for(field, ch.get("decision"))
        if legal:
            kind, scope, extra = ANSWER, (ANY if options else host), {}
        elif options:
            current = _profile_text(prof, line)
            if line and current is not None:
                kind, scope, extra = CHOICE, ANY, {"line_value": current}
            else:
                kind, scope, extra = ANSWER, (host if category in HOST_ONLY_CATEGORIES else ANY), {}
        else:
            current = _profile_text(prof, line)
            if line and current is not None and not line.startswith(SENSITIVE_PREFIXES + ("per_company.",)):
                if learn_mod.same_answer(current, answer, field.get("type")):
                    continue                 # a formatting-only difference teaches nothing
                kind, scope, extra = CANDIDATE, ANY, {"line_value": current}
            else:
                kind, scope, extra = ANSWER, (host if category in HOST_ONLY_CATEGORIES else ANY), {}
        if not scope:
            continue                         # host-only with no host to tie it to
        stamp = _now()
        entry = {"id": entry_id(key, kind, scope, answer), "kind": kind, "key": key,
                 "question": str(field.get("label") or "")[:300], "type": (field.get("type") or "").lower(),
                 "options": options[:60], "answer": answer, "line": line,
                 "legal": legal, "scope": scope, "host": host, "portal": portal or "",
                 "first_seen": stamp, "last_seen": stamp, "seen": 1, "uses": 0, "posting": str(url)[:300],
                 "change": ch.get("kind") or "", **extra}
        with _LOCK:
            learned.append(_upsert(store, entry))
    if learned:
        store.save()
    return learned


def reuse(field: dict, category: str, verdict: dict, prof, store: Learned | None, host: str) -> dict | None:
    """
    The plan decision a learned entry gives this field, or None. The
    router (pipeline.route) calls this after sensitive.decide: on a human
    verdict the entry fills the gap; on a profile fill only a choice
    mapping of the same line overrides it; on a model route the entry
    answers ahead of the model. Checked again here: a never_reason field,
    a choice that is no option of the form now, a choice mapping whose
    profile line changed since, and a legal entry on a field that doesn't
    read as legal now (or the reverse) all give None.
    """
    if store is None:
        return None
    entry = store.match(field, category, host)
    if entry is None:
        return None
    legal = is_legal(field, category)
    if verdict.get("action") == "fill":
        # The profile answered with a strict fit: only his own choice for
        # this exact key, mapped from the same line, replaces it.
        if legal or entry.get("kind") != CHOICE or entry.get("line") != verdict.get("profile_path"):
            return None
    if entry.get("kind") == CHOICE:
        current = _profile_text(prof, entry.get("line"))
        if current is None or _norm(current) != _norm(entry.get("line_value")):
            return None                       # the line changed since: the mapping no longer says anything
    options = [str(o) for o in field.get("options") or []]
    answer = str(entry.get("answer") or "")
    if options:
        value = next((o for o in options if _norm(o) == _norm(answer)), None)
        if value is None:
            return None
        fit = "exact"
    else:
        if not answer.strip():
            return None
        value, fit = answer, "verbatim"
    return {**verdict, "action": "fill", "value": value, "source": "learned", "fit": fit,
            "confidence": "high", "profile_path": entry.get("line") or None,
            "learned_key": entry["key"], "learned_id": entry["id"]}


def describe(entry: dict) -> str:
    """One line for a job log or a report: the question and the kind, never the answer."""
    what = {CHOICE: "choice", ANSWER: "legal answer" if entry.get("legal") else "answer",
            CANDIDATE: "profile candidate"}.get(entry.get("kind"), "entry")
    return f"'{str(entry.get('question') or '')[:40]}' ({what})"
