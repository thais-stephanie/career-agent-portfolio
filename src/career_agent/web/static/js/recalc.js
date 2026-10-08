/**
 * recalc.js -- the one "Recalculate" flow, shared by Jobs and Home.
 *
 * One flow, two looks: it starts the same targeted rescore (POST
 * /api/rescore), follows the same progress, and refuses to start a second
 * run. A run already going when the control is drawn (started from the other
 * page, another tab or the command line) is FOLLOWED rather than offered
 * again. `driveRescore` is the flow; `recalcButton` is the Jobs look, and
 * Home draws its own task row over the same flow.
 *
 * Opens no socket beyond this machine and asks no model of either kind.
 */

import { el } from './dom.js';
import { t } from './i18n.js';
import * as api from './api.js';

/**
 * Wire `control` to the rescore flow. `show(state)` draws each moment:
 * `phase` is idle, starting, running, done, failed, lost or refused;
 * `busy` is true while a run is starting or going (a click then does
 * nothing); `done` and `total` count jobs while running; `message` says why
 * a start was refused. `onDone` runs once a followed run finished well.
 */
export function driveRescore(control, { show, onDone = () => {} }) {
  let polling = null;
  let busy = false;
  const draw = (phase, extra = {}) => {
    busy = phase === 'starting' || phase === 'running';
    show({ phase, busy, ...extra });
  };

  async function poll() {
    const state = await api.getRescore();
    const run = state.run || {};
    if (state.running) {
      draw('running', { done: Number(run.boards_done) || 0, total: Number(run.boards_total) || 0 });
      return;
    }
    window.clearInterval(polling);
    polling = null;
    if (run.status === 'failed') {
      draw('failed');
      return;
    }
    draw('done');
    onDone();
  }

  function follow() {
    if (polling) return;
    draw('running', { done: 0, total: 0 });
    let misses = 0;
    polling = window.setInterval(() => {
      poll().then(() => { misses = 0; }).catch(() => {
        // A few missed polls are a busy server; many are a stopped one.
        misses += 1;
        if (misses < 10) return;
        window.clearInterval(polling);
        polling = null;
        draw('lost');
      });
    }, 700);
  }

  control.addEventListener('click', async () => {
    if (busy) return;
    draw('starting');
    try {
      await api.startRescore();
    } catch (error) {
      draw('refused', { message: error.userMessage || t('rescore.couldNotStart') });
      return;
    }
    follow();
  });

  // ALREADY RUNNING? Follow it instead of offering a second one.
  api.getRescore().then((state) => {
    if (state && state.running) follow();
  }).catch(() => {});
}

/** The Jobs look: a button whose label carries the progress. */
export function recalcButton({
  onDone = () => {}, className = 'btn btn--primary', labelKey = 'app.rescore',
} = {}) {
  const label = el('span', { text: t(labelKey) });
  const control = el('button', { className, attrs: { type: 'button' } }, [label]);
  const words = {
    starting: () => t('app.starting'),
    running: ({ done, total }) => (total
      ? t('rescore.progress', { done: done.toLocaleString(), total: total.toLocaleString() })
      : t('rescore.working')),
    done: () => t('app.rescoreDone'),
    failed: () => t('app.rescoreFailed'),
    lost: () => t('app.rescoreLost'),
    refused: ({ message }) => message,
  };
  driveRescore(control, {
    onDone,
    show: (state) => {
      control.disabled = state.busy;
      label.textContent = (words[state.phase] || (() => t(labelKey)))(state);
    },
  });
  return control;
}
