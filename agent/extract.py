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

import itertools
import json
import re
import unicodedata

from typing import TYPE_CHECKING
from urllib.parse import urlparse

from . import sensitive

if TYPE_CHECKING:
    from playwright.sync_api import Page

# We stamp this attribute on every element we find, so we can locate it again
# later even on sites (Workday) that regenerate their real IDs constantly.
REF_ATTR = "data-ja-ref"

# Ashby asks Yes/No questions as a pair of <button>s in this container, with
# the real value in a hidden checkbox. Matched as a class token, not a
# substring: the buttons themselves carry "...-input-yesno-option".
BUTTON_GROUP = ".ashby-application-form-input-yesno"

# A box the agent must never read: once any reader sees it as a password, it's
# marked with this attribute, and every later reader refuses a marked box.
SECRET_ATTR = "data-ja-secret"

# Names, ids, labels and placeholders that say "password", in the languages
# portals commonly use. Page text only ever makes a box secret here: it can
# stop a read, never allow one.
SECRET_WORDS = (r"pass[\s_-]*word|passw|passwd|pwd|passcode|pass[\s_-]*phrase|"
                # Whole words only, so "shipping", "secretary" and "tokens"
                # don't match; a Secret security clearance isn't a secret.
                r"(?<![a-z])pin(?![a-z])|(?<![a-z])token(?![a-z])|"
                r"(?<![a-z])secret(?![a-z])(?![\s_-]*clearance)|security[\s_-]*answer|"
                r"kennwort|passwort|"
                r"contrase[ñn]a|senha|mot[\s_-]*de[\s_-]*passe|wachtwoord|l[öo]senord|"
                r"salasana|has[łl]o|heslo|jelsz[óo]|пароль|密码|密碼|パスワード|비밀번호|"
                r"סיסמה|كلمة[\s_-]*(?:ال)?مرور")

# The same test in the page, shared by the scan and the snapshot. It marks what
# it finds, so a later "show password" toggle doesn't make the box readable.
# The constants are baked in, so the submit hook in outcome.py, which embeds
# _SNAPSHOT_JS as a function, needs no extra arguments.
_SECRET_JS = (r"""
  const SECRET_ATTR = """ + json.dumps(SECRET_ATTR) + r""";
  const SECRET_RX = new RegExp(""" + json.dumps(SECRET_WORDS) + r""", 'iu');
  // "pinCode" -> "pin Code", "PINCode" -> "PIN Code": the whole-word
  // patterns need the word boundary a camelCase name hides.
  const camelSplit = t => t.replace(/([a-z])([A-Z])/g, '$1 $2')
                           .replace(/([A-Z])([A-Z][a-z])/g, '$1 $2');
  const isSecret = (el, label) => {
    if (el.hasAttribute(SECRET_ATTR)) return true;
    const text = [el.name, el.id, label, el.getAttribute('placeholder'),
                  el.getAttribute('aria-label'),
                  ...[...(el.labels || [])].map(l => l.innerText)]
                 .filter(Boolean).join(' ').normalize('NFKC');
    const hit = (el.type || '').toLowerCase() === 'password'
      || /password/i.test(el.getAttribute('autocomplete') || '')
      || SECRET_RX.test(text) || SECRET_RX.test(camelSplit(text));
    if (hit) el.setAttribute(SECRET_ATTR, '1');
    return hit;
  };
""")

# Everything that can show a field's value as text: form controls, editable
# regions, dropdown and listbox widgets, button-like pickers, sliders,
# selected rows, and the chosen-value, chip and tag displays of the common
# widget libraries (plus script and style). Text inside these never reaches
# the model, whether it comes in as page text or as a field's label or help.
# A deny-list: a library that shows a value in none of these still shows
# through (TODO 23).
_VALUE_SHOWN = ('input, textarea, select, option, button, '
                '[contenteditable]:not([contenteditable="false"]), [role=combobox], '
                '[role=listbox], [role=option], [role=textbox], [role=searchbox], '
                '[role=spinbutton], [role=button], [role=slider], [aria-selected="true"], '
                '[class*="singleValue"], [class*="single-value"], '
                '[class*="multiValue"], [class*="multi-value"], '
                '[class*="chip" i], [class~="tag" i], [class*="ant-tag"], '
                '[class*="ant-select-selection"], [class*="select2-selection"], '
                '[class*="choices__item"], [class*="search-choice"], '
                '[class*="chosen-single"], [class*="selected-item" i], '
                '[class*="selectedItem" i], [data-automation-id="selectedItem"], '
                '[data-automation-id="selectedItemList"], '
                'script, style, noscript, template')

# A label or help text built like page_text: what innerText would give, minus
# the field itself and anything _VALUE_SHOWN, so an aria-labelledby that names
# the field's own value display (the accessible name of a combobox includes
# its value) or a <label> wrapping an editable region can't carry a value.
_TEXT_OF_JS = (r"""
  const VALUE_SHOWN = """ + json.dumps(_VALUE_SHOWN) + r""";
  const textOf = (root, field) => {
    if (!root || root === field || (field && field.contains(root))
        || root.closest(VALUE_SHOWN)) return '';
    // innerText's rule: a rendered root drops unrendered text inside it; an
    // unrendered one (a display:none label named by aria-labelledby) gives
    // all of it.
    const rendered = root.getClientRects().length > 0;
    const out = [];
    const walk = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    for (let n = walk.nextNode(); n; n = walk.nextNode()) {
      let skip = false;
      for (let e = n.parentElement; e && e !== root; e = e.parentElement) {
        if (e === field || e.matches(VALUE_SHOWN)) { skip = true; break; }
      }
      if (skip) continue;
      if (rendered) {
        const p = n.parentElement;
        if (!p.getClientRects().length) continue;
        const s = getComputedStyle(p);
        if (s.visibility === 'hidden' || s.display === 'none') continue;
      }
      const t = n.textContent.replace(/\s+/g, ' ').trim();
      if (t) out.push(t);
    }
    return out.join(' ');
  };
""")

# Can clicking this element submit a form? A <button> with no type is
# type=submit, so a typeless button inside a form counts.
_SUBMITS_JS = r"""el => ['BUTTON', 'INPUT'].includes(el.tagName) && !!el.form
    && ['submit', 'image'].includes(String(el.type || '').toLowerCase())"""

# Does a dropdown's box hold any text? Only true or false leaves the page,
# never the text or its length. The password test runs first, and a box (or
# any box inside it) that's secret is never read at all: it counts as
# holding text, so it isn't opened.
_HOLDS_TEXT_JS = ("""
el => {
""" + _SECRET_JS + r"""
  const boxes = [el, ...el.querySelectorAll('input, textarea, [contenteditable]')];
  if (boxes.some(b => isSecret(b, ''))) return true;
  // Only what you could see and type into: a visible text box or editable
  // region. A hidden input's default value or a checkbox's "on" isn't
  // search text, and counting it kept the menu shut for nothing.
  const shown = b => b.getClientRects().length > 0
      && getComputedStyle(b).visibility !== 'hidden';
  const typed = b => b.tagName === 'TEXTAREA' || (b.tagName === 'INPUT'
      && ['text', 'search', 'email', 'tel', 'url', 'number'].includes(
           String(b.type || 'text').toLowerCase()));
  return boxes.some(b => shown(b) && (typed(b) ? String(b.value) !== ''
      : b.isContentEditable && b.textContent.trim() !== ''));
}
""")

# The page's copy of the stop and guard checks, built from sensitive.py's
# patterns and invisible characters (one source). Run on a field's label
# and help read whole, as innerText, before any value display is skipped;
# only true / false ever comes back.
_RAW_RULES_JS = (r"""
  const RAW_STOPS = """ + json.dumps(sensitive.RAW_PATTERNS["stops"]) + r""".map(p => new RegExp(p, 'iu'));
  const RAW_LEGAL = """ + json.dumps(sensitive.RAW_PATTERNS["legal"]) + r""".map(p => new RegExp(p, 'iu'));
  const INVIS = new RegExp(""" + json.dumps(sensitive.INVISIBLE_JS) + r""", 'gu');
  const EARLIER = new RegExp(""" + json.dumps(sensitive.EARLIER_INVISIBLE_JS) + r""", 'gu');
  // Every reading of a text, as sensitive.text_variants gives them.
  const readings = t => {
    t = String(t || '');
    const n = t.normalize('NFKC');
    return [t, n, n.replace(INVIS, ''), n.replace(INVIS, ' '), n.replace(EARLIER, '')];
  };
  const anyHit = (rxs, t) => { const rs = readings(t); return rxs.some(rx => rs.some(r => rx.test(r))); };
  const rawFlags = t => ({raw_stop: anyHit(RAW_STOPS, t), raw_legal: anyHit(RAW_LEGAL, t)});
""")

# --- the injected JavaScript -------------------------------------------------
# This runs inside the page, in the browser, with full access to the DOM.
_SCAN_JS = r"""
([GROUP, GEN]) => {
  const REF = 'data-ja-ref';
  const SUBMITS = """ + _SUBMITS_JS + r""";
""" + _SECRET_JS + _TEXT_OF_JS + _RAW_RULES_JS + r"""
  // Every ref from an earlier scan goes first, hidden boxes' included, so a
  // box this scan skips can't keep a ref that a new box is about to get. The
  // generation in each new ref means an old one can never equal it anyway.
  document.querySelectorAll(`[${REF}]`).forEach(e => e.removeAttribute(REF));
  window.__jaGen = GEN;
  // A label from a real <label> or aria-label, never the nearby-text guess.
  // With a name attribute, it's a stable identity; an id or a placeholder
  // alone isn't one. Built by textOf like every other label, so it holds no
  // value either.
  const explicitLabel = el => ((el.getAttribute('aria-label') || '').trim()
      || [...(el.labels || [])].map(l => textOf(l, el)).filter(Boolean).join(' '))
      .slice(0, 300);
  const stableIdentity = el => !!(el.getAttribute('name') || '').trim() || !!explicitLabel(el);
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
  // `text` reads one source: textOf for anything sent to the model, so none
  // of them can carry a value.
  const labelWith = (el, text) => {
    if (el.getAttribute('aria-label')) return el.getAttribute('aria-label').trim();

    const labelledBy = el.getAttribute('aria-labelledby');
    if (labelledBy) {
      const joined = labelledBy.split(/\s+/)
        .map(id => text(document.getElementById(id), el))
        .join(' ').trim();
      if (joined) return joined;
    }

    if (el.id) {
      const explicit = text(document.querySelector(`label[for="${CSS.escape(el.id)}"]`), el);
      if (explicit) return explicit;
    }

    const wrapping = text(el.closest('label'), el);
    if (wrapping) return wrapping;

    // Last resort: climb a few levels looking for a legend or label sibling.
    let parent = el.parentElement;
    for (let i = 0; i < 4 && parent; i++, parent = parent.parentElement) {
      const nearby = text(parent.querySelector(
          ':scope > legend, :scope > label, :scope > .label'), el);
      if (nearby) return nearby;
    }

    return (el.getAttribute('placeholder') || el.getAttribute('name') || '').trim();
  };
  const labelFor = el => labelWith(el, textOf);
  // The same label read whole, as innerText, for the password test only. It
  // never leaves the page: page text may only ever add secrecy, so the test
  // sees at least what it saw before labels were filtered.
  const rawLabelFor = el => labelWith(el, r => (r?.innerText || '').trim());
  // Help text read whole, for the raw-label check only.
  const rawHelpFor = el => (el.getAttribute('aria-describedby') || '').split(/\s+/)
      .map(id => document.getElementById(id)?.innerText || '').join(' ');

  // Helper text under a field often contains the real question.
  const helpFor = el => {
    const describedBy = el.getAttribute('aria-describedby');
    if (!describedBy) return '';
    return describedBy.split(/\s+/)
      .map(id => textOf(document.getElementById(id), el))
      .join(' ').trim().slice(0, 300);
  };

  // Each stamped element's tag, type and name, as the scan saw it. A read
  // refuses an element that no longer matches (a reused or swapped node).
  const sigs = {};
  const stamp = el => {
    const r = GEN + '-f' + (counter++);
    el.setAttribute(REF, r);
    sigs[r] = [el.tagName.toLowerCase(), String(el.type || '').toLowerCase(),
               el.getAttribute('name') || ''];
    return r;
  };

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
      label: (textOf(title, box) || labelFor(box)).trim().slice(0, 300),
      options: buttons.map(optionText),
      option_refs: optionRefs,
      // Ashby marks required on the question title's class, not the input.
      required: /required/i.test(title?.className || '') || !!hidden?.required
             || box.getAttribute('aria-required') === 'true',
      help_text: helpFor(box),
      current_value: pressed ? optionText(pressed) : '',
      ...rawFlags([title?.innerText, rawLabelFor(box), rawHelpFor(box)].join(' ')),
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
        label: (textOf(el.closest('fieldset')?.querySelector('legend'), el)
                || labelFor(el)).trim().slice(0, 300),
        options: peers.map(p => labelFor(p)).filter(Boolean),
        option_refs: optionRefs,
        required: peers.some(p => p.required),
        help_text: helpFor(el),
        ...rawFlags([el.closest('fieldset')?.querySelector('legend')?.innerText,
                     rawLabelFor(el), rawHelpFor(el)].join(' ')),
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
    // The scan reads no input's value at all: what's in a box is read only by
    // the snapshot, for refs Python has cleared. It does mark a box marked,
    // hinted or named as a password, since a "show password" toggle flips
    // type=password to text and the type alone can't be trusted.
    const label = labelFor(el);
    const secret = isSecret(el, label + ' ' + rawLabelFor(el));
    const field = {
      ref: stamp(el),
      type: isCustomCombobox ? 'combobox'
          : tag === 'select' ? 'select' : tag === 'textarea' ? 'textarea' : type,
      name: el.name || el.id || '',
      label: label.slice(0, 300),
      placeholder: el.getAttribute('placeholder') || '',
      required: !!el.required || el.getAttribute('aria-required') === 'true',
      help_text: helpFor(el),
      maxlength: el.maxLength > 0 ? el.maxLength : null,
      tag, dom_name: el.name || '', dom_id: el.id || '',
      explicit_label: explicitLabel(el),
      stable_identity: stableIdentity(el),
      ...rawFlags([rawLabelFor(el), rawHelpFor(el)].join(' ')),
    };
    if (secret) field.secret = true;

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
      stable_identity: stableIdentity(el),
      label: (el.getAttribute('aria-label') || labelFor(el) || '').slice(0, 300),
      options: [],
      required: el.getAttribute('aria-required') === 'true',
      help_text: '',
      note: 'custom dropdown — filled by clicking and typing',
      // A typeless <button> in a form is a submit button. Opening this
      // dropdown would click it, so it's handed to you and never clicked.
      submits: SUBMITS(el),
      ...rawFlags([el.getAttribute('aria-label'), rawLabelFor(el), rawHelpFor(el)].join(' ')),
    });
  });

  // Any password box, or password autocomplete hint, anywhere in the page,
  // hidden ones included: Python remembers the page as a credential step.
  const hasPassword = !!document.querySelector(
      'input[type=password i], [autocomplete*="password" i]');
  return {fields, sigs, url: location.href, has_password: hasPassword};
}
"""

# Fields that are on the page but have nothing to do with the application.
_NOISE = ("search", "cookie", "newsletter", "subscribe", "promo code",
          "discount", "language selector")


_GENERATIONS = itertools.count(1)
_current = {"gen": None}


class ReadGuard:
    """
    One run's memory of what may never be read, whatever the page says now:
    every key of every field ever seen as secret, and every page (host and
    path) where a password box or password autocomplete hint was seen. Pass
    the same guard to every scan in a run.
    """

    def __init__(self):
        self.secret_keys: set[tuple] = set()
        self.credential_pages: set[str] = set()


def page_key(url: str) -> str:
    """A page for the credential-step rule: host and path, no query or fragment."""
    p = urlparse(url or "")
    return f"{(p.hostname or '').lower()}{p.path or ''}"


def current_generation() -> str | None:
    """
    The generation of the latest scan, or None before the first one.

    Each scan's refs carry a generation ("g3-f2") unique for the whole run,
    even across navigations, since Python, not the page, counts them. Only
    the latest generation's refs are ever resolved; an older one is treated
    as missing. The generation is module-global: one counter and one
    "current" for the whole process, shared by every page, tab and frame.
    Scanning a second page or frame makes the first one's refs old, so they
    then read as missing (safe, never a wrong read). Callers scan one form
    target at a time and rescan it before reading it again.
    """
    return _current["gen"]


def is_current(ref) -> bool:
    """
    Is this ref from the latest scan, in the module-global generation (see
    current_generation)? Anything older resolves to nothing.
    """
    gen = _current["gen"]
    return bool(gen) and isinstance(ref, str) and ref.startswith(gen + "-")


def secret_keys_of(f: dict) -> set[tuple]:
    """
    Every key a secret field is remembered by: its name, its id and its
    label, each on its own. A re-render that changes one of them (a framework
    id, a "Show" button inside the label) still matches on another. A false
    match only hands an ordinary box to you. The label is the explicit one
    (a real <label> or aria-label), never the scan's nearby-text guess, which
    can be a neighbouring field's label.
    """
    tag = f.get("tag") or ""
    label = " ".join(unicodedata.normalize("NFKC", str(f.get("explicit_label") or "")).split()).casefold()
    keys = set()
    if f.get("dom_name"):
        keys.add(("name", tag, f["dom_name"]))
    if f.get("dom_id"):
        keys.add(("id", tag, f["dom_id"]))
    if label:
        keys.add(("label", tag, label))
    if not keys:
        # Nothing to know it by: once any anonymous box of this tag has been
        # secret, every anonymous box of this tag stays secret. Fails safe.
        keys.add(("anon", tag))
    return keys


def is_secret(f: dict) -> bool:
    """A field no reader or writer may take a value from."""
    return bool(f.get("secret")) or (f.get("type") or "").lower() == "password"


_SECRET_RX = re.compile(SECRET_WORDS, re.I)


def _camel_split(text: str) -> str:
    """'pinCode' -> 'pin Code', 'PINCode' -> 'PIN Code', as camelSplit does in the page."""
    text = re.sub(r"([a-z])([A-Z])", r"\1 \2", text)
    return re.sub(r"([A-Z])([A-Z][a-z])", r"\1 \2", text)


def says_secret(f: dict) -> bool:
    """
    The password-word test again, in Python, on what the scan returned: the
    name, id, label and placeholder, as written and camelCase-split. A second
    layer beside the page's own test, which page script can tamper with.
    """
    text = unicodedata.normalize("NFKC", " ".join(
        str(f.get(k) or "") for k in ("name", "dom_name", "dom_id", "label",
                                      "explicit_label", "placeholder")))
    return bool(_SECRET_RX.search(text) or _SECRET_RX.search(_camel_split(text)))


def extract_fields(page: "Page", guard: ReadGuard | None = None) -> list[dict]:
    """
    Return a tidy list of every fillable question on the page.

    `guard` is the run's ReadGuard. The page can strip its marker and a
    re-render replaces the element, so a field with any key in the guard
    stays secret whatever the page says now, and every field on a page the
    guard knows as a credential step is marked as being on one, so nothing
    on it is read. Without a guard (a lone scan), a fresh one is used, and
    it records this page's password boxes all the same.
    """
    guard = ReadGuard() if guard is None else guard
    secret_keys = guard.secret_keys
    gen = f"g{next(_GENERATIONS)}"
    scanned = page.evaluate(_SCAN_JS, [BUTTON_GROUP, gen])
    _current["gen"] = gen
    fields = scanned.get("fields") or []
    sigs = scanned.get("sigs") or {}
    here = page_key(str(scanned.get("url") or ""))
    if scanned.get("has_password"):
        guard.credential_pages.add(here)
    credential = here in guard.credential_pages
    for f in fields:
        f["credential_step"] = credential
        f["sigs"] = {r: list(sigs[r]) for r in (f.get("option_refs") or [f.get("ref")])
                     if r in sigs}
    sticky = []
    for f in fields:
        keys = secret_keys_of(f)
        if not is_secret(f) and says_secret(f):
            f["secret"] = True
            sticky += f.get("option_refs") or [f["ref"]]
        if is_secret(f):
            secret_keys.update(keys)
        elif keys & secret_keys:
            f["secret"] = True
            sticky.append(f["ref"])
            secret_keys.update(keys)        # so a later change to another key still matches
    if sticky:
        # The page forgot (or never knew); mark the element again, so every
        # in-page reader refuses it too.
        page.evaluate(_MARK_SECRET_JS, [sticky, SECRET_ATTR])
    return [
        f for f in fields
        if not any(word in f"{f.get('label','')} {f.get('name','')}".lower()
                   for word in _NOISE)
    ]


# The page's own visible text, for the model, with every place a field value
# can show up as text left out: anything inside a form or a form control, an
# editable region, a dropdown or listbox widget, or react-select's display of
# the chosen value. innerText alone already drops what's typed in inputs and
# textareas, but not an editable region's text or a widget's chosen option.
_PAGE_TEXT_JS = r"""
() => {
  const SKIP = 'form, ' + """ + json.dumps(_VALUE_SHOWN) + r""";
  const out = [];
  const walk = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  for (let n = walk.nextNode(); n; n = walk.nextNode()) {
    const el = n.parentElement;
    if (!el || el.closest(SKIP)) continue;
    if (!el.getClientRects().length) continue;              // not rendered
    const s = getComputedStyle(el);
    if (s.visibility === 'hidden' || s.display === 'none') continue;
    const t = n.textContent.replace(/\s+/g, ' ').trim();
    if (t) out.push(t);
  }
  return out.join(String.fromCharCode(10));
}
"""


def page_text(page: "Page", limit: int = 3000) -> str:
    """
    A trimmed snapshot of the page's own visible text, never a field's value.

    This gives the AI enough context to draft a sensible "why this role"
    answer. It is treated strictly as untrusted background — never as a
    source of facts about you, and never as instructions. It is sent to the
    model, so it leaves out every place a field value can appear as text
    (see _PAGE_TEXT_JS): no value read from a field reaches the model.
    `fill` calls this once, on the posting, before Apply is clicked or
    anything is typed: a later page can show answers outside any form.
    """
    try:
        raw = page.evaluate(_PAGE_TEXT_JS)
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
        if field.get("type") != "combobox" or field.get("options") or field.get("submits"):
            continue
        try:
            box = page.locator(f"[{REF_ATTR}='{field['ref']}']").first
            # Checked again at click time, not trusted from the scan.
            if box.evaluate(_SUBMITS_JS):
                field["submits"] = True
                continue
            # A box already holding text filters its menu by that text, and a
            # creatable one offers 'Create "<text>"': its rows would carry
            # the text to the model. Not opened; the in-page check returns
            # only true or false.
            if box.evaluate(_HOLDS_TEXT_JS):
                field["note"] = ("this dropdown already holds text, so its choices "
                                 "weren't read")
                continue
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
# given, which are the fields the scan didn't see as secret. At read time it
# also skips, without touching the value, any element that is now secret:
# marked, type=password, hinted or named as a password. Same refusal as
# filler.py.
_SNAPSHOT_JS = r"""
(items) => {
""" + _SECRET_JS + r"""
  const out = {};
  // A credential step, judged afresh at every read and never from page
  // state the page could set: any password box or password hint anywhere in
  // the document, and nothing at all is read.
  if (document.querySelector('input[type=password i], [autocomplete*="password" i]')) return out;
  for (const item of (Array.isArray(items) ? items : [])) {
    if (!Array.isArray(item) || item.length !== 4) continue;
    const [ref, tag, type, name] = item.map(x => String(x));
    // Only the latest scan's refs, and only one carried by exactly one
    // element: anything else is treated as missing, never resolved.
    if (!window.__jaGen || !ref.startsWith(window.__jaGen + '-')) continue;
    const all = document.querySelectorAll(`[data-ja-ref="${CSS.escape(ref)}"]`);
    if (all.length !== 1) continue;
    const el = all[0];
    const t = (el.type || '').toLowerCase();
    // The element must still be what the scan saw: same tag, type and name.
    if (el.tagName.toLowerCase() !== tag || String(el.type || '').toLowerCase() !== type
        || (el.getAttribute('name') || '') !== name) continue;
    if (isSecret(el, '')) continue;
    // A read that can return text needs a stable identity: a name, or a
    // label from a real <label> or aria-label. Ticked / pressed state is
    // only ever true or false, so it can't carry a typed value.
    const flag = t === 'checkbox' || t === 'radio'
        || (el.tagName !== 'SELECT' && t !== 'file' && el.hasAttribute('aria-pressed'));
    const stable = !!(el.getAttribute('name') || '').trim()
        || !!(el.getAttribute('aria-label') || '').trim()
        || [...(el.labels || [])].some(l => (l.innerText || '').trim());
    if (!flag && !stable) continue;
    let v;
    if (t === 'checkbox' || t === 'radio') v = el.checked;
    // A file box: the chosen files' names, in order, and nothing else. The
    // count is the list's length. File contents are never opened.
    else if (t === 'file') v = Array.from(el.files || [], f => String(f.name));
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


def _flag_field(f: dict) -> bool:
    """A field whose read is only ever true / false: ticked or pressed state."""
    return (f.get("type") or "").lower() in ("checkbox", "radio") or f.get("widget") == "buttons"


def readable_refs(fields: list[dict]) -> list[str]:
    """
    The refs a snapshot may read. Default deny: a field is read only if all
    of these hold, and _SNAPSHOT_JS checks the same in the page.
      1. its ref is from the latest scan (the page also checks it's carried
         by exactly one element);
      2. it isn't secret by any layer (type, password hint, page marker,
         the password pattern, any sticky key);
      3. a read that can return text has a stable identity: a name, or a
         label from a real <label> or aria-label (ticked / pressed state,
         only ever true or false, is exempt);
      4. it isn't on a credential step: a page (host and path) where this
         run has seen a password box or password hint. Nothing there is
         read. The page also checks afresh at read time, and reads nothing
         while any password box or hint is in the document.
    The page also refuses an element whose tag, type or name differs from
    what the scan saw. Anything else is unreadable, which stops a zero-edit
    claim.
    """
    refs: list[str] = []
    for f in fields:
        if is_secret(f) or f.get("credential_step") is not False:
            continue
        if not _flag_field(f) and f.get("stable_identity") is not True:
            continue
        refs += [r for r in (f.get("option_refs") or [f["ref"]]) if is_current(r)]
    return refs


def read_items(fields: list[dict]) -> list[list[str]]:
    """
    What _SNAPSHOT_JS is given: each readable ref with the tag, type and name
    the scan saw for it. A ref with no recorded signature isn't read.
    """
    sigs = {r: s for f in fields for r, s in (f.get("sigs") or {}).items()}
    return [[r, *sigs[r]] for r in readable_refs(fields)
            if isinstance(sigs.get(r), list) and len(sigs[r]) == 3]


_MARK_SECRET_JS = r"""
([refs, attr]) => refs.forEach(r =>
  document.querySelectorAll(`[data-ja-ref="${CSS.escape(r)}"]`)
    .forEach(e => e.setAttribute(attr, '1')))
"""


def snapshot(page: "Page", fields: list[dict]) -> dict:
    """What's in each box right now, keyed by ref, under the read policy."""
    return snapshot_from_raw(fields, page.evaluate(_SNAPSHOT_JS, read_items(fields)))


def snapshot_from_raw(fields: list[dict], raw: dict) -> dict:
    """
    Turn the in-page read (one entry per element) into one value per field.
    A radio or button group is recorded only if every one of its options was
    read; otherwise it's left out, so it counts as unreadable, never as
    "nothing chosen" (the refused option may be the chosen one).
    """
    out = {}
    for f in fields:
        if f.get("type") == "radio":
            option_refs = f.get("option_refs") or []
            if not option_refs or not all(r in raw for r in option_refs):
                continue
            pairs = zip(option_refs, f.get("options", []))
            out[f["ref"]] = next((o for r, o in pairs if raw.get(r) is True), None)
        elif f["ref"] in raw:
            out[f["ref"]] = raw[f["ref"]]
    return out


def partly_read(fields: list[dict], raw: dict) -> bool:
    """
    Did the read get some, but not all, of a radio or button group's
    options? The group is left out of the snapshot, and the capture is
    inexact: the run isn't scored as a match.
    """
    for f in fields:
        if f.get("type") == "radio":
            got = [r in raw for r in f.get("option_refs") or []]
            if any(got) and not all(got):
                return True
    return False


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
