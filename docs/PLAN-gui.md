# PLAN — GUI, non-blocking pauses, Google sign-in, MyGreenhouse

Owner decisions of 2026-10-19 (recorded in CLAUDE.md): a job that needs
Jack never fails and never closes; Google sign-in first; the Chrome profile
may be signed in to Google by Jack's hand; chooser and basic-scope consent
may be clicked, anything else pauses. Waiting for Jack's go before item 1.

## Items (one commit each, in this order)

1. **Local GUI** — new `agent/gui.py` (stdlib `http.server`, a thread, bound
   to 127.0.0.1 on a random port; one inline HTML page polling `/state`
   every 2 s) and `apply.py gui`. Requests: `POST /start` (links, dry
   toggle), `/continue`, `/approve`, `/code`, `/close-browser`,
   `/google-login`. Every POST needs the per-session token (random, in the
   page, `X-Token` header); every request's Host must be `127.0.0.1:<port>`
   or `localhost:<port>`; an Origin that isn't the page's own is refused; no
   CORS headers; the page is served with a CSP (`default-src 'none';
   script-src 'nonce-…'; connect-src 'self'`) and every string from
   `/state` lands in the page as a text node (a label holding `<script>` or
   `<img onerror>` is inert; tested). State is one in-memory object behind a
   lock: per job the company, role, portal, status, reason, and for a gate
   stop each field's label, reason and, only for a field the gate's own
   sensitive predicate does not flag (category, PROFILE_PATHS line, legal
   kind, LEGAL_GUARD, raw flags) and that the agent filled, the proposed
   value with an approve flag. A field either layer flagged, and every
   password box, is never sent to the page (tested with a field the
   classifier called free_text and LEGAL_GUARD caught). Approvals and codes
   live in that object only.
2. **Non-blocking pauses** — `pause.Pauser.wait` gains a GUI mode that
   records the pause and raises `pause.Deferred`; `wizard.Wizard` catches
   it, keeps its place (page, step, the page's fill and gate result, the
   pause kind) and returns `waiting`. `apply.py gui` runs the queue on the
   main thread (Playwright's), one tab per job, and calls `resume(job)` when
   Jack presses Continue: the page is re-read; an approval counts as
   reviewed only if the field still holds the value Jack was shown (the
   same collapsed-whitespace comparison filler.py uses), otherwise its stop
   stands (negative test); a code is typed into the one-time-code box; the
   page is gated again, and the loop carries on to Submit. No timeout. The
   browser closes only on "Close browser" with no paused job, or on Jack's
   confirmation. In GUI mode a forgot-password pause asks Jack to sign in
   in the tab himself; no password passes through the GUI.
3. **`apply.py google-login`** — opens the persistent profile at
   accounts.google.com, waits for Enter (or the GUI button), reads nothing.
4. **Google sign-in** in `accounts.py` (helper `agent/google.py`): a
   "Sign in / Continue with Google" control (whole-text match) is pressed
   ahead of email + password. `google` alone leaves accounts.NEVER_WORDS
   under the 2026-10-19 decision; microsoft, apple, facebook, linkedin and
   sso stay refused (negative test). The Google page (same tab or popup)
   counts only when `urlparse(url).hostname == "accounts.google.com"` and
   the scheme is https (an `accounts.google.com.example` host or a path
   holding the string is not Google: negative tests), and is handled by
   structure only: the chooser entry whose visible email equals the
   profile email may be clicked; each permission listed on the consent
   screen, after NFKC / casefold / collapsed whitespace only, must equal an
   entry of a list in code written from Jack's words (name, email address,
   profile picture / basic profile info); an empty or unparsed list or any
   extra entry pauses; an email / password box, a 2-step challenge or
   "this browser may not be secure" pauses with nothing typed. Tests: local
   mocks for each page.
5. **MyGreenhouse** in `intake.py`: for `my.greenhouse.io/jobs/<slug>/<id>`
   try `job-boards.greenhouse.io/<slug>/jobs/<id>` then
   `boards.greenhouse.io/<slug>/jobs/<id>`; use one only if it loads and its
   title equals the original's; else the original with Google sign-in if
   offered, else the code pause. No password flow is invented.

Also in item 1: `run --dry` and the GUI's dry toggle (intake only: final URL,
portal, whether a form with fields was found; nothing filled, no account,
no sign-in, no Submit / Next / consent click). The real-page check on the
given MyGreenhouse link runs with it and is reported verbatim.

## What changes in the pause / park flow

Today: a pause blocks the terminal, times out after 15 minutes, parks the
job and the next link reuses the tab. After: a pause is a row state; the
tab stays open and filled; the queue continues in new tabs; Continue
resumes that tab from where it stopped. "Parked" survives only for refusals
(LinkedIn Easy Apply, Handshake built-in, a crash); "needs you" never ends
by itself.

## Files

New: `agent/gui.py`, `agent/google.py`, `tests/test_gui.py`,
`tests/test_resume.py`, `tests/test_google.py`, `tests/test_mygreenhouse.py`.
Changed: `apply.py`, `agent/wizard.py`, `agent/pause.py`,
`agent/accounts.py`, `agent/intake.py`, `agent/tracker.py` (status
`needs_you` keeps the tab), CLAUDE.md, README, TODO.

## Tests

GUI: endpoints through `http.client` against the server thread with a fake
state; a wrong Host, a missing token and a foreign Origin are refused;
legal / password fields never appear in `/state`. Pauses: the three-page
mock portal with a GUI-style pauser, a pause then Continue, an approval
counting as reviewed, a code typed, a second job running while the first
waits. Google and MyGreenhouse: local mocks per page as listed above.
