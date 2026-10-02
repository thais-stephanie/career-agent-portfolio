/**
 * cards.js -- the scanning view.
 *
 * A card answers one question: is this worth opening? Everything that helps
 * answer it is here; everything else is in the drawer.
 *
 * WHAT WAS WRONG BEFORE. The card carried the full analysis -- two progress
 * bars, six facts, six technology chips, three scored strength rows with their
 * points, a wrapped warning paragraph, an "N things this posting never said"
 * line, and the footer. At 1440x900 that is three tall report columns and
 * roughly two and a half cards visible. A grid you cannot scan is not a grid;
 * it is a list of documents.
 *
 * So the collapsed card shows the decision surface only (redesign handoff,
 * 2026-10): how well it matches, who and what, where, on what terms, the
 * pay, when and where it was posted, why it is set aside when it is, Apply,
 * the heart, and the two ways out. Posting completeness, the tools, the
 * status control and the content notes live in the drawer, one click away.
 *
 * THE WHOLE CARD OPENS IT. Previously only the title was clickable, which is a
 * small target in a grid of large objects. The card is a button-like surface:
 * click anywhere that is not itself a control, or focus it and press Enter or
 * Space. The interactive elements inside stop their own events, so changing a
 * status does not also open the drawer behind it.
 */

import { el, button, extLink, replace } from './dom.js';
import {
  compactPlace, formatDate, formatSalary, parseDate, relativeAge, scoreDisplay, statusLabel,
  vocabLabel,
} from './format.js';
import { eligibilityWords, searchFitIsReady } from './badges.js';
import { t } from './i18n.js';

export function renderCards(mount, items, handlers) {
  replace(mount, items.map((job) => card(job, handlers)));
  mount.className = 'cards';
  mount.setAttribute('role', 'list');
  return mount;
}

/**
 * The two ways a posting can be set aside, which are NOT the same thing.
 *
 * `blockers` is the employer: the posting states a requirement this person
 * does not meet, with a quote behind it. `screening_state` is US: the search
 * decided this is not the kind of work that was asked for.
 *
 * They were one flag, and it said "the posting rules you out" for both. Three
 * cards on the default screen therefore accused an employer of rejecting
 * somebody when the employer had done nothing of the kind, while the three
 * postings that genuinely do rule them out were hidden and silent.
 */
function setAside(job) {
  const gated = (job.blockers || []).length > 0;
  const offTarget = String(job.screening_state).toUpperCase() === 'BLOCKED';
  return { gated, offTarget, any: gated || offTarget };
}

/**
 * The match label beside the percentage. A DISPLAY label only: the bands are
 * read off the same number the list is ordered by, and nothing is decided by
 * them. Great at 90 and over, Good at 75 and over, Fair below.
 */
export function matchTone(score) {
  if (score === null || score === undefined || score === '') return null;
  const value = Number(score);
  if (!Number.isFinite(value)) return null;
  if (value >= 90) return { tone: 'm1', key: 'card.matchGreat' };
  if (value >= 75) return { tone: 'm2', key: 'card.matchGood' };
  return { tone: 'm3', key: 'card.matchFair' };
}

//: Statuses that mean an application was sent; the card shows where it stands
//: instead of offering to apply again.
const SENT = new Set(['APPLIED', 'INTERVIEW', 'OFFER', 'HIRED']);

//: Jobs whose "Apply" was opened in this tab and are waiting for the answer
//: to "Did you send your application?". Opening a page is not applying, so
//: nothing is recorded until the person says yes.
const asking = new Set();

function card(job, handlers) {
  const aside = setAside(job);
  const root = el('article', {
    className: `card${aside.gated ? ' card--blocked' : ''}${!aside.gated && aside.offTarget ? ' card--offtarget' : ''}`,
    attrs: {
      role: 'listitem',
      tabindex: '0',
      'aria-label': t('card.openLabel', {
        title: job.title || t('absent.untitled'),
        company: job.company_name || t('absent.company'),
      }),
    },
    dataset: { jobId: job.job_id },
  });

  // The whole card opens the posting; any control inside it says so with
  // `data-stops-open` and handles its own click.
  const open = () => handlers.onOpen(job.job_id);
  root.addEventListener('click', (event) => {
    if (event.target.closest('[data-stops-open]')) return;
    open();
  });
  root.addEventListener('keydown', (event) => {
    if (event.key !== 'Enter' && event.key !== ' ') return;
    if (event.target.closest('[data-stops-open]')) return;
    event.preventDefault();  // Space must not scroll the list.
    open();
  });

  root.appendChild(matchBlock(job, handlers));

  root.appendChild(el('div', { className: 'card__head' }, [
    el('p', { className: 'card__company', text: job.company_name || t('absent.companyStated') }),
    el('h3', {
      className: 'card__title',
      text: job.title || t('absent.untitled'),
      attrs: { title: job.title || '' },
    }),
  ]));

  const place = compactPlace(job.location_raw, job.work_model);
  const salary = formatSalary(job.salary);
  const count = Number(job.duplicate_count || 1);
  const whereTitle = count > 1
    ? [place.full, ...(job.sibling_locations || [])].filter(Boolean).join(' · ')
    : place.full;
  // A grouped card stands for one role posted in several places; it says how
  // many, and its tooltip names every one.
  const group = count > 1
    ? el('span', { className: 'card__group', text: ` · ${t('card.locations', { n: count })}` })
    : null;
  root.appendChild(el('dl', { className: 'card__facts' }, [
    el('dt', { className: 'sr-only', text: t('card.where') }),
    el('dd', { className: 'fact--place', attrs: { title: whereTitle } }, [place.text, group].filter(Boolean)),
    el('dt', { className: 'sr-only', text: t('card.contract') }),
    el('dd', { className: 'fact--terms', text: terms(job), attrs: { title: terms(job) } }),
    el('dt', { className: 'sr-only', text: t('card.salary') }),
    el('dd', {
      className: salary ? 'fact--salary' : 'fact--absent',
      text: salary || t('card.salaryUnstated'),
      attrs: { title: salary || t('card.salaryUnstated') },
    }),
    el('dt', { className: 'sr-only', text: t('card.postedLabel') }),
    el('dd', { className: 'fact--posted', text: postedLine(job), attrs: { title: postedTitle(job) } }),
  ]));

  // WHETHER THE EMPLOYER HIRES HERE, always, and the employer's reason when
  // a gate failed; the full answer, quoted, is in the details.
  root.appendChild(eligibilityLine(job, aside));
  const note = offTargetNote(job, aside);
  if (note) root.appendChild(note);

  root.appendChild(el('div', { className: 'card__grow', attrs: { 'aria-hidden': 'true' } }));
  const actions = el('div', { className: 'card__act' });
  const redraw = () => replace(actions, actionRow(job, handlers, redraw));
  redraw();
  root.appendChild(actions);

  const details = button(t('card.seeDetails'), open, { className: 'card__details' });
  details.dataset.stopsOpen = 'true';
  const hide = button(
    job.hidden ? t('card.unhide') : t('card.notForMe'),
    () => handlers.onHidden(
      job.job_id,
      !job.hidden,
      count > 1 ? 'role' : 'posting',
    ),
    {
      className: `card__hide${job.hidden ? ' is-hidden' : ''}`,
      ariaLabel: job.hidden
        ? t('card.unhideLabel', { title: job.title })
        : t('card.hideLabel', { title: job.title }),
    },
  );
  hide.dataset.stopsOpen = 'true';
  root.appendChild(el('div', { className: 'card__links' }, [details, hide]));
  return root;
}

/** The percentage, its label and the meter; a click opens the "Why" tab. */
function matchBlock(job, handlers) {
  if (!searchFitIsReady()) {
    return el('div', { className: 'card__match card__match--m3', attrs: { title: t('badge.notReadyHelp') } }, [
      el('div', { className: 'card__matchrow' }, [
        el('span', { className: 'card__pct', text: t('badge.notReady') }),
      ]),
    ]);
  }
  const score = scoreDisplay(job.match_score);
  const tone = score.scored ? matchTone(job.match_score) : null;
  const node = button('', () => handlers.onWhy(job.job_id), {
    className: `card__match card__match--${tone ? tone.tone : 'm3'}`,
    ariaLabel: t('card.whyLabel', { n: score.text }),
    attrs: { title: t('card.whyHelp') },
  });
  node.dataset.stopsOpen = 'true';
  // Through the CSSOM: the page's CSP refuses a `style` attribute.
  const bar = el('span', { className: 'card__meterbar' });
  bar.style.width = `${score.pct}%`;
  node.append(
    el('span', { className: 'card__matchrow' }, [
      el('span', { className: 'card__pct num', text: score.scored ? `${score.text}%` : score.text }),
      el('span', { className: 'card__matchtext', text: tone ? t(tone.key) : t('card.matchUnscored') }),
    ]),
    el('span', { className: 'card__meter', attrs: { 'aria-hidden': 'true' } }, [bar]),
  );
  return node;
}

/** Apply and the heart; then the question; then where the application stands. */
function actionRow(job, handlers, redraw) {
  const status = String(job.application_status || 'DISCOVERED');
  if (SENT.has(status)) {
    const undo = handlers.canUndoApply && handlers.canUndoApply(job.job_id)
      ? button(t('action.undo'), () => handlers.onUndoApply(job.job_id), { className: 'card__undo' })
      : null;
    if (undo) undo.dataset.stopsOpen = 'true';
    return [el('div', { className: 'card__sent' }, [
      el('span', { className: 'card__sentlabel', text: `✓ ${statusLabel(status)}` }),
      undo,
    ])];
  }
  if (asking.has(job.job_id)) {
    const yes = button(t('card.askYes'), () => {
      asking.delete(job.job_id);
      handlers.onApplied(job.job_id);
    }, { className: 'card__yes' });
    const no = button(t('card.askNo'), () => { asking.delete(job.job_id); redraw(); }, { className: 'card__no' });
    yes.dataset.stopsOpen = 'true';
    no.dataset.stopsOpen = 'true';
    return [el('div', { className: 'card__ask', attrs: { role: 'group', 'aria-label': t('card.askQuestion') } }, [
      el('p', { className: 'card__askq', text: t('card.askQuestion') }),
      el('div', { className: 'card__askbtns' }, [yes, no]),
    ])];
  }
  const link = extLink(job.url, t('card.applyShort'), {
    className: 'card__apply', title: t('card.apply'),
  });
  link.dataset.stopsOpen = 'true';
  if (link.tagName === 'A') {
    // The page opens in a new tab; the question waits here for the answer.
    link.addEventListener('click', () => {
      asking.add(job.job_id);
      setTimeout(redraw, 0);
    });
  }
  const heart = button(job.saved ? '♥' : '♡', () => handlers.onSave(job.job_id, !job.saved), {
    className: `card__heart${job.saved ? ' is-saved' : ''}`,
    ariaLabel: job.saved
      ? t('card.unsaveLabel', { title: job.title })
      : t('card.saveLabel', { title: job.title }),
    attrs: { 'aria-pressed': String(Boolean(job.saved)) },
  });
  heart.dataset.stopsOpen = 'true';
  return [el('div', { className: 'card__applyrow' }, [link, heart])];
}

/**
 * The eligibility answer, always: the handoff's card has no badge, and this
 * product may not drop the one fact it exists to state. One short line in
 * the shared vocabulary, with the employer's reason when a gate failed.
 */
function eligibilityLine(job, aside) {
  const status = aside.gated ? 'VERIFIED_NOT_ELIGIBLE' : (job.eligibility_status || 'UNRESOLVED');
  const tone = status === 'VERIFIED_ELIGIBLE' ? 'good' : status === 'VERIFIED_NOT_ELIGIBLE' ? 'bad' : 'warn';
  let text = eligibilityWords(status);
  let title = status === 'UNRESOLVED' ? t('card.unresolvedHelp') : text;
  if (aside.gated) {
    const blocker = (job.blockers || [])[0] || {};
    // A geography gate quotes where the job is; every other gate quotes the
    // sentence that stated the requirement.
    const because = (blocker.gate === 'geography' && job.location_raw)
      ? job.location_raw
      : (blocker.quote || blocker.reason || '');
    text = because ? `${text}: ${because}` : t('card.gated');
    title = text;
  }
  return el('p', { className: `card__elig card__elig--${tone}`, text, attrs: { title } });
}

/** The search set the WORK aside: a different fact from the employer's gate. */
function offTargetNote(job, aside) {
  if (!aside.offTarget || aside.gated) return null;
  const text = job.title_reason
    ? t('card.offTargetBecause', { reason: job.title_reason })
    : t('card.offTarget');
  return el('p', { className: 'card__note card__offtarget', text, attrs: { title: text } });
}

function postedLine(job) {
  const source = job.provider ? vocabLabel(job.provider) : t('absent.source');
  const date = parseDate(job.posted_at) ? formatDate(job.posted_at) : null;
  return date ? t('card.postedOn', { date, source }) : `${source} · ${t('card.noPostedDate')}`;
}

function postedTitle(job) {
  return parseDate(job.posted_at) ? `${postedLine(job)} (${relativeAge(job.posted_at)})` : postedLine(job);
}

/** Seniority and contract on one line: both are short and neither is worth a row. */
function terms(job) {
  const parts = [];
  // A level the POSTING stated, or the fact that it did not. `seniority` is
  // never empty any more -- an unstated level reads MID by default -- so the
  // card asks `seniority_stated` rather than the value. Printing "Mid-level"
  // over a posting that said nothing is the whole defect the reading was
  // built to end, and it is invisible: it looks exactly like a fact.
  //
  // The provenance itself stays off the card. It belongs in the drawer,
  // beside the quote it came from.
  if (job.seniority) {
    parts.push(job.seniority_stated ? vocabLabel(job.seniority) : t('absent.level'));
  }
  parts.push(job.employment_type ? vocabLabel(job.employment_type) : t('absent.contract'));
  return parts.join(' · ');
}

/** The loading grid. Same footprint as a real card, so nothing jumps. */
export function cardsSkeleton(mount, count = 6) {
  mount.className = 'cards';
  mount.setAttribute('role', 'list');
  replace(mount, Array.from({ length: count }, () => el('div', {
    className: 'card card--skeleton',
    attrs: { 'aria-hidden': 'true' },
  }, [
    el('div', { className: 'sk sk--badges' }),
    el('div', { className: 'sk sk--line sk--w40' }),
    el('div', { className: 'sk sk--line sk--w80' }),
    el('div', { className: 'sk sk--chips' }),
  ])));
}
