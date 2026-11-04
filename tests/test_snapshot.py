"""
test_snapshot.py — a page snapshot holds structure and never a profile or input value.

WHAT THIS DOES: Serves a mock page shaped like a Workday "My Information"
step (data-automation-id controls, a header with the signed-in name, boxes
pre-filled with values, a select with an option equal to a profile value,
a label naming the profile email, a honeypot) and dumps it through
agent.snapshot with a fake profile: the file carries the automation ids,
labels, aria attributes, required flags and container chains, and not one
profile value (name, email, phone in any digit form, city, employer), not
one input value, and nothing from the header / account area; the stripper
is also checked on its own.
WHY IT MATTERS: Jack will run `apply.py snapshot` on real Workday and
Greenhouse pages so mocks match real structure; the dump sits in the repo
folder (gitignored) and must never hold anything personal.

Run from anywhere:  .venv/Scripts/python.exe tests/test_snapshot.py
"""
import json

from _harness import TEST_HOME, Checks, mock_page, run_as_script
from agent import snapshot

PROFILE = {
    "personal": {"first_name": "Testfirst", "last_name": "Testlast", "email": "test.person@example.invalid",
                 "phone": "+1 (555) 824-9000", "city": "Springfield", "postal_code": "43210-1234",
                 "address_line_1": "12 Example Lane"},
    "experience": [{"company": "Example Widgets Inc", "title": "Intern"}],
    "eligibility": {"authorized_to_work_us": True, "security_clearance": "None"},
    "work_history": {"past_employers": ["Example Widgets Inc", "Acme"]},
}
VALUE_IN_BOX = "BOXVALUE-SENTINEL-77"

PAGE = TEST_HOME / "snapshot_page.html"
PAGE.write_text("""<!doctype html><html><head><meta charset="utf-8"><title>My Information - Testfirst Testlast</title></head><body>
<header data-automation-id="navigationHeader"><span data-automation-id="userName">Testfirst Testlast</span>
<a href="/account">Account Settings</a></header>
<main>
<h2 data-automation-id="pageHeader">My Information</h2>
<div data-automation-id="legalNameSection">
  <div data-automation-id="formField-legalNameSection_firstName" class="css-1abc css-2def extra1 extra2 extra3">
    <label for="fn">First Name<span aria-hidden="true">*</span></label>
    <input id="fn" data-automation-id="legalNameSection_firstName" aria-required="true" value="Testfirst">
  </div>
  <div data-automation-id="formField-legalNameSection_lastName">
    <label for="ln">Last Name</label><input id="ln" data-automation-id="legalNameSection_lastName" required value="BOXVALUE-SENTINEL-77">
  </div>
</div>
<div data-automation-id="phoneSection">
  <label for="pt">Phone Device Type</label>
  <button id="pt" data-automation-id="phone-device-type" aria-haspopup="listbox" aria-label="Phone Device Type Mobile">Mobile</button>
  <label for="cc">Country Phone Code</label>
  <div data-automation-id="countryPhoneCode"><input id="cc" data-automation-id="searchBox" placeholder="Search" aria-valuetext="+1"></div>
  <label for="ph">Phone Number</label><input id="ph" data-automation-id="phone-number" value="(555) 824-9000">
</div>
<div data-automation-id="formField-gender"><label for="gd">Gender</label>
  <button id="gd" data-automation-id="gender" aria-haspopup="listbox" aria-label="Gender CHOSEN-GENDER-SENTINEL">CHOSEN-GENDER-SENTINEL</button></div>
<label for="cl">Cover letter <div id="cl" contenteditable="true">TYPED-EDITOR-SENTINEL</div></label>
<div data-automation-id="formField-country"><span id="country-label">Country</span>
  <button id="country" data-automation-id="countryDropdown" aria-haspopup="listbox" aria-labelledby="country-label country">CHOSEN-COUNTRY-SENTINEL</button></div>
<div role="radiogroup" aria-labelledby="vet-label"><span id="vet-label">Veteran status</span>
  <div role="radio" aria-checked="true" tabindex="0">CHOSEN-VETERAN-SENTINEL</div><div role="radio" aria-checked="false" tabindex="-1">Not a veteran</div></div>
<a href="#" role="combobox" aria-haspopup="listbox">CHOSEN-LINK-SENTINEL</a>
<div role="slider" aria-valuenow="42" aria-valuemin="0" aria-valuemax="100" aria-label="Years"></div>
<label for="st">State</label><select id="st" name="state"><option>Select One</option><option>Springfield</option><option>Ohio</option></select>
<label for="em">Email Address for test.person@example.invalid</label><input id="em" type="email" value="test.person@example.invalid">
<label for="hp" style="display:none">Enter website. This input is for robots only, do not enter if you're human.</label>
<input id="hp" name="website" style="display:none" value="">
<fieldset><legend>Have you worked for Example Widgets Inc before?</legend>
<label><input type="radio" name="worked" value="y"> Yes</label><label><input type="radio" name="worked" value="n"> No</label></fieldset>
<div data-automation-id="formField-ssn"><label for="ssn">Password</label><input id="ssn" type="password" value="hunter2-sentinel"></div>
<button data-automation-id="bottom-navigation-next-button" type="button">Save and Continue</button>
</main></body></html>""", encoding="utf-8")


def test_the_stripper_removes_every_profile_value_and_digit_form():
    check = Checks()
    texts, digits = snapshot.profile_values(PROFILE)
    check("testfirst" in texts and "springfield" in texts and "example widgets inc" in texts
          and "none" in texts and "+1 (555) 824-9000" in texts, f"every leaf becomes a normalised text ({sorted(texts)[:6]})")
    check("15558249000" in digits and "432101234" in digits and "true" not in texts,
          f"numbers are kept by their digits; booleans are not values ({sorted(digits)})")
    got = snapshot.strip_values({"a": "Hello Testfirst", "b": ["555-824-9000", "1 555 824 9000 ext 2", "plain"],
                                 "c": {"label": "Lives in SPRINGFIELD, OH", "n": 3, "ok": True}}, texts, digits)
    check(got == {"a": snapshot.REDACTED, "b": [snapshot.REDACTED, snapshot.REDACTED, "plain"],
                  "c": {"label": snapshot.REDACTED, "n": 3, "ok": True}}, f"strings holding a value go whole ({got})")
    check.done()


def test_a_dump_holds_structure_and_no_value_from_the_profile_the_boxes_or_the_header():
    check = Checks()
    with mock_page(PAGE) as page:
        d = snapshot.dump(page, PROFILE)
        path = snapshot.write(d, "test-page", folder=TEST_HOME / "snapshots")
        text = path.read_text(encoding="utf-8")
        check(path.parent == TEST_HOME / "snapshots" and path.name.startswith("test-page-"), f"written under snapshots/ ({path})")
        banned = ["Testfirst", "Testlast", "test.person", "example.invalid", "5558249000", "555) 824", "824-9000",
                  "Springfield", "43210", "Example Lane", "Example Widgets", VALUE_IN_BOX, "hunter2", "Account Settings",
                  "userName", "CHOSEN-GENDER-SENTINEL", "TYPED-EDITOR-SENTINEL", "Mobile", "CHOSEN-COUNTRY-SENTINEL",
                  "CHOSEN-VETERAN-SENTINEL", "CHOSEN-LINK-SENTINEL", "aria-checked", "aria-valuenow", '"42"']
        for b in banned:
            check(b.lower() not in text.lower(), f"{b!r} is nowhere in the dump")
        ids = [e.get("automation_id") for e in d["elements"]]
        check("legalNameSection_firstName" in ids and "searchBox" in ids and "phone-device-type" in ids
              and "bottom-navigation-next-button" in ids, f"the automation ids are there ({[i for i in ids if i][:8]})")
        first = next(e for e in d["elements"] if e.get("automation_id") == "legalNameSection_firstName")
        check(first["label"] == "First Name*" or first["label"].startswith("First Name"), f"its label ({first['label']!r})")
        check(first["required"] is True and first["visible"] is True and first["aria"].get("aria-required") == "true",
              f"required, visible, aria kept ({first['required']}, {first['visible']}, {first['aria']})")
        check(any("legalNameSection" in c for c in first["chain"]) and any(c.startswith("main") for c in first["chain"]),
              f"the container chain, with automation ids ({first['chain'][:4]})")
        check(not any("value" in e for e in d["elements"]), "no element has a value key")
        dev = next(e for e in d["elements"] if e.get("automation_id") == "phone-device-type")
        check(dev["aria"].get("aria-haspopup") == "listbox" and dev["text"] == "" and "aria-label" not in dev["aria"]
              and dev["chooser"] is True and dev["label"] == "Phone Device Type",
              f"a button-dropdown keeps its shape and label, not its choice or aria-label ({dev['aria']}, {dev['text']!r}, {dev['label']!r})")
        gd = next(e for e in d["elements"] if e.get("automation_id") == "gender")
        check(gd["text"] == "" and "aria-label" not in gd["aria"] and gd["label"] == "Gender",
              f"a chosen EEO value on a dropdown button is nowhere ({gd})")
        cl = next(e for e in d["elements"] if e.get("id") == "cl")
        check(cl["label"] == "Cover letter" and cl["text"] == "", f"a label wrapping an editor keeps its own words only ({cl['label']!r}, {cl['text']!r})")
        country = next(e for e in d["elements"] if e.get("automation_id") == "countryDropdown")
        check(country["label"] == "Country" and country["text"] == "", f"a dropdown named by its own id keeps the label only ({country['label']!r})")
        radios = [e for e in d["elements"] if e.get("role") == "radio"]
        check(len(radios) == 2 and all("aria-checked" not in r["aria"] for r in radios) and all(r["text"] == "" for r in radios),
              f"custom radios carry no checked state and no text ({[(r['aria'], r['text']) for r in radios]})")
        slider = next(e for e in d["elements"] if e.get("role") == "slider")
        check("aria-valuenow" not in slider["aria"] and slider["aria"].get("aria-valuemax") == "100", f"a slider's value is dropped, its range kept ({slider['aria']})")
        nxt = next(e for e in d["elements"] if e.get("automation_id") == "bottom-navigation-next-button")
        check(nxt["text"] == "Save and Continue" and nxt["chooser"] is False, f"a plain button keeps its wording ({nxt['text']!r})")
        check(d["frames"] == 0 and d["shadow_hosts"] == 0 and "taken" in d, "frame and shadow-host counts, and the time, are recorded")
        cc = next(e for e in d["elements"] if e.get("automation_id") == "searchBox")
        check("aria-valuetext" not in cc["aria"], "aria-valuetext is never dumped")
        state = next(e for e in d["elements"] if e.get("name") == "state")
        check(state["options"][0] == "Select One" and state["options"][1] == snapshot.REDACTED and state["options"][2] == "Ohio",
              f"an option equal to a profile value is redacted, the others kept ({state['options']})")
        em = next(e for e in d["elements"] if e.get("id") == "em")
        check(em["label"] == snapshot.REDACTED, f"a label holding the profile email is redacted whole ({em['label']!r})")
        hp = next(e for e in d["elements"] if e.get("name") == "website")
        check(hp["visible"] is False and hp["label"].startswith("Enter website"), f"the honeypot is there, hidden ({hp['visible']}, {hp['label'][:20]!r})")
        pw = next(e for e in d["elements"] if e.get("id") == "ssn")
        check(pw["type"] == "password" and "hunter2" not in json.dumps(pw), "a password box is structure only")
        legend = next((e for e in d["elements"] if e["tag"] == "legend"), None)
        check(legend is not None and legend["text"] == snapshot.REDACTED, f"a legend naming a past employer is redacted ({legend})")
        check(not any(e.get("automation_id") == "userName" for e in d["elements"]) and "navigationHeader" not in text,
              "nothing from the header / account area")
        check(d["url"].startswith("file:///") and "?" not in d["url"] and d["title"] == snapshot.REDACTED,
              f"the url has no query; the title held the name and is redacted ({d['url'][:20]}, {d['title']!r})")
    check.done()


ECHO_PAGE = TEST_HOME / "snapshot_echo.html"
ECHO_PAGE.write_text("""<!doctype html><html><head><meta charset="utf-8"><title>Review</title></head><body><main>
<h2>You told us: TYPED-ECHO-SENTINEL</h2>
<h3>Your answer: LEARNED-ECHO-SENTINEL</h3>
<h4>Tab order</h4>
<label for="st">State</label><button id="st" aria-haspopup="listbox" data-automation-id="stateDropdown" aria-label="State ZQ">ZQ</button>
<ul role="listbox"><li role="option">Alabama</li><li role="option">ZQ</li></ul>
<label for="eth">Ethnicity</label><select id="eth" name="ethnicity"><option>Select</option><option>LEARNED-ECHO-SENTINEL</option><option>Other</option></select>
<fieldset id="vet"><legend>Veteran</legend><label><span role="presentation"></span><input type="radio" name="vet" value="a"> LEARNED-ECHO-SENTINEL</label><label><svg role="img"></svg><input type="radio" name="vet" value="b"> Not a veteran</label>
<div role="button" aria-label="Remove LEARNED-ECHO-SENTINEL" id="chip"></div></fieldset>
<fieldset id="dis"><legend>Disability</legend><input type="checkbox" id="d1" name="d_yes"><label for="d1">LEARNED-ECHO-SENTINEL</label>
<input type="checkbox" id="d2" name="d_no"><label for="d2">No, I do not have a disability</label></fieldset>
<fieldset id="btns" role="group"><legend>Gender</legend><button type="button">LEARNED-ECHO-SENTINEL</button><button type="button">Other</button></fieldset>
<div role="radiogroup" id="custom"><div role="radio" aria-label="LEARNED-ECHO-SENTINEL" tabindex="0"></div><div role="radio" aria-label="Prefer not to say" tabindex="-1"></div></div>
<div role="radiogroup" id="radix"><button type="button" role="radio" id="r1" aria-checked="false"></button><label for="r1">LEARNED-ECHO-SENTINEL</label>
<button type="button" role="radio" id="r2" aria-checked="false"></button><label for="r2">Decline</label>
<label id="r3lbl">LEARNED-ECHO-SENTINEL</label><div role="Radio menuitemradio" aria-labelledby="r3lbl" tabindex="-1"></div></div>
<div class="ashby-application-form-input-yesno" id="ashby"><button type="button">LEARNED-ECHO-SENTINEL</button><a href="#">LEARNED-ECHO-SENTINEL</a><div role="button" tabindex="0">Other</div></div>
<label for="nm">Nickname</label><input id="nm" value="TYPED-ECHO-SENTINEL">
<h3>Answer Yes or N/A if none applies</h3>
<label for="yn">Reply Yes or No</label><input id="yn">
</main></body></html>""", encoding="utf-8")


def test_a_learned_or_typed_yes_no_or_n_a_never_strips_anything():
    # Jack, 2026-11-03 evening, item f: a learned or typed "Yes" / "No" /
    # "N/A" / "None" (any case) is never stripped as an extra value, so the
    # headings and labels built from the form's own yes / no vocabulary
    # stay. Fails on 0ec23c5: "Yes" (three characters) redacted the heading and
    # the label. A profile value is read as
    # before (the mutation guard for "nothing else"), and a dropdown
    # button's shown value is still blanked.
    check = Checks()
    with mock_page(ECHO_PAGE) as page:
        d = snapshot.dump(page, {}, extra_values=["Yes", "N/A", "none", "NO", " yes ", "n/a"])
        h5 = [e for e in d["elements"] if e["tag"] == "h3"][-1]           # the last h3: the yes / n/a heading
        yn = next(e for e in d["elements"] if e.get("id") == "yn")
        text = json.dumps(d)
        check(h5["text"] == "Answer Yes or N/A if none applies", f"a heading holding Yes and N/A stays ({h5['text']!r})")
        check(yn["label"] == "Reply Yes or No", f"a text box's label holding Yes and No stays ({yn['label']!r})")
        check(snapshot.REDACTED not in text, "nothing at all is redacted for those values")
        check(snapshot.NEVER_STRIPPED == {"yes", "no", "n/a", "none"}, "the four values, as Jack named them")
        d2 = snapshot.dump(page, {}, extra_values=["Yes", "Answer"])
        check([e for e in d2["elements"] if e["tag"] == "h3"][-1]["text"] == snapshot.REDACTED,
              "...while another extra value beside them still strips (the floor and the strip are unchanged)")
        d3 = snapshot.dump(page, {"eeo": {"veteran": "Yes"}})
        check([e for e in d3["elements"] if e["tag"] == "h3"][-1]["text"] == snapshot.REDACTED,
              "a profile value 'Yes' is stripped as before: nothing else changed")
        btn = next(e for e in d["elements"] if e.get("automation_id") == "stateDropdown")
        check(btn["text"] == "" and "aria-label" not in btn["aria"], "a dropdown button's shown value is still blanked")
    check.done()


def test_a_dump_also_strips_learned_answers_and_typed_values_and_never_a_dropdowns_shown_value():
    # Jack, 2026-11-03: snapshots strip learned answers and the current
    # fill's typed values (3+ characters) too; listbox option rows stay; a
    # dropdown button's shown value is blanked whatever its length. Fails
    # on d9c5b10 for the extra values (dump took none). The dropdown case
    # is a guard: the page script never dumped a chooser's text or
    # aria-label (mutation check: make plainButton `__ja.tag(el) ===
    # 'BUTTON'` alone, or drop the chooser test on aria-label, and "ZQ"
    # appears). Fold of 99c5a77's review: an option entry (a select's
    # options, an <option>, a radio's label) is stripped against the
    # profile only, so a learned answer never shows by its position.
    check = Checks()
    with mock_page(ECHO_PAGE) as page:
        plain = snapshot.dump(page, {})
        text = json.dumps(plain)
        check("TYPED-ECHO-SENTINEL" in text and "LEARNED-ECHO-SENTINEL" in text,
              "with nothing to strip, the page's own echo of a value stays (the point of the extras)")
        d = snapshot.dump(page, {}, extra_values=["TYPED-ECHO-SENTINEL", "Learned-Echo-Sentinel", None, 7, "ab"])
        text = json.dumps(d)
        h2 = next(e for e in d["elements"] if e["tag"] == "h2")
        h3 = next(e for e in d["elements"] if e["tag"] == "h3")
        check(h2["text"] == snapshot.REDACTED and h3["text"] == snapshot.REDACTED,
              f"a typed value and a learned answer echoed in the page's headings are redacted whole ({h2['text']!r}, {h3['text']!r})")
        sel = next(e for e in d["elements"] if e.get("name") == "ethnicity")
        radios = [e for e in d["elements"] if e.get("tag") == "input" and e.get("type") == "radio"]
        check(sel["options"] == ["Select", "LEARNED-ECHO-SENTINEL", "Other"] and any(r["label"] == "LEARNED-ECHO-SENTINEL" for r in radios),
              f"an option entry is never redacted against a learned answer: that would mark the chosen one by position ({sel['options']}, {[r['label'] for r in radios]})")
        # Fold of f8fa10c's review: every choice entry, its <label> included.
        def strings_in(container, skip_ids=("chip",)):
            return [v for e in d["elements"] if (any(c.startswith(container) for c in e["chain"]) or e.get("id") == container.split("#")[-1])
                    and e.get("id") not in skip_ids
                    for v in [e.get("text"), e.get("label"), e.get("id"), e.get("name"), *e["aria"].values(), *e["classes"], *e["chain"]]
                    if isinstance(v, str)]
        GROUPS = (("fieldset#vet", "the radio group and its labels"), ("fieldset#dis", "the checkbox group and its labels"),
                  ("fieldset#btns", "the button group"), ("div#custom", "the custom radio group's aria-labels"),
                  ("div#radix", "a Radix-shaped group (role=radio buttons under <label for>, an aria-labelledby span)"),
                  ("div#ashby", "Ashby's button-group container with role=button elements"))
        for container, what in GROUPS:
            strs = strings_in(container)
            check(strs and "LEARNED-ECHO-SENTINEL" in strs and not any(v == snapshot.REDACTED for v in strs),
                  f"no string of {what} is redacted against the learned answer, and the answer is there ({strs[:6]})")
        check(any(e.get("choice") for e in d["elements"] if e.get("tag") == "label"), "a radio's <label> entry carries the choice mark")
        chip = next(e for e in d["elements"] if e.get("id") == "chip")
        check(chip["aria"].get("aria-label") == snapshot.REDACTED and not chip.get("choice"),
              f"a react-select remove chip (role=button in a fieldset) is no choice: its aria-label naming the learned answer is redacted ({chip['aria']})")
        r3 = next(e for e in d["elements"] if e.get("id") == "r3lbl")
        check(r3.get("choice") is True and r3["text"] == "LEARNED-ECHO-SENTINEL", f"a label a choice names through aria-labelledby is a choice ({r3})")
        ashby_a = next(e for e in d["elements"] if e.get("tag") == "a" and "ashby" in " ".join(e["chain"]))
        ashby_b = next(e for e in d["elements"] if e.get("tag") == "button" and "ashby" in " ".join(e["chain"]))
        check(ashby_a.get("choice") is True and ashby_b.get("choice") is True and ashby_a["text"] == ashby_b["text"] == "LEARNED-ECHO-SENTINEL",
              "a real <button> and a plain link inside extract's button-group container are both choices")
        # A PROFILE value in a choice entry is still redacted (the mutation guard for the profile-only sets).
        dp = snapshot.dump(page, {"eeo": {"race": "LEARNED-ECHO-SENTINEL"}})
        d_saved, d = d, dp
        for container, what in GROUPS:
            strs = strings_in(container)
            check(snapshot.REDACTED in strs and "LEARNED-ECHO-SENTINEL" not in strs, f"a profile value in {what} is still redacted ({strs[:6]})")
        d = d_saved
        prof_sel = snapshot.dump(page, {"eeo": {"race": "LEARNED-ECHO-SENTINEL"}})
        check(next(e for e in prof_sel["elements"] if e.get("name") == "ethnicity")["options"][1] == snapshot.REDACTED,
              "...while a profile value in an option is still redacted, as before")
        check(not any(e.get("tag") == "input" and "value" in e for e in d["elements"]), "no input value is dumped")
        btn = next(e for e in d["elements"] if e.get("automation_id") == "stateDropdown")
        check(btn["chooser"] is True and btn["text"] == "" and "aria-label" not in btn["aria"] and '"ZQ"' not in text,
              f"a dropdown button's two-letter shown value is nowhere ({btn})")
        rows = [e for e in d["elements"] if e.get("role") == "option"]
        check(len(rows) == 2 and all(r["text"] == "" for r in rows), f"the listbox option rows stay, as structure ({rows})")
        h4 = next(e for e in d["elements"] if e["tag"] == "h4")
        check(h4["text"] == "Tab order", "a two-character extra ('ab') strips nothing: the floor is three")
        h4b = next(e for e in snapshot.dump(page, {}, extra_values=["tab"])["elements"] if e["tag"] == "h4")
        check(h4b["text"] == snapshot.REDACTED, "...and a three-character one does")
    check.done()


if __name__ == "__main__":
    run_as_script(globals())
