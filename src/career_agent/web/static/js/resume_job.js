/**
 * resume_job.js -- a job version beside its ad, in the Editor.
 *
 * Read from `GET /api/resume/documents/<id>/job`, recomputed on the version
 * as it is now: how much of the ad has confirmed support (a count, never a
 * score), why a tailored version changed, what the ad asks that no confirmed
 * experience shows ("I couldn't find this in your confirmed experience"),
 * and suggestions to make it better.
 *
 * A suggestion with an exact change (add a confirmed line, show a hidden
 * one, add a confirmed skill) has Apply, which the Editor makes as an
 * ordinary, undoable edit. A gap has no Apply: it can only be answered in
 * Proof of my work. Dismiss sets a suggestion aside for this version only.
 * No requirement id, claim key or internal word is shown.
 */

import { dismissResumeSuggestion, getResumeJob } from './api.js';
import { button, el } from './dom.js';
import { t, tCount } from './i18n.js';

/** Coverage as a sign and a word, never a colour alone. */
const MARK = { COVERED: '✓', PARTLY: '◐', SAID: '◌', NOT_FOUND: '○', ELIGIBILITY: 'ℹ' };
const GROUPS = [
  ['emphasized', ['REORDER_BULLETS', 'SHOW_BULLET']],
  ['reworded', ['REWRITE_HEADLINE', 'REWRITE_SUMMARY', 'REWRITE_BULLET']],
  ['added', ['ADD_BULLET', 'ADD_SKILL']],
  ['hidden', ['HIDE_BULLET']],
];
const small = (label, onClick, extra = {}) => button(label, onClick, { className: 'btn btn--small', ...extra });

export function createJobPanel({ documentId, preferred, onApply, onFocus, onAddEvidence, onPrefer }) {
  const summary = el('summary');
  // Only the count is announced; the panel itself is redrawn quietly.
  const status = el('p', { className: 'rvj__status', attrs: { role: 'status' } });
  const body = el('div', { className: 'rvj' });
  const root = el('details', { className: 'rve__panel rvj__panel', props: { open: true } }, [
    summary, status, body,
  ]);
  let last = null;

  function source(where) {
    return where ? t('rv.job.source', { title: where.title, employer: where.employer }) : t('rv.job.sourceSkills');
  }

  function why(change) {
    return el('li', { className: 'rvj__change' }, [
      change.text ? el('p', { className: 'rvj__text', text: change.text }) : null,
      change.asks && change.asks.length
        ? el('p', { className: 'rve__note', text: t('rv.job.why', { ask: change.asks[0] }) }) : null,
      change.op === 'ADD_BULLET' || change.op === 'ADD_SKILL'
        ? el('p', { className: 'rve__note', text: source(change.where) }) : null,
      change.op === 'REORDER_BULLETS'
        ? el('p', { className: 'rve__note', text: t('rv.job.reordered', { title: (change.where || {}).title || '' }) })
        : null,
    ]);
  }

  function suggestion(s) {
    const actions = [];
    if (s.action) {
      actions.push(small(t('rv.job.apply'), () => onApply(s.key), {
        className: 'btn btn--small btn--primary',
        ariaLabel: `${t('rv.job.apply')}: ${s.action.text || s.action.label || s.ask}`,
      }));
    } else if (s.kind === 'NO_EVIDENCE') {
      actions.push(small(t('rv.job.addEvidence'), () => onAddEvidence(s.ask), {
        ariaLabel: `${t('rv.job.addEvidence')}: ${s.ask}`,
      }));
    } else if (s.kind === 'LONG_LINE') {
      actions.push(small(t('rv.job.edit'), () => onFocus(s.line_id)));
    }
    actions.push(small(t('rv.job.dismiss'), async (event) => {
      event.currentTarget.disabled = true;
      await dismissResumeSuggestion(documentId, s.key).catch(() => null);
      await load();
      summary.focus();
    }, { ariaLabel: `${t('rv.job.dismiss')}: ${s.ask || s.text}` }));
    let words = '';
    if (s.kind === 'UNSHOWN_EVIDENCE') {
      const what = s.action.type === 'add_skill' ? s.action.label : s.action.text;
      const key = s.action.type === 'show' ? 'rv.job.suggestShow' : 'rv.job.suggestAdd';
      words = t(key, { what: what || '', ask: s.ask });
    } else if (s.kind === 'NO_EVIDENCE') {
      words = t('rv.job.suggestGap', { ask: s.ask });
    } else {
      words = t('rv.job.suggestLong', { n: s.words });
    }
    return el('li', { className: 'rvj__suggestion', dataset: { kind: s.kind } }, [
      el('p', { text: words }),
      el('div', { className: 'rvl__rename' }, actions),
    ]);
  }

  function draw(view) {
    last = view;
    summary.textContent = t('rv.job.panel');
    if (!view || !view.job) {
      body.replaceChildren();
      return;
    }
    const gaps = view.coverage.filter((c) => c.coverage === 'NOT_FOUND');
    const counted = tCount('rv.job.supported', { n: view.supported, of: view.total });
    if (status.textContent !== counted) status.textContent = counted;
    const eligibility = view.coverage.filter((c) => c.coverage === 'ELIGIBILITY');
    const judged = view.coverage.filter((c) => c.coverage !== 'ELIGIBILITY');
    const parts = [
      el('p', { className: 'rvj__job', text: [view.job.title, view.job.company].filter(Boolean).join(' · ') }),
      view.tailored
        ? el('p', { className: 'rve__note', text: t(view.ai_assisted ? 'rv.job.aiAssisted' : 'rv.job.noAi') })
        : null,
      !preferred ? small(t('rv.job.prefer'), async (event) => {
        event.currentTarget.disabled = true;
        await onPrefer();
        event.currentTarget.replaceWith(el('p', { className: 'rve__note', text: t('rv.lib.preferredSaid') }));
      }) : null,
      el('h3', { className: 'rvl__h3', text: t('rv.job.coverage') }),
      el('ul', { className: 'rvj__list' }, judged.map((c) => el('li', {
        className: 'rvj__ask', dataset: { coverage: c.coverage },
      }, [
        el('span', { className: 'rve__mark', attrs: { 'aria-hidden': 'true' }, text: MARK[c.coverage] }),
        el('span', { text: `${t(`rv.job.state.${c.coverage}`)}: ${c.ask}` }),
      ]))),
      eligibility.length
        ? el('p', { className: 'rve__note', text: tCount('rv.job.eligibility', { n: eligibility.length }) }) : null,
    ];
    if (view.changes.length) {
      parts.push(el('h3', { className: 'rvl__h3', text: t('rv.job.changed') }));
      for (const [key, ops] of GROUPS) {
        const changes = view.changes.filter((c) => ops.includes(c.op));
        if (!changes.length) continue;
        parts.push(el('h4', { className: 'rvj__group', text: t(`rv.job.group.${key}`) }),
          el('ul', { className: 'rvj__list' }, changes.map(why)));
      }
    }
    if (gaps.length) {
      parts.push(
        el('h3', { className: 'rvl__h3', text: t('rv.job.before') }),
        el('p', { text: t('rv.job.beforeLede') }),
        el('ul', { className: 'rvj__list' }, gaps.map((g) => el('li', { text: g.ask }))),
        el('p', { className: 'rve__note', text: t('rv.job.notFound') }),
      );
    }
    parts.push(el('h3', { className: 'rvl__h3', text: t('rv.job.better') }));
    parts.push(view.suggestions.length
      ? el('ul', { className: 'rvj__list' }, view.suggestions.map(suggestion))
      : el('p', { className: 'rve__note', text: t('rv.job.nothing') }));
    body.replaceChildren(...parts.filter(Boolean));
  }

  async function load() {
    try {
      draw(await getResumeJob(documentId));
    } catch (error) {
      const said = error.userMessage || t('rv.failed');
      body.replaceChildren(el('p', { className: 'rve__notice rve__notice--bad', text: said }));
    }
  }

  summary.textContent = t('rv.job.panel');
  return { root, load, relabel: () => draw(last) };
}
