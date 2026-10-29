"""
test_preflight.py — the model IDs are checked before the queue starts, with the SDK mocked.

WHAT THIS DOES: Replaces the Anthropic client with a fake whose
models.retrieve answers per ID, and checks config.check_models and the
run-time readiness check built on it: a 404 for a model ID is "missing"
and refuses real jobs with one line naming the ID and its env override;
a network error or a 5xx is "unchecked", a warning, and the run goes on
(as the key check does); an ID that exists is "ok"; the same ID is
asked about once; no completion is ever made; the GUI's runner refuses
a real job with that line in the page's notice and still runs a dry
job. The real API is never called.
WHY IT MATTERS: A retired model ID used to surface, if at all, as a
form full of "unknown" fields; Jack's afternoon of real links must stop
at a banner instead (Jack, 2026-10-29).

Run from anywhere:  .venv/Scripts/python.exe tests/test_preflight.py
"""
import io
from argparse import Namespace
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest import mock

import anthropic
# The SDK's own HTTP library (anthropic 1.7 ships httpx2): the request
# and response an SDK error is built from, never sent anywhere.
try:
    import httpx2 as httpx
except ImportError:           # older SDKs
    import httpx

from _harness import Checks, run_as_script
from agent import config


def _not_found(model_id):
    req = httpx.Request("GET", f"https://api.example.invalid/v1/models/{model_id}")
    return anthropic.NotFoundError("not found", response=httpx.Response(404, request=req), body=None)


def _server_error(model_id):
    req = httpx.Request("GET", f"https://api.example.invalid/v1/models/{model_id}")
    return anthropic.InternalServerError("outage", response=httpx.Response(500, request=req), body=None)


def _connection_error(model_id):
    req = httpx.Request("GET", f"https://api.example.invalid/v1/models/{model_id}")
    return anthropic.APIConnectionError(request=req)


class _FakeClient:
    """The SDK client with one method that answers, and one that must never be called."""

    def __init__(self, answers: dict):
        self.answers = answers
        self.asked: list[str] = []
        self.models = SimpleNamespace(retrieve=self._retrieve)
        self.messages = SimpleNamespace(create=self._never)

    def _retrieve(self, model_id):
        self.asked.append(model_id)
        a = self.answers.get(model_id)
        if isinstance(a, Exception):
            raise a
        return SimpleNamespace(id=model_id)

    def _never(self, *a, **k):
        raise AssertionError("a completion was requested during preflight")


def _with_fake(answers, **patches):
    fake = _FakeClient(answers)
    stack = [mock.patch.object(anthropic, "Anthropic", lambda *a, **k: fake)]
    for name, value in patches.items():
        stack.append(mock.patch.object(config, name, value))
    return fake, stack


def _run(stack, fn):
    with mock.patch("sys.stdout", new_callable=io.StringIO) as out:
        for p in stack:
            p.start()
        try:
            result = fn()
        finally:
            for p in reversed(stack):
                p.stop()
        return result, out.getvalue()


def test_a_missing_model_is_named_with_its_env_override():
    # Fails on the previous commit: config.check_models and
    # apply.preflight_reason do not exist.
    import apply
    check = Checks()
    fake, stack = _with_fake({"claude-gone": _not_found("claude-gone")},
                             MODEL="claude-gone", CLASSIFIER_MODEL="claude-here")
    got, printed = _run(stack, config.check_models)
    check(got == [("missing", "claude-gone", "JOB_AGENT_MODEL", "the API returned 404 for this model ID"),
                  ("ok", "claude-here", "JOB_AGENT_CLASSIFIER_MODEL", "")],
          f"one verdict per ID: missing with its env name, ok for the other ({got})")
    check(fake.asked == ["claude-gone", "claude-here"], f"each ID retrieved once, nothing else ({fake.asked})")
    prof = SimpleNamespace(audit=lambda: {"required": []})
    with mock.patch.object(config, "check_api_key", lambda: ("ok", "")):
        reason, printed = _run(stack, lambda: apply.preflight_reason(prof, False, exit_on_key=False))
    check(reason and "claude-gone" in reason and "JOB_AGENT_MODEL" in reason and "404" in reason,
          f"one line names the model ID and the env override ({reason})")
    check(reason in printed, "and it is printed")
    with mock.patch.object(config, "check_api_key", lambda: ("ok", "")):
        ok, _ = _run(stack, lambda: apply._ready_to_fill(prof, False, exit_on_key=False))
    check(ok is False, "real jobs are refused")
    check.done()


def test_a_network_error_or_a_5xx_warns_and_goes_on():
    # Jack's decision 1 (2026-10-29): only a 404 refuses. Fails on the
    # previous commit (no check_models).
    import apply
    check = Checks()
    prof = SimpleNamespace(audit=lambda: {"required": []})
    for name, err in (("offline", _connection_error("claude-a")), ("outage", _server_error("claude-a"))):
        fake, stack = _with_fake({"claude-a": err, "claude-b": None}, MODEL="claude-a", CLASSIFIER_MODEL="claude-b")
        got, _ = _run(stack, config.check_models)
        check(got[0][:3] == ("unchecked", "claude-a", "JOB_AGENT_MODEL") and got[1][0] == "ok",
              f"{name}: unchecked, not missing ({got})")
        with mock.patch.object(config, "check_api_key", lambda: ("ok", "")):
            reason, printed = _run(stack, lambda: apply.preflight_reason(prof, False, exit_on_key=False))
        check(reason == "" and "claude-a" in printed and "couldn't check" in printed,
              f"{name}: the run goes on with a warning naming the ID ({reason!r}, {printed.strip()[:120]})")
    fake, stack = _with_fake({"claude-same": None}, MODEL="claude-same", CLASSIFIER_MODEL="claude-same")
    got, _ = _run(stack, config.check_models)
    check(fake.asked == ["claude-same"] and [g[0] for g in got] == ["ok", "ok"],
          f"the same ID for both roles is asked about once ({fake.asked}, {got})")
    check.done()


def test_the_gui_refuses_a_real_job_with_the_banner_and_still_runs_a_dry_one():
    # Fails on the previous commit (no banner in the notice; the runner
    # was not a module-level function).
    import apply
    from agent import gui
    check = Checks()
    fake, stack = _with_fake({"claude-gone": _not_found("claude-gone")},
                             MODEL="claude-gone", CLASSIFIER_MODEL="claude-gone")
    state = gui.State()
    prof = SimpleNamespace(audit=lambda: {"required": []})
    calls = []

    def fake_run_one(ctx, url, prof, job_args, pauser, **kw):
        calls.append((url, job_args.dry))
        return apply.DRY, "dry run: landed"
    runner = apply.checked_runner(state, Namespace(force=False))
    with mock.patch.object(config, "check_api_key", lambda: ("ok", "")), \
            mock.patch.object(apply, "run_one", fake_run_one):
        real, printed = _run(stack, lambda: runner(None, "https://x.example/1", prof,
                                                   Namespace(dry=False), None))
        dry, _ = _run(stack, lambda: runner(None, "https://x.example/2", prof, Namespace(dry=True), None))
        again, _ = _run(stack, lambda: runner(None, "https://x.example/3", prof, Namespace(dry=False), None))
    check(real[0] == apply.FAILED and "claude-gone" in real[1] and "JOB_AGENT_MODEL" in real[1],
          f"a real job fails with the banner line ({real})")
    check("claude-gone" in state.notice and "JOB_AGENT_MODEL" in state.notice, f"the page's notice carries it ({state.notice})")
    check(dry == (apply.DRY, "dry run: landed") and calls == [("https://x.example/2", True)],
          f"a dry job still runs; the real one never reached run_one ({dry}, {calls})")
    check(again[0] == apply.FAILED and fake.asked == ["claude-gone"],
          f"checked once per GUI session, every later real job refused ({again[0]}, {fake.asked})")
    check.done()


if __name__ == "__main__":
    run_as_script(globals())
