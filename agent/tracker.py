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


@contextlib.contextmanager
def _connect() -> Iterator[sqlite3.Connection]:
    # sqlite3's own `with conn:` commits but never closes, and an open
    # connection keeps the file locked on Windows until garbage collection.
    config.ensure_dirs()
    conn = sqlite3.connect(config.DATABASE_PATH)
    try:
        conn.row_factory = sqlite3.Row
        conn.executescript(SCHEMA)
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
           answers=None, flags=None, report_path="", status="draft") -> None:
    """
    Insert, or update in place if this URL is already logged.

    A row past draft keeps its status, submitted_at, and what it was sent
    with (company, role, ats, resume). Only the answers, flags and review
    sheet are refreshed. A draft row is refreshed in full.
    """
    keep = ",\n                ".join(
        _unless_settled(c) for c in ("company", "role", "ats", "resume_used", "status"))
    with _connect() as conn:
        conn.execute(
            f"""
            INSERT INTO applications
                (url, company, role, ats, status, resume_used,
                 answers_json, flags_json, report_path, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(url) DO UPDATE SET
                answers_json = excluded.answers_json,
                flags_json   = excluded.flags_json,
                report_path  = excluded.report_path,
                {keep}
            """,
            (url, company, role, ats, status, resume_used,
             json.dumps(answers or [], default=str),
             json.dumps(flags or [], default=str),
             report_path, _timestamp()),
        )


def mark_submitted(url: str, notes: str = "") -> None:
    with _connect() as conn:
        conn.execute(
            "UPDATE applications SET status='submitted', submitted_at=?, notes=? "
            "WHERE url=?", (_timestamp(), notes, url))


def set_status(url: str, status: str) -> None:
    """For statuses you set by hand: rejected, interview, offer."""
    with _connect() as conn:
        conn.execute("UPDATE applications SET status=? WHERE url=?", (status, url))


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
    with _connect() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM applications WHERE status='submitted' "
            "AND submitted_at <= datetime('now', ?) ORDER BY submitted_at",
            (f"-{days} days",))]
