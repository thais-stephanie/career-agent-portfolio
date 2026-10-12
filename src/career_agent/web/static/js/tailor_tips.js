/**
 * tailor_tips.js -- Resume Tailor > Tips: orientation and a checklist.
 *
 * Plain advice for people who may never have written a resume. It promises
 * nothing about interviews or offers, states no statistic, and suggests text
 * only when it can be built from the person's own data (the file name from
 * their own name). Marking a tip done is the person's act, kept in this
 * browser for this profile; nothing here is sent anywhere or generated.
 *
 * A note under a tip is real data or it is absent: how many confirmed lines
 * already carry a figure, how many skills are confirmed, whether the Master has
 * contact details. Nothing is invented to fill a gap.
 */

import { el, button } from './dom.js';
import { t } from './i18n.js';
import * as api from './api.js';

const STORE = 'careerAgent.tp.tips.v1';
const LINKEDIN = ['li1', 'li2', 'li3', 'li4', 'li5', 'li6'];
const RESUME = ['cv1', 'cv2', 'cv3', 'cv4', 'cv5', 'cv6'];
//: The tips that also say how, step by step.
const HOW = new Set(['li3', 'cv6']);

function storeKey() {
  return `${STORE}:${api.getLocalProfile() || 'default'}`;
}

function readDone() {
  try {
    const raw = JSON.parse(window.localStorage.getItem(storeKey()) || '[]');
    return new Set(Array.isArray(raw) ? raw : []);
  } catch {
    return new Set();
  }
}

function writeDone(done) {
  try {
    window.localStorage.setItem(storeKey(), JSON.stringify([...done]));
  } catch {
    /* The checklist then lasts for this visit only. */
  }
}

/** A file name built from the person's own name, or null. */
function fileName(fullName) {
  const words = String(fullName || '').normalize('NFKD').replace(/[̀-ͯ]/g, '')
    .split(/\s+/).filter(Boolean);
  if (words.length < 2) return null;
  const clean = words.map((w) => w.replace(/[^A-Za-z0-9]/g, '')).filter(Boolean);
  return clean.length >= 2 ? `${clean.join('-')}-${t('tips.fileSuffix')}.pdf` : null;
}

/** What the person's own data says about each tip. Absent when it says nothing. */
async function factsAbout() {
  const facts = {};
  let master = null;
  let career = null;
  let library = null;
  let saved = [];
  try {
    [master, career, library] = await Promise.all([
      api.getResumeMaster(), api.getCareer(), api.listResumeDocuments(),
    ]);
    saved = ((await api.listJobs('saved_only=true&limit=50')) || {}).items || [];
  } catch {
    /* A tip without a note is still a tip. */
  }
  const identity = master && master.master ? (master.master.document || {}).identity || {} : null;
  if (identity) {
    const name = fileName(identity.full_name);
    if (name) facts.cv5 = { suggestion: name };
    const missing = ['email', 'location'].filter((key) => !identity[key]);
    if (missing.length) facts.cv4 = {
      note: t('tips.note.contactMissing', { what: missing.map((key) => t(`tips.what.${key}`)).join(', ') }),
    };
  }
  const lines = ((career && career.experiences) || []).flatMap((e) => e.highlights || []);
  if (lines.length) {
    const withNumber = lines.filter((line) => /\d/.test(line.text || '')).length;
    facts.cv2 = { note: t('tips.note.numbers', { n: withNumber, total: lines.length }) };
  }
  const skills = new Set(((career && career.experiences) || []).flatMap((e) => e.skills || []));
  if (skills.size) facts.li4 = { note: t('tips.note.skills', { n: skills.size }) };
  const made = new Set(((library && library.jobs) || []).map((g) => g.job_id).filter(Boolean));
  const waiting = saved.filter((job) => !made.has(job.job_id)).length;
  if (waiting) facts.cv6 = { note: t('tips.note.waiting', { n: waiting }) };
  return facts;
}

export function createTips() {
  const root = el('div', { className: 'tp-tips' });
  let drawing = 0;

  function row(id, done, facts, redraw) {
    const fact = facts[id] || {};
    const title = t(`tips.${id}.title`);
    const box = button(done.has(id) ? '✓' : '', () => {
      if (done.has(id)) done.delete(id);
      else done.add(id);
      writeDone(done);
      redraw();
    }, {
      className: 'tp-tick', attrs: {
        role: 'checkbox', 'aria-checked': String(done.has(id)), 'aria-label': t('tips.mark', { tip: title }),
      },
    });
    const body = [
      el('span', { className: 'tp-tip__title', text: title }),
      el('span', { className: 'tp-tip__why', text: t(`tips.${id}.why`) }),
    ];
    if (!done.has(id)) {
      if (HOW.has(id)) body.push(el('span', { className: 'tp-tip__how', text: t(`tips.${id}.how`) }));
      if (fact.note) body.push(el('span', { className: 'tp-tip__note', text: fact.note }));
      if (fact.suggestion) {
        body.push(el('span', { className: 'tp-tip__suggest' }, [
          el('code', { text: fact.suggestion }),
          button(t('tips.copy'), async (event) => {
            const target = event.currentTarget;
            try {
              await navigator.clipboard.writeText(fact.suggestion);
              target.textContent = t('tips.copied');
            } catch {
              target.textContent = t('tips.copyFailed');
            }
          }, { className: 'btn btn--small' }),
        ]));
      }
    }
    return el('div', { className: `tp-tip${done.has(id) ? ' is-done' : ''}`, dataset: { tip: id } }, [
      box, el('div', { className: 'tp-tip__body' }, body),
    ]);
  }

  function group(icon, titleKey, ledeKey, ids, done, facts, redraw) {
    const finished = ids.filter((id) => done.has(id)).length;
    return el('section', { className: 'tp__card tp-tipgroup', attrs: { 'aria-labelledby': `tp-tips-${icon}` } }, [
      el('div', { className: 'tp-tipgroup__head' }, [
        el('div', {}, [
          el('h3', { className: 'tp__h2', attrs: { id: `tp-tips-${icon}` }, text: t(titleKey) }),
          el('p', { className: 'tp__lede', text: t(ledeKey) }),
        ]),
        el('span', { className: 'tp__pill', text: t('tips.count', { n: finished, total: ids.length }) }),
      ]),
      ...ids.map((id) => row(id, done, facts, redraw)),
    ]);
  }

  async function draw() {
    const turn = ++drawing;
    root.replaceChildren(el('p', { className: 'tp__lede', text: t('app.loading') }));
    const facts = await factsAbout();
    if (turn !== drawing) return;
    const done = readDone();
    const redraw = () => {
      root.replaceChildren(
        el('p', { className: 'tp__lede', text: t('tips.lede') }),
        group('li', 'tips.linkedin', 'tips.linkedinLede', LINKEDIN, done, facts, redraw),
        group('cv', 'tips.resume', 'tips.resumeLede', RESUME, done, facts, redraw),
        el('p', { className: 'tp__note', text: t('tips.promise') }),
      );
    };
    redraw();
  }

  return { root, draw };
}
