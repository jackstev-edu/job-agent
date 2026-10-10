"""
test_button_pairs.py — regression for Ashby's Yes/No button-pair questions.

WHAT THIS DOES: Loads tests/button_pairs.html in a headless browser and checks
the whole path: scan, routing, fill, and the snapshot the correction log reads.
WHY IT MATTERS: This is the one fill path that clicks a <button>, so the traps
here (an "I agree" option, a button inside a real <form>) must stay refused.

Run from anywhere:  .venv/Scripts/python.exe tests/test_button_pairs.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from playwright.sync_api import sync_playwright
from agent import extract, filler, sensitive

FORM = (Path(__file__).parent / "button_pairs.html").resolve().as_uri()
failures = []


def check(cond, what):
    print(("  ok   " if cond else "  FAIL ") + what)
    if not cond:
        failures.append(what)


class FakeProfile:
    """Stands in for profile.yaml; only the lines these questions route to."""
    data = {"eligibility.authorized_to_work_us": True,
            "eligibility.requires_sponsorship_future": False,
            "eligibility.requires_sponsorship_now": False,
            "eligibility.willing_to_relocate": True}

    def get(self, path):
        return self.data.get(path)


with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page()
    page.goto(FORM)

    print("\n-- scan")
    fields = extract.extract_fields(page)
    for f in fields:
        print(f"  {f['ref']:<4} {f['type']:<6} {f.get('widget', ''):<7} "
              f"req={f['required']!s:<5} {f['label'][:60]!r} {f.get('options')} "
              f"now={f.get('current_value', '')!r}")
    by_label = lambda start: next(f for f in fields if f["label"].startswith(start))
    groups = [f for f in fields if f.get("widget") == "buttons"]
    labels = [f["label"] for f in fields]

    check(len(groups) == 7, f"7 visible button groups found (got {len(groups)})")
    check(not any("Hidden follow-up" in l for l in labels), "hidden group skipped")
    check(not any(f.get("name") in ("stray", "q-auth") and f["type"] == "checkbox"
                  for f in fields), "hidden checkboxes not emitted as fields")
    check(labels[:3] == ["First name", "Preferred contact method",
                         "Are you legally authorized to work in the United States?"],
          "page order kept: groups land where they sit in the DOM")
    auth = by_label("Are you legally authorized")
    visa = by_label("Will you now or in the fut")
    commute = by_label("This role requires being ")
    mode = by_label("Preferred working arrangem")
    prior = by_label("Have you previously applie")
    terms = by_label("Terms of use")
    reloc = by_label("Are you willing to relocat")
    check(auth["required"] and visa["required"] and commute["required"],
          "the three live-markup questions are marked required")
    check(not mode["required"], "unmarked group is not required")
    check(mode["options"] == ["Remote", "Hybrid", "onsite"],
          "non-Yes/No options read as-is, data-option as fallback text")
    check(prior["current_value"] == "No", "aria-pressed read as current state")
    for f in groups:
        stamped = [page.locator(f"[data-ja-ref='{r}']").count() for r in f["option_refs"]]
        ok = (f["ref"] == f["option_refs"][0] and stamped == [1] * len(stamped)
              and page.locator(f"[data-ja-ref='{f['ref']}']").evaluate(
                  "e => e.tagName") == "BUTTON")
        check(ok, f"refs resolve, group ref == first option's ({f['label'][:30]})")

    print("\n-- routing (categories given by hand; the classifier is the API's job)")
    prof = FakeProfile()
    for f, cat in ((auth, "work_authorization"), (visa, "work_authorization"),
                   (commute, "per_company")):
        v = sensitive.decide(f, cat, prof)
        print(f"  {f['label'][:50]!r:<54} -> {v['action']} {v.get('value')!r} "
              f"{v.get('profile_path', '')}")
    check(sensitive.decide(auth, "work_authorization", prof)["value"] == "Yes",
          "authorization fills 'Yes' from the profile")
    check(sensitive.decide(visa, "work_authorization", prof)["value"] == "No",
          "sponsorship fills 'No' from the profile")
    check(sensitive.decide(commute, "per_company", prof)["action"] == "human",
          "commute declaration comes to you")

    print("\n-- fill")
    plan = {auth["ref"]: "Yes", visa["ref"]: "No", mode["ref"]: "Hybrid",
            prior["ref"]: "No", terms["ref"]: "I agree", reloc["ref"]: "Yes",
            by_label("Preferred contact method")["ref"]: "Email",
            by_label("First name")["ref"]: "Test"}
    results = {}
    for f in fields:
        if f["ref"] in plan:
            results[f["ref"]] = filler.fill_one(page, f, plan[f["ref"]])
            print(f"  {f['label'][:45]!r:<49} {plan[f['ref']]!r:<10} -> {results[f['ref']]}")

    pressed = lambda f: [page.locator(f"[data-ja-ref='{r}']").get_attribute("aria-pressed")
                         for r in f["option_refs"]]
    check(pressed(auth) == ["true", "false"], "authorization: Yes pressed")
    check(page.locator("input[name='q-auth']").is_checked(), "  ...and the hidden checkbox followed")
    check(pressed(visa) == ["false", "true"], "sponsorship: No pressed")
    check(pressed(mode) == ["false", "true", "false"], "non-Yes/No group: Hybrid pressed")
    check(results[prior["ref"]] == (True, "already selected") and pressed(prior) == ["false", "true"],
          "already-pressed answer left alone, not toggled off")
    check(results[terms["ref"]][0] is False and "refused" in results[terms["ref"]][1]
          and pressed(terms) == ["false", "false"], "'I agree' refused, nothing pressed")
    check(results[reloc["ref"]][0] is False and "refused" in results[reloc["ref"]][1],
          "button that would submit its <form> refused")
    check(not page.evaluate("window.__submitted"), "form never submitted")
    check(not any(c.startswith(("q-terms", "q-relocate")) for c in page.evaluate("window.__clicks")),
          "no click ever reached a refused button")
    check(page.locator("input[name='contact'][value='email']").is_checked(),
          "ordinary radio group still fills")
    check(page.locator("#fname").input_value() == "Test", "ordinary text still fills")

    print("\n-- snapshot (what the correction log reads)")
    snap = extract.snapshot(page, fields)
    for f in groups:
        print(f"  {f['label'][:50]!r:<54} {snap.get(f['ref'])!r}")
    check(snap[auth["ref"]] == "Yes" and snap[visa["ref"]] == "No"
          and snap[mode["ref"]] == "Hybrid" and snap[terms["ref"]] is None,
          "snapshot reads button groups by aria-pressed")

    browser.close()

print(f"\n{'ALL PASSED' if not failures else f'{len(failures)} FAILED'}")
sys.exit(1 if failures else 0)
