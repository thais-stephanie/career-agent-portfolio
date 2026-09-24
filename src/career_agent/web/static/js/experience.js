/**
 * experience.js -- Career Profile > Experience: the canonical career.
 *
 * One representation of a professional experience: `career_experience`,
 * read from `/api/career`. Each card reads as a person would write it --
 * role, company, period, a line about the job, highlights, skills -- and the
 * highlights are CONFIRMED statements only. A suggestion still waiting is
 * counted ("3 details to review"), never drawn as fact.
 *
 * Normal mode is for reading: the only controls are Edit and Remove, shown
 * on hover and whenever anything in the card has keyboard focus (always on
 * touch screens). "Edit profile" turns on edit mode: a banner says so, and
 * every card shows its actions. Editing happens in the card itself.
 *
 * What an edit changes, said plainly: company, role, dates and description
 * are organisation (undoable history); a highlight's wording is a new
 * revision of that statement (history kept); a new highlight is a statement
 * the person wrote, stored as such and never shown as a quote. Nothing here
 * changes Search Fit.
 */

import { el, button, field, select, replace } from './dom.js';
import { getLocale, t, tCount } from './i18n.js';
import * as api from './api.js';
import {
  chips, chipInput, inlineConfirm, keepFocus, lineList, periodLabel, toast, uid, workCard,
} from './ui.js';

const L = (key, params) => tCount(`xp.${key}`, params);
const KINDS = ['EMPLOYMENT', 'FREELANCE', 'VOLUNTEER', 'ACADEMIC', 'PERSONAL'];

/** A period control: an optional month and a year. Returns "YYYY" or "YYYY-MM". */
function periodInput(label, value, { disabled = false } = {}) {
  const [year, month] = String(value || '').split('-');
  const id = uid('xp-period');
  const monthSelect = select(
    [{ value: '', label: L('noMonth') }, ...Array.from({ length: 12 }, (_, i) => {
      const date = new Date(Date.UTC(2000, i, 1));
      return {
        value: String(i + 1).padStart(2, '0'),
        label: date.toLocaleDateString(getLocale(), { month: 'long', timeZone: 'UTC' }),
      };
    })],
    month || '', () => {},
    { ariaLabel: L('monthOf', { label }) },
  );
  const yearInput = el('input', {
    className: 'input xp-period__year',
    attrs: { type: 'text', inputmode: 'numeric', maxlength: '4', pattern: '(19|20)[0-9]{2}',
      id, placeholder: L('yearPlaceholder'), 'aria-label': L('yearOf', { label }) },
    props: { value: year || '' },
  });
  const wrap = el('div', { className: 'xp-period', attrs: { role: 'group', 'aria-label': label } }, [
    el('span', { className: 'field__label', text: label }),
    el('div', { className: 'xp-period__controls' }, [monthSelect, yearInput]),
  ]);
  wrap.setDisabled = (on) => {
    monthSelect.disabled = on;
    yearInput.disabled = on;
    wrap.classList.toggle('is-disabled', on);
  };
  wrap.setDisabled(disabled);
  wrap.value = () => {
    const y = yearInput.value.trim();
    if (!y) return null;
    return monthSelect.value ? `${y}-${monthSelect.value}` : y;
  };
  return wrap;
}

/**
 * The Experience tab. `onReview(experience)` opens the import review for the
 * details still waiting; `onChanged()` lets the page refresh its counts.
 */
export function experienceView({ onReview = null, onChanged = null } = {}) {
  const root = el('section', { className: 'xp', attrs: { 'aria-label': L('heading') } });
  let data = null;
  let editing = false;
  let open = null;   // experience id being edited, or 'new'

  /**
   * Fresh data from the server. While an editor is open it is not redrawn
   * (a language change or a refresh elsewhere would throw away what is being
   * typed); `force` is for this view's own changes.
   */
  async function load({ force = false } = {}) {
    data = await api.getCareer();
    if (open && !force) return;
    paint();
  }

  function setEditing(on) {
    editing = on;
    if (!on) open = null;
    paint();
    const target = root.querySelector(on ? '.xp-banner button' : '.xp-add');
    if (target) target.focus();
  }

  function paint() { keepFocus(root, draw); }

  function draw() {
    if (!data) return;
    const experiences = data.experiences || [];
    const waiting = experiences.reduce((sum, e) => sum + (e.waiting || 0), 0);
    replace(root, [
      editing ? el('div', { className: 'xp-banner', attrs: { role: 'status' } }, [
        el('span', { className: 'cw-eyebrow', text: L('editingEyebrow') }),
        el('span', { className: 'xp-banner__text', text: L('editingText') }),
        button(L('doneEditing'), () => setEditing(false), { className: 'btn btn--primary btn--small' }),
      ]) : null,
      open === 'new' ? editor(null) : addCard(),
      waiting && onReview ? el('p', { className: 'xp-waiting' }, [
        el('span', { text: L('waitingNote', { n: waiting }) }),
        button(L('continueReview'), () => onReview(null), { className: 'cw-link' }),
      ]) : null,
      experiences.length
        ? el('ol', { className: 'xp-list', attrs: { 'aria-label': L('listLabel') } },
          experiences.map((experience) => el('li', {}, [
            open === experience.id ? editor(experience) : card(experience),
          ])))
        : el('p', { className: 'xp-empty', text: L('empty') }),
    ]);
  }

  function addCard() {
    return el('button', {
      className: 'xp-add',
      attrs: { type: 'button' },
      on: { click: () => { open = 'new'; paint(); focusEditor(); } },
    }, [
      el('span', { className: 'xp-add__plus', attrs: { 'aria-hidden': 'true' }, text: '+' }),
      el('span', { className: 'xp-add__text' }, [
        el('strong', { text: L('add') }),
        el('span', { text: L('addHint') }),
      ]),
    ]);
  }

  function focusEditor() {
    requestAnimationFrame(() => {
      const first = root.querySelector('.xp-editor input');
      if (first) first.focus();
    });
  }

  // -- reading ------------------------------------------------------------
  function card(experience) {
    const role = experience.title || L('roleNotStated');
    const company = experience.company || L('companyNotStated');
    const confirmHost = el('div', { className: 'xp-card__confirm' });
    const editBtn = button(L('edit'), () => { open = experience.id; editing = true; paint(); focusEditor(); }, {
      className: 'cw-action', ariaLabel: L('editLabel', { role, company }),
    });
    const removeBtn = button(L('remove'), () => inlineConfirm(confirmHost, {
      message: L('removeQuestion'),
      detail: L('removeDetail'),
      confirmLabel: L('removeConfirm'),
      returnTo: removeBtn,
      onConfirm: async () => {
        const result = await api.changeCareer({ action: 'remove_experience', experience_id: experience.id });
        await load({ force: true });
        if (onChanged) onChanged();
        toast(L('removed', { role }), {
          undo: async () => {
            await api.undoCareer(result.event_id); await load({ force: true }); if (onChanged) onChanged();
          },
        });
      },
    }), { className: 'cw-action cw-action--danger', ariaLabel: L('removeLabel', { role, company }) });
    const node = workCard({
      className: 'xp-card',
      label: L('cardLabel', { role, company }),
      actions: [editBtn, removeBtn],
      always: editing,
      children: [
        el('header', { className: 'xp-card__head' }, [
          el('div', {}, [
            el('h3', { className: 'xp-card__role', text: role }),
            el('p', { className: 'xp-card__company', text: company }),
          ]),
          el('span', { className: 'xp-card__dates',
            text: periodLabel(experience.period_start, experience.period_end, experience.current_role) }),
        ]),
        experience.kind && experience.kind !== 'EMPLOYMENT'
          ? el('p', { className: 'xp-card__kind', text: t(`career.kind.${experience.kind}`) }) : null,
        experience.description ? el('p', { className: 'xp-card__description', text: experience.description }) : null,
        (experience.highlights || []).length
          ? el('ul', { className: 'xp-card__highlights' }, experience.highlights.map((h) => el('li', {
            className: h.origin === 'self' ? 'is-self' : '',
          }, [el('span', { text: h.text })])))
          : null,
        (experience.skills || []).length ? chips(experience.skills, { label: L('skillsLabel') }) : null,
        experience.waiting ? el('p', { className: 'xp-card__waiting' }, [
          el('span', { text: L('detailsWaiting', { n: experience.waiting }) }),
          onReview ? button(L('reviewThem'), () => onReview(experience), {
            className: 'cw-link', ariaLabel: L('reviewThemLabel', { n: experience.waiting, role }),
          }) : null,
        ]) : null,
        confirmHost,
      ],
    });
    return node;
  }

  // -- editing ------------------------------------------------------------
  function editor(experience) {
    const isNew = !experience;
    const id = (name) => `xp-${experience ? experience.id : 'new'}-${name}`;
    const input = (name, value, attrs = {}) => el('input', {
      className: 'input', attrs: { type: 'text', maxlength: '200', ...attrs },
      props: { value: value || '' },
    });
    const role = input('role', experience && experience.title);
    const company = input('company', experience && experience.company);
    const start = periodInput(L('start'), experience && experience.period_start);
    const current = el('input', { className: 'checkbox', attrs: { type: 'checkbox', id: id('current') },
      props: { checked: Boolean(experience && experience.current_role) } });
    const end = periodInput(L('end'), experience && experience.period_end, { disabled: current.checked });
    current.addEventListener('change', () => end.setDisabled(current.checked));
    const description = el('textarea', { className: 'input', attrs: { rows: '3', maxlength: '2000' },
      props: { value: (experience && experience.description) || '' } });
    const highlights = lineList({
      values: (experience ? experience.highlights : []).map((h) => ({ value: h.text, key: h.claim_key })),
      label: L('highlights'), addLabel: L('addHighlight'), placeholder: L('highlightPlaceholder'),
    });
    // Only skill statements can be removed here; a tool named on a highlight
    // leaves when that highlight changes, and says so rather than coming back.
    const skillKeys = (experience && experience.skill_keys) || {};
    const ownSkills = (experience ? experience.skills : []).filter((name) => name.toLowerCase() in skillKeys);
    const toolNames = (experience ? experience.skills : []).filter((name) => !(name.toLowerCase() in skillKeys));
    const skills = chipInput({ values: ownSkills, label: L('skills'), placeholder: L('skillPlaceholder') });
    const tools = toolNames.length
      ? el('p', { className: 'xp-editor__hint', text: L('toolsFromHighlights', { names: toolNames.join(', ') }) })
      : null;
    // What a failed save already stored, so pressing Save again does not
    // store it twice.
    let createdId = null;
    const createdLines = new Set();
    const createdSkills = new Set();
    const kind = select(KINDS.map((value) => ({ value, label: t(`career.kind.${value}`) })),
      (experience && experience.kind) || 'EMPLOYMENT', () => {}, { ariaLabel: L('changeType') });
    const error = el('p', { className: 'xp-editor__error', attrs: { role: 'alert' } });

    const save = button(L('save'), async () => {
      save.disabled = true;
      error.textContent = '';
      try {
        await commit();
        open = null;
        await load({ force: true });
        if (onChanged) onChanged();
        toast(L('saved'));
      } catch (problem) {
        error.textContent = problem.userMessage || problem.message;
        save.disabled = false;
      }
    }, { className: 'btn btn--primary' });

    async function commit() {
      const metadata = {
        company: company.value.trim() || null,
        title: role.value.trim() || null,
        period_start: start.value(),
        period_end: current.checked ? null : end.value(),
        current_role: current.checked,
        description: description.value.trim() || null,
        kind: kind.value,
      };
      const target = experience ? experience.id : createdId;
      const result = await api.changeCareer(target
        ? { action: 'edit', experience_id: target, metadata }
        : { action: 'create', keys: [], metadata });
      const experienceId = target || result.experience_id;
      if (isNew) createdId = experienceId;
      // Highlights: edited wording is a new revision; a removed line leaves
      // this experience (its statement and history stay); a new line is a
      // statement the person wrote.
      const before = new Map((experience ? experience.highlights : []).map((h) => [h.claim_key, h.text]));
      const rows = highlights.rows();
      const kept = new Set(rows.filter((r) => r.key).map((r) => r.key));
      for (const row of rows) {
        if (row.key && before.get(row.key) !== row.value) await api.editClaim(row.key, { text: row.value });
        if (!row.key && !createdLines.has(row.value)) {
          await api.createClaim({ claim_type: 'ACHIEVEMENT', text: row.value, experience_id: experienceId });
          createdLines.add(row.value);
        }
      }
      const removed = [...before.keys()].filter((key) => !kept.has(key));
      if (removed.length) await api.changeCareer({ action: 'move', keys: removed, experience_id: null });
      // Skills: a new chip is a skill statement; a removed chip that IS a
      // skill statement leaves this experience.
      const names = skills.values();
      const had = new Set((experience ? experience.skills : []).map((s) => s.toLowerCase()));
      for (const name of names) {
        if (!had.has(name.toLowerCase()) && !createdSkills.has(name.toLowerCase())) {
          await api.createClaim({ claim_type: 'SKILL', text: name, experience_id: experienceId });
          createdSkills.add(name.toLowerCase());
        }
      }
      const keep = new Set(names.map((s) => s.toLowerCase()));
      const dropped = Object.entries(skillKeys).filter(([name]) => !keep.has(name)).map(([, key]) => key);
      if (dropped.length) await api.changeCareer({ action: 'move', keys: dropped, experience_id: null });
    }

    const typeFold = el('details', { className: 'xp-editor__type' }, [
      el('summary', { text: L('changeType') }),
      field(id('kind'), L('typeLabel'), kind),
    ]);
    return el('form', {
      className: 'xp-editor cw-card',
      attrs: { 'aria-label': isNew ? L('newLabel') : L('editingLabel', { role: experience.title || '' }) },
      on: { submit: (event) => { event.preventDefault(); save.click(); } },
    }, [
      el('header', { className: 'xp-editor__head' }, [
        el('span', { className: 'cw-eyebrow', text: isNew ? L('newEyebrow') : L('editEyebrow') }),
        el('span', { className: 'xp-editor__note', text: L('editorNote') }),
      ]),
      el('div', { className: 'xp-editor__grid' }, [
        field(id('role'), L('role'), role),
        field(id('company'), L('company'), company),
        start,
        end,
        el('label', { className: 'xp-editor__current', attrs: { for: id('current') } }, [
          current, el('span', { text: L('current') }),
        ]),
      ]),
      field(id('description'), L('description'), description),
      el('div', { className: 'xp-editor__block' }, [
        el('div', { className: 'xp-editor__blockhead' }, [
          el('span', { className: 'field__label', text: L('highlights') }),
          el('span', { className: 'xp-editor__hint', text: L('highlightsHint') }),
        ]),
        highlights,
      ]),
      el('div', { className: 'xp-editor__block' }, [
        el('span', { className: 'field__label', text: L('skills') }),
        skills,
        tools,
      ]),
      typeFold,
      error,
      el('footer', { className: 'xp-editor__foot' }, [
        // A save that failed halfway may have stored part of it: reload.
        button(L('cancel'), () => { open = null; void load({ force: true }); }, { className: 'btn' }),
        save,
      ]),
    ]);
  }

  root.load = load;
  root.startEditing = () => {
    const first = (data && data.experiences && data.experiences[0]) || null;
    editing = true;
    open = first ? first.id : 'new';
    paint();
    focusEditor();
  };
  void load().catch((problem) => {
    replace(root, [el('p', { attrs: { role: 'alert' }, text: problem.userMessage || problem.message })]);
  });
  return root;
}
