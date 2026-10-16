"""
test_after_upload.py — help text read after the resume upload never reaches a model.

WHAT THIS DOES: Runs the real `fill` with a resume upload that rewrites
some help text (a sponsorship question appearing under "Location", a
phone validation message) and leaves one field's help alone. The changed
fields come to you and never reach the mapper; the unchanged one keeps its
pre-upload classification. Then: a consent keyword styled as a tag inside a
label still stops (the page's raw-label check); dropdowns holding a
password-hinted box or editable text aren't opened, while one holding only
a hidden input is; and the page's copy of the stop and guard patterns
matches the Python one on every sample.
WHY IT MATTERS: No page value may reach the model, and a field must clear
both the classifier and the local backstop before the model answers it.

Run from anywhere:  .venv/Scripts/python.exe tests/test_after_upload.py
"""
import json
import re
from pathlib import Path

from _harness import TEST_HOME, Checks, mock_page, run_as_script
from agent import extract, sensitive

ZWSP, LRM, SOFT, TAG_A = chr(0x200B), chr(0x200E), chr(0x00AD), chr(0xE0041)


def _write(name: str, html: str) -> Path:
    path = TEST_HOME / name
    path.write_text(html, encoding="utf-8")
    return path


UPLOAD_FORM = """<!doctype html><html><body><form>
  <label for="cv">Resume</label><input type="file" id="cv" name="resume">
  <label for="loc">Location</label><input id="loc" name="location" aria-describedby="loc-h">
  <div id="loc-h"></div>
  <label for="ph">Phone</label><input id="ph" name="phone" aria-describedby="ph-h">
  <div id="ph-h">Include your country code</div>
  <label for="ci">City</label><input id="ci" name="city" aria-describedby="ci-h">
  <div id="ci-h">Where you live now</div>
</form>
<script>
  document.getElementById('cv').addEventListener('change', () => {
    document.getElementById('loc-h').textContent =
      'Will you require visa sponsorship to work in this location?';
    document.getElementById('ph-h').textContent = "'555' is not a valid phone number";
  });
</script></body></html>"""


def test_changed_help_after_upload_comes_to_you_and_unchanged_is_reused():
    from _fill_run import PROFILE, map_from_profile, run_fill

    check = Checks()
    (TEST_HOME / "reuse-resume.pdf").write_bytes(b"%PDF-1.4 test resume\n")
    profile = PROFILE.replace("resume_default: missing.pdf", "resume_default: reuse-resume.pdf")
    calls, mapped = [], []

    def classify(fields):
        calls.append({f.get("label"): f.get("help_text") for f in fields})
        return {f["ref"]: "personal_fact" for f in fields}

    def map_fields(fields, prof, page_text=""):
        mapped.extend((f.get("label"), f.get("help_text")) for f in fields)
        return map_from_profile(fields, prof, page_text)
    text, records, error = run_fill(_write("reuse.html", UPLOAD_FORM), dry_run=False,
                                    profile_yaml=profile, classify=classify,
                                    map_fields=map_fields)
    print(text[-900:])
    print(f"  classify calls: {calls}\n  mapper: {mapped}")
    check(error is None and "uploaded reuse-resume.pdf" in text, f"the run uploaded ({error})")
    check(len(calls) == 2 and calls[0].get("City") == "Where you live now",
          "classified once before the upload, with that help text")
    check(len(calls) == 2 and set(calls[1]) == {"Location", "Phone"}
          and all(h == "" for h in calls[1].values()),
          "after it, only the changed fields are classified again, with no help text")
    labels = {m[0] for m in mapped}
    check("Location" not in labels and "Phone" not in labels,
          "neither changed field reached the mapper")
    check(("City", "Where you live now") in mapped,
          "the unchanged field kept its classification and its identical help")
    rec = records[-1] if records else {}
    by_label = {f["label"]: f["ref"] for f in rec.get("fields", [])}
    reasons = {fl["ref"]: fl.get("reason", "") for fl in rec.get("flags") or []}
    check(all("help text held back after upload" in reasons.get(by_label.get(k), "")
              for k in ("Location", "Phone")), "both came to you, held back after the upload")
    check("sponsorship" not in json.dumps(calls) + json.dumps(mapped),
          "the post-upload sponsorship text reached no model call")
    check.done()


TAG_LABEL = """<!doctype html><html><body><form>
  <label for="bg">Do you consent to a <span class="tag">background check</span>?</label>
  <input id="bg" name="bg">
</form></body></html>"""


def test_a_stop_word_styled_as_a_tag_still_stops():
    check = Checks()
    with mock_page(_write("tag_label.html", TAG_LABEL)) as page:
        fields = extract.extract_fields(page, extract.ReadGuard())
    f = fields[0]
    check("background check" not in f["label"],
          f"the label sent onward drops the tag's text ({f['label']!r}), so only the page check sees it")
    check(f.get("raw_stop") is True, "the page's raw-label check says stop")
    v = sensitive.decide(f, "personal_fact", None)
    check(v["action"] == "human", f"so it comes to you ({v['action']}, {v.get('reason')})")
    check.done()


COMBOS = """<!doctype html><html><body><form>
  <div role="combobox" aria-label="Region" id="c1" tabindex="0"
       style="display:block;width:160px;height:24px"><input name="pin" value="4321"></div>
  <div role="combobox" aria-label="Team" id="c2" tabindex="0"
       style="display:block;width:160px;height:24px"><span contenteditable="true">abc</span></div>
  <div role="combobox" aria-label="Office" id="c3" tabindex="0"
       style="display:block;width:160px;height:24px"><input type="hidden" name="office" value="nyc"></div>
</form>
<ul class="menu" id="menu" style="display:none"></ul>
<script>
  window.__opened = [];
  for (const id of ['c1', 'c2', 'c3']) document.getElementById(id).addEventListener('click', () => {
    window.__opened.push(id);
    const m = document.getElementById('menu');
    m.innerHTML = '<li class="option">North</li><li class="option">South</li>';
    m.style.display = 'block';
  });
  document.addEventListener('keydown', e => {
    if (e.key === 'Escape') document.getElementById('menu').style.display = 'none';
  });
</script></body></html>"""


def test_only_a_visible_text_box_keeps_a_dropdown_shut():
    check = Checks()
    with mock_page(_write("combos.html", COMBOS)) as page:
        fields = extract.enrich_comboboxes(page, extract.extract_fields(page, extract.ReadGuard()))
        opened = page.evaluate("window.__opened")
    by = {f["label"]: f for f in fields if f["type"] == "combobox"}
    print(f"  opened {opened}; " + json.dumps({k: (v.get("options"), v.get("note")) for k, v in by.items()}))
    check("c1" not in opened and "already holds text" in by["Region"].get("note", ""),
          "a dropdown holding a password-hinted box isn't opened, and says why")
    check("c2" not in opened and "already holds text" in by["Team"].get("note", ""),
          "one holding editable text isn't opened either")
    check("c3" in opened and by["Office"].get("options") == ["North", "South"],
          "one holding only a hidden input is opened and its choices read")
    check.done()


# ASCII words (JavaScript's \b is ASCII-only, Python's is Unicode) with
# invisible characters added: every pattern family, stops and guards.
_WORDS = ["password", "Security PIN", "security question", "secret answer", "verification code",
          "one-time code", "2FA", "authenticator", "Have you been convicted?", "felony",
          "misdemeanor", "criminal record", "arrested", "background check", "drug test",
          "substance abuse", "electronic signature", "I certify", "I attest", "acknowledge",
          "terms and conditions", "privacy policy", "I agree to the privacy notice",
          "previously worked here", "former employee", "referred by", "How did you hear",
          "relative", "non-compete", "work authorization", "authorized to work",
          "legally eligible", "right to work", "sponsorship", "petition", "visa", "citizen",
          "nationality", "green card", "permanent resident", "H-1B", "H1B", "gender", "sex",
          "race", "ethnicity", "Hispanic", "Latina", "veteran", "disabled", "disability",
          "sexual orientation", "transgender", "pronouns", "salary", "pay", "wage",
          "compensation", "remuneration", "expected earnings", "hourly rate", "OTE", "CTC",
          "First name", "PayPal email", "Start date", "Portfolio URL", "Embrace change"]


def _samples() -> list[str]:
    out = []
    for w in _WORDS:
        out.append(w)
        for c in (ZWSP, LRM, SOFT, TAG_A):
            mid = len(w) // 2
            out.append(w[:mid] + c + w[mid:])
            out.append(w.replace(" ", c) if " " in w else c + w)
    return out


def test_the_page_check_matches_the_python_patterns():
    check = Checks()
    samples = _samples()
    patterns = sensitive.RAW_PATTERNS["stops"] + sensitive.RAW_PATTERNS["legal"]
    compiled = [re.compile(p, re.I) for p in patterns]
    py = [[any(rx.search(v) for v in sensitive.text_variants(t)) for rx in compiled]
          for t in samples]
    chars = [chr(c) for c in (0x00AD, 0x034F, 0x200B, 0x200E, 0x202A, 0x2062, 0x2066, 0x180E,
                              0xFE0F, 0xFEFF, 0xE0041, 0x0041, 0x0020, 0x00E9, 0x2014)]
    with mock_page(_write("parity.html", "<!doctype html><html><body></body></html>")) as page:
        js = page.evaluate("""(texts) => {""" + extract._RAW_RULES_JS + """
            return texts.map(t => [...RAW_STOPS, ...RAW_LEGAL].map(
                rx => readings(t).some(r => rx.test(r))));
        }""", samples)
        js_count = page.evaluate("() => {" + extract._RAW_RULES_JS
                                 + " return RAW_STOPS.length + RAW_LEGAL.length; }")
        js_invis = page.evaluate("(cs) => {" + extract._RAW_RULES_JS
                                 + " return cs.map(c => new RegExp(INVIS.source, 'u').test(c)); }",
                                 chars)
    check(js_count == len(patterns), f"every pattern compiled in the page ({js_count}/{len(patterns)})")
    diffs = [(ascii(samples[i]), patterns[j][:30]) for i in range(len(samples))
             for j in range(len(patterns)) if py[i][j] != js[i][j]]
    check(not diffs, f"page and Python agree on {len(samples)} samples x {len(patterns)} "
                     f"patterns ({diffs[:5]})")
    check(sum(map(any, py)) > len(_WORDS),
          "most samples hit something (so the agreement means something)")
    check(js_invis == [sensitive.is_invisible(c) for c in chars],
          f"page and Python agree on which characters are invisible ({js_invis})")
    check.done()


if __name__ == "__main__":
    run_as_script(globals())
