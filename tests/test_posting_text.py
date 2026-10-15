"""
test_posting_text.py — the model sees the posting's text, taken before anything changed.

WHAT THIS DOES: Runs the real `fill`, cover letter included, on a mock page
that shows saved EEO answers as plain text outside any <form>: once when
Apply is pressed (prepare) and again once the agent starts typing. The text
handed to the mapper and to the cover-letter call must hold the posting and
neither sentinel. A Greenhouse-shaped posting must still give the model its
job description.
WHY IT MATTERS: No value from the page may reach the model. A review step or
an "your answers" summary lives outside the form, where page_text's form
skip can't see it, so the text is taken once, on the posting, before Apply.

Run from anywhere:  .venv/Scripts/python.exe tests/test_posting_text.py
"""
from pathlib import Path

from _harness import TEST_HOME, Checks, mock_page, run_as_script
from agent import adapters, extract

SENTINEL = "POSTING-TEXT-SENTINEL-8812"

SUMMARY_FORM = f"""<!doctype html><html><body>
  <h1>Data Engineer</h1><p>We build pipelines for a living.</p>
  <form><label for="fn">First Name</label><input id="fn" name="first_name">
    <label for="em">Email</label><input id="em" name="email" type="email"></form>
  <div id="after"></div>
<script>
  // Stands in for a portal that shows your saved answers as plain text,
  // outside the form, once you're past Apply and once you start typing.
  window.showSaved = () => document.body.insertAdjacentHTML('beforeend',
    '<section><h2>Your saved answers</h2><p>Gender: {SENTINEL}-prep</p></section>');
  document.getElementById('fn').addEventListener('input', () => {{
    document.getElementById('after').innerHTML =
      '<p>Veteran status: {SENTINEL}-fill</p>';
  }}, {{once: true}});
</script></body></html>"""

# The classic job-board layout: title and company in a header, the job
# description in #content, the form in its own block below.
BOARD = """<!doctype html><html><head><title>Data Engineer at Example Co</title></head><body>
<div id="app_body">
  <div id="header"><h1 class="app-title">Data Engineer</h1>
    <div class="company-name">at Example Co</div><div class="location">Remote</div></div>
  <div id="content"><p>You will design and run our batch pipelines.</p>
    <ul><li>Five years of SQL</li><li>Some Python</li></ul></div>
  <div id="application"><form id="application_form">
    <div class="field"><label for="first_name">First Name <span class="asterisk">*</span></label>
      <input id="first_name" name="job_application[first_name]" type="text"></div>
    <div class="field"><label for="email">Email</label>
      <input id="email" name="job_application[email]" type="text"></div>
  </form></div>
</div></body></html>"""


def _write(name: str, html: str) -> Path:
    path = TEST_HOME / name
    path.write_text(html, encoding="utf-8")
    return path


def _run(form: Path, *, on_prepare=None):
    """A real fill with a cover letter; returns (mapper texts, letter texts, printed, error)."""
    import apply
    from _fill_run import map_from_profile, run_fill

    mapper_texts, letter_texts = [], []
    real_prepare = adapters.prepare

    def prepare(page, key):
        real_prepare(page, key)
        if on_prepare:
            page.evaluate(on_prepare)

    def map_fields(fields, profile, page_text=""):
        mapper_texts.append(page_text)
        return map_from_profile(fields, profile, page_text)

    def draft(profile, job_text, company="", role=""):
        letter_texts.append(job_text)
        return "A draft."

    text, _, error = run_fill(
        form, dry_run=False, cover_letter=True, map_fields=map_fields,
        patches=((apply.adapters, "prepare", prepare),
                 (apply.letters, "draft_cover_letter", draft)))
    return mapper_texts, letter_texts, text, error


def test_answers_shown_outside_the_form_never_reach_the_model():
    check = Checks()
    form = _write("posting_summary.html", SUMMARY_FORM)
    with mock_page(form) as page:
        page.evaluate("window.showSaved()")
        page.fill("#fn", "x")
        later = extract.page_text(page, 6000)
    check(f"{SENTINEL}-prep" in later and f"{SENTINEL}-fill" in later,
          "page_text taken later would hold both sentinels (so the checks below mean something)")

    mapper_texts, letter_texts, text, error = _run(form, on_prepare="window.showSaved()")
    print(text[-600:])
    check(error is None, f"the run finished ({error})")
    check(mapper_texts and letter_texts, "the mapper and the cover-letter call were both reached")
    check(all("We build pipelines for a living." in t for t in mapper_texts + letter_texts),
          "both were given the posting")
    check(all(SENTINEL not in t for t in mapper_texts + letter_texts),
          "neither was given the saved answers shown after Apply or after typing")
    check.done()


def test_a_job_board_posting_still_gives_its_description():
    check = Checks()
    mapper_texts, letter_texts, text, error = _run(_write("posting_board.html", BOARD))
    check(error is None, f"the run finished ({error})")
    print(f"  mapper page_text: {mapper_texts[:1]!r}")
    for name, texts in (("mapper", mapper_texts), ("cover letter", letter_texts)):
        check(texts and all("Data Engineer" in t and "at Example Co" in t
                            and "You will design and run our batch pipelines." in t
                            and "Five years of SQL" in t for t in texts),
              f"the {name} gets the title, company and job description")
        check(texts and all("First Name" not in t for t in texts),
              f"the {name} gets nothing from inside the form")
    check.done()


if __name__ == "__main__":
    run_as_script(globals())
