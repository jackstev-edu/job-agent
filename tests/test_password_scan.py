"""
test_password_scan.py — the field scan never reads a password box's value.

WHAT THIS DOES: Loads a sign-up form whose password boxes already hold a
sentinel (as if you or Chrome had filled them): a plain password box, a
confirm box hinted `new-password`, a password box flipped to text by a
"show" toggle, and a text box named like one. Runs the real scan and checks
the sentinel is nowhere in what page.evaluate hands back, while the boxes
are still listed and an ordinary box's value is still read.
WHY IT MATTERS: Follow-along mode rescans pages after you've typed a
password. The agent never reads a password, so the scan must not either.

Run from anywhere:  .venv/Scripts/python.exe tests/test_password_scan.py
"""
import json

from _harness import TEST_HOME, Checks, mock_page, run_as_script
from agent import extract

SENTINEL = "PW-SCAN-SENTINEL-3307"

FORM = f"""<!doctype html><html><body><form>
  <label for="em">Email</label><input id="em" type="email" value="t@x.invalid">
  <label for="pw">Password</label><input id="pw" type="password">
  <label for="pw2">Confirm password</label><input id="pw2" type="text" autocomplete="new-password">
  <label for="pw3">Choose a password</label><input id="pw3" name="password" type="password">
  <label for="pw4">PIN</label><input id="pw4" name="user_pwd" type="text">
</form>
<script>
  for (const id of ['pw', 'pw2', 'pw3', 'pw4']) document.getElementById(id).value = '{SENTINEL}';
  document.getElementById('pw3').type = 'text';      // a "show password" toggle
</script></body></html>"""


def test_scan_never_reads_a_password_value():
    check = Checks()
    path = TEST_HOME / "password_scan.html"
    path.write_text(FORM, encoding="utf-8")
    with mock_page(path) as page:
        seen, real = [], page.evaluate

        def spy(*a, **k):
            out = real(*a, **k)
            seen.append(out)
            return out
        page.evaluate = spy
        check(real("() => document.getElementById('pw').value") == SENTINEL,
              "the boxes really hold the sentinel (so the next checks mean something)")
        fields = extract.extract_fields(page)
    by = {f["label"]: f for f in fields}
    print(f"  fields: {[(f['label'], f['type'], f.get('current_value')) for f in fields]}")
    check(SENTINEL not in json.dumps(seen, default=str),
          "the sentinel is nowhere in what the scan returned")
    for label in ("Password", "Confirm password", "Choose a password", "PIN"):
        check(label in by and by[label]["current_value"] == "",
              f"'{label}' is still listed, with no value read")
    check(by["Password"]["type"] == "password", "a password box is still typed as one")
    check(by["Email"]["current_value"] == "t@x.invalid", "an ordinary box's value is still read")
    check.done()


if __name__ == "__main__":
    run_as_script(globals())
