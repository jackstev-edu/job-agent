# job-agent

Fills out job applications for you, then **stops** so you can check the work
and press Submit yourself.

---

## 1. Your personal data never goes in this repo

Your profile holds your address, phone number, work history and supervisor
contacts. None of that belongs in a git repository, so it doesn't live here.
Run this once:

```bash
python apply.py init
```

That creates `~/.job-agent/profile.yaml` and `~/.job-agent/documents/` — the
same private folder that already holds your application log and saved browser
logins. On Windows it's `C:\Users\<you>\.job-agent\`.

The blank profile is written from a template kept inside the code
(`agent/template.py`), so the repo carries no profile-shaped file at all. You
can make this repo public without editing a thing.

**Already have a profile inside the project folder?** Move it:

```bash
python apply.py init --migrate
```

That relocates your profile and any resumes out of the repo. The tool warns you
on every run until you do.

### Then fill in three things

**a) Put your resume in `~/.job-agent/documents/`** and make sure the filename
matches `documents.resume_default` at the bottom of your profile. Relative paths
are resolved against that private folder first, so `resume.pdf` is enough.

**b) Your mailing address** — `personal.address_line_1` and
`personal.postal_code`. Asked on nearly every application.

**c) Read the eligibility block carefully.** This is the one part to actually
read rather than skim, because these are declarations you sign:

```yaml
authorized_to_work_us: true
requires_sponsorship_now: false
requires_sponsorship_future: false
work_authorization_type: "US Citizen"   # <- set to exactly what you are
export_control_us_person: true          # <- the ITAR question
```

Then `python apply.py check` lists anything still outstanding.

### Where your data lives

| Where | What | In git? |
|---|---|---|
| `~/.job-agent/profile.yaml` | Your facts | No |
| `~/.job-agent/documents/` | Resumes, transcripts, drafts | No |
| `~/.job-agent/applications.db` | Application log | No |
| `~/.job-agent/chrome-profile/` | Saved portal logins | No |
| `ANTHROPIC_API_KEY` (environment) | API key | No |
| the repo | Code and a blank template | Yes |

Override the location with `JOB_AGENT_HOME`, or point at a single file with
`JOB_AGENT_PROFILE` or `--profile`.

## 2. Setup

Every command in this README is `python apply.py <command>`, run with the
virtual environment active. That works the same on Windows, Mac and Linux.

**Windows (PowerShell):**

```powershell
cd job-agent
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
playwright install chromium
python apply.py init      # creates your profile outside the repo
```

Then set your API key from [console.anthropic.com](https://console.anthropic.com)
so it survives new terminals: `setx ANTHROPIC_API_KEY sk-ant-...`.

**Mac / Linux:**

```bash
cd job-agent
./setup.sh
source .venv/bin/activate
python apply.py init      # creates your profile outside the repo
```

`setup.sh` checks your Python version, creates the environment, installs the
three libraries, downloads a private copy of Chrome, and tells you what's
missing. Then add your key to `~/.zshrc` (Mac) or `~/.bashrc` (Linux):

```bash
export ANTHROPIC_API_KEY=sk-ant-...
```

Either way, open a new terminal, activate the environment, then:

```bash
python apply.py doctor    # confirms everything is wired up
python apply.py check     # confirms your profile is complete
```

**Optional shorthand on Mac / Linux:** the `Makefile` wraps these — `make
doctor`, `make check`, `make fill URL=...` — and calls the venv's Python
directly, so you needn't activate it. It doesn't run on Windows; `make help`
lists the rest.

Cost, for planning: each application costs a few cents of API usage. A hundred
applications is somewhere under five dollars.

---

## 3. Using it

```bash
python apply.py fill https://job-boards.greenhouse.io/company/jobs/1234 --dry-run
```

Do this first, two or three times. A dry run reads the form and prints what it
*would* enter without touching anything. It's how you build trust in it.

```bash
python apply.py fill https://job-boards.greenhouse.io/company/jobs/1234
```

The real thing. A Chrome window opens, the agent works out which portal it's
looking at, uploads your resume, fills the form, and stops. A review sheet
opens in your browser listing what it did and what needs you. You fix those
items in the form and click Submit.

After filling, `fill` prints the **shadow gate**'s call (whether it *would*
have submitted unattended, and why not) and then just watches its own tab.
Review and submit as normal; it never clicks anything. Press Enter in the
terminal when you're done, submitted or not, and answer "Did you submit it?".
See "The submit gate (shadow mode)" below.

Options:

```bash
python apply.py fill <url> --resume robotics   # use a resume variant
python apply.py fill <url> --cover-letter      # also draft a cover letter
python apply.py list                           # everything you've applied to
python apply.py list --status submitted
python apply.py status <url> submitted         # after you press Submit
python apply.py followup --days 21             # who hasn't replied
python apply.py learn                          # profile fixes your corrections suggest
python apply.py shadow-report                  # the gate's calls scored against yours
python apply.py --help                         # the full menu
```

With `make`, the same things are `make dry URL=...`, `make fill URL=...
RESUME=robotics LETTER=1`, `make list STATUS=submitted` and `make followup
DAYS=21`.

When something goes wrong, the browser window stays open while `fill` is
waiting for you. Finish by hand, and note which selector failed so you can
fix it. If `fill` exits instead (as it does after a Workday sign-in today),
the window closes with it.

**Use a second terminal for anything else.** While `fill` waits at a "Press
Enter…" prompt, anything you type into its terminal is read as that Enter:
the run carries on as if you'd finished, and your command never runs. Keep
`fill` in one terminal and run other commands in another.

### Follow mode: multi-page applications

```
python apply.py fill <posting url> --follow
```

Off by default. Use it for applications that run over several pages.

**You do:** click Apply, Next, Continue and Submit; sign in or create the
account (Chrome may fill a saved password); type any verification code;
answer what the agent leaves for you; press Enter in the terminal when
you're done, then answer "Did you submit it?".

**The agent does:** stays on its own tab and, each time a new page
settles, fills that page. It fills only empty fields that aren't yours (a
field that had a value, or that you changed, is never touched again), only
after you've been idle for two seconds (it prints "hands off — filling"
while it works), and it never types anywhere but the field it's filling.
On sign-in, account, verification, other-site or LinkedIn pages it fills
nothing and reads nothing. It never clicks Apply, Next, Continue, Sign in or
Submit. Your resume goes only into a box labelled as the resume.

One review sheet and one tracker record cover the whole application. The
run ends at the portal's confirmation page, when you press Enter, after 20
minutes with no new page and no input from you, or after 90 minutes; a
closed tab still asks whether you submitted. The shadow gate always says
would_stop for a multi-page run.

### The submit gate (shadow mode)

The agent still never submits. But after each real fill it works out whether
it *would* have been safe to submit unattended, logs that call, and later
scores it against what you actually did, per portal. Nothing acts on the
call; it's evidence, collected for later.

**The call (`agent/gate.py`)** is a pure function of the run: no model, no
page access. It says `would_submit` only if all of these hold, and otherwise
`would_stop` with every reason named:

1. Every required field is filled.
2. Every eligibility, EEO, pay or legal answer came from a named profile
   line, matched to the form's options exactly (a "close enough" option
   stops).
3. Nothing was handed to you, left blank (optional fields included), failed,
   flagged for review, or answered by the model with less than high
   confidence.
4. Every checkbox on the page, ticked or not, has a real `<label>` (or
   `aria-label`), no hidden text, no fine print, and its label is *exactly*
   one of your `consent_allowlist` entries. That holds for custom
   (`role=checkbox`, `role=switch`) and hidden boxes too; `aria-labelledby`
   and a box's own text don't count. Any hidden box that's already ticked,
   or whose state couldn't be read, stops it, whatever its label. The one
   exception is an Ashby button pair's own hidden box: the only box with its
   group's ref, the same box before and after the agent filled the page, and
   in exactly the state the planned answer leaves it, unticked then ticked
   for "Yes", unticked both times for "No". Every other box in a button group
   meets the same label and allowlist checks. A required allowlisted box counts as
   one the gate *would* tick. The agent still ticks nothing.
5. There are no per-company questions (how you heard about us, previously
   employed here). Answering those from the profile isn't enabled.
6. Free text is one of your `voice.canned_answers`, word for word.
7. Nothing unexpected happened: no CAPTCHA, no login wall (even one you
   signed past), no Next or Continue control (more pages to come), no
   visible embedded frame.

Separately, it notes whether the portal is on `autosubmit_portals` and how
much of `daily_submit_cap` is left. That's never part of the call and never
scored.

**What you did (`agent/outcome.py`).** It watches the tab and reads the form
as you go and at the moment you submit (never a password box). It spots the
portal's confirmation page and asks whether you submitted; your answer
decides. The confirmation signatures (Greenhouse, Lever, Ashby) haven't been
checked against real pages yet, so until one matches a submission you
confirmed, it's only a suggestion. All of this runs in the page's own
JavaScript, so it's only as trustworthy as the page. The scores are advice.

**The scores (`apply.py shadow-report`)**, per portal:
- `would_submit` matches only if you submitted with zero edits, read at the
  moment you submitted;
- `would_stop` matches if you changed something or didn't submit;
- anything uncertain is unknown.

A false `would_submit` resets the portal's streak of consecutive matches.
Next to the streak, the report shows how many of its matches were
`would_submit`s: a streak made only of `would_stop` matches says nothing
about the gate's yes. The report's enablement line uses the gate's own rule,
so a portal not detected by its host is never shown as enabled, and if
today's count can't be read, the cap remaining shows as unknown.

**Profile keys.** All of these start blank, and blank means "no":
- `autosubmit_portals`: a list of portals;
- `daily_submit_cap`: a whole number;
- `consent_allowlist`: checkbox labels copied exactly;
- `per_company`: recorded for later; nothing fills from it yet.

### Saving a portal login once

Some portals want an account per employer. Do this once for each (Workday
support is deferred; see section 6):

1. Run `fill` on a posting. At the login wall the agent pauses and says
   "This portal wants a login".
2. In that Chrome window, sign in yourself. If you need an account, create it
   yourself too: the terms checkbox, the verification code and the Create
   Account button are always yours. Use the portal's own email and password.
   "Sign in with Google" and other SSO buttons don't work here: Google blocks
   automated browsers, and an SSO login leaves no saved password to fill.
   Chrome won't suggest a generated password in this profile either (that
   needs a Chrome sign-in), so choose your own.
3. When Chrome offers to save the password, click **Save**. It goes into
   Chrome's password manager inside the agent's own profile,
   `~/.job-agent/chrome-profile`, and nowhere else. Don't sign that profile in
   to a Google account; the agent launches it with sync off, and saved logins
   should stay on this machine.
4. Press Enter in the terminal to carry on.

On later runs, Chrome fills the login form by itself. The agent still
pauses at the login wall and never presses Sign in: you press it, then
Enter in the terminal. To forget a login, open `chrome://password-manager`
in the agent's Chrome window.

---

## 4. What each piece does

Two sentences each.

### The things you touch

**`profile.yaml`** — Every fact about you, in one file. Everything else reads
from it, and nothing is ever invented that isn't written here.

**`setup.sh`** (Bash) — Installs the whole toolchain in one command. Written in
shell because it's pure orchestration and works before Python is set up.

**`Makefile`** (Make) — Optional short names for `apply.py` commands on Mac
and Linux. It hard-codes bash and `.venv/bin/python`, so it doesn't run on
Windows and isn't meant to; `apply.py` is the real interface.

**`apply.py`** (Python) — The command you run. It decides the order of
operations, which matters: the resume uploads before text is typed, because
portals autofill from it and would otherwise overwrite everything.

### The engine

**`agent/config.py`** — Holds every file path and setting in one place, so
moving something means editing one line. It also reads your API key from the
environment rather than storing it anywhere on disk.

**`agent/profile.py`** — Loads `profile.yaml` and answers questions about it,
like "what's the email" or "what's still blank". It returns nothing for blank
fields rather than a placeholder, which is what stops empty values leaking into
a form as the literal text "TODO".

**`agent/classify.py`** — Asks the model what KIND of question each field is —
biographical fact, password, legal declaration, consent checkbox — without ever
asking what the answer is. This is what makes the tool work on portals nobody
has seen: matching on wording breaks the moment a portal phrases something
differently, and a model reads both phrasings correctly.

**`agent/sensitive.py`** — **The guardrail, and the most important file here.**
It turns each category into one of four verdicts: fill from a named profile
line, let the model fill it, draft-and-flag, or hand it to you. A regex backstop
runs alongside, and a field has to clear both layers to be auto-filled — the
model can add caution, never remove it.

**`agent/browser.py`** (Playwright) — Opens a real Chrome window that remembers
you between runs, so you log in to a portal once by hand and every future run
inherits that session. It also spots CAPTCHAs and login walls and stops at
either, handing you the window. Chrome's own password manager may fill a
login; you press Sign in.

**`agent/extract.py`** (Python + injected JavaScript) — Runs a small script
inside the page that walks the HTML and returns just the questions, throwing
away the layout. This turns 200,000 characters of page into about 2,000, which
is the difference between an AI request that works and one that gets lost.

**`agent/mapper.py`** (Claude API) — The only place a model is used. It sees
the questions and your facts and returns which fact goes in which box, under a
prompt that forces it to return nothing rather than guess.

**`agent/filler.py`** (Playwright) — Types the answers in, character by
character, because many forms only register input they see as real keystrokes.
It contains no code path that clicks Submit — not as an option, not behind a
flag.

**`agent/adapters.py`** — Knows how the six major application systems differ
and applies what's known about each. Greenhouse is a clean single page;
Workday is a multi-step wizard that rebuilds its own HTML; treating them
identically fails on both.

**`agent/letters.py`** (Claude API) — Reads the posting and drafts a cover
letter in your voice, saved to a file. It never pastes one into a form, because
this is the piece most likely to embarrass you if sent unread.

**`agent/tracker.py`** (SQLite) — A small database of everywhere you've
applied, what you answered, and when. It lives outside the project folder so it
can't end up in a git commit.

**`agent/learn.py`** — Reads the corrections `fill` logs when you change its
answers, and suggests profile fixes for the ones that recur across postings.
It only reports: it never edits your profile or the code, and it only flags
eligibility, EEO and pay answers.

**`agent/report.py`** (HTML + CSS) — Writes the review sheet: one page listing
every question, what went in, and where it came from, with the things needing
your attention at the top. Written as HTML because you read it in a browser
next to the form itself, which turns a ten-minute check into a one-minute one.

---

## 5. Why it doesn't submit

Three reasons, and I'd argue against removing the gate.

**Quality.** Applications sent by a bot read like applications sent by a bot.
For specialised technical roles, one considered application beats forty
generic ones, and the review step is where "considered" happens.

**Accuracy.** An AI that invents a graduation date on a form you sign has made
you misrepresent yourself. The review sheet is the last check before that
becomes real.

**Legitimacy.** Most career portals are fine with tools that help you fill their
form. Very few are fine with a fully autonomous submitter, and the ones that
detect it ban the account. Stopping before Submit keeps you on the right side
of that without having to think about it each time.

## What it deliberately won't do

- **Solve CAPTCHAs.** It detects them, names them, and hands you the browser.
- **Read, type, generate or store a password.** The browser's own password
  manager, inside the agent's Chrome profile, may fill a login you saved
  (see "Saving a portal login once"). You press Sign in.
- **Create accounts.** Terms, verification codes and Create Account are yours.
- **Touch LinkedIn Easy Apply.** LinkedIn's terms prohibit automated access, and
  restrictions there are common and painful to reverse. Click those by hand.
- **Answer eligibility, EEO, or criminal-history questions with a model.**
- **Click Submit, Next, Continue, Sign in, Create account or an SSO button.**
  On a multi-page application it fills the page you're on, says more pages
  follow, and you move on yourself.

---

## 6. What to expect from each portal

**Greenhouse, Lever, Ashby** — should mostly work first time. Lever has one
gotcha already handled: its resume autoparse overwrites text fields, so upload
happens first.

**Workday** — support is **deferred**, and a run currently stops after
sign-in. The agent gets through Apply and the login wall, but Workday's next
page ("Autofill with Resume") has only buttons and a hidden file input. The
scan finds no form fields, `fill` exits, and the browser closes with it.
Making it work needs a Workday step handler; see "Deferred" in `TODO.md`.
Beyond that page it's a multi-step wizard with an account per employer,
element IDs that regenerate between renders, and dates split across three
spinners. The adapter already keys off `data-automation-id`. Roughly 40% of
large-employer applications, so it's worth finishing properly. Until then,
apply on Workday by hand.

**iCIMS, Taleo, SuccessFactors** — old, iframe-heavy, session-timeout prone, and
very common among large industrial employers.
The adapters are honest stubs.

**Anything else** falls through to the generic path and flags more for review.

---

## 7. What's been tested, and what hasn't

### The test suite

Each test drives a small mock form in headless Chromium, or the code directly.
None of them needs an API key, the network, or your profile: they run against
a throwaway `JOB_AGENT_HOME`, so `~/.job-agent` is never touched.

```powershell
.venv\Scripts\python.exe -m pip install -r requirements-dev.txt   # once
.venv\Scripts\python.exe -m pytest tests/
```

Every file also runs on its own as a script (`python tests/test_captcha.py`),
printing each check as it goes.

| Fixture / test | What it pins down |
| --- | --- |
| `button_pairs.html` · `test_button_pairs.py` | Ashby's Yes/No questions asked as `<button>` pairs over a hidden checkbox, copied from a live posting: read as radio groups in page order, answered by clicking, an already-pressed answer left alone. The traps are an "I agree" option and a button inside a real `<form>`. Both are refused, and the form is never submitted. |
| `greenhouse_select.html` · `test_greenhouse_select.py` | Greenhouse's react-select dropdowns. A required one renders a hidden `aria-hidden tabindex=-1` validation input; it must not come out as a second question. A styled radio group with opacity-0 inputs must still be read. After a choice, the snapshot reads the option the dropdown displays, not its empty search box. |
| `recaptcha.html` · `test_captcha.py` | Invisible reCAPTCHA's always-visible badge is not a challenge, so there's no pause. A revealed challenge frame, a checkbox reCAPTCHA, or any other captcha still stops the run. |
| `test_no_step_clicks.py` | A page with Next or Continue (a button, a link, a role=button, one that appears after filling, one behind many hidden matches, or a check that errors) is filled and left there: nothing is clicked, a multi_step event is logged and the gate stops. prepare() clicks nothing, Apply included. A dropdown that is a typeless button in a form is never clicked. |
| `test_filler_guards.py` | On a form that submits on Enter, filling a dropdown (matched, unmatched) and a text answer with a line break never presses Enter or sends it. A dropdown that becomes a submit button after the scan isn't clicked; a checkbox relabelled into terms isn't ticked. |
| `test_api_key.py` | Against a local stub API: a 401 or a missing key stops `fill`, dry runs included, before the browser opens. Any other failure falls through to the classifier's fail-safe. |
| `test_dry_run_and_tracker.py` | A null answer prints as a blank with the model's note, not `[ok] None`. A `--force` rerun refreshes a draft in full, but never knocks a submitted, rejected, interview or offer row back to draft. |
| `test_save_draft.py` | A cover-letter draft is written to the private documents folder, never the repo. |
| `test_learn.py` | `apply.py learn` on a seeded log: each kind of suggestion, eligibility/EEO/compensation flagged only, one-offs ignored, nothing written. Anonymised rows shaped like the first real log check that false corrections (unreadable dropdowns, a reformatted phone number) are set aside, and that the logger doesn't record them. |
| `login_wall.html` · `test_saved_login.py` | At a browser-filled login wall the real `fill` pauses for you and clicks nothing: no Sign in, Create account or "Continue with Google". The password never reaches Python. `fill_one` still refuses a password box. |
| `apply_then_login.html` · `test_login_after_apply.py` | The real `fill --dry-run` on a Workday-shaped mock whose sign-in page appears only after Apply: it asks you to click Apply, pauses at the sign-in page while you sign in, and scans the form behind it, not the login boxes. It clicks neither Apply nor Sign In. |
| `test_gate.py` | The submit gate, one condition at a time from a passing record: an arbitration box, a substring trap, CSS-hidden text, fine print, an injected instruction, a missing EEO profile line, a loose option fit, the free-text waiver, Ashby button pairs, allowlisted consent boxes (would_tick / leave), enablement kept apart, and stopping on any error. |
| `consent_boxes.html` · `test_gate_data.py` · `test_gate_profile.py` | The gate's inputs: every checkbox (hidden, custom, Ashby) and where its label came from, iframes, how profile answers were fitted, host-only portal detection, named review reasons, and the blank, safely-read profile keys. |
| `gate_form.html` · `gate_clean.html` · `test_gate_record.py` · `test_gate_fill.py` | The real `fill` builds the gate's record and prints and logs its call: the clean form would submit, the fuller one stops with its reasons named, dry runs aren't logged, and an EEO answer seeded as a sentinel never reaches the log. |
| `test_gate_tracker.py` | `gate_runs` is added to an old database without touching it, and no answer value reaches it, in any column or anywhere in the file. |
| `test_outcome.py` | Watching what you do: the would_tick/leave edit baselines both ways, your answer against the confirmation page, signatures on pages at real-looking hosts, and a scripted submit with a password typed and shown as text that never reaches Python. |
| `test_shadow_report.py` | Every scoring rule, the per-portal streak, and the report itself. |

### Real portals

- **A bespoke employer portal** (September 2026): the first live runs.
- **Ashby** (October 2026): a live posting's Yes/No button pairs were
  invisible to the scan; fixed, and copied into `button_pairs.html`.
- **Greenhouse** (October 2026): dry runs on a live posting completed, and a
  full fill ran, and after review that application was submitted, the
  first one end to end on a major ATS. Greenhouse is where three problems
  turned up, all fixed and covered above: every required dropdown appeared
  twice, every run paused for a CAPTCHA that wasn't there, and the first real
  corrections log was entirely false corrections (answered dropdowns read
  back as blank, and a phone number the page had reformatted).
- **Workday** (October 2026): got through Apply and sign-in, then stopped at
  "Autofill with Resume", a page with no form fields. Deferred; see section
  6 and `TODO.md`.

The very first mock form, a 20-field page, wasn't kept. It caught one real
bug: radio groups were overwriting their own reference tag, so every radio
button on every form would have silently failed to fill. The fix is
explained in a comment in `extract.py`.

**Not yet tested:**
- a later run signing in on a real portal with a login Chrome saved;
- Workday past sign-in;
- Lever, iCIMS, Taleo and SuccessFactors.

Mock forms are well-behaved and real portals aren't, so start each new portal
with `python apply.py fill <url> --dry-run`.

The most likely thing to need work is `labelFor` in `extract.py`. That's what
pairs a question with its input box, and it's where unusual portals break
first.

---

## 8. Where to take it next

1. Fill the profile gaps. Highest return per minute, by a wide margin.
2. Dry runs on real Lever postings (Greenhouse and Ashby are done); fix what
   the extractor misses.
3. Finish the Workday adapter properly — step detection, the three-spinner date
   widget, and the account-creation handoff.
4. A discovery feed: poll the public Greenhouse and Lever job APIs for your
   target companies, score postings against your profile, queue the good ones.

Step 4 is where this stops being a utility and becomes a portfolio project. A
retrieval-and-ranking pipeline over live postings with a human-in-the-loop
execution layer is a reasonable thing to have in a student's portfolio, and
the guardrail design in `sensitive.py` is the part worth talking about in an
interview — it's the same instinct as putting a hardware interlock outside the
control loop rather than trusting the controller.
