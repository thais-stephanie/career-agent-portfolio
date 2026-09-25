/**
 * setup.js -- the guided first run: one decision per card, in order.
 *
 * WHY IT EXISTS
 * -------------
 * A fresh install used to open on six numbered steps, four zeroed counters, a
 * second list of the same gaps and two empty sections -- every question the
 * product has, on one screen, before a single job. So this asks them one at a
 * time, says why each one matters, and saves each answer the moment Continue
 * is pressed. Leaving halfway keeps what was answered; coming back -- after a
 * reload, or a restart -- opens the card that was open. Back shows what was
 * answered. Nothing is a gate: every question can be skipped, and "Do this
 * later" leaves the whole flow.
 *
 * ONLY QUESTIONS WHOSE ANSWER CHANGES SOMETHING
 * --------------------------------------------
 * docs/ONBOARDING.md is the audit: every card here changes Search Fit, the
 * eligibility gate, which jobs Discover shows or what is collected. Career
 * stage, preferred levels and travel are stored but read by nothing, so they
 * are not asked.
 *
 * NOTHING HERE INVENTS AN ANSWER
 * ------------------------------
 * Every control starts from what the configuration holds. Where someone lives
 * is never copied into "countries that can hire you": that is asked as its own
 * question, and eligibility stays unknown until they answer it. Preferring
 * remote work is never read as "can be hired anywhere".
 *
 * NOTHING HERE IS A NEW WRITE PATH
 * --------------------------------
 * Answers go through the routes Settings uses -- `PATCH /api/profile`,
 * `POST /api/first-search`, `POST /api/cv/import` -- so their validation and
 * their whitelist apply unchanged, and the two screens write the same
 * configuration. Ways of working and arrangements are drawn by `choices.js`,
 * the same module the Career Profile draws them with. The last card starts
 * finding jobs through the app's one collection watcher.
 */

import { roleAnchorsEditor } from './roles.js';
import { el, button, replace } from './dom.js';
import { t, tVocab, getLocale } from './i18n.js';
import * as api from './api.js';
import { createProgressView, outcomeText } from './collection.js';
import { phraseProblem } from './format.js';
import {
  arrangementMatrix, arrangementSummary, workModelMatrix, workModelSummary,
  ARRANGEMENT_FIELDS, WORK_MODEL_FIELDS,
} from './choices.js';

/** Remembered in the browser only: "I chose to do this later". */
const LATER_KEY = 'careerAgent.setup.later.v1';
/** Remembered in the browser only: the card that was open, to come back to. */
const POSITION_KEY = 'careerAgent.setup.at.v1';

export function setupPostponed() {
  try {
    return window.localStorage.getItem(LATER_KEY) === '1';
  } catch {
    return false;
  }
}

function rememberPostponed(value) {
  try {
    if (value) window.localStorage.setItem(LATER_KEY, '1');
    else window.localStorage.removeItem(LATER_KEY);
  } catch {
    // Private windows may refuse storage. The flow still works; it simply
    // offers itself again next time.
  }
}

/**
 * The card to reopen, when the setup was left open by a reload, a closed tab
 * or a restart rather than by "Do this later". Null when there is none.
 */
export function setupResumeStep() {
  try {
    const key = window.localStorage.getItem(POSITION_KEY);
    return STEPS.some((step) => step.key === key) ? key : null;
  } catch {
    return null;
  }
}

function rememberPosition(key) {
  try {
    if (key) window.localStorage.setItem(POSITION_KEY, key);
    else window.localStorage.removeItem(POSITION_KEY);
  } catch {
    // Without storage a reload starts again from Home; nothing answered is lost.
  }
}

/**
 * The cards, in order. `optional` cards offer Skip. `when` hides a card whose
 * question has nothing to ask yet: hiring regions are asked only about regions
 * that contain where you live, so without a country there is nothing to ask.
 */
const STEPS = [
  { key: 'welcome' },
  { key: 'work', optional: true },
  { key: 'roles', optional: true },
  { key: 'home', optional: true },
  { key: 'hire', optional: true },
  { key: 'regions', optional: true, when: (setup) => setup.homeRegions().length > 0 },
  { key: 'workmodel', optional: true },
  { key: 'arrangement', optional: true },
  { key: 'level', optional: true },
  { key: 'pay', optional: true },
  { key: 'cv', optional: true },
  { key: 'review' },
  { key: 'ready' },
];

/** Not counted in "Question n of m": the two ends and the summary. */
const NOT_QUESTIONS = new Set(['welcome', 'review', 'ready']);

/** ISO 3166-1 alpha-2, named in the reader's language by `Intl.DisplayNames`. */
const COUNTRY_CODES = (
  'AD AE AF AG AI AL AM AO AQ AR AS AT AU AW AX AZ BA BB BD BE BF BG BH BI BJ BL BM BN BO BQ '
  + 'BR BS BT BV BW BY BZ CA CC CD CF CG CH CI CK CL CM CN CO CR CU CV CW CX CY CZ DE DJ DK DM '
  + 'DO DZ EC EE EG EH ER ES ET FI FJ FK FM FO FR GA GB GD GE GF GG GH GI GL GM GN GP GQ GR GS '
  + 'GT GU GW GY HK HM HN HR HT HU ID IE IL IM IN IO IQ IR IS IT JE JM JO JP KE KG KH KI KM KN '
  + 'KP KR KW KY KZ LA LB LC LI LK LR LS LT LU LV LY MA MC MD ME MF MG MH MK ML MM MN MO MP MQ '
  + 'MR MS MT MU MV MW MX MY MZ NA NC NE NF NG NI NL NO NP NR NU NZ OM PA PE PF PG PH PK PL PM '
  + 'PN PR PS PT PW PY QA RE RO RS RU RW SA SB SC SD SE SG SH SI SJ SK SL SM SN SO SR SS ST SV '
  + 'SX SY SZ TC TD TF TG TH TJ TK TL TM TN TO TR TT TV TW TZ UA UG UM US UY UZ VA VC VE VG VI '
  + 'VN VU WF WS XK YE YT ZA ZM ZW'
).split(' ');

/** Currencies offered first; any code already saved is kept even if absent here. */
const CURRENCIES = [
  'USD', 'EUR', 'GBP', 'BRL', 'CAD', 'AUD', 'MXN', 'ARS', 'CLP', 'COP', 'PEN', 'UYU',
  'INR', 'JPY', 'CHF', 'SEK', 'NOK', 'DKK', 'PLN', 'ZAR', 'NZD', 'SGD',
];

const LEVELS = ['INTERN', 'JUNIOR', 'MID', 'SENIOR', 'STAFF', 'PRINCIPAL', 'LEAD'];

/** What a CV picker accepts: the four formats `cv/extract.py` reads. */
const CV_ACCEPT = '.pdf,.docx,.txt,.md';

function displayNames(type) {
  try {
    return new Intl.DisplayNames([getLocale()], { type });
  } catch {
    return null;
  }
}

function countryName(code) {
  const names = displayNames('region');
  try {
    return (names && names.of(code)) || code;
  } catch {
    return code;
  }
}

function currencyName(code) {
  const names = displayNames('currency');
  try {
    const name = names && names.of(code);
    return name && name !== code ? `${name} (${code})` : code;
  } catch {
    return code;
  }
}

function sortedCountries() {
  const collator = new Intl.Collator(getLocale());
  return COUNTRY_CODES
    .map((code) => ({ code, name: countryName(code) }))
    .sort((a, b) => collator.compare(a.name, b.name));
}

/** A typed country name, in the reader's language, or a two-letter code. */
function countryCode(text) {
  const needle = String(text || '').trim();
  if (!needle) return '';
  const folded = needle.toLocaleLowerCase(getLocale());
  for (const code of COUNTRY_CODES) {
    if (countryName(code).toLocaleLowerCase(getLocale()) === folded) return code;
  }
  const upper = needle.toUpperCase();
  return COUNTRY_CODES.includes(upper) ? upper : '';
}

function vocab(value) {
  return tVocab(value) || value;
}

function sameList(a, b) {
  const left = Array.isArray(a) ? a : [];
  const right = Array.isArray(b) ? b : [];
  return left.length === right.length && left.every((item) => right.includes(item));
}

export function createSetup({ onExit = null, onGoTo = null, collection = null } = {}) {
  const root = el('section', {
    className: 'setup',
    attrs: { 'aria-labelledby': 'setup-title' },
  });
  let at = 'welcome';
  let fields = new Map();
  let firstRun = null;
  //: Roles in mind, for the review. Optional; null when unreadable.
  let roleAnchors = null;
  //: Set when a card was opened from the review with "Change": Continue (and
  //: Back) return to the review rather than walking the rest of the flow.
  let returnTo = null;
  //: Unsaved input, per card, so Back, Continue and a language switch never
  //: lose what was typed.
  const drafts = {};
  //: The last card's subscription to the app's one collection watcher. The
  //: card only draws what the watcher says; it never asks the server itself.
  let watching = null;

  async function open(stepKey = null) {
    stopPolling();
    replace(root, [el('p', { className: 'setup__loading', text: t('app.loading') })]);
    try {
      const [profile, state, roles] = await Promise.all([
        api.getProfile(), api.getFirstRun(), api.getRoleAnchors().catch(() => null),
      ]);
      fields = new Map((profile.editable || []).map((row) => [row.field, row]));
      firstRun = state;
      roleAnchors = roles;
    } catch (error) {
      replace(root, [
        el('p', { className: 'state__msg', text: error.userMessage || error.message }),
        button(t('action.retry'), () => open(stepKey), { className: 'btn' }),
      ]);
      return;
    }
    returnTo = null;
    // Opened on purpose, so it is no longer "later": a reload from here on
    // comes back to the card that was open.
    rememberPostponed(false);
    const wanted = stepKey && visibleSteps().some((step) => step.key === stepKey) ? stepKey : null;
    at = wanted || 'welcome';
    draw({ focus: Boolean(stepKey) });
  }

  function value(field) {
    const row = fields.get(field);
    return row ? row.value : null;
  }

  function values(names) {
    return Object.fromEntries(names.map((name) => [name, value(name)]));
  }

  function stepState(key) {
    return (firstRun && (firstRun.steps || []).find((step) => step.key === key)) || {};
  }

  /**
   * Hiring regions worth asking about: they contain where she lives and none
   * of the countries she already confirmed. A region holding a confirmed
   * country admits by itself (`gates._region_verdict`), so asking about it
   * would be a question whose answer changes nothing. From the server.
   */
  function homeRegions() {
    return stepState('where').regions || [];
  }

  /** Every hiring region containing where she lives, for showing answers back. */
  function regionsAroundHome() {
    return stepState('where').home_regions || [];
  }

  function visibleSteps() {
    const self = { homeRegions };
    return STEPS.filter((step) => !step.when || step.when(self));
  }

  function currentIndex() {
    const steps = visibleSteps();
    const index = steps.findIndex((step) => step.key === at);
    if (index >= 0) return index;
    // A card with nothing to ask right now: the next one that has something.
    const order = STEPS.findIndex((step) => step.key === at);
    const next = steps.findIndex((step) => STEPS.indexOf(step) > order);
    return next >= 0 ? next : 0;
  }

  // ===================================================================
  // drawing
  // ===================================================================

  function draw({ focus = true } = {}) {
    // The last card's subscription belongs to its nodes; drawing any card
    // replaces them, and the ready card subscribes again.
    stopPolling();
    const steps = visibleSteps();
    const step = steps[currentIndex()];
    at = step.key;
    // Remembered so a reload or a restart comes back here -- except the last
    // card: setup is finished there, and reopening it on every visit would be
    // onboarding that never ends.
    rememberPosition(at === 'ready' ? null : at);
    const body = BODIES[step.key]();
    const questions = steps.filter((item) => !NOT_QUESTIONS.has(item.key));
    const question = questions.findIndex((item) => item.key === step.key);
    const error = el('p', {
      className: 'setup__error',
      attrs: { id: 'setup-error', role: 'alert' },
      text: '',
    });
    const form = el('form', {
      className: 'setup__card',
      attrs: { novalidate: 'novalidate', 'aria-describedby': 'setup-why' },
      dataset: { step: step.key },
      on: {
        submit: (event) => {
          event.preventDefault();
          if (body.submit) body.submit(error);
        },
      },
    }, [
      // "STEP 3", NEVER "3 OF 9". The regions card appears only when it can
      // add something, so the number of questions is not known in advance,
      // and a total that grows from 8 to 9 mid-way reads as broken. The bar
      // says how far along this is; the words say which step it is.
      question >= 0
        ? el('div', { className: 'setup__progress' }, [
          el('p', {
            className: 'setup__count',
            attrs: { 'aria-hidden': 'true' },
            text: t('setup.progress', { n: question + 1 }),
          }),
          el('div', {
            className: 'setup__bar setup__stepbar',
            attrs: {
              role: 'progressbar',
              'aria-valuemin': '0',
              'aria-valuemax': '100',
              'aria-valuenow': String(Math.round(((question + 1) / questions.length) * 100)),
              'aria-valuetext': t('setup.progress', { n: question + 1 }),
              'aria-label': t('setup.progressLabel'),
            },
          }, [el('span', {
            className: 'setup__barfill',
            style: { width: `${Math.round(((question + 1) / questions.length) * 100)}%` },
          })]),
        ])
        : null,
      el('h2', {
        className: 'setup__title',
        attrs: { id: 'setup-title', tabindex: '-1' },
        text: t(`setup.${step.key}.title`, body.titleParams || null),
      }),
      el('p', {
        className: 'setup__why',
        attrs: { id: 'setup-why' },
        text: t(`setup.${step.key}.why`),
      }),
      ...body.nodes,
      error,
      el('div', { className: 'setup__actions' }, body.actions || defaultActions(step)),
    ].filter(Boolean));

    replace(root, [
      form,
      step.key !== 'ready'
        ? button(t('setup.later'), () => leave(), {
          className: 'btn btn--link setup__later',
          attrs: { id: 'setup-later' },
        })
        : null,
    ].filter(Boolean));

    if (focus) {
      const heading = root.querySelector('#setup-title');
      if (heading) heading.focus();
    }
  }

  function backButton() {
    return button(t('setup.back'), () => back(), { className: 'btn', attrs: { id: 'setup-back' } });
  }

  function defaultActions(step) {
    return [
      currentIndex() > 0 ? backButton() : null,
      step.optional
        ? button(t('setup.skip'), () => advance(), { className: 'btn btn--quiet', attrs: { id: 'setup-skip' } })
        : null,
      el('button', {
        className: 'btn btn--primary',
        attrs: { type: 'submit', id: 'setup-next' },
        text: returnTo ? t('setup.saveAndReturn') : t('setup.continue'),
      }),
    ].filter(Boolean);
  }

  /** Open one card by name. */
  function show(key) {
    at = key;
    draw();
  }

  /** Forward: the next card, or back to the review a "Change" came from. */
  function advance() {
    if (returnTo) {
      const target = returnTo;
      returnTo = null;
      show(target);
      return;
    }
    const steps = visibleSteps();
    show(steps[Math.min(steps.length - 1, currentIndex() + 1)].key);
  }

  function back() {
    if (returnTo) {
      const target = returnTo;
      returnTo = null;
      show(target);
      return;
    }
    const steps = visibleSteps();
    show(steps[Math.max(0, currentIndex() - 1)].key);
  }

  /** "Change" on the review: that card, and then back to where it was pressed. */
  function change(key) {
    returnTo = at;
    show(key);
  }

  function leave() {
    stopPolling();
    returnTo = null;
    rememberPosition(null);
    rememberPostponed(true);
    if (onExit) onExit();
  }

  /** Save through the existing whitelist, then advance. Errors stay beside the card. */
  async function save(changes, error, { refresh = false } = {}) {
    if (!Object.keys(changes).length) {
      advance();
      return;
    }
    const submit = root.querySelector('#setup-next');
    const label = submit ? submit.textContent : '';
    if (submit) {
      submit.disabled = true;
      submit.textContent = t('setup.saving');
    }
    error.textContent = '';
    try {
      await api.patchProfile(changes);
      for (const [field, next] of Object.entries(changes)) {
        const row = fields.get(field);
        if (row) row.value = next;
      }
      // The server derives and resolves some answers from others (only
      // remote, overlaps between lists, which regions contain a country), so
      // after those the screen reads back what was actually saved.
      if (refresh) {
        const [profile, state] = await Promise.all([api.getProfile(), api.getFirstRun()]);
        fields = new Map((profile.editable || []).map((row) => [row.field, row]));
        firstRun = state;
      }
      advance();
    } catch (failure) {
      error.textContent = failure.userMessage || failure.message;
      if (submit) {
        submit.disabled = false;
        submit.textContent = label;
      }
    }
  }

  function describedBy(input) {
    input.setAttribute('aria-describedby', 'setup-why setup-error');
    return input;
  }

  /**
   * Say what is wrong, beside the field, and take it back the moment it is
   * right. `isValid` is asked on every input and change; an error clears only
   * when the field it belongs to becomes valid -- typing another wrong value
   * keeps it, and an error about a different field is left alone.
   */
  function invalid(input, error, text, isValid = null) {
    error.textContent = text;
    error.dataset.for = input.id;
    input.setAttribute('aria-invalid', 'true');
    input.focus();
    if (!isValid) return;
    const recheck = () => {
      if (!isValid()) return;
      input.removeAttribute('aria-invalid');
      if (error.dataset.for === input.id) {
        error.textContent = '';
        delete error.dataset.for;
      }
      input.removeEventListener('input', recheck);
      input.removeEventListener('change', recheck);
    };
    input.addEventListener('input', recheck);
    input.addEventListener('change', recheck);
  }

  const lines = (text) => text.split('\n').map((line) => line.trim()).filter(Boolean);

  function label(forId, text) {
    return el('label', { className: 'setup__label', attrs: { for: forId }, text });
  }

  function hint(text) {
    return el('p', { className: 'setup__hint', text });
  }

  /**
   * A country, typed by name in the reader's language or picked from the
   * browser's own suggestion list. `<datalist>`, as the Career Profile does:
   * one element, searchable, keyboard-driven, and a plain text box if the
   * browser offers nothing more. The code is what is stored; the name is
   * what is shown.
   */
  function countryInput({ id, code = '', onInput = null, describe = true }) {
    const listId = `${id}-list`;
    const box = el('input', {
      className: 'input setup__country',
      attrs: {
        id,
        type: 'text',
        list: listId,
        autocomplete: 'off',
        spellcheck: 'false',
        placeholder: t('setup.home.placeholder'),
      },
      props: { value: code ? countryName(code) : '' },
      dataset: { code: code || '' },
      on: {
        input: () => { if (onInput) onInput(box.value); },
        change: () => {
          const found = countryCode(box.value);
          box.dataset.code = found;
          if (found) {
            box.value = countryName(found);
            box.removeAttribute('aria-invalid');
          }
          if (onInput) onInput(box.value);
        },
      },
    });
    if (describe) describedBy(box);
    const list = el('datalist', { attrs: { id: listId } },
      sortedCountries().map(({ name }) => el('option', { attrs: { value: name } })));
    return { box, list };
  }

  // ===================================================================
  // the cards
  // ===================================================================

  const BODIES = {
    welcome: () => ({
      nodes: [
        el('ul', { className: 'setup__list' }, [
          el('li', { text: t('setup.welcome.point1') }),
          el('li', { text: t('setup.welcome.point2') }),
          el('li', { text: t('setup.welcome.point3') }),
        ]),
        hint(t('setup.welcome.privacy')),
      ],
      actions: [
        el('button', {
          className: 'btn btn--primary',
          attrs: { type: 'submit', id: 'setup-next' },
          text: t('setup.welcome.start'),
        }),
      ],
      submit: () => advance(),
    }),

    work: () => {
      const described = stepState('work');
      if (described.done) {
        // ALREADY ANSWERED, and shown back in the words that were typed. They
        // are changed in Settings, where the phrases can be edited one by one
        // with the number of jobs each one reaches beside it.
        const roles = described.roles || [];
        const skills = described.skills || [];
        return {
          nodes: [
            roles.length
              ? el('div', { className: 'setup__saved' }, [
                el('p', { className: 'setup__label', text: t('setup.work.savedRoles') }),
                el('ul', { className: 'setup__chips setup__chips--static' },
                  roles.map((role) => el('li', { className: 'setup__chip', text: role }))),
              ])
              : el('p', { className: 'setup__done', text: t('setup.work.already', { n: described.phrases || 0 }) }),
            skills.length
              ? el('div', { className: 'setup__saved' }, [
                el('p', { className: 'setup__label', text: t('setup.work.savedSkills') }),
                el('ul', { className: 'setup__chips setup__chips--static' },
                  skills.map((skill) => el('li', { className: 'setup__chip', text: skill }))),
              ])
              : null,
            hint(t('setup.work.changeInSettings')),
          ].filter(Boolean),
          submit: () => advance(),
        };
      }
      const draft = drafts.work || { work: '', skills: '' };
      drafts.work = draft;
      const work = describedBy(el('textarea', {
        className: 'input setup__textarea',
        attrs: { id: 'setup-work', rows: '4', maxlength: '2000' },
        props: { value: draft.work },
        on: { input: (event) => { draft.work = event.target.value; } },
      }));
      const skills = el('textarea', {
        className: 'input setup__textarea',
        attrs: { id: 'setup-skills', rows: '3', maxlength: '2000' },
        props: { value: draft.skills },
        on: { input: (event) => { draft.skills = event.target.value; } },
      });
      return {
        nodes: [
          // Background and search intent are different things, and this is
          // where a new person first meets the difference.
          hint(t('setup.work.intent')),
          label('setup-work', t('setup.work.label')),
          work,
          hint(t('setup.work.example')),
          label('setup-skills', t('setup.work.skillsLabel')),
          skills,
          hint(t('setup.work.note')),
        ],
        submit: async (error) => {
          const roles = lines(draft.work);
          if (!roles.length) {
            invalid(work, error, t('setup.work.required'),
              () => lines(work.value).length > 0 && !phraseProblem(lines(work.value)));
            return;
          }
          const problem = [[roles, work], [lines(draft.skills), skills]]
            .map(([lines, box]) => [phraseProblem(lines), box])
            .find(([text]) => text);
          if (problem) {
            const [text, box] = problem;
            box.setAttribute('aria-describedby', 'setup-why setup-error');
            invalid(box, error, text, () => !phraseProblem(lines(box.value)));
            return;
          }
          const submit = root.querySelector('#setup-next');
          const before = submit.textContent;
          submit.disabled = true;
          submit.textContent = t('setup.saving');
          try {
            await api.createFirstSearch({ role_examples: roles, skills: lines(draft.skills) });
            firstRun = await api.getFirstRun();
            delete drafts.work;
            advance();
          } catch (failure) {
            error.textContent = failure.userMessage || failure.message;
            submit.disabled = false;
            submit.textContent = before;
          }
        },
      };
    },

    roles: () => {
      // Optional search anchors, never limits. One editor, shared with Settings.
      const editor = roleAnchorsEditor({ id: 'setup-roles' });
      return {
        nodes: [editor.root],
        submit: async (error) => {
          if (!editor.dirty) {
            advance();
            return;
          }
          const submit = root.querySelector('#setup-next');
          if (submit) submit.disabled = true;
          const ok = await editor.save();
          if (ok) roleAnchors = await api.getRoleAnchors().catch(() => roleAnchors);
          if (submit) submit.disabled = false;
          if (ok) advance();
          else error.textContent = t('roles.notSaved');
        },
      };
    },

    home: () => {
      const saved = value('candidate_country') || '';
      // Held as the code when the text names a country, so a language switch
      // redraws "Brasil" as "Brazil" rather than as a word this locale does
      // not recognise.
      const draft = drafts.home || { text: '', code: saved };
      const { box, list } = countryInput({
        id: 'setup-country',
        code: draft.code,
        onInput: (text) => { drafts.home = { text, code: countryCode(text) }; },
      });
      box.value = draft.code ? countryName(draft.code) : draft.text;
      return {
        nodes: [label('setup-country', t('setup.home.label')), box, list, hint(t('setup.home.note'))],
        submit: (error) => {
          const typed = box.value.trim();
          const code = countryCode(typed);
          if (typed && !code) {
            // Valid again when it names a country, or when it is emptied:
            // leaving where you live unanswered is allowed.
            invalid(box, error, t('setup.home.unknown'),
              () => !box.value.trim() || Boolean(countryCode(box.value)));
            return;
          }
          delete drafts.home;
          if (!code || code === saved) {
            advance();
            return;
          }
          // A new country: the regions that contain it are different, so the
          // screen reads them back before the next card asks about them.
          save({ candidate_country: code }, error, { refresh: true });
        },
      };
    },

    hire: () => {
      const home = value('candidate_country') || '';
      const saved = Array.isArray(value('eligible_countries')) ? value('eligible_countries') : [];
      // The draft answers a question ABOUT ONE COUNTRY. When where-you-live
      // changes, the old "Yes" was about somewhere else: start again from what
      // is saved, or a Continue would record an eligibility nobody gave.
      const draft = drafts.hire && drafts.hire.home === home ? drafts.hire : {
        home,
        homeAnswer: home ? (saved.includes(home) ? 'yes' : '') : '',
        others: saved.filter((code) => code !== home),
      };
      drafts.hire = draft;
      const nodes = [];
      if (home) {
        const homeName = countryName(home);
        const answers = [
          ['yes', t('setup.hire.yes', { country: homeName })],
          ['unsure', t('setup.hire.unsure')],
        ];
        nodes.push(el('fieldset', { className: 'setup__options' }, [
          el('legend', { className: 'setup__legend', text: t('setup.hire.homeQuestion', { country: homeName }) }),
          ...answers.map(([answer, text]) => {
            const id = `setup-hire-${answer}`;
            return el('label', { className: 'setup__option', attrs: { for: id } }, [
              el('input', {
                className: 'setup__radio',
                attrs: { type: 'radio', name: 'setup-hire', id, value: answer },
                props: { checked: draft.homeAnswer === answer },
                on: { change: () => { draft.homeAnswer = answer; } },
              }),
              el('span', { text }),
            ]);
          }),
        ]));
      }
      const chips = el('ul', { className: 'setup__chips', attrs: { 'aria-live': 'polite' } });
      function paint() {
        replace(chips, draft.others.map((code) => el('li', { className: 'setup__chip' }, [
          el('span', { text: countryName(code) }),
          button('×', () => {
            draft.others = draft.others.filter((item) => item !== code);
            paint();
          }, {
            className: 'setup__chipx',
            ariaLabel: t('setup.hire.remove', { country: countryName(code) }),
          }),
        ])));
      }
      paint();
      const picker = countryInput({ id: 'setup-hire-other', describe: false });
      const add = button(t('setup.hire.add'), () => {
        const code = countryCode(picker.box.value);
        if (code && code !== home && !draft.others.includes(code)) draft.others.push(code);
        picker.box.value = '';
        picker.box.dataset.code = '';
        paint();
        picker.box.focus();
      }, { className: 'btn', attrs: { id: 'setup-hire-add' } });
      // Enter in the country box adds it, rather than submitting the card with
      // a country typed and not yet added.
      picker.box.addEventListener('keydown', (event) => {
        if (event.key === 'Enter') {
          event.preventDefault();
          add.click();
        }
      });
      nodes.push(
        label('setup-hire-other', home ? t('setup.hire.othersLabel') : t('setup.hire.countriesLabel')),
        el('div', { className: 'setup__row' }, [picker.box, add]),
        picker.list,
        chips,
        hint(t('setup.hire.note')),
      );
      return {
        nodes,
        submit: (error) => {
          const next = [...(draft.homeAnswer === 'yes' && home ? [home] : []), ...draft.others];
          if (sameList(next, saved)) {
            advance();
            return;
          }
          // Read back afterwards: which regions are worth asking about
          // depends on the countries just confirmed.
          save({ eligible_countries: next }, error, { refresh: true });
        },
      };
    },

    // Only the regions that CONTAIN where she lives: a scope admits a posting
    // only through her residence (`gates._region_verdict`), so any other region
    // would be a box that does nothing.
    regions: () => multiChoice({
      field: 'eligible_scopes',
      choices: homeRegions(),
      labelOf: (code) => t(`setup.region.${code}`),
      legend: t('setup.regions.legend', { country: countryName(value('candidate_country') || '') }),
      note: t('setup.regions.note'),
      allowEmpty: false,
    }),

    workmodel: () => {
      const saved = values(WORK_MODEL_FIELDS);
      const draft = drafts.workmodel || { ...saved };
      drafts.workmodel = draft;
      return {
        nodes: [
          workModelMatrix({
            id: 'setup-workmodel',
            legend: t('setup.workmodel.legend'),
            describedBy: 'setup-why',
            values: draft,
            onChange: (next) => { Object.assign(draft, next); },
          }),
          hint(t('setup.workmodel.note')),
        ],
        submit: (error) => {
          const changes = {};
          for (const field of WORK_MODEL_FIELDS) {
            if (!sameList(draft[field], saved[field])) changes[field] = draft[field];
          }
          delete drafts.workmodel;
          save(changes, error, { refresh: true });
        },
      };
    },

    arrangement: () => {
      const saved = values(ARRANGEMENT_FIELDS);
      const draft = drafts.arrangement || { ...saved };
      drafts.arrangement = draft;
      return {
        nodes: [
          arrangementMatrix({
            id: 'setup-arrangement',
            legend: t('setup.arrangement.legend'),
            describedBy: 'setup-why',
            values: draft,
            onChange: (next) => { Object.assign(draft, next); },
          }),
          hint(t('setup.arrangement.note')),
        ],
        submit: (error) => {
          const changes = {};
          for (const field of ARRANGEMENT_FIELDS) {
            if (!sameList(draft[field], saved[field])) changes[field] = draft[field];
          }
          delete drafts.arrangement;
          save(changes, error, { refresh: true });
        },
      };
    },

    // LEVELS TO KEEP OFF THE LIST, not "levels you prefer". The preferred list
    // is stored but nothing ranks or filters by it; the excluded list hides
    // those postings from Discover, with a notice that shows them again.
    level: () => multiChoice({
      field: 'seniority_excluded',
      choices: LEVELS,
      labelOf: vocab,
      legend: t('setup.level.legend'),
      note: t('setup.level.note'),
      allowEmpty: true,
    }),

    pay: () => {
      const savedAmount = value('compensation_target');
      const savedCurrency = value('compensation_currency') || '';
      const draft = drafts.pay || {
        amount: savedAmount ? String(savedAmount) : '',
        currency: savedCurrency,
      };
      drafts.pay = draft;
      const amount = describedBy(el('input', {
        className: 'input input--num setup__amount',
        attrs: { id: 'setup-pay', type: 'number', min: '1', step: '1', inputmode: 'numeric' },
        props: { value: draft.amount },
        on: { input: (event) => { draft.amount = event.target.value; } },
      }));
      const codes = CURRENCIES.includes(draft.currency) || !draft.currency
        ? CURRENCIES
        : [draft.currency, ...CURRENCIES];
      const currency = el('select', {
        className: 'select setup__select',
        attrs: { id: 'setup-currency' },
        on: { change: (event) => { draft.currency = event.target.value; } },
      }, [
        el('option', { text: t('setup.pay.chooseCurrency'), attrs: { value: '' } }),
        ...codes.map((code) => el('option', {
          text: currencyName(code),
          attrs: { value: code, ...(code === draft.currency ? { selected: 'selected' } : {}) },
        })),
      ]);
      return {
        nodes: [
          el('div', { className: 'setup__row setup__row--pay' }, [
            el('div', { className: 'setup__field' }, [label('setup-pay', t('setup.pay.label')), amount]),
            el('div', { className: 'setup__field' }, [label('setup-currency', t('setup.pay.currency')), currency]),
          ]),
          hint(t('setup.pay.note')),
        ],
        submit: (error) => {
          const raw = draft.amount.trim();
          if (!raw) {
            delete drafts.pay;
            advance();
            return;
          }
          const number = Number(raw);
          if (!Number.isFinite(number) || number <= 0) {
            invalid(amount, error, t('setup.pay.invalid'), () => {
              const value = amount.value.trim();
              return !value || (Number.isFinite(Number(value)) && Number(value) > 0);
            });
            return;
          }
          if (!draft.currency) {
            invalid(currency, error, t('setup.pay.needCurrency'), () => Boolean(currency.value));
            return;
          }
          const changes = {};
          if (number !== Number(savedAmount)) changes.compensation_target = number;
          if (draft.currency !== savedCurrency) changes.compensation_currency = draft.currency;
          delete drafts.pay;
          save(changes, error);
        },
      };
    },

    cv: () => cvCard(),

    review: () => reviewCard(),

    ready: () => readyCard(),
  };

  /** A set of checkboxes for one enum-list field. */
  function multiChoice({ field, choices, labelOf, legend, note = '', allowEmpty }) {
    const saved = Array.isArray(value(field)) ? value(field) : [];
    const chosen = drafts[field] || new Set(saved.filter((item) => choices.includes(item)));
    drafts[field] = chosen;
    const boxes = choices.map((choice) => {
      const id = `setup-${field}-${choice}`;
      return el('label', { className: 'setup__option', attrs: { for: id } }, [
        el('input', {
          className: 'checkbox',
          attrs: { type: 'checkbox', id, value: choice },
          props: { checked: chosen.has(choice) },
          on: {
            change: (event) => {
              if (event.target.checked) chosen.add(choice);
              else chosen.delete(choice);
            },
          },
        }),
        el('span', { text: labelOf(choice) }),
      ]);
    });
    return {
      nodes: [
        el('fieldset', { className: 'setup__options', attrs: { 'aria-describedby': 'setup-why' } }, [
          el('legend', { className: 'setup__legend', text: legend }),
          ...boxes,
        ]),
        note ? hint(note) : null,
      ].filter(Boolean),
      submit: (error) => {
        // Only what this card offered is its answer. A saved value it does not
        // offer -- a region that does not contain where she now lives -- admits
        // nothing, and is dropped when she answers again.
        const next = choices.filter((choice) => chosen.has(choice));
        delete drafts[field];
        if (sameList(next, saved)) {
          advance();
          return;
        }
        if (!next.length && !allowEmpty) {
          if (!saved.length) {
            advance();
            return;
          }
          error.textContent = t('setup.pickOne');
          drafts[field] = chosen;
          return;
        }
        save({ [field]: next }, error);
      },
    };
  }

  // ===================================================================
  // career data: optional, and never confirmed by being read
  // ===================================================================

  function cvAdded() {
    return Boolean(stepState('documents').done);
  }

  /**
   * Add a CV, or skip. The search works without one; Career Evidence and
   * resume preparation need one. Read through the same route Career Evidence
   * uses (`POST /api/cv/import`): every line becomes a proposal, and nothing
   * is true about her until she confirms it there.
   */
  function cvCard() {
    const status = el('p', {
      className: 'setup__cvstatus',
      attrs: { role: 'status', 'aria-live': 'polite', id: 'setup-cv-status' },
      text: drafts.cvNotice || '',
    });
    const input = el('input', {
      className: 'ev__file',
      attrs: { type: 'file', id: 'setup-cv-file', accept: CV_ACCEPT, 'aria-describedby': 'setup-why' },
    });
    const read = button(t('setup.cv.read'), async () => {
      const file = input.files && input.files[0];
      if (!file) {
        status.textContent = t('setup.cv.chooseFirst');
        return;
      }
      read.disabled = true;
      status.textContent = t('setup.cv.reading', { name: file.name });
      try {
        const bytes = await file.arrayBuffer();
        const result = await api.importCv(file.name, bytes);
        // The server's own count of what it staged. The statements travel
        // grouped by kind; `total` is the number, and counting a list that
        // is not at the top level is how this said 0 after reading 9.
        const found = Number(result.total) || 0;
        drafts.cvNotice = t('setup.cv.found', { n: found });
        firstRun = await api.getFirstRun();
        draw({ focus: false });
        const again = root.querySelector('#setup-next');
        if (again) again.focus();
      } catch (failure) {
        status.textContent = failure.userMessage || failure.message;
        read.disabled = false;
      }
    }, { className: 'btn', attrs: { id: 'setup-cv-read' } });
    const chosenName = el('span', { className: 'setup__hint', attrs: { id: 'setup-cv-name' } });
    input.addEventListener('change', () => {
      const file = input.files && input.files[0];
      chosenName.textContent = file ? file.name : '';
    });
    return {
      nodes: [
        el('p', { className: 'setup__lead', text: t('setup.cv.noNeed') }),
        el('p', { className: 'setup__hint', text: t('setup.cv.helps') }),
        cvAdded()
          ? el('p', { className: 'setup__done', attrs: { id: 'setup-cv-added' }, text: t('setup.cv.added') })
          : null,
        el('div', { className: 'setup__row setup__cvrow' }, [
          input,
          el('label', { className: 'btn ev__filebtn', attrs: { for: 'setup-cv-file' }, text: t('setup.cv.choose') }),
          chosenName,
          read,
        ]),
        hint(t('setup.cv.privacy', { kinds: CV_ACCEPT.split(',').join(' ') })),
        status,
      ].filter(Boolean),
      actions: [
        backButton(),
        el('button', {
          className: cvAdded() ? 'btn btn--primary' : 'btn',
          attrs: { type: 'submit', id: 'setup-next' },
          text: cvAdded() || returnTo ? t('setup.continue') : t('setup.cv.skip'),
        }),
      ],
      submit: () => advance(),
    };
  }

  // ===================================================================
  // the review: every answer, readable, each with Change
  // ===================================================================

  /** "9,000 BRL", in the reader's number format, or null when unanswered. */
  function payTarget() {
    const amount = value('compensation_target');
    if (!amount) return null;
    const figure = Number(amount).toLocaleString(getLocale());
    return [figure, value('compensation_currency') || ''].join(' ').trim();
  }

  function hiringCoverage() {
    const countries = (value('eligible_countries') || []).map(countryName);
    const regions = (value('eligible_scopes') || [])
      .filter((code) => regionsAroundHome().includes(code))
      .map((code) => t(`setup.region.${code}`));
    return [...countries, ...regions].join(', ') || null;
  }

  function reviewRows() {
    const work = stepState('work');
    return [
      ['work', work.done
        ? ((work.roles || []).join(', ') || t('setup.ready.phrases', { n: work.phrases || 0 }))
        : null],
      ['roles', ((roleAnchors && roleAnchors.anchors) || []).map((anchor) => anchor.text).join(', ')
        || t('setup.review.noPreference')],
      ['home', value('candidate_country') ? countryName(value('candidate_country')) : null],
      // Unknown stays unknown, and says so: an empty answer here is not "no".
      ['hire', hiringCoverage() || (value('candidate_country') ? t('setup.review.hireUnknown') : null)],
      ['workmodel', workModelSummary(values(WORK_MODEL_FIELDS)) || t('setup.review.noPreference')],
      ['arrangement', arrangementSummary(values(ARRANGEMENT_FIELDS)) || t('setup.review.noPreference')],
      // Skipping this card hides nothing, which is an answer: "None".
      ['level', (value('seniority_excluded') || []).map(vocab).join(', ') || t('setup.ready.none')],
      ['pay', payTarget()],
      ['cv', cvAdded() ? t('setup.review.cvAdded') : t('setup.review.cvNotAdded')],
    ];
  }

  function reviewCard() {
    return {
      nodes: [
        el('dl', { className: 'setup__summary' }, reviewRows().flatMap(([key, text]) => [
          el('dt', { text: t(`setup.review.${key}`) }),
          el('dd', {}, [
            el('span', {
              className: text ? '' : 'setup__unanswered',
              text: text || t('setup.ready.notAnswered'),
            }),
            button(t('setup.ready.change'), () => change(key), {
              className: 'btn btn--link',
              attrs: { id: `setup-edit-${key}` },
              ariaLabel: t('setup.ready.changeLabel', { what: t(`setup.review.${key}`) }),
            }),
          ]),
        ])),
        hint(t('setup.review.note')),
      ],
      actions: [
        backButton(),
        el('button', {
          className: 'btn btn--primary',
          attrs: { type: 'submit', id: 'setup-next' },
          text: t('setup.review.looksRight'),
        }),
      ],
      submit: () => advance(),
    };
  }

  // ===================================================================
  // the last card: what now, and finding the first jobs
  // ===================================================================

  function readyCard() {
    const status = el('div', {
      className: 'setup__find',
      attrs: { role: 'status', 'aria-live': 'polite' },
    });
    const find = button(t('setup.ready.find'), () => startFinding(), {
      className: 'btn btn--primary',
      attrs: { id: 'setup-find' },
    });
    // A run already going -- started here earlier, on another page, in another
    // tab or before a reload -- is shown, never restarted.
    followRun(status, find);
    // Whether Search Fit can mean anything yet, in words. A person who
    // skipped the work question must not meet a page of low scores and read
    // them as "nothing fits".
    const readiness = el('p', {
      className: 'setup__readiness',
      attrs: { id: 'setup-readiness', role: 'status' },
    });
    api.getSearchFitReadiness().then((result) => {
      const missing = (result.missing || []).map((code) => t(`readiness.missing.${code}`));
      readiness.textContent = t(`readiness.${result.state}`, {
        missing: missing.join(t('readiness.join')),
      });
      readiness.dataset.state = result.state;
    }).catch(() => {});
    return {
      nodes: [
        readiness,
        hint(t('setup.ready.note')),
        // Without career data the search still works; this says what adding
        // it would change, and nothing louder. Resume Tailor is not the next
        // step until there is something for it to work from.
        cvAdded() ? null : el('p', { className: 'setup__hint', text: t('setup.ready.noCv') }),
        status,
      ].filter(Boolean),
      actions: [
        backButton(),
        cvAdded()
          ? null
          : button(t('setup.ready.addCv'), () => change('cv'), {
            className: 'btn', attrs: { id: 'setup-add-cv' },
          }),
        button(t('setup.ready.settings'), () => {
          leave();
          if (onGoTo) onGoTo('settings');
        }, { className: 'btn btn--quiet', attrs: { id: 'setup-settings' } }),
        button(t('setup.ready.toHome'), () => leave(), { className: 'btn btn--quiet', attrs: { id: 'setup-home' } }),
        find,
      ].filter(Boolean),
      submit: () => startFinding(),
    };
  }

  function startFinding() {
    if (collection) collection.start('all');
  }

  /** Draw the run from the app's watcher, for as long as this card is shown. */
  function followRun(status, find) {
    stopPolling();
    if (!collection) return;
    const view = createProgressView(collection, { stopId: 'setup-stop' });
    const keepUsing = hint(t('setup.ready.keepUsing'));
    watching = collection.subscribe((snapshot, reason) => {
      const phase = snapshot.phase;
      if (snapshot.active) {
        find.disabled = true;
        find.textContent = phase === 'starting' ? t('setup.ready.starting') : t('setup.ready.finding');
        view.update(snapshot);
        if (view.root.parentNode !== status) replace(status, [view.root, keepUsing]);
        return;
      }
      if (reason === 'tick') return;
      find.disabled = false;
      if (snapshot.error) {
        find.textContent = t('setup.ready.find');
        replace(status, [el('p', { className: 'setup__error', text: snapshot.error })]);
        return;
      }
      const text = outcomeText(snapshot);
      if (!text) {
        find.textContent = t('setup.ready.find');
        replace(status, []);
        return;
      }
      find.textContent = t('setup.ready.findAgain');
      replace(status, [
        el('p', { className: 'setup__findtext', text }),
        phase === 'failed'
          ? null
          : button(t('setup.ready.see'), () => {
            rememberPostponed(true);
            rememberPosition(null);
            if (onGoTo) onGoTo('jobs');
          }, { className: 'btn btn--primary', attrs: { id: 'setup-see' } }),
      ].filter(Boolean));
    });
    // Ask once on arrival, so a run started elsewhere is on this card now
    // rather than after the next poll. Shared with any request in flight.
    collection.refresh();
  }

  function stopPolling() {
    if (watching) {
      watching();
      watching = null;
    }
  }

  /** Whether the person is on the last card and its run has ended. */
  function finished() {
    if (!collection || at !== 'ready') return false;
    const phase = collection.state().phase;
    return phase === 'finished' || phase === 'cancelled' || phase === 'failed';
  }

  /** Redraw the current card in the reader's language, keeping what was typed. */
  function relabel() {
    if (fields.size) draw({ focus: false });
  }

  /** Whether the last card -- the one that finds jobs -- is on screen. */
  function atReady() {
    return at === 'ready' && fields.size > 0;
  }

  /** She went to another page: not "left open", so a reload goes to Home. */
  function forgetPosition() {
    rememberPosition(null);
  }

  return { root, open, relabel, stop: stopPolling, finished, atReady, forgetPosition };
}
