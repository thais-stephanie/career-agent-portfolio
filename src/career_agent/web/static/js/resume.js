/**
 * resume.js -- the Resume helper, inside Career Agent (V3 handoff).
 *
 * ONE APPLICATION. The Resume helper used to be a second app on its own port,
 * opened in a new tab. It is a page of this one now: the same window, the same
 * sidebar, the same profile. The tailoring engine is unchanged and still runs
 * in this process; this page reaches it through Career Agent's own server
 * (`/rt/api/...`, same origin), never through an iframe or a second address.
 *
 * ONE DATA MODEL. The jobs are Career Agent's jobs, the experience is Career
 * Agent's Career Profile, and the profile is the active local profile. Before
 * a resume is made, the confirmed Career Profile is copied into the engine's
 * own list (the existing bridge import, verbatim, with provenance), so nothing
 * is typed twice and nothing unconfirmed crosses. A resume made for a job
 * remembers that job by its id.
 *
 * NOTHING INVENTED. Every line of a resume comes from the person's confirmed
 * experience; the fit shown is the engine's own coverage figure; tips carry a
 * suggestion only when it is built from real data (the file name from their
 * own name), and a tip is marked done only by the person.
 */

import { el, button, replace } from './dom.js';
import { t } from './i18n.js';
import * as api from './api.js';
import { matchTone } from './cards.js';
import { formatDate, statusLabel, statusOptions } from './format.js';

const TABS = ['start', 'make', 'resumes', 'experience', 'tips'];
const TIP_STORE = 'careerAgent.rh.tips.v1';
//: Without a local profile the engine cannot read Career Agent's jobs, so it
//: refuses a job id; which resume was made for which job is kept here instead.
const JOB_OF_RUN = 'careerAgent.rh.jobOfRun.v1';

function jobsOfRuns() {
  try {
    return JSON.parse(window.localStorage.getItem(JOB_OF_RUN) || '{}') || {};
  } catch {
    return {};
  }
}
//: The engine's application statuses, in the order a click steps through them.
const RUN_STATUS = ['Considering', 'Applied', 'Interviewing', 'Closed'];
//: The engine's requirement results, as the four V3 states.
const RESULT_TONE = {
  'Strong match': ['have', 'm1'],
  'Related experience': ['close', 'blue'],
  'Partial match': ['partly', 'm2'],
  'Not evidenced': ['notYet', 'red'],
};

export function createResumeHelper({ host, onOpenJob, onGoJobs, onGoEvidence, onGoProfile, toast }) {
  let tab = 'start';
  // Which call of show() is the latest: an older one still loading never paints over it.
  let shown = 0;
  let ws = null; // { mode, candidate_id, candidate_name, profile }
  //: Starting a second run stops the first one's polling loop.
  let pollToken = 0;
  // The Make a resume screen's own state.
  const make = {
    stage: 'setup', jobId: null, jd: '', baseId: null, onlyTrue: true, twoPages: true,
    runId: null, view: null, draft: null, match: null, resTab: 'asks', askFilter: 'all',
    selected: null, editing: null, whyOpen: new Set(), showWhere: new Set(), skipped: new Set(),
    goodOpen: false, dlOpen: false, loadStep: 0, error: null,
  };
  let cache = { jobs: null, runs: null, bases: null, career: null, status: new Map() };

  // -- talking to the engine through Career Agent ------------------------------

  const cid = () => (ws && ws.candidate_id) || '';
  const cpath = (rest) => `/candidates/${encodeURIComponent(cid())}${rest}`;

  async function boot() {
    if (ws) return ws;
    const workspace = await api.rt('/workspace');
    if (workspace.mode === 'profile') {
      // The engine follows the active profile; a page that has not learnt
      // its own yet takes it from there, so its next calls are not refused.
      if (!api.getLocalProfile() && workspace.profile) api.setLocalProfile(workspace.profile.id);
      ws = workspace;
    } else {
      // The demo and an installation without local profiles: the engine's
      // own remembered candidate, or its only one. Never a guess between two.
      const people = (await api.rt('/candidates')) || [];
      const live = people.filter((p) => !p.archived);
      ws = {
        mode: 'standalone',
        candidate_id: live.length ? live[0].id : null,
        candidate_name: live.length ? live[0].name : '',
      };
    }
    return ws;
  }

  async function savedJobs() {
    if (cache.jobs) return cache.jobs;
    const query = 'saved_only=1&include_ineligible=1&include_off_target=1&include_unresolved=1&limit=50';
    const page = await api.listJobs(query);
    cache.jobs = (page && page.items) || [];
    return cache.jobs;
  }

  async function runs() {
    if (cache.runs) return cache.runs;
    const made = cid() ? ((await api.rt(cpath('/applications'))) || []) : [];
    const known = jobsOfRuns();
    // A version that failed (an ad nothing could be read from) is not a resume.
    cache.runs = made.filter((run) => !run.state || run.state === 'done')
      .map((run) => (run.career_job_id ? run : { ...run, career_job_id: known[run.id] || null }));
    return cache.runs;
  }

  /**
   * MANY VERSIONS PER JOB. Each resume made for a job is a version of it (V1,
   * V2, ...) in the order it was made, and a new one never replaces an older
   * one. A pasted ad is a job of its own. Newest job first.
   */
  function jobsWithVersions(made) {
    const byJob = new Map();
    for (const run of [...made].sort((a, b) => String(a.id).localeCompare(String(b.id)))) {
      const key = run.career_job_id || `ad:${run.id}`;
      if (!byJob.has(key)) byJob.set(key, []);
      byJob.get(key).push(run);
    }
    return [...byJob.values()].map((versions) => ({
      jobId: versions[0].career_job_id || null,
      versions,
      latest: versions[versions.length - 1],
    })).sort((a, b) => String(b.latest.id).localeCompare(String(a.latest.id)));
  }

  /** "Make another version": the same job, or the same pasted ad, set up again. */
  async function anotherVersion(run) {
    if (run.career_job_id) return show('make', { jobId: run.career_job_id });
    const answer = await api.rt(cpath(`/applications/${encodeURIComponent(run.id)}`));
    const ad = (answer && answer.view && answer.view.job && answer.view.job.ad) || '';
    Object.assign(make, { stage: 'setup', jobId: null, jd: ad, runId: null, error: null, unreadable: false });
    return show('make');
  }

  const versionsLabel = (group) => t(group.versions.length === 1 ? 'rh.ver.countOne' : 'rh.ver.count', {
    n: group.versions.length,
  });

  async function bases() {
    if (cache.bases) return cache.bases;
    cache.bases = cid() ? ((await api.rt(cpath('/resumes'))) || []) : [];
    return cache.bases;
  }

  async function career() {
    if (cache.career) return cache.career;
    cache.career = await api.getCareer().catch(() => null);
    return cache.career;
  }


  // -- the page --------------------------------------------------------------

  const tabRow = el('div', {
    className: 'rh__tabs segmented', attrs: { role: 'tablist', 'aria-label': t('nav.tailor') },
  });
  const body = el('div', { className: 'rh__body', attrs: { role: 'tabpanel', tabindex: '-1' } });
  replace(host, [tabRow, body]);

  /**
   * Redraw the panel and keep the reader's place: a control that was focused
   * and exists again (same `data-k`) gets the focus back, so a keyboard or a
   * screen reader is not thrown to the top on every choice.
   */
  function repaint(children) {
    const key = document.activeElement && body.contains(document.activeElement)
      ? document.activeElement.dataset.k : null;
    replace(body, children);
    body.setAttribute('aria-labelledby', `rh-tab-${tab}`);
    if (key) {
      const again = body.querySelector(`[data-k="${CSS.escape(key)}"]`);
      if (again) again.focus();
    }
  }

  function paintTabs(lookCount = 0) {
    replace(tabRow, TABS.map((key) => {
      const on = key === tab;
      const node = button(t(`rh.tab.${key}`), () => show(key), {
        className: 'segmented__btn rh__tab',
        attrs: { role: 'tab', 'aria-selected': String(on), id: `rh-tab-${key}`, tabindex: on ? '0' : '-1' },
      });
      if (key === 'experience' && lookCount > 0) {
        node.append(el('span', { className: 'rh__dot', attrs: { 'aria-label': t('rh.needsLook', { n: lookCount }) } }));
      }
      return node;
    }));
  }
  tabRow.addEventListener('keydown', (event) => {
    const moves = { ArrowLeft: -1, ArrowRight: 1 };
    if (!(event.key in moves)) return;
    event.preventDefault();
    const next = TABS[(TABS.indexOf(tab) + moves[event.key] + TABS.length) % TABS.length];
    show(next).then(() => document.getElementById(`rh-tab-${next}`)?.focus());
  });

  /**
   * Open a tab; `jobId` preselects a job in Make a resume. `fresh` (arriving
   * from another page) drops what was read before: a job saved or a line
   * confirmed elsewhere in the meantime must show here.
   */
  async function show(next = tab, { jobId = null, runId = null, fresh = false } = {}) {
    tab = TABS.includes(next) ? next : 'start';
    const turn = ++shown;
    if (fresh) {
      cache = { jobs: null, runs: null, bases: null, career: null, status: new Map() };
      ws = null;  // the person's name may have changed on My profile
    }
    if (jobId) {
      Object.assign(make, { stage: 'setup', jobId: String(jobId), jd: '', runId: null, error: null });
    }
    paintTabs(lookCount());
    repaint([el('div', { className: 'sk sk--block' })]);
    try {
      await boot();
    } catch (error) {
      if (turn === shown) repaint([unavailable(error)]);
      return;
    }
    if (runId) await openRun(runId);
    if (turn !== shown) return;
    try {
      const painter = {
        start: paintStart, make: paintMake, resumes: paintResumes, experience: paintExperience, tips: paintTips,
      }[tab];
      await painter();
    } catch (error) {
      repaint([el('p', { className: 'state__msg', text: error.userMessage || error.message })]);
    }
    paintTabs(lookCount());
  }

  function unavailable(error) {
    return el('section', { className: 'rh-card rh-empty' }, [
      el('h2', { className: 'rh-h2', text: t('rh.unavailable') }),
      el('p', { className: 'rh-muted', text: t('rh.unavailableHelp') }),
      error && error.status ? el('p', { className: 'rh-faint', text: error.userMessage || '' }) : null,
      button(t('action.tryAgain'), () => { ws = null; show(tab); }, { className: 'btn' }),
    ]);
  }

  const lookCount = () => ((cache.career && cache.career.proposals) || []).length;

  // -- Start -----------------------------------------------------------------

  async function paintStart() {
    const [jobs, made, data] = await Promise.all([savedJobs(), runs(), career()]);
    if (tab !== 'start') return;  // another tab was chosen while this one loaded
    const groups = jobsWithVersions(made);
    // Saved jobs with no version yet. A job with one has its own row below,
    // with "Make another version".
    const withResume = new Set(groups.map((group) => group.jobId).filter(Boolean));
    const waiting = jobs.filter((job) => !withResume.has(job.job_id));
    const confirmed = countConfirmed(data);
    const look = lookCount();
    repaint([el('div', { className: 'rh-grid' }, [
      el('section', { className: 'rh-card rh-hero' }, [
        el('div', { className: 'rh-hero__text' }, [
          el('h2', { className: 'rh-h2 rh-h2--big', text: t('rh.start.title') }),
          el('ol', { className: 'rh-steps' }, [1, 2, 3].map((n) => el('li', { className: 'rh-steps__item' }, [
            el('span', { className: `rh-num rh-num--${n}`, text: String(n) }),
            el('span', { text: t(`rh.start.step${n}`) }),
          ]))),
        ]),
        button(t('rh.makeResume'), () => show('make'), {
          className: 'rh-btn rh-btn--primary rh-btn--big', attrs: { id: 'rh-start-make' },
        }),
      ]),
      el('section', { className: 'rh-card rh-list' }, [
        el('div', { className: 'rh-list__head' }, [
          el('h2', { className: 'rh-h2', text: t('rh.start.waiting') }),
          el('span', { className: 'rh-muted', text: t('rh.start.waitingLede') }),
        ]),
        ...waiting.slice(0, 5).map((job) => el('div', { className: 'rh-row' }, [
          pct(job.match_score),
          el('div', { className: 'rh-row__main' }, [
            el('span', {
              className: 'rh-row__title', text: job.title || t('absent.untitled'), attrs: { title: job.title || '' },
            }),
            el('span', { className: 'rh-row__sub', text: job.company_name || '' }),
          ]),
          button(t('rh.makeOne'), () => show('make', { jobId: job.job_id }), {
            className: 'rh-btn rh-btn--chip rh-btn--small',
          }),
        ])),
        waiting.length
          ? null
          : el('div', { className: 'rh-row rh-row--note' }, [
            el('span', { text: jobs.length ? t('rh.start.allDone') : t('rh.start.noneSaved') }),
            button(t('nav.jobs'), () => onGoJobs(), { className: 'rh-link' }),
          ]),
      ]),
      el('section', { className: 'rh-card rh-list' }, [
        el('div', { className: 'rh-list__head rh-list__head--row' }, [
          el('h2', { className: 'rh-h2', text: t('rh.start.made') }),
          made.length ? button(t('rh.seeAll'), () => show('resumes'), { className: 'rh-link' }) : null,
        ]),
        ...groups.slice(0, 4).map((group) => el('div', { className: 'rh-row' }, [
          el('div', { className: 'rh-row__main' }, [
            el('span', { className: 'rh-row__title', text: group.latest.role || t('absent.untitled') }),
            el('span', {
              className: 'rh-row__sub',
              text: [group.latest.company, versionsLabel(group)].filter(Boolean).join(' · '),
            }),
          ]),
          el('div', { className: 'rh-actions rh-actions--end' }, [
            button(t('rh.ver.openLatest'), () => show('make', { runId: group.latest.id }), { className: 'rh-link' }),
            button(t('rh.ver.another'), () => anotherVersion(group.latest), {
              className: 'rh-btn rh-btn--chip rh-btn--small',
            }),
          ]),
        ])),
        made.length ? null : el('div', {
          className: 'rh-row rh-row--note',
        }, [el('span', { text: t('rh.start.noneMade') })]),
      ]),
      el('section', { className: 'rh-card rh-exp' }, [
        el('div', { className: 'rh-exp__text' }, [
          el('h2', { className: 'rh-h2', text: t('rh.start.experience') }),
          el('span', { className: 'rh-muted', text: look
            ? t('rh.start.expSomeLook', { n: confirmed, look })
            : t('rh.start.expAll', { n: confirmed }) }),
        ]),
        button(look ? t('rh.takeLook') : t('rh.review'), () => show('experience'), {
          className: 'rh-btn rh-btn--chip',
        }),
      ]),
    ])]);
  }

  // -- Make a resume -----------------------------------------------------------

  async function paintMake() {
    if (make.stage === 'loading') return paintLoading();
    if (make.stage === 'result' && make.view) return paintResult();
    return paintSetup();
  }

  async function paintSetup() {
    const [jobs, bs] = await Promise.all([savedJobs(), bases()]);
    let pickable = jobs.slice(0, 6);
    if (make.jobId && !pickable.some((job) => job.job_id === make.jobId)) {
      const chosen = jobs.find((job) => job.job_id === make.jobId)
        || await api.getJob(make.jobId).catch(() => null);
      if (chosen) pickable = [chosen, ...pickable].slice(0, 6);
    }
    if (!make.baseId || !bs.some((b) => b.id === make.baseId)) {
      make.baseId = (bs.find((b) => b.default) || bs[0] || {}).id || null;
    }
    const ready = () => Boolean(make.baseId) && (Boolean(make.jobId) || make.jd.trim().length >= 40);
    const canGo = ready();
    const why = !make.baseId ? t('rh.make.needBase') : t('rh.make.needJob');
    const go = button(t('rh.make.go'), () => startRun(), {
      className: 'rh-btn rh-btn--primary rh-btn--big', attrs: canGo ? { id: 'rh-make-go' } : {
        id: 'rh-make-go', disabled: true,
      },
    });
    const jd = el('textarea', {
      className: 'rh-input rh-jd',
      attrs: { rows: '5', placeholder: t('rh.make.pastePh'), 'aria-label': t('rh.make.pasteLabel') },
      props: { value: make.jd },
      on: {
        input: (event) => {
          make.jd = event.target.value;
          if (make.jd) make.jobId = null;
          go.disabled = !ready();
          reason.hidden = ready();
          for (const node of body.querySelectorAll('.rh-radio--job')) node.setAttribute('aria-checked', 'false');
        },
      },
    });
    const reason = el('span', { className: 'rh-muted', text: why, props: { hidden: canGo } });
    repaint([el('div', { className: 'rh-grid rh-grid--setup' }, [
      el('section', { className: 'rh-card rh-setup' }, [
        el('div', { className: 'rh-field' }, [
          el('span', { className: 'rh-field__label', text: t('rh.make.q1') }),
          el('div', {
            className: 'rh-radios', attrs: { role: 'radiogroup', 'aria-label': t('rh.make.q1') },
          }, pickable.map((job) => {
            const on = make.jobId === job.job_id && !make.jd;
            return button('', () => {
              Object.assign(make, { jobId: job.job_id, jd: '' });
              paintSetup();
            }, {
              className: 'rh-radio rh-radio--job',
              attrs: {
                role: 'radio', 'aria-checked': String(on), 'data-job-id': job.job_id, 'data-k': `job-${job.job_id}`,
              },
            });
          }).map((node, index) => {
            const job = pickable[index];
            node.append(
              el('span', { className: 'rh-radio__ring', attrs: { 'aria-hidden': 'true' } }),
              el('span', { className: 'rh-row__main' }, [
                el('span', { className: 'rh-row__title', text: job.title || t('absent.untitled') }),
                el('span', { className: 'rh-row__sub', text: job.company_name || '' }),
              ]),
              pct(job.match_score),
            );
            return node;
          })),
          pickable.length ? null : el('p', { className: 'rh-muted', text: t('rh.make.noSaved') }),
          el('div', {
            className: 'rh-or', attrs: { 'aria-hidden': 'true' },
          }, [el('span'), t('rh.make.or'), el('span')]),
          jd,
        ]),
        el('div', { className: 'rh-field' }, [
          el('span', { className: 'rh-field__label', text: t('rh.make.q2') }),
          bs.length
            ? el('div', {
              className: 'rh-radios rh-radios--grid', attrs: { role: 'radiogroup', 'aria-label': t('rh.make.q2') },
            }, bs.map((base) => {
              const node = button('', () => { make.baseId = base.id; paintSetup(); }, {
                className: 'rh-radio',
                attrs: { role: 'radio', 'aria-checked': String(make.baseId === base.id), 'data-k': `base-${base.id}` },
              });
              node.append(
                el('span', { className: 'rh-radio__ring', attrs: { 'aria-hidden': 'true' } }),
                el('span', { className: 'rh-row__main' }, [
                  el('span', { className: 'rh-row__title', text: base.name }),
                  el('span', {
                    className: 'rh-row__sub', text: base.default ? t('rh.make.yourDefault') : (base.headline || ''),
                  }),
                ]),
              );
              return node;
            }))
            : noBase(),
        ]),
        el('div', { className: 'rh-field' }, [
          el('span', { className: 'rh-field__label', text: t('rh.make.q3') }),
          toggle('onlyTrue', t('rh.make.onlyTrue'), t('rh.make.onlyTrueHelp')),
          toggle('twoPages', t('rh.make.twoPages'), t('rh.make.twoPagesHelp')),
        ]),
        el('div', { className: 'rh-setup__foot' }, [reason, go]),
        make.error ? el('div', { className: 'rh-error', attrs: { role: 'alert' } }, [
          el('p', { text: make.error }),
          make.unreadable ? el('div', { className: 'rh-actions' }, [
            button(t('rh.make.retry'), () => startRun(), { className: 'rh-btn rh-btn--chip' }),
            button(t('rh.make.changeAd'), changeAd, { className: 'rh-link' }),
          ]) : null,
        ]) : null,
      ]),
      el('aside', { className: 'rh-preview-note' }, [
        el('h2', { className: 'rh-h2', text: t('rh.make.willShow') }),
        el('ul', { className: 'rh-checks' }, [1, 2, 3].map((n) => el('li', {}, [
          el('span', { className: 'rh-check', text: '✓', attrs: { 'aria-hidden': 'true' } }), t(`rh.make.show${n}`),
        ]))),
        el('span', { className: 'rh-faint', text: t('rh.make.neverChanges') }),
      ]),
    ])]);
  }

  function toggle(key, label, help) {
    const node = button('', () => { make[key] = !make[key]; node.setAttribute('aria-checked', String(make[key])); }, {
      className: 'rh-switch', attrs: { role: 'switch', 'aria-checked': String(make[key]) },
    });
    node.append(
      el('span', { className: 'rh-switch__text' }, [
        el('span', { className: 'rh-switch__label', text: label }),
        el('span', { className: 'rh-switch__help', text: help }),
      ]),
      el('span', {
        className: 'rh-switch__track', attrs: { 'aria-hidden': 'true' },
      }, [el('span', { className: 'rh-switch__knob' })]),
    );
    return node;
  }

  function noBase() {
    return el('div', { className: 'rh-nobase' }, [
      el('p', { className: 'rh-muted', text: t('rh.make.noBase') }),
      el('div', { className: 'rh-actions' }, [
        ws && ws.mode === 'profile'
          ? button(t('rh.fromProfile'), async () => {
            try {
              await api.rt('/career/base-resume', { method: 'POST', body: {} });
              cache.bases = null;
              paintSetup();
            } catch (error) {
              toast(error.userMessage || error.message, true);
            }
          }, { className: 'rh-btn rh-btn--chip' })
          : null,
        uploadButton(() => paintSetup()),
      ]),
    ]);
  }

  function uploadButton(after, { big = false } = {}) {
    const input = el('input', {
      attrs: { type: 'file', accept: '.pdf,.docx,.md,.markdown,.txt', hidden: true },
      on: {
        change: async (event) => {
          const file = event.target.files && event.target.files[0];
          if (!file) return;
          try {
            await api.rtUpload(cpath('/resumes/upload'), file);
            cache.bases = null;
            toast(t('rh.uploaded'));
            after();
          } catch (error) {
            toast(error.userMessage || error.message, true);
          }
        },
      },
    });
    const node = big
      ? button('', () => input.click(), { className: 'rh-addcard' })
      : button(t('rh.addFile'), () => input.click(), { className: 'rh-btn rh-btn--chip' });
    if (big) {
      node.append(
        el('span', { className: 'rh-addcard__plus', text: '+', attrs: { 'aria-hidden': 'true' } }),
        el('span', { className: 'rh-addcard__title', text: t('rh.addFile') }),
        el('span', { className: 'rh-faint', text: t('rh.fileKinds') }),
      );
    }
    return el('span', { className: 'rh-upload' }, [node, input]);
  }

  async function startRun() {
    // One click is one version: a second click while starting does nothing.
    if (make.starting) return;
    make.starting = true;
    try {
      await makeVersion();
    } finally {
      make.starting = false;
    }
  }

  async function makeVersion() {
    make.error = null;
    make.unreadable = false;
    let jdText = make.jd.trim();
    let careerJobId = null;
    let target = {};
    if (make.jobId) {
      const job = await api.getJob(make.jobId).catch(() => null);
      if (!job) {
        make.error = t('rh.make.jobGone');
        return paintSetup();
      }
      careerJobId = make.jobId;
      // THE POSTING'S OWN TEXT, AS STORED, and its own title and company. In
      // profile mode the engine reads both from Career Agent and keeps the
      // exact text with the version; without a profile they are sent here.
      jdText = ws.mode === 'profile' ? '' : (job.description || job.description_excerpt || '');
      // Without a profile bridge the engine cannot read the posting itself.
      if (ws.mode !== 'profile') target = { target_title: job.title || '', target_company: job.company_name || '' };
    }
    if ((!careerJobId || ws.mode !== 'profile') && jdText.length < 20) {
      make.error = t('rh.make.adTooShort');
      return paintSetup();
    }
    Object.assign(make, {
      stage: 'loading', loadStep: 0, view: null, draft: null, selected: null, editing: null, dlOpen: false,
    });
    paintLoading();
    try {
      // The confirmed Career Profile, copied into the engine's list first, so
      // the resume is built from today's experience (profile mode only). A
      // failure stops here: a resume from yesterday's list could still carry
      // a line she has since removed.
      if (ws.mode === 'profile') await post('/career/evidence/import');
      const started = await api.rt(cpath('/tailor'), {
        method: 'POST',
        body: {
          jd_text: jdText,
          resume_id: make.baseId,
          ...target,
          career_job_id: ws.mode === 'profile' && careerJobId ? careerJobId : '',
          options: { evidence_only_claims: make.onlyTrue, max_two_pages: make.twoPages, use_llm: false },
        },
      });
      make.runId = started.application_id;
      if (careerJobId && ws.mode !== 'profile') {
        try {
          const known = jobsOfRuns();
          known[make.runId] = careerJobId;
          window.localStorage.setItem(JOB_OF_RUN, JSON.stringify(known));
        } catch {
          /* The resume is made; only its link to the job is not remembered. */
        }
      }
      await poll(make.runId, ++pollToken);
    } catch (error) {
      make.stage = 'setup';
      make.error = error.userMessage || error.message;
      make.unreadable = Boolean(error.unreadable);
      if (tab === 'make') paintSetup();
    }
  }

  async function poll(runId, mine) {
    for (let i = 0; i < 400; i += 1) {
      if (mine !== pollToken) return;
      const answer = await api.rt(cpath(`/applications/${encodeURIComponent(runId)}`));
      const status = answer && answer.status ? answer.status : {};
      if (status.status === 'done') {
        make.loadStep = 3;
        cache.runs = null;
        await openRun(runId, answer);
        if (tab === 'make') paintResult();
        return;
      }
      if (status.status === 'error') {
        // Nothing in the ad could be compared: said plainly, never drawn as "0 of 0".
        const unreadable = status.stage === 'no_requirements';
        throw Object.assign(new Error(t(unreadable ? 'rh.make.noRequirements' : 'rh.make.failed')), { unreadable });
      }
      const stage = String(status.stage || '').toLowerCase();
      make.loadStep = /generat|writ|valid|render|page/.test(stage) ? 2 : /match|evidence|select/.test(stage) ? 1 : 0;
      if (tab === 'make' && make.stage === 'loading') paintLoading();
      await new Promise((resolve) => { setTimeout(resolve, 700); });
    }
    throw new Error(t('rh.make.slow'));
  }

  async function openRun(runId, answer = null) {
    const done = answer || await api.rt(cpath(`/applications/${encodeURIComponent(runId)}`));
    const [draft, made] = await Promise.all([
      api.rt(cpath(`/applications/${encodeURIComponent(runId)}/draft`)),
      runs(),
    ]);
    const row = made.find((r) => r.id === runId) || {};
    const options = (done.view && done.view.options) || {};
    const job = (done.view && done.view.job) || {};
    Object.assign(make, {
      stage: 'result', runId, view: done.view, draft: draft.resume, match: row.match ?? null,
      jobId: row.career_job_id || (job.source && job.source.career_job_id) || null,
      // The version's own settings and, for a pasted ad, its own text: what
      // "Change" and "Make another version" start from.
      onlyTrue: options.evidence_only ?? make.onlyTrue,
      twoPages: options.two_pages ?? make.twoPages,
      jd: row.career_job_id ? '' : (job.ad || ''),
      selected: null, editing: null, resTab: 'asks', error: null, unreadable: false,
    });
    tab = 'make';
  }

  function paintLoading() {
    repaint([el('section', {
      className: 'rh-card rh-loading', attrs: { role: 'status', 'aria-live': 'polite' },
    }, [
      el('h2', { className: 'rh-h2', text: t('rh.load.title') }),
      ...[0, 1, 2].map((n) => {
        const done = make.loadStep > n;
        const current = make.loadStep === n;
        return el('div', { className: `rh-loadstep${done ? ' is-done' : current ? ' is-current' : ''}` }, [
          el('span', {
            className: 'rh-loadstep__icon', text: done ? '✓' : String(n + 1), attrs: { 'aria-hidden': 'true' },
          }),
          el('span', { text: t(`rh.load.step${n + 1}`) }),
        ]);
      }),
      el('span', { className: 'rh-faint', text: t('rh.load.time') }),
    ])]);
  }

  // -- the result ----------------------------------------------------------

  function paintResult() {
    const view = make.view;
    const rows = (view.match && view.match.rows) || [];
    const counts = { have: 0, close: 0, partly: 0, notYet: 0 };
    for (const row of rows) counts[(RESULT_TONE[row.result] || ['notYet'])[0]] += 1;
    const total = rows.length || 1;
    const fit = make.match === null || make.match === undefined ? null : Math.round(Number(make.match) * 100);
    const rules = [make.onlyTrue ? t('rh.res.onlyTrue') : null, make.twoPages ? t('rh.res.twoPages') : null]
      .filter(Boolean).join(' · ');

    const dl = el('div', { className: 'rh-dl' }, [
      button(t('rh.res.saved'), () => show('resumes'), { className: 'rh-btn rh-btn--chip', attrs: { id: 'rh-saved' } }),
      button(`${t('rh.res.download')} ▾`, () => { make.dlOpen = !make.dlOpen; paintResult(); }, {
        className: 'rh-btn rh-btn--primary', attrs: {
          'aria-expanded': String(make.dlOpen), 'aria-haspopup': 'menu', id: 'rh-download',
        },
      }),
      make.dlOpen
        ? el('div', {
          className: 'popover rh-dl__menu',
          on: { keydown: (event) => { if (event.key === 'Escape') { make.dlOpen = false; paintResult(); } } },
        }, ['docx', 'pdf'].map((fmt) => {
          const item = button('', () => download(fmt), {
            className: 'rh-dl__item', attrs: { 'data-format': fmt, 'data-k': `dl-${fmt}` },
          });
          item.append(
            el('span', { className: 'rh-dl__name', text: t(`rh.res.${fmt}`) }),
            el('span', { className: 'rh-dl__help', text: t(`rh.res.${fmt}Help`) }),
          );
          return item;
        }))
        : null,
    ]);

    const fitCard = el('section', { className: 'rh-card rh-fit' }, [
      el('div', { className: 'rh-fit__head' }, [
        fit === null ? null : el('span', { className: 'rh-fit__pct tpill--chip', text: `${fit}%` }),
        el('strong', { className: 'rh-fit__label', text: t('rh.res.coverLabel') }),
      ]),
      el('p', {
        className: 'rh-muted',
        text: t('rh.res.fitText', { have: counts.have, total: rows.length, close: counts.close + counts.partly }),
      }),
      el('div', { className: 'rh-fit__bar', attrs: { 'aria-hidden': 'true' } }, [
        bar(counts.have / total, 'have'),
        bar((counts.close + counts.partly) / total, 'close'),
        bar(counts.notYet / total, 'miss'),
      ]),
      el('div', { className: 'rh-fit__legend' }, [
        legend('have', t('rh.res.nHave', { n: counts.have })),
        legend('close', t('rh.res.nClose', { n: counts.close + counts.partly })),
        legend('miss', t('rh.res.nMiss', { n: counts.notYet })),
      ]),
    ]);

    const resTabs = el('div', {
      className: 'segmented rh-restabs', attrs: { role: 'tablist' },
    }, ['asks', 'fix', 'ad'].map((key) => button(
      t(`rh.res.tab.${key}`), () => { make.resTab = key; paintResult(); },
      {
        className: 'segmented__btn',
        attrs: { role: 'tab', 'aria-selected': String(make.resTab === key), 'data-k': `restab-${key}` },
      },
    )));

    const panel = make.resTab === 'fix' ? fixPanel(view) : make.resTab === 'ad' ? adPanel(view) : asksPanel(rows);
    const group = jobsWithVersions(cache.runs || []).find((g) => g.versions.some((v) => v.id === make.runId));
    const thisRun = group ? group.versions.find((v) => v.id === make.runId) : null;
    const version = thisRun ? group.versions.indexOf(thisRun) + 1 : null;

    repaint([el('div', { className: 'rh-result' }, [
      el('div', { className: 'rh-result__head' }, [
        el('div', { className: 'rh-result__who' }, [
          el('span', { className: 'rh-muted', text: t('rh.res.for') }),
          el('h2', { className: 'rh-h2 rh-h2--title', text: (view.job && view.job.role) || t('absent.untitled') }),
          el('span', { className: 'rh-muted' }, [
            [(view.job && view.job.company) || '', rules].filter(Boolean).join(' · '), ' · ',
            button(t('rh.res.change'), () => { make.stage = 'setup'; paintSetup(); }, { className: 'rh-link' }),
          ]),
          view.assembled_without_ai ? el('span', { className: 'rh-muted rh-mode', text: t('rh.res.noAi') }) : null,
          version ? el('span', { className: 'rh-version' }, [
            el('span', {
              className: 'tpill tpill--m1',
              text: t('rh.ver.of', { n: version, total: group.versions.length }),
            }),
            button(t('rh.ver.another'), () => anotherVersion(thisRun), {
              className: 'rh-link', attrs: { 'data-k': 'another-version' },
            }),
          ]) : null,
        ]),
        dl,
      ]),
      el('div', { className: 'rh-result__cols' }, [
        el('div', { className: 'rh-result__left' }, [
          make.blocked ? blockedCard() : null,
          make.needName && !personName() ? el('p', {
            className: 'rh-error', attrs: { role: 'alert' }, text: t('rh.err.no_name'),
          }) : null,
          rows.length ? fitCard : unreadableCard(), resTabs, panel,
        ].filter(Boolean)),
        paper(view),
      ]),
    ])]);
  }

  /**
   * NEVER "0 OF 0". A version made before the ad could be read has no
   * requirements; it says so, with the two ways on, instead of a 0% fit.
   */
  function unreadableCard() {
    return el('section', { className: 'rh-card rh-fit rh-unreadable', attrs: { role: 'status' } }, [
      el('strong', { className: 'rh-fit__label', text: t('rh.make.noRequirements') }),
      el('p', { className: 'rh-muted', text: t('rh.make.noRequirementsHelp') }),
      el('div', { className: 'rh-actions' }, [
        button(t('rh.make.retry'), () => anotherVersion({ id: make.runId, career_job_id: make.jobId }), {
          className: 'rh-btn rh-btn--chip',
        }),
        button(t('rh.make.changeAd'), changeAd, { className: 'rh-link' }),
      ]),
    ]);
  }

  /** Back to setup with the pasted-ad box in focus. */
  function changeAd() {
    Object.assign(make, { stage: 'setup', jobId: null, error: null, unreadable: false });
    paintSetup().then(() => body.querySelector('.rh-jd')?.focus());
  }

  /** Edited lines a download would have to change, and why: nothing is swapped. */
  function blockedCard() {
    return el('section', { className: 'rh-card rh-error rh-blocked', attrs: { role: 'alert' } }, [
      el('strong', { text: t('rh.err.edits_not_supported') }),
      el('ul', {}, make.blocked.map((line) => el('li', {}, [
        el('span', { text: `“${line.text}”` }),
        line.company ? el('span', { className: 'rh-muted', text: ` · ${line.company}` }) : null,
        (line.why || []).length ? el('span', { className: 'rh-muted', text: ` · ${line.why.join(', ')}` }) : null,
      ]))),
    ]);
  }

  function bar(share, kind) {
    const node = el('span', { className: `rh-fit__seg rh-fit__seg--${kind}` });
    node.style.width = `${Math.round(share * 1000) / 10}%`;
    return node;
  }

  function legend(kind, text) {
    return el('span', { className: 'rh-legend' }, [el('span', {
      className: `rh-legend__dot rh-legend__dot--${kind}`,
    }), text]);
  }

  function asksPanel(rows) {
    const filters = ['all', 'have', 'partly', 'notYet'];
    const keep = (row) => {
      const kind = (RESULT_TONE[row.result] || ['notYet'])[0];
      if (make.askFilter === 'all') return true;
      if (make.askFilter === 'partly') return kind === 'partly' || kind === 'close';
      return kind === make.askFilter;
    };
    return el('section', { className: 'rh-card rh-asks' }, [
      el('div', {
        className: 'rh-chips',
      }, filters.map((key) => button(t(`rh.ask.f.${key}`), () => { make.askFilter = key; paintResult(); }, {
        className: 'quickchip', attrs: { 'aria-pressed': String(make.askFilter === key), 'data-k': `ask-${key}` },
      }))),
      ...rows.filter(keep).map((row, index) => {
        const [kind, tone] = RESULT_TONE[row.result] || ['notYet', 'red'];
        const open = make.showWhere.has(row.requirement);
        return el('div', { className: 'rh-ask', dataset: { index } }, [
          el('span', { className: 'rh-ask__state' }, [el('span', {
            className: `tpill tpill--${tone}`, text: t(`rh.ask.${kind}`),
          })]),
          el('div', { className: 'rh-ask__body' }, [
            el('span', { className: 'rh-ask__text' }, [
              row.requirement,
              /prefer/i.test(row.kind || '') ? el('span', { className: 'rh-ask__nice', text: t('rh.ask.nice') }) : null,
            ]),
            button(open ? t('rh.ask.hideWhere') : t('rh.ask.showWhere'), () => {
              if (open) make.showWhere.delete(row.requirement); else make.showWhere.add(row.requirement);
              paintResult();
            }, { className: 'rh-link' }),
            open
              ? el('div', { className: 'rh-where' }, [
                ...(row.backed_by || []).map((b) => el('span', {}, [
                  b.company ? el('strong', { text: b.company }) : null, b.company ? ' · ' : null, b.text,
                ])),
                (row.backed_by || []).length ? null : el('span', { text: t('rh.ask.nothingBacks') }),
                kind === 'notYet' ? button(t('rh.ask.addIt'), () => onGoEvidence(row.requirement), {
                  className: 'rh-link',
                }) : null,
              ])
              : null,
          ]),
        ]);
      }),
      rows.length ? null : el('p', { className: 'rh-muted', text: t('rh.ask.none') }),
    ]);
  }

  /** "Make it better": the engine's own checks, the ones that want a look first. */
  function fixPanel(view) {
    const checks = view.checks || [];
    const warn = checks.filter((c) => c.level !== 'ok' && !make.skipped.has(c.name));
    const good = checks.filter((c) => c.level === 'ok');
    return el('section', { className: 'rh-card rh-fix' }, [
      el('div', {
        className: 'rh-fix__intro rh-muted',
        text: warn.length ? t('rh.fix.intro', { n: warn.length }) : t('rh.fix.none'),
      }),
      ...warn.map((check) => el('div', { className: 'rh-fix__row' }, [
        el('span', { className: 'rh-fix__icon', text: '!', attrs: { 'aria-hidden': 'true' } }),
        el('div', { className: 'rh-ask__body' }, [
          el('span', { className: 'rh-fix__title', text: check.name }),
          check.note ? el('span', { className: 'rh-muted', text: check.note }) : null,
          button(t('rh.fix.leave'), () => { make.skipped.add(check.name); paintResult(); }, {
            className: 'rh-link rh-link--quiet',
          }),
        ]),
      ])),
      good.length
        ? el('button', {
          className: 'rh-fix__good',
          attrs: { type: 'button', 'aria-expanded': String(make.goodOpen), 'data-k': 'good' },
          on: { click: () => { make.goodOpen = !make.goodOpen; paintResult(); } },
        }, [
          el('span', {}, [el('span', {
            className: 'rh-check', text: '✓',
          }), ` ${t('rh.fix.good', { n: good.length })}`]),
          el('span', { className: 'rh-faint', text: make.goodOpen ? '▴' : '▾' }),
        ])
        : null,
      make.goodOpen ? el('ul', { className: 'rh-fix__goodlist' }, good.map((c) => el('li', { text: c.name }))) : null,
    ]);
  }

  function adPanel(view) {
    const job = view.job || {};
    const list = (items, key) => (items && items.length ? el('div', { className: 'rh-adlist' }, [
      el('span', { className: 'rh-kicker', text: t(key) }),
      el('ul', {}, items.map((item) => el('li', { text: String(item) }))),
    ]) : null);
    return el('section', { className: 'rh-card rh-ad' }, [
      el('h2', { className: 'rh-h2', text: job.role || t('absent.untitled') }),
      job.company ? el('span', { className: 'rh-muted', text: job.company }) : null,
      list(job.required, 'rh.ad.need'),
      list(job.preferred, 'rh.ad.nice'),
      list(job.conditions, 'rh.ad.conditions'),
      job.ad ? el('div', { className: 'rh-adtext' }, [
        el('span', { className: 'rh-kicker', text: t('rh.ad.full') }),
        el('div', { className: 'rh-adtext__body', attrs: { tabindex: '0' }, text: job.ad }),
      ]) : null,
      make.jobId ? button(t('rh.ad.openJob'), () => onOpenJob(make.jobId), { className: 'rh-link' }) : null,
    ]);
  }

  /** The resume as a sheet of paper; a click on a line offers Edit, Hide, Why. */
  function paper(view) {
    const resume = make.draft || {};
    const pages = view.pages || {};
    const name = personName();
    const sheet = el('div', { className: 'rh-paper', attrs: { 'aria-label': t('rh.paper.label') } }, [
      name ? el('span', { className: 'rh-paper__name', text: name }) : nameForm(),
      resume.headline ? el('span', { className: 'rh-paper__headline', text: resume.headline }) : null,
      (resume.summary || []).length ? el('span', {
        className: 'rh-paper__section', text: t('rh.paper.summary'),
      }) : null,
      ...(resume.summary || []).map((line) => el('p', { className: 'rh-paper__p', text: line })),
      (resume.experience || []).length ? el('span', {
        className: 'rh-paper__section', text: t('rh.paper.experience'),
      }) : null,
      ...(resume.experience || []).map((group) => el('div', { className: 'rh-paper__group' }, [
        el('div', { className: 'rh-paper__rolerow' }, [
          el('strong', { text: group.title || '' }),
          el('span', { className: 'rh-paper__dates', text: [group.start, group.end].filter(Boolean).join(' - ') }),
        ]),
        el('span', { className: 'rh-paper__company', text: group.company || '' }),
        ...(group.bullets || []).map((b) => bulletNode(b)),
      ])),
      (resume.skills || []).length ? el('span', { className: 'rh-paper__section', text: t('rh.paper.skills') }) : null,
      ...(resume.skills || []).map((g) => el('p', { className: 'rh-paper__p' }, [
        g.group ? el('strong', { text: `${g.group}: ` }) : null, (g.items || []).join(' · '),
      ])),
      (resume.certifications || []).length ? el('span', {
        className: 'rh-paper__section', text: t('rh.paper.certs'),
      }) : null,
      ...(resume.certifications || []).map((c) => el('p', { className: 'rh-paper__p', text: c })),
    ]);
    const estimate = pages.actual || pages.estimate;
    return el('aside', { className: 'rh-result__paper' }, [
      el('div', { className: 'rh-paper__top' }, [
        el('span', { className: 'rh-muted', text: t('rh.paper.click') }),
        estimate
          ? el('span', {
            className: 'tpill tpill--m1',
            text: t(Number(estimate) === 1 ? 'rh.paper.pagesOne' : 'rh.paper.pages', { n: estimate }),
          })
          : null,
      ]),
      sheet,
      el('div', { className: 'rh-paper__foot' }, [
        el('span', { className: 'rh-faint', text: t('rh.paper.onlyThis') }),
        el('div', { className: 'rh-actions' }, [
          button(t('rh.paper.undoAll'), async () => {
            make.draft = (await api.rt(draftPath('/restore'), { method: 'POST', body: {} })).resume;
            paintResult();
          }, { className: 'rh-link rh-link--quiet' }),
          button(t('rh.paper.startOver'), () => { make.stage = 'setup'; paintSetup(); }, { className: 'rh-link' }),
        ]),
      ]),
    ]);
  }

  const draftPath = (rest) => cpath(`/applications/${encodeURIComponent(make.runId)}/draft${rest}`);

  async function edit(payload) {
    const answer = await api.rt(draftPath('/edit'), { method: 'POST', body: payload });
    make.draft = answer.resume;
    if (answer.edit_check && answer.edit_check.ok === false) {
      toast(t('rh.paper.unsupported'), true);
    }
    paintResult();
  }

  function bulletNode(b) {
    const selected = make.selected === b.id;
    if (make.editing === b.id) {
      const area = el('textarea', {
        className: 'rh-paper__edit',
        attrs: { rows: '3', 'aria-label': t('rh.paper.editLabel') },
        props: { value: b.text },
      });
      setTimeout(() => area.focus(), 0);
      return el('div', { className: 'rh-paper__editing' }, [
        area,
        el('div', { className: 'rh-actions rh-actions--end' }, [
          button(t('action.cancel'), () => { make.editing = null; paintResult(); }, { className: 'rh-paper__btn' }),
          button(t('rh.paper.saveLine'), () => {
            make.editing = null;
            edit({ op: 'bullet_text', bullet_id: b.id, text: area.value });
          }, { className: 'rh-paper__btn rh-paper__btn--dark' }),
        ]),
      ]);
    }
    if (b.hidden) {
      return el('div', { className: 'rh-paper__hidden' }, [
        t('rh.paper.hiddenLine'),
        button(t('rh.paper.showAgain'), () => edit({
          op: 'bullet_hide', bullet_id: b.id, hidden: false,
        }), { className: 'rh-paper__btn' }),
      ]);
    }
    const line = button('', () => { make.selected = selected ? null : b.id; paintResult(); }, {
      className: `rh-paper__line${selected ? ' is-selected' : ''}`, attrs: { 'aria-expanded': String(selected) },
    });
    line.append(el('span', {
      className: 'rh-paper__bullet', text: '•', attrs: { 'aria-hidden': 'true' },
    }), el('span', { text: b.text }));
    const why = make.whyOpen.has(b.id);
    return el('div', { className: 'rh-paper__item' }, [
      line,
      selected
        ? el('div', { className: 'rh-paper__tools' }, [
          button(t('rh.paper.edit'), () => { make.editing = b.id; paintResult(); }, { className: 'rh-paper__btn' }),
          button(t('rh.paper.hide'), () => edit({
            op: 'bullet_hide', bullet_id: b.id, hidden: true,
          }), { className: 'rh-paper__btn' }),
          button(why ? t('rh.paper.hideWhy') : t('rh.paper.why'), () => {
            if (why) make.whyOpen.delete(b.id); else make.whyOpen.add(b.id);
            paintResult();
          }, { className: 'rh-paper__btn' }),
        ])
        : null,
      selected && why
        ? el('div', { className: 'rh-paper__why', text: b.supported === false ? t('rh.paper.whyUnsupported')
          : b.edited ? t('rh.paper.whyEdited') : t('rh.paper.whyConfirmed') })
        : null,
    ]);
  }

  /** The person's own name ("" while none is given; never "You" or a profile label). */
  const personName = () => (ws && ws.candidate_name) || '';

  /** Give the name a resume prints, in Career Agent's own candidate row. */
  async function saveName(value) {
    await api.setCandidateName(value);
    ws = null;
    await boot();
    // The engine reads the name with the experience: copy both again.
    if (ws.mode === 'profile') await post('/career/evidence/import');
    make.needName = false;
    paintResult();
  }

  function nameForm() {
    const input = el('input', {
      className: 'rh-input', attrs: { type: 'text', maxlength: '200', autocomplete: 'name',
        'aria-label': t('rh.name.label'), placeholder: t('rh.name.placeholder') },
    });
    const form = el('form', { className: 'rh-nameform' }, [
      el('label', { className: 'rh-field__label', text: t('rh.name.label') }),
      input,
      button(t('rh.name.save'), null, { className: 'rh-btn rh-btn--chip', attrs: { type: 'submit' } }),
    ]);
    form.addEventListener('submit', async (event) => {
      event.preventDefault();
      try {
        await saveName(input.value);
      } catch (error) {
        toast(error.userMessage || error.message, true);
      }
    });
    return form;
  }

  async function download(fmt, runId = make.runId) {
    make.dlOpen = false;
    make.blocked = null;
    try {
      const where = cpath(`/applications/${encodeURIComponent(runId)}/export/${fmt}`);
      const name = await api.rtDownload(where, `resume.${fmt}`);
      toast(t('rh.downloaded', { name }));
    } catch (error) {
      const code = error.detail && error.detail.code;
      // The file is the resume on screen, or nothing: say why, never swap it.
      if (code === 'no_name') make.needName = true;
      else if (code === 'edits_not_supported') make.blocked = (error.detail.params || {}).lines || [];
      else toast(error.status === 501 ? t('rh.pdfUnavailable') : (error.userMessage || error.message), true);
    }
    if (tab === 'make' && make.view) paintResult();
  }

  // -- My resumes ----------------------------------------------------------

  async function paintResumes() {
    const [made, bs] = await Promise.all([runs(), bases()]);
    await Promise.all(made.filter((run) => run.career_job_id && !cache.status.has(run.career_job_id))
      .map((run) => api.getJob(run.career_job_id)
        .then((job) => cache.status.set(run.career_job_id, job.application_status || 'DISCOVERED'))
        .catch(() => null)));
    if (tab !== 'resumes') return;
    repaint([el('div', { className: 'rh-col' }, [
      el('section', { className: 'rh-card rh-list' }, [
        el('div', { className: 'rh-list__head rh-list__head--row' }, [
          el('div', { className: 'rh-list__titles' }, [
            el('h2', { className: 'rh-h2', text: t('rh.resumes.made') }),
            el('span', { className: 'rh-muted', text: t('rh.resumes.madeLede') }),
          ]),
          button(t('rh.makeResume'), () => show('make'), { className: 'rh-btn rh-btn--primary' }),
        ]),
        ...jobsWithVersions(made).flatMap((group) => {
          const run = group.latest;
          const open = openGroups.has(run.id);
          return [
            el('div', { className: 'rh-made' }, [
              el('div', { className: 'rh-row__main' }, [
                el('span', { className: 'rh-row__title', text: run.role || t('absent.untitled') }),
                el('span', {
                  className: 'rh-row__sub', text: [run.company, versionsLabel(group)].filter(Boolean).join(' · '),
                }),
              ]),
              el('span', { className: 'rh-muted', text: run.date ? formatDate(run.date) : '' }),
              fitPill(run),
              statusButton(run),
              el('div', { className: 'rh-actions rh-actions--end' }, [
                button(t('rh.ver.openLatest'), () => show('make', { runId: run.id }), { className: 'rh-link' }),
                button(t('rh.ver.another'), () => anotherVersion(run), { className: 'rh-link' }),
                group.versions.length > 1 ? button(open ? t('rh.ver.hide') : t('rh.ver.see'), () => {
                  if (open) openGroups.delete(run.id); else openGroups.add(run.id);
                  paintResumes();
                }, { className: 'rh-link rh-link--quiet', attrs: { 'aria-expanded': String(open) } }) : null,
              ]),
            ]),
            ...(open ? [...group.versions].reverse().map((version) => el('div', {
              className: 'rh-made rh-made--version',
            }, [
              el('span', {
                className: 'rh-row__title',
                text: t('rh.ver.n', { n: group.versions.indexOf(version) + 1 }),
              }),
              el('span', { className: 'rh-muted', text: version.date ? formatDate(version.date) : '' }),
              fitPill(version),
              el('span'),
              el('div', { className: 'rh-actions rh-actions--end' }, [
                button(t('rh.open'), () => show('make', { runId: version.id }), { className: 'rh-link' }),
                button(t('rh.res.download'), () => download('docx', version.id), {
                  className: 'rh-link rh-link--quiet',
                }),
              ]),
            ])) : []),
          ];
        }),
        made.length ? null : el('div', {
          className: 'rh-row rh-row--note',
        }, [el('span', { text: t('rh.start.noneMade') })]),
      ]),
      el('section', { className: 'rh-bases' }, [
        el('div', { className: 'rh-list__titles' }, [
          el('h2', { className: 'rh-h2', text: t('rh.resumes.bases') }),
          el('span', { className: 'rh-muted', text: t('rh.resumes.basesLede') }),
        ]),
        el('div', { className: 'rh-basegrid' }, [
          ...bs.map((base) => el('div', { className: 'rh-card rh-base' }, [
            el('div', { className: 'rh-base__head' }, [
              el('span', { className: 'rh-base__name', text: base.name }),
              base.default ? el('span', { className: 'tpill tpill--m1', text: t('rh.resumes.default') }) : null,
            ]),
            el('span', { className: 'rh-muted', text: base.headline || '' }),
            el('div', { className: 'rh-base__foot' }, [
              base.default ? null : button(
                t('rh.resumes.useDefault'),
                () => act(() => post(baseUrl(base, '/default'))),
                { className: 'rh-link' },
              ),
              button(t('rh.resumes.copy'), () => act(() => post(baseUrl(base, '/duplicate'))), {
                className: 'rh-link rh-link--quiet',
              }),
              base.default ? null : button(t('rh.resumes.remove'), () => {
                if (!window.confirm(t('rh.resumes.confirmRemove', { name: base.name }))) return;
                act(() => api.rt(cpath(`/resumes/${encodeURIComponent(base.id)}?confirm=true`), { method: 'DELETE' }));
              }, { className: 'rh-link rh-link--quiet' }),
            ]),
          ])),
          uploadButton(() => paintResumes(), { big: true }),
        ]),
        ws && ws.mode === 'profile' && !bs.some((b) => b.id === 'career_agent_profile')
          ? button(t('rh.fromProfile'), () => act(() => api.rt('/career/base-resume', {
            method: 'POST', body: {},
          })), { className: 'rh-link' })
          : null,
      ]),
    ])]);
  }

  //: Jobs in My resumes whose versions are listed (by latest version id).
  const openGroups = new Set();

  function fitPill(run) {
    const fit = run.match === null || run.match === undefined ? null : Math.round(Number(run.match) * 100);
    return fit === null ? el('span') : el('span', { className: 'tpill tpill--chip', text: `${fit}%` });
  }

  const baseUrl = (base, rest) => cpath(`/resumes/${encodeURIComponent(base.id)}${rest}`);
  const post = (path) => api.rt(path, { method: 'POST', body: {} });

  async function act(fn) {
    try {
      await fn();
      cache.bases = null;
      cache.runs = null;
      await (tab === 'resumes' ? paintResumes() : show(tab));
    } catch (error) {
      toast(error.userMessage || error.message, true);
    }
  }

  function statusButton(run) {
    // One tracker: a resume made for a job shows that job's own status in
    // Career Agent, and changing it here changes it there.
    if (run.career_job_id && cache.status.has(run.career_job_id)) {
      const jobId = run.career_job_id;
      const control = el('select', {
        className: 'tpill tpill--chip rh-status select--pill',
        attrs: { 'aria-label': t('rh.resumes.statusOf', { title: run.role || '' }), 'data-k': `st-${run.id}` },
        on: {
          change: async (event) => {
            try {
              await api.patchStatus(jobId, event.target.value);
              cache.status.set(jobId, event.target.value);
              toast(t('rh.resumes.statusSaved', { status: statusLabel(event.target.value) }));
            } catch (error) {
              toast(error.userMessage || error.message, true);
              event.target.value = cache.status.get(jobId);
            }
          },
        },
      }, statusOptions().map((option) => el('option', { text: option.label, attrs: { value: option.value } })));
      control.value = cache.status.get(jobId);
      return control;
    }
    const current = RUN_STATUS.includes(run.status) ? run.status : RUN_STATUS[0];
    const node = button(`${t(`rh.status.${current}`)} ▾`, async () => {
      const next = RUN_STATUS[(RUN_STATUS.indexOf(current) + 1) % RUN_STATUS.length];
      await act(() => api.rt(cpath(`/applications/${encodeURIComponent(run.id)}`), {
        method: 'PATCH', body: { status: next },
      }));
    }, { className: `tpill tpill--${statusTone(current)} rh-status`, attrs: { title: t('rh.resumes.clickStatus') } });
    return node;
  }

  // -- My experience: the Career Profile itself --------------------------------

  let expQuery = '';
  async function paintExperience() {
    const data = await career();
    if (tab !== 'experience') return;
    const experiences = (data && data.experiences) || [];
    const look = lookCount();
    const q = expQuery.trim().toLowerCase();
    const groups = experiences.map((entry) => ({
      entry,
      items: (entry.highlights || []).filter((h) => !q || String(h.text || '').toLowerCase().includes(q)
        || `${entry.title || ''} ${entry.company || ''}`.toLowerCase().includes(q)),
    })).filter((g) => g.items.length || (!q && !(g.entry.highlights || []).length));
    const search = el('input', {
      className: 'rh-input rh-search',
      attrs: { type: 'search', placeholder: t('rh.exp.search'), 'aria-label': t('rh.exp.search') },
      props: { value: expQuery },
      on: {
        input: (event) => {
          expQuery = event.target.value;
          paintExperience().then(() => body.querySelector('.rh-search')?.focus());
        },
      },
    });
    repaint([el('div', { className: 'rh-col' }, [
      el('div', { className: 'rh-exp__bar' }, [
        search,
        button(t('rh.exp.edit'), () => onGoProfile(), { className: 'rh-btn rh-btn--chip' }),
      ]),
      look
        ? el('div', { className: 'rh-look' }, [
          el('span', { text: t('rh.exp.look', { n: look }) }),
          button(t('rh.exp.reviewNow'), () => onGoEvidence(''), { className: 'rh-link' }),
        ])
        : null,
      el('p', { className: 'rh-faint', text: t('rh.exp.oneList') }),
      ...groups.map(({ entry, items }) => el('section', { className: 'rh-card rh-list' }, [
        el('div', { className: 'rh-list__head rh-list__head--row' }, [
          el('div', { className: 'rh-list__titles' }, [
            el('span', { className: 'rh-exp__role', text: entry.title || t('absent.untitled') }),
            el('span', { className: 'rh-muted', text: [entry.company, period(entry)].filter(Boolean).join(' · ') }),
          ]),
          el('span', { className: 'rh-faint', text: t('rh.exp.count', { n: items.length }) }),
        ]),
        ...items.map((h) => experienceRow(h)),
        items.length ? null : el('div', {
          className: 'rh-row rh-row--note',
        }, [el('span', { text: t('rh.exp.empty') })]),
      ])),
      groups.length ? null : el('div', {
        className: 'rh-empty rh-muted', text: q ? t('rh.exp.noMatch') : t('rh.exp.none'),
      }),
    ])]);
  }

  let editingKey = null;
  function experienceRow(h) {
    if (editingKey === h.claim_key) {
      const area = el('textarea', {
        className: 'rh-input', attrs: { rows: '3', 'aria-label': t('rh.paper.editLabel') }, props: { value: h.text },
      });
      return el('div', { className: 'rh-row rh-row--edit' }, [
        area,
        el('div', { className: 'rh-actions rh-actions--end' }, [
          button(t('action.cancel'), () => { editingKey = null; paintExperience(); }, {
            className: 'rh-btn rh-btn--chip rh-btn--small',
          }),
          button(t('rh.exp.save'), async () => {
            try {
              await api.editClaim(h.claim_key, { text: area.value });
              editingKey = null;
              cache.career = null;
              paintExperience();
            } catch (error) {
              toast(error.userMessage || error.message, true);
            }
          }, { className: 'rh-btn rh-btn--primary rh-btn--small' }),
        ]),
      ]);
    }
    return el('div', { className: 'rh-row' }, [
      el('span', { className: 'rh-exptext', text: h.text }),
      el('div', { className: 'rh-actions' }, [
        el('span', { className: 'tpill tpill--m1', text: `✓ ${t('rh.exp.confirmed')}` }),
        button(t('rh.exp.editOne'), () => { editingKey = h.claim_key; paintExperience(); }, { className: 'rh-link' }),
        button(t('rh.exp.remove'), async () => {
          try {
            await api.retireClaim(h.claim_key);
            cache.career = null;
            paintExperience();
            toast(t('rh.exp.removed'), false, {
              label: t('action.undo'),
              run: async () => { await api.confirmClaim(h.claim_key); cache.career = null; paintExperience(); },
            });
          } catch (error) {
            toast(error.userMessage || error.message, true);
          }
        }, { className: 'rh-link rh-link--quiet' }),
      ]),
    ]);
  }

  // -- Tips ----------------------------------------------------------------

  function tipsDone() {
    try {
      return JSON.parse(window.localStorage.getItem(TIP_STORE) || '{}')[api.getLocalProfile() || 'default'] || {};
    } catch {
      return {};
    }
  }
  function setTip(key, value) {
    try {
      const all = JSON.parse(window.localStorage.getItem(TIP_STORE) || '{}');
      const mine = all[api.getLocalProfile() || 'default'] || {};
      mine[key] = value;
      all[api.getLocalProfile() || 'default'] = mine;
      window.localStorage.setItem(TIP_STORE, JSON.stringify(all));
    } catch {
      /* A done mark is a nicety. */
    }
  }

  async function paintTips() {
    const done = tipsDone();
    const name = String((ws && ws.candidate_name) || '').trim();
    const fileName = name ? `${name.replace(/\s+/g, '_')}_Resume.pdf` : '';
    const tip = (key, { sugg = '', how = false } = {}) => {
      const isDone = Boolean(done[key]);
      return el('div', { className: `rh-tip${isDone ? ' is-done' : ''}` }, [
        button(isDone ? '✓' : '', () => { setTip(key, !isDone); paintTips(); }, {
          className: 'rh-tip__ring',
          ariaLabel: t('rh.tip.markDone', { tip: t(`rh.tip.${key}.title`) }),
          attrs: { role: 'checkbox', 'aria-checked': String(isDone), 'data-k': `tip-${key}` },
        }),
        el('div', { className: 'rh-tip__body' }, [
          el('span', { className: 'rh-tip__title', text: t(`rh.tip.${key}.title`) }),
          el('span', { className: 'rh-muted', text: t(`rh.tip.${key}.why`) }),
          !isDone && sugg
            ? el('div', { className: 'rh-tip__sugg' }, [
              el('span', { text: sugg }),
              button(t('rh.tip.copy'), async () => {
                try {
                  await navigator.clipboard.writeText(sugg);
                  toast(t('rh.tip.copied'));
                } catch {
                  toast(t('resume.copyFailed'), true);
                }
              }, { className: 'rh-btn rh-btn--chip rh-btn--small' }),
            ])
            : null,
          !isDone && how ? el('span', { className: 'rh-tip__how', text: t(`rh.tip.${key}.how`) }) : null,
        ]),
      ]);
    };
    const section = (icon, key, keys) => {
      const n = keys.filter(([k]) => done[k]).length;
      return el('section', { className: 'rh-card rh-tips' }, [
        el('div', { className: 'rh-tips__head' }, [
          el('span', {
            className: `rh-tips__icon rh-tips__icon--${key}`, text: icon, attrs: { 'aria-hidden': 'true' },
          }),
          el('div', { className: 'rh-list__titles' }, [
            el('h2', { className: 'rh-h2', text: t(`rh.tips.${key}`) }),
            el('span', { className: 'rh-muted', text: t(`rh.tips.${key}Lede`) }),
          ]),
          el('span', { className: 'tpill tpill--chip', text: t('rh.tips.count', { n, total: keys.length }) }),
        ]),
        ...keys.map(([k, opts]) => tip(k, opts)),
      ]);
    };
    repaint([el('div', { className: 'rh-col rh-col--narrow' }, [
      el('p', { className: 'rh-muted rh-center', text: t('rh.tips.lede') }),
      section('in', 'linkedin', [
        ['li1', {}], ['li2', {}], ['li3', { how: true }], ['li4', {}], ['li5', {}], ['li6', {}],
      ]),
      section('✎', 'resume', [
        ['cv1', {}], ['cv2', {}], ['cv3', {}], ['cv4', {}], ['cv5', { sugg: fileName }], ['cv6', { how: true }],
      ]),
    ])]);
  }

  // -- shared bits -----------------------------------------------------------

  function pct(score) {
    if (score === null || score === undefined) return el('span');
    const tone = matchTone(score);
    return el('span', {
      className: `matchpill matchpill--${tone ? tone.tone : 'm3'} num`, text: `${Math.round(Number(score))}%`,
    });
  }

  /** Whether a resume was made for this Career Agent job (step 3 of Before you apply). */
  async function hasResumeFor(jobId) {
    try {
      await boot();
      return (await runs()).some((run) => run.career_job_id === jobId);
    } catch {
      return false;
    }
  }

  return {
    show,
    hasResumeFor,
    relabel: () => { if (!host.hidden) show(tab); },
  };
}

function countConfirmed(data) {
  return ((data && data.experiences) || []).reduce((sum, e) => sum + (e.highlights || []).length, 0);
}

function period(entry) {
  const start = entry.period_start ? String(entry.period_start).slice(0, 7) : '';
  const end = entry.current_role ? t('rh.exp.now') : (entry.period_end ? String(entry.period_end).slice(0, 7) : '');
  return [start, end].filter(Boolean).join(' - ');
}

function statusTone(status) {
  return { Considering: 'chip', Applied: 'm1', Interviewing: 'blue', Closed: 'm3' }[status] || 'chip';
}

