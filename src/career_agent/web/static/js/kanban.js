/**
 * kanban.js -- the tracker.
 *
 * Cards and Table answer "what is out there". This answers "what am I doing
 * about it", and that is a different population: only jobs the person has
 * actually picked up. Rendering 18,549 postings as a board would be a board
 * with one enormous Discovered column and six empty ones, which is not a
 * tracker, it is the list again with worse ergonomics.
 *
 * So the board shows TRACKED opportunities. `DISCOVERED` is the untouched
 * state and is deliberately not a column: a job enters the board by being
 * given a status, and the way to do that is from Cards or Table.
 *
 * MOVING A CARD IS A WRITE, NOT A RENDER. A drop and the card's own back
 * and forward buttons call one `onMove` handler, which goes through the same
 * status route the other views use and offers an Undo that really undoes:
 * the previous status, and no applied date the move itself stamped.
 *
 * DRAGGING IS NOT THE ONLY WAY. The buttons move a card by keyboard, and the
 * job drawer's status control reaches every status, including Hired,
 * Withdrew and Archived, which no column button names.
 */

import { el, button, replace } from './dom.js';
import { t } from './i18n.js';
import { formatDate, statusLabel } from './format.js';
import { matchTone } from './cards.js';
import { TRACKED_STATUSES } from './state.js';

/**
 * The columns, in the order the work actually moves.
 *
 * `CLOSED` is one column carrying three canonical statuses, because a board
 * with separate Rejected, Withdrawn and Archived columns is three mostly-empty
 * columns and a horizontal scrollbar. The distinction is not lost -- each card
 * still shows its own status, and the select still offers all three.
 */
export const COLUMNS = Object.freeze([
  // No LABEL here, only the status the column holds. It used to carry one,
  // resolved by calling `statusLabel` at module scope -- before the locale was
  // known -- so every column heading was frozen in the language the module
  // happened to load in while the dropdown beside it translated correctly.
  // `columnLabel` resolves at render time instead.
  //
  // `CLOSED` has no status of its own: it is the one column carrying three.
  { key: 'SHORTLISTED', statuses: ['SHORTLISTED'] },
  { key: 'APPLIED', statuses: ['APPLIED'] },
  { key: 'INTERVIEW', statuses: ['INTERVIEW'] },
  { key: 'OFFER', statuses: ['OFFER', 'HIRED'] },
  { key: 'CLOSED', statuses: ['REJECTED', 'WITHDRAWN', 'ARCHIVED'] },
]);

/**
 * One column heading, in the reader's language.
 *
 * A column and the dropdown that moves jobs into it must not drift apart, so
 * both go through `statusLabel`. `CLOSED` is the exception because it carries
 * three statuses and is not one.
 */
export function columnLabel(column) {
  return column.key === 'CLOSED' ? t('kanban.closed') : t(`kanban.col.${column.key}`);
}

/** Which column a status belongs in. Built once from COLUMNS, not restated. */
const COLUMN_OF = new Map();
for (const column of COLUMNS) {
  for (const status of column.statuses) COLUMN_OF.set(status, column.key);
}

// The columns and the store's tracked list must describe the same set. Two
// hand-maintained lists that have to agree, with nothing checking, is exactly
// the shape of the SALARY_CONFIDENCE_ITEM bug this project already shipped
// once. This throws at load rather than rendering a board that quietly omits
// a status nobody can reach.
const COVERED = COLUMNS.flatMap((column) => column.statuses);
if ([...COVERED].sort().join('|') !== [...TRACKED_STATUSES].sort().join('|')) {
  throw new Error('kanban columns and TRACKED_STATUSES disagree');
}

export { TRACKED_STATUSES };

/**
 * Where a card's buttons take it. Forward is the next thing that happens;
 * back from "Didn't work out" is Interested, never Applied, because moving a
 * job back must not claim an application nobody sent.
 */
const NEXT = { SHORTLISTED: 'APPLIED', APPLIED: 'INTERVIEW', INTERVIEW: 'OFFER' };
const PREV = { APPLIED: 'SHORTLISTED', INTERVIEW: 'APPLIED', OFFER: 'INTERVIEW', CLOSED: 'SHORTLISTED' };

export function renderKanban(mount, items, handlers) {
  const byColumn = new Map(COLUMNS.map((column) => [column.key, []]));
  for (const job of items) {
    const key = COLUMN_OF.get(String(job.application_status || '').toUpperCase());
    if (key) byColumn.get(key).push(job);
  }

  replace(mount, COLUMNS.map((column) => renderColumn(column, byColumn.get(column.key), handlers)));
  mount.className = 'kanban';
  mount.setAttribute('role', 'list');
  mount.removeAttribute('aria-busy');
  return mount;
}

function renderColumn(column, jobs, handlers) {
  const root = el('section', {
    className: `kcol kcol--${column.key.toLowerCase()}`,
    attrs: {
      role: 'listitem',
      'aria-label': t('kanban.columnLabel', { column: columnLabel(column), n: jobs.length }),
    },
    dataset: { column: column.key },
  });

  root.appendChild(el('header', { className: 'kcol__head' }, [
    el('span', { className: 'kcol__bar', attrs: { 'aria-hidden': 'true' } }),
    el('div', { className: 'kcol__titlerow' }, [
      el('h3', { className: 'kcol__title', text: columnLabel(column) }),
      el('span', { className: 'kcol__count num', text: String(jobs.length) }),
    ]),
    el('p', { className: 'kcol__help', text: t(`kanban.help.${column.key}`) }),
  ]));

  const body = el('div', { className: 'kcol__body', dataset: { dropzone: column.key } });

  if (!jobs.length) {
    body.appendChild(el('p', {
      className: 'kcol__empty',
      text: emptyText(column.key),
    }));
  } else {
    for (const job of jobs) body.appendChild(kanbanCard(job, column, handlers));
  }

  // A drop is a status move through the same route as everything else; the
  // column's first status is the one it means ("Didn't work out" is Rejected,
  // and the drawer offers Withdrew and Archived).
  root.addEventListener('dragover', (event) => {
    event.preventDefault();
    root.classList.add('is-over');
  });
  root.addEventListener('dragleave', (event) => {
    if (!root.contains(event.relatedTarget)) root.classList.remove('is-over');
  });
  root.addEventListener('drop', (event) => {
    event.preventDefault();
    root.classList.remove('is-over');
    const jobId = event.dataTransfer.getData('text/plain');
    const from = event.dataTransfer.getData('application/x-career-column');
    if (!jobId || from === column.key) return;
    handlers.onMove(jobId, column.statuses[0]);
  });

  root.appendChild(body);
  return root;
}

/** Each column says what would put something in it, rather than just "empty". */
function emptyText(key) {
  switch (key) {
    case 'SHORTLISTED': return t('kanban.emptyShortlisted');
    case 'APPLIED': return t('kanban.emptyApplied');
    case 'INTERVIEW': return t('kanban.emptyInterview');
    case 'OFFER': return t('kanban.emptyOffer');
    default: return t('kanban.emptyClosed');
  }
}

function kanbanCard(job, column, handlers) {
  const status = String(job.application_status || '').toUpperCase();
  const root = el('article', {
    className: 'kcard',
    attrs: {
      tabindex: '0',
      draggable: 'true',
      title: t('kanban.cardHint'),
      'aria-label': t('kanban.cardLabel', {
        title: job.title || t('absent.untitled'),
        company: job.company_name || t('absent.company'),
        status: statusLabel(status),
      }),
    },
    dataset: { jobId: job.job_id },
  });

  const open = () => handlers.onOpen(job.job_id);
  root.addEventListener('click', (event) => {
    if (event.target.closest('[data-stops-open]')) return;
    open();
  });
  root.addEventListener('keydown', (event) => {
    if (event.key !== 'Enter' && event.key !== ' ') return;
    if (event.target.closest('[data-stops-open]')) return;
    event.preventDefault();
    open();
  });
  root.addEventListener('dragstart', (event) => {
    event.dataTransfer.setData('text/plain', job.job_id);
    event.dataTransfer.setData('application/x-career-column', column.key);
    event.dataTransfer.effectAllowed = 'move';
    root.classList.add('is-dragging');
  });
  root.addEventListener('dragend', () => root.classList.remove('is-dragging'));

  const tone = matchTone(job.match_score);
  root.appendChild(el('div', { className: 'kcard__top' }, [
    el('span', { className: 'kcard__company', text: job.company_name || t('absent.companyStated') }),
    tone ? el('span', {
      className: `kcard__match matchpill--${tone.tone} num`,
      text: `${Math.round(Number(job.match_score))}%`,
      attrs: { title: t(tone.key) },
    }) : null,
  ].filter(Boolean)));
  root.appendChild(el('h4', { className: 'kcard__title', text: job.title || t('absent.untitled') }));

  // What the column does not say: which of its statuses this is, and when
  // the application went out.
  const meta = [];
  if (column.statuses.length > 1) meta.push(statusLabel(status));
  if (job.applied_at) meta.push(t('kanban.appliedOn', { date: formatDate(job.applied_at) }));
  if (meta.length) {
    root.appendChild(el('p', {
      className: 'kcard__meta kcard__applied', text: meta.join(' · '), attrs: { title: t('kanban.appliedHelp') },
    }));
  }

  const prev = PREV[column.key];
  const next = NEXT[column.key];
  const back = prev
    ? button('←', () => handlers.onMove(job.job_id, prev), {
      className: 'kcard__back',
      ariaLabel: t('kanban.moveBack', { column: statusLabel(prev) }),
      attrs: { title: t('kanban.moveBack', { column: statusLabel(prev) }) },
    })
    : null;
  const forward = next
    ? button(t(`kanban.next.${column.key}`), () => handlers.onMove(job.job_id, next), {
      className: 'kcard__next',
      attrs: { title: t('kanban.moveTo', { column: statusLabel(next) }) },
    })
    : el('span', {
      className: 'kcard__done',
      text: column.key === 'OFFER' ? t('kanban.congrats') : t('kanban.archived'),
    });
  if (back) back.dataset.stopsOpen = 'true';
  if (forward.tagName === 'BUTTON') forward.dataset.stopsOpen = 'true';
  root.appendChild(el('div', { className: 'kcard__foot' }, [
    back, el('span', { className: 'kcard__grow' }), forward,
  ].filter(Boolean)));

  return root;
}

/** The loading board. Same shape, so nothing jumps when the data lands. */
export function kanbanSkeleton(mount) {
  mount.className = 'kanban';
  mount.setAttribute('role', 'list');
  mount.setAttribute('aria-busy', 'true');
  replace(mount, COLUMNS.map((column) => el('section', {
    className: `kcol kcol--${column.key.toLowerCase()}`,
    attrs: { 'aria-hidden': 'true' },
  }, [
    el('header', { className: 'kcol__head' }, [
      el('h3', { className: 'kcol__title', text: columnLabel(column) }),
    ]),
    el('div', { className: 'kcol__body' }, [
      el('div', { className: 'sk sk--kcard' }),
      el('div', { className: 'sk sk--kcard' }),
    ]),
  ])));
}
