/**
 * daily.js -- what is worth looking at today.
 *
 * The web half of `career-agent daily`, and the sections come from the same
 * module the terminal reads (`career_agent/digest.py`). Two copies of a list of
 * sections is how a terminal and an interface start disagreeing about what
 * "new" means, and the disagreement would be invisible because each would be
 * internally consistent.
 *
 * AN OVERLAY, NOT A FOURTH VIEW. Cards, Table and Board are three renderings of
 * ONE query, and the filter rail narrows all three. The digest is several
 * different queries the server composes, and making it a fourth view would put
 * a filter panel beside a list the filters do not reach.
 *
 * NOTHING HERE RANKS ANYTHING. Every section is a filter over the score the
 * cards already show, and the rows arrive in that order. Recency decides what
 * is IN a section; the score decides what is at the top of it. The first
 * version of the terminal digest sorted by date, and the first thing anybody
 * read each morning was a posting scoring zero.
 *
 * A DATE SAYS WHICH KIND OF DATE IT IS. `posted` is what a board published,
 * `first seen` is when this machine noticed, and neither is ever rendered as
 * the other. "Since you last looked" is built on the second, because a board
 * that publishes no dates would otherwise have every one of its postings
 * permanently invisible to the question.
 */

import { el, button, replace, focusables } from './dom.js';
import { t, tState } from './i18n.js';
import * as api from './api.js';

export function createDaily({ onOpenJob = null } = {}) {
  let invoker = null;
  let token = 0;

  const bodyHost = el('div', { className: 'drawer__body daily' });
  const titleNode = el('h2', {
    className: 'drawer__title',
    attrs: { id: 'daily-title' },
    text: '',
  });
  const closeButton = button(t('daily.close'), () => close(), { className: 'btn btn--close' });

  const panel = el('div', {
    className: 'drawer__panel drawer__panel--wide',
    attrs: {
      role: 'dialog',
      'aria-modal': 'true',
      'aria-labelledby': 'daily-title',
      tabindex: '-1',
    },
  }, [el('div', { className: 'drawer__head' }, [titleNode, closeButton]), bodyHost]);

  const scrim = el('div', { className: 'drawer__scrim', on: { click: () => close() } });
  const root = el('div', { className: 'drawer', attrs: { hidden: true } }, [scrim, panel]);

  root.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') {
      event.stopPropagation();
      close();
      return;
    }
    if (event.key !== 'Tab') return;
    const nodes = focusables(panel);
    if (!nodes.length) return;
    const first = nodes[0];
    const last = nodes[nodes.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  });

  async function open(invokedBy) {
    invoker = invokedBy || document.activeElement;
    root.hidden = false;
    document.body.classList.add('has-drawer');
    titleNode.textContent = t('daily.title');
    closeButton.textContent = t('daily.close');
    panel.focus();
    await refresh();
    const first = focusables(panel)[0];
    if (first) first.focus();
  }

  function close() {
    if (root.hidden) return;
    root.hidden = true;
    document.body.classList.remove('has-drawer');
    token += 1;
    replace(bodyHost, []);
    if (invoker && document.contains(invoker)) invoker.focus();
    invoker = null;
  }

  async function refresh() {
    const mine = ++token;
    replace(bodyHost, [el('div', { className: 'sk sk--block' })]);
    try {
      const payload = await api.getDaily();
      if (mine !== token) return;
      replace(bodyHost, render(payload));
    } catch (error) {
      if (mine !== token) return;
      replace(bodyHost, [
        el('p', { className: 'state__msg', text: error.userMessage || error.message }),
        button(t('daily.retry'), () => refresh(), { className: 'btn' }),
      ]);
    }
  }

  function render(payload) {
    return [header(payload), ...payload.sections.map((s) => section(s, payload.days))];
  }

  /**
   * The one number at the top, and the checkpoint control.
   *
   * "Mark as read" writes a timestamp only when somebody presses it. A digest
   * that marked itself read on every load would make "since you last looked"
   * mean "since this page last rendered" -- which is a fact about a render,
   * not about a reader, and it would empty the section she opened it for.
   */
  function header(payload) {
    // The same answer the job list gives, rather than four empty sections. A
    // digest that silently reports nothing after a settings change is
    // indistinguishable from one that failed to look, and this is exactly the
    // case where the difference matters.
    const stale = Number(payload.job_count) > 0 && Number(payload.scored_count) === 0;
    if (stale) {
      return el('section', { className: 'd-sec daily__head' }, [
        el('p', { className: 'daily__total', text: t('daily.unscoredHead') }),
        el('p', {
          className: 'ev__note',
          text: t('daily.unscoredBody', { n: payload.job_count }),
        }),
      ]);
    }
    return el('section', { className: 'd-sec daily__head' }, [
      el('p', { className: 'daily__total' }, [
        el('span', { className: 'num', text: String(payload.total) }),
        el('span', { text: ` ${t('daily.worthLooking')}` }),
      ]),
      el('p', {
        className: 'ev__note',
        text: payload.last_reviewed_at
          ? t('daily.lastLooked', { when: payload.last_reviewed_at.slice(0, 16).replace('T', ' ') })
          : t('daily.neverLooked'),
      }),
      button(t('daily.markRead'), async () => {
        replace(bodyHost, [el('div', { className: 'sk sk--block' })]);
        replace(bodyHost, render(await api.markReviewed()));
      }, { className: 'btn' }),
    ]);
  }

  /**
   * A section's heading, in the reader's language.
   *
   * `digest.py` defines all five and that definition is what the terminal
   * prints and the tests hold to. The catalogue carries the same sentences
   * translated and is preferred; a section the catalogue has never heard of
   * still shows the server's own words rather than nothing.
   */
  function words(data, part, params) {
    const key = `daily.section.${data.key}.${part}`;
    const translated = t(key, params);
    return translated === key ? data[part] : translated;
  }

  function section(data, days) {
    return el('section', { className: `d-sec daily__sec daily__sec--${data.key}` }, [
      el('h3', { className: 'd-sec__head' }, [
        el('span', { text: words(data, 'title', { n: days }) }),
        el('span', { className: 'daily__count num', text: String(data.count) }),
      ]),
      el('p', { className: 'd-sec__lede', text: words(data, 'lead', null) }),
      data.items.length
        ? el('ul', { className: 'daily__rows' }, data.items.map(row))
        // An empty section keeps its heading and says so. One that vanished
        // would be indistinguishable from one that failed to look.
        : el('p', { className: 'daily__empty', text: t('daily.nothing') }),
    ]);
  }

  function row(job) {
    const open = () => {
      if (!onOpenJob) return;
      close();
      onOpenJob(job.job_id);
    };
    return el('li', { className: 'daily__row' }, [
      el('button', {
        className: 'daily__open',
        attrs: { type: 'button' },
        on: { click: open },
      }, [
        el('span', {
          className: 'daily__score num',
          text: job.match_score === null || job.match_score === undefined
            ? t('daily.unscored')
            : `${job.match_score}%`,
        }),
        el('span', { className: 'daily__what' }, [
          el('span', { className: 'daily__title', text: job.title }),
          el('span', { className: 'daily__company', text: job.company_name }),
        ]),
        el('span', {
          className: 'daily__elig',
          text: tState('eligibility', job.eligibility_status, job.eligibility_status),
        }),
        el('span', { className: 'daily__when', text: whenOf(job) }),
      ]),
    ]);
  }

  /** The date, and which KIND of date it is. Never one word for both. */
  function whenOf(job) {
    if (job.posted_at) return t('daily.posted', { date: String(job.posted_at).slice(0, 10) });
    if (job.first_seen_at) {
      return t('daily.firstSeen', { date: String(job.first_seen_at).slice(0, 10) });
    }
    return t('daily.noDate');
  }

  return { root, open, close, refresh };
}
