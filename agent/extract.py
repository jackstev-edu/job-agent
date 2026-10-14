"""
extract.py — reading the form.

WHAT THIS DOES: A job application page is around 200,000 characters of HTML,
almost all of it layout junk. This injects a small piece of JavaScript that
walks the page and returns just the questions — the label, the type of box,
and the dropdown choices.

WHY IT MATTERS: That shrinks the page from 200,000 characters to about 2,000,
which is the difference between an AI request that costs cents and works, and
one that costs dollars and gets lost.
"""

from __future__ import annotations

import re

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from playwright.sync_api import Page

# We stamp this attribute on every element we find, so we can locate it again
# later even on sites (Workday) that regenerate their real IDs constantly.
REF_ATTR = "data-ja-ref"

# Ashby asks Yes/No questions as a pair of <button>s in this container, with
# the real value in a hidden checkbox. Matched as a class token, not a
# substring: the buttons themselves carry "...-input-yesno-option".
BUTTON_GROUP = ".ashby-application-form-input-yesno"

# --- the injected JavaScript -------------------------------------------------
# This runs inside the page, in the browser, with full access to the DOM.
_SCAN_JS = r"""
(GROUP) => {
  const REF = 'data-ja-ref';
  let counter = 0;
  const fields = [];

  // Is this element actually on screen? Hidden inputs are noise.
  const isVisible = el => {
    const rect = el.getBoundingClientRect();
    const style = getComputedStyle(el);
    return rect.width > 0 && rect.height > 0
        && style.visibility !== 'hidden' && style.display !== 'none';
  };

  // Find the human-readable question for an input. Ordered hardest-first,
  // because the accessible name is far more reliable than nearby text.
  const labelFor = el => {
    if (el.getAttribute('aria-label')) return el.getAttribute('aria-label').trim();

    const labelledBy = el.getAttribute('aria-labelledby');
    if (labelledBy) {
      const text = labelledBy.split(/\s+/)
        .map(id => document.getElementById(id)?.innerText || '')
        .join(' ').trim();
      if (text) return text;
    }

    if (el.id) {
      const explicit = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
      if (explicit?.innerText.trim()) return explicit.innerText.trim();
    }

    const wrapping = el.closest('label');
    if (wrapping?.innerText.trim()) return wrapping.innerText.trim();

    // Last resort: climb a few levels looking for a legend or label sibling.
    let parent = el.parentElement;
    for (let i = 0; i < 4 && parent; i++, parent = parent.parentElement) {
      const nearby = parent.querySelector(':scope > legend, :scope > label, :scope > .label');
      if (nearby?.innerText.trim()) return nearby.innerText.trim();
    }

    return (el.getAttribute('placeholder') || el.getAttribute('name') || '').trim();
  };

  // Helper text under a field often contains the real question.
  const helpFor = el => {
    const describedBy = el.getAttribute('aria-describedby');
    if (!describedBy) return '';
    return describedBy.split(/\s+/)
      .map(id => document.getElementById(id)?.innerText || '')
      .join(' ').trim().slice(0, 300);
  };

  const stamp = el => { const r = 'f' + (counter++); el.setAttribute(REF, r); return r; };

  // Radio buttons come in groups; we collapse each group into one question.
  const seenGroups = new Set();

  // A question answered by clicking one of several buttons. Its only input is
  // a hidden checkbox, which isVisible rightly drops, so without this the
  // whole question vanished from the field list instead of reaching you.
  // Emitted as a radio group so the rest of the pipeline needs no new case;
  // `widget` tells filler.py to click the button rather than check an input.
  const buttonGroup = box => {
    if (!isVisible(box)) return;
    const optionText = b => (b.innerText || '').trim()
                         || (b.getAttribute('data-option') || '').trim();
    const buttons = [...box.querySelectorAll('button')]
      .filter(b => isVisible(b) && optionText(b));
    if (!buttons.length) return;

    // Same order as radio groups below, for the same reason: options first,
    // then the first option's tag doubles as the group's.
    const optionRefs = buttons.map(b => stamp(b));

    const title = box.closest('.ashby-application-form-field-entry')
      ?.querySelector('.ashby-application-form-question-title');
    const hidden = box.querySelector('input');
    const pressed = buttons.find(b => b.getAttribute('aria-pressed') === 'true');

    fields.push({
      ref: optionRefs[0],
      type: 'radio',
      widget: 'buttons',
      name: hidden?.name || '',
      label: (title?.innerText || labelFor(box)).trim().slice(0, 300),
      options: buttons.map(optionText),
      option_refs: optionRefs,
      // Ashby marks required on the question title's class, not the input.
      required: /required/i.test(title?.className || '') || !!hidden?.required
             || box.getAttribute('aria-required') === 'true',
      help_text: helpFor(box),
      current_value: pressed ? optionText(pressed) : '',
    });
  };

  // GROUP is in this selector so a button group lands in page order: the
  // container comes before the hidden checkbox inside it.
  document.querySelectorAll(`input, select, textarea, ${GROUP}`).forEach(el => {
    if (el.matches(GROUP)) { buttonGroup(el); return; }
    if (el.closest(GROUP)) return;      // its hidden checkbox; the group covers it

    const type = (el.type || el.tagName).toLowerCase();
    if (['hidden','submit','button','reset','image'].includes(type)) return;
    if (el.disabled || el.readOnly || !isVisible(el)) return;
    // react-select (Greenhouse) renders an invisible <input required
    // aria-hidden tabindex=-1> under each REQUIRED dropdown, purely for form
    // validation. It isn't a question, and without this it appears twice.
    // Deliberately not an opacity check: styled radios hide the real input
    // with opacity 0, and those must still be read.
    if (el.getAttribute('aria-hidden') === 'true' && el.tabIndex === -1) return;

    if (type === 'radio') {
      const groupKey = el.name || labelFor(el);
      if (seenGroups.has(groupKey)) return;
      seenGroups.add(groupKey);

      const peers = el.name
        ? [...document.querySelectorAll(`input[type=radio][name="${CSS.escape(el.name)}"]`)]
        : [el];

      // Stamp the options FIRST, then reuse the first option's tag as the
      // group's own tag. Stamping the group separately would re-stamp this
      // same element a moment later and leave the group's tag pointing at
      // nothing — which silently breaks every radio button on the page.
      const optionRefs = peers.map(p => stamp(p));

      fields.push({
        ref: optionRefs[0],
        type: 'radio',
        name: el.name || '',
        label: (el.closest('fieldset')?.querySelector('legend')?.innerText
                || labelFor(el)).trim().slice(0, 300),
        options: peers.map(p => labelFor(p)).filter(Boolean),
        option_refs: optionRefs,
        required: peers.some(p => p.required),
        help_text: helpFor(el),
      });
      return;
    }

    // React-select-style widgets stamp a plain <input> with role="combobox"
    // (or aria-autocomplete) instead of being a real <select>. If we tag that
    // as 'text' here, the later [role=combobox] pass skips it — it's already
    // stamped — and enrich_comboboxes never opens it to read the options.
    const isCustomCombobox = el.getAttribute('role') === 'combobox'
        || el.getAttribute('aria-autocomplete') === 'list';

    const tag = el.tagName.toLowerCase();
    const field = {
      ref: stamp(el),
      type: isCustomCombobox ? 'combobox'
          : tag === 'select' ? 'select' : tag === 'textarea' ? 'textarea' : type,
      name: el.name || el.id || '',
      label: labelFor(el).slice(0, 300),
      placeholder: el.getAttribute('placeholder') || '',
      required: !!el.required || el.getAttribute('aria-required') === 'true',
      help_text: helpFor(el),
      current_value: (el.value || '').slice(0, 200),
      maxlength: el.maxLength > 0 ? el.maxLength : null,
    };

    if (field.type === 'select') {
      field.options = [...el.options].map(o => o.text.trim()).filter(Boolean);
    }
    fields.push(field);
  });

  // Custom dropdowns that aren't real <select> elements. Ashby and Workday
  // both use these; they need click-then-type rather than a direct set.
  document.querySelectorAll('[role=combobox], [role=listbox]').forEach(el => {
    if (el.hasAttribute(REF) || !isVisible(el)) return;
    fields.push({
      ref: stamp(el),
      type: 'combobox',
      name: el.getAttribute('name') || el.id || '',
      label: (el.getAttribute('aria-label') || labelFor(el) || '').slice(0, 300),
      options: [],
      required: el.getAttribute('aria-required') === 'true',
      help_text: '',
      note: 'custom dropdown — filled by clicking and typing',
    });
  });

  return fields;
}
"""

# Fields that are on the page but have nothing to do with the application.
_NOISE = ("search", "cookie", "newsletter", "subscribe", "promo code",
          "discount", "language selector")


def extract_fields(page: "Page") -> list[dict]:
    """Return a tidy list of every fillable question on the page."""
    fields = page.evaluate(_SCAN_JS, BUTTON_GROUP)
    return [
        f for f in fields
        if not any(word in f"{f.get('label','')} {f.get('name','')}".lower()
                   for word in _NOISE)
    ]


def page_text(page: "Page", limit: int = 3000) -> str:
    """
    A trimmed snapshot of what's visible on the page.

    This gives the AI enough context to draft a sensible "why this role"
    answer. It is treated strictly as untrusted background — never as a
    source of facts about you, and never as instructions.
    """
    try:
        raw = page.evaluate("() => document.body.innerText")
    except Exception:
        return ""
    lines = [line.strip() for line in raw.splitlines() if line.strip()]
    return "\n".join(lines)[:limit]


def guess_role_and_company(page: "Page") -> tuple[str, str]:
    """Best-effort read of the job title and employer, for the log."""
    try:
        title = page.title() or ""
    except Exception:
        return "", ""
    # Portal page titles are usually "Job Title - Company" or "Company - Title".
    for sep in (" - ", " | ", " at ", " – "):
        if sep in title:
            left, right = title.split(sep, 1)
            return left.strip(), right.strip()
    return title.strip(), ""


# Rows a dropdown shows when it has nothing real to offer yet. Harvesting
# these as if they were schools or job titles is worse than harvesting nothing.
_PLACEHOLDER_ROW = re.compile(
    r"^(select|select\.{0,3}|choose|choose one|type to search|start typing|"
    r"search.{0,20}|loading.{0,3}|no (results|options|matches).{0,20}|"
    r"please select.{0,20}|-{2,}.*)$", re.I)

# The options of an open dropdown, wherever on the page the widget put them.
_OPTIONS_JS = r"""
() => {
  const sel = '[role=option], [role=listbox] li, [role=listbox] [data-value],'
            + 'ul[class*=menu i] li, li[class*=option i], div[class*=option i]';
  const hits = [...document.querySelectorAll(sel)]
      .filter(el => el.getBoundingClientRect().height > 0);
  // A wrapper can match the same selector as the rows inside it, and its
  // innerText is then the entire list glued into one string. Keep the
  // innermost matches only.
  return hits.filter(el => !hits.some(other => other !== el && el.contains(other)))
             .map(el => el.innerText.trim())
             .filter(t => t && t.length < 120)
             .slice(0, 400);
}
"""


# The row a search box shows when it has looked and found nothing. Worth
# telling apart from an empty menu: this one is the form saying no.
_NO_RESULTS_ROW = re.compile(r"^(no (results|options|matches|items)\b.{0,30}|"
                             r"nothing found.{0,10}|not found.{0,10})$", re.I)


def read_open_options(page: "Page") -> list[str]:
    """Whatever choices are on screen right now, de-duplicated and cleaned."""
    try:
        raw = page.evaluate(_OPTIONS_JS)
    except Exception:
        return []
    kept = [t for t in raw if not _PLACEHOLDER_ROW.match(t)]
    return list(dict.fromkeys(kept))[:300]


def menu_says_no_results(page: "Page") -> bool:
    """True when the open menu is explicitly reporting that nothing matched."""
    try:
        raw = page.evaluate(_OPTIONS_JS)
    except Exception:
        return False
    return any(_NO_RESULTS_ROW.match(t) for t in raw)


def wait_for_options(page: "Page", timeout_ms: int = 2500) -> list[str]:
    """
    Poll until an opened dropdown has rendered its list.

    WHY POLL: the old code waited a flat 450ms and read whatever had arrived.
    Portals that fetch their options over the network answer in 600-1500ms, so
    we were reading an empty menu and recording "this field has no choices".
    Polling costs nothing on a dropdown that opens instantly.
    """
    waited = 0
    while waited < timeout_ms:
        options = read_open_options(page)
        if options:
            # One more beat — long lists arrive in chunks.
            page.wait_for_timeout(180)
            return read_open_options(page) or options
        page.wait_for_timeout(120)
        waited += 120
    return []


def close_dropdown(page: "Page") -> None:
    """Escape, and if the widget ignores Escape, take the focus off it."""
    try:
        page.keyboard.press("Escape")
        page.wait_for_timeout(120)
        if read_open_options(page):
            # Deliberately not a click somewhere neutral: on a real portal
            # "somewhere neutral" can turn out to be a link.
            page.evaluate("() => document.activeElement?.blur()")
            page.wait_for_timeout(120)
    except Exception:
        pass


def enrich_comboboxes(page: "Page", fields: list[dict]) -> list[dict]:
    """
    Read the choices out of custom dropdowns.

    WHY THIS EXISTS: modern portals build dropdowns out of divs rather than a
    real <select>, so scanning the HTML finds the box but none of its options.
    That produced "no options were provided for this field" on real forms, and
    a field with no options can't be matched against your profile.

    Two things can go wrong, and they need opposite answers. A slow menu just
    needs waiting for. A type-to-search box — a school field with forty
    thousand universities behind it — will never show a list at all, and
    waiting longer won't change that. Those get marked `search_required` so
    the rest of the pipeline knows the answer is typed rather than picked.
    """
    for field in fields:
        if field.get("type") != "combobox" or field.get("options"):
            continue
        try:
            box = page.locator(f"[{REF_ATTR}='{field['ref']}']").first
            box.scroll_into_view_if_needed(timeout=3000)
            box.click(timeout=3000)
            options = wait_for_options(page)

            if not options:
                # Some widgets stay shut on a click and open on a key.
                page.keyboard.press("ArrowDown")
                options = wait_for_options(page, 1200)

            if options:
                field["options"] = options
            else:
                field["search_required"] = True
                field["note"] = ("type-to-search dropdown: it lists nothing until "
                                 "text is typed, so answer with the exact profile "
                                 "value and the filler will search for it")
        except Exception:
            continue
        finally:
            close_dropdown(page)
    return fields


# --- reading the form back, after you've corrected it ------------------------
# Deliberately narrower than the scan above. It reads only the refs it's
# given, which are the fields the scan saw as anything but a password box.
# At read time it also skips, without touching the value, any element that
# is now type=password (a "show password" toggle flips it back and forth) or
# has a password autocomplete hint. Same refusal as filler.py.
_SNAPSHOT_JS = r"""
(refs) => {
  const out = {};
  for (const ref of refs) {
    const el = document.querySelector(`[data-ja-ref="${CSS.escape(ref)}"]`);
    if (!el) continue;
    const t = (el.type || '').toLowerCase();
    if (t === 'password' || /password/i.test(el.getAttribute('autocomplete') || '')) continue;
    let v;
    if (t === 'checkbox' || t === 'radio') v = el.checked;
    else if (el.tagName === 'SELECT') v = el.selectedOptions[0]?.text.trim() ?? '';
    // Before 'value': a <button> has one, and it's always empty.
    else if (el.hasAttribute('aria-pressed')) v = el.getAttribute('aria-pressed') === 'true';
    else if ('value' in el) {
      v = el.value;
      // react-select (Greenhouse) shows the chosen option in a sibling; its
      // own <input> holds only the search text, empty once a choice is made.
      // Reading the input logged every answered dropdown as "you cleared it".
      if (!v && el.getAttribute('role') === 'combobox') {
        const shown = el.closest('[class*="control"]')
          ?.querySelector('[class*="single-value"], [class*="singleValue"]');
        if (shown) v = shown.innerText.trim();
      }
    }
    else v = el.innerText.trim();           // custom comboboxes
    out[ref] = v;
  }
  return out;
}
"""


def readable_refs(fields: list[dict]) -> list[str]:
    """The refs a snapshot may read: never a field the scan saw as a password."""
    refs: list[str] = []
    for f in fields:
        if (f.get("type") or "").lower() == "password":
            continue
        refs += f.get("option_refs") or [f["ref"]]
    return refs


def snapshot(page: "Page", fields: list[dict]) -> dict:
    """What's in each box right now, keyed by ref. Never reads password inputs."""
    return snapshot_from_raw(fields, page.evaluate(_SNAPSHOT_JS, readable_refs(fields)))


def snapshot_from_raw(fields: list[dict], raw: dict) -> dict:
    """Turn the in-page read (one entry per element) into one value per field."""
    out = {}
    for f in fields:
        if f.get("type") == "radio":
            pairs = zip(f.get("option_refs", []), f.get("options", []))
            out[f["ref"]] = next((o for r, o in pairs if raw.get(r) is True), None)
        elif f["ref"] in raw:
            out[f["ref"]] = raw[f["ref"]]
    return out


# --- the checkbox census, for the submit gate --------------------------------
# Every checkbox on the page, hidden ones included, with where its label came
# from. Read-only: nothing here clicks or ticks. Unlike the scan, it keeps
# hidden native checkboxes, because a consent box styled out of sight still
# counts. Labels are reported twice, as rendered (innerText) and as written
# (textContent), so text hidden with CSS shows up as a difference.
_CHECKBOX_CENSUS_JS = r"""
(GROUP) => {
  const norm = t => (t || '').replace(/\s+/g, ' ').trim();
  const visible = el => {
    const r = el.getBoundingClientRect(), s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
  };
  const texts = els => ({inner: els.map(e => e.innerText).join(' '),
                         content: els.map(e => e.textContent).join(' ')});
  const labelOf = el => {
    const aria = el.getAttribute('aria-label');
    if (aria && aria.trim()) return {source: 'aria-label', inner: aria, content: aria};
    const ids = (el.getAttribute('aria-labelledby') || '').split(/\s+/).filter(Boolean);
    const byId = ids.map(id => document.getElementById(id)).filter(Boolean);
    if (byId.length) return {source: 'aria-labelledby', ...texts(byId)};
    if (el.id) {
      const l = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
      if (l) return {source: 'for', ...texts([l])};
    }
    const wrap = el.closest('label');
    if (wrap) return {source: 'wrap', ...texts([wrap])};
    // A role=checkbox element is usually named by its own text.
    if (el.tagName !== 'INPUT' && norm(el.textContent)) return {source: 'self', ...texts([el])};
    return {source: 'none', inner: '', content: ''};
  };
  const described = el => (el.getAttribute('aria-describedby') || '').split(/\s+/)
      .map(id => document.getElementById(id)?.textContent || '').join(' ');

  return [...document.querySelectorAll('input[type=checkbox], [role=checkbox], [role=switch]')]
    .map(el => {
      const group = el.closest(GROUP);
      const lab = labelOf(el);
      return {
        ref: el.getAttribute('data-ja-ref'),
        checked: el.tagName === 'INPUT' ? !!el.checked : el.getAttribute('aria-checked') === 'true',
        visible: visible(el),
        label_source: lab.source,
        label_inner: norm(lab.inner),
        label_content: norm(lab.content),
        described: norm(described(el)),
        in_button_group: !!group,
        // The Ashby button group's field ref is its first button's, as stamped
        // by the scan, so the gate can find that question in the plan.
        group_ref: group?.querySelector('button')?.getAttribute('data-ja-ref') || null,
      };
    });
}
"""

# Invisible means zero-size, display:none or visibility:hidden, computed on
# the frame element itself. Zero-size is judged on the computed content box
# too: a width=0 height=0 iframe still draws its default border, so its
# bounding box alone would call it a visible 4x4.
_FRAMES_JS = r"""
() => [...document.querySelectorAll('iframe, frame')].map(f => {
  const r = f.getBoundingClientRect(), s = getComputedStyle(f);
  const sized = r.width > 0 && r.height > 0
             && parseFloat(s.width) > 0 && parseFloat(s.height) > 0;
  return {src: f.getAttribute('src') || '',
          visible: sized && s.visibility !== 'hidden' && s.display !== 'none'};
})
"""


def checkbox_census(page: "Page") -> list[dict]:
    """Every checkbox on the page and its state. Raises if the page can't be read."""
    return page.evaluate(_CHECKBOX_CENSUS_JS, BUTTON_GROUP)


def frame_inventory(page: "Page") -> list[dict]:
    """Every iframe's src and visibility: the gate can't see inside one."""
    return page.evaluate(_FRAMES_JS)
