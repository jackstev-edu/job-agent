"""
conftest.py — pytest's first import in this folder.

WHAT THIS DOES: Imports _harness before any test module, so JOB_AGENT_HOME
already points at a throwaway folder when the first agent module loads.
WHY IT MATTERS: Under pytest every test shares one process, and config reads
JOB_AGENT_HOME only once; whichever test imported it first would otherwise
decide whether the rest write into your real ~/.job-agent.
"""
import _harness  # noqa: F401
