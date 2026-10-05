/**
 * resume_analyze.js -- Analyze: what Career Agent can verify about one resume,
 * and what it shows against what one job explicitly asks.
 *
 * Two modes: a resume alone, or a resume for a job (a job version's own ad,
 * a Career Agent job from the drawer, or a pasted ad). The server analyzes
 * the exact saved content as a milestone and answers with named findings and
 * counts: never a score, a percentage or a prediction. Each finding says
 * what it is about, whether it is a fact or advice, and what to do next;
 * Apply exists only for the exact, safe changes the server makes itself
 * (remove an exact duplicate line, add or show confirmed experience). A gap
 * has no Apply. Eligibility stays apart from what the resume shows.
 */

import {
  analyzeResume, applyAnalysis, dismissAnalysis, getResumeDocument, listResumeDocuments,
} from './api.js';
import { button, el } from './dom.js';
import { t, tCount } from './i18n.js';

/** Severity said with a sign and a word, never a colour alone. */
const SIGN = { BLOCKING: '!!', WARNING: '!', OPPORTUNITY: '+', INFO: 'i' };
const STATES = [
  'SUPPORTED_IN_RESUME', 'SUPPORTED_IN_CONFIRMED_EXPERIENCE_NOT_RESUME',
  'RESUME_TEXT_WITHOUT_CONFIRMED_SUPPORT', 'NOT_FOUND_IN_CONFIRMED_EXPERIENCE',
];
const small = (label, onClick, extra = {}) => button(label, onClick, { className: 'btn btn--small', ...extra });

export function createAnalyze({ host, show, open, onEvidence, onTailor }) {
  // What is being analyzed: the document, and how the job is chosen.
  let choice = { id: null, job: null };
  let last = null;
  const status = el('p', { className: 'rvt__state', attrs: { role: 'status', 'aria-live': 'polite' } });

  /** The form: which resume, which mode, which job. Nothing runs until Analyze. */
  async function draw(focus = true) {
    let library;
    try {
      library = await listResumeDocuments();
    } catch (error) {
      host.replaceChildren(el('p', { attrs: { role: 'alert' }, text: error.userMessage || t('rv.failed') }));
      return;
    }
    const docs = [library.master, ...library.others, ...library.jobs.flatMap((g) => g.versions.map(
      (v) => ({ ...v, title: v.title || [g.title, g.company].filter(Boolean).join(' · ') }),
    ))].filter((d) => d && !d.archived);
    if (!docs.length) {
      host.replaceChildren(el('p', { text: t('rv.an.noResume') }));
      return;
    }
    if (!docs.some((d) => d.id === choice.id)) choice = { id: docs[0].id, job: null };
    const picked = () => docs.find((d) => d.id === choice.id);
    const select = el('select', { className: 'select', attrs: { id: 'rva-doc' } }, docs.map((d) => el('option', {
      attrs: { value: d.id }, props: { selected: d.id === choice.id },
      text: d.kind === 'MASTER' ? `${t('rv.an.master')}: ${d.title}` : d.version_number
        ? `${d.title} · V${d.version_number}` : d.title,
    })));
    const jobBox = el('div', { className: 'rvan__job' });
    const mode = (value, key) => el('label', { className: 'rva__option' }, [
      el('input', {
        attrs: { type: 'radio', name: 'rvan-mode', value },
        props: { checked: (choice.job !== null) === (value === 'job') },
        on: { change: () => { choice.job = value === 'job' ? (picked().tailored ? 'own' : 'ad') : null; drawJob(); } },
      }),
      el('span', { text: ` ${t(key)}` }),
    ]);
    const title = el('input', { className: 'input', attrs: { id: 'rvan-title', maxlength: '300' } });
    const company = el('input', { className: 'input', attrs: { id: 'rvan-company', maxlength: '200' } });
    const text = el('textarea', { className: 'rve__textarea', attrs: { id: 'rvan-ad', rows: '5' } });
    const field = (id, key, node) => el('label', { className: 'rve__field', attrs: { for: id } }, [
      el('span', { className: 'rve__label', text: t(key) }), node,
    ]);
    function drawJob() {
      if (choice.job === null) jobBox.replaceChildren();
      else if (choice.job === 'own') {
        jobBox.replaceChildren(el('p', { className: 'rve__note', text: t('rv.an.ownJob') }));
      }
      else if (choice.job && choice.job.job_id) {
        jobBox.replaceChildren(el('p', { className: 'rve__note', text: t('rv.an.thisJob') }));
      } else {
        choice.job = 'ad';
        jobBox.replaceChildren(field('rvan-title', 'rv.tailor.title', title),
          field('rvan-company', 'rv.tailor.company', company), field('rvan-ad', 'rv.tailor.ad', text));
      }
    }
    select.addEventListener('change', () => { choice = { id: select.value, job: null }; void draw(false); });
    drawJob();
    const go = small(t('rv.an.go'), () => void run(), {
      className: 'btn btn--small btn--primary', attrs: { id: 'rvan-go' },
    });
    async function run() {
      let job = {};
      if (choice.job === 'own') job = { job: 'own' };
      else if (choice.job && choice.job.job_id) job = { job_id: choice.job.job_id };
      else if (choice.job === 'ad') {
        if (!title.value.trim() || text.value.trim().length < 20) {
          status.textContent = t('rv.tailor.need');
          return;
        }
        job = { ad: { title: title.value.trim(), company: company.value.trim() || null, text: text.value } };
      }
      await analyze(job);
    }
    host.replaceChildren(el('section', { className: 'rvan', attrs: { 'aria-labelledby': 'rvan-h' } }, [
      el('h2', { className: 'rvw__h2', attrs: { id: 'rvan-h', tabindex: '-1' }, text: t('rv.an.title') }),
      el('p', { className: 'rve__note', text: t('rv.an.lede') }),
      el('label', { className: 'rve__field', attrs: { for: 'rva-doc' } }, [
        el('span', { className: 'rve__label', text: t('rv.an.which') }), select,
      ]),
      el('fieldset', { className: 'rvan__modes' }, [
        el('legend', { className: 'rve__label', text: t('rv.an.mode') }),
        mode('resume', 'rv.an.modeResume'), mode('job', 'rv.an.modeJob'),
      ]),
      jobBox,
      go,
      status,
      el('div', { className: 'rvan__result', attrs: { id: 'rvan-result' } }),
    ]));
    if (focus) host.querySelector('h2').focus();
  }

  /** Analyze the saved content (the server checkpoints it) and draw the result. */
  async function analyze(job) {
    status.textContent = t('rv.an.working');
    try {
      const doc = await getResumeDocument(choice.id);
      last = { job, result: await analyzeResume(choice.id, { expected_sha256: doc.sha256, ...job }) };
    } catch (error) {
      status.textContent = error.userMessage || t('rv.failed');
      return;
    }
    status.textContent = t('rv.an.done');
    result(last.result);
  }

  async function act(fn) {
    try {
      await fn();
      await analyze(last.job);
    } catch (error) {
      status.textContent = error.userMessage || t('rv.failed');
    }
  }

  function findingItem(r, f) {
    const actions = [];
    if (f.action === 'APPLY' && f.kind === 'DUPLICATE_BULLET') {
      actions.push(small(t('rv.an.apply.DUPLICATE_BULLET'), () => void act(async () => {
        const doc = await getResumeDocument(r.document_id);
        await applyAnalysis(r.document_id, { key: f.key, expected_sha256: doc.sha256 });
      }), { className: 'btn btn--small btn--primary' }));
    } else if (f.action === 'EDIT') {
      actions.push(small(t('rv.an.edit'), () => void open(r.document_id)));
    } else if (f.action === 'OPEN_EVIDENCE' || f.action === 'ADD_EVIDENCE') {
      actions.push(small(t(f.action === 'ADD_EVIDENCE' ? 'rv.job.addEvidence' : 'rv.an.openEvidence'),
        () => onEvidence(f.quote || '')));
    } else if (f.action === 'EXPORT_CHECK') {
      actions.push(small(t('rv.an.exportCheck'), () => void open(r.document_id)));
    }
    const what = t(`rv.find.${f.kind}`, { n: f.n || '' });
    actions.push(small(t('rv.job.dismiss'), () => void act(() => dismissAnalysis(r.document_id, {
      key: f.key, ...(r.job ? { jd_snapshot_id: r.job.snapshot_id } : {}),
    })), { ariaLabel: `${t('rv.job.dismiss')}: ${what}` }));
    return el('li', { className: 'rvan__finding', dataset: { severity: f.severity, kind: f.kind } }, [
      el('p', {}, [
        el('strong', { text: `${SIGN[f.severity]} ${t(`rv.an.sev.${f.severity}`)}` }),
        ` · ${t(`rv.an.nature.${f.nature}`)}: `, what,
      ]),
      el('div', { className: 'rvl__rename' }, actions),
    ]);
  }

  function requirement(r, q) {
    const detail = [
      el('p', { className: 'rve__note', text: t(`rv.an.hard.${q.hardness}`) }),
      q.lines.length ? el('p', {}, [el('strong', { text: `${t('rv.an.where')}: ` }), q.lines.join(' / ')]) : null,
      q.evidence.length ? el('p', {}, [el('strong', { text: `${t('rv.an.from')}: ` }), q.evidence.join(' / ')]) : null,
    ];
    const actions = [];
    if (q.apply) {
      actions.push(small(t('rv.job.apply'), () => void act(async () => {
        const doc = await getResumeDocument(r.document_id);
        await applyAnalysis(r.document_id, {
          key: q.apply, expected_sha256: doc.sha256, jd_snapshot_id: r.job.snapshot_id,
        });
      }), { className: 'btn btn--small btn--primary', ariaLabel: `${t('rv.job.apply')}: ${q.quote}` }));
    }
    if (q.state === 'NOT_FOUND_IN_CONFIRMED_EXPERIENCE') {
      actions.push(small(t('rv.job.addEvidence'), () => onEvidence(q.quote), {
        ariaLabel: `${t('rv.job.addEvidence')}: ${q.quote}`,
      }));
    }
    return el('li', { className: 'rvan__req', dataset: { state: q.state } }, [
      el('details', {}, [
        el('summary', { text: `${t(`rv.an.state.${q.state}`)}: ${q.quote}` }),
        ...detail.filter(Boolean),
        actions.length ? el('div', { className: 'rvl__rename' }, actions) : null,
      ]),
    ]);
  }

  function result(r) {
    const box = host.querySelector('#rvan-result');
    if (!box) return;
    const ev = r.overview.evidence;
    const parts = [
      el('h3', { className: 'rvl__h3', text: t('rv.an.overview') }),
      el('p', { text: t('rv.an.of', { title: r.title }) }),
      el('p', { className: 'rve__note', text: t('rv.an.counts', {
        roles: r.overview.roles, bullets: r.overview.bullets, confirmed: ev.CONFIRMED,
        typed: ev.USER_AUTHORED, imported: ev.IMPORTED,
      }) }),
      el('p', { className: 'rve__note', text: t('rv.an.notScore') }),
    ];
    if (r.job) {
      const c = r.job.counts;
      parts.push(
        el('h3', {
          className: 'rvl__h3',
          text: t('rv.an.forJob', { job: [r.job.title, r.job.company].filter(Boolean).join(' · ') }),
        }),
        // Counts, one sentence each, and none for a zero beyond the first.
        el('p', { attrs: { id: 'rvan-summary' }, text: [
          tCount('rv.an.sumShown', { n: c.SUPPORTED_IN_RESUME, of: c.total }),
          c.SUPPORTED_IN_CONFIRMED_EXPERIENCE_NOT_RESUME
            ? tCount('rv.an.sumMore', { n: c.SUPPORTED_IN_CONFIRMED_EXPERIENCE_NOT_RESUME }) : '',
          c.RESUME_TEXT_WITHOUT_CONFIRMED_SUPPORT
            ? tCount('rv.an.sumSaid', { n: c.RESUME_TEXT_WITHOUT_CONFIRMED_SUPPORT }) : '',
          c.NOT_FOUND_IN_CONFIRMED_EXPERIENCE
            ? tCount('rv.an.sumMissing', { n: c.NOT_FOUND_IN_CONFIRMED_EXPERIENCE }) : '',
        ].filter(Boolean).join(' ') }),
        el('ul', { className: 'rvan__reqs' }, r.job.requirements.filter((q) => STATES.includes(q.state))
          .map((q) => requirement(r, q))),
      );
      const gaps = r.job.requirements.filter((q) => q.state === 'NOT_FOUND_IN_CONFIRMED_EXPERIENCE');
      if (gaps.length || r.job.eligibility.length) {
        parts.push(el('h3', { className: 'rvl__h3', text: t('rv.job.before') }));
        if (gaps.length) {
          parts.push(el('p', { text: t('rv.an.gaps') }),
            el('ul', { className: 'rvj__list' }, gaps.map((g) => el('li', { text: g.quote }))));
        }
        if (r.job.eligibility.length) {
          parts.push(el('p', { text: t('rv.an.eligibility') }),
            el('ul', { className: 'rvj__list' }, r.job.eligibility.map((g) => el('li', { text: g.quote }))));
        }
      }
      if (onTailor && r.job.job_id) {
        parts.push(small(t('rv.job.tailor'), () => onTailor(r.job.job_id)));
      }
    }
    const technical = r.findings.filter((f) => f.category === 'EXPORT');
    const improve = r.findings.filter((f) => f.category !== 'EXPORT' && f.category !== 'JOB');
    parts.push(el('h3', { className: 'rvl__h3', text: t('rv.an.improve') }));
    parts.push(improve.length
      ? el('ul', { className: 'rvan__findings' }, improve.map((f) => findingItem(r, f)))
      : el('p', { text: t('rv.find.none') }));
    if (r.dismissed) {
      parts.push(el('p', { className: 'rve__note', text: tCount('rv.an.dismissed', { n: r.dismissed }) }));
    }
    const checks = r.export ? r.export.checks.map((c) => el('li', {
      text: `${t(`rv.ats.${c.name}`)}: ${t(`rv.ats.status.${c.status}`)}`,
    })) : [];
    parts.push(el('details', { className: 'rvan__technical' }, [
      el('summary', { text: t('rv.an.technical') }),
      el('ul', { className: 'rvan__findings' }, technical.map((f) => findingItem(r, f))),
      r.export
        ? el('p', { className: 'rve__note', text: t(r.export.current ? 'rv.an.exportCurrent' : 'rv.an.exportOld') })
        : null,
      checks.length ? el('ul', { className: 'rvj__list' }, checks) : null,
    ].filter(Boolean)));
    box.replaceChildren(...parts);
  }

  return {
    /** Open Analyze, optionally on one resume and one job (`{job_id}` or 'own'). */
    async start(id = null, job = null) {
      if (id) choice = { id, job };
      show('analyze');
      await draw();
      if (id && job) await analyze(job === 'own' ? { job: 'own' } : { job_id: job.job_id });
    },
    show: () => draw(false),
    relabel: () => (last ? result(last.result) : null),
  };
}
