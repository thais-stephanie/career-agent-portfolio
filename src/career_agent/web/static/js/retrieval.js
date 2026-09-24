/**
 * retrieval.js -- going and getting more jobs, and seeing what happened.
 *
 * The funnel is the honest part. Every stage is COUNTED from the database, so
 * the panel can say "18,549 scored, 15,244 roles, 163 recommended" and each of
 * those is a query rather than a claim. Showing a total without showing what
 * narrowed it is how a product ends up looking like it found 163 jobs when it
 * read 18,550.
 *
 * PER-SOURCE, BECAUSE "SOMETHING FAILED" IS NOT USEFUL. One source down and
 * every source down are different situations and lead to different decisions.
 * A source nobody attempted says so rather than looking healthy.
 *
 * IT DOES NOT POLL. The run happens on the server's own thread and the app has
 * one watcher for it (`collection.js`), which this panel subscribes to while
 * Settings is open. The funnel is asked for once when the panel opens and once
 * when a run ends -- it is seven counts over the whole corpus, and nothing on
 * it changes fast enough to be worth asking every two seconds.
 */

import { el, button, replace } from './dom.js';
import { t } from './i18n.js';

/** The stages, in the order they narrow, with what each one means. */
//
// KEYS ONLY. The word and the sentence live in the catalogue under
// `stage.<key>` and `stage.<key>Help`, resolved when the panel is drawn.
const STAGES = [
  'fetched', 'active', 'normalised', 'scored', 'deduplicated', 'eligible', 'recommended',
];

export function createRetrievalPanel(mount, api, collection) {
  //: The funnel and the last-looked date, from the last full answer.
  let summary = null;
  let unsubscribe = null;
  let unfinish = null;
  let token = 0;

  function stop() {
    if (unsubscribe) { unsubscribe(); unsubscribe = null; }
    if (unfinish) { unfinish(); unfinish = null; }
  }

  async function loadSummary() {
    const mine = ++token;
    try {
      const data = await api.getRetrieval();
      if (mine !== token) return;
      summary = data;
    } catch (error) {
      if (mine !== token) return;
      replace(mount, [el('p', { className: 'retr__error', text: error.userMessage || error.message })]);
      return;
    }
    draw(collection.state(), 'update');
  }

  function draw(snapshot, reason) {
    if (!summary || reason === 'tick') return;
    render({
      ...summary,
      running: snapshot.phase === 'collecting' || snapshot.phase === 'starting',
      run: snapshot.run || summary.run,
      error: snapshot.error,
    });
  }

  /** Opened: one full answer, then follow the app's watcher. */
  async function refresh() {
    stop();
    unsubscribe = collection.subscribe(draw);
    unfinish = collection.onFinish(() => loadSummary());
    await loadSummary();
  }

  function render(data) {
    const run = data.run;
    const running = Boolean(data.running);
    const children = [];

    const startButton = button(running ? t('retrieval.retrieving') : t('rail.retrieve'), () => {
      startButton.disabled = true;
      collection.start('retrieval');
    }, { className: 'retr__start' });
    startButton.disabled = running;

    const status = el('div', { className: 'retr__status' });
    if (data.error) {
      status.appendChild(el('span', { className: 'retr__error', text: data.error }));
    }
    const actions = [startButton];
    if (running) {
      actions.push(button(t('action.cancel'), () => collection.cancel(), { className: 'retr__cancel' }));
    }
    children.push(el('div', { className: 'retr__row' }, [...actions, status]));

    children.push(el('p', {
      className: 'retr__when',
      text: data.last_retrieval_at
        ? t('retrieval.lastLooked', { when: data.last_retrieval_at })
        : t('retrieval.never'),
      attrs: {
        title: t('retrieval.lastLookedHelp'),
      },
    }));

    if (run) {
      const label = {
        running: t('retrieval.runningBoards', {
          done: run.boards_done,
          total: run.boards_total || '?',
        }),
        done: t('retrieval.finished'),
        cancelled: t('retrieval.cancelled'),
        failed: t('retrieval.failedWith', {
          error: run.error || t('retrieval.unknownError'),
        }),
      }[run.status] || run.status;
      children.push(el('p', {
        className: `retr__run retr__run--${run.status}`,
        text: label,
      }));
    }

    // The funnel. Always shown, because it describes the corpus rather than
    // the run: it is true before anyone presses anything.
    const funnel = data.funnel || {};
    children.push(el('ul', { className: 'retr__funnel', attrs: { 'aria-label': t('retrieval.funnel') } },
      STAGES.filter((key) => key in funnel).map((key) => el('li', {
        className: 'retr__stage',
        attrs: { title: t(`stage.${key}Help`) },
      }, [
        el('span', { className: 'retr__stage-label', text: t(`stage.${key}`) }),
        el('span', { className: 'retr__stage-count num', text: String(funnel[key]) }),
      ]))));

    // **WHY EVERY STAGE BELOW `scored` CAN READ ZERO OVER A FULL CORPUS.**
    //
    // The funnel is keyed on the preferences IN FORCE, which is correct: a
    // score is only true relative to the question it answered. When those
    // preferences change, the old scores do not vanish -- they stop answering
    // the current question, and four rows go to zero at once.
    //
    // Read cold on 2026-09-08 that was `Fetched 22048 / Scored 0` beside a
    // header counting 19,469 scores, and it looked exactly like data loss.
    // Nothing had been lost. This sentence is the difference.
    if (!funnel.scored && funnel.scored_other_versions) {
      children.push(el('p', { className: 'retr__note' }, [
        el('span', { text: t('retrieval.scoredElsewhere').replace(
          '{n}', String(funnel.scored_other_versions)) }),
      ]));
    }

    const sources = (run && run.sources) || [];
    if (sources.length) {
      children.push(el('h4', { className: 'retr__heading', text: t('retrieval.bySource') }));
      children.push(el('ul', { className: 'retr__sources' }, sources.map((source) => el('li', {
        className: `retr__source retr__source--${source.status.replace(/\s+/g, '-')}`,
      }, [
        el('span', { className: 'retr__source-name', text: source.provider }),
        el('span', { className: 'retr__source-state', text: source.status }),
        el('span', {
          className: 'retr__source-counts num',
          text: `${source.boards_succeeded}/${source.boards_attempted}`,
          attrs: { title: t('retrieval.boardsHelp') },
        }),
        ...(source.failures_total
          ? [el('span', {
            className: 'retr__source-fail',
            text: `${source.failures_total} failed`,
            attrs: { title: source.failures.join(' · ') },
          })]
          : []),
      ]))));
    }

    replace(mount, children);
    mount.className = 'retr';
  }

  return { refresh, stop };
}
