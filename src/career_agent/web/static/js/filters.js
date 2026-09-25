/**
 * filters.js: the filter rail, the starting points, and the active-filter chips.
 *
 * The panel writes to the store and reads back from it. It holds no filter
 * state of its own, which is why the Cards/Table switch cannot desynchronise
 * anything: there is only ever one answer to "what is being asked for".
 *
 * WHAT CHANGED IN V3, AND WHY
 * ---------------------------
 * The rail used to be one long column of every control this product has, named
 * after the things it does internally: "Role class", "Fit band", "Any signal
 * that fired", "Deterministically eligible", "Locally enriched". Each of those
 * is precise, and none of them is answerable by a person looking for a job.
 *
 * Two changes, and they are separate:
 *
 * 1. PROGRESSIVE DISCLOSURE. The controls are grouped into named sections that
 *    match the questions a person actually asks: where, what kind of work, how
 *    much, how far along am I. Sections a beginner needs are open; the rest are
 *    one click away. Each section says how many of its own filters are active
 *    and can be cleared on its own, so "I have narrowed this too far" has an
 *    answer smaller than "clear everything".
 *
 * 2. PLAIN LANGUAGE. Every visible label is written for someone who has not
 *    read the architecture. The internal vocabulary has NOT been deleted: it
 *    moved into `Advanced`, which says what it is for. Removing it would have
 *    traded one failure (unreadable) for another (a product that cannot explain
 *    itself), and the point of this system is that every number is traceable.
 *
 * The SECTIONS list is data, and both the panel and the section counters are
 * derived from it. That is deliberate: the previous version had hand-written
 * key lists that silently omitted every filter added after them, so a preset
 * stayed lit while a country filter narrowed the list underneath it.
 */

import { el, button, replace, clear } from './dom.js';
import { t, tState } from './i18n.js';
import { helpNote } from './help.js';
import { vocabLabel } from './format.js';
import { eligibilityWords } from './badges.js';
import {
  DEFAULTS, FLAG_KEYS, LIST_KEYS, NUMBER_KEYS, SEARCH_MAX, clearedFilters, debounce, isTrackedSet,
} from './state.js';

export const SHORTLIST_THRESHOLD = 70;

/**
 * A label from a table, in the reader's language.
 *
 * `SECTIONS`, `PRESETS` and `TOGGLES` hold catalogue KEYS rather than
 * sentences, because this module is evaluated during setup -- before
 * `setLocale` has read the stored choice -- so a resolved string here would
 * freeze the panel in English. That is exactly what happened to the drawer
 * tabs, and it was invisible for weeks because both of them were English
 * words somebody could mistake for a design decision.
 */
function labelOf(row) {
  // The fallback is deliberate. An entry still holding a literal renders in
  // English and is visible in `scripts/localisation_check.py`; without it,
  // `t(undefined)` returned undefined and seven section headings rendered as
  // empty strings, which looks like a layout bug rather than a missing key.
  return row.labelKey ? t(row.labelKey) : (row.label || '');
}

/** The same, for the optional sentence under a control. */
function helpOf(row) {
  return row.helpKey ? t(row.helpKey) : (row.help || '');
}


/**
 * The sections, in the order they are shown.
 *
 * `keys` is every state key the section owns. It drives the active count on
 * the section header and the section's own Clear button, so a control added to
 * a section without being listed here is a control whose section cannot count
 * or clear it. A test asserts every filter key belongs to exactly one section.
 */
export const SECTIONS = [
  {
    key: 'find',
    labelKey: 'filters.section.find',
    helpKey: 'filters.section.findHelp',
    // Not `search`: the free-text box lives in the Discover toolbar, and a
    // section that counted and cleared a control it does not hold would be
    // a count nobody can trace. The chip bar still lists and clears it.
    keys: ['keyword', 'exclude_keyword'],
  },
  {
    key: 'quick',
    labelKey: 'filters.section.quick',
    keys: ['saved_only', 'has_salary', 'remote_only', 'posted_within_days'],
  },
  {
    key: 'quality',
    labelKey: 'filters.section.quality',
    helpKey: 'filters.section.qualityHelp',
    keys: ['min_score', 'max_score', 'min_confidence', 'eligibility',
      'include_ineligible', 'include_off_target', 'include_unresolved',
      'include_excluded_seniority', 'include_excluded_work_model'],
  },
  {
    key: 'place',
    labelKey: 'filters.section.place',
    keys: ['country', 'region', 'worksite', 'latam_only', 'worldwide_only'],
  },
  {
    key: 'role',
    labelKey: 'filters.section.role',
    keys: ['seniority', 'employment_type', 'company'],
  },
  // WHAT THE POSTING ASKS OF SOMEBODY STARTING OUT. Its own section rather
  // than a row inside `role`, because the three questions here belong to
  // people the rest of this panel was not written for: somebody entering a
  // profession, somebody returning to work, and somebody moving from another
  // field. Burying "no previous experience required" under Seniority would
  // have made it findable only by the people who already know it exists.
  {
    key: 'entry',
    labelKey: 'filters.section.entry',
    helpKey: 'filters.section.entryHelp',
    keys: ['experience_max_years', 'experience_requirement', 'entry_signal',
      'include_transferable'],
  },
  {
    key: 'pay',
    labelKey: 'filters.section.pay',
    helpKey: 'filters.section.payHelp',
    keys: ['min_salary', 'salary_currency', 'salary_period'],
  },
  {
    key: 'skills',
    labelKey: 'filters.section.skills',
    keys: ['technology'],
  },
  {
    key: 'sources',
    labelKey: 'filters.section.sources',
    keys: ['provider'],
  },
  {
    key: 'progress',
    labelKey: 'filters.section.progress',
    keys: ['status'],
  },
  {
    key: 'advanced',
    labelKey: 'filters.section.advanced',
    helpKey: 'filters.section.advancedHelp',
    keys: ['role_class', 'fit_band', 'signal', 'enriched_only'],
  },
];

/**
 * Multi-value groups, keyed by the state key, and each assigned to a section.
 *
 * The labels say what the value IS ABOUT rather than which column it came
 * from. `country` is the clearest case: the value is the place the job board
 * printed, and it is NOT a statement about where the employer will hire. Those
 * are different questions, and a chip reading "Country: BR" is read as an
 * eligibility claim by anyone who has not read ADR-0011.
 */
const FACET_GROUPS = [
  { key: 'company', labelKey: 'facet.company', section: 'role' },
  { key: 'provider', labelKey: 'facet.provider', section: 'sources' },
  { key: 'technology', labelKey: 'facet.technology', section: 'skills' },
  // Widest question first. This used to run country, region, worksite, so
  // "Latin America" -- the one control most people here actually want -- sat
  // below a list of fifty-eight countries and was never seen. Nobody picks a
  // country before deciding whether they want an office at all.
  { key: 'worksite', labelKey: 'facet.worksite', section: 'place' },
  { key: 'region', labelKey: 'facet.region', section: 'place' },
  { key: 'country', labelKey: 'facet.country', section: 'place' },
  { key: 'seniority', labelKey: 'facet.seniority', section: 'role' },
  { key: 'employment_type', labelKey: 'facet.employment_type', section: 'role' },
  // Beside the contract TYPE rather than under it: `Full time` and `CLT`
  // answer different questions, and in Brazil a job can be full time
  // under either statute. Empty on a corpus with no Brazilian postings,
  // which is what a facet with no counts is supposed to do.
  { key: 'contract_regime', labelKey: 'facet.contract_regime', section: 'role' },
  // Both in the `entry` section, both tallied by `facets()` from the first
  // commit: a control the panel draws and the repository does not count is a
  // control that silently goes blank, which is what the `technology` note
  // above records.
  { key: 'experience_requirement', labelKey: 'facet.experience_requirement', section: 'entry' },
  { key: 'entry_signal', labelKey: 'facet.entry_signal', section: 'entry' },
  { key: 'salary_currency', labelKey: 'facet.salary_currency', section: 'pay' },
  { key: 'salary_period', labelKey: 'facet.salary_period', section: 'pay' },
  { key: 'status', labelKey: 'facet.status', section: 'progress' },
  { key: 'eligibility', labelKey: 'facet.eligibility', section: 'quality' },
  // Advanced. Each of these is this tool's own classification rather than
  // something the posting says, which is exactly why they sit together and
  // behind a disclosure that says so.
  { key: 'role_class', labelKey: 'facet.role_class', section: 'advanced' },
  { key: 'fit_band', labelKey: 'facet.fit_band', section: 'advanced' },
  { key: 'signal', labelKey: 'facet.signal', section: 'advanced' },
];

/**
 * The starting points. Each is a filter combination, not an endpoint.
 *
 * Named for the result rather than for the mechanism: "Deterministically
 * eligible" described how the answer is computed, which is the one thing a
 * person choosing it does not need to know.
 */
const PRESETS = [
  {
    id: 'all',
    labelKey: 'filters.preset.all',
    helpKey: 'filters.preset.allHelp',
    patch: {},
  },
  {
    // `strong`, not `shortlist`. This preset is a SCORE threshold, and
    // `SHORTLISTED` is a status the product renders "Interested" -- so a
    // preset called shortlist that had nothing to do with it was one word
    // meaning two things in one panel. The threshold constant keeps its name
    // because it is the configuration's own word for the number.
    id: 'strong',
    labelKey: 'filters.preset.strong',
    helpKey: 'filters.preset.strongHelp',
    patch: { min_score: SHORTLIST_THRESHOLD },
  },
  {
    // EXPLORING, and the preset that says out loud what it does not do.
    //
    // `SHORTLISTED` is rendered "Interested", and it is the status a person
    // sets while still deciding. It is the one most likely to be read as a
    // signal about the JOB; it is a fact about HER. The matcher cannot see it
    // -- `JobFacts` names no workflow state and `career_agent.match` imports
    // nothing that could reach one -- and `test_interest_is_not_fit.py`
    // proves it three ways rather than asserting it in a comment.
    id: 'interested',
    labelKey: 'filters.preset.interested',
    helpKey: 'filters.preset.interestedHelp',
    patch: { status: ['SHORTLISTED'] },
  },
  {
    // `VERIFIED_ELIGIBLE` ALONE, and the missing value is the whole story.
    //
    // This preset used to ask for `LIKELY_ELIGIBLE` as well. That value is in
    // `EligibilityStatus`, it has a label in this file, and the deterministic
    // matcher HAS NEVER PRODUCED IT -- `match/gates.py` says so in as many
    // words, and the real corpus holds zero rows carrying it under any
    // configuration version. `/api/jobs` accepts three values, not four, so
    // clicking this preset did not return fewer jobs: it returned HTTP 400,
    // and because the choice is persisted, the Jobs view then failed to load
    // on every subsequent visit until the filter was cleared by hand.
    //
    // The label is "Nothing standing in the way", which is
    // `eligibility.VERIFIED_ELIGIBLE` exactly. "Probably nothing in the way"
    // is a different sentence and it has nothing to say.
    id: 'eligible',
    labelKey: 'filters.preset.eligible',
    helpKey: 'filters.preset.eligibleHelp',
    patch: { eligibility: ['VERIFIED_ELIGIBLE'] },
  },
  {
    id: 'applied',
    labelKey: 'filters.preset.applied',
    helpKey: 'filters.preset.appliedHelp',
    patch: { status: ['APPLIED'] },
  },
];

const POSTED_WITHIN = [
  { value: '', labelKey: 'filters.posted.any' },
  { value: '3', labelKey: 'filters.posted.3' },
  { value: '7', labelKey: 'filters.posted.7' },
  { value: '14', labelKey: 'filters.posted.14' },
  { value: '30', labelKey: 'filters.posted.30' },
  { value: '90', labelKey: 'filters.posted.90' },
];

/**
 * The on/off filters, each with the section it belongs to and, where the
 * consequence is not obvious from the words, one sentence saying it.
 */
const TOGGLES = [
  { key: 'saved_only', labelKey: 'filters.toggle.saved', section: 'quick' },
  {
    key: 'has_salary',
    labelKey: 'filters.toggle.hasSalary',
    section: 'quick',
    helpKey: 'filters.toggle.hasSalaryHelp',
  },
  { key: 'remote_only', labelKey: 'filters.toggle.remote', section: 'quick' },
  {
    key: 'latam_only',
    labelKey: 'filters.toggle.latam',
    section: 'place',
    helpKey: 'filters.toggle.latamHelp',
  },
  {
    key: 'worldwide_only',
    labelKey: 'filters.toggle.worldwide',
    section: 'place',
    helpKey: 'filters.toggle.worldwideHelp',
  },
  {
    key: 'enriched_only',
    labelKey: 'filters.toggle.enriched',
    section: 'advanced',
    helpKey: 'filters.toggle.enrichedHelp',
  },
  // The only switch here that shows MORE. Unticked by default, and the count of
  // what it would add is printed above the results rather than left implicit.
  {
    key: 'include_ineligible',
    labelKey: 'filters.toggle.includeIneligible',
    section: 'quality',
    helpKey: 'filters.includeIneligibleHelp',
  },
  // The second switch that shows MORE, and a separate one on purpose. The
  // employer rules you out; your search rules the WORK out. One control for
  // both would make "show me the sales roles I filtered out" also reveal
  // postings that state a requirement you cannot meet.
  {
    key: 'include_off_target',
    labelKey: 'filters.toggle.includeOffTarget',
    section: 'quality',
    helpKey: 'filters.includeOffTargetHelp',
  },
  // The third, and the one that is NOT a rejection. An employer ruled you out;
  // your search ruled the work out; and this one is neither -- the posting
  // simply never said where it hires.
  //
  // Off by default since 2026-09-07, and that was the release-blocking half of
  // a correction. 12,574 of 19,469 open postings are unresolved on geography,
  // and mixed into best matches they buried the ones she can actually take:
  // the twenty highest-scoring were a hybrid role in Gurugram, offices in
  // Boston, Dublin and Bengaluru, and one eligible job. Compatibility ranked
  // them and eligibility never got to speak.
  {
    key: 'include_unresolved',
    labelKey: 'filters.toggle.includeUnresolved',
    section: 'quality',
    helpKey: 'filters.includeUnresolvedHelp',
  },
  // The fourth, and the only one that is a PREFERENCE rather than a fact about
  // a posting. The three above are things that happened to the search; this is
  // a level she said she did not want to see.
  //
  // Does nothing until she says so: the excluded list is empty by default and
  // "not preferred" is deliberately not read as "prohibited".
  {
    key: 'include_excluded_seniority',
    labelKey: 'filters.toggle.includeExcludedSeniority',
    section: 'quality',
    helpKey: 'filters.includeExcludedSeniorityHelp',
  },
  // The same kind of switch, for a way of working she said never to show.
  // A preference, never eligibility, and it does nothing until she says so.
  {
    key: 'include_excluded_work_model',
    labelKey: 'filters.toggle.includeExcludedWorkModel',
    section: 'quality',
    helpKey: 'filters.includeExcludedWorkModelHelp',
  },
  // The fifth switch that shows MORE, and the only one whose effect depends on
  // what the person has CONFIRMED about themselves.
  //
  // A search describes where somebody has been. For anybody moving into a new
  // field that is precisely the wrong description, and it blocks every posting
  // in the field they are moving TO. This asks the server to stop hiding the
  // postings that ask for things their own confirmed evidence answers -- not
  // to score them higher, and not to hide anything.
  //
  // It does nothing at all until something has been confirmed, which is
  // correct rather than broken: there is no evidence to transfer yet.
  {
    key: 'include_transferable',
    labelKey: 'filters.toggle.includeTransferable',
    section: 'entry',
    helpKey: 'filters.includeTransferableHelp',
  },
];

/**
 * The most previous experience a posting may DEMAND.
 *
 * Every value here EXCLUDES the postings that stated no minimum, and the help
 * sentence under the control says so. That is the same rule `country` follows
 * -- a posting that resolved to no country does not answer a request for one
 * -- and it is the difference between a list of jobs that said they would take
 * a beginner and a list of jobs that simply never mentioned it.
 */
const EXPERIENCE_YEARS = [
  { value: '', labelKey: 'filters.experience.any' },
  { value: '0', labelKey: 'filters.experience.none' },
  { value: '1', labelKey: 'filters.experience.upTo1' },
  { value: '2', labelKey: 'filters.experience.upTo2' },
  { value: '5', labelKey: 'filters.experience.upTo5' },
];

/** Chip labels for the number filters, so a chip can name what it removes. */
//
// FUNCTIONS, resolved at render. A table of finished strings here would be
// built at module load -- before `setLocale` has read the stored choice --
// and every chip in the product would be frozen in whichever language the
// module happened to load in. That is the same defect `STATUS_OPTIONS` had.
const NUMBER_CHIPS = {
  min_score: (value) => t('chip.minScore', { n: value }),
  max_score: (value) => t('chip.maxScore', { n: value }),
  min_confidence: (value) => t('chip.minConfidence', { n: value }),
  posted_within_days: (value) => (Number(value) === 1
    ? t('chip.postedWithinOne')
    : t('chip.postedWithin', { n: value })),
  experience_max_years: (value) => (Number(value) === 0
    ? t('chip.experienceNone')
    : t('chip.experienceUpTo', { n: value })),
};

/**
 * Build the panel once. Returns the element plus two sync functions, so
 * re-rendering never steals focus from the search box.
 */
/**
 * A node whose words come from the catalogue, remembered with its key.
 *
 * The panel is built ONCE -- rebuilding it would lose focus, collapse every
 * open disclosure and drop a half-typed phrase -- so every label in it is
 * resolved before `setLocale` has read the stored choice. `relabel()` fixed
 * the labels held in TABLES and could not fix the ones written inline,
 * because nothing recorded what they were: "Start from", "Search", "Must
 * mention", "Posted" and both slider labels sat in English on an otherwise
 * Portuguese rail.
 *
 * Now they record themselves. A label added tomorrow is relabelled without
 * anybody adding a line to `relabel`, which is the difference between a fix
 * and the same defect arriving again next quarter.
 *
 * Module scope rather than inside the panel, because `phraseList` and
 * `slider` are module-level functions and build labels too.
 */
const RELABELLED = new Map();

function fixed(node, key) {
  RELABELLED.set(node, key);
  return node;
}


export function createFilterPanel(store, { searchHost = null } = {}) {
  const root = el('form', {
    className: 'filters',
    attrs: { 'aria-label': t('rail.filters') },
    on: { submit: (event) => event.preventDefault() },
  });

  // -- starting points ---------------------------------------------------
  const presetButtons = new Map();
  const presetList = el('div', {
    className: 'presets',
    attrs: { role: 'group', 'aria-label': t('filters.startingPoints') },
  });
  for (const preset of PRESETS) {
    // `data-preset` carries the STABLE id beside the translated label, the
    // same way the status cell is built over stable ids with localized text.
    // A test that had to find this button by its English words would go blind
    // the moment the reader switched to Portuguese, which is the one language
    // this product must never be tested in less carefully.
    const node = button(labelOf(preset), () => {
      const base = clearedFilters(store.get());
      store.replaceAll({ ...base, ...preset.patch, view: base.view, sort: base.sort, direction: base.direction });
    }, {
      className: 'preset',
      attrs: { title: helpOf(preset), 'aria-pressed': 'false', 'data-preset': preset.id },
    });
    presetButtons.set(preset.id, node);
    presetList.appendChild(node);
  }
  root.appendChild(el('div', { className: 'filters__block filters__block--presets' }, [
    fixed(el('h2', { className: 'filters__label' }), 'filters.startFrom'),
    presetList,
  ]));

  // -- one host per section ----------------------------------------------
  const sections = new Map();
  for (const section of SECTIONS) {
    const body = el('div', { className: 'fsec__body' });
    const count = el('span', { className: 'fsec__count', props: { hidden: true } });
    const clearButton = button(t('action.clear'), () => {
      const patch = {};
      for (const key of section.keys) {
        // `include_ineligible` is excluded for exactly the reason
        // `clearedFilters` already gives about the same key: its DEFAULT is
        // false, so resetting it NARROWS the list. Every other key a Clear
        // button touches widens it. Clearing "How good a match" used to make
        // jobs disappear, which is the opposite of what the word promises.
        if (key === 'include_ineligible' || key === 'include_off_target'
          || key === 'include_unresolved' || key === 'include_excluded_seniority'
          || key === 'include_excluded_work_model') continue;
        patch[key] = DEFAULTS[key];
      }
      store.set(patch);
    }, { className: 'fsec__clear', attrs: { title: t('filters.clearSection', { section: labelOf(section) }) } });
    clearButton.hidden = true;
    // CLOSED, and the `open` flag on the section definition is no longer read.
    //
    // It was tuned for a 288px rail where four open sections read as a column
    // of controls. In the horizontal panel that replaced it the sections are a
    // grid, and a grid row is as tall as its tallest cell: four open sections
    // made the panel 1,232px, which pushed the first job card off the screen
    // and reproduced the problem the rail was removed to fix.
    //
    // `syncSectionCounts` opens any section that HOLDS a filter, so nothing
    // in force is ever behind a closed summary.
    const node = el('details', {
      className: 'fsec',
    }, [
      el('summary', { className: 'fsec__summary' }, [
        el('span', { className: 'fsec__label', text: labelOf(section) }),
        count,
      ]),
      section.helpKey ? el('p', { className: 'fsec__help', text: helpOf(section) }) : null,
      body,
      clearButton,
    ]);
    root.appendChild(node);
    sections.set(section.key, { section, node, body, count, clearButton, facetHost: null });
  }

  const into = (key) => sections.get(key).body;

  // -- find --------------------------------------------------------------
  //
  // THE SEARCH BOX, IN THE DISCOVER TOOLBAR. It filters the list already
  // collected, by title, company, place and posting text, through the local
  // server's own `/api/jobs?search=`: nothing typed here leaves this machine.
  // It is the same `search` state as before, so it combines with every filter
  // in this panel, shows as a chip and survives a reload in the address.
  // Debounced so a word is one request rather than one per key.
  //
  // `pendingSearch` is what the debounce is holding, or null, and
  // `searchFromBox` is true only while the box itself is writing. Together
  // they tell a change the box made from one made elsewhere ("Clear all", a
  // chip's x, the back button), and an external change to `search` CANCELS
  // the pending one: otherwise a word typed a moment before "Clear all" came
  // back 250 ms after it.
  let pendingSearch = null;
  let searchFromBox = false;
  const pushSearch = debounce((value) => {
    pendingSearch = null;
    searchFromBox = true;
    try {
      store.set({ search: value });
    } finally {
      searchFromBox = false;
    }
  }, 250);
  const searchInput = el('input', {
    className: 'input input--search toolsearch__input',
    attrs: {
      type: 'search', id: 'f-search', autocomplete: 'off', maxlength: String(SEARCH_MAX),
      placeholder: t('filters.searchPlaceholder'),
    },
    on: {
      input: (event) => {
        searchClear.hidden = !event.target.value;
        pendingSearch = event.target.value;
        pushSearch(event.target.value);
      },
      // Escape empties the box, the way a native search field does in most
      // browsers; handled here so it behaves the same in all of them.
      keydown: (event) => {
        if (event.key === 'Escape' && searchInput.value) {
          event.preventDefault();
          clearSearch();
        }
      },
    },
  });
  const searchClear = button('×', () => { clearSearch(); searchInput.focus(); }, {
    className: 'toolsearch__clear',
    ariaLabel: t('filters.searchClear'),
    attrs: { id: 'f-search-clear' },
  });
  searchClear.hidden = true;
  function clearSearch() {
    searchInput.value = '';
    searchClear.hidden = true;
    pushSearch.flush('');
  }
  const searchBlock = el('div', { className: 'toolsearch', attrs: { role: 'search' } }, [
    fixed(
      el('label', { className: 'sr-only', attrs: { for: 'f-search' } }),
      'filters.search',
    ),
    searchInput,
    searchClear,
  ]);
  if (searchHost) searchHost.appendChild(searchBlock);
  else into('find').appendChild(el('div', { className: 'filters__block' }, [searchBlock]));

  // FOUR PHRASE LISTS, IN TWO PAIRS, AND THE ORDER SAYS WHICH IS WHICH.
  //
  // The first two decide WHETHER a posting is in the list. The second two
  // decide only WHERE IN IT it appears, and cannot remove anything -- which is
  // what makes "I would rather not see agency work" expressible at all. Before
  // them the only way to say it was "never show me agency work".
  //
  // The hint under the soft pair is not decoration: a control that reorders is
  // indistinguishable from one that filters until somebody notices the count
  // did not move, and by then they have drawn the wrong conclusion about it.
  const wanted = phraseList('f-keyword', 'filters.mustMention', 'keyword', store);
  const unwanted = phraseList('f-exclude', 'filters.mustNotMention', 'exclude_keyword', store);
  const preferred = phraseList('f-prefer', 'filters.prefer', 'prefer_keyword', store);
  const avoided = phraseList('f-avoid', 'filters.avoid', 'avoid_keyword', store);
  into('find').appendChild(el('div', { className: 'filters__block' }, [wanted.root, unwanted.root]));
  into('find').appendChild(el('div', { className: 'filters__block' }, [
    fixed(el('p', { className: 'filters__hint' }), 'filters.softHint'),
    preferred.root,
    avoided.root,
  ]));

  // -- how good a match --------------------------------------------------
  const scoreSlider = slider(
    'f-min-score', 'filters.matchAtLeast', 'score',
    (value) => store.set({ min_score: value }),
  );
  const confidenceSlider = slider(
    'f-min-conf', 'filters.detailAtLeast', 'confidence',
    (value) => store.set({ min_confidence: value }),
  );
  into('quality').appendChild(el('div', { className: 'filters__block' }, [
    el('div', { className: 'filters__row' }, [
      scoreSlider.root,
      helpNote(t('filters.matchAtLeast'), ['badge.matchHelp']),
    ]),
    fixed(el('p', { className: 'filters__hint' }), 'filters.qualityHint'),
    el('div', { className: 'filters__row' }, [
      confidenceSlider.root,
      helpNote(t('filters.detailAtLeast'), ['badge.confidenceHelp']),
    ]),
  ]));

  // -- toggles, each into its own section --------------------------------
  const toggleInputs = new Map();
  const toggleHosts = new Map();
  for (const toggle of TOGGLES) {
    if (!toggleHosts.has(toggle.section)) {
      const list = el('div', { className: 'toggles' });
      toggleHosts.set(toggle.section, list);
      into(toggle.section).appendChild(list);
    }
    const id = `f-${toggle.key}`;
    const input = el('input', {
      className: 'checkbox',
      attrs: { type: 'checkbox', id },
      on: { change: (event) => store.set({ [toggle.key]: event.target.checked }) },
    });
    toggleInputs.set(toggle.key, input);
    const label = el('label', { className: 'toggle', attrs: { for: id } }, [
      input, el('span', { text: labelOf(toggle) }),
    ]);
    toggleHosts.get(toggle.section).appendChild(label);
    if (toggle.helpKey) {
      toggleHosts.get(toggle.section).appendChild(
        el('p', { className: 'toggle__help', text: helpOf(toggle) }),
      );
    }
  }

  // -- posted within -----------------------------------------------------
  const postedSelect = el('select', {
    className: 'select',
    attrs: { id: 'f-posted' },
    on: {
      change: (event) => store.set({
        posted_within_days: event.target.value === '' ? null : Number(event.target.value),
      }),
    },
  }, POSTED_WITHIN.map((option) => el('option', {
    text: labelOf(option), attrs: { value: option.value },
  })));
  into('quick').appendChild(el('div', { className: 'filters__block' }, [
    fixed(
      el('label', { className: 'filters__sublabel', attrs: { for: 'f-posted' } }),
      'filters.posted',
    ),
    postedSelect,
  ]));

  // -- how much previous experience a posting may demand ------------------
  const experienceSelect = el('select', {
    className: 'select',
    attrs: { id: 'f-experience-years' },
    on: {
      change: (event) => store.set({
        experience_max_years: event.target.value === '' ? null : Number(event.target.value),
      }),
    },
  }, EXPERIENCE_YEARS.map((option) => el('option', {
    text: labelOf(option), attrs: { value: option.value },
  })));
  into('entry').appendChild(el('div', { className: 'filters__block' }, [
    fixed(
      el('label', { className: 'filters__sublabel', attrs: { for: 'f-experience-years' } }),
      'filters.experience',
    ),
    experienceSelect,
    // The sentence that keeps the control honest. Without it "Up to 1 year"
    // reads as "jobs a beginner can do", and it is not: it is jobs that SAID
    // they want at most a year, which is a much smaller and much more useful
    // list than every posting that never raised the subject.
    fixed(el('p', { className: 'toggle__help' }), 'filters.experienceHelp'),
  ]));

  // -- minimum salary, which is meaningless without its currency ----------
  //
  // The server answers 400 for a figure with no currency, because nothing in
  // this system converts between them and "at least 120,000" over a corpus
  // quoting USD, EUR, BRL and INR compares amounts that are not comparable.
  // So the two controls are ONE control here: typing a figure with no
  // currency chosen would be a request the person cannot see is invalid.
  const salaryCurrency = el('select', {
    className: 'select',
    attrs: { id: 'f-salary-currency', 'aria-label': t('filters.salaryCurrency') },
    on: { change: (event) => store.set({ salary_currency: event.target.value ? [event.target.value] : [] }) },
  });
  const salaryAmount = el('input', {
    className: 'input',
    // `aria-label`, not the group heading: an `<h2>` above a control is not a
    // label, and a screen reader reading this input announced nothing. Found
    // by `test_every_control_has_a_name_a_screen_reader_can_read`, which is
    // the first thing that has ever checked.
    attrs: {
      type: 'number', id: 'f-min-salary', min: '0', step: '1000',
      placeholder: 'e.g. 120000', 'aria-label': t('filters.minSalaryYear'),
    },
    on: {
      change: (event) => {
        const value = event.target.value === '' ? null : Number(event.target.value);
        store.set({ min_salary: value });
      },
    },
  });
  into('pay').appendChild(el('div', { className: 'filters__block' }, [
    fixed(
      el('label', { className: 'filters__sublabel', attrs: { for: 'f-min-salary' } }),
      'filters.minSalary',
    ),
    // The unit, beside the box rather than only inside the label. There is no
    // period selector because there is nothing to select: a posting quoting a
    // monthly or hourly rate is converted to a year before it is compared, so
    // one number in one unit is the whole question. Saying "yearly" in the
    // label and nowhere else left a person typing a Brazilian monthly figure
    // with no way to find out it was being read as an annual one.
    el('div', { className: 'filters__pair' }, [
      salaryAmount,
      fixed(el('span', { className: 'filters__unit' }), 'filters.aYear'),
      salaryCurrency,
    ]),
    fixed(el('p', { className: 'filters__hint' }), 'filters.salaryHint'),
  ]));

  // -- facet hosts, one per section --------------------------------------
  for (const [key, entry] of sections) {
    if (!FACET_GROUPS.some((group) => group.section === key)) continue;
    entry.facetHost = el('div', { className: 'facets' });
    entry.body.appendChild(entry.facetHost);
  }

  root.appendChild(button(t('action.clearAllFilters'), () => {
    store.replaceAll(clearedFilters(store.get()));
  }, { className: 'btn btn--wide' }));

  let lastFacetSignature = '';

  function syncFacets(facets, state) {
    const signature = JSON.stringify(facets || {});
    if (signature === lastFacetSignature) {
      updateFacetChecks(root, state);
      return;
    }
    lastFacetSignature = signature;
    for (const entry of sections.values()) {
      if (entry.facetHost) clear(entry.facetHost);
    }
    for (const group of FACET_GROUPS) {
      // Technology and signal are the same membership index read two ways:
      // "which tools" and "which of our signals matched" are one query to the
      // database and two questions to a person, so the server emits both and
      // each group reads its own. This used to read a `signal` facet nobody
      // produced, so the group only appeared once a value was already chosen:
      // a control that could not be clicked.
      const rows = (facets && facets[group.key]) || [];
      const chosen = state[group.key] || [];
      if (!rows.length && !chosen.length) continue;
      const host = sections.get(group.section).facetHost;
      if (host) host.appendChild(facetGroup(group, rows, chosen, store));
    }
    // The currency select is fed by the same facets, so it can only offer a
    // currency the corpus actually contains.
    syncCurrencyOptions((facets && facets.salary_currency) || []);
  }

  function syncCurrencyOptions(rows) {
    // The chosen value comes from the STORE, not from the select's own value.
    // Reading the DOM made the order of the two sync calls load-bearing: on a
    // fresh `?salary_currency=BRL` load, `syncState` set `.value = 'BRL'`
    // before any option existed, the browser silently kept '', and this
    // function then "preserved" that empty string. The URL said BRL and the
    // control said nothing.
    const chosen = (store.get().salary_currency || [])[0] || '';
    const values = rows.map((row) => row.key).filter((key) => key && key !== 'NOT_STATED');
    // A chosen currency the current facets no longer report must still be
    // shown and removable: the same rule `facetGroup` follows, and for the
    // same reason, that filtering can narrow a facet out from under its value.
    if (chosen && !values.includes(chosen)) values.unshift(chosen);
    clear(salaryCurrency);
    salaryCurrency.appendChild(el('option', { text: t('filters.currencyPlaceholder'), attrs: { value: '' } }));
    for (const value of values) {
      salaryCurrency.appendChild(el('option', { text: value, attrs: { value } }));
    }
    salaryCurrency.value = chosen;
  }

  function syncState(state, meta = {}) {
    const search = state.search || '';
    const changed = meta.changed || [];
    const touched = changed.includes('search') || changed.includes('*');
    if (touched && !searchFromBox && pendingSearch !== null) {
      pushSearch.cancel();
      pendingSearch = null;
      searchInput.value = search;
    }
    if (document.activeElement !== searchInput) searchInput.value = search;
    searchClear.hidden = !searchInput.value;
    scoreSlider.set(state.min_score);
    confidenceSlider.set(state.min_confidence);
    if (document.activeElement !== salaryAmount) {
      salaryAmount.value = state.min_salary === null || state.min_salary === undefined
        ? '' : String(state.min_salary);
    }
    salaryCurrency.value = (state.salary_currency || [])[0] || '';
    wanted.set(state.keyword || []);
    unwanted.set(state.exclude_keyword || []);
    // THE SOFT PAIR REDRAWS HERE TOO, and forgetting it is not cosmetic: a
    // preference restored from `localStorage` was reordering the list on every
    // visit with nothing on screen saying why. The value acted and the control
    // looked empty, which is worse than a control that does nothing.
    preferred.set(state.prefer_keyword || []);
    avoided.set(state.avoid_keyword || []);
    for (const [key, input] of toggleInputs) input.checked = Boolean(state[key]);
    postedSelect.value = state.posted_within_days === null || state.posted_within_days === undefined
      ? '' : String(state.posted_within_days);
    experienceSelect.value = state.experience_max_years === null
      || state.experience_max_years === undefined
      ? '' : String(state.experience_max_years);
    updateFacetChecks(root, state);
    for (const preset of PRESETS) {
      presetButtons.get(preset.id).setAttribute('aria-pressed', matchesPreset(state, preset) ? 'true' : 'false');
    }
    syncSectionCounts(state);
  }

  /**
   * Each section says how many of its own filters are active.
   *
   * This is what makes a closed section safe. Without it, narrowing inside
   * `Pay`, collapsing it and forgetting produces an empty result list with no
   * visible cause, which is the exact failure progressive disclosure invites.
   */
  function syncSectionCounts(state) {
    for (const entry of sections.values()) {
      const active = countActive(state, entry.section.keys);
      entry.count.textContent = String(active);
      entry.count.hidden = active === 0;
      entry.clearButton.hidden = active === 0;
      entry.node.classList.toggle('is-active', active > 0);
      // A section that is narrowing the list opens itself, once. `hasOpened`
      // is what keeps it from fighting the reader: after they have collapsed
      // it by hand, the next state change must not reopen it.
      if (active > 0 && !entry.hasOpened) {
        entry.node.open = true;
        entry.hasOpened = true;
      }
      entry.count.setAttribute(
        'aria-label',
        t('filters.activeIn', { n: active, section: labelOf(entry.section) }),
      );
    }
  }

  /**
   * Put the current language on every word this panel drew itself.
   *
   * Walks the existing nodes rather than rebuilding: a rebuild would lose
   * focus, collapse every open section and throw away whatever is in the
   * search box. Nothing an employer wrote is touched -- facet labels come
   * from the person's own configuration and stay as the configuration wrote
   * them.
   */
  function relabel() {
    root.setAttribute('aria-label', t('rail.filters'));

    for (const preset of PRESETS) {
      const node = presetButtons.get(preset.id);
      if (!node) continue;
      node.textContent = labelOf(preset);
      node.setAttribute('title', helpOf(preset));
    }

    // `sections` maps a key to a RECORD, not to a node. The first version of
    // this walked it as though the value were the element and silently
    // relabelled nothing, which looked exactly like the bug it was written to
    // fix.
    for (const section of SECTIONS) {
      const entry = sections.get(section.key);
      if (!entry) continue;
      const heading = entry.node.querySelector('.fsec__label');
      if (heading) heading.textContent = labelOf(section);
      const hint = entry.node.querySelector('.fsec__help');
      if (hint) hint.textContent = helpOf(section);
      if (entry.clearButton) {
        entry.clearButton.textContent = t('action.clear');
        entry.clearButton.setAttribute(
          'title', t('filters.clearSection', { section: labelOf(section) }),
        );
      }
    }

    for (const toggle of TOGGLES) {
      const input = toggleInputs.get(toggle.key);
      const row = input ? input.closest('.toggle') : null;
      if (!row) continue;
      const words = row.querySelector('span');
      if (words) words.textContent = labelOf(toggle);
      const hint = row.nextElementSibling;
      if (hint && hint.classList.contains('toggle__help')) {
        hint.textContent = helpOf(toggle);
      }
    }

    searchInput.setAttribute('placeholder', t('filters.searchPlaceholder'));
    searchClear.setAttribute('aria-label', t('filters.searchClear'));

    // And every label that recorded its own key when it was built.
    for (const [node, key] of RELABELLED) node.textContent = t(key);

    // The one control whose OPTIONS are words rather than stored values.
    // `posted_within_days` sends a number; what a person reads is "the last
    // 7 days", and that phrase is ours.
    for (const option of postedSelect.options) {
      const row = POSTED_WITHIN.find((entry) => entry.value === option.value);
      if (row) option.textContent = t(row.labelKey);
    }
  }

  return {
    root, syncFacets, syncState, relabel, focusSearch: () => searchInput.focus(),
  };
}

/** How many of these keys hold something other than their default. */
export function countActive(state, keys) {
  let total = 0;
  for (const key of keys) {
    const value = state[key];
    if (value === null || value === undefined) continue;
    if (Array.isArray(value)) total += value.length;
    else if (typeof value === 'boolean') total += value ? 1 : 0;
    else if (value !== '' && value !== DEFAULTS[key]) total += 1;
  }
  return total;
}

function updateFacetChecks(host, state) {
  for (const input of host.querySelectorAll('input[type="checkbox"][data-facet]')) {
    const values = state[input.dataset.facet] || [];
    input.checked = values.includes(input.dataset.value);
  }
}

/**
 * One vocabulary for one answer, in the THIRD place it is rendered.
 *
 * `badges.js` says so in as many words above `eligibilityWords`: "One
 * vocabulary, one function, both views." The rail is a third view and never
 * got the function, so a card said "Did not say" and the filter beside it
 * offered "Unresolved 6". A person cannot connect those, and the whole point
 * of the eligibility work is that they read one answer.
 *
 * Only the eligibility facet is translated. Every other bucket goes through
 * `vocabLabel`, which is right for a currency or a contract type.
 */
function facetValueLabel(groupKey, row) {
  if (groupKey === 'eligibility') return eligibilityWords(row.key);
  // Named families rather than a bare `vocabLabel`, because both of these
  // vocabularies contain `NOT_STATED`, which `period` already owns. `tVocab`
  // searches the families in order and would return the first hit, so a value
  // in two of them resolves by accident of ordering -- which is exactly what
  // `test_the_state_families_are_disjoint` refuses. Asking by NAME here means
  // neither vocabulary has to join that search at all.
  if (groupKey === 'experience_requirement') return tState('experience', row.key);
  if (groupKey === 'entry_signal') return tState('entrySignal', row.key);
  const named = row.label && row.label !== row.key;
  return named ? row.label : vocabLabel(row.key);
}

function facetGroup(group, rows, chosen, store) {
  const list = el('div', { className: 'facet__list', attrs: { role: 'group', 'aria-label': labelOf(group) } });
  const seen = new Set();

  for (const row of rows) {
    seen.add(row.key);
    // A label the SERVER supplied is a human name, a company's say, and must
    // be shown as it came. A label that is just the key is a vocabulary value,
    // and `vocabLabel` is what turns `NOT_STATED` into "Not stated" while
    // leaving `BR` and `USD` alone. Sentence-casing everything would render
    // "Northwind systems".
    list.appendChild(facetRow(
      group.key, row.key, facetValueLabel(group.key, row), row.count, chosen, store,
    ));
  }
  // A chosen value the current facets no longer report must still be removable.
  for (const value of chosen) {
    if (!seen.has(value)) {
      list.appendChild(
        facetRow(group.key, value, facetValueLabel(group.key, { key: value }), 0, chosen, store),
      );
    }
  }

  return el('details', {
    className: 'facet',
    props: { open: chosen.length > 0 },
  }, [
    el('summary', { className: 'facet__summary' }, [
      el('span', { text: labelOf(group) }),
      chosen.length ? el('span', { className: 'facet__count', text: String(chosen.length) }) : null,
    ]),
    list,
  ]);
}

function facetRow(facetKey, value, label, count, chosen, store) {
  const id = `f-${facetKey}-${String(value).replace(/[^a-z0-9_-]/gi, '-')}`;
  const input = el('input', {
    className: 'checkbox',
    attrs: { type: 'checkbox', id },
    dataset: { facet: facetKey, value },
    props: { checked: chosen.includes(value) },
    on: {
      change: (event) => {
        const current = store.get()[facetKey] || [];
        const next = event.target.checked
          ? [...current, value]
          : current.filter((item) => item !== value);
        store.set({ [facetKey]: next });
      },
    },
  });
  return el('label', { className: 'facet__row', attrs: { for: id } }, [
    input,
    el('span', { className: 'facet__name', text: label }),
    el('span', { className: 'facet__n num', text: String(count) }),
  ]);
}

/**
 * A list of free-text phrases: type, Enter, and it becomes a removable tag.
 *
 * Not a facet, because a keyword is not a value the corpus offers, it is one
 * the person invents. Enter rather than a Save button because the list is the
 * confirmation: a phrase you can see in the list is a phrase that is applied.
 */
function phraseList(id, labelKey, key, store) {
  // A KEY rather than a finished word. This is built once, before `setLocale`
  // has read the stored choice, so a resolved label here is a label frozen in
  // whichever language the module happened to load in -- which is what
  // "Must mention" was doing on a Portuguese screen.
  const label = () => t(labelKey);
  const tags = el('div', { className: 'phrases__tags' });
  const input = el('input', {
    className: 'input',
    attrs: { type: 'text', id, placeholder: t('filters.phrasePlaceholder'), autocomplete: 'off' },
    on: {
      keydown: (event) => {
        if (event.key !== 'Enter') return;
        // Enter inside a form submits it. The panel already blocks its own
        // submit, but stopping here keeps the reason local to the control.
        event.preventDefault();
        const value = event.target.value.trim();
        if (!value) return;
        const current = store.get()[key] || [];
        if (!current.includes(value)) store.set({ [key]: [...current, value] });
        event.target.value = '';
      },
    },
  });
  const root = el('div', { className: 'phrases' }, [
    fixed(el('label', { className: 'filters__sublabel', attrs: { for: id } }), labelKey),
    input,
    tags,
  ]);
  return {
    root,
    set(values) {
      clear(tags);
      for (const value of values) {
        tags.appendChild(el('span', { className: 'chip chip--tag' }, [
          el('span', { className: 'chip__text', text: value }),
          button('×', () => {
            store.set({ [key]: (store.get()[key] || []).filter((item) => item !== value) });
          }, {
            className: 'chip__x',
            ariaLabel: t('chip.removeValue', { label: label().toLowerCase(), value }),
          }),
        ]));
      }
    },
  };
}

function slider(id, labelKey, kind, onChange) {
  const output = el('output', {
    className: `slider__out num slider__out--${kind}`,
    text: t('filters.anyValue'),
    attrs: { for: id },
  });
  const input = el('input', {
    className: `slider slider--${kind}`,
    attrs: { type: 'range', min: '0', max: '100', step: '5', id },
    on: {
      input: (event) => {
        output.textContent = event.target.value === '0'
          ? t('filters.anyValue')
          : event.target.value;
      },
      change: (event) => onChange(event.target.value === '0' ? null : Number(event.target.value)),
    },
  });
  const root = el('div', { className: 'slider__row' }, [
    fixed(el('label', { className: 'filters__sublabel', attrs: { for: id } }), labelKey),
    el('div', { className: 'slider__control' }, [input, output]),
  ]);
  return {
    root,
    set(value) {
      const number = value === null || value === undefined ? 0 : Number(value);
      input.value = String(number);
      output.textContent = number === 0 ? t('filters.anyValue') : String(number);
    },
  };
}

function matchesPreset(state, preset) {
  const expected = { ...clearedFilters(state), ...preset.patch };
  // Derived from the store's own key lists rather than spelled out again: the
  // hand-written version silently omitted every filter added after it, so a
  // preset stayed lit while a country filter narrowed the list under it.
  const keys = ['search', ...LIST_KEYS, ...NUMBER_KEYS, ...FLAG_KEYS];
  return keys.every((key) => {
    const left = state[key] ?? DEFAULTS[key];
    const right = expected[key] ?? DEFAULTS[key];
    if (Array.isArray(left) || Array.isArray(right)) {
      const a = [...(left || [])].sort();
      const b = [...(right || [])].sort();
      return a.length === b.length && a.every((value, index) => value === b[index]);
    }
    return left === right;
  });
}

// =========================================================================
// Active-filter chips
// =========================================================================

/** Every active filter, as a removable chip. Rendered above the results. */
export function renderChips(mount, state, store) {
  const chips = [];

  if (state.search) {
    chips.push(chip(t('chip.contains', { text: state.search }), () => store.set({ search: '' })));
  }
  // The tracker board narrows to its own nine statuses. Nine identical chips
  // is technically honest and practically unreadable, so it collapses into
  // one that says the same thing and removes the same thing. It is only
  // collapsed when the set is EXACTLY the board's: the moment the person
  // changes it, it is their filter again and every value shows individually.
  const boardNarrowing = isTrackedSet(state.status);
  if (boardNarrowing) {
    chips.push(chip(t('filters.trackingChip'), () => store.set({ status: [] })));
  }
  for (const group of FACET_GROUPS) {
    if (boardNarrowing && group.key === 'status') continue;
    for (const value of state[group.key] || []) {
      // `vocabLabel`, not `humanLabel`: the latter sentence-cases everything,
      // so the country chip for `BR` read "Br" and a currency read "Usd". A
      // code is not a word, and a filter chip that misspells the value the
      // person clicked is worse than one that shows the raw code.
      chips.push(chip(`${labelOf(group)}: ${facetValueLabel(group.key, { key: value })}`, () => {
        store.set({ [group.key]: (store.get()[group.key] || []).filter((item) => item !== value) });
      }));
    }
  }
  // Derived from NUMBER_KEYS rather than listed by hand. The hand-written
  // version omitted `max_score`, which meant a filter that came in through the
  // URL narrowed the results and offered nothing to remove it with.
  for (const key of NUMBER_KEYS) {
    const value = state[key];
    if (value === null || value === undefined) continue;
    if (key === 'min_salary') continue;
    const naming = NUMBER_CHIPS[key];
    chips.push(chip(naming ? naming(value) : `${key}: ${value}`, () => store.set({ [key]: null })));
  }
  if (state.min_salary !== null && state.min_salary !== undefined) {
    const currency = (state.salary_currency || [])[0] || '';
    // Clearing the figure clears the currency with it. The server rejects a
    // figure without one, so leaving a lone currency behind would leave a
    // chip that describes half a filter.
    chips.push(chip(
      currency
        ? t('chip.minSalaryCurrency', { amount: state.min_salary, currency })
        : t('chip.minSalary', { amount: state.min_salary }),
      () => store.set({ min_salary: null, salary_currency: [] }),
    ));
  }
  for (const toggle of TOGGLES) {
    // `include_ineligible` is not a filter and must not be chipped as one.
    //
    // Every other chip in this row NARROWS the list, and every one of them is
    // removed by "Clear all filters". This one WIDENS it, and `clearedFilters`
    // deliberately leaves it alone, because clearing a filter must never make
    // jobs disappear. So the row read "SHOWING ONLY [Include jobs with
    // eligibility conflicts x] - 1 filter active" immediately after somebody
    // pressed Clear all filters, which is the interface contradicting itself
    // about what it just did.
    //
    // The state is still visible and still reversible: the notice above the
    // list says how many are shown and offers to hide them again.
    if (toggle.key === 'include_ineligible' || toggle.key === 'include_off_target'
      || toggle.key === 'include_unresolved'
      || toggle.key === 'include_excluded_seniority'
      || toggle.key === 'include_excluded_work_model') continue;
    if (state[toggle.key]) {
      chips.push(chip(labelOf(toggle), () => store.set({ [toggle.key]: false })));
    }
  }

  if (!chips.length) {
    replace(mount, []);
    mount.hidden = true;
    return 0;
  }

  mount.hidden = false;
  replace(mount, [
    el('span', { className: 'chipbar__label', text: t('filters.showingOnly') }),
    ...chips,
    button(t('action.clearAll'), () => store.replaceAll(clearedFilters(store.get())), {
      className: 'chip chip--clear',
    }),
  ]);
  return chips.length;
}

function chip(label, onRemove) {
  return el('span', { className: 'chip chip--active' }, [
    el('span', { className: 'chip__text', text: label }),
    button('×', onRemove, {
      className: 'chip__x',
      ariaLabel: t('chip.removeFilter', { label }),
    }),
  ]);
}
