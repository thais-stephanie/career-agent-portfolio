/**
 * choices.js -- one answer per row, for questions the setup and Settings share.
 *
 * Two answers in this product are really one question asked of each value:
 *
 *   ways of working   Remote, Hybrid, On-site:  prefer / fine / rather avoid / never show
 *   arrangements      employee, contractor, employer of record:  works / no preference / rather not
 *
 * They are stored as separate lists (`work_models`, `avoided_work_models`,
 * `excluded_work_models`; `contract_preferred`, `contract_unwanted`) that the
 * server keeps disjoint. Drawing them as one radio group per value makes a
 * contradiction impossible to enter, and drawing them with ONE module in both
 * places is what keeps the setup and Settings from disagreeing about what an
 * answer means.
 *
 * What each answer does is in docs/ONBOARDING.md. None of it is eligibility.
 */

import { el } from './dom.js';
import { t } from './i18n.js';

export const WORK_MODELS = ['REMOTE', 'HYBRID', 'ONSITE'];
export const WORK_MODEL_ANSWERS = ['prefer', 'fine', 'avoid', 'never'];
export const WORK_MODEL_FIELDS = ['work_models', 'avoided_work_models', 'excluded_work_models'];

export const ARRANGEMENTS = ['FULL_TIME_EMPLOYEE', 'CONTRACTOR_B2B', 'EOR'];
export const ARRANGEMENT_ANSWERS = ['yes', 'none', 'no'];
export const ARRANGEMENT_FIELDS = ['contract_preferred', 'contract_unwanted'];

const asList = (value) => (Array.isArray(value) ? value : []);

/** `{ work_models, avoided_work_models, excluded_work_models }` -> answer per model. */
export function workModelAnswers(values) {
  const answers = {};
  for (const model of WORK_MODELS) {
    if (asList(values.excluded_work_models).includes(model)) answers[model] = 'never';
    else if (asList(values.avoided_work_models).includes(model)) answers[model] = 'avoid';
    else if (asList(values.work_models).includes(model)) answers[model] = 'prefer';
    else answers[model] = 'fine';
  }
  return answers;
}

/** Answer per model -> the three stored lists, in the vocabulary order. */
export function workModelFields(answers) {
  const pick = (answer) => WORK_MODELS.filter((model) => answers[model] === answer);
  return {
    work_models: pick('prefer'),
    avoided_work_models: pick('avoid'),
    excluded_work_models: pick('never'),
  };
}

export function arrangementAnswers(values) {
  const answers = {};
  for (const kind of ARRANGEMENTS) {
    if (asList(values.contract_preferred).includes(kind)) answers[kind] = 'yes';
    else if (asList(values.contract_unwanted).includes(kind)) answers[kind] = 'no';
    else answers[kind] = 'none';
  }
  return answers;
}

export function arrangementFields(answers) {
  const pick = (answer) => ARRANGEMENTS.filter((kind) => answers[kind] === answer);
  return { contract_preferred: pick('yes'), contract_unwanted: pick('no') };
}

/**
 * One radio group per row, inside one fieldset.
 *
 * Radios rather than chips because each row has exactly one answer, and a
 * radio group is what says so to a keyboard (arrow keys move within a row, Tab
 * moves between rows) and to a screen reader (the row's name, then its
 * options, then which is chosen). Every option has a stable id,
 * `${id}-${row}-${answer}`, for anything that has to point at one answer.
 */
function matrix({ id, legend, describedBy, rows, answers, optionLabel, current, onChange }) {
  const chosen = { ...current };
  return el('fieldset', {
    className: 'choicematrix',
    attrs: { id, ...(describedBy ? { 'aria-describedby': describedBy } : {}) },
  }, [
    legend ? el('legend', { className: 'choicematrix__legend', text: legend }) : null,
    ...rows.map((row) => el('fieldset', {
      className: 'choicematrix__row',
      attrs: { role: 'radiogroup' },
    }, [
      el('legend', { className: 'choicematrix__name', text: row.label }),
      el('div', { className: 'choicematrix__options' }, answers.map((answer) => {
        const optionId = `${id}-${row.value}-${answer}`;
        return el('label', { className: 'choicematrix__option', attrs: { for: optionId } }, [
          el('input', {
            className: 'choicematrix__radio',
            attrs: { type: 'radio', name: `${id}-${row.value}`, id: optionId, value: answer },
            props: { checked: chosen[row.value] === answer },
            on: {
              change: () => {
                chosen[row.value] = answer;
                onChange({ ...chosen });
              },
            },
          }),
          el('span', { text: optionLabel(answer) }),
        ]);
      })),
    ].filter(Boolean))),
  ].filter(Boolean));
}

/** Remote, Hybrid, On-site: prefer / fine / rather avoid / never show. */
export function workModelMatrix({ id, legend = '', describedBy = '', values, onChange }) {
  return matrix({
    id,
    legend,
    describedBy,
    rows: WORK_MODELS.map((value) => ({ value, label: t(`workModel.${value}`) })),
    answers: WORK_MODEL_ANSWERS,
    optionLabel: (answer) => t(`workModel.answer.${answer}`),
    current: workModelAnswers(values),
    onChange: (answers) => onChange(workModelFields(answers)),
  });
}

/** Employee, contractor, employer of record: works / no preference / rather not. */
export function arrangementMatrix({ id, legend = '', describedBy = '', values, onChange }) {
  return matrix({
    id,
    legend,
    describedBy,
    rows: ARRANGEMENTS.map((value) => ({ value, label: t(`arrangement.${value}`) })),
    answers: ARRANGEMENT_ANSWERS,
    optionLabel: (answer) => t(`arrangement.answer.${answer}`),
    current: arrangementAnswers(values),
    onChange: (answers) => onChange(arrangementFields(answers)),
  });
}

/** "Prefer Remote · Avoid On-site", for a summary line. Empty when unanswered. */
export function workModelSummary(values) {
  const answers = workModelAnswers(values);
  return WORK_MODELS
    .filter((model) => answers[model] !== 'fine')
    .map((model) => t(`workModel.summary.${answers[model]}`, { model: t(`workModel.${model}`) }))
    .join(' · ');
}

export function arrangementSummary(values) {
  const answers = arrangementAnswers(values);
  return ARRANGEMENTS
    .filter((kind) => answers[kind] !== 'none')
    .map((kind) => t(`arrangement.summary.${answers[kind]}`, { kind: t(`arrangement.short.${kind}`) }))
    .join(' · ');
}
