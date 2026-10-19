"""
console.py — one owner for the terminal's input, with timeouts.

WHAT THIS DOES: Reads a line from the terminal on a background thread, in
the open (`input`) or hidden (`getpass`), so the main thread can keep
polling the browser and give up after a deadline. One request is pending
at a time; a request that timed out is drained (its late answer thrown
away) before the next one starts.
WHY IT MATTERS: Playwright's sync API has to stay on the main thread, and
an unattended run can't block forever on a prompt. A hidden answer (a
password Jack set) is handed to exactly one caller and kept nowhere else.
"""

from __future__ import annotations

import getpass
import threading
import time


class Request:
    """One pending read. `value` is the line, or None if it hasn't come."""

    def __init__(self, prompt: str, hidden: bool):
        self.prompt, self.hidden = prompt, hidden
        self.value: str | None = None
        self.error: str | None = None
        self._done = threading.Event()
        self._thread = threading.Thread(target=self._read, daemon=True)

    def _read(self) -> None:
        try:
            line = getpass.getpass(self.prompt) if self.hidden else input(self.prompt)
            self.value = line.rstrip("\r\n")
        except (EOFError, KeyboardInterrupt) as e:
            self.error = type(e).__name__
        except Exception as e:
            self.error = type(e).__name__
        finally:
            self._done.set()

    def start(self) -> "Request":
        self._thread.start()
        return self

    def done(self) -> bool:
        return self._done.is_set()

    def wait(self, timeout_s: float | None) -> bool:
        return self._done.wait(timeout_s)

    def take(self) -> str | None:
        """The answer, handed out once: this object forgets it."""
        value, self.value = self.value, None
        return value


class Console:
    """The terminal's input, one request at a time."""

    def __init__(self, clock=time.monotonic):
        self._pending: Request | None = None
        self._clock = clock

    def start(self, prompt: str, hidden: bool = False, drain_s: float = 0.0) -> Request | None:
        """
        Begin a read. If an earlier read is still waiting for its line,
        wait up to `drain_s` for it and throw that line away; if it still
        hasn't come, nothing can be read now and None comes back.
        """
        if self._pending is not None and not self._pending.done():
            self._pending.wait(drain_s)
            if not self._pending.done():
                return None
        if self._pending is not None:
            self._pending.take()                 # a late answer is never used
        self._pending = Request(prompt, hidden).start()
        return self._pending

    def ask(self, prompt: str, hidden: bool = False, timeout_s: float | None = None,
            poll=None, poll_every_s: float = 0.5) -> str | None:
        """
        Read one line, hidden or not, waiting up to `timeout_s`. `poll`, if
        given, is called between waits and ends the wait early when it
        returns True (Jack finished the step in the browser instead).
        Returns the line, or None on timeout, on an early end, or when the
        terminal can't be read.
        """
        req = self.start(prompt, hidden)
        if req is None:
            return None
        deadline = None if timeout_s is None else self._clock() + timeout_s
        while True:
            step = poll_every_s if deadline is None else max(0.0, min(poll_every_s, deadline - self._clock()))
            if req.wait(step):
                return req.take()
            if poll is not None:
                try:
                    if poll():
                        return None
                except Exception:
                    pass
            if deadline is not None and self._clock() >= deadline:
                return None
