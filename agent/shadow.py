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

from . import extract, sensitive

if TYPE_CHECKING:
    from playwright.sync_api import Page

RECORD_VERSION = 1

# The fields of a scanned field worth keeping in the record.
_FIELD_KEYS = ("ref", "type", "widget", "label", "name", "placeholder", "required",
               "options", "option_refs", "help_text", "search_required", "secret",
               "stable_identity", "credential_step", "sigs")

# The parts of a plan decision the gate reads.
_DECISION_KEYS = ("value", "source", "profile_path", "fit", "confidence", "needs_review",
                  "review_reasons", "draft", "note", "category")


def step_entry(fields: list[dict], categories: dict, plan: dict, flags: list[dict],
               rows: list[dict] | None, prefill_census: list[dict] | None = None) -> dict:
    """
    One wizard step's worth of the run. `rows` is filler.run_plan's report, or
    None on a dry run. run_plan writes one row per plan entry whose field it
    found, in plan order, and carries no ref, so the refs are matched back
    here in that same order. `prefill_census` is the checkbox census read
    just before the agent filled this step, or None if it couldn't be read.
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
        "prefill_census": prefill_census,
    }


def build_record(page: "Page", *, url: str, portal: str, portal_method: str,
                 company: str, steps: list[dict], events: list[dict],
                 dry_run: bool, uploads: dict[str, list[str]] | None = None) -> dict:
    """
    Merge the steps into one record and read the page's final state. The
    pre-fill census kept is the last step's: its refs come from the same
    scan as the census read here, so the two can be compared box by box.

    `uploads` is {file box ref: [names of the files the agent put there]},
    the baseline for spotting a file you swapped; None if that isn't known.
    It stays in memory for the outcome diff and is never stored.

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
    # Invisible frames (zero-size, display:none or visibility:hidden on the
    # frame element) can't show you anything, so they don't stop the run, but
    # each one is logged, by host only.
    for fr in frames:
        if not fr.get("visible"):
            events.append({"kind": "hidden_iframe", "detail": _host(fr.get("src"))})
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
        "uploads": uploads,
        "prefill_census": steps[-1].get("prefill_census") if steps else None,
    }


def _host(src: str | None) -> str:
    return (urlparse(src or "").hostname or "(no host)").lower()


def failed_record(url: str, portal: str, reason: str) -> dict:
    """What the gate sees when the record itself couldn't be built."""
    return {"record_version": RECORD_VERSION, "url": url, "portal": portal,
            "errors": [reason]}



_SENSITIVE_PREFIXES = ("eligibility.", "eeo.", "compensation.")


def _redacted_box(box: dict, traced: dict) -> dict:
    out = {k: box.get(k) for k in ("checked", "visible", "label_source", "label_inner",
                                   "in_button_group", "group_ref")}
    out["hidden_text"] = box.get("label_inner") != box.get("label_content")
    out["fine_print"] = bool(box.get("described"))
    # A checkbox that IS an EEO or eligibility question ("I identify as a
    # protected veteran") answers it by being ticked, so its state is an
    # answer value and isn't kept.
    label = f"{box.get('label_inner') or ''} {box.get('label_content') or ''}"
    if box.get("ref") in traced or box.get("group_ref") in traced or any(
            rx.search(label) for rx, path in sensitive._PATHS
            if path.startswith(_SENSITIVE_PREFIXES)):
        out.pop("checked")
        out["checked_withheld"] = True
    return out


def redacted(record: dict, result: dict) -> dict:
    """
    The record as stored in gate_runs.record_json: enough to explain a verdict
    later, and no answer values for any field. Option lists, typed values, the
    read-back and flag texts are all left out, because for an EEO or
    eligibility question any of them can spell out your answer. Sensitive
    fields keep only field, profile line, source and fit kind.
    """
    traced = {t.get("ref"): t for t in result.get("trace") or []}
    consent = result.get("consent_actions") or {}
    plan = record.get("plan") or {}
    results = record.get("results")
    categories = record.get("categories") or {}
    fields = []
    for f in record.get("fields") or []:
        ref = f.get("ref")
        if extract.is_secret(f):
            # A secret box appears by label and kind only, never anything
            # about what's in it.
            fields.append({"ref": ref, "label": f.get("label"), "type": f.get("type"),
                           "secret": True})
            continue
        if ref in traced:
            t = traced[ref]
            keys = ("ref", "label", "category", "profile_path", "source", "fit")
            if "allowlist_index" in t:
                keys += ("allowlist_index",)
            fields.append({k: t.get(k) for k in keys})
            if ref in consent:
                fields[-1]["consent_action"] = consent[ref]
            continue
        d = plan.get(ref) or {}
        fields.append({
            "ref": ref, "label": f.get("label"), "type": f.get("type"),
            "required": f.get("required"), "category": categories.get(ref),
            "source": d.get("source"), "confidence": d.get("confidence"),
            "review_reasons": d.get("review_reasons"), "draft": d.get("draft"),
            "answered": d.get("value") is not None if d else False,
            "filled": None if results is None else bool((results.get(ref) or {}).get("filled")),
        })
        if ref in consent:
            fields[-1]["consent_action"] = consent[ref]
    return {
        "record_version": record.get("record_version"),
        "url": record.get("url"), "final_url": record.get("final_url"),
        "portal": record.get("portal"), "portal_method": record.get("portal_method"),
        "dry_run": record.get("dry_run"), "steps": record.get("steps"),
        "fields": fields,
        "flagged": [fl.get("ref") for fl in record.get("flags") or []],
        "census": [_redacted_box(b, traced) for b in record.get("census") or []],
        # The pre-fill state of button-pair boxes only, which is what the
        # gate's pair exemption compared. Withheld the same way as above.
        "prefill_census": None if not isinstance(record.get("prefill_census"), list) else
        [_redacted_box(b, traced) for b in record["prefill_census"]
         if isinstance(b, dict) and (b.get("in_button_group") or b.get("group_ref"))],
        # Hosts only: a frame's full address can carry tokens.
        "frames": [{"host": _host(fr.get("src")), "visible": fr.get("visible")}
                   for fr in record.get("frames") or []],
        "events": record.get("events"),
        "errors": record.get("errors"),
    }
