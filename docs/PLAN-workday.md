# PLAN — Workday adapter

Owner decisions of 2026-10-20 (third set, recorded in CLAUDE.md): optional
empty fields don't stop the gate; Enter inside a Workday prompt search box
once the box is known not to submit; one "Add" click per profile entry in
a repeating section, never a delete; `work_history.past_employers` answers
"have you worked for <tenant>" with "No" only on no loose match. Built
before this plan: fill-failure reasons (item 0), `apply.py snapshot` (item
1), the password store. Waiting for Jack's go before item 1 below.

Inputs first: Jack's snapshots of Workday's My Information and My
Experience pages and of Divergent's Greenhouse form, and one real `run`
with item 0's reasons on the Workday text boxes. Every mock below is built
from a snapshot, never from memory of Workday.

## Items (one commit each, in this order)

1. **Gate: optional empties don't stop** — `gate.decide` lists a
   non-required field left blank (any category) and an optional checkbox
   left unticked in the report, not among the stop reasons; required,
   legal / EEO / pay, flagged and unconfirmed fields still stop. Tests on
   the gate record alone. Independent of Workday; smallest first.
2. **Root cause + Workday text boxes** — `agent/workday.py`: a portal
   adapter keyed by data-automation-id for legal name, address, phone
   number, email; fills under Workday's click_filter the way accounts.py
   presses its buttons; reads back. The failure category from the real
   run decides the fix (obscured → the overlay; detached → React
   re-render, re-find by id before each step; not_editable → the wrapper
   isn't the input). Mock from the My Information snapshot.
3. **Button-dropdowns** (aria-haspopup=listbox: phone device type,
   country, state): open, choose only the option whose text equals the
   profile value after normalisation, confirm the button's own text shows
   it; never the first row.
4. **Prompt search boxes** (searchBox / multiselectInputContainer: country
   phone code, school, field of study): type, check the box can't submit
   (the submit check plus no form-submit on Enter), press Enter, wait for
   rows, pick only an exact match; none → left blank and reported. Enter
   nowhere else.
5. **Repeating sections** (Work Experience, Education, Languages,
   Websites): one click on the section's own Add control per profile
   entry, fields by automation-id inside the new entry, never Remove.
6. **Resume, voluntary disclosures, self-identification** — the resume to
   the page's file input; disclosures and self-id are EEO: named profile
   lines with the strict fit or Jack, never the model.
7. **Save and Continue, Review, Submit** — adapters.py recognises
   `bottom-navigation-next-button` as Next; the Review page goes to
   submit.py through the gate on the full record.
8. **Past employers** — template line `work_history.past_employers`; a
   "have you worked for <company>" question answered "No" only when the
   tenant from the job's URL host (`gm` in gm.wd5.myworkdayjobs.com)
   loosely matches no listed employer; any match or doubt → Jack.
9. **Divergent / Greenhouse** — why the submit control is no longer found,
   from the snapshot of the real form; fix in adapters.py with a mock.

Each item: mock from the snapshot, tests that fail on the previous commit,
rule-reviewer, FLAGs to TODO. Nothing here relaxes a design rule: the
model answers no legal question; no password is read from a page; Submit
only after `gate.decide` says would_submit on the full record.
