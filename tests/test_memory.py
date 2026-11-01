"""
test_memory.py — learned memory: Jack's own entries, reused on the same question only.

WHAT THIS DOES: Feeds corrections.changes-shaped changes to memory.learn
under a temporary state directory and routes fields through
pipeline.route with the store: a choice he picked for a profile line is
stored as a mapping, the profile file stays byte for byte the same, and
the mapping answers the same question on another posting but not a
question with other options or once the line changed; a typed
correction to an ordinary line becomes a profile candidate reused for
that key alone; a legal answer of his is reused on the identical
question and not when one word or one option differs, the model is
never handed the field, and the gate takes a "learned" legal answer
only with the exact key; a hard-stop question, a consent box, a
password, a credential and a code box are never learned; free text is
reused at the same host only; a deleted entry is never reused; no
module but approve.py can write profile.yaml; and, in a headless
browser, a box Jack fills under a gate pause is learned and fills the
same box on the next run.
WHY IT MATTERS: Decisions 2, 4 and 5 of 2026-11-01: learning is
automatic, legal answers come only from Jack's own recorded answer on
the same question, and profile.yaml is never written by learning.

Run from anywhere:  .venv/Scripts/python.exe tests/test_memory.py
"""
import re
from pathlib import Path
from unittest import mock

import yaml

from _fill_run import PROFILE
from _harness import REPO, TEST_HOME, Checks, run_as_script
from agent import gate, memory, pipeline
from agent.profile import Profile
from test_gate import SENTINEL_EEO, base, run as gate_run

URL_A, URL_B = "https://jobs.a.example/posting/1", "https://careers.b.example/jobs/2"


def _prof(name="memory-profile.yaml", text=PROFILE):
    p = TEST_HOME / name
    p.write_text(text, encoding="utf-8")
    return Profile(yaml.safe_load(text), p)


def _store(name):
    at = TEST_HOME / f"learned-{name}.yaml"
    at.unlink(missing_ok=True)
    return memory.Learned.load(at)


def _change(field, now, *, category="personal_fact", decision=None, agent=None, kind="filled_blank"):
    return {"field": field, "kind": kind, "agent": agent, "now": now,
            "decision": decision or {}, "category": category}


def _route(field, category, prof, store, host):
    plan, flags, for_model = pipeline.route([field], {field["ref"]: category}, prof, set(),
                                            learned=store, host=host)
    return plan.get(field["ref"]), flags, for_model


STATE = {"ref": "f1", "type": "select", "label": "State / Province", "options": ["XX - Example State", "YY - Other"]}


def test_a_choice_for_a_profile_line_is_stored_and_reused_and_the_profile_is_untouched():
    check = Checks()
    prof, store = _prof(), _store("choice")
    before = prof.path.read_bytes()
    learned = memory.learn([_change(STATE, "XX - Example State",
                                    decision={"value": None, "profile_path": "personal.state"})],
                           url=URL_A, portal="generic", prof=prof, store=store)
    check(len(learned) == 1 and learned[0]["kind"] == memory.CHOICE and learned[0]["line"] == "personal.state"
          and learned[0]["line_value"] == "XX" and learned[0]["scope"] == memory.ANY,
          f"one choice mapping, line named, the line's value kept with it ({learned})")
    check(prof.path.read_bytes() == before, "profile.yaml is byte for byte what it was")
    check(store.at.exists() and store.at != prof.path and "XX - Example State" in store.at.read_text(encoding="utf-8"),
          "the store is its own file beside nothing of the profile")
    decision, flags, for_model = _route(dict(STATE, ref="z9"), "personal_fact", prof, memory.Learned.load(store.at),
                                        "careers.b.example")
    check(decision and decision["value"] == "XX - Example State" and decision["source"] == "learned"
          and decision["fit"] == "exact" and decision["confidence"] == "high"
          and decision["learned_key"] == gate.question_key(STATE) and not for_model and not flags,
          f"reused on another posting, ahead of the model ({decision})")
    other = dict(STATE, options=["XX", "YY"])
    decision, _, for_model = _route(other, "personal_fact", prof, store, "careers.b.example")
    check(decision is None and for_model, "other options: another key, nothing reused")
    changed = _prof("memory-profile-2.yaml", PROFILE.replace("state: XX", "state: ZZ"))
    decision, _, for_model = _route(STATE, "personal_fact", changed, store, "careers.b.example")
    check(decision is None and for_model, "the profile line changed since: the mapping says nothing")
    check.done()


def test_a_typed_correction_becomes_a_profile_candidate_reused_for_that_key_only():
    check = Checks()
    prof, store = _prof(), _store("candidate")
    field = {"ref": "f2", "type": "text", "label": "Preferred first name"}
    learned = memory.learn([_change(field, "Tess", kind="overwrote", agent="Test",
                                    decision={"value": "Test", "profile_path": "personal.first_name"})],
                           url=URL_A, portal="generic", prof=prof, store=store)
    check(len(learned) == 1 and learned[0]["kind"] == memory.CANDIDATE and learned[0]["line"] == "personal.first_name"
          and learned[0]["line_value"] == "Test" and learned[0]["answer"] == "Tess",
          f"a candidate for the line, the profile's own value beside it ({learned})")
    decision, _, _ = _route(field, "personal_fact", prof, store, "careers.b.example")
    check(decision and decision["value"] == "Tess" and decision["source"] == "learned" and decision["fit"] == "verbatim",
          "reused for the same key, at any host")
    decision, _, for_model = _route(dict(field, label="Preferred name"), "personal_fact", prof, store, "careers.b.example")
    check(decision is None and for_model, "another wording: not reused")
    check(prof.get("personal.first_name") == "Test" and "Tess" not in prof.path.read_text(encoding="utf-8"),
          "the profile still says Test")
    nothing = memory.learn([_change(field, "test", kind="overwrote", agent="Test",
                                    decision={"value": "Test", "profile_path": "personal.first_name"})],
                           url=URL_A, portal="generic", prof=prof, store=store)
    check(nothing == [], "a formatting-only difference teaches nothing")
    check.done()


VETERAN = {"ref": "g1", "type": "select", "label": "Are you a protected veteran?",
           "options": ["Yes", "No", "I don't wish to answer"]}


def test_a_legal_answer_is_reused_on_the_identical_question_only_and_the_model_never_sees_it():
    check = Checks()
    prof, store = _prof(), _store("legal")
    decision, flags, for_model = _route(VETERAN, "eeo", prof, store, "jobs.a.example")
    check(decision is None and len(flags) == 1 and flags[0]["source"] == "human" and not for_model,
          f"with nothing learned, the question is Jack's and never the model's ({flags and flags[0].get('reason')})")
    learned = memory.learn([_change(VETERAN, "No", category="eeo")], url=URL_A, portal="generic", prof=prof, store=store)
    check(len(learned) == 1 and learned[0]["legal"] is True and learned[0]["kind"] == memory.ANSWER
          and learned[0]["scope"] == memory.ANY and learned[0]["answer"] == "No",
          f"his answer is learned as a legal one ({learned})")
    decision, flags, for_model = _route(VETERAN, "eeo", prof, store, "careers.b.example")
    check(decision and decision["value"] == "No" and decision["source"] == "learned" and decision["fit"] == "exact"
          and not flags and not for_model, f"the identical question on another posting takes his answer ({decision})")
    starred = dict(VETERAN, label="Are you a protected veteran? *")
    decision, _, _ = _route(starred, "eeo", prof, store, "careers.b.example")
    check(decision and decision["value"] == "No", "a trailing required star is the allowed normalisation")
    for what, variant in (("one word differs", dict(VETERAN, label="Are you a veteran?")),
                          ("one option differs", dict(VETERAN, options=["Yes", "No", "Prefer not to say"])),
                          ("an option is missing", dict(VETERAN, options=["Yes", "No"])),
                          ("the type differs", dict(VETERAN, type="radio"))):
        decision, flags, for_model = _route(variant, "eeo", prof, store, "careers.b.example")
        check(decision is None and len(flags) == 1 and flags[0]["source"] == "human" and not for_model,
              f"{what}: not reused, Jack's again, never the model's")
    with mock.patch.object(pipeline.mapper, "map_fields", side_effect=AssertionError("the model was called")):
        plan = {}
        pipeline.ask_mapper(for_model, prof, "", set(), plan)
        _, _, for_model2 = _route(VETERAN, "eeo", prof, store, "careers.b.example")
        pipeline.ask_mapper(for_model2, prof, "", set(), plan)
    check(plan == {}, "no mapper call for a legal field, learned or not")
    # A typed legal answer (a salary box): the same host only.
    salary = {"ref": "s1", "type": "text", "label": "Desired salary"}
    learned = memory.learn([_change(salary, "100000", category="compensation")], url=URL_A, portal="generic",
                           prof=prof, store=store)
    check(learned and learned[0]["legal"] and learned[0]["scope"] == "jobs.a.example", f"typed legal: host-scoped ({learned})")
    same, _, _ = _route(salary, "compensation", prof, store, "jobs.a.example")
    other, flags, _ = _route(salary, "compensation", prof, store, "careers.b.example")
    check(same and same["value"] == "100000" and same["fit"] == "verbatim" and other is None and flags,
          "reused at the same host, Jack's elsewhere")
    check.done()


def test_the_gate_takes_a_learned_legal_answer_only_with_the_exact_key():
    check = Checks()
    rec = base()
    f1 = next(f for f in rec["fields"] if f["ref"] == "f1")
    good = {"value": "Yes", "source": "learned", "fit": "exact", "confidence": "high",
            "profile_path": None, "learned_key": gate.question_key(f1), "learned_id": "abc"}
    rec["plan"]["f1"] = dict(good)
    res = gate_run(rec)
    check(res["content_verdict"] == gate.WOULD_SUBMIT, f"the exact key: traced ({res['content_reasons']})")
    trace = next(t for t in res["trace"] if t["ref"] == "f1")
    check(trace["source"] == "learned" and "Yes" not in str(trace), "the trace names the source, never the answer")
    for what, bad in (("another question's key", {"learned_key": gate.question_key(dict(f1, options=["Yes", "No", "Maybe"]))}),
                      ("no key at all", {"learned_key": None}),
                      ("a loose fit", {"fit": "substring"}),
                      ("a source that only says learned", {"learned_key": "[]"})):
        rec = base()
        rec["plan"]["f1"] = {**good, **bad}
        res = gate_run(rec)
        check(res["content_verdict"] == gate.WOULD_STOP and any("'Are you legally authorized" in r for r in res["content_reasons"]),
              f"{what}: would_stop ({res['content_reasons']})")
    # A typed legal answer in a box with no options: verbatim, with the key.
    rec = base()
    rec["fields"].append({"ref": "s1", "type": "text", "label": "Desired salary", "required": False})
    rec["categories"]["s1"] = "compensation"
    key = gate.question_key(rec["fields"][-1])
    rec["plan"]["s1"] = {"value": "100000", "source": "learned", "fit": "verbatim", "confidence": "high",
                         "profile_path": None, "learned_key": key}
    rec["results"]["s1"] = {"filled": True, "detail": "typed", "unconfirmed": False}
    rec["snapshot"]["s1"] = "100000"
    check(gate_run(rec)["content_verdict"] == gate.WOULD_SUBMIT, "a typed legal answer of his, exact key: traced")
    rec["plan"]["s1"]["source"] = "model"
    check(gate_run(rec)["content_verdict"] == gate.WOULD_STOP, "the same value from the model: never")
    check.done()


def test_never_learned_fields():
    check = Checks()
    prof, store = _prof(), _store("never")
    cases = [
        ("a hard-stop per-company question", {"ref": "p1", "type": "select", "label": "Have you previously worked for Acme?",
                                              "options": ["Yes", "No"]}, "No", "per_company"),
        ("a consent box", {"ref": "c1", "type": "checkbox", "label": "I agree to the terms and conditions"}, True, "consent"),
        ("any checkbox", {"ref": "c2", "type": "checkbox", "label": "Send me job alerts"}, True, "preference"),
        ("a password box", {"ref": "pw", "type": "password", "label": "Password"}, "hunter2hunter2", "credential"),
        ("a secret-classified box", {"ref": "pw2", "type": "text", "label": "PIN", "secret": True}, "1234", "credential"),
        ("a credential question", {"ref": "sq", "type": "text", "label": "Mother's maiden name"}, "Smith", "credential"),
        ("a one-time code", {"ref": "code", "type": "text", "label": "Verification code"}, "123456", "personal_fact"),
        ("a file box", {"ref": "cv", "type": "file", "label": "Resume"}, "cv.pdf", "document"),
        ("a legal declaration", {"ref": "ld", "type": "textarea", "label": "Explain any conviction"}, "none", "legal_declaration"),
        ("a question with no label", {"ref": "nl", "type": "text", "label": "", "name": "x1"}, "value", "personal_fact"),
    ]
    for what, field, now, category in cases:
        learned = memory.learn([_change(field, now, category=category)], url=URL_A, portal="generic", prof=prof, store=store)
        check(learned == [] and memory.never_reason(field, category), f"{what}: never learned")
    check(not store.at.exists() or "hunter2" not in store.at.read_text(encoding="utf-8"), "nothing of a password on disk")
    check(store.entries == [], "the store is empty")
    check.done()


def test_free_text_is_reused_at_the_same_host_only():
    check = Checks()
    prof, store = _prof(), _store("host")
    essay = {"ref": "e1", "type": "textarea", "label": "Why do you want to work here?"}
    learned = memory.learn([_change(essay, "Because of the mission.", category="free_text")],
                           url=URL_A, portal="generic", prof=prof, store=store)
    check(learned and learned[0]["scope"] == "jobs.a.example" and learned[0]["kind"] == memory.ANSWER,
          f"free text is tied to the host ({learned})")
    same, _, _ = _route(essay, "free_text", prof, store, "jobs.a.example")
    other, _, for_model = _route(essay, "free_text", prof, store, "careers.b.example")
    check(same and same["value"] == "Because of the mission." and other is None and for_model,
          "reused at jobs.a.example, the model's draft elsewhere")
    none, _, for_model = _route(essay, "free_text", prof, store, "")
    check(none is None and for_model, "no host known: not reused")
    check.done()


def test_a_deleted_entry_is_never_reused():
    check = Checks()
    prof, store = _prof(), _store("delete")
    field = {"ref": "n1", "type": "text", "label": "Nickname"}
    [entry] = memory.learn([_change(field, "TP")], url=URL_A, portal="generic", prof=prof, store=store)
    decision, _, _ = _route(field, "personal_fact", prof, memory.Learned.load(store.at), "x.example")
    check(decision and decision["value"] == "TP", "reused before the delete")
    check(store.delete(entry["id"]) is True and store.delete(entry["id"]) is False, "deleted once")
    reloaded = memory.Learned.load(store.at)
    decision, _, for_model = _route(field, "personal_fact", prof, reloaded, "x.example")
    check(decision is None and for_model and reloaded.entries == [], "gone from the file, nothing reused")
    check(store.edit("nope", "x") is False, "editing a missing entry is refused")
    check.done()


def test_no_module_but_approve_can_write_the_profile():
    check = Checks()
    writers = re.compile(r"(write_text|write_bytes|_atomic_write|os\.replace|apply_profile_line|\.dump\(|safe_dump\()")
    offenders = []
    for path in sorted((REPO / "agent").glob("*.py")) + [REPO / "apply.py"]:
        if path.name in ("approve.py",):
            continue
        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if "BLANK_PROFILE" in line:
                continue                # `init` writing the blank template, at Jack's command
            if writers.search(line) and re.search(r"profile", line, re.I) and "apply_profile_line" not in line:
                offenders.append(f"{path.name}:{i}: {line.strip()}")
            if "apply_profile_line" in line and path.name != "gui.py":
                offenders.append(f"{path.name}:{i}: {line.strip()}")
    check(not offenders, f"only approve.py writes profile.yaml, only gui.py's click calls it ({offenders})")
    # And at run time: learn, reuse, edit and delete leave the profile as it was.
    prof, store = _prof("memory-profile-rt.yaml"), _store("runtime")
    before, mtime = prof.path.read_bytes(), prof.path.stat().st_mtime_ns
    field = {"ref": "f1", "type": "text", "label": "Preferred first name"}
    [e] = memory.learn([_change(field, "Tess", kind="overwrote", agent="Test",
                                decision={"value": "Test", "profile_path": "personal.first_name"})],
                       url=URL_A, portal="generic", prof=prof, store=store)
    _route(field, "personal_fact", prof, store, "x.example")
    store.edit(e["id"], "Tessa")
    store.note_use(store.entries[0]["id"])
    store.delete(store.entries[0]["id"])
    check(prof.path.read_bytes() == before and prof.path.stat().st_mtime_ns == mtime, "the profile file is untouched")
    check.done()


def test_a_box_jack_fills_under_a_pause_is_learned_and_fills_next_time():
    from agent import wizard
    from test_pick_up import CITY_REQUIRED, _page, _run
    check = Checks()
    at = memory.path()
    assert str(at).startswith(str(TEST_HOME))
    at.unlink(missing_ok=True)
    with _run(_page(city=CITY_REQUIRED), auto_submit=lambda: False) as (page, w):
        out = w.run()
        check(out.status == wizard.WAITING and w.pending["kind"] == "gate", f"the gate pause on step 2 ({out.status})")
        check(page.input_value("#city") == "", "the agent left City empty")
        page.fill("#city", "Springfield")
        w.poll_watch()
        page.evaluate("() => show(3)")
        out = w.continue_from({})
    store = memory.Learned.load(at)
    check([e for e in store.entries if e["question"] == "City" and e["answer"] == "Springfield"],
          f"his City is learned ({[(e['question'], e['kind']) for e in store.entries]})")
    check(any(l.startswith("learned 1 answer(s) of yours") and "Springfield" not in l for l in out.log),
          f"the job log says so, without the value ({[l for l in out.log if 'learned' in l]})")
    with _run(_page(city=CITY_REQUIRED), auto_submit=lambda: False) as (page, w):
        out = w.run()
        # Step 2 passed its gate with City filled, so the tab has moved on:
        # the wizard's own read-back of that page holds his value.
        check("Springfield" in w.snapshots.values() and not any("gate stopped" in l and "City" in l for l in out.log),
              f"next time City is filled from memory and step 2 passes ({out.status}, {out.log[-4:]})")
    check.done()


if __name__ == "__main__":
    run_as_script(globals())
