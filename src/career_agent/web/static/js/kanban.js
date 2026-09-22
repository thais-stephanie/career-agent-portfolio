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
 * MOVING A CARD IS A WRITE, NOT A RENDER. Every drop calls the same
 * `onStatus` handler the other two views call, so the board cannot drift from
 * them. The card is put back where it came from if the write fails -- an
 * optimistic move that silently reverts on reload would be the worst of both.
 *
 * DRAGGING IS NOT THE ONLY WAY. Every card carries a status select, so the
 * board is fully operable by keyboard and by anyone who cannot drag. The
 * drag handlers are an accelerant on top of a control that already works.
 */

import { el, select, replace } from './dom.js';
import { t } from './i18n.js';
import { statusOptions, statusLabel, vocabLabel } from './format.js';
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
  { key: 'TO_APPLY', statuses: ['TO_APPLY'] },
  { key: 'APPLIED', statuses: ['APPLIED'] },
  { key: 'INTERVIEW', statuses: ['INTERVIEW'] },
  { key: 'OFFER', statuses: ['OFFER'] },
  { key: 'HIRED', statuses: ['HIRED'] },
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
  return column.key === 'CLOSED' ? t('kanban.closed') : statusLabel(column.key);
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
 * @param {HTMLElement} mount
 * @param {object[]} items
 * @param {object} handlers -- {onOpen, onStatus}
 */
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
    className: 'kcol',
    attrs: {
      role: 'listitem',
      'aria-label': t('kanban.columnLabel', { column: columnLabel(column), n: jobs.length }),
    },
    dataset: { column: column.key },
  });

  root.appendChild(el('header', { className: 'kcol__head' }, [
    el('h3', { className: 'kcol__title', text: columnLabel(column) }),
    el('span', { className: 'kcol__count', text: String(jobs.length) }),
  ]));

  const body = el('div', { className: 'kcol__body', dataset: { dropzone: column.key } });

  if (!jobs.length) {
    body.appendChild(el('p', {
      className: 'kcol__empty',
      text: emptyText(column.key),
    }));
  } else {
    for (const job of jobs) body.appendChild(kanbanCard(job, handlers));
  }

  // The drop target is the column body, so dropping into the gap below the
  // last card still lands in the column rather than falling through.
  body.addEventListener('dragover', (event) => {
    event.preventDefault();
    body.classList.add('is-over');
  });
  body.addEventListener('dragleave', () => body.classList.remove('is-over'));
  body.addEventListener('drop', (event) => {
    event.preventDefault();
    body.classList.remove('is-over');
    const jobId = event.dataTransfer.getData('text/plain');
    if (!jobId) return;
    // The first status of the target column is the one a drop means. Dropping
    // into Closed means REJECTED, and the select is there to say which kind.
    handlers.onStatus(jobId, column.statuses[0]);
  });

  root.appendChild(body);
  return root;
}

/** Each column says what would put something in it, rather than just "empty". */
function emptyText(key) {
  switch (key) {
    case 'SHORTLISTED': return t('kanban.emptyShortlisted');
    case 'TO_APPLY': return t('kanban.emptyToApply');
    case 'APPLIED': return t('kanban.emptyApplied');
    case 'INTERVIEW': return t('kanban.emptyInterview');
    case 'OFFER': return t('kanban.emptyOffer');
    case 'HIRED': return t('kanban.emptyHired');
    default: return t('kanban.emptyClosed');
  }
}

function kanbanCard(job, handlers) {
  const root = el('article', {
    className: 'kcard',
    attrs: {
      tabindex: '0',
      draggable: 'true',
      'aria-label': `${job.title} at ${job.company_name}. `
        + `${vocabLabel(job.application_status)}. Press Enter to open details.`,
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
    event.dataTransfer.effectAllowed = 'move';
    root.classList.add('is-dragging');
  });
  root.addEventListener('dragend', () => root.classList.remove('is-dragging'));

  root.appendChild(el('p', { className: 'kcard__company', text: job.company_name || 'Company not stated' }));
  root.appendChild(el('h4', { className: 'kcard__title', text: job.title || 'Untitled posting' }));

  const meta = [];
  if (job.match_score !== null && job.match_score !== undefined) {
    meta.push(el('span', { className: 'kcard__match', text: `${Math.round(job.match_score)}%` }));
  }
  if (job.applied_at) {
    meta.push(el('span', {
      className: 'kcard__applied',
      text: t('kanban.appliedOn', { date: job.applied_at }),
      attrs: { title: t('kanban.appliedHelp') },
    }));
  }
  if (meta.length) root.appendChild(el('p', { className: 'kcard__meta' }, meta));

  // Every status, not only the tracked ones. Filtering the list to
  // TRACKED_STATUSES meant nothing could ever be moved back to Discovered:
  // a job could enter the board and never leave it, which turns "I picked
  // this up by mistake" into a permanent row.
  const status = select(
    statusOptions(),
    job.application_status,
    (value) => handlers.onStatus(job.job_id, value),
    {
      className: `select--status status-tag status-tag--${String(job.application_status || '').toLowerCase()}`,
      ariaLabel: t('kanban.moveLabel', { title: job.title }),
    },
  );
  status.dataset.stopsOpen = 'true';
  root.appendChild(status);

  return root;
}

/** The loading board. Same shape, so nothing jumps when the data lands. */
export function kanbanSkeleton(mount) {
  mount.className = 'kanban';
  mount.setAttribute('role', 'list');
  mount.setAttribute('aria-busy', 'true');
  replace(mount, COLUMNS.map((column) => el('section', {
    className: 'kcol',
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
