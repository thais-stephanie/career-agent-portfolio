/**
 * tailor_page.js -- "Resume Tailor", the direct way in from the sidebar.
 *
 * It adds no engine. Adapting a resume to a job is the existing Tailor from the
 * Master (deterministic, no AI) and the existing AI drafter (disclosure first);
 * this page only lets a person choose the job and press the button, from the
 * sidebar, with the job kept when they have to make a Master resume first.
 *
 * Opening it generates nothing and sends nothing: it reads the Master's
 * existence, the job chosen and the versions already made, and waits.
 */

import { el, button } from './dom.js';
import { t } from './i18n.js';
import * as api from './api.js';

const LIST = 8;

function matchWord(job) {
  return typeof job.match_score === 'number' && job.scored !== false
    ? t('tp.match', { n: Math.round(job.match_score) })
    : null;
}

export function createTailorPage({ host, onAdapt, onAdaptAi, onMakeMaster, onOpenJob, onOpenVersion }) {
  let selected = null; // the job card chosen (title, company, job_id)
  let drawing = 0;

  function jobRow(job) {
    const word = matchWord(job);
    return el('li', {}, [
      button('', () => {
        selected = job;
        void draw();
      }, { className: 'tp__job', attrs: { 'data-job': job.job_id } }),
    ].map((node) => {
      node.append(
        el('span', { className: 'tp__jobmain' }, [
          el('span', { className: 'tp__jobtitle', text: job.title }),
          el('span', { className: 'tp__jobmeta', text: job.company_name || '' }),
        ]),
        word ? el('span', { className: 'tp__pill', text: word }) : null,
      );
      return node;
    }));
  }

  async function picker(token) {
    let saved = [];
    let best = [];
    try {
      [saved, best] = (await Promise.all([
        api.listJobs(`saved_only=true&limit=${LIST}`),
        api.listJobs(`limit=${LIST}`),
      ])).map((answer) => answer.items || []);
    } catch {
      /* an empty list says what to do next */
    }
    if (token !== drawing) return null;
    const seen = new Set(saved.map((job) => job.job_id));
    const rest = best.filter((job) => !seen.has(job.job_id));
    const group = (key, jobs) => (jobs.length
      ? [el('h3', { className: 'tp__h3', text: t(key) }), el('ul', { className: 'tp__jobs' }, jobs.map(jobRow))]
      : []);
    const body = [...group('tp.pick.saved', saved), ...group('tp.pick.best', rest)];
    return el('section', { className: 'tp__card', attrs: { 'aria-labelledby': 'tp-pick' } }, [
      el('h2', { className: 'tp__h2', attrs: { id: 'tp-pick' }, text: t('tp.pick.head') }),
      body.length ? el('p', { className: 'tp__lede', text: t('tp.pick.lede') }) : null,
      ...(body.length ? body : [el('p', { className: 'tp__lede', text: t('tp.pick.empty') })]),
    ].filter(Boolean));
  }

  async function chosen(token) {
    const job = selected;
    let versions = [];
    try {
      const answer = await api.getJobResumes(job.job_id);
      versions = answer.versions || [];
    } catch {
      /* the actions work without the list of versions */
    }
    if (token !== drawing) return null;
    return el('section', { className: 'tp__card', attrs: { 'aria-labelledby': 'tp-job' } }, [
      el('h2', { className: 'tp__h2', attrs: { id: 'tp-job' }, text: t('tp.job.head') }),
      el('p', { className: 'tp__jobtitle', text: job.title }),
      el('p', { className: 'tp__jobmeta', text: job.company_name || '' }),
      el('div', { className: 'tp__actions' }, [
        button(t('tp.act.adapt'), () => onAdapt(job), {
          className: 'btn btn--primary', attrs: { id: 'tp-adapt' },
        }),
        onAdaptAi ? button(t('tp.act.ai'), () => onAdaptAi(job), {
          className: 'btn', attrs: { id: 'tp-adapt-ai' },
        }) : null,
        button(t('tp.act.open'), () => onOpenJob(job), { className: 'btn btn--quiet', attrs: { id: 'tp-open-job' } }),
        button(t('tp.act.change'), () => { selected = null; void draw(); }, {
          className: 'btn btn--quiet', attrs: { id: 'tp-change' },
        }),
      ].filter(Boolean)),
      el('p', { className: 'tp__note', text: t('tp.act.note') }),
      versions.length ? el('div', {}, [
        el('h3', { className: 'tp__h3', text: t('tp.versions') }),
        el('ul', { className: 'tp__jobs' }, versions.map((v) => el('li', {}, [
          button(['V' + v.version_number, v.title].filter(Boolean).join(': '), () => onOpenVersion(v.id), {
            className: 'tp__job',
          }),
        ]))),
      ]) : null,
    ].filter(Boolean));
  }

  function needsMaster() {
    return el('section', { className: 'tp__card', attrs: { 'aria-labelledby': 'tp-master' } }, [
      el('h2', { className: 'tp__h2', attrs: { id: 'tp-master' }, text: t('tp.noMaster.head') }),
      el('p', {
        className: 'tp__lede',
        text: selected ? t('tp.noMaster.forJob', { job: selected.title }) : t('tp.noMaster.text'),
      }),
      el('div', { className: 'tp__actions' }, [
        button(t('tp.noMaster.go'), () => onMakeMaster(selected), {
          className: 'btn btn--primary', attrs: { id: 'tp-make-master' },
        }),
      ]),
    ]);
  }

  async function draw() {
    const token = ++drawing;
    host.dataset.state = 'loading';
    host.replaceChildren(el('p', { className: 'tp__lede', text: t('app.loading') }));
    let hasMaster = false;
    try {
      hasMaster = Boolean((await api.getResumeMaster()).master);
    } catch {
      host.replaceChildren(el('p', {
        className: 'tp__lede', attrs: { role: 'alert' }, text: t('rv.failed'),
      }));
      return;
    }
    if (token !== drawing) return;
    let body;
    let state;
    if (!hasMaster) {
      state = 'needs-master';
      body = needsMaster();
    } else if (selected) {
      state = 'job';
      body = await chosen(token);
    } else {
      state = 'pick';
      body = await picker(token);
    }
    // The state is named only once its words are on the page.
    if (body && token === drawing) {
      host.replaceChildren(el('div', { className: 'tp' }, [body]));
      host.dataset.state = state;
    }
  }

  return {
    /** Open the page, optionally with a job already chosen (from a job's drawer). */
    async show(job = null) {
      if (job) selected = job;
      await draw();
    },
    relabel: () => { if (!host.hidden) void draw(); },
  };
}
