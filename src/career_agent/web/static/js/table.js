/**
 * table.js -- the List view (V3 handoff).
 *
 * Same store, same request, same job ids as Cards. What changes is density:
 * a comparison table you can scan, not a spreadsheet at presentation size.
 *
 * V3 CONTRACT. Match and Job are fixed and first; fifteen columns are
 * optional, and five of them are on by default (Location, Salary, Can you
 * take it, Progress, Saved). Each column has a MINIMUM width and a share of
 * what is left, so the default set fits a normal window and horizontal
 * scrolling is the last resort, never the layout. The page scrolls; the
 * table never scrolls vertically inside itself.
 *
 * Rows are a CSS grid over table elements with explicit ARIA roles, because
 * a grid is what gives every column a minimum AND a share, and changing the
 * display of a table would otherwise drop its semantics.
 *
 * One rule worth stating out loud: the "Applied" checkbox does NOT own a
 * boolean. `has_applied` is computed by the server from status and date, so
 * the checkbox PATCHes the canonical status (to APPLIED, or back to
 * SHORTLISTED) and then renders whatever the server said.
 */

import { el, button, extLink, replace, clear } from './dom.js';
import { t } from './i18n.js';
import {
  compactPlace, dateInputValue, formatDate, formatSalary, freshness, parseDate,
  relativeAge, statusLabel, statusOptions, vocabLabel,
} from './format.js';
import { eligibilityWords, scoreCell, searchFitIsReady } from './badges.js';
import { matchTone } from './cards.js';

//: v2: the V3 defaults apply once to everybody, whatever v1 remembered.
const STORAGE_KEY = 'careerAgent.table.columns.v2';
const ORDER_KEY = 'careerAgent.table.order.v2';

/**
 * Column order is the reading order. `min` is the narrowest a column may
 * get and `fr` its share of the rest (the V3 source's own numbers).
 */
export const COLUMNS = [
  {
    id: 'score', labelKey: 'column.scoreShort', fullKey: 'column.score', sort: 'score', fixed: true,
    track: '52px', min: 52,
  },
  { id: 'title', labelKey: 'column.title', sort: 'title', fixed: true, track: 'minmax(180px,2.4fr)', min: 180 },
  { id: 'confidence', labelKey: 'column.confidence', sort: 'confidence', track: 'minmax(76px,.7fr)', min: 76 },
  { id: 'company', labelKey: 'column.company', sort: 'company', track: 'minmax(110px,1fr)', min: 110 },
  { id: 'location', labelKey: 'column.location', track: 'minmax(120px,1.1fr)', min: 120 },
  { id: 'source', labelKey: 'column.source', track: 'minmax(92px,.8fr)', min: 92 },
  { id: 'technologies', labelKey: 'column.technologies', track: 'minmax(120px,1.1fr)', min: 120 },
  { id: 'salary', labelKey: 'column.salary', track: 'minmax(120px,1.1fr)', min: 120 },
  { id: 'contract', labelKey: 'column.contract', track: 'minmax(84px,.7fr)', min: 84 },
  { id: 'posted', labelKey: 'column.posted', sort: 'posted', track: 'minmax(84px,.6fr)', min: 84 },
  { id: 'freshness', labelKey: 'column.freshness', track: 'minmax(72px,.6fr)', min: 72 },
  { id: 'eligibility', labelKey: 'column.eligibility', track: 'minmax(96px,.8fr)', min: 96 },
  { id: 'status', labelKey: 'column.status', sort: 'status', track: 'minmax(112px,.85fr)', min: 112 },
  { id: 'applied', labelKey: 'column.applied', track: 'minmax(60px,.5fr)', min: 60 },
  { id: 'applied_at', labelKey: 'column.applied_at', track: 'minmax(124px,.75fr)', min: 124 },
  { id: 'saved', labelKey: 'column.saved', track: '48px', min: 48 },
  { id: 'link', labelKey: 'column.link', track: '60px', min: 60 },
];

const DEFAULT_VISIBLE = new Set(['score', 'title', 'location', 'salary', 'eligibility', 'status', 'saved']);

/** localStorage can throw (private mode, disabled site data). It is a nicety. */
export function loadVisible() {
  const fallback = new Set(DEFAULT_VISIBLE);
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return fallback;
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return fallback;
    const known = new Set(COLUMNS.map((c) => c.id));
    const restored = new Set(parsed.filter((id) => known.has(id)));
    for (const column of COLUMNS) if (column.fixed) restored.add(column.id);
    return restored;
  } catch {
    return fallback;
  }
}

/**
 * The columns in the reader's order. Unknown ids are dropped and columns
 * this build added since are appended in their default place, so a stored
 * order can never hide a column nor name one that no longer exists.
 */
export function orderedColumns() {
  let stored = [];
  try {
    stored = JSON.parse(window.localStorage.getItem(ORDER_KEY) || '[]');
  } catch {
    stored = [];
  }
  const byId = new Map(COLUMNS.map((column) => [column.id, column]));
  const order = (Array.isArray(stored) ? stored : []).filter((id) => byId.has(id));
  for (const column of COLUMNS) if (!order.includes(column.id)) order.push(column.id);
  // Match and Job always lead, whatever was stored.
  const fixed = COLUMNS.filter((column) => column.fixed).map((column) => column.id);
  return [...fixed, ...order.filter((id) => !fixed.includes(id))].map((id) => byId.get(id));
}

function saveOrder(ids) {
  try {
    window.localStorage.setItem(ORDER_KEY, JSON.stringify(ids));
  } catch {
    /* A column order is not worth an error message. */
  }
}

function saveVisible(visible) {
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(Array.from(visible)));
  } catch {
    /* A column preference is not worth an error message. */
  }
}

/** The grid tracks and the narrowest the table may get, for these columns. */
function geometry(columns) {
  return {
    tracks: columns.map((column) => column.track).join(' '),
    // Every minimum, every 12px gap and the 16px padding on each side.
    min: columns.reduce((sum, column) => sum + column.min, 0) + 12 * (columns.length - 1) + 32,
  };
}

/**
 * @param {HTMLElement} mount
 * @param {object[]} items
 * @param {object} ctx -- {sort, direction, onSort, onOpen, onStatus, onSave,
 *                         onAppliedDate, visible}
 */
export function renderTable(mount, items, ctx) {
  mount.className = 'tablewrap';
  const visible = ctx.visible || loadVisible();
  const columns = orderedColumns().filter((column) => visible.has(column.id));
  // How common each signal is across the rows actually on screen. A signal 12
  // of 14 rows share distinguishes nothing; the two rarest do.
  const context = { ...ctx, frequency: signalFrequency(items), companyShown: visible.has('company') };

  const table = el('table', {
    className: 'jobs',
    attrs: { role: 'table', 'aria-label': t('table.caption') },
  }, [
    el('thead', { attrs: { role: 'rowgroup' } }, [head(columns, context)]),
    el('tbody', { attrs: { role: 'rowgroup' } }, items.map((job) => row(job, columns, context))),
  ]);
  sizeTable(table, columns);

  const scroller = el('div', {
    className: 'tablescroll',
    attrs: { tabindex: '0', role: 'region', 'aria-label': t('table.help') },
  }, [table]);
  const wrap = el('div', { className: 'tablescrollwrap' }, [scroller]);

  replace(mount, [wrap]);
  watchScrollEdges(wrap, scroller);
  return mount;
}

/** Through the CSSOM: the page's CSP refuses a `style` attribute. */
function sizeTable(table, columns) {
  const { tracks, min } = geometry(columns);
  table.style.setProperty('--cols', tracks);
  table.style.minWidth = `${min}px`;
}

/**
 * Mark which sides still hold content the reader cannot see. The CSS turns that
 * into an inset veil, so a value sliced by the edge of the region fades under
 * it instead of reading as a whole number.
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

/** A column heading, in the reader's language, resolved at render. */
function columnLabel(column) {
  return t(column.labelKey);
}

/** The heading's whole name: the short Match heading has a long one. */
function columnName(column) {
  return t(column.fullKey || column.labelKey);
}

function signalLabels(job) {
  const seen = new Set();
  for (const tech of job.technologies || []) {
    const label = tech.label || tech.signal_id;
    if (label) seen.add(String(label));
  }
  return Array.from(seen);
}

/**
 * The Columns popover: every column with a switch, and up and down to move
 * it. Match and Job are always shown and always first, so they have no
 * switch. Reset puts back the default order and set.
 */
export function renderColumnsMenu(host, onChange) {
  const visible = loadVisible();
  const all = orderedColumns();
  const fixed = all.filter((column) => column.fixed);
  const columns = all.filter((column) => !column.fixed);
  const move = (index, by) => {
    const ids = columns.map((column) => column.id);
    const [id] = ids.splice(index, 1);
    ids.splice(index + by, 0, id);
    saveOrder(ids);
    onChange(loadVisible());
    renderColumnsMenu(host, onChange);
    const again = host.querySelectorAll(`[data-move="${by < 0 ? 'up' : 'down'}"]`)[index + by];
    if (again && !again.disabled) again.focus();
  };
  const rows = columns.map((column, index) => {
    const on = visible.has(column.id);
    const toggle = button(columnName(column), () => {
      if (on) visible.delete(column.id);
      else visible.add(column.id);
      saveVisible(visible);
      onChange(visible);
      renderColumnsMenu(host, onChange);
      const again = [...host.querySelectorAll('.colrow')]
        .find((node) => node.dataset.column === column.id)?.querySelector('.colrow__switch');
      if (again) again.focus();
    }, {
      className: 'colrow__switch',
      attrs: { role: 'switch', 'aria-checked': String(on) },
    });
    toggle.prepend(el('span', { className: 'colrow__track', attrs: { 'aria-hidden': 'true' } }, [
      el('span', { className: 'colrow__knob' }),
    ]));
    return el('li', { className: 'colrow', dataset: { column: column.id } }, [
      toggle,
      button('▲', () => move(index, -1), {
        className: 'colrow__move',
        ariaLabel: t('list.moveUp', { column: columnName(column) }),
        attrs: { 'data-move': 'up', disabled: index === 0 ? 'disabled' : null },
      }),
      button('▼', () => move(index, 1), {
        className: 'colrow__move',
        ariaLabel: t('list.moveDown', { column: columnName(column) }),
        attrs: { 'data-move': 'down', disabled: index === columns.length - 1 ? 'disabled' : null },
      }),
    ]);
  });
  const pinned = fixed.map((column) => el('li', { className: 'colrow colrow--fixed' }, [
    el('span', { className: 'colrow__name', text: columnName(column) }),
    el('span', { className: 'colrow__fixed', text: t('list.fixed') }),
  ]));
  replace(host, [
    el('div', { className: 'colmenu__head' }, [
      el('strong', { className: 'colmenu__title', text: t('list.columnsTitle') }),
      el('span', { className: 'colmenu__help', text: t('list.columnsHelp') }),
    ]),
    el('ul', { className: 'colmenu__fixed', attrs: { 'aria-label': t('list.fixed') } }, pinned),
    el('ul', { className: 'colmenu__rows', attrs: { 'aria-label': t('table.visibleColumns') } }, rows),
    el('div', { className: 'colmenu__foot' }, [button(t('list.resetColumns'), () => {
      try {
        window.localStorage.removeItem(ORDER_KEY);
        window.localStorage.removeItem(STORAGE_KEY);
      } catch {
        /* Nothing stored, nothing to reset. */
      }
      onChange(loadVisible());
      renderColumnsMenu(host, onChange);
    }, { className: 'colmenu__reset' }),
    button(t('list.done'), () => onChange(null), { className: 'colmenu__done' })]),
  ]);
}

/**
 * The visible rows and columns as CSV, the way the list reads them: words,
 * not codes, and the posting's own link. Nothing beyond what is on screen.
 */
export function toCsv(items, visible) {
  const columns = orderedColumns().filter((column) => visible.has(column.id));
  const quote = (value) => {
    const text = String(value ?? '');
    // A cell an employer wrote may start like a formula; a spreadsheet must
    // read it as text, never run it.
    const safe = /^[=+\-@\t\r]/.test(text) ? `'${text}` : text;
    return /[",\r\n]/.test(safe) ? `"${safe.replace(/"/g, '""')}"` : safe;
  };
  const value = (column, job) => {
    switch (column.id) {
      case 'score': return job.match_score ?? '';
      case 'confidence': return job.data_confidence ?? '';
      case 'company': return job.company_name || '';
      case 'title': return job.title || '';
      case 'location': return job.location_raw || '';
      case 'source': return vocabLabel(job.provider);
      case 'technologies': return signalLabels(job).join('; ');
      case 'salary': return formatSalary(job.salary) || '';
      case 'contract': return job.employment_type ? vocabLabel(job.employment_type) : '';
      case 'posted': return job.posted_at ? formatDate(job.posted_at) : '';
      case 'freshness': return freshness(job.freshness).label;
      case 'eligibility': return eligibilityWords(job.eligibility_status);
      case 'status': return statusLabel(job.application_status || 'DISCOVERED');
      case 'applied': return job.has_applied ? t('value.yes') : '';
      case 'applied_at': return dateInputValue(job.applied_at);
      case 'saved': return job.saved ? t('value.yes') : '';
      case 'link': return job.url || '';
      default: return '';
    }
  };
  const lines = [columns.map((column) => quote(columnName(column))).join(',')];
  for (const job of items) lines.push(columns.map((column) => quote(value(column, job))).join(','));
  return `${lines.join('\r\n')}\r\n`;
}

function head(columns, ctx) {
  const tr = el('tr', { className: 'jobs__head', attrs: { role: 'row' } });
  for (const column of columns) {
    const isSorted = column.sort && column.sort === ctx.sort;
    const th = el('th', {
      className: `col--${column.id}`,
      attrs: {
        role: 'columnheader',
        scope: 'col',
        title: columnName(column),
        'aria-sort': column.sort ? (isSorted ? (ctx.direction === 'asc' ? 'ascending' : 'descending') : 'none') : null,
      },
    });
    if (column.sort) {
      const sortButton = button(columnLabel(column), () => ctx.onSort(column.sort), {
        className: `th__sort${isSorted ? ' is-sorted' : ''}`,
        ariaLabel: t('table.sortBy', { column: columnName(column) }),
      });
      th.appendChild(sortButton);
    } else {
      th.appendChild(el('span', { className: 'th__label', text: columnLabel(column) }));
    }
    tr.appendChild(th);
  }
  return tr;
}

function row(job, columns, ctx) {
  // `blockers` is the EMPLOYER stating a requirement. `screening_state` is
  // this search deciding the posting is not the work asked for.
  const gated = (job.blockers || []).length > 0;
  const offTarget = String(job.screening_state).toUpperCase() === 'BLOCKED';
  const tone = gated ? 'row--blocked' : (offTarget ? 'row--offtarget' : '');
  const tr = el('tr', {
    className: tone,
    attrs: { role: 'row' },
    dataset: { jobId: job.job_id },
  });
  // The whole row opens the job, exactly as the whole card does. The title
  // button stays: in a table it is the tab stop that opens the job.
  tr.addEventListener('click', (event) => {
    if (event.target.closest('[data-stops-open], button, a, input, select, label')) return;
    ctx.onOpen(job.job_id);
  });
  for (const column of columns) tr.appendChild(cell(column, job, ctx, { gated, offTarget }));
  return tr;
}

/**
 * "x4" beside a title that stands for four postings, with the places in the
 * tooltip. A grouped row that says nothing is a row that lies by omission.
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

/** One plain cell: a line of text that ellipsizes, its whole value on hover. */
function textCell(column, text, { absent = false, sub = '' } = {}) {
  return el('td', { className: `col--${column.id}`, attrs: { role: 'cell' } }, [
    el('span', {
      className: `cell__text${absent ? ' fact--absent' : ''}`,
      text,
      attrs: { title: sub ? `${text} · ${sub}` : text },
    }),
  ]);
}

function pill(column, text, tone, title = text) {
  return el('td', { className: `col--${column.id}`, attrs: { role: 'cell' } }, [
    el('span', { className: `tpill tpill--${tone}`, text, attrs: { title } }),
  ]);
}

function cell(column, job, ctx, aside) {
  switch (column.id) {
    case 'score':
      return el('td', { className: 'col--score', attrs: { role: 'cell' } }, [matchPill(job.match_score)]);

    case 'confidence': {
      const value = job.data_confidence;
      if (value === null || value === undefined) return textCell(column, t('value.notStated'), { absent: true });
      return el('td', { className: 'col--confidence', attrs: { role: 'cell' } }, [scoreCell(value, 'confidence')]);
    }

    case 'company':
      return textCell(column, job.company_name || t('absent.company'), { absent: !job.company_name });

    case 'title':
      return el('th', { className: 'col--title', attrs: { role: 'rowheader', scope: 'row' } }, [
        el('div', { className: 'cell__titlewrap' }, [
          el('div', { className: 'cell__titleline' }, [
            aside.gated
              ? el('span', {
                className: 'row__blocked', text: '✕',
                attrs: { title: t('card.gated'), 'aria-label': t('table.gatedShort') },
              })
              : aside.offTarget
                ? el('span', {
                  className: 'row__offtarget', text: '~',
                  attrs: {
                    title: job.title_reason || t('table.offTargetShort'),
                    'aria-label': t('table.offTargetShort'),
                  },
                })
                : null,
            button(job.title || t('absent.untitled'), () => ctx.onOpen(job.job_id), {
              className: 'cell__title-btn',
              ariaLabel: t('table.openDetails', { title: job.title, company: job.company_name }),
              attrs: { title: job.title || '' },
            }),
            groupMarker(job),
          ]),
          ctx.companyShown
            ? null
            : el('span', {
              className: 'cell__company', text: job.company_name || '', attrs: { title: job.company_name || '' },
            }),
        ]),
      ]);

    case 'location': {
      const place = compactPlace(job.location_raw, job.work_model);
      return textCell(column, place.text || t('drawer.notStated'), { absent: !place.text, sub: place.full });
    }

    case 'source':
      return textCell(column, vocabLabel(job.provider));

    case 'technologies': {
      const frequency = ctx.frequency || new Map();
      // Rarest first, original order as the tie-break, so the ones shown are
      // the ones that tell this row apart from its neighbours.
      const ranked = signalLabels(job)
        .map((label, index) => ({ label, index, n: frequency.get(label) || 0 }))
        .sort((a, b) => a.n - b.n || a.index - b.index)
        .map((entry) => entry.label);
      if (!ranked.length) return textCell(column, t('table.noneRecorded'), { absent: true });
      const td = textCell(column, ranked.slice(0, 3).join(', '));
      td.firstChild.title = ranked.join('\n');
      return td;
    }

    case 'salary': {
      const salary = formatSalary(job.salary);
      return textCell(column, salary || t('card.salaryUnstated'), { absent: !salary });
    }

    case 'contract':
      return textCell(column, job.employment_type ? vocabLabel(job.employment_type) : t('value.notStated'), {
        absent: !job.employment_type,
      });

    case 'posted': {
      if (!parseDate(job.posted_at)) return textCell(column, t('card.noPostedDate'), { absent: true });
      const td = textCell(column, relativeAge(job.posted_at));
      td.firstChild.title = formatDate(job.posted_at);
      return td;
    }

    case 'freshness': {
      const age = freshness(job.freshness);
      return textCell(column, age.label, { absent: age.tone === 'old' });
    }

    case 'eligibility': {
      const status = aside.gated ? 'VERIFIED_NOT_ELIGIBLE' : (job.eligibility_status || 'UNRESOLVED');
      const words = eligibilityWords(status);
      if (status === 'VERIFIED_ELIGIBLE') return pill(column, t('table.take.yes'), 'm1', words);
      if (status === 'VERIFIED_NOT_ELIGIBLE') return pill(column, t('table.take.no'), 'red', words);
      return pill(column, t('table.take.check'), 'm3', words);
    }

    case 'status': {
      // The pill IS the control: the status can be changed here, as before,
      // and at rest it reads like the V3 Progress pill.
      const status = String(job.application_status || 'DISCOVERED');
      const control = el('select', {
        className: `tpill select--status select--pill tpill--${statusTone(status)}`,
        attrs: { 'aria-label': t('card.statusLabel', { title: job.title }) },
        on: { change: (event) => ctx.onStatus(job.job_id, event.target.value) },
      }, statusOptions().map((option) => el('option', {
        // V3 reads a job nobody has touched as "Not started".
        text: option.value === 'DISCOVERED' ? t('table.notStarted') : option.label,
        attrs: { value: option.value },
      })));
      control.value = status;
      return el('td', { className: 'col--status', attrs: { role: 'cell' } }, [control]);
    }

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
      return el('td', { className: 'col--applied', attrs: { role: 'cell' } }, [box]);
    }

    case 'applied_at':
      return el('td', { className: 'col--applied_at', attrs: { role: 'cell' } }, [el('input', {
        className: 'input input--date',
        attrs: { type: 'date', 'aria-label': t('table.appliedDateFor', { title: job.title }) },
        props: { value: dateInputValue(job.applied_at) },
        on: { change: (event) => ctx.onAppliedDate(job.job_id, event.target.value || null) },
      })]);

    case 'saved':
      return el('td', { className: 'col--saved', attrs: { role: 'cell' } }, [button(
        job.saved ? '♥' : '♡',
        () => ctx.onSave(job.job_id, !job.saved),
        {
          className: `theart${job.saved ? ' is-saved' : ''}`,
          ariaLabel: t(job.saved ? 'card.unsaveLabel' : 'card.saveLabel', { title: job.title }),
          attrs: { 'aria-pressed': job.saved ? 'true' : 'false' },
        },
      )]);

    case 'link':
      return el('td', { className: 'col--link', attrs: { role: 'cell' } }, [
        extLink(job.url, `${t('table.openAd')} ↗`, { className: 'tlink', title: t('table.openAdTitle') }),
      ]);

    default:
      return el('td', { attrs: { role: 'cell' } });
  }
}

/** Where an application stands, as one of the V3 pill colours. */
function statusTone(status) {
  if (['OFFER', 'HIRED'].includes(status)) return 'm1';
  if (status === 'INTERVIEW') return 'blue';
  if (status === 'APPLIED') return 'm2';
  if (status === 'SHORTLISTED') return 'chip';
  if (['REJECTED', 'WITHDRAWN', 'ARCHIVED'].includes(status)) return 'm3';
  return 'none';
}

/** The match as the card shows it: a toned pill, or the not-ready words. */
function matchPill(score) {
  const tone = searchFitIsReady() ? matchTone(score) : null;
  if (!tone) return scoreCell(score, 'match');
  return el('span', {
    className: `matchpill matchpill--${tone.tone} num`,
    text: `${Math.round(Number(score))}%`,
    attrs: { title: t(tone.key) },
  });
}

/** Skeleton rows, same column geometry, so nothing jumps when data lands. */
export function tableSkeleton(mount, count = 8) {
  mount.className = 'tablewrap';
  clear(mount);
  const columns = orderedColumns().filter((column) => loadVisible().has(column.id));
  const table = el('table', { className: 'jobs', attrs: { 'aria-hidden': 'true' } }, [
    el('thead', {}, [el('tr', { className: 'jobs__head' }, columns.map((column) => el('th', {
      text: columnLabel(column),
      attrs: { scope: 'col' },
    })))]),
    el('tbody', {}, Array.from({ length: count }, () => el('tr', {}, columns.map(
      () => el('td', {}, [el('div', { className: 'sk sk--line' })]),
    )))),
  ]);
  sizeTable(table, columns);
  mount.appendChild(el('div', { className: 'tablescroll' }, [table]));
}
