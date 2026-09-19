"""
report.py — the review sheet.

WHAT THIS DOES: After a fill, this writes a single HTML page listing every
question, what went into it, and where that answer came from. It opens in
your browser next to the application itself.

WHY IT MATTERS: Scrolling a long form hunting for what the agent got wrong is
miserable. This puts the things needing your attention at the top and the
routine ones below, so checking an application takes a minute instead of ten.
"""

from __future__ import annotations

import html
import webbrowser
from datetime import datetime
from pathlib import Path

from . import config

# The page is styled as an instrument report rather than a web page: dense,
# left-aligned, status in a fixed gutter, data set in a monospaced face and
# prose in a sans. Everything is inlined so the file works offline and can be
# emailed or archived as a single artefact.
_CSS = """
:root {
  --paper:   #f7f8f9;
  --card:    #ffffff;
  --ink:     #16202b;
  --ink-soft:#5b6875;
  --rule:    #d5dae0;
  --pass:    #2f6b4f;
  --attend:  #a65a00;
  --fail:    #9b2226;
  --skip:    #8a93a0;
}
* { box-sizing: border-box; }
html { -webkit-text-size-adjust: 100%; }
body {
  margin: 0; padding: 2.5rem 1.5rem 6rem;
  background: var(--paper); color: var(--ink);
  font: 400 15px/1.55 ui-sans-serif, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
}
.sheet { max-width: 62rem; margin: 0 auto; }

/* ---- masthead ---- */
.masthead { border-bottom: 2px solid var(--ink); padding-bottom: 1rem; margin-bottom: 1.75rem; }
.masthead h1 { margin: 0 0 .35rem; font-size: 1.55rem; font-weight: 620; letter-spacing: -.015em; }
.masthead p { margin: 0; color: var(--ink-soft); font-size: .92rem; }
.masthead a { color: var(--ink-soft); }

.tally { display: flex; flex-wrap: wrap; gap: 1.75rem; margin-top: 1.1rem; }
.tally div { line-height: 1.2; }
.tally b { display: block; font-size: 1.6rem; font-weight: 600;
           font-variant-numeric: tabular-nums; }
.tally span { font-size: .82rem; color: var(--ink-soft); }
.tally .t-attend b { color: var(--attend); }
.tally .t-fail b   { color: var(--fail); }

/* ---- the checklist: the one loud element on the page ---- */
.checklist { border-left: 4px solid var(--attend); background: var(--card);
             padding: 1.25rem 1.5rem; margin-bottom: 2.25rem; }
.checklist h2 { margin: 0 0 .3rem; font-size: 1.05rem; font-weight: 620; }
.checklist > p { margin: 0 0 1rem; color: var(--ink-soft); font-size: .9rem; }
.checklist ol { margin: 0; padding-left: 1.4rem; }
.checklist li { margin-bottom: .9rem; }
.checklist li:last-child { margin-bottom: 0; }
.checklist .q { font-weight: 550; }
.checklist .why { display: block; color: var(--ink-soft); font-size: .88rem; margin-top: .15rem; }

/* ---- the table ---- */
h2.section { font-size: .95rem; font-weight: 620; margin: 2.25rem 0 .6rem;
             padding-bottom: .35rem; border-bottom: 1px solid var(--rule); }
table { width: 100%; border-collapse: collapse; background: var(--card); }
th { text-align: left; font-size: .78rem; font-weight: 600; color: var(--ink-soft);
     padding: .5rem .75rem; border-bottom: 1px solid var(--rule); }
td { padding: .6rem .75rem; border-bottom: 1px solid var(--rule);
     vertical-align: top; font-size: .89rem; }
tr:last-child td { border-bottom: none; }
td.status { width: 6.5rem; font-weight: 600; white-space: nowrap; }
td.question { width: 45%; }
td.answer { font-family: ui-monospace, "SF Mono", Menlo, Consolas, monospace;
            font-size: .84rem; color: var(--ink-soft); word-break: break-word; }
.s-pass   { color: var(--pass); }
.s-attend { color: var(--attend); }
.s-fail   { color: var(--fail); }
.s-skip   { color: var(--skip); }
.origin { font-size: .78rem; color: var(--ink-soft); display: block; margin-top: .2rem; }
.required::after { content: " (required)"; font-size: .75rem; color: var(--fail); font-weight: 500; }

.footnote { margin-top: 3rem; padding-top: 1rem; border-top: 1px solid var(--rule);
            color: var(--ink-soft); font-size: .85rem; max-width: 42rem; }

@media (max-width: 620px) {
  td.question, td.status { width: auto; }
  table, thead, tbody, th, td, tr { display: block; }
  thead { display: none; }
  tr { border-bottom: 1px solid var(--rule); padding: .5rem 0; }
  td { border: none; padding: .2rem .75rem; }
}
"""

_ORIGIN_LABEL = {
    "profile": "copied from your profile",
    "model":   "matched by AI",
    "human":   "left for you",
}


def build(*, url: str, ats_name: str, report_rows: list[dict],
          flags: list[dict], company: str = "", role: str = "") -> Path:
    """Write the review sheet and return its path."""
    config.ensure_dirs()
    e = html.escape

    attention = [r for r in report_rows if r["filled"] and r["needs_review"]]
    failed = [r for r in report_rows if not r["filled"] and r.get("value") is not None]
    clean = [r for r in report_rows if r["filled"] and not r["needs_review"]]
    skipped = [r for r in report_rows if not r["filled"] and r.get("value") is None]

    # --- the checklist: everything you must personally handle ---------------
    items = []
    for flag in flags:
        items.append((flag.get("label") or "(unlabelled question)",
                      flag.get("reason") or ""))
    for row in attention:
        items.append((row["label"],
                      row.get("note") or "Drafted by AI — read it before you send it."))
    for row in failed:
        items.append((row["label"],
                      f"Could not be filled: {row.get('detail','')}. Enter it by hand."))

    checklist_html = ""
    if items:
        entries = "".join(
            f'<li><span class="q">{e(str(q))}</span>'
            f'<span class="why">{e(str(why))}</span></li>'
            for q, why in items
        )
        checklist_html = (
            f'<section class="checklist">'
            f'<h2>Handle these before you submit</h2>'
            f'<p>Legal declarations, per-company answers, and anything the agent '
            f'drafted rather than copied.</p>'
            f'<ol>{entries}</ol></section>'
        )
    else:
        checklist_html = (
            '<section class="checklist" style="border-left-color:var(--pass)">'
            '<h2>Nothing needs your attention</h2>'
            '<p>Every field came straight from your profile. Still worth a '
            'read-through of the form itself before you submit.</p></section>'
        )

    # --- the full table -----------------------------------------------------
    def rows_html(rows, status_word, status_class):
        out = []
        for r in rows:
            value = r.get("value")
            has_value = value not in (None, "")
            shown = e(str(value)) if has_value else "—"
            if len(shown) > 400:
                shown = shown[:400] + "…"

            # Only name the origin when something was actually written. Saying
            # "matched by AI" next to an empty cell reads as a mistake.
            origin = ""
            if has_value:
                label = _ORIGIN_LABEL.get(r.get("source", "model"), "")
                origin = f'<span class="origin">{e(label)}</span>'

            note = f'<span class="origin">{e(r["note"])}</span>' if r.get("note") else ""
            req = " required" if r.get("required") else ""
            out.append(
                f'<tr><td class="status {status_class}">{status_word}</td>'
                f'<td class="question"><span class="{req.strip()}">{e(str(r["label"]))}</span>{note}</td>'
                f'<td class="answer">{shown}{origin}</td></tr>'
            )
        return "".join(out)

    sections = []
    if attention:
        sections.append(('Drafted — read these', rows_html(attention, "check", "s-attend")))
    if failed:
        sections.append(('Could not fill', rows_html(failed, "failed", "s-fail")))
    if clean:
        sections.append(('Filled from your profile', rows_html(clean, "done", "s-pass")))
    if skipped:
        sections.append(('Left blank', rows_html(skipped, "blank", "s-skip")))

    tables = "".join(
        f'<h2 class="section">{e(title)}</h2>'
        f'<table><thead><tr><th>Status</th><th>Question</th><th>Answer</th></tr></thead>'
        f'<tbody>{body}</tbody></table>'
        for title, body in sections
    )

    heading = " — ".join(x for x in (role, company) if x) or "Application review"
    stamp = datetime.now().strftime("%d %B %Y, %H:%M")

    page = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Review — {e(heading)}</title><style>{_CSS}</style></head>
<body><div class="sheet">
  <header class="masthead">
    <h1>{e(heading)}</h1>
    <p>{e(ats_name)} · filled {e(stamp)} · <a href="{e(url)}">{e(url[:90])}</a></p>
    <div class="tally">
      <div><b>{len(report_rows)}</b><span>fields found</span></div>
      <div><b>{len(clean)}</b><span>filled from profile</span></div>
      <div class="t-attend"><b>{len(items)}</b><span>need you</span></div>
      <div class="t-fail"><b>{len(failed)}</b><span>failed</span></div>
    </div>
  </header>
  {checklist_html}
  {tables}
  <p class="footnote">This tool never clicks submit. Work through the checklist
  above in the application window, then send it yourself.</p>
</div></body></html>"""

    slug = "-".join(w for w in f"{company}-{role}".lower().split() if w)[:50] or "application"
    path = config.REPORTS_DIR / f"{datetime.now():%Y%m%d-%H%M%S}-{slug}.html"
    path.write_text(page, encoding="utf-8")
    return path


def open_in_browser(path: Path) -> None:
    try:
        webbrowser.open(path.as_uri())
    except Exception:
        pass
