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
 * ONE WATCHER FOR THE WHOLE PAGE (2026-10-13). Every control used to start its
 * own 700 ms poll, and Home draws a new control on every redraw, so a long
 * run was polled two to three times a second by loops nobody could see, and
 * ten missed polls ended a loop for good. Now one loop polls every two seconds
 * while something runs, every control subscribes to it, a control taken off
 * the page stops listening, and a missed poll backs off and keeps trying: the
 * screen says it is reconnecting instead of giving up.
 *
 * The server's `state.phase` says what happened: RUNNING, RUNNING_ELSEWHERE,
 * DONE, FAILED, CANCELLED, INTERRUPTED or IDLE, with the scores committed
 * so far (`done`) and planned (`total`). A failure or an interruption is shown
 * as one, with what was saved, never as the starting notice again.
 *
 * Opens no socket beyond this machine and asks no model of either kind.
 */

import { el } from './dom.js';
import { t } from './i18n.js';
import * as api from './api.js';

const POLL_MS = 2000;
const MAX_BACKOFF_MS = 15000;
const ACTIVE = new Set(['RUNNING', 'RUNNING_ELSEWHERE']);

const listeners = new Set();
let timer = null;
let last = null;
let misses = 0;
let inFlight = null;
//: Controls that started a run and have not yet seen it answered for.
let awaiting = 0;

//: Which run an answer is about: its id, or its start (to the second) when
//: it failed before its run row was written.
function runOf(state) {
  return (state && (state.run_id || state.started_at)) || null;
}

function isActive(status) {
  return Boolean(status && status.state && ACTIVE.has(status.state.phase));
}

async function tick() {
  timer = null;
  if (!inFlight) {
    inFlight = api.getRescore()
      .then((status) => { last = status; misses = 0; })
      .catch(() => { misses += 1; })
      .finally(() => { inFlight = null; });
  }
  await inFlight;
  const snapshot = { status: last, unreachable: misses > 0 };
  // One listener's fault never stops the others, nor the polling.
  for (const listener of [...listeners]) {
    try {
      listener(snapshot);
    } catch (error) {
      console.error(error); // eslint-disable-line no-console
    }
  }
  if (!listeners.size || timer) return;
  if (misses > 0) {
    timer = window.setTimeout(tick, Math.min(MAX_BACKOFF_MS, POLL_MS * 2 ** Math.min(misses, 3)));
  } else if (isActive(last) || awaiting > 0) {
    timer = window.setTimeout(tick, POLL_MS);
  }
}

/** Ask now, and keep asking while something runs. */
export function refreshRescore() {
  if (timer) window.clearTimeout(timer);
  timer = null;
  // A question already on its way may have been asked before a run began;
  // its answer is delivered, and then the question is asked again.
  return inFlight ? inFlight.then(() => tick()) : tick();
}

/** Listen to the one watcher. Returns the way to stop listening. */
export function watchRescore(listener) {
  listeners.add(listener);
  if (last) listener({ status: last, unreachable: misses > 0 });
  if (!timer && !inFlight) refreshRescore();
  return () => listeners.delete(listener);
}

/**
 * Wire `control` to the rescore flow. `show(state)` draws each moment:
 * `phase` is idle, starting, running, reconnecting, done, failed,
 * interrupted, cancelled or refused; `busy` is true while a run is starting
 * or going (a click then does nothing); `done` and `total` count committed
 * and planned scores; `message` says why a start was refused or a run failed.
 * `onDone` runs once, when a run this page watched ends (well or not), so
 * counts read when the page opened are read again.
 */
export function driveRescore(control, { show, onDone = () => {} }) {
  let busy = false;
  let watched = false;
  let starting = false;
  let attached = false;
  let lastDone = 0;
  let lastTotal = 0;
  // After a start: the run that was last reported BEFORE it,
  // so an answer about that older run, or an idle one asked before the new
  // run began, is never read as the new run having ended.
  let before;
  let waitingFor = false;
  let stop = () => {};
  const settle = () => {
    if (waitingFor) { waitingFor = false; awaiting -= 1; }
  };
  const draw = (phase, extra = {}) => {
    busy = phase === 'starting' || phase === 'running' || phase === 'reconnecting';
    show({ phase, busy, ...extra });
  };

  const listener = ({ status, unreachable }) => {
    // A control taken off the page stops listening; one not yet placed is
    // simply not drawn into yet.
    if (control.isConnected) attached = true;
    else if (attached) { settle(); stop(); return; }
    if (unreachable && (watched || starting)) {
      draw('reconnecting', { done: lastDone, total: lastTotal });
      return;
    }
    const state = (status && status.state) || { phase: 'IDLE' };
    if (waitingFor) {
      if (!ACTIVE.has(state.phase) && runOf(state) === before) return;
      settle();
    }
    lastDone = Number(state.done) || 0;
    lastTotal = Number(state.total) || 0;
    const counts = { done: lastDone, total: lastTotal, message: state.error || '' };
    if (ACTIVE.has(state.phase)) {
      watched = true;
      starting = false;
      draw('running', counts);
      return;
    }
    if (starting) return; // a poll answered before the run began
    if (watched) {
      watched = false;
      draw(state.phase === 'DONE' ? 'done' : state.phase.toLowerCase(), counts);
      onDone(state);
      return;
    }
    // Not watched from this page: how the last run ended is said only when it
    // did NOT end well, so a stop is never mistaken for "nothing happened".
    if (state.phase === 'FAILED' || state.phase === 'INTERRUPTED') {
      draw(state.phase.toLowerCase(), counts);
      return;
    }
    draw('idle', counts);
  };
  stop = watchRescore(listener);

  control.addEventListener('click', async () => {
    if (busy) return;
    starting = true;
    before = runOf(last && last.state);
    draw('starting');
    try {
      await api.startRescore();
    } catch (error) {
      starting = false;
      draw('refused', { message: error.userMessage || t('rescore.couldNotStart') });
      return;
    }
    watched = true;
    if (!waitingFor) { waitingFor = true; awaiting += 1; }
    refreshRescore();
  });
  return stop;
}

/** Words for each phase. `keys` names the look's own catalogue entries for
 *  starting, progress, working and done; the rest are shared. */
export function rescoreWords(keys) {
  const counts = ({ done, total }) => ({
    done: done.toLocaleString(), total: total.toLocaleString(),
    left: Math.max(0, total - done).toLocaleString(),
  });
  return {
    starting: () => t(keys.starting),
    running: (state) => (state.total ? t(keys.progress, counts(state)) : t(keys.working)),
    reconnecting: () => t('rescore.reconnecting'),
    done: () => t(keys.done),
    failed: (state) => t('rescore.failedAt', counts(state)),
    interrupted: (state) => t('rescore.interruptedAt', counts(state)),
    cancelled: (state) => t('rescore.interruptedAt', counts(state)),
    refused: ({ message }) => message,
  };
}

/** The Jobs look: a button whose label carries the progress. */
export function recalcButton({
  onDone = () => {}, className = 'btn btn--primary', labelKey = 'app.rescore',
} = {}) {
  const label = el('span', { text: t(labelKey) });
  const control = el('button', { className, attrs: { type: 'button' } }, [label]);
  const words = rescoreWords({
    starting: 'app.starting', progress: 'rescore.progress', working: 'rescore.working', done: 'app.rescoreDone',
  });
  driveRescore(control, {
    onDone,
    show: (state) => {
      control.disabled = state.busy;
      label.textContent = (words[state.phase] || (() => t(labelKey)))(state);
    },
  });
  return control;
}
