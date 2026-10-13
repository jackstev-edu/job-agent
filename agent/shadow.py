"""
shadow.py — what the submit gate is shown, and (later) what you then did.

WHAT THIS DOES: After a fill, gathers one record of the run: every field the
agent scanned, how each was answered, what it typed, the state of every
checkbox, the page's iframes, and what happened along the way (a CAPTCHA, a
login wall, extra wizard steps). Reading the page is passive: nothing here
clicks, types or ticks.
WHY IT MATTERS: The gate in gate.py is a pure function of this record, so
the record has to be complete and honest. Anything that couldn't be read is
written down as an error, and the gate stops on errors.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from urllib.parse import urlparse

from . import extract

if TYPE_CHECKING:
    from playwright.sync_api import Page

RECORD_VERSION = 1

# The fields of a scanned field worth keeping in the record.
_FIELD_KEYS = ("ref", "type", "widget", "label", "name", "required", "options",
               "option_refs", "help_text", "search_required")

# The parts of a plan decision the gate reads.
_DECISION_KEYS = ("value", "source", "profile_path", "fit", "confidence", "needs_review",
                  "review_reasons", "draft", "note", "category")


def step_entry(fields: list[dict], categories: dict, plan: dict, flags: list[dict],
               rows: list[dict] | None) -> dict:
    """
    One wizard step's worth of the run. `rows` is filler.run_plan's report, or
    None on a dry run. run_plan writes one row per plan entry whose field it
    found, in plan order, and carries no ref, so the refs are matched back
    here in that same order.
    """
    by_ref = {f["ref"]: f for f in fields}
    results = None
    if rows is not None:
        refs = [ref for ref in plan if ref in by_ref]
        results = {ref: {"filled": bool(r.get("filled")), "detail": r.get("detail", "")}
                   for ref, r in zip(refs, rows)}
    return {
        "fields": [{k: f.get(k) for k in _FIELD_KEYS if k in f} for f in fields],
        "categories": dict(categories),
        "plan": {ref: {k: d.get(k) for k in _DECISION_KEYS if k in d} for ref, d in plan.items()},
        "flags": [{"ref": fl.get("ref"), "label": fl.get("label"), "category": fl.get("category"),
                   "reason": fl.get("reason")} for fl in flags],
        "results": results,
    }


def build_record(page: "Page", *, url: str, portal: str, portal_method: str,
                 company: str, steps: list[dict], events: list[dict],
                 dry_run: bool) -> dict:
    """
    Merge the steps into one record and read the page's final state.

    Never raises: a part that can't be read is left empty and named in
    "errors", which the gate treats as a reason to stop.
    """
    errors: list[str] = []
    fields: list[dict] = []
    categories: dict[str, str] = {}
    plan: dict[str, dict] = {}
    flags: list[dict] = []
    results: dict[str, dict] | None = None if dry_run else {}
    for step in steps:
        fields += step["fields"]
        categories.update(step["categories"])
        plan.update(step["plan"])
        flags += step["flags"]
        if results is not None:
            results.update(step["results"] or {})

    def read(name, fn, default):
        try:
            return fn()
        except Exception as e:
            errors.append(f"couldn't read {name}: {type(e).__name__}")
            return default

    census = read("the checkboxes", lambda: extract.checkbox_census(page), [])
    frames = read("the iframes", lambda: extract.frame_inventory(page), [])
    snapshot = read("the form's values",
                    lambda: extract.snapshot(page, fields), {})
    final_url = read("the page address", lambda: page.url, "")

    events = list(events)
    start_host = (urlparse(url).hostname or "").lower()
    end_host = (urlparse(final_url).hostname or "").lower() if final_url else ""
    if end_host and start_host and end_host != start_host:
        events.append({"kind": "host_changed", "detail": end_host})

    return {
        "record_version": RECORD_VERSION,
        "url": url,
        "final_url": final_url,
        "portal": portal,
        "portal_method": portal_method,
        "company": company,
        "dry_run": dry_run,
        "steps": len(steps),
        "fields": fields,
        "categories": categories,
        "plan": plan,
        "flags": flags,
        "results": results,
        "snapshot": snapshot,
        "census": census,
        "frames": frames,
        "events": events,
        "errors": errors,
    }


def failed_record(url: str, portal: str, reason: str) -> dict:
    """What the gate sees when the record itself couldn't be built."""
    return {"record_version": RECORD_VERSION, "url": url, "portal": portal,
            "errors": [reason]}

