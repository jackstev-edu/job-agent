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
    - Known limit of the password fix, narrowed by default-deny reads: a box
      is read only with a name or an explicit label, never on a page where a
      password box or hint has been seen this run or is present at read
      time, and never once seen as a password. What's left: a named or
      labelled box that says nothing about a password, already shown as text
      before the agent's first scan, on a page where no password box or
      password hint ever appears. Nothing marks it as one, so it reads as an
      ordinary box.
    - The password words are also tested on a camelCase-split copy (pinCode,
      PINCode, apiToken, userPin...), in the page and again in Python
      (`extract.says_secret`). Residual: an all-caps run glued to lowercase
      ("PINcode") has no split point, so PIN isn't a whole word there.
    - A radio or button group is recorded only if every option was read.
      If some were refused, the capture is "inexact" and the run is
      unscored (a false submit still counts).
    - Default-deny reads: ticked and pressed state (checkbox, radio, button
      pairs) is exempt from the stable-identity rule, since it's only ever
      true or false. Every other rule applies to it.
    - Accepted (Jack, R4): if you paste a password into an ordinary, readable
      box by mistake, it's read like any value there, and for an ordinary
      category the corrections log keeps it (~/.job-agent/corrections.jsonl,
      local only). The rules can't tell a pasted password from an answer.
    - Residual in page_text (what the model is shown of the page): it leaves
      out forms, form controls, editable regions, dropdown / listbox
      widgets and react-select's chosen-value display. A widget library
      that shows a chosen value as plain text outside all of those, with
      none of those roles or class names, would still show through.
    - page_text is taken once per `fill`, on the posting, after the first
      login check and before you're asked to click Apply. Every model call (the
      mapper on every step, the cover letter) gets that same text. Residual:
      a URL that opens on a logged-in page already showing saved answers
      outside any form (or a sign-in that lands on one) still puts them in.
    - Decided (Jack): in compute_edits a type=password box is skipped, while
      a box that's secret only by marker, hint, pattern or sticky key comes
      out "unreadable". Why: a pattern match may be a mislabelled ordinary
      field the person did edit; "unreadable" stops a false zero-edit claim,
      where skipping it would let one through. Also decided: "Show / Remember
      password" checkboxes match the pattern and come to you as credentials.
      That fails safe, so it stays.
    - Labels and help text (sent to the model) are built like page_text:
      the field itself and anything that can show a value (form controls,
      editable regions, combobox / listbox / option widgets, react-select
      value displays) are left out of aria-labelledby, label[for], a
      wrapping <label>, a nearby legend / label / .label and
      aria-describedby. Closed (fix round after 2c7a1ab):
      - Validation text echoing a value after the resume upload (fixed
        properly after the 0a2523e BLOCK): fields are classified once
        before the upload, with their help text. After it, a field keeps
        that category only if it's the one field with its identity (name
        attribute or explicit label, tag, type) before and after, and its
        label and help text are exactly the same. Every other field is
        classified again with no help text. COST: such a field with any
        help text comes to you ("help text held back after upload")
        unless its category is answered from your profile or by you. A
        portal that rewrites help text on upload, or fields with no name
        or explicit label, will send more questions to you. No post-upload
        help text ever reaches a model; a reused field's mapper input
        carries its identical pre-upload help.
      - The page also checks each field's label and help read whole
        (before value displays are skipped) against HARD_STOPS and
        LEGAL_GUARD, on every reading of the text, and returns only
        raw_stop / raw_legal booleans. A stop or legal word styled as a
        tag inside a label still stops. Patterns and invisible characters
        come from sensitive.py (one source), with a parity test.
      - A dropdown's options filtered by text in its box (or a creatable
        `Create "<text>"` row): a box already holding text isn't opened.
        The in-page check runs after the password test and returns only
        true or false. Only a visible text box or editable region counts
        (a hidden input's default value no longer keeps it shut).
      - Value displays in `[role=button]`, `[role=slider]`,
        `[aria-selected=true]` rows, and the chip / tag / selected-item
        displays of MUI, Ant, Select2, Choices, Chosen and Workday
        (`data-automation-id="selectedItem"`) are on the skip list.
      - The document title (role and company, for the tracker and the
        cover letter) is read on the posting, before Apply.
      Still open:
      - **Validation/error text (aria-describedby, aria-errormessage) can
        echo a typed value** ("'jo@x' is not a valid email") in the FIRST
        scan, when the page already holds a value (a saved draft, a page
        that opens filled). It's plain text, so the skip list doesn't catch
        it. aria-errormessage isn't read today; aria-describedby is.
        Follow mode sends a model only the fields it reads empty, unmarked,
        untouched and not yours, just after each scan, and never with help
        text (blanked; a field with help text that isn't profile- or
        human-routed comes to you, "help text held back in follow mode").
        A field holding any value is in no model input. A normal fill keeps
        the pre-upload rule (the
        one scan before the upload sends help; after it, unchanged fields
        reuse that classification and the rest go without help). Still
        open, in a normal fill only: the first scan of a page that already
        shows a value (a saved draft, a page that opens filled, or anything
        you typed before pressing Enter at "Click Apply").
      - The skip list is a deny-list. A widget library that shows a chosen
        value in none of its selectors still shows through, in labels,
        help and page text.
      - A dropdown whose search box is outside the element the scan
        stamped (a separate input it controls) isn't seen by the
        holds-text check.
      - Residual: an aria-label or placeholder that page script rewrites to
        include the value is sent as the label. Neither the scan nor the
        model calls read a `title` attribute.
    - Not covered by the password fix: a hostile page that moves a ref
      attribute onto its own unmarked, neutrally-labelled password box. The
      page controls its DOM and could hand over the value many other ways;
      the guards are against reading one by accident.

## Follow mode: deferred

- A per-step breakdown in the review sheet (today one sheet lists every
  page's rows together).
- Frames: follow mode, like the scan, sees only the top document. An
  iCIMS-style form inside an iframe isn't filled, and its sign-in pages
  aren't recognised there (run pages.fill_allowed on every frame first).
- The corrections log and the outcome read-back don't run in follow mode
  (no final snapshot; edits are unknown, so runs score unknown).
- The resume upload's baseline isn't tracked across pages (uploads=None).
- The activity guard only guarantees quiet before a fill starts. While the
  agent fills, what you type in, paste into or change, and Backspace or
  Delete, is marked and keeps the field out of model input; a mouse-only
  change then isn't (Jack's EXCEPTION accepts it, see MUST FIX). The fill
  itself re-checks a field as empty only at plan time.
- A page whose structure changes faster than the watcher settles may be
  skipped; real-portal timing unchecked.

## MUST FIX BEFORE AUTO-SUBMIT

- The dropdown display check compares whole words by Unicode letters and
  numbers; a script with no letter or number marks (and look-alike
  characters generally) still falls under the Unicode parity item below.

Accepted for now (CLAUDE.md threat model: a page that changes a field
between scan and fill, or splits words with invisible or look-alike
Unicode; Jack reviews every page before Submit). None of these may stay
open once anything submits without Jack.

- **Line vs none:** when one reading of the text resolves to a profile
  line and another to none, the field should come to Jack (today the line
  is used).
- **Unicode \w / \b parity:** the page's copy of HARD_STOPS and LEGAL_GUARD
  runs with ASCII-only \w and \b; rewrite them as [\p{L}\p{N}_] and
  Unicode-aware lookarounds, and add non-ASCII parity samples (accented
  neighbours, U+0130, U+0131). JavaScript's \s also differs from Python's
  (U+001C-001F, U+0085), so the collapsed readings aren't identical; use one
  explicit class on both sides, collapse the earlier reading too, and add
  whitespace samples.
- **raw_legal_kinds:** the page returns only raw_legal (true / false); it
  should return the kinds, merged into the guards before the two-area and
  same-area checks.
- **After-upload tests:** a field with no, or a duplicate, stable identity
  treated as new; the gate counting the raw flags.
- **The no-match-lost sweep as a pytest test** (sampled so the default run
  stays under 6 minutes), so the reviewer can rerun it.
- **Next detection:** by aria-label, and by "Proceed", "Review", "Save and
  continue", "Save & Next".
- **Test clean-ups from earlier rounds:** test_field_text.py (hide d3 or
  select by dom_id / type; drop the v1 docstring claim; an Ashby title with
  a single-value sentinel); test_posting_text.py (both sentinels on the
  live page inside the patched draft); a watch()-driven hook test;
  _fill_run answering the Apply prompt once via a flag;
  test_more_matches_than_it_looks_through_counts depending on the limit.
- **Stale comments:** gate.py _question_text ("normaliser included");
  sensitive.py PROFILE_PATHS sponsorship-row comment (the eligibility line
  set is now explicit).
- **Corrections log:** record no profile line when readings disagree.
- **Work-authorization lines:** export_control_us_person in,
  security_clearance out (to Jack). Confirmed by Jack.
- Host family on shared ATS domains: every Workday / Greenhouse tenant is
  one family, so a link to another employer's form would be filled.
  Compare whole hosts for known ATS domains; Lever and Greenhouse put
  tenants on one host (jobs.lever.co/<company>, job-boards.greenhouse.io/
  <company>), so they also need the path's tenant segment.
- The shows-choice lookup's `[class*="control"]` also matches Bootstrap's
  form-control / controls wrappers.
- A step that fails partway (fill_step_failed) loses the rows already
  filled from the review sheet and tracker.
- The gate treats a results entry with no "unconfirmed" key as confirmed;
  it should default to unconfirmed.
- An Indian "PIN code" (or a token / secret field) makes a page read as a
  sign-in page in follow mode, so nothing on it is filled.
- The react-select mock in tests/test_shows_choice.py is unverified
  against a live Greenhouse page (real react-select picks on click).
- Follow mode, cross-field echo (accepted by Jack's EXCEPTION in CLAUDE.md
  until auto-submit): a page can copy one field's value (yours, or the
  resume autofill's) into another, empty, untouched field's label or
  placeholder, and that field goes to the model with it. (Help text never
  goes in follow mode since the return to the conservative rule; the
  exception still allows it, so sending help again needs no new wording,
  only a clean review of the touch memory.)
  Also accepted: input in the instant before the listener is installed on
  a newly loaded page (an init script runs it before the page's own
  scripts; per-poll install is the backstop). Close before
  auto-submit (for instance, send no help for any field on a page where
  any field holds a value).
- Follow mode, mouse-only change during "hands off — filling" (accepted by
  Jack's EXCEPTION until auto-submit): a dropdown row you pick and clear
  with the mouse while the agent fills isn't marked, since the agent's own
  clicks are trusted too. Fix: the agent tells the listener which element
  it's acting on; trusted pointer events elsewhere are marked.
- Follow mode, the touch record lives in the page's own sessionStorage
  (cleared when a run starts, so an earlier run's touches aren't carried
  in). It is readable and writable by any script on that origin in the
  tab, and two follow runs in one tab would share it.
- Follow mode, the listener runs in the page's own world: a hostile page
  can set window.__jaAct.agent, strip data-ja-touched or rewrite the
  sessionStorage record, and a field you touched would go to the model.
  (Such a page already holds that value and could echo it into an
  untouched field, which the exception accepts.) Run it in an isolated
  world before auto-submit.
- Follow mode, touch marks: a touched field is remembered by identity
  (name, id, explicit label, and an anonymous tag key) as well as by the
  element's mark, so a re-mount or Back / Next keeps it yours; a tracked
  match is yours for the run, an untracked match keeps its kind and tag
  out of model input for the run. An element whose name, id and label all
  change between your touch and the next scan isn't matched. Marking the
  focused element means your next click anywhere after a fill makes the
  last field the agent filled yours (never refilled after a re-render).
  `closest('[class*="control"]')` also matches wrappers like
  "form-controls" or "*controller", so one click in such a wrapper makes
  every field inside it yours for the run, matched by label on later
  pages too; and the anonymous tag key means one touch on any input keeps
  every keyless input of that tag out of model input for the run
  (refusals only; narrowing either needs Jack's approval).
  The control-container mark can make every dropdown inside a
  Bootstrap "controls" wrapper yours once you touch any field in it, and a
  dropdown the agent typed into and then abandoned becomes yours (both
  refusals only).
- Follow mode, combobox "empty" (placeholder-class false positives): a
  button or div combobox counts as holding a value whenever it shows any
  text, so one showing a placeholder ("Select...") is never filled and
  comes to you. `choices__item` also matches a Choices.js
  placeholder, `chosen-single` is always there in Chosen, and the chip /
  tag selectors can match a chip-list wrapper with placeholder text: those
  empty dropdowns read as holding a value and are never filled (refusals
  only; narrowing them needs Jack's approval).
- Follow mode, combobox "empty": chips and selected items are looked for
  inside the control container, a button / div combobox's own text, and a
  Workday selectedItem in its `formField` container (unverified against a
  live Workday page). A display anywhere else still reads as empty.
- From review FLAGs this run:
  - scan-time decide reads the name only as written; read it split into
    words too (as click time does);
  - the 2+-profile-lines rule only runs for profile / guarded fields; Jack
    to decide whether it covers every field;
  - the gate has no 2+-profile-lines stop of its own;
  - keep the old first-match comparison as an extra click-time refusal;
  - a "veteranIsDisabled" rename test, and a positive single-line
    sponsorship test ("Do you require sponsorship?");
  - `[class*="control"]` also matches Bootstrap's form-control / controls
    wrappers (fills abandoned, or a sibling's single-value read); Jack to
    decide the lookup;
  - "unconfirmed" should default to true when the key is missing;
  - negative tests: a button combobox whose text never changes, a
    form-control wrapper, the Python collapsed reading; pin GATE_VERSION 6
    in a test; the react-select mock is an approximation (real
    react-select picks on click), unchecked against a live page.

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

   Superseded, 15 Oct 2026: `sign_in_with_saved_login` is gone. The design
   rules now forbid clicking Sign in, Next or Continue, so at a login wall
   the agent pauses (Chrome may still autofill) and you press Sign in, and
   `fill` fills only the page it's on, logging a `multi_step` event when a
   Next / Continue control is there. A multi-page wizard now needs follow
   mode (you move, it fills each page).
   prepare() no longer clicks Apply either (a signed-in "Apply" can be a
   one-click submit); fill asks you to click it, then press Enter.

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
