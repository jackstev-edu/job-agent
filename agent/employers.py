"""
employers.py — "have you worked for <company>" from Jack's own list.

WHAT THIS DOES: Decision C (Jack, 2026-11-02: "Yes, from my list"). A
yes / no question "have you worked for / been employed by / were you
formerly employed at <company>" is answered "No", and a required text
box asking for former-employment details at <company> gets "N/A", only
when the company named by the job's own address (the tenant: a Workday,
Avature or iCIMS subdomain, a shared board's slug, else the registrable
domain's first label) matches no entry of work_history.past_employers
under the loose match (any employer word of three or more characters
inside the tenant, the tenant inside the employer's name with its spaces
removed, or the employer's initials equal to the tenant), AND the company
the label names loosely matches the tenant. Any match or any doubt goes
to Jack: a tenant of four characters or fewer, more than one company
named, "subsidiaries", "affiliates", "or", a contractor / consultant /
supplier / staffing wording, an address on a known ATS domain that names
no tenant, or an empty list. The gate recomputes the same decision from
the record and the profile and trusts the fill only when it equals.
WHY IT MATTERS: These questions are per-company and were always Jack's;
his list is the one fact that answers them, and the model never does.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlsplit

from . import pages, sensitive

SOURCE = "past_employers"
NO, NA = "No", "N/A"
MIN_TENANT_CHARS = 5                    # a tenant of four characters or fewer is doubt (Jack)

SHARED_BOARDS = {"job-boards.greenhouse.io", "boards.greenhouse.io", "jobs.lever.co", "jobs.ashbyhq.com"}
SUBDOMAIN_HOSTS = (".myworkdayjobs.com", ".myworkdaysite.com", ".avature.net", ".icims.com")
# Hosts of applicant-tracking systems: their registrable domain never
# names the employer, so an address there outside the rules above is doubt.
ATS_DOMAINS = {"taleo.net", "successfactors.com", "jobvite.com", "smartrecruiters.com", "workable.com",
               "bamboohr.com", "ultipro.com", "paylocity.com", "applytojob.com", "lever.co", "greenhouse.io",
               "ashbyhq.com", "myworkdayjobs.com", "myworkdaysite.com", "avature.net", "icims.com", "oraclecloud.com",
               "brassring.com", "adp.com", "dayforcehcm.com", "paycomonline.net", "breezy.hr", "recruitee.com",
               "teamtailor.com", "pinpointhq.com", "rippling.com", "gem.com", "hirebridge.com", "jazz.co"}

# The company phrase of a past-employer question: what follows "worked
# for / at / with", "employed by / at / with", up to the sentence's end,
# a comma, a question mark, or a time word.
_COMPANY = re.compile(
    r"\b(?:worked|employed|an?\s+employee|employment)\s+(?:for|at|with|by|of)\s+"
    r"(?P<company>.+?)(?=\s*(?:[?,.;:(]|\bbefore\b|\bpreviously\b|\bin\s+the\s+past\b|\bat\s+any\s+time\b"
    r"|\bplease\b|\bif\s+so\b|$))", re.I)
# Doubt anywhere in the question: another company may be meant, or a kind
# of work a list of employers doesn't attest to.
_DOUBT = re.compile(r"subsidiar|affiliat|\bor\b|\band/or\b|contractor|consultant|supplier|vendor|staffing|agency|"
                    r"\btemp\b|temporary|\binterns?\b|internship|partner|parent\s+compan|group\s+compan", re.I)
# Doubt inside the company phrase itself: more than one company named.
_MANY = re.compile(r"[/,&]|\band\b", re.I)
_ALNUM = re.compile(r"[^0-9a-z]+")


def _norm(text: Any) -> str:
    return _ALNUM.sub(" ", str(text or "").casefold()).strip()


def company_from_url(url: str) -> str:
    """The company token the job's own address names, or '' (doubt)."""
    try:
        parts = urlsplit(str(url or ""))
    except ValueError:
        return ""
    host = (parts.hostname or "").lower().rstrip(".")
    if not host:
        return ""
    if host in SHARED_BOARDS:
        first = next((seg for seg in (parts.path or "").split("/") if seg), "")
        return "" if first.lower() == "embed" else _norm(first).replace(" ", "")
    if host.endswith(SUBDOMAIN_HOSTS):
        sub = host.split(".")[0]
        return "" if sub in ("www", "careers", "jobs", "apply") else _norm(sub).replace(" ", "")
    family = pages.host_family(f"https://{host}/")
    if family in ATS_DOMAINS or family == host and host.count(".") == 0:
        return ""
    return _norm(family.split(".")[0]).replace(" ", "")


def label_company(text: Any) -> str | None:
    """The company the question names, or None when it names none or the wording is doubt."""
    for v in sensitive.text_variants(str(text or "")):
        if _DOUBT.search(v):
            return None
    m = _COMPANY.search(str(text or ""))
    if not m:
        return None
    company = m.group("company").strip().strip("\"'")
    company = re.sub(r"^(the|our|this)\s+", "", company, flags=re.I)
    if not company or _MANY.search(company):
        return None
    return company


def loose_match(employer: Any, tenant: str) -> bool:
    """The plan's loose match: an employer word of 3+ characters in the tenant, the tenant in the spaceless employer name, or the initials equal."""
    e, t = _norm(employer), _norm(tenant).replace(" ", "")
    if not e or not t:
        return False
    words = [w for w in e.split() if len(w) >= 3]
    if any(w in t for w in words):
        return True
    if t in e.replace(" ", ""):
        return True
    initials = "".join(w[0] for w in e.split())
    return len(initials) >= 2 and initials == t


def kind_of(field: dict) -> str | None:
    """"yesno" (a choice with a bare "No" option), "details" (a required text box), or None."""
    options = [str(o) for o in field.get("options") or []]
    ftype = (field.get("type") or "").lower()
    if options:
        return "yesno" if any(_norm(o) == "no" for o in options) else None
    if ftype in ("text", "textarea") and field.get("required") is True:
        return "details"
    return None


def plan_for(field: dict, url: str, past_employers: list) -> dict | None:
    """
    Decision C's plan for this field, or None (Jack's): the label asks
    about past employment at one company, that company loosely matches
    the tenant the address names, the tenant is long enough, Jack's list
    is not empty, and no entry of it loosely matches the tenant or the
    company named. Never the model; never a secret box.
    """
    if field.get("secret") or (field.get("type") or "").lower() in ("password", "checkbox", "file"):
        return None
    text = sensitive._question_text(field)
    if not sensitive.is_past_employer_question(text):
        return None
    # Decision C reads past the per-company stop kind only: any other
    # HARD_STOPS kind (a credential, a legal declaration, a consent) and
    # any LEGAL_GUARD kind (a Department of Defense question is legal, fold
    # round decision 1) keep the question Jack's.
    if sensitive.stop_kinds(text) - {"per_company"} or sensitive.legal_guard_kinds(field):
        return None
    employers = [str(e) for e in (past_employers or []) if str(e or "").strip()]
    if not employers:
        return None
    tenant = company_from_url(url)
    if len(tenant) < MIN_TENANT_CHARS:
        return None
    named = label_company(field.get("label"))
    if named is None or not loose_match(named, tenant):
        return None
    if any(loose_match(e, tenant) or loose_match(e, _norm(named).replace(" ", "")) for e in employers):
        return None
    kind = kind_of(field)
    if kind is None:
        return None
    if kind == "yesno":
        value = next(o for o in (str(o) for o in field.get("options") or []) if _norm(o) == "no")
    else:
        value = NA
    return {"label": field.get("label"), "options": field.get("options"), "action": "fill", "value": value,
            "source": SOURCE, "fit": "exact", "profile_path": "work_history.past_employers", "tenant": tenant}


def verify(field: dict, decision: dict | None, url: str, past_employers: list) -> bool:
    """The gate's recomputation: the same plan from the record's field, address and the profile's list, equal to the fill."""
    if not decision or decision.get("source") != SOURCE:
        return False
    plan = plan_for(field, url, past_employers)
    return plan is not None and str(plan["value"]) == str(decision.get("value"))
