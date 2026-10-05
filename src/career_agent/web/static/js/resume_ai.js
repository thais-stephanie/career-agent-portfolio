/**
 * resume_ai.js -- Tailor with AI: say what is sent, ask, review each change.
 *
 * Nothing here calls a provider on its own. The person chooses Tailor with
 * AI, reads which provider and model will be used, what is sent and how many
 * requests it makes (one; two with the optional independent review), and
 * presses Send. The server runs the deterministic stages, asks the provider
 * once and checks every proposal; only what it verified comes back. With the
 * independent review, ONE more request asks a second AI pass for its opinion
 * on those proposals: advisory, shown apart from the suggestion and from the
 * person's decision, and gone the moment the wording is edited. Accept, Edit
 * (the server checks the new wording too) or Reject; then Create version.
 * No version exists until then, and Cancel or Discard leaves none.
 */

import {
  cancelResumeDraft, cancelResumeReview, decideResumeDraft, finalizeResumeDraft, getResumeAi,
  reviewResumeDraft, startResumeDraft,
} from './api.js';
import { button, el } from './dom.js';
import { t, tCount } from './i18n.js';
import { ulid } from './resume_editor.js';

/** Decisions and verdicts said with a sign and a word, never a colour alone. */
const DECIDED = { ACCEPTED: '✓', EDITED: '✎', REJECTED: '✕' };
const VERDICT = { SUPPORTED: '✓', CHECK: '?', UNSUPPORTED: '!' };
const small = (label, onClick, extra = {}) => button(label, onClick, { className: 'btn btn--small', ...extra });

export function createAiDraft({ host, show, open, onSettings, onWithoutAi, onBack }) {
  let jobId = null;
  let withReview = false;

  function screen(heading, children) {
    const box = el('section', { className: 'rvt rva', attrs: { 'aria-labelledby': 'rva-h' } }, [
      el('h2', { className: 'rvw__h2', attrs: { id: 'rva-h', tabindex: '-1' }, text: heading }),
      ...children,
    ]);
    host.replaceChildren(box);
    show('tailor');
    box.querySelector('h2').focus();
    return box;
  }

  const withoutAi = () => small(t('rv.ai.withoutAi'), () => onWithoutAi(jobId));

  /** Step 1: what will happen, before anything is sent. */
  async function disclose(id) {
    jobId = id;
    let ai;
    try {
      ai = await getResumeAi();
    } catch (error) {
      screen(t('rv.ai.title'), [el('p', { attrs: { role: 'alert' }, text: error.userMessage || t('rv.failed') }),
        el('div', { className: 'rvl__rename' }, [withoutAi(), small(t('rv.back'), onBack)])]);
      return;
    }
    if (!ai.available) {
      screen(t('rv.ai.title'), [
        el('p', { text: t(ai.reason === 'DEMO' ? 'rv.ai.demo' : 'rv.ai.notSetUp') }),
        el('div', { className: 'rvl__rename' }, [
          ai.reason === 'DEMO' ? null : small(t('rv.ai.setUp'), onSettings, {
            className: 'btn btn--small btn--primary', attrs: { id: 'rva-settings' },
          }),
          withoutAi(),
          small(t('rv.back'), onBack),
        ].filter(Boolean)),
      ]);
      return;
    }
    const calls = el('p', { className: 'rve__note', attrs: { id: 'rva-calls', 'aria-live': 'polite' } });
    const sayCalls = () => { calls.textContent = tCount('rv.ai.calls', { n: withReview ? 2 : 1 }); };
    const option = el('input', { attrs: { type: 'checkbox', id: 'rva-review' } });
    option.checked = withReview;
    option.addEventListener('change', () => { withReview = option.checked; sayCalls(); });
    sayCalls();
    screen(t('rv.ai.title'), [
      el('p', { className: 'rva__provider', text: t('rv.ai.uses', { provider: ai.name, model: ai.model || '' }) }),
      el('p', { text: t(`rv.ai.billing.${ai.billing}`) }),
      el('p', { text: t('rv.ai.sends') }),
      el('p', { className: 'rve__note', text: t('rv.ai.neverSends') }),
      el('label', { className: 'rva__option', attrs: { for: 'rva-review' } }, [
        option, el('span', { text: ` ${t('rv.ai.reviewOption')}` }),
      ]),
      el('p', { className: 'rve__note', text: t('rv.ai.reviewOptionHelp') }),
      calls,
      el('div', { className: 'rvl__rename' }, [
        small(t('rv.ai.send'), () => void draft(), {
          className: 'btn btn--small btn--primary', attrs: { id: 'rva-send' },
        }),
        withoutAi(),
        small(t('rv.back'), onBack),
      ]),
    ]);
  }

  /** True steps and a Cancel that stops waiting, for one request. */
  function progress(heading, keys, onCancel) {
    const steps = keys.map((key) => el('li', { className: 'rvt__step', text: t(`rv.ai.step.${key}`) }));
    const state = el('p', { className: 'rvt__state', attrs: { role: 'status', 'aria-live': 'polite' } });
    state.textContent = t('rv.ai.working');
    screen(heading, [
      el('ol', { className: 'rvt__steps' }, steps),
      state,
      small(t('rv.ai.cancel'), onCancel, { attrs: { id: 'rva-cancel' } }),
    ]);
  }

  /** Step 2: the drafting request, then (if chosen) the review request. */
  async function draft() {
    // Named here, so a request still in flight can be cancelled by name.
    const id = ulid();
    const controller = new AbortController();
    let cancelled = false;
    progress(t('rv.ai.drafting'), ['read', 'find', 'draft', 'check'], async () => {
      cancelled = true;
      controller.abort();
      await cancelResumeDraft(id).catch(() => null);
      ended('cancelled');
    });
    let answer;
    try {
      answer = await startResumeDraft(jobId, id, controller.signal);
    } catch (error) {
      if (cancelled) return;
      const code = (error.detail && error.detail.code) || '';
      ended(code.replace(/^ai_/, '') || 'failed');
      return;
    }
    if (cancelled) return;
    // No call is spent reviewing nothing.
    if (withReview && answer.changes.length) await independentReview(answer);
    else showReview(answer);
  }

  /** The second request. Its failure or cancel keeps every suggestion. */
  async function independentReview(answer) {
    const controller = new AbortController();
    let cancelled = false;
    progress(t('rv.ai.reviewing'), ['review'], async () => {
      cancelled = true;
      controller.abort();
      await cancelResumeReview(answer.id).catch(() => null);
      showReview({ ...answer, ai_review: { status: 'CANCELLED' } });
    });
    try {
      const reviewed = await reviewResumeDraft(answer.id, controller.signal);
      if (!cancelled) showReview(reviewed);
    } catch (error) {
      if (cancelled) return;
      const code = (error.detail && error.detail.code) || '';
      if (code === 'ai_stale') ended('stale');
      else showReview({ ...answer, ai_review: { status: 'FAILED', ended: code.replace(/^ai_/, '') } });
    }
  }

  /** Any end that made no version: said, with the ways on. */
  function ended(code) {
    const known = ['cancelled', 'stale', 'auth', 'limit', 'unreachable', 'invalid_output', 'unavailable',
      'no_master', 'discarded', 'budget'];
    const key = known.includes(code) ? code : 'failed';
    const retry = ['auth', 'limit', 'unreachable', 'invalid_output', 'failed', 'stale'].includes(key);
    screen(t('rv.ai.title'), [
      el('p', { attrs: { role: 'alert' }, text: t(`rv.ai.ended.${key}`) }),
      el('p', { className: 'rve__note', text: t('rv.ai.nothingSaved') }),
      el('div', { className: 'rvl__rename' }, [
        retry ? small(t(key === 'stale' ? 'rv.ai.tryAgain' : 'rv.ai.retry'), () => void disclose(jobId), {
          className: 'btn btn--small btn--primary', attrs: { id: 'rva-retry' },
        }) : null,
        withoutAi(),
        small(t('rv.back'), onBack),
      ].filter(Boolean)),
    ]);
  }

  /** What the independent review said, above the cards: counts, never a score. */
  function reviewSummary(answer) {
    const ai = answer.ai_review;
    if (!ai) return null;
    if (ai.status === 'DONE') {
      const count = (v) => answer.changes.filter((c) => c.review && c.review.verdict === v).length;
      return el('p', { className: 'rva__summary', attrs: { id: 'rva-summary' }, text: t('rv.ai.reviewSummary', {
        s: count('SUPPORTED'), c: count('CHECK'), u: count('UNSUPPORTED'),
      }) });
    }
    // Over the budget, or tried enough: said, and no button that cannot work.
    const final = ['budget', 'attempts'].includes(ai.ended);
    const key = final ? `rv.ai.reviewNot.${ai.ended}`
      : ai.status === 'CANCELLED' ? 'rv.ai.reviewCancelled' : 'rv.ai.reviewFailed';
    return el('div', { className: 'rve__notice', attrs: { role: 'status', id: 'rva-review-failed' } }, [
      el('p', { text: t(key) }),
      final ? null : el('div', { className: 'rvl__rename' }, [
        // A review lost mid-request is let go first, so trying again can work.
        small(t('rv.ai.retryReview'), async () => {
          await cancelResumeReview(answer.id).catch(() => null);
          await independentReview(answer);
        }, { attrs: { id: 'rva-retry-review' } }),
      ]),
      el('p', { className: 'rve__note', text: t('rv.ai.continueWithout') }),
    ]);
  }

  /** Step 3: each verified change, decided one by one. */
  function showReview(answer) {
    if (answer.stale) {
      ended('stale');
      return;
    }
    const decided = answer.changes.filter((c) => c.decision !== 'PENDING').length;
    const count = el('p', { className: 'rva__count', attrs: { role: 'status' },
      text: tCount('rv.ai.reviewed', { n: decided, of: answer.changes.length }) });
    const cards = answer.changes.map((c) => card(answer, c));
    const finish = small(t('rv.ai.create'), async (event) => {
      event.currentTarget.disabled = true;
      try {
        const made = await finalizeResumeDraft(answer.id);
        await open(made.id, { answer: made });
      } catch (error) {
        const code = (error.detail && error.detail.code) || '';
        if (code === 'stale') ended('stale');
        else {
          finish.disabled = false;
          finish.after(el('p', { attrs: { role: 'alert' }, text: t('rv.ai.notCreated') }));
        }
      }
    }, { className: 'btn btn--small btn--primary', attrs: { id: 'rva-create' } });
    finish.disabled = decided < answer.changes.length;
    screen(t('rv.ai.review'), [
      el('p', { className: 'rvj__job', text: [answer.job.title, answer.job.company].filter(Boolean).join(' · ') }),
      el('p', { className: 'rve__note', text: t('rv.ai.reviewLede') }),
      reviewSummary(answer),
      count,
      answer.refused ? el('p', { className: 'rve__note', attrs: { id: 'rva-refused' },
        text: tCount('rv.ai.refused', { n: answer.refused }) }) : null,
      answer.changes.length
        ? el('ol', { className: 'rva__cards' }, cards)
        : el('p', { attrs: { id: 'rva-none' }, text: t('rv.ai.none') }),
      answer.gaps.length ? el('section', { className: 'rva__gaps' }, [
        el('h3', { className: 'rvl__h3', text: t('rv.ai.gaps') }),
        el('ul', { className: 'rvj__list' }, answer.gaps.map((g) => el('li', { text: g }))),
        el('p', { className: 'rve__note', text: t('rv.ai.gapsNote') }),
      ]) : null,
      el('div', { className: 'rvl__rename' }, [
        finish,
        small(t('rv.ai.discard'), async () => {
          await cancelResumeDraft(answer.id).catch(() => null);
          ended('discarded');
        }, { attrs: { id: 'rva-discard' } }),
      ]),
    ].filter(Boolean));
  }

  /** The reviewer's opinion on this wording: a section of its own, in words. */
  function opinion(c) {
    if (!c.review) return null;
    const { verdict, findings, reason } = c.review;
    const why = findings.map((code) => t(`rv.ai.finding.${code}`)).join(' · ');
    return el('div', { className: 'rva__opinion', dataset: { verdict } }, [
      el('p', {}, [
        el('strong', { text: `${t('rv.ai.reviewHead')}: ` }),
        `${VERDICT[verdict]} ${t(`rv.ai.verdict.${verdict}`)}`,
      ]),
      why ? el('p', { className: 'rve__note', text: why }) : null,
      reason ? el('p', { className: 'rve__note', text: reason }) : null,
    ]);
  }

  function card(answer, c) {
    const actions = el('div', { className: 'rvl__rename' });
    const said = el('p', { className: 'rva__said', attrs: { 'aria-live': 'polite' } });
    const what = t(`rv.ai.op.${c.op}`);
    const decide = async (decision, text) => {
      for (const b of actions.querySelectorAll('button')) b.disabled = true;
      try {
        showReview(await decideResumeDraft(answer.id, c.id, text === undefined ? { decision } : { decision, text }));
        // Focus stays on the decision just made, now said in words.
        const again = Array.from(host.querySelectorAll('.rva__card')).find((n) => n.dataset.change === c.id);
        if (again) (again.querySelector('.rva__decided') || again).focus();
      } catch (error) {
        for (const b of actions.querySelectorAll('button')) b.disabled = false;
        const code = error.detail && error.detail.code;
        said.textContent = t(code === 'refused' ? 'rv.ai.unverified' : 'rv.failed');
      }
    };
    if (c.decision === 'PENDING') {
      actions.replaceChildren(
        small(t('rv.ai.accept'), () => void decide('ACCEPTED'), {
          className: 'btn btn--small btn--primary', ariaLabel: `${t('rv.ai.accept')}: ${what}`,
        }),
        small(t('rv.ai.edit'), () => editing(), { ariaLabel: `${t('rv.ai.edit')}: ${what}` }),
        small(t('rv.ai.reject'), () => void decide('REJECTED'), { ariaLabel: `${t('rv.ai.reject')}: ${what}` }),
      );
    }
    function editing() {
      const box = el('textarea', {
        className: 'rve__textarea', attrs: { rows: '3', 'aria-label': t('rv.ai.editLabel') },
      });
      box.value = c.after;
      actions.replaceChildren(
        box,
        small(t('rv.ai.saveEdit'), () => void decide('EDITED', box.value), {
          className: 'btn btn--small btn--primary',
        }),
        small(t('rv.ai.cancelEdit'), () => showReview(answer)),
      );
      box.focus();
    }
    const flagged = c.review && c.review.verdict === 'UNSUPPORTED' && c.decision === 'PENDING';
    return el('li', {
      className: 'rva__card',
      attrs: { tabindex: '-1' },
      dataset: { change: c.id, decision: c.decision, review: c.review ? c.review.verdict : '' },
    }, [
      el('h3', { className: 'rva__what', text: c.role ? `${what} · ${c.role.title}` : what }),
      flagged ? el('p', { className: 'rva__flag', text: `! ${t('rv.ai.needsReview')}` }) : null,
      c.before
        ? el('p', { className: 'rva__before' }, [el('strong', { text: `${t('rv.ai.before')}: ` }), c.before])
        : null,
      el('p', { className: 'rva__after' }, [el('strong', { text: `${t('rv.ai.after')}: ` }), c.after]),
      c.why ? el('p', { className: 'rve__note' }, [el('strong', { text: `${t('rv.ai.why')}: ` }), c.why]) : null,
      c.sources.length ? el('p', { className: 'rve__note' }, [
        el('strong', { text: `${t('rv.ai.source')}: ` }), c.sources.join(' / '),
      ]) : null,
      c.asks.length ? el('p', { className: 'rve__note' }, [
        el('strong', { text: `${t('rv.ai.ask')}: ` }), c.asks.join(' / '),
      ]) : null,
      opinion(c),
      el('p', { className: 'rva__yours', text: t('rv.ai.yourDecision') }),
      c.decision === 'PENDING' ? actions : el('div', { className: 'rvl__rename' }, [
        el('p', {
          className: 'rva__decided', attrs: { tabindex: '-1' },
          text: `${DECIDED[c.decision]} ${t(`rv.ai.decision.${c.decision}`)}`,
        }),
        // A decision can be taken back while the review is open.
        small(t('rv.ai.change'), () => void decide('PENDING'), { ariaLabel: `${t('rv.ai.change')}: ${what}` }),
      ]),
      said,
    ]);
  }

  return { start: disclose };
}
