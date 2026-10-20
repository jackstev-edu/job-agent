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
                  "userName", "CHOSEN-GENDER-SENTINEL", "TYPED-EDITOR-SENTINEL", "Mobile"]
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


if __name__ == "__main__":
    run_as_script(globals())
