# PLAN — Workday adapter

Owner decisions of 2026-10-20 (third set, recorded in CLAUDE.md): optional
empty fields don't stop the gate; Enter inside a Workday prompt search box
once the box is known not to submit; one "Add" click per profile entry in
a repeating section, never a delete; `work_history.past_employers` answers
"have you worked for <tenant>" with "No" only on no loose match. Built
before this plan: fill-failure reasons (item 0), `apply.py snapshot` (item
1), the password store. Waiting for Jack's go before item 1 below.

Inputs first: Jack's snapshots of Workday's My Information, My Experience
and Voluntary Disclosures pages and of Divergent's Greenhouse form, and
one real `run` with item 0's reasons on the Workday text boxes. Every
mock is built from a snapshot, never from memory of Workday, and each
fixture is scanned for personal data before it is committed (labels and
option text can carry prefilled values), as the push rule says.

## Items (one commit each, in this order)

1. **Gate: optional empties don't stop** — `gate.decide` waives exactly
   these reasons on a non-required field: `left blank`, `not answered`, an
   unanswered per-company question, and an optional checkbox left
   unticked; each is listed in the report. `filling it failed`,
   unconfirmed, flagged, sensitive, free-text, needs-review and
   model-confidence stops stay, with a negative test for each. Accepted
   and written down: "required" is page data (the attribute, aria-required,
   a trailing *), so a page that unmarks a field makes the waiver apply.
2. **Root cause + Workday text boxes** — `agent/workday.py` finds the
   fields for legal name, address, phone number and email by
   data-automation-id and fills them through filler.py's own primitives
   (the password-type refusal, never Enter, the click-time can't-submit
   checks and the whitespace-collapsed read-back stay in one place); it
   presses nothing. The failure category from the real run decides the
   fix (obscured → the click_filter overlay, as accounts.py's presses
   handle; detached → a React re-render, re-find by id before each step;
   not_editable → the wrapper isn't the input). Mock from the My
   Information snapshot; tests fail on the previous commit for each layer.
3. **Button-dropdowns** (aria-haspopup=listbox: phone device type,
   country, state): open, choose only the option whose text equals the
   profile value after normalisation, confirm the button's own text shows
   it; never the first row.
4. **Prompt search boxes** (searchBox / multiselectInputContainer: country
   phone code, school, field of study): Enter is pressed by workday.py,
   nowhere else, and only when the input's automation-id matches AND
   `input.form === null` (Workday's page has no enclosing form) AND the
   submit check passes; a mock with the same input inside a `<form>` must
   refuse. Then wait for rows, pick only an exact match; none → left blank
   and reported.
5. **Repeating sections** (Work Experience, Education, Languages,
   Websites): count the entries already present (Workday pre-creates one
   and the resume parser may add more), use an empty one first, click the
   section's own Add control only for the remainder, one at a time,
   confirming exactly one new entry appeared per click and stopping
   otherwise; fields by automation-id inside the entry; never Remove.
6. **Resume, voluntary disclosures, self-identification** — the resume to
   the page's file input; disclosures and self-id are EEO: named profile
   lines with the strict fit or Jack, never the model. Decision for Jack:
   Workday's required "I have read and consent to the terms and
   conditions" box sits on the Voluntary Disclosures page, mid-wizard,
   outside the consent policy (account creation and the final page), so
   the required-but-empty check pauses every run there; extend the policy
   to that box (terms / privacy wording only) or accept the pause.
7. **Review and Submit** — `bottom-navigation-next-button` is already
   Next (adapters.py, wizard.py; the Submit-wording exclusion stays, since
   Workday's Review-page Submit shares the id); what is missing: the Review
   page handed to submit.py, with Workday's Submit id and text verified
   from the snapshot, through the gate on the full record.
8. **Past employers** — template line `work_history.past_employers`; a
   guard kind of exact label phrases ("worked for / been employed by /
   contractor for <company>", label only) recognises the question; the
   gate's per_company rule admits this one line with a strict fit. "Loose
   match" means: casefolded token containment either way, or the
   employer's initials equal the tenant ("gm" ↔ "General Motors"); a
   tenant of four characters or fewer with no match, or a question naming
   more than one company, "subsidiaries", "affiliates" or "or", is doubt
   → Jack. Tests for each.
9. **Divergent / Greenhouse** (not asked for; listed for Jack's go) — why
   `submit.find_submit` no longer finds the control, from the snapshot of
   the real form; fix with a mock.

Each item: mock from the snapshot, tests that fail on the previous commit,
rule-reviewer, FLAGs to TODO. Nothing here relaxes a design rule: the
model answers no legal question; no password is read from a page; Submit
only after `gate.decide` says would_submit on the full record.
