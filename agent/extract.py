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

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from playwright.sync_api import Page

# We stamp this attribute on every element we find, so we can locate it again
# later even on sites (Workday) that regenerate their real IDs constantly.
REF_ATTR = "data-ja-ref"

# --- the injected JavaScript -------------------------------------------------
# This runs inside the page, in the browser, with full access to the DOM.
_SCAN_JS = r"""
() => {
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

  document.querySelectorAll('input, select, textarea').forEach(el => {
    const type = (el.type || el.tagName).toLowerCase();
    if (['hidden','submit','button','reset','image'].includes(type)) return;
    if (el.disabled || el.readOnly || !isVisible(el)) return;

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
    fields = page.evaluate(_SCAN_JS)
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


def enrich_comboboxes(page: "Page", fields: list[dict]) -> list[dict]:
    """
    Read the choices out of custom dropdowns.

    WHY THIS EXISTS: modern portals build dropdowns out of divs rather than a
    real <select>, so scanning the HTML finds the box but none of its options.
    That produced "no options were provided for this field" on real forms, and
    a field with no options can't be matched against your profile.

    The fix is to open each one, read what appears, and close it again. Costs
    about a second per dropdown and turns an unanswerable field into an
    answerable one.
    """
    for field in fields:
        if field.get("type") != "combobox" or field.get("options"):
            continue
        try:
            box = page.locator(f"[{REF_ATTR}='{field['ref']}']").first
            box.scroll_into_view_if_needed(timeout=3000)
            box.click(timeout=3000)
            page.wait_for_timeout(450)

            # Whatever popped open, anywhere on the page.
            options = page.evaluate("""() => {
                const sel = '[role=option], [role=listbox] li, [role=listbox] div[data-value],'
                          + 'ul[class*=menu i] li, div[class*=option i]';
                return [...document.querySelectorAll(sel)]
                    .filter(el => el.getBoundingClientRect().height > 0)
                    .map(el => el.innerText.trim())
                    .filter(t => t && t.length < 120)
                    .slice(0, 300);
            }""")

            if options:
                # De-duplicate but keep the original order.
                field["options"] = list(dict.fromkeys(options))
            page.keyboard.press("Escape")
            page.wait_for_timeout(200)
        except Exception:
            page.keyboard.press("Escape")
            continue
    return fields
