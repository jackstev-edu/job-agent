"""
test_learning_cleanup.py — the learning rules of 2026-11-02 (item 2).

WHAT THIS DOES: Shows that learned memory never takes a per-company
question (the classifier's category, the stop kind's wording, or
decision C's past-employer wording) or a marketing / communications
opt-in, and never reuses one planted for such a question; that two
"Year" boxes under two sections are two keys while two under none are
never learned, and a key two fields of one page share is neither
learned nor reused there; that a free-text answer at one company on a
shared board (job-boards.greenhouse.io/acme) is not reused at another
(…/beta), with "same company" the tenant; that stored entries breaking
these rules are removed on load, listed (questions only), and a shared
board's bare-host entry is rescoped from its posting; and, in a headless
browser, that the scan's `section` comes from a legend, a group label or
the nearest preceding heading through the value-free text filter.
WHY IT MATTERS: Jack's Workday run learned "If you were formerly
employed at <company>..." as an answer and two Year boxes under one key
(2026-11-02); his DoorDash run learned a "receive communications"
opt-in. Those are his rules, and this is where they are held.

Run from anywhere:  .venv/Scripts/python.exe tests/test_learning_cleanup.py
"""
import json
from pathlib import Path

import yaml

from _harness import REPO, TEST_HOME, Checks, mock_page, run_as_script
from agent import extract, gate, gui, memory, pipeline, shadow
from test_memory import _change, _prof, _route, _store

GH_A = "https://job-boards.greenhouse.io/acme/jobs/1"
GH_B = "https://job-boards.greenhouse.io/beta/jobs/2"
WORKDAY = "https://acme.wd5.myworkdayjobs.com/en-US/Careers/job/Boston/Engineer_R1/apply/applyManually"
AVATURE = "https://acme.avature.net/en_US/careers/ApplicationMethods?jobId=1"
PLAIN = "https://careers.b.example/jobs/2"


def _plant(store, field, answer, *, scope=memory.ANY, legal=False):
    key = gate.question_key(field)
    e = {"id": memory.entry_id(key, memory.ANSWER, scope, answer), "kind": memory.ANSWER, "key": key,
         "question": field["label"], "type": field["type"], "options": list(field.get("options") or []),
         "answer": answer, "line": None, "legal": legal, "scope": scope, "host": "", "portal": "generic",
         "first_seen": "2026-11-02T10:00:00", "last_seen": "2026-11-02T10:00:00", "seen": 1, "uses": 0,
         "posting": GH_A}
    store.entries.append(e)
    return e


def test_per_company_past_employer_and_opt_in_questions_are_never_learned_or_reused():
    # Fails on 6305d83: a classifier per_company answer, a "formerly
    # employed at" or "have you worked at" wording and a communications
    # opt-in were all learned (HOST_ONLY or ANY) and reused on the key.
    check = Checks()
    prof, store = _prof(), _store("never-2")
    cases = [
        ("the classifier's per_company category",
         {"ref": "q1", "type": "select", "label": "Do you know anyone who works here?", "options": ["Yes", "No"]},
         "No", "per_company"),
        ("the per-company stop wording",
         {"ref": "q0", "type": "select", "label": "Have you previously worked for Acme?", "options": ["Yes", "No"]},
         "No", "unknown"),
        ("decision C's 'have you worked at'",
         {"ref": "q2", "type": "select", "label": "Have you worked at Acme?", "options": ["Yes", "No"]},
         "No", "unknown"),
        ("decision C's 'formerly employed at' details box",
         {"ref": "q3", "type": "textarea",
          "label": "If you were formerly employed at Acme, please provide the information listed: dates, title, location"},
         "N/A", "free_text"),
        ("a communications opt-in",
         {"ref": "q4", "type": "select", "options": ["Yes", "No"],
          "label": "Would you like to receive communications from us about future opportunities?"},
         "No", "unknown"),
        ("an SMS opt-in",
         {"ref": "q5", "type": "radio", "label": "May we contact you by SMS text message?", "options": ["Yes", "No"]},
         "No", "personal_fact"),
        ("a talent-community opt-in",
         {"ref": "q6", "type": "select", "label": "Join our talent community?", "options": ["Yes", "No"]},
         "No", "unknown"),
    ]
    for what, field, now, category in cases:
        learned = memory.learn([_change(field, now, category=category)], url=PLAIN, portal="generic", prof=prof, store=store)
        why = memory.never_reason(field, category)
        check(learned == [] and why is not None, f"{what}: never learned ({why})")
        _plant(store, field, now)
        decision, flags, for_model = _route(field, category, prof, store, memory.tenant_of(PLAIN))
        check(decision is None, f"{what}: a planted entry is never reused ({decision})")
    check(all(e.get("answer") for e in store.entries) and not Path(store.at).exists(),
          "nothing was saved: the planted entries stayed in memory only")
    check.done()


def test_two_year_boxes_under_different_sections_get_different_keys_and_two_under_none_are_never_learned():
    # Fails on 6305d83: the key was label, type and options, so both Year
    # boxes shared one key and the second entry overwrote the first; bare
    # Year boxes were learned.
    check = Checks()
    prof, store = _prof(), _store("years")
    edu = {"ref": "y1", "type": "text", "label": "Year", "section": "Education"}
    work = {"ref": "y2", "type": "text", "label": "Year", "section": "Work history"}
    check(gate.question_key(edu) != gate.question_key(work) and json.loads(gate.question_key(edu))[3] == "education",
          "two sections, two keys, the section in the key")
    learned = memory.learn([_change(edu, "2020", category="personal_fact"), _change(work, "2023", category="personal_fact")],
                           url=PLAIN, portal="generic", prof=prof, store=store, fields=[edu, work])
    check(len(learned) == 2 and {e["answer"] for e in learned} == {"2020", "2023"}, f"both learned ({learned})")
    d_edu, _, _ = _route(edu, "personal_fact", prof, store, memory.tenant_of(PLAIN))
    d_work, _, _ = _route(work, "personal_fact", prof, store, memory.tenant_of(PLAIN))
    check(d_edu and d_edu["value"] == "2020" and d_work and d_work["value"] == "2023",
          f"each section gets its own year back ({d_edu and d_edu['value']}, {d_work and d_work['value']})")
    other = {"ref": "y9", "type": "text", "label": "Year", "section": "Certifications"}
    d_other, _, for_model = _route(other, "personal_fact", prof, store, memory.tenant_of(PLAIN))
    check(d_other is None and for_model, "a third section is a question nothing was learned for")
    # The record keeps the section, so the gate recomputes the same key.
    step = shadow.step_entry([edu], {"y1": "personal_fact"}, {}, [], None)
    check(step["fields"][0].get("section") == "Education" and gate.question_key(step["fields"][0]) == gate.question_key(edu),
          "the record's field carries the section and gives the same key")

    bare1 = {"ref": "y3", "type": "text", "label": "Year"}
    bare2 = {"ref": "y4", "type": "text", "label": "Year"}
    nothing = memory.learn([_change(bare1, "2020"), _change(bare2, "2023")], url=PLAIN, portal="generic",
                           prof=prof, store=store, fields=[bare1, bare2])
    why = memory.never_reason(bare1, "personal_fact")
    check(nothing == [] and why and "section" in why, f"two Year boxes under no section: never learned ({why})")
    lone = {"ref": "y5", "type": "text", "label": "Year"}
    check(memory.learn([_change(lone, "2020")], url=PLAIN, portal="generic", prof=prof, store=store) == [],
          "one Year box under no section: never learned either")

    # The same key twice on one page: never learned, never reused there.
    same1 = {"ref": "s1", "type": "text", "label": "Year", "section": "Education"}
    same2 = {"ref": "s2", "type": "text", "label": "Year", "section": "Education"}
    check(memory.shared_keys([same1, same2]) == {gate.question_key(same1)} and memory.shared_keys([same1]) == set(),
          "shared_keys names the key two fields share")
    before = len(store.entries)
    nothing = memory.learn([_change(same1, "1999"), _change(same2, "1998")], url=PLAIN, portal="generic",
                           prof=prof, store=store, fields=[same1, same2])
    check(nothing == [] and len(store.entries) == before, "a key two fields of the page share is never learned")
    plan, _, for_model = pipeline.route([same1, same2], {"s1": "personal_fact", "s2": "personal_fact"}, prof, set(),
                                        learned=store, host=memory.tenant_of(PLAIN))
    check("s1" not in plan and "s2" not in plan and len(for_model) == 2,
          f"on a page with two such boxes the learned Education year answers neither ({plan})")
    plan, _, _ = pipeline.route([same1], {"s1": "personal_fact"}, prof, set(), learned=store, host=memory.tenant_of(PLAIN))
    check(plan.get("s1", {}).get("value") == "2020", "alone on a page, the same key is answered")
    check.done()


def test_company_a_and_company_b_on_a_shared_board_do_not_share_a_free_text_answer():
    # Fails on 6305d83: the scope was the bare host, so an answer learned
    # at job-boards.greenhouse.io/acme was reused at /beta.
    check = Checks()
    prof, store = _prof(), _store("tenant")
    check(memory.tenant_of(GH_A) == "job-boards.greenhouse.io/acme" and memory.tenant_of(GH_B) == "job-boards.greenhouse.io/beta"
          and memory.tenant_of("https://boards.greenhouse.io/Gamma/jobs/3") == "boards.greenhouse.io/gamma"
          and memory.tenant_of("https://jobs.lever.co/delta/abc") == "jobs.lever.co/delta"
          and memory.tenant_of("https://jobs.ashbyhq.com/epsilon/123") == "jobs.ashbyhq.com/epsilon"
          and memory.tenant_of(WORKDAY) == "acme" and memory.tenant_of(AVATURE) == "acme"
          and memory.tenant_of(PLAIN) == "careers.b.example"
          and memory.tenant_of("https://job-boards.greenhouse.io/") == "" and memory.tenant_of("") == "",
          "the tenant: host plus slug on the shared boards, the subdomain on Workday and Avature, the host elsewhere")
    essay = {"ref": "e1", "type": "textarea", "label": "Why do you want to work here?"}
    learned = memory.learn([_change(essay, "Because of the mission.", category="free_text")],
                           url=GH_A, portal="greenhouse", prof=prof, store=store)
    check(learned and learned[0]["scope"] == "job-boards.greenhouse.io/acme" and learned[0]["host"] == "job-boards.greenhouse.io",
          f"the entry is scoped to the tenant, the host kept beside it ({learned})")
    same, _, _ = _route(essay, "free_text", prof, store, memory.tenant_of(GH_A))
    other, _, for_model = _route(essay, "free_text", prof, store, memory.tenant_of(GH_B))
    check(same and same["value"] == "Because of the mission." and other is None and for_model,
          "reused at /acme, the model's draft at /beta")
    bare, _, for_model = _route(essay, "free_text", prof, store, "job-boards.greenhouse.io")
    check(bare is None and for_model, "the bare host matches no tenant-scoped entry")
    check.done()


def test_stored_violating_entries_are_removed_on_load_and_listed():
    # Fails on 6305d83: load kept every well-formed entry as it was.
    check = Checks()
    at = TEST_HOME / "learned-violations.yaml"
    at.unlink(missing_ok=True)

    def entry(question, answer, *, options=(), scope=memory.ANY, posting=PLAIN, key=None, category=None, kind=memory.ANSWER):
        field = {"label": question, "type": "select" if options else "text", "options": list(options)}
        key = key or gate.question_key(field)
        e = {"id": memory.entry_id(key, kind, scope, answer), "kind": kind, "key": key, "question": question,
             "type": field["type"], "options": list(options), "answer": answer, "line": None, "legal": False,
             "scope": scope, "host": "", "portal": "generic", "first_seen": "x", "last_seen": "x", "seen": 1,
             "uses": 0, "posting": posting}
        if category:
            e["category"] = category
        return e

    old_year_key = json.dumps(["year", "text", []])          # the 2026-11-01 shape: no section
    entries = [
        entry("Have you previously worked for Acme?", "REMOVED-A", options=("Yes", "No")),
        entry("Have you worked at Acme?", "REMOVED-B", options=("Yes", "No")),
        entry("Do you know anyone who works here?", "REMOVED-C", options=("Yes", "No"), category="per_company"),
        entry("Would you like to receive communications about job alerts?", "REMOVED-D", options=("Yes", "No")),
        entry("Year", "REMOVED-E", key=old_year_key),
        entry("Why do you want to work here?", "REMOVED-F", scope="boards.greenhouse.io",
              posting="https://boards.greenhouse.io/"),
        entry("City", "KEPT-CITY", key=json.dumps(["city", "text", [], "address"])),
        entry("Why do you want to work here?", "KEPT-ESSAY", scope="job-boards.greenhouse.io", posting=GH_A),
        entry("Are you a protected veteran?", "KEPT-VET", options=("Yes", "No", "I don't wish to answer")),
    ]
    at.write_text(yaml.safe_dump({"version": 1, "entries": entries}, sort_keys=False), encoding="utf-8")
    store = memory.Learned.load(at)
    kept = {e["answer"] for e in store.entries}
    check(kept == {"KEPT-CITY", "KEPT-ESSAY", "KEPT-VET"}, f"the violating entries are gone ({kept})")
    essay = next(e for e in store.entries if e["answer"] == "KEPT-ESSAY")
    check(essay["scope"] == "job-boards.greenhouse.io/acme"
          and essay["id"] == memory.entry_id(essay["key"], essay["kind"], essay["scope"], essay["answer"]),
          f"the shared board's bare-host entry is rescoped from its posting, its id with it ({essay['scope']})")
    whys = {r["question"]: r["why"] for r in store.removed}
    check(len(store.removed) == 6 and len(store.just_removed) == 6
          and whys["Have you previously worked for Acme?"] == "per-company"
          and whys["Have you worked at Acme?"] == "per-company"
          and whys["Do you know anyone who works here?"] == "per-company"
          and whys["Would you like to receive communications about job alerts?"] == "opt-in"
          and whys["Year"] == "short label with no section context"
          and "bare host" in whys["Why do you want to work here?"],
          f"each removal is listed with its reason ({whys})")
    text = at.read_text(encoding="utf-8")
    check("removed:" in text and not any(f"REMOVED-{c}" in text for c in "ABCDEF")
          and all(set(r) <= {"at", "question", "why"} for r in store.removed),
          "the file keeps the removed questions and reasons, never their answers")
    again = memory.Learned.load(at)
    check(again.just_removed == [] and {e["answer"] for e in again.entries} == kept and len(again.removed) == 6,
          "a second load removes nothing more and keeps the list")
    state = gui.State()
    state.memory_path = at
    view = state.memory_view()
    check(view["ok"] and len(view["removed"]) == 6 and all(set(r) == {"at", "question", "why"} for r in view["removed"])
          and "REMOVED" not in json.dumps(view["removed"]),
          "the Memory page is told what was removed, questions and reasons only")
    check.done()


def test_the_scan_reads_a_fields_section_from_a_legend_a_group_label_or_the_nearest_heading():
    # Fails on 6305d83: the scan emitted no `section`.
    check = Checks()
    with mock_page(REPO / "tests" / "sections.html") as page:
        fields = extract.extract_fields(page)
        by_name = {f.get("name"): f for f in fields}
        want = {"edu_year": "Education", "work_year": "Work history", "award_year": "Awards",
                "lang_level": "Languages", "note_year": "Notes", "bare_year_1": "Notes", "bare_year_2": "Notes"}
        for name, section in want.items():
            check(by_name.get(name, {}).get("section") == section, f"{name}: section {section!r} ({by_name.get(name, {}).get('section')!r})")
        dumped = json.dumps(fields)
        check("SECRET-VALUE" not in dumped and "VALUE-IN-HEADING" not in dumped,
              "no input value reaches the scan through a legend, a label or a heading")
        check(gate.question_key(by_name["edu_year"]) != gate.question_key(by_name["work_year"]),
              "the two Year boxes are two keys")
        shared = memory.shared_keys(fields)
        check(gate.question_key(by_name["bare_year_1"]) in shared and gate.question_key(by_name["edu_year"]) not in shared,
              "the two Year boxes under one heading share a key; Education's is its own")
    check.done()


if __name__ == "__main__":
    run_as_script(globals())
