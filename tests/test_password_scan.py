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
from agent import config, extract, filler, outcome, tracker

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
         ("label", "비밀번호"), ("name", "user_pwd"), ("label", "Passcode"),
         ("label", "סיסמה"), ("label", "كلمة المرور"), ("label", "密碼"), ("name", "passwd"),
         ("label", "Pass word"),
         # Written in other Unicode forms, matched after NFKC normalisation:
         ("label", "Contrasen\u0303a"), ("label", "Lo\u0308senord"), ("label", "ｐａｓｓｗｏｒｄ")]


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
        keys = extract.ReadGuard()

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
        email = next(f for f in fresh if f["label"] == "Email")
        check(email.get("credential_step") and email["ref"] not in extract.snapshot(page, fresh),
              "the page had a password box, so it's a credential step: Email isn't read either")
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


TWO_STEP = f"""<!doctype html><html><body><form>
  <section id="s1"><label for="input-7">Choose one</label><input id="input-7" type="password"></section>
  <section id="s2" style="display:none"><label for="ci">City</label>
    <input id="ci" name="city" value="Springfield"></section>
</form>
<script>
  document.getElementById('input-7').value = '{SENTINEL}';
  window.next = () => {{
    const pw = document.getElementById('input-7');
    pw.type = 'text'; pw.removeAttribute('data-ja-secret');      // shown, marker gone
    document.getElementById('s1').style.display = 'none';         // hidden, not removed
    document.getElementById('s2').style.display = '';
  }};
</script></body></html>"""


def test_a_hidden_stale_box_never_shares_a_ref():
    check = Checks()
    with mock_page(_write("pw_two_step.html", TWO_STEP)) as page:
        real = page.evaluate
        seen = _spy(page)
        keys = extract.ReadGuard()
        first = extract.extract_fields(page, keys)
        old = next(f for f in first if f["label"] == "Choose one")
        check(old.get("secret") is True, "step 1: the password box is seen and marked secret")
        real("window.next()")
        # The page held a password box, so it's a credential step and nothing
        # on it would read. Forget that here, to test the ref rule on its own.
        keys.credential_pages.clear()
        second = extract.extract_fields(page, keys)
        city = next(f for f in second if f["label"] == "City")
        check(not real("() => document.getElementById('input-7').hasAttribute('data-ja-ref')"),
              "step 2's scan cleared the hidden step-1 box's old ref")
        dupes = real("""() => {
            const n = {}; document.querySelectorAll('[data-ja-ref]').forEach(e => {
              const r = e.getAttribute('data-ja-ref'); n[r] = (n[r] || 0) + 1; });
            return Object.values(n).filter(c => c > 1).length; }""")
        check(dupes == 0, "no ref is carried by two elements")
        check(city["ref"] != old["ref"], f"the new field's ref ({city['ref']}) can't equal the old one ({old['ref']})")
        check(extract.snapshot(page, second) == {city["ref"]: "Springfield"},
              "the snapshot reads step 2's field, and only it")
        # A collision forced by the page: the hidden box given the new field's ref.
        real("r => document.getElementById('input-7').setAttribute('data-ja-ref', r)", city["ref"])
        check(city["ref"] not in extract.snapshot(page, second)
              and city["ref"] not in page.evaluate(extract._SNAPSHOT_JS, [city["ref"]]),
              "a ref carried by two elements is treated as missing: nothing read")
    check(SENTINEL not in json.dumps(seen, default=str),
          "the sentinel is nowhere in anything page.evaluate returned")
    check.done()


GENS = """<!doctype html><html><body><form>
  <label for="ci">City</label><input id="ci" name="city" value="Springfield">
  <label for="em">Email</label><input id="em" name="email" value="t@x.invalid">
</form></body></html>"""


def test_old_generation_refs_resolve_to_nothing():
    check = Checks()
    with mock_page(_write("pw_gens.html", GENS)) as page:
        real = page.evaluate
        first = extract.extract_fields(page)
        old_city = next(f for f in first if f["label"] == "City")
        second = extract.extract_fields(page)
        new_city = next(f for f in second if f["label"] == "City")
        print(f"  scan N ref {old_city['ref']}, scan N+1 ref {new_city['ref']}")
        check(old_city["ref"] != new_city["ref"] and not extract.is_current(old_city["ref"])
              and extract.is_current(new_city["ref"]), "a rescan gives a new generation of refs")
        check(page.locator(f"[data-ja-ref='{old_city['ref']}']").count() == 0,
              "the old ref is on no element")
        check(extract.readable_refs(first) == [] and extract.snapshot(page, first) == {},
              "the snapshot, given the old scan's fields, reads nothing")
        worked, detail = filler.fill_one(page, old_city, "Shelbyville")
        check(not worked and real("() => document.getElementById('ci').value") == "Springfield",
              f"the filler, given the old ref, finds nothing and changes nothing ({detail[:40]})")
        # Even if the page puts an old ref back on an element, it isn't resolved.
        real("""r => { const e = document.createElement('input'); e.value = 'planted';
                     e.setAttribute('data-ja-ref', r); document.forms[0].appendChild(e); }""",
             old_city["ref"])
        check(page.evaluate(extract._SNAPSHOT_JS, [old_city["ref"]]) == {},
              "an old-generation ref put back by the page is still treated as missing")
        check(extract.snapshot(page, second) == {new_city["ref"]: "Springfield",
                                                 next(f["ref"] for f in second
                                                      if f["label"] == "Email"): "t@x.invalid"},
              "the current scan's refs read as before")
    check.done()


REKEY = f"""<!doctype html><html><body><form><div id="slot">
  <label for="input-7">Choose one</label><input id="input-7" type="password"></div>
</form>
<script>
  document.getElementById('input-7').value = '{SENTINEL}';
  const swap = (id, label) => {{
    document.getElementById('slot').innerHTML =
      `<label for="${{id}}">${{label}}</label><input id="${{id}}" type="text">`;
    document.getElementById(id).value = '{SENTINEL}';
  }};
  window.newId = () => swap('input-9', 'Choose one');           // a framework id changes
  window.newLabel = () => swap('input-9', 'Choose one Show');   // then the label changes
</script></body></html>"""


def test_a_rerender_that_changes_one_key_is_still_secret():
    check = Checks()
    with mock_page(_write("pw_rekey.html", REKEY)) as page:
        real = page.evaluate
        seen = _spy(page)
        keys = extract.ReadGuard()
        first = extract.extract_fields(page, keys)
        check(first[0].get("secret") is True, "seen as a password first")
        real("window.newId()")
        f = extract.extract_fields(page, keys)[0]
        check(f.get("secret") is True and f["dom_id"] == "input-9",
              "re-rendered with a new id, same label: still secret (label key)")
        real("window.newLabel()")
        f = extract.extract_fields(page, keys)[0]
        check(f.get("secret") is True and f["label"] == "Choose one Show",
              "then a new label, same id as last time: still secret (id key)")
        check(real("() => document.getElementById('input-9').hasAttribute('data-ja-secret')"),
              "  ...and the element is marked again, so in-page readers refuse it too")
        check(f["ref"] not in extract.snapshot(page, [f])
              and f["ref"] not in page.evaluate(extract._SNAPSHOT_JS, [f["ref"]]),
              "  ...and nothing reads it")
    check(SENTINEL not in json.dumps(seen, default=str),
          "the sentinel is nowhere in anything page.evaluate returned")
    check.done()


# Boxes with nothing stable to know them by, each starting as a password.
ANON = f"""<!doctype html><html><body><form>
  <label for="em">Email</label><input id="em" name="email" type="email" value="t@x.invalid">
  <div id="a"><input type="password"></div>
  <div id="i"><input id="input-7" type="password"></div>
  <div id="p"><input placeholder="Type it here" type="password"></div>
</form>
<script>
  for (const box of document.querySelectorAll('input[type=password]')) box.value = '{SENTINEL}';
  const swap = (slot, html) => {{
    const d = document.getElementById(slot); d.innerHTML = html;
    d.querySelector('input').value = '{SENTINEL}';
  }};
  // Each one shown as text (a re-render: a new element, no marker).
  window.showAll = () => {{
    swap('a', '<input type="text">');
    swap('i', '<input id="input-9" type="text">');
    swap('p', '<input placeholder="Type it here" type="text">');
  }};
</script></body></html>"""


def test_anonymous_id_only_and_placeholder_only_boxes_are_never_read():
    check = Checks()
    with mock_page(_write("pw_anon.html", ANON)) as page:
        real = page.evaluate
        seen = _spy(page)
        guard = extract.ReadGuard()
        first = extract.extract_fields(page, guard)
        check(sum(1 for f in first if f.get("secret")) == 3, "all three seen as passwords first")
        real("window.showAll()")
        again = extract.extract_fields(page, guard)
        shown = [f for f in again if f.get("dom_name") != "email"]
        print(f"  after the re-render: {[(f['label'], f.get('dom_id'), f.get('secret'), f.get('stable_identity')) for f in shown]}")
        check(len(shown) == 3, "the three re-rendered boxes are scanned")
        anon = next(f for f in shown if not f.get("dom_id") and not f.get("placeholder"))
        check(anon.get("secret") is True,
              "the anonymous box, re-rendered as anonymous text: still secret (anon key)")
        idonly = next(f for f in shown if f.get("dom_id") == "input-9")
        check(idonly.get("stable_identity") is False,
              "the id-only box, re-rendered with a new id: no stable identity")
        ph = next(f for f in shown if f.get("placeholder") == "Type it here")
        check(ph.get("stable_identity") is False and ph.get("secret") is True,
              "the placeholder-only box: no stable identity, and still secret (anon key)")
        refs = [f["ref"] for f in shown]
        check(not set(extract.readable_refs(again)) & set(refs),
              "readable_refs leaves out all three")
        check(not set(extract.snapshot(page, again)) & set(refs)
              and not set(page.evaluate(extract._SNAPSHOT_JS, refs)) & set(refs),
              "the snapshot reads none of them, even asked for their refs directly")
        email = next(f for f in again if f.get("dom_name") == "email")
        check(email.get("credential_step") and email["ref"] not in extract.snapshot(page, again),
              "the page had password boxes, so it's a credential step: Email isn't read either")
    check(SENTINEL not in json.dumps(seen, default=str),
          "the sentinel is nowhere in anything page.evaluate returned")
    check.done()


CRED = f"""<!doctype html><html><body><form>
  <label for="em">Email</label><input id="em" name="email" type="email">
  <label for="nn">Nickname</label><input id="nn" name="nickname">
  <label for="pw">Password</label><input id="pw" name="password" type="password">
</form>
<script>
  document.getElementById('pw').value = '{SENTINEL}';
  document.getElementById('nn').value = '{SENTINEL}';   // a password typed in the wrong box
</script></body></html>"""


def test_a_credential_step_reads_only_what_the_agent_filled_from_your_identity():
    from agent.profile import Profile
    check = Checks()
    prof = Profile({"personal": {"email": "t@x.invalid", "first_name": "Test"}}, Path("p.yaml"))
    with mock_page(_write("pw_cred.html", CRED)) as page:
        seen = _spy(page)
        guard = extract.ReadGuard()
        fields = extract.extract_fields(page, guard)
        by = {f["label"]: f for f in fields}
        check(all(f.get("credential_step") for f in fields) and guard.credential_pages,
              "a page with a password box is remembered as a credential step")
        nick, email = by["Nickname"], by["Email"]
        check(nick.get("stable_identity") and not nick.get("secret"),
              "Nickname is named and labelled, and not secret by any pattern")
        check(nick["ref"] not in extract.readable_refs(fields)
              and nick["ref"] not in page.evaluate(extract._SNAPSHOT_JS, [nick["ref"]]),
              "on a credential step, Nickname is never read, by Python or asked directly in the page")
        page.fill("#em", "t@x.invalid")                       # as the agent would
        plan = {email["ref"]: {"value": "t@x.invalid"}, nick["ref"]: {"value": "Test"}}
        marked = extract.mark_identity(page, fields, plan, [email["ref"]], prof)
        check(marked == [email["ref"]], "the email box the agent filled is marked as yours")
        check(extract.snapshot(page, fields) == {email["ref"]: "t@x.invalid"},
              "the email box the agent filled reads; nothing else on the step does")
        page.evaluate("r => document.getElementById('nn').setAttribute('data-ja-identity', '1')")
        check(nick["ref"] not in extract.snapshot(page, fields),
              "the page marking Nickname as 'identity' doesn't make it readable (Python says no)")
    check(SENTINEL not in json.dumps(seen, default=str),
          "the sentinel is nowhere in anything page.evaluate returned")
    check.done()


def test_an_old_generation_ref_never_reaches_the_filler_through_fill():
    import apply
    check = Checks()
    with mock_page(_write("pw_gens_fill.html", GENS)) as page:
        real = page.evaluate
        first = extract.extract_fields(page)
        old_city = next(f for f in first if f["label"] == "City")
        second = extract.extract_fields(page)
        new_city = next(f for f in second if f["label"] == "City")
        real("""r => { const e = document.createElement('input'); e.id = 'planted';
                     e.setAttribute('data-ja-ref', r); document.forms[0].appendChild(e); }""",
             old_city["ref"])
        plan = {old_city["ref"]: {"value": "Planted"}, new_city["ref"]: {"value": "Shelbyville"}}
        kept = apply._fillable_plan(second, plan)
        check(list(kept) == [new_city["ref"]], "fill's filter keeps the current ref and drops the old one")
        filler.run_plan(page, second + [old_city], kept)
        check(real("() => document.getElementById('planted').value") == ""
              and real("() => document.getElementById('ci').value") == "Shelbyville",
              "the planted element holding the old ref is never typed into; the current field is")
    check.done()


def test_an_ordinary_form_still_reads_normally():
    check = Checks()
    html = """<!doctype html><html><body><form>
      <label for="ci">City</label><input id="ci" value="Springfield">
      <input name="zip" value="12345">
      <input aria-label="Country" value="Exampleland">
      <label><input type="checkbox" checked> Text me updates</label>
    </form></body></html>"""
    with mock_page(_write("pw_ordinary.html", html)) as page:
        fields = extract.extract_fields(page, extract.ReadGuard())
        snap = extract.snapshot(page, fields)
        # Keyed by each box's own attributes: the scan's label for the zip box
        # is a guess from nearby text.
        got = {(f.get("dom_id") or f.get("dom_name") or f.get("explicit_label") or f["type"]):
               snap.get(f["ref"]) for f in fields}
        print(f"  {got}")
        check(got == {"ci": "Springfield", "zip": "12345", "Country": "Exampleland",
                      "Text me updates": True},
              "a <label>, a name, an aria-label and a ticked box all read as before")
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
    payloads: list = []
    hook = (outcome, "_accept",
            lambda raw, refs: (seen.append(raw), payloads.append(raw), real_accept(raw, refs))[2])
    real_watch = outcome.watch

    def watch(page, *, record, stop_requested, **kw):
        # You press the Submit button while the agent watches: a trusted mouse
        # press (isTrusted), which fires the submit-time hook. It's a plain
        # type=button, so nothing submits. Then Enter.
        pressed = {"n": 0}

        def stop():
            pressed["n"] += 1
            if pressed["n"] == 1:
                b = page.locator("#go").bounding_box()
                page.mouse.move(b["x"] + b["width"] / 2, b["y"] + b["height"] / 2)
                page.mouse.down()
                page.mouse.up()
                return False
            return True
        return real_watch(page, record=record, stop_requested=stop, poll_ms=200, **kw)
    patches = (tuple(spying(c) for c in (Page, Frame, ElementHandle, Locator))
               + (hook, (outcome, "watch", watch)))
    # A password box reads as a login wall: Enter at each of its three pauses
    # (before Apply, after Apply, at step 1), then "n" at "Did you submit it?".
    text, records, error = run_fill(form, dry_run=False, answers=("", "", "", "n"),
                                    patches=patches)
    print(text[-900:])
    check(error is None, f"the run finished ({error})")
    check(any(isinstance(x, (list, dict)) and x for x in seen),
          "the spies saw the page's reads (so the next checks mean something)")
    check(any(isinstance(p, dict) and p for p in payloads),
          "the submit hook fired and its payload was captured (so the next check means something)")
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
