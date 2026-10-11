/**
 * deep.js -- Find Jobs -> Deep reading.
 *
 * An overlay, like Today: it is not a fourth rendering of the list. It holds
 * the whole deep-reading flow for this profile, in the order a person needs
 * it: which provider and how it is paid, whether the confirmed evidence may
 * be sent, a PREVIEW of exactly what would leave and which postings, then a
 * run that can be followed, cancelled and resumed, and the results.
 *
 * Nothing starts on opening, on a setting or on a preview. Only the Start
 * button sends anything, and only after the preview it confirms.
 *
 * Results are grouped by the recommendation the CODE derived from what the
 * model observed, and every row carries the three answers it came from: how
 * the work aligns, how far the history sent shows the requirements, and what
 * is left to confirm. They sit beside the local score; postings without a
 * reading stay in the main list in the local order and are counted here,
 * never ranked below the read ones.
 */

import { el, button, replace, focusables } from './dom.js';
import { t } from './i18n.js';
import * as api from './api.js';

const POLL_MS = 1500;
const LEVELS = [
  'STRONG', 'PREPARE', 'CHECK_FIRST', 'NOT_COMPARED', 'TO_CONFIRM', 'LOW', 'OTHER_WORK', 'STALE',
];
// Gaps first: what would stop an application reads before what supports it.
const STATUS_ORDER = ['not_shown', 'review', 'declared', 'partial', 'unknown', 'shown'];
const STOPS = new Set([
  'LIMIT_REACHED', 'SIGN_IN', 'CANCELLED', 'PROVIDER_STOPPED', 'NOTHING_NEW', 'NO_INTENT',
  'DEEP_NEEDS_SUBSCRIPTION', 'ERROR',
]);

export function createDeep({ onOpenJob = null } = {}) {
  let invoker = null;
  let token = 0;
  let poller = null;
  let includeUnresolved = false;
  let preview = null;
  let checked = false;
  let message = '';
  // Which of the four states is on screen. A running reading always wins;
  // otherwise the person moves between them, and opening the panel lands on
  // the results when there are any, on the first step when there are none.
  let view = null;
  let showAll = false;
  let last = { status: null, reviews: [] };

  const titleNode = el('h2', { className: 'dr-title', attrs: { id: 'deep-title', tabindex: '-1' }, text: '' });
  const subNode = el('p', { className: 'dr-sub', text: '' });
  const eyebrow = el('span', { className: 'dr-eyebrow', text: '' });
  const closeButton = el('button', {
    className: 'dr-x', attrs: { type: 'button', 'aria-label': t('deep.close') }, text: '✕',
    on: { click: () => close() },
  });
  const stepsHost = el('ol', { className: 'dr-steps' });
  const bodyHost = el('div', { className: 'dr-body deep' });
  const footHost = el('footer', { className: 'dr-foot' });
  const panel = el('div', {
    className: 'drawer__panel drawer__panel--wide dr-panel',
    attrs: { role: 'dialog', 'aria-modal': 'true', 'aria-labelledby': 'deep-title', tabindex: '-1' },
  }, [
    el('header', { className: 'dr-head' }, [
      el('div', { className: 'dr-head__top' }, [
        el('div', { className: 'dr-head__text' }, [eyebrow, titleNode, subNode]),
        closeButton,
      ]),
      stepsHost,
    ]),
    bodyHost,
    footHost,
  ]);
  const scrim = el('div', { className: 'drawer__scrim', on: { click: () => close() } });
  const root = el('div', { className: 'drawer drawer--deep', attrs: { hidden: true } }, [scrim, panel]);

  root.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') {
      event.stopPropagation();
      close();
      return;
    }
    if (event.key !== 'Tab') return;
    const nodes = focusables(panel);
    if (!nodes.length) return;
    const [first, lastNode] = [nodes[0], nodes[nodes.length - 1]];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      lastNode.focus();
    } else if (!event.shiftKey && document.activeElement === lastNode) {
      event.preventDefault();
      first.focus();
    }
  });

  async function open(invokedBy) {
    invoker = invokedBy || document.activeElement;
    root.hidden = false;
    document.body.classList.add('has-drawer');
    view = null;
    showAll = false;
    closeButton.setAttribute('aria-label', t('deep.close'));
    head('choose');
    titleNode.focus();
    await refresh();
  }

  function close() {
    if (root.hidden) return;
    root.hidden = true;
    document.body.classList.remove('has-drawer');
    token += 1;
    stopPolling();
    preview = null;
    checked = false;
    message = '';
    view = null;
    replace(bodyHost, []);
    replace(footHost, []);
    if (invoker && document.contains(invoker)) invoker.focus();
    invoker = null;
  }

  function stopPolling() {
    if (poller) clearInterval(poller);
    poller = null;
  }

  /** Move to another state without asking the server again. */
  function go(next) {
    view = next;
    paint();
    bodyHost.scrollTop = 0;
    titleNode.focus();
  }

  async function refresh() {
    const mine = ++token;
    replace(bodyHost, [el('div', { className: 'sk sk--block' })]);
    replace(footHost, []);
    try {
      const [status, results] = await Promise.all([api.getDeep(), api.getDeepReviews()]);
      if (mine !== token) return;
      last = { status, reviews: results.reviews || [] };
      const running = status.run && status.run.status === 'running';
      if (running) view = 'reading';
      else if (view === 'reading' || view === null) {
        view = last.reviews.length || status.last_run ? 'results' : 'choose';
      }
      paint();
      if (running) poll();
    } catch (error) {
      if (mine !== token) return;
      head(view || 'choose');
      replace(bodyHost, [banner('pink', t('deep.error'), [
        button(t('deep.retry'), () => refresh(), { className: 'dr-btn2 dr-btn--sm' }),
      ])]);
    }
  }

  function poll() {
    if (poller) return;
    poller = setInterval(async () => {
      if (root.hidden) {
        stopPolling();
        return;
      }
      try {
        const status = await api.getDeepRun();
        if (!status.run || status.run.status !== 'running') {
          stopPolling();
          view = 'results';
          await refresh();
          return;
        }
        paintProgress(status.run.boards_done || 0, status.run.boards_total || 0);
      } catch {
        stopPolling();
      }
    }, POLL_MS);
  }

  // ---- frame: eyebrow, title, sub and the three steps --------------------

  const STEP_OF = { choose: 1, check: 2, reading: 3, results: 3 };

  function head(state) {
    const name = providerName(last.status);
    eyebrow.textContent = t('deep.title');
    titleNode.textContent = t(`deep.head.${state}.title`);
    subNode.textContent = t(`deep.head.${state}.sub`, { name });
    const current = STEP_OF[state];
    const items = [];
    ['choose', 'check', 'results'].forEach((key, index) => {
      const n = index + 1;
      const done = n < current || (state === 'results' && n === 3);
      if (index) items.push(el('li', { className: 'dr-step__line', attrs: { 'aria-hidden': 'true' } }));
      items.push(el('li', {
        className: `dr-step${done ? ' is-done' : ''}`,
        attrs: n === current && !done ? { 'aria-current': 'step' } : {},
      }, [
        el('span', { className: 'dr-step__n', attrs: { 'aria-hidden': 'true' }, text: done ? '✓' : String(n) }),
        el('span', { className: 'dr-step__label', text: t(`deep.step.${key}`) }),
        done ? el('span', { className: 'sr-only', text: ` (${t('deep.step.done')})` }) : null,
      ].filter(Boolean)));
    });
    stepsHost.setAttribute('aria-label', t('deep.step.label'));
    replace(stepsHost, items);
  }

  function paint() {
    const { status, reviews } = last;
    head(view);
    if (status.demo) {
      replace(bodyHost, [banner('plain', t('deep.demo'))]);
      replace(footHost, []);
      return;
    }
    const draw = { choose: chooseState, check: checkState, reading: readingState, results: resultsState }[view];
    const [body, foot] = draw(status, reviews);
    const note = message ? el('p', { className: 'dr-banner', text: message, attrs: { role: 'status' } }) : null;
    replace(bodyHost, [note, ...body].filter(Boolean));
    replace(footHost, foot);
  }

  // ---- 1 · choose what to share ---------------------------------------------

  function chooseState(status) {
    const body = [];
    if (!status.provider) {
      body.push(banner('pink', t(`deep.provider.${status.provider_reason || 'CHOOSE_PROVIDER'}`)));
    } else {
      body.push(providerCard(status), shareCard(status));
    }
    body.push(jobsCard(status));
    const next = el('button', {
      className: 'dr-btn', attrs: { type: 'button' }, text: t('deep.checkAndStart'),
      on: {
        click: async (event) => {
          event.currentTarget.disabled = true;
          try {
            preview = await api.previewDeep({ include_unresolved: includeUnresolved });
            message = '';
          } catch {
            message = t('deep.error');
          }
          checked = false;
          showAll = false;
          if (preview) go('check');
          else paint();
        },
      },
    });
    next.disabled = !status.provider;
    return [body, [el('span', { className: 'dr-foot__note', text: t('deep.nothingUntilStart') }), next]];
  }

  function providerCard(status) {
    const signedIn = status.auth === 'subscription';
    return el('section', { className: 'dr-card' }, [
      el('div', { className: 'dr-card__row' }, [
        el('span', {
          className: 'dr-glyph dr-glyph--lg', dataset: { tone: 'lilac' }, attrs: { 'aria-hidden': 'true' }, text: '✦',
        }),
        el('div', { className: 'dr-card__main' }, [
          el('div', { className: 'dr-card__titleline' }, [
            el('h3', { className: 'dr-card-title', text: t('deep.who.head') }),
            el('span', {
              className: 'dr-pill dr-pill--wrap', dataset: { tone: signedIn ? 'mint' : 'pink' },
              text: signedIn ? `✓ ${t('deep.auth.subscription')}` : t('deep.auth.missing'),
            }),
          ]),
          el('p', { className: 'dr-text' }, [
            el('strong', { text: t(`deep.access.${status.provider}`) }), ' ', t('deep.quota'),
          ]),
        ]),
      ]),
    ]);
  }

  function shareCard(status) {
    const name = providerName(status);
    const consent = switchControl(
      Boolean(status.share_evidence), t(`deep.consent.label.${status.provider}`), t('deep.consent.scope'),
      async (node) => {
        node.disabled = true;
        const next = node.getAttribute('aria-checked') !== 'true';
        try {
          await api.setDeepConsent(next);
          message = t(next ? 'deep.consent.on' : 'deep.consent.off');
        } catch {
          message = t('deep.error');
        }
        preview = null;
        checked = false;
        await refresh();
      },
    );
    const unresolved = switchControl(
      includeUnresolved, t('deep.includeUnresolved'), t('deep.includeUnresolvedHint'),
      (node) => {
        includeUnresolved = node.getAttribute('aria-checked') !== 'true';
        preview = null;
        checked = false;
        paint();
      },
    );
    return el('section', { className: 'dr-card' }, [
      el('h3', { className: 'dr-card-title', text: t('deep.see.head', { name }) }),
      el('p', { className: 'dr-help', text: t('deep.see.always') }),
      el('hr', { className: 'dr-hair' }),
      consent,
      status.share_evidence ? null : banner('yellow', t('deep.see.recommended')),
      el('hr', { className: 'dr-hair' }),
      unresolved,
    ].filter(Boolean));
  }

  function jobsCard(status) {
    return el('section', { className: 'dr-card' }, [
      el('h3', { className: 'dr-card-title', text: t('deep.which.head') }),
      el('div', { className: 'dr-strip' }, [
        strip('lilac', status.readable || 0, t('deep.which.readable')),
        strip('stone', status.reviewed || 0, t('deep.which.reviewed')),
        strip('plain', status.cap || 0, t('deep.which.cap')),
      ]),
      el('p', { className: 'dr-help', text: t('deep.previewLede', { cap: status.cap }) }),
      el('p', {
        className: 'dr-help',
        text: t('deep.coverage', {
          roles: status.main_list_roles || 0, readable: status.readable || 0, short: status.too_short || 0,
        }),
      }),
    ]);
  }

  // ---- 2 · check and start -------------------------------------------------

  function checkState(status) {
    const back = button(t('deep.back'), () => go('choose'), { className: 'dr-btn2' });
    const plan = preview;
    if (!plan || !plan.provider || !plan.postings.length) {
      const text = !plan ? t('deep.error') : !plan.provider ? t(`deep.provider.${plan.reason}`) : t('deep.nothing');
      return [[banner('plain', text)], [back]];
    }
    const name = providerName(status);
    const strata = {};
    for (const row of plan.postings) strata[row.stratum] = (strata[row.stratum] || 0) + 1;
    const sent = [
      sendRow(t('deep.sends.postings', { count: plan.postings.length })),
      sendRow(t('deep.sends.intent', { count: Object.values(plan.intent).flat().length })),
      sendRow(t('deep.sends.levels', {
        levels: plan.levels.map((level) => t(`seniority.${level}`)).join(', ') || t('deep.none'),
      })),
      plan.share_evidence
        ? sendRow(t('deep.sends.evidence', {
          max: Math.max(0, ...plan.evidence_per_posting), confirmed: plan.confirmed_evidence,
        }))
        : el('div', { className: 'dr-row' }, [
          glyph('stone', '\u2013'),
          el('span', { className: 'dr-row__main dr-row__meta', text: t('deep.sends.noEvidence') }),
          el('span', { className: 'dr-pill', dataset: { tone: 'stone' }, text: t('deep.notShared') }),
          button(t('deep.change'), () => go('choose'), { className: 'dr-quiet' }),
        ]),
    ];
    const example = plan.share_evidence && plan.evidence_example.length ? el('details', {}, [
      el('summary', { text: t('deep.exampleHead') }),
      ...plan.evidence_example.map((item) => el('div', { className: 'dr-row' }, [
        el('span', { className: 'dr-row__main' }, [
          el('span', { className: 'dr-row__meta', text: `${item.id} · ${t(`deep.claimType.${item.type}`)}` }),
          el('span', { text: item.text }),
        ]),
      ])),
    ]) : null;
    const shown = showAll ? plan.postings : plan.postings.slice(0, 4);
    const jobs = el('section', { className: 'dr-rows' }, [
      el('h3', { className: 'dr-rows__head', text: t('deep.jobsHead', { count: plan.postings.length }) }),
      el('p', {
        className: 'dr-rows__lede',
        text: `${t('deep.sample', {
          count: plan.postings.length, population: plan.population, reviewed: plan.already_reviewed,
          short: plan.too_short || 0,
        })} ${['top_local', 'lexical', 'control'].map((s) => `${t(`deep.stratum.${s}`)}: ${strata[s] || 0}`)
          .join(' · ')}`,
      }),
      ...shown.map((row) => el('div', { className: 'dr-row' }, [
        el('span', { className: 'dr-row__main' }, [
          el('span', { className: 'dr-row__title', text: row.title }),
          el('span', { className: 'dr-row__meta', text: `${row.company} · ${t(`deep.stratum.${row.stratum}`)}` }),
        ]),
        scorePill(row.local_score),
      ])),
      plan.postings.length > 4 ? el('div', { className: 'dr-rows__foot' }, [
        button(showAll ? t('deep.showFewer') : t('deep.showAll', { count: plan.postings.length }), () => {
          showAll = !showAll;
          paint();
        }, { className: 'dr-quiet' }),
      ]) : null,
    ].filter(Boolean));
    const confirm = el('input', {
      attrs: { type: 'checkbox' },
      props: { checked },
      on: { change: () => { checked = confirm.checked; start.disabled = !checked; } },
    });
    const start = button(t('deep.start', { count: plan.postings.length }), async (event) => {
      event.currentTarget.disabled = true;
      try {
        await api.startDeep({ include_unresolved: includeUnresolved, limit: plan.postings.length });
        message = '';
      } catch {
        message = t('deep.error');
      }
      preview = null;
      checked = false;
      view = 'reading';
      await refresh();
      // A short run can end between the two reads of `refresh`: follow it to
      // its end either way, and draw the results it stored.
      poll();
    }, { className: 'dr-btn' });
    start.disabled = !checked;
    const body = [
      el('section', { className: 'dr-rows' }, [
        el('h3', { className: 'dr-rows__head', text: t('deep.willRead', { name }) }),
        ...sent,
        example,
        el('div', { className: 'dr-rows__foot', text: t('deep.neverSent') }),
      ].filter(Boolean)),
      jobs,
      el('section', { className: 'dr-card', dataset: { tone: 'yellow' } }, [
        el('span', { className: 'dr-strong', text: t('deep.tokens', { tokens: plan.estimated_input_tokens }) }),
        el('p', { className: 'dr-help', text: t('deep.extraUsage') }),
      ]),
      el('label', { className: 'dr-check' }, [confirm, el('span', { text: t('deep.confirm') })]),
    ];
    return [body, [
      back,
      el('div', { className: 'dr-foot__end' }, [
        el('span', { className: 'dr-foot__note', text: t('deep.canStop') }), start,
      ]),
    ]];
  }

  // ---- 3 · reading ------------------------------------------------------------

  function readingState(status) {
    const run = status.run || {};
    const stop = button(t('deep.cancel'), async (event) => {
      event.currentTarget.disabled = true;
      await api.cancelDeep();
      view = 'results';
      await refresh();
    }, { className: 'dr-btn2' });
    const body = [
      el('section', { className: 'dr-card' }, [
        el('div', { className: 'dr-card__titleline', style: { 'justify-content': 'space-between' } }, [
          el('span', { className: 'dr-bignum' }, [
            el('span', { className: 'deep__done', text: String(run.boards_done || 0) }), ' ',
            el('small', { className: 'deep__of', text: t('deep.ofRead', { total: run.boards_total || 0 }) }),
          ]),
        ]),
        el('div', {
          className: 'dr-progress',
          attrs: {
            role: 'progressbar', 'aria-label': t('deep.progressLabel'), 'aria-valuemin': '0',
            'aria-valuemax': String(run.boards_total || 0), 'aria-valuenow': String(run.boards_done || 0),
          },
        }, [el('span', { style: { width: '0%' } })]),
        el('p', {
          className: 'deep__progress dr-help', attrs: { role: 'status' },
          text: t('deep.running', { done: run.boards_done || 0, total: run.boards_total || 0 }),
        }),
      ]),
      banner('plain', t('deep.readingKeeps')),
    ];
    queueMicrotask(() => paintProgress(run.boards_done || 0, run.boards_total || 0));
    return [body, [el('span', { className: 'dr-foot__note', text: t('deep.quota') }), stop]];
  }

  function paintProgress(done, total) {
    const bar = bodyHost.querySelector('.dr-progress');
    if (!bar) return;
    bar.setAttribute('aria-valuenow', String(done));
    bar.setAttribute('aria-valuemax', String(total));
    bar.firstChild.style.width = `${total ? Math.round((done / total) * 100) : 0}%`;
    bodyHost.querySelector('.deep__done').textContent = String(done);
    bodyHost.querySelector('.deep__of').textContent = t('deep.ofRead', { total });
    bodyHost.querySelector('.deep__progress').textContent = t('deep.running', { done, total });
  }

  // ---- 4 · results --------------------------------------------------------------

  function resultsState(status, reviews) {
    const body = [];
    if (status.last_run) body.push(lastRun(status.last_run));
    if (!status.share_evidence && status.provider && reviews.length) {
      body.push(banner('yellow', t('deep.notSharedNudge'), [
        button(t('deep.shareAndReadAgain'), () => go('choose'), { className: 'dr-btn2 dr-btn--sm' }),
      ]));
    }
    body.push(el('details', { className: 'dr-disc' }, [
      el('summary', { text: t('deep.howWeSort') }),
      el('div', { className: 'dr-disc__inner' }, [
        el('p', { text: t('deep.resultsLede') }),
        el('p', {
          text: t('deep.coverage', {
            roles: status.main_list_roles || 0, readable: status.readable || 0, short: status.too_short || 0,
          }),
        }),
      ]),
    ]));
    body.push(el('p', { className: 'dr-help', text: t('deep.notReviewed', { count: status.not_reviewed || 0 }) }));
    if (!reviews.length) {
      body.push(el('div', { className: 'dr-empty' }, [
        el('b', { text: t('deep.emptyHead') }), el('span', { text: t('deep.noResults') }),
      ]));
    }
    for (const level of LEVELS) {
      const rows = reviews.filter((r) => r.level === level);
      if (!rows.length) continue;
      body.push(el('section', { className: 'dr-group deep__results', dataset: { level } }, [
        el('div', { className: 'dr-group__head' }, [
          el('h3', {
            className: 'dr-card-title dr-card-title--sm',
            text: `${t(`deep.level.${level}`)} (${rows.length})`,
          }),
        ]),
        el('p', { className: 'dr-help', text: t(`deep.levelMeans.${level}`) }),
        ...rows.map(resultRow),
      ]));
    }
    const again = button(t('deep.readAgain'), () => go('choose'), { className: 'dr-btn2' });
    again.disabled = !status.provider;
    return [body, [again, button(t('deep.backToJobs'), () => close(), { className: 'dr-btn' })]];
  }

  function lastRun(run) {
    const code = String(run.stop_reason || '');
    const text = [t('deep.last', {
      published: run.published || 0, failed: run.failed || 0, rejected: run.rejected || 0,
      candidates: run.candidates || 0,
    })];
    if (STOPS.has(code)) text.push(t(`deep.stop.${code}`));
    // A pause, a failure or a refused answer is said in yellow; a clean run in mint.
    const warn = run.failed || run.rejected || ['LIMIT_REACHED', 'SIGN_IN', 'ERROR', 'PROVIDER_STOPPED'].includes(code);
    return banner(warn ? 'yellow' : 'mint', text.join(' '));
  }

  function resultRow(row) {
    return el('button', {
      className: 'dr-job deep__open deep__row',
      dataset: { level: row.level },
      attrs: { type: 'button' },
      // The job drawer replaces this panel: two overlays never stack.
      on: { click: () => { close(); if (onOpenJob) onOpenJob(row.job_id); } },
    }, [
      el('span', { className: 'dr-job__top' }, [
        el('span', { className: 'dr-job__who deep__what' }, [
          el('span', { className: 'dr-job__title deep__jobtitle', text: row.title }),
          el('span', { className: 'dr-row__meta deep__company', text: row.company }),
        ]),
        scorePill(row.local_score),
      ]),
      el('span', { className: 'dr-job__axes' }, axisLines(el, row.assessment)),
      row.stale ? el('span', {
        className: 'dr-pill dr-pill--wrap deep__stale', dataset: { tone: 'stone' }, text: staleText(row),
      }) : null,
      el('span', { className: 'dr-job__foot' }, [
        el('span', {
          className: 'deep__compare',
          text: row.stale
            ? t('deep.levelThen', { level: t(`deep.level.${row.level_then}`) })
            : t(`deep.compare.${row.comparison}`, { score: row.local_score }),
        }),
        el('span', { className: 'dr-job__see', text: t('deep.seeJob') }),
      ]),
    ].filter(Boolean));
  }

  return { root, open, close };
}

function providerName(status) {
  return status && status.provider ? t(`deep.name.${status.provider}`) : t('deep.name.none');
}

function banner(tone, text, actions = []) {
  return el('div', { className: 'dr-banner', dataset: { tone }, attrs: { role: 'status' } }, [
    el('span', { className: 'dr-banner__text', text }), ...actions,
  ]);
}

function glyph(tone, mark) {
  return el('span', { className: 'dr-glyph', dataset: { tone }, attrs: { 'aria-hidden': 'true' }, text: mark });
}

function sendRow(text) {
  return el('div', { className: 'dr-row' }, [glyph('mint', '✓'), el('span', { className: 'dr-row__main', text })]);
}

function strip(tone, count, label) {
  return el('span', { dataset: { tone } }, [el('b', { text: String(count) }), ` ${label}`]);
}

function scorePill(score) {
  return el('span', {
    className: 'dr-pill', dataset: { tone: Number(score) >= 80 ? 'mint' : 'yellow' },
    text: t('deep.localPill', { score: Number(score) || 0 }),
  });
}

/** A role=switch button: the label, what it does, and the track. */
function switchControl(on, label, hint, onToggle) {
  const node = el('button', {
    className: 'dr-switch', attrs: { type: 'button', role: 'switch', 'aria-checked': on ? 'true' : 'false' },
    on: { click: () => onToggle(node) },
  }, [
    el('span', { className: 'dr-switch__text' }, [
      el('span', { className: 'dr-switch__label', text: label }),
      el('span', { className: 'dr-switch__hint', text: hint }),
    ]),
    el('span', { className: 'dr-switch__track', attrs: { 'aria-hidden': 'true' } }, [
      el('span', { className: 'dr-switch__knob' }),
    ]),
  ]);
  return node;
}

/**
 * The drawer's section for ONE posting. `review` is `job.deep_review`, or
 * null when the posting has not been read: that is said plainly and is not a
 * verdict on the posting.
 */
export function deepSection(review, section) {
  const make = el;
  if (!review) {
    return section(t('deep.drawer.head'), [make('p', { className: 'deep__note', text: t('deep.drawer.none') })]);
  }
  const r = review.review;
  const rows = [];
  const a = review.assessment;
  rows.push(make('p', { className: 'deep__levelline' }, [
    make('strong', { text: t(`deep.level.${review.level}`) }),
    make('span', {
      className: 'deep__compare',
      text: review.stale
        ? t('deep.levelThen', { level: t(`deep.level.${review.level_then}`) })
        : t(`deep.compare.${review.comparison}`, { score: review.local_score }),
    }),
  ]));
  if (review.stale) rows.push(make('p', { className: 'deep__stale', text: staleText(review) }));
  rows.push(make('div', { className: 'deep__axes' }, axisLines(make, a)));
  rows.push(make('p', { className: 'deep__minor', text: t('deep.drawer.primary') }));
  if (r.primary_activity.summary) {
    rows.push(make('p', {
      className: 'deep__inference', text: `${t('deep.inference')}: ${r.primary_activity.summary}`,
    }));
  }
  rows.push(quoteList(make, r.primary_activity.quotes));
  rows.push(make('p', {
    text: t(`deep.relation.${r.work_relation.value}`)
      + (r.work_relation.reason ? ` ${t('deep.inference')}: ${r.work_relation.reason}` : ''),
  }));
  rows.push(quoteList(make, r.work_relation.quotes));
  const reqs = a.requirements.map((req, index) => ({ ...req, index }));
  const technical = (req) => req.category === 'technical' || req.category === 'credential';
  const byStatus = (x, y) => (a.central_gaps.includes(y.index) - a.central_gaps.includes(x.index))
    || (STATUS_ORDER.indexOf(x.status) - STATUS_ORDER.indexOf(y.status)) || (x.index - y.index);
  const groups = [
    ['deep.drawer.mandatory', reqs.filter((q) => q.kind === 'mandatory' && technical(q)).sort(byStatus)],
    ['deep.drawer.desirable', reqs.filter((q) => q.kind !== 'mandatory' && technical(q)).sort(byStatus)],
  ];
  for (const [head, items] of groups) {
    if (!items.length) continue;
    rows.push(make('p', { className: 'deep__minor', text: t(head) }));
    if (!review.evidence_shared) rows.push(make('p', { className: 'deep__note', text: t('deep.drawer.notShared') }));
    rows.push(make('ul', { className: 'deep__reqs' }, items.map((req) => requirementItem(make, review, a, req))));
  }
  const confirm = [
    ...a.conditions.map((c) => [c.quote, t(`deep.topic.${c.topic}`)]),
    ...a.languages.map((quote) => [quote, t('deep.topic.language')]),
    ...a.questions.map((quote) => [quote, t('deep.question')]),
  ];
  if (confirm.length) {
    rows.push(make('p', { className: 'deep__minor', text: t('deep.drawer.toConfirm') }));
    rows.push(make('ul', { className: 'deep__reqs' }, confirm.map(([quote, tag]) => make('li', {}, [
      make('q', { className: 'deep__quote', text: quote }),
      make('span', { className: 'deep__tag', text: tag }),
    ]))));
  }
  if (r.divergences.length) {
    rows.push(make('p', { className: 'deep__minor', text: t('deep.drawer.divergences') }));
    rows.push(make('ul', { className: 'deep__reqs' }, r.divergences.map((d) => make('li', {}, [
      make('span', { className: 'deep__inference', text: `${t('deep.inference')}: ${d.text}` }),
      quoteList(make, d.quotes),
    ]))));
  }
  if (r.tools.length) {
    rows.push(make('p', { className: 'deep__minor', text: t('deep.drawer.tools') }));
    const tools = r.tools.map((x) => `${x.name} (${t(`deep.importance.${x.importance}`)})`);
    rows.push(make('p', { text: tools.join(', ') }));
  }
  rows.push(make('details', { className: 'deep__details' }, [
    make('summary', { text: t('deep.drawer.provenance') }),
    make('ul', { className: 'deep__list' }, [
      t('deep.prov.access', { access: t(`deep.access.${review.provider}`) }),
      t('deep.prov.model', { model: review.model }),
      t('deep.prov.date', { date: String(review.created_at).slice(0, 16).replace('T', ' ') }),
      t('deep.prov.contract', { contract: review.contract }),
      t('deep.prov.profile', { digest: String(review.profile_digest).slice(0, 12) }),
      t('deep.prov.stratum', { stratum: t(`deep.stratum.${review.stratum}`) }),
      t('deep.prov.dropped', { count: Object.values(review.dropped || {}).reduce((a, b) => a + b, 0) }),
    ].map((text) => make('li', { text }))),
  ]));
  return section(t('deep.drawer.head'), rows, { lede: t('deep.drawer.lede') });
}

/**
 * The three answers, one line each, never folded into one word: how the work
 * aligns, how far the history SENT shows the requirements, what is left to
 * confirm. Central requirements not shown come after, quoted.
 */
function axisLines(make, a) {
  // "label: n" pairs, zeros left out, so no sentence has to agree with a number.
  const counted = (pairs) => pairs.filter(([, n]) => n)
    .map(([key, n]) => `${t(`deep.count.${key}`)}: ${n}`).join(' · ');
  const c = a.counts;
  const history = counted(['mandatory', 'shown', 'partial', 'declared', 'not_shown', 'review'].map((k) => [k, c[k]]));
  const confirm = counted([
    ['conditions', a.conditions.length + a.languages.length], ['questions', a.questions.length],
  ]);
  const lines = [
    [t('deep.axis.alignment'), t(`deep.alignment.${a.alignment}`)],
    [t('deep.axis.history'), a.history === 'NOT_SENT' || a.history === 'NONE_STATED'
      ? t(`deep.history.${a.history}`)
      : `${t(`deep.history.${a.history}`)} (${history})`],
    [t('deep.axis.confirm'), confirm || t('deep.confirmNone')],
  ];
  const out = lines.map(([label, text]) => make('span', { className: 'deep__axis' }, [
    make('span', { className: 'deep__axislabel', text: `${label}: ` }), text,
  ]));
  for (const index of a.central_gaps) {
    out.push(make('span', {
      className: 'deep__gap', text: t('deep.centralGap', { quote: a.requirements[index].quote }),
    }));
  }
  if (a.to_review) {
    out.push(make('span', { className: 'deep__note', text: t('deep.toReview', { count: a.to_review }) }));
  }
  return out;
}

function requirementItem(make, review, a, req) {
  const gap = a.central_gaps.includes(req.index);
  const tags = [t(`deep.kind.${req.kind}`), t(`deep.status.${req.status}`)];
  if (gap) tags.push(t('deep.centralTag'));
  else if (req.central) tags.push(t('deep.centralCore'));
  const children = [
    make('q', { className: 'deep__quote', text: req.quote }),
    make('span', { className: 'deep__tag', text: tags.join(' · ') }),
  ];
  if (req.parts.length) {
    children.push(make('span', {
      className: 'deep__parts',
      text: req.parts.map((p) => `${p.linked ? t('deep.part.linked') : t('deep.part.unknown')}: ${p.text}`).join(' · '),
    }));
  }
  for (const note of req.notes) {
    children.push(make('span', { className: 'deep__note', text: t(`deep.reqNote.${note}`) }));
  }
  const said = { supported: 'shown', partial: 'partial', not_shown: 'not_shown', unclear: 'unknown' }[req.reading_said];
  if (said !== req.status) {
    const text = t('deep.readingSaid', { said: t(`deep.status.${said}`) });
    children.push(make('span', { className: 'deep__note', text }));
  }
  for (const id of req.evidence_ids) {
    const ev = (review.evidence || {})[id];
    children.push(make('span', {
      className: 'deep__evidence',
      text: ev && !ev.changed
        ? `${t(`deep.claimType.${ev.type}`)}: ${ev.text}` : t('deep.evidenceChanged', { id }),
    }));
  }
  return make('li', {
    className: `deep__req deep__req--${req.status}${gap ? ' deep__req--gap' : ''}`,
    dataset: { status: req.status },
  }, children);
}

/** Why a reading is out of date, in words: the posting, the instructions or the profile changed. */
function staleText(row) {
  const why = (row.stale_reasons || []).map((code) => t(`deep.staleReason.${code}`)).join(' ');
  return `${t('deep.staleLong')} ${why}`.trim();
}

function quoteList(make, quotes) {
  return make('ul', { className: 'deep__quotes' }, (quotes || []).map((q) => make('li', {}, [make('q', { text: q })])));
}
