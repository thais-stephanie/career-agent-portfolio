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
 * So the collapsed card shows the decision surface only: the two numbers,
 * eligibility when it says something, who and what, where and on what terms,
 * three technologies, and the controls. The scoring breakdown, the evidence
 * quotes, the gaps and the unknown counts all live in the drawer, one click
 * away, where there is room to lay them out properly.
 *
 * THE WHOLE CARD OPENS IT. Previously only the title was clickable, which is a
 * small target in a grid of large objects. The card is a button-like surface:
 * click anywhere that is not itself a control, or focus it and press Enter or
 * Space. The interactive elements inside stop their own events, so changing a
 * status does not also open the drawer behind it.
 */

import { el, button, extLink, select, replace } from './dom.js';
import {
  statusOptions, compactPlace, formatDate, formatSalary, freshness, parseDate, prominenceWords,
  relativeAge, vocabLabel,
} from './format.js';
import { badges } from './badges.js';
import { t } from './i18n.js';

/** Three, then a count. The drawer lists all of them. */
//: How many tools a CARD names before it starts counting.
//
// Three, until the grid got dense enough to show five cards across. A
// lexicon signal is a described phrase rather than a word -- "REST APIs,
// webhooks, integration engineering" -- so three of them wrapped to six
// lines and were a third of the card's height, above the salary and the
// footer. Two, and the `+N` chip says exactly how many are not shown; the
// drawer lists every one of them with its prominence. Nothing is dropped
// silently, which is the only thing that would make this a lie.
const MAX_TECHNOLOGIES = 2;

//: How many of a grouped card's sibling locations it names before counting.
const MAX_PLACES = 2;

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

  // THE 6px STRIP ACROSS THE TOP, tinted by the match band.
  //
  // From the Workspace V2 design, and it earns its place: a wall of cards is
  // read by shape before it is read by word, and the strip is the only thing
  // that says "worth a look" at a glance without a number.
  //
  // IT IS THE MATCH BAND AND NOTHING ELSE. Not eligibility, not confidence.
  // ADR-0004 keeps three measurements apart and a strip that blended them
  // would put the blend at the top of every card. A card an employer has
  // ruled her out of keeps its own outline and its own sentence, so a high
  // match cannot soften a failed gate: the strip says the score, the border
  // says the verdict, and they are allowed to disagree on screen because they
  // disagree in fact.
  root.appendChild(el('div', {
    className: `card__strip card__strip--${String(job.fit_band || 'unknown').toLowerCase()}`,
    attrs: { 'aria-hidden': 'true' },
  }));

  const open = () => handlers.onOpen(job.job_id);
  root.addEventListener('click', (event) => {
    // A control inside the card handles its own click. Without this, changing
    // the status dropdown would also open the drawer behind it.
    if (event.target.closest('[data-stops-open]')) return;
    open();
  });
  root.addEventListener('keydown', (event) => {
    if (event.key !== 'Enter' && event.key !== ' ') return;
    if (event.target.closest('[data-stops-open]')) return;
    event.preventDefault();  // Space must not scroll the list.
    open();
  });

  // Compact, but never absent. A gate that failed outranks whatever the match
  // score says, and the card must not let a 90% soften it. The REASON lives
  // in the drawer.
  if (aside.gated) {
    // THE REASON, on the card, since 2026-09-07.
    //
    // It used to say only "this posting states a requirement you do not meet"
    // and leave the reason in the drawer. That was fine while these postings
    // were hidden by default and a rarity; it is not fine now that revealing
    // them is a deliberate act. Somebody who asked to see excluded jobs is
    // asking WHY, and making them open each one to find out is the interface
    // knowing something and not saying it.
    //
    // The employer's own words where the gate quoted any -- `Remote U.S.` is
    // shorter, clearer and more trustworthy than any sentence this product
    // could compose about it.
    // THE EMPLOYER'S OWN WORDS FIRST, and for a reason that is not brevity.
    //
    // A gate's `reason` is a sentence the SERVER composed, and the server
    // composes in English. Putting it on the card left one English line in the
    // middle of a Portuguese page. `location_raw` is what the employer typed --
    // `Remote U.S.` -- so it needs no translation, is shorter, and is more
    // trustworthy than any sentence this product could write about it.
    //
    // The reason is still the fallback, because a blocker that is not about
    // geography has nothing else to say, and an untranslated explanation beats
    // no explanation.
    const blocker = (job.blockers || [])[0] || {};
    const because = (blocker.gate === 'geography' && job.location_raw)
      ? job.location_raw
      : (blocker.quote || blocker.reason || '');
    const gatedText = because ? t('card.gatedBecause', { reason: because }) : t('card.gated');
    // One line on the card, the whole sentence in `title`: a six-country
    // list is the reason and it is still the reason when it is clipped.
    root.appendChild(el('p', { className: 'card__blocked', attrs: { title: gatedText } }, [
      el('span', { className: 'card__blocked-glyph', text: '✕', attrs: { 'aria-hidden': 'true' } }),
      el('span', { text: gatedText }),
    ]));
  } else if (aside.offTarget) {
    // A different fact and a different tone. Nobody rejected anybody: this
    // person said what work they want and this posting is about other work.
    // It carries the REASON on the card, because unlike a failed gate the
    // reason is short and is the whole of the news.
    // `title_reason` is the label of a rule in the person's OWN search
    // configuration. A word the configuration chose is the product
    // working, so it is shown as written rather than translated.
    const offText = job.title_reason
      ? t('card.offTargetBecause', { reason: job.title_reason })
      : t('card.offTarget');
    root.appendChild(el('p', { className: 'card__offtarget', attrs: { title: offText } }, [
      el('span', { className: 'card__blocked-glyph', text: '~', attrs: { 'aria-hidden': 'true' } }),
      el('span', { text: offText }),
    ]));
  }

  // A THIRD kind of note, and the quietest of the three on purpose.
  //
  // The two above are facts: an employer stated a requirement, or this person
  // said she wants other work. This one is a likelihood read off how the
  // compensation is described, so it must not borrow their weight. It appears
  // only where the employer said NOTHING about who may apply -- an explicit
  // scope, in either direction, outranks an inference from a benefits list,
  // and printing both would invite the reader to average them.
  if (job.domestic_context === 'LIKELY_US_DOMESTIC'
      && job.eligibility_status === 'UNRESOLVED') {
    root.appendChild(el('p', { className: 'card__context' }, [
      el('span', { className: 'card__blocked-glyph', text: '?', attrs: { 'aria-hidden': 'true' } }),
      el('span', { text: t('domestic.LIKELY_US_DOMESTIC') }),
    ]));
  }

  // A FOURTH note, and the only one about US rather than about the posting.
  //
  // The three above are readings of what an employer wrote. This one says how
  // much of what they wrote we actually hold: a source returning a `snippet`
  // with no way to fetch the rest has told us part of a job that may be
  // excellent. It sits beside them because a reader weighing a thin card
  // needs to know which kind of thin it is, and it is styled as the quietest
  // of the four because it is a fact about a pipeline.
  if (job.content_completeness === 'PARTIAL_CONTENT'
      || job.content_completeness === 'METADATA_ONLY') {
    root.appendChild(el('p', { className: 'card__partial' }, [
      el('span', { className: 'card__blocked-glyph', text: '…', attrs: { 'aria-hidden': 'true' } }),
      el('span', {
        text: t(`content.${job.content_completeness}`),
        attrs: {
          title: job.content_completeness === 'PARTIAL_CONTENT'
            ? t('content.partialHelp')
            : t('content.metadataHelp'),
        },
      }),
    ]));
  }

  root.appendChild(badges(job, { size: 'md' }));

  root.appendChild(el('div', { className: 'card__head' }, [
    el('p', { className: 'card__company', text: job.company_name || t('absent.companyStated') }),
    // A heading, not a button: the whole card is the control now, and a
    // nested button inside a clickable surface is two tab stops for one
    // action. The heading keeps the document outline for screen readers.
    // Clamped to two lines by the stylesheet, so the FULL title rides in
    // `title`; the drawer prints it whole.
    el('h3', {
      className: 'card__title',
      text: job.title || 'Untitled posting',
      attrs: { title: job.title || '' },
    }),
  ]));

  const group = groupBadge(job);
  if (group) root.appendChild(group);

  // THREE FACTS, THREE LINES, EACH ONE LINE.
  //
  // The labels (`Where`, `Contract`, `Salary`) are still in the markup for a
  // screen reader and the drawer, and visually hidden on the card: a 62px
  // label column beside a 12px fact was a quarter of the card's width spent
  // saying what the value already says. Each value is clipped to its line
  // with an ellipsis and carries the whole text in `title`, so a long office
  // list degrades into a shorter line rather than into a taller card. The
  // place goes through `compactPlace`, which COUNTS what it leaves out.
  const place = compactPlace(job.location_raw, job.work_model);
  const salary = formatSalary(job.salary);
  root.appendChild(el('dl', { className: 'card__facts' }, [
    el('dt', { className: 'sr-only', text: t('card.where') }),
    el('dd', {
      className: 'fact--place',
      text: place.text,
      attrs: { title: place.full },
    }),
    el('dt', { className: 'sr-only', text: t('card.contract') }),
    el('dd', { className: 'fact--terms', text: terms(job), attrs: { title: terms(job) } }),
    el('dt', { className: 'sr-only', text: t('card.salary') }),
    el('dd', {
      className: salary ? 'fact--salary' : 'fact--absent',
      // The FIGURE is the employer's and is never translated. The sentence
      // saying they did not give one is ours.
      text: salary || t('card.salaryUnstated'),
      attrs: { title: salary || t('card.salaryUnstated') },
    }),
  ]));

  const technologies = job.technologies || [];
  if (technologies.length) {
    const shown = technologies.slice(0, MAX_TECHNOLOGIES);
    const rest = technologies.length - shown.length;
    const chips = shown.map((tech) => el('li', {
      className: `chip chip--${String(tech.prominence || 'INCIDENTAL').toLowerCase()}`,
      text: tech.label || tech.signal_id,
      attrs: { title: `${tech.label || tech.signal_id}: ${prominenceWords(tech.prominence)}` },
    }));
    if (rest > 0) {
      chips.push(el('li', {
        className: 'chip chip--more',
        text: `+${rest}`,
        attrs: { title: t('card.moreTools', { n: rest }) },
      }));
    }
    root.appendChild(el('ul', {
      className: 'chips chips--tech',
      attrs: { 'aria-label': t('card.toolsLabel') },
    }, chips));
  }

  root.appendChild(footer(job, handlers));
  return root;
}

/**
 * What a grouped card stands for, said out loud.
 *
 * A representative row that quietly swallowed seven siblings is worse than
 * eight cards: the reader cannot tell anything was dropped, and the one thing
 * that actually differs between those postings -- where the job is -- is the
 * thing that disappeared.
 */
function groupBadge(job) {
  const count = Number(job.duplicate_count || 1);
  if (count <= 1) return null;
  // Two places named, and the rest counted. Seven of them set end to end
  // filled three lines with the same two words repeated, and the card is a
  // scanning surface: the drawer names all of them.
  const all = job.sibling_locations || [];
  const places = all.slice(0, MAX_PLACES);
  const hidden = count - places.length;
  const named = hidden > 0 ? [...places, t('card.morePlaces', { n: hidden })] : [...places];
  return el('p', { className: 'card__group' }, [
    el('span', {
      className: 'card__group-count',
      // The PLACES are the employer's own words and are joined untouched.
      text: t('card.locations', { n: count }),
      attrs: { title: t('card.locationsHelp', { n: count }) },
    }),
    el('span', { className: 'card__group-places', text: named.join(' · ') }),
  ]);
}

/** The status, lowercased, for the tag's colour class. */
function statusKey(job) {
  return String(job.application_status || 'DISCOVERED').toLowerCase();
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

function footer(job, handlers) {
  const age = freshness(job.freshness);

  const status = select(
    statusOptions(),
    job.application_status,
    (value) => handlers.onStatus(job.job_id, value),
    {
      // `select--status` is shared with the table: one class means "this is
      // the status control", whichever view drew it.
      className: `select--status status-tag status-tag--${statusKey(job)}`,
      ariaLabel: t('card.statusLabel', { title: job.title }),
    },
  );
  status.dataset.stopsOpen = 'true';

  // THE COMMITTED PIXEL HEART, from the design bundle, at 16px.
  //
  // A `<span>` carrying the sprite as a background rather than an `<img>`:
  // the grey-to-colour transition is a CSS filter, an `<img>` that failed to
  // load would leave a broken-image glyph in the middle of a card, and the
  // WORD beside it is what a screen reader announces either way.
  const heart = el('span', {
    className: 'card__heart',
    attrs: { 'aria-hidden': 'true' },
  });
  const saved = button(
    // The word is the label; the mark is decoration beside it. Only the word
    // moves between languages.
    job.saved ? t('card.saved') : t('card.save'),
    () => handlers.onSave(job.job_id, !job.saved),
    {
      className: `card__save${job.saved ? ' is-saved' : ''}`,
      // The TITLE inside the label is the employer's and is never translated.
      ariaLabel: job.saved
        ? t('card.unsaveLabel', { title: job.title })
        : t('card.saveLabel', { title: job.title }),
    },
  );
  saved.dataset.stopsOpen = 'true';
  saved.prepend(heart);

  // The action a person opened the card to take. It had no NAME at first --
  // the only words on a card were a status dropdown, Save, and "Open
  // original" -- and then no CLASS, so it rendered in the browser's default
  // link blue: a colour from outside this palette, weaker than the Save
  // button beside it and weaker than a coral button offering to show jobs
  // that rule them out. Coral is the action colour and this is the action.
  const link = extLink(job.url, t('card.apply'), { className: 'card__apply' });
  if (link) link.dataset.stopsOpen = 'true';

  // "Fresh" and a dropdown reading "New", side by side and neither labelled.
  // Two ideas of newness on one line: one is how old the ADVERT is, the other
  // is where YOU are with it. Both now say which.
  //
  // Three declared rows rather than one wrapping line: how old the advert
  // is and whether it is kept, then where this person is with it, then the
  // way out to the employer. The last has a row to itself because it is the
  // only control on a card that LEAVES this product -- nothing here submits
  // an application, and a link beside a dropdown reads as part of the same
  // gesture.
  // Hide, or put back. The word changes with the state because the control
  // does: in the restore view every card is already hidden and "Hide" would
  // be a button that does nothing visible.
  const hide = button(
    job.hidden ? t('card.unhide') : t('card.hide'),
    // A grouped card stands for a ROLE. Hiding one of its postings promoted
    // a sibling into the same place on screen, which reads as a control that
    // does nothing.
    () => handlers.onHidden(
      job.job_id,
      !job.hidden,
      Number(job.duplicate_count || 1) > 1 ? 'role' : 'posting',
    ),
    {
      className: `card__hide${job.hidden ? ' is-hidden' : ''}`,
      ariaLabel: job.hidden
        ? t('card.unhideLabel', { title: job.title })
        : t('card.hideLabel', { title: job.title }),
    },
  );
  hide.dataset.stopsOpen = 'true';

  // WHERE IT CAME FROM, beside how old it is.
  //
  // The design puts `source . postedAt` on the footer and the card had only
  // the age. Provenance belongs on the surface: "Greenhouse, 2 days ago" and
  // "an aggregator republished this, 2 days ago" are different postings to
  // trust, and making somebody open a drawer to tell them apart is the
  // interface knowing something and not saying it.
  //
  // One line, and it TRUNCATES rather than wrapping: a long source string
  // pushing the controls onto a second row is how a footer stops being a
  // footer. The full string stays in the `title`.
  //
  // The source is the board alone. How it was read (ATS structured,
  // aggregator API) is plumbing, not something a reader decides with. The
  // date part appears ONLY when the employer published one: `first_seen_at`
  // is when this app collected the posting, and printing it as "Posted" would
  // pass a collection timestamp off as the employer's date.
  const provenance = job.provider ? vocabLabel(job.provider) : t('absent.source');
  const postedDate = parseDate(job.posted_at) ? formatDate(job.posted_at) : null;
  const posted = postedDate ? t('card.posted', { date: postedDate }) : null;
  const meta = posted ? `${provenance} \u00b7 ${posted}` : provenance;

  return el('div', { className: 'card__footer' }, [
    el('div', { className: 'card__foot-top' }, [
      el('p', {
        className: `card__age card__age--${age.tone}`,
        text: meta,
        attrs: {
          title: posted
            ? `${meta} (${relativeAge(job.posted_at)})`
            :`${provenance} \u00b7 ${t('card.noPostedDate')}`,
        },
      }),
      el('span', { className: 'card__foot-right' }, [saved, hide]),
    ]),
    el('div', { className: 'card__actions' }, [
      el('span', { className: 'card__actions-label', text: t('card.you') }),
      status,
    ]),
    link,
  ].filter(Boolean));
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
