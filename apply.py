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
import sys
from pathlib import Path

from agent import (adapters, browser as browser_mod, classify, config, extract,
                   filler, letters, mapper, report, sensitive, tracker)
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

    if not config.TEMPLATE_PATH.exists():
        print(f"{RED}Template missing:{OFF} {config.TEMPLATE_PATH}")
        return

    shutil.copy(config.TEMPLATE_PATH, target)
    print(f"  {GREEN}created{OFF} {target}")
    print(f"  {GREEN}created{OFF} {docs}{DIM}  <- put your resume here{OFF}")
    print(f"\nNext: open the profile and fill it in, drop your resume PDF in the")
    print(f"documents folder, then run `python apply.py check`.")


# ---------------------------------------------------------------------------
#  doctor — is this machine set up correctly?
# ---------------------------------------------------------------------------
def cmd_doctor(args) -> None:
    import os
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

    line(bool(os.environ.get("ANTHROPIC_API_KEY")), "ANTHROPIC_API_KEY set",
         "" if os.environ.get("ANTHROPIC_API_KEY") else "export it in your shell profile")

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
        line(resume is not None, "resume file found", str(resume or "put a PDF in documents/"))
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
    print(f"{BOLD}Resume:{OFF}  {resume or RED + 'not found — put a PDF in documents/' + OFF}\n")

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

    with browser_mod.browser() as ctx:
        page = browser_mod.open_page(ctx, url)

        key, ats = adapters.detect(url, page)
        print(f"\n{BOLD}{ats.name}{OFF} {DIM}({ats.difficulty}){OFF}")
        print(f"{DIM}{ats.summary}{OFF}")
        for quirk in ats.quirks:
            print(f"{DIM}  · {quirk}{OFF}")

        # -- pause for anything we refuse to handle automatically ------------
        if found := browser_mod.detect_captcha(page):
            print(f"\n{AMBER}{found} on the page.{OFF} Solve it in the browser window, "
                  f"then press Enter here.")
            input()
            browser_mod.settle(page)

        if browser_mod.detect_login_wall(page):
            print(f"\n{AMBER}This portal wants a login.{OFF}")
            print("Sign in yourself in the browser window. The session is saved to")
            print(f"{DIM}{config.CHROME_PROFILE_DIR}{OFF} and reused from now on.")
            print("Press Enter when you're in.")
            input()
            browser_mod.settle(page)

        adapters.prepare(page, key)

        role, company = extract.guess_role_and_company(page)

        # A multi-step wizard is filled one page at a time. Each pass scans,
        # classifies, fills, then looks for a Continue button — never a Submit.
        all_rows: list[dict] = []
        all_flags: list[dict] = []
        resume = prof.resume_path(args.resume)
        max_steps = 1 if args.dry_run else args.max_steps

        for step in range(1, max_steps + 1):
            if step > 1:
                print(f"\n{BOLD}Step {step}{OFF}")

            fields = extract.extract_fields(page)
            if not fields:
                if step == 1:
                    print(f"\n{RED}No form fields found.{OFF} The form is probably "
                          f"behind another click, or inside an iframe.")
                    return
                break

            # Open custom dropdowns to read their choices — otherwise they
            # arrive with no options and can't be matched to your profile.
            fields = extract.enrich_comboboxes(page, fields)
            print(f"\n{len(fields)} field(s) on the page.")

            # 1. Resume first: portals autofill from it and would overwrite us.
            uploads = [f for f in fields if f.get("type") == "file"]
            if step == 1 and uploads and resume and not args.dry_run:
                worked, detail = filler.upload_file(page, uploads[0]["ref"], resume)
                print(f"  {GREEN if worked else RED}{detail}{OFF}")
                browser_mod.settle(page, 2500)
                fields = extract.enrich_comboboxes(
                    page, extract.extract_fields(page))

            # 2. Ask the model what KIND of question each field is. It never
            #    sees your data here and never answers anything.
            categories = classify.classify_fields(
                [f for f in fields if f.get("type") != "file"])

            # 3. Apply the policy. Sensitive fields never reach the mapper.
            plan: dict[str, dict] = {}
            flags: list[dict] = []
            for_model: list[dict] = []

            for field in fields:
                if field.get("type") == "file":
                    continue
                verdict = sensitive.decide(
                    field, categories.get(field["ref"], "unknown"), prof)
                if verdict["action"] == "human":
                    flags.append(verdict)
                elif verdict["action"] == "fill":
                    plan[field["ref"]] = verdict
                else:
                    field["_draft"] = verdict["action"] == "draft"
                    for_model.append(field)

            counts = {}
            for f in fields:
                c = categories.get(f["ref"], "file")
                counts[c] = counts.get(c, 0) + 1
            print(f"{DIM}  {', '.join(f'{v} {k}' for k, v in sorted(counts.items()))}{OFF}")
            print(f"{DIM}  {len(plan)} from profile · {len(flags)} for you "
                  f"· {len(for_model)} to the model{OFF}")

            # 4. The mapper answers only what's left.
            if for_model:
                mapped = mapper.map_fields(for_model, prof, extract.page_text(page))
                for ref, decision in mapped.items():
                    if next((f for f in for_model
                             if f["ref"] == ref and f.get("_draft")), None):
                        decision["needs_review"] = True
                    plan[ref] = decision

            if args.dry_run:
                _print_dry_run(fields, plan, flags)
                return

            all_rows += filler.run_plan(page, fields, plan)
            all_flags += flags
            _print_summary(all_rows[-len(plan):], flags)

            if step >= max_steps:
                break
            if flags:
                print(f"\n{AMBER}Stopping here — {len(flags)} question(s) need you "
                      f"before this page is complete.{OFF}")
                break
            if not adapters.next_step(page, key):
                break
            print(f"{DIM}  advanced to the next step{OFF}")

        rows, flags = all_rows, all_flags

        # -- 5. optional cover letter ----------------------------------------
        if args.cover_letter:
            print(f"\n{DIM}Drafting a cover letter…{OFF}")
            try:
                text = letters.draft_cover_letter(
                    prof, extract.page_text(page, 6000), company, role)
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
        if all_flags:
            print(f"{AMBER}{len(all_flags)} question(s) are waiting for you — see "
                  f"the top of the review sheet.{OFF}")
        print(f"\n{DIM}Press Enter when you've finished with the form.{OFF}")
        input()

        if input("Mark as submitted? [y/N] ").strip().lower().startswith("y"):
            tracker.mark_submitted(url)
            print(f"{GREEN}Logged.{OFF}")


def _print_dry_run(fields, plan, flags) -> None:
    print(f"\n{BOLD}Dry run — nothing was typed.{OFF}\n")
    by_ref = {f["ref"]: f for f in fields}
    for ref, decision in plan.items():
        label = str(by_ref.get(ref, {}).get("label", "?"))[:52]
        value = str(decision.get("value"))[:55]
        mark = f"{AMBER}review{OFF}" if decision.get("needs_review") else f"{GREEN}ok{OFF}"
        print(f"  [{mark}] {label:<54} {DIM}{value}{OFF}")
        if decision.get("note"):
            print(f"           {DIM}{decision['note']}{OFF}")
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
    p_fill.add_argument("--max-steps", type=int, default=6,
                        help="how many wizard pages to walk (default 6)")
    p_fill.set_defaults(fn=cmd_fill)

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
