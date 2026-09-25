/**
 * Settings & Sources -> AI & Semantic Matching.
 *
 * HOW Career Agent interprets postings, not WHAT the person wants. The default
 * view is two lines: whether AI semantic matching is ready, and which provider
 * Auto uses. Everything technical waits behind a disclosure.
 *
 * Every sentence comes from the catalogue by a CODE the server returns (a
 * provider id, an availability state, a billing kind), never from prose the
 * server wrote: that prose is English, and a Portuguese screen must not show it.
 */
import { el, button, replace, select, field } from './dom.js';
import { t } from './i18n.js';
import {
  getSemantic, patchSemantic, saveDeepseekKey, removeDeepseekKey,
  checkSemanticProvider, planSemantic, startSemantic, getSemanticRun, cancelSemantic,
} from './api.js';

const MODES = ['auto', 'deepseek', 'codex', 'claude_code', 'laya', 'deterministic'];
const POLL_MS = 1500;
const STOP_CODES = new Set([
  'BUDGET', 'PROVIDER_STOPPED', 'CANCELLED', 'NOTHING_NEW', 'NO_PROVIDER', 'NO_INTENT',
  'NO_INDEX', 'PRICE_UNKNOWN', 'ERROR',
]);

// One poll for the whole page. Each visit to Settings draws the panel again,
// and an interval per drawing kept polling for panels nobody could see.
let activePoll = null;

function stopPolling() {
  if (activePoll) clearInterval(activePoll);
  activePoll = null;
}

/** A failure, in the reader's language. Never the server's own sentence. */
function failureText(error, invalidKey = 'ai.error.failed') {
  const status = error && error.status;
  if (status === 400) return t(invalidKey);
  if (status === 409) return t('ai.error.busy');
  return t('ai.error.failed');
}

function stopText(code) {
  return STOP_CODES.has(code) ? t(`ai.stop.${code}`) : '';
}

function money(value) {
  if (value === null || value === undefined) return '';
  return `$${Number(value).toFixed(value < 0.01 ? 4 : 2)}`;
}

function providerName(id) {
  return t(`ai.mode.${id}`);
}

function summaryLine(data) {
  if (data.demo) return t('ai.summary.demo');
  if (!data.settings.enabled) return t('ai.summary.off');
  if (data.settings.mode === 'deterministic') return t('ai.summary.deterministic');
  if (!data.active_provider) return t('ai.summary.unavailable');
  return `${t('ai.summary.ready')} · ${t('ai.using', { provider: providerName(data.active_provider) })}`;
}

function fallbackLines(data) {
  return (data.fallbacks || [])
    .filter((row) => row.preferred)
    .map((row) => el('p', {
      className: 'ai__fallback',
      text: row.used && row.used !== 'deterministic'
        ? t('ai.fellBack', { preferred: providerName(row.preferred), used: providerName(row.used) })
        : t('ai.fellBackDeterministic', { preferred: providerName(row.preferred) }),
    }));
}

function stateText(row) {
  return t(`ai.state.${row.state}`);
}

export function renderAiSettings(host) {
  let data = null;
  let message = '';
  stopPolling();

  async function load(refresh = false) {
    try {
      data = await getSemantic(refresh);
      draw();
      if (data.run && data.run.status === 'running') poll();
    } catch (error) {
      replace(host, [el('p', { className: 'ai__error', text: failureText(error) })]);
    }
  }

  async function save(changes) {
    try {
      await patchSemantic(changes);
      message = t('ai.saved');
    } catch (error) {
      message = failureText(error, 'ai.error.setting');
    }
    await load();
  }

  function poll() {
    if (activePoll) return;
    activePoll = setInterval(async () => {
      if (!host.isConnected) {
        stopPolling();
        return;
      }
      try {
        const status = await getSemanticRun();
        if (!status.run || status.run.status !== 'running') {
          stopPolling();
          await load();
          return;
        }
        const node = host.querySelector('.ai__progress');
        if (node) {
          node.textContent = t('ai.running', {
            done: status.run.boards_done || 0, total: status.run.boards_total || 0,
          });
        }
      } catch {
        stopPolling();
      }
    }, POLL_MS);
  }

  function providerRow(row) {
    const children = [
      el('div', { className: 'ai__provider-head' }, [
        el('strong', { text: providerName(row.id) }),
        el('span', {
          className: `ai__state ai__state--${row.usable ? 'ok' : 'off'}`,
          text: stateText(row),
          dataset: { state: row.state },
        }),
      ]),
      el('p', { className: 'ai__billing', text: t(`ai.billing.${row.billing}`) }),
      el('p', { className: 'ai__sends', text: t(`ai.sends.${row.id}`) }),
    ];
    if (row.state === 'SIGN_IN_REQUIRED' && row.auth === 'api_key') {
      children.push(el('p', { className: 'ai__note', text: t('ai.authApiKey') }));
    }
    if (row.id === 'laya') children.push(el('p', { className: 'ai__note', text: t('ai.laya.why') }));
    if (row.id === 'deepseek') children.push(keyEditor());
    if (row.usable && !data.demo) {
      children.push(button(t('ai.check'), async (event) => {
        const target = event.currentTarget;
        target.disabled = true;
        target.textContent = t('ai.checking');
        try {
          const result = await checkSemanticProvider(row.id);
          message = `${providerName(row.id)}: ${t(`ai.state.${result.state}`)}`;
        } catch (error) {
          message = failureText(error);
        }
        await load(true);
      }, { className: 'btn btn--quiet' }));
    }
    return el('li', { className: 'ai__provider', dataset: { provider: row.id } }, children);
  }

  function keyEditor() {
    const configured = data.deepseek_key && data.deepseek_key.configured;
    const input = el('input', {
      className: 'input',
      attrs: { type: 'password', autocomplete: 'off', spellcheck: 'false', placeholder: t('ai.key.placeholder') },
    });
    const actions = [button(configured ? t('ai.key.replace') : t('ai.key.add'), async () => {
      const key = input.value.trim();
      input.value = '';
      if (!key) return;
      try {
        await saveDeepseekKey(key);
        message = t('ai.key.saved');
      } catch (error) {
        message = failureText(error, 'ai.error.key');
      }
      await load(true);
    }, { className: 'btn' })];
    if (configured) {
      actions.push(button(t('ai.key.remove'), async () => {
        try {
          await removeDeepseekKey();
          message = t('ai.key.removed');
        } catch (error) {
          message = failureText(error);
        }
        await load(true);
      }, { className: 'btn btn--quiet' }));
    }
    return el('div', { className: 'ai__key' }, [
      el('p', { text: configured ? t('ai.key.configured') : t('ai.key.missing') }),
      field('ai-deepseek-key', t('ai.key.label'), input, { hint: t('ai.key.help') }),
      el('div', { className: 'ai__actions' }, actions),
    ]);
  }

  function runPanel() {
    const settings = data.settings;
    const planHost = el('div', { className: 'ai__plan' });
    const budget = el('input', {
      className: 'input',
      attrs: { type: 'number', min: '0', max: '5', step: '0.05', inputmode: 'decimal' },
      props: { value: String(settings.budget_per_run_usd) },
      on: {
        change: () => {
          const value = Number(budget.value);
          if (Number.isFinite(value) && value >= 0 && value <= 5) {
            void save({ budget_per_run_usd: value });
          }
        },
      },
    });
    const estimate = button(t('ai.estimate'), async () => {
      estimate.disabled = true;
      try {
        const plan = await planSemantic();
        replace(planHost, planRows(plan));
      } catch (error) {
        replace(planHost, [el('p', { text: failureText(error) })]);
      }
      estimate.disabled = false;
    }, { className: 'btn' });
    const running = data.run && data.run.status === 'running';
    const children = [
      field('ai-budget', t('ai.budget'), budget, { hint: t('ai.budgetHelp') }),
      el('div', { className: 'ai__actions' }, running
        ? [el('p', { className: 'ai__progress', text: t('ai.running', { done: 0, total: 0 }) }),
          button(t('ai.cancel'), async () => {
            await cancelSemantic();
            await load();
          }, { className: 'btn btn--quiet' })]
        : [estimate]),
      planHost,
    ];
    if (data.last_run) children.push(lastRun(data.last_run));
    return children;
  }

  function planRows(plan) {
    if (!plan.provider) return [el('p', { text: t('ai.summary.unavailable') })];
    if (!plan.candidates) return [el('p', { text: stopText(plan.reason) || t('ai.planNone') })];
    const text = plan.billing === 'METERED_API'
      ? t('ai.plan', {
        candidates: plan.candidates, eligible: plan.eligible,
        expected: money(plan.expected_usd), worst: money(plan.worst_usd), budget: money(plan.budget_usd),
      })
      : t('ai.planSubscription', { candidates: plan.candidates, eligible: plan.eligible });
    return [
      el('p', { text }),
      button(t('ai.run', { count: plan.candidates }), async (event) => {
        event.currentTarget.disabled = true;
        try {
          await startSemantic();
          message = t('ai.recalc');
        } catch (error) {
          message = failureText(error);
        }
        await load();
      }, { className: 'btn btn--primary' }),
    ];
  }

  function lastRun(run) {
    const tokens = Number(run.input_tokens || 0) + Number(run.output_tokens || 0);
    const values = {
      published: run.published || 0, failed: run.failed || 0, calls: run.calls || 0,
      tokens, spent: money(run.spent_usd || 0),
    };
    const lines = [el('p', {
      className: 'ai__last',
      text: run.budget_usd === null || run.budget_usd === undefined
        ? t('ai.lastSubscription', values) : t('ai.last', values),
    })];
    const reason = stopText(String(run.stop_reason || ''));
    if (reason) lines.push(el('p', { text: reason }));
    return el('div', {}, lines);
  }

  function draw() {
    const settings = data.settings;
    const enabled = el('input', {
      attrs: { type: 'checkbox' },
      props: { checked: Boolean(settings.enabled), disabled: data.demo },
      on: { change: () => { void save({ enabled: enabled.checked }); } },
    });
    const mode = select(
      MODES.map((value) => ({ value, label: providerName(value) })),
      settings.mode,
      (value) => { void save({ mode: value }); },
      { ariaLabel: t('ai.provider') },
    );
    mode.disabled = data.demo || !settings.enabled;
    const providers = el('details', { className: 'ai__details' }, [
      el('summary', { text: t('ai.providersHead') }),
      el('ul', { className: 'ai__providers' }, data.providers.map(providerRow)),
      el('h3', { className: 'ai__subhead', text: t('ai.privacyHead') }),
      el('p', { text: t('ai.privacy.sends') }),
      el('p', { text: t('ai.privacy.never') }),
    ]);
    const children = [
      el('p', { className: 'ai__intro', text: t('ai.intro') }),
      el('p', {
        className: `ai__summary ai__summary--${data.active_provider ? 'ready' : 'fallback'}`,
        text: summaryLine(data),
        attrs: { role: 'status' },
      }),
      ...fallbackLines(data),
      el('label', { className: 'ai__toggle' }, [enabled, el('span', { text: t('ai.enabled') })]),
      field('ai-mode', t('ai.provider'), mode),
      providers,
    ];
    if (!data.demo && settings.enabled && settings.mode !== 'deterministic') {
      children.push(el('details', { className: 'ai__details' }, [
        el('summary', { text: t('ai.runHead') }),
        ...runPanel(),
      ]));
    }
    if (message) children.push(el('p', { className: 'ai__message', text: message, attrs: { role: 'status' } }));
    replace(host, children);
  }

  void load();
  return { reload: () => load(true) };
}
