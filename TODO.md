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

   Still open, and yours to decide: `eeo.disability_status` is the boolean
   `false` rather than a decline string, so unlike the other eight it makes the
   tool actively select "No, I do not have a disability" on a form. Working as
   written; flagged only because it reads differently from its neighbours.

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

13. **Decide on the mock form as a test fixture**
    Currently only in a Claude Code scratchpad.

14. **Decide whether `make` stays in the docs**
    README and setup.sh both hand you `make check` / `make fill`, but the
    Makefile hardcodes `.venv/bin/python`, which doesn't exist on Windows —
    so every `make` example is dead on your own machine. Either teach the
    Makefile to find `.venv/Scripts/python.exe` too, or drop `make` from the
    docs and show `python apply.py` everywhere.

15. **`documents/README.txt` is untracked**
    `.gitignore` negates it (`!documents/README.txt`) but it was never added,
    so a fresh clone gets no `documents/` at all and no explanation of the
    fallback. Either `git add -f` it, or drop `ROOT / "documents"` from
    `DOCUMENTS_DIRS` and make the private folder the only place documents live.
