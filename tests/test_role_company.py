"""
test_role_company.py — the role and company shown for a link, never invented.

WHAT THIS DOES: Feeds guess_role_and_company fake pages (a title and an
address, no browser) and checks what it reads: on a Workday host the
tenant the address names is the company and the title is the role only
when its words equal the job slug's words; a title that just names the
site ("Careers") is never a role, on any host; the usual
"Title - Company" split still works elsewhere.
WHY IT MATTERS: Jack's General Motors run showed Role "Careers" and no
company (Jack, 2026-10-26). Blank beats wrong: a title is used only where
the address corroborates it, and nothing is reconstructed from a slug.

Run from anywhere:  .venv/Scripts/python.exe tests/test_role_company.py
"""
from _harness import Checks, run_as_script
from agent import extract

# Jack's link from the run, a local string only; no fetch happens.
GM = ("https://generalmotors.wd5.myworkdayjobs.com/Careers_GM/job/Warren-Michigan-United-States-of-America/"
      "XMLNAME-2027-Summer-Intern---Digital-Product--Embedded-and-Systems-Engineering_JR-202620513/apply"
      "?source=Handshake")
GM_TITLE = "2027 Summer Intern - Digital Product, Embedded and Systems Engineering"


class _Page:
    def __init__(self, title, url):
        self._title, self.url = title, url

    def title(self):
        return self._title


def test_workday_company_is_the_tenant_and_the_role_needs_the_address_to_agree():
    check = Checks()
    cases = [
        (_Page("Careers", GM), ("", "generalmotors"), "the /apply page titled 'Careers': no role, the tenant as company"),
        (_Page(GM_TITLE, GM), (GM_TITLE, "generalmotors"), "a title the job slug corroborates is the role, as written"),
        (_Page(GM_TITLE + " - Careers", GM), (GM_TITLE, "generalmotors"), "  ...or one segment of it"),
        (_Page("Careers | " + GM_TITLE, GM), (GM_TITLE, "generalmotors"), "  ...whichever side it sits on"),
        (_Page("Software Engineer", GM), ("", "generalmotors"), "a title the slug doesn't corroborate is not a role"),
        (_Page("", GM), ("", "generalmotors"), "no title: no role"),
        (_Page(GM_TITLE, "https://acme.wd1.myworkdaysite.com/en-US/jobs/job/Austin/Engineer_R12"),
         ("", "acme"), "a myworkdaysite host; the slug says Engineer, the title doesn't match"),
        (_Page("Engineer", "https://acme.wd1.myworkdaysite.com/en-US/jobs/job/Austin/Engineer_R12"),
         ("Engineer", "acme"), "  ...and the matching title is the role"),
        (_Page("Careers", "https://acme.wd1.myworkdayjobs.com/Careers"), ("", "acme"), "a Workday site page with no job path"),
    ]
    for page, want, what in cases:
        got = extract.guess_role_and_company(page)
        check(got == want, f"{what}: {got}")
    check.done()


def test_a_generic_site_title_is_never_a_role_anywhere():
    check = Checks()
    for title in ("Careers", "Jobs", "Job Search", "Workday", "Sign In", "Home", "  careers  "):
        got = extract.guess_role_and_company(_Page(title, "https://jobs.example-co.com/apply"))
        check(got == ("", ""), f"'{title}' on an ordinary host: {got}")
    got = extract.guess_role_and_company(_Page("Engineer - Careers", "https://jobs.example-co.com/apply"))
    check(got == ("Engineer", ""), f"a generic company half is dropped, the role kept: {got}")
    got = extract.guess_role_and_company(_Page("Careers - Example Co", "https://jobs.example-co.com/apply"))
    check(got == ("", "Example Co"), f"a generic role half is dropped, the company kept: {got}")
    check.done()


def test_ordinary_titles_split_as_before():
    # A guard: these passed on 403c058 too.
    check = Checks()
    got = extract.guess_role_and_company(_Page("Engineer - Example Co", "https://jobs.example-co.com/apply"))
    check(got == ("Engineer", "Example Co"), f"'Title - Company': {got}")
    got = extract.guess_role_and_company(_Page("Analyst | Acme", "https://acme.example/x"))
    check(got == ("Analyst", "Acme"), f"'Title | Company': {got}")
    got = extract.guess_role_and_company(_Page("Designer at Acme", "https://acme.example/x"))
    check(got == ("Designer", "Acme"), f"'Title at Company': {got}")
    got = extract.guess_role_and_company(_Page("Just a title", "https://acme.example/x"))
    check(got == ("Just a title", ""), f"no separator: the title is the role: {got}")

    class Broken:
        url = "https://acme.example/x"

        def title(self):
            raise RuntimeError("gone")
    check(extract.guess_role_and_company(Broken()) == ("", ""), "an unreadable title gives nothing")
    check.done()


if __name__ == "__main__":
    run_as_script(globals())
