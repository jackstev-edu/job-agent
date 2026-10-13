"""
test_gate_profile.py — the submit gate's profile keys start blank and read safely.

WHAT THIS DOES: Parses the blank template `init` writes, checks the new gate
keys are there and empty, and checks the Profile accessors turn missing or
malformed values into the safe default.
WHY IT MATTERS: The gate trusts these accessors. A typo in profile.yaml must
make it stop, never let something through.

Run from anywhere:  .venv/Scripts/python.exe tests/test_gate_profile.py
"""
from pathlib import Path

import yaml

from _harness import Checks, run_as_script
from agent import template
from agent.profile import Profile


def test_template_has_blank_gate_keys():
    check = Checks()
    data = yaml.safe_load(template.BLANK_PROFILE)
    check(data.get("autosubmit_portals") == [], "autosubmit_portals starts empty")
    check(data.get("daily_submit_cap") is None, "daily_submit_cap starts blank")
    check(data.get("consent_allowlist") == [], "consent_allowlist starts empty")
    pc = data.get("per_company") or {}
    check(set(pc.get("defaults") or {}) == {"referral_source", "previously_employed",
                                            "previously_applied", "relatives_at_company"}
          and all(v is None for v in pc["defaults"].values()),
          "per_company.defaults has the four keys, all blank")
    check(pc.get("overrides") == {}, "per_company.overrides starts empty")

    prof = Profile(data, Path("blank.yaml"))
    check(prof.autosubmit_portals() == [] and prof.daily_submit_cap() is None
          and prof.consent_allowlist() == [] and prof.canned_answers() == {},
          "a blank profile reads as: no portals, no cap, no consents, no canned answers")
    check.done()


def test_accessors_fall_back_safely():
    check = Checks()
    good = Profile({"autosubmit_portals": [" Greenhouse ", "ashby", 3, ""],
                    "daily_submit_cap": 5,
                    "consent_allowlist": ["I agree to receive SMS updates", "", None],
                    "voice": {"canned_answers": {"why_this_role": "Because.",
                                                 "why_this_company": "TODO",
                                                 "greatest_strength": None}}},
                   Path("p.yaml"))
    check(good.autosubmit_portals() == ["greenhouse", "ashby"], "portals lowercased, junk dropped")
    check(good.daily_submit_cap() == 5, "a whole-number cap is read")
    check(good.consent_allowlist() == ["I agree to receive SMS updates"],
          "allowlist keeps real strings only, unaltered")
    check(good.canned_answers() == {"why_this_role": "Because."},
          "canned answers skip blanks and TODO")

    for bad_cap in (True, -1, "5", 2.5):
        check(Profile({"daily_submit_cap": bad_cap}, Path("p.yaml")).daily_submit_cap() is None,
              f"cap {bad_cap!r} -> None")
    bad = Profile({"autosubmit_portals": "greenhouse", "consent_allowlist": "I agree",
                   "voice": {"canned_answers": ["x"]}}, Path("p.yaml"))
    check(bad.autosubmit_portals() == [] and bad.consent_allowlist() == []
          and bad.canned_answers() == {}, "a string where a list belongs -> empty")
    check.done()


def test_check_does_not_nag_about_gate_keys():
    # Blank is the safe default, so `check` must not push you to fill these.
    check = Checks()
    from agent import profile as profile_mod
    paths = profile_mod.REQUIRED_PATHS + profile_mod.RECOMMENDED_PATHS
    check(not any(p.split(".")[0] in ("autosubmit_portals", "daily_submit_cap",
                                       "consent_allowlist", "per_company") for p in paths),
          "no gate key is required or recommended")
    check.done()


if __name__ == "__main__":
    run_as_script(globals())
