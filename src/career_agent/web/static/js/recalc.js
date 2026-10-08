/**
 * recalc.js -- the one "Recalculate" control, shared by Jobs and Home.
 *
 * One flow, two places: it starts the same targeted rescore (POST
 * /api/rescore), shows the same progress, and refuses to start a second run.
 * A run already going when the control is drawn (started from the other
 * page, another tab or the command line) is FOLLOWED rather than offered
 * again: the button is disabled and shows how far it got.
 *
 * Opens no socket beyond this machine and asks no model of either kind.
 */

import { el } from './dom.js';
import { t } from './i18n.js';
import * as api from './api.js';

/**
 * `onDone` runs once a run this control followed has finished successfully,
 * so the page can ask for its data again.
 */
export function recalcButton({
  onDone = () => {}, className = 'btn btn--primary', labelKey = 'app.rescore',
} = {}) {
  const label = el('span', { text: t(labelKey) });
  const control = el('button', { className, attrs: { type: 'button' } }, [label]);

  let polling = null;

  async function poll() {
    const state = await api.getRescore();
    const run = state.run || {};
    if (state.running) {
      const done = Number(run.boards_done) || 0;
      const total = Number(run.boards_total) || 0;
      label.textContent = total
        ? t('rescore.progress', { done: done.toLocaleString(), total: total.toLocaleString() })
        : t('rescore.working');
      return;
    }
    window.clearInterval(polling);
    polling = null;
    control.disabled = false;
    if (run.status === 'failed') {
      label.textContent = t('app.rescoreFailed');
      return;
    }
    label.textContent = t('app.rescoreDone');
    onDone();
  }

  function follow() {
    if (polling) return;
    control.disabled = true;
    let misses = 0;
    polling = window.setInterval(() => {
      poll().then(() => { misses = 0; }).catch(() => {
        // A few missed polls are a busy server; many are a stopped one.
        misses += 1;
        if (misses < 10) return;
        window.clearInterval(polling);
        polling = null;
        control.disabled = false;
        label.textContent = t('app.rescoreLost');
      });
    }, 700);
  }

  control.addEventListener('click', async () => {
    control.disabled = true;
    label.textContent = t('app.starting');
    try {
      await api.startRescore();
    } catch (error) {
      control.disabled = false;
      label.textContent = error.userMessage || t('rescore.couldNotStart');
      return;
    }
    follow();
  });

  // ALREADY RUNNING? Follow it instead of offering a second one.
  api.getRescore().then((state) => {
    if (state && state.running) {
      label.textContent = t('rescore.working');
      follow();
    }
  }).catch(() => {});

  return control;
}
