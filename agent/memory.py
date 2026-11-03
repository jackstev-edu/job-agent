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
now and the entry was learned as one; a profile line answered with a
strict fit (exact, verbatim, bool, decline) wins over it, a loose fit
loses to it (Jack, 2026-11-01, fold round). A Department of Defense or
clearance question is learned and reused like any other legal one (same
day, decision 1). Never learned, never reused: a file box, a password
or anything secret-classified, a credential, a checkbox of any kind, a
consent or legal-declaration field, any HARD_STOPS match, a per-company
question (the classifier's, the stop kind's or decision C's past-employer
wording), a marketing / communications opt-in, a label of three words or
fewer with no section context, and a key two or more fields of one page
share (Jack, 2026-11-02, item 2). "Same company" is the tenant
(tenant_of): host plus first path segment on the shared boards, the
subdomain on Workday and Avature, the host elsewhere. Entries that break
these rules are removed on load and listed (the questions, never a
value); a shared board's bare-host entry is rescoped from its posting.
Local only: nothing here reaches a model or the
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
from urllib.parse import parse_qsl, urlsplit

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
# Categories never learned: a credential, a consent, a legal declaration,
# and (Jack, 2026-11-02) a per-company question.
NEVER_CATEGORIES = {"credential", "consent", "legal_declaration", "per_company"}
# Job boards many employers share: there "same company" is the host plus
# the first path segment (the board's slug), never the bare host.
SHARED_BOARDS = {"job-boards.greenhouse.io", "boards.greenhouse.io", "jobs.lever.co", "jobs.ashbyhq.com"}
# Hosts where the employer is the subdomain.
SUBDOMAIN_HOSTS = (".myworkdayjobs.com", ".avature.net")
REMOVED_KEPT = 50                             # removed-on-load questions kept in the file for the Memory page
OFFERS_KEPT = 50                              # "Always tick this exact wording" offers kept (Jack, 2026-11-02, item 6)

_LOCK = threading.RLock()


def path() -> Path:
    return config.STATE_DIR / LEARNED_FILE


def host_of(url: str) -> str:
    try:
        return (urlsplit(str(url or "")).hostname or "").lower()
    except ValueError:
        return ""


def tenant_of(url: str) -> str:
    """
    "Same company" for a host-only entry (Jack, 2026-11-02, item 2): on
    job-boards.greenhouse.io, boards.greenhouse.io, jobs.lever.co and
    jobs.ashbyhq.com the host plus the first path segment; on
    *.myworkdayjobs.com and *.avature.net the subdomain; the host
    otherwise. '' when the address has no host, or a shared board's
    address names no slug.
    """
    try:
        parts = urlsplit(str(url or ""))
    except ValueError:
        return ""
    host = (parts.hostname or "").lower().rstrip(".")
    if not host:
        return ""
    if host in SHARED_BOARDS:
        first = next((seg for seg in (parts.path or "").split("/") if seg), "")
        if first.lower() == "embed":
            # Greenhouse's embedded board (/embed/job_app?for=<slug>): the
            # slug is the `for` parameter, and every employer shares the
            # path (fold of 168606b's finding 2).
            first = next((v for k, v in parse_qsl(parts.query or "") if k == "for"), "")
        return f"{host}/{first.lower()}" if first else ""
    if host.endswith(SUBDOMAIN_HOSTS):
        return host.split(".")[0]
    return host


def _norm(value: Any) -> str:
    return gate.normalize_label(value)


# The guard kinds decision 1 of the 2026-11-01 fold round names: "Have you
# worked for the Department of Defense?" is a legal question learned as
# Jack's own answer, not an employer question. No other guard kind lifts
# decision C's wording (fold of 168606b's finding 1: a legal word in help
# text or an option must not make a past-employer question learnable).
_DOD_KINDS = frozenset({"dod", "clearance"})


def _past_employer(field: dict, text: str) -> bool:
    """Decision C's past-employer wording, off a Department of Defense / clearance question."""
    if not sensitive.is_past_employer_question(text):
        return False
    kinds = set(sensitive.legal_guard_kinds(field))
    return not (kinds and kinds <= _DOD_KINDS)


def is_opt_in(field: dict) -> bool:
    """Does the question read as a marketing / communications / newsletter / SMS / talent-community opt-in (the consent policy's own patterns)?"""
    return any(gate.CONSENT_MARKETING.search(v) for v in sensitive.text_variants(sensitive._question_text(field)))


def shared_keys(fields: list[dict]) -> set[str]:
    """The question keys two or more fields of one page share: never learned, never reused there."""
    seen: dict[str, int] = {}
    for f in fields:
        if f.get("type") == "file":
            continue
        key = gate.question_key(f)
        seen[key] = seen.get(key, 0) + 1
    return {k for k, n in seen.items() if n > 1}


def never_reason(field: dict, category: str) -> str | None:
    """Why this field is never learned from and never answered from memory, or None."""
    ftype = (field.get("type") or "").lower()
    if ftype in ("file", "password", "checkbox") or extract.is_secret(field):
        return f"a {ftype or 'secret'} box is never learned"
    if field.get("widget") == "checkboxes":
        return "a checkbox group is never learned"       # Jack: no checkbox is learned (fold of ab0a09e's finding 4)
    if category in NEVER_CATEGORIES:
        return f"a {category.replace('_', ' ')} field is never learned"
    text = sensitive._question_text(field)
    if sensitive.stop_reason(text) is not None or field.get("raw_stop"):
        return "a hard-stop question is never learned"
    # Per-company by any reading (Jack, 2026-11-02, item 2): the classifier's
    # category (NEVER_CATEGORIES above), the stop kind, or decision C's
    # past-employer wording; and any opt-in the consent policy refuses.
    if "per_company" in sensitive.stop_kinds(text) or _past_employer(field, text):
        return "a per-company question is never learned"
    if is_opt_in(field):
        return "a marketing / communications opt-in is never learned"
    # A Department of Defense or clearance question is learned and reused
    # like every other legal question: Jack's own answer on the exact key,
    # a choice only as a current option, never the model (Jack,
    # 2026-11-01, fold round, decision 1: "only my own answers" covered
    # DoD explicitly and supersedes the 2026-10-20 ruling).
    if not _norm(field.get("label")):
        return "a question with no label has no key"
    if gate.short_label(field.get("label")) and not _norm(field.get("section")):
        return "a short label with no section context has no key"
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


class Unreadable(Exception):
    """learned.yaml exists but couldn't be read as the store: nothing is saved over it."""


def _violation(e: dict) -> str | None:
    """Why a stored entry breaks the learning rules of 2026-11-02 (item 2), or None. Judged on the question, never a value."""
    field = {"label": e.get("question"), "type": e.get("type"), "options": e.get("options") or []}
    text = sensitive._question_text(field)
    if (e.get("category") == "per_company" or "per_company" in sensitive.stop_kinds(text)
            or _past_employer(field, text)):
        return "per-company"
    if is_opt_in(field):
        return "opt-in"
    try:
        parts = json.loads(e["key"])
    except (TypeError, ValueError):
        parts = None                       # not a key this module wrote: matched by nothing, judged by nothing
    if isinstance(parts, list) and gate.short_label(e.get("question")) and (
            len(parts) < 4 or not str(parts[3] or "").strip()):
        return "short label with no section context"
    return None


class Learned:
    """
    The store. Every write (learn, edit, delete) re-reads the file under
    the one lock before applying and saving atomically, so a GUI edit and
    a learn() racing each other lose nothing (fold of 580cfd0's finding
    2). A file that exists but didn't parse marks the store unreadable:
    it loads empty, every save is refused, and the file is kept as it is
    for Jack to fix or delete (fold of fe30a9a's finding 3). On load,
    entries that break the learning rules (a per-company or opt-in
    question, a short label with no section context) are removed and a
    shared board's bare-host entry is rescoped from its posting, or
    removed when the posting names no slug; the questions removed are
    kept in the file (`removed`, the last REMOVED_KEPT) for the Memory
    page and in `just_removed` for the job log (Jack, 2026-11-02).
    """

    def __init__(self, entries: list[dict], at: Path | None = None, unreadable: str | None = None,
                 removed: list[dict] | None = None, offers: list[dict] | None = None):
        self.entries = entries
        self.at = at or path()
        self.unreadable = unreadable
        self.removed: list[dict] = removed or []     # {"at", "question", "why"}, newest last
        self.just_removed: list[str] = []            # what this load removed, as one line each
        # Boxes Jack ticked himself that neither the consent policy nor the
        # attestation class covers, offered once on the Memory page as
        # "Always tick this exact wording" (item 6): {"id", "label", "host",
        # "posting", "at"}. Never a value; never ticked from here.
        self.offers: list[dict] = offers or []

    @classmethod
    def load(cls, at: Path | None = None) -> "Learned":
        at = at or path()
        unreadable = None
        with _LOCK:
            try:
                raw = yaml.safe_load(at.read_text(encoding="utf-8")) if at.exists() else {"entries": []}
            except (OSError, yaml.YAMLError) as e:
                raw, unreadable = None, type(e).__name__
            if unreadable is None and not (isinstance(raw, dict) and isinstance(raw.get("entries"), list)):
                unreadable = "not a mapping with an entries list"
            entries = raw.get("entries") if isinstance(raw, dict) else None
            good = [e for e in (entries if isinstance(entries, list) else []) if isinstance(e, dict) and isinstance(e.get("key"), str)
                    and e.get("kind") in (CHOICE, ANSWER, CANDIDATE) and isinstance(e.get("id"), str)]
            kept_removed = [r for r in ((raw.get("removed") if isinstance(raw, dict) else None) or [])
                            if isinstance(r, dict) and isinstance(r.get("question"), str)]
            kept_offers = [o for o in ((raw.get("tick_offers") if isinstance(raw, dict) else None) or [])
                           if isinstance(o, dict) and isinstance(o.get("id"), str) and isinstance(o.get("label"), str)]
            store = cls(good, at, unreadable, kept_removed, kept_offers)
            if unreadable is None and store._clean_up():
                try:
                    store.save()
                except OSError:
                    pass                   # the cleaned copy is still what this process uses
        return store

    def _clean_up(self) -> bool:
        """Apply the load-time rules to self.entries; True when anything changed."""
        changed = False
        kept: list[dict] = []
        stamp = _now()
        for e in self.entries:
            why = _violation(e)
            scope = str(e.get("scope") or "")
            if why is None and (scope in SHARED_BOARDS or scope.endswith(SUBDOMAIN_HOSTS)):
                # A scope from before 2026-11-02: a shared board's bare host
                # (rescoped from the posting, or removed when it names no
                # slug) or a Workday / Avature host (its subdomain).
                tenant = tenant_of(e.get("posting") or "") if scope in SHARED_BOARDS else tenant_of(f"https://{scope}/")
                if tenant and tenant != scope:
                    e["scope"] = tenant
                    e["id"] = entry_id(e["key"], e["kind"], tenant, e.get("answer"))
                    changed = True
                elif scope in SHARED_BOARDS:
                    why = "a shared board's bare host (no slug in the posting address)"
            if why is None:
                kept.append(e)
                continue
            question = str(e.get("question") or "")[:120]
            self.removed.append({"at": stamp, "question": question, "why": why})
            self.just_removed.append(f"'{question[:60]}' ({why})")
            changed = True
        self.entries = kept
        self.removed = self.removed[-REMOVED_KEPT:]
        return changed

    def _refresh(self) -> None:
        """Under the lock: the file as it is now replaces this copy, before a write is applied to it."""
        fresh = Learned.load(self.at)
        self.entries, self.unreadable, self.removed, self.offers = (
            fresh.entries, fresh.unreadable, fresh.removed, fresh.offers)
        if self.unreadable:
            raise Unreadable(self.unreadable)

    def save(self) -> None:
        with _LOCK:
            if self.unreadable:
                raise Unreadable(self.unreadable)
            self.at.parent.mkdir(parents=True, exist_ok=True)
            text = ("# Learned by job-agent from what you entered yourself (Jack, 2026-11-01).\n"
                    "# Reused only on the exact same question. Edit or delete entries on the\n"
                    "# GUI's Memory page; profile.yaml is never written from here.\n"
                    + yaml.safe_dump({"version": 1, "entries": self.entries,
                                      "removed": self.removed[-REMOVED_KEPT:],
                                      "tick_offers": self.offers[-OFFERS_KEPT:]},
                                     allow_unicode=True, sort_keys=False, width=4096))
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
            self._refresh()
            before = len(self.entries)
            self.entries = [e for e in self.entries if e.get("id") != entry_id_]
            if len(self.entries) == before:
                return False
            self.save()
            return True

    def edit(self, entry_id_: str, answer: str) -> bool:
        """Jack tunes an answer; the id changes with it (it names the answer), the key and kind stay."""
        with _LOCK:
            self._refresh()
            e = self.get(entry_id_)
            if e is None or not isinstance(answer, str) or not answer.strip():
                return False
            e["answer"] = answer.strip()
            e["id"] = entry_id(e["key"], e["kind"], e.get("scope") or ANY, e["answer"])
            e["last_seen"] = _now()
            self.save()
            return True

    # -- "Always tick this exact wording" offers (item 6) -----------------------
    def add_offer(self, label: str, host: str, posting: str) -> dict | None:
        """One offer per exact wording (normalised); None when it is already listed or the wording is refused."""
        from . import approve
        label = " ".join(str(label or "").split())
        if not label or approve.allowlist_refusal(label) is not None:
            return None
        with _LOCK:
            self._refresh()
            key = _norm(label)
            if any(_norm(o.get("label")) == key for o in self.offers):
                return None
            offer = {"id": hashlib.sha1(key.encode("utf-8")).hexdigest()[:12], "label": label[:300],
                     "host": str(host or "")[:120], "posting": str(posting or "")[:300], "at": _now()}
            self.offers.append(offer)
            self.offers = self.offers[-OFFERS_KEPT:]
            self.save()
            return offer

    def get_offer(self, offer_id: str) -> dict | None:
        return next((o for o in self.offers if o.get("id") == offer_id), None)

    def drop_offer(self, offer_id: str) -> bool:
        with _LOCK:
            self._refresh()
            before = len(self.offers)
            self.offers = [o for o in self.offers if o.get("id") != offer_id]
            if len(self.offers) == before:
                return False
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


def learn(changes: list[dict], *, url: str, portal: str, prof, store: Learned | None = None,
          fields: list[dict] | None = None) -> list[dict]:
    """
    One entry per change Jack made (corrections.changes: the field, the
    kind of change, what the agent held, what the box holds now, the
    plan's decision and the category). Returns the entries learned, in
    the store's shape, and saves. Values of a legal field are kept here
    and nowhere else; a field never_reason refuses is skipped, and so is
    one whose key another field of the page (`fields`, the whole page)
    shares. Raises Unreadable, saving nothing, when the file exists but
    didn't parse.
    """
    store = store or Learned.load()
    with _LOCK:
        store._refresh()
        learned = _learn_into(store, changes, url=url, portal=portal, prof=prof,
                              shared=shared_keys(fields) if fields else set())
        if learned:
            store.save()
    return learned


def _learn_into(store: Learned, changes: list[dict], *, url: str, portal: str, prof,
                shared: set[str] = frozenset()) -> list[dict]:
    host = tenant_of(url)                    # "same company" (Jack, 2026-11-02): the tenant, not the bare host
    learned: list[dict] = []
    for ch in changes:
        field, category = ch["field"], ch.get("category") or "unknown"
        now = ch.get("now")
        if now in (None, False) or not str(now).strip():
            continue
        if never_reason(field, category) is not None:
            continue
        key = gate.question_key(field)
        if key in shared:
            continue                         # two fields of the page ask this: nothing says which
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
                 "legal": legal, "scope": scope, "host": host_of(url), "portal": portal or "",
                 "category": category,
                 "first_seen": stamp, "last_seen": stamp, "seen": 1, "uses": 0, "posting": str(url)[:300],
                 "change": ch.get("kind") or "", **extra}
        learned.append(_upsert(store, entry))
    return learned


def reuse(field: dict, category: str, verdict: dict, prof, store: Learned | None, host: str,
          shared: set[str] | None = None) -> dict | None:
    """
    The plan decision a learned entry gives this field, or None. The
    router (pipeline.route) calls this after sensitive.decide: on a human
    verdict the entry fills the gap; on a profile fill the profile line
    wins on a strict fit (exact, verbatim, bool or decline: the fits the
    gate trusts) and his exact-key entry beats a loose one (substring,
    bool_prefix, any other kind) (Jack, 2026-11-01, fold round, decision
    2); on a model route the entry answers ahead of the model. Checked
    again here: a never_reason field, a choice that is no option of the
    form now, a choice mapping whose profile line changed since, and a
    legal entry on a field that doesn't read as legal now (or the
    reverse) all give None; so does a key shared by two or more fields of
    the page (`shared`). `host` is the tenant (tenant_of) since
    2026-11-02.
    """
    if store is None:
        return None
    if shared and gate.question_key(field) in shared:
        return None
    entry = store.match(field, category, host)
    if entry is None:
        return None
    if verdict.get("action") == "fill" and verdict.get("fit") in gate.STRICT_FITS:
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
