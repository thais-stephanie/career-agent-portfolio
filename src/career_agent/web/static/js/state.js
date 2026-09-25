/**
 * state.js -- the single store.
 *
 * There is exactly one place that knows what is being asked for: this module.
 * Cards and Table do not hold filters of their own, do not build query strings
 * of their own, and cannot drift apart -- because `apiQuery()` is derived from
 * the store and `view` is deliberately NOT part of it.
 *
 * That is the whole trick behind the hard requirement "identical filters must
 * produce identical job ids in both views": switching view changes a key that
 * the request cannot see.
 *
 * The store is serialised to `location.search`, so the back button works, a
 * reload restores everything, and a filtered view is a URL you can send to
 * yourself.
 */

/** Multi-value filters. Repeatable in the query string, per the API contract. */
export const LIST_KEYS = [
  'company', 'provider', 'role_class', 'status', 'eligibility', 'fit_band', 'signal',
  // The twelve section 16 listed as absent. They are ordinary list keys, so
  // the URL round trip, the chips, the active count and `clearedFilters` all
  // pick them up without a second mechanism.
  'country', 'region', 'worksite', 'seniority', 'employment_type',
  // ADDED HERE FIRST, on purpose. V1.5 shipped two controls that
  // rendered, stored a value and changed nothing for a whole session
  // because `apiQuery` builds the request from these lists and they had
  // not been touched. Adding a narrowing means adding it to the STORE as
  // well as to the server.
  'contract_regime',
  'salary_currency', 'salary_period', 'technology', 'keyword', 'exclude_keyword',
  // THE SOFT PAIR. They sort rather than filter, and they are in this list
  // for the same reason as everything else in it: a control that exists on
  // the server and not here renders, stores a value nothing reads, and moves
  // nothing. V1.5 shipped two of those for a whole session.
  'prefer_keyword', 'avoid_keyword',
  // WHAT THE POSTING ASKED FOR, and who it invited. Migration 0027.
  // Here FIRST, per the note above: a narrowing that exists on the server and
  // not in this list is a control that renders and does nothing.
  'experience_requirement', 'entry_signal',
];

/** Booleans that only appear in the URL when true. */
export const FLAG_KEYS = [
  'saved_only', 'has_salary', 'remote_only', 'enriched_only',
  'latam_only', 'worldwide_only',
  // The two flags that WIDEN. Every other key here narrows when true; these
  // undo narrowings the server applies by default. They live in the same list
  // because they round-trip through the URL identically, and being in the list
  // is what gives each a chip, a place in the active count and a reset.
  //
  // Two, not one, because they undo different things: an employer stating a
  // requirement you do not meet, and your own search setting work aside as a
  // different kind. One control for both would make "show me the roles I
  // filtered out" quietly also show the ones that rule you out.
  'include_ineligible',
  'include_off_target',
  // The third, and the only one that undoes something SHE did. It widens
  // exactly like the two above and round-trips through the URL the same way,
  // so it belongs in the same list; what it reveals is different, and only
  // she can have hidden it.
  'include_user_hidden',
  // The FOURTH and FIFTH, from the V1.5 eligibility correction, and they were
  // missing from this list for a session. Everything else about them existed
  // -- the server filter, the rail toggle, the banner and its button -- so
  // they rendered, they stored a value, and nothing read it: `apiQuery` builds
  // the request from `API_KEYS`, which is this list, so the request never
  // carried them and the list never moved.
  //
  // A control that cannot do anything is worse than a missing one. It is also
  // exactly what a store like this is for, and the lesson is that adding a
  // narrowing means adding it HERE as well as to the server.
  'include_unresolved',
  'include_excluded_seniority',
  'include_excluded_work_model',
  // NARROWS, to exactly what she hid. The restore view. A separate key from
  // the one above because "show me these as well" and "show me only these"
  // are different questions, and one flag answering both is how a widening
  // control comes to narrow.
  'user_hidden_only',
  // WIDENS, like `include_ineligible` and `include_off_target`, and it is the
  // only key in this file whose value depends on what the person has
  // CONFIRMED about themselves. Off by default; switching it on asks the
  // server to stop hiding postings that fire a signal their own confirmed
  // evidence answers. It cannot narrow: the server adds a disjunct to a
  // hiding clause and nothing else.
  'include_transferable',
];

/** Where each reveal choice is remembered between visits. */
export const ELIGIBILITY_KEY = 'careerAgent.includeIneligible.v1';
export const OFF_TARGET_KEY = 'careerAgent.includeOffTarget.v1';
export const UNRESOLVED_KEY = 'careerAgent.includeUnresolved.v1';
export const EXCLUDED_SENIORITY_KEY = 'careerAgent.includeExcludedSeniority.v1';
export const EXCLUDED_WORK_MODEL_KEY = 'careerAgent.includeExcludedWorkModel.v1';

/**
 * The two ad-hoc phrase lists, remembered between visits.
 *
 * WHY HERE AND NOT IN `search.local.yaml`
 * ---------------------------------------
 * Because a keyword filter is a VIEW and the search configuration is a
 * QUESTION. Every section of that file changes how a POSTING IS READ, and a
 * stored score is only true relative to the configuration that produced it --
 * so writing a phrase there bumps `config_version` and marks every stored score
 * in the corpus stale. Asking somebody to rescore a hundred thousand postings
 * because they typed "SAP" into a hide box would be the product punishing them
 * for using a control.
 *
 * They still have to SURVIVE, though, and until this they did not: they lived
 * in the URL, so they came back on a reload and were gone in a new tab. "I
 * never want to see this word again" is not a per-tab opinion.
 *
 * So they are remembered exactly where the other view preferences are, and
 * exactly as conditionally: THE URL STILL WINS, because a link somebody was
 * given has to describe what they will see.
 */
export const KEYWORD_KEY = 'careerAgent.keyword.v1';
export const EXCLUDE_KEYWORD_KEY = 'careerAgent.excludeKeyword.v1';

/** One remembered phrase list, or an empty one. */
function storedPhrases(key) {
  try {
    const raw = window.localStorage.getItem(key);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    // Shape-checked rather than trusted. `localStorage` is per-origin and this
    // origin is only ever this product, but a value written by an older build
    // reaching a newer one is an ordinary event and a filter built from a
    // number would be a 400 on every request afterwards.
    return Array.isArray(parsed) ? parsed.filter((v) => typeof v === 'string' && v) : [];
  } catch (error) {
    return [];
  }
}

function rememberPhrases(key, values) {
  try {
    if (values && values.length) window.localStorage.setItem(key, JSON.stringify(values));
    else window.localStorage.removeItem(key);
  } catch (error) {
    /* Applies for this visit either way. */
  }
}

const PREFER_KEYWORD_KEY = 'careerAgent.preferKeyword.v1';
const AVOID_KEYWORD_KEY = 'careerAgent.avoidKeyword.v1';

export function storedPreferredKeywords() {
  return storedPhrases(PREFER_KEYWORD_KEY);
}

export function rememberPreferredKeywords(values) {
  rememberPhrases(PREFER_KEYWORD_KEY, values);
}

export function storedAvoidedKeywords() {
  return storedPhrases(AVOID_KEYWORD_KEY);
}

export function rememberAvoidedKeywords(values) {
  rememberPhrases(AVOID_KEYWORD_KEY, values);
}

export function storedKeywords() {
  return storedPhrases(KEYWORD_KEY);
}

export function rememberKeywords(values) {
  rememberPhrases(KEYWORD_KEY, values);
}

export function storedExcludedKeywords() {
  return storedPhrases(EXCLUDE_KEYWORD_KEY);
}

export function rememberExcludedKeywords(values) {
  rememberPhrases(EXCLUDE_KEYWORD_KEY, values);
}

/**
 * The stored eligibility preference, or false.
 *
 * Wrapped, because `localStorage` throws rather than returning null in a
 * private window and in browsers set to block site data. A remembered
 * preference is not worth a blank page.
 */
export function storedIncludeIneligible() {
  try {
    return window.localStorage.getItem(ELIGIBILITY_KEY) === '1';
  } catch (error) {
    return false;
  }
}

export function rememberIncludeIneligible(value) {
  try {
    window.localStorage.setItem(ELIGIBILITY_KEY, value ? '1' : '0');
  } catch (error) {
    /* Applies for this visit either way. */
  }
}

export function storedIncludeOffTarget() {
  try {
    return window.localStorage.getItem(OFF_TARGET_KEY) === '1';
  } catch (error) {
    return false;
  }
}

export function rememberIncludeOffTarget(value) {
  try {
    window.localStorage.setItem(OFF_TARGET_KEY, value ? '1' : '0');
  } catch (error) {
    /* Applies for this visit either way. */
  }
}

export function storedIncludeUnresolved() {
  try {
    return window.localStorage.getItem(UNRESOLVED_KEY) === '1';
  } catch (error) {
    return false;
  }
}

export function rememberIncludeUnresolved(value) {
  try {
    window.localStorage.setItem(UNRESOLVED_KEY, value ? '1' : '0');
  } catch (error) {
    /* Applies for this visit either way. */
  }
}

export function storedIncludeExcludedSeniority() {
  try {
    return window.localStorage.getItem(EXCLUDED_SENIORITY_KEY) === '1';
  } catch (error) {
    return false;
  }
}

export function rememberIncludeExcludedSeniority(value) {
  try {
    window.localStorage.setItem(EXCLUDED_SENIORITY_KEY, value ? '1' : '0');
  } catch (error) {
    /* Applies for this visit either way. */
  }
}

export function storedIncludeExcludedWorkModel() {
  try {
    return window.localStorage.getItem(EXCLUDED_WORK_MODEL_KEY) === '1';
  } catch (error) {
    return false;
  }
}

export function rememberIncludeExcludedWorkModel(value) {
  try {
    window.localStorage.setItem(EXCLUDED_WORK_MODEL_KEY, value ? '1' : '0');
  } catch (error) {
    /* Applies for this visit either way. */
  }
}

/** Numbers, or null when unset. */
export const NUMBER_KEYS = [
  'min_score', 'max_score', 'min_confidence', 'posted_within_days', 'min_salary',
  // The most previous experience a posting may DEMAND. A number rather than a
  // list because "up to 2 years" is one threshold, and because the server
  // takes one integer. It EXCLUDES the postings that stated no minimum, which
  // is the same rule `country` follows: absence is not a match.
  'experience_max_years',
];

/**
 * Whether each view collapses an employer's one-role-per-location postings.
 *
 * Cards is the reading view, where the same job appearing eight times is
 * nineteen per cent of a shortlist spent on repetition. Table is the "show me
 * every row" view, and that is exactly why it stays ungrouped.
 *
 * This is a DEFAULT, not a derivation. `group_duplicates` is an ordinary store
 * key that goes into the query string and is reflected by a visible toggle; the
 * map below only decides what it starts as when the URL does not say. Switching
 * view therefore WRITES the new value into the store, in the open, rather than
 * apiQuery quietly reading `view` -- which would break the one structural
 * guarantee this module exists to give.
 */
export const VIEW_GROUPING = Object.freeze({ cards: true, table: false, kanban: false });

/** The three views. Named once so the URL parser and the header agree. */
export const VIEWS = Object.freeze(['cards', 'table', 'kanban']);

/**
 * The statuses the tracker board shows.
 *
 * Here rather than in `kanban.js` because this module must stay import-free --
 * it is the one place with no dependencies, which is what lets every other
 * module read the store without a cycle. `kanban.js` builds its columns from
 * this list and asserts it covers exactly these, so the two cannot drift.
 *
 * `DISCOVERED` is absent on purpose: it is the untouched state, and a board
 * whose first column holds every posting you have never looked at is the list
 * again with worse ergonomics.
 */
export const TRACKED_STATUSES = Object.freeze([
  'SHORTLISTED', 'TO_APPLY', 'APPLIED', 'INTERVIEW', 'OFFER', 'HIRED',
  'REJECTED', 'WITHDRAWN', 'ARCHIVED',
]);

export const DEFAULTS = Object.freeze({
  search: '',
  company: [],
  provider: [],
  role_class: [],
  status: [],
  eligibility: [],
  fit_band: [],
  signal: [],
  country: [],
  region: [],
  worksite: [],
  seniority: [],
  employment_type: [],
  // Beside its neighbour, and NOT only in the key list. The check below
  // exists because a key in a list with no default is a control that
  // renders and does nothing -- and it threw on load here, which is
  // louder and better: the whole interface stayed blank until this line
  // was written, rather than one filter quietly doing nothing.
  contract_regime: [],
  salary_currency: [],
  salary_period: [],
  technology: [],
  keyword: [],
  exclude_keyword: [],
  // The soft pair. Empty is the neutral value: a candidate who has set
  // neither gets exactly the ordering this product had before they existed.
  prefer_keyword: [],
  avoid_keyword: [],
  experience_requirement: [],
  entry_signal: [],
  experience_max_years: null,
  min_score: null,
  max_score: null,
  min_confidence: null,
  saved_only: false,
  has_salary: false,
  remote_only: false,
  enriched_only: false,
  latam_only: false,
  worldwide_only: false,
  include_ineligible: false,
  include_off_target: false,
  include_user_hidden: false,
  include_unresolved: false,
  include_excluded_seniority: false,
  include_excluded_work_model: false,
  user_hidden_only: false,
  include_transferable: false,
  posted_within_days: null,
  // Meaningless without a currency, and the server says so with a 400 rather
  // than comparing amounts that are not comparable. The panel pairs the two
  // controls; this is only the default.
  min_salary: null,
  // Not in FLAG_KEYS on purpose. Grouping excludes no posting from the world;
  // it collapses several representations of one role into the row that stands
  // for them. So it is a presentation control like `sort`, not a filter: it
  // gets no chip, it does not count towards "3 filters active", and clearing
  // the filters leaves it alone.
  group_duplicates: VIEW_GROUPING.cards,
  sort: 'score',
  direction: 'desc',
  limit: 60,
  offset: 0,
  // -- client-side only; never sent to /api/jobs -------------------------
  view: 'cards',
  openJobId: null,
});

/** Keys the server is allowed to see. `view` and `openJobId` are not here. */
const API_KEYS = [
  'search', ...LIST_KEYS, ...NUMBER_KEYS, ...FLAG_KEYS,
  'group_duplicates', 'sort', 'direction', 'limit', 'offset',
];

/**
 * Ordering, named for what a person is asking for rather than for the column
 * it sorts on. "Data confidence" is the name of a stored measurement; "Most
 * complete posting" is the reason anyone would pick it.
 *
 * The `value` half is the API vocabulary and must not drift: the server's
 * `_VOCABULARIES['sort']` accepts exactly these six, and index.html renders
 * the same six so the control is never empty on first paint.
 */
//
// VALUES ONLY. The words live in the catalogue under `sort.<value>` and are
// resolved when the control is drawn: this module is imported by everything
// and evaluated before `setLocale` has read the stored choice, so a label
// written here is a label frozen in English.
export const SORTS = ['score', 'confidence', 'posted', 'company', 'title', 'status'];

function cloneState(state) {
  const copy = { ...state };
  for (const key of LIST_KEYS) copy[key] = [...(state[key] || [])];
  return copy;
}

/**
 * Every key named in a key list must also have a default.
 *
 * `normalise` drops any key that is not in `DEFAULTS`, silently, which is right
 * for a stray key from a URL and wrong for one of our own. `include_ineligible`
 * was added to `FLAG_KEYS` and not to `DEFAULTS`, and the result was a control
 * that rendered, took a click, and did nothing at all: the patch was discarded
 * before it reached the store. Nothing failed; the button simply had no effect.
 *
 * Checked at module load so the next one fails at the first page load rather
 * than in a bug report.
 */
for (const key of [...LIST_KEYS, ...FLAG_KEYS, ...NUMBER_KEYS]) {
  if (!(key in DEFAULTS)) {
    throw new Error(`state.js: ${key} is in a key list but has no entry in DEFAULTS`);
  }
}

function normalise(patch) {
  const clean = {};
  for (const [key, value] of Object.entries(patch)) {
    if (!(key in DEFAULTS)) continue;
    if (LIST_KEYS.includes(key)) {
      clean[key] = Array.from(new Set((value || []).map(String))).sort();
    } else if (FLAG_KEYS.includes(key) || key === 'group_duplicates') {
      clean[key] = Boolean(value);
    } else if (NUMBER_KEYS.includes(key) || key === 'limit' || key === 'offset') {
      clean[key] = value === null || value === '' || value === undefined ? null : Number(value);
      if (Number.isNaN(clean[key])) clean[key] = null;
      if ((key === 'limit' || key === 'offset') && clean[key] === null) clean[key] = DEFAULTS[key];
    } else if (key === 'search') {
      clean[key] = value === null || value === undefined ? '' : String(value).slice(0, SEARCH_MAX);
    } else {
      clean[key] = value === null || value === undefined ? DEFAULTS[key] : value;
    }
  }
  return clean;
}

/**
 * The longest free-text search the list accepts, in characters.
 *
 * The server refuses anything longer with a 400 (`SEARCH_MAX_CHARS` in
 * `web/api.py`), so the box, the URL reader and the store all cut to the same
 * length rather than letting a pasted paragraph turn into an error.
 */
export const SEARCH_MAX = 200;

/** Read a state object out of a query string. Unknown parameters are ignored. */
export function fromSearch(search) {
  const params = new URLSearchParams(search || '');
  const state = cloneState(DEFAULTS);

  if (params.has('search')) state.search = (params.get('search') || '').slice(0, SEARCH_MAX);
  for (const key of LIST_KEYS) {
    const values = params.getAll(key).filter(Boolean);
    if (values.length) state[key] = Array.from(new Set(values)).sort();
  }
  for (const key of NUMBER_KEYS) {
    if (params.has(key)) {
      const number = Number(params.get(key));
      state[key] = Number.isFinite(number) ? number : null;
    }
  }
  for (const key of FLAG_KEYS) {
    if (params.has(key)) state[key] = params.get(key) === '1' || params.get(key) === 'true';
  }
  // The eligibility choice is a PREFERENCE rather than a filter, so it falls
  // back to what this person chose last time when the URL does not say. The
  // URL still wins: a link somebody was given describes what they will see.
  if (!params.has('include_ineligible')) {
    state.include_ineligible = storedIncludeIneligible();
  }
  if (!params.has('include_off_target')) {
    state.include_off_target = storedIncludeOffTarget();
  }
  if (!params.has('include_unresolved')) {
    state.include_unresolved = storedIncludeUnresolved();
  }
  if (!params.has('include_excluded_seniority')) {
    state.include_excluded_seniority = storedIncludeExcludedSeniority();
  }
  if (!params.has('include_excluded_work_model')) {
    state.include_excluded_work_model = storedIncludeExcludedWorkModel();
  }
  // The two phrase lists, on the same terms: remembered between visits, and
  // overridden by anything the URL says. A shared link carrying `?keyword=x`
  // describes what its reader will see; a shared link carrying NO keyword must
  // not silently drag the reader's own hide list into somebody else's search --
  // which is why this only fills in when the parameter is absent AND why
  // `clearedFilters` empties them like any other filter.
  if (!params.has('keyword')) state.keyword = storedKeywords();
  if (!params.has('exclude_keyword')) state.exclude_keyword = storedExcludedKeywords();
  if (!params.has('prefer_keyword')) state.prefer_keyword = storedPreferredKeywords();
  if (!params.has('avoid_keyword')) state.avoid_keyword = storedAvoidedKeywords();
  if (params.has('sort')) state.sort = params.get('sort');
  if (params.has('direction')) state.direction = params.get('direction') === 'asc' ? 'asc' : 'desc';
  if (params.has('limit')) state.limit = Number(params.get('limit')) || DEFAULTS.limit;
  if (params.has('offset')) state.offset = Number(params.get('offset')) || 0;
  if (params.has('view')) {
    const asked = params.get('view');
    state.view = VIEWS.includes(asked) ? asked : 'cards';
  }
  // A shared `?view=kanban` link has to narrow exactly as the button does.
  // Without this the header counted 19 postings while the board rendered 5,
  // because the columns silently dropped everything untracked -- a count and
  // a list disagreeing, which is the defect this codebase keeps closing.
  if (state.view === 'kanban' && !params.has('status')) {
    state.status = [...TRACKED_STATUSES];
  }
  // AFTER `view`, and only when the URL is silent. A shared `?view=table` link
  // must open the ungrouped table its author saw; an explicit
  // `?group_duplicates=1` on it must still win, because that is a choice
  // somebody made.
  state.group_duplicates = params.has('group_duplicates')
    ? params.get('group_duplicates') === '1' || params.get('group_duplicates') === 'true'
    : VIEW_GROUPING[state.view];
  if (params.has('job')) state.openJobId = params.get('job') || null;
  return state;
}

/** Serialise a state object. Defaults are omitted, so a clean URL stays clean. */
export function toSearch(state) {
  const params = new URLSearchParams();
  // `debug` is not state: it is read once at load (`?debug=1` shows the
  // scoring vocabulary). It is CARRIED so the first filter change does not
  // drop it from the address and a reload keeps what the page was opened with.
  // Copied as written; nothing here reads or enables anything from it.
  const debug = typeof window === 'undefined'
    ? null : new URLSearchParams(window.location.search).get('debug');
  if (debug) params.set('debug', debug);
  if (state.search) params.set('search', state.search);
  for (const key of LIST_KEYS) {
    for (const value of state[key] || []) params.append(key, value);
  }
  for (const key of NUMBER_KEYS) {
    if (state[key] !== null && state[key] !== undefined) params.set(key, String(state[key]));
  }
  for (const key of FLAG_KEYS) {
    if (state[key]) params.set(key, '1');
  }
  if (state.sort !== DEFAULTS.sort) params.set('sort', state.sort);
  if (state.direction !== DEFAULTS.direction) params.set('direction', state.direction);
  if (state.limit !== DEFAULTS.limit) params.set('limit', String(state.limit));
  if (state.offset) params.set('offset', String(state.offset));
  if (state.view !== DEFAULTS.view) params.set('view', state.view);
  // Written whenever it differs from what this view would have chosen, so
  // `fromSearch(toSearch(s))` is `s` for every state -- including "I turned
  // grouping off in Cards", which an omit-when-default rule would silently
  // undo on the next reload.
  if (state.group_duplicates !== VIEW_GROUPING[state.view]) {
    params.set('group_duplicates', state.group_duplicates ? '1' : '0');
  }
  // Written whenever it differs from the DEFAULT, so a link always describes
  // what its recipient will see. Omitting it when false would let a reader's
  // own remembered "show them" quietly widen somebody else's link.
  //
  // Against DEFAULTS, not against the sender's stored preference, and that is
  // the correction. Comparing to the sender's storage made the parameter
  // appear and then DISAPPEAR: turn the toggle on, and the URL gains
  // `include_ineligible=1` while the storage is written; change any other
  // filter, and now `state === stored`, so the parameter is dropped from the
  // URL -- and the recipient, whose own storage says false, sees the narrow
  // list. A link's meaning cannot depend on a value only the sender holds.
  if (state.include_ineligible !== DEFAULTS.include_ineligible) {
    params.set('include_ineligible', state.include_ineligible ? '1' : '0');
  }
  if (state.openJobId) params.set('job', state.openJobId);
  const query = params.toString();
  return query ? `?${query}` : '';
}

/**
 * The parameters the API request is built from. `view` and `openJobId` are
 * structurally absent: both views produce a byte-identical query string.
 */
export function apiQuery(state) {
  const params = new URLSearchParams();
  for (const key of API_KEYS) {
    const value = state[key];
    if (value === null || value === undefined || value === '' || value === false) continue;
    if (Array.isArray(value)) {
      for (const item of [...value].sort()) params.append(key, item);
    } else if (value === true) {
      params.set(key, '1');
    } else {
      params.set(key, String(value));
    }
  }
  params.sort();
  return params;
}

/** True when any filter (not sort, not paging, not view) is active. */
export function activeFilterCount(state) {
  let count = state.search ? 1 : 0;
  for (const key of LIST_KEYS) {
    // The tracker board's own narrowing is nine statuses but ONE filter, and
    // it shows as one chip. A counter saying "9 filters active" beside a
    // single chip is two numbers on one screen disagreeing, which is the
    // thing this interface is most careful about.
    if (key === 'status' && isTrackedSet(state.status)) {
      count += 1;
      continue;
    }
    // A PREFERENCE IS NOT A FILTER, and counting one here would put "1 filter
    // active" beside a list whose total did not move -- while the hint under
    // those two boxes says, correctly, that they change the order and not the
    // list. Two sentences on one screen contradicting each other is the defect
    // this counter already avoids for the tracker board's nine statuses.
    if (key === 'prefer_keyword' || key === 'avoid_keyword') continue;
    count += (state[key] || []).length;
  }
  for (const key of FLAG_KEYS) {
    // Counted only when it NARROWS. `include_ineligible` widens the list, and
    // "1 filter active" beside a chipless row after pressing "Clear all
    // filters" is the counter disagreeing with the button.
    if (key === 'include_ineligible') continue;
    if (key === 'include_unresolved' || key === 'include_excluded_seniority'
      || key === 'include_excluded_work_model') continue;
    if (state[key]) count += 1;
  }
  for (const key of NUMBER_KEYS) if (state[key] !== null && state[key] !== undefined) count += 1;
  return count;
}

/** A fresh state with every filter cleared, keeping view and sort. */
export function clearedFilters(state) {
  const next = cloneState(DEFAULTS);
  next.view = state.view;
  next.sort = state.sort;
  next.direction = state.direction;
  next.limit = state.limit;
  // Kept for the same reason as sort: "clear all filters" is about which
  // postings are in scope, and grouping changes neither the scope nor the
  // question. Resetting it here would silently undo a deliberate choice under
  // a button that promises to touch filters only.
  next.group_duplicates = state.group_duplicates;
  // Kept for the same reason, and one more: clearing it would HIDE postings.
  // Every other key this function resets widens the list; resetting this one
  // would narrow it, so a button labelled "clear all filters" would make
  // results disappear. That is the opposite of what it promises.
  next.include_ineligible = state.include_ineligible;
  return next;
}

/** Which keys differ between two states. Used to describe a popstate. */
function changedKeys(before, after) {
  const keys = [];
  for (const key of Object.keys(DEFAULTS)) {
    const left = before[key];
    const right = after[key];
    if (Array.isArray(left) || Array.isArray(right)) {
      const a = left || [];
      const b = right || [];
      if (a.length !== b.length || a.some((value, index) => value !== b[index])) keys.push(key);
    } else if (left !== right) {
      keys.push(key);
    }
  }
  return keys;
}

export function createStore() {
  let state = fromSearch(window.location.search);
  const subscribers = new Set();
  let lastSearch = toSearch(state);

  // True when the entry currently on top of the history stack is the one THIS
  // store pushed to open the drawer. Only then may closing the drawer go back
  // rather than push a second entry: otherwise back() would leave the app, or
  // undo a filter change made while the drawer was open.
  let drawerEntryIsOurs = false;

  function notify(meta) {
    for (const subscriber of subscribers) subscriber(cloneState(state), meta);
  }

  return {
    get() {
      return cloneState(state);
    },

    /**
     * Apply a patch.
     * @param {object} patch
     * @param {object} [options]
     *   history 'push' | 'replace' | 'none'
     *   resetOffset {boolean} -- default true for anything but paging
     */
    set(patch, options = {}) {
      const clean = normalise(patch);
      const touchesFilters = Object.keys(clean).some(
        (key) => key !== 'view' && key !== 'openJobId' && key !== 'offset',
      );

      // Closing the drawer pops the entry that opening it pushed, so the URL
      // history reads open/close rather than accumulating one entry per glance.
      const onlyDrawer = Object.keys(clean).length === 1 && 'openJobId' in clean;
      if (onlyDrawer && clean.openJobId === null && state.openJobId
          && drawerEntryIsOurs && options.history !== 'none') {
        drawerEntryIsOurs = false;
        window.history.back();
        return;
      }

      const next = { ...cloneState(state), ...clean };
      if (touchesFilters && options.resetOffset !== false && !('offset' in clean)) {
        next.offset = 0;
      }

      const nextSearch = toSearch(next);
      if (nextSearch === lastSearch && shallowEqual(next, state)) return;
      // Remembered on the way through, so the choice survives the next visit.
      // Written here rather than in the control, because the control is not the
      // only thing that can change it: a preset, a URL and `clearedFilters` all
      // route through this one method.
      if ('include_ineligible' in clean) rememberIncludeIneligible(next.include_ineligible);
      if ('include_off_target' in clean) rememberIncludeOffTarget(next.include_off_target);
      if ('include_unresolved' in clean) rememberIncludeUnresolved(next.include_unresolved);
      if ('include_excluded_seniority' in clean) {
        rememberIncludeExcludedSeniority(next.include_excluded_seniority);
      }
      if ('include_excluded_work_model' in clean) {
        rememberIncludeExcludedWorkModel(next.include_excluded_work_model);
      }
      if ('keyword' in clean) rememberKeywords(next.keyword);
      if ('exclude_keyword' in clean) rememberExcludedKeywords(next.exclude_keyword);
      if ('prefer_keyword' in clean) rememberPreferredKeywords(next.prefer_keyword);
      if ('avoid_keyword' in clean) rememberAvoidedKeywords(next.avoid_keyword);
      state = next;

      const mode = options.history || 'push';
      if (mode !== 'none' && nextSearch !== lastSearch) {
        const url = `${window.location.pathname}${nextSearch}${window.location.hash}`;
        if (mode === 'replace') window.history.replaceState({ s: nextSearch }, '', url);
        else window.history.pushState({ s: nextSearch }, '', url);
        // Only a push whose sole effect was opening the drawer is ours to pop.
        drawerEntryIsOurs = mode === 'push' && onlyDrawer && Boolean(clean.openJobId);
      }
      lastSearch = nextSearch;
      notify({ changed: Object.keys(clean) });
    },

    /** Replace the whole state, e.g. a preset or "clear all". */
    replaceAll(nextState, options = {}) {
      this.set({ ...cloneState(DEFAULTS), ...nextState }, options);
    },

    subscribe(fn) {
      subscribers.add(fn);
      return () => subscribers.delete(fn);
    },

    /** Wire the back/forward buttons. Never pushes; only reads. */
    startHistory() {
      window.addEventListener('popstate', () => {
        const before = state;
        state = fromSearch(window.location.search);
        lastSearch = toSearch(state);
        drawerEntryIsOurs = false;
        // Report which keys actually moved. Back with the drawer open changes
        // openJobId and nothing else, and the list underneath must not be
        // rebuilt -- the element focus returns to lives in it.
        const changed = changedKeys(before, state);
        notify({ changed: changed.length ? changed : ['*'], popstate: true });
      });
    },

    apiQueryString() {
      return apiQuery(state).toString();
    },
  };
}

function shallowEqual(a, b) {
  for (const key of Object.keys(DEFAULTS)) {
    const left = a[key];
    const right = b[key];
    if (Array.isArray(left) || Array.isArray(right)) {
      const l = left || [];
      const r = right || [];
      if (l.length !== r.length || l.some((value, index) => value !== r[index])) return false;
    } else if (left !== right) {
      return false;
    }
  }
  return true;
}

/** Trailing-edge debounce. The search box runs at 250 ms. */
export function debounce(fn, wait = 250) {
  let timer = null;
  const wrapped = (...args) => {
    if (timer) clearTimeout(timer);
    timer = setTimeout(() => { timer = null; fn(...args); }, wait);
  };
  wrapped.cancel = () => { if (timer) clearTimeout(timer); timer = null; };
  wrapped.flush = (...args) => { wrapped.cancel(); fn(...args); };
  return wrapped;
}


/**
 * Is this status list exactly the tracker board's narrowing?
 *
 * Exported so the chip bar and the counter decide it the same way rather than
 * each carrying its own copy of the rule.
 */
export function isTrackedSet(status) {
  const chosen = status || [];
  if (chosen.length !== TRACKED_STATUSES.length) return false;
  return [...chosen].sort().join('|') === [...TRACKED_STATUSES].sort().join('|');
}
