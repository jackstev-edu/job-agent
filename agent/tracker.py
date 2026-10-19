"""
tracker.py — the logbook.

WHAT THIS DOES: Keeps a small database of everywhere you've applied, what you
answered, and when. It lives in ~/.job-agent/applications.db, outside the
project folder, so it can never end up in a git commit.

WHY IT MATTERS: It stops you double-applying to the same posting, gives you a
follow-up list three weeks later, and records exactly what you said — which
you'll want on hand the day someone calls about it.
"""

from __future__ import annotations

import contextlib
import json
import sqlite3
from collections.abc import Iterator
from datetime import datetime, timezone

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS applications (
  id            INTEGER PRIMARY KEY,
  url           TEXT UNIQUE NOT NULL,
  company       TEXT,
  role          TEXT,
  ats           TEXT,
  -- draft: filled but not sent. submitted: you clicked the button.
  status        TEXT NOT NULL DEFAULT 'draft',
  resume_used   TEXT,
  answers_json  TEXT,      -- the full fill report
  flags_json    TEXT,      -- what was handed back to you
  report_path   TEXT,      -- the HTML review sheet
  created_at    TEXT NOT NULL,
  submitted_at  TEXT,
  notes         TEXT
);
CREATE INDEX IF NOT EXISTS idx_applications_status ON applications(status);
"""

# Schema changes since the first release, applied in order to any database
# whose PRAGMA user_version is below the number. Additive only: a migration
# never rewrites or drops what's already logged.
MIGRATIONS: list[tuple[int, str]] = [
    (1, """
    -- The submit gate in shadow mode: one row per fill run. Nothing is ever
    -- submitted by the agent; agent_submitted stays 0 until something is.
    CREATE TABLE IF NOT EXISTS gate_runs (
      id               INTEGER PRIMARY KEY,
      application_url  TEXT NOT NULL,
      portal           TEXT,
      portal_method    TEXT,
      verdict          TEXT NOT NULL,     -- the content verdict only
      reasons_json     TEXT,              -- content reasons
      enablement_json  TEXT,              -- {portal_allowlisted, cap_remaining, reasons}
      record_json      TEXT,              -- redacted: no answer values
      gate_version     INTEGER,
      agent_submitted  INTEGER NOT NULL DEFAULT 0,
      human_submitted  TEXT NOT NULL DEFAULT 'unknown',   -- y / n / unknown
      edits_json       TEXT,              -- which fields you changed
      capture          TEXT,              -- exact / polled / none
      success_signal   TEXT,
      created_at       TEXT NOT NULL,
      outcome_at       TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_gate_runs_portal ON gate_runs(portal, created_at);
    """),
    (2, """
    -- The unattended run (`apply.py run`): what the confirmation page showed
    -- after the agent submitted, and whether it was the agent that did.
    -- Statuses gain needs_you and failed; the reason lives in notes.
    ALTER TABLE applications ADD COLUMN evidence_json TEXT;
    ALTER TABLE applications ADD COLUMN agent_submitted INTEGER NOT NULL DEFAULT 0;
    """),
]
SCHEMA_VERSION = MIGRATIONS[-1][0]


def _migrate(conn: sqlite3.Connection) -> None:
    current = conn.execute("PRAGMA user_version").fetchone()[0]
    for version, sql in MIGRATIONS:
        if version > current:
            conn.executescript(sql)
            # PRAGMA takes no bound parameters; the version is our own int.
            conn.execute(f"PRAGMA user_version = {int(version)}")


@contextlib.contextmanager
def _connect() -> Iterator[sqlite3.Connection]:
    # sqlite3's own `with conn:` commits but never closes, and an open
    # connection keeps the file locked on Windows until garbage collection.
    config.ensure_dirs()
    conn = sqlite3.connect(config.DATABASE_PATH)
    try:
        conn.row_factory = sqlite3.Row
        conn.executescript(SCHEMA)
        _migrate(conn)
        with conn:                      # commit on success, roll back on error
            yield conn
    finally:
        conn.close()


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def find(url: str) -> dict | None:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM applications WHERE url = ?", (url,)).fetchone()
        return dict(row) if row else None


# Statuses that record what happened after you sent it. A --force rerun is
# only refilling the form, so it must not knock one of these back to draft.
PAST_DRAFT = ("submitted", "rejected", "interview", "offer")

# In the upsert below: true when the row already logged is past draft.
_SETTLED = "applications.status IN ({})".format(", ".join(f"'{s}'" for s in PAST_DRAFT))


def _unless_settled(column: str) -> str:
    return f"{column} = CASE WHEN {_SETTLED} THEN applications.{column} ELSE excluded.{column} END"


def record(url: str, *, company="", role="", ats="", resume_used="",
           answers=None, flags=None, report_path="", status="draft", notes="") -> None:
    """
    Insert, or update in place if this URL is already logged.

    A row past draft keeps its status, submitted_at, notes and what it was
    sent with (company, role, ats, resume). Only the answers, flags and
    review sheet are refreshed. A draft or parked row is refreshed in full
    (`notes` holds why a `run` parked it).
    """
    keep = ",\n                ".join(
        _unless_settled(c) for c in ("company", "role", "ats", "resume_used", "status", "notes"))
    with _connect() as conn:
        conn.execute(
            f"""
            INSERT INTO applications
                (url, company, role, ats, status, resume_used,
                 answers_json, flags_json, report_path, created_at, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(url) DO UPDATE SET
                answers_json = excluded.answers_json,
                flags_json   = excluded.flags_json,
                report_path  = excluded.report_path,
                {keep}
            """,
            (url, company, role, ats, status, resume_used,
             json.dumps(answers or [], default=str),
             json.dumps(flags or [], default=str),
             report_path, _timestamp(), notes),
        )


def mark_submitted(url: str, notes: str = "") -> None:
    with _connect() as conn:
        conn.execute(
            "UPDATE applications SET status='submitted', submitted_at=?, notes=? "
            "WHERE url=?", (_timestamp(), notes, url))


def set_status(url: str, status: str) -> None:
    """
    For statuses you set by hand: submitted, rejected, interview, offer.

    "submitted" also records submitted_at, but only when it's empty. Without
    it `followup` never lists the application; an existing date is the real
    one and is never overwritten.
    """
    with _connect() as conn:
        conn.execute(
            """
            UPDATE applications SET
                status = ?,
                submitted_at = CASE
                    WHEN ? = 'submitted' AND COALESCE(submitted_at, '') = '' THEN ?
                    ELSE submitted_at END
            WHERE url = ?
            """, (status, status, _timestamp(), url))


def all_applications(status: str | None = None) -> list[dict]:
    query = "SELECT * FROM applications"
    args: tuple = ()
    if status:
        query += " WHERE status = ?"
        args = (status,)
    query += " ORDER BY created_at DESC"
    with _connect() as conn:
        return [dict(r) for r in conn.execute(query, args)]


def awaiting_reply(days: int = 14) -> list[dict]:
    """Submitted more than N days ago and still marked submitted."""
    # datetime() on both sides: the stored stamp is ISO ("…T…+00:00") and
    # SQLite's is "… …", and compared as text the T sorts after the space.
    with _connect() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM applications WHERE status='submitted' "
            "AND datetime(submitted_at) <= datetime('now', ?) ORDER BY submitted_at",
            (f"-{days} days",))]


# --- the submit gate's shadow log ---------------------------------------------

def record_gate_run(url: str, *, portal: str, portal_method: str, result: dict,
                    record_json: dict, agent_submitted: bool = False) -> int:
    """
    Log one gate verdict. Returns the row id, for the outcome to update
    later. `agent_submitted` is True only when submit.py pressed Submit on
    this run.
    """
    enablement = {**(result.get("enablement") or {}),
                  "reasons": result.get("enablement_reasons") or []}
    with _connect() as conn:
        cur = conn.execute(
            "INSERT INTO gate_runs (application_url, portal, portal_method, verdict, "
            "reasons_json, enablement_json, record_json, gate_version, agent_submitted, "
            "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (url, portal, portal_method, result.get("content_verdict") or "would_stop",
             json.dumps(result.get("content_reasons") or []), json.dumps(enablement),
             json.dumps(record_json, default=str), result.get("gate_version"),
             1 if agent_submitted is True else 0, _timestamp()))
        return int(cur.lastrowid)


def set_evidence(url: str, evidence: dict | None, *, agent_submitted: bool) -> None:
    """What the confirmation page showed after the agent submitted (no page text at large)."""
    with _connect() as conn:
        conn.execute(
            "UPDATE applications SET evidence_json = ?, agent_submitted = ?, "
            "submitted_at = CASE WHEN ? THEN COALESCE(submitted_at, ?) ELSE submitted_at END "
            "WHERE url = ?",
            (json.dumps(evidence or {}, default=str), 1 if agent_submitted else 0,
             1 if agent_submitted else 0, _timestamp(), url))


def set_gate_outcome(run_id: int, *, human_submitted: str, edits: list | None,
                     capture: str, success_signal: str = "") -> None:
    """What you actually did after a run: y / n / unknown, and what you changed."""
    if human_submitted not in ("y", "n", "unknown"):
        human_submitted = "unknown"
    with _connect() as conn:
        conn.execute(
            "UPDATE gate_runs SET human_submitted=?, edits_json=?, capture=?, "
            "success_signal=?, outcome_at=? WHERE id=?",
            (human_submitted, None if edits is None else json.dumps(edits), capture,
             success_signal, _timestamp(), run_id))


def gate_runs(portal: str | None = None) -> list[dict]:
    query, args = "SELECT * FROM gate_runs", ()
    if portal:
        query, args = query + " WHERE portal = ?", (portal,)
    with _connect() as conn:
        return [dict(r) for r in conn.execute(query + " ORDER BY created_at, id", args)]


def agent_submissions_today() -> int:
    """Applications the agent itself submitted today, by your local date. Zero for now."""
    with _connect() as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM gate_runs WHERE agent_submitted = 1 AND "
            "date(datetime(created_at), 'localtime') = date('now', 'localtime')").fetchone()[0]
