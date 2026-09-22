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

5. **Write the `why_this_company` and `why_this_role` voice answers**
   Still blank. Drafted essays are built from them — blank in, generic out.

6. **Run it on a Greenhouse or Lever posting**
   Only tested against a bespoke portal so far.

7. **Get past the account wall**
   Load a password manager extension into `chrome-profile` so registration and
   login are one click. Then confirm the agent resumes on post-login pages.
   No plaintext credentials in profile.yaml.

8. **Extend the EEO block for voluntary diversity questions**
   LGBTQ+, first-generation, socio-economic background. New `eeo.*` lines
   answered verbatim from profile, defaulting to "Prefer not to say" like the
   existing ones. Profile-lookup path, never the model. Depends on #7.
   New `eeo.*` patterns go ABOVE the gender pattern in `PROFILE_PATHS` —
   "gender identity" otherwise catches orientation questions.

9. **Collect corrections, then write `learn`**
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

10. **Resolve the four `# VERIFY` lines** in `~/.job-agent/profile.yaml`
    The current-status wording is copied verbatim onto forms.

11. **Fix `letters.py` → `save_draft`**
    Uses `config.DOCUMENTS_DIR`, which doesn't exist (config has
    `DOCUMENTS_DIRS`). `--cover-letter` fails every time.

12. **Clean up stale references**
    README and setup.sh still point at `profile.example.yaml` and the in-repo
    `documents/`. Template now lives in `agent/template.py`.

13. **Decide on the mock form as a test fixture**
    Currently only in a Claude Code scratchpad.
