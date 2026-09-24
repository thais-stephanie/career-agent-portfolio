/**
 * The Career Profile: what this product believes you are looking for.
 *
 * It was entirely read-only, on the argument that a second write path is how
 * two files start disagreeing. That was right about the PHRASE GROUPS, which
 * have their own editor two disclosures away, and wrong about everything else:
 * the choices a person is most likely to want to change -- how they will work,
 * what they will accept, where they live -- could only be changed by opening
 * YAML. The screen described somebody's search back to them and let them alter
 * none of it.
 *
 * So there is one form, over `PATCH /api/profile`, and it draws itself from
 * what the SERVER says is editable rather than from a list kept here. Two lists
 * would drift, and the failure mode is a control that saves, fails, and teaches
 * the reader that the panel is unreliable.
 */

import { patchProfile } from './api.js';
import { button, clear, el, field, replace } from './dom.js';
import { t, tVocab } from './i18n.js';
import { tagInput } from './tags.js';
import {
  ARRANGEMENT_FIELDS, WORK_MODEL_FIELDS, arrangementMatrix, workModelMatrix,
} from './choices.js';

/**
 * THE FOUR DESTINATIONS, AND WHAT SEPARATES THEM.
 *
 * The page used to be four tabs' worth of PREFERENCES and no candidate at
 * all: it described a search back to somebody and never once described the
 * person doing the searching, while 295 facts she had personally confirmed
 * sat in a ledger on another page.
 *
 * The split the design draws is the split that matters here, and it is
 * WHAT YOU HAVE DONE against WHAT YOU ARE LOOKING FOR:
 *
 *   overview     you at a glance. Counts of what you have confirmed, your
 *                most recent work, and your skills.
 *   experience   the roles, grouped by employer and period, with the lines
 *                you stand behind under each.
 *   skills       skills, tools and qualifications, as words rather than rows.
 *   preferences  everything the page used to be: your answers, what the
 *                system does with them, and the phrase machinery.
 *
 * The first three read from the EVIDENCE LEDGER and are read-only here.
 * Editing a claim is an act with invariants around it -- nothing becomes
 * verified except through the three places allowed to set it -- and those
 * live on the Evidence page. A second editor over the same facts is how two
 * screens start disagreeing about what somebody has confirmed.
 *
 * A tab with nothing in it is not drawn, so a machine where nothing has been
 * confirmed shows Overview (which says so, and offers the way in) beside
 * Preferences, and no empty Experience.
 */
const PROFILE_TABS = [
  { key: 'overview', labelKey: 'profileTab.overview' },
  { key: 'experience', labelKey: 'profileTab.experience' },
  { key: 'skills', labelKey: 'profileTab.skills' },
  { key: 'preferences', labelKey: 'profileTab.preferences' },
];

/** Claim kinds that describe WORK DONE, and belong on Experience. */
const HISTORY_TYPES = new Set(['EMPLOYMENT', 'PROJECT', 'ACHIEVEMENT']);
/** Claim kinds that are a word rather than a sentence. */
const SKILL_TYPES = new Set(['SKILL', 'TOOL']);
/** Claim kinds that name a qualification somebody else awarded. */
const QUALIFICATION_TYPES = ['CERTIFICATION', 'EDUCATION'];
/** Roles shown before the rest go behind a disclosure. */
const ROLE_PREVIEW = 3;
/** Lines shown inside one role before the rest go behind a disclosure. */
const LINE_PREVIEW = 4;
/** Skill chips shown on Overview, which is a glance rather than the list. */
const SKILL_GLANCE = 12;

/**
 * WHERE A SECTION SITS INSIDE PREFERENCES.
 *
 * `answers` is what she decided and reads first. `reading` is what the system
 * then does with those decisions -- where she can work, what rules a job out,
 * the shape of the work, pay -- and it is a CONSEQUENCE rather than a
 * setting, so it sits behind a disclosure. `phrases` is the matching
 * machinery, edited under Settings, and the longest thing on the page: 1,437
 * pixels of chips in front of somebody who came to check what salary she had
 * asked for.
 *
 * A section id this list has never heard of lands in `reading`. A new section
 * on the wrong fold is a layout bug; one that no fold claimed would be data
 * quietly disappearing.
 */
const SECTION_FOLD = {
  about: 'answers',
  place: 'reading',
  blockers: 'reading',
  shape: 'reading',
  pay: 'reading',
};

function foldOf(section) {
  if (SECTION_FOLD[section.id]) return SECTION_FOLD[section.id];
  if (String(section.id || '').startsWith('signals-')) return 'phrases';
  return 'reading';
}

/**
 * Render the profile payload into `mount`.
 *
 * `data` is `GET /api/profile` verbatim. Every section it carries has content
 * -- the server drops empty ones -- so there is no "nothing here" branch per
 * section, only for a payload with no sections at all.
 */
/**
 * Render the profile into `mount`.
 *
 * `data` is `GET /api/profile` verbatim and `ledger` is `GET /api/evidence`,
 * or null. Null is a legitimate state rather than a failure: the profile is
 * readable on a machine whose evidence has not loaded, and the three
 * candidate tabs simply do not appear.
 */
export function renderProfile(mount, data, ledger = null, { experience = null, tab = null } = {}) {
  clear(mount);

  // NO PROVENANCE ABOVE THE ANSWERS. Two sentences used to open this page:
  // which YAML file the answers were read from, and that there is no candidate
  // profile on this machine "which is the expected state". Both are true and
  // neither is addressed to the person reading. One names a file she does not
  // have, in a directory she has never opened, and tells her to run a terminal
  // command; the other reassures her about the absence of a thing she was
  // never told existed. A page called "Your career profile" opens on her
  // answers.
  //
  // What survives is the DISAGREEMENT notice, which is not provenance: two
  // files describing one person and saying different things is a fact about
  // HER, it changes which jobs she is shown, and nothing on screen would
  // otherwise say so.
  const preamble = [];
  const candidate = data.candidate_profile || {};
  if (candidate.present && (data.divergences || []).length) {
    preamble.push(divergenceNotice(
      data.divergences,
      keyed(data.ownership_rule_key, data.ownership_rule),
      candidate,
    ));
  }

  const panels = { overview: [], experience: [], skills: [], preferences: [] };

  // -- what she has confirmed about herself ------------------------------
  const confirmed = ((ledger && ledger.claims) || []).filter((claim) => claim.verified);
  const roles = roleGroups(confirmed);
  const skills = confirmed.filter((claim) => SKILL_TYPES.has(claim.claim_type));

  // Overview is drawn whenever there is a ledger at all, INCLUDING an empty
  // one: "you have not confirmed anything yet, here is where to start" is the
  // most useful thing this page can say on a fresh machine, and an absent tab
  // says nothing at all.
  if (ledger) panels.overview.push(...overviewPanel(roles, skills, confirmed));
  // THE CANONICAL EXPERIENCES, when the page was handed them: the career as
  // `career_experience` holds it, editable in place (experience.js). The
  // claim-grouped panel below is the fallback for a caller that has none.
  if (experience) panels.experience.push(experience);
  else if (roles.length) panels.experience.push(experiencePanel(roles));
  if (skills.length || qualifications(confirmed).length) {
    panels.skills.push(skillsPanel(skills, confirmed));
  }

  // -- and what she is looking for ---------------------------------------
  const folds = { answers: [], reading: [], phrases: [] };
  if ((data.editable || []).length) {
    folds.answers.push(editableBlock(data.editable, data.source, data.place_names));
  }
  for (const section of data.sections || []) {
    folds[foldOf(section)].push(sectionBlock(section));
  }
  panels.preferences.push(...preferencesPanel(folds));

  const filled = PROFILE_TABS.filter((tab) => panels[tab.key].length);
  if (!filled.length && !preamble.length) {
    mount.textContent = t('profile.nothingConfigured');
    return;
  }

  // One tab is not a tab. With a single filled panel the row is pointless
  // chrome above the only thing there is, so it is not drawn.
  if (filled.length < 2) {
    replace(mount, [...preamble, ...filled.flatMap((tab) => panels[tab.key])]);
    return;
  }

  const bodies = new Map();
  const buttons = [];
  const body = el('div', { className: 'profile__panel' });

  function show(key) {
    for (const entry of buttons) {
      entry.node.setAttribute('aria-pressed', String(entry.key === key));
    }
    replace(body, bodies.get(key));
  }

  for (const tab of filled) {
    bodies.set(tab.key, panels[tab.key]);
    const node = button(t(tab.labelKey), () => show(tab.key), {
      className: 'profiletab',
    });
    node.setAttribute('aria-pressed', 'false');
    buttons.push({ key: tab.key, node });
  }

  const row = el('div', {
    className: 'profiletabs',
    attrs: { role: 'group', 'aria-label': t('profile.tabsLabel') },
  }, buttons.map((entry) => entry.node));

  replace(mount, [...preamble, row, body]);
  show(tab && bodies.has(tab) ? tab : filled[0].key);
  return { show };
}

// =========================================================================
// WHAT YOU HAVE DONE
//
// Read from the evidence ledger and never written to it. Everything here is
// a fact somebody personally confirmed; the product invents no proficiency
// figure, no completeness percentage and no ranking of one skill over
// another, because it holds none of those and a bar with a number in it
// would be this page making one up.
// =========================================================================

/** The confirmed qualifications, in the order the vocabulary lists them. */
function qualifications(confirmed) {
  return QUALIFICATION_TYPES.flatMap(
    (type) => confirmed.filter((claim) => claim.claim_type === type),
  );
}

/**
 * The work, grouped the way a CV is: one employer, one period, its lines.
 *
 * Two hundred and thirty-seven confirmed employment claims are not two
 * hundred and thirty-seven jobs. They are the sentences under about eight
 * roles, and a flat list of them is the database view this page exists to
 * stop being.
 *
 * The key is employer AND period, not employer alone: two spells at one
 * company are two entries, and merging them would invent a continuous tenure
 * nobody claimed.
 */
function roleGroups(confirmed) {
  const groups = new Map();
  for (const claim of confirmed) {
    if (!HISTORY_TYPES.has(claim.claim_type)) continue;
    const key = [claim.employer || '', claim.period_start || '', claim.period_end || ''].join('|');
    let group = groups.get(key);
    if (!group) {
      group = {
        employer: claim.employer || '',
        start: claim.period_start || '',
        end: claim.period_end || '',
        lines: [],
        sources: new Set(),
      };
      groups.set(key, group);
    }
    group.lines.push(claim);
    if (claim.source) group.sources.add(claim.source);
  }
  // NEWEST FIRST, and everything undated after everything dated. A document
  // that gave no dates is not a job from 1970, which is where an empty string
  // sorts if you let it.
  return [...groups.values()].sort((a, b) => {
    if (Boolean(a.start) !== Boolean(b.start)) return a.start ? -1 : 1;
    if (a.start !== b.start) return a.start < b.start ? 1 : -1;
    return a.employer.localeCompare(b.employer);
  });
}

/** The dates as the DOCUMENT gave them, or the absence of them, in words. */
function periodText(group) {
  if (group.start && group.end) {
    return t('profile.periodRange', { start: group.start, end: group.end });
  }
  if (group.start) return t('profile.periodFrom', { start: group.start });
  if (group.end) return t('profile.periodUntil', { end: group.end });
  return t('profile.periodUnknown');
}

/** Where these lines came from, translated. Never a filename. */
function provenance(sources) {
  const names = [...sources].map((source) => t(`ledger.source.${source}`));
  return names.length ? names.join(', ') : '';
}

/** One count, on the tinted card the home screen already uses for a figure. */
function countCard(key, value, labelKey) {
  return el('li', { className: `metric metric--${key}` }, [
    el('span', { className: 'metric__value num', text: String(value) }),
    el('span', { className: 'metric__label', text: t(labelKey) }),
  ]);
}

/**
 * YOU, AT A GLANCE.
 *
 * Counts, the most recent role, and the skills as words. No completeness
 * percentage: the design shows one, and this product does not know what a
 * complete profile would be, so the figure would be a fraction with an
 * invented denominator. Counts of real things say more and claim less.
 */
function overviewPanel(roles, skills, confirmed) {
  const quals = qualifications(confirmed);
  const work = confirmed.filter((claim) => HISTORY_TYPES.has(claim.claim_type));
  const out = [];

  if (!confirmed.length) {
    // The honest empty state, and a way out of it. A page that simply had no
    // Overview would leave somebody wondering where their career went.
    out.push(el('section', { className: 'card card--static card--empty profile__blank' }, [
      el('h3', { className: 'profile__heading', text: t('profile.nothingConfirmed') }),
      el('p', { className: 'profile__lead', text: t('profile.nothingConfirmedLead') }),
      linkToEvidence(),
    ]));
    return out;
  }

  // WHAT THE LEDGER HOLDS, NOT WHAT IT IMPLIES. The first version of this
  // counted the employer-and-period GROUPS and called them "Roles", which
  // read 25 over a career of four companies: her CV and her LinkedIn export
  // name the same employer differently -- Globalfy and Globalfy LLC, Teem and
  // Teem LLC -- and seven groups name no employer at all. Nothing here merges
  // two names into one company, so nothing here may count as though it had.
  // A confirmed statement is a thing that exists and can be counted.
  const counts = el('ul', { className: 'profile__metrics' }, [
    work.length ? countCard('work', work.length, 'profile.countWork') : null,
    skills.length ? countCard('skills', skills.length, 'profile.countSkills') : null,
    quals.length ? countCard('quals', quals.length, 'profile.countQuals') : null,
  ].filter(Boolean));

  out.push(el('section', { className: 'card card--static profile__glance' }, [
    el('h3', { className: 'profile__heading', text: t('profile.glanceHeading') }),
    el('p', { className: 'profile__lead', text: t('profile.glanceLead') }),
    counts,
  ]));

  if (roles.length) {
    out.push(el('section', { className: 'card card--static profile__glance' }, [
      el('h3', { className: 'profile__heading', text: t('profile.recentWork') }),
      roleBody(roles[0], { lines: 2, more: false }),
    ]));
  }

  if (skills.length) {
    const shown = skills.slice(0, SKILL_GLANCE);
    out.push(el('section', { className: 'card card--static profile__glance' }, [
      el('h3', { className: 'profile__heading', text: t('profile.yourSkills') }),
      el('div', { className: 'evchips profile__chips' }, shown.map(skillChip)),
      skills.length > shown.length
        ? el('p', {
          className: 'profile__lead',
          text: t('profile.andMoreSkills', { n: skills.length - shown.length }),
        })
        : null,
    ].filter(Boolean)));
  }

  out.push(el('section', { className: 'card card--static card--flat profile__glance' }, [
    el('p', { className: 'profile__lead', text: t('profile.confirmedNote') }),
    linkToEvidence(),
  ]));
  return out;
}

/** The one route off these three tabs, and it goes where editing lives. */
function linkToEvidence() {
  return button(t('profile.openEvidence'), () => {
    const nav = document.querySelector('.topnav__link[data-page="evidence"]');
    if (nav) nav.click();
  }, { className: 'btn btn--primary' });
}

/** A confirmed skill, as a word. Its provenance is the title, not a badge. */
function skillChip(claim) {
  return el('span', {
    className: 'evchip evchip--static',
    text: claim.text,
    attrs: { title: t(`ledger.source.${claim.source}`) },
  });
}

/** The lines under one role, with the rest behind a disclosure. */
function roleBody(group, { lines = LINE_PREVIEW, more = true } = {}) {
  const item = (claim) => el('li', { className: 'role__line', text: claim.text });
  const head = el('div', { className: 'role__head' }, [
    el('h4', {
      className: 'role__employer',
      text: group.employer || t('profile.employerNotStated'),
    }),
    el('span', { className: 'badge role__period', text: periodText(group) }),
  ]);
  const body = [head];
  const from = provenance(group.sources);
  if (from) body.push(el('p', { className: 'role__from', text: from }));
  body.push(el('ul', { className: 'role__lines' }, group.lines.slice(0, lines).map(item)));
  if (more && group.lines.length > lines) {
    body.push(el('details', { className: 'fold fold--quiet' }, [
      el('summary', {
        className: 'fold__summary',
        text: t('profile.showAllLines', { n: group.lines.length }),
      }),
      el('ul', { className: 'role__lines' }, group.lines.slice(lines).map(item)),
    ]));
  }
  return el('div', { className: 'role__body' }, body);
}

/** Every role, newest first, with the older ones folded away. */
function experiencePanel(roles) {
  const card = (group) => el('article', { className: 'card card--static role' }, [roleBody(group)]);
  const shown = roles.slice(0, ROLE_PREVIEW);
  const rest = roles.slice(ROLE_PREVIEW);
  return el('section', { className: 'profile__stack' }, [
    el('p', { className: 'profile__lead', text: t('profile.experienceLead') }),
    // SAID OUT LOUD, BECAUSE IT IS VISIBLE. A CV and a LinkedIn export name
    // the same employer differently often enough that two cards for one
    // company is the normal case, and a reader who is not told why will read
    // it as the product being broken. Deciding that two names are one company
    // is a judgement about her own history; the place to make it is the
    // ledger, where a claim can be edited.
    el('p', { className: 'profile__lead profile__aside', text: t('profile.experienceNote') }),
    ...shown.map(card),
    rest.length
      ? el('details', { className: 'fold' }, [
        el('summary', {
          className: 'fold__summary',
          text: t('profile.showOlderRoles', { n: rest.length }),
        }),
        el('div', { className: 'profile__stack' }, rest.map(card)),
      ])
      : null,
  ].filter(Boolean));
}

/**
 * Skills as words, and qualifications under them.
 *
 * The design puts a "skill map" here: a bar per skill with a number on the
 * end, React 95, TypeScript 90. This product holds no such number. There is
 * no proficiency column in `verified_claim`, nothing measures one, and a bar
 * filled to an invented percentage on somebody's own career page is the
 * clearest possible case of manufacturing fit. What it does hold is WHERE
 * each skill came from, which is on the chip.
 */
function skillsPanel(skills, confirmed) {
  const quals = qualifications(confirmed);
  const out = [];
  if (skills.length) {
    out.push(el('section', { className: 'card card--static' }, [
      el('h3', { className: 'profile__heading', text: t('profile.skillsHeading') }),
      el('p', { className: 'profile__lead', text: t('profile.skillsLead') }),
      el('div', { className: 'evchips profile__chips' }, skills.map(skillChip)),
    ]));
  }
  if (quals.length) {
    out.push(el('section', { className: 'card card--static' }, [
      el('h3', { className: 'profile__heading', text: t('profile.qualsHeading') }),
      el('ul', { className: 'role__lines' }, quals.map((claim) => el('li', {
        className: 'role__line',
      }, [
        el('span', { text: claim.text }),
        el('span', { className: 'role__from', text: ` ${t(`ledger.source.${claim.source}`)}` }),
      ]))),
    ]));
  }
  return el('section', { className: 'profile__stack' }, out);
}

// =========================================================================
// AND WHAT YOU ARE LOOKING FOR
// =========================================================================

/**
 * One tab, three depths.
 *
 * Her answers are open, because they are the reason anybody comes here. What
 * the system does with them is a consequence and folds away. The phrase
 * groups fold away too, and they are the reason the fold exists: 1,437 pixels
 * of chips that used to sit between somebody and the salary she came to check.
 */
function preferencesPanel(folds) {
  const fold = (key, contents) => (contents.length
    ? el('details', { className: 'fold' }, [
      el('summary', { className: 'fold__summary', text: t(`profileFold.${key}`) }),
      el('div', { className: 'profile__stack' }, contents),
    ])
    : null);
  return [
    ...folds.answers,
    fold('reading', folds.reading),
    fold('phrases', folds.phrases),
  ].filter(Boolean);
}

/**
 * A label the SERVER sent, in the reader's language.
 *
 * The payload carries both: `label_key` is the semantic name and `label` is
 * the English the server composed, which is what a caller that is not this
 * browser gets. The key wins where the catalogue knows it, and the English is
 * the fallback for a row this build has never heard of -- better than a bare
 * identifier, and visible as untranslated rather than as missing.
 *
 * A row with NO key is deliberate: a signal's label and a blocker's label are
 * what the person's own configuration says, and a word the configuration
 * chose is the product working rather than a string to translate.
 */
function keyed(key, fallback) {
  if (!key) return fallback || '';
  const translated = t(key);
  return translated === key ? (fallback || '') : translated;
}

function labelOf(row) {
  return keyed(row.label_key, row.label);
}


/**
 * Two files describing one person, disagreeing.
 *
 * Both values, both filenames, and no winner. Picking one would be the
 * ownership migration, which is a decision somebody makes rather than
 * something a screen does quietly while nobody is looking.
 */
function divergenceNotice(divergences, rule, candidate) {
  return el('div', { className: 'profile__conflict' }, [
    el('p', {
      className: 'profile__conflict-lead',
      // Two keys, not one sentence with a count spliced into it. "one thing"
      // and "{n} things" inflect differently in every language this speaks,
      // and a translator handed the fragment cannot see the sentence.
      text: divergences.length === 1
        ? t('profile.disagreeOne')
        : t('profile.disagreeMany', { n: divergences.length }),
    }),
    el('ul', { className: 'profile__conflict-list' }, divergences.map((row) => el('li', {}, [
      el('strong', { text: `${row.subject}: ` }),
      el('span', {
        text: t('profile.saysValue', { path: row.profile_path, value: row.profile_value }) + '; ',
      }),
      el('span', {
        text: t('profile.saysValue', { path: row.config_path, value: row.config_value }) + '.',
      }),
    ]))),
    el('p', {
      className: 'profile__conflict-note',
      text: `${keyed(candidate.note_key, candidate.note)} ${rule || ''}`.trim(),
    }),
  ]);
}

function sectionBlock(section) {
  const rows = section.rows.map((row) => el('div', { className: 'profile__row' }, [
    el('span', { className: 'profile__row-label', text: labelOf(row) }),
    el('span', { className: 'profile__row-value', text: valueOf(row) }),
  ]));

  return el('section', {
    className: 'profile__section',
    // The id, so the three sections the design gives a ground of their own
    // can take it without a second list of names living in the stylesheet.
    attrs: { 'data-section': section.id || '' },
  }, [
    el('h3', { className: 'profile__heading', text: keyed(section.label_key, section.label) }),
    el('p', { className: 'profile__lead', text: keyed(section.lead_key, section.lead) }),
    el('div', { className: 'profile__rows' }, rows),
    // An editable section points at the editor that already exists. A
    // read-only one used to name the FILE it is held in -- `Held in
    // search.local.yaml.`, four times on one tab -- which is the same
    // provenance the top of this page stopped printing, and no more use to
    // her at the bottom of a section than it was at the top of the screen.
    section.editable
      ? el('p', { className: 'profile__where', text: t('profile.editUnder') })
      : null,
  ].filter(Boolean));
}

/**
 * One row's value, with the count when the value was truncated.
 *
 * The server sends the first eight phrases and the real total. Printing eight
 * of twenty-nine without saying so would understate somebody's own search back
 * to them.
 */
function valueOf(row) {
  const value = row.value || '';
  if (!row.count || row.count <= 8) return value;
  // `profile.andMore` is a SUFFIX -- ' ... and {count} more' -- and existed
  // before this call site did. Passing it a `{value}` it never names would
  // have rendered the placeholder to the screen.
  return value + t('profile.andMore', { count: row.count - 8 });
}


/**
 * The one form on this screen.
 *
 * Nothing is sent until Save, and Save sends only what CHANGED. A panel that
 * posted all nine fields every time would bump `config_version` and invalidate
 * every score in the corpus because somebody opened a disclosure and closed it
 * again.
 */
/**
 * THE QUESTIONS, IN THREE GROUPS.
 *
 * Twelve controls in one column is a form, and a form is what somebody fills
 * in once and never opens again. These are answers to three different
 * questions about a working life, and grouping them is what lets a reader
 * come here to change one thing and find it.
 *
 *   where  where and how you work, and who is allowed to hire you
 *   what   level and contract: what the job IS
 *   pay    the amount and the currency, together, because a number without
 *          its currency is the same defect as a percentage without its unit
 *
 * A field this list has never heard of goes in `where`, which is the first
 * group: an unplaced question must still be askable. The order inside a group
 * is the order the server sent, so the server stays in charge of sequence and
 * this only decides company.
 */
const CHOICE_GROUPS = [
  { key: 'where', fields: ['work_models', 'candidate_country',
    'eligible_countries', 'eligible_scopes'] },
  { key: 'what', fields: ['contract_preferred', 'contract_unwanted',
    'seniority_preferred', 'seniority_excluded', 'travel_max_pct'] },
  { key: 'pay', fields: ['compensation_target'] },
];

/**
 * FIELDS THAT ARE NOT ASKED, because the answer is already known.
 *
 * `require_remote` was a checkbox reading "Only show me fully remote roles",
 * directly under the question that asks about ways of working. It asked the
 * same decision twice and let the two answers disagree. The SERVER derives it
 * now -- true exactly when hybrid and on-site are both never to be shown --
 * so the setup and this screen cannot work it out differently.
 */
const DERIVED_FIELDS = new Set(['require_remote']);

/**
 * QUESTIONS DRAWN AS ONE CONTROL, one answer per row (`choices.js`), the same
 * control the guided setup draws. The first field of each is where it sits;
 * the others are written by it and never drawn on their own.
 */
const COMPOSITES = {
  work_models: { fields: WORK_MODEL_FIELDS, draw: workModelMatrix },
  contract_preferred: { fields: ARRANGEMENT_FIELDS, draw: arrangementMatrix },
};
const ABSORBED = new Set(Object.values(COMPOSITES)
  .flatMap((composite) => composite.fields.slice(1)));

/** Answers whose value decides what another question is allowed to offer. */
const REDRAWS_THE_FORM = new Set([
  'seniority_preferred',  // decides what may be excluded
  'seniority_excluded',
]);

/**
 * Which questions share a line, because they are one decision.
 *
 * An amount and its currency are not two questions. They were two, several
 * inches apart, one of them a three-character text box labelled "In which
 * currency" -- so the screen asked "how much" and then, separately, "of
 * what".
 */
const PAIRED = { compensation_target: 'compensation_currency' };

/**
 * Which answers may not contradict each other.
 *
 * A level cannot be both what she is looking for and what she never wants to
 * see. The form used to draw two identical rows of seven chips and let both
 * be ticked; the gates then had to decide which one won, which is a decision
 * the person should have made.
 */
const EXCLUSIVE = {
  seniority_excluded: 'seniority_preferred',
  seniority_preferred: 'seniority_excluded',
};

/** One of the composite questions, with the same label and help as the rest. */
function compositeControl(row, composite, byField, change, currentValue, prefix) {
  const rows = composite.fields.map((field) => byField.get(field));
  if (rows.some((member) => !member)) return null;
  const values = Object.fromEntries(rows.map((member) => [member.field, currentValue(member)]));
  const id = prefix + row.field;
  const help = helpFor(row);
  if (help) help.id = `${id}-help`;
  return el('div', { className: 'choice choice--matrix' }, [
    el('p', { className: 'choice__q field__label', attrs: { id: `${id}-q` }, text: labelOf(row) }),
    help,
    composite.draw({
      id,
      describedBy: help ? `${id}-q ${id}-help` : `${id}-q`, // localisation-check: allow ids
      values,
      onChange: (next) => {
        for (const member of rows) {
          // An empty answer to a list the file never held is not a change.
          // An empty answer to a list the file never held, or the same values
          // in another order, is not a change: saving it would bump the
          // configuration version and ask for a rescore over nothing.
          const value = next[member.field];
          const stored = Array.isArray(member.value) ? member.value : [];
          const same = value.length === stored.length && value.every((item) => stored.includes(item));
          change(member, same ? member.value : value);
        }
      },
    }),
  ].filter(Boolean));
}

function groupedControls(fields, change, currentValue, names, prefix) {
  const placed = new Set(CHOICE_GROUPS.flatMap((group) => group.fields));
  const byField = new Map(fields.map((row) => [row.field, row]));
  const out = [];
  for (const group of CHOICE_GROUPS) {
    const rows = fields.filter((row) => !DERIVED_FIELDS.has(row.field)
      && !ABSORBED.has(row.field)
      && (group.fields.includes(row.field)
        || (group.key === 'where' && !placed.has(row.field)
          && !Object.values(PAIRED).includes(row.field))));
    if (!rows.length) continue;
    out.push(el('section', { className: 'choicegroup' }, [
      el('h4', { className: 'choicegroup__head', text: t(`choiceGroup.${group.key}`) }),
      el('p', { className: 'choicegroup__lede', text: t(`choiceGroup.${group.key}Lede`) }),
      el('div', { className: 'choicegroup__body' },
        rows.map((row) => (COMPOSITES[row.field]
          ? compositeControl(row, COMPOSITES[row.field], byField, change, currentValue, prefix)
          : control(row, change, currentValue, {
            names,
            prefix,
            partner: PAIRED[row.field] ? byField.get(PAIRED[row.field]) : null,
            against: EXCLUSIVE[row.field]
              ? currentValue(byField.get(EXCLUSIVE[row.field]))
              : null,
          }))).filter(Boolean)),
    ]));
  }
  return out;
}

export function editableBlock(fields, source, names, prefix = 'profile-field-') {
  const pending = new Map();
  const status = el('p', { className: 'profile__save-status', attrs: { role: 'status' } });
  const save = button(t('action.save'), () => commit(), { className: 'btn btn--primary' });
  const cancel = button(t('action.cancel'), () => reset(), { className: 'btn' });
  const body = el('div', { className: 'profile__form' });

  function currentValue(row) {
    return pending.has(row.field) ? pending.get(row.field) : row.value;
  }

  function mark() {
    // Both controls are meaningless with nothing pending, and a Save that does
    // nothing is a Save that makes a person doubt the last one worked.
    save.disabled = pending.size === 0;
    cancel.disabled = pending.size === 0;
    save.textContent = pending.size === 0
      ? t('action.save')
      : (pending.size === 1 ? t('profile.saveOne') : t('profile.saveMany', { n: pending.size }));
  }

  function draw() {
    replace(body, groupedControls(fields, change, currentValue, names, prefix));
    mark();
  }

  function reset() {
    pending.clear();
    status.textContent = '';
    status.className = 'profile__save-status';
    draw();
  }

  function change(row, value) {
    // A value back at its original is not a change. Without this, choosing
    // REMOTE and then choosing the original again leaves a pending edit that
    // bumps the configuration version for nothing.
    if (JSON.stringify(value) === JSON.stringify(row.value)) pending.delete(row.field);
    else pending.set(row.field, value);

    // SOME ANSWERS CHANGE WHAT ANOTHER QUESTION MAY OFFER, and the screen has
    // to follow. Accepting only one working arrangement removes the "which
    // would you prefer less" question entirely; ticking a level as wanted has
    // to take it out of the levels she never wants to see. Redrawing is the
    // honest response -- the alternative is a chip you can press that the
    // save will then contradict.
    if (REDRAWS_THE_FORM.has(row.field)) {
      draw();
      return;
    }
    mark();
  }

  async function commit() {
    if (!pending.size) return;
    save.disabled = true;
    cancel.disabled = true;
    status.className = 'profile__save-status';
    status.textContent = t('profile.saving');
    try {
      const result = await patchProfile(Object.fromEntries(pending));
      for (const row of fields) {
        if (pending.has(row.field)) row.value = pending.get(row.field);
      }
      pending.clear();
      status.className = 'profile__save-status is-ok';
      // The server's sentence, not ours. It is the one that says the existing
      // matches now need recalculating, and rewriting it here would be this
      // file deciding how important that is.
      status.textContent = result.note || t('prefs.saved');
      draw();
    } catch (error) {
      status.className = 'profile__save-status is-error';
      // Also verbatim. "Where you live is a two-letter country code, such as
      // BR" names the box; a generic message does not.
      status.textContent = (error && error.message) || t('profile.notSaved');
      mark();
    }
  }

  draw();
  return el('section', { className: 'profile__section profile__section--edit' }, [
    el('h3', { className: 'profile__heading', text: t('profile.yourChoices') }),
    el('p', {
      className: 'profile__lead',
      text: source && source.is_local
        ? t('profile.editLeadLocal')
        : t('profile.editLeadFirst'),
    }),
    body,
    el('div', { className: 'profile__actions' }, [save, cancel, status]),
  ]);
}

/** One control, chosen by the KIND the server declared. */
/**
 * The word for a share of working time spent travelling.
 *
 * The boundaries are read as "up to": 10 is occasional, 11 begins some. They
 * are a READING of the number and never replace it -- the stored value is
 * whatever the slider says, and this only ever names the neighbourhood.
 */
function travelBand(pct) {
  if (pct <= 0) return t('travel.none');
  if (pct <= 10) return t('travel.occasional');
  if (pct <= 25) return t('travel.some');
  if (pct <= 50) return t('travel.frequent');
  return t('travel.heavy');
}

/**
 * The one sentence under a question saying what the answer is USED FOR.
 *
 * Returns null when the catalogue has nothing for that field, so a question
 * added later renders without help rather than with the key printed under it.
 */
function helpFor(row) {
  const key = `fieldHelp.${row.field}`;
  const text = t(key);
  if (!text || text === key) return null;
  return el('p', { className: 'choice__help', text });
}

/**
 * The three currency segments, drawn wherever the pay control puts them.
 *
 * A value outside the three is kept and shown rather than dropped: this
 * program does not hold a list of which currencies are real, for the same
 * reason it does not hold a list of which passports count.
 */
function currencySegments(row, change, currentValue) {
  const value = currentValue(row);
  const known = ['BRL', 'USD', 'EUR'];
  const options = known.includes(value) ? known : [...known, value].filter(Boolean);
  const buttons = options.map((code) => {
    const node = button(code, () => {
      change(row, code);
      for (const other of buttons) {
        other.setAttribute('aria-pressed', String(other.textContent === code));
      }
    }, { className: 'segbtn num' });
    node.setAttribute('aria-pressed', String(code === value));
    return node;
  });
  return el('div', {
    className: 'segmented segmented--inline',
    // Labelled, because on the pay row it has no visible question of its own.
    attrs: { role: 'group', 'aria-label': t('fieldHelp.compensation_currency') },
  }, buttons);
}

/**
 * A stored code, shown as the word for it.
 *
 * `BR` is what belongs in the file and `Brazil` is what belongs on the
 * screen. The names come from the gazetteer by way of `/api/profile`, so
 * there is no second list here to drift from it; a code the gazetteer does
 * not name falls back to itself, which is honest and was the whole state of
 * this screen before.
 */
function namedChoice(code, names) {
  const all = names || {};
  return (all.countries && all.countries[code])
    || (all.regions && all.regions[code])
    || choiceLabel(code);
}

/**
 * A country field: type the name, store the code.
 *
 * `<datalist>` rather than a bespoke combobox. It is one element, the browser
 * gives it search and keyboard navigation for free, and it degrades to a
 * plain text box rather than to nothing. What it replaces is a seven-
 * character box with `maxlength=2` and the instruction "Two letters each" --
 * which asked somebody to know the ISO code for the country they live in.
 */
/** A typed country name or code, back to a code this product stores. */
function codeForCountry(text, names) {
  const countries = (names && names.countries) || {};
  const needle = String(text || '').trim().toLowerCase();
  if (!needle) return '';
  for (const [code, label] of Object.entries(countries)) {
    if (label.toLowerCase() === needle) return code;
  }
  const upper = needle.toUpperCase();
  return countries[upper] ? upper : '';
}

function countryPicker({ id, value, names, placeholder, onPick }) {
  const countries = (names && names.countries) || {};
  const listId = `${id}-countries`;
  const options = Object.entries(countries)
    .sort((a, b) => a[1].localeCompare(b[1]))
    .map(([code, label]) => el('option', { attrs: { value: label, 'data-code': code } }));
  const list = el('datalist', { attrs: { id: listId } }, options);

  const box = el('input', {
    className: 'input choice__country',
    attrs: {
      type: 'text',
      id,
      list: listId,
      autocomplete: 'off',
      spellcheck: 'false',
      placeholder: placeholder || '',
    },
    props: { value: value ? namedChoice(value, names) : '' },
  });

  box.addEventListener('change', () => {
    const code = codeForCountry(box.value, names);
    if (!code) {
      // Nothing recognised. The box goes back to what was stored rather than
      // silently keeping a word that means nothing to the product.
      box.value = value ? namedChoice(value, names) : '';
      return;
    }
    box.value = namedChoice(code, names);
    onPick(code);
  });

  return { box, list };
}

function control(row, change, currentValue, options = {}) {
  const { names = {}, partner = null, against = null, prefix = 'profile-field-' } = options;
  const id = prefix + row.field;
  const value = currentValue(row);

  if (row.kind === 'bool') {
    const box = el('input', {
      // `checkbox` is the design's control -- an 18px square with a 2px line
      // and a mint fill when it is on. Without it the browser draws its own,
      // which is R5 of the gap analysis and the one thing on this page that
      // still looked like a form from 2004.
      className: 'checkbox profile__check',
      attrs: { type: 'checkbox', id },
      props: { checked: Boolean(value) },
      on: { change: (event) => change(row, event.target.checked) },
    });
    return el('div', { className: 'choice choice--bool' }, [
      el('div', { className: 'choice__boolrow' }, [
        box,
        el('label', { className: 'choice__q field__label', text: labelOf(row), attrs: { for: id } }),
      ]),
      helpFor(row),
    ].filter(Boolean));
  }

  // CHIPS THAT TOGGLE, which is what the design draws and what these answers
  // actually are: a handful of short values, several of which can be true.
  //
  // They were checkboxes, and seven of them in a column for "what level are
  // you looking for" read as a form to fill in rather than as a set to pick
  // from. Not a multi-select either: that needs a modifier key nobody
  // discovers, and silently drops every other choice when somebody clicks one
  // option without it.
  //
  // `aria-pressed` on a button rather than a checkbox because that is what a
  // chip is -- a control with an on state -- and the group carries the
  // question as its accessible name, so a screen reader hears the question
  // once and then each value.
  if (row.kind === 'enum_list') {
    const chosen = new Set(Array.isArray(value) ? value : []);
    // The values this answer must not collide with. A level cannot be both
    // what she is looking for and what she never wants to see, and the form
    // used to draw two identical rows of seven and let both be ticked.
    const blocked = new Set(Array.isArray(against) ? against : []);

    // A SECOND ASK THAT ONLY EXISTS WHEN IT HAS SOMETHING TO ASK ABOUT.
    //
    // "Any of those you would prefer less?" offered all three arrangements,
    // including ones she had not accepted -- so the screen could hold "I do
    // not accept Employer of Record" and "I prefer Employer of Record less"
    // at once, which is not a preference anybody has. It now offers only what
    // she accepted, and disappears entirely below two.
    const offer = row.choices.filter((choice) => !blocked.has(choice) || chosen.has(choice));

    const chips = offer.map((choice) => {
      // A STABLE ID PER CHOICE. These were checkboxes with
      // `profile-field-<field>-<choice>` ids, and a chip is the same control
      // wearing a different shape -- so it keeps the same handle, for the
      // browser suite and for anything else that has to point at one answer.
      const node = button(namedChoice(choice, names), () => {
        if (chosen.has(choice)) chosen.delete(choice);
        else chosen.add(choice);
        node.setAttribute('aria-pressed', String(chosen.has(choice)));
        change(row, row.choices.filter((option) => chosen.has(option)));
      }, { className: 'choicechip' });
      node.id = `${id}-${choice}`;
      node.setAttribute('aria-pressed', String(chosen.has(choice)));
      return node;
    });
    return el('div', { className: 'choice' }, [
      el('p', {
        className: 'choice__q field__label',
        attrs: { id: id + '-q' },
        text: labelOf(row),
      }),
      helpFor(row),
      el('div', {
        className: 'choicechips',
        attrs: { role: 'group', 'aria-labelledby': id + '-q' },
      }, chips),
    ].filter(Boolean));
  }

  // A PERCENTAGE WITH A WORD BESIDE IT.
  //
  // This rendered as a bare number input reading `15`, under the label "Most
  // travel you would accept". Fifteen what: days, trips, hours, per month,
  // per year? The stored field is `travel.max_tolerated_pct` -- a share of
  // working time -- and nothing on screen said so.
  //
  // A slider rather than the named bands the brief sketched, and the reason
  // is her own value. Bands of 0/10/25/50 cannot represent 15, so choosing
  // one would silently rewrite a preference she set. The slider keeps every
  // value the field can hold and the readout supplies the meaning the bands
  // were for: the number, the unit, and the word for that part of the range.
  if (row.kind === 'percent') {
    const readout = el('p', { className: 'choice__readout' });
    const paint = (pct) => {
      readout.textContent = t('travel.readout', { pct, band: travelBand(pct) });
    };
    const slider = el('input', {
      className: 'choice__slider',
      attrs: { type: 'range', id, min: '0', max: '100', step: '5' },
      props: { value: value === null || value === undefined ? 0 : value },
      on: {
        input: (event) => paint(Number.parseInt(event.target.value, 10) || 0),
        change: (event) => change(row, Number.parseInt(event.target.value, 10) || 0),
      },
    });
    paint(value === null || value === undefined ? 0 : value);
    return el('div', { className: 'choice' }, [
      el('label', { className: 'choice__q field__label', text: labelOf(row), attrs: { for: id } }),
      helpFor(row),
      slider,
      readout,
    ].filter(Boolean));
  }

  // AN AMOUNT AND ITS CURRENCY ARE ONE DECISION, so they are one control.
  // They were two questions several inches apart, the second of them a
  // three-character text box labelled "In which currency" -- so the screen
  // asked "how much", and then separately, "of what".
  if (row.kind === 'money') {
    const numeric = el('input', {
      className: 'input choice__amount num',
      attrs: { type: 'number', id, min: '0', step: '100', inputmode: 'numeric' },
      props: { value: value === null || value === undefined ? 0 : value },
      on: {
        change: (event) => {
          const parsed = Number.parseInt(event.target.value, 10);
          change(row, Number.isNaN(parsed) ? 0 : parsed);
        },
      },
    });
    return el('div', { className: 'choice' }, [
      el('label', { className: 'choice__q field__label', text: labelOf(row), attrs: { for: id } }),
      helpFor(row),
      el('div', { className: 'choice__money' }, [
        numeric,
        partner ? currencySegments(partner, change, currentValue) : null,
      ].filter(Boolean)),
    ].filter(Boolean));
  }

  // THE CURRENCY, as segments. It was a three-character text box beside a
  // label reading "In which currency" -- a field you could type `XYZ` into.
  // Three buttons say what the answers are, and a value outside them is kept
  // and shown rather than dropped, because this program does not hold a list
  // of which currencies are real.
  // A currency reached on its own is one the pay control did not claim.
  if (row.kind === 'currency') return currencySegments(row, change, currentValue);

  if (row.kind === 'country_list') {
    // AN OPEN LIST, one value at a time. Not checkboxes and not a dropdown,
    // because the vocabulary is every country there is and this program
    // refuses to hold a list of which ones are real -- having one would be
    // this file having an opinion about somebody's passport.
    //
    // It was a comma-separated line, which works right up until somebody
    // looks at it: no feedback that three values were understood as three,
    // no way to remove the middle one without re-typing the line, and no
    // signal at all when a stray character makes one of them something the
    // server will refuse. A pill is that feedback.
    // Chips that read `Brazil`, storing `BR`. The hint used to say "Two
    // letters each, one at a time", which is the shape of the stored value
    // described to somebody who should never have to know it.
    const tags = tagInput({
      id,
      label: labelOf(row),
      values: Array.isArray(value) ? value : [],
      placeholder: t('profile.countryPlaceholder'),
      // A name back to its code. Anything unrecognised is dropped rather than
      // stored as itself: a two-letter typo would otherwise become a country.
      normalise: (part) => codeForCountry(part, names),
      display: (code) => namedChoice(code, names),
      onChange: (values) => change(row, values),
      hint: '',
    });
    return el('div', { className: 'choice' }, [
      el('p', { className: 'choice__q field__label', text: labelOf(row) }),
      helpFor(row),
      tags.root,
    ].filter(Boolean));
  }

  // WHERE SOMEBODY IS BASED. A two-letter code, upper-cased on the way out so
  // the server never refuses a value only because of how it was typed.
  //
  // It stays a text box and does not become a dropdown, for the reason the
  // country LIST already gives: the vocabulary is every country there is, and
  // holding a list of which ones count would be this program having an
  // opinion about somebody's passport. What it gains is the question above it
  // and the sentence under it -- this is the single most consequential answer
  // on the page, because it decides which employers can hire her at all, and
  // it used to render as a 7-character box labelled "Where you live".
  const picker = countryPicker({
    id,
    value,
    names,
    placeholder: t('profile.countryPlaceholder'),
    onPick: (picked) => change(row, picked),
  });
  return el('div', { className: 'choice' }, [
    el('label', { className: 'choice__q field__label', text: labelOf(row), attrs: { for: id } }),
    helpFor(row),
    picker.box,
    picker.list,
  ].filter(Boolean));
}

/** A stored value's label, or the value itself when nothing is translated. */
function choiceLabel(value) {
  const translated = tVocab(value);
  return translated === null ? value.replace(/_/g, ' ').toLowerCase() : translated;
}
