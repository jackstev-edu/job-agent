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
         ("label", "Contrasen\u0303a"), ("label", "Lo\u0308senord"), ("label", "ｐａｓｓｗｏｒｄ"),
         ("label", "Passphrase"), ("label", "PIN"), ("name", "user_pin"), ("label", "Secret answer"),
         ("label", "Security answer"), ("label", "Token"), ("name", "api_token")]

# Ordinary questions with those words inside other words, or a clearance level.
NOT_SECRET = ["Shipping address", "Your opinion", "Executive secretary to the CEO",
              "Tokens of appreciation", "Spinning class", "Do you hold an active Secret clearance?"]


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


def _direct(page, fields, refs) -> dict:
    """
    _SNAPSHOT_JS asked directly for these refs, bypassing readable_refs, with
    the tag, type and name the scan saw for each. Every ref must have them,
    or the page's refusal would prove nothing.
    """
    sigs = {r: s for f in fields for r, s in (f.get("sigs") or {}).items()}
    assert all(r in sigs for r in refs), f"no scan signature for some of {refs}"
    return page.evaluate(extract._SNAPSHOT_JS, [[r, *sigs[r]] for r in refs])


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
        direct = _direct(page, toggled, [box(toggled)["ref"]])
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
        direct = _direct(page, fields, secret_refs)
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
              and city["ref"] not in _direct(page, second, [city["ref"]]),
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
                     e.name = 'city'; e.setAttribute('data-ja-ref', r);
                     document.forms[0].appendChild(e); }""",
             old_city["ref"])
        check(_direct(page, first, [old_city["ref"]]) == {},
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
              and f["ref"] not in _direct(page, [f], [f["ref"]]),
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
              and not set(_direct(page, again, refs)) & set(refs),
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


def test_a_credential_step_reads_nothing():
    check = Checks()
    with mock_page(_write("pw_cred.html", CRED)) as page:
        seen = _spy(page)
        for guard, how in ((extract.ReadGuard(), "with the run's guard"), (None, "a lone scan")):
            fields = extract.extract_fields(page, guard)
            by = {f["label"]: f for f in fields}
            check(all(f.get("credential_step") for f in fields),
                  f"{how}: a page with a password box is a credential step")
            nick, email = by["Nickname"], by["Email"]
            check(nick.get("stable_identity") and not nick.get("secret"),
                  f"{how}: Nickname is named and labelled, and not secret by any pattern")
            page.fill("#em", "t@x.invalid")                   # as the agent would
            refs = [f["ref"] for f in fields if f.get("sigs")]
            check(extract.readable_refs(fields) == [] and extract.snapshot(page, fields) == {},
                  f"{how}: nothing is read, not Nickname, not even the email the agent filled")
            check(_direct(page, fields, refs) == {},
                  f"{how}: asked directly for every ref, the page returns nothing while a "
                  f"password box is there")
    check(SENTINEL not in json.dumps(seen, default=str),
          "the sentinel is nowhere in anything page.evaluate returned")
    check.done()


LATE = """<!doctype html><html><body><form>
  <label for="ci">City</label><input id="ci" name="city" value="Springfield">
</form>
<script>
  window.addPassword = () => document.forms[0].insertAdjacentHTML('beforeend',
    '<input type="password">');
  window.addHint = () => document.forms[0].insertAdjacentHTML('beforeend',
    '<input type="text" autocomplete="current-password">');
  window.removeAdded = () => [...document.forms[0].querySelectorAll('input:not(#ci)')]
    .forEach(e => e.remove());
</script></body></html>"""


def test_a_password_box_appearing_after_the_scan_stops_every_read():
    check = Checks()
    with mock_page(_write("pw_late.html", LATE)) as page:
        real = page.evaluate
        fields = extract.extract_fields(page, extract.ReadGuard())
        city = fields[0]
        check(extract.snapshot(page, fields) == {city["ref"]: "Springfield"},
              "no password box: the named, labelled City box reads")
        for add, what in (("addPassword", "a password box"), ("addHint", "a password-hinted box")):
            real(f"window.{add}()")
            check(city["ref"] in extract.readable_refs(fields)
                  and extract.snapshot(page, fields) == {},
                  f"{what} appears after the scan: Python still allows City, the page reads nothing")
            real("window.removeAdded()")
        check(extract._SNAPSHOT_JS in outcome._HOOK_JS,
              "the submit hook runs the same read, so it refuses the same way")
    check.done()


SIG = """<!doctype html><html><body><form>
  <label for="ci">City</label><input id="ci" name="city" value="Springfield">
</form></body></html>"""


def test_an_element_no_longer_matching_its_scan_is_not_read():
    check = Checks()
    changes = [
        ("document.getElementById('ci').setAttribute('name', 'town')", "its name changed"),
        ("document.getElementById('ci').type = 'email'", "its type changed"),
        ("""(() => { const old = document.getElementById('ci'), t = document.createElement('textarea');
             t.name = 'city'; t.id = 'ci'; t.value = 'Springfield';
             t.setAttribute('data-ja-ref', old.getAttribute('data-ja-ref')); old.replaceWith(t); })()""",
         "it was swapped for a textarea carrying the same ref and name"),
    ]
    for js, what in changes:
        with mock_page(_write("pw_sig.html", SIG)) as page:
            fields = extract.extract_fields(page, extract.ReadGuard())
            ref = fields[0]["ref"]
            check(extract.snapshot(page, fields) == {ref: "Springfield"}, f"before: City reads ({what})")
            page.evaluate(f"() => {{ {js}; }}")
            check(ref not in extract.snapshot(page, fields) and _direct(page, fields, [ref]) == {},
                  f"{what}: the element isn't what the scan saw, so it isn't read")
    check.done()


RADIO_CRED = """<!doctype html><html><body><form>
  <fieldset><legend>Are you willing to relocate?</legend>
    <label><input type="radio" name="relocate" value="y"> Yes</label>
    <label><input type="radio" name="relocate" value="n"> No</label></fieldset>
  <label for="pw">Password</label><input id="pw" name="password" type="password">
</form></body></html>"""


def test_a_radio_on_a_credential_step_is_unreadable_and_proves_nothing():
    from agent import score
    check = Checks()
    with mock_page(_write("pw_radio.html", RADIO_CRED)) as page:
        fields = extract.extract_fields(page, extract.ReadGuard())
        radio = next(f for f in fields if f["type"] == "radio")
        final = extract.snapshot(page, fields)
    check(radio["ref"] not in final, "the radio group isn't in the read at all, not even as 'none chosen'")
    rec = {"fields": fields, "plan": {}}
    edits = outcome.compute_edits(rec, {"consent_actions": {}}, final)
    kinds = {e["label"]: e["kind"] for e in edits}
    check(kinds.get("Are you willing to relocate?") == "unreadable",
          f"compute_edits lists it as unreadable ({kinds})")
    row = {"verdict": "would_submit", "human_submitted": "y", "capture": "exact",
           "outcome_at": "2026-10-15", "edits_json": json.dumps(edits)}
    check(score.score_run(row) == "unknown",
          "so score.py can't claim zero edits: a would_submit with that read is unknown, not a match")
    unit = {"ref": "r0", "type": "radio", "option_refs": ["r0", "r1"], "options": ["Yes", "No"]}
    check(extract.snapshot_from_raw([unit], {}) == {}
          and extract.snapshot_from_raw([unit], {"r0": False, "r1": False}) == {"r0": None}
          and extract.snapshot_from_raw([unit], {"r1": True}) == {},
          "a group is recorded only when every one of its options was read")
    check.done()


ID_ONLY = f"""<!doctype html><html><body><form>
  <input id="x7" value="{SENTINEL}">
  <input placeholder="Your city" value="{SENTINEL}">
  <label for="ok">City</label><input id="ok" name="city" value="Springfield">
</form></body></html>"""


def test_an_id_only_or_placeholder_only_box_is_never_read_without_a_password_on_the_page():
    check = Checks()
    with mock_page(_write("pw_idonly.html", ID_ONLY)) as page:
        seen = _spy(page)
        fields = extract.extract_fields(page, extract.ReadGuard())
        check(not any(f.get("credential_step") for f in fields),
              "no password box on the page: not a credential step (so rule 3 is what's tested)")
        idonly = next(f for f in fields if f.get("dom_id") == "x7")
        ph = next(f for f in fields if f.get("placeholder") == "Your city")
        city = next(f for f in fields if f.get("dom_name") == "city")
        for f, what in ((idonly, "an id-only box"), (ph, "a placeholder-only box")):
            check(not f.get("secret") and f.get("stable_identity") is False,
                  f"{what}: not secret, but no stable identity")
            check(f["ref"] not in extract.readable_refs(fields)
                  and _direct(page, fields, [f["ref"]]) == {},
                  f"{what}: left out of readable_refs, and refused when asked directly")
        check(extract.snapshot(page, fields) == {city["ref"]: "Springfield"},
              "the named, labelled box beside them reads")
    check(SENTINEL not in json.dumps(seen, default=str),
          "neither box's value is anywhere in what page.evaluate returned")
    check.done()


def test_ordinary_words_that_contain_secret_ones_stay_readable():
    check = Checks()
    html = ("<!doctype html><html><body><form>"
            + "".join(f'<label for="n{i}">{w}</label><input id="n{i}" name="n{i}" value="v{i}">'
                      for i, w in enumerate(NOT_SECRET))
            + "</form></body></html>")
    with mock_page(_write("pw_notsecret.html", html)) as page:
        fields = extract.extract_fields(page, extract.ReadGuard())
        snap = extract.snapshot(page, fields)
        for i, w in enumerate(NOT_SECRET):
            f = next(f for f in fields if f.get("dom_name") == f"n{i}")
            check(not f.get("secret") and snap.get(f["ref"]) == f"v{i}",
                  f"{ascii(w)}: not secret, and read")
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


PAGE_TEXT = f"""<!doctype html><html><body>
  <h1>Data Engineer</h1><p>We build pipelines for a living.</p>
  <form><label for="a">About you</label><textarea id="a"></textarea>
    <label for="c">City</label><input id="c" name="city"></form>
  <div contenteditable="true" id="ce"></div>
  <div class="select__control"><div class="select__single-value">{SENTINEL}-chosen</div></div>
  <div role="combobox" id="cb">{SENTINEL}-combo</div>
  <p style="display:none">hidden text</p>
</body></html>"""


def test_page_text_for_the_model_holds_no_field_value():
    check = Checks()
    with mock_page(_write("pw_page_text.html", PAGE_TEXT)) as page:
        page.fill("#a", f"{SENTINEL}-typed")
        page.fill("#c", f"{SENTINEL}-city")
        page.evaluate(f"() => {{ document.getElementById('ce').textContent = '{SENTINEL}-edit'; }}")
        text = extract.page_text(page, 6000)
    print(f"  page_text: {text!r}")
    check("Data Engineer" in text and "We build pipelines for a living." in text,
          "the page's own text is there")
    check(SENTINEL not in text, "no typed value, editable text, chosen option or widget text is")
    check("hidden text" not in text and "City" not in text,
          "nor hidden text, nor anything inside the form")
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
    check(payloads and all(p == {} for p in payloads),
          "the submit hook fired, and handed back nothing on this credential step "
          "(so the next check means something)")
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
