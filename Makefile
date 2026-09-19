# Makefile — short names for the commands you'll actually type.
#
# WHAT THIS DOES: Turns long commands into short ones. `make fill URL=...`
# instead of remembering to activate the virtual environment and type the
# whole python invocation every time.
#
# WHY MAKE: It's already on every Mac and Linux machine, it needs no install,
# and `make help` gives you a menu. On Windows, use the commands in the
# right-hand column directly.

PY := .venv/bin/python
SHELL := /bin/bash

.DEFAULT_GOAL := help
.PHONY: help setup init doctor check fill dry list followup clean

help:  ## Show this menu
	@echo ""
	@echo "  job-agent"
	@echo ""
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[1m%-12s\033[0m %s\n", $$1, $$2}'
	@echo ""
	@echo "  Examples:"
	@echo "    make fill URL=https://job-boards.greenhouse.io/acme/jobs/123"
	@echo "    make dry  URL=https://jobs.lever.co/acme/abc-123"
	@echo "    make fill URL=... RESUME=robotics LETTER=1"
	@echo ""

setup:  ## Install everything (run this first)
	@bash setup.sh

init:  ## Create your profile outside the repo (run once). MIGRATE=1 to move an existing one
	@$(PY) apply.py init $(if $(MIGRATE),--migrate,)

doctor:  ## Check this machine is set up correctly
	@$(PY) apply.py doctor

check:  ## Show what's still missing from your profile
	@$(PY) apply.py check $(if $(V),--verbose,)

fill:  ## Fill an application. Needs URL=. Optional RESUME=, LETTER=1
	@test -n "$(URL)" || { echo "Usage: make fill URL=<job posting url>"; exit 1; }
	@$(PY) apply.py fill "$(URL)" \
		$(if $(RESUME),--resume $(RESUME),) \
		$(if $(LETTER),--cover-letter,) \
		$(if $(FORCE),--force,)

dry:  ## Plan an application without touching the page. Needs URL=
	@test -n "$(URL)" || { echo "Usage: make dry URL=<job posting url>"; exit 1; }
	@$(PY) apply.py fill "$(URL)" --dry-run $(if $(RESUME),--resume $(RESUME),)

list:  ## Show everything you've applied to
	@$(PY) apply.py list $(if $(STATUS),--status $(STATUS),)

followup:  ## Applications with no reply. Optional DAYS=
	@$(PY) apply.py followup --days $(or $(DAYS),14)

clean:  ## Remove the virtual environment and Python caches
	@rm -rf .venv **/__pycache__ __pycache__
	@echo "Removed .venv and caches. Your profile, logs and logins are untouched."
