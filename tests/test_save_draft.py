"""
test_save_draft.py — a cover-letter draft lands in the private folder.

WHAT THIS DOES: Saves a draft with JOB_AGENT_HOME pointed at the harness's temp
folder, and checks the file exists there with the text in it — and nowhere in
the repo.
WHY IT MATTERS: save_draft once failed on every run behind a generic "cover
letter failed" message, and a draft names a company you're applying to.

Run from anywhere:  .venv/Scripts/python.exe tests/test_save_draft.py
"""
from pathlib import Path

from _harness import REPO, TEST_HOME, Checks, run_as_script
from agent import config, letters


def test_draft_lands_in_private_folder():
    check = Checks()
    repo_docs_before = set((REPO / "documents").glob("*"))
    path = letters.save_draft("Dear team,\n\nA test body.", "Acme Corp", "Test Engineer")
    print(f"  wrote {path}")

    check(config.STATE_DIR == TEST_HOME, "config picked up the temp JOB_AGENT_HOME")
    check(path.is_file(), "the draft file exists")
    check(path.parent == TEST_HOME / "documents", "it's in <JOB_AGENT_HOME>/documents")
    check(REPO not in path.resolve().parents, "it's not inside the repo")
    text = path.read_text()
    check("A test body." in text and "Acme Corp" in text and "Test Engineer" in text,
          "it holds the body, company and role")
    check(path.name.endswith("-acme-corp-test-engineer.md"), "filename slugs company and role")
    check(set((REPO / "documents").glob("*")) == repo_docs_before,
          "nothing new appeared in the repo's documents/")
    check(Path.home() / ".job-agent" not in path.parents, "not in the real ~/.job-agent")
    check.done()


if __name__ == "__main__":
    run_as_script(globals())
