/**
 * badges.js -- the paired readout, as numbers.
 *
 * This replaces the stacked progress bars. Two reasons, and the second is the
 * one that matters.
 *
 * A bar invites comparison of LENGTHS, and these two quantities must never be
 * compared to each other. `match_score` is "how much this is the work I want";
 * `data_confidence` is "how much of this posting we could actually read".
 * Drawing them as two tracks of the same width, one above the other, makes
 * "confidence is shorter than match" look like a finding. It is not a finding.
 * They are answers to different questions and share no scale (ADR-0004).
 *
 * The bars were also the tallest thing on every card -- roughly a fifth of the
 * card's height for two numbers -- which is what made the grid unscannable.
 *
 * So: two compact chips. Match is visually primary; confidence is secondary
 * and carries a title that says what it is confidence IN, because "Confidence
 * 85%" reads as "85% likely to get this job" to anyone who has not read the
 * documentation, and that is the single most dangerous misreading available
 * in this interface.
 *
 * A job with `match_score === null` shows "not scored". It never shows 0 --
 * "we did not score this" and "this scored zero" are opposite statements.
 */

import { tState, t } from './i18n.js';
import { el } from './dom.js';
import { eligibilityTone, humanLabel, scoreDisplay } from './format.js';

// FUNCTIONS, not constants. These were two module-level strings, built
// before `setLocale` had read the stored choice, so the tooltip on the single
// most important number in the product was frozen in whichever language the
// module happened to load in.
const CONFIDENCE_TITLE = () => t('badge.confidenceHelp');
const MATCH_TITLE = () => t('badge.matchHelp');

// Whether Career Agent knows enough about what the person wants to score at
// all. When it does not, Search Fit reads "not ready" instead of a number:
// a low score must mean "does not fit what you asked for", never "you have
// not said what you want yet".
let searchFitReady = true;

/** Set from `/api/search-fit/readiness`; NOT_READY hides every Search Fit number. */
export function setSearchFitReadiness(state) {
  searchFitReady = state !== 'NOT_READY';
}

export function searchFitIsReady() {
  return searchFitReady;
}

/**
 * The Match and Confidence chips.
 *
 * @param {object} job
 * @param {object} [options]
 *   size 'sm' | 'md'          -- 'sm' is the table row
 *   showEligibility {boolean} -- the third measurement, when it says something
 */
export function badges(job, { size = 'md', showEligibility = true } = {}) {
  const match = scoreDisplay(job.match_score);
  const confidence = scoreDisplay(job.data_confidence);

  const children = [
    searchFitReady
      ? chip({
        kind: 'match',
        label: t('legend.match'),
        display: match,
        band: job.fit_band,
        title: MATCH_TITLE(),
      })
      : el('span', {
        className: 'badge badge--match badge--unscored badge--not-ready',
        attrs: { title: t('badge.notReadyHelp') },
      }, [
        el('span', { className: 'badge__label', text: t('legend.match') }),
        el('span', { className: 'badge__value', text: t('badge.notReady') }),
      ]),
    chip({
      kind: 'confidence',
      // "Posting detail", not "Detail". Beside "Match 80%", a bare "Detail
      // 77%" reads as a second grade for the job, and it is not a grade at
      // all: it is how much the POSTING said. Naming what it measures is the
      // whole correction, and it is the same measurement either way.
      label: t('legend.detail'),
      display: confidence,
      band: job.confidence_band,
      title: CONFIDENCE_TITLE(),
    }),
  ];

  if (showEligibility) {
    const badge = eligibilityBadge(job);
    if (badge) children.push(badge);
  }

  return el('div', {
    className: `badges badges--${size}`,
    attrs: { role: 'group', 'aria-label': summary(job) },
  }, children);
}

function chip({ kind, label, display, band, title }) {
  const value = display.scored ? `${display.text}%` : t('badge.notScored');
  return el('span', {
    className: `badge badge--${kind}${display.scored ? '' : ' badge--unscored'}`
      + (band ? ` badge--band-${String(band).toLowerCase()}` : ''),
    attrs: { title },
  }, [
    el('span', { className: 'badge__label', text: label }),
    el('span', { className: 'badge__value', text: value }),
  ]);
}

/**
 * Whether you could take this job, in words.
 *
 * Exported because the TABLE rendered the same data through `humanLabel` and
 * got "Verified not eligible", "Unresolved", "Verified eligible". The first of
 * those says a person was checked and failed, which is not what happened: the
 * posting stated a requirement. One vocabulary, one function, both views.
 */
export function eligibilityWords(status) {
  // The catalogue, so the ONE vocabulary this function exists to enforce is
  // one vocabulary in both languages too. The stored value is untouched:
  // `VERIFIED_NOT_ELIGIBLE` is what the column holds and what the query
  // string asks for, and only what a person reads moves.
  const translated = tState('eligibility', status, null);
  return translated === null ? humanLabel(status) : translated;
}

/**
 * Eligibility, which is a different KIND of answer and so looks different.
 *
 * `UNRESOLVED` is the common case and the honest one -- most postings never
 * say where they hire -- so it is shown quietly rather than as a warning. A
 * failed gate is shown loudly, because a high match score must never soften
 * one.
 */
function eligibilityBadge(job) {
  const status = String(job.eligibility_status || '').toUpperCase();
  if (!status) return null;
  const tone = eligibilityTone(status);
  const text = eligibilityWords(status);
  return el('span', {
    className: `badge badge--eligibility badge--${tone}`,
    attrs: {
      title: status === 'UNRESOLVED'
        ? t('badge.eligibilityUnresolvedHelp')
        : t('badge.eligibilityHelp'),
    },
    text,
  });
}

/** One sentence for a screen reader, so the group is not read as loose numbers. */
function summary(job) {
  const match = scoreDisplay(job.match_score);
  const confidence = scoreDisplay(job.data_confidence);
  const parts = [
    match.scored ? t('a11y.matchPercent', { n: match.text }) : t('a11y.notScored'),
    confidence.scored
      ? t('a11y.readablePercent', { n: confidence.text })
      : t('badge.readabilityUnknown'),
  ];
  const status = String(job.eligibility_status || '').toUpperCase();
  if (status) parts.push(humanLabel(status).toLowerCase());
  return parts.join(', ');
}


/**
 * One number for a table cell. No track, no bar.
 *
 * The table had the same two-bar problem the cards did, at a twentieth of the
 * size: a 40-pixel track under each number, in two columns, inviting exactly
 * the length comparison these two quantities do not support. A dense table
 * wants a number and nothing else, and the column header says which number.
 */
export function scoreCell(value, kind) {
  const display = scoreDisplay(value);
  return el('span', {
    className: `num num--${kind}${display.scored ? '' : ' num--none'}`,
    text: display.scored ? display.text : t('badge.notScored'),
    attrs: display.scored
      ? {}
      : { title: kind === 'match' ? t('badge.notScored') : t('badge.notMeasured') },
  });
}
