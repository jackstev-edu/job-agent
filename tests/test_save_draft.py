"""
test_save_draft.py — a cover-letter draft lands in the private folder.

WHAT THIS DOES: Points JOB_AGENT_HOME at a temp folder, saves a draft, and
checks the file exists there with the text in it — and nowhere in the repo.
WHY IT MATTERS: save_draft once failed on every run behind a generic "cover
letter failed" message, and a draft names a company you're applying to.

Run from anywhere:  .venv/Scripts/python.exe tests/test_save_draft.py
"""
import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

failures = []


def check(cond, what):
    print(("  ok   " if cond else "  FAIL ") + what)
    if not cond:
        failures.append(what)


with tempfile.TemporaryDirectory() as home:
    # config reads JOB_AGENT_HOME at import, so this has to come first.
    os.environ["JOB_AGENT_HOME"] = home
    from agent import config, letters

    repo_docs_before = set((REPO / "documents").glob("*"))
    path = letters.save_draft("Dear team,\n\nA test body.", "Acme Corp", "Test Engineer")
    print(f"  wrote {path}")

    check(config.STATE_DIR == Path(home), "config picked up the temp JOB_AGENT_HOME")
    check(path.is_file(), "the draft file exists")
    check(path.parent == Path(home) / "documents", "it's in <JOB_AGENT_HOME>/documents")
    check(REPO not in path.resolve().parents, "it's not inside the repo")
    text = path.read_text()
    check("A test body." in text and "Acme Corp" in text and "Test Engineer" in text,
          "it holds the body, company and role")
    check(path.name.endswith("-acme-corp-test-engineer.md"), "filename slugs company and role")
    check(set((REPO / "documents").glob("*")) == repo_docs_before,
          "nothing new appeared in the repo's documents/")

print(f"\n{'ALL PASSED' if not failures else f'{len(failures)} FAILED'}")
sys.exit(1 if failures else 0)
