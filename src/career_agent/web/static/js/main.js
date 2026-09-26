/**
 * main.js -- bootstrap, wiring and the one render loop.
 *
 * The shape of this file is the argument of the whole frontend:
 *
 *   store  ->  apiQuery(store)  ->  ONE request  ->  ONE response
 *                                                      |
 *                                            +---------+---------+
 *                                            |                   |
 *                                        renderCards         renderTable
 *
 * The view is chosen AFTER the response exists. `apiQuery` cannot see `view`,
 * and the response is cached under the query string, so switching Cards/Table
 * re-renders the same objects instead of asking a second question. Identical
 * filters therefore return identical job ids by construction, not by luck.
 */

import * as api from './api.js';
import { el, button, replace, clear } from './dom.js';
import { toast } from './ui.js';
import {
  createStore, SORTS, VIEW_GROUPING, activeFilterCount, clearedFilters, TRACKED_STATUSES,
  LIST_KEYS, FLAG_KEYS,
} from './state.js';
import { createFilterPanel, renderChips } from './filters.js';
import { renderCards, cardsSkeleton } from './cards.js';
import { renderTable, tableSkeleton, loadVisible } from './table.js';
import { renderKanban, kanbanSkeleton } from './kanban.js';
import { renderPreferences } from './preferences.js';
import { renderSearchSettings } from './search-settings.js';
import { renderAiSettings } from './ai-settings.js';
import { setSearchFitReadiness } from './badges.js';
import { renderProfile } from './profile.js';
import { LOCALES, getLocale, initialLocale, setLocale, t, tState } from './i18n.js';
import { createRetrievalPanel } from './retrieval.js';
import { createCollection, createProgressView, outcomeText } from './collection.js';
import { createDrawer } from './detail.js';
import { createEvidence } from './evidence.js';
import { documentsPage } from './documents.js';
import { evidencePage } from './evidence_page.js';
import { experienceView } from './experience.js';
import { createDaily } from './daily.js';
import { createHome } from './home.js';
import { createSourcesPanel } from './sources.js';
import { createShell } from './shell.js';
import { createLocalProfiles } from './local-profiles.js';
import { humanLabel, statusLabel } from './format.js';

const store = createStore();

//: THE ONE WATCHER of "is Career Agent finding jobs". Every screen that shows
//: a run subscribes to it; none of them polls, and leaving a page never stops
//: a run. See `collection.js`.
const collection = createCollection(api, { disabled: api.MOCK });

const dom = {
  filters: document.getElementById('filters-host'),
  chips: document.getElementById('chipbar'),
  count: document.getElementById('resultcount'),
  hidden: document.getElementById('hiddennotice'),
  revision: document.getElementById('revnotice'),
  healthSummary: document.getElementById('health-summary'),
  healthSummaryText: document.getElementById('health-summary-text'),
  healthMode: document.getElementById('health-mode'),
  list: document.getElementById('list'),
  boardEmpty: document.getElementById('boardempty'),
  pager: document.getElementById('pager'),
  viewCards: document.getElementById('view-cards'),
  viewTable: document.getElementById('view-table'),
  viewKanban: document.getElementById('view-kanban'),
  exportGoodStrong: document.getElementById('export-good-strong'),
  prefsHost: document.getElementById('prefs-host'),
  profileDisclosure: document.getElementById('profile-disclosure'),
  profileHost: document.getElementById('profile-host'),
  localeHost: document.getElementById('locale-host'),
  retrHost: document.getElementById('retr-host'),
  group: document.getElementById('group-duplicates'),
  sort: document.getElementById('sort'),
  direction: document.getElementById('direction'),
  health: document.getElementById('health'),
};

let lastResponse = null;      // the response currently on screen
let lastQueryString = null;   // the query that produced it
let inFlight = 0;
//: Whether a list request is being fetched; see `mergeJob`.
let loading = false;
//: The in-flight save for each job, chained, so saves for one job reach the
//: server in the order they were made and the last one wins. See `queueJobSave`.
const jobSaves = new Map();
let columnVisibility = loadVisible();

const panel = createFilterPanel(store, { searchHost: document.getElementById('toolbar-search') });
dom.filters.appendChild(panel.root);

/**
 * What is true about you, in an overlay of its own.
 *
 * An overlay rather than a rail panel because a CV review is forty proposals
 * each carrying the line it was read from, and in a 280-pixel column the
 * comparison that makes the review trustworthy stops being possible to make at
 * a glance.
 *
 * It is created BEFORE the drawer because the drawer's Prepare tab opens it.
 */
/**
 * The confirmed set or the career's organisation moved, on any of the career
 * pages. Any preparation already on screen was computed against evidence that
 * no longer exists, and the profile's tabs are the same facts drawn another
 * way. Forgetting is enough; each page loads on arrival.
 */
function careerChanged() {
  if (drawer.job) drawer.reloadPreparation();
  profileLoaded = false;
  lastLedger = null;
  careerContextCache = null;
}

// THE LEGACY STATEMENT MANAGER. Every capability it had -- the organising
// workspace, the full statement list with revisions, package review and
// conflict resolution -- is kept, on a secondary page reached from Evidence
// and Documents ("Manage all statements"). It is no longer the career's front
// door: Career Profile, Evidence and Documents are (docs/CAREER_WORKSPACE.md).
//
// NOT A USER SURFACE ANY MORE (2026-09-25). The first real walkthrough showed
// that keeping it one link away brought the whole administrative model back:
// Needs organizing, imported groups, organization history. It is kept only as
// a developer tool, opened with `?debug=statements` in the address and linked
// from nowhere; without that flag it is not even built.
const DEV_STATEMENTS = new URLSearchParams(window.location.search).get('debug') === 'statements';

// THE SCORING VOCABULARY IS NOT A USER SURFACE EITHER (2026-09-25). The phrase
// groups, their reach and the concept review are how the scoring policy is
// tuned, and showing them in Settings made weights and a lexicon look like
// something a person had to manage. The panel is kept as a developer view,
// shown only with `?debug=1` (or `?debug=scoring`) in the address. Nothing
// behind it changed: the configuration files, the /api/preferences and
// /api/search-review endpoints and the CLI still read and write the same data.
const DEV_SCORING = ['1', 'scoring'].includes(new URLSearchParams(window.location.search).get('debug'));
const scoringBlock = document.getElementById('settings-model-block');
if (scoringBlock) scoringBlock.hidden = !DEV_SCORING;
const evidence = DEV_STATEMENTS ? createEvidence({ onChanged: () => careerChanged() }) : null;
if (evidence) document.getElementById('evidence-host').appendChild(evidence.root);

//: Text boxes on Career Evidence the person has typed into. A box that is
//: gone from the page was saved or discarded by the page itself; one still
//: there with text in it is work a redraw would lose.
const evidenceEdits = new Set();
for (const host of [document.getElementById('page-manage'), evidence && evidence.root]) {
  host?.addEventListener('input', (event) => {
    const node = event.target;
    if (node instanceof HTMLTextAreaElement
      || (node instanceof HTMLInputElement && ['text', ''].includes(node.getAttribute('type') || ''))) {
      evidenceEdits.add(node);
    }
  }, true);
}

function evidenceHasUnsavedText() {
  for (const node of [...evidenceEdits]) {
    if (!node.isConnected) evidenceEdits.delete(node);
    else if (node.value.trim()) return true;
  }
  return false;
}

const drawer = createDrawer({
  onStatus: (jobId, status) => changeStatus(jobId, status),  // returns the updated job
  onSave: (jobId, saved) => changeSaved(jobId, saved),
  onNotes: (jobId, notes) => changeNotes(jobId, notes),
  // The drawer confirms before calling this. It is the only path in the
  // interface that can delete the record of having applied: ADR-0012.
  onClearAppliedAt: (jobId) => changeAppliedDate(jobId, null),
  onClosed: () => {
    if (store.get().openJobId) store.set({ openJobId: null }, { history: 'replace' });
  },
  onChanged: (job) => mergeJob(job),
  // Read at open time, never at build time: the health call is in flight while
  // this module is evaluating.
  getOllama: () => ollamaHealth,
  // Somebody said a requirement is answered by experience that is not in
  // her profile. Nothing was claimed by saying so; this is the route to
  // where she can write it down herself -- OR, now, to whatever a staged
  // package is already waiting to be asked about it. The requirement was
  // always passed here and always discarded, so the trip used to end at the
  // top of a long screen with nothing saying what it was for.
  onEvidence: (row) => {
    // Evidence, with the add drawer open and the requirement named in it: the
    // person writes down what they did. Never the old statement manager.
    goTo('evidence');
    evidenceView.add({ requirement: (row && row.label) || '' });
  },
  // Whether Career Agent holds anything about this person's career -- a read
  // document, or evidence waiting or confirmed. Resume Tailor keeps its own
  // store, which this never reads; this only decides whether Tailor is
  // offered as the next step or after the step that gives it something.
  careerContext: () => careerContext(),
  onAddCareer: () => {
    store.set({ openJobId: null });
    goTo('documents');
  },
});
document.getElementById('drawer-host').appendChild(drawer.root);

// The digest, opened from the toolbar. An overlay rather than a fourth view:
// Cards, Table and Board are three renderings of ONE query that the filter
// rail narrows, and the digest is several queries the server composes.
const daily = createDaily({
  onOpenJob: (jobId) => {
    store.set({ openJobId: jobId });
    drawer.open(jobId, document.getElementById('daily-open'));
  },
});
document.getElementById('daily-host').appendChild(daily.root);
document.getElementById('daily-open')
  .addEventListener('click', (event) => daily.open(event.currentTarget));

// =========================================================================
// The five destinations
// =========================================================================
//
// PAGES, not view switches. Cards/Table/Board decide how ONE page draws its
// results; these decide which part of the product is on screen. Mixing them
// in one control made the whole product look like a single screen with a lot
// of switches on it.
//
// The rail belongs to Jobs alone. It filters a list, and a filter panel
// beside a dashboard would be a control that reaches nothing.
const PAGES = {
  home: document.getElementById('page-home'),
  jobs: document.getElementById('page-jobs'),
  applications: document.getElementById('page-jobs'),
  profile: document.getElementById('page-profile'),
  evidence: document.getElementById('page-evidence'),
  documents: document.getElementById('page-documents'),
  // The old statement manager: a developer tool, only with ?debug=statements.
  ...(DEV_STATEMENTS ? { manage: document.getElementById('page-manage') } : {}),
  // Promoted out of the filter rail. Where the postings come from is not a
  // filter, and reaching it meant opening a disclosure inside a panel that
  // only exists on one page.
  settings: document.getElementById('page-settings'),
};

// The rail, the page header and the mobile drawer. See `shell.js` for why the
// header is a contract rather than five headers that happen to look alike.
const shell = createShell();

// -- the career pages -------------------------------------------------------
const evidenceView = evidencePage({
  onImport: () => goTo('documents'),
  onChanged: () => careerChanged(),
});
PAGES.evidence.appendChild(evidenceView);
const documentsView = documentsPage({
  onChanged: () => careerChanged(),
});
PAGES.documents.appendChild(documentsView);

//: The Experience tab's view, kept across profile redraws so edit mode and an
//: open editor survive a language change.
let experienceNode = null;
let profileTabs = null;

/** Open the import review from the profile ("3 details to review"). */
function reviewFromProfile() {
  goTo('documents');
}

/**
 * The header's actions, one primary at most. The profile's primary is Edit
 * profile; Import resume sits beside it as the secondary way in.
 */
function headerAction(page) {
  if (page === 'profile') {
    return el('div', { className: 'pagehead__buttons' }, [
      button(t('profileHead.import'), () => { goTo('documents'); documentsView.choose(); },
        { className: 'btn' }),
      button(t('profileHead.edit'), () => {
        if (profileTabs) profileTabs.show('experience');
        if (experienceNode) experienceNode.startEditing();
      }, { className: 'btn btn--primary' }),
    ]);
  }
  if (page === 'evidence') {
    return button(t('evp.add'), () => evidenceView.add(), { className: 'btn btn--primary' });
  }
  if (page === 'home' && homeHeader === 'setup' && home.offersLater()) {
    // THE ONE GLOBAL EXIT from the guided setup, in its header. What was
    // answered is kept; the flow offers itself again later.
    return button(t('setup.later'), () => home.leaveSetup(), {
      className: 'btn btn--link pagehead__later',
      attrs: { id: 'setup-later' },
    });
  }
  return null;
}

const home = createHome({
  collection,
  onOpenJob: (jobId) => {
    store.set({ openJobId: jobId });
    drawer.open(jobId, document.querySelector('.topnav__link[data-page="home"]'));
  },
  onGoTo: (page, patch) => {
    if (patch) store.set(patch);
    goTo(page);
  },
  // "How is your search going?" is the wrong heading above "Let's set up your
  // job search", so Home says which of the two it is showing.
  onSetupShown: (shown) => {
    homeHeader = shown ? 'setup' : 'home';
    if (currentPage === 'home') shell.setPage(homeHeader, { action: headerAction('home') });
  },
});
PAGES.home.appendChild(home.root);
//: Which header Home wears: its own, or the guided setup's.
let homeHeader = 'home';

let currentPage = 'home';

function goTo(page, { push = true } = {}) {
  if (!PAGES[page]) return;
  currentPage = page;

  for (const [name, node] of Object.entries(PAGES)) {
    if (name === 'applications') continue;   // shares the Jobs container
    node.hidden = !(name === page || (page === 'applications' && name === 'jobs'));
  }
  for (const link of document.querySelectorAll('.topnav__link[data-page]')) {
    const chosen = link.dataset.page === page;
    if (chosen) link.setAttribute('aria-current', 'page');
    else link.removeAttribute('aria-current');
  }
  // The results toolbar is about the job list and is meaningless anywhere
  // else, so it travels with the page rather than sitting above all of them.
  const toolbar = document.querySelector('.topbar__controls');
  if (toolbar) toolbar.dataset.page = page;
  // JOB CONTROLS BELONG TO DISCOVER, and the page says whose they are. The
  // toolbar, the filter panel and the active-filter chips are hidden by one
  // rule on `body[data-page]` (app.css) unless the page is Discover: a list of
  // pages to hide them on missed Documents, and every new page leaked them.
  document.body.dataset.page = page;
  // EVERY page gets the same header shape, filled from one table. Five call
  // sites setting their own is how five screens come to disagree about how
  // tall a header is.
  shell.setPage(page === 'home' ? homeHeader : page, { action: headerAction(page) });

  // The board's empty notice belongs to Applications alone. Discover shares its
  // container and repaints only when its list arrives; clearing it here keeps
  // "No applications tracked yet" from lingering over Discover meanwhile.
  if (page !== 'applications') paintBoardEmpty(false);
  // The hidden-jobs notices belong to Discover alone. Applications shares the
  // container, and "Show them too" over a board of tracked jobs would widen a
  // search nobody is looking at.
  renderHiddenNotice(store.get());

  if (page === 'applications') {
    // The board IS the applications view. Switching to it also narrows to the
    // tracked statuses, exactly as pressing Board does, so the two routes
    // cannot disagree about what an application is.
    store.set({ view: 'kanban', group_duplicates: false, status: [...TRACKED_STATUSES] });
  } else if (page === 'jobs' && store.get().view === 'kanban') {
    store.set({ view: 'cards', group_duplicates: true, status: [] });
  }

  if (page === 'home') home.load({ arrival: true });
  // Walking away from the setup by the navigation is a choice, not an
  // interruption: the next reload goes to Home, not back into the setup.
  else home.leftForAnotherPage();
  // What Career Agent knows about the person's career can change on any page;
  // the drawer asks again on its next open rather than trusting a count from
  // before.
  careerContextCache = null;
  paintCollectBar(collection.state(), 'update');
  if (page === 'profile') loadProfile();
  if (page === 'evidence') void evidenceView.load().catch(() => {});
  if (page === 'documents') void documentsView.load().catch(() => {});
  if (page === 'manage' && evidence) evidence.mount(PAGES.manage);
  // Loaded on arrival rather than on page load: the catalogue answers a
  // question nobody has asked yet, and a list of jobs should not wait on it.
  if (page === 'settings') {
    renderSetupEntry(document.getElementById('settings-setup-host'));
    renderSearchSettings(document.getElementById('search-settings-host'), store);
    sourcesPanel.load();
    renderAiSettings(document.getElementById('ai-settings-host'));
    const model = document.getElementById('settings-model');
    if (DEV_SCORING && model) {
      model.ontoggle = () => { if (model.open) loadPreferences(); };
      if (model.open) loadPreferences();
    }
    loadRetrieval();
  } else {
    stopRetrieval();
  }

  if (push) {
    const url = new URL(window.location.href);
    url.hash = page === 'home' ? '' : `#${page}`;
    window.history.replaceState({}, '', url);
  }
}

for (const link of document.querySelectorAll('.topnav__link[data-page]')) {
  link.addEventListener('click', () => goTo(link.dataset.page));
}

// =========================================================================
// The rail folds away
// =========================================================================
//
// PRESENTATION STATE, remembered in the browser. It changes what is on screen
// and nothing about how a posting is read, so it must never reach
// `search.local.yaml`: a layout preference that bumped `config_version` would
// invalidate every score in the corpus for the sake of a wider grid.
const RAIL_KEY = 'careerAgent.rail.v1';
const railToggle = document.getElementById('rail-toggle');
const filterPanel = document.getElementById('filterpanel');

// The word and the count are separate nodes, because the count has to be
// hidden when it is zero rather than rendered as "(0)". An empty pair of
// brackets is a control reporting on itself.
const railToggleWord = el('span', { className: 'railtoggle__word' });
const railToggleCount = el('span', {
  className: 'railtoggle__count num',
  props: { hidden: true },
});
railToggle.replaceChildren(railToggleWord, railToggleCount);

/**
 * Open or close the filter panel.
 *
 * CLOSED IS THE DEFAULT, which is the change. The panel used to be a rail
 * that was open on arrival and took 288px of every screen; the design puts
 * the filters under a toggle and gives the width to the results.
 *
 * Closing hides the panel with `hidden`, so it occupies no space and holds no
 * tab stop -- and it changes NOTHING about the query. Every chosen filter
 * stays chosen, the chip bar under the toolbar still lists them, and the
 * count on this button still says how many are in force. A panel that could
 * be closed to widen a search would be a filter nobody can see.
 */
function setRail(open, { persist = true } = {}) {
  filterPanel.hidden = !open;
  railToggle.setAttribute('aria-expanded', String(open));
  railToggleWord.textContent = open ? t('rail.hide') : t('rail.show');
  if (persist) {
    try {
      window.localStorage.setItem(RAIL_KEY, open ? 'open' : 'closed');
    } catch { /* a private window refuses storage; the preference is simply
                 not remembered, which is better than a broken control. */ }
  }
}

/** How many narrowings are in force, for the badge on the closed button. */
function syncRailCount(active) {
  const n = Number.isFinite(active) ? active : 0;
  railToggleCount.textContent = String(n);
  railToggleCount.hidden = n === 0;
}

railToggle.addEventListener('click', () => {
  setRail(filterPanel.hidden);
});

try {
  setRail(window.localStorage.getItem(RAIL_KEY) === 'open', { persist: false });
} catch {
  setRail(false, { persist: false });
}

// The source matrix, loaded the first time somebody opens the panel. Not on
// page load: it answers a question nobody has asked yet, and a list of jobs
// should not wait on a catalogue read.
const sourcesPanel = createSourcesPanel(document.getElementById('sources-host'), { collection });

// =========================================================================
// Header controls
// =========================================================================

// Switching view WRITES the new view's grouping default into the store, in the
// open. It is not a hidden derivation: `apiQuery` still cannot see `view`, the
// value lands in the URL, and the button beside these two shows what it became.
// Cards is the reading view and groups; Table is the "show me every row" view
// and does not.
if (dom.exportGoodStrong) {
  dom.exportGoodStrong.addEventListener('click', async () => {
    const control = dom.exportGoodStrong;
    control.disabled = true;
    const label = control.textContent;
    control.textContent = t('export.working');
    try {
      const rows = await api.exportGoodStrong(lastQueryString || '');
      toast(t('export.done', { n: rows }));
    } catch (error) {
      toast(error.userMessage || t('export.failed'), { tone: 'bad' });
    } finally {
      control.disabled = false;
      control.textContent = label;
    }
  });
}

for (const [node, view] of [[dom.viewCards, 'cards'], [dom.viewTable, 'table'], [dom.viewKanban, 'kanban']]) {
  node.addEventListener('click', () => {
    const patch = { view, group_duplicates: VIEW_GROUPING[view] };
    // Kanban tracks what you have picked up, which is a different population
    // from "what is out there". Rendering 18,549 postings as a board is one
    // enormous untouched column and six empty ones.
    //
    // Written into the store in the open, exactly like `group_duplicates`:
    // it lands in the URL, the filter chips show it, and `apiQuery` still
    // cannot see `view`. A narrowing the person cannot see or undo would be
    // the "silent filter" defect this codebase already fixed once.
    if (view === 'kanban' && !store.get().status.length) patch.status = [...TRACKED_STATUSES];
    // Leaving Kanban drops the board's own narrowing, but never a status
    // filter the person chose for themselves.
    if (view !== 'kanban' && sameStatuses(store.get().status, TRACKED_STATUSES)) patch.status = [];
    store.set(patch);
  });
}

/**
 * Arrow keys move between the buttons of a segmented group.
 *
 * Three adjacent buttons are three tab stops by default, which is correct but
 * slow: a keyboard reader has to Tab past Cards and Table to reach Board, and
 * then Tab back through them to leave. A segmented control is one control with
 * several segments, and Left/Right (plus Home/End) is how every other one
 * behaves.
 *
 * Tab still reaches each button. This is added navigation, not a roving
 * tabindex that would REMOVE tab stops: `aria-pressed` buttons are what the
 * acceptance tests drive, and taking them out of the tab order to be clever
 * would break a keyboard path that already worked.
 */
document.addEventListener('keydown', (event) => {
  const keys = { ArrowLeft: -1, ArrowRight: 1, Home: 'first', End: 'last' };
  const move = keys[event.key];
  if (move === undefined) return;
  // Delegated from the document rather than bound per group, because the theme
  // switch is built by `theme.js` on DOMContentLoaded and a module runs before
  // that. A query at module time would find the view switch and silently miss
  // the theme one, which is exactly the kind of half-working keyboard path
  // nobody notices.
  const group = event.target.closest && event.target.closest('.segmented, .themeswitch');
  if (!group) return;
  const buttons = [...group.querySelectorAll('button')];
  const here = buttons.indexOf(document.activeElement);
  if (here < 0) return;
  event.preventDefault();
  let next;
  if (move === 'first') next = 0;
  else if (move === 'last') next = buttons.length - 1;
  else next = (here + move + buttons.length) % buttons.length;
  buttons[next].focus();
});

/** Order-insensitive: the board's narrowing is a set, not a sequence. */
function sameStatuses(a, b) {
  if (a.length !== b.length) return false;
  const left = [...a].sort().join('|');
  return left === [...b].sort().join('|');
}

dom.group.addEventListener('click', () => {
  store.set({ group_duplicates: !store.get().group_duplicates });
});

// The words come from the catalogue at draw time; `SORTS` is the six values.
replace(dom.sort, SORTS.map((value) => el('option', {
  text: t(`sort.${value}`),
  attrs: { value },
})));
dom.sort.addEventListener('change', (event) => store.set({ sort: event.target.value }));
dom.direction.addEventListener('click', () => {
  store.set({ direction: store.get().direction === 'asc' ? 'desc' : 'asc' });
});

// There is no width-dependent disclosure any more. The filters were a rail
// that a media query forced open above 900px and closed below it; they are a
// panel now, the reader opens it, and the answer is the same at every width.
// The panel's own grid reflows with `auto-fit`, so nothing here has to know
// how wide the screen is.

// =========================================================================
// The render loop
// =========================================================================

store.subscribe((state, meta) => {
  panel.syncState(state, meta);
  syncRailCount(renderChips(dom.chips, state, store));
  syncHeader(state);

  // A popstate that moved only `openJobId` -- Back with the drawer open -- is
  // still a drawer-only change, and rebuilding the list would destroy the
  // element the drawer must return focus to.
  const changedOnly = (key) => meta.changed.length === 1 && meta.changed[0] === key;

  // Opening or closing the drawer must not rebuild the list underneath it:
  // the element that opened the drawer is the element focus returns to.
  if (changedOnly('openJobId')) {
    syncDrawer(state);
    return;
  }

  const queryString = store.apiQueryString();
  if (queryString !== lastQueryString || !lastResponse) {
    load(queryString, state);
  } else {
    paint(state);
  }

  syncDrawer(state);
});

/**
 * Fetch the list and paint it.
 *
 * `quiet` keeps the current list on screen while the new one is fetched.
 * The recalculation watcher asks for that: it reloads every few seconds for
 * the life of a rescore, and on the real corpus one fetch takes longer than
 * the interval. With the skeleton drawn on every reload the list was a row
 * of grey blocks for the whole of a three-hour recalculation -- measured
 * 2026-09-11 at 203,765 of 234,184 rows, cards never once on screen. The
 * first load of a query still draws the skeleton, because there is nothing
 * else to show.
 */
/** Search Fit readiness, read with every list: a setup edit changes it. */
async function refreshReadiness() {
  try {
    const readiness = await api.getSearchFitReadiness();
    setSearchFitReadiness(readiness.state);
  } catch {
    // Unknown is not "not ready": the scores keep their numbers.
    setSearchFitReadiness('READY');
  }
}

async function load(queryString, state, { quiet = false } = {}) {
  lastQueryString = queryString;
  const token = ++inFlight;
  loading = true;
  if (!quiet || !lastResponse) {
    showSkeleton(state);
    dom.count.textContent = t('app.loading');
  }

  try {
    // A LIST NEVER OVERTAKES A SAVE. Changing a status and opening Applications
    // at once used to send the board's request while the save was still in
    // flight; the board was built without the job, and the save's answer could
    // only update jobs already on screen. Waiting for pending saves makes the
    // list the server returns include them.
    await settledJobSaves();
    if (token !== inFlight) return;
    const [response] = await Promise.all([
      api.listJobs(new URLSearchParams(queryString)),
      refreshReadiness(),
    ]);
    if (token !== inFlight) return;
    loading = false;
    lastResponse = response;
    panel.syncFacets(response.facets, store.get());
    paint(store.get());
  } catch (error) {
    if (token !== inFlight) return;
    loading = false;
    lastResponse = null;
    showError(error, queryString);
  }
}

/**
 * Nothing tracked yet: say what the board is for and where things come from.
 * The columns already explain each state; this is the one sentence and the one
 * way forward a first visit needs. Cleared for every other view.
 */
function paintBoardEmpty(show) {
  if (!dom.boardEmpty) return;
  dom.boardEmpty.hidden = !show;
  if (!show) {
    replace(dom.boardEmpty, []);
    return;
  }
  replace(dom.boardEmpty, [
    el('p', { className: 'boardempty__text', text: t('board.empty') }),
    button(t('board.toDiscover'), () => goTo('jobs'), {
      className: 'btn btn--primary',
      attrs: { id: 'board-to-discover' },
    }),
  ]);
}

function paint(state) {
  if (!lastResponse) return;
  const items = lastResponse.items || [];
  if (state.view !== 'kanban') paintBoardEmpty(false);
  // NO POSTINGS AT ALL. Filters, three views, grouping and a sort order over an
  // empty database are a wall of controls that can do nothing; they come back
  // the moment there is something to filter.
  const toolbar = document.querySelector('.topbar__controls');
  if (toolbar) toolbar.dataset.corpusEmpty = corpusEmpty() ? 'true' : 'false';

  // The board is its own empty state. Seven columns, each saying what would
  // put something in it, is a better answer than a generic "no results" panel
  // -- and it is the ONLY answer that tells a person the tracker exists and
  // is waiting for them. A new user has nothing tracked by definition, so the
  // generic panel is what they would have seen first.
  if (!items.length && state.view !== 'kanban') {
    showEmpty(state);
    announce(state, 0, 0);
    return;
  }

  if (state.view === 'table') {
    renderTable(dom.list, items, {
      sort: state.sort,
      direction: state.direction,
      visible: columnVisibility,
      onVisibilityChange: (visible) => { columnVisibility = visible; paint(store.get()); },
      onSort: (column) => {
        const current = store.get();
        store.set(current.sort === column
          ? { direction: current.direction === 'asc' ? 'desc' : 'asc' }
          : { sort: column, direction: 'desc' });
      },
      onOpen: openJob,
      onStatus: changeStatus,
      onSave: changeSaved,
      onAppliedDate: (jobId, date) => {
        // Both directions go to /applied-at, never to /status. Routing an
        // emptied field through a status change did nothing at all: `/status`
        // reads an absent date as "leave it", so the field cleared on screen
        // and the stored date survived, and the next paint put it back.
        if (date) return changeAppliedDate(jobId, date);
        const job = findJob(jobId);
        const ok = window.confirm(t('confirm.clearApplied', {
          title: job ? job.title : t('app.thisPosting'),
        }));
        // Repaint on cancel: the date input is already empty on screen, and
        // leaving it that way would show a date that is not stored.
        if (!ok) return paint(store.get());
        return changeAppliedDate(jobId, null);
      },
    });
  } else if (state.view === 'kanban') {
    renderKanban(dom.list, items, {
      onOpen: openJob,
      onStatus: changeStatus,
    });
    paintBoardEmpty(items.length === 0 && activeFilterCount(state) <= 1);
  } else {
    renderCards(dom.list, items, {
      onOpen: openJob,
      onStatus: changeStatus,
      onSave: changeSaved,
      onHidden: changeHidden,
    });
  }

  announce(state, items.length, lastResponse.total);
  renderPager(state);
}

function announce(state, shown, total) {
  const filters = activeFilterCount(state);
  const from = total ? state.offset + 1 : 0;
  const to = state.offset + shown;
  // "roles" and "postings" are different nouns because they are different
  // counts. Saying "132 postings" over a grouped list would be false: there
  // are 163, and 132 of them are the ones standing for the rest.
  const unit = state.group_duplicates ? 'Role' : 'Job';

  // Why the numbers on this screen do not subtract.
  //
  // The header says "19 jobs", the notice says "3 hidden", and this line said
  // "14 roles". 19 minus 3 is 16, and nothing said where the other two went:
  // they are the same role posted in three cities, which grouping collapsed
  // into one card. Three numbers on one screen that do not reconcile is the
  // thing this interface is most careful about, and it had them.
  const grouped = Number(lastResponse && lastResponse.grouped_away) || 0;
  //
  // A key per number, singular and plural. `pluralise` appends an English
  // `s`, which is the whole of English pluralisation and none of anybody
  // else's, so the count chooses the sentence rather than inflecting a noun
  // inside one.
  const parts = [
    total === 1 ? t(`count.one${unit}`) : t(`count.${unit.toLowerCase()}s`, { n: total }),
    state.group_duplicates && grouped
      ? (grouped === 1 ? t('count.oneRepostFolded') : t('count.repostsFolded', { n: grouped }))
      : null,
    total > shown ? t('count.showingRange', { from, to }) : null,
    filters
      ? (filters === 1 ? t('count.oneFilterActive') : t('count.filtersActive', { n: filters }))
      : t('count.noFilters'),
  ].filter(Boolean);
  dom.count.textContent = parts.join(' · ');
  renderRevisionNotice();
  renderHiddenNotice(state);
}

//: The handle of the poll that watches a recalculation to its end. One at a
//: time: a second timer would double the request rate and race the first to
//: refresh the list.
let recalcTimer = null;
//: How often the list is refreshed while a recalculation runs. Longer than
//: the retrieval panel's two seconds on purpose: each refresh is a full list
//: query against a database another thread is writing to, and nothing on
//: the screen changes faster than the progress bar, which has its own poll.
const RECALC_POLL_MS = 5000;

/**
 * Poll until the recalculation stops, then fetch the list once.
 *
 * Two seconds, matching the retrieval panel. `/api/rescore` reads a count
 * from the database rather than from the runner, so it is right before any
 * rescore has happened and stays right after one that was interrupted.
 */
function watchRecalculation() {
  if (recalcTimer) return;
  recalcTimer = window.setTimeout(async function tick() {
    recalcTimer = null;
    let status;
    try {
      status = await api.getRescore();
    } catch {
      // A failed poll is not a failed rescore, but a bar that simply stops
      // moving is the stall the owner met at 57% (2026-09-25): the server had
      // been stopped and nothing on screen said so. Say it, and offer to look
      // again rather than retrying for ever.
      showRecalculationLost();
      return;
    }
    // **`load`, NOT the store subscriber.** The subscriber only re-fetches
    // when the QUERY changed and otherwise repaints from `lastResponse`; a
    // recalculation changes nothing in the query, so a repaint would redraw
    // the stale payload for ever -- the notice frozen on "answers your
    // previous preferences" and, worse, the LIST still showing the previous
    // scores after the new ones had landed.
    //
    // The first version of this called a `refresh()` that does not exist:
    // the reload path here is a subscriber registered on the store, not a
    // named function. The call rejected silently, the notice never cleared,
    // and the network log showed the POST and the poll with no `/api/jobs`
    // between them, which is what finally named the bug.
    await load(store.apiQueryString(), store.get(), { quiet: true });
    if (status && status.running) watchRecalculation();
  }, RECALC_POLL_MS);
}


/**
 * "These answer your previous preferences." The sentence that was missing.
 *
 * THE MORNING THIS EXISTS FOR
 * ---------------------------
 * The owner set her seniority preferences in this interface on 2026-09-08.
 * Her configuration went from revision 4 to revision 6, all 19,469 stored
 * scores answered revision 4, and the Jobs list went empty. Nothing was lost
 * and nothing was broken -- a score is only true relative to the preferences
 * that produced it -- but an empty list reads as "this has stopped working",
 * and the remedy was a seven-minute terminal command.
 *
 * The server now serves the previous complete answer while the new one is
 * being computed (`storage/revisions.py`). This is where it says so. Without
 * this sentence the fix would be worse than the bug: a list that silently
 * answers a question she stopped asking.
 *
 * NOT DISMISSABLE, unlike the hidden-count notices. Those describe a nuance of
 * a correct result; this one says the result is provisional. A person who
 * waved it away once would have no way of knowing which preferences the list
 * in front of them belongs to.
 */
/** "Lost contact while recalculating", in place of a progress bar that froze. */
function showRecalculationLost() {
  const node = dom.revision;
  if (!node) return;
  node.hidden = false;
  replace(node, [
    el('span', { className: 'revnotice__text', attrs: { role: 'status' }, text: t('revision.lost') }),
    button(t('revision.checkAgain'), () => {
      load(store.apiQueryString(), store.get(), { quiet: true }).catch(() => {});
    }, { className: 'revnotice__go' }),
  ]);
}

function renderRevisionNotice() {
  const node = dom.revision;
  if (!node) return;
  const revision = lastResponse && lastResponse.revision;
  // No payload, or the answer matches the question: nothing to say. Silence
  // here is the ordinary case and it should look like nothing at all.
  if (!revision || revision.is_current) {
    clear(node);
    node.hidden = true;
    return;
  }

  const parts = [];
  if (revision.serving) {
    parts.push(el('span', {
      className: 'revnotice__text',
      text: t('revision.stale', {
        serving: revision.serving.config_version,
        current: revision.current.config_version,
      }),
    }));
  }
  if (revision.is_building) {
    // A real denominator or no percentage at all. `scoreable` is counted from
    // the corpus, so this is measured rather than guessed.
    const done = revision.current.scored;
    const total = revision.current.scoreable;
    const pct = total ? Math.floor((100 * done) / total) : 0;
    parts.push(el('span', {
      className: 'revnotice__progress num',
      text: t('revision.progress', {
        done: done.toLocaleString(),
        total: total.toLocaleString(),
        pct,
      }),
    }));
    // The same fact as a bar. A recalculation is the one thing here that takes
    // minutes, and "13,402 of 19,469" does not say at a glance whether that is
    // nearly done. `aria-hidden` because the sentence beside it already says
    // it, in words, with the denominator.
    parts.push(el('div', {
      className: 'revnotice__bar',
      attrs: { 'aria-hidden': 'true' },
    }, [el('span', { className: 'revnotice__barfill', style: `width:${pct}%` })]));
    // WATCH IT FINISH. Without this the notice is correct and then frozen:
    // the payload only changes when the list is fetched, so a recalculation
    // would complete in the background and the reader would go on being told
    // her results answer the previous preferences until she reloaded.
    //
    // Driven by `is_building` rather than by who pressed the button, so a
    // rescore started from the CLI, from another tab, or resumed after an
    // interrupted one is watched just the same.
    watchRecalculation();
  } else if (revision.is_interrupted) {
    // Stopped part way and nothing is running: say where it stopped, never
    // show it as still moving, and continue from there (a rescore only scores
    // what is still missing).
    const done = revision.current.scored;
    const total = revision.current.scoreable;
    const pct = total ? Math.floor((100 * done) / total) : 0;
    parts.push(el('span', {
      className: 'revnotice__progress num',
      attrs: { role: 'status' },
      text: t('revision.interrupted', { done: done.toLocaleString(), total: total.toLocaleString(), pct }),
    }));
    parts.push(button(t('revision.resume'), () => {
      api.startRescore().then(() => {
        watchRecalculation();
        return load(store.apiQueryString(), store.get(), { quiet: true });
      }).catch((error) => {
        replace(node, [el('span', { className: 'revnotice__text', attrs: { role: 'alert' },
          text: (error && error.userMessage) || t('rescore.couldNotStart') })]);
      });
    }, { className: 'revnotice__go' }));
  } else if (revision.serving) {
    // Stale but nothing running: the recalculation has not been started, or
    // it was interrupted. Either way the way forward is the same button.
    parts.push(button(t('revision.recalculate'), () => {
      // The watch is started HERE as well as from `is_building`, because in
      // the first seconds after the button there are no rows written yet:
      // the payload still reads "not building", the button would redraw, and
      // nothing would be watching the work that had just begun.
      api.startRescore().then(() => {
        watchRecalculation();
        return refresh();
      }).catch(() => {});
    }, { className: 'revnotice__go' }));
  }

  node.hidden = false;
  replace(node, parts);
}

/**
 * "24 jobs hidden because their posting rules you out."
 *
 * The number comes from the server, counted against this exact query with one
 * field flipped, so it is precisely what the control would add rather than an
 * estimate. It is rendered as a sentence with a button, not as a chip, because
 * a chip in the chipbar reads as "a filter you applied" and nobody applied
 * this one: it is the default.
 *
 * Absent when nothing is hidden. A line saying "0 hidden" is noise, and a
 * disclosure that is always on screen stops being read.
 */
function renderHiddenNotice(state) {
  // DISCOVER ONLY. Every narrowing below is about the discovery list; on any
  // other page (Applications shares this container) the notice is cleared
  // rather than left saying something about a list that is not on screen.
  if (currentPage !== 'jobs' || !lastResponse) {
    clear(dom.hidden);
    dom.hidden.hidden = true;
    return;
  }

  // TWO narrowings, two sentences, never one number.
  //
  // An employer stating a requirement you do not meet, and your own search
  // setting work aside as not the kind you asked for, are different facts.
  // Merging them is the defect this product already shipped once: three cards
  // carried "this posting states a requirement you do not meet" while having
  // no blockers at all, because `BLOCKED` was read as an employer rejecting
  // somebody. It is the opposite -- the SEARCH rejected the work.
  const counts = {
    include_ineligible: lastResponse ? (lastResponse.hidden_by_eligibility || 0) : 0,
    include_unresolved: lastResponse ? (lastResponse.hidden_unresolved || 0) : 0,
    include_excluded_seniority: lastResponse ? (lastResponse.hidden_by_seniority || 0) : 0,
    include_excluded_work_model: lastResponse ? (lastResponse.hidden_by_work_model || 0) : 0,
    include_off_target: lastResponse ? (lastResponse.hidden_off_target || 0) : 0,
    include_user_hidden: lastResponse ? (lastResponse.hidden_by_you || 0) : 0,
  };

  const NOTICES = [
    {
      key: 'include_ineligible',
      // Not "Show them anyway". That is what you say to somebody being
      // unreasonable, and wanting to see a job that asks for a work permit you
      // are in the middle of getting is not unreasonable.
      hidden: (n) => (n === 1 ? t('hidden.eligibilityOne') : t('hidden.eligibility', { count: n })),
      showing: () => t('hidden.eligibilityShowing'),
      reveal: () => t('hidden.eligibilityReveal'),
    },
    {
      // THREE narrowings now, and this is the one that is NOT a rejection.
      // The posting never said where the employer hires, so nothing rules her
      // out and nothing confirms her either. It sits second because a reader
      // scanning down should meet "an employer ruled you out" first and then
      // "nobody could tell", which are different sentences and must not be
      // read as the same one.
      key: 'include_unresolved',
      hidden: (n) => (n === 1 ? t('hidden.unresolvedOne') : t('hidden.unresolved', { count: n })),
      showing: () => t('hidden.unresolvedShowing'),
      reveal: () => t('hidden.unresolvedReveal'),
    },
    {
      // The only one of the four that is a PREFERENCE. Zero, and therefore
      // absent, until a candidate names a level she does not want shown.
      key: 'include_excluded_seniority',
      hidden: (n) => (n === 1 ? t('hidden.seniorityOne') : t('hidden.seniority', { count: n })),
      showing: () => t('hidden.seniorityShowing'),
      reveal: () => t('hidden.seniorityReveal'),
    },
    {
      // A way of working she said never to show. A preference, like levels.
      key: 'include_excluded_work_model',
      hidden: (n) => (n === 1 ? t('hidden.workModelOne') : t('hidden.workModel', { count: n })),
      showing: () => t('hidden.workModelShowing'),
      reveal: () => t('hidden.workModelReveal'),
    },
    {
      key: 'include_off_target',
      // About the WORK, and phrased so nothing here reads as a judgement about
      // the employer or the person. Your search decided; you can undecide.
      hidden: (n) => (n === 1 ? t('hidden.offTargetOne') : t('hidden.offTarget', { count: n })),
      showing: () => t('hidden.offTargetShowing'),
      reveal: () => t('hidden.offTargetReveal'),
    },
    {
      // THE ONE SHE DID HERSELF. Third and last, because the two above are
      // things that happened to her and this is a thing she chose; a reader
      // scanning down should meet the machine's decisions before her own.
      key: 'include_user_hidden',
      hidden: (n) => (n === 1 ? t('hidden.byYouOne') : t('hidden.byYou', { count: n })),
      showing: () => t('hidden.byYouShowing'),
      reveal: () => t('hidden.byYouReveal'),
      // And a second way out, which the other two do not have and do not
      // need: their populations are defined by a rule, and this one is a list
      // of individual decisions worth looking at as a list.
      only: () => store.set({ user_hidden_only: true, include_user_hidden: true }),
    },
  ];

  const rows = [];
  for (const notice of NOTICES) {
    const speaking = Boolean(state[notice.key] || counts[notice.key]);
    if (!speaking) continue;
    // DISMISSED BY CATEGORY, NEVER BY COUNT. The stored key is the notice's
    // kind (its state key, which never changes), not the sentence it printed.
    // A dismissal used to be forgotten whenever the count reached zero, so a
    // filter that briefly emptied a category brought the notice straight back
    // with a new number; a changed count is the same notice and stays quiet.
    //
    // Showing them is a STATE, and the way back out lives here rather than on
    // a filter chip: that row is for things that narrow, and "Clear all
    // filters" deliberately does not touch these. So a notice whose state is
    // IN FORCE is never suppressed, dismissed or not: hiding it would take
    // "Hide them again" with it.
    const showing = Boolean(state[notice.key]);
    if (dismissed(notice.key) && !showing) {
      // WHAT SHE HID HERSELF HAS NO OTHER DOOR. The eligibility, place and
      // seniority narrowings all have a control in the filter panel; the
      // postings she set aside do not. So a dismissed "hidden by you" notice
      // leaves a compact entry behind that cannot be dismissed, and waving
      // the sentence away can never strand them.
      if (notice.only && counts[notice.key]) {
        rows.push(el('span', {
          className: 'hidden__row hidden__row--compact',
          dataset: { notice: `${notice.key}-compact` },
        }, [
          button(t('hidden.byYouCompact', { n: counts[notice.key] }), notice.only, {
            className: 'hidden__show hidden__compact',
          }),
        ]));
      }
      continue;
    }
    rows.push(el('span', { className: 'hidden__row', dataset: { notice: notice.key } }, [
      el('span', {
        className: 'hidden__text',
        text: showing ? notice.showing() : notice.hidden(counts[notice.key]),
      }),
      button(
        showing ? t('hidden.hideAgain') : notice.reveal(),
        () => store.set({ [notice.key]: !showing }),
        { className: 'hidden__show' },
      ),
      // DISMISSES THE SENTENCE, NEVER THE STATE. The postings stay exactly as
      // hidden or as shown as they were; this only stops saying so. The
      // control that actually changes the population is in the filter rail
      // and is unaffected, which is what makes a dismissable notice safe.
      notice.only && !showing
        ? button(t('hidden.restoreView'), notice.only, { className: 'hidden__show' })
        : null,
      // Only while they are hidden: the showing sentence carries the way back.
      showing
        ? null
        : button('×', () => {
          dismiss(notice.key);
          renderHiddenNotice(store.get());
        }, {
          className: 'hidden__dismiss',
          ariaLabel: t('hidden.dismiss'),
        }),
    ].filter(Boolean)));
  }

  if (!rows.length) {
    clear(dom.hidden);
    dom.hidden.hidden = true;
    return;
  }
  dom.hidden.hidden = false;
  replace(dom.hidden, rows);
}

// -------------------------------------------------------------------------
// Which notices this reader has waved away
// -------------------------------------------------------------------------
//
// PRESENTATION STATE, in the browser and nowhere else. It changes what is
// said about a result set and nothing about the result set, so it must never
// reach `search.local.yaml`: a dismissed sentence that bumped
// `config_version` would invalidate every score in the corpus.
const NOTICES_KEY = 'careerAgent.notices.v1';

function dismissedSet() {
  try {
    const raw = window.localStorage.getItem(NOTICES_KEY);
    return new Set(raw ? JSON.parse(raw) : []);
  } catch {
    // A private window refuses storage, and a parse can fail on a key an
    // older version wrote. Nothing is dismissed, which is the safe answer:
    // the reader sees the notice rather than losing it silently.
    return new Set();
  }
}

function writeDismissed(set) {
  try {
    window.localStorage.setItem(NOTICES_KEY, JSON.stringify([...set]));
  } catch { /* not remembered, still dismissed for this visit */ }
}

//: Dismissed this visit even when storage refuses the write, so the × always
//: works; storage only decides whether it is remembered after a reload.
const dismissedThisVisit = new Set();

function dismissed(key) {
  return dismissedThisVisit.has(key) || dismissedSet().has(key);
}

function dismiss(key) {
  dismissedThisVisit.add(key);
  const set = dismissedSet();
  set.add(key);
  writeDismissed(set);
}

function renderPager(state) {
  const total = lastResponse ? lastResponse.total : 0;
  if (total <= state.limit) {
    clear(dom.pager);
    dom.pager.hidden = true;
    return;
  }
  dom.pager.hidden = false;
  const page = Math.floor(state.offset / state.limit) + 1;
  const pages = Math.ceil(total / state.limit);
  const step = (delta) => store.set(
    { offset: Math.max(0, state.offset + delta) },
    { resetOffset: false },
  );
  replace(dom.pager, [
    button(`← ${t('action.previous')}`, () => step(-state.limit), {
      className: 'btn', attrs: state.offset === 0 ? { disabled: true } : {},
    }),
    el('span', {
      className: 'pager__pos num',
      text: t('pager.position', { page, pages }),
    }),
    button(`${t('action.next')} →`, () => step(state.limit), {
      className: 'btn', attrs: page >= pages ? { disabled: true } : {},
    }),
  ]);
}

// =========================================================================
// States: loading, empty, error
// =========================================================================

function showSkeleton(state) {
  if (state.view === 'table') tableSkeleton(dom.list);
  else if (state.view === 'kanban') kanbanSkeleton(dom.list);
  else cardsSkeleton(dom.list);
}

/**
 * Advice that names only filters that are actually set. Suggesting "remove the
 * eligibility filter" to somebody who never set one is noise, and it teaches
 * the reader to distrust the rest of the sentence.
 */
/**
 * The word for a filter INSIDE a sentence.
 *
 * A different word from the filter's heading in the rail: "removing the job
 * board filter" reads, "removing the Where these come from filter" does not.
 *
 * Resolved when the sentence is built, never at module load. This was a table
 * of finished English -- twenty entries, evaluated before `setLocale` had read
 * the stored choice -- so a Portuguese reader whose filters returned nothing
 * was advised, in English, to remove a filter named in English.
 *
 * A filter with no entry falls back to a readable form of its own key, which
 * is how a filter added tomorrow is never invisible in this sentence.
 */
function filterWord(key) {
  const translated = t(`filterWord.${key}`);
  return translated === `filterWord.${key}` ? humanLabel(key).toLowerCase() : translated;
}

function emptyAdvice(state) {
  const suggestions = [];
  if (state.min_score !== null && state.min_score !== undefined) {
    suggestions.push(t('advice.matchBelow', { n: state.min_score }));
  }
  if (state.min_confidence !== null && state.min_confidence !== undefined) {
    suggestions.push(t('advice.detailBelow', { n: state.min_confidence }));
  }
  if (state.search) suggestions.push(t('advice.searchingLess', { text: state.search }));
  const ELIGIBILITY = t('advice.eligibility');
  if ((state.eligibility || []).length) suggestions.push(ELIGIBILITY);
  // Every list and flag key, with a human word for the ones that have one.
  // It used to be two hand-written lists, which silently omitted every filter
  // added after them: somebody who narrowed to nothing with a country filter
  // was told "try clearing the filters" instead of which one to clear. Same
  // defect as `matchesPreset`, same fix -- derive from the store's own keys,
  // and fall back to the key itself so a new filter is never invisible.
  for (const key of LIST_KEYS) {
    if (!(state[key] || []).length) continue;
    if (key === 'eligibility') continue;   // already suggested, with its reason
    suggestions.push(t('advice.removingFilter', { what: filterWord(key) }));
  }
  for (const key of FLAG_KEYS) {
    if (state[key]) suggestions.push(t('advice.turningOff', { what: filterWord(key) }));
  }
  if (state.min_salary !== null && state.min_salary !== undefined) {
    suggestions.push(t('advice.salaryLess', { amount: state.min_salary }));
  }
  if (state.posted_within_days) {
    suggestions.push(Number(state.posted_within_days) === 1
      ? t('advice.postedMoreThanOne')
      : t('advice.postedMoreThan', { n: state.posted_within_days }));
  }

  if (!suggestions.length) return t('advice.tryClearing');
  const shown = suggestions.slice(0, 3);
  const list = shown.length === 1
    ? shown[0]
    : `${shown.slice(0, -1).join(', ')} ${t('advice.or')} ${shown[shown.length - 1]}`;
  const why = shown.includes(ELIGIBILITY) ? t('advice.eligibilityWhy') : '';
  return t('advice.try', { list, why });
}

/**
 * Why an UNFILTERED list is empty, which is two different situations.
 *
 * "The database has no postings. Run a collection first." was printed for
 * both, and for the second one it is actively wrong: after editing a
 * preference the version is bumped, every stored score belongs to the previous
 * one, and the corpus is untouched. Being told to run a COLLECTION -- the one
 * operation in this product that opens a socket -- to fix a scoring problem
 * sends a person to the network for something a local, free, offline command
 * fixes in four minutes.
 */
/**
 * Whether the health payload has arrived at all.
 *
 * `lastHealth` starts as `{}`, and `Number(undefined) || 0` is 0, so an
 * unanswered health call was indistinguishable from an empty database. The
 * list can and does finish first, and for that moment a person with 19
 * postings was told "No jobs have been collected yet" -- a claim about their
 * database made from no evidence about it. Absence is never permission, and
 * it is not proof of emptiness either.
 */
function healthKnown() {
  return Object.hasOwn(lastHealth, 'job_count');
}

/**
 * Settings' first block: change the guided-setup answers, or find jobs.
 * Both open the same setup that a fresh install starts on, at the right card.
 */
function renderSetupEntry(host) {
  if (!host) return;
  replace(host, [
    el('p', { className: 'settings__lede', text: t('settings.setupLede') }),
    el('div', { className: 'settings__actions' }, [
      button(t('settings.setupOpen'), () => {
        home.openSetup('welcome');
        goTo('home');
      }, { className: 'btn btn--primary', attrs: { id: 'settings-open-setup' } }),
      button(t('empty.findJobs'), () => {
        home.openSetup('ready');
        goTo('home');
      }, { className: 'btn', attrs: { id: 'settings-find-jobs' } }),
    ]),
  ]);
}

/** The database holds no postings at all -- known, not merely unanswered. */
function corpusEmpty() {
  return healthKnown() && Number(lastHealth.job_count) === 0;
}

/**
 * The way out of an empty database: the guided setup's last card, which starts
 * finding jobs and shows its progress. Replaces directions to a panel inside a
 * collapsed "Advanced" section of Settings.
 */
function findJobsButton() {
  return button(t('empty.findJobs'), () => {
    home.openSetup('ready');
    goTo('home');
  }, { className: 'btn btn--primary', attrs: { id: 'empty-find-jobs' } });
}

function emptyHeadline() {
  if (!healthKnown()) return t('empty.headline');
  const stale = Number(lastHealth.job_count) > 0 && Number(lastHealth.scored_count) === 0;
  return stale ? t('empty.notScoredYet') : t('empty.nothingYet');
}

function emptyReason() {
  if (!healthKnown()) return t('empty.checking');
  const jobs = Number(lastHealth.job_count) || 0;
  const scored = Number(lastHealth.scored_count) || 0;
  if (jobs > 0 && scored === 0) {
    return t('empty.unscored', { n: jobs.toLocaleString() });
  }
  return t('empty.noneCollected');
}

/**
 * The recalculate button, and the only thing on this page that starts it.
 *
 * An independent UX review found that the way out of an empty screen was a
 * terminal command, printed for somebody who had reached that screen by
 * editing a preference in the interface. The words were fixed first; a
 * clearer description of a dead end is still a dead end, so this is the
 * exit.
 *
 * It is safe to be a button because `rescore` opens no socket and asks no
 * model of either kind. It is deliberately NOT a spinner-and-forget: the
 * count moves, because "a few minutes" with nothing changing on screen is
 * indistinguishable from a hang.
 */
function rescoreButton() {
  const label = el('span', { text: t('app.rescore') });
  const control = el('button', {
    className: 'btn btn--primary',
    attrs: { type: 'button' },
  }, [label]);

  let polling = null;

  async function poll() {
    const state = await api.getRescore();
    const run = state.run || {};
    if (state.running) {
      const done = Number(run.boards_done) || 0;
      const total = Number(run.boards_total) || 0;
      label.textContent = total
        ? t('rescore.progress', { done: done.toLocaleString(), total: total.toLocaleString() })
        : t('rescore.working');
      return;
    }
    window.clearInterval(polling);
    polling = null;
    control.disabled = false;
    if (run.status === 'failed') {
      label.textContent = t('app.rescoreFailed');
      return;
    }
    label.textContent = t('app.rescoreDone');
    // The scores are new, so the list has to be ASKED again rather than
    // repainted from what it already holds. The health payload too: it is
    // where `scored_count` lives, and the empty state's whole sentence is
    // built from it.
    showHealth();
    load(store.apiQueryString(), store.get());
  }

  control.addEventListener('click', async () => {
    control.disabled = true;
    label.textContent = t('app.starting');
    try {
      await api.startRescore();
    } catch (error) {
      control.disabled = false;
      label.textContent = error.userMessage || t('rescore.couldNotStart');
      return;
    }
    let misses = 0;
    polling = window.setInterval(() => {
      poll().then(() => { misses = 0; }).catch(() => {
        // A few missed polls are a busy server; many are a stopped one.
        misses += 1;
        if (misses < 10) return;
        window.clearInterval(polling);
        polling = null;
        control.disabled = false;
        label.textContent = t('app.rescoreLost');
      });
    }, 700);
  });

  return control;
}

/**
 * The star, or the static star, or nothing.
 *
 * `prefers-reduced-motion` is answered by asking for a DIFFERENT FILE rather
 * than by pausing an animation, because a file that decides for itself has
 * already been fetched and parsed before it can decide.
 *
 * `matchMedia` is guarded: it exists everywhere this product runs, and a
 * decoration is not worth a thrown exception on the day it does not.
 */
function star() {
  let still = false;
  try {
    still = Boolean(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches);
  } catch { /* no matchMedia: take the calm one */ still = true; }
  return el('img', {
    className: 'state__star',
    attrs: {
      src: still ? './star.svg' : './star-twinkle.svg',
      alt: '',
      width: 48,
      height: 48,
      // Decoration. A screen reader that announced "star" here would be
      // reading out a drawing to somebody waiting to hear why the list is
      // empty, which is the sentence underneath.
      'aria-hidden': 'true',
    },
  });
}

//: Re-read the rail's counts and sources after a collection finishes.
function refreshRail() {
  loadRailReadouts();
}

function showEmpty(state) {
  dom.list.className = 'state';
  const filtered = activeFilterCount(state) > 0;
  const unscored = healthKnown()
    && Number(lastHealth.job_count) > 0
    && Number(lastHealth.scored_count) === 0;
  replace(dom.list, [
    star(),
    el('h2', {
      className: 'state__head',
      text: filtered ? t('empty.filtered') : emptyHeadline(),
    }),
    el('p', { className: 'state__msg', text: filtered ? emptyAdvice(state) : emptyReason() }),
    filtered
      ? button(
        t('action.clearAllFilters'),
        () => store.replaceAll(clearedFilters(store.get())),
        { className: 'btn btn--primary' },
      )
      : null,
    // Only when there is something to recalculate. On a database with no
    // postings at all this would be a button that correctly does nothing.
    !filtered && unscored ? rescoreButton() : null,
    !filtered && corpusEmpty() ? findJobsButton() : null,
    // The one line of encouragement in the product, and it is only true when
    // nothing is filtered: with a filter on, the answer is the filter, not a
    // reflection on how a career works.
    !filtered
      ? el('p', {
        className: 'state__aside',
        text: t('app.emptyAside'),
      })
      : null,
  ]);
  clear(dom.pager);
  dom.pager.hidden = true;
}

function showError(error, queryString) {
  dom.list.className = 'state state--error';
  replace(dom.list, [
    el('h2', { className: 'state__head', text: t('app.listFailed') }),
    el('p', { className: 'state__msg', text: error.userMessage || error.message }),
    // NO STATUS NUMBER. `HTTP 500` under the sentence told a person nothing
    // she could act on. `api.js` writes it to the console instead, with the
    // route and the server's own words beside it, which is where somebody
    // debugging this would look anyway.
    button(t('action.retry'), () => load(queryString, store.get()), { className: 'btn btn--primary' }),
  ]);
  dom.count.textContent = t('app.listFailedShort');
  clear(dom.pager);
  dom.pager.hidden = true;
}

// =========================================================================
// Mutations. Every one of them renders what the SERVER said, never a guess.
// =========================================================================

function findJob(jobId) {
  return (lastResponse && lastResponse.items || []).find((job) => job.job_id === jobId) || null;
}

function mergeJob(updated) {
  if (!updated || !lastResponse) return;
  const index = lastResponse.items.findIndex((job) => job.job_id === updated.job_id);
  if (index >= 0) lastResponse.items[index] = { ...lastResponse.items[index], ...updated };
  // While a new list is on its way, the one in hand belongs to the previous
  // query; painting it into the new view would show the wrong jobs. The new
  // list is fetched after the save settles, so it already carries the change.
  if (!loading) paint(store.get());
}

// -------------------------------------------------------------------------
// Saves that change where a job stands, and what waits for them
// -------------------------------------------------------------------------

/** Resolves when every pending save has finished, whatever its outcome. */
function settledJobSaves() {
  return jobSaves.size ? Promise.allSettled([...jobSaves.values()]) : Promise.resolve();
}

/**
 * Run a save for one job after any save already queued for it.
 *
 * `<html data-saving="status">` is present while any save is in flight: the
 * page's own signal that what is on screen may be about to change, and the
 * condition anything relying on confirmed state waits for.
 */
function queueJobSave(jobId, save) {
  const before = jobSaves.get(jobId) || Promise.resolve();
  const next = before.then(save, save);
  jobSaves.set(jobId, next);
  document.documentElement.setAttribute('data-saving', 'status');
  const done = () => {
    if (jobSaves.get(jobId) === next) jobSaves.delete(jobId);
    if (!jobSaves.size) document.documentElement.removeAttribute('data-saving');
  };
  next.then(done, done);
  return next;
}

/**
 * A refused save leaves the control showing a status the server never
 * recorded. Repaint from the last confirmed data, which the refusal did not
 * touch, so the screen agrees with the server again.
 */
function restoreConfirmed() {
  if (!loading) paint(store.get());
}

function changeStatus(jobId, status, appliedAt) {
  return queueJobSave(jobId, async () => {
    try {
      const updated = await api.patchStatus(jobId, status, appliedAt);
      mergeJob(updated);
      flash(t('flash.movedTo', { status: statusLabel(status) }));
      // Returned, not only merged: the drawer repaints from this rather than
      // issuing a second GET for the row it was just handed.
      return updated;
    } catch (error) {
      flash(error.userMessage || error.message, true);
      restoreConfirmed();
      return null;
    }
  });
}

/**
 * Set or clear the applied date without moving the status.
 *
 * `date === null` clears it, and that is the one destructive action in this
 * interface, which is why the caller confirms first and this function does
 * not. A confirmation buried in a shared helper is a confirmation nobody can
 * see when reading the call site.
 */
function changeAppliedDate(jobId, date) {
  // The same queue as a status change: both move where an application stands,
  // and an applied date saved beside a status change must land after it.
  return queueJobSave(jobId, async () => {
    try {
      const updated = await api.patchAppliedAt(jobId, date);
      mergeJob(updated);
      flash(date ? t('flash.appliedDateSet', { date }) : t('flash.appliedDateCleared'));
      return updated;
    } catch (error) {
      flash(error.userMessage || error.message, true);
      restoreConfirmed();
      return null;
    }
  });
}

async function changeSaved(jobId, saved) {
  try {
    mergeJob(await api.patchSaved(jobId, saved));
  } catch (error) {
    flash(error.userMessage || error.message, true);
  }
}

/**
 * Hide one posting, or put it back.
 *
 * The list is reloaded rather than patched in place, because hiding CHANGES
 * THE POPULATION: the row leaves, the count moves and the notice above the
 * results gains a line. Merging the row would leave a card on screen that the
 * next request would not return.
 */
//: The reasons a posting can be set aside for, in the order they are offered.
//:
//: Read off `HiddenReason`, which the database CHECK constraint is the second
//: spelling of. Ordered by what the recommendation audit found most often
//: rather than alphabetically: the audit on 2026-09-08 could see that 92 of
//: her top 100 were WEAK matches and could not see WHY, and the first two
//: below are the two explanations it most needed and could not distinguish.
const HIDE_REASONS = [
  'WRONG_WORK', 'WRONG_PLACE', 'WRONG_LEVEL', 'TITLE_MISLEADING',
  'PAY', 'EMPLOYER', 'STALE', 'OTHER',
];

/**
 * Hide one posting, or put it back.
 *
 * The list is reloaded rather than patched in place, because hiding CHANGES
 * THE POPULATION: the row leaves, the count moves and the notice above the
 * results gains a line. Merging the row would leave a card on screen that the
 * next request would not return.
 */
async function changeHidden(jobId, hidden, scope = 'posting', reason = null) {
  try {
    await api.patchHidden(jobId, hidden, scope, reason);
    // Reload rather than merge: this row no longer belongs to this query.
    await load(store.apiQueryString(), store.get());
    if (!hidden) {
      flash(t('flash.unhidden'), false, () => changeHidden(jobId, true, scope));
      return;
    }
    if (reason) {
      // She already said why. Repeating the offer would be asking twice.
      flash(t('flash.hiddenWithReason'), false, () => changeHidden(jobId, false, scope));
      return;
    }
    // **THE REASON IS OFFERED AFTER THE FACT AND NEVER WAITED ON.**
    //
    // Hiding stays one click. A control that interrogates somebody for
    // setting one job aside is a control they stop using, and most hides
    // have no reason worth typing.
    //
    // What it buys when she does answer: the audit could measure that 92 of
    // her top 100 sit in the WEAK band, and could not tell a posting that is
    // wrong for WHERE IT IS from one that is wrong for WHAT THE WORK IS.
    // Those want completely different corrections and only she knows which.
    //
    // Nothing learns from it. `test_hidden_reason.py` asserts that a reason
    // changes no score and no preference; if a pattern in them ever becomes a
    // rule, it goes through the preference editor as a proposal she confirms.
    flash(
      t('flash.hidden'),
      false,
      // The way back, against THIS row rather than "the last one". By the
      // time somebody presses it they may have hidden three more.
      () => changeHidden(jobId, false, scope),
      [
        el('span', { className: 'flash__why', text: t('flash.whyHidden') }),
        ...HIDE_REASONS.map((code) => button(
          t(`hideReason.${code}`),
          () => changeHidden(jobId, true, scope, code),
          { className: 'flash__reason' },
        )),
      ],
    );
  } catch (error) {
    flash(error.userMessage || error.message, true);
  }
}

async function changeNotes(jobId, notes) {
  try {
    mergeJob(await api.patchNotes(jobId, notes));
    flash(t('flash.notesSaved'));
  } catch (error) {
    flash(error.userMessage || error.message, true);
  }
}

let flashTimer = null;
/**
 * A sentence, and optionally the way back out of what it describes.
 *
 * `undo` is a function and a label, not a boolean: the caller knows which row
 * it wrote and can restore exactly that one. "Undo the last thing" is a
 * different feature and a worse one -- by the time somebody presses it they
 * may have hidden three more.
 */
function flash(message, isError = false, undo = null, extras = null) {
  const host = document.getElementById('flash');
  host.className = `flash${isError ? ' flash--error' : ''}`;
  const children = [el('span', { className: 'flash__text', text: message })];
  if (undo) {
    children.push(button(t('action.undo'), () => {
      host.hidden = true;
      undo();
    }, { className: 'flash__undo' }));
  }
  // OPTIONAL, and after the fact. Anything here is something the person may
  // do next, never something the action waited on: the hide has already
  // happened by the time this is drawn.
  for (const extra of extras || []) children.push(extra);
  replace(host, children);
  host.hidden = false;
  if (flashTimer) clearTimeout(flashTimer);
  // Longer when there is something to press. Four seconds is enough to read a
  // sentence and not enough to decide to undo it.
  flashTimer = setTimeout(() => { host.hidden = true; }, undo ? 9000 : 4000);
}

// =========================================================================
// Drawer routing
// =========================================================================

function openJob(jobId) {
  store.set({ openJobId: jobId });
}

let drawerJobId = null;
function syncDrawer(state) {
  if (state.openJobId && state.openJobId !== drawerJobId) {
    drawerJobId = state.openJobId;
    drawer.open(state.openJobId, document.activeElement);
  } else if (!state.openJobId && drawerJobId) {
    drawerJobId = null;
    drawer.close();
  }
}

function syncHeader(state) {
  dom.viewCards.setAttribute('aria-pressed', state.view === 'cards' ? 'true' : 'false');
  dom.viewTable.setAttribute('aria-pressed', state.view === 'table' ? 'true' : 'false');
  dom.viewKanban.setAttribute('aria-pressed', state.view === 'kanban' ? 'true' : 'false');
  if (dom.exportGoodStrong) dom.exportGoodStrong.hidden = state.view !== 'table';
  dom.group.setAttribute('aria-pressed', state.group_duplicates ? 'true' : 'false');
  dom.group.classList.toggle('is-on', Boolean(state.group_duplicates));
  dom.group.textContent = state.group_duplicates
    ? t('order.oneRow')
    : t('order.everyPosting');
  dom.group.title = state.group_duplicates
    ? t('order.oneRowHelp')
    : t('order.everyPostingHelp');
  dom.sort.value = state.sort;
  // The words say what you GET, not which way an arrow points. "Ascending" is
  // only meaningful once you already know what is being sorted.
  const ascending = state.direction === 'asc';
  dom.direction.textContent = ascending ? t('order.lowestFirst') : t('order.highestFirst');
  dom.direction.setAttribute(
    'aria-label',
    ascending ? t('order.lowestFirstLabel') : t('order.highestFirstLabel'),
  );
}

// =========================================================================
// Boot
// =========================================================================

/**
 * `ollama.reachable` is three-valued: null "not asked", true "answered",
 * false "asked and did not answer". A page load never opens a socket to the
 * model, so null is the normal state and must not be printed as "not running".
 */
function ollamaReadout(ollama) {
  if (ollama.configured === false) {
    return { tone: 'is-unknown', text: t('health.noLocalModel') };
  }
  if (ollama.reachable === true) {
    return { tone: 'is-up', text: t('health.localModelUp', { model: ollama.model || '' }).replace('  ', ' ') };
  }
  if (ollama.reachable === false) {
    return { tone: 'is-down', text: t('health.localModelSilent') };
  }
  return { tone: 'is-unknown', text: t('health.localModelUntried') };
}

// The last health payload seen. The drawer reads it to decide whether Enrich
// can be offered, and with what warning.
let ollamaHealth = {};
//: The last `/api/health`, kept so the empty state can tell "there are no
//: postings" apart from "there are postings and none of them are scored under
//: the configuration version you are now on". Those need different sentences
//: and one of them used to get the other's.
let lastHealth = {};

function separator() {
  return el('span', { className: 'health__sep', text: '·', attrs: { 'aria-hidden': 'true' } });
}

async function showHealth() {
  try {
    const health = await api.getHealth();
    lastHealth = health;
    // The empty state's sentence depends on this payload, and the list can
    // finish first: on a fast list and a slow health call the person was told
    // "the database has no postings, run a collection" while it was loading
    // the very numbers that say otherwise. Repaint if the empty state is what
    // is currently on screen -- only then, so an ordinary list is never
    // rebuilt underneath somebody's cursor.
    if (dom.list.querySelector('.state__head')) paint(store.get());
    const ollama = health.ollama || {};
    ollamaHealth = ollama;
    const readout = ollamaReadout(ollama);
    const runtime = health.runtime || {};
    // The mode banner is rendered OUTSIDE the disclosure, because it is the
    // one piece of status a person must not have to open anything to see.
    if (dom.healthMode) {
      replace(dom.healthMode, [
        el('span', {
          className: `health__mode health__mode--${String(runtime.mode || 'unknown').toLowerCase()}`,
          // THE KEY, NOT THE SENTENCE. `runtime.mode` is `PERSONAL`, `DEMO`
          // or `UNKNOWN` -- a stored value, the same in every language --
          // and `runtime.banner` is the server's ENGLISH rendering of it.
          // Printing the banner put "Demo data" on an otherwise Portuguese
          // header, which is the whole shape of defect this catalogue
          // exists to end: server text arriving already translated into one
          // language. The banner stays as the fallback for a mode this
          // catalogue has never heard of, which is better than an enum.
          text: tState('modeTag', runtime.mode, runtime.banner || ''),
          attrs: {
            title: `${tState('mode', runtime.mode, runtime.banner || '')}. ${runtime.is_personal
              ? t('health.modePersonalHelp')
              : t('health.modeDemoHelp')}`,
          },
        }),
      ]);
    }

    replace(dom.health, [
      // WHICH POPULATION AM I LOOKING AT. The whole point of the mode work,
      // and for one commit it reached no pixel: the server started sending
      // `runtime` and `db_name`, this line still read the removed `db_path`,
      // so the header rendered an empty span and no banner existed anywhere.
      // A guarantee the interface never states is a guarantee the person
      // cannot rely on.
      el('span', {
        className: 'health__jobs num',
        text: health.job_count === null || health.job_count === undefined
          ? t('health.jobsUnknown')
          : t('health.jobs', { n: health.job_count }),
      }),
      // Everything from here down is a STATUS, and it moves into the details
      // element below rather than sitting on the front of the product. None
      // of it is dropped: a degraded search really is semantically different
      // and saying nothing about it is the defect the fallback was built to
      // avoid. It is simply not the first thing somebody should read.
      ...(health.search_indexed === false
        ? [
          el('span', {
            className: 'health__degraded',
            text: t('health.searchSlow'),
            attrs: {
              title: t('health.searchSlowHelp'),
            },
          }),
          separator(),
        ]
        : []),
      // Scores written before the filter columns existed. Four filters --
      // country, region, workplace, minimum salary -- return NOTHING for those
      // rows, which is indistinguishable from a correctly-filtered empty
      // result unless something says so. Same discipline as the stale index
      // above: a degraded state is reported, never silent.
      ...(Number(health.stale_scores) > 0
        ? [
          el('span', {
            className: 'health__degraded',
            text: t('health.staleScores', {
              n: Number(health.stale_scores).toLocaleString(),
            }),
            attrs: {
              title: t('health.staleFacetsHelp'),
            },
          }),
          separator(),
        ]
        : []),
      el('span', {
        className: `health__ollama ${readout.tone}`,
        text: readout.text,
        attrs: {
          title: ollama.note || t('health.localModelHelp'),
        },
      }),
      el('span', {
        className: 'health__db num',
        text: runtime.database_ref || health.db_name || '',
        attrs: { title: t('health.databaseHelp') },
      }),
    ]);

    // How many of those statuses are something a person might act on. They
    // are not sources, so they only speak when no source needs attention;
    // the details are the status line's tooltip.
    railStatus.degraded = (health.search_indexed === false ? 1 : 0)
      + (Number(health.stale_scores) > 0 ? 1 : 0);
    railStatus.detail = [...dom.health.children]
      .map((node) => node.textContent.trim())
      .filter((text) => text && text !== '\u00b7')
      .join(' \u00b7 ');
    drawRailStatus();
  } catch {
    dom.health.textContent = t('health.unknown');
  }
}

//: What the rail's one status line is drawn from: sources whose last refresh
//: failed (from `/api/sources`, the same rows Settings & Sources shows) and
//: local conditions worth knowing (from `/api/health`).
const railStatus = { failed: null, degraded: 0, detail: '' };

/**
 * ONE status line. A source that needs attention wins, is yellow, and is a
 * button to Settings & Sources, where it can be dealt with. Otherwise a
 * local condition worth knowing is yellow with its details as the tooltip.
 * Otherwise mint: every source working.
 */
function drawRailStatus() {
  const node = dom.healthSummary;
  if (!node || !dom.healthSummaryText) return;
  const failed = Number(railStatus.failed) || 0;
  let text = t('sidenav.statusOk');
  let attention = false;
  let actionable = false;
  if (failed > 0) {
    text = failed === 1 ? t('sidenav.statusAttentionOne') : t('sidenav.statusAttention', { n: failed });
    attention = true;
    actionable = true;
  } else if (railStatus.degraded > 0) {
    // Two keys rather than one with a pluralised noun: Portuguese does not
    // inflect "coisa" the way an English `s` would.
    text = railStatus.degraded === 1
      ? t('health.worthKnowingOne')
      : t('health.worthKnowing', { n: railStatus.degraded });
    attention = true;
  }
  dom.healthSummaryText.textContent = text;
  node.classList.toggle('is-attention', attention);
  node.classList.toggle('is-actionable', actionable);
  // Focusable in every state, so its detail can be reached: not `disabled`.
  node.setAttribute('aria-disabled', String(!actionable));
  node.setAttribute('aria-describedby', 'health');
  node.title = actionable
    ? `${t('sidenav.statusOpenSources')}. ${railStatus.detail || ''}`.trim()
    : (railStatus.detail || '');
}

/**
 * The rail's own readout, from numbers the database already holds.
 *
 * NONE OF IT IS INVENTED. The footer counts are the corpus and the tracked
 * applications. Anything that cannot be had is left out rather than drawn as
 * a zero, because "we could not work this out" and "there are none" are
 * opposite facts and must not render the same.
 *
 * Failures here are SILENT on purpose. This is the frame around the product;
 * a rail that put an error where a count goes would make a slow request look
 * like a broken install.
 */
async function loadRailReadouts() {
  try {
    // `/api/home` already composes these and is the ONE place they are
    // counted. A second count here is how a rail and a dashboard come to
    // disagree about how many applications are open.
    const home = await api.getHome();
    const metric = (key) => {
      const found = (home.metrics || []).find((entry) => entry.key === key);
      return found && Number.isFinite(found.value) ? found.value : null;
    };
    shell.setStats({
      jobs: Number.isFinite(home.job_count) ? home.job_count : null,
      open: metric('tracking'),
      interview: metric('interviews'),
    });
  } catch { /* the frame stays blank rather than shouting */ }
  try {
    const sources = await api.getSources();
    // The same rule Settings & Sources uses (`needs_attention`): stale,
    // failed or refused. One definition, so the two can never disagree.
    railStatus.failed = (sources.refresh || []).filter((row) => row.needs_attention).length;
    drawRailStatus();
  } catch { /* the status line keeps what it last knew */ }

  // The evidence review used to be fetched here as well, to fill a progress
  // bar in the rail. The bar is gone -- the review's progress belongs inside
  // Evidence, where there is something to do about it -- and so is the
  // request that fed it. A call whose only reader has been deleted is a call
  // to delete, not one to leave running into a variable nobody reads.
}

// The status line leads to Settings & Sources when a source needs attention.
dom.healthSummary?.addEventListener('click', () => {
  if (dom.healthSummary.classList.contains('is-actionable')) goTo('settings');
});

store.startHistory();
showHealth();
loadRailReadouts();
// One question at boot: is a run already going (started before a reload, or
// in another tab)? If so every page shows it; if not, nothing polls.
collection.refresh();

// First paint. `history: 'none'` so the initial URL is not pushed onto itself.
const initial = store.get();
panel.syncState(initial);
syncRailCount(renderChips(dom.chips, initial, store));
syncHeader(initial);
load(store.apiQueryString(), initial).then(() => syncDrawer(store.get()));

// Escape closes the drawer from anywhere; the drawer itself restores focus.
document.addEventListener('keydown', (event) => {
  if (event.key === 'Escape' && store.get().openJobId) store.set({ openJobId: null });
});


// =========================================================================
// Search preferences
// =========================================================================

/**
 * Loaded when the panel is first opened, not on page load.
 *
 * It is 54 phrase groups the person has not asked to see, and the list is the
 * thing they came for. Opening the disclosure is the ask.
 */
let profileLoaded = false;
let lastProfile = null;
let lastLedger = null;

/**
 * Draw the profile again in the reader's language.
 *
 * The third panel with this defect, after the source catalogue and the
 * phrase editor: a screen that renders once keeps the words it rendered
 * with, so switching to Portuguese left twelve English questions under a
 * Portuguese heading. It redraws from the payload already in hand.
 *
 * A redraw DISCARDS AN UNSAVED EDIT, and that is the accepted cost: the
 * alternative is a form asking one question in each language. Switching
 * language is a deliberate act, and the Save button is on screen while
 * anything is pending.
 */
/**
 * Draw the profile with the canonical Experience tab. The experience view is
 * made once and reloaded on each draw, so edit mode is not lost to a redraw
 * the person did not ask for.
 */
function drawProfile(host, tab = null) {
  if (!experienceNode) {
    experienceNode = experienceView({ onReview: reviewFromProfile, onChanged: () => careerChanged() });
  } else {
    void experienceNode.load().catch(() => {});
  }
  profileTabs = renderProfile(host, lastProfile, lastLedger, { experience: experienceNode, tab }) || null;
}

function retranslateProfile() {
  const host = document.getElementById('page-profile');
  if (lastProfile && host) drawProfile(host);
}

/**
 * The Career Profile, drawn once and kept.
 *
 * It used to be a rail disclosure that loaded when opened, which was right
 * while it was a panel beside a list. As a page it loads when the page is
 * first visited, for the same reason: a screen of jobs should not wait on a
 * configuration read nobody asked for.
 */
async function loadProfile() {
  const host = document.getElementById('page-profile');
  if (profileLoaded) return;
  profileLoaded = true;
  host.textContent = t('app.loading');
  try {
    // TWO READS, AND ONE OF THEM MAY FAIL WITHOUT TAKING THE PAGE WITH IT.
    // The evidence ledger is what the Overview, Experience and Skills tabs
    // are made of, and the preferences are what the fourth is made of; a
    // machine with no candidate answers 404 on the ledger and must still be
    // able to show somebody the search they configured. So the ledger is
    // caught on its own and its absence simply removes three tabs.
    const [profile, ledger] = await Promise.all([
      api.getProfile(),
      api.getEvidence().catch(() => null),
    ]);
    // Kept so a language switch can redraw without asking again.
    lastProfile = profile;
    lastLedger = ledger;
    drawProfile(host);
  } catch (error) {
    profileLoaded = false;  // let them try again
    host.textContent = error.userMessage || error.message;
  }
}

let prefsLoaded = false;

/**
 * Loaded when Settings is first opened, not on page load.
 *
 * It used to be a rail disclosure whose `toggle` event was the ask. As a
 * section of a page the arrival is the ask, for the same reason: it is 54
 * phrase groups nobody has asked to see, and a list of jobs should not wait
 * on a configuration read.
 */
/**
 * Draw the phrase editor again in the reader's language.
 *
 * Same shape of defect the source catalogue had, and the same fix: a panel
 * that renders once keeps the words it was rendered with, so switching to
 * Portuguese left three English group headings above Portuguese chips. It
 * re-renders from the payload already in hand rather than asking again.
 */
let lastPreferences = null;

function retranslatePreferences() {
  renderSearchSettings(document.getElementById('search-settings-host'), store);
  document.getElementById('settings-model-head').textContent = t('settings.model');
  document.getElementById('settings-model-summary').textContent = t('settings.modelAdvanced');
  document.getElementById('settings-advanced-summary').textContent = t('settings.advancedRefresh');
  if (lastPreferences && dom.prefsHost) renderPreferences(dom.prefsHost, ...lastPreferences);
}

async function loadPreferences() {
  if (prefsLoaded || !dom.prefsHost) return;
  prefsLoaded = true;
  dom.prefsHost.textContent = t('app.loading');
  try {
    const [data, provenance] = await Promise.all([api.getPreferences(), api.getSearchReview()]);
    const origins = new Map(provenance.rows.map(row => [row.path, row.origin]));
    data.signals = data.signals.map(signal => ({ ...signal,
      origin: origins.get(`lexicon.${signal.signal_id}.patterns`) || 'unknown_provenance',
    }));
    const onPatch = async (patch) => {
      const result = await api.patchPreferences(patch);
      // The scores on screen were computed under the previous version. Say
      // so where the person is looking rather than leaving them to wonder
      // why nothing moved.
      dom.count.textContent = t('app.stale');
      // The same button, beside the same sentence. Saying a thing is out of
      // date without offering to fix it is half a message.
      dom.count.appendChild(rescoreButton());
      return result;
    };
    // Kept so a language switch can redraw without asking the server the same
    // question again.
    lastPreferences = [data, onPatch];
    renderPreferences(dom.prefsHost, data, onPatch);
  } catch (error) {
    prefsLoaded = false;  // let them try again
    dom.prefsHost.textContent = error.userMessage || error.message;
  }
}


// =========================================================================
// Retrieval
// =========================================================================

/**
 * Built the first time Settings is opened. It follows the app's one watcher
 * while Settings is on screen and asks for the funnel on arrival; it never
 * polls on its own.
 */
let retrievalPanel = null;

function loadRetrieval() {
  if (!dom.retrHost) return;
  if (!retrievalPanel) retrievalPanel = createRetrievalPanel(dom.retrHost, api, collection);
  retrievalPanel.refresh();
}

/** Leaving Settings stops following. It never stops the run. */
function stopRetrieval() {
  if (retrievalPanel) retrievalPanel.stop();
}

// =========================================================================
// Finding jobs, on every page
// =========================================================================

const collectBar = document.getElementById('collectbar');
const collectBarView = createProgressView(collection, { compact: true });
let collectBarShape = null;

/**
 * One line across the top of every page while jobs are being found, and once
 * when it has finished. Not on Home when Home is itself showing the run: the
 * same run twice on one screen would be two things to read for one fact.
 */
function paintCollectBar(snapshot, reason) {
  if (!collectBar) return;
  const ended = outcomeText(snapshot);
  const hide = (!snapshot.active && !ended)
    || (currentPage === 'home' && home.showsCollection());
  collectBar.hidden = hide;
  if (hide) {
    collectBarShape = null;
    return;
  }
  if (snapshot.active) {
    collectBarView.update(snapshot);
    if (collectBarShape !== 'active') {
      collectBarShape = 'active';
      replace(collectBar, [
        el('strong', { className: 'collectbar__head', text: t('setup.ready.finding') }),
        collectBarView.root,
        button(t('collect.show'), () => goTo('home'), {
          className: 'btn btn--link', attrs: { id: 'collectbar-show' },
        }),
      ]);
    }
    return;
  }
  if (reason === 'tick' && collectBarShape === 'ended') return;
  collectBarShape = 'ended';
  replace(collectBar, [
    el('span', { className: 'collectbar__text', text: ended }),
    snapshot.phase === 'failed' || currentPage === 'jobs'
      ? null
      : button(t('setup.ready.see'), () => goTo('jobs'), {
        className: 'btn btn--link', attrs: { id: 'collectbar-see' },
      }),
    button(t('collect.dismiss'), () => collection.dismiss(), {
      className: 'btn btn--link', attrs: { id: 'collectbar-dismiss' },
    }),
  ].filter(Boolean));
}

collection.subscribe(paintCollectBar);

// Jobs arrive source by source. On a database that was empty, the list and
// its toolbar -- filters, sort, views -- stayed hidden until a reload, because
// "no postings at all" was read once at boot. Read it again as each source
// finishes, but only while it still says empty: one small request per source,
// and none once there is something to filter.
collection.onProgress(() => {
  if (corpusEmpty()) showHealth();
});

// A finished run changed the corpus. Everything that counted it asks again,
// once.
collection.onFinish(() => {
  refreshAfterCollection();
  if (currentPage === 'settings') sourcesPanel.load(true);
  // Home's counts moved -- unless Home is the setup's last card, which is
  // already saying "Done" and offering the jobs.
  if (currentPage === 'home' && !home.inSetup()) home.load();
});

async function refreshAfterCollection() {
  await showHealth();
  // A source that failed during the run, or recovered, changes the status.
  refreshRail();
  load(store.apiQueryString(), store.get(), { quiet: Boolean(lastResponse && lastResponse.items.length) });
}

// =========================================================================
// What Career Agent knows about the person's career, for the Tailor handoff
// =========================================================================

let careerContextCache = null;

/**
 * True when the person has given Career Agent something about their career:
 * a document it read, or evidence waiting for review or confirmed. Cached
 * until the next navigation or evidence change.
 */
function careerContext() {
  if (!careerContextCache) {
    careerContextCache = api.getFirstRun().then((state) => {
      const steps = state.steps || [];
      const documents = steps.find((step) => step.key === 'documents') || {};
      const evidenceStep = steps.find((step) => step.key === 'evidence') || {};
      return Boolean(documents.done
        || Number(evidenceStep.confirmed) > 0
        || Number(evidenceStep.waiting) > 0);
    }).catch(() => {
      careerContextCache = null;
      // Unknown is not "absent": never hide the way in because a read failed.
      return true;
    });
  }
  return careerContextCache;
}

// =========================================================================
// Language
// =========================================================================
//
// A UI preference, next to the theme control, and stored in the same place
// for the same reason: it changes how the page LOOKS and nothing about what
// it means. Nothing here reaches the matcher, the query string or the
// database -- `VERIFIED_NOT_ELIGIBLE` is still `VERIFIED_NOT_ELIGIBLE`
// everywhere it is stored or asked for, and only its label moves.
//
// Changing it repaints from the store rather than reloading the page: a
// reload would lose the drawer, the scroll position and any unsaved status
// change, and none of those are language.

function buildLocaleControl(host) {
  if (!host || host.firstChild) return;
  const group = el('div', {
    className: 'segmented localeswitch',
    attrs: { role: 'group', 'aria-label': t('locale.label') },
  });

  const buttons = LOCALES.map((locale) => {
    // THE CODE, AND NO FLAG. A flag is a country and a language is not, and
    // the design system uses no emoji or flag at all; the language's own
    // name is the tooltip and the accessible name.
    const button = el('button', {
      className: 'segmented__btn localeswitch__btn',
      attrs: {
        type: 'button',
        'data-locale': locale,
        lang: locale,
        title: t(`locale.name.${locale}`),
        'aria-label': t(`locale.name.${locale}`),
      },
      on: {
        click: () => {
          setLocale(locale, { persist: true });
          sync();
          group.setAttribute('aria-label', t('locale.label'));
          // Repaint everything the catalogue reaches. `store.set({})` is a
          // no-op change that still runs the subscriber, which is exactly
          // what a language switch needs and what a reload would overdo.
          store.set({});
          relabelStaticText();
          // The source catalogue renders once and would otherwise keep its
          // English column headings and state chips under a translated
          // heading. It redraws from the payload it already has.
          sourcesPanel.retranslate();
          localProfiles.retranslate();
          // CAREER EVIDENCE TOO. It is drawn with `t()` when it loads and
          // never again, so switching language while on it left every
          // sentence on the page in the old one under a translated menu.
          // `refresh` redraws whatever is open -- a package, a CV review, the
          // home view -- rather than going back to the top. NOT while
          // something typed there is unsaved: a redraw would throw it away,
          // and the page translates on its next arrival anyway.
          if (currentPage === 'manage' && evidence && !evidenceHasUnsavedText()) evidence.refresh();
          // The new career pages redraw from what they already hold.
          if (currentPage === 'evidence') void evidenceView.load().catch(() => {});
          if (currentPage === 'documents') void documentsView.load().catch(() => {});
          shell.setPage(currentPage === 'home' ? homeHeader : currentPage,
            { action: headerAction(currentPage) });
          retranslatePreferences();
          retranslateProfile();
          // The health readout too. It is built with `t()` and drawn once at
          // boot, so a Portuguese sidebar carried "Everything is working" in
          // English at the bottom of every screen. It re-asks `/api/health`,
          // which is the one call in this list cheap enough not to cache:
          // it reads three counts and opens no socket.
          showHealth();
        },
      },
    });
    button.appendChild(el('span', {
      className: 'localeswitch__code', attrs: { 'aria-hidden': 'true' }, text: t(`locale.${locale}`),
    }));
    group.appendChild(button);
    return button;
  });

  function sync() {
    const current = getLocale();
    for (const button of buttons) {
      button.setAttribute('aria-pressed', String(button.dataset.locale === current));
    }
  }

  sync();
  host.appendChild(group);
}

/**
 * The strings that live in `index.html` rather than in a render function.
 *
 * The shell is server-rendered so the page is never a blank frame waiting for
 * JavaScript, which means its headings start in English and are relabelled
 * once here. Every one is OURS -- a heading, a control, a sort option. No
 * posting text is touched, in either direction.
 */
/**
 * The theme control, in the reader's language.
 *
 * `theme.js` is a CLASSIC script in the head -- a module is deferred, and the
 * page would paint in the wrong theme and then jump -- so it cannot import
 * the catalogue. It renders the English words and this puts the reader's over
 * them, exactly as the server-rendered headings in `index.html` are handled.
 *
 * The word is what carries the meaning; the sun and moon beside it are
 * `aria-hidden` decoration. So the accessible name is rebuilt here too,
 * rather than left saying "Dark theme" to somebody reading in Portuguese.
 */
function relabelTheme() {
  for (const button of document.querySelectorAll('.themeswitch__btn')) {
    const choice = button.dataset.theme;
    const label = t(`theme.${choice}`);
    const hint = t(`theme.${choice}Hint`);
    const word = button.querySelector('.themeswitch__word');
    if (word) word.textContent = label;
    button.title = label;
    button.setAttribute('aria-label', t('theme.buttonLabel', { label, hint }));
  }
  const group = document.querySelector('.themeswitch');
  if (group) group.setAttribute('aria-label', t('theme.label'));
}

function relabelStaticText() {
  const swap = (selector, key) => {
    const node = document.querySelector(selector);
    if (node) node.textContent = t(key);
  };
  swap('.skip', 'app.skip');
  swap('#view-cards', 'view.cards');
  swap('#view-table', 'view.table');
  swap('#view-kanban', 'view.board');
  swap('#export-good-strong', 'export.goodStrong');
  // Resume Tailor opens in the reader's language (its messages follow it).
  const tailorLink = document.querySelector('.topnav__link--tailor');
  if (tailorLink) tailorLink.setAttribute('href', `/resume-tailor?lang=${encodeURIComponent(getLocale())}`);
  // The two toolbar controls whose words depend on STATE rather than only
  // on the catalogue: which way the sort runs, and whether duplicates are
  // folded. `syncHeader` already knows how to label both from the state,
  // so it is called rather than reimplemented here.
  //
  // It was `swap('#group-duplicates', 'order.oneRow')`, which set that
  // button to "One row per role" whatever the state actually was, and
  // left `#direction` in English entirely: an empty `store.set({})` does
  // not notify subscribers, so nothing else relabelled it on a switch.
  syncHeader(store.get());
  swap('label[for="sort"]', 'order.sortLabel');
  // The results heading. Invisible, and still a sentence somebody reads: a
  // screen reader announces it, so leaving it in English on a Portuguese page
  // would be the localisation defect this file keeps closing, in the one place
  // nobody would ever see it.
  swap('#resultsheading', 'rail.resultsHeading');
  swap('#daily-open', 'daily.open');
  relabelTheme();
  home.relabel();
  // The drawer builds its own tabs during module setup, before the locale is
  // known, so it relabels itself rather than being relabelled by selector.
  drawer.relabel();
  // The rail is built once and then only state-synced, so it relabels itself
  // rather than being rebuilt: a rebuild would lose focus, collapse every open
  // section and discard whatever is in the search box.
  panel.relabel();
  // The rail relabels itself: its destinations now carry an icon beside the
  // word, and writing `textContent` on the button would have thrown the icon
  // away on the first language switch.
  shell.retranslate(currentPage === 'home' ? homeHeader : currentPage);
  const settingsHeads = {
    'settings-setup-head': 'settings.setupHead',
    'settings-prefs-head': 'rail.preferences',
    'settings-sources-head': 'rail.sources',
    'settings-ai-head': 'ai.head',
    'settings-profiles-head': 'profiles.settingsHead',
    'settings-retr-head': 'rail.retrieve',
  };
  for (const [id, key] of Object.entries(settingsHeads)) {
    const node = document.getElementById(id);
    if (node) node.textContent = t(key);
  }
  // The filters toggle, re-worded for the new language. It reads the PANEL,
  // which is where the state now lives. This line used to ask whether
  // `document.body` carried `rail-collapsed` -- a class the rail took with
  // it when it was removed -- so `contains` was always false, `!false` was
  // always true, and every relabel silently reopened the panel. It is why
  // the filters were open on arrival with nothing stored.
  setRail(!filterPanel.hidden, { persist: false });

  const viewGroup = document.querySelector('.viewswitch');
  if (viewGroup) viewGroup.setAttribute('aria-label', t('view.group'));
  const resultBar = document.querySelector('.resultbar');
  if (resultBar) resultBar.setAttribute('aria-label', t('order.group'));

  for (const option of document.querySelectorAll('#sort option')) {
    option.textContent = t(`sort.${option.value}`);
  }
}

// THE LANGUAGE FIRST, THEN THE PAGE.
//
// `goTo` draws a page, and a page drawn before `setLocale` has read the
// stored choice is drawn in English -- silently, and only for whichever
// surface happens to render synchronously. This is the module-scope
// evaluation trap in its last form: not a `t()` frozen at import, but a
// render ordered before the locale was known.
setLocale(initialLocale());
buildLocaleControl(dom.localeHost);
const localProfiles = createLocalProfiles(document.getElementById('local-profiles-host'));
localProfiles.mountSettings(document.getElementById('settings-profiles-host'));

// The landing page is HOME. A hash chooses another, so a bookmark and a
// reload land where the person left off.
const wanted = DEV_STATEMENTS ? 'manage' : window.location.hash.replace('#', '');
goTo(PAGES[wanted] ? wanted : 'home', { push: false });

relabelStaticText();

// The theme control is built by a CLASSIC script on `DOMContentLoaded`, and
// this module runs BEFORE that event: a module is deferred, so it executes
// once parsing finishes -- `readyState` is already `interactive` -- and
// `DOMContentLoaded` fires afterwards. So the `relabelTheme()` inside
// `relabelStaticText` above finds no buttons at all, and the control sat in
// English beside a page rendered in Portuguese.
//
// The obvious guard is `readyState === 'loading'`, and it is wrong here for
// exactly that reason: it is false, and the else-branch relabels nothing a
// moment too early. The event is what to wait for, not the state.
if (document.readyState !== 'complete') {
  document.addEventListener('DOMContentLoaded', relabelTheme);
}
