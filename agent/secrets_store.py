"""
secrets_store.py — the one place a portal password is stored and read back.

WHAT THIS DOES: Keeps the password the agent generated for a portal
account, or the one Jack pasted after a reset, in Windows Credential
Manager through the keyring library, keyed by the portal's host
(tenant); hands it back for one typed sign-in on that host; forgets one
or all. An index of the hosts (no values) sits in the same vault so
`apply.py passwords` can list them. WHY IT MATTERS: Jack's decision of
2026-10-20: a password may live in Credential Manager and nowhere else
(never a file, the repo, ~/.job-agent, a log, a report, the DB,
gate_runs, the GUI or any model input). accounts.py is the only caller
that types one; nothing reads a password from a page.
"""

from __future__ import annotations

import json

import keyring
import keyring.errors

SERVICE = "job-agent"          # the Credential Manager entry: job-agent / <host>
_INDEX = "__hosts__"           # the entry that lists the hosts (no values)


def _host(host: str) -> str:
    host = str(host or "").strip().lower().rstrip(".")
    if not host or "/" in host or " " in host or host == _INDEX:
        raise ValueError("a password is keyed by a bare host name")
    return host


def hosts() -> list[str]:
    """The hosts with a stored password, from the index; never a value."""
    try:
        raw = keyring.get_password(SERVICE, _INDEX)
    except Exception:
        return []
    try:
        got = json.loads(raw or "[]")
    except ValueError:
        return []
    return sorted({str(h) for h in got if isinstance(h, str) and h}) if isinstance(got, list) else []


def _set_index(names: list[str]) -> None:
    if names:
        keyring.set_password(SERVICE, _INDEX, json.dumps(sorted(set(names))))
    else:
        try:
            keyring.delete_password(SERVICE, _INDEX)
        except keyring.errors.PasswordDeleteError:
            pass


def save(host: str, password: str) -> None:
    """Store (or replace) the password for this host."""
    host = _host(host)
    if not isinstance(password, str) or not password:
        raise ValueError("no password to store")
    keyring.set_password(SERVICE, host, password)
    _set_index(hosts() + [host])


def get(host: str) -> str | None:
    """The stored password for this host, or None. Read for one typed sign-in only."""
    try:
        return keyring.get_password(SERVICE, _host(host))
    except Exception:
        return None


def forget(host: str) -> bool:
    """Delete this host's password. True if there was one."""
    host = _host(host)
    had = get(host) is not None
    try:
        keyring.delete_password(SERVICE, host)
    except keyring.errors.PasswordDeleteError:
        pass
    _set_index([h for h in hosts() if h != host])
    return had


def forget_all() -> int:
    """Delete every stored password. Returns how many hosts were forgotten."""
    names = hosts()
    for host in names:
        forget(host)
    _set_index([])
    return len(names)


def backend_name() -> str:
    """The keyring backend in use, for `doctor` and the passwords listing."""
    try:
        k = keyring.get_keyring()
        return f"{type(k).__module__}.{type(k).__name__}"
    except Exception as e:
        return f"unavailable ({type(e).__name__})"
