"""
test_console_notify.py — the terminal reader with timeouts, and the notifier.

WHAT THIS DOES: Drives agent.console with stand-ins for input and getpass
that answer on cue or never: an answer arrives, a hidden answer is handed
out once and then forgotten, a timeout returns None and the late line is
thrown away before the next read, a poll ends the wait early, and the
hidden prompt never goes through `input`. Drives agent.notify with a
desktop stand-in that succeeds or fails: the fallback rings the bell and
prints; the title and body are XML-escaped for the toast.
WHY IT MATTERS: An unattended run must never block on a prompt forever,
and a pasted password must reach exactly one caller.

Run from anywhere:  .venv/Scripts/python.exe tests/test_console_notify.py
"""
import threading
from unittest import mock

from _harness import Checks, run_as_script
from agent import console, notify


class _Terminal:
    """Stands in for the terminal: lines are released one at a time by the test."""

    def __init__(self):
        self.gate = threading.Semaphore(0)
        self.lines: list[str] = []
        self.calls: list[tuple[str, str]] = []

    def feed(self, line: str):
        self.lines.append(line)
        self.gate.release()

    def input(self, prompt=""):
        self.calls.append(("input", prompt))
        self.gate.acquire()
        return self.lines.pop(0)

    def getpass(self, prompt=""):
        self.calls.append(("getpass", prompt))
        self.gate.acquire()
        return self.lines.pop(0)


def test_answers_timeouts_and_draining():
    check = Checks()
    term = _Terminal()
    with mock.patch("builtins.input", term.input), mock.patch.object(console.getpass, "getpass", term.getpass):
        c = console.Console()
        # An answer that comes in time.
        threading.Timer(0.2, term.feed, args=["yes\n"]).start()
        check(c.ask("Continue? ", timeout_s=5) == "yes", "a line typed in time comes back, stripped")
        # A hidden answer, handed out once.
        threading.Timer(0.2, term.feed, args=["SENTINEL-PW-1"]).start()
        req = c.start("Password: ", hidden=True)
        req.wait(5)
        check(req.take() == "SENTINEL-PW-1" and req.take() is None and req.value is None,
              "a hidden answer is handed out once, then forgotten")
        check(term.calls[-1][0] == "getpass" and not any(
            kind == "input" and "Password" in p for kind, p in term.calls),
              "the hidden prompt went through getpass, never input")
        # A timeout: None, and the late line is thrown away before the next read.
        check(c.ask("Code: ", timeout_s=0.3) is None, "no line in time -> None")
        term.feed("late-code")                       # the old read takes this
        threading.Timer(0.3, term.feed, args=["fresh"]).start()
        check(c.ask("Again: ", timeout_s=5, poll_every_s=0.05) == "fresh",
              "the late answer to the timed-out read was drained, the new one returned")
        # A poll that says "done in the browser" ends the wait early.
        ticks = {"n": 0}

        def finished():
            ticks["n"] += 1
            return ticks["n"] >= 2
        got = c.ask("Paste or finish in the browser: ", timeout_s=10, poll=finished, poll_every_s=0.05)
        check(got is None and ticks["n"] >= 2, "a poll returning True ends the wait with None")
        term.feed("unblock")                         # release that read's thread
    check.done()


def test_notify_falls_back_to_the_terminal():
    check = Checks()
    printed = []
    echo = lambda *a, **k: printed.append(" ".join(str(x) for x in a))
    how = notify.notify("Needs you", "code <sent> & waiting", echo=echo,
                        desktop_fn=lambda t, b: True)
    check(how == "desktop" and printed and "\a" not in printed[0],
          "with a desktop toast, no bell")
    printed.clear()
    how = notify.notify("Needs you", "code", echo=echo, desktop_fn=lambda t, b: False)
    check(how == "terminal" and printed[0] == "\a" and "Needs you: code" in printed[-1],
          f"without one, the bell and a printed line ({printed})")
    check(notify._ps_quote("a<b>&'c'") == "a&lt;b&gt;&amp;''c''",
          "title and body are XML-escaped and single-quote-safe for PowerShell")
    check.done()


if __name__ == "__main__":
    run_as_script(globals())
