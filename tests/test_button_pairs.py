"""
test_button_pairs.py — regression for Ashby's Yes/No button-pair questions.

WHAT THIS DOES: Loads tests/button_pairs.html in a headless browser and checks
the whole path: scan, routing, fill, and the snapshot the correction log reads.
WHY IT MATTERS: This is the one fill path that clicks a <button>, so the traps
here (an "I agree" option, a button inside a real <form>) must stay refused.

Run from anywhere:  .venv/Scripts/python.exe tests/test_button_pairs.py
"""
from pathlib import Path

from _harness import Checks, mock_page, run_as_script
from agent import extract, filler, sensitive

FORM = Path(__file__).parent / "button_pairs.html"


class FakeProfile:
    """Stands in for profile.yaml; only the lines these questions route to."""
    data = {"eligibility.authorized_to_work_us": True,
            "eligibility.requires_sponsorship_future": False,
            "eligibility.requires_sponsorship_now": False,
            "eligibility.willing_to_relocate": True}

    def get(self, path):
        return self.data.get(path)


# What test_fill and test_snapshot type in, by the start of each label.
# "I agree" and the in-<form> "Yes" are the traps: both must be refused.
PLAN = {"Are you legally authorized": "Yes", "Will you now or in the fut": "No",
        "Preferred working arrangem": "Hybrid", "Have you previously applie": "No",
        "Terms of use": "I agree", "Are you willing to relocat": "Yes",
        "Preferred contact method": "Email", "First name": "Test"}


def _scan(page) -> list[dict]:
    fields = extract.extract_fields(page)
    for f in fields:
        print(f"  {f['ref']:<4} {f['type']:<6} {f.get('widget', ''):<7} "
              f"req={f['required']!s:<5} {f['label'][:60]!r} {f.get('options')} "
              f"now={f.get('current_value', '')!r}")
    return fields


def _by_label(fields, start) -> dict:
    return next(f for f in fields if f["label"].startswith(start))


def _fill(page, fields) -> dict:
    plan = {_by_label(fields, start)["ref"]: value for start, value in PLAN.items()}
    results = {}
    for f in fields:
        if f["ref"] in plan:
            results[f["ref"]] = filler.fill_one(page, f, plan[f["ref"]])
            print(f"  {f['label'][:45]!r:<49} {plan[f['ref']]!r:<10} -> {results[f['ref']]}")
    return results


def _pressed(page, f) -> list:
    return [page.locator(f"[data-ja-ref='{r}']").get_attribute("aria-pressed")
            for r in f["option_refs"]]


def test_scan():
    check = Checks()
    with mock_page(FORM) as page:
        fields = _scan(page)
        groups = [f for f in fields if f.get("widget") == "buttons"]
        labels = [f["label"] for f in fields]

        check(len(groups) == 7, f"7 visible button groups found (got {len(groups)})")
        check(not any("Hidden follow-up" in l for l in labels), "hidden group skipped")
        check(not any(f.get("name") in ("stray", "q-auth") and f["type"] == "checkbox"
                      for f in fields), "hidden checkboxes not emitted as fields")
        check(labels[:3] == ["First name", "Preferred contact method",
                             "Are you legally authorized to work in the United States?"],
              "page order kept: groups land where they sit in the DOM")
        auth = _by_label(fields, "Are you legally authorized")
        visa = _by_label(fields, "Will you now or in the fut")
        commute = _by_label(fields, "This role requires being ")
        mode = _by_label(fields, "Preferred working arrangem")
        prior = _by_label(fields, "Have you previously applie")
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
    check.done()


def test_routing():
    # Categories given by hand; the classifier is the API's job.
    check = Checks()
    with mock_page(FORM) as page:
        fields = _scan(page)
    auth = _by_label(fields, "Are you legally authorized")
    visa = _by_label(fields, "Will you now or in the fut")
    commute = _by_label(fields, "This role requires being ")
    prof = FakeProfile()
    for f, cat in ((auth, "work_authorization"), (visa, "work_authorization"),
                   (commute, "per_company")):
        v = sensitive.decide(f, cat, prof)
        print(f"  {f['label'][:50]!r:<54} -> {v['action']} {v.get('value')!r} "
              f"{v.get('profile_path', '')}")
    check(sensitive.decide(auth, "work_authorization", prof)["value"] == "Yes",
          "authorization fills 'Yes' from the profile")
    # "Now or in the future ... sponsorship" matches both the future and the
    # now sponsorship lines, and a question touching two profile lines comes
    # to you (Jack's rule since the all-lines check).
    v = sensitive.decide(visa, "work_authorization", prof)
    check(v["action"] == "human" and "more than one profile line" in v.get("reason", ""),
          "sponsorship (two profile lines) comes to you")
    check(sensitive.decide(commute, "per_company", prof)["action"] == "human",
          "commute declaration comes to you")
    check.done()


def test_fill():
    check = Checks()
    with mock_page(FORM) as page:
        fields = _scan(page)
        results = _fill(page, fields)
        auth = _by_label(fields, "Are you legally authorized")
        visa = _by_label(fields, "Will you now or in the fut")
        mode = _by_label(fields, "Preferred working arrangem")
        prior = _by_label(fields, "Have you previously applie")
        terms = _by_label(fields, "Terms of use")
        reloc = _by_label(fields, "Are you willing to relocat")

        check(_pressed(page, auth) == ["true", "false"], "authorization: Yes pressed")
        check(page.locator("input[name='q-auth']").is_checked(),
              "  ...and the hidden checkbox followed")
        check(_pressed(page, visa) == ["false", "true"], "sponsorship: No pressed")
        check(_pressed(page, mode) == ["false", "true", "false"],
              "non-Yes/No group: Hybrid pressed")
        check(results[prior["ref"]] == (True, "already selected")
              and _pressed(page, prior) == ["false", "true"],
              "already-pressed answer left alone, not toggled off")
        check(results[terms["ref"]][0] is False and "refused" in results[terms["ref"]][1]
              and _pressed(page, terms) == ["false", "false"],
              "'I agree' refused, nothing pressed")
        check(results[reloc["ref"]][0] is False and "refused" in results[reloc["ref"]][1],
              "button that would submit its <form> refused")
        check(not page.evaluate("window.__submitted"), "form never submitted")
        check(not any(c.startswith(("q-terms", "q-relocate"))
                      for c in page.evaluate("window.__clicks")),
              "no click ever reached a refused button")
        check(page.locator("input[name='contact'][value='email']").is_checked(),
              "ordinary radio group still fills")
        check(page.locator("#fname").input_value() == "Test", "ordinary text still fills")
    check.done()


def test_snapshot():
    # What the correction log reads after you've finished with the form.
    check = Checks()
    with mock_page(FORM) as page:
        fields = _scan(page)
        _fill(page, fields)
        snap = extract.snapshot(page, fields)
    for f in fields:
        if f.get("widget") == "buttons":
            print(f"  {f['label'][:50]!r:<54} {snap.get(f['ref'])!r}")
    check(snap[_by_label(fields, "Are you legally authorized")["ref"]] == "Yes"
          and snap[_by_label(fields, "Will you now or in the fut")["ref"]] == "No"
          and snap[_by_label(fields, "Preferred working arrangem")["ref"]] == "Hybrid"
          and snap[_by_label(fields, "Terms of use")["ref"]] is None,
          "snapshot reads button groups by aria-pressed")
    check.done()


if __name__ == "__main__":
    run_as_script(globals())
