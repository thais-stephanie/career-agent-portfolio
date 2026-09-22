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
import { createOnboarding } from './onboarding.js';
import { createFirstRun } from './firstrun.js';
import { t } from './i18n.js';
import * as api from './api.js';

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

export function createHome({ onOpenJob = null, onGoTo = null } = {}) {
  const root = el('div', { className: 'home' });
  // The questions, asked HERE rather than on a screen of their own.
  // Every one of them closes a gap this page has just named, and a
  // wizard somewhere else would be a second place to be told the same
  // thing.
  const ask = createOnboarding({
    onGoTo: (page) => onGoTo && onGoTo(page),
    // A saved answer changes what is missing, so the page that listed
    // the gaps re-reads them rather than keeping a stale list.
    onSaved: () => load(),
  });
  // Hidden until it is asked for. Home re-renders the section around it
  // on every load, so this is set once and the flow's own `close` puts
  // it back.
  ask.root.hidden = true;

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

  async function load() {
    const mine = ++token;
    replace(root, [el('div', { className: 'sk sk--block' })]);
    try {
      payload = await api.getHome();
      if (mine !== token) return;
      // The first-run state BEFORE the first paint, so the list is never drawn
      // empty and then filled in -- a step row appearing a moment later reads
      // as something having just happened.
      await firstRun.load();
      if (mine !== token) return;
      replace(root, render(payload));
    } catch (error) {
      if (mine !== token) return;
      replace(root, [
        el('p', { className: 'state__msg', text: error.userMessage || error.message }),
        button(t('action.retry'), () => load(), { className: 'btn' }),
      ]);
    }
  }

  function render(payload) {
    return [
      heading(payload),
      startHere(),
      metrics(payload),
      completeProfile(payload),
      ...payload.sections.map(section),
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
    const left = firstRun.outstanding();
    return el('details', {
      className: 'home__sec home__sec--start',
      props: { open: left },
    }, [
      el('summary', { className: 'home__sechead' }, [
        el('span', { text: t('firstrun.title') }),
      ]),
      firstRun.root,
    ]);
  }

  function heading(payload) {
    return el('header', { className: 'home__head' }, [
      el('h2', { className: 'home__title', text: t('home.title') }),
      el('p', {
        className: 'home__sub',
        text: payload.last_reviewed_at
          ? t('home.since', { when: payload.last_reviewed_at.slice(0, 10) })
          : t('home.neverReviewed'),
      }),
    ]);
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
          button(t('ask.start'), () => {
            ask.root.hidden = false;
            ask.open(gaps);
          }, { className: 'btn btn--primary' }),
          button(t('home.openProfile'), () => onGoTo('profile'), { className: 'btn' }),
          button(t('home.openEvidence'), () => onGoTo('evidence'), { className: 'btn' }),
        ])
        : null,
      ask.root,
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
    if (payload) replace(root, render(payload));
  }

  return { root, load, relabel };
}
