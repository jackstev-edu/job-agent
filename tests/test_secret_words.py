"""
test_secret_words.py — password words hidden in camelCase are still caught, in the page and in Python.

WHAT THIS DOES: Boxes named, id'd, labelled or placeholdered pinCode,
PINCode, apiToken, authToken, accessToken, secretAnswer and userPin, each
holding a sentinel. The page's own test (the scan JS run alone) must mark
every one secret, the Python test (says_secret) must too, and a full scan
and snapshot must never return the sentinel. Ordinary camelCase names that
only contain the words must stay readable.
WHY IT MATTERS: The whole-word patterns (PIN, token, secret) need a word
boundary, and camelCase hides it: "pinCode" has none before "Code".

Run from anywhere:  .venv/Scripts/python.exe tests/test_secret_words.py
"""
import json
from pathlib import Path

from _harness import TEST_HOME, Checks, mock_page, run_as_script
from agent import extract

SENTINEL = "CAMEL-SECRET-SENTINEL-2290"

# (where the word is, the word)
CAMEL = [("name", "pinCode"), ("id", "PINCode"), ("label", "apiToken"),
         ("placeholder", "authToken"), ("name", "accessToken"), ("label", "secretAnswer"),
         ("name", "userPin")]
ORDINARY = ["spinCycle", "pinnacleScore", "secretaryName", "topSecretClearance",
            "shippingCode", "tokensEarned"]


def _form() -> str:
    boxes = []
    for i, (where, word) in enumerate(CAMEL):
        box_id = word if where == "id" else f"s{i}"
        attr = {"name": f'name="{word}"', "placeholder": f'placeholder="{word}" name="n{i}"'
                }.get(where, f'name="n{i}"' if where != "id" else "")
        label = word if where == "label" else f"Box {i}"
        boxes.append(f'<label for="{box_id}">{label}</label>'
                     f'<input id="{box_id}" {attr} value="{SENTINEL}">')
    for i, word in enumerate(ORDINARY):
        boxes.append(f'<label for="o{i}">Question {i}</label>'
                     f'<input id="o{i}" name="{word}" value="ordinary-{i}">')
    return "<!doctype html><html><body><form>" + "".join(boxes) + "</form></body></html>"


def _write(name: str, html: str) -> Path:
    path = TEST_HOME / name
    path.write_text(html, encoding="utf-8")
    return path


def test_the_page_layer_catches_camel_case():
    check = Checks()
    with mock_page(_write("camel_js.html", _form())) as page:
        # The scan's JS alone, without the Python layer that runs after it.
        scanned = page.evaluate(extract._SCAN_JS, [extract.BUTTON_GROUP, "gjs"])
    by_box = {(f.get("dom_id") or ""): f for f in scanned["fields"]}
    for i, (where, word) in enumerate(CAMEL):
        f = by_box.get(word if where == "id" else f"s{i}", {})
        check(f.get("secret") is True, f"the page marks a box with {word} in its {where} secret")
    for i, word in enumerate(ORDINARY):
        check(not by_box.get(f"o{i}", {}).get("secret"), f"  ...but not one named {word}")
    check.done()


def test_the_python_layer_catches_camel_case():
    check = Checks()
    key = {"name": "dom_name", "id": "dom_id", "label": "label", "placeholder": "placeholder"}
    for where, word in CAMEL:
        check(extract.says_secret({key[where]: word}), f"says_secret: {word} in {key[where]}")
        check(extract._SECRET_RX.search(word) is None,
              f"  ...where the plain pattern alone misses {word} (so the check means something)")
    for word in ORDINARY:
        check(not extract.says_secret({"dom_name": word, "label": "Question"}),
              f"says_secret leaves {word} alone")
    check.done()


def test_a_full_scan_never_reads_them():
    check = Checks()
    with mock_page(_write("camel_full.html", _form())) as page:
        fields = extract.extract_fields(page, extract.ReadGuard())
        snap = extract.snapshot(page, fields)
    secret = {f.get("dom_id") for f in fields if extract.is_secret(f)}
    check(len(secret) == len(CAMEL), f"all {len(CAMEL)} camelCase boxes are secret ({sorted(secret)})")
    check(SENTINEL not in json.dumps(snap), "the snapshot holds none of their values")
    check(sum(1 for v in snap.values() if str(v).startswith("ordinary-")) == len(ORDINARY),
          "the ordinary boxes still read (so the check above means something)")
    check.done()


if __name__ == "__main__":
    run_as_script(globals())
