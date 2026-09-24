/**
 * import_review.js -- one guided review for every kind of import.
 *
 * Experiences first, the way a person remembers a career: "Experience 2 of 6
 * -- Frontend Engineer at Loggi, already in your profile, 2 new details".
 * Then skills, certifications and education, then a summary.
 *
 * Each experience is compared with the profile and says what it is, in
 * words: New, Already in your profile, N new details found, Check the dates
 * (both versions shown, one click to choose), Needs your help. Inside it,
 * every statement is reviewed on its own -- Confirm, Edit, Not sure yet,
 * Reject -- with the line it came from right beside it.
 *
 * WHAT THIS SCREEN DOES NOT DO: confirm anything in bulk (there is no
 * "confirm everything"), wait until the end to save (each answer is saved as
 * it is given, and the review resumes where it stopped), or change Search
 * Fit (confirming changes what application preparation can draw on).
 */

import { el, button, field, replace } from './dom.js';
import { tCount } from './i18n.js';
import * as api from './api.js';
import { formatDate } from './format.js';
import { badge, periodLabel, sourceSnippet, toast, uid } from './ui.js';

const L = (key, params) => tCount(`imp.${key}`, params);
const STEPS = ['experiences', 'skills', 'certifications', 'education', 'summary'];
/** Still wanting an answer. */
const isOpen = (item) => ['waiting', 'unsure'].includes(item.state);
/** An experience with something left to do: unanswered details, or not yet placed. */
const unsettled = (entry) => entry.counts.waiting || entry.state === 'NEW';

/** The dates a document gave an experience, as words. */
function documentPeriod(entry) {
  const p = entry.period;
  const start = p.start || (p.start_year && String(p.start_year));
  const end = p.end || (p.end_year && String(p.end_year));
  return periodLabel(start, end, p.current, p.text);
}

const TONE = {
  NEW: 'blue',
  EXISTING_UNCHANGED: 'plain',
  EXISTING_WITH_NEW_DETAILS: 'mint',
  DATE_CONFLICT: 'yellow',
  STRUCTURE_UNCERTAIN: 'pink',
};

/**
 * The review of one document. `kind` is `cv` or `package`. `focusEntry` opens
 * a particular experience first; `onDone()` returns to the documents list.
 */
export function importReview({ kind, id, focusEntry = null, onDone, onChanged = null }) {
  const root = el('section', { className: 'imp', attrs: { 'aria-label': L('heading') } });
  let model = null;
  let step = 'experiences';
  let index = 0;
  let focusKey = null;
  const status = el('p', { className: 'imp-status', attrs: { role: 'status', 'aria-live': 'polite' } });

  async function load() {
    model = await api.getDocumentReview(kind, id);
    if (focusEntry) {
      const at = model.experiences.findIndex((e) => e.key === focusEntry || e.match?.experience_id === focusEntry);
      if (at >= 0) index = at;
      focusEntry = null;
    } else {
      const firstOpen = model.experiences.findIndex((e) => e.counts.waiting || e.state === 'NEW');
      index = firstOpen >= 0 ? firstOpen : 0;
      if (!model.experiences.length) step = nextStep('experiences');
    }
    paint();
  }

  async function act(promise, message = '') {
    try {
      const next = await promise;
      model = next.experiences ? next : await api.getDocumentReview(kind, id);
      if (message) {
        status.textContent = message;
        toast(message);
      }
      paint();
      if (onChanged) onChanged();
      return next;
    } catch (problem) {
      status.textContent = problem.userMessage || problem.message;
      paint();
      return null;
    }
  }

  function nextStep(from) {
    const order = STEPS.slice(STEPS.indexOf(from) + 1);
    return order.find((name) => name === 'summary' || (model.sections[name] || []).length) || 'summary';
  }

  // =====================================================================
  function paint() {
    replace(root, [
      el('div', { className: 'imp-layout' }, [rail(), el('div', { className: 'imp-main' }, [
        status,
        !model.editable ? el('p', { className: 'imp-readonly', text: model.status === 'archived'
          ? L('archivedNote') : L('replacedNote') }) : null,
        step === 'experiences' ? experienceStep()
          : step === 'summary' ? summaryStep() : listStep(step),
      ])]),
    ]);
    requestAnimationFrame(() => {
      const item = focusKey && [...root.querySelectorAll('.imp-item')].find((n) => n.dataset.key === focusKey);
      const target = item && item.querySelector('.imp-item__actions button');
      if (target) { target.focus(); focusKey = null; return; }
      const heading = root.querySelector('.imp-main h3');
      if (heading) heading.focus({ preventScroll: true });
    });
  }

  function rail() {
    const s = model.summary;
    const stepRow = (name, number, count) => el('li', {
      className: `imp-rail__step${step === name ? ' is-current' : ''}`,
    }, [
      button('', () => { step = name; paint(); }, {
        className: 'imp-rail__button',
        attrs: step === name ? { 'aria-current': 'step' } : {},
      }),
    ]);
    const steps = [
      ['experiences', 1, `${s.experiences - model.experiences.filter(unsettled).length}/${s.experiences}`],
      ['skills', 2, String((model.sections.skills || []).length)],
      ['certifications', 3, String((model.sections.certifications || []).length)],
      ['education', 4, String((model.sections.education || []).length)],
      ['summary', 5, ''],
    ];
    const list = el('ol', { className: 'imp-rail__steps' }, steps.map(([name, number, count]) => {
      const row = stepRow(name, number, count);
      const done = name === 'experiences'
        ? model.experiences.every((e) => !e.counts.waiting && e.state !== 'NEW')
        : name !== 'summary' && (model.sections[name] || []).every((i) => !['waiting', 'unsure'].includes(i.state));
      row.firstChild.append(
        el('span', { className: `imp-rail__num${done ? ' is-done' : ''}`, attrs: { 'aria-hidden': 'true' },
          text: done ? '✓' : String(number) }),
        el('span', { className: 'imp-rail__name', text: L(`step.${name}`) }),
        ...(count ? [el('span', { className: 'imp-rail__count', text: count })] : []),
      );
      if (name === 'experiences' && step === 'experiences' && model.experiences.length) {
        row.append(el('ol', { className: 'imp-rail__xps' }, model.experiences.map((entry, at) => {
          const settled = !entry.counts.waiting && entry.state !== 'NEW';
          return el('li', {}, [button(entry.role || entry.company || entry.label || L('unnamed'), () => {
            index = at; paint();
          }, {
            className: `imp-rail__xp${at === index ? ' is-current' : ''}${settled ? ' is-done' : ''}`,
            attrs: at === index ? { 'aria-current': 'true' } : {},
          })]);
        })));
      }
      return row;
    }));
    return el('aside', { className: 'imp-rail cw-card', attrs: { 'aria-label': L('railLabel') } }, [
      el('div', { className: 'imp-rail__doc' }, [
        el('p', { className: 'imp-rail__name', text: model.name }),
        el('p', { className: 'imp-rail__meta', text: L('readOn', { date: formatDate(model.created_at) }) }),
      ]),
      list,
      el('p', { className: 'imp-rail__saved', text: L('savedAsYouGo') }),
    ]);
  }

  // -- experiences ----------------------------------------------------------
  function experienceStep() {
    const total = model.experiences.length;
    if (!total) {
      return el('div', { className: 'imp-card cw-card' }, [
        el('h3', { attrs: { tabindex: '-1' }, text: L('noExperiences') }),
        el('p', { text: L('noExperiencesBody') }),
        el('div', { className: 'imp-foot' }, [button(L('continue'), () => { step = nextStep('experiences'); paint(); },
          { className: 'btn btn--primary' })]),
      ]);
    }
    const entry = model.experiences[Math.min(index, total - 1)];
    const role = entry.role || L('roleNotStated');
    const company = entry.company || entry.label || L('companyNotStated');
    const dots = el('ol', { className: 'imp-dots', attrs: { 'aria-hidden': 'true' } },
      model.experiences.map((e, at) => el('li', { className: `imp-dot${at === index ? ' is-current' : ''}${
        !e.counts.waiting && e.state !== 'NEW' ? ' is-done' : ''}` })));
    const waitingItems = entry.items.filter((i) => ['waiting', 'unsure'].includes(i.state));
    const isLast = index >= total - 1;
    const next = () => {
      if (!isLast) { index += 1; paint(); return; }
      step = nextStep('experiences');
      paint();
    };
    return el('div', { className: 'imp-experience' }, [
      el('div', { className: 'imp-counter' }, [
        el('span', { className: 'cw-eyebrow', text: L('experienceOf', { n: index + 1, total }) }),
        dots,
      ]),
      el('article', { className: `imp-card cw-card imp-card--${TONE[entry.state] || 'plain'}`,
        attrs: { 'aria-label': L('experienceLabel', { role, company }) } }, [
        banner(entry),
        el('header', { className: 'imp-card__head' }, [
          el('div', {}, [
            el('h3', { className: 'imp-card__role', attrs: { tabindex: '-1' }, text: role }),
            el('p', { className: 'imp-card__company', text: [company, entry.where].filter(Boolean).join(' · ') }),
          ]),
          el('span', { className: 'xp-card__dates', text: documentPeriod(entry) }),
        ]),
        entry.state === 'DATE_CONFLICT' ? dateChoice(entry) : null,
        entry.state === 'STRUCTURE_UNCERTAIN' ? structureHelp(entry) : null,
        placement(entry),
        entry.items.length ? el('div', { className: 'imp-items' }, [
          el('div', { className: 'imp-items__head' }, [
            el('h4', { text: L('details', { n: entry.items.length }) }),
            el('span', { className: 'imp-items__tally', text: L('tally', {
              confirmed: entry.counts.confirmed, waiting: entry.counts.waiting }) }),
          ]),
          el('ol', { className: 'imp-items__list' }, entry.items.map((item) => statement(item, entry))),
        ]) : el('p', { className: 'imp-muted', text: L('noDetails') }),
        entry.source && entry.source.length ? el('details', { className: 'evp-fold imp-source' }, [
          el('summary', { text: L('headingSource') }),
          ...entry.source.map((line) => el('p', { className: 'cw-source__quote', text: L('sourceLine', {
            n: line.line, text: line.text }) })),
        ]) : null,
        el('footer', { className: 'imp-foot' }, [
          model.editable && waitingItems.length ? button(L('dontImport'), () => act(
            api.skipDocumentEntry(kind, id, entry.key), L('skipped', { n: waitingItems.length }),
          ), { className: 'cw-link', ariaLabel: L('dontImportLabel', { role }) }) : el('span'),
          el('div', { className: 'imp-foot__right' }, [
            index > 0 ? button(L('previous'), () => { index -= 1; paint(); }, { className: 'btn' }) : null,
            button(isLast ? L('continueTo', { step: L(`step.${nextStep('experiences')}`) }) : L('nextExperience'),
              next, { className: waitingItems.length ? 'btn' : 'btn btn--primary' }),
          ]),
        ]),
      ]),
    ]);
  }

  function banner(entry) {
    const count = entry.counts.new;
    const words = {
      NEW: [L('state.new'), L('state.newBody')],
      EXISTING_UNCHANGED: [L('state.existing'), L('state.unchangedBody')],
      EXISTING_WITH_NEW_DETAILS: [L('state.existing'), L('state.newDetailsBody', { n: count })],
      DATE_CONFLICT: [L('state.dates'), L('state.datesBody')],
      STRUCTURE_UNCERTAIN: [L('state.help'), L('state.helpBody')],
    }[entry.state] || ['', ''];
    return el('div', { className: `imp-banner imp-banner--${TONE[entry.state] || 'plain'}` }, [
      el('span', { className: 'cw-eyebrow', text: words[0] }),
      el('span', { text: words[1] }),
    ]);
  }

  /** Where this experience lives in the profile, and the one action to put it there. */
  function placement(entry) {
    if (!model.editable) return null;
    if (entry.state === 'NEW') {
      return el('div', { className: 'imp-place' }, [
        el('p', { text: L('placeNew') }),
        button(L('addToProfile'), () => act(api.placeDocumentEntry(kind, id, { entry: entry.key, choice: 'new' }),
          L('added', { role: entry.role || entry.company || '' })), { className: 'btn btn--primary' }),
      ]);
    }
    if (entry.match && !entry.placed && entry.state !== 'DATE_CONFLICT' && entry.counts.waiting) {
      return el('div', { className: 'imp-place' }, [
        el('p', { text: L('placeExisting', { role: entry.match.title || '', company: entry.match.company || '' }) }),
        button(L('keepTogether'), () => act(api.placeDocumentEntry(kind, id, {
          entry: entry.key, choice: 'existing', dates: 'profile' }), L('keptTogether')), { className: 'btn' }),
      ]);
    }
    return null;
  }

  function dateChoice(entry) {
    const profile = periodLabel(entry.match.period_start, entry.match.period_end, entry.match.current_role);
    const document = documentPeriod(entry);
    const option = (label, value, dates) => button('', () => act(api.placeDocumentEntry(kind, id, {
      entry: entry.key, choice: 'existing', dates }), dates === 'document' ? L('datesUpdated') : L('datesKept')), {
      className: 'imp-choice',
      attrs: { 'aria-label': L('useDates', { where: label, dates: value }) },
    });
    const a = option(L('inProfile'), profile, 'profile');
    a.append(el('span', { className: 'cw-eyebrow', text: L('inProfile') }), el('strong', { text: profile }));
    const b = option(L('inDocument'), document, 'document');
    b.append(el('span', { className: 'cw-eyebrow', text: L('inDocument') }), el('strong', { text: document }));
    return el('div', { className: 'imp-dates' }, [
      el('p', { className: 'imp-dates__q', text: L('whichDates') }),
      model.editable ? el('div', { className: 'imp-dates__options' }, [a, b])
        : el('p', { text: `${profile} / ${document}` }),
    ]);
  }

  function structureHelp(entry) {
    if (!model.editable) return null;
    const role = el('input', { className: 'input', attrs: { type: 'text', maxlength: '200' },
      props: { value: entry.role || '' } });
    const company = el('input', { className: 'input', attrs: { type: 'text', maxlength: '200' },
      props: { value: entry.company || entry.label || '' } });
    const ids = [uid('imp-role'), uid('imp-company')];
    const save = button(L('saveStructure'), async () => {
      const fields = { role_title: role.value.trim() || null, company: company.value.trim() || null };
      if (kind === 'cv' && entry.editable_structure) {
        await act(api.organizeCvImport(id, { action: 'edit_entry', entry_key: entry.key, fields }).then(() =>
          api.getDocumentReview(kind, id)), L('structureSaved'));
      } else {
        await act(api.placeDocumentEntry(kind, id, { entry: entry.key, choice: 'new', fields: {
          company: fields.company, title: fields.role_title } }), L('added', { role: fields.role_title || '' }));
      }
    }, { className: 'btn btn--primary btn--small' });
    return el('div', { className: 'imp-help' }, [
      el('p', { text: L('helpAsk') }),
      el('div', { className: 'imp-help__grid' }, [
        field(ids[0], L('role'), role),
        field(ids[1], L('company'), company),
      ]),
      save,
    ]);
  }

  // -- one statement, one review interaction ---------------------------------
  function statement(item, entry) {
    const editor = el('div', { className: 'imp-item__editor' });
    const answer = (verb, extra = {}) => {
      const pending = entry.items.filter((i) => ['waiting', 'unsure'].includes(i.state) && i.key !== item.key);
      focusKey = pending.length ? pending[0].key : null;
      const message = {
        CONFIRM: L('confirmed'), EDIT: L('confirmedEdit'), REJECT: L('rejected'),
        UNSURE: L('unsure'), REOPEN: L('reopened'),
      }[verb];
      if (verb === 'REJECT' && extra.kept) {
        return act(api.answerDocument(kind, id, { key: item.key, answer: 'REJECT' }), L('keptExisting'));
      }
      return act(api.answerDocument(kind, id, { key: item.key, answer: verb, ...extra }), message);
    };
    const open = ['waiting', 'unsure'].includes(item.state) && model.editable;
    const actions = open ? [
      button(L('edit'), () => {
        const text = el('textarea', { className: 'input', attrs: { rows: '3', maxlength: '2000',
          'aria-label': L('editLabel') }, props: { value: item.text } });
        replace(editor, [text, el('div', { className: 'imp-item__editactions' }, [
          button(L('cancel'), () => replace(editor, []), { className: 'btn btn--small' }),
          button(L('confirmMyWords'), () => {
            if (text.value.trim()) answer('EDIT', { text: text.value.trim() });
          }, { className: 'btn btn--small btn--primary' }),
        ])]);
        text.focus();
      }, { className: 'btn btn--small', ariaLabel: L('editItemLabel', { text: item.text }) }),
      item.state === 'unsure' ? null
        : button(L('notSure'), () => answer('UNSURE'), { className: 'btn btn--small btn--quiet',
          ariaLabel: L('notSureLabel', { text: item.text }) }),
      button(L('reject'), () => answer('REJECT'), { className: 'btn btn--small btn--quiet',
        ariaLabel: L('rejectLabel', { text: item.text }) }),
      // The same line is already confirmed in the profile: confirming it
      // again would store a second copy, so the main answer keeps the one
      // that is there and adds nothing.
      item.conflict ? null : item.already ? button(L('keepExisting'), () => answer('REJECT', { kept: true }), {
        className: 'btn btn--small btn--primary', ariaLabel: L('keepExistingLabel', { text: item.text }),
      }) : button(L('confirm'), () => answer('CONFIRM'), {
        className: 'btn btn--small btn--primary', ariaLabel: L('confirmLabel', { text: item.text }),
      }),
    ] : [
      item.state === 'rejected' && model.editable ? button(L('reopen'), () => answer('REOPEN'),
        { className: 'btn btn--small btn--quiet' }) : null,
    ];
    return el('li', {
      className: `imp-item imp-item--${item.state}`,
      dataset: { key: item.key, state: item.state },
    }, [
      el('div', { className: 'imp-item__top' }, [
        el('p', { className: 'imp-item__text', text: item.text }),
        item.state !== 'waiting' ? badge(L(`itemState.${item.state}`), {
          confirmed: 'mint', rejected: 'plain', unsure: 'yellow' }[item.state] || 'plain') : null,
        item.already && item.state === 'waiting' ? badge(L('alreadyThere'), 'plain') : null,
        item.duplicate_of ? badge(L('duplicate'), 'yellow') : null,
      ]),
      item.conflict ? el('p', { className: 'imp-item__conflict', text: L('conflictNote') }) : null,
      item.conflict && model.editable ? button(L('chooseThis'), () => answer('CHOOSE',
        { group: item.conflict.group }), { className: 'btn btn--small btn--primary' }) : null,
      sourceSnippet({ origin: 'document', document: model.name, where: item.locator || '',
        line: item.line, quote: item.quote || item.suggested, raw: item.raw || '' }),
      item.suggested && item.suggested !== item.text ? el('p', { className: 'imp-item__understood',
        text: L('youChanged', { text: item.suggested }) }) : null,
      editor,
      el('div', { className: 'imp-item__actions' }, actions.filter(Boolean)),
    ]);
  }

  // -- skills, certifications, education -----------------------------------
  function listStep(name) {
    const items = model.sections[name] || [];
    const waiting = items.filter((i) => ['waiting', 'unsure'].includes(i.state)).length;
    return el('article', { className: 'imp-card cw-card' }, [
      el('header', { className: 'imp-card__head' }, [
        el('div', {}, [
          el('h3', { attrs: { tabindex: '-1' }, text: L(`found.${name}`, { n: items.length }) }),
          el('p', { className: 'imp-muted', text: L('oneByOne') }),
        ]),
      ]),
      name === 'skills' ? chipLegend() : null,
      el('ol', { className: `imp-items__list imp-items__list--${name}` }, items.map((item) => (
        name === 'skills' ? skillChip(item) : statement(item, { items })))),
      el('footer', { className: 'imp-foot' }, [
        el('span', { className: 'imp-muted', text: L('leftToAnswer', { n: waiting }) }),
        button(L('continueTo', { step: L(`step.${nextStep(name)}`) }), () => { step = nextStep(name); paint(); },
          { className: waiting ? 'btn' : 'btn btn--primary' }),
      ]),
    ]);
  }

  function chipLegend() {
    return el('p', { className: 'imp-legend' }, [
      badge(L('legendNew'), 'mint'), badge(L('legendIn'), 'plain'), badge(L('legendOut'), 'plain'),
      el('span', { text: L('legendHint') }),
    ]);
  }

  /** A skill: press to keep it (one confirmation), or leave it out. */
  function skillChip(item) {
    const state = item.state;
    const open = ['waiting', 'unsure'].includes(state) && model.editable;
    const keep = button(`${state === 'confirmed' ? '✓' : '+'} ${item.text}`, () => {
      if (!open) return;
      act(api.answerDocument(kind, id, { key: item.key, answer: 'CONFIRM' }), L('kept', { name: item.text }));
    }, {
      className: `imp-skill imp-skill--${state}${item.already ? ' is-already' : ''}`,
      ariaLabel: open ? L('keepLabel', { name: item.text })
        : L('stateOf', { state: L(`itemState.${state}`), name: item.text }),
      attrs: open ? {} : { 'aria-disabled': 'true' },
    });
    const out = open ? button('×', () => act(api.answerDocument(kind, id, { key: item.key, answer: 'REJECT' }),
      L('leftOut', { name: item.text })), {
      className: 'imp-skill__out', ariaLabel: L('leaveOutLabel', { name: item.text }),
    })
      : null;
    return el('li', { className: 'imp-skillrow', dataset: { key: item.key, state } }, [keep, out]);
  }

  // -- summary ---------------------------------------------------------------
  function summaryStep() {
    const s = model.summary;
    const tile = (value, label) => el('div', { className: 'imp-tile' }, [
      el('strong', { text: String(value) }), el('span', { text: label }),
    ]);
    return el('article', { className: 'imp-card cw-card imp-summary' }, [
      el('h3', { attrs: { tabindex: '-1' }, text: s.waiting ? L('summaryWaiting') : L('summaryDone') }),
      el('div', { className: 'imp-tiles' }, [
        tile(s.confirmed, L('tileConfirmed')),
        tile(s.waiting, L('tileWaiting')),
        tile(s.rejected, L('tileRejected')),
        tile(model.experiences.filter((e) => e.placed || e.state === 'EXISTING_UNCHANGED').length,
          L('tileExperiences')),
      ]),
      el('p', { className: 'imp-muted', text: L('summaryNote') }),
      el('footer', { className: 'imp-foot' }, [
        s.waiting ? button(L('reviewWaiting'), () => {
          const at = model.experiences.findIndex((e) => e.counts.waiting);
          if (at >= 0) { step = 'experiences'; index = at; }
          else step = STEPS.find((n) => (model.sections[n] || []).some(isOpen)) || 'summary';
          paint();
        }, { className: 'btn' }) : el('span'),
        button(L('finish'), onDone, { className: 'btn btn--primary' }),
      ]),
    ]);
  }

  root.load = load;
  void load().catch((problem) => {
    replace(root, [el('p', { attrs: { role: 'alert' }, text: problem.userMessage || problem.message })]);
  });
  return root;
}

