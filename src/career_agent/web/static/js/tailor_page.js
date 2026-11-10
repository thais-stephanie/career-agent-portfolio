/**
 * tailor_page.js -- "Resume Tailor": the whole way from a job to a resume.
 *
 * Five tabs, as the Resume helper had before it was folded into Resumes:
 *
 *   Start        what it is, saved jobs with no resume, resumes made, evidence waiting
 *   Make         choose a job (or paste an ad), choose the base, check what will be used, make it
 *   My resumes   the Master and other bases, the versions for each job, open and download
 *   Experience   search, confirm and edit the same evidence the profile holds
 *   Tips         a checklist, with notes only where the person's own data says something
 *
 * It adds no engine and no store. A version is made by the existing Tailor
 * (deterministic, no AI) and opens in the existing Editor; the library is the
 * existing library; Experience is the existing career workspace. Opening a tab
 * generates nothing and sends nothing. Pasting an ad starts no collection and
 * asks no model, and the ad is kept as typed, marked as pasted by hand.
 *
 * THE HONESTY RULE IS NOT A SETTING. "Only use what I have really done" is
 * shown, and is always on: every line of a version comes from experience the
 * person confirmed, whatever any control says. Two pages is an aim, not a
 * licence to cut: nothing is dropped to fit.
 */

import { el, button, replace } from './dom.js';
import { t } from './i18n.js';
import * as api from './api.js';
import { formatDate } from './format.js';
import { careerWorkspace } from './career.js';
import { createTips } from './tailor_tips.js';

const TABS = ['start', 'make', 'resumes', 'experience', 'tips'];
const LIST = 8;
const MIN_AD = 20;
/** The job lists: the slowest read here, and the only one a tab can do without. */
const JOBS_SLOW_MS = 8000;
const JOBS_LIMIT_MS = 45000;
/** The library and the career are quick reads; past this they have failed. */
const READ_LIMIT_MS = 20000;
/**
 * The two job lists, as the Jobs page asks them: the "N more if you tick this
 * box" counts (`narrowings`) are never shown here, and counting them was most
 * of the wait (each one counts the whole corpus again).
 */
const SAVED_QUERY = `saved_only=true&group_duplicates=1&limit=${LIST * 3}&narrowings=later`;
const BEST_QUERY = `group_duplicates=1&limit=${LIST}&narrowings=later`;

/** A long quoted ask, cut for reading; the whole of it stays in the tooltip. */
function shorten(text) {
  const clean = String(text).replace(/\s+/g, ' ').trim();
  return clean.length > 150 ? `${clean.slice(0, 147).trimEnd()}...` : clean;
}

function versionsWord(n) {
  return n === 1 ? t('tp.versionsOne') : t('tp.versionsCount', { n });
}

function matchWord(job) {
  return typeof job.match_score === 'number' && job.scored !== false
    ? t('tp.match', { n: Math.round(job.match_score) })
    : null;
}

export function createTailorPage({
  host, onMake, onMakeAi = null, onMakeMaster, onOpenDocument, onGoJobs, onCareerChanged = () => {},
}) {
  let tab = 'start';
  let turn = 0;
  // The Make tab's own state; it survives a switch of tab, not a page load.
  const make = {
    mode: 'job', job: null, ad: { title: '', company: '', text: '' }, baseId: null,
    stage: 'setup', plan: null, failed: null, busy: false,
  };
  const tips = createTips();

  const tabRow = el('div', {
    className: 'tp-tabs segmented', attrs: { role: 'tablist', 'aria-label': t('nav.tailor') },
  });
  const panel = el('div', { className: 'tp-panel', attrs: { role: 'tabpanel', tabindex: '-1' } });
  host.classList.add('tp');
  replace(host, [tabRow, panel]);

  // ------------------------------------------------------------- data
  //
  // THREE READS, EACH ON ITS OWN. A tab draws what does not need them at once
  // and fills each part in when its read answers; a slow or failed read holds
  // only its own part. A read in flight is shared: switching tabs and back
  // asks nothing again. Each has a time limit, then it is cancelled IN THE
  // BROWSER (the server may still finish the work, and remembers its totals),
  // and the part says so with a way to try again -- never an empty list.

  const reads = {
    library: { value: null, error: null, promise: null, controller: null, profile: null },
    jobs: { value: null, error: null, promise: null, controller: null, profile: null },
    career: { value: null, error: null, promise: null, controller: null, profile: null },
  };

  function timedOut(name) {
    return new api.ApiError({
      kind: 'timeout', status: 0, message: t(name === 'jobs' ? 'tp.load.jobsTimeout' : 'tp.load.timeout'),
    });
  }

  /**
   * A read that failed, said as a READ that failed. The general sentence for
   * a server fault speaks of a change not made, which is not what happened
   * here; the server's own detail stays on the error for the console.
   */
  function unreadable(name, error) {
    if (error && (error.kind === 'aborted' || error.status === 409)) return error;
    return new api.ApiError({
      kind: (error && error.kind) || 'http',
      status: (error && error.status) || 0,
      message: t(name === 'jobs' ? 'tp.load.jobsFailed' : 'tp.load.failed'),
      detail: (error && error.detail) || null,
    });
  }

  /** `fetch(signal)` once at a time for `name`, within `limit` ms. */
  function read(name, fetchIt, limit) {
    const slot = reads[name];
    if (slot.promise) return slot.promise;
    const controller = new AbortController();
    const profile = api.getLocalProfile();
    let late = false;
    const timer = window.setTimeout(() => { late = true; controller.abort(); }, limit);
    slot.controller = controller;
    slot.promise = (async () => {
      try {
        const value = await fetchIt(controller.signal);
        // Another profile opened meanwhile: this answer is not hers to show.
        if (!sameProfile(profile, api.getLocalProfile())) {
          throw new api.ApiError({ kind: 'aborted', status: 0, expected: true });
        }
        Object.assign(slot, { value, error: null, profile });
        return value;
      } catch (error) {
        slot.error = late ? timedOut(name) : unreadable(name, error);
        throw slot.error;
      } finally {
        window.clearTimeout(timer);
        slot.promise = null;
        slot.controller = null;
      }
    })();
    return slot.promise;
  }

  /**
   * Whether two profile ids name the same profile. Not known yet (the page
   * learns it a moment after opening) is the profile the server has open:
   * a request without one is answered for it. A real switch reloads the page.
   */
  const sameProfile = (a, b) => !a || !b || a === b;

  /** Kept only for the profile it was read for. */
  const held = (name) => (sameProfile(reads[name].profile, api.getLocalProfile()) ? reads[name].value : null);

  /** The library is small and changes with every resume made: read it each time. */
  function loadLibrary() {
    return read('library', (signal) => api.listResumeDocuments(false, { signal }), READ_LIMIT_MS);
  }

  async function readJobs(signal) {
    const [saved, best] = await Promise.all([
      api.listJobs(SAVED_QUERY, { signal }),
      api.listJobs(BEST_QUERY, { signal }),
    ]);
    const seen = new Set((saved.items || []).map((job) => job.job_id));
    return {
      saved: saved.items || [],
      best: (best.items || []).filter((job) => !seen.has(job.job_id)),
      // Kept WITH the lists it describes, so an answer that is dropped never
      // leaves its token beside older lists.
      freshness: saved.freshness || null,
    };
  }

  /**
   * The job lists. Lists already held are kept while the server's freshness
   * token is the same (the one the Jobs page uses: it moves with every
   * posting, score, save and write), so coming back to a tab reads nothing again.
   */
  function loadJobs() {
    const slot = reads.jobs;
    if (slot.promise) return slot.promise;
    const kept = held('jobs');
    if (!kept || !kept.freshness) return read('jobs', readJobs, JOBS_LIMIT_MS);
    return read('jobs', async (signal) => {
      let now = null;
      try {
        now = (await api.jobsFreshness({ signal })).freshness;
      } catch (error) {
        if (signal.aborted) throw error;
      }
      return now && now === kept.freshness ? kept : readJobs(signal);
    }, JOBS_LIMIT_MS);
  }

  function loadCareer({ again = false } = {}) {
    const kept = held('career');
    if (kept && !again && !reads.career.promise) return Promise.resolve(kept);
    return read('career', (signal) => api.getCareer({ signal }), READ_LIMIT_MS);
  }

  /** Experience statements the person has confirmed, and how many still wait. */
  function evidenceCounts(career) {
    const experiences = (career && career.experiences) || [];
    return {
      confirmed: experiences.reduce((sum, e) => sum + (e.highlights || []).length, 0),
      waiting: experiences.reduce((sum, e) => sum + Number(e.waiting || 0), 0),
    };
  }

  // ------------------------------------------------------------- shell

  function paintTabs(dot = 0) {
    replace(tabRow, TABS.map((key) => {
      const on = key === tab;
      const node = button(t(`tp.tab.${key}`), () => void show(key), {
        className: 'segmented__btn tp-tab',
        attrs: { role: 'tab', 'aria-selected': String(on), id: `tp-tab-${key}`, tabindex: on ? '0' : '-1' },
      });
      if (key === 'experience' && dot > 0) {
        node.append(el('span', { className: 'tp-dot', attrs: { 'aria-label': t('tp.waitingDot', { n: dot }) } }));
      }
      return node;
    }));
  }

  tabRow.addEventListener('keydown', (event) => {
    const moves = { ArrowLeft: -1, ArrowRight: 1 };
    if (!(event.key in moves) && event.key !== 'Home' && event.key !== 'End') return;
    event.preventDefault();
    const next = event.key === 'Home' ? TABS[0]
      : event.key === 'End' ? TABS[TABS.length - 1]
        : TABS[(TABS.indexOf(tab) + moves[event.key] + TABS.length) % TABS.length];
    void show(next).then(() => document.getElementById(`tp-tab-${next}`)?.focus());
  });

  function repaint(children) {
    replace(panel, children);
    panel.setAttribute('aria-labelledby', `tp-tab-${tab}`);
  }

  /** Open a tab. `job` (from the job drawer or Start) preselects it in Make. */
  async function show(next = tab, job = null) {
    if (job && typeof job === 'object' && job.job_id) {
      Object.assign(make, { mode: 'job', job, stage: 'setup', plan: null, failed: null });
      next = 'make';
    }
    tab = TABS.includes(next) ? next : 'start';
    const mine = ++turn;
    // `state` is the tab once EVERY part has answered (or failed); `shell` is
    // the tab as soon as it can be used, with parts still filling in.
    host.dataset.state = 'loading';
    host.dataset.tab = tab;
    delete host.dataset.shell;
    paintTabs(dotCount());
    repaint([el('p', { className: 'tp__lede', text: t('app.loading') })]);
    try {
      const painter = {
        start: paintStart, make: paintMake, resumes: paintResumes, experience: paintExperience, tips: paintTips,
      }[tab];
      await painter(mine);
    } catch (error) {
      if (mine === turn) {
        repaint([
          el('p', { className: 'tp__lede', attrs: { role: 'alert' }, text: failure(error) }),
          button(t('action.tryAgain'), () => void show(tab), { className: 'btn' }),
        ]);
      }
    }
    if (mine !== turn) return;
    host.dataset.shell = tab;
    host.dataset.state = tab;
    try {
      await loadCareer();
    } catch {
      return; // the dot is a hint; Start and Experience say it when the career fails
    }
    if (mine === turn) paintTabs(dotCount());
  }

  function dotCount() {
    return evidenceCounts(held('career')).waiting;
  }

  function failure(error) {
    return (error && error.userMessage) || t('rv.failed');
  }

  /**
   * Draw `node` now -- `header()` and a short "loading" line -- and fill it in
   * when every read in `needs` has answered. A failure or a time-out is said
   * in the part, with Try again, and never drawn as an empty list. Resolves
   * when the part has settled, either way.
   */
  async function fill(mine, node, header, needs, draw, slowNote = null) {
    if (mine !== turn) return;
    node.dataset.load = 'loading';
    node.setAttribute('aria-busy', 'true');
    node.replaceChildren(...header(), el('p', {
      className: 'tp__note tp-part__wait', attrs: { role: 'status' }, text: t('tp.load.part'),
    }));
    const slow = slowNote ? window.setTimeout(() => {
      if (mine === turn && node.dataset.load === 'loading') node.append(slowNote());
    }, JOBS_SLOW_MS) : null;
    try {
      const values = await Promise.all(needs.map((need) => need()));
      if (mine !== turn) return;
      node.replaceChildren(...draw(...values).filter(Boolean));
      node.dataset.load = 'ready';
    } catch (error) {
      if (mine !== turn) return;
      node.dataset.load = 'error';
      node.replaceChildren(
        ...header(),
        el('p', { className: 'tp__alert', attrs: { role: 'alert' }, text: failure(error) }),
        button(t('action.tryAgain'), () => void fill(mine, node, header, needs, draw, slowNote), {
          className: 'btn btn--small',
        }),
      );
    } finally {
      if (slow) window.clearTimeout(slow);
      node.removeAttribute('aria-busy');
    }
  }

  function slowJobs(lead, withLink) {
    return () => el('p', { className: 'tp__note tp-part__slow', attrs: { role: 'status' } }, [
      el('span', { text: t(lead) }),
      withLink ? button(t('tp.load.pasteInstead'), () => void show('make'), {
        className: 'btn btn--quiet btn--small',
      }) : null,
    ].filter(Boolean));
  }

  // ------------------------------------------------------------- Start

  async function paintStart(mine) {
    const steps = [1, 2, 3].map((n) => el('li', { className: 'tp-step' }, [
      el('span', { className: `tp-step__n tp-step__n--${n}`, text: String(n), attrs: { 'aria-hidden': 'true' } }),
      el('span', { text: t(`tp.start.step${n}`) }),
    ]));
    const card = (key, label, extra = '') => el('section', {
      className: `tp__card${extra}`, attrs: { 'aria-labelledby': label }, dataset: { part: key },
    });
    const waitingCard = card('waiting', 'tp-waiting');
    const madeCard = card('made', 'tp-made');
    const evidenceCard = card('evidence', 'tp-evid', ' tp-evidence');
    repaint([el('div', { className: 'tp-grid' }, [
      el('section', { className: 'tp__card tp-hero', attrs: { 'aria-labelledby': 'tp-hero' } }, [
        el('div', { className: 'tp-hero__text' }, [
          el('h2', { className: 'tp__h2 tp__h2--big', attrs: { id: 'tp-hero' }, text: t('tp.start.title') }),
          el('ol', { className: 'tp-steps' }, steps),
        ]),
        button(t('tp.start.go'), () => void show('make'), {
          className: 'btn btn--primary tp-hero__go', attrs: { id: 'tp-start-make' },
        }),
      ]),
      waitingCard, madeCard, evidenceCard,
    ])]);
    host.dataset.shell = 'start';
    const waitingHead = () => [
      el('h2', { className: 'tp__h2', attrs: { id: 'tp-waiting' }, text: t('tp.start.waiting') }),
      el('p', { className: 'tp__lede', text: t('tp.start.waitingLede') }),
    ];
    const madeHead = () => [el('div', { className: 'tp-row tp-row--head' }, [
      el('h2', { className: 'tp__h2', attrs: { id: 'tp-made' }, text: t('tp.start.made') }),
    ])];
    const evidenceHead = () => [el('div', { className: 'tp-evidence__text' }, [
      el('h2', { className: 'tp__h2', attrs: { id: 'tp-evid' }, text: t('tp.start.evidence') }),
    ])];
    await Promise.all([
      // Which saved jobs have no resume needs the library too: a job with a
      // resume is never listed as waiting for one.
      fill(mine, waitingCard, waitingHead, [loadJobs, loadLibrary], (jobs, lib) => {
        const made = new Set((lib.jobs || []).map((g) => g.job_id).filter(Boolean));
        const waiting = jobs.saved.filter((job) => !made.has(job.job_id));
        return [
          ...waitingHead(),
          ...waiting.slice(0, 5).map((job) => el('div', { className: 'tp-row', dataset: { job: job.job_id } }, [
            matchWord(job) ? el('span', { className: 'tp__pill', text: matchWord(job) }) : null,
            el('div', { className: 'tp-row__main' }, [
              el('span', { className: 'tp-row__title', text: job.title || t('absent.untitled') }),
              el('span', { className: 'tp-row__sub', text: job.company_name || '' }),
            ]),
            button(t('tp.start.makeOne'), () => void show('make', job), { className: 'btn btn--small' }),
          ].filter(Boolean))),
          waiting.length ? null : el('div', { className: 'tp-row tp-row--note' }, [
            el('span', { text: jobs.saved.length ? t('tp.start.allDone') : t('tp.start.noneSaved') }),
            button(t('nav.jobs'), () => onGoJobs(), { className: 'btn btn--quiet btn--small' }),
          ]),
        ];
      }, slowJobs('tp.load.jobsSlow', true)),
      fill(mine, madeCard, madeHead, [loadLibrary], (lib) => {
        const versions = (lib.jobs || []).slice(0, 4);
        return [
          el('div', { className: 'tp-row tp-row--head' }, [
            el('h2', { className: 'tp__h2', attrs: { id: 'tp-made' }, text: t('tp.start.made') }),
            versions.length
              ? button(t('tp.start.seeAll'), () => void show('resumes'), { className: 'btn btn--quiet btn--small' })
              : null,
          ].filter(Boolean)),
          ...versions.map((group) => {
            const latest = group.versions[0];
            return el('div', { className: 'tp-row', dataset: { group: group.key } }, [
              el('div', { className: 'tp-row__main' }, [
                el('span', { className: 'tp-row__title', text: group.title || t('absent.untitled') }),
                el('span', {
                  className: 'tp-row__sub',
                  text: [group.company, versionsWord(group.versions.length),
                    group.manual ? t('tp.pasted') : null].filter(Boolean).join(' · '),
                }),
              ]),
              button(t('tp.start.openLatest'), () => onOpenDocument(latest.id), { className: 'btn btn--small' }),
            ]);
          }),
          versions.length ? null : el('p', { className: 'tp__lede', text: t('tp.start.noneMade') }),
        ];
      }),
      fill(mine, evidenceCard, evidenceHead, [() => loadCareer({ again: true })], (career) => {
        const counts = evidenceCounts(career);
        const evidenceText = counts.waiting
          ? t('tp.start.evidenceWaiting', { n: counts.confirmed, waiting: counts.waiting })
          : counts.confirmed
            ? t('tp.start.evidenceAll', { n: counts.confirmed })
            : t('tp.start.evidenceNone');
        return [
          el('div', { className: 'tp-evidence__text' }, [
            el('h2', { className: 'tp__h2', attrs: { id: 'tp-evid' }, text: t('tp.start.evidence') }),
            el('p', { className: 'tp__lede', text: evidenceText }),
          ]),
          button(counts.waiting ? t('tp.start.review') : t('tp.start.seeExperience'),
            () => void show('experience'), { className: 'btn' }),
        ];
      }),
    ]);
  }

  // ------------------------------------------------------------- Make

  function needed() {
    if (make.mode === 'job') return make.job ? null : t('tp.make.need.job');
    return make.ad.title.trim() && make.ad.text.trim().length >= MIN_AD ? null : t('tp.make.need.ad');
  }

  async function paintMake(mine) {
    const lib = await loadLibrary();
    if (mine !== turn) return;
    if (!lib.master && !(lib.others || []).length) {
      repaint([el('section', { className: 'tp__card', attrs: { 'aria-labelledby': 'tp-master' } }, [
        el('h2', { className: 'tp__h2', attrs: { id: 'tp-master' }, text: t('tp.noMaster.head') }),
        el('p', {
          className: 'tp__lede',
          text: make.job ? t('tp.noMaster.forJob', { job: make.job.title }) : t('tp.noMaster.text'),
        }),
        el('div', { className: 'tp__actions' }, [
          button(t('tp.noMaster.go'), () => onMakeMaster(make.job), {
            className: 'btn btn--primary', attrs: { id: 'tp-make-master' },
          }),
        ]),
      ])]);
      return;
    }
    if (make.stage === 'check' && make.plan) {
      repaint([checkView()]);
      return;
    }
    // The ad can be pasted, the base chosen and the rule read while the job
    // list is still being read; only the list of jobs waits for it.
    const view = setupView(lib);
    repaint([view.root]);
    host.dataset.shell = 'make';
    await view.fillJobs(mine);
  }

  function radio(label, sub, on, onPick, extra = null) {
    return el('button', {
      className: `tp-radio${on ? ' is-on' : ''}`,
      attrs: { type: 'button', role: 'radio', 'aria-checked': String(on) },
      on: { click: onPick },
    }, [
      el('span', { className: 'tp-radio__dot', attrs: { 'aria-hidden': 'true' } }),
      el('span', { className: 'tp-radio__text' }, [
        el('span', { className: 'tp-radio__label', text: label }),
        sub ? el('span', { className: 'tp-radio__sub', text: sub }) : null,
      ].filter(Boolean)),
      extra,
    ].filter(Boolean));
  }

  function setupView(lib) {
    const jobGroup = el('div', {
      className: 'tp-radios', attrs: { role: 'radiogroup', 'aria-labelledby': 'tp-make-1' },
      dataset: { part: 'jobs' },
    });
    const adTitle = el('input', {
      className: 'input', attrs: { id: 'tp-ad-title', maxlength: '300', placeholder: t('tp.make.adTitlePh') },
      props: { value: make.ad.title },
    });
    const adCompany = el('input', {
      className: 'input', attrs: { id: 'tp-ad-company', maxlength: '200', placeholder: t('tp.make.adCompanyPh') },
      props: { value: make.ad.company },
    });
    const adText = el('textarea', {
      className: 'input tp-ad', attrs: { id: 'tp-ad-text', rows: '6', placeholder: t('tp.make.adPh') },
      props: { value: make.ad.text },
    });
    const hint = el('span', { className: 'tp__note tp-make__still', attrs: { 'aria-live': 'polite' } });
    const go = button(t('tp.make.check'), () => void check(), {
      className: 'btn btn--primary', attrs: { id: 'tp-check' },
    });

    function sync() {
      const need = needed();
      go.disabled = Boolean(need) || make.busy;
      hint.textContent = need ? t('tp.make.still', { what: need }) : '';
      for (const node of jobGroup.querySelectorAll('.tp-radio')) {
        const on = make.mode === 'job' && make.job && node.dataset.job === make.job.job_id;
        node.classList.toggle('is-on', Boolean(on));
        node.setAttribute('aria-checked', String(Boolean(on)));
      }
    }
    function jobRadio(job) {
      const node = radio(
        job.title || t('absent.untitled'), job.company_name || '',
        make.mode === 'job' && make.job && make.job.job_id === job.job_id,
        () => {
          Object.assign(make, { mode: 'job', job });
          sync();
        },
        matchWord(job) ? el('span', { className: 'tp__pill', text: matchWord(job) }) : null,
      );
      node.dataset.job = job.job_id;
      return node;
    }
    // A job chosen in the drawer or on Start is there at once, list or not.
    const chosenNow = () => (make.job ? [jobRadio(make.job)] : []);
    const fillJobs = async (mine) => {
      await fill(mine, jobGroup, chosenNow, [loadJobs], (jobs) => {
        const choices = [...jobs.saved.slice(0, LIST), ...jobs.best];
        if (make.job && !choices.some((job) => job.job_id === make.job.job_id)) choices.unshift(make.job);
        return choices.length
          ? choices.map(jobRadio)
          : [el('p', { className: 'tp__note', text: t('tp.pick.empty') })];
      }, slowJobs('tp.load.jobsSlowMake', false));
      sync();
    };
    const typed = () => {
      make.ad = { title: adTitle.value, company: adCompany.value, text: adText.value };
      if (adText.value.trim() || adTitle.value.trim()) make.mode = 'ad';
      else if (make.job) make.mode = 'job';
      sync();
    };
    for (const node of [adTitle, adCompany, adText]) node.addEventListener('input', typed);

    // Step 2: the base. The Master by default, or another resume of their own.
    const bases = [lib.master, ...(lib.others || []).filter((d) => !d.archived)].filter(Boolean);
    const baseGroup = el('div', {
      className: 'tp-radios', attrs: { role: 'radiogroup', 'aria-labelledby': 'tp-make-2' },
    });
    const chosenBase = () => make.baseId || (lib.master ? lib.master.id : bases[0].id);
    const paintBases = () => {
      baseGroup.replaceChildren(...bases.map((base) => radio(
        base.kind === 'MASTER' ? t('tp.make.master') : base.title,
        base.kind === 'MASTER'
          ? t('tp.make.masterSub') : t('tp.make.otherSub', { date: formatDate(base.updated_at) }),
        chosenBase() === base.id,
        () => { make.baseId = base.id; paintBases(); },
      )));
    };
    paintBases();
    sync();

    const rules = el('div', { className: 'tp-rules' }, [
      // Always on. Shown so the rule is visible, and not offered as a choice.
      el('div', {
        className: 'tp-rule is-locked',
        attrs: { role: 'switch', 'aria-checked': 'true', 'aria-disabled': 'true', tabindex: '0', id: 'tp-honest' },
      }, [
        el('span', { className: 'tp-rule__text' }, [
          el('span', { className: 'tp-rule__label', text: t('tp.make.honest') }),
          el('span', { className: 'tp-rule__sub', text: t('tp.make.honestSub') }),
        ]),
        el('span', { className: 'tp__pill tp-rule__always', text: t('tp.make.always') }),
        el('span', { className: 'tp-switch is-on', attrs: { 'aria-hidden': 'true' } }, [el('span')]),
      ]),
      el('p', { className: 'tp__note tp-rule__pages', text: t('tp.make.pages') }),
    ]);
    const root = el('div', { className: 'tp-make' }, [
      el('section', { className: 'tp__card tp-make__form', attrs: { 'aria-labelledby': 'tp-make-1' } }, [
        el('h2', { className: 'tp__h2', attrs: { id: 'tp-make-1' }, text: t('tp.make.step1') }),
        jobGroup,
        el('div', { className: 'tp-or' }, [el('span', { text: t('tp.make.or') })]),
        el('div', { className: 'tp-paste' }, [
          el('label', { className: 'field__label', attrs: { for: 'tp-ad-title' }, text: t('tp.make.adTitle') }),
          adTitle,
          el('label', { className: 'field__label', attrs: { for: 'tp-ad-company' }, text: t('tp.make.adCompany') }),
          adCompany,
          el('label', { className: 'field__label', attrs: { for: 'tp-ad-text' }, text: t('tp.make.adText') }),
          adText,
          el('p', { className: 'tp__note', text: t('tp.make.adNote') }),
        ]),
        el('h2', { className: 'tp__h2', attrs: { id: 'tp-make-2' }, text: t('tp.make.step2') }),
        baseGroup,
        el('h2', { className: 'tp__h2', attrs: { id: 'tp-make-3' }, text: t('tp.make.step3') }),
        rules,
        make.failed ? el('p', { className: 'tp__alert', attrs: { role: 'alert' }, text: make.failed }) : null,
        el('div', { className: 'tp-make__go' }, [hint, go]),
      ].filter(Boolean)),
      el('aside', { className: 'tp__card tp-make__aside', attrs: { 'aria-labelledby': 'tp-aside' } }, [
        el('h2', { className: 'tp__h2', attrs: { id: 'tp-aside' }, text: t('tp.make.asideHead') }),
        el('ul', { className: 'tp-ticks' }, [1, 2, 3].map((n) => el('li', {}, [
          el('span', { className: 'tp-ticks__mark', text: '✓', attrs: { 'aria-hidden': 'true' } }),
          el('span', { text: t(`tp.make.aside${n}`) }),
        ]))),
        el('p', { className: 'tp__note', text: t('tp.make.asideNote') }),
      ]),
    ]);
    return { root, fillJobs };
  }

  /** The ad to work from, as the server wants it. */
  function adBody() {
    const base = make.baseId ? { base_id: make.baseId } : {};
    if (make.mode === 'job') return { ...base, job_id: make.job.job_id };
    return {
      ...base, title: make.ad.title.trim(), company: make.ad.company.trim() || null, text: make.ad.text,
    };
  }

  async function check() {
    if (needed() || make.busy) return;
    make.busy = true;
    make.failed = null;
    try {
      make.plan = await api.previewTailor(adBody());
      make.stage = 'check';
    } catch (error) {
      const code = error && error.detail && error.detail.code;
      make.failed = code === 'no_master' ? t('rv.job.needMaster') : t('tp.make.checkFailed');
      make.stage = 'setup';
    }
    make.busy = false;
    void show('make');
  }

  function planList(items, render) {
    return el('ul', { className: 'tp-plan__list' }, items.map((item) => el('li', {}, render(item))));
  }

  function checkView() {
    const plan = make.plan;
    const cov = plan.coverage;
    const what = make.mode === 'job' ? make.job.title : make.ad.title;
    const asks = (list) => (list && list.length
      ? el('span', {
        className: 'tp-plan__asks', text: t('tp.check.answers', { asks: list.map(shorten).join(' / ') }),
        attrs: { title: list.join(' / ') },
      })
      : null);
    const sections = [];
    const add = (key, items, render) => {
      if (!items.length) return;
      sections.push(el('section', { className: 'tp-plan__sec', dataset: { plan: key } }, [
        el('h3', { className: 'tp__h3', text: t(`tp.check.${key}`, { n: items.length }) }),
        planList(items, render),
      ]));
    };
    add('adds', plan.adds, (line) => [el('span', { className: 'tp-plan__text', text: line.text }), asks(line.asks)]);
    add('shows', plan.shows, (line) => [el('span', { className: 'tp-plan__text', text: line.text })]);
    add('skills', plan.skills, (s) => [el('span', { className: 'tp-plan__text', text: s.label }), asks(s.asks)]);
    add('gaps', plan.gaps, (quote) => [el('span', { className: 'tp-plan__text', text: quote })]);
    const nothing = !plan.adds.length && !plan.shows.length && !plan.skills.length;
    const actions = [
      button(t('tp.check.back'), () => { make.stage = 'setup'; void show('make'); }, { className: 'btn' }),
      onMakeAi && make.mode === 'job'
        ? button(t('tp.act.ai'), () => void onMakeAi({ jobId: make.job.job_id }), {
          className: 'btn', attrs: { id: 'tp-adapt-ai' },
        })
        : null,
      button(t('tp.check.make'), () => void makeIt(), { className: 'btn btn--primary', attrs: { id: 'tp-make-it' } }),
    ].filter(Boolean);
    return el('section', { className: 'tp__card tp-check', attrs: { 'aria-labelledby': 'tp-check-h' } }, [
      el('h2', { className: 'tp__h2', attrs: { id: 'tp-check-h' }, text: t('tp.check.head', { job: what }) }),
      el('p', { className: 'tp__lede', text: t('tp.check.from', { base: plan.base.title }) }),
      el('p', {
        className: 'tp-check__cov',
        text: t('tp.check.cov', {
          covered: cov.covered, partly: cov.partly, none: cov.not_found, total: plan.requirements,
        }),
      }),
      nothing ? el('p', { className: 'tp__note', text: t('tp.check.nothing') }) : null,
      ...sections,
      el('p', { className: 'tp__note', text: t('tp.check.promise') }),
      make.failed ? el('p', { className: 'tp__alert', attrs: { role: 'alert' }, text: make.failed }) : null,
      el('div', { className: 'tp__actions' }, actions),
    ].filter(Boolean));
  }

  async function makeIt() {
    const baseId = make.baseId || undefined;
    make.stage = 'setup';
    make.plan = null;
    if (make.mode === 'job') {
      await onMake({ jobId: make.job.job_id, baseId });
    } else {
      await onMake({
        ad: { title: make.ad.title.trim(), company: make.ad.company.trim() || null, text: make.ad.text },
        baseId,
      });
    }
  }

  // ------------------------------------------------------------- My resumes

  function downloads(item) {
    const box = el('div', { className: 'tp-dl', props: { hidden: true } });
    const toggle = button(t('tp.res.downloads'), async () => {
      box.hidden = !box.hidden;
      toggle.setAttribute('aria-expanded', String(!box.hidden));
      if (box.hidden || box.dataset.loaded) return;
      box.dataset.loaded = 'true';
      let files = [];
      try {
        files = (await api.listResumeExports(item.id)).filter((f) => f.available !== false);
      } catch {
        box.replaceChildren(el('p', { className: 'tp__note', text: t('rv.failed') }));
        return;
      }
      box.replaceChildren(...(files.length
        ? files.map((file) => button(
          `${file.format} · ${formatDate(file.created_at)}`,
          () => void api.downloadResumeExport(file.download, `resume.${String(file.format).toLowerCase()}`),
          { className: 'btn btn--small' },
        ))
        : [el('p', { className: 'tp__note', text: t('tp.res.noFiles') })]));
    }, { className: 'btn btn--quiet btn--small', attrs: { 'aria-expanded': 'false' } });
    return [toggle, box];
  }

  function resumeRow(item, title, sub) {
    const [toggle, box] = downloads(item);
    return el('li', { className: 'tp-res', dataset: { document: item.id } }, [
      el('div', { className: 'tp-res__main' }, [
        el('span', { className: 'tp-row__title', text: title }),
        el('span', { className: 'tp-row__sub', text: sub }),
      ]),
      el('div', { className: 'tp-res__acts' }, [
        button(t('tp.res.open'), () => onOpenDocument(item.id), { className: 'btn btn--small' }),
        toggle,
      ]),
      box,
    ]);
  }

  async function paintResumes(mine) {
    const lib = await loadLibrary();
    if (mine !== turn) return;
    const bases = [lib.master, ...(lib.others || [])].filter((d) => d && !d.archived);
    const groups = lib.jobs || [];
    const groupNode = (group) => el('div', { className: 'tp-group', dataset: { group: group.key } }, [
      el('h3', { className: 'tp__h3', text: [group.title, group.company].filter(Boolean).join(' · ') }),
      group.manual ? el('span', { className: 'tp__pill', text: t('tp.pasted') }) : null,
      el('ul', { className: 'tp-reslist' }, group.versions.map((v) => resumeRow(
        v, v.label
          ? t('tp.res.versionLabel', { n: v.version_number, label: v.label })
          : t('tp.res.version', { n: v.version_number }),
        [formatDate(v.updated_at), v.preferred ? t('tp.res.preferred') : null].filter(Boolean).join(' · '),
      ))),
    ].filter(Boolean));
    repaint([el('div', { className: 'tp-grid tp-grid--one' }, [
      el('section', { className: 'tp__card', attrs: { 'aria-labelledby': 'tp-res-jobs' } }, [
        el('div', { className: 'tp-row tp-row--head' }, [
          el('div', {}, [
            el('h2', { className: 'tp__h2', attrs: { id: 'tp-res-jobs' }, text: t('tp.res.made') }),
            el('p', { className: 'tp__lede', text: t('tp.res.madeLede') }),
          ]),
          button(t('tp.start.go'), () => void show('make'), { className: 'btn btn--primary' }),
        ]),
        groups.length
          ? el('div', {}, groups.map(groupNode))
          : el('p', { className: 'tp__lede', text: t('tp.res.noneMade') }),
      ]),
      el('section', { className: 'tp__card', attrs: { 'aria-labelledby': 'tp-res-bases' } }, [
        el('h2', { className: 'tp__h2', attrs: { id: 'tp-res-bases' }, text: t('tp.res.bases') }),
        el('p', { className: 'tp__lede', text: t('tp.res.basesLede') }),
        bases.length
          ? el('ul', { className: 'tp-reslist' }, bases.map((base) => resumeRow(
            base,
            base.kind === 'MASTER' ? t('tp.make.master') : base.title,
            [base.kind === 'MASTER' ? t('tp.res.masterKind') : t('tp.res.otherKind'), formatDate(base.updated_at)]
              .join(' · '),
          )))
          : el('p', { className: 'tp__lede', text: t('tp.res.noBases') }),
        el('p', { className: 'tp__note', text: t('tp.res.formats') }),
      ]),
    ])]);
  }

  // ------------------------------------------------------------- Experience

  async function paintExperience(mine) {
    if (mine !== turn) return;
    // The same career workspace the profile uses: one store of experience.
    const workspace = careerWorkspace({ onChanged: () => { reads.career.value = null; onCareerChanged(); } });
    repaint([el('p', { className: 'tp__lede', text: t('tp.exp.lede') }), workspace]);
  }

  // ------------------------------------------------------------- Tips

  async function paintTips(mine) {
    if (mine !== turn) return;
    repaint([tips.root]);
    await tips.draw();
  }

  return {
    show: (job = null) => show(job ? 'make' : tab, job),
    relabel: () => { if (!host.hidden) void show(tab); },
    /** Another profile or a changed library: read it all again next time. */
    forget() {
      for (const slot of Object.values(reads)) {
        if (slot.controller) slot.controller.abort();
        Object.assign(slot, { value: null, error: null, profile: null, promise: null, controller: null });
      }
    },
  };
}
