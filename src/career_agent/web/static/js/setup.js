/**
 * setup.js -- the guided first run: one question per card, in order.
 *
 * WHY IT EXISTS
 * -------------
 * A fresh install used to open on six numbered steps, four zeroed counters, a
 * second list of the same gaps and two empty sections -- every question the
 * product has, on one screen, before a single job. The questions were right
 * and their order was right; showing all of them at once was the problem.
 *
 * So this asks them one at a time, says why each one matters, and saves each
 * answer the moment Continue is pressed. Leaving halfway keeps what was
 * answered. Back shows what was answered. Nothing is a gate: every card except
 * the first and last can be skipped, and "Do this later" leaves the whole flow.
 *
 * NOTHING HERE INVENTS AN ANSWER
 * ------------------------------
 * Every control starts from what the configuration holds. Where someone lives
 * is never copied into "countries that can hire you": that is asked as its own
 * yes-or-no question, because residence is not proof an employer can hire
 * somebody there, and eligibility must stay unknown until they say so.
 *
 * NOTHING HERE IS A NEW WRITE PATH
 * --------------------------------
 * Answers go through the same routes the Career Profile and the first-run list
 * already use -- `PATCH /api/profile`, `POST /api/first-search`,
 * `POST /api/firstrun/stage` -- so their validation and their whitelist apply
 * unchanged. The last card starts `POST /api/sources/refresh-all`, which runs
 * the same collectors as each source's own "Refresh now".
 */

import { el, button, replace } from './dom.js';
import { t, tVocab, getLocale } from './i18n.js';
import * as api from './api.js';

/** Remembered in the browser only: "I chose to do this later". */
const LATER_KEY = 'careerAgent.setup.later.v1';

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

/** The cards, in order. `optional` cards offer Skip. */
const STEPS = [
  { key: 'welcome' },
  { key: 'work', optional: true },
  { key: 'stage', optional: true },
  { key: 'home', optional: true },
  { key: 'hire', optional: true },
  { key: 'regions', optional: true },
  { key: 'level', optional: true },
  { key: 'pay', optional: true },
  { key: 'ready' },
];

/** The questions counted in "Step n of m": everything between the two ends. */
const QUESTIONS = STEPS.filter((step) => step.key !== 'welcome' && step.key !== 'ready');

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

/** The hiring regions the matcher knows, with the words a person would use. */
const REGIONS = ['WORLDWIDE', 'AMERICAS', 'LATAM', 'NORTH_AMERICA', 'EMEA', 'APAC'];

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

function vocab(value) {
  return tVocab(value) || value;
}

export function createSetup({ onExit = null, onGoTo = null } = {}) {
  const root = el('section', {
    className: 'setup',
    attrs: { 'aria-labelledby': 'setup-title' },
  });
  let at = 0;
  let fields = new Map();
  let firstRun = null;
  //: Unsaved input, per card, so Back and Continue never lose what was typed.
  const drafts = {};
  let poll = null;

  async function open(stepKey = null) {
    stopPolling();
    replace(root, [el('p', { className: 'setup__loading', text: t('app.loading') })]);
    try {
      const [profile, state] = await Promise.all([api.getProfile(), api.getFirstRun()]);
      fields = new Map((profile.editable || []).map((row) => [row.field, row]));
      firstRun = state;
    } catch (error) {
      replace(root, [
        el('p', { className: 'state__msg', text: error.userMessage || error.message }),
        button(t('action.retry'), () => open(stepKey), { className: 'btn' }),
      ]);
      return;
    }
    const index = stepKey ? STEPS.findIndex((step) => step.key === stepKey) : 0;
    at = index >= 0 ? index : 0;
    draw({ focus: Boolean(stepKey) });
  }

  function value(field) {
    const row = fields.get(field);
    return row ? row.value : null;
  }

  function stepState(key) {
    return (firstRun && (firstRun.steps || []).find((step) => step.key === key)) || {};
  }

  // ===================================================================
  // drawing
  // ===================================================================

  function draw({ focus = true } = {}) {
    // A progress poll belongs to the last card's nodes; drawing any card
    // replaces them, and the ready card starts its own poll again.
    stopPolling();
    const step = STEPS[at];
    const body = BODIES[step.key]();
    const question = QUESTIONS.findIndex((item) => item.key === step.key);
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
      question >= 0
        ? el('div', { className: 'setup__progress' }, [
          el('p', {
            className: 'setup__count',
            text: t('setup.progress', { n: question + 1, of: QUESTIONS.length }),
          }),
          el('ol', { className: 'setup__dots', attrs: { 'aria-hidden': 'true' } },
            QUESTIONS.map((item, index) => el('li', {
              className: `setup__dot${index < question ? ' is-done' : ''}${index === question ? ' is-now' : ''}`,
            }))),
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

  function defaultActions(step) {
    return [
      at > 0 ? button(t('setup.back'), () => go(-1), { className: 'btn', attrs: { id: 'setup-back' } }) : null,
      step.optional
        ? button(t('setup.skip'), () => go(1), { className: 'btn btn--quiet', attrs: { id: 'setup-skip' } })
        : null,
      el('button', {
        className: 'btn btn--primary',
        attrs: { type: 'submit', id: 'setup-next' },
        text: t('setup.continue'),
      }),
    ].filter(Boolean);
  }

  function go(delta) {
    at = Math.max(0, Math.min(STEPS.length - 1, at + delta));
    draw();
  }

  function leave() {
    stopPolling();
    rememberPostponed(true);
    if (onExit) onExit();
  }

  /** Save through the existing whitelist, then advance. Errors stay beside the card. */
  async function save(changes, error) {
    if (!Object.keys(changes).length) {
      go(1);
      return;
    }
    const submit = root.querySelector('#setup-next');
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
      go(1);
    } catch (failure) {
      error.textContent = failure.userMessage || failure.message;
      if (submit) {
        submit.disabled = false;
        submit.textContent = t('setup.continue');
      }
    }
  }

  function describedBy(input) {
    input.setAttribute('aria-describedby', 'setup-why setup-error');
    return input;
  }

  function label(forId, text) {
    return el('label', { className: 'setup__label', attrs: { for: forId }, text });
  }

  function hint(text) {
    return el('p', { className: 'setup__hint', text });
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
      submit: () => go(1),
    }),

    work: () => {
      const described = stepState('work');
      if (described.done) {
        return {
          nodes: [
            el('p', {
              className: 'setup__done',
              text: t('setup.work.already', { n: described.phrases || 0 }),
            }),
          ],
          submit: () => go(1),
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
          label('setup-work', t('setup.work.label')),
          work,
          hint(t('setup.work.example')),
          label('setup-skills', t('setup.work.skillsLabel')),
          skills,
          hint(t('setup.work.note')),
        ],
        submit: async (error) => {
          const split = (text) => text.split('\n').map((line) => line.trim()).filter(Boolean);
          const roles = split(draft.work);
          if (!roles.length) {
            error.textContent = t('setup.work.required');
            work.setAttribute('aria-invalid', 'true');
            work.focus();
            return;
          }
          const submit = root.querySelector('#setup-next');
          submit.disabled = true;
          submit.textContent = t('setup.saving');
          try {
            await api.createFirstSearch({ role_examples: roles, skills: split(draft.skills) });
            firstRun = await api.getFirstRun();
            go(1);
          } catch (failure) {
            error.textContent = failure.userMessage || failure.message;
            submit.disabled = false;
            submit.textContent = t('setup.continue');
          }
        },
      };
    },

    stage: () => {
      const current = stepState('career_stage').value || null;
      const stages = (firstRun && firstRun.career_stages) || [];
      const choice = { value: current };
      const radios = stages.map((stage) => {
        const id = `setup-stage-${stage}`;
        return el('label', { className: 'setup__option', attrs: { for: id } }, [
          el('input', {
            className: 'setup__radio',
            attrs: { type: 'radio', name: 'setup-stage', id, value: stage },
            props: { checked: stage === current },
            on: { change: () => { choice.value = stage; } },
          }),
          el('span', { text: t(`firstrun.stage.${stage}`) }),
        ]);
      });
      return {
        nodes: [el('fieldset', { className: 'setup__options' }, [
          el('legend', { className: 'setup__legend', text: t('setup.stage.legend') }),
          ...radios,
        ])],
        submit: async (error) => {
          if (!choice.value || choice.value === current) {
            go(1);
            return;
          }
          const submit = root.querySelector('#setup-next');
          submit.disabled = true;
          submit.textContent = t('setup.saving');
          try {
            await api.setCareerStage(choice.value);
            firstRun = await api.getFirstRun();
            go(1);
          } catch (failure) {
            error.textContent = failure.userMessage || failure.message;
            submit.disabled = false;
            submit.textContent = t('setup.continue');
          }
        },
      };
    },

    home: () => {
      const current = value('candidate_country') || '';
      const draft = drafts.home ?? current;
      const select = describedBy(el('select', {
        className: 'select setup__select',
        attrs: { id: 'setup-country', autocomplete: 'country' },
        on: { change: (event) => { drafts.home = event.target.value; } },
      }, [
        el('option', { text: t('setup.home.choose'), attrs: { value: '' } }),
        ...sortedCountries().map(({ code, name }) => el('option', {
          text: name,
          attrs: { value: code, ...(code === draft ? { selected: 'selected' } : {}) },
        })),
      ]));
      return {
        nodes: [label('setup-country', t('setup.home.label')), select],
        submit: (error) => {
          const code = select.value;
          if (!code || code === current) {
            go(1);
            return;
          }
          save({ candidate_country: code }, error);
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
      const list = el('ul', { className: 'setup__chips', attrs: { 'aria-live': 'polite' } });
      function paint() {
        replace(list, draft.others.map((code) => el('li', { className: 'setup__chip' }, [
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
      const picker = el('select', {
        className: 'select setup__select',
        attrs: { id: 'setup-hire-other' },
      }, [
        el('option', { text: t('setup.hire.pick'), attrs: { value: '' } }),
        ...sortedCountries()
          .filter(({ code }) => code !== home)
          .map(({ code, name }) => el('option', { text: name, attrs: { value: code } })),
      ]);
      const add = button(t('setup.hire.add'), () => {
        const code = picker.value;
        if (code && !draft.others.includes(code)) draft.others.push(code);
        picker.value = '';
        paint();
      }, { className: 'btn', attrs: { id: 'setup-hire-add' } });
      nodes.push(
        label('setup-hire-other', home ? t('setup.hire.othersLabel') : t('setup.hire.countriesLabel')),
        el('div', { className: 'setup__row' }, [picker, add]),
        list,
        hint(t('setup.hire.note')),
      );
      return {
        nodes,
        submit: (error) => {
          const next = [...(draft.homeAnswer === 'yes' && home ? [home] : []), ...draft.others];
          const same = next.length === saved.length && next.every((code) => saved.includes(code));
          if (same) {
            go(1);
            return;
          }
          save({ eligible_countries: next }, error);
        },
      };
    },

    regions: () => multiChoice({
      field: 'eligible_scopes',
      choices: REGIONS,
      labelOf: (code) => t(`setup.region.${code}`),
      legend: t('setup.regions.legend'),
      note: t('setup.regions.note'),
      allowEmpty: false,
    }),

    // LEVELS TO KEEP OFF THE LIST, not "levels you prefer". The preferred list
    // is recorded but nothing ranks or filters by it today; the excluded list
    // hides those postings from Discover, with a notice that shows them again.
    // A first run asks only questions whose answer changes something.
    level: () => multiChoice({
      field: 'seniority_excluded',
      choices: ['INTERN', 'JUNIOR', 'MID', 'SENIOR', 'STAFF', 'PRINCIPAL', 'LEAD'],
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
            go(1);
            return;
          }
          const number = Number(raw);
          if (!Number.isFinite(number) || number <= 0) {
            error.textContent = t('setup.pay.invalid');
            amount.setAttribute('aria-invalid', 'true');
            amount.focus();
            return;
          }
          if (!draft.currency) {
            error.textContent = t('setup.pay.needCurrency');
            currency.focus();
            return;
          }
          const changes = {};
          if (number !== Number(savedAmount)) changes.compensation_target = number;
          if (draft.currency !== savedCurrency) changes.compensation_currency = draft.currency;
          save(changes, error);
        },
      };
    },

    ready: () => readyCard(),
  };

  /** A set of checkboxes for one enum-list field. */
  function multiChoice({ field, choices, labelOf, legend, note = '', allowEmpty }) {
    const saved = Array.isArray(value(field)) ? value(field) : [];
    const chosen = drafts[field] || new Set(saved);
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
        el('fieldset', { className: 'setup__options' }, [
          el('legend', { className: 'setup__legend', text: legend }),
          ...boxes,
        ]),
        note ? hint(note) : null,
      ].filter(Boolean),
      submit: (error) => {
        const next = choices.filter((choice) => chosen.has(choice));
        const same = next.length === saved.length && next.every((item) => saved.includes(item));
        if (same) {
          go(1);
          return;
        }
        if (!next.length && !allowEmpty) {
          if (!saved.length) {
            go(1);
            return;
          }
          error.textContent = t('setup.pickOne');
          return;
        }
        save({ [field]: next }, error);
      },
    };
  }

  // ===================================================================
  // the last card: what was answered, and finding the first jobs
  // ===================================================================

  /** "9,000 BRL", in the reader's number format, or null when unanswered. */
  function payTarget() {
    const amount = value('compensation_target');
    if (!amount) return null;
    const figure = Number(amount).toLocaleString(getLocale());
    return [figure, value('compensation_currency') || ''].join(' ').trim();
  }

  function readyCard() {
    const summary = [
      ['work', stepState('work').done
        ? t('setup.ready.phrases', { n: stepState('work').phrases || 0 })
        : null],
      ['home', value('candidate_country') ? countryName(value('candidate_country')) : null],
      ['hire', (value('eligible_countries') || []).length
        ? (value('eligible_countries') || []).map(countryName).join(', ')
        : null],
      // Skipping this card hides nothing, which is an answer: "None".
      ['level', (value('seniority_excluded') || []).map(vocab).join(', ') || t('setup.ready.none')],
      ['pay', payTarget()],
    ];
    const status = el('div', {
      className: 'setup__find',
      attrs: { role: 'status', 'aria-live': 'polite' },
    });
    const find = button(t('setup.ready.find'), () => startFinding(status, find), {
      className: 'btn btn--primary',
      attrs: { id: 'setup-find' },
    });
    // A run already going (another tab, or a reload) is picked up, not restarted.
    followRun(status, find, { quiet: true });
    return {
      nodes: [
        el('dl', { className: 'setup__summary' }, summary.flatMap(([key, text]) => [
          el('dt', { text: t(`setup.ready.${key}`) }),
          el('dd', {}, [
            el('span', {
              className: text ? '' : 'setup__unanswered',
              text: text || t('setup.ready.notAnswered'),
            }),
            button(t('setup.ready.change'), () => {
              at = STEPS.findIndex((step) => step.key === key);
              draw();
            }, {
              className: 'btn btn--link',
              ariaLabel: t('setup.ready.changeLabel', { what: t(`setup.ready.${key}`) }),
            }),
          ]),
        ])),
        hint(t('setup.ready.note')),
        status,
      ],
      actions: [
        button(t('setup.back'), () => go(-1), { className: 'btn', attrs: { id: 'setup-back' } }),
        button(t('setup.ready.toHome'), () => leave(), { className: 'btn', attrs: { id: 'setup-home' } }),
        find,
      ],
      submit: () => startFinding(status, find),
    };
  }

  async function startFinding(status, find) {
    find.disabled = true;
    find.textContent = t('setup.ready.starting');
    try {
      await api.findJobs();
    } catch (failure) {
      replace(status, [el('p', { className: 'setup__error', text: failure.userMessage || failure.message })]);
      find.disabled = false;
      find.textContent = t('setup.ready.find');
      return;
    }
    followRun(status, find);
  }

  async function followRun(status, find, { quiet = false } = {}) {
    stopPolling();
    let data;
    try {
      data = await api.getRetrieval();
    } catch (failure) {
      if (!quiet) {
        const text = failure.userMessage || failure.message;
        replace(status, [el('p', { className: 'setup__error', text })]);
      }
      return;
    }
    const run = data.run;
    if (data.running && run) {
      find.disabled = true;
      find.textContent = t('setup.ready.finding');
      const total = run.boards_total || 0;
      const done = Math.min(run.boards_done || 0, total);
      const percent = total ? Math.round((done / total) * 100) : 0;
      replace(status, [
        el('p', { className: 'setup__findtext', text: t('setup.ready.progress', { done, total }) }),
        el('div', {
          className: 'setup__bar',
          attrs: {
            role: 'progressbar',
            'aria-valuemin': '0',
            'aria-valuemax': String(total || 1),
            'aria-valuenow': String(done),
            'aria-label': t('setup.ready.progressLabel'),
          },
        }, [el('span', { className: 'setup__barfill', style: { width: `${percent}%` } })]),
        hint(t('setup.ready.keepUsing')),
        button(t('setup.ready.stop'), async () => {
          try { await api.cancelRetrieval(); } catch { /* the next poll reports it */ }
        }, { className: 'btn btn--quiet', attrs: { id: 'setup-stop' } }),
      ]);
      poll = setTimeout(() => followRun(status, find), 2000);
      return;
    }
    if (quiet || !run) return;
    find.disabled = false;
    find.textContent = t('setup.ready.findAgain');
    const sources = run.sources || [];
    const ok = sources.filter((row) => row.status === 'ok').length;
    const text = run.status === 'cancelled'
      ? t('setup.ready.cancelled', { ok })
      : run.status === 'failed'
        ? t('setup.ready.failed')
        : t('setup.ready.finished', { ok, total: sources.length });
    replace(status, [
      el('p', { className: 'setup__findtext', text }),
      run.status === 'failed'
        ? null
        : button(t('setup.ready.see'), () => {
          rememberPostponed(true);
          if (onGoTo) onGoTo('jobs');
        }, { className: 'btn btn--primary', attrs: { id: 'setup-see' } }),
    ].filter(Boolean));
  }

  function stopPolling() {
    if (poll) {
      clearTimeout(poll);
      poll = null;
    }
  }

  /** Redraw the current card in the reader's language, keeping what was typed. */
  function relabel() {
    if (fields.size) draw({ focus: false });
  }

  return { root, open, relabel, stop: stopPolling };
}
