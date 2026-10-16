#!/usr/bin/env python3
"""
apply.py — the command you actually run.

WHAT THIS DOES: Ties the other pieces together in order — open the page, work
out the portal, upload the resume, split off the legal questions, ask the AI
about the rest, type it all in, write the review sheet, stop.

WHY IT MATTERS: This is the file that decides the order of operations, and
the order matters. Resume upload happens before text entry because several
portals autofill from the resume and would overwrite everything otherwise.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import threading
import time
from pathlib import Path

from agent import (adapters, browser as browser_mod, classify, config, extract,
                   filler, follow, gate, learn, ledger, letters, mapper, outcome,
                   pages, report, score, sensitive, shadow, template, tracker)
from agent.profile import Profile

# Terminal colours. Harmless if your terminal ignores them.
BOLD, DIM, GREEN, AMBER, RED, OFF = (
    "\033[1m", "\033[2m", "\033[32m", "\033[33m", "\033[31m", "\033[0m")



# ---------------------------------------------------------------------------
#  init — put your personal files somewhere private
# ---------------------------------------------------------------------------
def cmd_init(args) -> None:
    """
    Create your profile OUTSIDE the project folder.

    Your profile holds your address, phone number and work history. It has no
    business in a git repository, so it lives in ~/.job-agent/ alongside the
    application log and your saved browser logins. The repo only ever carries
    a blank template.
    """
    import shutil

    config.ensure_dirs()
    target = config.STATE_DIR / "profile.yaml"
    docs = config.STATE_DIR / "documents"
    legacy = config.ROOT / "profile.yaml"
    legacy_docs = config.ROOT / "documents"

    print(f"\n{BOLD}Private folder:{OFF} {config.STATE_DIR}")

    # -- move an existing in-repo profile across ---------------------------
    if args.migrate:
        if not legacy.exists():
            print(f"{DIM}Nothing to migrate — no profile.yaml in the project folder.{OFF}")
        elif target.exists():
            print(f"{RED}{target} already exists.{OFF} Merge by hand, or move it "
                  f"aside first — I won't overwrite a profile.")
            return
        else:
            shutil.move(str(legacy), str(target))
            print(f"  {GREEN}moved{OFF} profile.yaml  ->  {target}")

        moved = 0
        if legacy_docs.exists():
            for f in legacy_docs.iterdir():
                if f.suffix.lower() in (".pdf", ".docx", ".doc", ".md", ".txt") \
                        and not f.name.startswith("README"):
                    dest = docs / f.name
                    if not dest.exists():
                        shutil.move(str(f), str(dest))
                        moved += 1
        if moved:
            print(f"  {GREEN}moved{OFF} {moved} document(s)  ->  {docs}")

        print(f"\n{GREEN}Done.{OFF} Nothing personal is left in the project folder.")
        return

    # -- fresh start from the template -------------------------------------
    if target.exists():
        print(f"{GREEN}You already have a profile:{OFF} {target}")
        print(f"{DIM}Edit it there. Use --migrate if you also have an old copy "
              f"inside the project folder.{OFF}")
        return

    target.write_text(template.BLANK_PROFILE, encoding="utf-8")
    print(f"  {GREEN}created{OFF} {target}")
    print(f"  {GREEN}created{OFF} {docs}{DIM}  <- put your resume here{OFF}")
    print(f"\nNext: open the profile and fill it in, drop your resume PDF in the")
    print(f"documents folder, then run `python apply.py check`.")


# ---------------------------------------------------------------------------
#  doctor — is this machine set up correctly?
# ---------------------------------------------------------------------------
def cmd_doctor(args) -> None:
    import shutil

    ok = True

    def line(good, label, detail=""):
        nonlocal ok
        ok = ok and good
        mark = f"{GREEN}pass{OFF}" if good else f"{RED}FAIL{OFF}"
        print(f"  {mark}  {label}{('  ' + DIM + detail + OFF) if detail else ''}")

    print(f"\n{BOLD}Environment{OFF}")
    line(sys.version_info >= (3, 10), "Python 3.10+", sys.version.split()[0])

    try:
        import playwright  # noqa: F401
        line(True, "playwright installed")
    except ImportError:
        line(False, "playwright installed", "run: pip install -r requirements.txt")

    try:
        import anthropic  # noqa: F401
        line(True, "anthropic SDK installed")
    except ImportError:
        line(False, "anthropic SDK installed", "run: pip install -r requirements.txt")

    # The same call `fill` makes first, so a key that's set but dead fails
    # here rather than looking fine.
    status, detail = config.check_api_key()
    line(status == "ok", "API key accepted", {
        "ok": "",
        "rejected": "key rejected (401) — set a new ANTHROPIC_API_KEY",
        "missing": "set ANTHROPIC_API_KEY in your shell profile",
    }.get(status, f"couldn't check: {detail}"))

    # Ask Playwright where its browser is rather than guessing at a cache path —
    # PLAYWRIGHT_BROWSERS_PATH can move it anywhere.
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            chromium_path = Path(pw.chromium.executable_path)
        line(chromium_path.exists(), "Chromium downloaded", str(chromium_path.parent))
    except Exception:
        line(bool(shutil.which("chromium")), "Chromium downloaded",
             "run: playwright install chromium")

    print(f"\n{BOLD}Files{OFF}")
    profile_path = config.resolve_profile(args.profile)
    line(profile_path.exists(), "profile found", str(profile_path))
    if profile_path == config.ROOT / "profile.yaml" and profile_path.exists():
        print(f"        {AMBER}in the project folder — run `apply.py init --migrate`{OFF}")
    try:
        resume = Profile.load(args.profile).resume_path()
        line(resume is not None, "resume file found", str(resume or f"put a PDF in {config.STATE_DIR / 'documents'}"))
    except Exception as e:
        line(False, "profile.yaml loads", str(e)[:80])

    print(f"\n{GREEN}Ready.{OFF}\n" if ok else f"\n{RED}Fix the failures above first.{OFF}\n")


# ---------------------------------------------------------------------------
#  check — what still needs filling in?
# ---------------------------------------------------------------------------
def cmd_check(args) -> None:
    prof = Profile.load(args.profile)
    audit = prof.audit()

    print(f"\n{BOLD}Profile:{OFF} {prof.path}")
    resume = prof.resume_path()
    missing = f"{RED}not found — put a PDF in {config.STATE_DIR / 'documents'}{OFF}"
    print(f"{BOLD}Resume:{OFF}  {resume or missing}\n")

    if audit["required"]:
        print(f"{RED}{BOLD}Must fill before this is useful ({len(audit['required'])}){OFF}")
        for path in audit["required"]:
            print(f"  · {path}")
        print()

    if audit["recommended"]:
        print(f"{AMBER}Worth filling ({len(audit['recommended'])}){OFF}")
        for path in audit["recommended"]:
            print(f"  · {path}")
        print()

    if audit["verify"] and args.verbose:
        print(f"{DIM}Guessed from your resume — confirm these ({len(audit['verify'])}){OFF}")
        for item in audit["verify"]:
            print(f"  · {item}")
        print()
    elif audit["verify"]:
        print(f"{DIM}{len(audit['verify'])} values were read off your resume and should be "
              f"confirmed. Run with --verbose to list them.{OFF}\n")

    if not audit["required"]:
        print(f"{GREEN}Nothing required is missing. Good to go.{OFF}\n")


# ---------------------------------------------------------------------------
#  fill — the main event
# ---------------------------------------------------------------------------
def cmd_fill(args) -> None:
    prof = Profile.load(args.profile)
    url = args.url

    if getattr(args, "follow", False) and args.dry_run:
        # A dry run touches nothing; follow mode fills as you go.
        print(f"{RED}--follow and --dry-run don't go together.{OFF} "
              f"Use --dry-run alone to see the plan for one page.")
        return

    if (existing := tracker.find(url)) and not args.force:
        print(f"{AMBER}Already logged: status '{existing['status']}', "
              f"{existing['created_at'][:10]}.{OFF}")
        print(f"{DIM}Add --force to do it again.{OFF}")
        return

    blocking = prof.audit()["required"]
    if blocking and not args.force:
        print(f"{RED}{len(blocking)} required profile field(s) are blank.{OFF} "
              f"Run `make check` first, or --force past this.")
        return

    # Before the browser opens. --force doesn't skip this: with no working
    # key every field would come back "unknown" and the run is wasted.
    status, detail = config.check_api_key()
    if status in ("rejected", "missing"):
        print(f"{RED}Anthropic API key {'rejected' if status == 'rejected' else 'not found'}"
              f"{OFF} ({detail}).")
        print("Set a new ANTHROPIC_API_KEY and try again — "
              "`python apply.py doctor` re-checks it.")
        sys.exit(1)
    if status == "unchecked":
        print(f"{AMBER}Couldn't check the API key ({detail}).{OFF} "
              f"{DIM}Carrying on; if the API is unreachable, every field comes to you.{OFF}")

    with browser_mod.browser() as ctx:
        page = browser_mod.open_page(ctx, url)

        key, ats, detected_by = adapters.detect_with_method(url, page)
        print(f"\n{BOLD}{ats.name}{OFF} {DIM}({ats.difficulty}){OFF}")
        print(f"{DIM}{ats.summary}{OFF}")
        for quirk in ats.quirks:
            print(f"{DIM}  · {quirk}{OFF}")

        # What happened along the way, for the submit gate's record. The gate
        # stops on any CAPTCHA or login wall, even one that was got past.
        events: list[dict] = []

        # -- pause for anything we refuse to handle automatically ------------
        if found := browser_mod.detect_captcha(page):
            events.append({"kind": "captcha", "detail": found})
            print(f"\n{AMBER}{found} on the page.{OFF} Solve it in the browser window, "
                  f"then press Enter here.")
            input()
            browser_mod.settle(page)

        if _get_past_login_wall(page):
            events.append({"kind": "login_wall", "detail": "before Apply"})
        # The posting's text, taken once, before Apply is clicked or anything
        # is typed, and the only page text any model call is given. Later the
        # page can show your answers outside any form (a review step, a
        # summary), and no value from the page may reach the model.
        posting_text = extract.page_text(page, 6000)
        # Role and company from the posting's title, for the same reason: a
        # later page's title can carry an answer, and the letter is sent both.
        role, company = extract.guess_role_and_company(page)
        if getattr(args, "follow", False):
            _follow(page, args, prof, url, key, ats, detected_by, events, posting_text,
                    role, company)
            return
        # You open the form: on a portal you're signed in to, "Apply" can be
        # a one-click submit, so the agent never presses it.
        print(f"\n{BOLD}Click Apply if the form isn't open, then press Enter.{OFF}")
        input()
        adapters.prepare(page, key)
        # Again: Workday only shows its sign-in page once Apply is clicked.
        if _get_past_login_wall(page):
            events.append({"kind": "login_wall", "detail": "after Apply"})

        # One page: the one you're on. The agent fills it and stops. It never
        # clicks Next or Continue; you move between pages yourself.
        steps: list[dict] = []
        # {file box ref: names the agent uploaded there}, the baseline for
        # spotting a resume you swapped. None once it can't be known.
        uploaded: dict[str, list[str]] | None = {}
        # What this run may never read: every field seen as secret (by each
        # of its keys) and every page seen with a password box on it.
        guard = extract.ReadGuard()
        resume = prof.resume_path(args.resume)
        # Fields whose help text was read after the resume upload and never
        # seen before it. The portal may have autofilled fields by then, and
        # help text can be validation text echoing a value ("'(555) 12' is
        # not a valid phone number"), so none of it is ever sent to a model.
        new_after_upload: set[str] = set()
        # And once more just before the scan: a page can ask you to sign in late.
        if _get_past_login_wall(page):
            events.append({"kind": "login_wall", "detail": "step 1"})

        fields = extract.extract_fields(page, guard)
        if not fields:
            print(f"\n{RED}No form fields found.{OFF} The form is probably "
                  f"behind another click, or inside an iframe.")
            return

        # Open custom dropdowns to read their choices — otherwise they
        # arrive with no options and can't be matched to your profile.
        fields = extract.enrich_comboboxes(page, fields)
        print(f"\n{len(fields)} field(s) on the page.")

        # 1. Resume first: portals autofill from it and would overwrite us.
        uploads = [f for f in fields if f.get("type") == "file"]
        upload_now = bool(uploads and resume and not args.dry_run)
        if upload_now:
            # Classified now, with help text read before the upload changed
            # anything, so an unchanged field keeps this answer after it.
            before_fields = fields
            before = classify.classify_fields(
                [f for f in before_fields if f.get("type") != "file"])
        if upload_now:
            try:
                box = page.query_selector(
                    f"[{extract.REF_ATTR}='{uploads[0]['ref']}']")
            except Exception:
                box = None
            worked, detail = filler.upload_file(page, uploads[0]["ref"], resume)
            print(f"  {GREEN if worked else RED}{detail}{OFF}")
            if not worked:
                events.append({"kind": "upload_failed", "detail": detail})
            browser_mod.settle(page, 2500)
            fields = extract.enrich_comboboxes(
                page, extract.extract_fields(page, guard))
            # A failed upload put nothing there, or nothing known; either
            # way the baseline can't be trusted.
            ref = outcome.upload_ref(box, fields) if worked else None
            uploaded = {ref: [Path(resume).name]} if ref else None

        # 2. Ask the model what KIND of question each field is. It never
        #    sees your data here and never answers anything.
        if upload_now:
            categories, new_after_upload = _reclassify_after_upload(before_fields, before,
                                                                    fields)
        else:
            categories = classify.classify_fields(
                [f for f in fields if f.get("type") != "file"])

        # 3. Apply the policy. Sensitive fields never reach the mapper.
        plan, flags, for_model = _route(fields, categories, prof, new_after_upload)

        counts = {}
        for f in fields:
            c = categories.get(f["ref"], "file")
            counts[c] = counts.get(c, 0) + 1
        print(f"{DIM}  {', '.join(f'{v} {k}' for k, v in sorted(counts.items()))}{OFF}")
        print(f"{DIM}  {len(plan)} from profile · {len(flags)} for you "
              f"· {len(for_model)} to the model{OFF}")

        # 4. The mapper answers only what's left.
        _ask_mapper(for_model, prof, posting_text, new_after_upload, plan)

        # The checkboxes as they stand just before the agent fills this
        # step's answers, so the gate can tell a box the agent's answer
        # ticked from one that came ticked. On step 1 this is after the
        # resume upload, so anything the portal ticks from the resume
        # reads as already ticked, which stops. A read only.
        prefill = _prefill_census(page)

        # A Next or Continue control means the application goes on past this
        # page. It's only looked for, never clicked: the gate stops on it,
        # and you carry on yourself.
        more_pages = adapters.has_next_step(page, key)
        if more_pages:
            events.append({"kind": "multi_step", "detail": "a Next or Continue control"})

        if args.dry_run:
            steps.append(shadow.step_entry(fields, categories, plan, flags, None,
                                           prefill_census=prefill))
            record = _build_record(page, url, key, detected_by, company, steps,
                                   events, dry_run=True, uploads=uploaded)
            _print_dry_run(fields, plan, flags)
            _shadow_gate(record, prof, url, key, detected_by, dry_run=True)
            return

        plan = _fillable_plan(fields, plan)
        rows = filler.run_plan(page, fields, plan)
        steps.append(shadow.step_entry(fields, categories, plan, flags, rows,
                                       prefill_census=prefill))
        _print_summary(rows, flags)
        # Again after filling: a Next or Continue can appear once a page is
        # answered.
        if not more_pages and adapters.has_next_step(page, key):
            more_pages = True
            events.append({"kind": "multi_step", "detail": "a Next or Continue control"})
        if more_pages:
            print(f"\n{AMBER}This application continues on another page.{OFF} I filled "
                  f"this one only, and I never click Next or Continue: carry on in the "
                  f"browser yourself.")

        # Read before the review sheet opens and you start editing: this is
        # the form as the agent left it.
        record = _build_record(page, url, key, detected_by, company, steps, events,
                               dry_run=False, uploads=uploaded)

        # -- 5. optional cover letter ----------------------------------------
        if args.cover_letter:
            print(f"\n{DIM}Drafting a cover letter…{OFF}")
            try:
                text = letters.draft_cover_letter(prof, posting_text, company, role)
                path = letters.save_draft(text, company, role)
                print(f"  {GREEN}draft saved:{OFF} {path}")
            except Exception as e:
                print(f"  {RED}cover letter failed: {str(e)[:100]}{OFF}")

        # -- 6. review sheet -------------------------------------------------
        sheet = report.build(url=url, ats_name=ats.name, report_rows=rows,
                             flags=flags, company=company, role=role)
        report.open_in_browser(sheet)

        tracker.record(url, company=company, role=role, ats=ats.name,
                       resume_used=str(resume or ""), answers=rows, flags=flags,
                       report_path=str(sheet))

        print(f"\n{BOLD}Form filled. Stopping here — I don't submit.{OFF}")
        print(f"Review sheet: {sheet}")
        if flags:
            print(f"{AMBER}{len(flags)} question(s) are waiting for you — see "
                  f"the top of the review sheet.{OFF}")
        run_id, gate_result = _shadow_gate(record, prof, url, key, detected_by,
                                           dry_run=False)

        # -- 7. watch, don't touch: what you actually did ---------------------
        print(f"\nReview and submit in the browser as you normally would. I'm only "
              f"watching this tab")
        print("for the portal's confirmation page, and I won't click anything.")
        print(f"{DIM}Press Enter here when you're done, submitted or not.{OFF}")
        enter = _EnterWatcher()
        watched = outcome.watch(page, record=record, stop_requested=enter.pressed)
        if not enter.pressed():
            print(f"\n{_WATCH_ENDED.get(watched['ended'], 'Stopped watching.')} "
                  f"Press Enter to continue.")
            enter.wait()
        _log_corrections(page, url, fields, plan, categories, final=watched["final"])

        answer = _ask_submitted()
        if answer == "y":
            tracker.mark_submitted(url)
            print(f"{GREEN}Logged.{OFF}")
        _record_outcome(run_id, key, record, gate_result, watched, answer)


_WATCH_ENDED = {
    "success": "The portal's confirmation page appeared.",
    "tab_closed": "The tab closed.",
    "timeout": "Stopped watching after 30 minutes.",
    "idle": "No new page and no input from you for 20 minutes.",
    "hard_cap": "Stopped following after 90 minutes.",
}

def _route(fields: list[dict], categories: dict, prof,
           new_after_upload: set[str]) -> tuple[dict, list[dict], list[dict]]:
    """
    The policy for one page's fields: (plan from the profile, flags for you,
    fields for the mapper). Sensitive fields never reach the mapper. Shared
    by a normal fill and follow mode, so both route a field the same way.
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
                          "reason": "Its help text changed or appeared after the resume "
                                    "upload (help text held back after upload), so the "
                                    "classifier couldn't be shown it. Answer it yourself."})
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


def _ask_mapper(for_model: list[dict], prof, posting_text: str,
                new_after_upload: set[str], plan: dict) -> None:
    """The mapper answers only what's left, into `plan`."""
    if not for_model:
        return
    mapped = mapper.map_fields(
        [_for_model(f, f["ref"] not in new_after_upload) for f in for_model],
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


# ---------------------------------------------------------------------------
#  follow mode: you move through the application; the agent fills each page
# ---------------------------------------------------------------------------
FOLLOW_IDLE_S = 20 * 60        # no new page and no input from you for this long
FOLLOW_CAP_S = 90 * 60         # and never longer than this
FOLLOW_POLL_MS = 500

# A file box the agent may put your resume in: its label or name says resume
# or CV, and not cover letter. Exactly one such box, or none is used.
_RESUME_BOX = re.compile(r"\b(r.sum.|cv|curriculum\s+vitae)\b", re.I)
_COVER = re.compile(r"\bcover\b", re.I)


def _resume_box(fields: list[dict]) -> dict | None:
    boxes = [f for f in fields if f.get("type") == "file"
             and _RESUME_BOX.search(f"{f.get('label') or ''} {f.get('name') or ''}")
             and not _COVER.search(f"{f.get('label') or ''} {f.get('name') or ''}")]
    return boxes[0] if len(boxes) == 1 else None


class _FollowRun:
    """One follow-mode application: every step's answers, one record at the end."""

    def __init__(self, page, prof, args, url, key, posting_text, events):
        self.page, self.prof, self.args, self.url = page, prof, args, url
        self.key, self.posting_text, self.events = key, posting_text, events
        self.watcher = follow.StepWatcher(quiet_ms=follow.QUIET_MS)
        self.ledger = ledger.FieldLedger()
        self.guard = ledger.ActivityGuard(quiet_ms=ledger.QUIET_MS)
        self.cache = ledger.ClassificationCache()
        self.read_guard = extract.ReadGuard()
        self.seen_pages: dict = {}
        self.resume = prof.resume_path(args.resume)
        self.resume_done = False
        self.uploaded: dict[str, list[str]] | None = {}
        self.rows: list[dict] = []
        self.flags: list[dict] = []
        self.steps: list[dict] = []
        self.step_numbers: set[int] = set()
        self.last_shape: dict | None = None
        self.scanned = False            # any page has been scanned in this run

    def _classify(self, fields: list[dict], with_help: bool = True) -> dict[str, str]:
        """
        Categories, from the cache or the classifier. Without help, every
        field goes over (and is cached) with its help text blanked.
        """
        fields = [f if with_help else _for_model(f, False)
                  for f in fields if f.get("type") != "file"]
        known, todo = self.cache.split(fields)
        fresh = classify.classify_fields(todo) if todo else {}
        self.cache.remember(todo, fresh)
        return {**known, **fresh}

    def _note_next_step(self) -> None:
        """A visible Next or Continue control: multi_step, as a normal fill logs it."""
        if adapters.has_next_step(self.page, self.key) and not any(
                e.get("kind") == "multi_step" for e in self.events):
            self.events.append({"kind": "multi_step", "detail": "a Next or Continue control"})

    def fill_step(self, number: int) -> None:
        page = self.page
        # Quiet first, so even the first scan doesn't read help text while
        # you're typing.
        if not self.guard.wait_quiet(page):
            self.events.append({"kind": "never_quiet", "detail": f"step {number}"})
            return
        # Help text goes to a model only on the run's very first scan. Once
        # anything was scanned, uploaded or filled, help text can echo a
        # value (your input, the resume autofill, validation text).
        later = bool(self.resume_done or self.steps or self.scanned)
        fields = extract.extract_fields(page, self.read_guard)
        self.scanned = True
        if not fields:
            return
        with self.guard.filling(page):
            fields = extract.enrich_comboboxes(page, fields, contained=True)
        new_after_upload: set[str] = set()
        box = None if self.resume_done or not self.resume else _resume_box(fields)
        if box is not None and self.ledger.state(page, box).get("empty"):
            before_fields, before = fields, self._classify(fields, with_help=not later)
            with self.guard.filling(page):
                worked, detail = filler.upload_file(page, box["ref"], self.resume)
            print(f"  {GREEN if worked else RED}{detail}{OFF}")
            self.resume_done = True
            if not worked:
                self.events.append({"kind": "upload_failed", "detail": detail})
            browser_mod.settle(page, 2500)
            fields = extract.enrich_comboboxes(
                page, extract.extract_fields(page, self.read_guard), contained=True)
            self.uploaded = None            # the baseline across steps isn't tracked yet
            categories, new_after_upload = _reclassify_after_upload(before_fields, before, fields)
            if later:
                new_after_upload = {f["ref"] for f in fields if f.get("type") != "file"}
        elif later:
            # After the resume upload, or once any page was filled, help text
            # can echo a value (validation text, an autofilled summary): none
            # is sent to a model, and a field with help comes to you.
            categories = self._classify(fields, with_help=False)
            new_after_upload = {f["ref"] for f in fields if f.get("type") != "file"}
        else:
            categories = self._classify(fields)
        self._note_next_step()
        plan, flags, for_model = _route(fields, categories, self.prof, new_after_upload)
        _ask_mapper(for_model, self.prof, self.posting_text, new_after_upload, plan)
        plan = _fillable_plan(fields, plan)
        prefill = _prefill_census(page)
        plan = self.ledger.fillable(page, fields, plan, step=number)
        rows: list[dict] = []
        if plan:
            if not self.guard.wait_quiet(page):
                self.events.append({"kind": "never_quiet", "detail": f"step {number}"})
                return
            with self.guard.filling(page):
                rows = filler.run_plan(page, fields, plan, contained=True)
            self.ledger.filled(page, fields, plan, rows, step=number)
        self._note_next_step()
        self.watcher.filled()
        self.last_shape = follow.read_shape(page)
        self.steps.append(shadow.step_entry(fields, categories, plan, flags, rows,
                                            prefill_census=prefill))
        self.rows += rows
        self.flags += flags
        print(f"\n{BOLD}Step {number}{OFF} {DIM}({len(rows)} filled, "
              f"{len(flags)} for you){OFF}")
        _print_summary(rows, flags)

    def loop(self, enter, clock=time.monotonic) -> tuple[str, dict]:
        page = self.page
        start = last = clock()
        detection = {"result": "none", "signals": {}, "url_shape": ""}
        self.guard.install(page)
        while True:
            if page.is_closed():
                return "tab_closed", detection
            if enter.pressed():
                return "enter", detection
            now = clock()
            if now - start > FOLLOW_CAP_S:
                return "hard_cap", detection
            if self.guard.quiet_for(page) < FOLLOW_POLL_MS * 2:
                last = now                              # you're active
            if now - last > FOLLOW_IDLE_S:
                return "idle", detection
            try:
                detection = outcome.detect_success(page, portal=self.key,
                                                   portal_method=self.args._detected_by,
                                                   start_url=self.url, refs=[])
            except Exception:
                pass
            if detection.get("result") == "match":
                return "success", detection
            event = self.watcher.observe(follow.read_shape(page))
            if event is not None:
                last = now
                kind, number = event
                self.step_numbers.add(number)
                if len(self.step_numbers) > 1 and not any(
                        e.get("kind") == "multi_step" for e in self.events):
                    self.events.append({"kind": "multi_step", "detail": "more than one page"})
                verdict = pages.fill_allowed(page, self.url, self.seen_pages)
                if verdict:
                    self.events.append({"kind": verdict, "detail": f"step {number}"})
                    print(f"{DIM}  step {number}: {verdict.replace('_', ' ')}; "
                          f"I fill nothing here{OFF}")
                else:
                    self.guard.install(page)
                    try:
                        self.fill_step(number)
                    except Exception as e:
                        # You clicked on, or closed the tab, mid-fill. Logged
                        # (it stops the gate), and following carries on.
                        self.events.append({"kind": "fill_step_failed",
                                            "detail": f"step {number}: {type(e).__name__}"})
                        if page.is_closed():
                            return "tab_closed", detection
            else:
                self.ledger.poll(page)
            try:
                page.wait_for_timeout(FOLLOW_POLL_MS)
            except Exception:
                return "tab_closed", detection


def _follow(page, args, prof, url, key, ats, detected_by, events, posting_text,
            role, company) -> None:
    """
    Follow mode. You click Apply, Next, Continue, Sign in and Submit; the
    agent stays on its tab, fills each new page as it appears (only empty
    fields that aren't yours, after you've been idle a moment, never typing
    anywhere but the field it fills), and fills nothing on sign-in, account,
    verification, other-site or LinkedIn pages. One record and one review
    sheet cover every page.
    """
    print(f"\n{BOLD}Follow mode.{OFF} You click Apply, Next, Continue, sign in and "
          f"Submit yourself;")
    print("I fill each page as it appears, only empty fields, and only once you've "
          "paused for a moment.")
    print(f"{DIM}Press Enter here when you're done, submitted or not.{OFF}")
    args._detected_by = detected_by
    run = _FollowRun(page, prof, args, url, key, posting_text, events)
    enter = _EnterWatcher()
    ended, detection = run.loop(enter)
    print(f"\n{_WATCH_ENDED.get(ended, 'Stopped following.')}")
    # The gate's record is read now, from whatever page is showing. Unless
    # you pressed Enter on the form the agent last filled, that isn't the
    # form, so the read can't vouch for it.
    try:
        now_shape = None if page.is_closed() else follow.read_shape(page)
    except Exception:
        now_shape = None
    if (ended != "enter" or run.last_shape is None or now_shape is None
            or (now_shape["url"], now_shape["fields"])
            != (run.last_shape["url"], run.last_shape["fields"])):
        events.append({"kind": "final_read_off_form", "detail": ended})

    record = _build_record(page, url, key, detected_by, company, run.steps, events,
                           dry_run=False, uploads=run.uploaded)
    sheet = report.build(url=url, ats_name=ats.name, report_rows=run.rows,
                         flags=run.flags, company=company, role=role)
    report.open_in_browser(sheet)
    tracker.record(url, company=company, role=role, ats=ats.name,
                   resume_used=str(run.resume or ""), answers=run.rows, flags=run.flags,
                   report_path=str(sheet))
    print(f"Review sheet: {sheet}")
    run_id, gate_result = _shadow_gate(record, prof, url, key, detected_by, dry_run=False)
    answer = _ask_submitted()
    if answer == "y":
        tracker.mark_submitted(url)
        print(f"{GREEN}Logged.{OFF}")
    watched = {"ended": ended, "detection": detection, "final": None,
               "capture": "none", "hooked": False}
    _record_outcome(run_id, key, record, gate_result, watched, answer)



class _EnterWatcher:
    """
    Notices Enter in this terminal without blocking the watch. A background
    thread only reads one line of input; every browser call stays on the main
    thread, where Playwright needs it.
    """

    def __init__(self):
        self._done = threading.Event()
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        try:
            sys.stdin.readline()
        finally:
            self._done.set()

    def pressed(self) -> bool:
        return self._done.is_set()

    def wait(self) -> None:
        self._done.wait()


def _ask_submitted() -> str | None:
    """y, n, or None if you'd rather not say. Your answer is the ground truth."""
    reply = input("Did you submit it? [y/n, Enter to skip] ").strip().lower()
    return "y" if reply.startswith("y") else "n" if reply.startswith("n") else None


def _record_outcome(run_id, portal, record, gate_result, watched, answer) -> None:
    """Store what you did against the gate's call. Never fails the run."""
    if run_id is None:
        return
    try:
        verified = outcome.signature_verified(portal, tracker.gate_runs(portal))
        result = outcome.human_submitted(detected=watched["detection"]["result"],
                                         answer=answer, signature_verified=verified)
        edits = outcome.compute_edits(record, gate_result, watched["final"])
        tracker.set_gate_outcome(run_id, human_submitted=result, edits=edits,
                                 capture=watched["capture"],
                                 success_signal=outcome.success_signal(watched, answer))
        changed = "unknown" if edits is None else len(edits)
        print(f"{DIM}  shadow outcome: submitted {result} · fields changed {changed} · "
              f"read {watched['capture']}{OFF}")
    except Exception as e:
        print(f"{DIM}  couldn't record the outcome ({type(e).__name__}){OFF}")


def _shadow_gate(record, prof, url, portal, detected_by, *,
                 dry_run: bool) -> tuple[int | None, dict]:
    """
    Shadow mode: print what the submit gate would have done, and log it. It
    only ever reports. Submit stays yours, and nothing here can fail a run.
    A dry run's verdict is printed but not logged, so it's never scored.
    Returns (the gate_runs row id or None, the gate's result).
    """
    try:
        submitted_today = tracker.agent_submissions_today()
    except Exception:
        submitted_today = None          # unknown: never read as none used
    result = gate.decide(record, prof, agent_submissions_today=submitted_today)
    verdict = result["content_verdict"]
    colour = GREEN if verdict == gate.WOULD_SUBMIT else AMBER
    print(f"\n{BOLD}Shadow gate:{OFF} {colour}{verdict}{OFF}"
          f"{' (dry run, not logged)' if dry_run else ''} — Submit is still yours.")
    for reason in result["content_reasons"][:15]:
        print(f"  · {reason}")
    if len(result["content_reasons"]) > 15:
        print(f"  {DIM}…and {len(result['content_reasons']) - 15} more{OFF}")
    ena = result["enablement"]
    print(f"{DIM}  enablement: portal allowlisted {'yes' if ena['portal_allowlisted'] else 'no'}"
          f" · cap remaining: {gate.cap_text(ena, result['enablement_reasons'])}{OFF}")
    actions = list(result.get("consent_actions", {}).values())
    if actions:
        print(f"{DIM}  allowlisted consent boxes: {actions.count('would_tick')} would be ticked, "
              f"{actions.count('leave')} left; the agent ticked none{OFF}")
    if dry_run:
        return None, result
    try:
        return tracker.record_gate_run(url, portal=portal, portal_method=detected_by,
                                       result=result,
                                       record_json=shadow.redacted(record, result)), result
    except Exception as e:
        print(f"{DIM}  couldn't log the gate's verdict ({type(e).__name__}){OFF}")
        return None, result


def _for_model(field: dict, send_help: bool) -> dict:
    """A field as a model call is given it: a copy, without its help text unless allowed."""
    return dict(field) if send_help else {**field, "help_text": ""}


def _identity(f: dict) -> tuple | None:
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


def _reclassify_after_upload(before: list[dict], before_categories: dict,
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
            key = _identity(f)
            if key is not None and f.get("type") != "file":
                seen.setdefault(key, []).append(f)
        return {k: v[0] for k, v in seen.items() if len(v) == 1}
    was, now = unique(before), unique(after)
    categories: dict[str, str] = {}
    new: list[dict] = []
    for f in after:
        if f.get("type") == "file":
            continue
        key = _identity(f)
        old = was.get(key) if key in now else None
        if (old is not None and old.get("ref") in before_categories
                and old.get("label") == f.get("label")
                and (old.get("help_text") or "") == (f.get("help_text") or "")):
            categories[f["ref"]] = before_categories[old["ref"]]
        else:
            new.append(f)
    if new:
        categories.update(classify.classify_fields([_for_model(f, False) for f in new]))
    return categories, {f["ref"] for f in new}


def _fillable_plan(fields: list[dict], plan: dict) -> dict:
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


def _prefill_census(page) -> list[dict] | None:
    """The checkbox census before filling; None if unreadable, which the gate never exempts."""
    try:
        return extract.checkbox_census(page)
    except Exception:
        return None


def _build_record(page, url, portal, detected_by, company, steps, events, *,
                  dry_run: bool, uploads: dict | None = None) -> dict:
    """The submit gate's record of this run; a failed build is itself a record."""
    try:
        return shadow.build_record(page, url=url, portal=portal, portal_method=detected_by,
                                   company=company, steps=steps, events=events,
                                   dry_run=dry_run, uploads=uploads)
    except Exception as e:
        return shadow.failed_record(url, portal, f"record not built: {type(e).__name__}")


def _get_past_login_wall(page) -> bool:
    """
    If the page is a login wall, pause while you sign in. Returns whether
    there was a wall at all. The agent never clicks Sign in, Create account
    or an SSO button, and never types, reads or stores a password: Chrome's
    own password manager may fill the boxes, and you press the button.

    Run at the start, after you've opened the form, and just before the
    scan: Workday only shows its sign-in page once Apply is clicked, so a
    check made once, up front, never saw it.
    """
    if not browser_mod.detect_login_wall(page):
        return False
    print(f"\n{AMBER}This portal wants a login.{OFF}")
    print("Sign in yourself in the browser window. Chrome may fill a login it")
    print("saved; you press Sign in. Create the account yourself if you need one,")
    print(f"and let Chrome save the password when it offers. The session is saved")
    print(f"to {DIM}{config.CHROME_PROFILE_DIR}{OFF}.")
    print("Press Enter when you're in.")
    input()
    browser_mod.settle(page)
    return True


def _log_corrections(page, url, fields, plan, categories, final=None) -> None:
    """
    Record what you changed after the agent finished. Values are kept only for
    ordinary fields; for anything sensitive we log THAT you answered, not WHAT.
    `final` is the watcher's last read of the form, taken before it was
    submitted away; without one, the page is read now.
    """
    if final is None:
        try:
            final = extract.snapshot(page, fields)
        except Exception as e:
            print(f"{DIM}  couldn't read the final form ({str(e)[:60]}){OFF}")
            return

    norm = lambda v: "" if v in (None, False) else str(v).strip()
    rows = []
    for f in fields:
        if f.get("type") == "file" or extract.is_secret(f):
            continue
        if f["ref"] not in final:
            continue        # not read back; logging it as cleared would be false
        agent = (plan.get(f["ref"]) or {}).get("value")
        now = final.get(f["ref"])
        if norm(agent) == norm(now):
            continue
        # Judged on the real values, before sensitive ones are dropped: a
        # reformatted phone number or a dropdown that reads back blank isn't
        # you correcting anything, and logging it teaches learn the wrong thing.
        if (learn.same_answer(agent, now, f.get("type"))
                or learn.unreadable(agent, now, f.get("type"))):
            continue
        cat = categories.get(f["ref"], "unknown")
        # Values are only kept for fields both layers agreed were ordinary --
        # the same test that let the model near them in the first place.
        safe = (classify.action_for(cat) in ("autofill", "draft")
                and sensitive.stop_reason(sensitive._question_text(f)) is None
                and sensitive.legal_guard(f) is None)
        # Which profile line the question belongs to: the one that filled it,
        # else the one its wording maps to. Only the line's name is kept, never
        # its value, so this is safe to log for sensitive questions too.
        line = ((plan.get(f["ref"]) or {}).get("profile_path")
                or next(iter(sensitive.profile_lines(sensitive._question_text(f))), None))
        rows.append({
            "url": url, "label": f.get("label"), "type": f.get("type"),
            "category": cat,
            "kind": "filled_blank" if norm(agent) == "" else "overwrote",
            "agent_value": agent if safe else None,
            "final_value": now if safe else None,
            "profile_path": line,
            "logged_at": tracker._timestamp(),
            "log_version": learn.LOG_VERSION,
        })

    if rows:
        path = config.STATE_DIR / "corrections.jsonl"
        with path.open("a", encoding="utf-8") as fh:
            for r in rows:
                fh.write(json.dumps(r, default=str) + "\n")
        print(f"{DIM}  logged {len(rows)} correction(s) to {path}{OFF}")


def _print_dry_run(fields, plan, flags) -> None:
    print(f"\n{BOLD}Dry run — nothing was typed.{OFF}\n")
    by_ref = {f["ref"]: f for f in fields}
    for ref, decision in plan.items():
        label = str(by_ref.get(ref, {}).get("label", "?"))[:52]
        # A null is the model declining to guess. Printing it as "[ok] None"
        # read like an answer of "None" — show the blank, and say why.
        blank = decision.get("value") is None
        value = "" if blank else str(decision.get("value"))[:55]
        if blank:
            mark = f"{AMBER}blank{OFF}"
        elif decision.get("needs_review"):
            mark = f"{AMBER}review{OFF}"
        else:
            mark = f"{GREEN}ok{OFF}"
        print(f"  [{mark}] {label:<54} {DIM}{value}{OFF}")
        note = decision.get("note") or ("left for you — no answer given" if blank else "")
        if note:
            print(f"           {DIM}{note}{OFF}")
    _print_flags(flags)


def _print_summary(rows, flags) -> None:
    print()
    for row in rows:
        if row["filled"] and not row["needs_review"]:
            mark = f"{GREEN}done  {OFF}"
        elif row["filled"]:
            mark = f"{AMBER}check {OFF}"
        else:
            mark = f"{RED}failed{OFF}"
        print(f"  {mark} {str(row['label'])[:52]:<54} {DIM}{str(row['value'])[:45]}{OFF}")
        if not row["filled"] and row["detail"] != "skipped — no value":
            print(f"         {RED}{row['detail']}{OFF}")
    _print_flags(flags)


def _print_flags(flags) -> None:
    if not flags:
        return
    print(f"\n{AMBER}{BOLD}Left for you{OFF} {DIM}— legal declarations and "
          f"per-company answers I don't guess at:{OFF}")
    for flag in flags:
        print(f"  · {flag['label']}")
        print(f"    {DIM}{flag['reason']}{OFF}")


# ---------------------------------------------------------------------------
#  learn — what your corrections keep saying (report only)
# ---------------------------------------------------------------------------
def cmd_learn(args) -> None:
    # Report only: reads the log and the profile, writes nothing anywhere.
    path = learn.corrections_path()
    rows, bad = learn.load_rows(path)
    try:
        profile_data = Profile.load(args.profile).data
    except FileNotFoundError:
        profile_data = None
        print(f"{DIM}No profile found; suggestions can't point at profile lines.{OFF}")
    print(learn.render(rows, bad, learn.analyse(rows, profile_data), path))


# ---------------------------------------------------------------------------
#  shadow-report — the gate's calls against what you actually did
# ---------------------------------------------------------------------------
def _enablement_now(prof, portal: str, method: str, submitted_today: int | None) -> str:
    """
    The report's enablement line, from the gate's own rule (gate.enablement),
    so the two can't disagree. `method` is how the portal's latest run was
    detected; a portal found any way but by its host is never enabled.
    """
    ena, reasons = gate.enablement(portal, method, prof, submitted_today)
    if ena["portal_allowlisted"]:
        listed = "on autosubmit_portals"
    elif method != "host":
        listed = f"not enabled: detected by {method or 'nothing'}, not by its host"
    else:
        listed = "not on autosubmit_portals"
    return f"{listed} · cap remaining: {gate.cap_text(ena, reasons)}"


def cmd_shadow_report(args) -> None:
    rows = tracker.gate_runs()
    print(f"\n{BOLD}Shadow gate report{OFF} {DIM}— content verdicts only. The agent has "
          f"submitted nothing; every Submit was yours.{OFF}")
    if not rows:
        print("\nNo gate runs logged yet. Each real `fill` adds one.")
        return
    try:
        prof = Profile.load(args.profile)
    except FileNotFoundError:
        prof = None
    try:
        submitted_today = tracker.agent_submissions_today()
    except Exception:
        submitted_today = None          # unknown: never read as none used

    portals = score.by_portal(rows)
    for portal, s in portals.items():
        enabled = ("unknown (no profile)" if prof is None else
                   _enablement_now(prof, portal, s["portal_method"], submitted_today))
        print(f"\n{BOLD}{portal}{OFF}  {DIM}enablement now: {enabled}{OFF}")
        print(f"  runs {s['runs']} · scored {s['scored']} · {GREEN}matches {s['match']}{OFF} · "
              f"{RED}false submits {s['false_submit']}{OFF} · false stops {s['false_stop']} · "
              f"unknown {s['unknown']} · pending {s['pending']} · "
              f"{BOLD}consecutive matches {s['streak']}{OFF}, of which matched "
              f"would_submits {s['streak_submits']}")

    mismatches = [m for s in portals.values() for m in s["mismatches"]]
    print(f"\n{BOLD}Mismatches ({len(mismatches)}){OFF}")
    if not mismatches:
        print(f"{DIM}  none{OFF}")
    for m in mismatches:
        row = m["row"]
        label = ("would_submit, but it shouldn't have" if m["result"] == score.FALSE_SUBMIT
                 else "would_stop, but you sent it untouched")
        print(f"  {(row.get('created_at') or '')[:10]}  {row.get('portal')}  "
              f"{RED if m['result'] == score.FALSE_SUBMIT else AMBER}{label}{OFF}")
        print(f"    {DIM}{row.get('application_url')}{OFF}")
        try:
            reasons = json.loads(row.get("reasons_json") or "[]")
        except ValueError:
            reasons = []
        if reasons:
            for r in reasons[:8]:
                print(f"    · {r}")
            if len(reasons) > 8:
                print(f"    {DIM}…and {len(reasons) - 8} more{OFF}")
        else:
            print(f"    {DIM}· the gate found nothing to stop on{OFF}")
        print(f"    {score.describe_outcome(row)}")
    print(f"\n{DIM}Scoring: would_submit is a match only if you submitted with zero edits, "
          f"read at the moment you submitted. would_stop is a match if you changed "
          f"something or didn't submit. A false submit resets the streak; a false stop "
          f"doesn't add to it. Unknown and pending runs don't count.{OFF}")


# ---------------------------------------------------------------------------
#  log commands
# ---------------------------------------------------------------------------
def cmd_list(args) -> None:
    rows = tracker.all_applications(args.status)
    if not rows:
        print("Nothing logged yet.")
        return
    for r in rows:
        label = r["company"] or r["url"][:50]
        print(f"{r['created_at'][:10]}  {r['status']:<10} {(r['ats'] or ''):<14} "
              f"{label[:40]:<42} {DIM}{(r['role'] or '')[:30]}{OFF}")
    print(f"\n{len(rows)} application(s).")


def cmd_followup(args) -> None:
    rows = tracker.awaiting_reply(args.days)
    if not rows:
        print(f"Nothing has been waiting more than {args.days} days.")
        return
    print(f"Submitted {args.days}+ days ago, no update logged:\n")
    for r in rows:
        print(f"  {r['submitted_at'][:10]}  {(r['company'] or r['url'])[:60]}")


def cmd_status(args) -> None:
    tracker.set_status(args.url, args.new_status)
    print(f"{GREEN}Set to '{args.new_status}'.{OFF}")


# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(
        prog="apply", description="Fill a job application, then stop for review.")
    parser.add_argument("--profile", default=None, help="path to profile.yaml")
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init", help="create your profile outside the repo")
    p_init.add_argument("--migrate", action="store_true",
                        help="move an existing profile and documents out of the repo")
    p_init.set_defaults(fn=cmd_init)

    sub.add_parser("doctor", help="check this machine is set up").set_defaults(fn=cmd_doctor)

    p_check = sub.add_parser("check", help="what's still missing from your profile")
    p_check.add_argument("-v", "--verbose", action="store_true")
    p_check.set_defaults(fn=cmd_check)

    p_fill = sub.add_parser("fill", help="fill an application")
    p_fill.add_argument("url")
    p_fill.add_argument("--resume", default=None, help="variant key from profile.yaml")
    p_fill.add_argument("--cover-letter", action="store_true", help="also draft one")
    p_fill.add_argument("--dry-run", action="store_true", help="plan only, touch nothing")
    p_fill.add_argument("--force", action="store_true", help="ignore warnings")
    p_fill.add_argument("--follow", action="store_true",
                        help="follow along: you move through every page and click Submit; "
                             "I fill each page as it appears")
    p_fill.set_defaults(fn=cmd_fill)

    sub.add_parser("learn", help="suggest profile fixes from your logged corrections "
                   "(changes nothing)").set_defaults(fn=cmd_learn)

    sub.add_parser("shadow-report", help="the submit gate's calls scored against yours, "
                   "per portal").set_defaults(fn=cmd_shadow_report)

    p_list = sub.add_parser("list", help="everything you've applied to")
    p_list.add_argument("--status", default=None)
    p_list.set_defaults(fn=cmd_list)

    p_follow = sub.add_parser("followup", help="who hasn't replied")
    p_follow.add_argument("--days", type=int, default=14)
    p_follow.set_defaults(fn=cmd_followup)

    p_status = sub.add_parser("status", help="update an application's status")
    p_status.add_argument("url")
    p_status.add_argument("new_status",
                          choices=["draft", "submitted", "rejected", "interview", "offer"])
    p_status.set_defaults(fn=cmd_status)

    args = parser.parse_args()
    try:
        args.fn(args)
    except KeyboardInterrupt:
        print("\nStopped.")
        sys.exit(130)
    except FileNotFoundError as e:
        print(f"{RED}{e}{OFF}")
        sys.exit(1)


if __name__ == "__main__":
    main()
