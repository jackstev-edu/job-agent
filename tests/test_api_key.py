"""
test_api_key.py — a rejected API key stops `fill` before the browser opens.

WHAT THIS DOES: Points the real Anthropic SDK at a tiny local server that
answers 401, 500 or 200, and checks what config.check_api_key makes of each;
then runs `fill --dry-run` against a 401 and checks it exits without a browser.
WHY IT MATTERS: A dead key used to look like a form full of "unknown" fields.
Only a 401 (or no key at all) may stop a run; every other failure still falls
through to the classifier's fail-safe.

Run from anywhere:  .venv/Scripts/python.exe tests/test_api_key.py
"""
import os
import threading
from argparse import Namespace
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer

from _harness import Checks, run_as_script
from agent import config

_STUB_BODIES = {
    200: b'{"data": [], "has_more": false, "first_id": null, "last_id": null}',
    401: b'{"type": "error", "error": {"type": "authentication_error", "message": "invalid x-api-key"}}',
    500: b'{"type": "error", "error": {"type": "api_error", "message": "stub outage"}}',
}


@contextmanager
def stub_api(status: int, key: str | None = "sk-ant-test-not-a-real-key"):
    """Serve every request with `status`, with the SDK pointed at it."""
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(status)
            self.send_header("content-type", "application/json")
            self.end_headers()
            self.wfile.write(_STUB_BODIES[status])

        def log_message(self, *a):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    names = ("ANTHROPIC_BASE_URL", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")
    saved = {k: os.environ.get(k) for k in names}
    os.environ["ANTHROPIC_BASE_URL"] = f"http://127.0.0.1:{server.server_port}"
    os.environ.pop("ANTHROPIC_AUTH_TOKEN", None)
    if key is None:
        os.environ.pop("ANTHROPIC_API_KEY", None)
    else:
        os.environ["ANTHROPIC_API_KEY"] = key
    try:
        yield
    finally:
        server.shutdown()
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def test_check_api_key_statuses():
    check = Checks()
    for status, expected in ((200, "ok"), (401, "rejected"), (500, "unchecked")):
        with stub_api(status):
            got, detail = config.check_api_key()
        check(got == expected, f"HTTP {status} -> {expected!r} (got {got!r}: {detail})")
    with stub_api(200, key=None):
        got, detail = config.check_api_key()
    check(got == "missing", f"no key at all -> 'missing' (got {got!r}: {detail})")
    check.done()


def test_fill_stops_before_the_browser_on_401():
    import apply

    class FakeProfile:
        @classmethod
        def load(cls, *a, **k):
            return cls()

        def audit(self):
            return {"required": [], "recommended": [], "verify": []}

    def no_browser():
        raise AssertionError("the browser was opened")

    saved = (apply.Profile, apply.tracker.find, apply.browser_mod.browser)
    apply.Profile = FakeProfile
    apply.tracker.find = lambda url: None
    apply.browser_mod.browser = no_browser
    check = Checks()
    args = Namespace(profile=None, url="https://example.invalid/job", force=False,
                     dry_run=True, resume=None, cover_letter=False)
    try:
        with stub_api(401):
            try:
                apply.cmd_fill(args)
                code = None
            except SystemExit as e:
                code = e.code
    finally:
        apply.Profile, apply.tracker.find, apply.browser_mod.browser = saved
    check(code == 1, f"dry run exits with status 1 on a 401 (got {code!r})")
    check.done()


if __name__ == "__main__":
    run_as_script(globals())
