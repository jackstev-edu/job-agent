"""
outcome.py — what you did after the agent stopped, watched without touching.

WHAT THIS DOES: After a fill, watches the agent's own tab until you press
Enter, the portal's confirmation page appears, the tab closes, or time runs
out. It reads the form at the moment you submit and as you go, works out
which fields you changed, and turns the confirmation page and your answer at
the prompt into y / n / unknown. It never clicks, types or ticks.
WHY IT MATTERS: Shadow mode is only useful if its calls can be scored
against yours. Scoring needs your outcome to be right or honestly unknown:
the detector only suggests, your answer decides, and page text is matched
against fixed patterns and never kept, since a confirmation heading can
carry your name. Password boxes are never read.

A limit worth stating: every read of the form, polled or at submit, runs in
the page's own JavaScript, so a hostile page could feed it false values. The
submit-time read is handed to Python by a hook under a random name, and only
a dict of known refs to short plain values is accepted. That narrows what a
page can claim, but it doesn't make the page trustworthy. Scores are advice
about the gate, never a reason to act.
"""

from __future__ import annotations

import json
import re
import secrets
import time
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from . import extract, learn

if TYPE_CHECKING:
    from playwright.sync_api import Page

# What each portal's "application received" page looks like, as far as is
# known. Every signal listed must hold. These are UNVERIFIED until one of
# your confirmed submissions matches: until then a match is only a
# suggestion, and your answer at the prompt decides.
SUCCESS_SIGNATURES: dict[str, dict] = {
    "greenhouse": {"url": re.compile(r"/confirmation/?$"),
                   "text": re.compile(r"thank you for applying", re.I)},
    "lever": {"url": re.compile(r"/thanks/?$"),
              "text": re.compile(r"application submitted", re.I)},
    "ashby": {"same_host": True,
              "text": re.compile(r"thank you for applying|application (?:was|has been) "
                                 r"(?:successfully )?submitted", re.I)},
}

_PRESENT_JS = r"""
(refs) => refs.some(r => document.querySelector(`[data-ja-ref="${CSS.escape(r)}"]`))
"""
_TEXT_JS = "() => (document.body ? document.body.innerText : '').slice(0, 20000)"

# Installed in the page: on submit or a button press, read the form with the
# same password-safe snapshot and hand the result to Python. It has to be read
# in the page: once the click is under way, Python can't call into the page
# from the binding (the sync API deadlocks), and moments later the form is gone.
_HOOK_JS = ("""
(args) => {
  const [name, refs] = args;
  const snap = """ + extract._SNAPSHOT_JS + """;
  const fire = () => {
    let data = null;
    try { data = snap(refs); } catch (e) {}
    try { window[name](data); } catch (e) {}
  };
  document.addEventListener('submit', fire, true);
  document.addEventListener('pointerdown', e => {
    if (e.target.closest && e.target.closest(
        'button, input[type=submit], input[type=button], [role=button]')) fire();
  }, true);
}
""")

_MAX_VALUE = 5000
_MAX_FILES, _MAX_FILE_NAME = 50, 1000


def _accept(raw, refs: set[str]) -> dict | None:
    """
    The hook's read, if it's shaped like ours: known refs, short plain values,
    and for a file box a short list of short file names.
    """
    if not isinstance(raw, dict) or not raw or not set(raw) <= refs:
        return None
    for v in raw.values():
        if isinstance(v, list):
            if len(v) > _MAX_FILES or not all(
                    isinstance(n, str) and len(n) <= _MAX_FILE_NAME for n in v):
                return None
            continue
        if v is not None and not isinstance(v, (str, bool)):
            return None
        if isinstance(v, str) and len(v) > _MAX_VALUE:
            return None
    return raw


def url_shape(url: str) -> str:
    """Host plus path, every run of digits replaced. No query, no ids."""
    parsed = urlparse(url or "")
    return f"{(parsed.hostname or '').lower()}{re.sub(r'[0-9]+', '<n>', parsed.path or '')}"


def detect_success(page: "Page", *, portal: str, portal_method: str, start_url: str,
                   refs: list[str]) -> dict:
    """
    Does the tab show the portal's confirmation page? Returns {"result":
    "match" | "partial" | "none" | "unsupported", "signals", "url_shape"}.
    Only host-detected portals with a known signature can ever match.
    """
    sig = SUCCESS_SIGNATURES.get(portal)
    if not sig or portal_method != "host":
        return {"result": "unsupported", "signals": {}, "url_shape": ""}
    try:
        url = page.url
        gone = not page.evaluate(_PRESENT_JS, refs)
        text = page.evaluate(_TEXT_JS)
    except Exception:
        return {"result": "none", "signals": {"readable": False}, "url_shape": ""}
    signals = {"form_gone": gone, "text": bool(sig["text"].search(text or ""))}
    if "url" in sig:
        signals["url"] = bool(sig["url"].search(urlparse(url).path or ""))
    if sig.get("same_host"):
        signals["same_host"] = ((urlparse(url).hostname or "")
                                == (urlparse(start_url).hostname or ""))
    if all(signals.values()):
        result = "match"
    elif any(v for k, v in signals.items() if k != "same_host"):
        result = "partial"
    else:
        result = "none"
    return {"result": result, "signals": signals, "url_shape": url_shape(url)}


def watch(page: "Page", *, record: dict, stop_requested, poll_ms: int = 500,
          timeout_s: float = 1800, clock=time.monotonic) -> dict:
    """
    Watch the agent's own tab until you press Enter (`stop_requested`), the
    confirmation page appears, the tab closes, or `timeout_s` passes. Keeps
    the last read of the form taken while its fields were still on the page.
    A read made at the moment of submitting is "exact"; one from polling is
    "polled".
    """
    fields = record.get("fields") or []
    refs = extract.readable_refs(fields)
    portal, method = record.get("portal") or "", record.get("portal_method") or ""
    start_url = record.get("final_url") or record.get("url") or ""
    state = {"final": None, "capture": "none"}
    allowed = set(refs)

    def keep(snap: dict, kind: str) -> None:
        # A poll just after the submit-time read sees the same form while the
        # request is in flight. That doesn't make the capture any less exact.
        if kind == "polled" and state["capture"] == "exact" and snap == state["final"]:
            return
        state["final"], state["capture"] = snap, kind

    def poll() -> None:
        try:
            if not page.evaluate(_PRESENT_JS, refs):
                return
            keep(extract.snapshot(page, fields), "polled")
        except Exception:
            return

    def on_submit(source, raw=None) -> None:
        # Runs inside Playwright's dispatch: no calls back into the page here.
        accepted = _accept(raw, allowed)
        if accepted is not None:
            keep(extract.snapshot_from_raw(fields, accepted), "exact")

    hooked = False
    try:
        name = f"__ja_{secrets.token_hex(8)}"
        page.expose_binding(name, on_submit)
        page.evaluate(_HOOK_JS, [name, refs])
        hooked = True
    except Exception:
        pass

    started = clock()
    detection = {"result": "none", "signals": {}, "url_shape": ""}
    while True:
        if page.is_closed():
            ended = "tab_closed"
            break
        detection = detect_success(page, portal=portal, portal_method=method,
                                   start_url=start_url, refs=refs)
        if detection["result"] == "match":
            ended = "success"
            break
        poll()
        if stop_requested():
            ended = "enter"
            break
        if clock() - started > timeout_s:
            ended = "timeout"
            break
        try:
            page.wait_for_timeout(poll_ms)      # the hook's calls arrive while waiting
        except Exception:
            ended = "tab_closed"
            break
    return {"ended": ended, "detection": detection, "final": state["final"],
            "capture": state["capture"], "hooked": hooked}


# The uploaded box's ref after a rescan. Every scan restamps refs, and one
# that skips a box leaves it holding a stale ref another element may now
# carry, so the ref only counts if the box is still in the page and is the
# one element holding it.
_UPLOAD_REF_JS = r"""
(el) => {
  if (!el.isConnected) return null;
  const r = el.getAttribute('data-ja-ref');
  if (!r) return null;
  return document.querySelectorAll(`[data-ja-ref="${CSS.escape(r)}"]`).length === 1 ? r : null;
}
"""


def upload_ref(handle, fields: list[dict]) -> str | None:
    """
    The ref the rescan gave the file box the agent uploaded to, or None if
    it can't be pinned down (then the upload baseline is unknown).
    """
    if handle is None:
        return None
    try:
        ref = handle.evaluate(_UPLOAD_REF_JS)
    except Exception:
        return None
    return ref if ref in {f.get("ref") for f in fields if f.get("type") == "file"} else None


def _empty(value) -> bool:
    return value is None or value is False or not str(value).strip()


def compute_edits(record: dict, result: dict, final: dict | None) -> list[dict] | None:
    """
    Which fields you changed from what the agent left, by label and kind,
    never values. None if the form was never read. A field that can't be
    read is "unreadable", so zero edits can't be claimed for it.

    Baselines: what the agent typed or picked; for an allowlisted consent box,
    ticked if the gate planned would_tick and unticked for leave. So ticking a
    would_tick box is not an edit, leaving it unticked is, and ticking a
    leave box is. For a file box, the names of the files the agent uploaded
    (record["uploads"], none for a box it didn't upload to): different names
    or a different count is an edit. Only names are compared, never contents.
    Without that baseline a file box is unreadable.
    """
    if final is None:
        return None
    plan = record.get("plan") or {}
    consent = result.get("consent_actions") or {}
    uploads = record.get("uploads")
    edits = []
    for f in record.get("fields") or []:
        ref, ftype = f.get("ref"), (f.get("type") or "").lower()
        if ftype == "password":
            continue
        label = (f.get("label") or f.get("name") or "(unlabelled)")[:120]
        if extract.is_secret(f):
            # Never read, so never diffed: by label and kind only, and zero
            # edits can't be claimed while it's on the form.
            edits.append({"ref": ref, "label": label, "kind": "unreadable"})
            continue
        if ftype == "file":
            kind = _file_edit(None if uploads is None else uploads.get(ref, []),
                              final.get(ref))
            if kind:
                edits.append({"ref": ref, "label": label, "kind": kind})
            continue
        baseline = (consent[ref] == "would_tick") if ref in consent \
            else (plan.get(ref) or {}).get("value")
        if ref not in final:
            edits.append({"ref": ref, "label": label, "kind": "unreadable"})
            continue
        now = final[ref]
        if ftype == "checkbox":
            if bool(baseline) != bool(now):
                edits.append({"ref": ref, "label": label,
                              "kind": "ticked" if now else "unticked"})
            continue
        if _empty(baseline) and _empty(now):
            continue
        if learn.unreadable(baseline, now, ftype):
            edits.append({"ref": ref, "label": label, "kind": "unreadable"})
        elif not learn.same_answer(baseline, now, ftype):
            kind = ("filled_blank" if _empty(baseline) else
                    "cleared" if _empty(now) else "changed")
            edits.append({"ref": ref, "label": label, "kind": kind})
    return edits


def _file_edit(uploaded: list | None, now) -> str | None:
    """How a file box changed from what the agent uploaded: by names and count."""
    if not isinstance(uploaded, list) or not isinstance(now, list):
        return "unreadable"
    if sorted(map(str, uploaded)) == sorted(map(str, now)):
        return None
    if not now:
        return "file_removed"
    return "file_added" if not uploaded else "file_replaced"


def human_submitted(*, detected: str, answer: str | None, signature_verified: bool) -> str:
    """
    Your answer decides; the detector only suggests. They disagree when the
    confirmation page was seen and you said no, and that's "unknown". So is
    no answer with a signature not yet confirmed. Never a guess.
    """
    if answer == "y":
        return "y"
    if answer == "n":
        return "unknown" if detected == "match" else "n"
    if detected == "match" and signature_verified:
        return "y"
    return "unknown"


def signature_verified(portal: str, past_runs: list[dict]) -> bool:
    """A portal's signature is trusted once it has matched a submission you confirmed."""
    for run in past_runs:
        if run.get("portal") != portal:
            continue
        try:
            sig = json.loads(run.get("success_signal") or "{}")
        except ValueError:
            continue
        if sig.get("detected") == "match" and sig.get("answer") == "y":
            return True
    return False


def success_signal(watched: dict, answer: str | None) -> str:
    """What's stored about the confirmation page: which signals held, the URL shape, no text."""
    det = watched.get("detection") or {}
    return json.dumps({"ended": watched.get("ended"), "detected": det.get("result"),
                       "signals": det.get("signals"), "url_shape": det.get("url_shape"),
                       "answer": answer, "hooked": watched.get("hooked")})
