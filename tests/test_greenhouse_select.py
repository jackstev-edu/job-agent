"""
test_greenhouse_select.py — one field per question on a Greenhouse react-select form.

WHAT THIS DOES: Scans tests/greenhouse_select.html and checks that a required
react-select dropdown comes out once, not twice, and that a styled radio group
whose real inputs are opacity 0 is still read.
WHY IT MATTERS: The duplicate was react-select's hidden validation input being
read as its own question; the fix must not also drop inputs that are only
visually hidden, which styled radios on many portals are.

Run from anywhere:  .venv/Scripts/python.exe tests/test_greenhouse_select.py
"""
from pathlib import Path

from _harness import Checks, mock_page, run_as_script
from agent import extract

FORM = Path(__file__).parent / "greenhouse_select.html"


def test_one_field_per_question():
    check = Checks()
    with mock_page(FORM) as page:
        fields = extract.extract_fields(page)
    for f in fields:
        print(f"  {f['ref']:<4} {f['type']:<8} req={f['required']!s:<5} "
              f"{f['label'][:60]!r} {f.get('options')}")

    starts = ("First Name", "Are you legally authorized", "Pronouns",
              "Preferred working arrangement")
    for start in starts:
        n = sum(f["label"].startswith(start) for f in fields)
        check(n == 1, f"exactly one field for {start!r} (got {n})")
    check(len(fields) == len(starts), f"{len(starts)} fields in all (got {len(fields)})")
    # The combobox's name also comes out as question_1 (it falls back to the
    # id), so the requiredInput is the one that would arrive as plain text.
    check(not any(f.get("name") == "question_1" and f["type"] != "combobox"
                  for f in fields),
          "react-select's requiredInput is not emitted")

    by_label = {s: next((f for f in fields if f["label"].startswith(s)), {}) for s in starts}
    auth = by_label["Are you legally authorized"]
    check(auth.get("type") == "combobox" and auth.get("required") is True,
          "required dropdown read as a required combobox")
    pronouns = by_label["Pronouns"]
    check(pronouns.get("type") == "combobox" and pronouns.get("required") is False,
          "optional dropdown read as an optional combobox")
    check.done()


def test_styled_radio_still_read():
    check = Checks()
    with mock_page(FORM) as page:
        fields = extract.extract_fields(page)
        radio = next((f for f in fields if f["type"] == "radio"), {})
        check(radio.get("label") == "Preferred working arrangement",
              "opacity-0 radio group found under its legend")
        check(radio.get("options") == ["Remote", "Hybrid", "On-site"],
              "all three options read, including the tabindex=-1 ones")
        refs = radio.get("option_refs") or []
        check(len(refs) == 3 and all(
                  page.locator(f"[data-ja-ref='{r}']").count() == 1 for r in refs),
              "every option is stamped and resolvable")
    check.done()


if __name__ == "__main__":
    run_as_script(globals())
