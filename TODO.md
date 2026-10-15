# job-agent — next steps

1. ~~**Fix `enrich_comboboxes` in `agent/extract.py`**~~ DONE
   Slow menus are now polled for rather than waited on for a flat 450ms, and a
   type-to-search box (which never lists anything) is marked `search_required`
   so the model answers it with the full name typed out. The filler clicks the
   matching row instead of pressing Enter on whatever was highlighted, and
   refuses ambiguous or unfound values rather than committing one.

2. ~~**Add `current_status` to `profile.yaml`**~~ DONE
   `current_status:` block added to the profile; `Profile.current_employment()`
   resolves the rule in code (a `current: true` entry wins, then
   `current_status`, then the field goes to you) and hands the answer to the
   model pre-resolved. The four new lines are marked `# VERIFY`.

3. ~~**Add `field_of_study` synonyms**~~ DONE
   New `agent/matching.py` holds the option matcher and a nearest-heading table
   for degrees. It also splits a double major written as one string. Anything
   matched that way is flagged for review, because a heading that describes
   your degree is not the same as a copy of it.

4. ~~**Fold in the Chromium-path fix**~~ DONE
   `cmd_doctor` reads `pw.chromium.executable_path` inside `sync_playwright()`,
   so the loop is still alive when it closes. `doctor` now runs clean — no
   `Task was destroyed` traceback.

5. ~~**Write the `why_this_company` and `why_this_role` voice answers**~~ DONE
   Drafted essays are built from them — blank in, generic out. Both filled;
   confirmed 12 Oct 2026: `check --verbose` no longer lists either under
   "Worth filling".

6. ~~**Run it on a Greenhouse or Lever posting**~~ DONE — a Greenhouse
   application was filled by the agent, reviewed, and submitted by you on
   13 Oct 2026; it's logged as submitted. Lever is still untested live.
   First tested against a bespoke portal; progress on real ATSs below.

   Progress — Ashby, 9 Oct 2026: a live Ashby posting asked its Yes/No
   questions (work authorization, sponsorship, commute) as a pair of
   `<button>`s over a hidden checkbox. The scan dropped hidden inputs, so those
   questions vanished from the field list entirely. `extract.py` now emits each
   button group as a radio group with `widget: 'buttons'`, and `filler.py`
   answers it by clicking — re-checking at click time that the button is still
   in its group, can't submit a form, and doesn't read like Submit or I agree,
   and leaving an already-pressed answer alone. Covered by
   `tests/test_button_pairs.py`.

   Progress — Greenhouse, Oct 2026: a dry run on a live Greenhouse posting
   completed. Greenhouse is also where two bugs showed up, both now fixed
   (#16, #17): every required react-select dropdown came out twice, and
   invisible reCAPTCHA's badge paused every run. A full (non-dry) Greenhouse
   fill and anything on Lever are still untested live.

   Closed, 13 Oct 2026: dry runs passed on a real Greenhouse posting, a full
   fill ran, and after your review the application was submitted and
   logged with `apply.py status <url> submitted`.

   If Ashby ever wraps these buttons in a `<form>`, the submit guard refuses
   every one of them (they're typeless, so they'd submit it) and the questions
   all come to you. That's deliberate — the safe failure. Don't loosen it.

7. *Deferred: Workday and the account wall. See "Deferred" at the end.*

8. ~~**Extend the EEO block for voluntary diversity questions**~~ DONE
   Four new lines in `agent/template.py`, all defaulting to "Prefer not to
   say": `sexual_orientation`, `transgender`, `first_generation`,
   `socioeconomic_background`. Four matching rows in `PROFILE_PATHS`, placed
   ABOVE the gender row as planned — "sexual orientation or gender identity"
   is one field on plenty of portals, and the gender pattern matches that
   phrase, so a lower-placed rule would never have been reached. The `eeo`
   category in `classify.py` now names these so the classifier can't file one
   as `personal_fact` and hand it to the model; `eeo` still routes to
   profile-lookup only. It did NOT depend on #7 — profile lines plus regex,
   nothing to do with the account wall.

   Found and fixed while testing: `fit_to_widget` matched decline options with
   a substring list containing "not wish", which misses Greenhouse's actual
   wording, "I don't wish to answer", on the apostrophe. Every EEO default
   — including the five that already shipped — silently became a manual field
   on the commonest portal there is. Now a `_DECLINE` regex covering decline /
   prefer not / don't wish / don't want to answer / choose not to self-identify
   / no answer, in both curly and straight apostrophes. The "want" wording
   turned up only when the regex was tested against the real profile rather
   than invented examples, which is the argument for #13. Verified it still
   won't hijack a real answer ("Male" stays "Male", "Yes, I have a disability"
   stays itself, on lists that also offer a decline option).

   Profile updated by hand on 22 Sep 2026 — all four keys present and
   resolving, verified end to end against the real file: a bundled
   "sexual orientation or gender identity" dropdown now fills the decline
   option instead of being handed back.

   Decided by you, 12 Oct 2026: `eeo.disability_status` stays the boolean
   `false`. Unlike the other eight, which decline, it makes the tool actively
   select "No, I do not have a disability" on a form. That's intended, not an
   oversight. Don't "fix" it to a decline string.

9. ~~**Collect corrections, then write `learn`**~~ DONE — verified on 1 real
   application; rerun after ~10.

   Verified 13 Oct 2026 against the real log: 6 rows from one Greenhouse
   application. All 6 turned out to be false corrections, and `learn` now sets
   them aside, so no suggestion has been exercised on real data yet. Five were
   react-select dropdowns: the snapshot read the widget's search box, which
   is empty once an option is chosen, so every answered dropdown looked
   cleared (four were EEO or work-authorization questions). One was a phone
   number the page's input mask had reformatted. Fixed at three levels:
   - the snapshot reads what a react-select dropdown displays;
   - the logger skips formatting-only diffs and dropdowns that read back
     blank, and stamps rows `log_version: 2`;
   - `learn` sets such rows aside (all version-1 dropdown rows included) and
     says how many and why.
   Covered by tests with anonymised rows in the same shape. The real log is
   left as it is.
   Every `fill` now diffs the form at the "Press Enter" prompt and appends what
   you changed to `~/.job-agent/corrections.jsonl`. Values are kept only for
   ordinary fields; sensitive ones record THAT you answered, not what. Gather
   ~10 real applications before building anything on top of it.

   Then a `learn` command can PROPOSE, for your approval: new label→profile-path
   aliases, profile gaps ("you typed this on 4 forms and it isn't in
   profile.yaml"), and few-shot examples for the classifier. Learned data may
   add mappings or add caution — it must never move a field out of `human`, and
   it never writes to profile.yaml on its own. A value you typed on one form
   isn't automatically a general fact about you.

   Limits of the log as built: it only sees the last wizard page (on Workday the
   earlier steps are gone), Workday re-renders can strip `data-ja-ref` so those
   fields drop out of the diff, and you have to press Enter before Submit
   because the page navigates away afterwards.

   Progress, 12 Oct 2026: a report-only `apply.py learn` exists (`agent/learn.py`).
   It groups corrections that recur on 2+ postings by profile line, or by
   question when no line is known. For each group it prints a suggested
   profile edit, a profile addition, or a label→line alias. Eligibility, EEO
   and compensation corrections are only flagged. It writes nothing. Each log
   row now also records `profile_path` (the line's name, never a sensitive
   value) and `logged_at`. Still open: collecting ~10 real applications, then
   deciding which suggestions to act on. Classifier few-shot examples aren't
   proposed yet. No real corrections exist yet, so it has only run on seeded
   data.

10. ~~**Resolve the four `# VERIFY` lines** in `~/.job-agent/profile.yaml`~~ DONE
    The current-status wording is copied verbatim onto forms. Confirmed
    12 Oct 2026: `check --verbose` lists no `# VERIFY` lines.

11. ~~**Fix `letters.py` → `save_draft`**~~ DONE
    Now writes to `config.DOCUMENTS_DIRS[0]` — always the private
    `~/.job-agent/documents`, never the in-repo fallback, because a draft names
    a company you're applying to. The old `AttributeError` was invisible:
    `apply.py` catches it and prints "cover letter failed" with the first 100
    characters, so every `--cover-letter` run looked like an API problem.

12. ~~**Clean up stale references**~~ DONE
    `profile.example.yaml` was already gone everywhere, and README already
    described `agent/template.py` and the private folder correctly — the only
    file still steering you wrong was setup.sh. It no longer runs
    `mkdir -p documents`, it captures `STATE_DIR` once and reports the private
    folder, it looks for your resume in `$JA_HOME/documents/` rather than the
    repo, and it points at `documents.resume_default` instead of "the path in
    profile.yaml". It also now tells you to run `init`, which nothing in the
    setup path mentioned.

13. ~~**Decide on the mock form as a test fixture**~~ DONE
    The Ashby button-pair form is now `tests/button_pairs.html`, driven by
    `tests/test_button_pairs.py` (30 checks in four tests). Every test now
    runs under `python -m pytest tests/`, and still runs alone as a script. The original 20-field
    mock form from the first round wasn't recovered; only this one survived.

14. ~~**Decide whether `make` stays in the docs**~~ DONE
    It stays, as a Mac/Linux shorthand. README and setup.sh now lead with
    `python apply.py <cmd>` everywhere, with a PowerShell setup block, and
    mention `make` once as optional. The Makefile itself is unchanged apart
    from its header comment — it hard-codes `SHELL := /bin/bash`, so making
    it Windows-aware would be fighting it.

15. ~~**`documents/README.txt` is untracked**~~ DONE
    Tracked now. The negation never worked: `.gitignore` excluded the folder
    itself (`documents/`), and git can't re-include a file whose parent
    directory is excluded. It's `documents/*` now, so the README is visible
    to git and every other file in there — subfolders included — is still
    ignored. The in-repo fallback in `DOCUMENTS_DIRS` stays.

16. ~~**react-select dropdowns appear twice on Greenhouse**~~ DONE
    react-select puts a hidden `<input required aria-hidden tabindex=-1>`
    under each required dropdown, purely for form validation. It passed the
    visibility test, so it was read as a second question. `extract.py` now
    skips inputs that are both `aria-hidden` and `tabindex=-1`. It
    deliberately doesn't check opacity, because styled radios hide their real
    inputs that way. Covered by `tests/test_greenhouse_select.py`.

17. ~~**reCAPTCHA false positive on every Greenhouse run**~~ DONE
    Invisible reCAPTCHA's badge iframe is always on screen, so `detect_captcha`
    paused every run. reCAPTCHA now counts only when something other than
    that badge is visible: the challenge frame, a checkbox anchor, or any
    other reCAPTCHA frame. The generic "bot check" no longer trips on the
    badge's wrapper, and every match is checked, not just the first. Real
    challenges still stop the run; nothing solves them. Covered by
    `tests/test_captcha.py`.

18. ~~**Fail fast on a bad API key**~~ DONE
    `fill`, dry runs included, makes one free authenticated call before the
    browser opens. On a 401, or with no key at all, it stops and says to set
    a new `ANTHROPIC_API_KEY`. Any other error (offline, an outage) only warns,
    and the classifier's fail-safe still covers the run. `doctor` now reports
    "API key accepted" from the same call. Covered by `tests/test_api_key.py`.

19. **Move the classifier to the current Haiku once Haiku 4.5 is deprecated**
    `CLASSIFIER_MODEL` defaults to `claude-haiku-4-5-20251001`, which is legacy
    but active. Its earliest possible retirement is 15 Oct 2026, and at least
    60 days' notice is promised. When a deprecation notice for it appears at
    platform.claude.com/docs/en/about-claude/model-deprecations, switch the
    default to `claude-haiku-5-5`, keeping the `JOB_AGENT_CLASSIFIER_MODEL`
    override. Then re-run the classifier against real forms. Haiku 5.5 has
    thinking on by default, so the `max_tokens=4000` ceiling in `classify.py`
    and the JSON-only reply can behave differently. Nothing in `tests/` calls
    the real API, so this needs a live check, not just `pytest`.

20. ~~**Submit gate, shadow mode**~~ DONE (gate phases 1-9)
    After every real fill the gate logs would_submit / would_stop with named
    reasons, apart from enablement. `fill` then watches the tab, never
    clicking, records your outcome and edits, and `apply.py shadow-report`
    scores the calls per portal. Nothing auto-submits; no new click, tick or
    submit path; fill-time routing unchanged. See README, "The submit gate
    (shadow mode)".

21. **Confirm the success signatures on real portals**
    The Greenhouse (`/confirmation` + "Thank you for applying"), Lever
    (`/thanks` + "Application submitted") and Ashby (form gone + text, same
    host) signatures are my understanding, not checked against real pages.
    Each run logs which signals held and the URL shape. After your next
    real submission on each portal, run `shadow-report` and check
    `success_signal` in `gate_runs`: a "match" you answered y confirms that
    signature, and a miss means the pattern needs fixing. Needs you: real
    submissions.
    - Also unconfirmed: Ashby ticks a button pair's hidden checkbox only for
      "Yes" (and leaves it unticked otherwise). The gate's one button-pair
      exemption rests on it; it comes from `tests/button_pairs.html`, not a
      live page. Check the box's state after answering Yes and No on a real
      Ashby posting. If it's wrong, the gate stops more, never less.

22. **Collect shadow data before deciding anything**
    The gate is strict by design. Optional fields left blank stop it, as do
    any per-company question, any login wall, and loose option fits. Expect
    mostly would_stop at first. Let `shadow-report` gather a few weeks of
    runs per portal before changing a rule. Open questions for then:
    - per-company answering from `per_company` (it would mean relaxing
      HARD_STOPS and ticking boxes);
    - ~~whether census-only checkboxes should also need a `<label>` or
      `aria-label`~~ decided in fix round 13 (gate_version 2): they do, and
      any hidden ticked box stops. Since gate_version 3 an unread tick counts
      as ticked. Since gate_version 5 the only exemption is an Ashby button
      pair's own box: the only box with its group_ref, hidden, the same
      element before and after the fill, read unticked then ticked for a
      planned "Yes", or unticked both times for a planned "No". Every other
      box in a group meets the label and allowlist checks, so it stops;
    - whether a false stop should break a portal's streak. Today only a
      false submit resets it.
    Needs you.

23. **Fix-round follow-ups (deferred from the rule-reviewer, rounds 10-13 and after)**
    Not started. One line each, with the reviewer's smallest fix.
    - Item 14a: `score._edits` crashes on an `edits_json` entry that isn't a
      dict. Fix: return None unless every element is a dict.
    - Item 14b: `shadow-report` dies on a malformed profile. Fix: catch
      YAML errors and ValueError on profile load and show enablement "unknown".
    - Item 14c: page-derived text (gate reasons, labels) is printed raw. Fix:
      strip control characters, ANSI and other escape sequences before
      printing.
    - Item 15: CLAUDE.md Workflow. Fix: add "After each review, run git status
      and confirm the tree is unchanged. If the reviewer changed anything,
      stop and tell Jack.", and a trailing newline.
    - eb900c6: a would_stop with an unknown answer and any edit (which the
      page itself can cause) adds to the streak. Fix: count it as a match,
      but don't add to the streak when human_submitted isn't y/n.
    - eb900c6: the "a field unreadable, no edit" scoring case also uses
      capture="polled", so it proves nothing about the unreadable field.
      Fix: drop `capture="polled"`, or relabel it.
    - eb900c6: no would_stop + UNREAD + EDIT -> match case. Fix: add one
      case row.
    - 7b29c3d: the upload baseline is assumed, not read back. A portal that
      clears its native input logs file_removed every run. Fix: after
      `upload_ref`, read the box with `extract.snapshot`, and set
      `uploaded = None` unless it equals `[Path(resume).name]`.
    - 7b29c3d: the file-box test never runs the apply.py upload path. Fix:
      drive `filler.upload_file` in the test, and add a failed-upload case
      asserting every file box comes out "unreadable".
    - 7b29c3d: `outcome._accept` takes a list for any known ref. Fix: pass
      the file-field refs in and reject a list for any other ref.
    - 7b29c3d: a re-saved resume with the same name counts as no edit. Fix,
      if wanted: also compare `f.size` / `f.lastModified` (metadata, not
      contents); otherwise leave as specified.
    - 7b29c3d: nothing pins `uploads` (it holds the resume's file name) out
      of gate_runs. Fix: a test asserting "uploads" and the name are absent
      from `json.dumps(shadow.redacted(record, result))`.
    - c8f7f57: `gate.decide` defaults `agent_submissions_today` to 0, so a
      caller that leaves it out gets a full cap. Fix: default to None.
    - c8f7f57: on a gate error, `cap_text` prints "not set" even with a cap
      set. Fix: return "unknown" when "gate error" is in the reasons, or add
      CAP_COUNT_UNKNOWN to the fallback's enablement_reasons.
    - c8f7f57: the report's enablement line uses the latest run's detection
      method. The reviewer says no fix is needed, since it errs toward "not
      enabled"; noted so you know where the line comes from.
    - c8f7f57: the parity test's closing `check(True, ...)` always passes.
      Fix: count the combinations compared and assert there are 96.
    - 7619f96: the census misses other ways of hiding a box (opacity 0,
      off-screen, clip / clip-path, a 1x1 sr-only rect). Fix: treat those
      as not visible in `extract.py`'s census.
    - 7619f96: scoring ignores gate_version, so v1 would_submits that v2+
      would stop still count toward a streak. Fix: count only rows with the
      current gate_version toward a streak, or show runs per version.
    - 7619f96: no test combines aria-labelledby with a hidden unticked
      native box. Fix: add `visible=False` to the aria-labelledby case.
    - 4ec285c (MEDIUM; must fix before any portal is enabled for
      auto-submit): the same-element check is empty on real Ashby pair
      boxes. They have no label and no ref, so before and after always
      compare equal; the tests used a label shape the census can't produce.
      Fix: have the census record the box's own `name` attribute (q-auth
      etc.) and compare it; build `_pair` boxes as the census emits them
      (empty labels); add a test running the real census on
      button_pairs.html.
    - 4ec285c: the sentinel test wasn't extended, and the new test never
      reads the raw db. Fix: add a ticked sensitive pair with pre-fill and
      post-fill states to `test_no_answer_values_reach_gate_runs`, and assert
      on the whole stored `prefill_census` and the raw db bytes.
    - 4ec285c: withholding a pair's state only because its question is
      traced is untested (both test pairs also match the regex). Fix: give
      a pair an empty label so withholding has to come from
      `group_ref in traced`, and add an untraced empty-label pair.
    - 4ec285c: unticked before and after is also an unpressed pair, so on
      a dry run a No pair is exempt while a Yes pair stops. Fix: withhold
      the No exemption when `dry_run` is set (or note it in the
      `_is_pair_mirror` docstring and TODO item 21).
    - Known limit of the password fix: a box whose type, autocomplete hint,
      name, id, label and placeholder all say nothing about a password, and
      which is already shown as text before the agent's first scan, can't be
      recognised: the agent never saw it as a password. Every reader then
      treats it as an ordinary box. A box seen as a password once stays
      secret for the run (page marker plus Python's sticky set).

## Deferred

Parked for after the MVP. Each one says what's known and what it needs.

7. **Get past the account wall (Workday)**
   Load a password manager extension into `chrome-profile` so registration and
   login are one click. Then confirm the agent resumes on post-login pages.
   No plaintext credentials in profile.yaml.

   Progress, 12 Oct 2026: no extension needed. Chrome's built-in password
   manager works in Playwright's bundled Chromium on our persistent profile.
   Checked in a throwaway profile against a local login page: it offers to
   save, and on a fresh launch it autofills both boxes with no gesture. So the
   launch stays on bundled Chromium, not `channel="chrome"`. On a login wall,
   `browser.sign_in_with_saved_login` waits for autofill and presses one plain
   Sign in / Log in / Continue button; otherwise it pauses for you as before.
   The design rule in CLAUDE.md changed to match (the agent never reads,
   types, generates or stores a password; the browser may fill one). Account
   creation stays manual. The check runs at the start, again after
   `adapters.prepare()` (Workday shows its sign-in page only after Apply is
   clicked), and before every wizard step. What it still needs: a later run
   on a real portal that gets past the wall and resumes filling on the
   post-login page. On Workday that's blocked by the step below.

   Findings from a live Workday attempt, Oct 2026. These are why it's
   deferred:
   - **"Sign in with Google" / SSO isn't supported.** Google blocks sign-in
     from automated browsers, and an SSO login leaves no saved password for
     Chrome to autofill, so `sign_in_with_saved_login` has nothing to work
     with. Use the portal's own email-and-password account.
   - **Chrome's password generator isn't offered in the agent's profile.**
     It needs a Chrome sign-in, and this profile is deliberately never signed
     in. You have to choose your own password when creating the account.
     Saving it when Chrome offers works.
   - **After sign-in, the "Autofill with Resume" step stops the run.** That
     page has only buttons and a hidden file input, so the scan reports "No
     form fields found", `fill` exits, and exiting closes the browser. It
     needs a Workday step handler that recognises this page (upload the
     resume to the hidden input, or choose "Apply Manually") before scanning.
   - **Gotcha: don't type commands into the terminal `fill` is waiting in.**
     While `fill` sits at an `input()` prompt ("Press Enter when you're
     in", "Press Enter when you've finished"), whatever you type there is
     read as that Enter, and the command never runs. Run shell commands in a
     second terminal. Documented in the README too.
