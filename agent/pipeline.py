"""
pipeline.py — one page's fill, from scan to read-back, shared by every mode.

WHAT THIS DOES: Holds the policy pieces a normal fill, follow mode and the
unattended wizard all run on a page: routing each field to the profile, the
model or Jack; asking the mapper; the re-classification after a resume
upload; the plan the filler may act on; the pre-fill census; the record for
the gate; and `fill_page`, which runs the whole sequence on the page it is
given and returns what the gate needs to judge it.
WHY IT MATTERS: The rules that keep a legal question away from the model
and a password away from everything live in sensitive.py and filler.py;
this file is where they are applied in order, once, so three modes can't
drift apart.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field as dataclass_field
from pathlib import Path
from typing import TYPE_CHECKING

from . import (adapters, browser as browser_mod, classify, extract, filler, mapper, outcome,
               sensitive, shadow)

if TYPE_CHECKING:
    from playwright.sync_api import Page

# Why follow mode left a field out of every model input (FieldLedger.left_out).
LEFT_OUT_REASON = {
    "value": "already holds a value the agent didn't put there; check it",
    "touched": "you've typed in, changed or cleared this field (or one like it), "
               "so it stays yours; check it",
    "while_filling": "something was typed here while I was filling (my own attempt at "
                     "it, or you), so it stays yours; check it",
}

# Why a field whose help text the classifier wasn't shown comes to you.
HELD_AFTER_UPLOAD = ("Its help text changed or appeared after the resume upload (help "
                     "text held back after upload), so the classifier couldn't be shown "
                     "it. Answer it yourself.")
HELD_IN_FOLLOW = ("Follow mode never shows a model help text (help text held back in "
                  "follow mode), so the classifier couldn't be shown it. Answer it "
                  "yourself.")

# A file box the agent may put your resume in: its label or name says resume
# or CV, and not cover letter. Exactly one such box, or none is used.
_RESUME_BOX = re.compile(r"\b(r.sum.|cv|curriculum\s+vitae)\b", re.I)
_COVER = re.compile(r"\bcover\b", re.I)


def resume_box(fields: list[dict]) -> dict | None:
    """The one file box labelled or named as the resume (resume_file, resumeUpload too)."""
    def words(f):
        name = str(f.get("name") or "")
        return f"{f.get('label') or ''} {name} {sensitive._identifier_words(name)}"
    boxes = [f for f in fields if f.get("type") == "file"
             and _RESUME_BOX.search(words(f)) and not _COVER.search(words(f))]
    return boxes[0] if len(boxes) == 1 else None


def route(fields: list[dict], categories: dict, prof, new_after_upload: set[str],
          held_reason: str = HELD_AFTER_UPLOAD) -> tuple[dict, list[dict], list[dict]]:
    """
    The policy for one page's fields: (plan from the profile, flags for you,
    fields for the mapper). Sensitive fields never reach the mapper. Shared
    by every mode, so each routes a field the same way. `new_after_upload`
    holds the fields whose help text the classifier wasn't shown;
    `held_reason` says why, on the review sheet.
    """
    plan: dict[str, dict] = {}
    flags: list[dict] = []
    for_model: list[dict] = []
    for field in fields:
        if field.get("type") == "file":
            continue
        if field.get("submits"):
            # Opening this dropdown means clicking a button that can
            # submit the form, so it's never touched.
            flags.append({"ref": field["ref"], "label": field.get("label"),
                          "options": field.get("options"), "action": "human",
                          "value": None, "source": "human",
                          "category": categories.get(field["ref"], "unknown"),
                          "reason": "This dropdown is a button that could submit "
                                    "the form, so I don't click it. Answer it yourself."})
            continue
        category = categories.get(field["ref"], "unknown")
        if (field["ref"] in new_after_upload and (field.get("help_text") or "").strip()
                and classify.action_for(category) not in ("profile", "human")):
            # Its question may be in help text the classifier wasn't shown.
            flags.append({"ref": field["ref"], "label": field.get("label"),
                          "options": field.get("options"), "action": "human",
                          "value": None, "source": "human", "category": category,
                          "reason": held_reason})
            continue
        verdict = sensitive.decide(
            field, categories.get(field["ref"], "unknown"), prof)
        if verdict["action"] == "human":
            flags.append({**verdict, "ref": field["ref"]})
        elif verdict["action"] == "fill":
            plan[field["ref"]] = verdict
        else:
            field["_draft"] = verdict["action"] == "draft"
            for_model.append(field)
    return plan, flags, for_model


def ask_mapper(for_model: list[dict], prof, posting_text: str,
               new_after_upload: set[str], plan: dict) -> None:
    """The mapper answers only what's left, into `plan`."""
    if not for_model:
        return
    mapped = mapper.map_fields(
        [for_model_copy(f, f["ref"] not in new_after_upload) for f in for_model],
        prof, posting_text[:3000])
    for ref, decision in mapped.items():
        if next((f for f in for_model
                 if f["ref"] == ref and f.get("_draft")), None):
            decision["needs_review"] = True
            decision["draft"] = True
            # The mapper prompt tells the model to flag every
            # draft, so its own flag on one is the draft's flag.
            # Flags raised in code (too long, approximate option,
            # low confidence) keep their own names.
            decision["review_reasons"] = ["draft"] + [
                r for r in decision.get("review_reasons", []) if r != "model"]
        plan[ref] = decision


def for_model_copy(field: dict, send_help: bool) -> dict:
    """A field as a model call is given it: a copy, without its help text unless allowed."""
    return dict(field) if send_help else {**field, "help_text": ""}


def identity(f: dict) -> tuple | None:
    """
    What a field is known by across a rescan: its kind, tag, name attribute
    and explicit label (a real <label> or aria-label). None without a name
    or an explicit label: an id or a nearby-text label isn't an identity.
    """
    name = f.get("dom_name") if "dom_name" in f else (
        f.get("name") if f.get("type") == "radio" else "")
    label = f.get("explicit_label") or ""
    if not (name or label):
        return None
    return (f.get("type"), f.get("tag") or "", name or "", label)


def reclassify_after_upload(before: list[dict], before_categories: dict,
                            after: list[dict]) -> tuple[dict, set[str]]:
    """
    The categories for the post-upload scan, and the refs treated as new.
    A field keeps its pre-upload category only when it's the one field with
    its identity before and after, and its label AND help text are exactly
    the same (compared here, never sent). Every other field is classified
    again with no help text at all.
    """
    def unique(fields):
        seen: dict = {}
        for f in fields:
            key = identity(f)
            if key is not None and f.get("type") != "file":
                seen.setdefault(key, []).append(f)
        return {k: v[0] for k, v in seen.items() if len(v) == 1}
    was, now = unique(before), unique(after)
    categories: dict[str, str] = {}
    new: list[dict] = []
    for f in after:
        if f.get("type") == "file":
            continue
        key = identity(f)
        old = was.get(key) if key in now else None
        if (old is not None and old.get("ref") in before_categories
                and old.get("label") == f.get("label")
                and (old.get("help_text") or "") == (f.get("help_text") or "")):
            categories[f["ref"]] = before_categories[old["ref"]]
        else:
            new.append(f)
    if new:
        categories.update(classify.classify_fields([for_model_copy(f, False) for f in new]))
    return categories, {f["ref"] for f in new}


def fillable_plan(fields: list[dict], plan: dict) -> dict:
    """
    The plan entries the filler may act on. The policy already hands every
    secret box to you; this makes sure no entry for one can reach the
    filler, nor one whose ref isn't from the latest scan. filler.py resolves
    refs without checking their generation, so this is where an old
    generation's ref is treated as missing, never filled. Nor can a dropdown
    that is a submit-capable button (opening it would click it).
    """
    secret_refs = {f["ref"] for f in fields if extract.is_secret(f) or f.get("submits")}
    return {ref: d for ref, d in plan.items()
            if ref not in secret_refs and extract.is_current(ref)}


def prefill_census(page) -> list[dict] | None:
    """The checkbox census before filling; None if unreadable, which the gate never exempts."""
    try:
        return extract.checkbox_census(page)
    except Exception:
        return None


def build_record(page, url, portal, detected_by, company, steps, events, *,
                 dry_run: bool, uploads: dict | None = None) -> dict:
    """The submit gate's record of this run; a failed build is itself a record."""
    try:
        return shadow.build_record(page, url=url, portal=portal, portal_method=detected_by,
                                   company=company, steps=steps, events=events,
                                   dry_run=dry_run, uploads=uploads)
    except Exception as e:
        return shadow.failed_record(url, portal, f"record not built: {type(e).__name__}")


# --- one page, start to finish -----------------------------------------------

@dataclass
class PageFill:
    """What one page's fill produced, for the gate and the review sheet."""
    fields: list[dict]
    categories: dict
    plan: dict
    flags: list[dict]
    rows: list[dict]
    step: dict                                  # shadow.step_entry
    events: list[dict] = dataclass_field(default_factory=list)
    uploaded: dict | None = None                # {file box ref: [names]} or None
    snapshot: dict = dataclass_field(default_factory=dict)   # read-back after filling
    lines: list[str] = dataclass_field(default_factory=list) # what to print


def fill_page(page: "Page", prof, *, guard: extract.ReadGuard, resume: Path | None,
              posting_text: str, portal: str = "generic") -> PageFill | None:
    """
    Fill the page the agent is on: scan, upload the resume into the one box
    labelled as the resume (if `resume` is given), classify, route, map,
    fill, read back. None when the page has no fields. Clicks nothing but
    what the filler clicks (rows, option buttons, checkboxes it is allowed).
    """
    fields = extract.extract_fields(page, guard)
    if not fields:
        return None
    fields = extract.enrich_comboboxes(page, fields)
    events: list[dict] = []
    lines: list[str] = []
    uploaded: dict | None = {}
    new_after_upload: set[str] = set()
    box = resume_box(fields) if resume else None
    if box is not None:
        before_fields = fields
        before = classify.classify_fields([f for f in before_fields if f.get("type") != "file"])
        try:
            handle = page.query_selector(f"[{extract.REF_ATTR}='{box['ref']}']")
        except Exception:
            handle = None
        worked, detail = filler.upload_file(page, box["ref"], resume)
        lines.append(detail)
        if not worked:
            events.append({"kind": "upload_failed", "detail": detail})
        browser_mod.settle(page, 2500)
        fields = extract.enrich_comboboxes(page, extract.extract_fields(page, guard))
        ref = outcome.upload_ref(handle, fields) if worked else None
        uploaded = {ref: [Path(resume).name]} if ref else None
        categories, new_after_upload = reclassify_after_upload(before_fields, before, fields)
    else:
        categories = classify.classify_fields([f for f in fields if f.get("type") != "file"])
    plan, flags, for_model = route(fields, categories, prof, new_after_upload)
    ask_mapper(for_model, prof, posting_text, new_after_upload, plan)
    prefill = prefill_census(page)
    plan = fillable_plan(fields, plan)
    rows = filler.run_plan(page, fields, plan)
    step = shadow.step_entry(fields, categories, plan, flags, rows, prefill_census=prefill)
    try:
        snap = extract.snapshot(page, fields)
    except Exception:
        snap = {}
    return PageFill(fields=fields, categories=categories, plan=plan, flags=flags, rows=rows,
                    step=step, events=events, uploaded=uploaded, snapshot=snap, lines=lines)


def has_next(page: "Page", portal: str) -> bool:
    return adapters.has_next_step(page, portal)
