"""
test_password_scan.py — no reader or writer ever takes a password's value.

WHAT THIS DOES: Puts a sentinel password into boxes that hide it in every
way this round found: a box with a neutral id, name and label that is
type=password at the first scan and then toggled to text, the same box with
its marker stripped by page script, the same box replaced by a re-render, and
text boxes named like a password in several languages. Every value
page.evaluate hands back is recorded, and the sentinel must be in none of
them, through the scan, the snapshot (called directly too) and
readable_refs. Then a real `fill` runs on a form with such boxes, and the
sentinel must be absent from everything evaluate and the submit hook
returned, the printed output, the corrections log, the review sheet, the
tracker rows and the raw applications.db bytes, gate_runs included.
WHY IT MATTERS: The agent never reads, types, generates or stores a
password. A "show password" toggle, a stripped marker or a re-render must
not change that.

Run from anywhere:  .venv/Scripts/python.exe tests/test_password_scan.py
"""
import json
from contextlib import ExitStack
from pathlib import Path
from unittest import mock

from _harness import TEST_HOME, Checks, mock_page, run_as_script
from agent import config, extract, outcome, tracker

SENTINEL = "PW-SCAN-SENTINEL-3307"

# One neutral box (no name, an id like a framework's, a label that says
# nothing about passwords) that starts as type=password.
NEUTRAL = f"""<!doctype html><html><body><form id="f">
  <label for="em">Email</label><input id="em" name="email" type="email">
  <div id="slot"><label for="input-7">Choose one</label><input id="input-7" type="password"></div>
</form>
<script>
  document.getElementById('input-7').value = '{SENTINEL}';
  window.toggle = () => {{ document.getElementById('input-7').type = 'text'; }};
  window.strip = () => document.getElementById('input-7').removeAttribute('data-ja-secret');
  // A re-render: a brand-new element, already showing its value as text.
  window.rerender = () => {{
    document.getElementById('slot').innerHTML =
      '<label for="input-7">Choose one</label><input id="input-7" type="text">';
    document.getElementById('input-7').value = '{SENTINEL}';
  }};
</script></body></html>"""

# Text boxes that say "password" by name, id, label or placeholder.
WORDS = [("name", "kennwort"), ("name", "passwort"), ("id", "contrasena"),
         ("label", "Contraseña"), ("label", "Senha"), ("placeholder", "Mot de passe"),
         ("name", "motdepasse"), ("name", "mot_de_passe"), ("label", "Wachtwoord"),
         ("label", "Lösenord"), ("label", "Salasana"), ("label", "Hasło"), ("label", "Heslo"),
         ("label", "Jelszó"), ("label", "Пароль"), ("label", "密码"), ("label", "パスワード"),
         ("label", "비밀번호"), ("name", "user_pwd"), ("label", "Passcode")]


def _languages_form() -> str:
    boxes = []
    for i, (where, word) in enumerate(WORDS):
        attrs = {"name": f'name="{word}"', "id": f'id="{word}"',
                 "placeholder": f'placeholder="{word}"'}.get(where, "")
        label = word if where == "label" else f"Box {i}"
        box_id = word if where == "id" else f"b{i}"
        attrs = attrs.replace(f'id="{word}"', "") if where == "id" else attrs
        boxes.append(f'<label for="{box_id}">{label}</label>'
                     f'<input id="{box_id}" type="text" {attrs} value="{SENTINEL}">')
    return ("<!doctype html><html><body><form>" + "".join(boxes)
            + '<label for="ok">City</label><input id="ok" name="city" value="Springfield">'
            + "</form></body></html>")


def _spy(page) -> list:
    seen, real = [], page.evaluate

    def spy(*a, **k):
        out = real(*a, **k)
        seen.append(out)
        return out
    page.evaluate = spy
    return seen


def _write(name: str, html: str) -> Path:
    path = TEST_HOME / name
    path.write_text(html, encoding="utf-8")
    return path


def test_neutral_box_toggled_stripped_and_rerendered():
    check = Checks()
    with mock_page(_write("pw_neutral.html", NEUTRAL)) as page:
        real = page.evaluate
        check(real("() => document.getElementById('input-7').value") == SENTINEL,
              "the box really holds the sentinel (so the checks below mean something)")
        seen = _spy(page)
        keys: set = set()

        def box(fields):
            return next(f for f in fields if f.get("label") == "Choose one")

        first = extract.extract_fields(page, keys)
        check(box(first).get("secret") is True and box(first)["type"] == "password",
              "first scan: a password box, marked secret")
        real("window.toggle()")
        toggled = extract.extract_fields(page, keys)
        check(box(toggled).get("secret") is True and box(toggled)["type"] == "text",
              "toggled to text and rescanned: still secret (the page marker)")
        snap = extract.snapshot(page, toggled)
        check(box(toggled)["ref"] not in snap and box(toggled)["ref"] not in
              extract.readable_refs(toggled), "  ...readable_refs and the snapshot leave it out")
        direct = page.evaluate(extract._SNAPSHOT_JS, [box(toggled)["ref"]])
        check(box(toggled)["ref"] not in direct,
              "  ...and the snapshot refuses it even when asked for its ref directly")

        real("window.strip()")
        check(real("() => document.getElementById('input-7').hasAttribute('data-ja-secret')") is False,
              "page script stripped the marker")
        stripped = extract.extract_fields(page, keys)
        check(box(stripped).get("secret") is True, "marker stripped, rescanned: still secret (the sticky set)")
        check(box(stripped)["ref"] not in extract.snapshot(page, stripped),
              "  ...and the snapshot doesn't read it")

        real("window.rerender()")
        fresh = extract.extract_fields(page, keys)
        check(box(fresh).get("secret") is True and box(fresh)["type"] == "text",
              "replaced by a re-render with a new text element: still secret (the sticky set)")
        check(box(fresh)["ref"] not in extract.snapshot(page, fresh),
              "  ...and the snapshot doesn't read it")
        check(extract.snapshot(page, fresh).get(next(f["ref"] for f in fresh
                                                    if f["label"] == "Email")) == "",
              "an ordinary box is still read by the snapshot")
    check(SENTINEL not in json.dumps(seen, default=str),
          "the sentinel is nowhere in anything page.evaluate returned")
    check.done()


def test_text_boxes_named_like_a_password_in_many_languages():
    check = Checks()
    with mock_page(_write("pw_words.html", _languages_form())) as page:
        seen = _spy(page)
        fields = extract.extract_fields(page)
        by_label = {f["label"]: f for f in fields}
        for i, (where, word) in enumerate(WORDS):
            label = word if where == "label" else f"Box {i}"
            f = by_label.get(label)
            check(f is not None and f.get("secret") is True,
                  f"{where} {ascii(word)}: marked secret")      # ascii(): Windows consoles
        secret_refs = [f["ref"] for f in fields if f.get("secret")]
        direct = page.evaluate(extract._SNAPSHOT_JS, secret_refs)
        check(not set(direct) & set(secret_refs),
              "the snapshot refuses every one of them, asked directly")
        city = next(f for f in fields if f["label"] == "City")
        check(not city.get("secret") and extract.snapshot(page, fields).get(city["ref"]) == "Springfield",
              "an ordinary box beside them isn't secret, and is read")
    check(SENTINEL not in json.dumps(seen, default=str, ensure_ascii=False),
          "the sentinel is nowhere in anything page.evaluate returned")
    check.done()


FILL_FORM = f"""<!doctype html><html><body><form>
  <label for="fn">First Name *</label><input id="fn" name="first_name" required>
  <label for="em">Email</label><input id="em" name="email" type="email">
  <label for="input-7">Choose one</label><input id="input-7" type="password">
  <label for="b2">Your key</label><input id="b2" name="kennwort" type="text">
  <label for="b3">Repeat it</label><input id="b3" type="text" autocomplete="new-password">
  <button type="button" id="go">Submit Application</button>
</form>
<script>
  // Stands in for the browser's password manager and for you: the boxes are
  // filled before the agent looks, and you press "show" on the password once
  // the agent has started typing (after its scan saw a password box).
  for (const id of ['input-7', 'b2', 'b3']) document.getElementById(id).value = '{SENTINEL}';
  document.getElementById('fn').addEventListener('input', () => {{
    document.getElementById('input-7').type = 'text';
  }}, {{once: true}});
</script></body></html>"""


def test_fill_never_stores_or_returns_the_password():
    from playwright.sync_api import ElementHandle, Frame, Locator, Page
    from _fill_run import run_fill

    check = Checks()
    form = _write("pw_fill.html", FILL_FORM)
    seen: list = []

    def spying(cls):
        real = cls.evaluate

        def spy(self, *a, **k):
            out = real(self, *a, **k)
            seen.append(out)
            return out
        return (cls, "evaluate", spy)
    real_accept = outcome._accept
    hook = (outcome, "_accept", lambda raw, refs: (seen.append(raw), real_accept(raw, refs))[1])
    patches = tuple(spying(c) for c in (Page, Frame, ElementHandle, Locator)) + (hook,)
    # A password box reads as a login wall: Enter at each of its three pauses
    # (before Apply, after Apply, at step 1), then "n" at "Did you submit it?".
    text, records, error = run_fill(form, dry_run=False, answers=("", "", "", "n"),
                                    patches=patches)
    print(text[-900:])
    check(error is None, f"the run finished ({error})")
    check(any(isinstance(x, list) and x for x in seen),
          "the spies saw the page's reads (so the next checks mean something)")
    check(SENTINEL not in json.dumps(seen, default=str), "not in anything evaluate or the hook returned")
    check(SENTINEL not in text, "not printed")
    rec = records[0] if records else {}
    secret = [f["label"] for f in rec.get("fields", []) if f.get("secret") or f.get("type") == "password"]
    check(set(secret) >= {"Choose one", "Your key", "Repeat it"},
          f"all three boxes are secret in the record ({secret})")
    check(not any(f.get("label") in ("Choose one", "Your key", "Repeat it")
                  for f in rec.get("fields", []) if f["ref"] in (rec.get("plan") or {})),
          "none of them is in the plan, so nothing was typed into them")
    check(records and SENTINEL not in json.dumps(records, default=str),
          "not in the record the gate was shown")
    corrections = config.STATE_DIR / "corrections.jsonl"
    check(not corrections.exists() or SENTINEL not in corrections.read_text(encoding="utf-8"),
          "not in the corrections log")
    row = tracker.find(form.resolve().as_uri()) or {}
    sheet = Path(row.get("report_path") or "")
    check(sheet.is_file() and SENTINEL not in sheet.read_text(encoding="utf-8"),
          "not in the review sheet")
    check(SENTINEL not in json.dumps(tracker.all_applications(), default=str)
          and SENTINEL not in json.dumps(tracker.gate_runs(), default=str),
          "not in the tracker's applications or gate_runs rows")
    raw = Path(config.DATABASE_PATH).read_bytes()
    check(SENTINEL.encode() not in raw, "not in the raw applications.db bytes")
    stored = json.loads([r for r in tracker.gate_runs()
                         if r["application_url"] == form.resolve().as_uri()][-1]["record_json"])
    kept = [f for f in stored["fields"] if f.get("secret")]
    check(kept and all(set(f) == {"ref", "label", "type", "secret"} for f in kept),
          "gate_runs keeps a secret box by label and kind only")
    check.done()


if __name__ == "__main__":
    run_as_script(globals())
