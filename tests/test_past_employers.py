"""
test_past_employers.py — decision C (Jack, 2026-11-02): "have you worked for <company>" from his list.

WHAT THIS DOES: Routes past-employer questions through pipeline.route with
a profile holding work_history.past_employers and a job address naming a
tenant: a tenant on no entry gives "No" to the yes / no question and
"N/A" to the required details box, and the gate traces both; an employer
on the list, an empty list, a four-character tenant with no match, a
label naming a different company than the tenant, "or affiliates", a
contractor wording and an address on an ATS domain that names no tenant
each go to Jack; the gate recomputes the plan from the record and the
profile and stops when the list now matches or the value differs; the
template gains the line and the profile reads it; nothing here is ever
learned.
WHY IT MATTERS: These questions were always per-company and Jack's; his
list is the one fact that answers them, and the model never does.

Run from anywhere:  .venv/Scripts/python.exe tests/test_past_employers.py
"""
import yaml

from _fill_run import PROFILE
from _harness import TEST_HOME, Checks, run_as_script
from agent import employers, gate, memory, pipeline, template
from agent.profile import Profile
from test_gate import base, run as gate_run

WORKDAY = "https://northwind.wd5.myworkdayjobs.com/en-US/Careers/job/Boston/Engineer_R1/apply/applyManually"
SHORT = "https://abcd.wd5.myworkdayjobs.com/en-US/Careers/job/Boston/Engineer_R1/apply"
GREENHOUSE = "https://job-boards.greenhouse.io/northwind/jobs/1"
PLAIN = "https://careers.northwind-traders.com/apply/1"
ATS = "https://careers-northwind.taleo.net/careersection/1/jobapply.ftl"

YESNO = {"ref": "q1", "type": "radio", "label": "Have you worked for Northwind before?", "options": ["Yes", "No"],
         "option_refs": ["q1", "q1b"], "required": True}
DETAILS = {"ref": "q2", "type": "textarea", "required": True,
           "label": "If you were formerly employed at Northwind, please provide the information listed: dates, title, location"}
EMPLOYED_BY = {"ref": "q3", "type": "select", "label": "Have you ever been employed by Northwind?", "options": ["Select", "Yes", "No"]}


def _prof(name, employers_list):
    text = PROFILE + "work_history:\n  past_employers:\n" + "".join(f"    - {e}\n" for e in employers_list)
    if not employers_list:
        text = PROFILE + "work_history:\n  past_employers: []\n"
    p = TEST_HOME / f"employers-{name}.yaml"
    p.write_text(text, encoding="utf-8")
    return Profile(yaml.safe_load(text), p)


def _route(fields, prof, url):
    cats = {f["ref"]: "per_company" for f in fields}
    return pipeline.route(fields, cats, prof, set(), url=url)


def test_a_tenant_on_no_entry_gives_no_and_n_a_and_the_gate_traces_them():
    # Fails on the previous commit: no employers module; the questions were Jack's.
    check = Checks()
    prof = _prof("none", ["Beta Corp", "Gamma Widgets"])
    check(prof.past_employers() == ["Beta Corp", "Gamma Widgets"], "the profile reads the list")
    check("work_history:" in template.BLANK_PROFILE and "past_employers: []" in template.BLANK_PROFILE,
          "the template carries the line")
    for url, what in ((WORKDAY, "a Workday tenant"), (GREENHOUSE, "a shared board's slug"), (PLAIN, "a plain host's domain")):
        plan, flags, for_model = _route([YESNO, DETAILS, EMPLOYED_BY], prof, url)
        check(plan.get("q1", {}).get("value") == "No" and plan.get("q1", {}).get("source") == employers.SOURCE
              and plan.get("q2", {}).get("value") == "N/A" and plan.get("q3", {}).get("value") == "No"
              and not flags and not for_model,
              f"{what}: No, N/A and No, never the model ({plan})")
    check(employers.company_from_url(WORKDAY) == "northwind" and employers.company_from_url(GREENHOUSE) == "northwind"
          and employers.company_from_url(PLAIN) == "northwindtraders" and employers.company_from_url(ATS) == ""
          and employers.company_from_url("https://boards.greenhouse.io/embed/job_app?for=northwind") == "",
          "the company token the address names; an ATS domain or an embed address names none")
    r = base()
    r["url"] = r["final_url"] = WORKDAY
    r["census"] = []                      # base()'s SMS box is allowlisted only by test_gate's own profile
    r["fields"] += [YESNO, DETAILS]
    r["categories"].update(q1="per_company", q2="per_company")
    r["plan"]["q1"] = {"value": "No", "source": employers.SOURCE, "fit": "exact", "profile_path": "work_history.past_employers"}
    r["plan"]["q2"] = {"value": "N/A", "source": employers.SOURCE, "fit": "exact", "profile_path": "work_history.past_employers"}
    r["results"].update(q1={"filled": True, "unconfirmed": False}, q2={"filled": True, "unconfirmed": False})
    r["snapshot"].update(q1="No", q2="N/A")
    v = gate_run(r, prof)
    check(v["content_verdict"] == gate.WOULD_SUBMIT and sum(1 for t in v["trace"] if t.get("source") == employers.SOURCE) == 2,
          f"the gate recomputes both and traces them ({v['content_reasons']})")
    bad = dict(r, plan={**r["plan"], "q1": {**r["plan"]["q1"], "value": "Yes"}})
    check(gate_run(bad, prof)["content_verdict"] == gate.WOULD_STOP, "a 'Yes' with source past_employers stops")
    check(gate_run(r, _prof("gate-match", ["Northwind Traders"]))["content_verdict"] == gate.WOULD_STOP,
          "the list now matching the tenant: the gate stops")
    check(gate_run(dict(r, url=ATS, final_url=ATS), prof)["content_verdict"] == gate.WOULD_STOP,
          "an address naming no tenant: the gate stops")
    check(memory.never_reason(YESNO, "per_company") is not None and memory.never_reason(DETAILS, "free_text") is not None,
          "never learned")
    check.done()


def test_any_match_or_any_doubt_goes_to_jack():
    # Fails on the previous commit: no employers module (every case was Jack's,
    # so the "No" cases above are what fail there; these guard the limits).
    check = Checks()
    on_list = _prof("match", ["Northwind Traders"])
    cases = [
        ("an employer on the list", [YESNO], on_list, WORKDAY),
        ("an employer on the list by initials", [YESNO], _prof("initials", ["North Wind"]), WORKDAY),
        ("an empty list", [YESNO], _prof("empty", []), WORKDAY),
        ("a four-character tenant", [dict(YESNO, label="Have you worked for ABCD before?")], _prof("short", ["Beta Corp"]), SHORT),
        ("a label naming another company", [dict(YESNO, label="Have you worked for Contoso before?")], _prof("other", ["Beta Corp"]), WORKDAY),
        ("'or its affiliates'", [dict(YESNO, label="Have you worked for Northwind or its affiliates?")], _prof("aff", ["Beta Corp"]), WORKDAY),
        ("'subsidiaries'", [dict(YESNO, label="Have you ever been employed by Northwind, its subsidiaries or parent companies?")], _prof("sub", ["Beta Corp"]), WORKDAY),
        ("a contractor wording", [dict(YESNO, label="Have you worked for Northwind as a contractor or consultant?")], _prof("con", ["Beta Corp"]), WORKDAY),
        ("two companies named", [dict(YESNO, label="Have you worked for Northwind / Southwind?")], _prof("two", ["Beta Corp"]), WORKDAY),
        ("an address on an ATS domain", [YESNO], _prof("ats", ["Beta Corp"]), ATS),
        ("no company in the label", [dict(YESNO, label="Have you previously worked here?")], _prof("here", ["Beta Corp"]), WORKDAY),
        ("an optional details box", [dict(DETAILS, required=False)], _prof("opt", ["Beta Corp"]), WORKDAY),
        ("a yes / no with no bare No option", [dict(YESNO, options=["Yes, currently", "Yes, in the past", "Never"])], _prof("nono", ["Beta Corp"]), WORKDAY),
        ("another stop kind beside it", [dict(YESNO, help_text="You certify this answer by electronic signature")], _prof("stop", ["Beta Corp"]), WORKDAY),
        ("a legal guard beside it", [dict(YESNO, label="Have you worked for Northwind under a visa sponsorship?")], _prof("guard", ["Beta Corp"]), WORKDAY),
    ]
    for what, fields, prof, url in cases:
        plan, flags, for_model = _route(fields, prof, url)
        refs = {f["ref"] for f in fields}
        check(not (refs & set(plan)) and not for_model and {fl["ref"] for fl in flags} == refs,
              f"{what}: Jack's, never the model ({plan}, {[fl.get('reason') for fl in flags]})")
    check(employers.loose_match("General Motors", "gm") and employers.loose_match("Northwind Traders", "northwind")
          and employers.loose_match("Beta Corp", "betacorp") and not employers.loose_match("Beta Corp", "northwind")
          and not employers.loose_match("Co", "northwind"),
          "the loose match: a word in the tenant, the tenant in the name, or the initials")
    check(employers.label_company("Have you worked for Northwind before?") == "Northwind"
          and employers.label_company("If you were formerly employed at Northwind, please provide dates") == "Northwind"
          and employers.label_company("Have you worked for Northwind or Southwind?") is None,
          "the company the label names; a doubt wording names none")
    check.done()


if __name__ == "__main__":
    run_as_script(globals())
