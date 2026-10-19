"""
notify.py — telling Jack the agent needs him.

WHAT THIS DOES: Shows a Windows desktop notification (a toast, through
PowerShell's WinRT bindings) with a short title and body, and falls back to
a terminal bell and a printed line when that can't be done. Nothing here
reads a page or stores anything.
WHY IT MATTERS: An unattended run pauses for verification codes, CAPTCHAs
and gate stops; if Jack isn't told, the 15-minute wait just runs out and
the job is parked.
"""

from __future__ import annotations

import subprocess
import sys
from xml.sax.saxutils import escape

# PowerShell's own AppUserModelId, so the toast shows without registering one.
_APP_ID = r"{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe"

_TOAST_PS = r"""
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null
$xml = New-Object Windows.Data.Xml.Dom.XmlDocument
$xml.LoadXml('<toast duration="long"><visual><binding template="ToastGeneric"><text>%s</text><text>%s</text></binding></visual></toast>')
$toast = New-Object Windows.UI.Notifications.ToastNotification $xml
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('%s').Show($toast)
"""


def _ps_quote(text: str) -> str:
    """XML-escaped, then safe inside a single-quoted PowerShell string."""
    return escape(text).replace("'", "''")


def desktop(title: str, body: str, timeout_s: float = 10) -> bool:
    """Show a toast. True if PowerShell ran it without error; never raises."""
    if sys.platform != "win32":
        return False
    script = _TOAST_PS % (_ps_quote(title[:120]), _ps_quote(body[:400]), _ps_quote(_APP_ID))
    try:
        done = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, timeout=timeout_s, check=False)
        return done.returncode == 0
    except Exception:
        return False


def notify(title: str, body: str, *, echo=print, desktop_fn=desktop) -> str:
    """
    Tell Jack: a desktop notification, or a bell and a printed line when
    that fails. Returns "desktop" or "terminal", for the log. The printed
    line always goes out too, so the terminal shows what the toast said.
    """
    how = "desktop" if desktop_fn(title, body) else "terminal"
    if how == "terminal":
        echo("\a", end="")
    echo(f"\n>>> {title}: {body}")
    return how
