/**
 * cv_review.js -- one CV read, reviewed as a CAREER rather than as a list.
 *
 * WHY THIS IS NOT A LIST OF CARDS
 * -------------------------------
 * A realistic CV is a hundred or more suggestions. The first review drew
 * every one of them as a card, grouped by claim type, with no company, role
 * or dates on any of them: a wall nobody finishes. So the review has two
 * levels and opens on the first:
 *
 *   the read        what was found ("6 experiences / 142 suggestions / 18 need
 *                   attention"), then one row per experience -- company,
 *                   role, dates, how many wait, how many are confirmed
 *   one group       an experience (or Skills, Education...) and the
 *                   suggestions in it, with Confirm, Edit, Reject, Move,
 *                   Delete, and the experience's own details to correct
 *
 * "Next item needing review" walks the whole read in order, so nobody has to
 * find their place.
 *
 * WHAT THIS SCREEN MAY NOT DO
 * ---------------------------
 * Confirm more than one statement at a time. There is no "accept all" and no
 * batch confirmation: every confirmed fact can reach a real application, and
 * one click cannot honestly mean somebody read a hundred sentences. The batch
 * actions move, split, reject and delete -- none of which creates evidence.
 *
 * Everything imported is TEXT. A line from the document is set with
 * `textContent`, never parsed as HTML, whatever it contains.
 */

import { el, button, replace, field, select } from './dom.js';
import { t } from './i18n.js';
import * as api from './api.js';
import { formatDate } from './format.js';

let sequence = 0;
const L = (key, params) => t(`cvr.${key}`, params);
const JOB_SECTIONS = new Set(['experience', 'volunteering', 'internships', 'freelance']);

/** "Mar 2022 - present", from what the document wrote or what she set. */
export function periodText(entry) {
  const month = (value) => {
    if (!value) return '';
    const [year, number] = String(value).split('-');
    const date = new Date(Date.UTC(Number(year), Number(number) - 1, 1));
    return date.toLocaleDateString(undefined, { month: 'short', year: 'numeric', timeZone: 'UTC' });
  };
  if (entry.edited || !entry.period_text) {
    if (!entry.period_start && !entry.start_year) return L('datesUnknown');
    const start = entry.period_start ? month(entry.period_start) : String(entry.start_year);
    if (entry.current_role) return L('periodCurrent', { start });
    const end = entry.period_end ? month(entry.period_end) : (entry.end_year ? String(entry.end_year) : '');
    return end ? L('period', { start, end }) : start;
  }
  return entry.period_text;
}

function entryTitle(entry) {
  return {
    company: entry.company || entry.label || L('companyUnknown'),
    role: entry.role_title || L('roleUnknown'),
  };
}

/**
 * The review of one read.
 *
 * `review` is the server's payload. `onBack` returns to Career Evidence;
 * `onChanged` fires whenever the confirmed set may have moved.
 */
export function cvReview(initial, { onBack, onChanged } = {}) {
  let review = initial;
  let open = null;          // entry_key or `section:<name>` of the open group
  let selected = new Set();
  let focusKey = null;      // the suggestion to put focus on after a repaint
  // Not a live region: the whole review repaints, and announcing all of it
  // after every answer would drown the one sentence that matters. The flash
  // line below is the status, and says what just happened.
  const root = el('div', { className: 'cvr' });
  const flash = el('p', { className: 'cvr__flash', attrs: { role: 'status' } });

  const fail = (error) => {
    flash.textContent = error.userMessage || error.message;
    flash.className = 'cvr__flash cvr__flash--bad';
  };
  const run = (fn) => async () => {
    try { await fn(); } catch (error) { fail(error); }
  };
  async function apply(promise, message = '') {
    const next = await promise;
    review = next;
    flash.textContent = message;
    flash.className = 'cvr__flash';
    paint();
    if (onChanged) onChanged();
  }
  const organize = (body, message) => apply(api.organizeCvImport(review.import_id, body), message);

  // -- the order a person walks the read in -----------------------------
  function groups() {
    return [
      ...review.entries.map((entry) => ({ id: entry.entry_key, entry, items: entry.proposals })),
      ...review.sections.map((section) => ({
        id: `section:${section.group}`, section, items: section.proposals,
      })),
    ];
  }
  /** The next unanswered suggestion after `after` (or from `from`, inclusive). */
  function nextWaiting(after = null, { from = null } = {}) {
    const all = groups().flatMap((group) => group.items.map((item) => ({ group, item })));
    let start = 0;
    if (from) start = Math.max(0, all.findIndex((row) => row.group.id === from));
    else if (after) start = all.findIndex((row) => row.item.claim_key === after) + 1;
    const rest = [...all.slice(start), ...all.slice(0, start)];
    return rest.find((row) => row.item.decision === 'PENDING') || null;
  }
  function goNext(after = null, options = {}) {
    const next = nextWaiting(after, options);
    if (!next) {
      open = null; selected = new Set();
      flash.textContent = L('allAnswered');
      paint();
      return;
    }
    open = next.group.id; selected = new Set(); focusKey = next.item.claim_key;
    paint();
  }

  // =====================================================================
  function paint() {
    const group = open && groups().find((g) => g.id === open);
    if (open && !group) open = null;
    replace(root, [flash, group ? groupView(group) : summaryView()]);
    if (focusKey) {
      const card = root.querySelector(`[data-claim-key="${CSS.escape(focusKey)}"]`);
      if (card) { card.scrollIntoView({ block: 'nearest' }); card.focus(); }
      focusKey = null;
    } else {
      const heading = root.querySelector('h3');
      if (heading) heading.focus();
    }
  }

  // -- level one: the read ----------------------------------------------
  function summaryView() {
    const s = review.summary;
    const archived = review.archived;
    const rows = review.entries.map((entry) => {
      const { company, role } = entryTitle(entry);
      return el('li', {
        className: `cvr__row${entry.attention ? ' cvr__row--attention' : ''}`,
        dataset: { entryKey: entry.entry_key },
      }, [
        el('div', { className: 'cvr__rowmain' }, [
          el('span', { className: 'cvr__company', text: company }),
          el('span', { className: 'cvr__role', text: role }),
          el('span', { className: 'cvr__dates', text: periodText(entry) }),
        ]),
        el('span', { className: 'cvr__counts', text: L('rowCounts', {
          waiting: entry.waiting, confirmed: entry.confirmed,
        }) }),
        entry.attention
          ? el('span', { className: 'cvr__chip', text: L('attentionChip', { n: entry.attention }) })
          : null,
        button(L('review'), () => { open = entry.entry_key; selected = new Set(); paint(); }, {
          className: entry.waiting ? 'btn btn--small' : 'btn btn--small btn--quiet',
          ariaLabel: L('reviewLabel', { company, role }),
        }),
      ]);
    });
    const others = review.sections.map((section) => el('li', {
      className: `cvr__row${section.attention ? ' cvr__row--attention' : ''}`,
      dataset: { section: section.group },
    }, [
      el('div', { className: 'cvr__rowmain' }, [
        el('span', { className: 'cvr__company', text: L(`section.${section.group}`) }),
      ]),
      el('span', { className: 'cvr__counts', text: L('rowCounts', {
        waiting: section.waiting, confirmed: section.confirmed,
      }) }),
      section.attention
        ? el('span', { className: 'cvr__chip', text: L('attentionChip', { n: section.attention }) })
        : null,
      button(L('review'), () => { open = `section:${section.group}`; selected = new Set(); paint(); }, {
        className: section.waiting ? 'btn btn--small' : 'btn btn--small btn--quiet',
        ariaLabel: L('reviewSectionLabel', { name: L(`section.${section.group}`) }),
      }),
    ]));

    const lifecycle = el('div', { className: 'cvr__lifecycle' });
    const actions = el('div', { className: 'cvr__actions' }, [
      !archived && s.waiting ? button(L('next'), () => goNext(), {
        className: 'btn btn--primary', attrs: { 'data-action': 'next' },
      }) : null,
      !archived ? button(L('addExperience'), () => entryForm(null, lifecycle), { className: 'btn' }) : null,
      archived
        ? button(L('restore'), run(async () => {
          await api.restoreCvImport(review.import_id);
          await apply(api.getCvReview(review.import_id), L('restored'));
        }), { className: 'btn btn--primary' })
        : button(L('archive'), run(async () => {
          await api.archiveCvImport(review.import_id);
          await apply(api.getCvReview(review.import_id), L('archived'));
        }), { className: 'btn btn--quiet', attrs: { 'data-action': 'archive' } }),
      button(L('delete'), run(() => deletePanel(lifecycle)), {
        className: 'btn btn--quiet btn--danger', attrs: { 'data-action': 'delete' },
      }),
    ]);

    return el('section', { className: 'cvr__summary' }, [
      el('div', { className: 'cvr__head' }, [
        el('h3', { className: 'd-sec__head', attrs: { tabindex: '-1' },
          text: L('heading', { name: review.source_name }) }),
        button(L('back'), onBack, { className: 'btn btn--quiet' }),
      ]),
      el('p', { className: 'cvr__found', attrs: { 'data-summary': '' }, text: L('found', {
        experiences: s.experiences, suggestions: s.suggestions, attention: s.attention,
      }) }),
      el('p', { className: 'ev__lede', text: s.waiting
        ? L('waiting', { waiting: s.waiting, confirmed: s.confirmed })
        : L('allAnsweredLede', { confirmed: s.confirmed, rejected: s.rejected }) }),
      archived ? el('p', { className: 'cvr__archived', text: L('archivedLede') }) : null,
      el('p', { className: 'ev__note', text: L('oneAtATime') }),
      actions,
      lifecycle,
      rows.length ? el('h4', { className: 'cvr__subhead', text: L('experiences') }) : null,
      rows.length ? el('ol', { className: 'cvr__rows', attrs: { 'aria-label': L('experiences') } }, rows) : null,
      others.length ? el('h4', { className: 'cvr__subhead', text: L('otherSections') }) : null,
      others.length ? el('ul', { className: 'cvr__rows' }, others) : null,
    ]);
  }

  /** Say exactly what a delete will do, from the server's own plan, first. */
  async function deletePanel(host) {
    const { plan } = await api.deleteCvImport(review.import_id);
    const lines = plan.confirmed_kept
      ? [L('deleteKeeps', { removed: plan.removed, pending: plan.pending, rejected: plan.rejected,
        confirmed: plan.confirmed_kept })]
      : [L('deleteAll', { removed: plan.removed, pending: plan.pending, rejected: plan.rejected })];
    const confirmButton = button(L('deleteConfirm'), run(async () => {
      confirmButton.disabled = true;
      await api.deleteCvImport(review.import_id, { confirm: true });
      if (onChanged) onChanged();
      onBack();
    }), { className: 'btn btn--danger', attrs: { 'data-action': 'delete-confirm' } });
    replace(host, [el('div', { className: 'cvr__confirm', attrs: { role: 'alertdialog',
      'aria-labelledby': 'cvr-delete-title' } }, [
      el('h4', { attrs: { id: 'cvr-delete-title' }, text: L('deleteTitle', { name: review.source_name }) }),
      ...lines.map((text) => el('p', { text })),
      el('p', { className: 'ev__note', text: L('deleteArchiveInstead') }),
      el('p', { text: L('deleteForever') }),
      el('div', { className: 'cvr__actions' }, [
        confirmButton,
        button(L('cancel'), () => replace(host, []), { className: 'btn' }),
      ]),
    ])]);
    confirmButton.focus();
  }

  /**
   * The details of one experience, or of a new one. `onSave` receives the
   * fields; by default it edits `entry` or creates an experience.
   */
  function entryForm(entry, host, { saveLabel = null, onSave = null } = {}) {
    const input = (value, type = 'text') => el('input', {
      className: 'input', attrs: { type, maxlength: '200' }, props: { value: value || '' },
    });
    // An unresolved heading's own words, offered as the company to start from.
    const company = input(entry?.company || entry?.label);
    const role = input(entry?.role_title);
    const start = input(entry?.period_start, 'month');
    const end = input(entry?.period_end, 'month');
    const current = el('input', { className: 'checkbox', attrs: { type: 'checkbox' },
      props: { checked: !!entry?.current_role } });
    end.disabled = current.checked;
    current.addEventListener('change', () => { end.disabled = current.checked; if (current.checked) end.value = ''; });
    const id = () => `cvr-${++sequence}`;
    const save = button(saveLabel || (entry ? L('saveDetails') : L('createExperience')), run(async () => {
      const fields = {
        company: company.value.trim() || null,
        role_title: role.value.trim() || null,
        period_start: start.value || null,
        period_end: current.checked ? null : (end.value || null),
        current_role: current.checked,
      };
      if (onSave) {
        await onSave(fields);
      } else if (entry) {
        await organize({ action: 'edit_entry', entry_key: entry.entry_key, fields }, L('saved'));
      } else {
        await organize({ action: 'create_entry', fields }, L('created'));
      }
    }), { className: 'btn btn--primary' });
    replace(host, [el('div', { className: 'cvr__form' }, [
      el('h4', { text: entry ? L('editDetails') : L('addExperience') }),
      field(id(), L('company'), company),
      field(id(), L('role'), role),
      field(id(), L('start'), start),
      field(id(), L('end'), end),
      field(id(), L('current'), current, { className: 'field field--inline' }),
      entry && entry.period_text ? el('p', { className: 'field__hint',
        text: L('writtenAs', { text: entry.period_text }) }) : null,
      el('div', { className: 'cvr__actions' }, [
        save, button(L('cancel'), () => replace(host, []), { className: 'btn' }),
      ]),
    ])]);
    company.focus();
  }

  // -- level two: one experience, or one section ------------------------
  function groupView(group) {
    const entry = group.entry || null;
    const heading = entry
      ? [entryTitle(entry).company, entryTitle(entry).role].join(' / ')
      : L(`section.${group.section.group}`);
    const tools = el('div', { className: 'cvr__tools' });
    const targets = [
      { value: '', label: L('noExperience') },
      ...review.entries.filter((e) => !entry || e.entry_key !== entry.entry_key).map((e) => ({
        value: e.entry_key,
        label: `${entryTitle(e).company} / ${entryTitle(e).role} (${periodText(e)})`,
      })),
    ];
    const status = el('span', { className: 'cvr__selected', attrs: { role: 'status' } });
    const bulkMove = select(targets, '', () => {}, { ariaLabel: L('moveSelectedTo') });
    const batch = el('div', { className: 'cvr__batch', attrs: { hidden: true } }, [
      status,
      bulkMove,
      button(L('moveSelected'), run(() => organize({ action: 'move', keys: [...selected],
        entry_key: bulkMove.value || null }, L('moved', { n: selected.size }))), { className: 'btn btn--small' }),
      entry ? button(L('splitSelected'), () => {
        const keys = [...selected];
        // A new experience at the same company, for the chosen suggestions.
        entryForm({ ...entry, entry_key: null, role_title: null, period_start: null,
          period_end: null, current_role: false, period_text: null, edited: true }, tools, {
          saveLabel: L('splitSave'),
          onSave: (fields) => organize({ action: 'split', keys, fields }, L('split', { n: keys.length })),
        });
      }, { className: 'btn btn--small' }) : null,
      button(L('rejectSelected'), run(() => organize({ action: 'reject', keys: [...selected] },
        L('rejectedN', { n: selected.size }))), { className: 'btn btn--small' }),
      button(L('deleteSelected'), () => {
        const keys = [...selected];
        replace(tools, [el('div', { className: 'cvr__confirm', attrs: { role: 'alertdialog' } }, [
          el('p', { text: L('deleteSuggestions', { n: keys.length }) }),
          el('div', { className: 'cvr__actions' }, [
            button(L('deleteConfirm'), run(() => organize({ action: 'delete', keys, confirm: true },
              L('deletedN', { n: keys.length }))), { className: 'btn btn--danger' }),
            button(L('cancel'), () => replace(tools, []), { className: 'btn' }),
          ]),
        ])]);
      }, { className: 'btn btn--small btn--quiet' }),
    ]);
    function selectionChanged() {
      batch.hidden = selected.size === 0;
      status.textContent = L('selectedN', { n: selected.size });
    }

    const cards = group.items.map((item) => card(item, targets, selectionChanged));
    const waiting = group.items.filter((item) => item.decision === 'PENDING').length;
    const mergeTarget = entry ? select(targets.filter((option) => option.value), '', () => {},
      { ariaLabel: L('mergeInto') }) : null;

    return el('section', { className: 'cvr__group', dataset: { group: group.id } }, [
      el('div', { className: 'cvr__head' }, [
        button(L('backToRead'), () => { open = null; selected = new Set(); paint(); },
          { className: 'btn btn--quiet' }),
        nextWaiting() ? button(L('next'), () => goNext(null, { from: group.id }),
          { className: 'btn btn--primary', attrs: { 'data-action': 'next' } }) : null,
      ]),
      el('h3', { className: 'd-sec__head', attrs: { tabindex: '-1' }, text: heading }),
      entry ? el('p', { className: 'cvr__dates',
        text: periodText(entry) + (entry.location ? ` / ${entry.location}` : '') }) : null,
      el('p', { className: 'ev__lede', text: L('groupCounts', { waiting, total: group.items.length }) }),
      entry && entry.unresolved.length ? el('p', { className: 'cvr__attention',
        text: L('unresolved', { what: entry.unresolved.map((r) => L(`missing.${r}`)).join(', ') }) }) : null,
      entry && entry.source.length ? el('details', { className: 'cvr__source' }, [
        el('summary', { text: L('documentSaid') }),
        ...entry.source.map((line) => el('p', { className: 'quote', text: L('sourceLine', {
          line: line.line, text: line.text }) })),
      ]) : null,
      entry && !review.archived ? el('div', { className: 'cvr__actions' }, [
        button(L('editDetails'), () => entryForm(entry, tools), { className: 'btn btn--small' }),
        mergeTarget && targets.length > 1 ? el('details', { className: 'cvr__merge' }, [
          el('summary', { text: L('mergeFold') }),
          el('p', { className: 'field__hint', text: L('mergeHelp') }),
          el('div', { className: 'cvr__actions' }, [
            mergeTarget,
            button(L('merge'), run(() => {
              if (!mergeTarget.value) return undefined;
              const target = mergeTarget.value;
              return organize({ action: 'merge', source: entry.entry_key, target }, L('merged'))
                .then(() => { open = target; paint(); });
            }), { className: 'btn btn--small' }),
          ]),
        ]) : null,
        !group.items.length ? button(L('deleteExperience'), run(() => organize(
          { action: 'delete_entry', entry_key: entry.entry_key }, L('experienceDeleted'))),
        { className: 'btn btn--small btn--quiet btn--danger' }) : null,
      ]) : null,
      tools,
      batch,
      el('ol', { className: 'cvr__cards' }, cards),
    ]);
  }

  /** Delete one suggestion for good, after a second, explicit press. */
  function deleteTwice(item) {
    const node = button(L('deleteOne'), () => {
      if (node.dataset.armed !== 'true') {
        node.dataset.armed = 'true';
        node.textContent = L('deleteOneSure');
        node.classList.add('btn--danger');
        return;
      }
      void run(() => organize({ action: 'delete', keys: [item.claim_key], confirm: true },
        L('deletedN', { n: 1 })))();
    }, { className: 'btn btn--quiet', ariaLabel: L('deleteOneLabel', { text: item.text }) });
    node.addEventListener('blur', () => {
      if (node.dataset.armed === 'true') {
        node.dataset.armed = 'false';
        node.textContent = L('deleteOne');
        node.classList.remove('btn--danger');
      }
    });
    return node;
  }

  /** One suggestion: the words, the line they came from, and the answers. */
  function card(item, targets, selectionChanged) {
    const decided = item.decision !== 'PENDING';
    const confirmed = item.decision === 'ACCEPTED' || item.decision === 'EDITED';
    const editor = el('textarea', { className: 'input ev__editor', attrs: { rows: '3',
      'aria-label': t('cv.editLabel') }, props: { value: item.decided_text || item.text, hidden: true } });
    const answer = (decision, text = null) => run(async () => {
      const next = await api.decideProposal(review.import_id, item.claim_key, decision, text);
      review = next;
      flash.textContent = L(`answered.${decision}`);
      flash.className = 'cvr__flash';
      if (onChanged) onChanged();
      if (decision === 'PENDING') { focusKey = item.claim_key; paint(); return; }
      goNext(item.claim_key);
    })();
    const saveEdit = button(t('cv.saveEdit'), () => {
      const text = editor.value.trim();
      if (text) answer('EDITED', text);
    }, { className: 'btn btn--accept' });
    saveEdit.hidden = true;
    editor.addEventListener('input', () => { saveEdit.hidden = false; });

    const pick = el('input', { className: 'checkbox', attrs: { type: 'checkbox',
      'aria-label': L('selectLabel', { text: item.text }) }, props: { disabled: confirmed || review.archived },
      on: { change: (event) => {
        if (event.target.checked) selected.add(item.claim_key); else selected.delete(item.claim_key);
        selectionChanged();
      } } });
    const here = item.entry_key
      ? [{ value: item.entry_key, label: L('thisExperience') }, ...targets]
      : targets;
    const move = confirmed || review.archived ? null : select(here, item.entry_key || '', (value) => {
      if ((value || null) === (item.entry_key || null)) return;
      void run(() => organize({ action: 'move', keys: [item.claim_key], entry_key: value || null },
        L('moved', { n: 1 })))();
    }, { ariaLabel: L('moveLabel', { text: item.text }) });

    // An archived read is kept whole and answers nothing until restored.
    const locked = review.archived;
    const actions = confirmed || locked
      ? [el('span', { className: 'ev__note', text: locked ? L('archivedLede') : L('confirmedNote') })]
      : [
        button(t('cv.accept'), () => answer('ACCEPTED'), { className: 'btn btn--accept',
          ariaLabel: t('cv.acceptLabel', { text: item.text }), attrs: { 'data-answer': 'ACCEPTED' } }),
        button(t('cv.edit'), () => { editor.hidden = !editor.hidden; if (!editor.hidden) editor.focus(); },
          { className: 'btn' }),
        item.decision === 'REJECTED'
          ? button(L('undoReject'), () => answer('PENDING'), { className: 'btn' })
          : button(t('cv.reject'), () => answer('REJECTED'), { className: 'btn btn--reject',
            ariaLabel: t('cv.rejectLabel', { text: item.text }), attrs: { 'data-answer': 'REJECTED' } }),
        deleteTwice(item),
      ];
    const bare = String(item.source_text || '')
      .replace(/^\s*(?:[-*+\u2022]|\d{1,3}[.)])\s+/, '').trim();
    const raw = Boolean(item.source_text) && bare !== item.evidence.trim();
    return el('li', {
      className: `ev__card cvr__card${decided ? ` is-${item.decision.toLowerCase()}` : ''}`,
      attrs: { tabindex: '-1' },
      dataset: { claimKey: item.claim_key, decision: item.decision },
    }, [
      el('div', { className: 'ev__cardhead' }, [
        pick,
        el('p', { className: 'ev__proposal', text: item.decided_text || item.text }),
        decided ? el('span', { className: `ev__decision ev__decision--${item.decision.toLowerCase()}`,
          text: t(`cv.decision.${item.decision}`) }) : null,
      ]),
      item.attention && item.attention.length ? el('p', { className: 'cvr__attention',
        text: item.attention.map((reason) => L(`attention.${reason}`)).join(' ') }) : null,
      el('div', { className: 'ev__from' }, [
        el('span', { className: 'ev__fromlabel', text: item.source_line
          ? L('fromLine', { line: item.source_line }) : t('cv.fromCv') }),
        el('p', { className: 'quote', text: item.evidence }),
        raw ? el('details', { className: 'cvr__raw' }, [
          el('summary', { text: L('asWritten') }),
          el('pre', { className: 'cvr__rawtext', text: item.source_text }),
        ]) : null,
      ]),
      item.has_measurement ? el('p', { className: 'ev__figure', text: t('cv.carriesFigure') }) : null,
      editor,
      saveEdit,
      el('div', { className: 'ev__actions' }, [
        ...actions,
        move ? el('label', { className: 'cvr__move' }, [el('span', { text: L('moveTo') }), move]) : null,
      ]),
      decided && item.decided_at ? el('p', { className: 'cvr__when', text: formatDate(item.decided_at) }) : null,
    ]);
  }

  paint();
  return root;
}

/** Whether a section groups job suggestions (for callers that label rows). */
export function isJobSection(name) { return JOB_SECTIONS.has(name); }
