"""
test_gate_data.py — the facts the submit gate is built on, gathered correctly.

WHAT THIS DOES: Checks the checkbox census (hidden boxes, custom role=checkbox
boxes, where each label came from, CSS-hidden clauses, fine print, Ashby
button pairs), the iframe inventory, the fit kind recorded for profile
answers, how the portal was detected, and the named reasons behind every
review flag.
WHY IT MATTERS: The gate is only as strict as these inputs. A label read from
the wrong place, or a fuzzy fit recorded as exact, would let it pass
something it shouldn't.

Run from anywhere:  .venv/Scripts/python.exe tests/test_gate_data.py
"""
from pathlib import Path

from _harness import Checks, mock_page, run_as_script
from agent import adapters, extract, mapper, sensitive

FORM = Path(__file__).parent / "consent_boxes.html"


def test_checkbox_census():
    check = Checks()
    with mock_page(FORM) as page:
        fields = extract.extract_fields(page)       # stamps refs, as in a real run
        census = extract.checkbox_census(page)
        frames = extract.frame_inventory(page)
    for c in census:
        print(f"  {c}")
    by_text = {c["label_content"]: c for c in census}

    sms = by_text.get("I agree to receive SMS updates", {})
    check(sms.get("label_source") == "for" and sms.get("checked") is False,
          "plain consent: label from for=, unticked")
    hidden = next((c for c in census if "binding arbitration" in c["label_content"]
                   and c["label_source"] == "wrap"), {})
    check(hidden.get("label_inner") == "I agree to receive SMS updates"
          and hidden.get("label_content") == "I agree to receive SMS updates and to binding arbitration",
          "a CSS-hidden clause shows up as innerText != textContent")
    bg = by_text.get("I consent to a background check", {})
    check(bg and bg["visible"] is False and bg["checked"] is True,
          "a hidden, pre-ticked native checkbox is still counted")
    news = by_text.get("Subscribe to the newsletter", {})
    check(news.get("label_source") == "self" and news.get("checked") is True,
          "a custom role=checkbox is counted, with aria-checked read as its state")
    check(any(c["label_source"] == "none" for c in census), "an unlabelled checkbox says so")
    terms = by_text.get("I agree", {})
    check(terms.get("described") == "By ticking this you accept the Terms of Service.",
          "fine print in aria-describedby is captured")
    pair = next((c for c in census if c["in_button_group"]), {})
    group_field = next((f for f in fields if f.get("widget") == "buttons"), {})
    check(pair and pair["group_ref"] == group_field.get("ref"),
          "an Ashby pair's checkbox points at its question's ref in the plan")
    check(len(census) == 11, f"11 checkboxes counted (got {len(census)})")
    # Required as the page states it (gate version 8 waives nothing on a
    # box read as required): the attribute, aria-required, a trailing *.
    check(sms.get("required") is False, "a plain box with no required sign reads required=False")
    check(by_text.get("I agree to the Terms of Service", {}).get("required") is True,
          "the required attribute reads True")
    check(by_text.get("I accept the Privacy Policy", {}).get("required") is True,
          "aria-required=true on a custom box reads True")
    check(by_text.get("Keep me posted *", {}).get("required") is True,
          "a trailing * on the label reads True")
    check(all(isinstance(c.get("required"), bool) for c in census),
          "every box carries a boolean required key")
    check(frames == [{"src": "about:blank", "visible": True}], "the embedded frame is listed")
    check.done()


def test_fit_kinds():
    check = Checks()
    radio = lambda opts: {"type": "radio", "options": opts}
    cases = [
        (True, {"type": "checkbox"}, "bool"),
        (False, {"type": "text"}, "bool"),
        ("US Citizen", {"type": "text"}, "verbatim"),
        (True, radio(["Yes", "No"]), "bool"),
        (True, radio(["Yes, I am authorized", "No, I am not"]), "bool_prefix"),
        ("US Citizen", radio(["US Citizen", "Permanent Resident"]), "exact"),
        ("US Citizen", radio(["US Citizen or Permanent Resident", "Other"]), "substring"),
        ("Prefer not to say", radio(["Male", "Female", "I don't wish to answer"]), "decline"),
        ("Martian", radio(["Male", "Female"]), None),
    ]
    for raw, field, want in cases:
        value, kind = sensitive.fit_with_kind(raw, field)
        check(kind == want, f"{raw!r} into {field.get('options') or field['type']} -> {want} (got {kind})")
        check(sensitive.fit_to_widget(raw, field) == value, "  ...and fit_to_widget still agrees")

    class P:
        def get(self, path):
            return {"eligibility.authorized_to_work_us": True}.get(path)
    v = sensitive.decide({"label": "Are you legally authorized to work in the US?",
                          "type": "radio", "options": ["Yes", "No"]},
                         "work_authorization", P())
    check(v["action"] == "fill" and v["profile_path"] == "eligibility.authorized_to_work_us"
          and v["fit"] == "bool", "decide records the line and the fit kind")
    v = sensitive.decide({"label": "Are you legally authorized to work in the US?",
                          "type": "radio", "options": ["Yes, I am", "No"]},
                         "work_authorization", P())
    check(v["action"] == "human",
          "a legal question fitted only loosely (bool_prefix) comes to you, not filled")
    check.done()


def test_detection_method():
    check = Checks()

    class Page:
        def __init__(self, html):
            self.html = html

        def content(self):
            return self.html

    cases = [("https://job-boards.greenhouse.io/acme/jobs/1", None, "greenhouse", "host"),
             ("https://careers.example.com/apply?ref=greenhouse.io", None, "greenhouse", "url"),
             ("https://careers.example.com/apply", Page("<div>Powered by greenhouse.io</div>"),
              "greenhouse", "html"),
             ("https://careers.example.com/apply", Page("<p>hello</p>"), "generic", "default"),
             # Item 4c of 2026-10-29 (afternoon); on bc7343d this was generic/default.
             ("https://acme.avature.net/careers/JobDetail/Engineer/1", None, "avature", "host"),
             ("https://acme.avature.net/careers/Application?folderId=1", Page("<p>x</p>"), "avature", "host")]
    for url, page, key, method in cases:
        got = adapters.detect_with_method(url, page)
        check((got[0], got[2]) == (key, method), f"{url} -> {key}/{method} (got {got[0]}/{got[2]})")
        check(adapters.detect(url, page)[0] == key, "  ...and detect() is unchanged")
    check.done()


def test_review_reasons_are_named():
    check = Checks()
    field = {"type": "text", "maxlength": 10}
    item = mapper._double_check({"value": "far too long for this", "confidence": "low",
                                 "needs_review": True}, field)
    check(item["review_reasons"] == ["model", "too_long", "low_confidence"],
          f"model, too_long and low_confidence each named (got {item['review_reasons']})")
    clean = mapper._double_check({"value": "ok", "confidence": "high"}, field)
    check(clean["review_reasons"] == [] and not clean.get("needs_review"),
          "a clean answer has no reasons")
    off = mapper._double_check({"value": "Martian", "confidence": "high"},
                               {"type": "radio", "options": ["Yes", "No"], "label": "Planet"})
    check("unmatched_option" in off["review_reasons"] and off["value"] is None,
          "an answer that isn't an option is blanked and named")
    check.done()


if __name__ == "__main__":
    run_as_script(globals())
