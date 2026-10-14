/**
 * home.js -- where the job search stands today.
 *
 * The Jobs list used to be the landing page, which answered "which postings
 * exist" to somebody who had not asked it yet. This answers the question a
 * person actually opens the product with.
 *
 * EVERY NUMBER DECLARES WHAT KIND OF NUMBER IT IS. A STOCK is how many things
 * are in a state right now; an EVENT COUNT is how many times something
 * happened since she last marked the list read. Rendering both under one
 * heading is how "5 interviews" comes to mean either five conversations or
 * five moves depending on who wrote the query, so the two are labelled
 * differently and the labels come from the server's own classification.
 *
 * NOTHING HERE RANKS ANYTHING. The two lists are the digest's own sections,
 * ordered by the same deterministic score every other surface uses. A
 * dashboard with its own ordering would be a second opinion nobody could
 * trace, which is what ADR-0004 refuses.
 *
 * AND IT IS NOT AN ANALYTICS DASHBOARD. No sparkline, no percentage, no
 * trend arrow. Those need a series, and this product stores states and
 * events rather than a time series -- a trend drawn from two points would be
 * a decoration that reads as a measurement.
 */

import { scopedKey } from './profile-scope.js';
import { el, button, replace } from './dom.js';
import { toast } from './ui.js';
import { matchTone } from './cards.js';
import { createFirstRun } from './firstrun.js';
import { createSetup, setupPostponed, setupResumeStep } from './setup.js';
import { getLocale, t, tCount } from './i18n.js';
import * as api from './api.js';
import { createProgressView, outcomeText } from './collection.js';
import { driveRescore, rescoreWords } from './recalc.js';

/**
 * The cards, in reading order.
 *
 * `offers` is last and is dropped when empty: a row of zeroes reads as a
 * report about the person rather than about the data, and an offer count of
 * nothing is the normal state of a job search for most of its length.
 */
//: The four steps a job moves through, in order (redesign handoff). Offers
//: joins them only when there is one: a fifth empty step is a promise.
const STEPS = [
  { key: 'new', tone: 'green' },
  // Each patch says BOTH narrowings, so one card's filter never rides into
  // the next card's list.
  { key: 'saved', tone: 'purple', patch: { saved_only: true, status: [] } },
  { key: 'applied', tone: 'blue' },
  { key: 'interviews', tone: 'yellow' },
];

const TIP_KEY = 'careerAgent.homeTip.v1';
const SEEN_KEY = 'careerAgent.homeNewSeen.v1';

/** localStorage can throw; a remembered tip is a nicety, never a fault. */
function stored(key) {
  try {
    return window.localStorage.getItem(scopedKey(key));
  } catch {
    return null;
  }
}

function store(key, value) {
  try {
    if (value === null) window.localStorage.removeItem(scopedKey(key));
    else window.localStorage.setItem(scopedKey(key), value);
  } catch {
    /* nothing to do */
  }
}


/**
 * Where each right-to-work question is answered on the profile page: the
 * field the page opens at. "I don't know" is an answer there too.
 */
const FIELD_FOR_QUESTION = {
  authorization: 'authorized_countries',
  contractor: 'contractor',
  contractor_abroad: 'contractor_abroad',
};

/** What the footer says once a task is CONFIRMED gone (the next read no longer has it). */
const TOAST_FOR_TASK = {
  recalc: 'home.todo.toast.recalc',
  sites: 'home.todo.toast.sites',
  'gap.evidence': 'home.todo.toast.evidence',
  'gap.compensation': 'home.todo.toast.compensation',
};

/**
 * What became of a source that needed attention. RECOVERED states say it
 * works again; IN_FLIGHT ones are not an answer yet. Anything else (paused,
 * blocked, never started, or no longer listed) took it OUT of collection,
 * which fixes nothing and is never announced as fixed.
 */
const SOURCE_RECOVERED = new Set(['COMPLETE', 'PARTIAL', 'DUE']);
const SOURCE_IN_FLIGHT = new Set(['RUNNING', 'QUEUED']);

/** Which card of the guided setup answers each gap `home.profile_gaps` names. */
const SETUP_FOR_GAP = {
  work: 'work',
  residence: 'home',
  hiring_scopes: 'hire',
  compensation: 'pay',
};

/** The first card that answers one of these gaps, or the start. */
function firstSetupCard(gaps) {
  const gap = gaps.find((name) => SETUP_FOR_GAP[name]);
  return gap ? SETUP_FOR_GAP[gap] : 'welcome';
}

export function createHome({
  onOpenJob = null, onGoTo = null, onSetupShown = null, collection = null,
  sitesNeedingAttention = () => 0, onFixSites = null,
} = {}) {
  const root = el('div', { className: 'home' });
  // THE GUIDED SETUP, one question per card. It REPLACES this page on a fresh
  // install -- where six steps, four zeroes and a second list of the same gaps
  // used to greet somebody who had not answered anything -- and it is what
  // every "answer this" button on this page opens, at the right card.
  // Leaving it ("Do this later", "Go to Home") comes back here.
  const setup = createSetup({
    onExit: () => {
      showingSetup = false;
      load();
    },
    onGoTo: (page) => onGoTo && onGoTo(page),
    collection,
    onStep: () => onSetupShown && onSetupShown(showingSetup),
  });
  //: The card to open at, when something asked for the setup explicitly.
  let setupAt = null;
  let showingSetup = false;
  //: Whether the setup has already opened BY ITSELF during this page session.
  //: It offers itself once; coming back to Home after leaving it -- or after
  //: finding jobs from its last card -- must not start it over.
  let autoOpened = false;

  // THE SIX STEPS OF A FIRST RUN, above everything else on this page.
  //
  // Here rather than on a page of its own, and above the metrics rather than
  // below them, because on a fresh install every metric is zero and a screen
  // of six zeroes is not an answer to "what do I do now". It is built once and
  // drawn into the section each render, exactly like `ask`.
  //
  // It stays visible while ANY step is outstanding, not only while the install
  // is brand new: somebody who imported a CV in January and never reviewed it
  // still has a first thing to do, and hiding the list the moment the product
  // had one fact about them would be the product deciding they were finished.
  const firstRun = createFirstRun({
    onGoTo: (page) => onGoTo && onGoTo(page),
    onSetup: (step) => openSetup(step),
    // Importing documents or answering the career question changes what this
    // page can say, so the page that listed the gaps re-reads them.
    onChanged: () => load(),
  });
  let token = 0;
  //: The last thing the server said, kept so a language change can repaint
  //: without asking again. Home is not driven by the store, so the `store.set
  //: ({})` that repaints every other surface on a locale switch reaches
  //: nothing here -- and the whole page stayed in English while the
  //: navigation above it moved.
  let payload = null;
  // "Small things to do" across reads: which tasks the last read listed, so a
  // task that is gone from the next one is said to be done; and the footer
  // line that says so, until it expires.
  // `todoSeen` remembers the profile too: a task gone because another
  // profile answered is not a task done.
  let todoSeen = null;
  let todoToast = null;
  let todoSeq = 0;
  // The card on screen, so the sources' status can redraw it alone; and the
  // recalculation row of the current read, reused by such a redraw so a run
  // it follows keeps its progress (and its one poller).
  let todoCard = null;
  let recalcRow = null;
  let recalcFor = null;
  // The sources that needed attention, per profile, and the ones whose
  // answer is still running: what "sites working again" is checked against.
  let sitesWatch = null;

  /**
   * Draw Home.
   *
   * `arrival` is true when the person navigated here. It starts a new visit
   * for the setup list, which is what decides that a step finished before
   * they arrived is no longer drawn as an open task.
   *
   * THE SETUP IS NOT RESTARTED BY COMING BACK. Leaving Home in the middle of
   * the guided setup -- or on its last card while jobs are being found -- and
   * returning shows the same card, with the run still drawn from the app's
   * one watcher. It used to be rebuilt from the start of Home, which made a
   * run that was still going look as if it had stopped.
   */
  async function load({ arrival = false } = {}) {
    if (arrival) firstRun.beginVisit();
    if (showingSetup && setupAt === null && !setup.finished()) {
      if (onSetupShown) onSetupShown(true);
      return;
    }
    const mine = ++token;
    setup.stop();
    root.dataset.view = 'loading';
    replace(root, [el('div', { className: 'sk sk--block' })]);
    try {
      payload = await api.getHome();
      if (mine !== token) return;
      // The first-run state BEFORE the first paint, so the list is never drawn
      // empty and then filled in -- a step row appearing a moment later reads
      // as something having just happened.
      await firstRun.load();
      if (mine !== token) return;
      // A fresh install -- nothing confirmed and no search described -- opens
      // on the guided setup, unless the person chose to do it later. Asked for
      // explicitly, it opens at the card that was asked for.
      // LEFT OPEN, NOT LEFT: a reload, a closed tab or a restart in the
      // middle of the setup comes back to the card that was open. "Do this
      // later" is the way out, and it forgets the position.
      const resume = !autoOpened && !setupPostponed() ? setupResumeStep() : null;
      if (setupAt !== null || resume || (!autoOpened && firstRun.isFresh() && !setupPostponed())) {
        autoOpened = true;
        const at = setupAt ?? resume;
        setupAt = null;
        showingSetup = true;
        if (onSetupShown) onSetupShown(true);
        root.dataset.view = 'setup';
        replace(root, [setup.root]);
        await setup.open(at);
        return;
      }
      showingSetup = false;
      if (onSetupShown) onSetupShown(false, firstRun.isFresh());
      // Which of its two faces Home is showing, for anything that waits on it.
      root.dataset.view = 'dashboard';
      replace(root, render(payload));
    } catch (error) {
      if (mine !== token) return;
      replace(root, [
        el('p', { className: 'state__msg', text: error.userMessage || error.message }),
        button(t('action.retry'), () => load(), { className: 'btn' }),
      ]);
    }
  }

  /** Open the guided setup, at one card or from the start. */
  function openSetup(step = 'welcome') {
    setupAt = step;
    load();
  }

  function render(payload) {
    if (firstRun.scoredCount() === 0) {
      return [
        howItWorks(), collectionCard(), startHere(),
        firstRun.outstanding() ? null : completeProfile(payload),
      ].filter(Boolean);
    }
    // The lists with something in them; an empty one only when all are.
    const filled = payload.sections.filter((data) => data.items.length);
    const lists = filled.length ? filled : payload.sections.slice(0, 1);
    // V3: the next step and the lists on the left; the progress path and
    // the small things to do on the right.
    return [
      howItWorks(), collectionCard(), startHere(),
      el('div', { className: 'home__cols' }, [
        el('div', { className: 'home__main' }, [nextStep(payload), ...lists.map((d) => section(d, payload))]),
        el('div', { className: 'home__side' }, [progress(payload), todos(payload)]),
      ]),
    ].filter(Boolean);
  }

  /** "New here? Here's how it works": three sentences, put away for good. */
  function howItWorks() {
    if (stored(TIP_KEY) === 'done') return null;
    const steps = [1, 2, 3].map((n) => el('li', { className: 'home__tipstep' }, [
      el('span', { className: 'home__tipnum num', text: String(n), attrs: { 'aria-hidden': 'true' } }),
      el('span', { className: 'home__tiptext' }, [
        el('strong', { text: t(`home.tip.${n}.title`) }),
        el('span', { text: t(`home.tip.${n}.body`) }),
      ]),
    ]));
    return el('section', { className: 'home__tip', attrs: { 'aria-labelledby': 'home-tip-head' } }, [
      el('div', { className: 'home__tiphead' }, [
        el('h2', { className: 'home__tiptitle', attrs: { id: 'home-tip-head' }, text: t('home.tip.title') }),
        el('span', { className: 'home__tipactions' }, [
          button(t('home.tip.setup'), () => openSetup('welcome'), { className: 'home__tipgo' }),
          button(t('home.tip.gotIt'), () => {
            store(TIP_KEY, 'done');
            load();
          }, { className: 'home__tipok' }),
        ]),
      ]),
      el('ol', { className: 'home__tipsteps' }, steps),
    ]);
  }

  /**
   * "N new jobs found for you": the latest refresh's own count. Hiding it
   * remembers THAT run, in this browser only, so the next run shows again.
   * Nothing is marked read anywhere.
   */
  /**
   * "Your next step": one thing to do, chosen from the counts alone (an
   * interview, then new jobs not yet seen, then saved jobs, then browsing).
   * It names no job it was not given.
   */
  function nextStep(payload) {
    const byKey = new Map((payload.metrics || []).map((metric) => [metric.key, metric]));
    const count = (key) => Number((byKey.get(key) || {}).value) || 0;
    const run = payload.latest_refresh_run ? String(payload.latest_refresh_run) : null;
    const unseen = run && stored(SEEN_KEY) !== run ? count('new') : 0;
    const interviews = byKey.get('interviews');
    const offers = byKey.get('offers');
    let step = { key: 'browse', tone: 'green', n: 0, go: () => onGoTo('jobs') };
    if (count('offers') && offers.statuses.length) {
      step = { key: 'offer', tone: 'lav', n: count('offers'),
        go: () => onGoTo('jobs', { status: offers.statuses, saved_only: false }) };
    } else if (count('interviews') && interviews.statuses.length) {
      step = { key: 'interview', tone: 'lav', n: count('interviews'),
        go: () => onGoTo('jobs', { status: interviews.statuses, saved_only: false }) };
    } else if (unseen) {
      step = { key: 'new', tone: 'green', n: unseen, go: () => onGoTo('jobs'), seen: run };
    } else if (count('saved')) {
      step = { key: 'saved', tone: 'lav', n: count('saved'), go: () => onGoTo('jobs', STEPS[1].patch) };
    }
    const n = step.n.toLocaleString(getLocale());
    return el('section', {
      className: `home__next home__next--${step.tone}`, attrs: { 'aria-labelledby': 'home-next-head' },
    }, [
      el('span', { className: 'home__kicker', text: t('home.ns.kicker') }),
      el('div', { className: 'home__nextrow' }, [
        el('div', { className: 'home__nexttext' }, [
          el('h2', { className: 'home__nexttitle', attrs: { id: 'home-next-head' },
            text: tCount(`home.ns.${step.key}.title`, { n }) }),
          el('p', { className: 'home__nextbody', text: t(`home.ns.${step.key}.body`) }),
        ]),
        el('div', { className: 'home__nextactions' }, [
          onGoTo ? button(t(`home.ns.${step.key}.go`), step.go, { className: 'home__nextgo' }) : null,
          step.seen ? button(t('home.newSeen'), () => markSeen(step.seen), { className: 'home__nextalt' }) : null,
        ].filter(Boolean)),
      ]),
    ]);
  }

  function markSeen(run) {
    const before = stored(SEEN_KEY);
    store(SEEN_KEY, run);
    load();
    toast(t('home.newSeenDone'), {
      undo: () => {
        store(SEEN_KEY, before);
        load();
      },
    });
  }

  /**
   * "Small things to do": scores to recalculate, a job site to fix, and the
   * answers still missing. Every row is an action that exists right now;
   * "Nothing to do right now" means exactly that. Preferred markets are
   * optional and never a row: empty means search everywhere.
   */
  /** The job-site task: the source by name, what happened, and its detail. */
  function sitesTask(task, sites, failed) {
    const ids = sites.attention || [];
    if (failed === 1 && ids.length === 1) {
      const id = ids[0];
      const name = (sites.names && sites.names[id]) || id;
      const stale = (sites.states || {})[id] === 'STALE';
      return task('sites', t('home.todo.siteNamed', { name }),
        t(stale ? 'home.todo.siteStaleHelp' : 'home.todo.siteFailedHelp'), () => onFixSites(id));
    }
    return task('sites', tCount('home.todo.sites', { n: failed }), t('home.todo.sitesHelp'),
      () => onFixSites(null));
  }

  function todos(payload) {
    // The sources' status arrives on its own, usually after Home. It counts
    // only when it is known and belongs to the profile this read is for.
    const profile = payload.profile ?? null;
    const sites = sitesNeedingAttention() || {};
    const sitesKnown = sites.failed !== null && sites.failed !== undefined
      && (sites.profile ?? null) === profile;
    const failed = sitesKnown ? Number(sites.failed) || 0 : 0;
    // Each task is ONE button: title, a short line under it, a chevron. The
    // line is named by `aria-describedby`, so the button's name is the title.
    const task = (id, title, desc, onClick, { emphasis = false } = {}) => {
      const descId = `home-todo-desc-${(todoSeq += 1)}`;
      const help = el('span', {
        className: `home__todohelp${emphasis ? ' home__todohelp--amber' : ''}`, attrs: { id: descId }, text: desc,
      });
      const button = el('button', {
        className: 'home__todo', attrs: { type: 'button', 'aria-describedby': descId, 'data-task': id },
        on: onClick ? { click: onClick } : {},
      }, [
        el('span', { className: 'home__todotext' }, [el('span', { className: 'home__todotitle', text: title }), help]),
        el('span', { className: 'home__todochev', attrs: { 'aria-hidden': 'true' }, text: '\u203a' }),
      ]);
      return { id, li: el('li', {}, [button]), button, help };
    };
    const scores = payload.scores || {};
    const outdated = Number(scores.outdated) || 0;
    const idleScores = outdated > 0
      ? tCount('home.todo.scoresHelp', { n: outdated.toLocaleString(getLocale()) })
      : t('home.todo.scoresWorking');
    const scoreWords = rescoreWords({
      starting: 'home.todo.scoresStarting',
      progress: 'home.todo.scoresProgress',
      working: 'home.todo.scoresWorking',
      done: 'home.todo.scoresChecking',
    });
    // The recalculation row IS the shared Recalculate flow: a click starts the
    // targeted pass, a run already going is followed, a second start is
    // refused. It leaves the list only when a fresh read says nothing is
    // outdated; the other rows stay usable meanwhile.
    const recalc = recalcFor === payload ? recalcRow : outdated > 0 || scores.running
      ? (() => {
        const row = task('recalc', t('home.todo.scores'), idleScores, null, { emphasis: true });
        const bar = el('span', { className: 'home__todobar', attrs: { 'aria-hidden': 'true' } });
        row.button.classList.add('home__todo--recalc');
        row.button.append(bar);
        driveRescore(row.button, {
          onDone: () => load(),
          show: (state) => {
            row.button.setAttribute('aria-busy', String(state.busy));
            row.button.classList.toggle('is-running', state.busy || state.phase === 'done');
            row.button.classList.toggle('is-stopped', state.phase === 'failed' || state.phase === 'interrupted');
            row.help.textContent = (scoreWords[state.phase] || (() => idleScores))(state);
            const pct = state.phase === 'done' ? 100
              : state.total ? Math.min(100, Math.round((state.done / state.total) * 100)) : 0;
            bar.style.width = `${pct}%`;
          },
        });
        return row;
      })()
      : null;
    recalcRow = recalc;
    recalcFor = payload;
    const questions = payload.declarations_pending || [];
    const rights = questions.length && onGoTo
      ? task('rights', t('home.todo.rights'), tCount('home.todo.rightsHelp', { n: questions.length }),
        () => onGoTo('profile', null, { field: FIELD_FOR_QUESTION[questions[0]] }))
      : null;
    const tasks = [
      recalc,
      rights,
      failed && onFixSites ? sitesTask(task, sites, failed) : null,
      ...(payload.profile_gaps || []).map((gap) => task(`gap.${gap}`, t(`home.todo.gap.${gap}`),
        t(`home.todo.gapHelp.${gap}`),
        () => (SETUP_FOR_GAP[gap] ? openSetup(SETUP_FOR_GAP[gap]) : onGoTo && onGoTo('profile')))),
    ].filter(Boolean);

    // DONE means confirmed: a task the previous read listed and this one does
    // not, for the same profile. Nothing is removed on a click; a task that
    // APPEARS says nothing; and while the sources' status is not known the
    // sites row is neither there nor gone.
    const now = new Set(tasks.map((x) => x.id));
    const unknown = sitesKnown ? [] : ['sites'];
    const same = Boolean(todoSeen) && todoSeen.profile === profile;
    if (!same) todoToast = null;
    // The sites row has its own rule: gone is not enough, the sources must
    // have RECOVERED (a paused or switched-off source leaves the row too).
    const gone = same
      ? [...todoSeen.ids].filter((id) => id !== 'sites' && !now.has(id) && !unknown.includes(id))
      : [];
    if (sitesKnown && sitesRecovered(profile, sites)) gone.push('sites');
    const carried = same ? unknown.filter((id) => todoSeen.ids.has(id)) : [];
    todoSeen = { profile, ids: new Set([...now, ...carried]) };
    if (gone.length) {
      const last = gone[gone.length - 1];
      todoToast = { text: t(TOAST_FOR_TASK[last] || 'home.todo.toast.saved'), until: Date.now() + 3200 };
    }
    const toastLeft = todoToast ? todoToast.until - Date.now() : 0;
    const toast = toastLeft > 0
      ? el('p', { className: 'home__todofoot home__todofoot--ok', attrs: { role: 'status' },
        text: `\u2713 ${todoToast.text}` })
      : null;
    if (toast) window.setTimeout(() => toast.remove(), toastLeft);

    todoCard = el('section', { className: 'home__card home__todos', attrs: { 'aria-labelledby': 'home-todo-head' } }, [
      el('div', { className: 'home__cardhead' }, [
        el('div', { className: 'home__todohead' }, [
          el('h2', { className: 'home__cardtitle', attrs: { id: 'home-todo-head' }, text: t('home.todo.title') }),
          tasks.length
            ? el('span', { className: 'home__todocount', text: tCount('home.todo.left', { n: tasks.length }) })
            : null,
        ]),
        el('span', { className: 'home__cardsub', text: tasks.length ? t('home.todo.sub') : t('home.todo.subNone') }),
      ]),
      tasks.length ? el('ul', { className: 'home__todolist' }, tasks.map((x) => x.li)) : null,
      toast,
      tasks.length ? null : el('p', { className: 'home__todofoot', text: t('home.todo.none') }),
    ]);
    return todoCard;
  }

  /**
   * Whether the sources that needed attention are now WORKING, with none
   * still needing it and none still running. Updates the watch; another
   * profile's status starts it afresh and announces nothing.
   */
  function sitesRecovered(profile, sites) {
    const states = sites.states || {};
    const attention = new Set(sites.attention || []);
    const before = sitesWatch && sitesWatch.profile === profile ? sitesWatch.ids : new Set();
    let recovered = 0;
    const pending = [];
    for (const id of before) {
      if (attention.has(id)) continue;
      if (SOURCE_RECOVERED.has(states[id])) recovered += 1;
      else if (SOURCE_IN_FLIGHT.has(states[id])) pending.push(id);
    }
    sitesWatch = { profile, ids: new Set([...attention, ...pending]) };
    return recovered > 0 && attention.size === 0 && pending.length === 0;
  }

  /**
   * Redraw "Small things to do" alone, from the read already held: the
   * sources' status moved. No new request for Home; focus stays on the task
   * it was on.
   */
  function refreshTodos() {
    if (!payload || root.dataset.view !== 'dashboard' || !todoCard || !todoCard.isConnected) return;
    const focused = todoCard.contains(document.activeElement)
      ? document.activeElement.getAttribute('data-task') : null;
    const before = todoCard;
    before.replaceWith(todos(payload));
    if (focused) todoCard.querySelector(`[data-task="${focused}"]`)?.focus();
  }

  /**
   * The first-run list, folded away once there is nothing left in it.
   *
   * A `<details>` rather than a banner with a dismiss button: dismissing is a
   * fourth piece of state to store, and the honest version of "I have read
   * this" is simply collapsing it. It starts OPEN while something is
   * outstanding and CLOSED when everything is recorded, so a finished setup is
   * one line rather than a wall, and is still there to be reopened.
   */
  function startHere() {
    // Redrawn on every render, not only on load. `relabel()` re-renders this
    // page to change its language and passes the SAME node back in; without
    // this the six steps kept the words they were first drawn with, and a
    // Portuguese page carried an English checklist.
    firstRun.redraw();
    // SETUP THAT WAS ALREADY FINISHED IS NOT A TASK. Nothing is drawn: the
    // answers are changed from Settings ("Change my answers"), and Home is
    // for the search itself.
    if (!firstRun.visible()) return null;
    const left = firstRun.remaining();
    const title = firstRun.showingAll()
      ? t('firstrun.title')
      : (left ? t('firstrun.finishTitle', { n: left }) : t('firstrun.finished'));
    return el('details', {
      className: 'home__sec home__sec--start',
      props: { open: left > 0 },
    }, [
      el('summary', { className: 'home__sechead' }, [
        el('span', { text: title }),
      ]),
      firstRun.root,
    ]);
  }

  // -------------------------------------------------------------------
  // the collection, drawn from the app's one watcher
  // -------------------------------------------------------------------

  const collectionHost = el('section', {
    className: 'home__sec home__sec--collect',
    attrs: { role: 'status', 'aria-live': 'polite', 'aria-labelledby': 'home-collect-head' },
  });
  const collectionView = collection ? createProgressView(collection, { stopId: 'home-stop' }) : null;
  let collectionShape = null;

  /** The run, if there is one worth mentioning. Updated in place, not rebuilt. */
  function paintCollection(snapshot, reason) {
    if (!collection) return;
    const ended = outcomeText(snapshot);
    collectionHost.hidden = !snapshot.active && !ended && !snapshot.error;
    if (collectionHost.hidden) {
      collectionShape = null;
      return;
    }
    if (snapshot.active) {
      collectionView.update(snapshot);
      if (collectionShape !== 'active') {
        collectionShape = 'active';
        replace(collectionHost, [
          el('h3', { className: 'home__sechead', attrs: { id: 'home-collect-head' }, text: t('setup.ready.finding') }),
          collectionView.root,
          el('p', { className: 'home__lede', text: t('setup.ready.keepUsing') }),
        ]);
      }
      return;
    }
    if (reason === 'tick' && collectionShape === 'ended') return;
    collectionShape = 'ended';
    replace(collectionHost, [
      el('h3', { className: 'home__sechead', attrs: { id: 'home-collect-head' }, text: t('collect.lastRun') }),
      el('p', { className: 'home__lede', text: snapshot.error || ended }),
      el('div', { className: 'home__gapactions' }, [
        snapshot.phase === 'failed' || snapshot.error || !onGoTo
          ? null
          : button(t('setup.ready.see'), () => onGoTo('jobs'), {
            className: 'btn btn--primary', attrs: { id: 'home-see-jobs' },
          }),
        button(t('collect.dismiss'), () => collection.dismiss(), { className: 'btn btn--quiet' }),
      ].filter(Boolean)),
    ]);
  }

  if (collection) collection.subscribe(paintCollection);

  function collectionCard() {
    if (!collection) return null;
    // Redrawn for the language in force; the watcher's state is unchanged.
    collectionShape = null;
    paintCollection(collection.state(), 'update');
    return collectionHost;
  }

  /**
   * "Your progress": the four steps. Each one with a list behind it is a
   * button that opens exactly the population it counted, so a number can be
   * checked rather than believed.
   */
  /** A count that fits the step's circle: whole up to 9,999, then the
   *  locale's own short form ("125K", "125 mil"). A first search makes New
   *  six digits, and "124528" spilled out of a 30px circle. */
  function shortCount(value) {
    const n = Number(value);
    if (!Number.isFinite(n) || Math.abs(n) < 10000) return n.toLocaleString(getLocale());
    return new Intl.NumberFormat(getLocale(), { notation: 'compact', maximumFractionDigits: 0 }).format(n);
  }

  function progress(payload) {
    const byKey = new Map(payload.metrics.map((metric) => [metric.key, metric]));
    const moved = byKey.get('progressed') ? byKey.get('progressed').value : 0;
    const steps = [...STEPS];
    const offers = byKey.get('offers');
    if (offers && offers.value) steps.push({ key: 'offers', tone: 'green' });
    const cards = steps.map((step, index) => {
      const metric = byKey.get(step.key);
      if (!metric) return null;
      const unknown = metric.value === null || metric.value === undefined;
      const label = t(`home.metric.${step.key}`);
      const inner = [
        el('span', {
          className: 'step__value num',
          text: unknown ? '-' : shortCount(metric.value),
          // The exact number, wherever the circle shows the short form.
          attrs: unknown ? {} : { title: Number(metric.value).toLocaleString(getLocale()) },
        }),
        el('span', { className: 'step__label', text: label }),
        el('span', {
          className: 'step__help',
          text: unknown ? t('home.kind.noRefresh') : t(`home.stepHelp.${step.key}`),
        }),
      ];
      // New has no list that reproduces it, so it is not a link.
      const patch = step.patch
        || (metric.statuses.length ? { status: metric.statuses, saved_only: false } : null);
      return el('li', { className: `step step--${step.tone} metric metric--${step.key}` }, [
        onGoTo && patch
          ? button('', () => onGoTo('jobs', patch), {
            className: 'metric__open',
            ariaLabel: t('home.metricOpen', { label }),
          })
          : null,
        ...inner,
      ].filter(Boolean));
    }).filter(Boolean);
    return el('section', {
      className: 'home__card home__progress', attrs: { 'aria-labelledby': 'home-progress-head' },
    }, [
      el('h2', { className: 'home__sectitle', attrs: { id: 'home-progress-head' }, text: t('home.progress') }),
      el('p', { className: 'home__seclede', text: t('home.progressLede') }),
      // Moves since she last marked the list read: an EVENT count, said as a
      // sentence beside the stock counts rather than as a fifth step.
      moved ? el('p', { className: 'home__seclede home__moved', text: t('home.moved', { n: moved }) }) : null,
      el('ul', { className: 'home__metrics home__steps' }, cards),
    ].filter(Boolean));
  }

  /**
   * What the profile is missing, as named facts.
   *
   * Section 20: no "72% complete". A percentage over a profile invents a
   * denominator -- how many facts IS a whole person -- and then reports
   * somebody's career as a progress bar. Each line below names one specific
   * thing and what stops working without it.
   */
  function completeProfile(payload) {
    const gaps = payload.profile_gaps || [];
    if (!gaps.length) return null;
    return el('section', { className: 'home__sec home__sec--complete' }, [
      el('h2', { className: 'home__sectitle', text: t('home.complete') }),
      el('p', { className: 'home__lede', text: t('home.completeLede') }),
      el('ul', { className: 'home__gaps' }, gaps.map((gap) => el('li', {
        className: 'home__gap',
        text: t(`home.gap.${gap}`),
      }))),
      onGoTo
        ? el('div', { className: 'home__gapactions' }, [
          // The first thing offered, because it is the one that does not
          // require already knowing which screen holds which answer.
          button(t('ask.start'), () => openSetup(firstSetupCard(gaps)), {
            className: 'btn btn--primary',
          }),
          button(t('home.openProfile'), () => onGoTo('profile'), { className: 'btn' }),
          button(t('home.openEvidence'), () => onGoTo('evidence'), { className: 'btn' }),
        ])
        : null,
    ].filter(Boolean));
  }

  function section(data, payload = {}) {
    // Scores that are not current yet are said where they are READ: the best
    // matches are ordered by them.
    const outdated = Number((payload.scores || {}).outdated) || 0;
    const stale = data.key === 'best' && outdated > 0
      ? el('p', { className: 'home__stale', attrs: { role: 'note' },
        text: tCount('home.best.stale', { n: outdated.toLocaleString(getLocale()) }) })
      : null;
    return el('section', { className: `home__card home__sec home__sec--${data.key}` }, [
      el('h2', { className: 'home__sectitle' }, [
        el('span', { text: words(data, 'title') }),
        el('span', { className: 'home__count num', text: String(data.count) }),
      ]),
      stale,
      data.items.length
        ? el('ul', { className: 'home__rows' }, data.items.map(row))
        : el('p', { className: 'home__empty', text: t('home.nothing') }),
    ]);
  }

  /** A section heading in the reader's language, falling back to the server's. */
  function words(data, part) {
    const key = `daily.section.${data.key}.${part}`;
    const translated = t(key, { n: 7 });
    return translated === key ? data[part] : translated;
  }

  function row(job) {
    const tone = matchTone(job.match_score);
    return el('li', { className: 'home__row' }, [
      button('', () => onOpenJob && onOpenJob(job.job_id), {
        className: 'home__open',
        ariaLabel: job.title,
      }),
      el('span', {
        className: `home__score num${tone ? ` matchpill matchpill--${tone.tone}` : ''}`,
        text: job.match_score === null || job.match_score === undefined
          ? t('daily.unscored')
          : `${job.match_score}%`,
      }),
      el('span', { className: 'home__what' }, [
        el('span', { className: 'home__jobtitle', text: job.title }),
        el('span', { className: 'home__company', text: job.company_name }),
      ]),
      el('span', { className: 'home__view', text: t('home.viewJob'), attrs: { 'aria-hidden': 'true' } }),
    ]);
  }

  /** Redraw in the reader's language, from what the server already said. */
  function relabel() {
    if (showingSetup) setup.relabel();
    else if (payload) replace(root, render(payload));
  }

  /** Whether Home is itself showing the run, so the page-wide bar need not. */
  function showsCollection() {
    if (!root.isConnected) return false;
    return !showingSetup || setup.atReady();
  }

  /** Whether Home is showing the guided setup rather than the dashboard. */
  function inSetup() {
    return showingSetup;
  }

  /** Called when the person navigates to another page. */
  function leftForAnotherPage() {
    if (showingSetup) setup.forgetPosition();
  }

  /** The page header's "Finish setup later": the flow's one global exit. */
  function leaveSetup() {
    setup.leave();
  }

  return {
    root, load, relabel, openSetup, showsCollection, inSetup, leftForAnotherPage, leaveSetup, refreshTodos,
    offersLater: () => setup.offersLater(),
  };
}
