"""
template.py — the blank profile `init` starts you off with.

WHAT THIS DOES: Holds the empty profile as text, so `python apply.py init` can
write one without the repo carrying a profile-shaped file on disk.

WHY IT MATTERS: The one rule this project will not bend is that nothing
personal lives in the repo. A blank template is harmless, but an example
profile sitting next to the code is one careless edit away from being a real
profile that gets committed. Keeping it as a string removes the temptation.
"""

from __future__ import annotations

BLANK_PROFILE = """\
# =============================================================================
#  YOUR PROFILE
#
#  This is the only file you have to maintain. Everything else reads from it.
#  The agent will never invent a value - if a fact isn't written here, the
#  field gets left blank and handed to you.
#
#  Markers:
#    REQUIRED  you must fill this before the tool is useful
#    VERIFY    something that was read off your resume and may be wrong
#    OPTIONAL  nice to have, costs you a manual step each time if blank
#
#  Run `python apply.py check` at any time to see what's still outstanding.
# =============================================================================

personal:
  first_name:                  # REQUIRED
  middle_name:
  last_name:                   # REQUIRED
  preferred_name:
  pronouns:                    # OPTIONAL
  email:                       # REQUIRED
  phone:                       # REQUIRED

  # REQUIRED - nearly every application asks for a current mailing address.
  address_line_1:
  address_line_2:
  city:
  state:                       # two-letter code, e.g. PA
  state_full:                  # spelled out, e.g. Pennsylvania
  postal_code:
  country: United States
  country_code: US

links:
  linkedin:
  github:
  portfolio:
  orcid:
  google_scholar:

# -----------------------------------------------------------------------------
#  CURRENT STATUS
#
#  What you are doing RIGHT NOW. Forms ask for "Current Employer" and "Current
#  Job Title" whether or not you have one, and the top entry of your work
#  history is the wrong answer once that job has ended. These lines are used
#  verbatim when no experience entry is marked `current: true`.
# -----------------------------------------------------------------------------
current_status:
  employed:                    # true / false
  situation:                   # e.g. Full-time graduate student
  employer_answer:             # what to put in a "Current Employer" box
  job_title_answer:            # what to put in a "Current Job Title" box

# -----------------------------------------------------------------------------
#  ELIGIBILITY  -  READ THIS SECTION CAREFULLY.
#
#  These are legal declarations you are signing, not biography. The agent
#  copies them verbatim from here and no AI model ever sees or guesses them.
#  Wrong values here become wrong answers on a signed document.
#  Anything left blank is handed to you to answer by hand.
# -----------------------------------------------------------------------------
eligibility:
  authorized_to_work_us:       # true / false
  requires_sponsorship_now:    # true / false
  requires_sponsorship_future: # true / false

  # Set this to the exact phrase that describes you:  # REQUIRED
  #   US Citizen / Permanent Resident / F-1 OPT / F-1 STEM OPT / H-1B / TN / other
  work_authorization_type:
  visa_status_notes:

  # You'll meet this question constantly in mining, rail, energy and defense.
  export_control_us_person:    # true / false
  security_clearance:          # e.g. None / Secret / TS-SCI
  clearance_eligible:          # true / false

  willing_to_relocate:         # true / false
  relocation_preferences:
  willing_to_travel_pct:       # e.g. 25
  has_drivers_license:         # true / false
  earliest_start_date:
  internship_start_date:

compensation:
  # Leave both blank and the agent skips salary fields entirely, which is
  # usually what you want - naming a number first rarely helps you.
  # If a form makes the field mandatory, it uses desired_base_min.
  desired_base_min:
  desired_base_max:
  hourly_rate:
  currency: USD
  open_text_answer: Negotiable

# -----------------------------------------------------------------------------
#  EEO / SELF-IDENTIFICATION
#  Voluntary in the US. Declining is always a valid answer and is the default
#  here. Change any line if you'd rather answer.
# -----------------------------------------------------------------------------
eeo:
  gender: Prefer not to say
  race_ethnicity: Prefer not to say
  hispanic_latino: Prefer not to say
  veteran_status: Prefer not to say
  disability_status: Prefer not to say

  # Voluntary diversity questions. Same rule as above: declining is a real
  # answer, not a gap. Portals ask these under a dozen different headings,
  # so answer by meaning, not by the wording you happen to have seen.
  sexual_orientation: Prefer not to say
  transgender: Prefer not to say
  first_generation: Prefer not to say      # first in your family at university
  socioeconomic_background: Prefer not to say

# -----------------------------------------------------------------------------
#  EDUCATION  (most recent first)
#
#  field_of_study is matched against dropdown menus that only list broad
#  headings. Write the real name of your degree; the tool works out the
#  nearest heading a form offers and flags it for you to confirm.
# -----------------------------------------------------------------------------
education:
  - institution:
    degree:                    # e.g. Bachelor of Science
    degree_short:              # e.g. BS
    field_of_study:
    location:
    start_date:                # "YYYY-MM"
    end_date:                  # "YYYY-MM"
    currently_attending:       # true / false
    gpa:                       # leave blank and the agent skips GPA fields
    gpa_scale: 4.0

# -----------------------------------------------------------------------------
#  WORK HISTORY  (most recent first)
#
#  Exact dates matter - Workday validates them and complains about overlaps.
#  Supervisor details get asked far more often than you'd expect, especially
#  by the older portals used in heavy industry.
#
#  Mark your present job `current: true`. If none of them is current, fill in
#  the `current_status` block above instead.
# -----------------------------------------------------------------------------
experience:
  - company:
    title:
    location:
    start_date:                # "YYYY-MM"
    end_date:                  # "YYYY-MM", or blank if this is your current job
    current:                   # true / false
    employment_type:           # Full-time / Part-time / Internship / Contract
    supervisor_name:
    supervisor_title:
    supervisor_email:
    supervisor_phone:
    may_contact:               # true / false
    reason_for_leaving:
    bullets:
      -

# -----------------------------------------------------------------------------
#  SKILLS & CREDENTIALS
# -----------------------------------------------------------------------------
skills:
  languages:
    -
  tools:
    -
  domains:
    -

certifications:
  - name:
    issuer:
    year:

achievements:
  -

references:
  -

# -----------------------------------------------------------------------------
#  DOCUMENTS  -  file names, looked for in ~/.job-agent/documents first.
# -----------------------------------------------------------------------------
documents:
  resume_default:              # REQUIRED
  resume_variants: {}          # e.g.  robotics: Resume_Robotics.pdf
  transcript:

# -----------------------------------------------------------------------------
#  VOICE - this is what stops the drafted answers sounding like a chatbot.
#
#  Write these badly and honestly rather than politely and generically.
#  The model imitates whatever is here, so vague input gives vague output.
# -----------------------------------------------------------------------------
voice:
  style_notes:
  positioning:

  # These get adapted per company. The agent always flags them for your edit
  # and never sends them untouched.
  canned_answers:
    why_this_company:
    why_this_role:
    greatest_strength:
    biggest_weakness:
    tell_me_about_yourself:
    leadership_example:
"""
