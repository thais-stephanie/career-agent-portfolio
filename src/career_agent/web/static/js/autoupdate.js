/**
 * autoupdate.js -- the choice "Update jobs when I open the app".
 *
 * Off until the person turns it on, in the setup or here. What it does and what
 * it never does is said beside the switch, with the limits the server enforces
 * (web/auto_update.py): overdue sites only, ten minutes, no AI, no recalculation
 * of the whole catalogue, and a limited update may leave work for later.
 */

import { el } from './dom.js';
import { t } from './i18n.js';
import * as api from './api.js';
import { switchControl } from './switch.js';

function when(iso) {
  return String(iso || '').slice(0, 16).replace('T', ' ');
}

function lastLine(state) {
  const r = state.last_result;
  if (!r) return t('auto.neverRan');
  const parts = [t('auto.last', {
    date: when(r.at), updated: r.updated, asked: r.asked, later: r.left_for_later, jobs: r.jobs_new,
  })];
  const w = r.workable;
  if (w) {
    const n = { new: w.jobs_new, changed: w.jobs_changed };
    if (w.outcome === 'DEFERRED' && w.reason !== 'ALREADY_RUNNING') {
      parts.push(w.jobs_new + w.jobs_changed + w.jobs_unchanged ? t('auto.workablePartial', n)
        : t('auto.workableNone'));
    } else if (w.outcome === 'FAILED') {
      parts.push(t('auto.workableNone'));
    } else if (w.outcome === 'OK') {
      parts.push(w.jobs_new + w.jobs_changed ? t('auto.workableDone', n) : t('auto.workableSame'));
    }
  }
  if (r.scoring === 'PENDING_LARGE') parts.push(t('auto.scoringLarge'));
  if (r.stopped === 'ERROR') parts.push(t('auto.stoppedError'));
  return parts.join(' ');
}

/** The switch and its explanation. `onChange(enabled)` runs after a save. */
export function autoUpdateCard({ onChange = null } = {}) {
  const root = el('section', { className: 'au', attrs: { 'aria-labelledby': 'au-head' } });
  const message = el('p', { className: 'au__status', attrs: { role: 'status', 'aria-live': 'polite' } });

  async function draw() {
    let state;
    try {
      state = await api.getAutoUpdate();
    } catch {
      root.replaceChildren(el('p', { className: 'au__status', text: t('auto.unavailable') }));
      return;
    }
    if (!state.available) {
      root.replaceChildren(el('p', { className: 'au__status', text: t('auto.demo') }));
      return;
    }
    const lim = state.limits;
    const toggle = switchControl(state.enabled, t('auto.label'), t('auto.hint', {
      days: lim.stale_days, minutes: lim.minutes,
    }), async (node) => {
      const next = node.getAttribute('aria-checked') !== 'true';
      node.disabled = true;
      try {
        await api.setAutoUpdate(next);
        node.setAttribute('aria-checked', next ? 'true' : 'false');
        message.textContent = t(next ? 'auto.on' : 'auto.off');
        if (onChange) onChange(next);
      } catch (error) {
        message.textContent = error.userMessage || error.message;
      }
      node.disabled = false;
    });
    root.replaceChildren(
      el('h3', { className: 'au__head', attrs: { id: 'au-head' }, text: t('auto.head') }),
      toggle,
      el('details', { className: 'au__more' }, [
        el('summary', { text: t('auto.more') }),
        el('ul', {}, [
          t('auto.pointWhat', { days: lim.stale_days }),
          t('auto.pointBudget', { minutes: lim.minutes, sites: lim.max_sites, requests: lim.max_requests }),
          t('auto.pointWorkable', { minutes: lim.workable_minutes }),
          t('auto.pointStop'),
          t('auto.pointNever'),
          t('auto.pointLimit'),
        ].map((text) => el('li', { text }))),
      ]),
      el('p', { className: 'au__last', text: lastLine(state) }),
      message,
    );
  }

  void draw();
  return root;
}
