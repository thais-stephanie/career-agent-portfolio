import { t, tVocab, tState } from './i18n.js';

/**
 * format.js -- dates, freshness, salary and the vocabulary labels.
 *
 * Two rules live here and nowhere else:
 *
 *   1. A job with `match_score === null` is "not scored". It is never 0, never
 *      an average, never a dash that a reader could mistake for a low number.
 *   2. Nothing in this file combines match_score with data_confidence. They are
 *      formatted separately because they are separate measurements (ADR-0004).
 */

//: The last-resort placeholder, and the only English left in this file.
//:
//: It is exported for the tests and used by `plainVocabLabel`, which runs for
//: values the catalogue does NOT name -- a technology id, a provider, a
//: signal. Everything a reader actually sees goes through `t('value.notStated')`.
const MISSING = 'not stated';   // localisation-check: allow structural fallback

/** Sentence-case a SCREAMING_SNAKE vocabulary value. */
export function humanLabel(value) {
  if (value === null || value === undefined || value === '') return t('value.notStated');
  const raw = String(value).replace(/_/g, ' ').toLowerCase().trim();
  return raw.charAt(0).toUpperCase() + raw.slice(1);
}

/**
 * Vocabulary values whose sentence-case form reads wrong ("Ats structured").
 * Anything not listed falls through to humanLabel, so a value the server adds
 * tomorrow still renders legibly instead of blank.
 */
const LABEL_OVERRIDES = {
  API: 'API',
  SQL: 'SQL',
  PJ: 'PJ',
  CLT: 'CLT',
  // -- regions ------------------------------------------------------------
  // Acronyms the four-character rule below is too short to catch, plus the
  // two that are phrases rather than codes.
  EMEA: 'EMEA',
  APAC: 'APAC',
  // -- salary periods -----------------------------------------------------
  // These ARE words, and three of them are four characters, so without an
  // entry here the acronym rule would shout YEAR and DAY at the reader while
  // leaving MONTH in sentence case. Five values, one row each, no rule.
  // -- seniority and match bands ------------------------------------------
  // The same trap as the salary periods, found by an independent UX review
  // reading a card: one posting said "LEAD - Full-time" and the next said
  // "Senior - Full-time". `LEAD` and `MID` are four characters or fewer and
  // sail through the acronym rule; `SENIOR` does not. The filter rail was
  // shouting too: "LEAD 3 - MID 1 - Senior 1" and "GOOD 4 - Strong 4 -
  // WEAK 4 - Moderate 2".
  //
  // The comment on `isAcronym` predicted exactly this ("real words in the
  // vocabulary -- OFFER, HIRED, REMOTE") and chose four anyway. Every short
  // word this vocabulary actually contains now has a row.
  LEAD: 'Lead',
  MID: 'Mid',
  GOOD: 'Good',
  WEAK: 'Weak',
};

/**
 * A short all-caps token with no underscore is an acronym, not a phrase, and
 * sentence-casing it produces a non-word: `PJ` (pessoa juridica) became "Pj"
 * and `CLT` became "Clt". Four characters is the cut so that real words in the
 * vocabulary -- OFFER, HIRED, REMOTE -- keep their sentence case.
 */
function isAcronym(value) {
  return /^[A-Z0-9]{1,4}$/.test(value);
}

/**
 * What a tool's prominence means, in words rather than in the internal name.
 *
 * PRIMARY / SECONDARY / INCIDENTAL is the scorer's vocabulary and it earns its
 * keep there: a tool a job is ABOUT and a tool a job mentions once are worth
 * different numbers of points. Rendered to a person, "Primary in this posting"
 * asks them to already know that, so the words say the thing instead.
 */
export function prominenceWords(prominence) {
  const key = String(prominence || '').toUpperCase();
  const translated = t(`prominence.${key}`);
  return translated === `prominence.${key}` ? t('prominence.OTHER') : translated;
}

/**
 * A stored enum, as a person reads it.
 *
 * Asks the catalogue first, so an eligibility state, a match band, a level and
 * an application status all render in the reader's language while the VALUE
 * behind them stays exactly what the database and the query string hold. When
 * nothing is translated the existing English rules run unchanged, which is why
 * adding a locale did not require touching any caller.
 */
export function vocabLabel(value) {
  const translated = tVocab(value);
  return translated === null ? plainVocabLabel(value) : translated;
}

/**
 * The English rules, unchanged: the override table, then the acronym rule,
 * then sentence case.
 *
 * Kept as its own function rather than folded into the branch above, because
 * it is what runs for every value the catalogue does NOT name -- a technology
 * id, a work model, a provider -- and those are the majority.
 */
export function plainVocabLabel(value) {
  if (value === null || value === undefined || value === '') return MISSING;
  const raw = String(value).trim();
  const key = raw.toUpperCase();
  if (LABEL_OVERRIDES[key]) return LABEL_OVERRIDES[key];
  if (isAcronym(raw)) return raw;
  return humanLabel(raw);
}

/** "Greenhouse · ATS structured", where the posting came from and how. */
export function sourceLabel(provider, accessMethod) {
  const left = provider ? vocabLabel(provider) : t('absent.source');
  return accessMethod ? `${left} · ${vocabLabel(accessMethod)}` : left;
}

/**
 * "Remote, Americas · Remote" is noise. Only add the work model when the
 * location string does not already say it.
 */
export function placeLabel(locationRaw, workModel) {
  const place = (locationRaw || '').trim();
  const model = workModel ? vocabLabel(workModel) : '';
  if (!place) return model || t('absent.place');
  if (!model) return place;
  return place.toLowerCase().includes(model.toLowerCase()) ? place : `${place} · ${model}`;
}

//: How a board joins several places into one string. Workable folds one
//: posting's countries with ` / ` (`AR / BR / CO / MX / CL / PA`); Greenhouse
//: joins offices with ` | `. Both are the employer's punctuation, read here
//: and never rewritten: the pieces are shown in the order they arrived.
const PLACE_SEPARATOR = /\s+(?:\/|\|)\s+/;

//: How many places a COMPACT label names before it starts counting.
//:
//: Four, so the common Workable shape (`MX / CO / BR / CR`) is shown whole,
//: and a six-country list becomes `AR / BR / CO / MX +2 more` rather than
//: an ellipsis that says nothing about how much was cut.
const MAX_COMPACT_PLACES = 4;

/**
 * The place, short enough for one line of a card, and honest about what it
 * left out.
 *
 * A collapsed card is a scanning surface. `Shenzhen, Guangdong Province, CN
 * / Kuala Lumpur, Federal Territory of Kuala Lumpur, MY` wrapped to three
 * lines on it and pushed the salary and the controls down; a six-country
 * hiring list did the same. So a list longer than `MAX_COMPACT_PLACES` names
 * the first four and COUNTS the rest, in the same `+N more` words the group
 * badge uses. Nothing is dropped silently: the full string is returned
 * beside it for a `title`, and the drawer prints every place.
 *
 * It does not reorder. Putting the reader's own country first would need the
 * card to know who is reading it, and the eligibility badge beside the label
 * already answers the question a reordering would be trying to answer.
 */
export function compactPlace(locationRaw, workModel) {
  const full = placeLabel(locationRaw, workModel);
  const place = (locationRaw || '').trim();
  const parts = place ? place.split(PLACE_SEPARATOR).map((p) => p.trim()).filter(Boolean) : [];
  if (parts.length <= MAX_COMPACT_PLACES) return { text: full, full, hidden: 0 };
  const hidden = parts.length - MAX_COMPACT_PLACES;
  const named = parts.slice(0, MAX_COMPACT_PLACES).join(' / ');
  const short = `${named} ${t('card.morePlaces', { n: hidden })}`;
  return { text: placeLabel(short, workModel), full, hidden };
}

/** Parse an ISO date or datetime into a Date, or null. */
export function parseDate(value) {
  if (!value || typeof value !== 'string') return null;
  const parsed = new Date(value.length === 10 ? `${value}T00:00:00Z` : value);
  return Number.isNaN(parsed.getTime()) ? null : parsed;
}

/** "21 Aug 2026", or an em dash. Locale-fixed so columns stay aligned. */
export function formatDate(value) {
  const date = parseDate(value);
  if (!date) return MISSING;
  const months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  return `${String(date.getUTCDate()).padStart(2, '0')} ${months[date.getUTCMonth()]} ${date.getUTCFullYear()}`;
}

/** The yyyy-mm-dd form an <input type="date"> wants. */
export function dateInputValue(value) {
  const date = parseDate(value);
  return date ? date.toISOString().slice(0, 10) : '';
}

/** Whole days between a date and now. Null when the date is missing. */
export function daysSince(value, now = new Date()) {
  const date = parseDate(value);
  if (!date) return null;
  return Math.floor((now.getTime() - date.getTime()) / 86400000);
}

/** "today" / "3 days ago" / "2 months ago". */
export function relativeAge(value, now = new Date()) {
  const days = daysSince(value, now);
  if (days === null) return t('age.notStated');
  if (days <= 0) return t('age.today');
  if (days === 1) return t('age.yesterday');
  if (days < 31) return t('age.daysAgo', { n: days });
  const months = Math.round(days / 30);
  return months <= 1 ? t('age.oneMonthAgo') : t('age.monthsAgo', { n: months });
}

//: The TONE of each freshness token. The word beside it comes from the
//: catalogue at render time -- this table used to carry it, and a table of
//: labels evaluated at module scope is the frozen-in-English trap that
//: `STATUS_OPTIONS` fell into on every card in the product.
const FRESHNESS_TONE = {
  FRESH: 'good',
  RECENT: 'neutral',
  AGING: 'warn',
  STALE: 'warn',
  CLOSED: 'bad',
  UNKNOWN: 'neutral',
};

/** {label, tone} for a freshness token, tolerating values we have not seen. */
export function freshness(value) {
  const key = String(value || 'UNKNOWN').toUpperCase();
  const tone = FRESHNESS_TONE[key];
  if (!tone) return { label: humanLabel(value), tone: 'neutral' };
  return { label: t(`freshness.${key}`), tone };
}

/**
 * The score as it is allowed to be displayed.
 * @returns {{text: string, scored: boolean, pct: number}}
 */
export function scoreDisplay(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) {
    return { text: MISSING, scored: false, pct: 0 };
  }
  const number = Math.round(Number(value));
  return { text: String(number), scored: true, pct: clampPct(number) };
}

export function clampPct(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return 0;
  return Math.max(0, Math.min(100, number));
}

/** Points as they appear next to a contribution: "+6.0" / "-2.5". */
export function formatPoints(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return MISSING;
  const sign = number > 0 ? '+' : '';
  return `${sign}${number.toFixed(1)}`;
}

/**
 * Salary, from either a plain string or a {min,max,currency,period,raw} object.
 * Returns null when the posting did not state one -- the caller is required to
 * say so explicitly rather than leaving a blank.
 */
export function formatSalary(salary) {
  if (!salary) return null;
  if (typeof salary === 'string') return salary.trim() || null;
  if (typeof salary !== 'object') return null;
  if (typeof salary.raw === 'string' && salary.raw.trim() && salary.min === undefined) {
    return salary.raw.trim();
  }
  const currency = salary.currency ? `${salary.currency} ` : '';
  const period = salary.period ? ` / ${String(salary.period).toLowerCase()}` : '';
  const min = Number.isFinite(Number(salary.min)) ? groupThousands(salary.min) : null;
  const max = Number.isFinite(Number(salary.max)) ? groupThousands(salary.max) : null;
  if (min && max) return `${currency}${min} to ${max}${period}`;
  if (min) return `${currency}from ${min}${period}`;
  if (max) return `${currency}up to ${max}${period}`;
  return typeof salary.raw === 'string' && salary.raw.trim() ? salary.raw.trim() : null;
}

function groupThousands(value) {
  return Math.round(Number(value)).toString().replace(/\B(?=(\d{3})+(?!\d))/g, ' ');
}

/** Split server text into paragraphs. The caller renders each as textContent. */
export function paragraphs(text) {
  if (typeof text !== 'string' || !text.trim()) return [];
  return text
    .replace(/\r\n/g, '\n')
    .split(/\n\s*\n/)
    .map((block) => block.trim())
    .filter(Boolean);
}

/** Eligibility tone: this drives colour AND the text beside it, never colour alone. */
export function eligibilityTone(status) {
  switch (String(status || '').toUpperCase()) {
    case 'VERIFIED_ELIGIBLE': return 'good';
    case 'LIKELY_ELIGIBLE': return 'good-soft';
    case 'VERIFIED_NOT_ELIGIBLE': return 'bad';
    default: return 'warn';
  }
}

export function gateTone(result) {
  switch (String(result || '').toUpperCase()) {
    case 'PASS': return 'good';
    case 'FAIL': return 'bad';
    default: return 'warn';
  }
}

export const APPLICATION_STATUSES = [
  'DISCOVERED', 'SHORTLISTED', 'TO_APPLY', 'APPLIED', 'INTERVIEW',
  'OFFER', 'HIRED', 'REJECTED', 'WITHDRAWN', 'ARCHIVED',
];

/**
 * What each stage is called on screen.
 *
 * `DISCOVERED` is the only one that needed its own word. It is the SYSTEM's
 * name for "I found this", and it is the default on every untouched job, so
 * sentence-casing it put a stage nobody chose at the top of every dropdown.
 * The board already made this correction once, calling `SHORTLISTED`
 * "Interested" rather than "Shortlisted".
 */
export const STATUS_WORDS = {
  DISCOVERED: 'New',
  // The board called this "Interested" and the dropdown called it
  // "Shortlisted", so somebody who marked a job Shortlisted went looking for a
  // column with that name and found one called INTERESTED instead. The comment
  // above already noted that the board "made this correction once"; what it did
  // not do was make the correction in one place.
  //
  // "Interested" wins because "shortlist" is a recruiter's word for what a
  // hiring side does to candidates, and this is a person saying they like a
  // job.
  SHORTLISTED: 'Interested',
};

/**
 * One status, in the words the whole product uses for it.
 *
 * The catalogue first. `STATUS_WORDS` stays as the English fallback and as
 * the place the SHORTLISTED -> "Interested" correction is recorded, so a
 * status the catalogue has never heard of still renders a word rather than an
 * enum.
 */
export function statusLabel(value) {
  const key = String(value || '').toUpperCase();
  const translated = tState('status', key, '');
  return translated || STATUS_WORDS[key] || humanLabel(key);
}

/**
 * The status dropdown, built ON DEMAND.
 *
 * It used to be a module-level constant, computed at import -- before
 * `setLocale` had read the stored choice -- so every status dropdown in the
 * product was frozen in whatever language the module happened to load in.
 * The drawer tabs and the filter rail each had this defect once; this is the
 * third and the only one that appeared on every card.
 */
export function statusOptions() {
  return APPLICATION_STATUSES.map((value) => ({ value, label: statusLabel(value) }));
}

/** Truncate for a chip or a cell, on a word boundary where possible. */
export function truncate(text, max = 90) {
  const value = String(text ?? '');
  if (value.length <= max) return value;
  const cut = value.slice(0, max);
  const space = cut.lastIndexOf(' ');
  return `${(space > max * 0.6 ? cut.slice(0, space) : cut).trimEnd()}…`;
}

export function pluralise(count, singular, plural = `${singular}s`) {
  return `${count} ${count === 1 ? singular : plural}`;
}

export { MISSING };
