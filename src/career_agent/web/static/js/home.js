/**
 * home.js -- where the job search stands today.
 *
 * The Jobs list used to be the landing page, which answered "which postings
 * exist" to somebody who had not asked it yet. This answers the question a
 * person actually opens the product with.
 *
 * EVERY NUMBER DECLARES WHAT KIND OF NUMBER IT IS. A STOCK is how many things
 * are in a state right now; an EVENT COUNT is how many times something
 * happened since she last marked the list read. Rendering both under one
 * heading is how "5 interviews" comes to mean either five conversations or
 * five moves depending on who wrote the query, so the two are labelled
 * differently and the labels come from the server's own classification.
 *
 * NOTHING HERE RANKS ANYTHING. The two lists are the digest's own sections,
 * ordered by the same deterministic score every other surface uses. A
 * dashboard with its own ordering would be a second opinion nobody could
 * trace, which is what ADR-0004 refuses.
 *
 * AND IT IS NOT AN ANALYTICS DASHBOARD. No sparkline, no percentage, no
 * trend arrow. Those need a series, and this product stores states and
 * events rather than a time series -- a trend drawn from two points would be
 * a decoration that reads as a measurement.
 */

import { el, button, replace } from './dom.js';
import { createFirstRun } from './firstrun.js';
import { createSetup, setupPostponed, setupResumeStep } from './setup.js';
import { t } from './i18n.js';
import * as api from './api.js';
import { createProgressView, outcomeText } from './collection.js';

/**
 * The cards, in reading order.
 *
 * `offers` is last and is dropped when empty: a row of zeroes reads as a
 * report about the person rather than about the data, and an offer count of
 * nothing is the normal state of a job search for most of its length.
 */
const CARDS = ['new', 'saved', 'applied', 'interviews', 'offers', 'progressed'];

/** Cards that are meaningless at zero and are simply not drawn. */
const HIDE_WHEN_EMPTY = new Set(['offers', 'progressed']);

/** Which card of the guided setup answers each gap `home.profile_gaps` names. */
const SETUP_FOR_GAP = {
  work: 'work',
  residence: 'home',
  hiring_scopes: 'hire',
  compensation: 'pay',
};

/** The first card that answers one of these gaps, or the start. */
function firstSetupCard(gaps) {
  const gap = gaps.find((name) => SETUP_FOR_GAP[name]);
  return gap ? SETUP_FOR_GAP[gap] : 'welcome';
}

export function createHome({
  onOpenJob = null, onGoTo = null, onSetupShown = null, collection = null,
} = {}) {
  const root = el('div', { className: 'home' });
  // THE GUIDED SETUP, one question per card. It REPLACES this page on a fresh
  // install -- where six steps, four zeroes and a second list of the same gaps
  // used to greet somebody who had not answered anything -- and it is what
  // every "answer this" button on this page opens, at the right card.
  // Leaving it ("Do this later", "Go to Home") comes back here.
  const setup = createSetup({
    onExit: () => {
      showingSetup = false;
      load();
    },
    onGoTo: (page) => onGoTo && onGoTo(page),
    collection,
  });
  //: The card to open at, when something asked for the setup explicitly.
  let setupAt = null;
  let showingSetup = false;
  //: Whether the setup has already opened BY ITSELF during this page session.
  //: It offers itself once; coming back to Home after leaving it -- or after
  //: finding jobs from its last card -- must not start it over.
  let autoOpened = false;

  // THE SIX STEPS OF A FIRST RUN, above everything else on this page.
  //
  // Here rather than on a page of its own, and above the metrics rather than
  // below them, because on a fresh install every metric is zero and a screen
  // of six zeroes is not an answer to "what do I do now". It is built once and
  // drawn into the section each render, exactly like `ask`.
  //
  // It stays visible while ANY step is outstanding, not only while the install
  // is brand new: somebody who imported a CV in January and never reviewed it
  // still has a first thing to do, and hiding the list the moment the product
  // had one fact about them would be the product deciding they were finished.
  const firstRun = createFirstRun({
    onGoTo: (page) => onGoTo && onGoTo(page),
    onSetup: (step) => openSetup(step),
    // Importing documents or answering the career question changes what this
    // page can say, so the page that listed the gaps re-reads them.
    onChanged: () => load(),
  });
  let token = 0;
  //: The last thing the server said, kept so a language change can repaint
  //: without asking again. Home is not driven by the store, so the `store.set
  //: ({})` that repaints every other surface on a locale switch reaches
  //: nothing here -- and the whole page stayed in English while the
  //: navigation above it moved.
  let payload = null;

  /**
   * Draw Home.
   *
   * `arrival` is true when the person navigated here. It starts a new visit
   * for the setup list, which is what decides that a step finished before
   * they arrived is no longer drawn as an open task.
   *
   * THE SETUP IS NOT RESTARTED BY COMING BACK. Leaving Home in the middle of
   * the guided setup -- or on its last card while jobs are being found -- and
   * returning shows the same card, with the run still drawn from the app's
   * one watcher. It used to be rebuilt from the start of Home, which made a
   * run that was still going look as if it had stopped.
   */
  async function load({ arrival = false } = {}) {
    if (arrival) firstRun.beginVisit();
    if (showingSetup && setupAt === null && !setup.finished()) {
      if (onSetupShown) onSetupShown(true);
      return;
    }
    const mine = ++token;
    setup.stop();
    replace(root, [el('div', { className: 'sk sk--block' })]);
    try {
      payload = await api.getHome();
      if (mine !== token) return;
      // The first-run state BEFORE the first paint, so the list is never drawn
      // empty and then filled in -- a step row appearing a moment later reads
      // as something having just happened.
      await firstRun.load();
      if (mine !== token) return;
      // A fresh install -- nothing confirmed and no search described -- opens
      // on the guided setup, unless the person chose to do it later. Asked for
      // explicitly, it opens at the card that was asked for.
      // LEFT OPEN, NOT LEFT: a reload, a closed tab or a restart in the
      // middle of the setup comes back to the card that was open. "Do this
      // later" is the way out, and it forgets the position.
      const resume = !autoOpened && !setupPostponed() ? setupResumeStep() : null;
      if (setupAt !== null || resume || (!autoOpened && firstRun.isFresh() && !setupPostponed())) {
        autoOpened = true;
        const at = setupAt ?? resume;
        setupAt = null;
        showingSetup = true;
        if (onSetupShown) onSetupShown(true);
        replace(root, [setup.root]);
        await setup.open(at);
        return;
      }
      showingSetup = false;
      if (onSetupShown) onSetupShown(false);
      replace(root, render(payload));
    } catch (error) {
      if (mine !== token) return;
      replace(root, [
        el('p', { className: 'state__msg', text: error.userMessage || error.message }),
        button(t('action.retry'), () => load(), { className: 'btn' }),
      ]);
    }
  }

  /** Open the guided setup, at one card or from the start. */
  function openSetup(step = 'welcome') {
    setupAt = step;
    load();
  }

  function render(payload) {
    // NOTHING SCORED YET. Four zero counters and two lists reading "Nothing
    // here today" answer no question a new person has; one card that says
    // what is missing and offers the way to fix it does.
    const noJobs = firstRun.scoredCount() === 0;
    return [
      heading(payload, noJobs),
      collectionCard(),
      startHere(),
      // With nothing scored the start list is always open, and its last step
      // is "Find jobs now"; a row of zero counters would say nothing more.
      noJobs ? null : metrics(payload),
      // The same gaps the start list names, said again, only once that list
      // has nothing left in it.
      firstRun.outstanding() ? null : completeProfile(payload),
      ...(noJobs ? [] : payload.sections.map(section)),
    ].filter(Boolean);
  }

  /**
   * The first-run list, folded away once there is nothing left in it.
   *
   * A `<details>` rather than a banner with a dismiss button: dismissing is a
   * fourth piece of state to store, and the honest version of "I have read
   * this" is simply collapsing it. It starts OPEN while something is
   * outstanding and CLOSED when everything is recorded, so a finished setup is
   * one line rather than a wall, and is still there to be reopened.
   */
  function startHere() {
    // Redrawn on every render, not only on load. `relabel()` re-renders this
    // page to change its language and passes the SAME node back in; without
    // this the six steps kept the words they were first drawn with, and a
    // Portuguese page carried an English checklist.
    firstRun.redraw();
    // SETUP THAT WAS ALREADY FINISHED IS NOT A TASK. Nothing is drawn: the
    // answers are changed from Settings ("Change my answers"), and Home is
    // for the search itself.
    if (!firstRun.visible()) return null;
    const left = firstRun.remaining();
    const title = firstRun.showingAll()
      ? t('firstrun.title')
      : (left ? t('firstrun.finishTitle', { n: left }) : t('firstrun.finished'));
    return el('details', {
      className: 'home__sec home__sec--start',
      props: { open: left > 0 },
    }, [
      el('summary', { className: 'home__sechead' }, [
        el('span', { text: title }),
      ]),
      firstRun.root,
    ]);
  }

  // -------------------------------------------------------------------
  // the collection, drawn from the app's one watcher
  // -------------------------------------------------------------------

  const collectionHost = el('section', {
    className: 'home__sec home__sec--collect',
    attrs: { role: 'status', 'aria-live': 'polite', 'aria-labelledby': 'home-collect-head' },
  });
  const collectionView = collection ? createProgressView(collection, { stopId: 'home-stop' }) : null;
  let collectionShape = null;

  /** The run, if there is one worth mentioning. Updated in place, not rebuilt. */
  function paintCollection(snapshot, reason) {
    if (!collection) return;
    const ended = outcomeText(snapshot);
    collectionHost.hidden = !snapshot.active && !ended && !snapshot.error;
    if (collectionHost.hidden) {
      collectionShape = null;
      return;
    }
    if (snapshot.active) {
      collectionView.update(snapshot);
      if (collectionShape !== 'active') {
        collectionShape = 'active';
        replace(collectionHost, [
          el('h3', { className: 'home__sechead', attrs: { id: 'home-collect-head' }, text: t('setup.ready.finding') }),
          collectionView.root,
          el('p', { className: 'home__lede', text: t('setup.ready.keepUsing') }),
        ]);
      }
      return;
    }
    if (reason === 'tick' && collectionShape === 'ended') return;
    collectionShape = 'ended';
    replace(collectionHost, [
      el('h3', { className: 'home__sechead', attrs: { id: 'home-collect-head' }, text: t('collect.lastRun') }),
      el('p', { className: 'home__lede', text: snapshot.error || ended }),
      el('div', { className: 'home__gapactions' }, [
        snapshot.phase === 'failed' || snapshot.error || !onGoTo
          ? null
          : button(t('setup.ready.see'), () => onGoTo('jobs'), {
            className: 'btn btn--primary', attrs: { id: 'home-see-jobs' },
          }),
        button(t('collect.dismiss'), () => collection.dismiss(), { className: 'btn btn--quiet' }),
      ].filter(Boolean)),
    ]);
  }

  if (collection) collection.subscribe(paintCollection);

  function collectionCard() {
    if (!collection) return null;
    // Redrawn for the language in force; the watcher's state is unchanged.
    collectionShape = null;
    paintCollection(collection.state(), 'update');
    return collectionHost;
  }

  function heading(payload, noJobs) {
    return el('header', { className: 'home__head' }, [
      el('h2', { className: 'home__title', text: t('home.title') }),
      // "Since you last marked the list read" means nothing before there is a
      // list, so it waits for the first scored job.
      noJobs
        ? null
        : el('p', {
          className: 'home__sub',
          text: payload.last_reviewed_at
            ? t('home.since', { when: payload.last_reviewed_at.slice(0, 10) })
            : t('home.neverReviewed'),
        }),
    ].filter(Boolean));
  }

  /**
   * The summary cards.
   *
   * Each is a button that narrows the Jobs list to exactly the population it
   * counted, so a number can be checked rather than believed. A metric with
   * no status filter behind it -- "new", "progressed" -- is not a button,
   * because there is no list that would reproduce it and a control that
   * navigated somewhere approximate would be worse than none.
   */
  function metrics(payload) {
    const byKey = new Map(payload.metrics.map((metric) => [metric.key, metric]));
    const cards = CARDS
      .map((key) => byKey.get(key))
      .filter((metric) => metric && !(HIDE_WHEN_EMPTY.has(metric.key) && !metric.value))
      .map((metric) => {
        const inner = [
          el('span', { className: 'metric__value num', text: String(metric.value) }),
          el('span', { className: 'metric__label', text: t(`home.metric.${metric.key}`) }),
          // STOCK or EVENT, said in words rather than implied by position.
          el('span', {
            className: 'metric__kind',
            text: metric.kind === 'event' ? t('home.kind.event') : t('home.kind.now'),
          }),
        ];
        if (!metric.statuses.length || !onGoTo) {
          return el('li', { className: `metric metric--${metric.key}` }, inner);
        }
        return el('li', { className: `metric metric--${metric.key}` }, [
          button('', () => onGoTo('jobs', { status: metric.statuses }), {
            className: 'metric__open',
            ariaLabel: t('home.metricOpen', { label: t(`home.metric.${metric.key}`) }),
          }, ),
          ...inner,
        ]);
      });
    return el('ul', { className: 'home__metrics' }, cards);
  }

  /**
   * What the profile is missing, as named facts.
   *
   * Section 20: no "72% complete". A percentage over a profile invents a
   * denominator -- how many facts IS a whole person -- and then reports
   * somebody's career as a progress bar. Each line below names one specific
   * thing and what stops working without it.
   */
  function completeProfile(payload) {
    const gaps = payload.profile_gaps || [];
    if (!gaps.length) return null;
    return el('section', { className: 'home__sec home__sec--complete' }, [
      el('h3', { className: 'home__sechead', text: t('home.complete') }),
      el('p', { className: 'home__lede', text: t('home.completeLede') }),
      el('ul', { className: 'home__gaps' }, gaps.map((gap) => el('li', {
        className: 'home__gap',
        text: t(`home.gap.${gap}`),
      }))),
      onGoTo
        ? el('div', { className: 'home__gapactions' }, [
          // The first thing offered, because it is the one that does not
          // require already knowing which screen holds which answer.
          button(t('ask.start'), () => openSetup(firstSetupCard(gaps)), {
            className: 'btn btn--primary',
          }),
          button(t('home.openProfile'), () => onGoTo('profile'), { className: 'btn' }),
          button(t('home.openEvidence'), () => onGoTo('evidence'), { className: 'btn' }),
        ])
        : null,
    ].filter(Boolean));
  }

  function section(data) {
    return el('section', { className: `home__sec home__sec--${data.key}` }, [
      el('h3', { className: 'home__sechead' }, [
        el('span', { text: words(data, 'title') }),
        el('span', { className: 'home__count num', text: String(data.count) }),
      ]),
      data.items.length
        ? el('ul', { className: 'home__rows' }, data.items.map(row))
        : el('p', { className: 'home__empty', text: t('home.nothing') }),
    ]);
  }

  /** A section heading in the reader's language, falling back to the server's. */
  function words(data, part) {
    const key = `daily.section.${data.key}.${part}`;
    const translated = t(key, { n: 7 });
    return translated === key ? data[part] : translated;
  }

  function row(job) {
    return el('li', { className: 'home__row' }, [
      button('', () => onOpenJob && onOpenJob(job.job_id), {
        className: 'home__open',
        ariaLabel: job.title,
      }),
      el('span', {
        className: 'home__score num',
        text: job.match_score === null || job.match_score === undefined
          ? t('daily.unscored')
          : `${job.match_score}%`,
      }),
      el('span', { className: 'home__what' }, [
        el('span', { className: 'home__jobtitle', text: job.title }),
        el('span', { className: 'home__company', text: job.company_name }),
      ]),
    ]);
  }

  /** Redraw in the reader's language, from what the server already said. */
  function relabel() {
    if (showingSetup) setup.relabel();
    else if (payload) replace(root, render(payload));
  }

  /** Whether Home is itself showing the run, so the page-wide bar need not. */
  function showsCollection() {
    if (!root.isConnected) return false;
    return !showingSetup || setup.atReady();
  }

  /** Whether Home is showing the guided setup rather than the dashboard. */
  function inSetup() {
    return showingSetup;
  }

  /** Called when the person navigates to another page. */
  function leftForAnotherPage() {
    if (showingSetup) setup.forgetPosition();
  }

  /** The page header's "Finish setup later": the flow's one global exit. */
  function leaveSetup() {
    setup.leave();
  }

  return { root, load, relabel, openSetup, showsCollection, inSetup, leftForAnotherPage, leaveSetup };
}
