/**
 * onboarding.js -- the questions this product cannot answer for her.
 *
 * WHAT THIS IS NOT
 * ----------------
 * It is not a wizard that must be finished before the product works. Every
 * screen already works with none of these answered; what changes is what the
 * product can CONCLUDE. Without a hiring scope the geography gate can never
 * pass, so every posting stays UNRESOLVED; without a pay target the
 * compensation part of a score is dead weight. So the questions are asked
 * where their absence is visible -- on Home, beside the gap each one closes --
 * and they can be left unanswered forever.
 *
 * It is not a progress bar either. Section 20: there is no honest denominator
 * for a person. The flow says "question 2 of 4" because that is a fact about
 * THIS FLOW, which does have a length, and never a percentage of a career.
 *
 * ONE QUESTION AT A TIME, AND EACH ONE SAVES ON ITS OWN
 * -----------------------------------------------------
 * The Career Profile page shows every field at once, which is right for
 * somebody changing one thing. It is wrong for somebody who has never seen
 * any of them: sixteen controls with no order is where a first run stops.
 * Each answer here is written immediately through `PATCH /api/profile`, so
 * leaving halfway keeps what was answered rather than discarding it.
 *
 * NOTHING HERE INVENTS A VALUE
 * ----------------------------
 * Every control starts from what the configuration actually holds, and a
 * question whose field is already answered is not asked. A default that
 * looked like an answer would be this program putting words in her mouth
 * about where she may legally work.
 */

import { el, button, replace } from './dom.js';
import { t } from './i18n.js';
import { tagInput } from './tags.js';
import * as api from './api.js';

/**
 * Which editable field closes which gap, and what to ask about it.
 *
 * `gap` matches the names `career_agent/home.py::profile_gaps` returns. Two of
 * those gaps -- evidence and work -- are not single fields and cannot be
 * closed by a text box; they send the reader to the page that owns them,
 * which is honest and is the whole of what this flow can do about them.
 */
const QUESTIONS = [
  {
    gap: 'residence',
    fields: ['candidate_country'],
    titleKey: 'ask.residence',
    whyKey: 'ask.residenceWhy',
  },
  {
    gap: 'hiring_scopes',
    fields: ['eligible_scopes', 'eligible_countries'],
    titleKey: 'ask.scopes',
    whyKey: 'ask.scopesWhy',
  },
  {
    gap: 'compensation',
    fields: ['compensation_target', 'compensation_currency'],
    titleKey: 'ask.pay',
    whyKey: 'ask.payWhy',
  },
  {
    gap: 'evidence',
    goTo: 'evidence',
    titleKey: 'ask.evidence',
    whyKey: 'ask.evidenceWhy',
  },
  {
    gap: 'work',
    goTo: 'home',
    titleKey: 'ask.work',
    whyKey: 'ask.workWhy',
  },
];

/**
 * An editable field's label, in the reader's language.
 *
 * The server sends `label_key` beside the English it composed, so the same
 * table that validates the write also names it. The English is the fallback
 * for a field this build's catalogue has never heard of.
 */
function fieldLabel(row) {
  const key = row.label_key;
  if (!key) return row.label || row.field;
  const translated = t(key);
  return translated === key ? (row.label || row.field) : translated;
}


export function createOnboarding({ onGoTo = null, onSaved = null } = {}) {
  const root = el('div', { className: 'ask' });
  let questions = [];
  let at = 0;
  let editable = new Map();
  let pending = {};

  /**
   * Ask only about the gaps the server actually reported.
   *
   * `gaps` comes from `/api/home`, which computes them from the same
   * configuration the matcher reads. Asking about a field that is already
   * answered would be the product not having looked.
   */
  async function open(gaps) {
    at = 0;
    replace(root, [el('p', { className: 'ask__loading', text: t('app.loading') })]);
    try {
      const profile = await api.getProfile();
      editable = new Map((profile.editable || []).map((row) => [row.field, row]));
    } catch (error) {
      replace(root, [el('p', { className: 'state__msg', text: error.userMessage || error.message })]);
      return;
    }
    const wanted = new Set(gaps || []);
    questions = QUESTIONS.filter((question) => wanted.has(question.gap));
    draw();
  }

  function draw() {
    if (!questions.length) {
      replace(root, [el('p', { className: 'ask__done', text: t('ask.nothingToAsk') })]);
      return;
    }
    if (at >= questions.length) {
      replace(root, [
        el('p', { className: 'ask__done', text: t('ask.finished') }),
        button(t('ask.close'), () => close(), { className: 'btn' }),
      ]);
      return;
    }

    const question = questions[at];
    pending = {};
    replace(root, [
      el('p', {
        className: 'ask__step',
        // A fact about this flow, which has a length. Never a percentage of
        // a person: section 20 of the review, and the reason Home has no
        // completion score either.
        text: t('ask.step', { n: at + 1, of: questions.length }),
      }),
      el('h3', { className: 'ask__title', text: t(question.titleKey) }),
      el('p', { className: 'ask__why', text: t(question.whyKey) }),
      ...(question.goTo
        ? [button(t('ask.takeMeThere'), () => {
          close();
          if (onGoTo) onGoTo(question.goTo);
        }, { className: 'btn btn--primary' })]
        : (question.fields || []).map(control).filter(Boolean)),
      el('div', { className: 'ask__actions' }, [
        question.goTo
          ? null
          : button(t('ask.save'), () => save(), { className: 'btn btn--primary' }),
        button(t('ask.skip'), () => {
          at += 1;
          draw();
        }, { className: 'btn' }),
      ].filter(Boolean)),
      el('p', { className: 'ask__status', attrs: { role: 'status', 'aria-live': 'polite' } }),
    ]);
  }

  /** One editable field, drawn as the kind of control it actually is. */
  function control(field) {
    const row = editable.get(field);
    if (!row) return null;
    const id = `ask-${field}`;

    if (row.kind === 'enum_list') {
      const chosen = new Set(Array.isArray(row.value) ? row.value : []);
      const boxes = row.choices.map((choice) => {
        const boxId = `${id}-${choice}`;
        const input = el('input', {
          className: 'checkbox',
          attrs: { type: 'checkbox', id: boxId },
          props: { checked: chosen.has(choice) },
          on: {
            change: (event) => {
              if (event.target.checked) chosen.add(choice);
              else chosen.delete(choice);
              pending[field] = row.choices.filter((option) => chosen.has(option));
            },
          },
        });
        return el('label', { className: 'ask__choice', attrs: { for: boxId } }, [
          input,
          el('span', { text: t(`vocab.${choice}`) === `vocab.${choice}` ? choice : t(`vocab.${choice}`) }),
        ]);
      });
      return el('fieldset', { className: 'ask__field' }, [
        el('legend', { className: 'field__label', text: fieldLabel(row) }),
        el('div', { className: 'ask__choices' }, boxes),
      ]);
    }

    if (row.kind === 'country_list') {
      const tags = tagInput({
        id,
        label: fieldLabel(row),
        values: Array.isArray(row.value) ? row.value : [],
        placeholder: 'BR',
        normalise: (value) => value.toUpperCase().slice(0, 2),
        onChange: (values) => { pending[field] = values; },
        hint: t('ask.countriesHint'),
      });
      return tags.root;
    }

    const numeric = row.kind === 'money' || row.kind === 'percent';
    const input = el('input', {
      className: numeric ? 'input input--num' : 'input input--code',
      attrs: {
        type: numeric ? 'number' : 'text',
        id,
        ...(numeric ? { min: '0' } : { maxlength: row.kind === 'currency' ? '3' : '2' }),
        ...(numeric ? {} : { autocapitalize: 'characters', spellcheck: 'false' }),
      },
      props: { value: row.value === null || row.value === undefined ? '' : String(row.value) },
      on: {
        input: (event) => {
          const raw = event.target.value.trim();
          if (!raw) {
            delete pending[field];
            return;
          }
          pending[field] = numeric ? Number(raw) : raw.toUpperCase();
        },
      },
    });
    return el('div', { className: 'ask__field' }, [
      el('label', { className: 'field__label', text: fieldLabel(row), attrs: { for: id } }),
      input,
    ]);
  }

  async function save() {
    const status = root.querySelector('.ask__status');
    if (!Object.keys(pending).length) {
      status.textContent = t('ask.nothingTyped');
      return;
    }
    try {
      const result = await api.patchProfile(pending);
      // Every value the server accepted goes back into the table this flow
      // draws from, so pressing Back does not offer to re-answer a question
      // that is already answered.
      for (const [field, value] of Object.entries(pending)) {
        const row = editable.get(field);
        if (row) row.value = value;
      }
      if (onSaved) onSaved(result);
      at += 1;
      draw();
    } catch (error) {
      // The server's own sentence. It names the box: "Where you live is a
      // two-letter country code, such as BR" is actionable and "invalid
      // input" is not.
      status.textContent = error.userMessage || error.message;
    }
  }

  function close() {
    root.hidden = true;
    replace(root, []);
  }

  return { root, open, close };
}
