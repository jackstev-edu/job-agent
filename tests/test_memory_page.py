"""
test_memory_page.py — the GUI's Memory tab: what the agent learned, for Jack to tune or delete.

WHAT THIS DOES: Starts gui.Server with a State whose learned store is a
temporary file holding one entry of each group, and talks to it over
http.client: /memory lists the four groups with every answer (a legal
one included, local only), an edit changes what the next job routes on,
a delete removes it from the file and from routing, Save to profile
writes a candidate's line and nothing else; every route refuses a
missing token, a foreign Origin and a wrong Host; the jobs view (/state)
carries nothing of memory. Then a headless browser opens the Memory tab
with a question holding markup: it is drawn as text, nothing runs.
WHY IT MATTERS: Decision 3 of 2026-11-01: "I should have the option to
delete anything it's learned... delete and tune information if you're
getting something wrong." The page shows untrusted page text (the
questions) and holds the token that edits the store.

Run from anywhere:  .venv/Scripts/python.exe tests/test_memory_page.py
"""
import json

import yaml

from _fill_run import PROFILE
from _harness import TEST_HOME, Checks, run_as_script
from agent import gate, gui, memory, pipeline, template
from agent.profile import Profile
from test_gui import _req

EVIL = '<img src=x onerror="window.__pwned=1"><script>window.__pwned=2</script>Bold <b>question</b>'


def _entry(store, kind, question, answer, *, options=(), line=None, legal=False, scope=memory.ANY, line_value=None):
    field = {"label": question, "type": "select" if options else "text", "options": list(options),
             "section": "About you"}      # a short label's key carries its section (Jack, 2026-11-02)
    e = {"id": memory.entry_id(gate.question_key(field), kind, scope, answer), "kind": kind,
         "key": gate.question_key(field), "question": question, "type": field["type"], "options": list(options),
         "answer": answer, "line": line, "legal": legal, "scope": scope, "host": "" if scope == memory.ANY else scope,
         "portal": "generic", "first_seen": "2026-11-01T10:00:00", "last_seen": "2026-11-01T10:00:00",
         "seen": 1, "uses": 0, "posting": "https://a.example/1"}
    if line_value is not None:
        e["line_value"] = line_value
    store.entries.append(e)
    return e


def _state(name):
    state = gui.State()
    state.memory_path = TEST_HOME / f"memory-page-{name}.yaml"
    state.memory_path.unlink(missing_ok=True)
    store = memory.Learned.load(state.memory_path)
    entries = {
        "choice": _entry(store, memory.CHOICE, "State / Province", "XX - Example State",
                         options=("XX - Example State", "YY - Other"), line="personal.state", line_value="XX"),
        "candidate": _entry(store, memory.CANDIDATE, "Preferred first name", "Tess", line="personal.first_name",
                            line_value="Test"),
        "legal": _entry(store, memory.ANSWER, "Are you a protected veteran?", "SENTINEL-LEGAL",
                        options=("Yes", "No", "SENTINEL-LEGAL"), legal=True),
        "portal": _entry(store, memory.ANSWER, "Why do you want to work here?", "Because of the mission.",
                         scope="jobs.a.example"),
    }
    store.save()
    return state, entries


def test_memory_lists_four_groups_and_edit_and_delete_take_effect_on_the_next_job():
    # Fails on da8281a: /memory and /memory/edit were 404.
    check = Checks()
    state, entries = _state("groups")
    prof = Profile(yaml.safe_load(PROFILE), TEST_HOME / "memory-page-profile.yaml")
    server = gui.Server(state).start()
    try:
        status, _, body = _req(server, "GET", "/memory")
        groups = {e["group"]: e for e in body.get("entries", [])}
        check(status == 200 and body.get("ok") is True and set(groups) == {"choice", "candidate", "legal", "portal"}
              and body.get("groups") == ["choice", "candidate", "legal", "portal"], f"four groups ({sorted(groups)})")
        check(groups["legal"]["answer"] == "SENTINEL-LEGAL" and groups["candidate"]["can_save"] is True
              and groups["candidate"]["line_value"] == "Test" and groups["choice"]["can_save"] is False
              and groups["portal"]["scope"] == "jobs.a.example" and groups["choice"]["scope"] == "everywhere",
              "each entry shows its answer, scope and whether it can be saved")
        status, _, body = _req(server, "GET", "/state")
        check(status == 200 and "SENTINEL-LEGAL" not in json.dumps(body) and "entries" not in body,
              "the jobs view carries nothing of memory")
        origin = f"http://127.0.0.1:{server.port}"
        field = {"ref": "p1", "type": "select", "label": "State / Province", "section": "About you", "options": ["XX - Example State", "YY - Other"]}
        plan, _, _ = pipeline.route([field], {"p1": "personal_fact"}, prof, set(),
                                    learned=memory.Learned.load(state.memory_path), host="x.example")
        check(plan.get("p1", {}).get("value") == "XX - Example State", "before the edit the mapping answers")
        status, _, body = _req(server, "POST", "/memory/edit", {"id": entries["choice"]["id"], "answer": "YY - Other"}, origin=origin)
        check(status == 200 and body.get("ok") is True, f"the edit is accepted ({body})")
        plan, _, _ = pipeline.route([field], {"p1": "personal_fact"}, prof, set(),
                                    learned=memory.Learned.load(state.memory_path), host="x.example")
        check(plan.get("p1", {}).get("value") == "YY - Other", f"the next job routes on the edited answer ({plan.get('p1')})")
        status, _, body = _req(server, "POST", "/memory/edit", {"id": entries["choice"]["id"], "answer": "  "}, origin=origin)
        check(status == 200 and body.get("ok") is False, "an empty edit is refused")
        new_id = next(e["id"] for e in _req(server, "GET", "/memory")[2]["entries"] if e["group"] == "choice")
        status, _, body = _req(server, "POST", "/memory/delete", {"id": new_id}, origin=origin)
        check(status == 200 and body.get("ok") is True, "the delete is accepted")
        plan, _, for_model = pipeline.route([field], {"p1": "personal_fact"}, prof, set(),
                                            learned=memory.Learned.load(state.memory_path), host="x.example")
        check("p1" not in plan and for_model, "the next job no longer has it")
        check("XX - Example State" not in state.memory_path.read_text(encoding="utf-8")
              and "YY - Other" not in state.memory_path.read_text(encoding="utf-8").split("answer:")[0], "gone from the file")
        status, _, body = _req(server, "POST", "/memory/delete", {"id": "nope"}, origin=origin)
        check(status == 200 and body.get("ok") is False, "an unknown id: nothing")
    finally:
        server.stop()
    check.done()


def test_save_to_profile_route_writes_a_candidate_only():
    # Fails on da8281a: /memory/save was 404.
    check = Checks()
    state, entries = _state("save")
    path = TEST_HOME / "memory-page-save-profile.yaml"
    path.write_text(template.BLANK_PROFILE.replace("  first_name:                  # REQUIRED",
                                                   "  first_name: Test                   # REQUIRED"), encoding="utf-8")
    state.profile_path = path
    server = gui.Server(state).start()
    origin = f"http://127.0.0.1:{server.port}"
    try:
        before = path.read_text(encoding="utf-8")
        for what, key in (("a legal entry", "legal"), ("a choice", "choice"), ("a portal answer", "portal")):
            status, _, body = _req(server, "POST", "/memory/save", {"id": entries[key]["id"]}, origin=origin)
            check(status == 200 and body.get("ok") is False and path.read_text(encoding="utf-8") == before,
                  f"{what}: refused, nothing written ({body.get('error')})")
        status, _, body = _req(server, "POST", "/memory/save", {"id": entries["candidate"]["id"]}, origin=origin)
        text = path.read_text(encoding="utf-8")
        check(status == 200 and body.get("ok") is True and 'first_name: "Tess"' in text, f"the candidate is written, quoted ({body})")
        check(not any(e["group"] == "candidate" for e in _req(server, "GET", "/memory")[2]["entries"]),
              "the saved candidate leaves memory")
        check("SENTINEL-LEGAL" not in text, "no legal value near the profile")
    finally:
        server.stop()
    check.done()


def test_every_memory_route_refuses_a_missing_token_foreign_origin_or_wrong_host():
    # Fails on da8281a: the routes were 404 for every call.
    check = Checks()
    state, entries = _state("refuse")
    server = gui.Server(state).start()
    try:
        before = state.memory_path.read_text(encoding="utf-8")
        for method, route, body in (("GET", "/memory", None), ("POST", "/memory/edit", {"id": entries["choice"]["id"], "answer": "x"}),
                                    ("POST", "/memory/delete", {"id": entries["choice"]["id"]}),
                                    ("POST", "/memory/save", {"id": entries["candidate"]["id"]})):
            status, _, _ = _req(server, method, route, body, token=None)
            check(status == 403, f"{route} without the token: refused ({status})")
            status, _, _ = _req(server, method, route, body, token="wrong")
            check(status == 403, f"{route} with a wrong token: refused ({status})")
            status, _, _ = _req(server, method, route, body, origin="http://evil.example")
            check(status == 403, f"{route} from a foreign Origin: refused ({status})")
            status, _, _ = _req(server, method, route, body, host="evil.example")
            check(status == 403, f"{route} with a wrong Host: refused ({status})")
        check(state.memory_path.read_text(encoding="utf-8") == before, "nothing changed")
    finally:
        server.stop()
    check.done()


def test_markup_in_a_learned_question_is_drawn_as_text():
    from playwright.sync_api import sync_playwright
    check = Checks()
    state, _ = _state("markup")
    store = memory.Learned.load(state.memory_path)
    _entry(store, memory.ANSWER, EVIL, "Bold <b>answer</b>", scope="jobs.a.example")
    store.save()
    server = gui.Server(state).start()
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                page = browser.new_page()
                page.goto(server.url)
                page.wait_for_timeout(600)
                page.click("#tab-memory")
                page.wait_for_timeout(900)
                pwned = page.evaluate("window.__pwned || 0")
                text = page.inner_text("#memory-groups")
                tags = page.evaluate("document.querySelectorAll('#memory img, #memory script, #memory b').length")
                groups = page.evaluate("document.querySelectorAll('#memory-groups .mgroup').length")
                page.fill("#memory-search", "veteran")
                page.wait_for_timeout(300)
                filtered = page.evaluate("document.querySelectorAll('#memory-groups .mentry').length")
                value = page.evaluate("document.querySelector('#memory-groups .mentry input.answer').value")
            finally:
                browser.close()
        check(pwned == 0 and tags == 0, f"nothing from the question ran or rendered ({pwned}, {tags})")
        check("Bold <b>question</b>" in text and "<img src=x onerror=" in text, "the question shows as the text it is")
        check(groups == 4 and "Legal & EEO (1)" in text, f"four groups drawn ({groups})")
        check(filtered == 1 and value == "SENTINEL-LEGAL", f"the search narrows to the veteran question, its answer in the box for editing ({filtered})")
    finally:
        server.stop()
    check.done()


if __name__ == "__main__":
    run_as_script(globals())
