"""
pause.py — stopping for Jack.

WHAT THIS DOES: One pause: a desktop notification (or the terminal bell)
and then either a prompt in the terminal that waits for Jack's answer (or
for the page to show he finished the step), or, in GUI mode, a Deferred
raised to the wizard, which records where it stopped and hands the tab
back to the queue until Jack presses Continue in the GUI. There is no
timeout: a job that needs Jack waits (Jack, 2026-10-19).
WHY IT MATTERS: Every pause the owner decisions list (codes, CAPTCHAs,
gate stops, account walls) goes through here, so the notification and
the no-timeout rule happen the same way each time, and in GUI mode the
browser never closes on a waiting job.
"""

from __future__ import annotations

import time

from . import console as console_mod, notify as notify_mod

# No deadline: a job that needs Jack waits until he acts (Jack, 2026-10-19).
TIMEOUT_S: float | None = None

ANSWERED, FINISHED, TIMED_OUT, NO_TERMINAL = "answered", "finished", "timed_out", "no_terminal"


class Deferred(Exception):
    """
    Raised by a GUI-mode pauser instead of waiting: the wizard catches it,
    keeps its place, and the queue moves on. `info` is what the GUI shows
    (kind, message, wants_code, fields); it never holds a password.
    """

    def __init__(self, title: str, body: str, *, prompt: str = "", hidden: bool = False,
                 wants_code: bool = False, until=None, info: dict | None = None):
        super().__init__(body)
        self.title, self.body, self.prompt = title, body, prompt
        self.hidden, self.wants_code, self.until = hidden, wants_code, until
        self.info = info


class Pauser:
    """Pauses in the terminal, each with a notification and a prompt."""

    deferring = False

    def __init__(self, console: console_mod.Console | None = None, *, notify_fn=None,
                 timeout_s: float | None = TIMEOUT_S, echo=print, clock=time.monotonic):
        self.console = console or console_mod.Console(clock=clock)
        self.notify_fn = notify_fn or notify_mod.notify
        self.timeout_s = timeout_s
        self.echo = echo
        self.clock = clock
        self.log: list[dict] = []           # what each pause was for and how it ended

    def wait(self, title: str, body: str, *, prompt: str, hidden: bool = False,
             until=None, wants_code: bool = False) -> tuple[str | None, str]:
        """
        Notify, prompt, wait. `until`, if given, is polled and ends the wait
        with FINISHED when it returns True (Jack did it in the browser).
        Returns (the line typed or None, how it ended). `wants_code` says
        the answer is a verification code (the GUI shows a box for it).
        """
        self.notify_fn(title, body, echo=self.echo)
        limit = "No time limit." if self.timeout_s is None else f"Up to {int(self.timeout_s // 60)} minutes."
        self.echo(f"{body}\n{limit}")
        answer = self.console.ask(prompt, hidden=hidden, timeout_s=self.timeout_s, poll=until)
        if answer is not None and answer.strip() != "":
            how = ANSWERED
        elif until is not None and _safe(until):
            how = FINISHED
        elif answer is not None:
            how = ANSWERED                 # an empty line: Jack pressed Enter
        elif getattr(self.console, "blocked", False):
            how = NO_TERMINAL              # an earlier prompt still owns the terminal
        else:
            how = TIMED_OUT
        self.log.append({"title": title, "ended": how})
        return (answer if how == ANSWERED else None), how


class GuiPauser(Pauser):
    """
    GUI mode: a pause notifies and raises Deferred; nothing waits here. A
    hidden prompt (a password) is never asked for in this mode: the
    Deferred tells Jack to do that step in the tab himself.
    """

    deferring = True

    def wait(self, title: str, body: str, *, prompt: str, hidden: bool = False,
             until=None, wants_code: bool = False) -> tuple[str | None, str]:
        if hidden:
            body = f"{body} (In the GUI, do this step in the tab yourself, then press Continue.)"
        try:
            self.notify_fn(title, body, echo=self.echo)
        except Exception:
            pass
        self.log.append({"title": title, "ended": "deferred"})
        raise Deferred(title, body, prompt=prompt, hidden=hidden, wants_code=wants_code,
                       until=until)


def _safe(fn) -> bool:
    try:
        return bool(fn())
    except Exception:
        return False
