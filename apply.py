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
from argparse import Namespace
import json
import re
import unicodedata
import sys
import threading
import time
from pathlib import Path

from agent import (accounts, adapters, browser as browser_mod, classify, config, extract,
                   filler, follow, gate, google, gui, intake, learn, ledger, letters, mapper,
                   outcome, pages, pause, pipeline, report, score, secrets_store, sensitive, shadow,
                   snapshot, template, tracker, wizard)
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
        if getattr(args, "follow", False) and not args.dry_run:
            # Before the first page loads: from then on every document has
            # the activity listener before its own scripts run, so what you
            # touch is marked even before follow mode starts polling.
            ctx.add_init_script(ledger.ACTIVITY_INIT_SCRIPT)
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
        if google.is_google_host(page.url):
            # Google's own site is never filled (Jack, 2026-10-19): sign in
            # there yourself, then run fill again on the application's page.
            print(f"\n{RED}This is Google's own page; the agent fills nothing here.{OFF} Sign in "
                  f"yourself, then run fill again on the application page.")
            return

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


# The page-fill pipeline lives in agent/pipeline.py, shared with the
# unattended wizard; these names stay for the code and tests that use them.
LEFT_OUT_REASON, HELD_AFTER_UPLOAD, HELD_IN_FOLLOW = (
    pipeline.LEFT_OUT_REASON, pipeline.HELD_AFTER_UPLOAD, pipeline.HELD_IN_FOLLOW)
_route, _ask_mapper, _for_model = pipeline.route, pipeline.ask_mapper, pipeline.for_model_copy
_identity, _reclassify_after_upload = pipeline.identity, pipeline.reclassify_after_upload
_fillable_plan, _prefill_census, _build_record = (
    pipeline.fillable_plan, pipeline.prefill_census, pipeline.build_record)
_resume_box = pipeline.resume_box


_WATCH_ENDED = {
    "success": "The portal's confirmation page appeared.",
    "tab_closed": "The tab closed.",
    "timeout": "Stopped watching after 30 minutes.",
    "idle": "No new page and no input from you for 20 minutes.",
    "hard_cap": "Stopped following after 90 minutes.",
}


# ---------------------------------------------------------------------------
#  follow mode: you move through the application; the agent fills each page
# ---------------------------------------------------------------------------
FOLLOW_IDLE_S = 20 * 60        # no new page and no input from you for this long
FOLLOW_CAP_S = 90 * 60         # and never longer than this
FOLLOW_POLL_MS = 500


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
        # The same passive watcher a normal fill installs, armed again on
        # every page followed, so your submit is captured exactly.
        self.capture = outcome.Capture()
        self.seen_fields: list[dict] = []        # every step's fields, for the diff
        self.seen_pages: dict = {}
        self.resume = prof.resume_path(args.resume)
        self.resume_done = False
        self.uploaded: dict[str, list[str]] | None = {}
        self.rows: list[dict] = []
        self.flags: list[dict] = []
        self.steps: list[dict] = []
        self.step_numbers: set[int] = set()
        self.last_shape: dict | None = None

    def _classify(self, fields: list[dict]) -> dict[str, str]:
        """Categories, from the cache or the classifier."""
        fields = [f for f in fields if f.get("type") != "file"]
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
        # Quiet first, so the scan doesn't read the page while you're typing.
        if not self.guard.wait_quiet(page):
            self.events.append({"kind": "never_quiet", "detail": f"step {number}"})
            return
        fields = extract.extract_fields(page, self.read_guard)
        if not fields:
            return
        with self.guard.filling(page):
            fields = extract.enrich_comboboxes(page, fields, contained=True)
        box = None if self.resume_done or not self.resume else _resume_box(fields)
        if box is not None and self.ledger.state(page, box).get("empty"):
            with self.guard.filling(page):
                worked, detail = filler.upload_file(page, box["ref"], self.resume)
            print(f"  {GREEN if worked else RED}{detail}{OFF}")
            self.resume_done = True
            if not worked:
                self.events.append({"kind": "upload_failed", "detail": detail})
            browser_mod.settle(page, 2500)
            fields = extract.extract_fields(page, self.read_guard)
            with self.guard.filling(page):
                # The agent's own clicks opening each dropdown aren't yours.
                fields = extract.enrich_comboboxes(page, fields, contained=True)
            self.uploaded = None            # the baseline across steps isn't tracked yet
        # Only fields read empty, unmarked, untouched by you and not yours,
        # after the scan, go to a model, and never with their help text: a
        # page can echo what you typed (or the resume autofill) into it
        # ("'jo@x' is not a valid email"). Each goes with its help blanked,
        # and one with help text that isn't profile- or human-routed comes
        # to you. A field holding a value, or one you typed in and cleared,
        # is in no model input at all, and comes to you on the review sheet.
        open_refs = self.ledger.open_fields(page, fields)
        for_classifier = [f for f in fields if f["ref"] in open_refs]
        categories = self._classify([_for_model(f, False) for f in for_classifier])
        held = {f["ref"] for f in for_classifier}
        self._note_next_step()
        plan, flags, for_model = _route(for_classifier, categories, self.prof, held,
                                        held_reason=HELD_IN_FOLLOW)
        # Every field left out that the agent didn't fill comes to you, so
        # the review sheet shows it.
        for f in fields:
            why = self.ledger.left_out.get(f["ref"])
            if why in LEFT_OUT_REASON and not extract.is_secret(f):
                flags.append({"ref": f["ref"], "label": f.get("label"),
                              "options": f.get("options"), "action": "human",
                              "value": None, "source": "human", "category": "unknown",
                              "reason": LEFT_OUT_REASON[why]})
        _ask_mapper(for_model, self.prof, self.posting_text, held, plan)
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
        # Arm the capture over every step's fields, so an edit you make on
        # any page the agent filled is seen against what it put there.
        self.seen_fields = [f for f in self.seen_fields
                            if f["ref"] not in {g["ref"] for g in fields}] + list(fields)
        self.capture.arm(page, self.seen_fields)
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
        while True:
            if page.is_closed():
                return "tab_closed", detection
            # Every poll, so a newly loaded page (any page: refused ones,
            # fieldless ones, a reload) has the listener within one poll:
            # what you touch is marked and the idle ending works there.
            self.guard.install(page)
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
            # What you touched, by identity, before a re-mount or a new page
            # can drop it (booleans and identity parts only, never a value).
            self.ledger.sweep(page)
            # And what the form holds now, while its fields are still there.
            self.capture.poll(page)
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
    # A record an earlier run left in this tab isn't this run's.
    ledger.clear_touch_record(page)
    # You open the form first, as in a normal fill: nothing is filled
    # before this, so nothing on the posting page itself is touched.
    print(f"\n{BOLD}Click Apply if the form isn't open, then press Enter.{OFF}")
    input()
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
    if ended != "enter":
        # The Enter watcher is still reading the terminal; let it take its
        # line, so your y/n below isn't swallowed by it.
        print("Press Enter to continue.")
        enter.wait()
    answer = _ask_submitted()
    if answer == "y":
        tracker.mark_submitted(url)
        print(f"{GREEN}Logged.{OFF}")
    watched = {"ended": ended, "detection": detection, **run.capture.watched()}
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
    # Optional fields left empty (Jack, 2026-10-20): listed, not stopped on.
    for reason in (result.get("waived") or [])[:15]:
        print(f"  {DIM}· optional, left empty (not a stop): {reason}{OFF}")
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
        if row["filled"] and not row["needs_review"] and not row.get("unconfirmed"):
            mark = f"{GREEN}done  {OFF}"
        elif row["filled"]:
            mark = f"{AMBER}check {OFF}"
        elif row.get("value") is None:
            # No answer in the profile: nothing went wrong, it's yours.
            mark = f"{DIM}for you{OFF}"
        else:
            mark = f"{RED}failed{OFF}"
        # A missing answer prints as nothing, never as the word "None",
        # which reads like one.
        shown = "" if row.get("value") is None else str(row["value"])[:45]
        print(f"  {mark} {str(row['label'])[:52]:<54} {DIM}{shown}{OFF}")
        if not row["filled"] and row["detail"] != "skipped — no value":
            why = f"[{row['reason']}] " if row.get("reason") else ""
            print(f"         {RED}{why}{row['detail']}{OFF}")
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
#  run — a link in, an application out (milestones 2-7 of the autonomy plan)
# ---------------------------------------------------------------------------
NEEDS_YOU, FAILED, DRY = "needs_you", "failed", "dry"


def read_queue(path: Path) -> list[str]:
    """One link per line; blank lines and # comments skipped; duplicates dropped."""
    links: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line not in links:
            links.append(line)
    return links


def _park(url: str, reason: str, *, company: str = "", role: str = "", ats: str = "",
          status: str = NEEDS_YOU) -> None:
    """Log a link the agent stopped on, with the reason, so needs-you lists it."""
    tracker.record(url, company=company, role=role, ats=ats, status=status, notes=reason)


def dry_report(landing: "intake.Landing", ats: str, role: str = "", company: str = "") -> tuple[str, bool, int]:
    """
    What a dry run says about a link: the final address, the portal, the
    role and company read off the page's title (Jack, 2026-10-20: so a
    public-board lookup can be checked before a real run), and whether a
    form with fields is there (two or more fields, or a file box; the
    scan's count beside it). The scan reads structure only; nothing is
    filled or clicked here.
    """
    page = landing.page
    found, count = False, 0
    if page is not None and not page.is_closed():
        found = intake.has_form_fields(page)
        try:
            count = len(extract.extract_fields(page, extract.ReadGuard()))
        except Exception:
            count = 0
    where = landing.url if landing.page is not None else "(the link didn't open)"
    tail = f"; parked: {landing.parked}" if landing.parked else ""
    steps = f"; {'; '.join(landing.steps)}" if landing.steps else "; nothing clicked"
    who = (f"; role '{_clean_title(role)}' at '{_clean_title(company)}'" if (role or company) else
           "; role and company: not read")
    wd = (sorted(accounts.workday_controls(page))
          if ats == adapters.CATALOG["workday"].name and page is not None and not page.is_closed() else [])
    if wd:
        who += f"; Workday account controls: {', '.join(wd)}"
    text = (f"dry run: landed on {where}; portal {ats} (detected by {landing.portal_method}){who}; "
            f"form with fields: {'yes' if found else 'no'} ({count} field(s) scanned){steps}{tail}")
    return text, found, count


def _clean_title(text: str) -> str:
    """Page-derived text for the terminal: no control, escape or format characters, capped."""
    cleaned = "".join(" " if unicodedata.category(c) == "Cf" else c for c in str(text or ""))
    return re.sub(r"[\x00-\x1f\x7f-\x9f]", " ", cleaned)[:80]


def run_one(ctx, url: str, prof, args, pauser: "pause.Pauser | None" = None, *,
            new_tab: bool = False, report_to=None, on_pause=None, on_wait=None) -> tuple[str, str]:
    """
    One link, end to end: open it, click Apply, follow to the form, detect
    the portal, then the wizard (accounts, every page, the gate, Submit).
    Returns (status, reason); everything is logged in the tracker. With
    `args.dry`, intake only: the landing is reported and nothing is
    filled, signed in to, or submitted, and the tracker isn't written.
    `report_to(dict)`, if given, is told the landing (company, role,
    portal, final URL, and for a dry run whether a form was found).
    `on_pause(info)` and `on_wait(wizard, finish)` serve GUI mode: a
    deferred pause tells the page what to show, and the waiting Wizard is
    kept to be resumed when Jack presses Continue.
    """
    pauser = pauser or pause.Pauser()
    landing = intake.open_link(ctx, url, new_tab=new_tab)
    for step in landing.steps:
        print(f"{DIM}  {step}{OFF}")
    page = landing.page
    role = company = ""
    if page is not None:
        try:
            role, company = extract.guess_role_and_company(page)
        except Exception:
            pass
    ats = adapters.CATALOG.get(landing.portal, adapters.CATALOG["generic"]).name
    if getattr(args, "dry", False):
        text, found, _ = dry_report(landing, ats, role=role, company=company)
        print(f"  {text}")
        if report_to is not None:
            report_to({"company": company, "role": role, "portal": ats,
                       "final_url": landing.url if page is not None else "", "form_found": found})
        return DRY, text
    if report_to is not None:
        report_to({"company": company, "role": role, "portal": ats,
                   "final_url": landing.url if page is not None else ""})
    if landing.parked:
        if on_wait is not None and page is not None and not page.is_closed():
            # GUI mode (Jack, 2026-10-26): the park waits in its tab with
            # Continue, which starts the wizard on whatever the tab shows
            # then (a form Jack opened himself, or the same refusal again).
            # A link that didn't open has no tab, so nothing to resume.
            portal, _, method = adapters.detect_with_method(page.url, page)
            finish = {"url": url, "company": company, "role": role, "ats": ats,
                      "portal": portal, "portal_method": method,
                      "resume": str(prof.resume_path(args.resume) or "")}
            w = wizard.Wizard(page, prof, url=url, portal=portal, portal_method=method,
                              company=company, role=role, resume=prof.resume_path(args.resume),
                              posting_text=extract.page_text(page, 6000), pauser=pauser, echo=print,
                              reload_profile=lambda: Profile.load(args.profile), on_pause=on_pause)
            return finish_outcome(w.park_for_continue(landing.parked), finish, on_wait=on_wait)
        _park(url, landing.parked, company=company, role=role, ats=ats)
        return NEEDS_YOU, landing.parked
    print(f"  {BOLD}{ats}{OFF} {DIM}({landing.portal_method}) at {landing.url[:90]}{OFF}")
    # The posting's text, for the mapper, taken once before anything is
    # typed, as `fill` takes it.
    posting_text = extract.page_text(page, 6000)
    try:
        submitted_today = tracker.agent_submissions_today()
    except Exception:
        submitted_today = None
    finish = {"url": url, "company": company, "role": role, "ats": ats,
              "portal": landing.portal, "portal_method": landing.portal_method,
              "resume": str(prof.resume_path(args.resume) or "")}
    out = wizard.run_application(
        page, prof, url=url, portal=landing.portal, portal_method=landing.portal_method,
        company=company, role=role, resume=prof.resume_path(args.resume),
        posting_text=posting_text, pauser=pauser, echo=print,
        agent_submissions_today=submitted_today,
        reload_profile=lambda: Profile.load(args.profile), on_pause=on_pause)
    return finish_outcome(out, finish, on_wait=on_wait)


def finish_outcome(out, finish: dict, *, on_wait=None) -> tuple[str, str]:
    """
    What happens once the wizard returns: a waiting job (GUI mode) is
    logged as needs-you with its reason and handed to `on_wait` with its
    Wizard, to be resumed later; anything else gets the review sheet, the
    tracker row, the gate-run row and the evidence.
    """
    url, company, role, ats = finish["url"], finish["company"], finish["role"], finish["ats"]
    for line in out.log:
        print(f"{DIM}  {line}{OFF}")
    if out.status == wizard.WAITING:
        tracker.record(url, company=company, role=role, ats=ats, resume_used=finish["resume"],
                       answers=out.rows, flags=out.flags, status=NEEDS_YOU, notes=out.reason)
        if on_wait is not None and out.wizard is not None:
            on_wait(out.wizard, finish)
        return NEEDS_YOU, out.reason
    _print_summary(out.rows, out.flags)
    sheet = None
    try:
        sheet = report.build(url=url, ats_name=ats, report_rows=out.rows, flags=out.flags,
                             company=company, role=role)
    except Exception as e:
        print(f"{DIM}  couldn't write the review sheet ({type(e).__name__}){OFF}")
    status = out.status if out.status in ("submitted", NEEDS_YOU, FAILED) else NEEDS_YOU
    tracker.record(url, company=company, role=role, ats=ats,
                   resume_used=finish["resume"], answers=out.rows,
                   flags=out.flags, report_path=str(sheet or ""), status=status, notes=out.reason)
    if out.record is not None and out.gate_result is not None:
        try:
            run_id = tracker.record_gate_run(
                url, portal=finish["portal"], portal_method=finish["portal_method"],
                result=out.gate_result, record_json=shadow.redacted(out.record, out.gate_result),
                agent_submitted=(status == "submitted"))
            if status == "submitted":
                tracker.set_gate_outcome(run_id, human_submitted="unknown", edits=[],
                                         capture="none",
                                         success_signal=json.dumps(
                                             {"ended": "agent_submit", "detected":
                                              out.evidence.get("portal_signal"),
                                              "signals": out.evidence.get("signals"),
                                              "url_shape": out.evidence.get("url_shape"),
                                              "answer": None, "hooked": False}))
        except Exception as e:
            print(f"{DIM}  couldn't log the gate's verdict ({type(e).__name__}){OFF}")
    try:
        tracker.set_evidence(url, out.evidence, agent_submitted=(status == "submitted"))
    except Exception as e:
        print(f"{DIM}  couldn't store the evidence ({type(e).__name__}){OFF}")
    return status, out.reason


def cmd_run(args) -> None:
    prof = Profile.load(args.profile)
    links = list(args.urls or [])
    if args.queue:
        for link in read_queue(Path(args.queue).expanduser()):
            if link not in links:
                links.append(link)
    if not links:
        print(f"{RED}No links given.{OFF} Pass one or more URLs, or --queue FILE.")
        return
    dry = bool(getattr(args, "dry", False))
    if not dry:
        if not _ready_to_fill(prof, args.force):
            return
    results: list[tuple[str, str, str]] = []
    pauser = pause.Pauser()
    with browser_mod.browser() as ctx:
        # What Jack touches at a pause is marked by the activity listener,
        # so an answer of his can be told from one the page filled in.
        ctx.add_init_script(ledger.ACTIVITY_INIT_SCRIPT)
        for url in links:
            print(f"\n{BOLD}{url[:100]}{OFF}")
            existing = tracker.find(url)
            if existing and not args.force and not dry and existing["status"] not in (NEEDS_YOU, "draft"):
                print(f"{AMBER}Already logged: status '{existing['status']}', "
                      f"{existing['created_at'][:10]}.{OFF} {DIM}Add --force to do it again.{OFF}")
                results.append((url, "skipped", existing["status"]))
                continue
            try:
                status, reason = run_one(ctx, url, prof, args, pauser)
            except Exception as e:
                status, reason = FAILED, f"{type(e).__name__}: {str(e)[:120]}"
                _park(url, reason, status=FAILED)
            colour = (GREEN if status == "submitted" else AMBER if status == NEEDS_YOU
                      else DIM if status == DRY else RED)
            print(f"  {colour}{status}{OFF} {DIM}{reason if status != DRY else ''}{OFF}")
            results.append((url, status, reason))
    summary = ", ".join(f"{s} {sum(1 for _, st, _ in results if st == s)}"
                        for s in dict.fromkeys(st for _, st, _ in results))
    print(f"\n{BOLD}{len(results)} link(s):{OFF} {summary}")
    # One notification at the end of the batch, with the counts.
    try:
        pauser.notify_fn("job-agent: batch done", f"{len(results)} link(s): {summary}", echo=print)
    except Exception:
        pass


def _ready_to_fill(prof, force: bool, *, exit_on_key: bool = True) -> bool:
    """
    The profile and the API key, checked once before anything is filled.
    `run` exits on a bad key; the GUI (`exit_on_key=False`) keeps its
    browser and page up and fails the job instead.
    """
    blocking = prof.audit()["required"]
    if blocking and not force:
        print(f"{RED}{len(blocking)} required profile field(s) are blank.{OFF} "
              f"Run `python apply.py check` first, or --force past this.")
        return False
    status, detail = config.check_api_key()
    if status in ("rejected", "missing"):
        print(f"{RED}Anthropic API key {'rejected' if status == 'rejected' else 'not found'}"
              f"{OFF} ({detail}). Set a new ANTHROPIC_API_KEY and try again.")
        if exit_on_key:
            sys.exit(1)
        return False
    return True


# ---------------------------------------------------------------------------
#  gui — paste links into a page; rows show where each one is
# ---------------------------------------------------------------------------
GUI_POLL_S = 0.5


def gui_loop(ctx, state: "gui.State", prof, args, pauser, *, run=None, clock=time.monotonic,
             idle_limit_s: float | None = None, waiting: dict | None = None) -> None:
    """
    The queue loop behind `apply.py gui`, on the main thread (Playwright's):
    take queued jobs from the GUI one at a time, each in its own tab; a job
    that needs Jack is deferred (its Wizard kept in `waiting`, its tab left
    open and filled) and resumed, in that tab, when Jack presses Continue
    in the GUI; the queue carries on meanwhile. No timeout. Ends when the
    page asks to close the browser, or after `idle_limit_s` with nothing
    queued or resumable (tests).
    """
    run = run or run_one
    waiting = {} if waiting is None else waiting
    last_busy = clock()
    while True:
        if state.close_browser:
            return
        if state.google_login:
            state.google_login = False
            # A tab on Google's sign-in site, for Jack's hands only: nothing
            # is typed, read or pressed there by the agent.
            try:
                google.open_login_tab(ctx)
                state.notice = ("a Google sign-in tab is open: sign in there yourself; the agent "
                                "touches nothing on it")
            except Exception as e:
                state.notice = f"couldn't open the Google sign-in tab ({type(e).__name__})"
        for job_id in state.resumable():
            info = state.take_resume(job_id)
            kept = waiting.pop(job_id, None)
            if kept is None:
                state.update(job_id, status=FAILED, reason="nothing to resume for this job")
                continue
            wiz, finish = kept
            try:
                status, reason = finish_outcome(
                    wiz.continue_from(info), finish,
                    on_wait=lambda w, f, j=job_id: waiting.__setitem__(j, (w, f)))
            except Exception as e:
                status, reason = FAILED, f"{type(e).__name__}: {str(e)[:120]}"
                _park(finish["url"], reason, status=FAILED)
            state.update(job_id, status=status, reason=reason)
            last_busy = clock()
        job = state.next_queued()
        if job is None:
            if idle_limit_s is not None and clock() - last_busy > idle_limit_s:
                return
            time.sleep(GUI_POLL_S)
            continue
        last_busy = clock()
        existing = tracker.find(job.url)
        if (existing and not job.dry and not getattr(args, "force", False)
                and existing["status"] not in (NEEDS_YOU, "draft")):
            # As `run` does: a link already sent isn't applied to twice.
            state.update(job.id, status="skipped",
                         reason=f"already logged: {existing['status']} on "
                                f"{(existing['created_at'] or '')[:10]}; use --force to redo")
            continue
        job_args = Namespace(profile=args.profile, resume=getattr(args, "resume", None),
                             force=bool(getattr(args, "force", False)),
                             dry=job.dry or bool(getattr(args, "dry", False)))
        job_pauser = pauser if job.dry else pause.GuiPauser(notify_fn=getattr(pauser, "notify_fn", None))
        try:
            status, reason = run(
                ctx, job.url, prof, job_args, job_pauser, new_tab=True,
                report_to=lambda facts, j=job: state.update(j.id, **facts),
                on_pause=lambda info, j=job: state.set_pause(
                    j.id, info.get("kind", ""), info.get("message", ""), info.get("fields") or [],
                    wants_code=bool(info.get("wants_code"))),
                on_wait=lambda w, f, j=job: waiting.__setitem__(j.id, (w, f)))
        except Exception as e:
            status, reason = FAILED, f"{type(e).__name__}: {str(e)[:120]}"
            if not job.dry:
                _park(job.url, reason, status=FAILED)
        if job.id not in waiting:
            state.update(job.id, status=status, reason=reason)
        last_busy = clock()


def cmd_google_login(args) -> None:
    """
    Open the agent's own Chrome profile at accounts.google.com and wait for
    Jack to sign in by hand. The agent types nothing, reads nothing from the
    page and presses nothing: the session Google leaves in the profile is
    what later "Continue with Google" buttons use (Jack, 2026-10-19).
    """
    print(f"\n{BOLD}Google sign-in{OFF}")
    print(f"{DIM}A browser window opens on Google's sign-in page, in the agent's own profile "
          f"({config.CHROME_PROFILE_DIR}). Sign in there yourself. The agent touches nothing on "
          f"that page and reads nothing from it.{OFF}")
    with browser_mod.browser() as ctx:
        google.open_login_tab(ctx)
        print("Press Enter here when you're signed in (or to give up).")
        input()
    print(f"{GREEN}Done.{OFF} {DIM}Whatever Google saved in the profile stays on this machine.{OFF}")


def cmd_passwords(args) -> None:
    """
    The hosts with a password in Windows Credential Manager (hosts only,
    never a value), or forget one / all of them (Jack, 2026-10-20).
    """
    if args.forget_all:
        n = secrets_store.forget_all()
        print(f"{GREEN}Forgot {n} stored password(s).{OFF}")
        return
    if args.forget:
        had = secrets_store.forget(args.forget)
        print(f"{GREEN}Forgot the password for {args.forget.lower()}.{OFF}" if had
              else f"{DIM}No stored password for {args.forget.lower()}.{OFF}")
        return
    names = secrets_store.hosts()
    print(f"\n{BOLD}Stored portal passwords{OFF} {DIM}({secrets_store.backend_name()}){OFF}")
    if not names:
        print(f"{DIM}none{OFF}")
    for host in names:
        print(f"  {host}")
    print(f"{DIM}Values are never shown. `apply.py passwords --forget <host>` or `--forget-all` deletes them.{OFF}")


def cmd_snapshot(args) -> None:
    """
    A structure dump of the page the agent's browser shows, for building
    mocks that match a real portal (Jack, 2026-10-20): the agent's own
    Chrome profile opens, you bring up the page (signing in if you need
    to), press Enter here, and the page's tag / type / role / aria-* /
    data-automation-id / name / id / label / required / visibility and
    container chain of every control go to snapshots/<name>-<host>.json
    in the repo folder (gitignored). No input value is read, nothing from
    the header / account area is kept, and every string holding a value
    from your profile (loaded here, at run time) is replaced by "[profile]"
    before anything is written.
    """
    prof = Profile.load(args.profile)
    print(f"\n{BOLD}Page snapshot{OFF}")
    print(f"{DIM}A browser window opens in the agent's own profile ({config.CHROME_PROFILE_DIR}). Bring up "
          f"the page you want a snapshot of, in that window's last tab, then press Enter here. The dump "
          f"holds structure only: no input values, nothing from the header, nothing equal to a profile "
          f"value.{OFF}")
    with browser_mod.browser() as ctx:
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        if args.url:
            try:
                page.goto(args.url, wait_until="domcontentloaded", timeout=60_000)
            except Exception as e:
                print(f"{RED}Couldn't open {args.url} ({type(e).__name__}); open the page yourself.{OFF}")
        print("Press Enter here when the page you want is showing (or to give up).")
        input()
        target = ctx.pages[-1] if ctx.pages else page
        if google.is_google_host(target.url):
            print(f"{RED}That tab is on Google's site: nothing there is dumped.{OFF}")
            return
        snap = snapshot.dump(target, prof.data)
        path = snapshot.write(snap, args.name)
    print(f"{GREEN}Wrote {path}{OFF} {DIM}({len(snap['elements'])} elements of {snap['total_matched']} matched; "
          f"profile values replaced by {snapshot.REDACTED}).{OFF}")


def cmd_gui(args) -> None:
    import webbrowser

    prof = Profile.load(args.profile)
    state = gui.State()
    server = gui.Server(state).start()
    print(f"\n{BOLD}GUI:{OFF} {server.url}")
    print(f"{DIM}Paste links there. A job that needs you waits in its tab until you press "
          f"Continue; close the browser from the page.{OFF}")
    try:
        webbrowser.open(server.url)
    except Exception:
        pass
    pauser = pause.Pauser()
    try:
        with browser_mod.browser() as ctx:
            ctx.add_init_script(ledger.ACTIVITY_INIT_SCRIPT)
            state.browser_open = True
            # The profile and key are needed only once something is filled;
            # a page of dry runs needs neither.
            checked = {"ok": None}

            def run_checked(ctx, url, prof, job_args, pauser, **kw):
                if not job_args.dry and checked["ok"] is None:
                    checked["ok"] = _ready_to_fill(prof, bool(args.force), exit_on_key=False)
                if not job_args.dry and not checked["ok"]:
                    return FAILED, "the profile isn't ready (see the terminal)"
                return run_one(ctx, url, prof, job_args, pauser, **kw)
            gui_loop(ctx, state, prof, args, pauser, run=run_checked)
            state.browser_open = False
    finally:
        server.stop()
    print(f"{DIM}Browser closed.{OFF}")


def cmd_applied(args) -> None:
    """Every link `run` or `fill` has logged: company, role, URL, date, portal, status."""
    rows = tracker.all_applications()
    if not rows:
        print("Nothing logged yet.")
        return
    for r in rows:
        colour = GREEN if r["status"] == "submitted" else AMBER if r["status"] in (NEEDS_YOU, "draft") \
            else RED if r["status"] == FAILED else ""
        who = " (agent)" if r.get("agent_submitted") else ""
        print(f"{r['created_at'][:10]}  {colour}{r['status']:<10}{OFF if colour else ''}{who:<8} "
              f"{(r['ats'] or ''):<14} {(r['company'] or '')[:30]:<32} {(r['role'] or '')[:30]:<32}")
        print(f"            {DIM}{r['url'][:110]}{OFF}")
        if r["status"] in (NEEDS_YOU, FAILED) and r.get("notes"):
            print(f"            {DIM}{_clean_line(r['notes'])}{OFF}")
    print(f"\n{len(rows)} application(s).")


def cmd_needs_you(args) -> None:
    """The parked jobs: what stopped each one, oldest first."""
    rows = [r for r in tracker.all_applications() if r["status"] in (NEEDS_YOU, FAILED)]
    if not rows:
        print("Nothing is waiting for you.")
        return
    for r in sorted(rows, key=lambda r: r["created_at"]):
        print(f"\n{AMBER if r['status'] == NEEDS_YOU else RED}{r['status']}{OFF}  "
              f"{r['created_at'][:10]}  {(r['company'] or '')[:40]}  {DIM}{(r['role'] or '')[:40]}{OFF}")
        print(f"  {r['url'][:110]}")
        print(f"  {_clean_line(r.get('notes') or '(no reason recorded)')}")
    print(f"\n{len(rows)} waiting. When you've finished one in the browser: "
          f"`python apply.py status <url> submitted`; to retry it: `python apply.py run <url> --force`.")


def _clean_line(text: str) -> str:
    """Page-derived text for the terminal: no control or escape characters."""
    return re.sub(r"[\x00-\x1f\x7f-\x9f]", " ", str(text or ""))[:300]


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

    p_run = sub.add_parser("run", help="apply to each link end to end, pausing only "
                                       "where the owner decisions say so")
    p_run.add_argument("urls", nargs="*", help="job links (any Apply button's page)")
    p_run.add_argument("--queue", default=None, help="a file of links, one per line")
    p_run.add_argument("--resume", default=None, help="variant key from profile.yaml")
    p_run.add_argument("--force", action="store_true",
                       help="redo links already logged; ignore profile warnings")
    p_run.add_argument("--dry", action="store_true",
                       help="intake only: open, click Apply, follow, report the landing; "
                            "fill nothing, sign in to nothing, submit nothing")
    p_run.set_defaults(fn=cmd_run)

    p_gui = sub.add_parser("gui", help="a local page to paste links into and watch each one")
    p_gui.add_argument("--resume", default=None, help="variant key from profile.yaml")
    p_gui.add_argument("--dry", action="store_true", help="every job a dry run")
    p_gui.add_argument("--force", action="store_true",
                       help="redo links already logged; ignore profile warnings")
    p_gui.set_defaults(fn=cmd_gui)

    sub.add_parser("google-login", help="open the agent's browser at Google's sign-in page "
                                        "for you to sign in by hand").set_defaults(fn=cmd_google_login)

    p_pw = sub.add_parser("passwords", help="the hosts with a stored portal password (never the values); "
                                            "--forget <host> or --forget-all deletes them")
    p_pw.add_argument("--forget", default=None, metavar="HOST")
    p_pw.add_argument("--forget-all", action="store_true")
    p_pw.set_defaults(fn=cmd_passwords)

    p_snap = sub.add_parser("snapshot", help="dump the structure of the page the agent's browser is on "
                                             "to snapshots/ (no values, no profile data)")
    p_snap.add_argument("url", nargs="?", default=None, help="a page to open first (optional)")
    p_snap.add_argument("--name", default=None, help="a name for the file (default: a timestamp)")
    p_snap.set_defaults(fn=cmd_snapshot)

    sub.add_parser("applied", help="every link run has logged, with its status"
                   ).set_defaults(fn=cmd_applied)
    sub.add_parser("needs-you", help="the parked jobs and why each one stopped"
                   ).set_defaults(fn=cmd_needs_you)

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
                          choices=["draft", "submitted", "rejected", "interview", "offer",
                                   "needs_you", "failed"])
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
