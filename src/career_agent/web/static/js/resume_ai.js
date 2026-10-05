/**
 * resume_ai.js -- Tailor with AI: say what is sent, ask once, review each change.
 *
 * Nothing here calls a provider on its own. The person chooses Tailor with
 * AI, reads which provider and model will be used and what is sent, and
 * presses Send: ONE request. The server runs the deterministic stages, asks
 * the provider once and checks every proposal; only what it verified comes
 * back, each with Before, After, Why, its source and the job's ask. Accept,
 * Edit (the server checks the new wording too) or Reject; then Create version.
 * No version exists until then, and Cancel or Discard leaves none.
 */

import {
  cancelResumeDraft, decideResumeDraft, finalizeResumeDraft, getResumeAi, startResumeDraft,
} from './api.js';
import { button, el } from './dom.js';
import { t, tCount } from './i18n.js';
import { ulid } from './resume_editor.js';

/** Decisions said with a sign and a word, never a colour alone. */
const DECIDED = { ACCEPTED: '✓', EDITED: '✎', REJECTED: '✕' };
const small = (label, onClick, extra = {}) => button(label, onClick, { className: 'btn btn--small', ...extra });

export function createAiDraft({ host, show, open, onSettings, onWithoutAi, onBack }) {
  let jobId = null;

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

  const withoutAi = () => small(t('rv.ai.withoutAi'), () => onWithoutAi(jobId), { className: 'btn btn--small' });

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
    screen(t('rv.ai.title'), [
      el('p', { className: 'rva__provider', text: t('rv.ai.uses', { provider: ai.name, model: ai.model || '' }) }),
      el('p', { text: t(`rv.ai.billing.${ai.billing}`) }),
      el('p', { text: t('rv.ai.sends') }),
      el('p', { className: 'rve__note', text: t('rv.ai.neverSends') }),
      el('p', { className: 'rve__note', text: t('rv.ai.oneCall') }),
      el('div', { className: 'rvl__rename' }, [
        small(t('rv.ai.send'), () => void draft(), {
          className: 'btn btn--small btn--primary', attrs: { id: 'rva-send' },
        }),
        withoutAi(),
        small(t('rv.back'), onBack),
      ]),
    ]);
  }

  /** Step 2: the one request, with true steps and a Cancel that stops waiting. */
  async function draft() {
    // Named here, so a request still in flight can be cancelled by name.
    const id = ulid();
    const controller = new AbortController();
    const steps = ['read', 'find', 'draft', 'check'].map((key) => el('li', {
      className: 'rvt__step', text: t(`rv.ai.step.${key}`),
    }));
    const state = el('p', { className: 'rvt__state', attrs: { role: 'status', 'aria-live': 'polite' } });
    state.textContent = t('rv.ai.working');
    let cancelled = false;
    const box = screen(t('rv.ai.drafting'), [
      el('ol', { className: 'rvt__steps' }, steps),
      state,
      small(t('rv.ai.cancel'), async () => {
        cancelled = true;
        controller.abort();
        await cancelResumeDraft(id).catch(() => null);
        ended('cancelled');
      }, { attrs: { id: 'rva-cancel' } }),
    ]);
    try {
      const answer = await startResumeDraft(jobId, id, controller.signal);
      if (!cancelled) review(answer);
    } catch (error) {
      if (cancelled) return;
      const code = (error.detail && error.detail.code) || '';
      ended(code.replace(/^ai_/, '') || 'failed', box);
    }
  }

  /** Any end that made no version: said, with the ways on. */
  function ended(code) {
    const known = ['cancelled', 'stale', 'auth', 'limit', 'unreachable', 'invalid_output', 'unavailable',
      'no_master', 'discarded'];
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

  /** Step 3: each verified change, decided one by one. */
  function review(answer) {
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

  function card(answer, c) {
    const actions = el('div', { className: 'rvl__rename' });
    const said = el('p', { className: 'rva__said', attrs: { 'aria-live': 'polite' } });
    const what = t(`rv.ai.op.${c.op}`);
    const decide = async (decision, text) => {
      for (const b of actions.querySelectorAll('button')) b.disabled = true;
      try {
        review(await decideResumeDraft(answer.id, c.id, text === undefined ? { decision } : { decision, text }));
        // Focus stays on the decision just made, now said in words.
        const again = Array.from(host.querySelectorAll('.rva__card')).find((n) => n.dataset.change === c.id);
        if (again) again.querySelector('.rva__decided').focus();
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
        small(t('rv.ai.cancelEdit'), () => review(answer)),
      );
      box.focus();
    }
    return el('li', { className: 'rva__card', dataset: { change: c.id, decision: c.decision } }, [
      el('h3', { className: 'rva__what', text: c.role ? `${what} · ${c.role.title}` : what }),
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
      c.decision === 'PENDING' ? actions : el('p', {
        className: 'rva__decided', attrs: { tabindex: '-1' },
        text: `${DECIDED[c.decision]} ${t(`rv.ai.decision.${c.decision}`)}`,
      }),
      said,
    ]);
  }

  return { start: disclose };
}
