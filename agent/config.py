"""
config.py — where everything lives.

WHAT THIS DOES: Holds all the file paths and settings in one place, so if you
ever want to move something you change it here and nowhere else. It also
reads your API key out of the environment rather than storing it in a file.
"""

from __future__ import annotations

import os
from pathlib import Path

# The project folder (wherever you cloned this to).
ROOT = Path(__file__).resolve().parent.parent

# Everything personal lives here, OUTSIDE the project folder, so the repo can
# be shared or made public without you ever having to think about it. Your
# profile, your resume, your application log and your saved browser logins
# are all under this one directory.
STATE_DIR = Path(os.environ.get("JOB_AGENT_HOME", Path.home() / ".job-agent"))

# The blank template that ships with the repo. Contains no personal data.
TEMPLATE_PATH = ROOT / "profile.example.yaml"


def resolve_profile(explicit: str | None = None) -> Path:
    """
    Find your profile, in order of preference:
      1. --profile on the command line
      2. the JOB_AGENT_PROFILE environment variable
      3. ~/.job-agent/profile.yaml          <- the normal place
      4. ./profile.yaml                     <- legacy, warned about on use
    """
    if explicit:
        return Path(explicit).expanduser()
    if os.environ.get("JOB_AGENT_PROFILE"):
        return Path(os.environ["JOB_AGENT_PROFILE"]).expanduser()

    private = STATE_DIR / "profile.yaml"
    if private.exists():
        return private

    legacy = ROOT / "profile.yaml"
    if legacy.exists():
        print("  ! Your profile is sitting inside the project folder, where a "
              "careless `git add` would commit it.")
        print("    Run `python apply.py init --migrate` to move it somewhere private.")
        return legacy

    return private          # doesn't exist yet; `init` creates it


# Resumes, cover letters, transcripts. Searched in this order, so a document
# kept privately always wins over one left in the repo.
DOCUMENTS_DIRS = [STATE_DIR / "documents", ROOT / "documents"]
PROFILE_PATH = STATE_DIR / "profile.yaml"           # where `init` puts it
CHROME_PROFILE_DIR = STATE_DIR / "chrome-profile"   # your saved logins
DATABASE_PATH = STATE_DIR / "applications.db"       # your application log
REPORTS_DIR = STATE_DIR / "reports"                 # the HTML review sheets

# Which Claude model does the field matching. Sonnet is the right balance of
# speed and care here; override with JOB_AGENT_MODEL if you want to experiment.
MODEL = os.environ.get("JOB_AGENT_MODEL", "claude-sonnet-5")

# Sorting questions into categories is an easier job than answering them, so
# it runs on a smaller, faster model. Override if you want them identical.
CLASSIFIER_MODEL = os.environ.get("JOB_AGENT_CLASSIFIER_MODEL", "claude-haiku-4-5-20251001")

# Set JOB_AGENT_HEADLESS=1 to hide the browser window. Not recommended:
# headless browsers get flagged as bots far more often, and you want to watch.
HEADLESS = os.environ.get("JOB_AGENT_HEADLESS") == "1"

# Milliseconds of delay between browser actions. Slower is more reliable on
# slow-rendering portals and much easier to follow with your eyes.
SLOW_MO = int(os.environ.get("JOB_AGENT_SLOWMO", "120"))


def ensure_dirs() -> None:
    for d in (STATE_DIR, CHROME_PROFILE_DIR, REPORTS_DIR, STATE_DIR / "documents"):
        d.mkdir(parents=True, exist_ok=True)
