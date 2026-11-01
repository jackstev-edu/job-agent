"""
joblog.py — one job's log, on disk as it lands.

WHAT THIS DOES: Appends every line a job's log gains, as it lands and
exactly as the log holds it, to `~/.job-agent/logs/<date>-<job id>-<host>.log`
(Jack, 2026-11-01: "track everything in the current window and record it
for future use"); reads a job's file back for the GUI's "View log"; and
prunes files older than KEEP_DAYS at GUI start, by the date in the name.
WHY IT MATTERS: The GUI row showed the newest line only and the terminal
scrolled past; a job's whole story is now kept, with nothing added to it
and no value beyond what the log already holds. A write that fails never
touches the job: the log hook swallows it.
"""

from __future__ import annotations

import re
import time
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import urlsplit

from . import config

LOGS_DIR = config.STATE_DIR / "logs"
KEEP_DAYS = 60

_NAME = re.compile(r"^(\d{4}-\d{2}-\d{2})-([0-9a-f]+)-(.*)\.log$")
_UNSAFE = re.compile(r"[^a-z0-9.-]+")


def host_of(url: str) -> str:
    """The link's host as a file-name part: lower case, letters, digits, dots and dashes only."""
    try:
        host = urlsplit(str(url or "")).hostname or ""
    except ValueError:
        host = ""
    host = _UNSAFE.sub("-", host.lower()).strip("-.")[:80]
    return host or "unknown-host"


def path_for(job_id: str, url: str, *, root: Path | None = None, day: str | None = None) -> Path:
    """`<root>/<YYYY-MM-DD>-<job id>-<host>.log`; the id is kept to hex so the name parses back."""
    safe_id = re.sub(r"[^0-9a-f]", "", str(job_id or "").lower())[:32] or "0"
    return (root or LOGS_DIR) / f"{day or time.strftime('%Y-%m-%d')}-{safe_id}-{host_of(url)}.log"


class Sink:
    """
    Writes the lines of one job's log that haven't been written yet. Its
    own cursor, apart from the terminal's `printed`, so a line printed
    once is also written once, across a pause and a resume.
    """

    def __init__(self, job_id: str, url: str, *, root: Path | None = None):
        self.path = path_for(job_id, url, root=root)
        self.written = 0

    def write(self, out) -> None:
        """Append `out.log[self.written:]`, one line each, verbatim."""
        lines = list(out.log[self.written:])
        if not lines:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "a", encoding="utf-8", newline="\n") as fh:
            for line in lines:
                fh.write(str(line) + "\n")
        self.written = len(out.log)


def find(job_id: str, *, root: Path | None = None) -> Path | None:
    """The file of one job id, if any (the newest when more than one day holds it)."""
    safe_id = re.sub(r"[^0-9a-f]", "", str(job_id or "").lower())[:32]
    if not safe_id:
        return None
    found = sorted((root or LOGS_DIR).glob(f"????-??-??-{safe_id}-*.log"))
    return found[-1] if found else None


def read(job_id: str, *, root: Path | None = None, limit: int = 2000) -> list[str] | None:
    """The job's lines (the last `limit`), or None when it has no file yet."""
    path = find(job_id, root=root)
    if path is None:
        return None
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None
    return lines[-limit:]


def prune(*, root: Path | None = None, keep_days: int = KEEP_DAYS, today: date | None = None) -> int:
    """Delete log files whose name carries a date older than `keep_days`; others are left. Returns the count."""
    folder = root or LOGS_DIR
    if not folder.is_dir():
        return 0
    cutoff = (today or date.today()) - timedelta(days=keep_days)
    gone = 0
    for path in folder.iterdir():
        m = _NAME.match(path.name)
        if not m or not path.is_file():
            continue
        try:
            when = date.fromisoformat(m.group(1))
        except ValueError:
            continue
        if when < cutoff:
            try:
                path.unlink()
                gone += 1
            except OSError:
                pass
    return gone
