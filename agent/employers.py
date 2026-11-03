"""
employers.py — "have you worked for <company>" from Jack's own list.

WHAT THIS DOES: Decision C (Jack, 2026-11-02: "Yes, from my list"; every
portal, 2026-11-03). A yes / no question "have you worked for / at, been
employed by / at, been an employee of <company>" is answered "No", and a
required details box "if you were formerly employed at <company>, please
provide ..." gets "N/A", only when the whole label is one of those
decided phrases (a fixed grammar on every reading of it; "worked with",
"in any capacity", a second company after a comma, a slash or
"(formerly ...)", "a <company> store" and any other wording are Jack's),
the company it names matches the tenant the job's own address names as
a whole (every word of it, corporate suffixes aside, inside the tenant,
or the tenant equal to the name without spaces or to its initials), the
tenant is five characters or more, Jack's list is a plain list of names
that is not empty, and no entry of it loosely matches the tenant or the
company named, in either direction. Any match or any doubt goes to Jack:
a doubt word anywhere in the label, help text, placeholder or options
("subsidiaries", "affiliates", "or", "contractor", "including",
"formerly" ...), a stop kind other than per-company, a legal guard kind,
the page's own raw stop / legal flag, a sensitive or credential category,
a shared ATS host or an address that names no tenant, an entry that is
not a bare name (the whole list is doubt then, said so in the job log).
The gate recomputes the same decision from the record and the profile
and trusts the fill only when it equals. WHY IT MATTERS: These questions
are per-company and were always Jack's; his list is the one fact that
answers them, and the model never does.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlsplit

from . import pages, sensitive

SOURCE = "past_employers"
NO, NA = "No", "N/A"
MIN_TENANT_CHARS = 5                    # a tenant of four characters or fewer is doubt (Jack)
# The job log's line when the list holds anything but bare names (Jack, 2026-11-03).
LIST_DOUBT = ("work_history.past_employers holds an entry that is not a bare name (a mapping such as "
              "`Name: dates`, a list, or an empty line): every past-employer question is yours until you fix it")

SHARED_BOARDS = {"job-boards.greenhouse.io", "boards.greenhouse.io", "jobs.lever.co", "jobs.ashbyhq.com"}
SUBDOMAIN_HOSTS = (".myworkdayjobs.com", ".myworkdaysite.com", ".avature.net", ".icims.com")
# Hosts of applicant-tracking systems: their registrable domain never
# names the employer, so an address there outside the rules above is doubt.
ATS_DOMAINS = {"taleo.net", "successfactors.com", "jobvite.com", "smartrecruiters.com", "workable.com",
               "bamboohr.com", "ultipro.com", "paylocity.com", "applytojob.com", "lever.co", "greenhouse.io",
               "ashbyhq.com", "myworkdayjobs.com", "myworkdaysite.com", "avature.net", "icims.com", "oraclecloud.com",
               "brassring.com", "adp.com", "dayforcehcm.com", "paycomonline.net", "breezy.hr", "recruitee.com",
               "teamtailor.com", "pinpointhq.com", "rippling.com", "gem.com", "hirebridge.com", "jazz.co"}
# Classifier categories the route never answers past (the reviewer's BLOCK 3
# on 5ba4a07): sensitive.decide hands these to Jack or a profile line.
NEVER_CATEGORIES = frozenset({"work_authorization", "eeo", "compensation", "legal_declaration", "credential", "consent"})

# The decided phrases, as a fixed grammar over the WHOLE label (casefolded,
# whitespace collapsed): a yes / no question and the details box. The
# company slot is one to four plain words; a comma, a slash, an
# ampersand, a parenthesis or "and" / "or" inside it fails the grammar.
# Lazy, so a tail word ("before") is read as the tail, not as part of the name.
_CO = r"(?P<company>[a-z0-9][a-z0-9'’.\-]*(?: [a-z0-9][a-z0-9'’.\-]*){0,3}?)"
# Past employment only: "have you worked for / been employed by / been an
# employee of", "were you (formerly) employed at / an employee of". The
# present tense ("are you employed by") asks something else (fix of
# 0698a98's review, BLOCK 3).
_YESNO = re.compile(
    r"^(?:have you (?:ever |previously |formerly |at any time )?(?:worked (?:for|at)|been employed (?:by|at)|been an employee of)"
    r"|were you (?:ever |previously |formerly |at any time )?(?:employed (?:by|at)|an employee of)) "
    r"(?:the )?" + _CO + r"(?: before| previously| in the past| at any time)?\s*\??$")
_DETAILS = re.compile(
    r"^if (?:you were|you have been|you've been|you’ve been|yes,?) (?:ever |formerly |previously )?employed (?:at|by) (?:the )?"
    + _CO + r",? (?:please )?(?:provide|list|give|enter|include|indicate|specify|state|describe) (?P<tail>.+)$")
# Doubt anywhere in the question (label, help, placeholder, options, every
# reading): another company may be meant, or a kind of work a list of
# employers doesn't attest to.
_DOUBT = re.compile(
    r"subsidiar|affiliat|\bor\b|and/or|contractor|consultant|supplier|vendor|staffing|agency|agencies|"
    r"\btemp\b|temporary|\binterns?\b|internship|partner|parent\s+compan|group\s+compan|includ|\balso\b|"
    r"\bother\b|another|formerly\s+(known|called|named)|known\s+as|\bnow\b|entit(y|ies)|brands?\b|division|franchise|dealer|"
    r"capacity|products?\b|services?\b|customers?\b|clients?\b|recruit|\bwith\b|\bof\s+any\b|\bany\s+of\b", re.I)
# A company slot that names a kind of place or a group, not one company,
# or two companies joined by "and" (fix of 0698a98's review, BLOCK 1).
_CO_DOUBT = re.compile(r"^(?:a|an|any|our|this|that|these|those|all)\b|\band\b|\b(?:store|stores|company|companies|entity|"
                       r"location|locations|site|sites|office|offices|facility|facilities|plant|plants|dealership|"
                       r"franchise|affiliate|subsidiary|division|unit|group|brand|team|teams|department)$", re.I)
# Beside the label (help text, placeholder, options), only a closed
# allowlist passes (fix of b4623af's review, BLOCK 2: a second company in
# plain prose, "Answer Yes if you have worked at Contoso", escaped every
# blacklist): the help text and placeholder empty or a fixed placeholder,
# every option "Yes", "No" or a placeholder row. Anything else is doubt.
_PLACEHOLDERS = frozenset({"", "select", "select one", "select an option", "please select", "choose", "choose one",
                           "please choose", "select...", "choose...", "-", "--", "---", "none selected", "not selected"})
_PLAIN_OPTIONS = frozenset({"yes", "no"})
# A role word anywhere in a host's company token (careers-ibm, ibm-careers,
# careersibm, jobs-ups) or a second token beside the company (careers-ibm-us)
# means the address names no company word on its own: doubt (fix of
# b4623af's review, BLOCK 4).
_ROLE_WORD = re.compile(r"careers?|jobs?|apply|recruit\w*|talent|hiring|external|internal|candidates?|employment|"
                        r"workwithus|joinus|opportunit\w*", re.I)
# The section over the box (a legend, a heading): any shape a second
# company could take there is doubt ("Northwind, a Contoso company").
_SECTION_DOUBT = re.compile(r"[(),;/&]|\band\b|formerly|previously|\bfka\b|f/k/a|compan|corporation|\binc\b|\bltd\b|\bllc\b", re.I)
_SUFFIXES = frozenset({"inc", "incorporated", "llc", "llp", "lp", "corp", "corporation", "co", "company", "ltd",
                       "limited", "plc", "gmbh", "ag", "sa", "nv", "bv", "pty", "the"})
_ALNUM = re.compile(r"[^0-9a-z]+")


def _norm(text: Any) -> str:
    return _ALNUM.sub(" ", str(text or "").casefold()).strip()


def _spaceless(text: Any) -> str:
    return _norm(text).replace(" ", "")


def _initials(text: Any) -> str:
    """The initials of a name's words, corporate suffixes dropped ("United Parcel Service Inc" -> "ups")."""
    words = [w for w in _norm(text).split() if w not in _SUFFIXES] or _norm(text).split()
    return "".join(w[0] for w in words)


def company_from_url(url: str) -> str:
    """
    The company token the job's own address names, or '' (doubt): the
    2026-11-02 tenant (memory.tenant_of: a shared board's slug, a Workday /
    Avature subdomain, else the host) reduced to its company word: the
    slug; the subdomain (iCIMS and myworkdaysite hosts too); a plain
    host's registrable domain, first label; a shared ATS host that names
    no company is doubt.
    """
    from . import memory
    tenant = memory.tenant_of(str(url or ""))
    if not tenant:
        return ""
    if "/" in tenant:                                           # a shared board: host/slug
        return _spaceless(tenant.split("/", 1)[1])
    host = (urlsplit(f"https://{tenant}/").hostname or "").lower() if "." in tenant else ""
    if not host:                                                # a bare Workday / Avature subdomain
        return _subdomain_company(tenant)
    if host.endswith(SUBDOMAIN_HOSTS):
        return _subdomain_company(host.split(".")[0])
    family = pages.host_family(f"https://{host}/")
    if family in ATS_DOMAINS or (family == host and host.count(".") == 0):
        return ""
    return _subdomain_company(family.split(".")[0])


def _subdomain_company(token: str) -> str:
    """
    The company word of a host's token (a portal subdomain, a plain host's
    registrable label), or '' (doubt): a role word anywhere in it
    (careers-ibm, ibm-careers, careersibm, jobs-ups), or more than one
    word (careers-ibm-us, acme-us), names no company on its own.
    """
    token = str(token or "").lower().strip()
    if not token or token == "www" or _ROLE_WORD.search(token):
        return ""
    # A hyphenated company name ("northwind-traders") is one word; a part
    # of three letters or fewer beside another ("acme-us", "careers-ibm-us")
    # is a region or role token, so the address names no company on its own.
    parts = [p for p in re.split(r"[-_]+", token) if p]
    if len(parts) > 1 and any(len(p) <= 3 for p in parts):
        return ""
    return _spaceless(token)


def _readings(text: Any) -> list[str]:
    """Every reading of a text, casefolded, whitespace collapsed, trailing punctuation kept."""
    return [" ".join(v.casefold().split()) for v in sensitive.text_variants(str(text or ""))]


def has_doubt(field: dict) -> bool:
    """
    A doubt word on any reading of the label, help text, placeholder,
    section (the legend or heading over the box) or an option; and beside
    the label (whose grammar admits one plain company) anything outside
    the closed allowlist: a help text or placeholder that is not empty or
    a fixed placeholder, an option that is not "Yes", "No" or a
    placeholder row. A second company in any shape fails that.
    """
    texts = [field.get("label"), field.get("help_text"), field.get("placeholder"), field.get("section"),
             *(field.get("options") or [])]
    if any(_DOUBT.search(v) for t in texts for v in sensitive.text_variants(str(t or ""))):
        return True
    if any(_SECTION_DOUBT.search(v) for v in sensitive.text_variants(str(field.get("section") or ""))):
        return True
    for t in (field.get("help_text"), field.get("placeholder")):
        if any(_norm(v) not in _PLACEHOLDERS for v in sensitive.text_variants(str(t or ""))):
            return True
    for o in field.get("options") or []:
        if any(_norm(v) not in _PLAIN_OPTIONS | _PLACEHOLDERS for v in sensitive.text_variants(str(o or ""))):
            return True
    return False


def parse_label(label: Any) -> tuple[str, str] | None:
    """
    (kind, company) when EVERY reading of the whole label is one decided
    phrase naming one plain company ("yesno" or "details"), else None.
    """
    readings = _readings(label)
    if not readings:
        return None
    found: set[tuple[str, str]] = set()
    for text in readings:
        m = _YESNO.fullmatch(text)
        kind = "yesno"
        if not m:
            m = _DETAILS.fullmatch(text)
            kind = "details"
        if not m:
            return None
        company = m.group("company").strip().strip("'’.")
        if not company or _CO_DOUBT.search(company) or _DOUBT.search(company):
            return None
        if kind == "details" and (_DOUBT.search(m.group("tail")) or "(" in m.group("tail")):
            return None
        found.add((kind, company))
    return found.pop() if len(found) == 1 else None


def label_company(text: Any) -> str | None:
    """The company the question names, or None when it names none or the wording is doubt."""
    parsed = parse_label(text)
    return parsed[1] if parsed else None


def company_matches_tenant(named: Any, tenant: str) -> bool:
    """
    The label's company as a whole against the tenant: every word of it
    (corporate suffixes aside, at least one word of three or more
    characters left) inside the tenant, or the tenant equal to the name
    without spaces, or to its initials. "Northwind recruiters" and "a
    Northwind store" do not match "northwind".
    """
    t = _spaceless(tenant)
    words = [w for w in _norm(named).split() if w not in _SUFFIXES]
    if not t or not words or not any(len(w) >= 3 for w in words):
        return False
    if all(w in t for w in words):
        return True
    joined = "".join(words)
    return t == joined or (len(words) >= 2 and t == "".join(w[0] for w in words))


def loose_match(employer: Any, tenant: str) -> bool:
    """The plan's loose match: an employer word of 3+ characters in the tenant, the tenant in the spaceless employer name, or the initials equal."""
    e, t = _norm(employer), _spaceless(tenant)
    if not e or not t:
        return False
    words = [w for w in e.split() if len(w) >= 3]
    if any(w in t for w in words):
        return True
    if t in e.replace(" ", ""):
        return True
    initials = _initials(e)
    return len(initials) >= 2 and initials == t


def entry_matches(entry: Any, tenant: str, named: Any) -> bool:
    """
    Does a list entry match the tenant or the company named, in either
    direction (the safe direction: more matches, more of Jack's)? An entry
    with no word of three or more characters ("GM", "P&G") matches by its
    letters equal to the tenant or to the named company's initials, or
    as a whole word of the named company.
    """
    e_words = _norm(entry).split()
    e_sl = "".join(e_words)
    if not e_sl:
        return False
    named_sl = _spaceless(named)
    t_sl = _spaceless(tenant)
    named_initials = _initials(named)
    if not any(len(w) >= 3 for w in e_words):
        # Its letters inside the tenant count too ("GM" in "gmfinancial":
        # the safe direction, 0698a98's review).
        return (e_sl == t_sl or (len(e_sl) >= 2 and e_sl in t_sl)
                or (len(e_sl) >= 2 and e_sl == named_initials) or e_sl in _norm(named).split())
    # A short word inside a longer entry ("GE Healthcare" against "General
    # Electric", "GM Financial" against "General Motors") counts when it
    # is the named company's initials, a prefix of them, or the tenant's
    # leading letters (b4623af's review: the safe direction).
    short_words = [w for w in e_words if len(w) < 3 and w not in _SUFFIXES]
    if any(len(w) >= 2 and (w == named_initials or (named_initials.startswith(w) and len(named_initials) >= 2)
                             or t_sl.startswith(w)) for w in short_words):
        return True
    return (loose_match(entry, tenant) or loose_match(entry, named_sl) or loose_match(named, e_sl)
            or (len(e_sl) >= 2 and e_sl == named_initials) or (len(named_sl) >= 2 and named_sl == _initials(entry)))


def kind_of(field: dict) -> str | None:
    """"yesno" (a choice with a bare "No" option), "details" (a required text box), or None."""
    options = [str(o) for o in field.get("options") or []]
    ftype = (field.get("type") or "").lower()
    if options:
        return "yesno" if any(_norm(o) == "no" for o in options) else None
    if ftype in ("text", "textarea") and field.get("required") is True:
        return "details"
    return None


def plan_for(field: dict, url: str, past_employers: list | None, category: str | None = None) -> dict | None:
    """
    Decision C's plan for this field, or None (Jack's): the whole label is
    a decided phrase about past employment at one plain company, that
    company matches the tenant the address names as a whole, the tenant is
    long enough, Jack's list is a plain non-empty list, no entry of it
    matches the tenant or the company named, no doubt word anywhere in the
    question, no stop kind but the per-company one, no legal guard kind,
    no raw flag, no sensitive or credential category. Never the model;
    never a secret box.
    """
    if field.get("secret") or (field.get("type") or "").lower() in ("password", "checkbox", "file"):
        return None
    if past_employers is None or category in NEVER_CATEGORIES:
        return None
    text = sensitive._question_text(field)
    if not sensitive.is_past_employer_question(text):
        return None
    # Decision C reads past the per-company stop kind only: any other
    # HARD_STOPS kind (a credential, a legal declaration, a consent), the
    # page's own raw stop / legal flag (a hit on text Python's label
    # skips) and any LEGAL_GUARD kind (a Department of Defense question is
    # legal, fold round decision 1) keep the question Jack's.
    if (sensitive.stop_kinds(text) - {"per_company"} or sensitive.legal_guard_kinds(field)
            or field.get("raw_stop") or field.get("raw_legal")):
        return None
    if has_doubt(field):
        return None
    employers = [str(e) for e in past_employers if str(e or "").strip()]
    if not employers:
        return None
    parsed = parse_label(field.get("label"))
    if parsed is None:
        return None
    label_kind, named = parsed
    tenant = company_from_url(url)
    if len(tenant) < MIN_TENANT_CHARS or not company_matches_tenant(named, tenant):
        return None
    if any(entry_matches(e, tenant, named) for e in employers):
        return None
    kind = kind_of(field)
    if kind is None or kind != label_kind:
        return None
    if kind == "yesno":
        value = next(o for o in (str(o) for o in field.get("options") or []) if _norm(o) == "no")
    else:
        value = NA
    return {"label": field.get("label"), "options": field.get("options"), "action": "fill", "value": value,
            "source": SOURCE, "fit": "exact", "profile_path": "work_history.past_employers", "tenant": tenant}


def verify(field: dict, decision: dict | None, url: str, past_employers: list | None,
           category: str | None = None) -> bool:
    """The gate's recomputation: the same plan from the record's field, address and the profile's list, equal to the fill."""
    if not decision or decision.get("source") != SOURCE:
        return False
    plan = plan_for(field, url, past_employers, category)
    return plan is not None and str(plan["value"]) == str(decision.get("value"))
