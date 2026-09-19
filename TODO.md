# job-agent — next steps

1. **Fix `enrich_comboboxes` in `agent/extract.py`**
   Custom dropdowns return no options, so the model can't match them. Broke
   `School *` twice on Bain. Most portals use these. Best first task for
   Claude Code.

2. **Add `current_status` to `profile.yaml`**
   Plus a fallback rule for Current Employer / Current Job Title when no
   experience entry is marked `current: true`. Recurs on every application.

3. **Add `field_of_study` synonyms**
   "Artificial Intelligence Engineering" matched nothing twice.

4. **Fold in the Chromium-path fix**
   Kills the `Task was destroyed` traceback after `doctor`.

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