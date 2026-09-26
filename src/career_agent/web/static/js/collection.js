/**
 * collection.js -- the one owner of "is Career Agent finding jobs right now".
 *
 * WHY THIS IS APP-LEVEL
 * ---------------------
 * A collection runs on the server's own thread for minutes. It used to be
 * watched by whichever screen started it: the guided setup's last card kept
 * its own two-second poll, the Settings panel kept another, and neither knew
 * about the other. Leaving Home and coming back rebuilt the page without the
 * progress, so a run that was still going looked stopped; redrawing the setup
 * card while a poll was in flight orphaned that poll, so every language switch
 * or return to the card added a loop that nothing could cancel.
 *
 * So there is exactly one watcher, created once by `main.js`. Screens
 * SUBSCRIBE to it and draw what it says; none of them polls, and none of them
 * can stop a run by being navigated away from. Only Stop stops a run.
 *
 * HOW OFTEN IT ASKS
 * -----------------
 * One timer and at most one request in flight, ever. Every two seconds while
 * a run is going and the tab is visible, every fifteen while it is hidden, and
 * not at all while nothing is running. The progress poll leaves out the
 * funnel, which is counted over the whole corpus and changes nothing on a
 * progress bar. Elapsed time ticks locally from the server's own clock, so the
 * seconds move without a request per second.
 *
 * WHAT IT DOES NOT SAY
 * -------------------
 * No time remaining. One source answers in a second and the next reads for
 * several minutes, so an estimate from the ones already done would be a guess
 * dressed as a measurement. It says what it measured: how many sources are
 * done, which one it is reading and for how long, and how long the whole run
 * has taken.
 */

import { el, button } from './dom.js';
import { t } from './i18n.js';

const POLL_MS = 2000;
const HIDDEN_POLL_MS = 15000;
const MAX_BACKOFF_MS = 30000;
//: A source reading for longer than this gets a sentence saying that is
//: normal. Measured, not guessed: most sources answer within half a minute.
const SLOW_SOURCE_S = 45;

export function createCollection(api, { disabled = false } = {}) {
  let data = null;
  //: Server clock minus browser clock, in milliseconds.
  let skew = 0;
  let timer = null;
  let ticker = null;
  let inflight = null;
  let failures = 0;
  //: Set by `start()` until the server's answer arrives.
  let starting = false;
  let stopping = false;
  let startError = '';
  //: A run this page saw running. Its scoring afterwards belongs to it, and
  //: its end is worth saying; a run that ended before the page opened is not.
  let watched = null;
  //: Whether the watched run's end has been reported to `onFinish` listeners.
  let finishedReported = false;
  let lastDone = -1;
  const listeners = new Set();
  const finishListeners = new Set();
  const progressListeners = new Set();

  function collecting() {
    return Boolean(data && data.running);
  }

  function scoringAfterRun() {
    return Boolean(
      !collecting() && watched && data && data.scoring && data.scoring.running
        && data.run && data.run.run_id === watched,
    );
  }

  /** What is happening, as one word the screens switch on. */
  function phase() {
    if (starting) return 'starting';
    if (collecting()) return 'collecting';
    if (scoringAfterRun()) return 'scoring';
    const run = data && data.run;
    if (run && watched && run.run_id === watched) {
      if (run.status === 'cancelled') return 'cancelled';
      if (run.status === 'failed') return 'failed';
      if (run.status === 'done') return 'finished';
    }
    return 'idle';
  }

  function active() {
    const now = phase();
    return now === 'starting' || now === 'collecting' || now === 'scoring';
  }

  function serverNow() {
    return Date.now() + skew;
  }

  function seconds(from, to = null) {
    const start = Date.parse(from || '');
    if (Number.isNaN(start)) return null;
    const end = to ? Date.parse(to) : serverNow();
    return Math.max(0, Math.round(((Number.isNaN(end) ? serverNow() : end) - start) / 1000));
  }

  function state() {
    const run = (data && data.run) || null;
    const now = phase();
    return {
      phase: now,
      active: active(),
      run,
      scoring: (data && data.scoring) || { running: false, done: 0, total: 0 },
      stopping,
      error: startError,
      elapsed: run ? seconds(run.started_at, now === 'collecting' ? null : run.finished_at) : null,
      currentFor: run && run.current ? seconds(run.current_started_at) : null,
    };
  }

  function emit(reason = 'update') {
    const snapshot = state();
    for (const listener of listeners) {
      try { listener(snapshot, reason); } catch (error) { console.error(error); }
    }
  }

  function syncTicker() {
    const moving = phase() === 'collecting' || phase() === 'scoring';
    if (moving && !ticker) {
      ticker = window.setInterval(() => emit('tick'), 1000);
    } else if (!moving && ticker) {
      window.clearInterval(ticker);
      ticker = null;
    }
  }

  function schedule() {
    if (timer) { window.clearTimeout(timer); timer = null; }
    if (disabled || !active()) return;
    const base = document.hidden ? HIDDEN_POLL_MS : POLL_MS;
    const delay = failures ? Math.min(base * 2 ** failures, MAX_BACKOFF_MS) : base;
    timer = window.setTimeout(() => { timer = null; refresh(); }, delay);
  }

  function absorb(payload) {
    data = payload;
    if (payload && payload.now) {
      const server = Date.parse(payload.now);
      if (!Number.isNaN(server)) skew = server - Date.now();
    }
    const run = payload && payload.run;
    if (payload && payload.running && run && run.run_id !== watched) {
      watched = run.run_id;
      finishedReported = false;
      lastDone = -1;
    }
    if (!payload || !payload.running) stopping = false;
    const done = run && run.run_id === watched ? Number(run.boards_done) || 0 : -1;
    if (done !== lastDone && done >= 0) {
      lastDone = done;
      for (const listener of progressListeners) {
        try { listener(state()); } catch (error) { console.error(error); }
      }
    }
    const now = phase();
    if (!finishedReported && watched && (now === 'finished' || now === 'cancelled' || now === 'failed')) {
      finishedReported = true;
      for (const listener of finishListeners) {
        try { listener(state()); } catch (error) { console.error(error); }
      }
    }
  }

  /**
   * Ask the server once. Concurrent callers share the request in flight, so
   * no screen can add a second one by asking at the same moment.
   */
  function refresh() {
    if (disabled) return Promise.resolve(state());
    if (inflight) return inflight;
    inflight = (async () => {
      try {
        absorb(await api.getRetrieval({ funnel: false }));
        failures = 0;
      } catch (error) {
        failures += 1;
      } finally {
        inflight = null;
      }
      syncTicker();
      schedule();
      emit();
      return state();
    })();
    return inflight;
  }

  /**
   * Begin finding jobs. `kind` is `all` (every source that may be refreshed)
   * or `retrieval` (the three documented ATS APIs, from Settings).
   */
  async function start(kind = 'all') {
    if (active()) return state();
    starting = true;
    startError = '';
    syncTicker();
    emit();
    try {
      const run = kind === 'retrieval' ? await api.startRetrieval()
        : kind === 'due' ? await api.refreshDueSources()
          : await api.findJobs();
      if (run && run.started === false) {
        // Nothing was due: no run exists, and the caller says so.
        starting = false;
        emit();
        return { ...state(), nothingDue: true, coolingDown: run.cooling_down || [] };
      }
      if (run && run.run_id) {
        watched = run.run_id;
        finishedReported = false;
        lastDone = -1;
        // The server has started it. Hold that as the state until a poll
        // says otherwise, so one failed poll cannot make a live run look idle
        // and leave nothing scheduled to ask again.
        data = { ...(data || {}), running: true, run };
      }
    } catch (error) {
      // Already running elsewhere (another tab, a reload): follow that one.
      if (!(error && error.status === 409 && /already/i.test(error.message || ''))) {
        startError = error.userMessage || error.message;
      }
    } finally {
      starting = false;
    }
    // A poll already in flight was asked BEFORE the run existed. Sharing it
    // would absorb a stale "nothing running" and schedule no further poll, so
    // wait for it and ask again.
    if (inflight) await inflight;
    return refresh();
  }

  /** Only this stops a run. Leaving a page never does. */
  async function cancel() {
    stopping = true;
    emit();
    try {
      await api.cancelRetrieval();
    } catch {
      // The next poll says what actually happened.
    }
    return refresh();
  }

  /** Forget a finished run, so it stops being announced. */
  function dismiss() {
    if (active()) return;
    watched = null;
    startError = '';
    emit();
  }

  function subscribe(listener) {
    listeners.add(listener);
    listener(state(), 'update');
    return () => listeners.delete(listener);
  }

  function onFinish(listener) {
    finishListeners.add(listener);
    return () => finishListeners.delete(listener);
  }

  /** Called each time another source is done: jobs may have just arrived. */
  function onProgress(listener) {
    progressListeners.add(listener);
    return () => progressListeners.delete(listener);
  }

  if (!disabled && typeof document !== 'undefined') {
    document.addEventListener('visibilitychange', () => {
      if (active()) {
        if (document.hidden) schedule();
        else refresh();
      }
    });
  }

  return { refresh, start, cancel, dismiss, subscribe, onFinish, onProgress, state };
}

/** "45 s", "3 min 05 s", "1 h 02 min". Measured time, never an estimate. */
export function duration(total) {
  if (total === null || total === undefined) return '';
  const s = Math.max(0, Math.round(total));
  if (s < 60) return t('time.seconds', { s });
  const m = Math.floor(s / 60);
  if (m < 60) return t('time.minutes', { m, s: String(s % 60).padStart(2, '0') });
  return t('time.hours', { h: Math.floor(m / 60), m: String(m % 60).padStart(2, '0') });
}

/**
 * The progress of the current run, drawn once and then UPDATED IN PLACE.
 *
 * One drawing for every screen that shows a run, so no two screens describe
 * the same run in different words. Updated rather than rebuilt: the seconds
 * move every second, and rebuilding would take keyboard focus off Stop and
 * make a screen reader announce the whole block each time. The ticking line
 * sits in its own `aria-live="off"` region for the same reason.
 *
 * `compact` is the one-line form for the bar across the top of every page.
 */
export function createProgressView(collection, { compact = false, stopId = null } = {}) {
  const line = el('p', { className: 'collect__line' });
  const meter = el('div', {
    className: 'setup__bar',
    attrs: { role: 'progressbar', 'aria-valuemin': '0' },
  });
  const fill = el('span', { className: 'setup__barfill' });
  meter.appendChild(fill);
  const meta = el('p', { className: 'collect__meta', attrs: { 'aria-live': 'off' } });
  const slow = el('p', { className: 'collect__note', props: { hidden: true } });
  const noEta = el('p', { className: 'collect__note', props: { hidden: true } });
  const stop = button('', () => {
    stop.disabled = true;
    stop.textContent = t('collect.stopping');
    collection.cancel();
  }, { className: 'btn btn--quiet', attrs: stopId ? { id: stopId } : {} });
  const root = el('div', { className: `collect${compact ? ' collect--compact' : ''}` },
    compact ? [line, meta] : [line, meter, meta, slow, noEta, stop]);

  function setBar(done, total, label) {
    meter.hidden = compact || !total;
    meter.setAttribute('aria-valuemax', String(total || 1));
    meter.setAttribute('aria-valuenow', String(done));
    meter.setAttribute('aria-label', label);
    fill.style.width = `${total ? Math.round((done / total) * 100) : 0}%`;
  }

  function setText(node, text) {
    if (node.textContent !== text) node.textContent = text;
    node.hidden = !text;
  }

  function update(snapshot) {
    const run = snapshot.run || {};
    // Words first, visibility second: `setText` shows any node it gives text
    // to, and Stop belongs only to a run the server is actually reading.
    setText(stop, snapshot.stopping ? t('collect.stopping') : t('setup.ready.stop'));
    stop.hidden = compact || snapshot.phase !== 'collecting';
    stop.disabled = Boolean(snapshot.stopping);
    if (snapshot.phase === 'starting') {
      setText(line, t('setup.ready.starting'));
      setBar(0, 0, t('setup.ready.progressLabel'));
      setText(meta, '');
      setText(slow, '');
      setText(noEta, '');
      return;
    }
    if (snapshot.phase === 'scoring') {
      const { done = 0, total = 0 } = snapshot.scoring || {};
      setText(line, total
        ? t('collect.scoring', { done: done.toLocaleString(), total: total.toLocaleString() })
        : t('collect.scoringStart'));
      setBar(Math.min(done, total), total, t('collect.scoringLabel'));
      setText(meta, compact ? '' : t('collect.took', { time: duration(snapshot.elapsed) }));
      setText(slow, '');
      setText(noEta, '');
      return;
    }
    const total = Number(run.boards_total) || 0;
    const done = Math.min(Number(run.boards_done) || 0, total);
    setText(line, t('setup.ready.progress', { done, total }));
    setBar(done, total, t('setup.ready.progressLabel'));
    const parts = [t('collect.elapsed', { time: duration(snapshot.elapsed) })];
    if (!compact && run.current) {
      parts.unshift(t('collect.now', { source: run.current, time: duration(snapshot.currentFor) }));
    }
    if (!compact && Number(run.skipped) > 0) parts.push(t('collect.deferred', { n: run.skipped }));
    setText(meta, parts.join(' · '));
    setText(slow, !compact && run.current && snapshot.currentFor >= SLOW_SOURCE_S ? t('collect.slow') : '');
    setText(noEta, compact ? '' : t('collect.noEta'));
  }

  return { root, update };
}

/** The sentence for a run that has ended, or '' while it has not. */
export function outcomeText(snapshot) {
  const run = snapshot.run || {};
  const sources = run.sources || [];
  const ok = sources.filter((row) => row.status === 'ok').length;
  if (snapshot.phase === 'cancelled') return t('setup.ready.cancelled', { ok });
  if (snapshot.phase === 'failed') return t('setup.ready.failed');
  if (snapshot.phase === 'finished') {
    return t('collect.finished', { ok, total: sources.length, time: duration(snapshot.elapsed) });
  }
  return '';
}
