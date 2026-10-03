/**
 * The person's name and contact details: what a resume puts at the top.
 *
 * BACKGROUND, NOT A SEARCH ANSWER. Saving here changes no score, no filter
 * and no "can you take it". The place is where a resume says you live; where
 * you can WORK is answered in What I'm looking for, and this form says so.
 */

import { el, button, field, replace } from './dom.js';
import { t } from './i18n.js';
import * as api from './api.js';
import { openDrawer, toast } from './ui.js';

//: In the order the form asks. `required` ones a resume cannot go out without.
const FIELDS = [
  { key: 'full_name', type: 'text', required: true, autocomplete: 'name' },
  { key: 'email', type: 'email', required: true, autocomplete: 'email' },
  { key: 'phone', type: 'tel', autocomplete: 'tel' },
  { key: 'city', type: 'text', autocomplete: 'address-level2' },
  { key: 'region', type: 'text', autocomplete: 'address-level1' },
  { key: 'country', type: 'text', autocomplete: 'country-name' },
  { key: 'linkedin_url', type: 'url' },
  { key: 'portfolio_url', type: 'url' },
  { key: 'github_url', type: 'url' },
];

/** "City, State, Country", from whatever parts were given. */
export function placeOf(contact) {
  return ['city', 'region', 'country'].map((key) => (contact || {})[key]).filter(Boolean).join(', ');
}

/**
 * Open the form. `onSaved(contact)` runs after a save; `country` prefills an
 * empty Country with the country already given for job searches.
 */
export function editContact({ contact = {}, country = '', onSaved = null } = {}) {
  const drawer = openDrawer({
    eyebrow: t('contact.eyebrow'),
    title: t('contact.title'),
    lede: t('contact.lede'),
  });
  const inputs = {};
  const rows = FIELDS.map((spec) => {
    const value = contact[spec.key] || (spec.key === 'country' ? country : '') || '';
    const input = el('input', {
      className: 'input',
      attrs: {
        type: spec.type,
        maxlength: '200',
        ...(spec.autocomplete ? { autocomplete: spec.autocomplete } : {}),
        ...(spec.required ? { 'aria-required': 'true' } : {}),
        placeholder: t(`contact.placeholder.${spec.key}`),
      },
      props: { value },
    });
    inputs[spec.key] = input;
    const label = spec.required ? t(`contact.field.${spec.key}`) : t('contact.optional', {
      label: t(`contact.field.${spec.key}`),
    });
    return field(`contact-${spec.key}`, label, input);
  });
  const error = el('p', { className: 'xp-editor__error', attrs: { role: 'alert' } });
  const save = button(t('contact.save'), async () => {
    const values = Object.fromEntries(FIELDS.map((spec) => [spec.key, inputs[spec.key].value.trim()]));
    const blank = FIELDS.find((spec) => spec.required && !values[spec.key]);
    if (blank) {
      error.textContent = t('contact.needed', { label: t(`contact.field.${blank.key}`) });
      inputs[blank.key].focus();
      return;
    }
    save.disabled = true;
    try {
      const saved = await api.patchContact(values);
      drawer.close();
      toast(t('contact.saved'));
      if (onSaved) onSaved(saved.contact);
    } catch (problem) {
      error.textContent = problem.userMessage || problem.message;
      save.disabled = false;
    }
  }, { className: 'btn btn--primary' });
  replace(drawer.body, [
    el('div', { className: 'contact-form' }, rows),
    el('p', { className: 'evp-note', text: t('contact.whereNote') }),
    error,
  ]);
  replace(drawer.footer, [button(t('contact.cancel'), () => drawer.close(), { className: 'btn' }), save]);
  return drawer;
}
