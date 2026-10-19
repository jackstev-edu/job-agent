"""
test_consent_policy.py — which checkbox labels the consent policy may tick.

WHAT THIS DOES: Runs gate.consent_ok over terms / privacy wordings (ticked),
marketing and opt-in wordings (never), certifications, signatures and
declarations (never), bundles that agree to terms and certify in one breath
(never), over-long labels and empty ones (never), and the same words split
by invisible characters (judged the same). And consent_box_ok on the
census shape: fine print, hidden text and a nearby-text label all refuse.
WHY IT MATTERS: This is the one place page text leads to a tick. It can
only tick a terms / privacy box, and a single marketing or legal word
anywhere in the label is a refusal.

Run from anywhere:  .venv/Scripts/python.exe tests/test_consent_policy.py
"""
from _harness import Checks, run_as_script
from agent import gate

OK = [
    "I agree to the Terms of Service and Privacy Policy",
    "I have read and agree to the privacy policy",
    "I accept the terms and conditions",
    "By checking this box I acknowledge the Privacy Notice",
    "I consent to the processing of my data as described in the privacy policy",
    "I agree to the terms of use",
    "I have read and understood the Terms & Conditions",
]
NEVER = [
    ("I agree to receive SMS updates", "SMS opt-in"),
    ("I agree to receive marketing emails under the privacy policy", "marketing bundled with privacy"),
    ("Subscribe to the newsletter", "newsletter"),
    ("Join our talent community and accept the terms", "talent community"),
    ("I agree to the terms and certify that my answers are true", "terms bundled with a certification"),
    ("I certify that the information provided is accurate", "certification"),
    ("Electronic signature: I agree to the terms", "signature"),
    ("I consent to a background check", "background check"),
    ("I agree to resolve disputes through binding arbitration", "arbitration"),
    ("I authorize you to contact my references", "authorization"),
    ("Keep me informed about future opportunities", "future opportunities"),
    ("I agree", "no document named"),
    ("Privacy Policy", "no agreement word"),
    ("", "empty"),
    ("I agree to the terms " + "and the privacy policy " * 12, "over-long"),
    ("Email me job alerts and I agree to the privacy policy", "job alerts"),
    ("I agree to the terms and to a drug screen", "drug screen"),
    # BLOCK fix: a tick that would answer an eligibility, EEO, pay or age question.
    ("I agree to the privacy policy and confirm I am legally eligible to work in the US", "eligibility"),
    ("I accept the terms and do not require visa sponsorship", "sponsorship"),
    ("I agree to the terms and I am not a veteran", "EEO"),
    ("I have read the terms and I am at least 18", "age"),
    ("I agree to the privacy policy and my salary expectation is flexible", "pay"),
    ("I accept the terms; my gender is listed below", "gender"),
]


def test_terms_and_privacy_only():
    check = Checks()
    for label in OK:
        check(gate.consent_ok(label), f"ticks: {label[:60]}")
    for label, why in NEVER:
        check(not gate.consent_ok(label), f"never: {why}")
    # The same words split by invisible characters read the same way.
    check(gate.consent_ok("I agree to the Terms of Ser­vice and Pri​vacy Policy"),
          "invisible characters inside the words don't hide a consent box")
    check(not gate.consent_ok("I agree to the terms and receive S​MS updates"),
          "...nor a marketing word")
    check.done()


def test_the_census_shape():
    check = Checks()
    box = lambda **k: {"label_source": "for", "label_inner": "I agree to the Privacy Policy",
                       "label_content": "I agree to the Privacy Policy", "described": "", **k}
    check(gate.consent_box_ok(box()), "a plain labelled consent box")
    check(not gate.consent_box_ok(box(described="By ticking you also agree to arbitration")),
          "fine print attached refuses")
    check(not gate.consent_box_ok(box(label_content="I agree to the Privacy Policy and arbitration")),
          "hidden text refuses")
    for source in ("aria-label", "aria-labelledby", "self", "none"):
        check(not gate.consent_box_ok(box(label_source=source)),
              f"label source {source} refuses (the policy reads only a visible <label>)")
    check(gate.consent_box_ok(box(label_source="wrap")), "a wrapping <label> is read")
    check(not gate.consent_box_ok(None) and not gate.consent_box_ok({}), "nothing refuses")
    check.done()


if __name__ == "__main__":
    run_as_script(globals())
