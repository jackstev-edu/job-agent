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

The repo carries only `profile.example.yaml`, a blank template with no personal
data in it. You can make this repo public without editing a thing.

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

```bash
cd job-agent
./setup.sh
python apply.py init     # creates your profile outside the repo
```

That checks your Python version, creates an isolated environment, installs the
three libraries, downloads a private copy of Chrome, and tells you what's
missing. Takes about two minutes, mostly the browser download.

Then get an API key from [console.anthropic.com](https://console.anthropic.com)
and add it to your shell profile (`~/.zshrc` on a Mac, `~/.bashrc` on Linux):

```bash
export ANTHROPIC_API_KEY=sk-ant-...
```

Open a new terminal, then:

```bash
source .venv/bin/activate
make doctor     # confirms everything is wired up
make check      # confirms your profile is complete
```

**Windows:** `make` and `setup.sh` won't run natively. Either use WSL, which is
the easy path, or run the underlying commands directly — `python -m venv .venv`,
`.venv\Scripts\activate`, `pip install -r requirements.txt`,
`playwright install chromium`, then `python apply.py <command>` instead of
`make <command>`.

Cost, for planning: each application costs a few cents of API usage. A hundred
applications is somewhere under five dollars.

---

## 3. Using it

```bash
make dry URL=https://job-boards.greenhouse.io/company/jobs/1234
```

Do this first, two or three times. A dry run reads the form and prints what it
*would* enter without touching anything. It's how you build trust in it.

```bash
make fill URL=https://job-boards.greenhouse.io/company/jobs/1234
```

The real thing. A Chrome window opens, the agent works out which portal it's
looking at, uploads your resume, fills the form, and stops. A review sheet
opens in your browser listing what it did and what needs you. You fix those
items in the form and click Submit.

Options:

```bash
make fill URL=... RESUME=robotics    # use a resume variant
make fill URL=... LETTER=1           # also draft a cover letter
make list                            # everything you've applied to
make list STATUS=submitted
make followup DAYS=21                # who hasn't replied
make help                            # the full menu
```

When something goes wrong — and it will, on Workday — the browser window stays
open. Finish by hand, and note which selector failed so you can fix it.

---

## 4. What each piece does

Two sentences each.

### The things you touch

**`profile.yaml`** — Every fact about you, in one file. Everything else reads
from it, and nothing is ever invented that isn't written here.

**`setup.sh`** (Bash) — Installs the whole toolchain in one command. Written in
shell because it's pure orchestration and works before Python is set up.

**`Makefile`** (Make) — Turns long commands into short ones. Not available on
Windows; use `python apply.py <command>` instead.

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
inherits that session. It also spots CAPTCHAs and login walls and stops, rather
than trying to get around them.

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

**`agent/report.py`** (HTML + CSS) — Writes the review sheet: one page listing
every question, what went in, and where it came from, with the things needing
your attention at the top. Written as HTML because you read it in a browser
next to the form itself, which turns a ten-minute check into a one-minute one.

---

## 5. Why it doesn't submit

Three reasons, and I'd argue against removing the gate.

**Quality.** Applications sent by a bot read like applications sent by a bot.
For the roles your background points at — robotics, autonomy, industrial ML —
one considered application beats forty generic ones, and the review step is
where "considered" happens.

**Accuracy.** An AI that invents a graduation date on a form you sign has made
you misrepresent yourself. The review sheet is the last check before that
becomes real.

**Legitimacy.** Most career portals are fine with tools that help you fill their
form. Very few are fine with a fully autonomous submitter, and the ones that
detect it ban the account. Stopping before Submit keeps you on the right side
of that without having to think about it each time.

## What it deliberately won't do

- **Solve CAPTCHAs.** It detects them, names them, and hands you the browser.
- **Enter passwords or create accounts.** You log in once by hand; the session
  persists in `~/.job-agent/chrome-profile`.
- **Touch LinkedIn Easy Apply.** LinkedIn's terms prohibit automated access, and
  restrictions there are common and painful to reverse. Click those by hand.
- **Answer eligibility, EEO, or criminal-history questions with a model.**
- **Click Submit.**

---

## 6. What to expect from each portal

**Greenhouse, Lever, Ashby** — should mostly work first time. Lever has one
gotcha already handled: its resume autoparse overwrites text fields, so upload
happens first.

**Workday** — where your evenings will go. Multi-step wizard, account required
per employer, element IDs that regenerate between renders, dates split across
three separate spinners. The adapter knows to key off `data-automation-id`
instead of `id` and will fill one step at a time. Roughly 40% of large-employer
applications, so it's worth finishing properly.

**iCIMS, Taleo, SuccessFactors** — old, iframe-heavy, session-timeout prone, and
very common at exactly the industrial employers your Liebherr background suits.
The adapters are honest stubs.

**Anything else** falls through to the generic path and flags more for review.

---

## 7. What's been tested, and what hasn't

Run end to end against a mock application form with 20 fields — text inputs,
dropdowns, radio groups, checkboxes, textareas, a file upload, and a decoy
search box. Verified in the live DOM:

- every text field, dropdown and radio group received the right value
- the resume uploaded
- the search box was correctly ignored
- **Desired Salary was left blank**, because it's blank in your profile
- **"I certify the above is true and complete" was left unticked**
- **"Have you previously been employed here?" was left blank**
- "now or in the future require sponsorship" was answered from the *future*
  field, not the *now* field — those are different questions and the rule order
  in `sensitive.py` gets it right

That test caught one real bug on the first run: radio groups were overwriting
their own reference tag, so every radio button on every form would have
silently failed to fill. Fixed, with a comment explaining why at
`extract.py`.

**Not yet tested:** real portals. Mock forms are well-behaved and Workday is
not. Browser automation always needs a first session of fixing selectors
against live pages, so start with `make dry` on a couple of real Greenhouse or
Lever postings.

The most likely thing to need work is `labelFor` in `extract.py` — that's what
pairs a question with its input box, and it's where unusual portals break first.

---

## 8. Where to take it next

1. Fill the profile gaps. Highest return per minute, by a wide margin.
2. Three dry runs on real Greenhouse and Lever postings; fix what the extractor
   misses.
3. Finish the Workday adapter properly — step detection, the three-spinner date
   widget, and the account-creation handoff.
4. A discovery feed: poll the public Greenhouse and Lever job APIs for your
   target companies, score postings against your profile, queue the good ones.

Step 4 is where this stops being a utility and becomes a portfolio project. A
retrieval-and-ranking pipeline over live postings with a human-in-the-loop
execution layer is a reasonable thing to have on a CMU AI student's GitHub, and
the guardrail design in `sensitive.py` is the part worth talking about in an
interview — it's the same instinct as putting a hardware interlock outside the
control loop rather than trusting the controller.
