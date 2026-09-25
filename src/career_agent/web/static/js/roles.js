/**
 * roles.js -- "Any roles in mind?", for onboarding and Settings.
 *
 * Optional. The roles are SEARCH ANCHORS, not limits: they help Career Agent
 * ask job sources better questions, and they never hide, rule out or score a
 * posting. Jobs under other titles are still found. That is said ONCE, in
 * the callout under the suggestions.
 *
 * THE PERSON'S OWN ROLES ARE THE CONTROL. A full-width token field: type a
 * title and press Enter, and it becomes a chip; Backspace in the empty field
 * removes the last one; a title already there is ignored; it stops at the
 * limit and says so. The counter says how many of the limit are used.
 *
 * Titles from the Career Profile are offered as CARDS below, and are added
 * only when the person presses one: what somebody has done is not
 * automatically what they want next. A card and its chip are the same role,
 * so adding either shows both as added, and removing the chip un-adds the
 * card. A pressed suggestion is kept as a confirmed suggestion, so the two
 * kinds of words stay apart. "Role | context" titles are shown as the role
 * with its context under it; the stored experience is never changed.
 */

import { el, replace } from './dom.js';
import { t } from './i18n.js';
import { getRoleAnchors, saveRoleAnchors } from './api.js';

const fold = (text) => String(text || '').trim().replace(/\s+/g, ' ').toLocaleLowerCase();

/**
 * The editor. `autosave` saves on every change (Settings); without it the
 * caller saves with `save()` (onboarding's Continue). `onChange` hears the
 * number of roles after every change, so a caller can say what Continue
 * carries forward.
 */
export function roleAnchorsEditor({
  id = 'roles', autosave = false, initial = null, onChange = null, draft = null,
} = {}) {
  const root = el('div', { className: 'roles', dataset: { roles: id } });
  const status = el('p', { className: 'roles__status', attrs: { role: 'status', 'aria-live': 'polite' } });
  let data = null;
  let values = [];
  //: folded text -> 'user' | 'confirmed_suggestion'
  const sources = new Map();
  //: Saves run one at a time; a change made meanwhile is sent once the one in
  //: flight returns, so an older answer can never overwrite newer chips.
  let saving = null;
  let queued = false;

  // The parts that are redrawn. The text box itself is never rebuilt, so
  // focus and a half-typed title survive every change and every save.
  const counter = el('span', { className: 'roles__count num', attrs: { id: `${id}-count` } });
  const chips = el('span', { className: 'roles__chips' });
  const box = el('input', {
    className: 'roles__input',
    attrs: {
      type: 'text',
      id: `${id}-anchors`,
      autocomplete: 'off',
      autocapitalize: 'off',
      spellcheck: 'false',
      'aria-describedby': [`${id}-hint`, `${id}-count`].join(' '),
    },
  });
  const field = el('div', {
    className: 'roles__field',
    on: {
      // A click anywhere in the field (between the chips) is a click in it.
      click: (event) => {
        if (event.target === field || event.target === chips) box.focus();
      },
    },
  }, [chips, box]);
  const cards = el('div', { className: 'roles__suggestions' });
  const aliasLine = el('p', { className: 'roles__aliases' });

  const limit = () => (data && data.limits && data.limits.anchors) || 8;
  const maxText = () => (data && data.limits && data.limits.text) || 80;
  const has = (text) => values.some((value) => fold(value) === fold(text));

  async function load() {
    replace(root, [el('p', { className: 'roles__loading', text: t('app.loading') })]);
    try {
      data = initial || await getRoleAnchors();
      // A draft (unsaved roles kept by the caller across a redraw) wins over
      // what is saved, so a language switch never loses what was typed.
      const start = draft || data.anchors || [];
      values = start.map((anchor) => anchor.text);
      sources.clear();
      for (const anchor of start) sources.set(fold(anchor.text), anchor.source);
      build();
    } catch (error) {
      replace(root, [el('p', { className: 'state__msg', text: error.userMessage || error.message })]);
    }
  }

  function payload() {
    return {
      anchors: values.map((text) => ({ text, source: sources.get(fold(text)) || 'user' })),
    };
  }

  async function saveOnce() {
    const sent = payload();
    const next = await saveRoleAnchors(sent);
    // The server's answer updates what it alone knows (aliases, suggestions).
    // The chips stay as they are unless nothing changed since this was sent.
    data = next;
    if (JSON.stringify(sent) === JSON.stringify(payload())) {
      for (const anchor of next.anchors || []) sources.set(fold(anchor.text), anchor.source);
    }
    drawSuggestions();
    drawAliases();
  }

  async function save() {
    if (saving) {
      queued = true;
      return saving;
    }
    status.textContent = t('roles.saving');
    saving = (async () => {
      try {
        do {
          queued = false;
          await saveOnce();
        } while (queued);
        status.textContent = t('roles.saved');
        return true;
      } catch (error) {
        status.textContent = error.userMessage || error.message;
        return false;
      } finally {
        saving = null;
      }
    })();
    return saving;
  }

  function changed() {
    for (const key of [...sources.keys()]) {
      if (!values.some((value) => fold(value) === key)) sources.delete(key);
    }
    drawChips();
    drawSuggestions();
    drawAliases();
    if (onChange) onChange(values.length, payload().anchors);
    if (autosave) save();
  }

  /** Add one title. Returns whether it was added. */
  function add(text, source = 'user') {
    const clean = String(text || '').trim().replace(/\s+/g, ' ').slice(0, maxText());
    if (!clean || has(clean)) return false;
    if (values.length >= limit()) {
      status.textContent = t('roles.full', { n: limit() });
      return false;
    }
    values = [...values, clean];
    sources.set(fold(clean), source);
    status.textContent = '';
    changed();
    return true;
  }

  function remove(text) {
    values = values.filter((value) => fold(value) !== fold(text));
    changed();
  }

  box.addEventListener('keydown', (event) => {
    if (event.key === 'Enter' || event.key === ',') {
      // Enter inside a form would submit it; a comma would land in the box.
      event.preventDefault();
      if (add(box.value) || has(box.value)) box.value = '';
      return;
    }
    if (event.key === 'Backspace' && !box.value && values.length) {
      event.preventDefault();
      remove(values[values.length - 1]);
    }
  });
  // Leaving the box keeps what was typed as a role, as a person expects when
  // they type a title and press Continue.
  box.addEventListener('blur', () => {
    if (box.value.trim() && (add(box.value) || has(box.value))) box.value = '';
  });

  function drawChips() {
    replace(chips, values.map((value) => el('span', { className: 'roles__chip' }, [
      el('span', { className: 'roles__chiptext', text: value, attrs: { title: value } }),
      el('button', {
        className: 'roles__chipx',
        attrs: { type: 'button', 'aria-label': t('roles.remove', { role: value }) },
        on: {
          click: () => {
            remove(value);
            box.focus();
          },
        },
        text: '✕',
      }),
    ])));
    counter.textContent = t('roles.count', { n: values.length, max: limit() });
    box.placeholder = values.length ? t('roles.placeholderMore') : t('roles.placeholder');
  }

  function drawSuggestions() {
    const offered = [...((data && data.suggestions) || [])];
    // A suggestion chosen on an earlier visit arrives among the anchors, not
    // the suggestions; it is shown as an added card all the same.
    for (const anchor of (data && data.anchors) || []) {
      if (anchor.source === 'confirmed_suggestion'
        && !offered.some((item) => fold(item.text) === fold(anchor.text))) {
        offered.push({ text: anchor.text, context: anchor.context || '' });
      }
    }
    if (!offered.length) {
      replace(cards, []);
      return;
    }
    replace(cards, [
      el('h3', {
        className: 'roles__eyebrow',
        attrs: { id: `${id}-suggest-heading` },
        text: t('roles.suggestions'),
      }),
      el('p', { className: 'roles__suggestnote', text: t('roles.suggestionsNote') }),
      el('ul', {
        className: 'roles__grid',
        attrs: { 'aria-labelledby': `${id}-suggest-heading` },
      }, offered.map((item, index) => {
        const added = has(item.text);
        return el('li', {}, [
          el('button', {
            className: `roles__card${added ? ' is-added' : ''}`,
            attrs: {
              type: 'button',
              'aria-pressed': String(added),
              'aria-label': item.text,
              ...(item.context ? { 'aria-describedby': `${id}-suggest-${index}` } : {}),
            },
            on: {
              click: () => {
                if (has(item.text)) remove(item.text);
                else add(item.text, 'confirmed_suggestion');
                // The cards redraw; keep the keyboard on the same card.
                const again = cards.querySelectorAll('.roles__card')[index];
                if (again) again.focus();
              },
            },
          }, [
            el('span', { className: 'roles__cardtext' }, [
              el('span', { className: 'roles__cardrole', text: item.text }),
              item.context
                ? el('span', {
                  className: 'roles__cardcontext',
                  attrs: { id: `${id}-suggest-${index}` },
                  text: item.context,
                })
                : null,
            ].filter(Boolean)),
            el('span', {
              className: 'roles__cardtag',
              attrs: { 'aria-hidden': 'true' },
              text: added ? t('roles.added') : t('roles.addShort'),
            }),
          ]),
        ]);
      })),
    ]);
  }

  function drawAliases() {
    const aliases = ((data && data.aliases) || []).filter((alias) => has(alias.anchor));
    aliasLine.textContent = aliases.length
      ? t('roles.aliases', { list: aliases.map((alias) => alias.text).join(', ') })
      : '';
  }

  function build() {
    replace(root, [
      el('div', { className: 'roles__head' }, [
        el('label', { className: 'roles__label', attrs: { for: `${id}-anchors` }, text: t('roles.label') }),
        counter,
      ]),
      field,
      el('p', { className: 'roles__hint', attrs: { id: `${id}-hint` }, text: t('roles.hint') }),
      aliasLine,
      cards,
      el('div', { className: 'roles__callout' }, [
        el('img', {
          className: 'roles__callouticon',
          attrs: { src: './px/px-globe-16.png', alt: '', width: '16', height: '16' },
        }),
        el('p', {}, [
          el('strong', { text: t('roles.calloutLead') }),
          ` ${t('roles.calloutBody')}`,
        ]),
      ]),
      status,
    ]);
    drawChips();
    drawSuggestions();
    drawAliases();
    if (onChange) onChange(values.length);
  }

  load();
  return {
    root,
    save,
    //: The server's last answer, so a caller can reuse it without asking again.
    get data() {
      return data;
    },
    get count() {
      return values.length;
    },
    get dirty() {
      const saved = ((data && data.anchors) || []).map((anchor) => fold(anchor.text)).join('\n');
      return saved !== values.map(fold).join('\n');
    },
  };
}
