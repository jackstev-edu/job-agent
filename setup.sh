#!/usr/bin/env bash
#
# setup.sh — one command to install everything.
#
# WHAT THIS DOES: Creates an isolated Python environment, installs the three
# libraries this needs, downloads a copy of Chrome for the agent to drive, and
# tells you what's still missing.
#
# WHY BASH: This is orchestration — checking versions, making folders, running
# installers. That's what shell is for, and it means you don't need Python
# working before you can start.

set -euo pipefail

cd "$(dirname "$0")"

bold=$(tput bold 2>/dev/null || echo "")
dim=$(tput dim 2>/dev/null || echo "")
green=$(tput setaf 2 2>/dev/null || echo "")
red=$(tput setaf 1 2>/dev/null || echo "")
amber=$(tput setaf 3 2>/dev/null || echo "")
off=$(tput sgr0 2>/dev/null || echo "")

step() { printf "\n%s==> %s%s\n" "$bold" "$1" "$off"; }
ok()   { printf "    %s✓%s %s\n" "$green" "$off" "$1"; }
warn() { printf "    %s!%s %s\n" "$amber" "$off" "$1"; }
die()  { printf "\n%sError:%s %s\n\n" "$red" "$off" "$1" >&2; exit 1; }

# --- 1. Python ---------------------------------------------------------------
step "Checking Python"

PYTHON=""
for candidate in python3.13 python3.12 python3.11 python3.10 python3; do
    if command -v "$candidate" >/dev/null 2>&1; then
        if "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)'; then
            PYTHON="$candidate"; break
        fi
    fi
done
[ -n "$PYTHON" ] || die "Python 3.10 or newer is required. Install it from python.org, then re-run."
ok "$("$PYTHON" --version)"

# --- 2. virtual environment --------------------------------------------------
# A venv is a private folder of libraries just for this project, so installing
# things here can't break anything else on your machine.
step "Creating the virtual environment"
if [ -d .venv ]; then
    ok ".venv already exists, reusing it"
else
    "$PYTHON" -m venv .venv
    ok "created .venv"
fi

# shellcheck disable=SC1091
source .venv/bin/activate

# --- 3. libraries ------------------------------------------------------------
step "Installing libraries"
python -m pip install --upgrade pip --quiet
python -m pip install -r requirements.txt --quiet
ok "playwright, anthropic, pyyaml"

# --- 4. browser --------------------------------------------------------------
# Playwright ships its own Chromium so the agent never touches your daily
# browser or your personal cookies.
step "Downloading Chromium (about 150 MB, one time only)"
if python -m playwright install chromium 2>/dev/null; then
    ok "Chromium ready"
else
    warn "Chromium download failed — run 'source .venv/bin/activate && playwright install chromium' manually"
fi

# --- 5. folders --------------------------------------------------------------
step "Preparing folders"
mkdir -p documents
python - <<'PY'
from agent import config
config.ensure_dirs()
print(f"    working folder: {config.STATE_DIR}")
PY
ok "documents/ and ~/.job-agent/ ready"

# --- 6. what's left ----------------------------------------------------------
step "What's still needed"

if [ -z "${ANTHROPIC_API_KEY:-}" ]; then
    warn "ANTHROPIC_API_KEY is not set."
    printf "      Get one at console.anthropic.com, then add this to ~/.zshrc or ~/.bashrc:\n"
    printf "        %sexport ANTHROPIC_API_KEY=sk-ant-...%s\n" "$dim" "$off"
else
    ok "ANTHROPIC_API_KEY is set"
fi

if ls documents/*.pdf >/dev/null 2>&1; then
    ok "found a resume in documents/"
else
    warn "no PDF in documents/ — put your resume there and check the path in profile.yaml"
fi

printf "\n%sDone.%s Next:\n\n" "$green" "$off"
printf "  source .venv/bin/activate\n"
printf "  make check                 %s# see what's missing from your profile%s\n" "$dim" "$off"
printf "  make fill URL=<posting>    %s# fill an application%s\n\n" "$dim" "$off"
