/**
 * deep.js -- Find Jobs -> Deep reading.
 *
 * An overlay, like Today: it is not a fourth rendering of the list. It holds
 * the whole deep-reading flow for this profile, in the order a person needs
 * it: which provider and how it is paid, whether the confirmed evidence may
 * be sent, a PREVIEW of exactly what would leave and which postings, then a
 * run that can be followed, cancelled and resumed, and the results.
 *
 * Nothing starts on opening, on a setting or on a preview. Only the Start
 * button sends anything, and only after the preview it confirms.
 *
 * Results are grouped by the recommendation the CODE derived from what the
 * model observed, and every row carries the three answers it came from: how
 * the work aligns, how far the history sent shows the requirements, and what
 * is left to confirm. They sit beside the local score; postings without a
 * reading stay in the main list in the local order and are counted here,
 * never ranked below the read ones.
 */

import { el, button, replace, focusables } from './dom.js';
import { t } from './i18n.js';
import * as api from './api.js';

const POLL_MS = 1500;
const LEVELS = [
  'STRONG', 'PREPARE', 'CHECK_FIRST', 'NOT_COMPARED', 'TO_CONFIRM', 'LOW', 'OTHER_WORK', 'STALE',
];
// Gaps first: what would stop an application reads before what supports it.
const STATUS_ORDER = ['not_shown', 'review', 'declared', 'partial', 'unknown', 'shown'];
const STOPS = new Set([
  'LIMIT_REACHED', 'SIGN_IN', 'CANCELLED', 'PROVIDER_STOPPED', 'NOTHING_NEW', 'NO_INTENT',
  'DEEP_NEEDS_SUBSCRIPTION', 'ERROR',
]);

export function createDeep({ onOpenJob = null } = {}) {
  let invoker = null;
  let token = 0;
  let poller = null;
  let includeUnresolved = false;
  let preview = null;
  let checked = false;
  let message = '';

  const bodyHost = el('div', { className: 'drawer__body deep' });
  const titleNode = el('h2', { className: 'drawer__title', attrs: { id: 'deep-title' }, text: '' });
  const closeButton = button(t('deep.close'), () => close(), { className: 'btn btn--close' });
  const panel = el('div', {
    className: 'drawer__panel drawer__panel--wide',
    attrs: { role: 'dialog', 'aria-modal': 'true', 'aria-labelledby': 'deep-title', tabindex: '-1' },
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
    const [first, last] = [nodes[0], nodes[nodes.length - 1]];
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
    titleNode.textContent = t('deep.title');
    closeButton.textContent = t('deep.close');
    panel.focus();
    await refresh();
  }

  function close() {
    if (root.hidden) return;
    root.hidden = true;
    document.body.classList.remove('has-drawer');
    token += 1;
    stopPolling();
    preview = null;
    checked = false;
    message = '';
    replace(bodyHost, []);
    if (invoker && document.contains(invoker)) invoker.focus();
    invoker = null;
  }

  function stopPolling() {
    if (poller) clearInterval(poller);
    poller = null;
  }

  async function refresh() {
    const mine = ++token;
    replace(bodyHost, [el('div', { className: 'sk sk--block' })]);
    try {
      const [status, results] = await Promise.all([api.getDeep(), api.getDeepReviews()]);
      if (mine !== token) return;
      replace(bodyHost, render(status, results.reviews || []));
      if (status.run && status.run.status === 'running') poll();
    } catch (error) {
      if (mine !== token) return;
      replace(bodyHost, [
        el('p', { className: 'state__msg', text: t('deep.error') }),
        button(t('deep.retry'), () => refresh(), { className: 'btn' }),
      ]);
    }
  }

  function poll() {
    if (poller) return;
    poller = setInterval(async () => {
      if (root.hidden) {
        stopPolling();
        return;
      }
      try {
        const status = await api.getDeepRun();
        const node = bodyHost.querySelector('.deep__progress');
        if (!status.run || status.run.status !== 'running') {
          stopPolling();
          await refresh();
          return;
        }
        if (node) {
          node.textContent = t('deep.running', {
            done: status.run.boards_done || 0, total: status.run.boards_total || 0,
          });
        }
      } catch {
        stopPolling();
      }
    }, POLL_MS);
  }

  function render(status, reviews) {
    const children = [el('p', { className: 'deep__intro', text: t('deep.intro') })];
    if (status.demo) return [...children, el('p', { text: t('deep.demo') })];
    children.push(providerBlock(status));
    if (status.provider) {
      const running = status.run && status.run.status === 'running';
      children.push(running ? runningBlock() : runBlock(status));
      if (status.last_run) children.push(lastRun(status.last_run));
    }
    if (message) children.push(el('p', { className: 'deep__message', text: message, attrs: { role: 'status' } }));
    children.push(resultsBlock(status, reviews));
    return children;
  }

  function providerBlock(status) {
    if (!status.provider) {
      return el('div', { className: 'deep__box' }, [
        el('p', { text: t(`deep.provider.${status.provider_reason || 'CHOOSE_PROVIDER'}`) }),
      ]);
    }
    const signedIn = status.auth === 'subscription';
    const consent = el('input', {
      attrs: { type: 'checkbox' },
      props: { checked: Boolean(status.share_evidence) },
      on: {
        change: async () => {
          consent.disabled = true;
          try {
            await api.setDeepConsent(consent.checked);
            message = t(consent.checked ? 'deep.consent.on' : 'deep.consent.off');
          } catch {
            message = t('deep.error');
          }
          preview = null;
          checked = false;
          await refresh();
        },
      },
    });
    return el('div', { className: 'deep__box' }, [
      el('p', { className: 'deep__access' }, [
        el('strong', { text: t(`deep.access.${status.provider}`) }),
        el('span', {
          className: `deep__auth deep__auth--${signedIn ? 'ok' : 'off'}`,
          text: t(signedIn ? 'deep.auth.subscription' : 'deep.auth.missing'),
        }),
      ]),
      el('p', { className: 'deep__note', text: t('deep.quota') }),
      el('label', { className: 'deep__consent' }, [
        consent,
        el('span', { text: t(`deep.consent.label.${status.provider}`) }),
      ]),
      el('p', { className: 'deep__note', text: t('deep.consent.scope') }),
    ]);
  }

  function runBlock(status) {
    const unresolved = el('input', {
      attrs: { type: 'checkbox' },
      props: { checked: includeUnresolved },
      on: {
        change: () => {
          includeUnresolved = unresolved.checked;
          preview = null;
          checked = false;
          void refresh();
        },
      },
    });
    const children = [
      el('h3', { className: 'deep__subhead', text: t('deep.previewHead') }),
      el('p', { className: 'deep__note', text: t('deep.previewLede', { cap: status.cap }) }),
      el('label', { className: 'deep__consent' }, [unresolved, el('span', { text: t('deep.includeUnresolved') })]),
      button(t('deep.previewButton'), async (event) => {
        event.currentTarget.disabled = true;
        try {
          preview = await api.previewDeep({ include_unresolved: includeUnresolved });
        } catch {
          message = t('deep.error');
        }
        checked = false;
        await refresh();
      }, { className: 'btn' }),
    ];
    if (preview) children.push(...previewRows(preview));
    return el('section', { className: 'deep__box' }, children);
  }

  function previewRows(plan) {
    if (!plan.provider) return [el('p', { text: t(`deep.provider.${plan.reason}`) })];
    if (!plan.postings.length) return [el('p', { text: t('deep.nothing') })];
    const strata = {};
    for (const row of plan.postings) strata[row.stratum] = (strata[row.stratum] || 0) + 1;
    const confirm = el('input', {
      attrs: { type: 'checkbox' },
      props: { checked },
      on: { change: () => { checked = confirm.checked; start.disabled = !checked; } },
    });
    const start = button(t('deep.start', { count: plan.postings.length }), async (event) => {
      event.currentTarget.disabled = true;
      try {
        await api.startDeep({ include_unresolved: includeUnresolved, limit: plan.postings.length });
        message = '';
      } catch {
        message = t('deep.error');
      }
      preview = null;
      checked = false;
      await refresh();
      // A short run can end between the two reads of `refresh`: follow it to
      // its end either way, and draw the results it stored.
      poll();
    }, { className: 'btn btn--primary' });
    start.disabled = !checked;
    const sends = [
      t('deep.sends.intent', { count: Object.values(plan.intent).flat().length }),
      t('deep.sends.levels', {
        levels: plan.levels.map((level) => t(`seniority.${level}`)).join(', ') || t('deep.none'),
      }),
      t('deep.sends.postings', { count: plan.postings.length }),
      plan.share_evidence
        ? t('deep.sends.evidence', {
          max: Math.max(0, ...plan.evidence_per_posting), confirmed: plan.confirmed_evidence,
        })
        : t('deep.sends.noEvidence'),
    ];
    return [
      el('p', {
        text: t('deep.sample', {
          count: plan.postings.length, population: plan.population, reviewed: plan.already_reviewed,
          short: plan.too_short || 0,
        }),
      }),
      el('p', {
        className: 'deep__note',
        text: ['top_local', 'lexical', 'control']
          .map((s) => t(`deep.stratum.${s}`) + `: ${strata[s] || 0}`).join(' · '),
      }),
      el('h4', { className: 'deep__minor', text: t('deep.sendsHead') }),
      el('ul', { className: 'deep__list' }, sends.map((line) => el('li', { text: line }))),
      plan.share_evidence && plan.evidence_example.length ? el('details', { className: 'deep__details' }, [
        el('summary', { text: t('deep.exampleHead') }),
        el('ul', { className: 'deep__list' }, plan.evidence_example.map((item) => el('li', {
          text: `${item.id} · ${t(`deep.claimType.${item.type}`)} · ${item.text}`,
        }))),
      ]) : null,
      el('details', { className: 'deep__details' }, [
        el('summary', { text: t('deep.postingsHead') }),
        el('ol', { className: 'deep__list' }, plan.postings.map((row) => el('li', {
          text: `${row.title} · ${row.company} · ${t(`deep.stratum.${row.stratum}`)} · ${row.local_score}`,
        }))),
      ]),
      el('p', { className: 'deep__note', text: t('deep.tokens', { tokens: plan.estimated_input_tokens }) }),
      el('p', { className: 'deep__note deep__warn', text: t('deep.extraUsage') }),
      el('label', { className: 'deep__consent' }, [confirm, el('span', { text: t('deep.confirm') })]),
      start,
    ].filter(Boolean);
  }

  function runningBlock() {
    return el('section', { className: 'deep__box' }, [
      el('p', {
        className: 'deep__progress', attrs: { role: 'status' }, text: t('deep.running', { done: 0, total: 0 }),
      }),
      button(t('deep.cancel'), async (event) => {
        event.currentTarget.disabled = true;
        await api.cancelDeep();
        await refresh();
      }, { className: 'btn btn--quiet' }),
    ]);
  }

  function lastRun(run) {
    const lines = [t('deep.last', {
      published: run.published || 0, failed: run.failed || 0, rejected: run.rejected || 0,
      candidates: run.candidates || 0,
    })];
    const code = String(run.stop_reason || '');
    if (STOPS.has(code)) lines.push(t(`deep.stop.${code}`));
    return el('div', { className: 'deep__last' }, lines.map((text) => el('p', { text })));
  }

  function resultsBlock(status, reviews) {
    const children = [
      el('h3', { className: 'deep__subhead', text: t('deep.resultsHead') }),
      el('p', { className: 'deep__note', text: t('deep.resultsLede') }),
      el('p', {
        className: 'deep__note',
        text: t('deep.coverage', {
          roles: status.main_list_roles || 0, readable: status.readable || 0, short: status.too_short || 0,
        }),
      }),
      el('p', { className: 'deep__note', text: t('deep.notReviewed', { count: status.not_reviewed || 0 }) }),
    ];
    if (!reviews.length) {
      children.push(el('p', { text: t('deep.noResults') }));
      return el('section', { className: 'deep__results' }, children);
    }
    for (const level of LEVELS) {
      const rows = reviews.filter((r) => r.level === level);
      if (!rows.length) continue;
      children.push(el('h4', {
        className: `deep__level deep__level--${level.toLowerCase()}`,
        text: `${t(`deep.level.${level}`)} (${rows.length})`,
      }));
      children.push(el('p', { className: 'deep__note', text: t(`deep.levelMeans.${level}`) }));
      children.push(el('ul', { className: 'deep__rows' }, rows.map(resultRow)));
    }
    return el('section', { className: 'deep__results' }, children);
  }

  function resultRow(row) {
    return el('li', { className: 'deep__row', dataset: { level: row.level } }, [
      el('button', {
        className: 'deep__open',
        attrs: { type: 'button' },
        // The job drawer replaces this panel: two overlays never stack.
        on: { click: () => { close(); if (onOpenJob) onOpenJob(row.job_id); } },
      }, [
        el('span', { className: 'deep__what' }, [
          el('span', { className: 'deep__jobtitle', text: row.title }),
          el('span', { className: 'deep__company', text: row.company }),
        ]),
        ...axisLines(el, row.assessment),
        el('span', {
          className: 'deep__compare',
          text: row.stale
            ? t('deep.levelThen', { level: t(`deep.level.${row.level_then}`) })
            : t(`deep.compare.${row.comparison}`, { score: row.local_score }),
        }),
        row.stale ? el('span', { className: 'deep__stale', text: staleText(row) }) : null,
      ].filter(Boolean)),
    ]);
  }

  return { root, open, close };
}

/**
 * The drawer's section for ONE posting. `review` is `job.deep_review`, or
 * null when the posting has not been read: that is said plainly and is not a
 * verdict on the posting.
 */
export function deepSection(review, section) {
  const make = el;
  if (!review) {
    return section(t('deep.drawer.head'), [make('p', { className: 'deep__note', text: t('deep.drawer.none') })]);
  }
  const r = review.review;
  const rows = [];
  const a = review.assessment;
  rows.push(make('p', { className: 'deep__levelline' }, [
    make('strong', { text: t(`deep.level.${review.level}`) }),
    make('span', {
      className: 'deep__compare',
      text: review.stale
        ? t('deep.levelThen', { level: t(`deep.level.${review.level_then}`) })
        : t(`deep.compare.${review.comparison}`, { score: review.local_score }),
    }),
  ]));
  if (review.stale) rows.push(make('p', { className: 'deep__stale', text: staleText(review) }));
  rows.push(make('div', { className: 'deep__axes' }, axisLines(make, a)));
  rows.push(make('p', { className: 'deep__minor', text: t('deep.drawer.primary') }));
  if (r.primary_activity.summary) {
    rows.push(make('p', {
      className: 'deep__inference', text: `${t('deep.inference')}: ${r.primary_activity.summary}`,
    }));
  }
  rows.push(quoteList(make, r.primary_activity.quotes));
  rows.push(make('p', {
    text: t(`deep.relation.${r.work_relation.value}`)
      + (r.work_relation.reason ? ` ${t('deep.inference')}: ${r.work_relation.reason}` : ''),
  }));
  rows.push(quoteList(make, r.work_relation.quotes));
  const reqs = a.requirements.map((req, index) => ({ ...req, index }));
  const technical = (req) => req.category === 'technical' || req.category === 'credential';
  const byStatus = (x, y) => (a.central_gaps.includes(y.index) - a.central_gaps.includes(x.index))
    || (STATUS_ORDER.indexOf(x.status) - STATUS_ORDER.indexOf(y.status)) || (x.index - y.index);
  const groups = [
    ['deep.drawer.mandatory', reqs.filter((q) => q.kind === 'mandatory' && technical(q)).sort(byStatus)],
    ['deep.drawer.desirable', reqs.filter((q) => q.kind !== 'mandatory' && technical(q)).sort(byStatus)],
  ];
  for (const [head, items] of groups) {
    if (!items.length) continue;
    rows.push(make('p', { className: 'deep__minor', text: t(head) }));
    if (!review.evidence_shared) rows.push(make('p', { className: 'deep__note', text: t('deep.drawer.notShared') }));
    rows.push(make('ul', { className: 'deep__reqs' }, items.map((req) => requirementItem(make, review, a, req))));
  }
  const confirm = [
    ...a.conditions.map((c) => [c.quote, t(`deep.topic.${c.topic}`)]),
    ...a.languages.map((quote) => [quote, t('deep.topic.language')]),
    ...a.questions.map((quote) => [quote, t('deep.question')]),
  ];
  if (confirm.length) {
    rows.push(make('p', { className: 'deep__minor', text: t('deep.drawer.toConfirm') }));
    rows.push(make('ul', { className: 'deep__reqs' }, confirm.map(([quote, tag]) => make('li', {}, [
      make('q', { className: 'deep__quote', text: quote }),
      make('span', { className: 'deep__tag', text: tag }),
    ]))));
  }
  if (r.divergences.length) {
    rows.push(make('p', { className: 'deep__minor', text: t('deep.drawer.divergences') }));
    rows.push(make('ul', { className: 'deep__reqs' }, r.divergences.map((d) => make('li', {}, [
      make('span', { className: 'deep__inference', text: `${t('deep.inference')}: ${d.text}` }),
      quoteList(make, d.quotes),
    ]))));
  }
  if (r.tools.length) {
    rows.push(make('p', { className: 'deep__minor', text: t('deep.drawer.tools') }));
    const tools = r.tools.map((x) => `${x.name} (${t(`deep.importance.${x.importance}`)})`);
    rows.push(make('p', { text: tools.join(', ') }));
  }
  rows.push(make('details', { className: 'deep__details' }, [
    make('summary', { text: t('deep.drawer.provenance') }),
    make('ul', { className: 'deep__list' }, [
      t('deep.prov.access', { access: t(`deep.access.${review.provider}`) }),
      t('deep.prov.model', { model: review.model }),
      t('deep.prov.date', { date: String(review.created_at).slice(0, 16).replace('T', ' ') }),
      t('deep.prov.contract', { contract: review.contract }),
      t('deep.prov.profile', { digest: String(review.profile_digest).slice(0, 12) }),
      t('deep.prov.stratum', { stratum: t(`deep.stratum.${review.stratum}`) }),
      t('deep.prov.dropped', { count: Object.values(review.dropped || {}).reduce((a, b) => a + b, 0) }),
    ].map((text) => make('li', { text }))),
  ]));
  return section(t('deep.drawer.head'), rows, { lede: t('deep.drawer.lede') });
}

/**
 * The three answers, one line each, never folded into one word: how the work
 * aligns, how far the history SENT shows the requirements, what is left to
 * confirm. Central requirements not shown come after, quoted.
 */
function axisLines(make, a) {
  // "label: n" pairs, zeros left out, so no sentence has to agree with a number.
  const counted = (pairs) => pairs.filter(([, n]) => n)
    .map(([key, n]) => `${t(`deep.count.${key}`)}: ${n}`).join(' · ');
  const c = a.counts;
  const history = counted(['mandatory', 'shown', 'partial', 'declared', 'not_shown', 'review'].map((k) => [k, c[k]]));
  const confirm = counted([
    ['conditions', a.conditions.length + a.languages.length], ['questions', a.questions.length],
  ]);
  const lines = [
    [t('deep.axis.alignment'), t(`deep.alignment.${a.alignment}`)],
    [t('deep.axis.history'), a.history === 'NOT_SENT' || a.history === 'NONE_STATED'
      ? t(`deep.history.${a.history}`)
      : `${t(`deep.history.${a.history}`)} (${history})`],
    [t('deep.axis.confirm'), confirm || t('deep.confirmNone')],
  ];
  const out = lines.map(([label, text]) => make('span', { className: 'deep__axis' }, [
    make('span', { className: 'deep__axislabel', text: `${label}: ` }), text,
  ]));
  for (const index of a.central_gaps) {
    out.push(make('span', {
      className: 'deep__gap', text: t('deep.centralGap', { quote: a.requirements[index].quote }),
    }));
  }
  if (a.to_review) {
    out.push(make('span', { className: 'deep__note', text: t('deep.toReview', { count: a.to_review }) }));
  }
  return out;
}

function requirementItem(make, review, a, req) {
  const gap = a.central_gaps.includes(req.index);
  const tags = [t(`deep.kind.${req.kind}`), t(`deep.status.${req.status}`)];
  if (gap) tags.push(t('deep.centralTag'));
  else if (req.central) tags.push(t('deep.centralCore'));
  const children = [
    make('q', { className: 'deep__quote', text: req.quote }),
    make('span', { className: 'deep__tag', text: tags.join(' · ') }),
  ];
  if (req.parts.length) {
    children.push(make('span', {
      className: 'deep__parts',
      text: req.parts.map((p) => `${p.linked ? t('deep.part.linked') : t('deep.part.unknown')}: ${p.text}`).join(' · '),
    }));
  }
  for (const note of req.notes) {
    children.push(make('span', { className: 'deep__note', text: t(`deep.reqNote.${note}`) }));
  }
  const said = { supported: 'shown', partial: 'partial', not_shown: 'not_shown', unclear: 'unknown' }[req.reading_said];
  if (said !== req.status) {
    const text = t('deep.readingSaid', { said: t(`deep.status.${said}`) });
    children.push(make('span', { className: 'deep__note', text }));
  }
  for (const id of req.evidence_ids) {
    const ev = (review.evidence || {})[id];
    children.push(make('span', {
      className: 'deep__evidence',
      text: ev && !ev.changed
        ? `${t(`deep.claimType.${ev.type}`)}: ${ev.text}` : t('deep.evidenceChanged', { id }),
    }));
  }
  return make('li', {
    className: `deep__req deep__req--${req.status}${gap ? ' deep__req--gap' : ''}`,
    dataset: { status: req.status },
  }, children);
}

/** Why a reading is out of date, in words: the posting, the instructions or the profile changed. */
function staleText(row) {
  const why = (row.stale_reasons || []).map((code) => t(`deep.staleReason.${code}`)).join(' ');
  return `${t('deep.staleLong')} ${why}`.trim();
}

function quoteList(make, quotes) {
  return make('ul', { className: 'deep__quotes' }, (quotes || []).map((q) => make('li', {}, [make('q', { text: q })])));
}
