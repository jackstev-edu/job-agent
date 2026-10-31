"""
test_suggestions.py — suggestions Jack approves with one click, and nothing before.

WHAT THIS DOES: Drives learn.suggestions, approve.py, the router's alias
route and the GUI's Suggestions endpoints on a temporary profile. Apply
writes exactly one line of profile.yaml with every comment kept and a
backup beside it; a file that changed since the list was made refuses;
a legal / EEO / pay suggestion carries no value in /state and no Apply
(the endpoint refuses it too); an alias to a legal line, or to a
PROFILE_PATHS target, is refused; a field an alias names that HARD_STOPS
catches still comes to Jack, and one that clears both layers is answered
from the line; a dismissed suggestion stays dismissed for that value and
returns for another; the endpoints refuse a missing token, a foreign
Origin and a wrong Host.
WHY IT MATTERS: Decision B (Jack, 2026-10-29 evening): learning is
"suggest, I approve". Nothing is reused until he clicks, and no click can
write a legal, eligibility, EEO or pay line or move a field out of "me".

Run from anywhere:  .venv/Scripts/python.exe tests/test_suggestions.py
"""
import http.client
import json
import os
from unittest import mock

from _harness import TEST_HOME, Checks, run_as_script
from agent import approve, gui, learn, pipeline, template
from agent.profile import Profile

A, B = "https://a.example/jobs/1", "https://b.example/jobs/2"


def _row(url, label, category, kind, agent, final, **extra):
    return {"url": url, "label": label, "type": "text", "category": category, "kind": kind,
            "agent_value": agent, "final_value": final, "log_version": 2, **extra}


def _profile(name):
    """A profile from the blank template with a few lines filled: comments intact, nothing personal."""
    path = TEST_HOME / f"suggest-{name}.yaml"
    text = template.BLANK_PROFILE.replace("  first_name:                  # REQUIRED", "  first_name: Test                   # REQUIRED")
    text = text.replace("  github:\n", "  github: github.com/old\n")
    text = text.replace("  preferred_name:\n", "  preferred_name: Tee\n")
    path.write_text(text, encoding="utf-8")
    for extra in (approve.aliases_path(path), approve.dismissed_path(path)):
        extra.unlink(missing_ok=True)
    return path


def _req(server, method, path, body=None, token="ok", origin=None, host=None):
    headers = {"Content-Type": "application/json"}
    if token is not None:
        headers["X-Token"] = server.token if token == "ok" else token
    if origin:
        headers["Origin"] = origin
    headers["Host"] = host or f"127.0.0.1:{server.port}"
    for attempt in (1, 2, 3):
        conn = http.client.HTTPConnection("127.0.0.1", server.port, timeout=30)
        try:
            conn.request(method, path, body=json.dumps(body) if body is not None else None, headers=headers)
            resp = conn.getresponse()
            raw = resp.read().decode("utf-8")
            break
        except (ConnectionAbortedError, ConnectionResetError):
            # The loopback abort seen once per round on Windows (TODO, the
            # 2026-10-29 round): the server closes a refused request before
            # the client has finished sending. Retried, never a verdict.
            if attempt == 3:
                raise
        finally:
            conn.close()
    try:
        return resp.status, json.loads(raw)
    except ValueError:
        return resp.status, raw


def test_apply_writes_exactly_one_line_with_comments_kept_and_a_backup():
    # Fails on 2c42879: no agent.approve.
    check = Checks()
    path = _profile("apply")
    before = path.read_text(encoding="utf-8")
    stamp = approve.profile_stamp(path)
    backup = approve.apply_profile_line(path, "personal.middle_name", "Q.", stamp)
    after = path.read_text(encoding="utf-8")
    diff = [(b, a) for b, a in zip(before.splitlines(), after.splitlines()) if b != a]
    check(len(before.splitlines()) == len(after.splitlines()) and len(diff) == 1
          and diff[0][0].strip() == "middle_name:" and diff[0][1].strip() == "middle_name: Q.",
          f"exactly one line changed ({diff})")
    check(after.count("#") == before.count("#") and "# REQUIRED" in after and "#  YOUR PROFILE" in after,
          "every comment kept")
    check(backup.exists() and backup.read_text(encoding="utf-8") == before and backup.parent == path.parent
          and backup.name.startswith(path.name + ".bak-"), f"a timestamped backup holds the old file ({backup.name})")
    check(Profile.load(path).get("personal.middle_name") == "Q." and Profile.load(path).get("personal.first_name") == "Test",
          "the new value loads; the rest is as it was")
    # A list entry, and a blank top-level line (add).
    stamp = approve.profile_stamp(path)
    approve.apply_profile_line(path, "experience[0].company", "Example Co", stamp)
    check(Profile.load(path).get("experience[0].company") == "Example Co", "a list entry can be written")
    for line in ("eligibility.authorized_to_work_us", "eeo.gender", "compensation.desired_base_min",
                 "per_company.defaults.referral_source", "documents.resume_default", "nonsense"):
        try:
            approve.apply_profile_line(path, line, "x", approve.profile_stamp(path))
            refused = False
        except approve.Refused:
            refused = True
        check(refused, f"a click never writes `{line}`")
    check.done()


def test_a_changed_file_refuses():
    # Fails on 2c42879: no agent.approve.
    check = Checks()
    path = _profile("changed")
    stamp = approve.profile_stamp(path)
    path.write_text(path.read_text(encoding="utf-8").replace("preferred_name: Tee", "preferred_name: Tea"), encoding="utf-8")
    try:
        approve.apply_profile_line(path, "personal.middle_name", "Q.", stamp)
        refused = False
    except approve.Changed:
        refused = True
    text = path.read_text(encoding="utf-8")
    check(refused and "middle_name:\n" in text and "preferred_name: Tea" in text and not list(path.parent.glob(path.name + ".bak-*")),
          "a file changed since the list was made: refused, untouched, no backup")
    check.done()


def _sensitive_rows():
    return [_row(A, "Gender", "eeo", "overwrote", None, None, profile_path="eeo.gender"),
            _row(A, "Are you legally authorized to work in the US?", "work_authorization", "overwrote", None, None),
            # The plan named the (blank) line, as a profile-routed field's does.
            _row(B, "Middle name", "personal_fact", "filled_blank", None, "Q.", profile_path="personal.middle_name")]


def test_a_sensitive_suggestion_has_no_apply_and_no_value_anywhere():
    # Fails on 2c42879: /state has no suggestions and the endpoint is 404.
    check = Checks()
    path = _profile("sensitive")
    learn.corrections_path().unlink(missing_ok=True)
    from agent import corrections
    corrections.append(_sensitive_rows())
    state = gui.State()
    state.profile_path = path
    server = gui.Server(state).start()
    try:
        state.refresh_suggestions()
        status, body = _req(server, "GET", "/state")
        sugg = body.get("suggestions") or []
        kinds = {s["kind"] for s in sugg}
        sens = [s for s in sugg if s["kind"] == "sensitive"]
        check(status == 200 and len(sens) == 2 and "add" in kinds,
              f"two sensitive suggestions and one addition are listed ({[(s['kind'], s['line'] or s['label']) for s in sugg]})")
        check(all(s["value"] is None and s["current"] is None and s["applicable"] is False
                  and "by hand" in s["text"] for s in sens), f"edit by hand, no value, no Apply ({sens})")
        text = json.dumps(body)
        check("Decline" not in text and "Prefer not" not in text, "no EEO value anywhere in /state")
        before = path.read_text(encoding="utf-8")
        status, body = _req(server, "POST", "/suggestion/apply", {"id": sens[0]["id"]},
                            origin=f"http://127.0.0.1:{server.port}")
        check(status == 200 and body.get("ok") is False and "by hand" in body.get("error", "")
              and path.read_text(encoding="utf-8") == before, f"Apply on it is refused and writes nothing ({body})")
        add = next(s for s in sugg if s["kind"] == "add")
        status, body = _req(server, "POST", "/suggestion/apply", {"id": add["id"]},
                            origin=f"http://127.0.0.1:{server.port}")
        check(status == 200 and body.get("ok") is True and Profile.load(path).get("personal.middle_name") == "Q."
              and state.take_profile_changed() is True,
              f"Apply on the ordinary addition writes the line and flags the reload ({body})")
        check(not any(s["id"] == add["id"] for s in state.snapshot()["suggestions"]),
              "an applied suggestion leaves the list (the profile now holds the value)")
    finally:
        server.stop()
        learn.corrections_path().unlink(missing_ok=True)
    check.done()


def test_an_alias_to_a_legal_line_is_refused():
    # Fails on 2c42879: no agent.approve.
    check = Checks()
    path = _profile("alias-legal")
    for line in ("eligibility.authorized_to_work_us", "eeo.veteran_status", "compensation.hourly_rate",
                 "per_company.defaults.previously_employed", "eligibility.security_clearance"):
        try:
            approve.add_alias(path, "Some question", line)
            refused = False
        except approve.Refused:
            refused = True
        check(refused and not approve.aliases_path(path).exists(), f"an alias to `{line}` is refused")
    approve.add_alias(path, "Nickname *", "personal.preferred_name")
    check(approve.load_aliases(path) == {"nickname": "personal.preferred_name"}, "an ordinary alias is recorded, normalised")
    # Hand-edited to a sensitive line: ignored on load.
    approve.aliases_path(path).write_text("nickname: personal.preferred_name\nvisa: eligibility.work_authorization_type\n",
                                          encoding="utf-8")
    check(approve.load_aliases(path) == {"nickname": "personal.preferred_name"}, "a hand-edited sensitive alias is never loaded")
    check.done()


def test_an_aliased_field_hard_stops_catches_still_comes_to_jack():
    # Fails on 2c42879: no alias route at all (the ordinary field below went
    # to the model, not the line).
    check = Checks()
    path = _profile("alias-route")
    approve.add_alias(path, "Nickname", "personal.preferred_name")
    approve.add_alias(path, "Have you ever been convicted of a felony?", "personal.preferred_name")
    approve.add_alias(path, "Do you require visa sponsorship?", "personal.preferred_name")
    prof = Profile.load(path)
    fields = [{"ref": "g1-f0", "label": "Nickname", "type": "text", "name": "nick"},
              {"ref": "g1-f1", "label": "Have you ever been convicted of a felony?", "type": "text", "name": "x"},
              {"ref": "g1-f2", "label": "Do you require visa sponsorship?", "type": "text", "name": "y"},
              {"ref": "g1-f3", "label": "Nickname", "type": "text", "name": "nick2", "raw_stop": True}]
    cats = {"g1-f0": "personal_fact", "g1-f1": "personal_fact", "g1-f2": "personal_fact", "g1-f3": "personal_fact"}
    plan, flags, for_model = pipeline.route(fields, cats, prof, set())
    check(plan.get("g1-f0", {}).get("value") == "Tee" and plan["g1-f0"]["source"] == "alias"
          and plan["g1-f0"]["profile_path"] == "personal.preferred_name" and not any(f["ref"] == "g1-f0" for f in for_model),
          f"a field that clears both layers is answered from the aliased line, not the model ({plan.get('g1-f0')})")
    flagged = {f["ref"] for f in flags}
    check({"g1-f1", "g1-f2", "g1-f3"} <= flagged and not any(r in plan for r in ("g1-f1", "g1-f2", "g1-f3")),
          f"HARD_STOPS, LEGAL_GUARD and the page's raw_stop still send the field to Jack, alias or not ({flagged})")
    # The classifier's layer: a category routed to "me" is never aliased.
    plan, flags, for_model = pipeline.route([fields[0]], {"g1-f0": "work_authorization"}, prof, set())
    check("g1-f0" not in plan and any(f["ref"] == "g1-f0" for f in flags), "a category the classifier routes to Jack stays his")
    # A draft field is left to the model (it is adapted, not copied).
    plan, flags, for_model = pipeline.route([fields[0]], {"g1-f0": "free_text"}, prof, set())
    check("g1-f0" not in plan and any(f["ref"] == "g1-f0" for f in for_model), "a draft field is not aliased")
    check.done()


def test_a_dismissed_suggestion_stays_dismissed():
    # Fails on 2c42879: no suggestions / dismissals.
    check = Checks()
    path = _profile("dismiss")
    rows = [_row(A, "GitHub URL", "personal_fact", "overwrote", "github.com/old", "https://github.com/new")]
    data = Profile.load(path).data
    sugg = learn.suggestions(rows, data)
    check(len(sugg) == 1 and sugg[0]["kind"] == "edit" and sugg[0]["line"] == "links.github"
          and sugg[0]["count"] == 1 and sugg[0]["postings"] == 1, f"a single correction is a suggestion with its count ({sugg})")
    approve.dismiss(path, sugg[0]["id"])
    dismissed = approve.load_dismissed(path)
    check(learn.suggestions(rows, data, dismissed) == [], "dismissed: gone")
    rows2 = rows + [_row(B, "GitHub URL", "personal_fact", "overwrote", "github.com/old", "https://github.com/new")]
    check(learn.suggestions(rows2, data, dismissed) == [], "the same value on another posting stays dismissed")
    rows3 = rows + [_row(B, "GitHub URL", "personal_fact", "overwrote", "github.com/old", "https://github.com/newer")]
    check(learn.suggestions(rows3, data, dismissed) == [], "two different values: no single suggestion")
    rows4 = [_row(B, "GitHub URL", "personal_fact", "overwrote", "github.com/old", "https://github.com/newer")]
    again = learn.suggestions(rows4, data, dismissed)
    check(len(again) == 1 and again[0]["value"] == "https://github.com/newer", "another value comes back as its own suggestion")
    check.done()


def test_the_endpoints_refuse_a_missing_token_foreign_origin_or_wrong_host():
    # Fails on 2c42879: both routes were "no such action" (404) for every call.
    check = Checks()
    path = _profile("endpoints")
    state = gui.State()
    state.profile_path = path
    server = gui.Server(state).start()
    try:
        for route in ("/suggestion/apply", "/suggestion/dismiss"):
            status, _ = _req(server, "POST", route, {"id": "x"}, token=None)
            check(status == 403, f"{route} without the token: refused ({status})")
            status, _ = _req(server, "POST", route, {"id": "x"}, token="wrong")
            check(status == 403, f"{route} with a wrong token: refused ({status})")
            status, _ = _req(server, "POST", route, {"id": "x"}, origin="http://evil.example")
            check(status == 403, f"{route} from a foreign Origin: refused ({status})")
            status, _ = _req(server, "POST", route, {"id": "x"}, host="evil.example")
            check(status == 403, f"{route} with a wrong Host: refused ({status})")
            status, body = _req(server, "POST", route, {"id": "x"}, origin=f"http://127.0.0.1:{server.port}")
            check(status == 200 and body.get("ok") is False, f"{route} from the page with an unknown id: not found, no write ({body})")
        status, body = _req(server, "GET", "/")
        check(status == 200 and "Suggestions" in body and "/suggestion/apply" in body and "innerHTML" not in body,
              "the page carries the panel, rendered as text nodes")
        check(not approve.dismissed_path(path).exists() and not approve.aliases_path(path).exists(), "nothing written")
    finally:
        server.stop()
    check.done()


if __name__ == "__main__":
    run_as_script(globals())
