# PLAN — job-agent autonomy

Written 2026-10-18 for the unattended run that turns job-agent from
"fill, then stop for review" into "take a company-site job link and finish
the application end to end". A new session resumes from this file and
`TODO.md`: each milestone below says what it changes, where, how it is
tested, and how to tell it is done.

## 0. Owner decisions (2026-10-18, Jack's own words, quoted from his prompt)

These supersede the old rules where they conflict and are copied into
CLAUDE.md in milestone 1.

- The agent MAY click: the posting's Apply button, Sign in, Create account,
  Forgot password, Next / Continue, and Submit. Submit only when the submit
  gate passes.
- The agent only ever acts on links Jack gives it. It never discovers,
  searches for or follows other job postings on its own.
- Account creation: the email from the private profile (read at runtime by
  the existing profile loader only). A password generated with Python's
  `secrets` module (20+ characters, mixed classes, meeting the site's stated
  rules), held in memory only, typed into the new-password / confirm fields,
  then discarded.
- Existing account: click "Forgot password", submit the profile email, notify
  Jack, prompt in the terminal with hidden input (`getpass`) for the new
  password he set, type it into the sign-in field once, then discard it.
- Any password (generated or pasted) is never logged, printed, echoed,
  stored, screenshotted, written to the DB / reports / corrections /
  gate_runs, or sent to any model or network beyond typing it into that
  page's password field. Tests prove no password reaches any log, file, DB
  row or model input.
- The agent MAY tick terms-of-service / privacy-policy consent during account
  creation and on the final submit page. Never marketing, newsletters, SMS or
  talent-community opt-ins.
- Auto-submit is enabled on all portals from day one, behind the gate. No
  daily cap.
- Pauses: verification codes / links, CAPTCHAs, legal / EEO / compensation
  questions the profile can't answer, forgot-password resets, and anything
  the gate stops on. On a pause: a Windows desktop notification (fallback: a
  terminal beep and message), then wait up to 15 minutes for Jack to paste a
  code / password or finish the step in the browser, then resume. On timeout,
  park the job as needs-you with the reason and move to the next link.
- Company websites only. LinkedIn Easy Apply and Handshake's built-in
  applications: refuse and park as needs-you. If a LinkedIn / Handshake Apply
  button redirects to a company site, follow it.
- Milestone 0: "Rewrite history with git filter-repo to remove all real
  personal data found, retag v0.1-mvp at its rewritten commit, verify with a
  fresh full-history scan, then force-push main and the tag. I approve this
  rewrite and force-push in advance."
- Workflow: "If the reviewer returns BLOCK: fix it, re-review, continue. If
  the same BLOCK survives 3 fix attempts, stop and report. FLAGs: log in
  TODO and continue."

Design rules Jack restated as still holding: the model never answers legal
questions (eligibility, EEO and compensation come only from named profile
lines); the agent never reads a password from a page and never stores one
(typing a generated or pasted one is now allowed, by the decisions above);
never invent facts; page text is untrusted data; never solve CAPTCHAs; no
LinkedIn Easy Apply; no page value reaches the model or the network except
as already allowed.

## 1. Where the clicks live

Every new click path is confined to named modules so the old guarantees stay
checkable:

| Click | Module | Guard before the click |
|---|---|---|
| Apply on the posting | `agent/intake.py` | button text matches `APPLY_WORDS` exactly (after normalisation), never `SUBMIT_WORDS`; never on LinkedIn / Handshake hosts (those are parked unless the button leaves the host) |
| Sign in / Create account / Forgot password / Verify | `agent/accounts.py` | only on a page `pages.page_kind` reports as sign-in / account / verification; button text matches that step's word list; never "Submit" |
| Next / Continue | `agent/wizard.py` | the page's gate verdict is would_submit (or Jack resumed after a pause); the control matches `adapters.NEXT_WORDS`, never `SUBMIT_WORDS` |
| Consent checkbox tick | `agent/submit.py` (and accounts for the sign-up box) | the gate's `consent_actions` says would_tick; the label re-read at tick time passes the consent policy (section 3) |
| Submit | `agent/submit.py` | `gate.decide` on the final record is would_submit, no CAPTCHA, no login wall, no unconfirmed fill; button text matches `SUBMIT_WORDS` |

`filler.py` keeps every refusal it has: it still never clicks Submit, Next,
Sign in or a consent box, never presses Enter, and still refuses
`type="password"`. Plan-driven filling is unchanged. Password typing happens
only in `accounts.py`, through one function that takes the value, types it
into a located new-password / confirm box (never a current-password box when
a new-password box is present), reads nothing back, and returns only whether
the keystrokes were sent. The outcome hook stays the password-safe snapshot
in `outcome.py`, reused as is.

## 2. Milestones

### M0 — history scrub (done first, before any other commit lands on the new history)

Found by a full-history blob scan (phone-shaped numbers, emails, street
addresses, dates of birth, IDs, plus the owner's name and employer / school /
city words). The hits, by kind (the strings themselves are kept out of this
file on purpose; the replacement list lives outside the repo, in the
session's scratch folder, and is listed by kind in TODO):

- a real phone number in three shapes, in `tests/test_containment.py` at
  22457d0 (replaced with a 555 stub at eeb19b8);
- the owner's full name as a test value in the same blob;
- two companies named in code comments (`tests/test_legal_guard.py`,
  `agent/sensitive.py`, an early TODO);
- an employer and a university in early README text, a university as the
  example in `agent/matching.py`'s docstring, and a city as a test value;
- the first blob of this plan (9620262), which named those strings.

Commit messages and author fields hold only GitHub noreply addresses; test
stubs (555 numbers, example.invalid, 1 Example Street) stay.

Steps: `git bundle` backup to the scratch folder; `git filter-repo
--replace-text` with the list; re-add `origin`; re-scan all blobs and commit
messages; confirm `v0.1-mvp` points at the rewritten 9d84445; force push
`main` and the tag; write the old hashes into TODO under "Jack: ask GitHub
Support to purge".

### M1 — open MUST-FIX items and CLAUDE.md

- (a) legal / EEO checkbox answers written to gate_runs as edits: `edits_json`
  rows carry `label` and `kind` (ticked / unticked). For a checkbox whose
  label or kind is legal / EEO / pay (sensitive line, legal guard, HARD_STOPS
  legal, or a traced ref), the kind is stored as `changed_withheld` so the
  answer can't be read off the row.
- (b) sponsorship precedence: `sponsorship_line` looks for "future" / "now"
  words anywhere in the question. Narrow it to the sponsorship clause: the
  deciding word must sit within the same sentence as the sponsorship word
  (sponsor / immigration / petition / work permit / visa), else the field is
  Jack's. Narrowing a routing toward "yours" adds caution.
- (c) submit binding live on refused pages / "exact" capture from Next: the
  capture hook fires on any button pointerdown, so a Next click is recorded
  as "exact". The hook records which control fired it (text, submits-form
  flag); a read that came from a control whose text is in NEXT_WORDS, or
  from a page `pages.fill_allowed` refused, is stored as "polled", never
  "exact".
- (d) normal fill lacking value read-back: `fill_one`'s uncontained path
  types and returns "typed" without reading back. After typing it runs the
  same read-back as the contained path (`exact` / `same` → filled, `other` →
  unconfirmed, `empty` → not filled). The read-back never runs on a password
  box (fill_one refuses those before anything is typed).
- CLAUDE.md: owner decisions recorded; design rules rewritten to match. The
  reviewer definition (`.claude/agents/rule-reviewer.md`) gets one added
  paragraph pointing at CLAUDE.md's owner-decisions section; its BLOCK list
  keeps: any password read, any page value to the model or network, a
  consent tick outside the policy in section 3, Submit from anywhere but
  `submit.py` after `gate.decide`, and missing-key gate defaults that pass.
  Jack is told about that edit in the report (it is not something he asked
  for; he can revert it).

### M2 — link intake

`apply.py run <url> [<url> ...] [--queue FILE]`. `agent/intake.py`:

- `open_link(ctx, url)`: open; LinkedIn / Handshake: look for the Apply
  control; if it is Easy Apply / Handshake-internal → `parked(reason)`; if it
  opens a new tab or navigates off-host → follow (new tab via
  `ctx.expect_page`); anything else → parked.
- `find_apply(page)`: visible buttons / links whose normalised text is in
  `APPLY_WORDS` ("apply", "apply now", "apply for this job", "apply for this
  position", "i'm interested", "apply manually"); exactly one, else none. A
  control matching `SUBMIT_WORDS` is never clicked.
- `click_apply(page)`: clicks it, follows a new tab, settles, returns the
  page the form is on. Workday's "Autofill with Resume / Apply Manually"
  chooser: pick "Apply Manually".
- Portal detection via `adapters.detect_with_method` on the landing page.
- Queue file: one URL per line, `#` comments; links already logged are
  skipped unless `--force`.

### M3 — accounts

`agent/accounts.py`, driven by `pages.page_kind`:

- ACCOUNT (sign-up): type the profile email (and first / last name if boxes
  are labelled so, from the profile), generate a password under one fixed
  policy (24 characters: upper, lower, digit, symbol; shortened only to a
  `maxlength` attribute, never below 20; no page text is read for rules), type
  it into the new-password and confirm boxes, tick a TOS / privacy consent
  box if one is required and allowed, click Create account / Sign up /
  Register. Then re-read the page kind. If the page rejects the password,
  pause as needs-you rather than parse the rule.
- SIGN_IN: type the email; click Sign in (Chrome may have autofilled a saved
  password); if still a sign-in page: if a "Create account" link exists → go
  there (ACCOUNT); else forgot password. (A page saying the email already
  exists also leads to forgot password: a page-text cue, but both branches
  end there, so it decides nothing.)
- Forgot password: click it, type the email, submit, notify, `getpass` with a
  15-minute timeout, come back to sign-in (go back / reopen the job link and
  click Apply), type email + password, click Sign in, discard.
- VERIFICATION: notify, wait 15 minutes for a pasted code (typed into the
  one-time-code box, then Verify / Continue) or for the page to stop being a
  verification page.
- `agent/console.py`: one stdin-owning thread; `ask(prompt, hidden, timeout)`
  returns the line or None on timeout; a stale read is drained before the
  next prompt.
- `agent/notify.py`: Windows toast via PowerShell; fallback `\a` + message.
- Open question for Jack (reviewer FLAG): whether Playwright's bundled
  Chromium on the persistent profile saves the generated password without a
  click on its save bubble is unverified. If it doesn't, every return visit is
  a forgot-password reset. M3 tests what can be tested headlessly and the
  report says what was proven.
- Tests: a mock sign-up page, a mock sign-in page with a forgot-password
  flow, a verification page. Sentinel passwords; every file in the test home,
  every DB row, every print, and every model call input are searched for the
  sentinel and must not contain it.

### M4 — multi-page wizard

`agent/wizard.py`: `run_application(ctx, page, prof, link) -> Outcome`. The
activity listener (`ledger.ACTIVITY_INIT_SCRIPT`) is installed on every page,
as follow mode does, so Jack's own input during a pause is recorded. Loop:

1. CAPTCHA → pause (notify, wait for it to clear, 15 min). An event cleared
   during a pause is marked `handled: "jack"`; the gate stops on any
   unhandled one and on one present on the final page.
2. `pages.fill_allowed`: LinkedIn / off-host → parked; account / sign-in /
   verification → `accounts.handle(...)` (the login_wall event is marked
   `handled: "agent"`), then continue the loop.
3. Scan, classify, route, map, fill (the normal-fill pipeline from apply.py,
   extracted into `wizard.fill_page`), read back (snapshot), `shadow.step_entry`.
4. Page gate: `gate.decide` on a record of this page; would_stop → pause with
   the reasons; after Jack resumes (Enter), re-read: a flagged field counts
   as Jack's answer (`human_filled`) only if it was empty before the pause,
   holds a value now, AND the activity listener recorded a trusted input
   event on that element during the pause. Page behaviour alone never waives
   a stop. The page is gated again. Timeout → parked.
5. If a Submit control is on the page and no Next: final page → M5.
   Else click Next / Continue, settle, detect a new step (`follow.read_shape`
   must change) and loop. Validation errors (same step after Next) → pause.
6. Safety caps: 25 steps, 60 minutes per application.

### M5 — submit

`agent/submit.py`: on the final page, build the full record (every step),
`gate.decide`; tick `would_tick` consent boxes (re-reading labels); re-census;
re-gate; if would_submit: find the one Submit control, click it, settle,
capture evidence (final URL, title, the detection signals, and only the
sentence the confirmation pattern matched, never the page's text at large)
and log `agent_submitted=1` in gate_runs. Status "submitted" only if a
confirmation was detected (portal signature or the generic one); otherwise
needs-you ("submit clicked, no confirmation seen").

Gate changes: the consent policy of section 3 beside the profile allowlist;
enablement (allowlist, cap) becomes informational, by the owner decision
"all portals, no cap"; `human_filled` and `handled` events as in M4.

### M6 — applied list

Tracker migration 2: `applications` gains `reason`, `evidence_json`,
`portal_key`, `agent_submitted`; statuses add `needs_you` and `failed`.
`apply.py applied` prints company, role, URL, date, portal and status.

### M7 — notifications and needs-you queue

`apply.py needs-you` lists parked jobs with reasons. `run` sends a toast at
each pause and one at the end of the batch with counts.

## 3. Consent policy

Jack's decision allows ticking terms-of-service / privacy-policy consent and
forbids marketing opt-ins. Portals word these boxes a hundred ways, so an
exact list can't be the only route. The policy, applied to the label read
from a real `<label>` or aria-label at tick time, on every reading of the
text (`sensitive.text_variants`):

- ticked only if it names an agreement word (agree / accept / consent /
  acknowledge / have read) AND a document word (terms / conditions / privacy
  policy / privacy notice / data processing);
- never if any reading matches MARKETING (newsletter, marketing, promotional,
  SMS / text message, talent community / network / pool, job alerts,
  updates about, keep me informed) or CERTIFY (certify, attest, signature,
  true and accurate / correct / complete, background check, drug) or any
  other HARD_STOPS legal pattern beyond the terms / privacy one;
- never with fine print attached (aria-describedby), never with hidden text,
  never over 240 characters;
- the profile's `consent_allowlist` (exact match) remains as a second route.

This is pattern-based page-text matching that leads to a tick, which the
reviewer flagged against the old "exact comparison only" rule. It is the
owner's 2026-10-18 decision, recorded here for Jack to confirm or tighten.

## 4. Tests

Every milestone adds fixture-driven tests under `tests/` (mock HTML, headless
Chromium, no network, no model). Each new test is checked to fail on the
previous commit. The password-containment tests search: stdout, every file
under the test home, every SQLite row, the reports folder, and every
`anthropic.Anthropic().messages.create` input (patched) for the sentinel.

## 5. How Jack runs it

```
.venv\Scripts\python.exe apply.py run https://company.example/jobs/123
.venv\Scripts\python.exe apply.py run --queue links.txt
.venv\Scripts\python.exe apply.py applied
.venv\Scripts\python.exe apply.py needs-you
```

`run` opens each link in the agent's Chrome profile, applies, and pauses
with a notification whenever it needs him. `fill` keeps working as before.
