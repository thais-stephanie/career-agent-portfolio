/**
 * sources.js: the source-coverage matrix, in the product rather than in a file.
 *
 * `/api/sources` existed and nothing called it, so the classification was a
 * document that happened to have an endpoint. A capability matrix nobody can
 * open is a claim, not a feature: the question it answers; "why is there
 * nothing here from Gupy?": is asked of the interface, not of `docs/`.
 *
 * Every row is REPORTED, never asserted: the server re-checks each coverage
 * claim against the provider registry and the corpus before it answers, so a
 * source cannot read as operational because a YAML file said so.
 *
 * Rendered once, on first open. Nothing here polls, and the panel starts
 * closed: the catalogue changes when somebody edits it, not while you look.
 */

import { el, clear, button, select, field } from './dom.js';
import { t } from './i18n.js';
import { MISSING } from './format.js';
import * as api from './api.js';

/**
 * The five words a person who is not debugging a connector actually wants,
 * in the order that most needs saying.
 *
 * `coverage` below is precise and is written for somebody fixing an adapter.
 * This answers a different question -- is anything wrong, and is it mine to
 * fix -- and answering it with PARTIAL and UNDOCUMENTED is answering a
 * question nobody asked.
 */
const STATES = [
  'ATTENTION', 'NEEDS_SETUP', 'HEALTHY', 'NOT_RUN', 'WAITING',
  'BLOCKED_PROVIDER', 'DISABLED_QUOTA', 'NOTHING_PUBLISHED', 'DISABLED',
];

/** The existing `.tag--*` tones, not new ones: this panel is not special. */
/**
 * The states for which "what happened when we ran it" is a real question.
 *
 * A DISABLED or WAITING source has not been asked and will not be, so its
 * observation is a restatement of its reason -- in the operator's words rather
 * than the reader's.
 */
const RUNNABLE = new Set(['HEALTHY', 'ATTENTION', 'NEEDS_SETUP', 'NOT_RUN']);

const STATE_TONE = {
  HEALTHY: 'good',
  ATTENTION: 'bad',
  NEEDS_SETUP: 'warn',
  WAITING: 'warn',
  NOT_RUN: 'neutral',
  DISABLED: 'muted',
  BLOCKED_PROVIDER: 'warn',
  DISABLED_QUOTA: 'muted',
  NOTHING_PUBLISHED: 'neutral',
};

/**
 * Coverage ids, in the order a person cares about them.
 *
 * `UNDOCUMENTED` was missing from both lists, because both were written
 * before it existed. The effect was that the two rows V3 added -- the two
 * most interesting rows in the panel, a Workday feed and a Gupy feed that
 * both answer -- sorted after everything else with no tone on the tag.
 */
const ORDER = [
  'OPERATIONAL', 'PARTIAL', 'BLOCKED_PROVIDER', 'UNDOCUMENTED', 'PLANNED', 'BLOCKED', 'UNSUPPORTED',
];

/** The existing `.tag--*` tones, not new ones: this panel is not special. */
const TONE = {
  OPERATIONAL: 'good',
  PARTIAL: 'warn',
  UNDOCUMENTED: 'warn',
  PLANNED: 'neutral',
  BLOCKED: 'bad',
  UNSUPPORTED: 'neutral',
  BLOCKED_PROVIDER: 'warn',
};

export function createSourcesPanel(host, { collection = null } = {}) {
  //: Whether "Each job source" is open. The panel redraws after every refresh
  //: or timing change, and a section that snapped shut under the button just
  //: pressed would lose the person's place.
  let eachOpen = false;
  let loaded = false;
  let refreshTimer = null;

  /**
   * Draw the catalogue again in the reader's language, from the payload
   * already in hand.
   *
   * The panel renders ONCE, which is right -- the catalogue changes when
   * somebody edits it, not while you look -- and it meant that switching to
   * Portuguese left this table's column headings, its state chips and its
   * explanations in English under a translated heading. A language switch is
   * the one event that has to redraw a thing that otherwise never redraws.
   *
   * It re-renders rather than re-fetching: the answer has not changed, only
   * the words for it.
   */
  function retranslate() {
    if (lastPayload) render(lastPayload);
  }

  //: The last answer, kept so a language switch can redraw without asking
  //: the server the same question again.
  let lastPayload = null;

  async function load(force = false) {
    if (loaded && !force) return;
    loaded = true;
    clear(host);
    host.appendChild(el('div', { className: 'src' }, [
      el('p', { className: 'src__note', text: t('sources.loading') }),
    ]));
    try {
      const payload = await api.getSources();
      try { payload.maintenance = await api.getSourceMaintenance(); }
      catch { payload.maintenance = null; }
      lastPayload = payload;
      render(payload);
      clearTimeout(refreshTimer);
      if (payload.maintenance?.maintenance_running || payload.maintenance?.app_refresh_running
          || (payload.refresh || []).some(row => row.state === 'RUNNING')) {
        refreshTimer = setTimeout(() => {
          if (!host.closest('[hidden]')) void load(true);
        }, 5000);
      }
    } catch (error) {
      loaded = false;
      clear(host);
      host.appendChild(el('div', { className: 'src' }, [
        el('p', { className: 'src__note', text: error.userMessage || error.message }),
      ]));
    }
  }

  /**
   * SOURCE HEALTH, one row per source that can run: when it last worked and
   * what that means now. Read from the same rows as the cards below and the
   * sidebar's "N sources need attention", so the three never disagree.
   * "Refresh due sources" runs only the due ones; nothing runs by opening it.
   */
  //: What the last "Refresh due sources" press led to. Kept here, not in the
  //: node, because the panel redraws after the press and must say it again.
  let healthNotice = '';

  function healthTable(rows, sources) {
    const due = rows.filter((row) => row.due).length;
    const attention = rows.filter((row) => row.needs_attention).length;
    const notice = el('p', {
      className: 'src__note', text: healthNotice, attrs: { 'aria-live': 'polite', id: 'health-notice' },
    });
    const running = rows.some((row) => row.state === 'RUNNING');
    const refreshDue = button(t('sources.refreshDue'), async () => {
      if (!collection) return;
      refreshDue.disabled = true;
      notice.textContent = t('sources.refreshDueStarting');
      const outcome = await collection.start('due');
      if (outcome && outcome.nothingDue) {
        healthNotice = t('sources.nothingDue');
      } else if (outcome && outcome.error) {
        healthNotice = outcome.error;
      } else if (outcome && outcome.alreadyRunning) {
        healthNotice = t('sources.alreadyRunning');
      } else {
        const started = outcome && typeof outcome.due === 'number' ? outcome.due : due;
        healthNotice = t('sources.refreshDueStarted', { n: started });
      }
      notice.textContent = healthNotice;
      refreshDue.disabled = false;
      void load(true);
    }, { className: 'btn', attrs: { id: 'refresh-due' } });
    refreshDue.disabled = running || !collection || Boolean(collection.state().active);
    const table = el('table', { className: 'src__health-table' }, [
      el('thead', {}, [el('tr', {}, [
        el('th', { text: t('sources.healthSource') }),
        el('th', { text: t('sources.healthLastSuccess') }),
        el('th', { text: t('sources.healthStatus') }),
      ])]),
      el('tbody', {}, rows.map((row) => {
        const source = sources.find((entry) => entry.id === row.source_id) || {};
        const detail = row.cooldown_until
          ? t('sources.coolingDown', { date: shortDate(row.cooldown_until) })
          : row.reason ? t(`sources.reason.${row.reason}`) : '';
        return el('tr', {
          dataset: { healthSource: row.source_id, state: row.state, due: String(Boolean(row.due)) },
          className: row.needs_attention ? 'is-attention' : '',
        }, [
          el('td', {}, [
            el('span', { text: row.name || source.name || row.source_id }),
            source.experimental ? el('span', { className: 'tag tag--warn', text: t('sources.experimentalTag') }) : null,
          ].filter(Boolean)),
          el('td', { text: row.last_success ? shortDate(row.last_success) : t('sources.neverFresh') }),
          el('td', {}, [
            el('strong', { text: t(`sources.state.${row.state}`) }),
            detail ? el('span', { className: 'src__reason', text: ` ${detail}` }) : null,
          ].filter(Boolean)),
        ]);
      })),
    ]);
    return el('section', { className: 'src__health', attrs: { 'aria-labelledby': 'health-head' } }, [
      el('h3', { text: t('sources.healthTitle'), attrs: { id: 'health-head' } }),
      el('p', { text: t('sources.healthSummary', { due, attention }) }),
      el('div', { className: 'src__health-actions' }, [refreshDue, notice]),
      table,
    ]);
  }

  function render(payload) {
    const sources = payload.sources || [];
    const groups = new Map(ORDER.map((key) => [key, []]));
    for (const source of sources) {
      const group = source.state === 'BLOCKED_PROVIDER' ? source.state : source.coverage;
      if (!groups.has(group)) groups.set(group, []);
      groups.get(group).push(source);
    }

    clear(host);
    // Everything goes inside one padded container. The rail's other panels
    // each carry their own inset and this one had none, so its text ran to
    // the very edge of the column.
    const body = el('details', { className: 'src' }, [
      el('summary', { text: t('settings.sourceDetails') }),
    ]);
    host.appendChild(healthTable(payload.refresh || [], sources));
    host.appendChild(maintenanceSummary(payload.maintenance));
    host.appendChild(el('p', { text: t('settings.sourceHelp') }));
    // EXPERIMENTAL SOURCES, IN THE OPEN. A source the site itself restricts
    // is never switched on quietly: the warning is on the card, in plain
    // words, above the switch, and nothing runs until the person ticks that
    // they read it.
    for (const source of sources.filter((entry) => entry.experimental)) {
      host.appendChild(experimentalCard(source));
    }
    // EACH SOURCE, ONE CLICK AWAY. Twenty-odd cards, each with a status, a
    // timing menu and a button, made Settings about ten thousand pixels tall
    // before a single preference; most people never change one of them, and
    // "Find jobs now" runs all of them. The count is in the summary.
    const refreshRows = payload.refresh || [];
    const pausedCount = refreshRows.filter((row) => row.state === 'PAUSED').length;
    const each = el('details', {
      className: 'src__each',
      props: { open: eachOpen },
      on: { toggle: (event) => { eachOpen = event.target.open; } },
    }, [
      el('summary', {
        className: 'src__eachsummary',
        text: t('settings.sourceEach', { n: refreshRows.length, paused: pausedCount }),
      }),
    ]);
    host.appendChild(each);
    each.appendChild(el('div', { className: 'career__cards' }, refreshRows.map(row => {
      const source = sources.find(entry => entry.id === row.source_id) || {};
      return el('article', { className: 'career__card', dataset: { source: row.source_id } }, [
        el('h3', { text: row.name || source.name }),
        el('p', { text: t('settings.sourceMarket', { market: source.region
          ? t(`settings.market.${source.region}`) : t('value.notStated') }) }),
        el('strong', { text: t(`sources.state.${row.state}`) }),
        row.state === 'PAUSED' ? el('p', { text: t(source.refresh_mode === 'PAUSED'
          ? 'settings.sourcePausedByYou' : 'settings.sourcePaused') }) : null,
        row.state === 'FAILED' ? el('p', { text: t('settings.sourceFailure') }) : null,
        row.reason ? el('p', { className: 'src__reason', text: t(`sources.reason.${row.reason}`) }) : null,
        row.state === 'STALE' ? el('p', { className: 'src__reason', text: t('sources.staleHelp') }) : null,
        row.state === 'BLOCKED' ? el('p', { text: row.blocker || source.reason_plain }) : null,
        el('p', { text: progressText(row) }),
        el('p', { text: t('settings.sourceUpdated', {
          date: row.last_success ? shortDate(row.last_success) : t('sources.neverFresh'),
        }) }),
        source.can_refresh ? sourceControls(source, row) : null,
      ]);
    })));
    host.appendChild(body);

    // IS ANYTHING WRONG. First, and in five words, because the matrix below
    // answers "what could this product collect" while a person opening this
    // panel is usually asking "is something broken".
    const states = payload.states || {};
    const shown = STATES.filter((state) => states[state]);
    if (shown.length) {
      body.appendChild(el('ul', { className: 'src__states' }, shown.map((state) => el(
        'li',
        { className: 'src__state' },
        [
          el('span', {
            className: `tag tag--${STATE_TONE[state] || 'muted'}`,
            text: t(`source.state.${state}`),
          }),
          el('span', { className: 'src__n num', text: String(states[state]) }),
        ],
      ))));
    }

    // The board/connector distinction, next, because it is the number most
    // easily overstated. 234 boards reached by three adapters is not 234
    // sources, and the panel says so before it lists anything.
    const live = sources.filter((s) => s.boards);
    if (live.length) {
      const boards = live.reduce((sum, s) => sum + (s.boards || 0), 0);
      const producing = live.reduce((sum, s) => sum + (s.boards_with_postings || 0), 0);
      body.appendChild(el('p', { className: 'src__lede' }, [
        el('strong', {
          text: live.length === 1
            ? t('sources.oneFamily')
            : t('sources.families', { n: live.length }),
        }),
        el('span', {
          // Two keys rather than one sentence with a conditional verb in it.
          // English inflects the verb and Portuguese inflects it differently,
          // and a translator handed `' reaches'` cannot see what agrees with
          // what.
          text: ' ' + t(live.length === 1 ? 'sources.reachOne' : 'sources.reachMany', {
            boards,
            producing,
          }),
        }),
      ]));
    }

    for (const key of groups.keys()) {
      const rows = groups.get(key);
      if (!rows.length) continue;
      body.appendChild(el('h3', { className: 'src__head' }, [
        el('span', {
          className: `tag tag--${TONE[key] || 'muted'}`,
          // The plain wording, with the exact classification as the tooltip
          // so the two vocabularies stay visibly connected rather than
          // becoming two different claims.
          text: key === 'BLOCKED_PROVIDER'
            ? t('source.state.BLOCKED_PROVIDER')
            : rows[0].coverage_plain || rows[0].coverage_label,
          attrs: { title: rows[0].coverage_label },
        }),
        el('span', { className: 'src__n num', text: String(rows.length) }),
      ]));
      body.appendChild(sourceTable(rows));
    }

    body.appendChild(refreshSection(payload.refresh || []));
    body.appendChild(el('p', { className: 'src__note', text: payload.note || '' }));
  }

  function experimentalCard(source) {
    const on = Boolean(source.experimental.opted_in);
    const usable = source.experimental.available !== false;
    const status = el('p', { attrs: { role: 'status', 'aria-live': 'polite' } });
    const ackId = `experimental-ack-${source.id}`;
    const ack = el('input', { attrs: { type: 'checkbox', id: ackId } });
    const toggle = button(t(on ? 'experimental.disable' : 'experimental.enable'), async () => {
      if (!on && !ack.checked) {
        status.textContent = t('experimental.ackFirst');
        ack.focus();
        return;
      }
      toggle.disabled = true;
      try {
        await api.setExperimentalSource(source.id, !on, !on && ack.checked);
        await load(true);
      } catch (error) {
        status.textContent = error.userMessage || error.message;
        toggle.disabled = false;
      }
    }, { className: on ? 'btn' : 'btn btn--primary', attrs: { id: `experimental-toggle-${source.id}` } });
    return el('section', {
      className: 'career__card src__experimental',
      dataset: { experimental: source.id },
      attrs: { 'aria-labelledby': `experimental-title-${source.id}` },
    }, [
      el('h3', {
        attrs: { id: `experimental-title-${source.id}` },
        text: t('experimental.title', { name: source.name }),
      }),
      el('span', { className: 'tag tag--warn', text: t('experimental.badge') }),
      el('p', {
        className: 'src__experimentalstate',
        text: on
          ? t('experimental.on', { date: source.experimental.changed_at
            ? shortDate(source.experimental.changed_at) : '' })
          : t('experimental.off'),
      }),
      el('ul', { className: 'src__experimentalpoints' }, [
        'experimental.pointRules', 'experimental.pointDefault', 'experimental.pointBlocks',
        'experimental.pointPartial', 'experimental.pointAccount',
      ].map((key) => el('li', { text: t(key) }))),
      usable ? null : el('p', { className: 'src__reason', text: t('experimental.unavailable') }),
      on || !usable ? null : el('label', { className: 'src__experimentalack', attrs: { for: ackId } }, [
        ack, el('span', { text: ' ' + t('experimental.ack') }),
      ]),
      usable || on ? toggle : null,
      status,
    ].filter(Boolean));
  }

  function sourceControls(source, progress) {
    const status = el('p', { attrs: { role: 'status' } });
    const mode = select(['AUTO', 'ENABLED', 'PAUSED'].map(value => ({
      value, label: t(`settings.refreshMode.${value}`),
    })), source.refresh_mode || 'AUTO', async value => {
      try { await api.setSourceSchedule(source.id, value); await load(true); }
      catch (error) { status.textContent = error.userMessage || error.message; }
    });
    const refresh = button(t(progress.state === 'PAUSED' ? 'settings.refreshAnyway' : 'settings.refreshNow'),
      async () => {
        refresh.disabled = true;
        try { await api.refreshSource(source.id); await load(true); }
        catch (error) { status.textContent = error.userMessage || error.message; refresh.disabled = false; }
      });
    refresh.disabled = progress.state === 'RUNNING';
    return el('div', {}, [field(`source-mode-${source.id}`, t('settings.refreshTiming'), mode), refresh, status]);
  }

  /**
   * HOW FRESH EACH SOURCE IS, AND WHETHER ANYTHING IS MOVING RIGHT NOW.
   *
   * A separate section from the capability table above because the two answer
   * different questions. That one says what a person can do with a board at
   * all; this says what happened to it last night. A board can be perfectly
   * capable and eight hours stale, and before this there was nowhere to see it.
   *
   * IT BLOCKS NOTHING. Reading this panel starts no collection, and every other
   * screen works while a source is RUNNING -- which is the rule the whole area
   * serves: the corpus already in the database is usable while the market data
   * improves.
   *
   * NO INVENTED PERCENTAGES. `percent` arrives as null whenever the provider
   * published no total, and a null renders as the counters plus "total not
   * published" rather than as a bar at zero. A bar that stays at 90% for two
   * hours is worse than no bar, because the next one is not believed either.
   */
  function refreshSection(rows) {
    if (!rows.length) return el('div', {});
    const body = [
      el('h3', { className: 'src__grouphead', text: t('sources.refreshHead') }),
      el('p', { className: 'src__note', text: t('sources.refreshNote') }),
    ];
    const head = el('tr', {}, [
      el('th', { text: t('sources.colSource'), attrs: { scope: 'col' } }),
      el('th', { text: t('sources.colRefresh'), attrs: { scope: 'col' } }),
      el('th', { text: t('sources.colProgress'), attrs: { scope: 'col' } }),
      el('th', { text: t('sources.colFresh'), attrs: { scope: 'col' } }),
    ]);
    body.push(el('div', { className: 'src__tablewrap' }, [
      el('table', { className: 'src__table' }, [
        el('thead', {}, [head]),
        el('tbody', {}, rows.map(refreshRow)),
      ]),
    ]));
    return el('div', { className: 'src__group' }, body);
  }

  function refreshRow(r) {
    return el('tr', {}, [
      el('td', { text: r.name || r.source_id }),
      el('td', {}, [
        el('span', {
          className: 'chip chip--' + String(r.state || '').toLowerCase(),
          text: t('sources.state.' + r.state) || r.state,
          attrs: r.blocker ? { title: r.blocker } : {},
        }),
      ]),
      el('td', { className: 'src__num', text: progressText(r) }),
      el('td', { text: r.last_success ? shortDate(r.last_success) : t('sources.neverFresh') }),
    ]);
  }

  /**
   * The one cell where it would be easiest to lie.
   *
   * Four cases, and three of them refuse to show a percentage: no counters at
   * all, counters with no provider total, and a total that exists. The fourth
   * is an ETA, which the server only sends once a rate and a remainder are both
   * real.
   */
  function progressText(r) {
    if (r.retrieved === null || r.retrieved === undefined) return t('sources.notMeasured');
    const count = Number(r.retrieved).toLocaleString();
    if (!r.measurable || r.percent === null || r.percent === undefined) {
      return count + ' ' + t('sources.retrievedNoTotal');
    }
    const total = Number(r.expected_total).toLocaleString();
    let out = count + ' / ' + total + '  ' + r.percent + '%';
    if (r.eta_seconds) out += '  ' + t('sources.etaAbout') + ' ' + minutes(r.eta_seconds);
    return out;
  }

  function minutes(seconds) {
    const m = Math.round(seconds / 60);
    if (m < 1) return t('sources.etaUnderMinute');
    if (m < 90) return m + ' ' + t('sources.etaMinutes');
    return Math.round(m / 60) + ' ' + t('sources.etaHours');
  }

  /**
   * The sources of one coverage group, as a TABLE.
   *
   * They were a vertical stack of cards, and a card is the wrong shape for
   * this: these rows are directly comparable -- same questions, different
   * answers -- and comparing them meant reading nine cards top to bottom and
   * holding the numbers in your head.
   *
   * FOUR COLUMNS, and every one of them is a field the server actually sends:
   *
   *   Source    the name
   *   Status    the state, as a chip
   *   Where     the region it covers
   *   Postings  how many are in the corpus from it, and out of how many boards
   *
   * There is deliberately no "Enabled" column and no "Last sync". This
   * product has no per-source switch and records no sync clock, and a column
   * of "On" would be a control that does not exist. The reason, the quoted
   * first-party text and the operator's notes stay behind the row's own
   * disclosure in the last cell.
   */
  function sourceTable(rows) {
    const head = el('tr', {}, [
      el('th', { text: t('sources.colSource'), attrs: { scope: 'col' } }),
      el('th', { text: t('sources.colStatus'), attrs: { scope: 'col' } }),
      el('th', { text: t('sources.colWhere'), attrs: { scope: 'col' } }),
      el('th', {
        text: t('sources.colPostings'),
        attrs: { scope: 'col', class: 'src__num' },
      }),
      el('th', { text: t('sources.colWhy'), attrs: { scope: 'col' } }),
    ]);
    return el('div', { className: 'src__tablewrap' }, [
      el('table', { className: 'src__table' }, [
        el('thead', {}, [head]),
        el('tbody', {}, rows.map(row)),
      ]),
    ]);
  }

  function row(source) {
    const detail = [
      // What the DATABASE observed, not what the catalogue declared. Without
      // it a connector that had stopped returning anything looked exactly
      // like one that was working.
      RUNNABLE.has(source.state) && source.note
        ? el('p', { className: 'src__why', text: source.note })
        : null,
      // In plain words first. "We cannot fetch this" is only useful with
      // "because".
      source.reason_plain
        ? el('p', { className: 'src__why', text: source.reason_plain })
        : null,
      // A downgraded row's `reason_plain` is what actually happened; the
      // sentence the catalogue wrote described a coverage the corpus did not
      // support, so it is reported as a claim rather than as a fact.
      source.downgraded_because && source.declared_reason_plain
        ? el('p', {
          className: 'src__why src__why--claim',
          text: t('sources.catalogueSays', { reason: source.declared_reason_plain }),
        })
        : null,
      // A third party's words about our request. Verbatim, never interpreted.
      source.last_error
        ? el('p', {
          className: 'src__techbody',
          text: t('sources.lastError', { error: source.last_error }),
        })
        : null,
      source.last_new_posting
        ? el('p', {
          className: 'src__techbody',
          text: t('sources.newestSeen', { date: source.last_new_posting.slice(0, 10) }),
        })
        : null,
      source.reason ? el('p', { className: 'src__techbody', text: source.reason }) : null,
      // A note to whoever builds the connector, in their vocabulary.
      source.unblocked_by
        ? el('p', {
          className: 'src__techbody src__why--next',
          text: t('sources.wouldChange', { what: source.unblocked_by }),
        })
        : null,
      source.downgraded_because
        ? el('p', {
          className: 'src__techbody',
          text: t('sources.downgraded', { why: source.downgraded_because }),
        })
        : null,
    ].filter(Boolean);

    // Postings, and out of how many boards. Two numbers in one cell because
    // "409" and "409 from 116 boards" answer the same question at two
    // resolutions, and splitting them would give a column of blanks for every
    // source that is not a board family.
    const counts = [];
    if (source.collected) {
      counts.push(t('sources.postingsCount', { n: source.collected.toLocaleString() }));
    }
    if (source.boards) {
      counts.push(t('sources.boardsCount', {
        producing: source.boards_with_postings,
        total: source.boards,
      }));
    }

    return el('tr', { className: 'src__row' }, [
      el('th', {
        className: 'src__title',
        text: source.name,
        attrs: { scope: 'row' },
      }),
      el('td', {}, [
        source.state
          ? el('span', {
            className: `tag tag--${STATE_TONE[source.state] || 'muted'}`,
            text: t(`source.state.${source.state}`),
          })
          : el('span', { className: 'src__blank', text: MISSING }),
      ]),
      el('td', { className: 'src__region', text: source.region || MISSING }),
      el('td', { className: 'src__num num' },
        counts.length
          ? counts.map((line) => el('span', { className: 'src__count', text: line }))
          : [el('span', { className: 'src__blank', text: MISSING })]),
      el('td', {}, [
        detail.length
          ? el('details', { className: 'src__tech' }, [
            el('summary', { className: 'src__techhead', text: t('sources.exactReason') }),
            ...detail,
          ])
          : el('span', { className: 'src__blank', text: MISSING }),
      ]),
    ]);
  }

  return { load, retranslate };
}

function shortDate(iso) {
  return String(iso).slice(0, 16).replace('T', ' ');
}

/** A receipt is history; only the lock/runner proves activity. */
function maintenanceSummary(state) {
  const root = el('section', { className: 'maintenance career__card', attrs: { 'aria-live': 'polite' } });
  root.appendChild(el('h3', { text: t('maintenance.title') }));
  if (!state) {
    root.appendChild(el('p', { text: t('maintenance.unavailable') }));
    return root;
  }
  const running = state.maintenance_running || state.app_refresh_running;
  root.appendChild(el('strong', { text: t(running ? 'maintenance.running' : 'maintenance.idle') }));
  root.appendChild(el('p', { text: t('maintenance.last', { date: state.last_successful_check
    ? shortDate(state.last_successful_check) : t('sources.neverFresh') }) }));
  root.appendChild(el('p', { text: t('maintenance.counts', {
    fresh: state.fresh, pending: state.pending, total: state.inventory,
  }) }));
  if (!running && state.activity === 'UNCONFIRMED_RUNNING_OR_INTERRUPTED') {
    root.appendChild(el('p', { text: t('maintenance.interrupted') }));
  }
  const counts = new Map();
  for (const item of state.items || []) {
    if (!item.selected && item.reason !== 'FRESH') counts.set(item.reason, (counts.get(item.reason) || 0) + 1);
  }
  const reasons = {
    OWNER_RUN_ONLY: 'manual', PROVIDER_COOLDOWN: 'cooldown', PROVIDER_REFUSAL: 'refusal',
    FEED_EXECUTOR_NOT_BUDGETED_YET: 'separate', NEEDS_LARGER_BUDGET: 'larger',
    UNKNOWN_SLOT_LIMIT: 'later', ITEM_BOUND: 'later', TIME_BUDGET: 'later',
  };
  const grouped = new Map();
  for (const [reason, count] of counts) {
    const key = reasons[reason] || 'separate';
    grouped.set(key, (grouped.get(key) || 0) + count);
  }
  root.appendChild(el('ul', {}, [...grouped].map(([reason, n]) => el('li', {
    text: t(`maintenance.${reason}`, { n }),
  }))));
  root.appendChild(el('p', { text: t('maintenance.explanation') }));
  return root;
}
