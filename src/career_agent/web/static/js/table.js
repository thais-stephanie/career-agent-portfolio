/**
 * table.js -- the working view.
 *
 * Same store, same request, same job ids as Cards. What changes is density and
 * what you can edit without opening anything.
 *
 * One rule worth stating out loud: the "Applied?" checkbox does NOT own a
 * boolean. `has_applied` is computed by the server from status and date, so the
 * checkbox PATCHes the canonical status (to APPLIED, or back to SHORTLISTED)
 * and then renders whatever the server said. There is no second source of
 * truth for "did I apply".
 */

import { el, button, extLink, select, replace, clear } from './dom.js';
import { t } from './i18n.js';
import {
  dateInputValue, formatDate, formatSalary, freshness, humanLabel,
  statusOptions, truncate, vocabLabel,
} from './format.js';
import { eligibilityWords, scoreCell } from './badges.js';

const STORAGE_KEY = 'careerAgent.table.columns.v1';

/** Column order is the reading order of the working view. */
export const COLUMNS = [
  { id: 'score', labelKey: 'column.score', sort: 'score', className: 'col--num' },
  // Detail, not Confidence. The card, the legend, the filter and the sort
  // control all say Detail; this column said Confidence for the same number,
  // and one measurement with two names is two measurements to a reader.
  { id: 'confidence', labelKey: 'column.confidence', sort: 'confidence', className: 'col--num' },
  { id: 'company', labelKey: 'column.company', sort: 'company' },
  { id: 'title', labelKey: 'column.title', sort: 'title', fixed: true },
  { id: 'location', labelKey: 'column.location' },
  { id: 'source', labelKey: 'column.source' },
  { id: 'technologies', labelKey: 'column.technologies' },
  { id: 'salary', labelKey: 'column.salary' },
  { id: 'contract', labelKey: 'column.contract' },
  { id: 'posted', labelKey: 'column.posted', sort: 'posted', className: 'col--num' },
  { id: 'freshness', labelKey: 'column.freshness' },
  { id: 'eligibility', labelKey: 'column.eligibility' },
  { id: 'status', labelKey: 'column.status', sort: 'status' },
  { id: 'applied', labelKey: 'column.applied' },
  { id: 'applied_at', labelKey: 'column.applied_at', className: 'col--num' },
  { id: 'saved', labelKey: 'column.saved' },
  { id: 'link', labelKey: 'column.link' },
];

const DEFAULT_HIDDEN = new Set(['source', 'contract']);

/** localStorage can throw (private mode, disabled site data). It is a nicety. */
export function loadVisible() {
  const fallback = new Set(COLUMNS.filter((c) => !DEFAULT_HIDDEN.has(c.id)).map((c) => c.id));
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return fallback;
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed) || !parsed.length) return fallback;
    const known = new Set(COLUMNS.map((c) => c.id));
    const restored = new Set(parsed.filter((id) => known.has(id)));
    for (const column of COLUMNS) if (column.fixed) restored.add(column.id);
    return restored;
  } catch {
    return fallback;
  }
}

function saveVisible(visible) {
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(Array.from(visible)));
  } catch {
    /* A column preference is not worth an error message. */
  }
}

/**
 * @param {HTMLElement} mount
 * @param {object[]} items
 * @param {object} ctx -- {sort, direction, onSort, onOpen, onStatus, onSave,
 *                         onAppliedDate, visible, onVisibilityChange}
 */
export function renderTable(mount, items, ctx) {
  mount.className = 'tablewrap';
  const visible = ctx.visible || loadVisible();

  // How common each signal is across the rows actually on screen. A signal 12
  // of 14 rows share distinguishes nothing; the two rarest do.
  const context = { ...ctx, frequency: signalFrequency(items) };

  const table = el('table', { className: 'jobs', attrs: { 'aria-label': t('table.caption') } }, [
    head(visible, context),
    body(items, visible, context),
  ]);

  const scroller = el('div', {
    className: 'tablescroll',
    attrs: { tabindex: '0', role: 'region', 'aria-label': t('table.help') },
  }, [table]);
  const wrap = el('div', { className: 'tablescrollwrap' }, [scroller]);

  replace(mount, [toolbar(visible, context), wrap]);
  watchScrollEdges(wrap, scroller);
  return mount;
}

/**
 * Mark which sides still hold content the reader cannot see. The CSS turns that
 * into an inset veil, so a value sliced by the edge of the region ("USD 95")
 * fades under it instead of reading as a whole number.
 */
function watchScrollEdges(wrap, scroller) {
  const update = () => {
    const max = scroller.scrollWidth - scroller.clientWidth;
    const left = scroller.scrollLeft;
    const sides = [];
    if (left > 1) sides.push('left');
    if (left < max - 1) sides.push('right');
    wrap.dataset.scroll = sides.join(' ');
  };
  scroller.addEventListener('scroll', update, { passive: true });
  if (typeof ResizeObserver === 'function') {
    // Disconnects with the element: the observer holds no reference the
    // detached subtree does not already hold.
    new ResizeObserver(update).observe(scroller);
  } else {
    window.addEventListener('resize', update);
  }
  update();
}

/** Map of signal label -> how many of the rendered rows carry it. */
function signalFrequency(items) {
  const counts = new Map();
  for (const job of items) {
    for (const label of signalLabels(job)) {
      counts.set(label, (counts.get(label) || 0) + 1);
    }
  }
  return counts;
}

/**
 * A column heading, in the reader's language.
 *
 * `COLUMNS` is a module-level table and was a table of finished English, so
 * every heading in the table view -- and the "Sort by X" name on each sort
 * button -- was frozen in whichever language this module happened to load in.
 */
function columnLabel(column) {
  return column.labelKey ? t(column.labelKey) : (column.label || '');
}


function signalLabels(job) {
  const seen = new Set();
  for (const tech of job.technologies || []) {
    const label = tech.label || tech.signal_id;
    if (label) seen.add(String(label));
  }
  return Array.from(seen);
}

// No bulk "move the ticked jobs" control and no row checkboxes. Each row
// already carries its own status select, and ticking rows existed only to feed
// that one bulk control, so both went together.
function toolbar(visible, ctx) {
  return el('div', { className: 'tablebar' }, [columnMenu(visible, ctx)]);
}

function columnMenu(visible, ctx) {
  const list = el('div', {
    className: 'colmenu__list',
    attrs: { role: 'group', 'aria-label': t('table.visibleColumns') },
  },
    COLUMNS.filter((column) => column.labelKey).map((column) => {
      const id = `col-${column.id}`;
      const input = el('input', {
        className: 'checkbox',
        attrs: { type: 'checkbox', id },
        props: { checked: visible.has(column.id), disabled: Boolean(column.fixed) },
        on: {
          change: (event) => {
            if (event.target.checked) visible.add(column.id);
            else visible.delete(column.id);
            saveVisible(visible);
            ctx.onVisibilityChange(visible);
          },
        },
      });
      return el('label', { className: 'colmenu__item', attrs: { for: id } }, [
        input,
        el('span', { text: columnLabel(column) }),
      ]);
    }));

  return el('details', { className: 'colmenu' }, [
    el('summary', { className: 'colmenu__summary', text: t('table.columns') }),
    list,
  ]);
}

function head(visible, ctx) {
  const row = el('tr');
  for (const column of COLUMNS) {
    if (!visible.has(column.id)) continue;

    const isSorted = column.sort && column.sort === ctx.sort;
    const th = el('th', {
      className: column.className || '',
      attrs: {
        scope: 'col',
        'aria-sort': column.sort ? (isSorted ? (ctx.direction === 'asc' ? 'ascending' : 'descending') : 'none') : null,
      },
    });

    if (column.sort) {
      th.appendChild(button(
        `${columnLabel(column)}${isSorted ? (ctx.direction === 'asc' ? ' ▲' : ' ▼') : ''}`,
        () => ctx.onSort(column.sort),
        {
          className: `th__sort${isSorted ? ' is-sorted' : ''}`,
          ariaLabel: t('table.sortBy', { column: columnLabel(column) }),
        },
      ));
    } else {
      th.appendChild(el('span', { text: columnLabel(column) }));
    }
    row.appendChild(th);
  }
  return el('thead', {}, [row]);
}

function body(items, visible, ctx) {
  return el('tbody', {}, items.map((job) => row(job, visible, ctx)));
}

function row(job, visible, ctx) {
  // `blockers` is the EMPLOYER stating a requirement. `screening_state` is
  // this search deciding the posting is not the work asked for. They used to
  // share one class and therefore one meaning; see `setAside` in `cards.js`.
  const gated = (job.blockers || []).length > 0;
  const offTarget = String(job.screening_state).toUpperCase() === 'BLOCKED';
  const tone = gated ? 'row--blocked' : (offTarget ? 'row--offtarget' : '');
  const tr = el('tr', {
    className: tone,
    dataset: { jobId: job.job_id },
  });

  // The whole row opens the job, exactly as the whole card does. The title
  // button stays -- in a table it is the sensible tab stop and the thing a
  // screen reader announces -- but hitting a 24-pixel target in a row you
  // have already read is not a reasonable ask.
  const open = () => ctx.onOpen(job.job_id);
  tr.addEventListener('click', (event) => {
    if (event.target.closest('[data-stops-open], button, a, input, select, label')) return;
    open();
  });
  // No `tabindex` and no Space handler on the row.
  //
  // A card is ONE object and earns a tab stop. A row already contains a
  // checkbox, a title button, a status select, a date input and a link, so
  // making the row focusable too added a sixth stop per row -- roughly four
  // times the tabbing to cross the same table -- and its Space handler
  // suppressed page scrolling whenever a row held focus.
  //
  // The keyboard path through a table is its controls, and the title button
  // is the one that opens the job. Clicking the row is a mouse affordance on
  // top of that, not a replacement for it.

  for (const column of COLUMNS) {
    if (!visible.has(column.id)) continue;
    tr.appendChild(cell(column, job, ctx, { gated, offTarget }));
  }
  return tr;
}

/**
 * "×4" beside a title that stands for four postings, with the places in the
 * tooltip. Compact because the table is dense, present because a grouped row
 * that says nothing is a row that lies by omission. Null for a singleton.
 */
function groupMarker(job) {
  const count = Number(job.duplicate_count || 1);
  if (count <= 1) return null;
  const places = (job.sibling_locations || []).join(', ');
  return el('span', {
    className: 'row__group num',
    text: `×${count}`,
    attrs: {
      title: places
        ? t('table.groupTitle', { n: count, places })
        : t('table.groupTitleBare', { n: count }),
      'aria-label': t('table.groupLabel', { n: count }),
    },
  });
}

function cell(column, job, ctx, aside) {
  switch (column.id) {
    case 'score':
      return el('td', { className: 'col--num' }, [scoreCell(job.match_score, 'match')]);

    case 'confidence':
      return el('td', { className: 'col--num' }, [scoreCell(job.data_confidence, 'confidence')]);

    case 'company':
      return el('td', {}, [el('span', {
        className: 'cell--company',
        text: job.company_name || '; ',
        attrs: job.company_name ? { title: job.company_name } : {},
      })]);

    case 'title':
      return el('th', { className: 'col--title', attrs: { scope: 'row' } }, [
        el('div', { className: 'cell__titlewrap' }, [
          aside.gated
            ? el('span', {
              className: 'row__blocked',
              text: '✕',
              attrs: {
                title: t('card.gated'),
                'aria-label': t('table.gatedShort'),
              },
            })
            : aside.offTarget
            ? el('span', {
              className: 'row__offtarget',
              text: '~',
              attrs: {
                title: job.title_reason || t('table.offTargetShort'),
                'aria-label': t('table.offTargetShort'),
              },
            })
            : null,
          button(job.title || t('absent.untitled'), () => ctx.onOpen(job.job_id), {
            className: 'cell__title-btn',
            ariaLabel: t('table.openDetails', { title: job.title, company: job.company_name }),
          }),
          // The Table defaults to ungrouped, so this is normally absent. When
          // the toggle is on it must still be here: a row standing for four
          // postings without saying so is exactly the thing grouping is not
          // allowed to do, in whichever view it happens.
          groupMarker(job),
        ]),
      ]);

    case 'location':
      return el('td', {}, [
        el('span', { text: truncate(job.location_raw || t('drawer.notStated'), 34) }),
        job.work_model ? el('span', { className: 'cell__sub', text: vocabLabel(job.work_model) }) : null,
      ]);

    case 'source':
      return el('td', {}, [
        el('span', { text: vocabLabel(job.provider) }),
      ]);

    case 'technologies': {
      const all = job.technologies || [];
      if (!all.length) {
        return el('td', {}, [el('span', { className: 'cell__tech cell__tech--none', text: t('table.noneRecorded') })]);
      }
      const full = all.map((t) => `${t.label || t.signal_id} (${humanLabel(t.prominence)})`).join('\n');
      // Rarest first, original order as the tie-break, so the two shown are the
      // ones that tell this row apart from its neighbours.
      const frequency = ctx.frequency || new Map();
      const ranked = signalLabels(job)
        .map((label, index) => ({ label, index, n: frequency.get(label) || 0 }))
        .sort((a, b) => a.n - b.n || a.index - b.index);
      const shown = ranked.slice(0, 2);
      const rest = ranked.length - shown.length;
      return el('td', {}, [
        el('span', { className: 'cell__tech', attrs: { title: full } },
          shown.map((row, index) => el('span', { className: 'cell__techline' }, [
            el('span', { className: 'cell__techname', text: row.label }),
            rest > 0 && index === shown.length - 1
              ? el('span', {
                className: 'cell__techmore',
                text: `+${rest}`,
                attrs: {
                  title: t('table.andMoreTools', {
                    n: rest,
                    names: ranked.slice(2).map((r) => r.label).join(', '),
                  }),
                },
              })
              : null,
          ]))),
      ]);
    }

    case 'salary': {
      const salary = formatSalary(job.salary);
      return el('td', {}, [el('span', {
        className: salary ? '' : 'fact--absent',
        text: salary || t('value.notStated'),
      })]);
    }

    case 'contract':
      return el('td', {}, [el('span', {
        className: job.employment_type ? '' : 'fact--absent',
        text: job.employment_type ? vocabLabel(job.employment_type) : t('value.notStated'),
      })]);

    case 'posted':
      return el('td', { className: 'col--num' }, [el('span', { className: 'num', text: formatDate(job.posted_at) })]);

    case 'freshness': {
      const age = freshness(job.freshness);
      return el('td', {}, [el('span', { className: `tag tag--${age.tone}`, text: age.label })]);
    }

    case 'eligibility':
      return el('td', {}, [el('span', {
        className: `pill pill--${job.eligibility_status === 'VERIFIED_NOT_ELIGIBLE'
          ? 'bad'
          : job.eligibility_status === 'UNRESOLVED' ? 'warn' : 'good'}`,
        text: eligibilityWords(job.eligibility_status),
      })]);

    case 'status':
      return el('td', {}, [select(statusOptions(), job.application_status, (value) => {
        ctx.onStatus(job.job_id, value);
      }, {
        className: 'select select--status',
        ariaLabel: t('card.statusLabel', { title: job.title }),
      })]);

    case 'applied': {
      // Never a boolean of its own: it writes the canonical status.
      const box = el('input', {
        className: 'checkbox',
        attrs: { type: 'checkbox', 'aria-label': t('table.markApplied', { title: job.title }) },
        props: { checked: Boolean(job.has_applied) },
        on: {
          change: (event) => ctx.onStatus(job.job_id, event.target.checked ? 'APPLIED' : 'SHORTLISTED'),
        },
      });
      return el('td', { className: 'col--pick' }, [box]);
    }

    case 'applied_at':
      return el('td', { className: 'col--num' }, [el('input', {
        className: 'input input--date',
        attrs: { type: 'date', 'aria-label': t('table.appliedDateFor', { title: job.title }) },
        props: { value: dateInputValue(job.applied_at) },
        on: {
          change: (event) => ctx.onAppliedDate(job.job_id, event.target.value || null),
        },
      })]);

    case 'saved':
      return el('td', { className: 'col--pick' }, [el('button', {
        className: `btn btn--icon${job.saved ? ' is-on' : ''}`,
        attrs: {
          type: 'button',
          'aria-pressed': job.saved ? 'true' : 'false',
          'aria-label': `${job.saved ? 'Unsave' : 'Save'} ${job.title}`,
        },
        on: { click: () => ctx.onSave(job.job_id, !job.saved) },
      }, [el('span', { text: job.saved ? '★' : '☆' })])]);

    case 'link':
      return el('td', {}, [
        extLink(job.url, `${t('action.apply')} ↗︎`, { className: 'btn btn--link' }),
      ]);

    default:
      return el('td', { text: '' });
  }
}

/** Skeleton rows, same column geometry, so nothing jumps when data lands. */
export function tableSkeleton(mount, count = 8) {
  mount.className = 'tablewrap';
  clear(mount);
  const visible = loadVisible();
  const columns = COLUMNS.filter((column) => visible.has(column.id));
  const table = el('table', { className: 'jobs', attrs: { 'aria-hidden': 'true' } }, [
    el('thead', {}, [el('tr', {}, columns.map((column) => el('th', {
      text: columnLabel(column),
      attrs: { scope: 'col' },
    })))]),
    el('tbody', {}, Array.from({ length: count }, () => el('tr', {}, columns.map(
      () => el('td', {}, [el('div', { className: 'sk sk--line' })]),
    )))),
  ]);
  mount.appendChild(el('div', { className: 'tablescroll' }, [table]));
}

