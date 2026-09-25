/**
 * api.js -- every HTTP call the frontend makes, and the one error shape.
 *
 * A network failure and a 500 are the same kind of event to a view: the list
 * could not be produced, say so and offer a retry. So both leave this module
 * as an `ApiError` with `{kind, status, message}` and views handle one thing.
 *
 * MOCK MODE. `dev.html` sets `window.__CAREER_AGENT_MOCK__` in a classic
 * script, which runs before any module, and then every call is answered from
 * `web/mock/api-fixture.json` inside the browser with no socket opened.
 *
 * It is deliberately NOT reachable from a query string. `?mock=1` used to turn
 * it on, which meant the production page carried a live branch that anyone
 * with the URL could take -- it failed loudly, because the fixture sits outside
 * `static/` and 404s, so it deceived nobody. But a page that serves a person's
 * job search should not have a mode that replaces its data with invented
 * postings, and "it only fails" is a weak reason to keep one.
 */

import { t } from './i18n.js';

export const MOCK = window.__CAREER_AGENT_MOCK__ === true;

const BASE = '/api';

export const getCareer = () => request('/career');
export const getCareerHistory = (offset) => request(`/career/history?offset=${offset}`);
export const setSourceSchedule = (source_id, mode) => request('/sources/schedule', {
  method: 'PATCH', body: { source_id, mode },
});
export const refreshSource = (source_id) => request('/sources/refresh', {
  method: 'POST', body: { source_id },
});
/** A forbidden source's local experimental override, for this profile only. */
export const setExperimentalSource = (source_id, opted_in, acknowledged = false) =>
  request('/sources/experimental', {
    method: 'PATCH', body: { source_id, opted_in, acknowledged },
  });
export const getCareerEvidence = (filters = {}) =>
  request(`/career/evidence?${new URLSearchParams(filters)}`);
export const previewCareer = (command) => request('/career/preview', { method: 'POST', body: command });
export const applyCareer = (preview, reviewed) => request('/career/changes', {
  method: 'POST', body: { command: preview.command, preview_hash: preview.preview_hash, reviewed },
});

export class ApiError extends Error {
  constructor({ kind, status, message, detail = null, expected = false }) {
    super(message);
    this.name = 'ApiError';
    this.kind = kind;         // 'network' | 'http' | 'parse' | 'aborted'
    this.status = status;     // 0 for network failures
    this.detail = detail;
    // `expected` marks an outcome that is part of normal operation rather than
    // a fault: "Ollama is not running" is an answer, not an error. Callers show
    // it calmly and nothing is written to the console.
    this.expected = expected;
    this.userMessage = message;
  }
}

/**
 * WHAT SOMEBODY IS TOLD WHEN SOMETHING GOES WRONG.
 *
 * Every view in this product prints `error.userMessage`, so this is the one
 * place that decides what a person reads, and it used to hand them the
 * server's diagnostics: `internal error -- see the terminal running \`serve\``
 * over `HTTP 500`. Both are true. Neither is addressed to somebody who
 * pressed a button to set two claims aside, and the first asks her to go and
 * find a terminal window she may never have opened.
 *
 * The rule is that the SERVER SAYS WHO ITS MESSAGE IS FOR. `for_reader` on
 * the payload marks a sentence written for a person -- "Where you live is a
 * two-letter country code, such as BR", "a rescore is already running" -- and
 * those are shown as they are, because they are more useful than anything
 * this module could say instead. Everything else, including every 5xx, gets
 * the plain sentence for its kind, in the reader's language, and the server's
 * words go to `detail` where the console can still find them.
 *
 * Absence is not permission here either: a message the server has not marked
 * is treated as a diagnostic. A new one added upstream degrades to the
 * friendly copy, which loses precision and never leaks a filesystem path.
 */
function faultMessage(status) {
  if (status >= 500) return t('error.serverFault');
  if (status === 404) return t('error.notThere');
  if (status === 403 || status === 415) return t('error.refused');
  if (status === 413) return t('error.tooBig');
  return t('error.didNotWork');
}

function networkError(cause) {
  return new ApiError({
    kind: 'network',
    status: 0,
    message: t('error.notAnswering'),
    detail: String(cause && cause.message ? cause.message : cause),
  });
}

async function request(path, { method = 'GET', body = null, signal = null } = {}) {
  let response;
  try {
    response = await fetch(`${BASE}${path}`, {
      method,
      headers: body ? { 'Content-Type': 'application/json' } : {},
      body: body ? JSON.stringify(body) : null,
      signal,
    });
  } catch (cause) {
    if (cause && cause.name === 'AbortError') throw abortError();
    throw networkError(cause);
  }

  let payload = null;
  const text = await response.text().catch(() => '');
  if (text) {
    try {
      payload = JSON.parse(text);
    } catch (cause) {
      if (response.ok) {
        throw new ApiError({
          kind: 'parse',
          status: response.status,
          message: t('error.unreadable'),
          detail: String(cause),
        });
      }
    }
  }

  if (!response.ok) {
    const said = (payload && (payload.message || payload.error)) || null;
    // Marked for the reader, and there is something to read: her words win.
    const speaks = Boolean(payload && payload.for_reader && said);
    if (!speaks) {
      // THE DIAGNOSTIC IS NOT LOST, IT IS MOVED. What the screen stops
      // saying, the console says, once, for every view rather than in each
      // one: the route, the status and the server's own sentence. Somebody
      // debugging this opens the console; somebody looking for a job does not.
      console.warn('Career Agent:', method, path, response.status, said || '');
    }
    throw new ApiError({
      kind: 'http',
      status: response.status,
      message: speaks ? said : faultMessage(response.status),
      // Kept on the error too, so a caller that wants the detail has it.
      detail: payload,
    });
  }
  return payload;
}

// =========================================================================
// The public surface. Views only ever call these.
// =========================================================================

export async function getHealth() {
  if (MOCK) return (await fixture()).health;
  return request('/health');
}

/** @param {URLSearchParams} query -- built by state.apiQuery(), never by a view. */
export async function listJobs(query) {
  const queryString = query.toString();
  const payload = MOCK
    ? await mockList(query)
    : await request(`/jobs${queryString ? `?${queryString}` : ''}`);
  payload.facets = normaliseFacets(payload.facets, payload.items, payload.facet_labels);
  return payload;
}

/**
 * One shape for the filter panel, whatever the server sends.
 *
 * `milestone-3-local-product.md` documents facets as
 * `{"role_class": [{key,label,count}]}`. The repository currently groups by
 * its own column names and returns `{"title_class": {"PRIMARY": 12}}`. Both
 * are legitimate answers to the same question, and the panel should not have
 * to know which one arrived, so the translation happens once, here.
 */
const FACET_ALIASES = { title_class: 'role_class', eligibility_status: 'eligibility' };

export function normaliseFacets(raw, items = [], labels = {}) {
  const names = new Map();
  for (const item of items || []) {
    if (item && item.company_slug) names.set(item.company_slug, item.company_name || item.company_slug);
  }
  // Display names the SERVER supplied for id-shaped buckets -- signal ids,
  // whose human labels live in the configuration's lexicon and are what the
  // cards already print. Without them the panel sentence-cased the id and
  // offered "Ipaas" next to a card reading "iPaaS / orchestration tooling".
  const supplied = labels || {};

  const out = {};
  for (const [rawName, value] of Object.entries(raw || {})) {
    const name = FACET_ALIASES[rawName] || rawName;
    const given = supplied[rawName] || supplied[name] || {};
    let rows;
    if (Array.isArray(value)) {
      rows = value.map((row) => ({
        key: String(row.key),
        label: row.label || names.get(String(row.key)) || String(row.key),
        count: Number(row.count) || 0,
      }));
    } else if (value && typeof value === 'object') {
      rows = Object.entries(value).map(([key, count]) => ({
        key: String(key),
        label: given[key] || names.get(String(key)) || String(key),
        count: Number(count) || 0,
      }));
    } else {
      continue;
    }
    out[name] = rows.sort((a, b) => b.count - a.count || a.label.localeCompare(b.label));
  }
  return out;
}

/**
 * The source-coverage matrix. Read on demand, never on load.
 *
 * Cheap (it reads one YAML file and two aggregates) but not free, and nobody
 * needs it to look at a list of jobs.
 */
export async function getSources() {
  if (MOCK) return { sources: [], counts: {}, note: 'Not available in the mock fixture.' };
  return request('/sources');
}

export async function getJob(jobId) {
  if (MOCK) return mockDetail(jobId);
  return request(`/jobs/${encodeURIComponent(jobId)}`);
}

export async function patchStatus(jobId, status, appliedAt) {
  const body = { status };
  if (appliedAt !== undefined) body.applied_at = appliedAt;
  if (MOCK) return mockPatch(jobId, body, 'status');
  return request(`/jobs/${encodeURIComponent(jobId)}/status`, { method: 'PATCH', body });
}

/**
 * Set or clear the applied date on its own, leaving the status alone.
 *
 * A separate call from `patchStatus` because the two disagree about what an
 * absent date means, and one function cannot hold both readings. On `/status`
 * an omitted `applied_at` means "leave it": that reading is what stops being
 * rejected after applying from erasing the day you applied. Here the date IS
 * the request, so `null` clears it. See ADR-0012.
 *
 * The only call in this module that can destroy the record of an application,
 * which is why `main.js` puts a confirmation in front of it.
 */
export async function patchAppliedAt(jobId, appliedAt) {
  const body = { applied_at: appliedAt };
  if (MOCK) return mockPatch(jobId, body, 'applied_at');
  return request(`/jobs/${encodeURIComponent(jobId)}/applied-at`, { method: 'PATCH', body });
}

export async function patchSaved(jobId, saved) {
  if (MOCK) return mockPatch(jobId, { saved }, 'saved');
  return request(`/jobs/${encodeURIComponent(jobId)}/saved`, { method: 'PATCH', body: { saved } });
}

/**
 * Hide one posting from the discovery views, or put it back.
 *
 * A route of its own rather than a status: hiding is not a position in the
 * application workflow, and the nearest status -- ARCHIVED -- would record
 * that an application ended when none was ever sent.
 */
export async function patchHidden(jobId, hidden, scope = 'posting', reason = null) {
  if (MOCK) throw noCandidateHere();
  // `reason` is omitted rather than sent as null when there is none: the
  // route treats a reason on a RESTORE as an error, and an explicit null on
  // every request would make that check read the same as an accident.
  const body = reason ? { hidden, scope, reason } : { hidden, scope };
  return request(`/jobs/${encodeURIComponent(jobId)}/hidden`, {
    method: 'PATCH',
    body,
  });
}

export async function patchNotes(jobId, notes) {
  if (MOCK) return mockPatch(jobId, { notes }, 'notes');
  return request(`/jobs/${encodeURIComponent(jobId)}/notes`, { method: 'PATCH', body: { notes } });
}

/**
 * Explicit user action only. Never called on load.
 *
 * A 503 here is not a fault: it is the server saying "the local model is not
 * running", which is a supported state of this application. It is caught here,
 * re-thrown as `expected`, and never logged: the drawer prints the server's
 * own message inline. Any other status keeps the ordinary error shape and stays
 * loggable.
 *
 * @param {string} jobId
 * @param {AbortSignal} [signal] -- the Cancel button in the drawer.
 */
export async function enrichJob(jobId, signal = null) {
  if (MOCK) {
    // The harness exists to make these states real rather than theoretical, and
    // "waiting on a local model" is a state with an elapsed counter and a Cancel
    // button in it. So the mock waits, cancellably, before it refuses.
    await slowly(MOCK_ENRICH_MS, signal);
    throw new ApiError({
      kind: 'http',
      status: 503,
      expected: true,
      message: t('error.localModelOff'),
    });
  }
  try {
    return await request(`/jobs/${encodeURIComponent(jobId)}/enrich`, {
      method: 'POST', body: {}, signal,
    });
  } catch (error) {
    if (error instanceof ApiError && error.status === 503) {
      throw new ApiError({
        kind: 'http',
        status: 503,
        expected: true,
        message: error.userMessage,
        detail: error.detail,
      });
    }
    throw error;
  }
}

export async function importJob(payload) {
  if (MOCK) {
    throw new ApiError({
      kind: 'http',
      status: 501,
      message: t('error.noImportHere'),
    });
  }
  return request('/import', { method: 'POST', body: payload });
}

// =========================================================================
// Mock mode. Everything below runs only under `dev.html`.
// =========================================================================

let fixturePromise = null;

function fixturePath() {
  const meta = document.querySelector('meta[name="mock-fixture"]');
  return (meta && meta.getAttribute('content')) || '../mock/api-fixture.json';
}

async function fixture() {
  if (!fixturePromise) {
    fixturePromise = (async () => {
      const inline = document.getElementById('mock-fixture');
      if (inline && inline.textContent.trim()) {
        return JSON.parse(inline.textContent);
      }
      let response;
      try {
        response = await fetch(fixturePath());
      } catch (cause) {
        throw networkError(cause);
      }
      if (!response.ok) {
        throw new ApiError({
          kind: 'http', status: response.status, message: t('error.noFixture'),
        });
      }
      return response.json();
    })();
  }
  return fixturePromise;
}

const has = (query, key) => query.getAll(key).filter(Boolean);

function matchesFilters(job, query) {
  const search = (query.get('search') || '').trim().toLowerCase();
  if (search) {
    const hay = [job.title, job.company_name, job.description_excerpt, job.location_raw]
      .filter(Boolean).join(' ').toLowerCase();
    if (!hay.includes(search)) return false;
  }

  const listFilters = [
    ['company', (j) => [j.company_slug, j.company_name]],
    ['provider', (j) => [j.provider]],
    ['role_class', (j) => [j.title_class]],
    ['status', (j) => [j.application_status]],
    ['eligibility', (j) => [j.eligibility_status]],
    ['fit_band', (j) => [j.fit_band]],
    ['signal', (j) => (j.technologies || []).map((t) => t.signal_id)],
  ];
  for (const [key, pick] of listFilters) {
    const wanted = has(query, key);
    if (wanted.length && !pick(job).some((value) => wanted.includes(value))) return false;
  }

  if (query.has('min_score')) {
    if (job.match_score === null || job.match_score === undefined) return false;
    if (job.match_score < Number(query.get('min_score'))) return false;
  }
  if (query.has('max_score')) {
    if (job.match_score !== null && job.match_score > Number(query.get('max_score'))) return false;
  }
  if (query.has('min_confidence') && (job.data_confidence ?? 0) < Number(query.get('min_confidence'))) return false;
  if (query.get('saved_only') && !job.saved) return false;
  if (query.get('enriched_only') && !job.enriched) return false;
  if (query.get('has_salary') && !job.salary) return false;
  if (query.get('remote_only') && job.work_model !== 'REMOTE') return false;
  if (query.has('posted_within_days')) {
    const limit = Number(query.get('posted_within_days'));
    if (!job.posted_at) return false;
    const days = Math.floor((Date.now() - new Date(`${job.posted_at}T00:00:00Z`).getTime()) / 86400000);
    if (days > limit) return false;
  }
  return true;
}

const SORT_PICKERS = {
  score: (j) => (j.match_score === null || j.match_score === undefined ? -1 : j.match_score),
  confidence: (j) => j.data_confidence ?? -1,
  posted_at: (j) => (j.posted_at ? new Date(`${j.posted_at}T00:00:00Z`).getTime() : 0),
  company: (j) => (j.company_name || '').toLowerCase(),
  title: (j) => (j.title || '').toLowerCase(),
  status: (j) => (j.application_status || '').toLowerCase(),
};

const FACET_FIELDS = {
  company: (j) => [{ key: j.company_slug, label: j.company_name }],
  provider: (j) => [{ key: j.provider, label: j.provider }],
  role_class: (j) => [{ key: j.title_class, label: j.title_class }],
  eligibility: (j) => [{ key: j.eligibility_status, label: j.eligibility_status }],
  status: (j) => [{ key: j.application_status, label: j.application_status }],
  fit_band: (j) => [{ key: j.fit_band, label: j.fit_band }],
  signal: (j) => (j.technologies || []).map((t) => ({ key: t.signal_id, label: t.label })),
};

function buildFacets(items, query) {
  const facets = {};
  for (const [name, pick] of Object.entries(FACET_FIELDS)) {
    // Count against everything except this facet's own selection, so the
    // options a person can still add do not vanish the moment they pick one.
    const partial = new URLSearchParams(query);
    partial.delete(name);
    const scoped = items.filter((job) => matchesFilters(job, partial));
    const counts = new Map();
    for (const job of scoped) {
      for (const { key, label } of pick(job)) {
        if (!key) continue;
        const row = counts.get(key) || { key, label: label || key, count: 0 };
        row.count += 1;
        counts.set(key, row);
      }
    }
    facets[name] = Array.from(counts.values()).sort(
      (a, b) => b.count - a.count || String(a.label).localeCompare(String(b.label)),
    );
  }
  return facets;
}

async function mockList(query) {
  const data = await fixture();
  await tick();
  const all = data.items;
  const filtered = all.filter((job) => matchesFilters(job, query));
  const sortKey = query.get('sort') || 'score';
  const direction = query.get('direction') === 'asc' ? 1 : -1;
  const pick = SORT_PICKERS[sortKey] || SORT_PICKERS.score;
  filtered.sort((a, b) => {
    const left = pick(a);
    const right = pick(b);
    if (left < right) return -1 * direction;
    if (left > right) return 1 * direction;
    return String(a.job_id).localeCompare(String(b.job_id));
  });
  const offset = Number(query.get('offset') || 0);
  const limit = Number(query.get('limit') || 60);
  return {
    total: filtered.length,
    offset,
    limit,
    items: filtered.slice(offset, offset + limit),
    facets: buildFacets(all, query),
  };
}

async function mockDetail(jobId) {
  const data = await fixture();
  await tick();
  const card = data.items.find((job) => job.job_id === jobId);
  if (!card) throw new ApiError({ kind: 'http', status: 404, message: t('error.notThere') });
  const stored = (data.details || {})[jobId];
  if (stored) return { ...card, ...stored };
  return synthesiseDetail(card);
}

/**
 * A detail view for a fixture job that carries no hand-written detail block.
 * It only re-expresses what the card already states -- no number is invented,
 * and a job with no score still has no score here.
 */
function synthesiseDetail(card) {
  const components = [{
    component_id: 'signals',
    label: 'Matched responsibilities',
    points: (card.top_strengths || []).reduce((sum, s) => sum + Number(s.points || 0), 0),
    max_points: 30,
    contributions: (card.top_strengths || []).map((strength) => ({
      signal_id: strength.label,
      label: strength.label,
      prominence: 'PRIMARY',
      weight: strength.points,
      points: strength.points,
      quote: strength.quote || null,
    })),
  }];
  const gates = [...(card.blockers || [])];
  if (card.primary_gap) {
    gates.push({
      gate: card.primary_gap.label,
      result: 'UNRESOLVED',
      reason: card.primary_gap.reason,
      quote: null,
    });
  }
  return {
    ...card,
    description: card.description_excerpt || '',
    components,
    penalties: [],
    gates,
    confidence_items: [],
    signals: card.technologies || [],
    unknowns: [],
    history: [],
    enrichment: {},
  };
}

async function mockPatch(jobId, patch, kind) {
  const data = await fixture();
  await tick();
  const card = data.items.find((job) => job.job_id === jobId);
  if (!card) throw new ApiError({ kind: 'http', status: 404, message: t('error.notThere') });

  const post = ['APPLIED', 'INTERVIEW', 'OFFER', 'HIRED', 'WITHDRAWN'];

  if (kind === 'applied_at') {
    // The server refuses to clear a date the status would immediately put
    // back, and answers 409 with the reason. Mirrored rather than approximated:
    // a harness that silently allowed it would make the drawer's confirmation
    // look like it worked against a server that says otherwise.
    if (patch.applied_at === null && post.includes(card.application_status)) {
      throw new ApiError({
        kind: 'http',
        status: 409,
        message: `${card.application_status} means an application was sent, so its `
          + 'date cannot be cleared. Move the status first if it was recorded in error.',
      });
    }
    card.applied_at = patch.applied_at;
  }

  if (kind === 'status') {
    card.application_status = patch.status;
    // A status move NEVER clears the date, in either direction: ADR-0012. It
    // used to, and one drag on the board destroyed the record of applying.
    if (patch.applied_at !== undefined && patch.applied_at !== null) {
      card.applied_at = patch.applied_at;
    } else if (post.includes(patch.status)) {
      card.applied_at = card.applied_at || new Date().toISOString().slice(0, 10);
    }
    const detail = (data.details || {})[jobId];
    if (detail && Array.isArray(detail.history)) {
      detail.history.push({
        at: new Date().toISOString(),
        from_status: null,
        to_status: patch.status,
        note: 'Changed in this session',
      });
    }
  } else if (kind === 'saved') {
    card.saved = Boolean(patch.saved);
  } else if (kind === 'notes') {
    card.notes = patch.notes || null;
    const detail = (data.details || {})[jobId];
    if (detail) detail.notes = card.notes;
  }

  // The SERVER's rule, not a second one: a post-application status implies
  // applied whatever the date says, and a date implies applied whatever the
  // status says. `domain/application.py::has_applied`. Two rules for one
  // invariant is how a harness starts disagreeing with the thing it stands in
  // for, and this invariant is the one the product promises cannot break.
  //
  // Recomputed for EVERY kind, not only for 'status'. It sat inside the status
  // branch, so clearing the date left `has_applied` reading true off a date
  // that no longer existed: the contradiction this line exists to prevent.
  card.has_applied = post.includes(card.application_status) || card.applied_at !== null;
  return { ...card };
}

/** A frame of latency so the loading state is real rather than theoretical. */
function tick() {
  return new Promise((resolve) => setTimeout(resolve, MOCK_LATENCY_MS));
}

/** A cancellable wait, so the mock enrichment can be aborted like the real one. */
function slowly(ms, signal) {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(resolve, ms);
    if (!signal) return;
    if (signal.aborted) { clearTimeout(timer); reject(abortError()); return; }
    signal.addEventListener('abort', () => { clearTimeout(timer); reject(abortError()); }, { once: true });
  });
}

function abortError() {
  return new ApiError({
    kind: 'aborted', status: 0, expected: true,
    message: t('error.cancelled'),
  });
}

const MOCK_LATENCY_MS = 120;
const MOCK_ENRICH_MS = 6000;


/** The editable phrase groups. */
export async function getPreferences() {
  return request('/preferences');
}

/**
 * The whole career profile: what this product believes you are looking for.
 *
 * A READ. Everything in it is already stored somewhere, and the one part with
 * a safe writer is reached through `patchPreferences` -- there is deliberately
 * no `patchProfile`, because a second write path over the same file is how two
 * copies of one fact start disagreeing.
 */
export async function getProfile() {
  return request('/profile');
}

/**
 * Replace one signal's phrases.
 *
 * PATCH, not PUT: this changes one group and leaves the rest of the
 * configuration alone, which is also what the writer does on disk.
 */
export async function patchPreferences(patch) {
  return request('/preferences', { method: 'PATCH', body: patch });
}

export async function getSearchReview() {
  return request('/search-review');
}

// ---------------------------------------------------------------------------
// AI & Semantic Matching. HOW postings are interpreted, never WHAT you want.
// No response here ever carries a credential; the key goes in and is only
// ever reported as configured or not.
// ---------------------------------------------------------------------------

export async function getSemantic(refresh = false) {
  return request(`/semantic${refresh ? '?refresh=1' : ''}`);
}

export async function patchSemantic(changes) {
  return request('/semantic', { method: 'PATCH', body: changes });
}

export async function saveDeepseekKey(key) {
  return request('/semantic/deepseek-key', { method: 'POST', body: { key } });
}

export async function removeDeepseekKey() {
  return request('/semantic/deepseek-key/remove', { method: 'POST', body: {} });
}

export async function checkSemanticProvider(provider) {
  return request('/semantic/healthcheck', { method: 'POST', body: { provider } });
}

export async function planSemantic() {
  return request('/semantic/plan', { method: 'POST', body: {} });
}

export async function startSemantic() {
  return request('/semantic/run', { method: 'POST', body: {} });
}

export async function getSemanticRun() {
  return request('/semantic/run');
}

export async function cancelSemantic() {
  return request('/semantic/run/cancel', { method: 'POST', body: {} });
}

export async function getSearchFitReadiness() {
  return request('/search-fit/readiness');
}

export async function patchSearchReview(patch) {
  return request('/search-review', { method: 'PATCH', body: patch });
}

/**
 * Change the candidate's own choices. One request, all fields or none.
 *
 * `changes` is `{field: value}` for fields the server itself advertised in
 * `GET /api/profile` under `editable`. The browser sends nothing it was not
 * offered, and the server refuses anything outside its whitelist regardless --
 * this end of it is a convenience, not the check.
 */
export async function patchProfile(changes) {
  return request('/profile', { method: 'PATCH', body: { changes } });
}


/**
 * The funnel and the last retrieval. Safe to poll.
 *
 * `funnel: false` leaves the funnel out: it is counted over the whole corpus,
 * and the progress poll only needs the run.
 */
export async function getRetrieval({ funnel = true } = {}) {
  return request(funnel ? '/retrieval' : '/retrieval?funnel=false');
}

/** Begin a collection pass. Returns immediately; poll `getRetrieval`. */
export async function startRetrieval() {
  return request('/retrieval', { method: 'POST', body: {} });
}

/**
 * Find jobs: every source that may be refreshed and is not paused, in turn.
 * Returns at once; progress is `getRetrieval()`, cancel is `cancelRetrieval()`.
 */
export async function findJobs() {
  return request('/sources/refresh-all', { method: 'POST', body: {} });
}

/** Ask the run to stop after the board it is on. */
export async function cancelRetrieval() {
  return request('/retrieval/cancel', { method: 'POST', body: {} });
}

/** Whether a rescore is running, and how far it got. Safe to poll. */
export async function getRescore() {
  return request('/rescore');
}

/**
 * Score every posting against the current preferences.
 *
 * Returns immediately; poll `getRescore`. Opens no socket and asks no model,
 * hosted or local -- it is arithmetic over text already on this machine,
 * which is why it is allowed to be a button rather than a terminal command.
 */
export async function startRescore() {
  return request('/rescore', { method: 'POST', body: {} });
}


// =========================================================================
// The candidate's half. Preparation, evidence and CV intake.
//
// Every one of these talks to `web/workspace_api.py`, and none of them can
// reach an employer: there is no route that submits an application, so there
// is no function here that could call one.
// =========================================================================

/**
 * What one posting asks for, beside what has been confirmed.
 *
 * Returns EVERY requirement the posting fired, in every state. There is no
 * parameter that would ask for only the matches, and the absence is the
 * design: a preparation view that hid the gaps would be a view that flatters
 * somebody on the way into an interview.
 */
export async function getPreparation(jobId) {
  if (MOCK) throw noCandidateHere();
  return request(`/jobs/${encodeURIComponent(jobId)}/preparation`);
}

/**
 * The dev harness has a fixture of postings and no candidate at all.
 *
 * Refusing is the honest answer. Inventing confirmed claims for a fixture that
 * ships in the repository would put a made-up career into git, and every
 * screenshot taken from the harness would show experience nobody has.
 */
function noCandidateHere() {
  return new ApiError({
    kind: 'http',
    status: 404,
    expected: true,
    message: t('error.noCandidateHere'),
  });
}

/**
 * Record what you think of one requirement and the evidence proposed for it.
 *
 * Changes no score and creates no claim. `verdict: null` withdraws a previous
 * answer -- and it must be sent explicitly, because an omitted field means
 * "leave it alone" everywhere else in this API.
 */
export async function reviewRequirement(jobId, signalId, verdict, note = null) {
  return request(
    `/jobs/${encodeURIComponent(jobId)}/requirements/${encodeURIComponent(signalId)}`,
    { method: 'PATCH', body: { verdict, note } },
  );
}

/**
 * What is worth looking at today: the same sections the terminal digest has.
 *
 * Several server-composed queries in one response. Nothing here re-ranks
 * anything -- each section is a filter over the score the cards already show.
 */
export async function getHome() {
  if (MOCK) throw noCandidateHere();
  return request('/home');
}

export async function getDaily() {
  if (MOCK) throw noCandidateHere();
  return request('/daily');
}

/**
 * Record that you have read the list, now.
 *
 * Only when a person presses the button. A digest that marked itself read on
 * every load would make "since you last looked" mean "since this page last
 * rendered", and would empty the section she opened it for.
 */
export async function markReviewed() {
  return request('/daily/reviewed', { method: 'POST', body: {} });
}

/**
 * Which of your own confirmed sentences this posting argues for, in order.
 *
 * SELECTION AND ORDER, NEVER NEW WORDS. Every string here came from a claim
 * you confirmed or from a requirement label in your own settings. No model of
 * either kind is anywhere near it, and nothing is rewritten or strengthened.
 */
export async function getResumePlan(jobId) {
  return request(`/jobs/${encodeURIComponent(jobId)}/resume`);
}

/** Everything confirmed about you, and everything retired. */
export async function getEvidence() {
  if (MOCK) throw noCandidateHere();
  return request('/evidence');
}

/** A fact you state about yourself. Recorded as SELF_ATTESTED, citing nothing. */
export async function createClaim(claim) {
  return request('/evidence', { method: 'POST', body: claim });
}

/** Correct a claim. A new revision; the evidence and the confirmation do not move. */
export async function editClaim(claimKey, changes) {
  return request(`/evidence/${encodeURIComponent(claimKey)}`, { method: 'PATCH', body: changes });
}

/** Stop drawing on a claim. A revision, never a delete: the history is the point. */
export async function retireClaim(claimKey) {
  return request(`/evidence/${encodeURIComponent(claimKey)}/retire`, { method: 'POST', body: {} });
}

/** Stand behind a retired claim again. */
export async function confirmClaim(claimKey) {
  return request(`/evidence/${encodeURIComponent(claimKey)}/confirm`, { method: 'POST', body: {} });
}

/** Every revision of one fact, oldest first. */
export async function getClaimHistory(claimKey) {
  return request(`/evidence/${encodeURIComponent(claimKey)}/history`);
}

/**
 * What a fresh install still has to do, as facts rather than as a score.
 *
 * Six steps, each either recorded or not. No total and no percentage: there is
 * no honest denominator for a career, which is the same refusal the Home
 * page's gap list makes.
 */
export async function getFirstRun() {
  if (MOCK) throw noCandidateHere();
  return request('/firstrun');
}

/**
 * Read one or more career documents on this computer. Confirms nothing.
 *
 * The same promise `importCv` carries, for several documents at once: the
 * bytes go to the LOCAL server, which reads them in memory with deterministic
 * readers -- no model of any kind, no socket -- and stores only the statements
 * it found and the lines they came from. Base64 inside a JSON body for the
 * same security reason: the server requires JSON on every body because a
 * cross-origin form can send multipart without a preflight and cannot send
 * JSON.
 *
 * `documents` is `{ filename, kind, content_base64 }` per file, and the KIND
 * is declared rather than guessed from the name -- a LinkedIn export's
 * Experience section needs a different reader from a CV's.
 */
export async function createIntake(documents) {
  return request('/intake', { method: 'POST', body: { documents } });
}

/** Staged CV reads and how far through reviewing each one you are. */
export async function getCvImports() {
  if (MOCK) throw noCandidateHere();
  return request('/cv/imports');
}

/**
 * Read a CV on this computer. Confirms nothing.
 *
 * The bytes go to the local server, which parses them in memory and stores
 * only the proposals and the lines they came from. They are base64 inside a
 * JSON body rather than a multipart form because the server requires JSON on
 * every body -- a cross-origin form can send multipart without a preflight and
 * cannot send JSON, which is what makes that requirement a defence.
 */
export async function importCv(filename, bytes) {
  return request('/cv/import', {
    method: 'POST',
    body: { filename, content_base64: base64Of(bytes) },
  });
}

/** One staged read, with every proposal and the answer it carries. */
export async function getCvReview(importId) {
  return request(`/cv/imports/${encodeURIComponent(importId)}`);
}

/** Accept, edit or reject one proposal. Committed on its own, immediately. */
export async function decideProposal(importId, claimKey, decision, text = null) {
  return request(`/cv/imports/${encodeURIComponent(importId)}/decide`, {
    method: 'POST',
    body: { claim_key: claimKey, decision, text },
  });
}

// -------------------------------------------------------------------------
// The Candidate Intake Package
//
// Five calls, and there is deliberately NO "give me every claim".
// A real package holds three hundred proposals; a list of three hundred is a
// wall, and a wall is the thing people close the tab on. The overview returns
// counts and headings, and claims arrive one group at a time.
// -------------------------------------------------------------------------

/** Every staged package, newest first, with how far each one has got. */
export async function getIntakePackages() {
  if (MOCK) throw noCandidateHere();
  return request('/intake');
}

/** What a package holds, WITHOUT returning what it holds. */
export async function getIntakeOverview(packageId) {
  return request(`/intake/${encodeURIComponent(packageId)}`);
}

/** One group of claims, or one review state. Never everything. */
export async function getIntakeClaims(packageId, { group = null, state = null, step = null } = {}) {
  const query = new URLSearchParams();
  if (group) query.set('group', group);
  if (state) query.set('state', state);
  if (step) query.set('step', step);
  return request(`/intake/${encodeURIComponent(packageId)}/claims?${query.toString()}`);
}

/**
 * One claim, one answer. Committed on its own, immediately.
 *
 * A review that saved only at the end would lose an hour of decisions to a
 * closed tab, and these are decisions about somebody's own career.
 */
export async function answerIntakeClaim(packageId, claimKey, answer, text = null) {
  return request(`/intake/${encodeURIComponent(packageId)}/answer`, {
    method: 'POST',
    body: { claim_key: claimKey, answer, text },
  });
}

/**
 * A disagreement, answered ONCE.
 *
 * `claimKey` names WHICH DOCUMENT'S dates she accepted -- never dates typed
 * here, so every confirmed fact stays traceable to a document that states it.
 * Pass `{ reopen: true }` to unmake the answer.
 */
export async function resolveIntakeConflict(packageId, group, claimKey, { reopen = false } = {}) {
  return request(`/intake/${encodeURIComponent(packageId)}/resolve`, {
    method: 'POST',
    body: reopen ? { group, reopen: true } : { group, claim_key: claimKey },
  });
}

/**
 * Put a package away. NOT a delete: every row stays, and so does everything
 * confirmed from it, which stopped belonging to the package on confirmation.
 */
export async function discardIntakePackage(packageId) {
  return request(`/intake/${encodeURIComponent(packageId)}/discard`, { method: 'POST' });
}

/**
 * WHERE TO START. Counts and step keys, never claims.
 *
 * `term` is the requirement she arrived from, when she arrived from one. It
 * is reported BESIDE the steps rather than as one of them: the claims that
 * mention a requirement are already recent work, or outcomes, or skills, and
 * a step of their own would steal them from the step they belong to.
 */
export async function getIntakePriority(packageId, { term = null } = {}) {
  const search = term ? `?term=${encodeURIComponent(term)}` : '';
  return request(`/intake/${encodeURIComponent(packageId)}/priority${search}`);
}

/**
 * Put THIS reading of her documents in force, and retire whatever was.
 *
 * The one act that answers "which package am I reviewing". It confirms
 * nothing, deletes nothing and is reversible in both directions: the package
 * it displaces becomes SUPERSEDED, which is the product's doing rather than
 * hers, and selecting that one again puts it straight back.
 */
export async function selectIntakePackage(packageId) {
  return request(`/intake/${encodeURIComponent(packageId)}/select`, { method: 'POST' });
}

/**
 * Take a package back out of the drawer.
 *
 * The server says which state it landed in and the caller must read it: with
 * nothing in force it becomes ACTIVE, and beside a live review it becomes
 * SUPERSEDED. Assuming ACTIVE would be wrong exactly when it matters.
 */
export async function restoreIntakePackage(packageId) {
  return request(`/intake/${encodeURIComponent(packageId)}/restore`, { method: 'POST' });
}

/**
 * ARCHIVE a CV read. Reversible: every row stays, and it waits nowhere until
 * it is restored. (This used to be a hard delete called "discard".)
 */
export async function archiveCvImport(importId) {
  return request(`/cv/imports/${encodeURIComponent(importId)}/archive`, {
    method: 'POST',
    body: {},
  });
}

/** Bring an archived CV read back, exactly as it was. */
export async function restoreCvImport(importId) {
  return request(`/cv/imports/${encodeURIComponent(importId)}/restore`, {
    method: 'POST',
    body: {},
  });
}

/**
 * DELETE a CV read, permanently. Without `confirm` the server only answers
 * what would be removed and what kept, and changes nothing: the interface
 * shows that plan before it asks.
 */
export async function deleteCvImport(importId, { confirm = false } = {}) {
  return request(`/cv/imports/${encodeURIComponent(importId)}/delete`, {
    method: 'POST',
    body: confirm ? { confirm: true } : {},
  });
}

/**
 * Correct a read's structure: edit_entry, create_entry, move, merge, split,
 * delete_entry, reject, delete. Never confirms anything.
 */
export async function organizeCvImport(importId, body) {
  return request(`/cv/imports/${encodeURIComponent(importId)}/organize`, {
    method: 'POST',
    body,
  });
}

/** DELETE an intake package, permanently. Plan first, exactly as for a CV. */
export async function deleteIntakePackage(packageId, { confirm = false } = {}) {
  return request(`/intake/${encodeURIComponent(packageId)}/delete`, {
    method: 'POST',
    body: confirm ? { confirm: true } : {},
  });
}

/**
 * Bytes as base64, in chunks.
 *
 * `String.fromCharCode(...bytes)` on a whole file blows the argument limit and
 * throws for anything over a few hundred kilobytes -- which is most CVs with a
 * photograph in them.
 */
function base64Of(bytes) {
  const view = new Uint8Array(bytes);
  const step = 0x8000;
  let binary = '';
  for (let index = 0; index < view.length; index += step) {
    binary += String.fromCharCode.apply(null, view.subarray(index, index + step));
  }
  return btoa(binary);
}

/** Read-only maintenance status; never starts collection. */
export async function getSourceMaintenance() { return request('/source-maintenance'); }

export async function createFirstSearch(body) { return request('/first-search', { method: 'POST', body }); }

/** Roles in mind: optional search anchors. Never scores or hides a posting. */
export async function getRoleAnchors() {
  if (MOCK) return { anchors: [], aliases: [], suggestions: [], limits: { anchors: 8, text: 80 } };
  return request('/role-anchors');
}
export async function saveRoleAnchors(body) {
  if (MOCK) return { ...(await getRoleAnchors()), anchors: body.anchors, saved: true };
  return request('/role-anchors', { method: 'PATCH', body });
}

/**
 * One organising change, previewed and applied. Returns `{ event_id, ... }`;
 * the event id is what Undo sends back. Confirming is never done here.
 */
export async function changeCareer(command) {
  const preview = await previewCareer(command);
  return applyCareer({ ...preview, command }, false);
}

/** Undo one organising change by its history event. */
export async function undoCareer(eventId) {
  return changeCareer({ action: 'undo', event_id: eventId });
}

// -- Documents: every import, and the guided review -----------------------
export const getDocuments = () => request('/documents');
const docPath = (kind, id) => `/documents/${encodeURIComponent(kind)}/${encodeURIComponent(id)}`;
export const getDocumentReview = (kind, id) => request(docPath(kind, id));
/** One statement, one answer: CONFIRM, EDIT, UNSURE, REJECT, REOPEN or CHOOSE. */
export const answerDocument = (kind, id, body) =>
  request(`${docPath(kind, id)}/answer`, { method: 'POST', body });
/** Put an experience on the profile: `choice` new, or existing (with `dates`). */
export const placeDocumentEntry = (kind, id, body) =>
  request(`${docPath(kind, id)}/place`, { method: 'POST', body });
/** "Don't import": reject what is still unanswered in one experience. */
export const skipDocumentEntry = (kind, id, entry) =>
  request(`${docPath(kind, id)}/skip`, { method: 'POST', body: { entry } });
