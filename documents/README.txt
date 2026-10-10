This folder is a fallback only. Your real documents belong in the private
folder instead:

    ~/.job-agent/documents/          (Windows: C:\Users\<you>\.job-agent\documents\)

Run `python apply.py init` to create it. Anything you drop in there is found
automatically and is never in this repo.

Files placed HERE are gitignored, but keeping them out of the project folder
entirely is the safer habit.
