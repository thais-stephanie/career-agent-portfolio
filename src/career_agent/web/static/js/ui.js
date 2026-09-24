/**
 * ui.js -- the career workspace's shared interaction primitives.
 *
 * The approved prototype's patterns, built once so every screen behaves the
 * same way:
 *
 *   workCard         a bordered object with hover AND focus-within actions
 *   inlineConfirm    "Remove this?" inside the thing being removed, not a modal
 *   toast            one short result line, with Undo when it is really undoable
 *   openDrawer       a side panel (a full-screen sheet on a phone) that
 *                    returns focus where it came from
 *   sourceSnippet    "From your CV · line 14 [View source]", expandable
 *   periodLabel      "Apr 2022 to Apr 2023", "2015 to 2017", "Not stated"
 *
 * Nothing here is hover-only. Actions that appear on hover also appear when
 * anything inside the card has keyboard focus, and on touch screens (no
 * hover) they are always visible. All text is set as text.
 */

import { el, button, focusables, replace } from './dom.js';
import { getLocale, t } from './i18n.js';

let sequence = 0;
export const uid = (prefix = 'ui') => `${prefix}-${++sequence}`;

// ---------------------------------------------------------------------------
// dates
// ---------------------------------------------------------------------------

function monthName(value) {
  const [year, month] = String(value).split('-');
  if (!month) return year;
  const date = new Date(Date.UTC(Number(year), Number(month) - 1, 1));
  return date.toLocaleDateString(getLocale(), { month: 'short', year: 'numeric', timeZone: 'UTC' });
}

/** "Apr 2022 to Apr 2023", "2015 to 2017", "Mar 2022 to present", or not stated. */
export function periodLabel(start, end, current = false, written = null) {
  if (!start && !end && written) return written;
  if (!start && !end) return current ? t('ui.periodPresentOnly') : t('ui.datesNotStated');
  const from = start ? monthName(start) : t('ui.dateUnknown');
  if (current) return t('ui.periodCurrent', { start: from });
  if (!end) return from;
  return t('ui.period', { start: from, end: monthName(end) });
}

// ---------------------------------------------------------------------------
// badges and chips
// ---------------------------------------------------------------------------

/** A small uppercase label. `tone` is one of mint, lilac, yellow, pink, blue, plain. */
export function badge(text, tone = 'plain', { className = '' } = {}) {
  return el('span', { className: `cw-badge cw-badge--${tone} ${className}`.trim(), text });
}

export function chips(names, { tone = 'mint', onRemove = null, label = '' } = {}) {
  return el('ul', {
    className: 'cw-chips',
    attrs: label ? { 'aria-label': label } : {},
  }, names.map((name) => el('li', { className: `cw-chip cw-chip--${tone}` }, [
    el('span', { text: name }),
    onRemove ? button('×', () => onRemove(name), {
      className: 'cw-chip__remove',
      ariaLabel: t('ui.removeChip', { name }),
    }) : null,
  ])));
}

// ---------------------------------------------------------------------------
// a card with contextual actions
// ---------------------------------------------------------------------------

/**
 * A meaningful object: an experience, an evidence card, an import step.
 * `actions` are drawn in a bar on the card's top edge, shown on hover, on
 * focus-within, always in edit mode and always without hover (touch).
 */
export function workCard({ className = '', label = '', actions = [], children = [],
  tag = 'article', always = false } = {}) {
  const bar = actions.length ? el('div', {
    className: `cw-card__actions${always ? ' is-always' : ''}`,
  }, actions) : null;
  const body = el('div', { className: 'cw-card__body' }, children);
  const node = el(tag, {
    className: `cw-card ${className}`.trim(),
    attrs: label ? { 'aria-label': label } : {},
  }, [bar, body]);
  node.body = body;
  return node;
}

// ---------------------------------------------------------------------------
// confirmation in place, and undo
// ---------------------------------------------------------------------------

/**
 * "Remove this experience from your profile?" drawn INSIDE `host`.
 * Focus moves to Cancel (the safe answer) and returns to `returnTo` on cancel.
 */
export function inlineConfirm(host, { message, detail = '', confirmLabel, onConfirm,
  returnTo = null, tone = 'pink' }) {
  const cancel = button(t('ui.cancel'), () => close(true), { className: 'btn btn--small' });
  const confirmBtn = button(confirmLabel, async () => {
    confirmBtn.disabled = true;
    try {
      await onConfirm();
      close(false);
    } catch (error) {
      confirmBtn.disabled = false;
      note.textContent = error.userMessage || error.message;
    }
  }, { className: `btn btn--small ${tone === 'pink' ? 'btn--danger' : 'btn--primary'}` });
  const note = el('p', { className: 'cw-confirm__detail', text: detail });
  const strip = el('div', {
    className: `cw-confirm cw-confirm--${tone}`,
    attrs: { role: 'group', 'aria-label': message },
  }, [
    el('p', { className: 'cw-confirm__message', text: message }),
    note,
    el('div', { className: 'cw-confirm__actions' }, [cancel, confirmBtn]),
  ]);
  function close(cancelled) {
    strip.remove();
    if (cancelled && returnTo && document.contains(returnTo)) returnTo.focus();
  }
  host.append(strip);
  cancel.focus();
  return { close: () => close(true) };
}

const toastRegion = el('div', {
  className: 'cw-toasts',
  attrs: { role: 'status', 'aria-live': 'polite' },
});
let toastTimer = null;

/** One short line about what just happened. `undo` only when it truly reverses. */
export function toast(message, { undo = null, tone = 'ok', timeout = 6000 } = {}) {
  if (!document.body.contains(toastRegion)) document.body.append(toastRegion);
  clearTimeout(toastTimer);
  const node = el('div', { className: `cw-toast cw-toast--${tone}` }, [
    el('span', { text: message }),
    undo ? button(t('ui.undo'), async () => {
      node.remove();
      try {
        await undo();
        toast(t('ui.undone'));
      } catch (error) {
        toast(error.userMessage || error.message, { tone: 'bad' });
      }
    }, { className: 'cw-toast__undo' }) : null,
  ]);
  replace(toastRegion, [node]);
  toastTimer = setTimeout(() => node.remove(), timeout);
  return node;
}

// ---------------------------------------------------------------------------
// the drawer
// ---------------------------------------------------------------------------

/**
 * A side panel over the page; a full-screen sheet on narrow screens.
 * Returns `{ root, body, footer, close }`. Escape closes it, Tab stays
 * inside it, and focus goes back to what opened it.
 */
export function openDrawer({ eyebrow = '', title, lede = '', onClose = null }) {
  const opener = document.activeElement;
  const titleId = uid('cw-drawer-title');
  const body = el('div', { className: 'cw-drawer__body' });
  const footer = el('div', { className: 'cw-drawer__footer' });
  const closeBtn = button('×', () => close(), {
    className: 'cw-drawer__close', ariaLabel: t('ui.close'),
  });
  const panel = el('div', {
    className: 'cw-drawer__panel',
    attrs: { role: 'dialog', 'aria-modal': 'true', 'aria-labelledby': titleId, tabindex: '-1' },
  }, [
    el('header', { className: 'cw-drawer__head' }, [
      el('div', {}, [
        eyebrow ? el('p', { className: 'cw-eyebrow', text: eyebrow }) : null,
        el('h2', { className: 'cw-drawer__title', attrs: { id: titleId }, text: title }),
        lede ? el('p', { className: 'cw-drawer__lede', text: lede }) : null,
      ]),
      closeBtn,
    ]),
    body,
    footer,
  ]);
  const scrim = el('div', { className: 'cw-drawer__scrim', on: { click: () => close() } });
  const root = el('div', { className: 'cw-drawer' }, [scrim, panel]);
  root.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') { event.stopPropagation(); close(); return; }
    if (event.key !== 'Tab') return;
    const nodes = focusables(panel);
    if (!nodes.length) return;
    const first = nodes[0];
    const last = nodes[nodes.length - 1];
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
  });
  document.body.append(root);
  document.body.classList.add('has-cw-drawer');
  requestAnimationFrame(() => {
    const first = focusables(body)[0];
    (first || panel).focus();
  });
  function close() {
    if (!root.isConnected) return;
    root.remove();
    document.body.classList.remove('has-cw-drawer');
    if (onClose) onClose();
    if (opener && document.contains(opener)) opener.focus();
  }
  return { root, body, footer, close };
}

// ---------------------------------------------------------------------------
// where a statement came from
// ---------------------------------------------------------------------------

/**
 * "From your CV · line 14 [View source]", or "Written by you" for a
 * statement that cites nothing. A typed statement is never drawn as a quote.
 */
export function sourceSnippet({ origin = 'document', document: docName = '', where = '',
  line = null, quote = '', raw = '' } = {}) {
  if (origin === 'self' || !quote) {
    return el('p', { className: 'cw-source cw-source--self', text: t('ui.writtenByYou') });
  }
  const parts = [docName || t('ui.yourDocument'), where, line ? t('ui.line', { n: line }) : '']
    .filter(Boolean).join(' · ');
  const details = el('details', { className: 'cw-source' }, [
    el('summary', {}, [
      el('span', { className: 'cw-source__label', text: t('ui.source', { where: parts }) }),
      el('span', { className: 'cw-source__open', text: t('ui.viewSource') }),
    ]),
    el('blockquote', { className: 'cw-source__quote', text: quote }),
    raw && raw.trim() !== quote.trim()
      ? el('pre', { className: 'cw-source__raw', attrs: { 'aria-label': t('ui.asWritten') },
        text: raw })
      : null,
  ]);
  return details;
}

/**
 * An editable list of short lines: highlights. `values` are strings or
 * `{ value, key }`; `rows()` returns `{ key, value }` for what is left, so a
 * caller can tell an edited line from a new one and a removed one.
 */
export function lineList({ values = [], label, addLabel, placeholder = '' }) {
  const list = el('ol', { className: 'cw-lines', attrs: { 'aria-label': label } });
  function row(given = '') {
    const value = typeof given === 'string' ? given : given.value;
    const key = typeof given === 'string' ? '' : (given.key || '');
    const input = el('input', {
      className: 'input cw-lines__input',
      attrs: { type: 'text', maxlength: '2000', 'aria-label': label, placeholder },
      props: { value },
    });
    const item = el('li', { className: 'cw-lines__row', dataset: { key } }, [
      input,
      button('×', () => {
        const next = item.nextElementSibling || item.previousElementSibling;
        item.remove();
        const focus = next && next.querySelector('input');
        if (focus) focus.focus(); else add.focus();
      }, { className: 'cw-lines__remove', ariaLabel: t('ui.removeLine', { text: value || label }) }),
    ]);
    return item;
  }
  for (const value of values) list.append(row(value));
  const add = button(addLabel, () => {
    const item = row('');
    list.append(item);
    item.querySelector('input').focus();
  }, { className: 'cw-link' });
  const root = el('div', { className: 'cw-lines-wrap' }, [list, add]);
  root.rows = () => [...list.querySelectorAll('.cw-lines__row')]
    .map((item) => ({ key: item.dataset.key || '', value: item.querySelector('input').value.trim() }))
    .filter((r) => r.value);
  return root;
}

/** Skill chips with a type-and-Enter input. */
export function chipInput({ values = [], label, placeholder = '' }) {
  let current = [...values];
  const host = el('div', { className: 'cw-chipinput' });
  const input = el('input', {
    className: 'cw-chipinput__input',
    attrs: { type: 'text', maxlength: '60', 'aria-label': label, placeholder },
    on: {
      keydown: (event) => {
        if (event.key !== 'Enter' && event.key !== ',') return;
        event.preventDefault();
        const name = input.value.trim().replace(/,$/, '');
        if (name && !current.some((v) => v.toLowerCase() === name.toLowerCase())) {
          current.push(name);
          draw();
        }
        input.value = '';
      },
    },
  });
  function draw() {
    replace(host, [chips(current, {
      onRemove: (name) => { current = current.filter((v) => v !== name); draw(); input.focus(); },
      label,
    }), input]);
  }
  draw();
  host.values = () => current;
  return host;
}
