/**
 * practice.js -- the drawer's Practice tab (V3): interview practice for one job.
 *
 * Seven common questions, one at a time, each with why they ask it and how to
 * answer it. Two of them name the job: the company, and a tool the ad asks
 * for. Questions that ask for a story ("Tell me about a time...") get the STAR
 * method and four small boxes; the others a free box.
 *
 * NOTHING HERE IS INVENTED ABOUT THE PERSON. "You can talk about this" is shown
 * only when the Career Profile holds a confirmed line that fits the question,
 * quoted as it is; with no such line the box is simply absent. The STAR
 * example is labelled as an example, never as their experience.
 *
 * What was practiced and what was written stays in this browser, per local
 * profile and per job: a rehearsal aid, not a record, so not the database.
 */

import { el, button, replace } from './dom.js';
import { t } from './i18n.js';
import * as api from './api.js';

const STORE = 'careerAgent.practice.v1';
//: Answers typed while practising stay in this tab only: free text about
//: salary and past jobs is not written to disk by a rehearsal aid.
const DRAFTS = new Map();

//: The STAR parts, in order, with the V3 colour pair each one wears.
const STAR = [
  { l: 'S', key: 'situation', tone: 'blue' },
  { l: 'T', key: 'task', tone: 'm2' },
  { l: 'A', key: 'action', tone: 'lav' },
  { l: 'R', key: 'result', tone: 'm1' },
];

//: The questions: catalogue key, whether it is a story, and what may answer it.
const QUESTIONS = [
  { key: 'about', star: false, from: 'latest' },
  { key: 'why', star: false, from: null },
  { key: 'problem', star: true, from: 'number' },
  { key: 'tool', star: true, from: 'tool' },
  { key: 'mistake', star: true, from: null },
  { key: 'salary', star: false, from: null },
  { key: 'questions', star: false, from: null },
];

function letter(part) {
  return el('span', {
    className: `pq__letter pq__letter--${part.tone}`, text: part.l, attrs: { 'aria-hidden': 'true' },
  });
}

/**
 * A small mark per local profile and job, in this browser (practised
 * questions, an ad marked as read). Never free text.
 */
export function jobMark(store, jobId) {
  try {
    const all = JSON.parse(window.localStorage.getItem(store) || '{}') || {};
    return all[`${api.getLocalProfile() || 'default'}:${jobId}`];
  } catch {
    return undefined;
  }
}

export function setJobMark(store, jobId, value) {
  try {
    const all = JSON.parse(window.localStorage.getItem(store) || '{}') || {};
    const key = `${api.getLocalProfile() || 'default'}:${jobId}`;
    if (value === null || value === undefined) delete all[key];
    else all[key] = value;
    window.localStorage.setItem(store, JSON.stringify(all));
  } catch {
    /* A mark is a nicety. */
  }
}

/** The tools the ad names: the local reading's when there is one, else the search's labels. */
export function adTools(job) {
  const enriched = ((job.enrichment && job.enrichment.technologies) || [])
    .map((item) => String((item && (item.text || item.name)) || '').trim()).filter(Boolean);
  if (enriched.length) return enriched;
  return [...new Set((job.technologies || []).map((tech) => String(tech.label || '')).filter(Boolean))];
}

/**
 * A confirmed line from the Career Profile that fits a question, or null.
 * Read, never written; quoted as it is.
 */
function lineFor(kind, career, tool) {
  const experiences = (career && career.experiences) || [];
  const lines = experiences.flatMap((entry) => (entry.highlights || [])
    .map((h) => String(h.text || '').trim()).filter(Boolean));
  if (kind === 'latest') {
    // Only a role she has confirmed something about is named as hers.
    const confirmed = experiences.filter((entry) => (entry.highlights || []).length);
    const latest = confirmed.find((entry) => entry.current_role) || confirmed[0];
    if (!latest || !(latest.title || latest.company)) return null;
    const role = [latest.title, latest.company].filter(Boolean).join(` ${t('practice.at')} `);
    const first = (latest.highlights || []).map((h) => String(h.text || '').trim()).find(Boolean);
    return first ? `${t('practice.latestJob', { role })} ${first}` : t('practice.latestJob', { role });
  }
  if (kind === 'number') return lines.find((line) => /\d/.test(line)) || null;
  if (kind === 'tool' && tool) {
    const needle = tool.toLowerCase();
    return lines.find((line) => line.toLowerCase().includes(needle)) || null;
  }
  return null;
}

export function createPractice() {
  const host = el('div', { className: 'pq' });
  let job = null;
  let career = null;
  let current = 0;
  let exampleOpen = false;
  let token = 0;

  const scope = () => `${api.getLocalProfile() || 'default'}:${job ? job.job_id : ''}`;
  const state = () => ({
    done: {}, current: 0, ...(jobMark(STORE, job.job_id) || {}), drafts: DRAFTS.get(scope()) || {},
  });
  const save = (next) => {
    DRAFTS.set(scope(), next.drafts);
    setJobMark(STORE, job.job_id, { done: next.done, current: next.current });
  };

  function load(nextJob) {
    job = nextJob;
    current = state().current || 0;
    exampleOpen = false;
    paint();
    const mine = ++token;
    api.getCareer().then((payload) => {
      if (mine !== token) return;
      career = payload;
      paint();
    }).catch(() => { /* No profile line is shown; the questions still are. */ });
  }

  function reset() {
    token += 1;
    job = null;
    replace(host, []);
  }

  function go(index) {
    current = Math.max(0, Math.min(QUESTIONS.length - 1, index));
    save({ ...state(), current });
    paint();
  }

  function paint() {
    if (!job) return;
    const saved = state();
    const tool = adTools(job)[0] || '';
    const doneCount = QUESTIONS.filter((_, index) => saved.done[index]).length;
    const all = doneCount === QUESTIONS.length;
    const q = QUESTIONS[current];
    const params = { company: job.company_name || t('practice.thisCompany'), tool: tool || t('practice.thisSkill') };
    const from = q.from ? lineFor(q.from, career, tool) : null;
    const draftKey = (part) => (part ? [current, part].join('-') : String(current));
    const setDraft = (part, value) => {
      const next = state();
      next.drafts = { ...next.drafts, [draftKey(part)]: value };
      save(next);
    };
    const isDone = Boolean(saved.done[current]);

    replace(host, [
      el('section', { className: 'pq__top' }, [
        el('div', { className: 'pq__titles' }, [
          el('h3', { className: 'pq__head', text: t('practice.head') }),
          el('p', { className: 'pq__lede', text: t('practice.lede') }),
        ]),
        el('span', {
          className: `pq__count${all ? ' is-all' : ''}`,
          text: all ? t('practice.allDone') : t('practice.count', { n: doneCount, total: QUESTIONS.length }),
        }),
      ]),
      starCard(),
      el('nav', {
        className: 'pq__nav', attrs: { 'aria-label': t('practice.questions') },
      }, QUESTIONS.map((_, index) => {
        const done = Boolean(saved.done[index]);
        return button(done ? '✓' : String(index + 1), () => go(index), {
          className: `pq__dot${index === current ? ' is-current' : ''}${done ? ' is-done' : ''}`,
          ariaLabel: t('practice.questionN', { n: index + 1 }),
          attrs: index === current ? { 'aria-current': 'step' } : {},
        });
      })),
      el('section', { className: 'pq__card' }, [
        el('div', { className: 'pq__kickrow' }, [
          el('span', {
            className: 'pq__kicker', text: t('practice.kicker', { n: current + 1, total: QUESTIONS.length }),
          }),
          q.star ? el('span', { className: 'pq__story', text: t('practice.story') }) : null,
        ]),
        el('h4', { className: 'pq__q', text: t(`practice.q.${q.key}`, params) }),
        el('div', { className: 'pq__help' }, [
          el('div', { className: 'pq__box' }, [
            el('span', { className: 'pq__boxhead', text: t('practice.whyAsk') }),
            el('span', { className: 'pq__boxtext', text: t(`practice.why.${q.key}`) }),
          ]),
          el('div', { className: 'pq__box' }, [
            el('span', { className: 'pq__boxhead', text: t('practice.howAnswer') }),
            el('span', { className: 'pq__boxtext', text: t(`practice.how.${q.key}`) }),
          ]),
        ]),
        from
          ? el('div', { className: 'pq__from' }, [
            el('span', { className: 'pq__frommark', text: '✓', attrs: { 'aria-hidden': 'true' } }),
            el('div', { className: 'pq__fromtext' }, [
              el('span', { className: 'pq__boxhead', text: t('practice.youCanTalk') }),
              el('span', { text: from }),
            ]),
          ])
          : null,
        q.star
          ? el('div', { className: 'pq__build' }, [
            el('span', { className: 'pq__buildhead' }, [
              t('practice.build'), ' ', el('span', { className: 'pq__optional', text: t('practice.optional') }),
            ]),
            ...STAR.map((part) => el('label', { className: 'pq__field' }, [
              letter(part),
              el('textarea', {
                className: 'pq__input',
                attrs: {
                  rows: '2', placeholder: t(`practice.ph.${part.key}`), 'aria-label': t(`practice.star.${part.key}`),
                },
                props: { value: saved.drafts[draftKey(part.l)] || '' },
                on: { input: (event) => setDraft(part.l, event.target.value) },
              }),
            ])),
          ])
          : el('textarea', {
            className: 'pq__input pq__input--free',
            attrs: { rows: '3', placeholder: t('practice.freePh'), 'aria-label': t('practice.freeLabel') },
            props: { value: saved.drafts[draftKey('')] || '' },
            on: { input: (event) => setDraft('', event.target.value) },
          }),
        el('div', { className: 'pq__foot' }, [
          button(`← ${t('practice.previous')}`, () => go(current - 1), {
            className: 'pq__prev', attrs: current === 0 ? { disabled: true } : {},
          }),
          el('div', { className: 'pq__footbtns' }, [
            button(isDone ? `✓ ${t('practice.practiced')}` : t('practice.markDone'), () => {
              const next = state();
              next.done = { ...next.done, [current]: !isDone };
              save(next);
              if (!isDone && current < QUESTIONS.length - 1) go(current + 1);
              else paint();
            }, { className: `pq__done${isDone ? ' is-done' : ''}`, attrs: { 'aria-pressed': String(isDone) } }),
            current < QUESTIONS.length - 1
              ? button(`${t('practice.next')} →`, () => go(current + 1), { className: 'pq__next' })
              : null,
          ]),
        ]),
      ]),
    ]);
  }

  function starCard() {
    return el('section', { className: 'pq__star' }, [
      el('div', { className: 'pq__starhead' }, [
        el('strong', { text: t('practice.starHead') }),
        button(exampleOpen ? t('practice.hideExample') : t('practice.seeExample'), () => {
          exampleOpen = !exampleOpen;
          paint();
        }, { className: 'pq__examplebtn', attrs: { 'aria-expanded': String(exampleOpen) } }),
      ]),
      el('p', { className: 'pq__starlede', text: t('practice.starLede') }),
      el('div', { className: 'pq__parts' }, STAR.map((part) => el('div', { className: 'pq__part' }, [
        letter(part),
        el('span', { className: 'pq__partname', text: t(`practice.star.${part.key}`) }),
        el('span', { className: 'pq__partdesc', text: t(`practice.starDesc.${part.key}`) }),
      ]))),
      exampleOpen
        ? el('div', { className: 'pq__example' }, [
          el('span', { className: 'pq__boxhead', text: t('practice.exampleHead') }),
          ...STAR.map((part) => el('div', { className: 'pq__exline' }, [
            el('span', { className: `pq__exletter pq__exletter--${part.tone}`, text: part.l }),
            el('span', { text: t(`practice.example.${part.key}`) }),
          ])),
        ])
        : null,
    ]);
  }

  return { host, load, reset };
}
