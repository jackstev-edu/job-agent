"""
profile.py — reading your facts.

WHAT THIS DOES: Loads profile.yaml off disk and answers questions about it,
like "what's his email" or "what's still blank." It also enforces the rule
that a blank field stays blank rather than getting filled with a guess.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from . import config

# Values that all mean "you haven't filled this in yet."
_BLANK = (None, "", "TODO", "todo", "TBD")

# Fields that make the tool meaningfully less useful when left empty.
# Checked by `make check`. Dotted paths; [] matches any list index.
REQUIRED_PATHS = [
    "personal.first_name", "personal.last_name", "personal.email", "personal.phone",
    "personal.address_line_1", "personal.city", "personal.state", "personal.postal_code",
    "eligibility.work_authorization_type",
    "documents.resume_default",
]

RECOMMENDED_PATHS = [
    "links.github",
    "current_status.situation",
    "voice.canned_answers.why_this_company",
    "voice.canned_answers.why_this_role",
    "experience[0].supervisor_name",
]


class Profile:
    """Your facts, loaded into memory."""

    def __init__(self, data: dict[str, Any], path: Path):
        self.data = data
        self.path = path

    @classmethod
    def load(cls, path: str | Path | None = None) -> "Profile":
        path = config.resolve_profile(str(path) if path else None)
        if not path.exists():
            raise FileNotFoundError(
                f"No profile found at {path}.\n"
                f"Run `python apply.py init` to create one from the template.")
        with path.open(encoding="utf-8") as f:
            return cls(yaml.safe_load(f) or {}, path)

    # -- lookups -----------------------------------------------------------

    def get(self, dotted: str, default=None):
        """
        Fetch by dotted path: get('personal.email').
        Supports list indexes: get('experience[0].company').
        Returns `default` for anything blank, so callers never see "TODO".
        """
        node: Any = self.data
        for part in dotted.split("."):
            m = re.match(r"^(\w+)\[(\d+)\]$", part)
            if m:
                key, idx = m.group(1), int(m.group(2))
                if not isinstance(node, dict) or key not in node:
                    return default
                node = node[key]
                if not isinstance(node, list) or idx >= len(node):
                    return default
                node = node[idx]
            else:
                if not isinstance(node, dict) or part not in node:
                    return default
                node = node[part]
        if isinstance(node, str) and node.strip() in _BLANK:
            return default
        return default if node is None else node

    def current_employment(self) -> dict:
        """
        Who you work for RIGHT NOW, resolved in code rather than inferred.

        WHY THIS EXISTS: forms ask for "Current Employer" and "Current Job
        Title" constantly, and the obvious answer — the top entry of your work
        history — is wrong the moment that job has ended. Left to a model, the
        most recent past employer gets offered as the current one, which is a
        false statement on a form you sign. So the rule lives here:

          1. an experience entry marked `current: true` wins;
          2. otherwise the answers you wrote in `current_status`;
          3. otherwise nothing, and the field goes to you.
        """
        for i, job in enumerate(self.data.get("experience") or []):
            if job.get("current") is True:
                return {
                    "known": True,
                    "employed": True,
                    "employer": self.get(f"experience[{i}].company"),
                    "job_title": self.get(f"experience[{i}].title"),
                    "situation": self.get("current_status.situation"),
                    "source": "experience entry marked current",
                }

        employer = self.get("current_status.employer_answer")
        title = self.get("current_status.job_title_answer")
        if employer or title:
            return {
                "known": True,
                "employed": bool(self.get("current_status.employed")),
                "employer": employer,
                "job_title": title,
                "situation": self.get("current_status.situation"),
                "source": "current_status in profile.yaml",
            }

        return {
            "known": False,
            "employed": None,
            "employer": None,
            "job_title": None,
            "situation": self.get("current_status.situation"),
            "source": "nothing in the profile answers this",
        }

    def facts_for_model(self) -> dict:
        """
        Everything the AI is permitted to see.

        Note what's absent: `eligibility`, `eeo` and `compensation`. Those are
        legal or negotiating positions and they're resolved without a model,
        in sensitive.py.
        """
        facts = {
            k: self.data.get(k)
            for k in ("personal", "links", "education", "experience", "skills",
                      "certifications", "achievements", "voice")
            if k in self.data
        }
        # Handed over already resolved, so the model copies an answer instead
        # of working one out from dates. See current_employment().
        facts["current_employment"] = self.current_employment()
        return facts

    # -- completeness ------------------------------------------------------

    def audit(self) -> dict[str, list[str]]:
        """Returns {'required': [...], 'recommended': [...], 'verify': [...]}."""
        required = [p for p in REQUIRED_PATHS if self.get(p) in (None, "")]
        recommended = [p for p in RECOMMENDED_PATHS if self.get(p) in (None, "")]

        # The path can be set while the file itself is missing, which is the
        # more common mistake. Check the file, not the string.
        if self.resume_path() is None and "documents.resume_default" not in required:
            required.append("documents.resume_default (file not found on disk)")
        return {
            "required": required,
            "recommended": recommended,
            "verify": self._verify_comments(),
        }

    def _verify_comments(self) -> list[str]:
        """
        Scrape the `# VERIFY` comments straight out of the YAML source, so the
        check command shows you exactly which lines I guessed at.
        """
        out = []
        for i, line in enumerate(self.path.read_text().splitlines(), 1):
            if "# VERIFY" in line:
                out.append(f"line {i}: {line.split('#')[0].strip() or line.strip()}")
        return out

    # -- documents ---------------------------------------------------------

    def resume_path(self, variant: str | None = None) -> Path | None:
        docs = self.data.get("documents") or {}
        names = []
        if variant:
            names.append((docs.get("resume_variants") or {}).get(variant))
        names.append(docs.get("resume_default"))
        for name in names:
            if name:
                found = self.find_document(name)
                if found:
                    return found
        return None

    def find_document(self, name: str) -> Path | None:
        """
        Resolve a document path from the profile.

        An absolute path is used as given. A relative one is tried against the
        profile's own folder first, then each of the configured document
        directories — so a resume kept privately in ~/.job-agent/documents is
        found without the path in your profile needing to change.
        """
        p = Path(name).expanduser()
        if p.is_absolute():
            return p if p.exists() else None

        roots = [self.path.parent] + list(config.DOCUMENTS_DIRS) + [config.ROOT]
        for root in roots:
            for candidate in (root / p, root / p.name):
                if candidate.exists():
                    return candidate
        return None
