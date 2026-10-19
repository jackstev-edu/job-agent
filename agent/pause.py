"""
pause.py — stopping for Jack, for up to fifteen minutes.

WHAT THIS DOES: One pause: a desktop notification (or the terminal bell),
a prompt in the terminal, and a wait that ends when Jack answers, when
the page shows he finished the step in the browser, or when the time runs
out. Returns his answer (handed over once, kept nowhere) and how the
pause ended.
WHY IT MATTERS: Every pause the owner decisions list (codes, CAPTCHAs,
resets, gate stops) goes through here, so the fifteen-minute rule and the
notification happen the same way each time, and a timeout parks the job
rather than hanging the run.
"""

from __future__ import annotations

import time

from . import console as console_mod, notify as notify_mod

TIMEOUT_S = 15 * 60

ANSWERED, FINISHED, TIMED_OUT, NO_TERMINAL = "answered", "finished", "timed_out", "no_terminal"


class Pauser:
    """Pauses, each with a notification, a prompt and a deadline."""

    def __init__(self, console: console_mod.Console | None = None, *, notify_fn=None,
                 timeout_s: float = TIMEOUT_S, echo=print, clock=time.monotonic):
        self.console = console or console_mod.Console(clock=clock)
        self.notify_fn = notify_fn or notify_mod.notify
        self.timeout_s = timeout_s
        self.echo = echo
        self.clock = clock
        self.log: list[dict] = []           # what each pause was for and how it ended

    def wait(self, title: str, body: str, *, prompt: str, hidden: bool = False,
             until=None) -> tuple[str | None, str]:
        """
        Notify, prompt, wait. `until`, if given, is polled and ends the wait
        with FINISHED when it returns True (Jack did it in the browser).
        Returns (the line typed or None, how it ended).
        """
        self.notify_fn(title, body, echo=self.echo)
        self.echo(f"{body}\nUp to {int(self.timeout_s // 60)} minutes.")
        answer = self.console.ask(prompt, hidden=hidden, timeout_s=self.timeout_s, poll=until)
        if answer is not None and answer.strip() != "":
            how = ANSWERED
        elif until is not None and _safe(until):
            how = FINISHED
        elif answer is not None:
            how = ANSWERED                 # an empty line: Jack pressed Enter
        else:
            how = TIMED_OUT
        self.log.append({"title": title, "ended": how})
        return (answer if how == ANSWERED else None), how


def _safe(fn) -> bool:
    try:
        return bool(fn())
    except Exception:
        return False
