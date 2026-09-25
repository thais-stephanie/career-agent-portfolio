/**
 * roles.js -- "Do you have specific roles in mind?", for onboarding and Settings.
 *
 * Optional. The roles are SEARCH ANCHORS, not limits: they help Career Agent
 * ask job sources better questions, and they never hide, rule out or score a
 * posting. Jobs under other titles are still found.
 *
 * Titles from the Career Profile are offered as suggestions and are added only
 * when the person presses one: what somebody has done is not automatically
 * what they want next. A pressed suggestion is kept as a confirmed suggestion,
 * so the two kinds of words stay apart.
 */

import { el, button, replace } from './dom.js';
import { tagInput } from './tags.js';
import { t } from './i18n.js';
import { getRoleAnchors, saveRoleAnchors } from './api.js';

const fold = (text) => String(text || '').trim().replace(/\s+/g, ' ').toLocaleLowerCase();

/**
 * The editor. `autosave` saves on every change (Settings); without it the
 * caller saves with `save()` (onboarding's Continue).
 */
export function roleAnchorsEditor({ id = 'roles', autosave = false } = {}) {
  const root = el('div', { className: 'roles', dataset: { roles: id } });
  const status = el('p', { className: 'roles__status', attrs: { role: 'status', 'aria-live': 'polite' } });
  let data = null;
  let values = [];
  //: folded text -> 'user' | 'confirmed_suggestion'
  const sources = new Map();

  async function load() {
    replace(root, [el('p', { className: 'roles__loading', text: t('app.loading') })]);
    try {
      accept(await getRoleAnchors());
    } catch (error) {
      replace(root, [el('p', { className: 'state__msg', text: error.userMessage || error.message })]);
    }
  }

  function accept(next) {
    data = next;
    values = (next.anchors || []).map((anchor) => anchor.text);
    sources.clear();
    for (const anchor of next.anchors || []) sources.set(fold(anchor.text), anchor.source);
    draw();
  }

  function payload() {
    return {
      anchors: values.map((text) => ({ text, source: sources.get(fold(text)) || 'user' })),
    };
  }

  async function save() {
    status.textContent = t('roles.saving');
    try {
      accept(await saveRoleAnchors(payload()));
      status.textContent = t('roles.saved');
      return true;
    } catch (error) {
      status.textContent = error.userMessage || error.message;
      return false;
    }
  }

  function changed(next) {
    values = next;
    for (const key of [...sources.keys()]) {
      if (!values.some((value) => fold(value) === key)) sources.delete(key);
    }
    if (autosave) save();
  }

  function draw() {
    const limit = (data && data.limits && data.limits.anchors) || 8;
    const input = tagInput({
      id: `${id}-anchors`,
      label: t('roles.label'),
      values,
      hint: t('roles.hint', { n: limit }),
      placeholder: t('roles.placeholder'),
      normalise: (value) => value.trim().replace(/\s+/g, ' ').slice(0, (data && data.limits && data.limits.text) || 80),
      autocapitalize: 'off',
      onChange: (next) => changed(next.slice(0, limit)),
    });
    const suggestions = ((data && data.suggestions) || [])
      .filter((item) => !values.some((value) => fold(value) === fold(item.text)));
    const aliases = (data && data.aliases) || [];
    replace(root, [
      input.root,
      suggestions.length
        ? el('div', { className: 'roles__suggestions' }, [
          el('p', { className: 'roles__suggestLabel', text: t('roles.suggestions') }),
          el('ul', { className: 'roles__suggestList' }, suggestions.map((item) => el('li', {}, [
            button(t('roles.add', { role: item.text }), () => {
              if (values.length >= limit) {
                status.textContent = t('roles.full', { n: limit });
                return;
              }
              values = [...values, item.text];
              sources.set(fold(item.text), 'confirmed_suggestion');
              draw();
              if (autosave) save();
            }, { className: 'btn btn--quiet roles__suggest' }),
          ]))),
          el('p', { className: 'field__hint', text: t('roles.suggestionsNote') }),
        ])
        : null,
      aliases.length
        ? el('p', {
          className: 'roles__aliases',
          text: t('roles.aliases', { list: aliases.map((alias) => alias.text).join(', ') }),
        })
        : null,
      el('p', { className: 'field__hint', text: t('roles.notLimits') }),
      status,
    ].filter(Boolean));
  }

  load();
  return {
    root,
    save,
    get dirty() {
      const saved = ((data && data.anchors) || []).map((anchor) => fold(anchor.text)).join('\n');
      return saved !== values.map(fold).join('\n');
    },
  };
}
