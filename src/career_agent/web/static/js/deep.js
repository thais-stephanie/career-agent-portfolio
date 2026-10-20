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
import { switchControl } from './switch.js';

const POLL_MS = 1500;
const LEVELS = [
  'STRONG', 'PREPARE', 'CHECK_FIRST', 'NOT_COMPARED', 'TO_CONFIRM', 'LOW', 'OTHER_WORK', 'STALE',
];
// Gaps first: what would stop an application reads before what supports it.
const STATUS_ORDER = ['not_shown', 'review', 'declared', 'partial', 'unknown', 'shown'];
const STOPS = new Set([
  'LIMIT_REACHED', 'SIGN_IN', 'CANCELLED', 'PROVIDER_STOPPED', 'NOTHING_NEW', 'NO_INTENT',
  'DEEP_UNSUPPORTED', 'NO_BUDGET', 'NO_PRICE', 'BUDGET_REACHED', 'UNACCOUNTED', 'MODEL_CHANGED',
  'PROFILE_CHANGED', 'PAUSED', 'ERROR', 'AUTH_FAILED',
]);
// Stops a job can be resumed from, said in yellow with the reason.
const WARN_STOPS = new Set([
  'LIMIT_REACHED', 'SIGN_IN', 'ERROR', 'PROVIDER_STOPPED', 'BUDGET_REACHED', 'UNACCOUNTED',
  'MODEL_CHANGED', 'PROFILE_CHANGED', 'NO_BUDGET', 'NO_PRICE', 'DEEP_UNSUPPORTED', 'AUTH_FAILED',
]);
const STATES = new Set([
  'AVAILABLE', 'CONNECTED', 'NOT_INSTALLED', 'SIGN_IN_REQUIRED', 'KEY_MISSING', 'CONNECTION_FAILED',
  'LIMIT_OR_ERROR', 'UNSUPPORTED',
]);
const BATCH = 25;

export function createDeep({ onOpenJob = null } = {}) {
  let invoker = null;
  let token = 0;
  let poller = null;
  let includeUnresolved = false;
  let retryRejected = false;
  let preview = null;
  let checked = false;
  let message = '';
  // Which of the four states is on screen. A running reading always wins;
  // otherwise the person moves between them, and opening the panel lands on
  // the results when there are any, on the first step when there are none.
  let view = null;
  let showAll = false;
  // The count and the budget the person typed, kept while the panel is open.
  let count = null;
  let budget = null;
  // True when a number the person chose was lowered to what is available now.
  let adjusted = false;
  let last = { status: null, reviews: [] };

  /** Options belong to a profile: another profile starts from nothing. */
  function resetOptions() {
    includeUnresolved = false;
    retryRejected = false;
    preview = null;
    checked = false;
    message = '';
    count = null;
    budget = null;
    adjusted = false;
  }

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
    // The counts are their own request: the panel and its controls are drawn
    // as soon as the fast answer arrives, and only the counts wait.
    const counted = api.getDeepCoverage();
    counted.catch(() => {});
    try {
      const [status, results] = await Promise.all([api.getDeep(), api.getDeepReviews()]);
      if (mine !== token) return;
      if (last.status && status.profile !== last.status.profile) resetOptions();
      last = { status: { ...status, coverage_ok: null }, reviews: results.reviews || [] };
      const running = status.run && status.run.status === 'running';
      if (running) view = 'reading';
      else if (view === 'reading' || view === null) {
        view = last.reviews.length || status.last_run ? 'results' : 'choose';
      }
      paint();
      if (running) poll();
      settleCoverage(mine, status.profile, counted);
    } catch (error) {
      if (mine !== token) return;
      head(view || 'choose');
      replace(bodyHost, [banner('pink', fault(error, 'load'), [
        button(t('deep.retry'), () => refresh(), { className: 'dr-btn2 dr-btn--sm' }),
      ])]);
    }
  }

  /**
   * Merge the counts into the panel when they arrive, unless the panel moved
   * on (another refresh, another profile) in the meantime: an older answer
   * never overwrites a newer state. A failure is a failure, never a zero.
   */
  async function settleCoverage(mine, profile, counted) {
    let coverage;
    try {
      coverage = await counted;
    } catch {
      coverage = { coverage_ok: false, profile };
    }
    if (mine !== token || !last.status || last.status.profile !== profile) return;
    if (coverage.profile && coverage.profile !== profile) return;
    last.status = { ...last.status, ...coverage };
    if (view !== 'choose' && view !== 'results') return;
    // Keep the person where they were: the field they were in keeps focus.
    const focused = document.activeElement && root.contains(document.activeElement)
      ? document.activeElement.id : '';
    paint();
    if (focused) {
      const again = root.querySelector(`#${CSS.escape(focused)}`);
      if (again) again.focus();
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
        paintProgress(status.run.boards_done || 0, status.run.boards_total || 0, status.live || {});
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
      // Nothing chosen yet: say so, and show the choice right here. Nothing is
      // sent or assumed until one is picked.
      body.push(banner('pink', t(`deep.provider.${status.provider_reason || 'CHOOSE_PROVIDER'}`)));
      if ((status.providers || []).length) body.push(providerCard(status));
    } else {
      body.push(providerCard(status), shareCard(status));
    }
    const ready = providerReady(status);
    if (status.provider && !ready) {
      body.push(banner('yellow', t('deep.err.notReady', {
        name: providerName(status), state: t(`deep.state.${chosenState(status)}`),
      })));
    }
    if (status.coverage_ok === null) {
      body.push(countingCard());
    } else if (status.coverage_ok === false) {
      // A failed count is not a count of zero: no strip, no number.
      body.push(banner('pink', t('deep.cov.failed'), [
        button(t('deep.retry'), () => refresh(), { className: 'dr-btn2 dr-btn--sm' }),
      ]));
    } else {
      const older = scoresNotice(status);
      if (older) body.push(older);
      const none = noneNotice(status);
      if (none) body.push(none);
      body.push(jobsCard(status));
    }
    const metered = isMetered(status);
    const most = availableNow(status);
    const next = el('button', {
      className: 'dr-btn', attrs: { type: 'button' }, text: t('deep.checkAndStart'),
      on: {
        click: async (event) => {
          const asked = chosenCount(status);
          const money = Number(budget ?? status.budget_default);
          if (!Number.isInteger(asked) || asked < 1 || asked > most) {
            message = t('deep.count.invalid', { max: most });
            paint();
            return;
          }
          if (metered && !(money > 0 && money <= 100)) {
            message = t('deep.budget.invalid');
            paint();
            return;
          }
          event.currentTarget.disabled = true;
          const options = { include_unresolved: includeUnresolved, limit: asked };
          if (metered) options.budget_usd = money;
          if (retryRejected) options.retry_rejected = true;
          try {
            preview = await api.previewDeep(options);
            message = '';
          } catch (error) {
            message = fault(error, 'preview');
            // The numbers changed under the page: show the true ones.
            if (error && error.detail && error.detail.code) {
              await refresh();
              return;
            }
          }
          checked = false;
          showAll = false;
          if (preview) go('check');
          else paint();
        },
      },
    });
    next.disabled = !status.provider || !ready || status.coverage_ok !== true || most < 1;
    // A disabled primary button says what is still needed (the design rules).
    const needed = !status.provider ? t('deep.still.provider')
      : !ready ? t('deep.still.ready')
        : status.coverage_ok === null ? t('deep.still.counting')
          : status.coverage_ok === false ? t('deep.still.count')
            : most < 1 ? t('deep.still.posting') : '';
    return [body, [
      el('span', { className: 'dr-foot__note', text: needed || t('deep.nothingUntilStart') }), next,
    ]];
  }

  /** The counts are on their way: the card says so, with no number in it. */
  function countingCard() {
    return el('section', { className: 'dr-card', attrs: { 'aria-busy': 'true' } }, [
      el('h3', { className: 'dr-card-title', text: t('deep.which.head') }),
      el('p', { className: 'dr-help', attrs: { role: 'status' }, text: t('deep.cov.counting') }),
      el('div', { className: 'sk sk--line', attrs: { 'aria-hidden': 'true' } }),
    ]);
  }

  /** How many postings could be read now, with the options as they stand. */
  function availableNow(status) {
    if (status.coverage_ok !== true) return 0;
    const base = includeUnresolved && isSet(status.available_unresolved)
      ? status.available_unresolved : status.available || 0;
    return base + (retryRejected ? status.rejected || 0 : 0);
  }

  /** The chosen number, never above what is available now. */
  function chosenCount(status) {
    const max = availableNow(status);
    if (max < 1) return 0;
    if (count === null) return Math.min(BATCH, max);
    if (count > max) {
      count = max;
      adjusted = true;
    }
    return Math.max(1, count);
  }

  function chosenState(status) {
    const chosen = (status.providers || []).find((p) => p.chosen);
    return chosen && STATES.has(chosen.state) ? chosen.state : 'OTHER';
  }

  function providerReady(status) {
    const chosen = (status.providers || []).find((p) => p.chosen);
    return !chosen || chosen.usable;
  }

  /** Scores from earlier preferences are shown as such, never as current. */
  function scoresNotice(status) {
    const s = status.scores || {};
    if (s.state !== 'older') return null;
    return banner('yellow', t('deep.scores.older', { serving: s.serving_version, current: s.current_version }), [
      button(t('deep.openJobs'), () => openJobs(), { className: 'dr-btn2 dr-btn--sm' }),
    ]);
  }

  /** Why nothing can be read, in words, with the one action that helps. */
  function noneNotice(status) {
    if (!status.provider || !status.unavailable) return null;
    const why = status.unavailable;
    let action = null;
    if (why === 'NO_SCORES') {
      action = button(t('deep.openJobs'), () => openJobs(), { className: 'dr-btn2 dr-btn--sm' });
    } else if (why === 'UNRESOLVED_ONLY') {
      action = button(t('deep.none.include'), () => {
        includeUnresolved = true;
        preview = null;
        checked = false;
        paint();
      }, { className: 'dr-btn2 dr-btn--sm' });
    } else if (why === 'ALL_READ') {
      action = button(t('deep.none.seeResults'), () => go('results'), { className: 'dr-btn2 dr-btn--sm' });
    }
    return banner('plain', t(`deep.none.${why}`, {
      count: status.available_unresolved || 0, short: status.too_short || 0,
    }), action ? [action] : []);
  }

  function openJobs() {
    close();
    const link = document.querySelector('.topnav__link[data-page="jobs"]');
    if (link) link.click();
  }

  function providerCard(status) {
    const providers = status.providers || [];
    const chosen = providers.find((p) => p.chosen);
    return el('section', { className: 'dr-card' }, [
      el('div', { className: 'dr-card__row' }, [
        el('span', {
          className: 'dr-glyph dr-glyph--lg', dataset: { tone: 'lilac' },
          attrs: { 'aria-hidden': 'true' }, text: '\u2726',
        }),
        el('div', { className: 'dr-card__main' }, [
          el('h3', { className: 'dr-card-title', attrs: { id: 'deep-who' }, text: t('deep.who.head') }),
          el('p', { className: 'dr-help', text: t('deep.who.choose') }),
          el('div', {
            className: 'dr-choices', attrs: { role: 'radiogroup', 'aria-labelledby': 'deep-who' },
          }, providers.map(providerOption)),
          chosen ? el('p', { className: 'dr-text' }, [
            el('strong', { text: t(`deep.access.${chosen.id}`) }), ' ',
            t(chosen.connection === 'api' ? 'deep.quota.api' : 'deep.quota'),
          ]) : null,
        ].filter(Boolean)),
      ]),
    ]);
  }

  /** A subscription says how it is signed in, as it always did; an API says whether its key is set. */
  function pillText(p, state, ready) {
    if (p.connection === 'subscription' && p.usable && p.auth) {
      return p.auth === 'subscription' ? `\u2713 ${t('deep.auth.subscription')}` : t('deep.auth.missing');
    }
    return ready ? `\u2713 ${t(`deep.state.${state}`)}` : t(`deep.state.${state}`);
  }

  function providerOption(p) {
    const state = STATES.has(p.state) ? p.state : 'OTHER';
    const ready = p.usable && !(p.connection === 'subscription' && p.auth && p.auth !== 'subscription');
    const price = p.price
      ? t('deep.choice.price', {
        input: p.price.input_per_mtok, cached: p.price.cached_input_per_mtok,
        output: p.price.output_per_mtok, date: p.price.observed_at,
      })
      : t('deep.choice.subscription');
    const node = el('button', {
      className: 'dr-choice',
      attrs: { type: 'button', role: 'radio', 'aria-checked': p.chosen ? 'true' : 'false' },
      dataset: { provider: p.id },
      on: {
        click: async () => {
          if (p.chosen) return;
          node.disabled = true;
          try {
            await api.setDeepProvider(p.id);
            message = '';
          } catch (error) {
            message = fault(error, 'save');
          }
          preview = null;
          checked = false;
          // The number the person chose stays (lowered if this provider has fewer
          // to read); a budget belongs to the provider it was typed for.
          budget = null;
          await refresh();
        },
      },
    }, [
      el('span', { className: 'dr-choice__top' }, [
        el('span', { className: 'dr-choice__name', text: t(`deep.name.${p.id}`) }),
        el('span', {
          className: 'dr-pill dr-pill--wrap', dataset: { tone: ready ? 'mint' : 'stone' },
          text: pillText(p, state, ready),
        }),
      ]),
      el('span', {
        className: 'dr-row__meta',
        text: [
          p.model ? t('deep.choice.model', { model: p.model }) : t('deep.choice.modelCli'),
          t(`deep.connection.${p.connection}`),
        ].join(' \u00b7 '),
      }),
      el('span', { className: 'dr-row__meta', text: price }),
    ]);
    return node;
  }

  function shareCard(status) {
    const name = providerName(status);
    const consent = switchControl(
      Boolean(status.share_evidence), t(`deep.consent.label.${status.provider}`),
      `${t('deep.consent.scope')} ${t('deep.consent.perProvider', { name })}`,
      async (node) => {
        node.disabled = true;
        const next = node.getAttribute('aria-checked') !== 'true';
        try {
          await api.setDeepConsent(next);
          message = t(next ? 'deep.consent.on' : 'deep.consent.off');
        } catch (error) {
          message = fault(error, 'save');
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
    const max = availableNow(status);
    const countInput = el('input', {
      className: 'dr-input',
      attrs: {
        id: 'deep-count', type: 'number', min: '1', max: String(Math.max(max, 1)), step: '1', inputmode: 'numeric',
      },
      props: { value: String(chosenCount(status) || 0) },
      on: {
        input: () => { count = Number(countInput.value); adjusted = false; preview = null; checked = false; },
        // Leaving the field with more than is available brings it back, in place.
        change: () => {
          if (Number(countInput.value) <= max) return;
          count = max;
          adjusted = true;
          countInput.value = String(max);
          const hint = countInput.parentElement.querySelector('.dr-help');
          if (hint) {
            hint.textContent = `${t('deep.count.adjusted', { count: max })} `
              + t('deep.count.help', { max, size: BATCH });
          }
        },
      },
    });
    countInput.disabled = max < 1;
    const help = t('deep.count.help', { max, size: BATCH });
    const fields = [field(
      'deep-count', t('deep.count.label'), countInput,
      adjusted ? `${t('deep.count.adjusted', { count: max })} ${help}` : help,
    )];
    if (isMetered(status)) {
      const budgetInput = el('input', {
        className: 'dr-input',
        attrs: { id: 'deep-budget', type: 'number', min: '0.01', max: '100', step: '0.01', inputmode: 'decimal' },
        props: { value: String(budget ?? status.budget_default ?? 1) },
        on: { input: () => { budget = budgetInput.value; preview = null; checked = false; } },
      });
      fields.push(field('deep-budget', t('deep.budget.label'), budgetInput, t('deep.budget.help')));
    }
    // A rejected answer was paid for: asking again is the person's explicit
    // choice, never a default, and it is paid again.
    const retry = status.rejected ? switchControl(
      retryRejected, t('deep.retry.label', { count: status.rejected }), t('deep.retry.help'),
      (node) => {
        retryRejected = node.getAttribute('aria-checked') !== 'true';
        preview = null;
        checked = false;
        paint();
      },
    ) : null;
    return el('section', { className: 'dr-card' }, [
      el('h3', { className: 'dr-card-title', text: t('deep.which.head') }),
      el('div', { className: 'dr-strip' }, [
        strip('lilac', max, t('deep.which.available')),
        strip('stone', status.reviewed || 0, t('deep.which.reviewed')),
        strip('plain', BATCH, t('deep.which.batch')),
      ]),
      el('div', { className: 'dr-fields' }, fields),
      retry,
      el('p', { className: 'dr-help', text: t('deep.previewLede.batches') }),
      el('p', {
        className: 'dr-help',
        text: t('deep.coverage', {
          roles: status.main_list_roles || 0, readable: status.readable || 0, short: status.too_short || 0,
        }),
      }),
      el('p', { className: 'dr-help dr-help--age', text: ageLine(status) }),
    ]);
  }

  // ---- 2 · check and start -------------------------------------------------

  function checkState(status) {
    const back = button(t('deep.back'), () => go('choose'), { className: 'dr-btn2' });
    const plan = preview;
    if (!plan || !plan.provider || !plan.postings.length) {
      const text = !plan ? t('deep.err.preview')
        : !plan.provider ? t(`deep.provider.${plan.reason}`) : t('deep.nothing');
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
        const options = { include_unresolved: includeUnresolved, limit: plan.requested };
        if (isSet(plan.budget_usd)) options.budget_usd = plan.budget_usd;
        if (plan.retry_rejected) options.retry_rejected = true;
        await api.startDeep(options);
        message = '';
      } catch (error) {
        message = fault(error, 'start');
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
      el('p', { className: 'dr-help dr-help--age', text: plan.age_limit && plan.age_limit.days
        ? t('deep.age.used', { days: plan.age_limit.days }) : t('deep.age.none') }),
      costCard(plan),
      packetCard(plan),
      el('label', { className: 'dr-check' }, [confirm, el('span', { text: t('deep.confirm') })]),
    ];
    return [body, [
      back,
      el('div', { className: 'dr-foot__end' }, [
        el('span', { className: 'dr-foot__note', text: t('deep.canStop') }), start,
      ]),
    ]];
  }

  function costCard(plan) {
    const e = plan.estimate || {};
    if (plan.billing !== 'METERED_API') {
      return el('section', { className: 'dr-card', dataset: { tone: 'yellow' } }, [
        el('span', { className: 'dr-strong', text: t('deep.tokens', { tokens: plan.estimated_input_tokens }) }),
        el('p', { className: 'dr-help', text: t('deep.batches', { count: plan.requested, size: plan.batch_size }) }),
        el('p', { className: 'dr-help', text: t('deep.extraUsage') }),
      ]);
    }
    const facts = [
      t('deep.cost.tokens', {
        input: e.input_tokens, output: e.output_tokens_typical, ceiling: e.output_tokens_ceiling,
      }),
      t('deep.cost.range', { typical: usd(e.typical_usd), offpeak: usd(e.off_peak_usd) }),
      t('deep.cost.worst', { worst: usd(e.worst_usd) }),
      t('deep.cost.covers', {
        budget: usd(plan.budget_usd), typical: e.covers_typical ?? 0, worst: e.covers_worst ?? 0,
      }),
      t('deep.batches', { count: plan.requested, size: plan.batch_size }),
    ];
    return el('section', { className: 'dr-card dr-cost', dataset: { tone: 'yellow' } }, [
      el('span', {
        className: 'dr-strong',
        text: t('deep.cost.head', { count: plan.requested, typical: usd(e.typical_usd), budget: usd(plan.budget_usd) }),
      }),
      el('ul', { className: 'dr-facts' }, facts.map((text) => el('li', { text }))),
      el('details', { className: 'dr-disc' }, [
        el('summary', { text: t('deep.cost.howHead') }),
        el('div', { className: 'dr-disc__inner' }, [
          el('p', {
            text: t('deep.cost.price', {
              input: plan.price?.input_per_mtok, cached: plan.price?.cached_input_per_mtok,
              output: plan.price?.output_per_mtok, date: plan.price?.observed_at, model: plan.model,
            }),
          }),
          el('p', { text: t('deep.cost.assumptions') }),
          el('p', { text: t('deep.cost.enforced') }),
        ]),
      ]),
    ]);
  }

  function packetCard(plan) {
    const p = plan.packet || {};
    const notes = [];
    if (p.postings_cut) {
      notes.push(t('deep.packet.cut', { count: p.postings_cut, chars: p.posting_chars_cut, max: p.max_posting_chars }));
    }
    if (p.skills_left_out) notes.push(t('deep.packet.skills', { count: p.skills_left_out }));
    if (p.experience_left_out_max) notes.push(t('deep.packet.experience', { count: p.experience_left_out_max }));
    if (p.experience_shortened_max) notes.push(t('deep.packet.shortened', { count: p.experience_shortened_max }));
    if (p.schema_in_prompt) notes.push(t('deep.packet.schema'));
    if (!notes.length) return null;
    return el('section', { className: 'dr-card' }, [
      el('span', { className: 'dr-strong', text: t('deep.packet.head') }),
      el('ul', { className: 'dr-facts' }, notes.map((text) => el('li', { text }))),
    ]);
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
    const pauseButton = button(t('deep.pause'), async (event) => {
      event.currentTarget.disabled = true;
      await api.pauseDeep();
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
        el('p', { className: 'deep__spent dr-help', text: '' }),
      ]),
      banner('plain', t('deep.readingKeeps')),
    ];
    queueMicrotask(() => paintProgress(run.boards_done || 0, run.boards_total || 0, status.live || {}));
    const note = isMetered(status) ? t('deep.quota.api') : t('deep.quota');
    return [body, [
      el('span', { className: 'dr-foot__note', text: note }),
      el('div', { className: 'dr-foot__end' }, [pauseButton, stop]),
    ]];
  }

  function paintProgress(done, total, live = {}) {
    const bar = bodyHost.querySelector('.dr-progress');
    if (!bar) return;
    bar.setAttribute('aria-valuenow', String(done));
    bar.setAttribute('aria-valuemax', String(total));
    bar.firstChild.style.width = `${total ? Math.round((done / total) * 100) : 0}%`;
    bodyHost.querySelector('.deep__done').textContent = String(done);
    bodyHost.querySelector('.deep__of').textContent = t('deep.ofRead', { total });
    bodyHost.querySelector('.deep__progress').textContent = t('deep.running', { done, total });
    const spent = bodyHost.querySelector('.deep__spent');
    if (spent) {
      const parts = [];
      if (live.batches) parts.push(t('deep.batchOf', { batch: live.batch, batches: live.batches }));
      if (isSet(live.budget_usd)) {
        parts.push(t('deep.spent', { spent: usd(live.spent_usd), budget: usd(live.budget_usd) }));
      }
      spent.textContent = parts.join(' \u00b7 ');
    }
  }

  // ---- 4 · results --------------------------------------------------------------

  function resultsState(status, reviews) {
    const body = [];
    if (status.last_run) body.push(lastRun(status.last_run));
    if (status.job) body.push(jobBanner(status, status.job));
    const older = status.coverage_ok === false ? null : scoresNotice(status);
    if (older) body.push(older);
    const counting = status.coverage_ok === null;
    const coverageLine = counting ? t('deep.cov.counting')
      : status.coverage_ok === false ? t('deep.cov.failed')
        : t('deep.coverage', {
          roles: status.main_list_roles || 0, readable: status.readable || 0, short: status.too_short || 0,
        });
    if (!status.share_evidence && status.provider && reviews.length) {
      body.push(banner('yellow', t('deep.notSharedNudge'), [
        button(t('deep.shareAndReadAgain'), () => go('choose'), { className: 'dr-btn2 dr-btn--sm' }),
      ]));
    }
    body.push(el('details', { className: 'dr-disc' }, [
      el('summary', { text: t('deep.howWeSort') }),
      el('div', { className: 'dr-disc__inner' }, [
        el('p', { text: t('deep.resultsLede') }),
        el('p', { text: coverageLine }),
      ]),
    ]));
    if (status.coverage_ok === true) {
      body.push(el('p', { className: 'dr-help', text: t('deep.notReviewed', { count: status.not_reviewed || 0 }) }));
    } else {
      body.push(el('p', {
        className: 'dr-help', text: coverageLine, attrs: counting ? { role: 'status', 'aria-busy': 'true' } : {},
      }));
    }
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

  function jobBanner(status, job) {
    const parts = [t('deep.job.summary', {
      done: job.done, requested: job.requested, name: t(`deep.name.${job.provider}`),
      model: job.model ? ` (${job.model})` : '',
    })];
    if (isSet(job.budget_usd)) parts.push(t('deep.spent', { spent: usd(job.spent_usd), budget: usd(job.budget_usd) }));
    const actions = [];
    if (job.resumable) {
      let raise = null;
      if (job.billing === 'METERED_API') {
        raise = el('input', {
          className: 'dr-input dr-input--sm',
          attrs: {
            id: 'deep-resume-budget', type: 'number', min: '0.01', max: '100', step: '0.01',
            'aria-label': t('deep.budget.label'),
          },
          props: { value: String(job.budget_usd ?? status.budget_default ?? 1) },
        });
        actions.push(raise);
      }
      actions.push(button(t('deep.resume'), async (event) => {
        event.currentTarget.disabled = true;
        const options = { resume: job.id };
        if (raise) options.budget_usd = Number(raise.value);
        try {
          await api.startDeep(options);
          message = '';
          view = 'reading';
        } catch (error) {
          message = fault(error, 'start');
        }
        await refresh();
        poll();
      }, { className: 'dr-btn2 dr-btn--sm' }));
    } else if (job.status === 'PAUSED') {
      parts.push(t('deep.job.notResumable'));
    }
    return banner(job.status === 'DONE' ? 'plain' : 'yellow', parts.join(' \u00b7 '), actions);
  }

  function lastRun(run) {
    const code = String(run.stop_reason || '');
    const text = [t('deep.last', {
      published: run.published || 0, failed: run.failed || 0, rejected: run.rejected || 0,
      candidates: run.candidates || 0,
    })];
    if (STOPS.has(code)) text.push(t(`deep.stop.${code}`));
    // A pause, a failure or a refused answer is said in yellow; a clean run in mint.
    const warn = run.failed || run.rejected || WARN_STOPS.has(code);
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
      row.stale || row.state === 'evidence' ? el('span', {
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

/** The age limit this reading respects (the profile's, as in Find jobs), and what it leaves out. */
function ageLine(status) {
  const limit = status.age_limit || {};
  return limit.days
    ? t('deep.age.limit', { days: limit.days, count: status.excluded_by_age || 0 })
    : t('deep.age.none');
}

function isSet(value) {
  return value !== null && value !== undefined;
}

/**
 * What went wrong, said for what it was. The routes that load, save a choice
 * or prepare a preview never reach a provider, so "nothing was sent" is true
 * of them. A start that was refused with a 4xx was refused before anything
 * ran. A start that failed any other way (no answer, a server fault) may have
 * reached the provider: that is said, and a repeat is not encouraged.
 */
function fault(error, stage) {
  const detail = (error && error.detail) || {};
  const code = detail.code;
  if (code === 'LIMIT_OUT_OF_RANGE' || code === 'NO_POSTINGS') {
    return t('deep.err.changed', { count: detail.available || 0 });
  }
  if (code === 'PROVIDER_NOT_READY') {
    const state = STATES.has(detail.state) ? detail.state : 'OTHER';
    return t('deep.err.notReady', { name: t('deep.name.this'), state: t(`deep.state.${state}`) });
  }
  if (code === 'RUN_ACTIVE') return t('deep.err.running');
  const refused = Boolean(error && error.status >= 400 && error.status < 500);
  if (stage === 'start') return refused ? t('deep.err.start') : t('deep.err.startUnknown');
  if (!error || error.kind === 'network') return t('deep.err.network');
  return t(`deep.err.${stage}`);
}

function isMetered(status) {
  const chosen = (status.providers || []).find((p) => p.chosen);
  return Boolean(chosen && chosen.connection === 'api');
}

/** US dollars, to the cent below a dollar's tenth and to the dollar above. */
function usd(value) {
  if (!isSet(value) || Number.isNaN(Number(value))) return '\u2013';
  const n = Number(value);
  return `US$ ${n < 0.1 ? n.toFixed(4) : n.toFixed(2)}`;
}

function field(id, label, input, help) {
  return el('div', { className: 'dr-field' }, [
    el('label', { attrs: { for: id }, text: label }),
    input,
    el('span', { className: 'dr-help', text: help }),
  ]);
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
  if (review.stale || review.state === 'evidence') {
    rows.push(make('p', { className: 'deep__stale', text: staleText(review) }));
  }
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

/** Why a reading is out of date, in words: the posting, the instructions or the profile changed.
 * When only the evidence changed, says whether the level still stands. */
function staleText(row) {
  if (row.state === 'evidence') return t(row.stale ? 'deep.evidenceLong' : 'deep.evidenceHolds');
  const why = (row.stale_reasons || []).map((code) => t(`deep.staleReason.${code}`)).join(' ');
  return `${t('deep.staleLong')} ${why}`.trim();
}

function quoteList(make, quotes) {
  return make('ul', { className: 'deep__quotes' }, (quotes || []).map((q) => make('li', {}, [make('q', { text: q })])));
}
