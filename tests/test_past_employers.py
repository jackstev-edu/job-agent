"""
test_past_employers.py — decision C (Jack, 2026-11-02; every portal and the four holes closed, 2026-11-03): "have you worked for <company>" from his list.

WHAT THIS DOES: Routes past-employer questions through pipeline.route with
a profile holding work_history.past_employers and a job address naming a
tenant: a tenant on no entry gives "No" to the yes / no question and
"N/A" to the required details box, and the gate traces both; an employer
on the list (by a word, by initials, by a short entry such as "GM"), an
empty list, a four-character tenant, a label naming a different company,
"or affiliates", a contractor wording, a doubt word in the help text or
an option, a second company after a comma or "(formerly ...)", "worked
with ... products", "a <company> store", "in any capacity", "<company>
recruiters", a raw stop / legal flag, a sensitive category, a shared ATS
host and a list holding a `Name: dates` entry (the whole list is doubt,
said so in the job log) each go to Jack; the gate recomputes the plan
from the record and the profile and stops when the list now matches, the
value differs or the field reads as legal; the template gains the line
and the profile reads it; nothing here is ever learned.
WHY IT MATTERS: These questions were always per-company and Jack's; his
list is the one fact that answers them, and the model never does.

Run from anywhere:  .venv/Scripts/python.exe tests/test_past_employers.py
"""
import io
from contextlib import redirect_stdout
from unittest import mock

import yaml

from _fill_run import PROFILE, map_from_profile
from _harness import TEST_HOME, Checks, mock_page, run_as_script
from agent import employers, extract, gate, memory, pipeline, template
from agent.profile import Profile
from test_gate import base, run as gate_run

WORKDAY = "https://northwind.wd5.myworkdayjobs.com/en-US/Careers/job/Boston/Engineer_R1/apply/applyManually"
SHORT = "https://abcd.wd5.myworkdayjobs.com/en-US/Careers/job/Boston/Engineer_R1/apply"
GREENHOUSE = "https://job-boards.greenhouse.io/northwind/jobs/1"
PLAIN = "https://careers.northwind-traders.com/apply/1"
ATS = "https://careers-northwind.taleo.net/careersection/1/jobapply.ftl"
SHARED_ATS = "https://jobs.smartrecruiters.com/Northwind/743999"
AVATURE = "https://northwind.avature.net/careers/ApplicationMethods?jobId=1"

YESNO = {"ref": "q1", "type": "radio", "label": "Have you worked for Northwind before?", "options": ["Yes", "No"],
         "option_refs": ["q1", "q1b"], "required": True}
DETAILS = {"ref": "q2", "type": "textarea", "required": True,
           "label": "If you were formerly employed at Northwind, please provide the information listed: dates, title, location"}
EMPLOYED_BY = {"ref": "q3", "type": "select", "label": "Have you ever been employed by Northwind?", "options": ["Select", "Yes", "No"]}


def _prof(name, employers_list, raw=None):
    if raw is not None:
        text = PROFILE + "work_history:\n  past_employers:\n" + raw
    elif employers_list:
        text = PROFILE + "work_history:\n  past_employers:\n" + "".join(f"    - {e}\n" for e in employers_list)
    else:
        text = PROFILE + "work_history:\n  past_employers: []\n"
    p = TEST_HOME / f"employers-{name}.yaml"
    p.write_text(text, encoding="utf-8")
    return Profile(yaml.safe_load(text), p)


def _route(fields, prof, url, category="per_company"):
    cats = {f["ref"]: category for f in fields}
    return pipeline.route(fields, cats, prof, set(), url=url)


def test_a_tenant_on_no_entry_gives_no_and_n_a_and_the_gate_traces_them():
    # Fails on 2c24303: no employers module; the questions were Jack's.
    check = Checks()
    prof = _prof("none", ["Beta Corp", "Gamma Widgets"])
    check(prof.past_employers() == ["Beta Corp", "Gamma Widgets"], "the profile reads the list")
    check("work_history:" in template.BLANK_PROFILE and "past_employers: []" in template.BLANK_PROFILE,
          "the template carries the line")
    for url, what in ((WORKDAY, "a Workday tenant"), (GREENHOUSE, "a shared board's slug"), (PLAIN, "a plain host's domain"),
                      (AVATURE, "an Avature tenant")):
        plan, flags, for_model = _route([YESNO, DETAILS, EMPLOYED_BY], prof, url)
        check(plan.get("q1", {}).get("value") == "No" and plan.get("q1", {}).get("source") == employers.SOURCE
              and plan.get("q2", {}).get("value") == "N/A" and plan.get("q3", {}).get("value") == "No"
              and not flags and not for_model,
              f"{what}: No, N/A and No, never the model ({plan})")
    check(employers.company_from_url(WORKDAY) == "northwind" and employers.company_from_url(GREENHOUSE) == "northwind"
          and employers.company_from_url(PLAIN) == "northwindtraders" and employers.company_from_url(ATS) == ""
          and employers.company_from_url(SHARED_ATS) == "" and employers.company_from_url("https://jobs.lever.co/") == ""
          and employers.company_from_url(AVATURE) == "northwind"
          and employers.company_from_url("https://boards.greenhouse.io/embed/job_app?for=northwind") == "northwind",
          "the company token the 2026-11-02 tenant names; a shared ATS host or one naming no company gives none")
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
    # 5ba4a07's review, BLOCK 3: a field the gate calls legal / sensitive is never answered from the list.
    legal = dict(r, fields=[*r["fields"][:-2], dict(YESNO, help_text="Have you held a security clearance there?"), DETAILS])
    check(gate_run(legal, prof)["content_verdict"] == gate.WOULD_STOP, "a legal guard beside the question: the gate stops")
    flagged = dict(r, fields=[*r["fields"][:-2], dict(YESNO, raw_legal=True), DETAILS])
    check(gate_run(flagged, prof)["content_verdict"] == gate.WOULD_STOP, "the page's raw legal flag: the gate stops")
    cat = dict(r, categories={**r["categories"], "q1": "legal_declaration"})
    check(gate_run(cat, prof)["content_verdict"] == gate.WOULD_STOP, "a legal_declaration category: the gate stops")
    check(memory.never_reason(YESNO, "per_company") is not None and memory.never_reason(DETAILS, "free_text") is not None,
          "never learned")
    check.done()


def test_any_match_or_any_doubt_goes_to_jack():
    # The 2026-11-02 cases guard the limits (each Jack's on 2c24303 as well,
    # with no employers module); the 2026-11-03 cases, marked (hole), each
    # gave "No" or "N/A" on 0530a70.
    check = Checks()
    on_list = _prof("match", ["Northwind Traders"])
    beta = _prof("beta", ["Beta Corp"])
    cases = [
        ("an employer on the list", [YESNO], on_list, WORKDAY, "per_company"),
        ("an employer on the list by a word", [YESNO], _prof("word", ["North Wind"]), WORKDAY, "per_company"),
        ("a short entry equal to the label company's initials (hole)",
         [dict(YESNO, label="Have you worked for General Motors before?")], _prof("gm", ["GM"]),
         "https://generalmotors.wd5.myworkdayjobs.com/careers/job/1/apply", "per_company"),
        ("a short entry equal to the tenant (hole)", [dict(YESNO, label="Have you worked for GE Aerospace before?")],
         _prof("ge", ["GE"]), "https://geaerospace.wd5.myworkdayjobs.com/careers/job/1/apply", "per_company"),
        ("an empty list", [YESNO], _prof("empty", []), WORKDAY, "per_company"),
        ("a four-character tenant", [dict(YESNO, label="Have you worked for ABCD before?")], _prof("short", ["Beta Corp"]), SHORT, "per_company"),
        ("a label naming another company", [dict(YESNO, label="Have you worked for Contoso before?")], beta, WORKDAY, "per_company"),
        ("'or its affiliates'", [dict(YESNO, label="Have you worked for Northwind or its affiliates?")], beta, WORKDAY, "per_company"),
        ("'subsidiaries'", [dict(YESNO, label="Have you ever been employed by Northwind, its subsidiaries or parent companies?")], beta, WORKDAY, "per_company"),
        ("a contractor wording", [dict(YESNO, label="Have you worked for Northwind as a contractor or consultant?")], beta, WORKDAY, "per_company"),
        ("two companies named", [dict(YESNO, label="Have you worked for Northwind / Southwind?")], beta, WORKDAY, "per_company"),
        ("an address on an ATS domain", [YESNO], beta, ATS, "per_company"),
        ("a shared ATS host (hole)", [YESNO], beta, SHARED_ATS, "per_company"),
        ("no company in the label", [dict(YESNO, label="Have you previously worked here?")], beta, WORKDAY, "per_company"),
        ("an optional details box", [dict(DETAILS, required=False)], beta, WORKDAY, "per_company"),
        ("a yes / no with no bare No option", [dict(YESNO, options=["Yes, currently", "Yes, in the past", "Never"])], beta, WORKDAY, "per_company"),
        ("another stop kind beside it", [dict(YESNO, help_text="You certify this answer by electronic signature")], beta, WORKDAY, "per_company"),
        ("a legal guard beside it", [dict(YESNO, label="Have you worked for Northwind under a visa sponsorship?")], beta, WORKDAY, "per_company"),
        # 2026-11-03, the four holes of 5ba4a07's review.
        ("a doubt word in the help text (hole)", [dict(YESNO, help_text="Include any subsidiary or affiliate, or work as a contractor")], beta, WORKDAY, "per_company"),
        ("a doubt word in an option (hole)", [dict(YESNO, options=["Yes", "Yes, as a contractor", "No"])], beta, WORKDAY, "per_company"),
        ("'(formerly Contoso)' (hole)", [dict(YESNO, label="Have you ever worked for Northwind (formerly Contoso)?")], _prof("formerly", ["Contoso"]), WORKDAY, "per_company"),
        ("a second company after a comma (hole)", [dict(YESNO, label="Have you been employed by Northwind, Contoso Ltd.?")], _prof("comma", ["Contoso"]), WORKDAY, "per_company"),
        ("'worked with ... products' (hole)", [dict(YESNO, label="Have you worked with Northwind's products before?")], beta, WORKDAY, "per_company"),
        ("'worked with ... recruiters' (hole)", [dict(YESNO, label="Have you worked with Northwind recruiters before?")], beta, WORKDAY, "per_company"),
        ("'a Northwind store' (hole)", [dict(YESNO, label="Have you worked at a Northwind store?")], beta, WORKDAY, "per_company"),
        ("'in any capacity' (hole)", [dict(YESNO, label="Have you worked for Northwind in any capacity?")], beta, WORKDAY, "per_company"),
        ("'Northwind recruiters' (hole)", [dict(YESNO, label="Have you worked for Northwind recruiters before?")], beta, WORKDAY, "per_company"),
        ("'a Northwind company' (hole)", [dict(YESNO, label="Have you worked for a Northwind company?")], beta, WORKDAY, "per_company"),
        ("the page's raw stop flag (hole)", [dict(YESNO, raw_stop=True)], beta, WORKDAY, "per_company"),
        ("the page's raw legal flag (hole)", [dict(YESNO, raw_legal=True)], beta, WORKDAY, "per_company"),
        ("a legal_declaration category (hole)", [YESNO], beta, WORKDAY, "legal_declaration"),
        ("a credential category (hole)", [YESNO], beta, WORKDAY, "credential"),
        ("a yes / no question rendered as a text box (hole)", [dict(YESNO, type="text", options=None, option_refs=None)], beta, WORKDAY, "per_company"),
        ("a `Name: dates` entry: the whole list is doubt (hole)", [YESNO, DETAILS], _prof("mapping", None, raw="    - Beta Corp\n    - Contoso: 2019-2021\n"), WORKDAY, "per_company"),
        ("a blank entry: the whole list is doubt (hole)", [YESNO], _prof("blank", None, raw="    - Beta Corp\n    - ''\n"), WORKDAY, "per_company"),
        # Fix of 0698a98's review (each gave No on 0698a98).
        ("'and' in the company slot", [dict(YESNO, label="Have you worked for Sandia and SA?")], beta,
         "https://sandia.wd5.myworkdayjobs.com/careers/job/1/apply", "per_company"),
        ("'P and G' with the list's Procter & Gamble", [dict(YESNO, label="Have you worked for P and G?")], _prof("pg", ["Procter & Gamble"]),
         "https://pandg.wd5.myworkdayjobs.com/careers/job/1/apply", "per_company"),
        ("'(formerly ...)' in the help text", [dict(YESNO, help_text="Northwind (formerly Contoso Ltd.)")], _prof("fk", ["Contoso"]), WORKDAY, "per_company"),
        ("a comma in the help text", [dict(YESNO, help_text="Northwind, Contoso Ltd.")], _prof("comma2", ["Contoso"]), WORKDAY, "per_company"),
        ("'and' in the help text", [dict(YESNO, help_text="Northwind and Contoso")], _prof("and2", ["Contoso"]), WORKDAY, "per_company"),
        ("'fka' in the placeholder", [dict(YESNO, placeholder="Northwind fka Contoso")], _prof("fka", ["Contoso"]), WORKDAY, "per_company"),
        ("a second company in an option", [dict(YESNO, options=["Yes", "No", "Yes, at Contoso"])], _prof("opt2", ["Contoso"]), WORKDAY, "per_company"),
        ("the present tense 'are you employed by'", [dict(YESNO, label="Are you employed by Northwind?")], beta, WORKDAY, "per_company"),
        ("the present tense 'are you an employee of'", [dict(YESNO, label="Are you an employee of Northwind?")], beta, WORKDAY, "per_company"),
        ("an iCIMS careers- host naming a three-letter company", [dict(YESNO, label="Have you worked for IBM before?")], beta,
         "https://careers-ibm.icims.com/jobs/1/job", "per_company"),
        ("a short entry inside the tenant", [dict(YESNO, label="Have you worked for Financial before?")], _prof("gm2", ["GM"]),
         "https://gmfinancial.wd5.myworkdayjobs.com/careers/job/1/apply", "per_company"),
    ]
    for what, fields, prof, url, category in cases:
        plan, flags, for_model = _route(fields, prof, url, category)
        refs = {f["ref"] for f in fields}
        check(not (refs & set(plan)) and not for_model and {fl["ref"] for fl in flags} == refs,
              f"{what}: Jack's, never the model ({plan}, {[fl.get('reason') for fl in flags]})")
    check(_prof("mapping2", None, raw="    - Beta Corp\n    - Contoso: 2019-2021\n").past_employers() is None
          and _prof("list-ok", ["Beta Corp"]).past_employers() == ["Beta Corp"],
          "a non-plain entry makes past_employers None; a plain list reads")
    check(employers.company_from_url("https://careers-ibm.icims.com/jobs/1/job") == "ibm"
          and employers.company_from_url("https://careers-northwind.icims.com/jobs/1/job") == "northwind"
          and employers.company_from_url("https://careers.icims.com/jobs/1/job") == "",
          "an iCIMS careers- subdomain names the company after the role word; a bare role word names none")
    check(employers.label_company("Were you formerly employed at Northwind?") == "northwind"
          and employers.label_company("Are you employed by Northwind?") is None,
          "the past tense is a decided phrase; the present tense is not")
    check(employers.loose_match("General Motors", "gm") and employers.loose_match("Northwind Traders", "northwind")
          and employers.loose_match("Beta Corp", "betacorp") and not employers.loose_match("Beta Corp", "northwind")
          and not employers.loose_match("Co", "northwind"),
          "the loose match: a word in the tenant, the tenant in the name, or the initials")
    check(employers.company_matches_tenant("Northwind", "northwind") and employers.company_matches_tenant("Northwind Traders Inc.", "northwindtraders")
          and employers.company_matches_tenant("General Motors", "generalmotors") and employers.company_matches_tenant("Northwind", "northwindtraders")
          and not employers.company_matches_tenant("Northwind recruiters", "northwind")
          and not employers.company_matches_tenant("a Northwind store", "northwind") and not employers.company_matches_tenant("here", "northwind"),
          "the label's company matches the tenant as a whole, not by one word")
    check(employers.label_company("Have you worked for Northwind before?") == "northwind"
          and employers.label_company("If you were formerly employed at Northwind, please provide dates") == "northwind"
          and employers.label_company("Have you worked for Northwind or Southwind?") is None
          and employers.label_company("Have you ever worked for Northwind (formerly Contoso)?") is None
          and employers.label_company("Have you been employed by Northwind, Contoso Ltd.?") is None
          and employers.label_company("Have you worked with Northwind's products before?") is None,
          "the company the label names; a doubt wording or a wording outside the decided phrases names none")
    check.done()


PAGE = """<!doctype html><html><body><h1>Additional questions (mock)</h1><form>
<fieldset><legend>Have you worked for Northwind before?</legend>
<label><input type="radio" name="worked" value="y"> Yes</label><label><input type="radio" name="worked" value="n"> No</label></fieldset>
</form></body></html>"""


def test_a_non_plain_list_entry_is_said_in_the_job_log_and_the_question_stays_jacks():
    # Jack, 2026-11-03: "a non-plain past_employers entry makes the whole
    # list doubt, with a line in the job log saying why". Fails on 0530a70
    # (the entry was dropped silently and the question answered "No").
    check = Checks()
    p = TEST_HOME / "employers-page.html"
    p.write_text(PAGE, encoding="utf-8")
    prof = _prof("mapping-page", None, raw="    - Beta Corp\n    - Contoso: 2019-2021\n")
    with mock_page(p) as page:
        # The address is the mock file's: with the list doubt the plan is
        # refused before the tenant is read, which is the point.
        with mock.patch("agent.classify.classify_fields", lambda fields: {f["ref"]: "per_company" for f in fields}), \
                mock.patch("agent.mapper.map_fields", map_from_profile), redirect_stdout(io.StringIO()):
            fill = pipeline.fill_page(page, prof, guard=extract.ReadGuard(), resume=None, posting_text="")
        check(any(l == employers.LIST_DOUBT for l in fill.lines), f"the job log says why ({fill.lines})")
        q = next(f for f in fill.fields if f["type"] == "radio")
        check(q["ref"] not in fill.plan and any(fl.get("ref") == q["ref"] for fl in fill.flags), "the question is Jack's")
        check(page.evaluate("() => [...document.querySelectorAll('input[type=radio]')].every(r => !r.checked)"), "nothing chosen")
    check.done()


if __name__ == "__main__":
    run_as_script(globals())
