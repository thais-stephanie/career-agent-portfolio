/**
 * detail.js -- the job drawer (V3 handoff).
 *
 * FIVE TABS, AND THE ORDER INSIDE THEM IS THE ARGUMENT.
 *
 *   About              the job itself: four facts, a short version when one
 *                      exists, the tools it names, and the WHOLE ad. The
 *                      posting is the primary evidence, so nothing the local
 *                      model wrote may stand in front of it.
 *   Why it fits        the match as a sentence ("4 of the 5 things you asked
 *                      for"), what fits with the ad's own words, what does
 *                      not yet, whether you can take it, and your opinion.
 *   Before you apply   three steps: read the ad, proof for what it asks,
 *                      a resume for this job.
 *   Practice           interview questions, one at a time (practice.js).
 *   Notes              your words, where the application stands, its history.
 *
 * NOTHING TECHNICAL IN NORMAL USE. The score's arithmetic, the posting
 * completeness items, the raw local reading and its schema live behind
 * `?debug=1`. Nothing was deleted; the reasons a person reads are still
 * quotes from the posting, and every quote is verbatim (ADR-0002).
 *
 * The description is rendered as escaped text with paragraph breaks. Server
 * text never touches innerHTML: a job description is a string a stranger
 * wrote.
 */

import { el, button, extLink, select, replace, clear, focusables } from './dom.js';
import {
  compactPlace, formatDate, formatPoints, formatSalary, humanLabel, paragraphs, parseDate,
  prominenceWords, relativeAge, scoreDisplay, statusLabel, statusOptions, vocabLabel,
} from './format.js';
import { searchFitIsReady } from './badges.js';
import { SENT, matchTone } from './cards.js';
import { createPrepare } from './prepare.js';
import { adTools, createPractice, jobMark, setJobMark } from './practice.js';
import { t, tState } from './i18n.js';
import * as api from './api.js';

//: The gates, in the order "Can you take this job?" shows them.
const GATE_ORDER = ['geography', 'work_authorization', 'worksite', 'travel', 'clearance', 'credential', 'requirement'];
const PREP_STORE = 'careerAgent.prep.read.v1';

export function createDrawer({
  onStatus, onSave, onNotes, onClearAppliedAt, onClosed, onChanged,
  onApplied = null,
  getOllama = () => ({}), onEvidence = null,
  careerContext = null, onAddCareer = null,
  onTailor = null,
  resumeFor = null,
  debug = false,
}) {
  let invoker = null;
  let currentJob = null;
  //: Search Fit answers, saved one after another (fitFeedbackSection).
  let fitSaves = Promise.resolve();
  let requestToken = 0;
  // Set while an enrichment request is in flight. Aborts the request and stops
  // its elapsed-seconds timer, so neither survives a close or a re-open.
  let enrichCleanup = null;

  const panelNode = (key, hidden = true) => el('div', {
    className: 'drawer__tabpanel',
    attrs: { role: 'tabpanel', id: `drawer-panel-${key}`, 'aria-labelledby': `drawer-tab-${key}` },
    props: { hidden },
  });
  const detailPanel = panelNode('details', false);
  const whyPanel = panelNode('why');
  const preparePanel = panelNode('prepare');
  const practicePanel = panelNode('practice');
  const notesPanel = panelNode('notes');

  // The preparation question is its own request, asked when the tab opens.
  // Step 2 reads its answer; the full requirement-by-requirement view stays
  // available under step 2, folded.
  let preparation = null;
  const prepare = createPrepare({
    onEvidence,
    onLoaded: (payload) => {
      preparation = payload;
      if (currentJob) paintPrepare(currentJob);
    },
  });
  const prepDetail = el('details', { className: 'd-prepdetail' }, [
    el('summary', { className: 'd-link', text: t('prep3.details') }),
    prepare.host,
  ]);
  const practice = createPractice();
  practicePanel.append(practice.host);

  const TABS = [
    // Keys, not strings: the drawer is built before `setLocale` has read the
    // stored choice. `relabel()` resolves them, now and on every switch.
    { key: 'details', labelKey: 'drawer.tab.details', panel: detailPanel },
    { key: 'why', labelKey: 'drawer.tab.why', panel: whyPanel },
    { key: 'prepare', labelKey: 'drawer.tab.prepare', panel: preparePanel },
    { key: 'practice', labelKey: 'drawer.tab.practice', panel: practicePanel },
    { key: 'notes', labelKey: 'drawer.tab.notes', panel: notesPanel },
  ];

  // The chosen tab survives while the drawer is open, including across the
  // re-render a status change triggers. It resets on close.
  let activeTab = 'details';
  let loadedPrepareFor = null;
  let loadedPracticeFor = null;

  const tabButtons = TABS.map((tab) => button(t(tab.labelKey), () => selectTab(tab.key), {
    className: 'drawer__tab',
    attrs: {
      role: 'tab',
      id: `drawer-tab-${tab.key}`,
      'aria-controls': `drawer-panel-${tab.key}`,
      'aria-selected': String(tab.key === activeTab),
      tabindex: tab.key === activeTab ? '0' : '-1',
    },
  }));

  const tabList = el('div', {
    className: 'drawer__tabs',
    attrs: { role: 'tablist', 'aria-label': t('drawer.tabs.label') },
  }, tabButtons);

  /** Our words on the tabs and the close button; never the job's. */
  function relabel() {
    TABS.forEach((tab, index) => {
      tabButtons[index].textContent = t(tab.labelKey);
    });
    tabList.setAttribute('aria-label', t('drawer.tabs.label'));
    closeButton.textContent = '✕';
    closeButton.setAttribute('aria-label', t('drawer.close'));
  }

  /** Roving tabindex: one stop for the tab set, arrows and Home/End inside. */
  tabList.addEventListener('keydown', (event) => {
    const moves = { ArrowLeft: -1, ArrowRight: 1, Home: 'first', End: 'last' };
    const move = moves[event.key];
    if (move === undefined) return;
    event.preventDefault();
    const here = TABS.findIndex((tab) => tab.key === activeTab);
    let next;
    if (move === 'first') next = 0;
    else if (move === 'last') next = TABS.length - 1;
    else next = (here + move + TABS.length) % TABS.length;
    selectTab(TABS[next].key);
    tabButtons[next].focus();
  });

  function selectTab(key) {
    if (key !== activeTab) bodyHost.scrollTop = 0;
    activeTab = key;
    // Loaded on demand: most opens never reach these tabs.
    if (key === 'prepare' && currentJob && loadedPrepareFor !== currentJob.job_id) {
      loadedPrepareFor = currentJob.job_id;
      preparation = null;
      prepare.load(currentJob.job_id);
      paintPrepare(currentJob);
    }
    if (key === 'practice' && currentJob && loadedPracticeFor !== currentJob.job_id) {
      loadedPracticeFor = currentJob.job_id;
      practice.load(currentJob);
    }
    TABS.forEach((tab, index) => {
      const chosen = tab.key === key;
      tabButtons[index].setAttribute('aria-selected', String(chosen));
      tabButtons[index].setAttribute('tabindex', chosen ? '0' : '-1');
      tab.panel.hidden = !chosen;
    });
  }

  const PANELS = [detailPanel, whyPanel, preparePanel, practicePanel, notesPanel];
  const bodyHost = el('div', { className: 'drawer__body' }, PANELS);
  const companyNode = el('span', { className: 'd-company drawer__company' });
  const whereNode = el('span', { className: 'drawer__where' });
  const actionsHost = el('div', { className: 'd-sec--identity drawer__actions' });
  const titleNode = el('h2', { className: 'drawer__title', attrs: { id: 'drawer-title' }, text: '' });

  /** Paint every panel. All are built; one is visible. */
  function paint(job) {
    companyNode.textContent = job.company_name || t('absent.companyStated');
    whereNode.textContent = whereLine(job);
    replace(actionsHost, headActions(job));
    replace(detailPanel, aboutSections(job));
    replace(whyPanel, whySections(job));
    paintPrepare(job);
    replace(notesPanel, notesSections(job));
    if (!bodyHost.contains(detailPanel)) replace(bodyHost, PANELS);
    selectTab(activeTab);
  }

  const closeButton = button('', () => close(), { className: 'btn btn--close drawer__close' });
  relabel();

  const panel = el('div', {
    className: 'drawer__panel',
    attrs: { role: 'dialog', 'aria-modal': 'true', 'aria-labelledby': 'drawer-title', tabindex: '-1' },
  }, [
    el('header', { className: 'drawer__head' }, [
      el('div', { className: 'drawer__headtop' }, [
        el('div', { className: 'drawer__who' }, [companyNode, titleNode, whereNode]),
        closeButton,
      ]),
      actionsHost,
      tabList,
    ]),
    bodyHost,
  ]);

  const scrim = el('div', { className: 'drawer__scrim', on: { click: () => close() } });
  const root = el('div', { className: 'drawer', attrs: { hidden: true } }, [scrim, panel]);

  root.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') {
      event.stopPropagation();
      close();
      return;
    }
    if (event.key !== 'Tab') return;
    const nodes = focusables(panel);
    if (!nodes.length) return;
    const first = nodes[0];
    const last = nodes[nodes.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  });

  async function open(jobId, invokedBy, tab = null) {
    if (enrichCleanup) enrichCleanup();
    invoker = invokedBy || document.activeElement;
    if (tab && TABS.some((entry) => entry.key === tab)) activeTab = tab;
    root.hidden = false;
    document.body.classList.add('has-drawer');
    titleNode.textContent = t('drawer.loading');
    companyNode.textContent = '';
    whereNode.textContent = '';
    clear(actionsHost);
    replace(bodyHost, [el('div', { className: 'sk sk--block' }), el('div', { className: 'sk sk--block' })]);
    panel.focus();

    const token = ++requestToken;
    try {
      const job = await api.getJob(jobId);
      if (token !== requestToken) return;
      currentJob = job;
      titleNode.textContent = job.title || t('absent.untitled');
      paint(job);
      const firstFocus = focusables(panel)[0];
      if (firstFocus) firstFocus.focus();
    } catch (error) {
      if (token !== requestToken) return;
      titleNode.textContent = t('drawer.loadFailed');
      replace(bodyHost, [
        el('p', { className: 'state__msg', text: error.userMessage || error.message }),
        button(t('action.tryAgain'), () => open(jobId, invoker), { className: 'btn' }),
      ]);
    }
  }

  function close() {
    if (root.hidden) return;
    if (enrichCleanup) enrichCleanup();
    requestToken += 1;
    root.hidden = true;
    document.body.classList.remove('has-drawer');
    clear(bodyHost);
    activeTab = 'details';
    askingFor = null;
    prepare.reset();
    practice.reset();
    preparation = null;
    loadedPrepareFor = null;
    loadedPracticeFor = null;
    madeFor.clear();
    currentJob = null;
    if (invoker && document.contains(invoker)) invoker.focus();
    invoker = null;
    if (onClosed) onClosed();
  }

  /** Redraw from whatever a mutation resolved to (the full job, or nothing). */
  function refreshWith(result) {
    return Promise.resolve(result).then((job) => {
      if (!job || !job.job_id) return;
      if (!currentJob || currentJob.job_id !== job.job_id) return;
      currentJob = job;
      paint(job);
    });
  }

  // ======================================================================
  // THE HEADER
  // ======================================================================

  /** Where, and when and where it was posted: the line under the title. */
  function whereLine(job) {
    const place = compactPlace(job.location_raw, job.work_model).text;
    const source = job.provider ? vocabLabel(job.provider) : t('absent.source');
    const date = parseDate(job.posted_at) ? relativeAge(job.posted_at) : null;
    const posted = date ? t('drawer.postedAgo', { age: date, source }) : source;
    return `${place} · ${posted}`;
  }

  //: The drawer's own "Did you send your application?", like the card's.
  let askingFor = null;

  /** The match, the heart, the job ad, Apply, and the question after it. */
  function headActions(job) {
    const score = scoreDisplay(job.match_score);
    const tone = searchFitIsReady() && score.scored ? matchTone(job.match_score) : null;
    const match = tone
      ? button('', () => selectTab('why'), {
        className: `drawer__match card__match--${tone.tone}`,
        attrs: { title: t('card.whyHelp') },
      })
      : null;
    if (match) {
      match.append(
        el('span', { className: 'card__pct num', text: `${score.text}%` }),
        el('span', { className: 'card__matchtext', text: t(tone.key) }),
      );
    }
    const heart = button(job.saved ? '♥' : '♡', () => refreshWith(onSave(job.job_id, !job.saved)), {
      className: `card__heart btn--save${job.saved ? ' is-saved is-on' : ''}`,
      ariaLabel: job.saved ? t('card.unsaveLabel', { title: job.title }) : t('card.saveLabel', { title: job.title }),
      attrs: { 'aria-pressed': job.saved ? 'true' : 'false' },
    });
    const status = String(job.application_status || 'DISCOVERED');
    const sent = SENT.has(status);
    const ad = extLink(job.url, t('drawer.openAd'), { className: 'drawer__ad' });
    let apply = null;
    if (sent) {
      apply = el('span', { className: 'drawer__sent', text: `✓ ${statusLabel(status)}` });
    } else if (job.url && onApplied) {
      apply = extLink(job.url, t('drawer.applyOnSite'), { className: 'drawer__apply' });
      if (apply.tagName === 'A') {
        apply.addEventListener('click', () => {
          askingFor = job.job_id;
          setTimeout(() => refreshWith(Promise.resolve(currentJob)), 0);
        });
      }
    }
    const ask = askingFor === job.job_id && !sent
      ? el('div', { className: 'drawer__ask', attrs: { role: 'group', 'aria-label': t('drawer.askQuestion') } }, [
        el('span', { className: 'drawer__askq', text: t('drawer.askQuestion') }),
        el('span', { className: 'drawer__askbtns' }, [
          button(t('drawer.askYes'), () => {
            askingFor = null;
            refreshWith(onApplied(job.job_id));
          }, { className: 'card__yes' }),
          button(t('card.askNo'), () => {
            askingFor = null;
            refreshWith(Promise.resolve(currentJob));
          }, { className: 'card__no' }),
        ]),
      ])
      : null;
    return [
      el('div', { className: 'drawer__actrow' }, [match, el('span', { className: 'drawer__grow' }), heart, ad, apply]
        .filter(Boolean)),
      ask,
    ].filter(Boolean);
  }

  // ======================================================================
  // ABOUT: four facts, In short, the tools, the whole ad
  // ======================================================================

  function aboutSections(job) {
    return [
      tiles(job),
      contentNotes(job),
      inShortSection(job),
      toolsSection(job),
      descriptionSection(job),
      duplicatesSection(job),
      debug ? rawReading(job) : null,
    ].filter(Boolean);
  }

  /** Pay, Job type, Where, Level: only what the posting says, never a guess. */
  function tiles(job) {
    const salary = formatSalary(job.salary);
    const place = compactPlace(job.location_raw, job.work_model);
    const facts = [
      { key: 'pay', icon: '$', value: salary || t('drawer.tile.notShown'), absent: !salary },
      {
        key: 'type', icon: '◷',
        value: job.employment_type ? vocabLabel(job.employment_type) : t('drawer.tile.notStated'),
        absent: !job.employment_type,
      },
      { key: 'where', icon: '\u2302', value: place.text || t('drawer.tile.notStated'), absent: !place.text },
      {
        key: 'level', icon: '▲',
        value: job.seniority_stated ? vocabLabel(job.seniority) : t('drawer.tile.notStated'),
        absent: !job.seniority_stated,
      },
    ];
    return el('div', { className: 'd-tiles' }, facts.map((fact) => el('div', {
      className: `d-tile d-tile--${fact.key}`,
    }, [
      el('div', { className: 'd-tile__head' }, [
        el('span', { className: 'd-tile__icon', text: fact.icon, attrs: { 'aria-hidden': 'true' } }),
        el('span', { className: 'd-tile__label', text: t(`drawer.tile.${fact.key}`) }),
      ]),
      el('span', { className: `d-tile__value${fact.absent ? ' is-absent' : ''}`, text: fact.value }),
    ])));
  }

  /** A thin posting, or a US-domestic reading: they change how the rest reads. */
  function contentNotes(job) {
    const notes = [];
    if (job.content_completeness === 'PARTIAL_CONTENT' || job.content_completeness === 'METADATA_ONLY') {
      notes.push(el('p', { className: 'd-note', text: `${t(`content.${job.content_completeness}`)}. ${
        job.content_completeness === 'PARTIAL_CONTENT' ? t('content.partialHelp') : t('content.metadataHelp')}` }));
    }
    if (job.domestic_context === 'LIKELY_US_DOMESTIC' && job.eligibility_status === 'UNRESOLVED') {
      notes.push(el('p', { className: 'd-note', text: t('domestic.LIKELY_US_DOMESTIC') }));
    }
    return notes.length ? el('section', { className: 'd-sec d-contentnotes' }, notes) : null;
  }

  /** The tools the ad names: the local reading's when there is one. */
  function toolsSection(job) {
    const labels = adTools(job);
    if (!labels.length) return null;
    return section(t('drawer.toolsHead'), [
      el('ul', { className: 'd-chips' }, labels.map((label) => el('li', { className: 'd-chip', text: label }))),
    ], { className: 'd-sec--tools' });
  }

  function descriptionSection(job) {
    const blocks = paragraphs(job.description || job.description_excerpt || '');
    return section(t('drawer.fullAd'), blocks.length
      ? blocks.map((block) => el('p', { className: 'desc__p', text: block }))
      : [el('p', { className: 'd-note', text: t('drawer.noDescription') })],
    { className: 'd-sec--desc d-desc' });
  }

  /** The other postings of this same role, if there are any. */
  function duplicatesSection(job) {
    const count = Number(job.duplicate_count || 1);
    if (count <= 1) return null;
    const places = job.sibling_locations || [];
    const hidden = count - places.length;
    return section(t('drawer.secPlaces'), [
      el('p', { className: 'd-sec__lede', text: t('drawer.placesLede', { company: job.company_name, n: count }) }),
      el('ul', { className: 'd-places' }, [
        ...places.map((place) => el('li', { className: 'd-places__item', text: place })),
        hidden > 0
          ? el('li', { className: 'd-places__item d-places__item--more', text: t('drawer.morePlaces', { n: hidden }) })
          : null,
      ].filter(Boolean)),
    ]);
  }

  // -- In short: the local model's summary, or the way to ask for one -------

  /**
   * `health.ollama.reachable` is three-valued: `null` is "we have not asked"
   * (a page load never opens a socket), and only `false` is a failed contact.
   */
  function enrichHint(ollama) {
    const endpoint = ollama.endpoint || t('drawer.configuredEndpoint');
    if (ollama.configured === false) return t('drawer.noLocalModel');
    if (ollama.reachable === false) return t('drawer.localModelSilent', { endpoint });
    if (ollama.reachable === true) {
      return t('drawer.localModelReady', { model: ollama.model || t('drawer.theLocalModel'), endpoint });
    }
    return t('drawer.localModelUntried');
  }

  /**
   * IN SHORT. The local reading's summary, in plain words, when it exists;
   * otherwise the one control that asks the model on this computer for it.
   * A reading takes minutes, so the server runs it in the background: this
   * starts it, asks for its state every second and a half, and cancels it.
   * Closing the drawer only stops asking. Only the model on this computer is
   * ever asked.
   */
  function inShortSection(job) {
    const ollama = getOllama() || {};
    const model = ollama.model || t('drawer.theLocalModel');
    const enrichment = job.enrichment && Object.keys(job.enrichment).length ? job.enrichment : null;
    const summary = enrichment && String(enrichment.summary || '').trim();
    const messageHost = el('p', { className: 'enrich__msg', attrs: { 'aria-live': 'polite', id: 'enrich-msg' } });
    const elapsedHost = el('span', { className: 'enrich__elapsed num', attrs: { 'aria-hidden': 'true' } });
    const hint = el('p', {
      className: `enrich__hint${ollama.reachable === false ? ' enrich__hint--blocked' : ''}`,
      attrs: { id: 'enrich-hint' },
      text: enrichHint(ollama),
    });

    let poller = null;
    let stopped = false;
    // Only a reading this drawer SAW running may redraw it on success; a
    // finished reading answers SUCCESS again on every open.
    let sawRunning = false;

    //: Error codes (`local_ai/runner.py` states, `web/api.py`) with a sentence.
    const ERROR_KEYS = {
      below_threshold: 'local.state.belowThreshold',
      unverifiable: 'local.state.unverifiable',
      ollama_error: 'local.state.ollamaError',
      no_such_job: 'local.state.noSuchJob',
      busy: 'local.state.busy',
      refused: 'local.state.refused',
    };

    function stopPolling() {
      if (poller) clearTimeout(poller);
      poller = null;
    }

    function running(state) {
      sawRunning = true;
      runButton.disabled = true;
      cancelButton.hidden = false;
      cancelButton.disabled = Boolean(state.cancel_requested);
      messageHost.className = 'enrich__msg';
      messageHost.dataset.state = 'RUNNING';
      let words = t('drawer.inShortWriting');
      if (state.cancel_requested) words = t('local.cancelling');
      else if (debug) {
        const params = { model: state.model || model, tokens: state.tokens || 0 };
        words = t(`local.phase.${state.phase || 'checking'}`, params);
      }
      messageHost.textContent = words;
      elapsedHost.textContent = `${Math.round(state.elapsed_s || 0)}s`;
    }

    function finished(state) {
      stopPolling();
      runButton.disabled = false;
      cancelButton.hidden = true;
      elapsedHost.textContent = '';
      messageHost.dataset.state = state.state;
      messageHost.className = state.state === 'SUCCESS' ? 'enrich__msg' : 'enrich__msg enrich__msg--calm';
      const seconds = Math.round(state.elapsed_s || 0);
      const key = state.state === 'ERROR'
        ? (ERROR_KEYS[state.code] || (state.code ? 'local.state.unexpected' : 'local.state.ERROR'))
        : `local.state.${state.state}`;
      messageHost.textContent = t(key, { seconds, model: state.model || model, error: state.message || '' });
      if (state.state === 'SUCCESS' && sawRunning) {
        sawRunning = false;
        api.getJob(job.job_id).then((updated) => {
          if (stopped) return;
          if (onChanged) onChanged(updated);
          open(job.job_id, invoker);
        }).catch(() => {});
      }
    }

    function show(state) {
      if (stopped || !state) return;
      if (state.state === 'RUNNING') {
        running(state);
        stopPolling();
        poller = setTimeout(poll, 1500);
      } else if (state.state === 'NOT_RUN') {
        messageHost.textContent = '';
        runButton.disabled = ollama.configured === false;
      } else {
        finished(state);
      }
    }

    async function poll() {
      try {
        show(await api.enrichStatus(job.job_id));
      } catch (error) {
        finished({ state: 'ERROR', message: error.userMessage || error.message });
      }
    }

    const cancelButton = button(t('action.cancel'), async () => {
      cancelButton.disabled = true;
      messageHost.textContent = t('local.cancelling');
      try { await api.cancelEnrich(job.job_id); } catch { /* the next poll says what happened */ }
    }, { className: 'btn', ariaLabel: t('drawer.cancelLocalModel'), attrs: { id: 'enrich-cancel' } });
    cancelButton.hidden = true;

    const runButton = button(summary ? t('drawer.inShortAgain') : t('drawer.inShortMake'), async () => {
      runButton.disabled = true;
      try {
        show(await api.startEnrich(job.job_id));
      } catch (error) {
        finished({ state: 'ERROR', code: 'refused', message: error.userMessage || error.message });
      }
    }, { className: 'btn btn--quiet', attrs: { 'aria-describedby': 'enrich-hint', id: 'enrich-run' } });
    if (ollama.configured === false) runButton.disabled = true;

    if (enrichCleanup) enrichCleanup();
    enrichCleanup = () => {
      stopped = true;
      stopPolling();
    };
    api.enrichStatus(job.job_id).then(show).catch(() => {});

    if (!summary && ollama.configured === false) {
      enrichCleanup();
      return null;
    }
    return section(t('drawer.inShort'), [
      summary
        ? el('p', { className: 'd-short' }, [summary])
        : el('p', { className: 'd-note', text: t('drawer.inShortNone') }),
      el('div', { className: 'enrich__actions' }, [runButton, cancelButton, elapsedHost, messageHost]),
      debug ? hint : null,
    ], { lede: t('drawer.inShortLede'), className: 'd-sec--enrich d-sec--short' });
  }

  /** The local reading exactly as stored. Debug only: it is the machinery. */
  function rawReading(job) {
    const enrichment = job.enrichment && Object.keys(job.enrichment).length ? job.enrichment : null;
    if (!enrichment) return null;
    return el('details', { className: 'd-sec d-advanced' }, [
      el('summary', { className: 'd-advanced__summary', text: t('drawer.secEnrich') }),
      el('dl', { className: 'kv kv--enrich' }, Object.entries(enrichment).flatMap(([key, value]) => [
        el('dt', { text: humanLabel(key) }),
        el('dd', { text: renderValue(value) }),
      ])),
    ]);
  }

  // ======================================================================
  // WHY IT FITS
  // ======================================================================

  function whySections(job) {
    return [
      fitSummary(job),
      whatFits(job),
      whatDoesNotFit(job),
      canYouTake(job),
      fitFeedbackSection(job),
      debug ? scoreBreakdown(job) : null,
    ].filter(Boolean);
  }

  /** The configured parts of the match, in a plain name. */
  function componentName(component) {
    return named(`component.${component.component_id}`, component.label || component.component_id);
  }

  /**
   * The things the person asked for, as the score already counts them: every
   * configured component with points to give, in three states. FULL earned
   * its whole share, PART some of it, NONE nothing. Partial credit is never
   * shown as a fit (invariant 6).
   *
   * Silence is not a mismatch (V3, and invariant 2 the other way round): pay
   * when the ad states none, a level the ad never stated (ADR-0014: a default
   * level earns zero) and a way of working the ad never named are left out.
   */
  function askedFor(job) {
    const silent = {
      compensation_contract: !job.salary,
      seniority: !job.seniority_stated,
      work_model: !job.work_model,
    };
    const counted = (job.components || []).filter((c) => c.configured !== false && Number(c.max_points) > 0
      && !(silent[c.component_id] && !(Number(c.points) > 0)));
    const full = (c) => Number(c.points) >= Number(c.max_points) - 0.05;
    return {
      fits: counted.filter(full),
      parts: counted.filter((c) => Number(c.points) > 0 && !full(c)),
      gaps: counted.filter((c) => !(Number(c.points) > 0)),
    };
  }

  function fitSummary(job) {
    const scored = job.match_score !== null && job.match_score !== undefined;
    if (!scored || !searchFitIsReady()) {
      return el('section', { className: 'd-card d-fit' }, [
        el('p', { className: 'd-note', text: t('drawer.unscored') }),
      ]);
    }
    const score = Math.round(job.match_score);
    const tone = matchTone(job.match_score);
    const { fits, parts, gaps } = askedFor(job);
    const total = fits.length + parts.length + gaps.length;
    let sentence = '';
    if (total && fits.length === total) sentence = t('drawer.fitAll');
    else if (total && parts.length) sentence = t('drawer.fitSomePart', { n: fits.length, total, part: parts.length });
    else if (total) sentence = t('drawer.fitSome', { n: fits.length, total });
    const bars = el('div', { className: 'd-fit__bars', attrs: { 'aria-hidden': 'true' } },
      Array.from({ length: total }, (_, index) => el('span', {
        className: `d-fit__bar${index < fits.length ? ' is-on' : index < fits.length + parts.length ? ' is-part' : ''}`,
      })));
    return el('section', { className: 'd-card d-fit' }, [
      el('div', { className: 'd-fit__head' }, [
        el('span', { className: `d-fit__pct card__match--${tone.tone}` }, [
          el('span', { className: 'card__pct num', text: `${score}%` }),
        ]),
        el('div', { className: 'd-fit__words' }, [
          el('strong', { className: `d-fit__label d-fit__label--${tone.tone}`, text: t(tone.key) }),
          sentence ? el('span', { className: 'd-fit__sentence why__summary', text: sentence }) : null,
        ]),
      ]),
      total ? bars : null,
      el('span', { className: 'd-fit__note', text: t('drawer.fitNote') }),
    ]);
  }

  /** The rows the score earned, strongest first, each with the ad's line. */
  function strengthRows(job) {
    const rows = [];
    for (const component of job.components || []) {
      for (const contrib of component.contributions || []) {
        if (Number(contrib.points) > 0 && contrib.counted !== false) {
          // The level row's server label names the enum ("a MID role"); the
          // reader gets the level in words, from the same reading.
          rows.push(component.component_id === 'seniority' && job.seniority_stated
            ? { ...contrib, label: t('drawer.levelRow', { level: vocabLabel(job.seniority) }) }
            : contrib);
        }
      }
    }
    rows.sort((a, b) => Number(b.points) - Number(a.points));
    return rows.slice(0, 8);
  }

  function whatFits(job) {
    const rows = strengthRows(job);
    if (!rows.length) return null;
    return section(t('drawer.fitsHead'), [
      el('ul', { className: 'reasons d-list-card' }, rows.map((row) => el('li', { className: 'reason d-row' }, [
        el('span', { className: 'd-row__mark d-row__mark--yes', text: '✓', attrs: { 'aria-hidden': 'true' } }),
        el('div', { className: 'd-row__body' }, [
          el('p', { className: 'reason__label d-row__title', text: row.label || humanLabel(row.signal_id) }),
          row.quote
            ? el('p', { className: 'd-row__text' }, [
              `${t('drawer.adSays')} `, el('q', { className: 'quote quote--inline', text: row.quote }),
            ])
            : el('p', { className: 'quote-absent d-row__text', text: t('drawer.matchedNoQuote') }),
        ]),
      ]))),
    ], { lede: t('drawer.fitsLede') });
  }

  function whatDoesNotFit(job) {
    const { parts, gaps } = askedFor(job);
    if (!gaps.length && !parts.length) return null;
    const row = (component, text) => el('li', { className: 'gap d-row' }, [
      el('span', { className: 'd-row__mark d-row__mark--part', text: '-', attrs: { 'aria-hidden': 'true' } }),
      el('div', { className: 'd-row__body' }, [
        el('p', { className: 'd-row__title', text: componentName(component) }),
        el('p', { className: 'd-row__text', text }),
      ]),
    ]);
    return section(t('drawer.gapsHead'), [
      el('ul', { className: 'gaps d-list-card' }, [
        ...parts.map((component) => row(component, t('drawer.partText'))),
        ...gaps.map((component) => row(component, t('drawer.gapText'))),
      ]),
    ], { lede: t('drawer.gapsLede2') });
  }

  /**
   * CAN YOU TAKE THIS JOB? One card per gate, plus the experience the ad asks
   * of somebody. "Not in the ad" is the normal state, never an error: silence
   * is UNRESOLVED (invariant 2), shown as exactly that.
   */
  function canYouTake(job) {
    const gates = [...(job.gates || [])].sort((a, b) => GATE_ORDER.indexOf(a.gate) - GATE_ORDER.indexOf(b.gate));
    const cards = gates.map((gate) => {
      const result = String(gate.result || 'UNRESOLVED').toUpperCase();
      const tone = result === 'PASS' ? 'm1' : result === 'FAIL' ? 'red' : 'chip';
      return el('li', { className: `gate gate--${result.toLowerCase()} d-take` }, [
        el('div', { className: 'gate__head d-take__head' }, [
          el('strong', { className: 'gate__name', text: gateName(gate.gate) }),
          el('span', { className: `gate__result tpill tpill--${tone}`, text: t(`drawer.take.${result}`) }),
        ]),
        gate.reason ? el('p', { className: 'gate__reason d-take__text', text: gate.reason }) : null,
        gate.quote ? el('blockquote', { className: 'quote', text: gate.quote }) : null,
      ]);
    });
    const experience = experienceCard(job);
    if (experience) cards.push(experience);
    if (!cards.length) {
      return section(t('drawer.takeHead'), [el('p', { className: 'd-note', text: t('drawer.nothingChecked') })]);
    }
    return section(t('drawer.takeHead'), [el('ul', { className: 'gates d-takes' }, cards)], {
      lede: t('drawer.takeLede'),
    });
  }

  function gateName(gate) {
    return named(`gateName.${gate}`, gate);
  }

  /**
   * The employer's ask for previous experience, with the line it was read
   * from. Silence gets its own sentence: a posting that never mentions
   * experience is not a posting that said none is needed.
   */
  function experienceCard(job) {
    const requirement = job.experience_requirement;
    const signals = job.entry_signals || [];
    if (!requirement && !signals.length) return null;
    const years = job.experience_min_years;
    const sentence = {
      NONE_REQUIRED: t('drawer.experienceNone'),
      REQUIRED_MINIMUM: years === null || years === undefined
        ? t('drawer.experienceUnquantified') : t('drawer.experienceYears', { n: years }),
      REQUIRED_UNQUANTIFIED: t('drawer.experienceUnquantified'),
      PREFERRED: t('drawer.experiencePreferred'),
      NICE_TO_HAVE: t('drawer.experienceBonus'),
    }[requirement] || t('drawer.experienceSilent');
    const tone = requirement === 'NONE_REQUIRED' ? ['m1', 'PASS']
      : (!requirement || requirement === 'NOT_STATED') ? ['chip', 'UNRESOLVED'] : ['m2', 'CHECK'];
    return el('li', { className: 'd-take d-take--experience' }, [
      el('div', { className: 'd-take__head' }, [
        el('strong', { text: t('drawer.experienceHeading') }),
        el('span', { className: `tpill tpill--${tone[0]}`, text: t(`drawer.take.${tone[1]}`) }),
      ]),
      el('p', { className: 'd-take__text', text: sentence }),
      signals.length
        ? el('ul', { className: 'd-list' }, signals.map((value) => el('li', { text: tState('entrySignal', value) })))
        : null,
      job.experience_evidence ? el('blockquote', { className: 'quote', text: job.experience_evidence }) : null,
    ].filter(Boolean));
  }

  // -- what the person thinks of the score (migration 0046) ---------------
  /**
   * Observation only. The answer is kept for this profile, beside the score it
   * judged, and exported on request; nothing that scores, ranks, filters or
   * retrieves reads it, so no number moves because of it. The screen says so.
   */
  function fitFeedbackSection(job) {
    if (job.match_score === null || job.match_score === undefined) return null;
    const given = job.fit_feedback || null;
    const verdict = given ? given.verdict : null;
    const status = el('p', {
      className: 'd-note', attrs: { role: 'status' }, text: given ? t('fitFeedback.saved') : '',
    });
    let note = given ? given.note : null;
    const fail = (err) => {
      status.textContent = t(err && err.status === 409 ? 'fitFeedback.changed' : 'fitFeedback.failed');
    };
    // One save at a time, in order: blurring the note and clicking a verdict
    // fire two saves, and the later answer must land last.
    const save = (next, reason) => {
      const seen = note;
      fitSaves = fitSaves
        .then(() => api.patchFitFeedback(job.job_id, next, reason, seen, job.match_score))
        .then(refreshWith, fail);
    };
    const extra = [];
    if (FIT_REASONS[verdict]) {
      extra.push(select(
        [{ value: '', label: t('fitFeedback.noReason') },
          ...FIT_REASONS[verdict].map((r) => ({ value: r, label: t(`fitFeedback.reason.${verdict}.${r}`) }))],
        given.reason || '',
        (value) => save(verdict, value || null),
        { ariaLabel: t('fitFeedback.reason') },
      ));
    }
    if (verdict) {
      extra.push(el('textarea', {
        className: 'input input--notes',
        attrs: { rows: '2', placeholder: t('fitFeedback.note'), 'aria-label': t('fitFeedback.note') },
        props: { value: given.note || '' },
        on: {
          input: (event) => { note = event.target.value; },
          blur: () => { if ((given.note || '') !== (note || '')) save(verdict, given.reason); },
        },
      }));
    }
    return el('section', { className: 'd-sec d-card d-sec--fit-feedback d-feedback' }, [
      el('div', { className: 'd-feedback__row' }, [
        el('div', { className: 'd-feedback__words' }, [
          el('strong', {
            className: 'd-feedback__title',
            text: verdict ? t('fitFeedback.thanks') : t('fitFeedback.feelRight', { n: Math.round(job.match_score) }),
          }),
          el('span', { className: 'd-feedback__stays', text: t('fitFeedback.stays') }),
        ]),
        el('div', {
          className: 'segmented',
          attrs: { role: 'group', 'aria-label': t('fitFeedback.question') },
        }, FIT_VERDICTS.map((v) => button(t(`fitFeedback.short.${v}`), () => save(v, null), {
          className: 'segmented__btn',
          attrs: { 'aria-pressed': v === verdict ? 'true' : 'false' },
        }))),
      ]),
      ...extra,
      status,
      verdict
        ? button(t('fitFeedback.export'), () => api.exportFitFeedback().catch(fail), { className: 'btn btn--link' })
        : null,
    ].filter(Boolean));
  }

  // -- debug: the arithmetic, item by item (?debug=1) ----------------------
  function scoreBreakdown(job) {
    const components = job.components || [];
    const penalties = job.penalties || [];
    const body = [];
    if (job.title_class || job.title_reason) {
      body.push(el('div', { className: 'd-row d-row--title' }, [
        el('span', { className: 'd-row__label', text: `${humanLabel(job.title_class)}` }),
        el('p', { className: 'd-row__reason', text: job.title_reason || '' }),
      ]));
    }
    for (const component of components) {
      const unconfigured = component.configured === false ? ' component--unconfigured' : '';
      body.push(el('article', { className: `component${unconfigured}` }, [
        el('header', { className: 'component__head' }, [
          el('h4', { className: 'component__label', text: componentName(component) }),
          component.configured === false ? null : el('span', {
            className: 'component__points num',
            text: `${Number(component.points || 0).toFixed(1)} / ${Number(component.max_points || 0).toFixed(1)}`,
          }),
        ]),
        component.note ? el('p', { className: 'component__note', text: component.note }) : null,
        (component.contributions || []).length
          ? el('ul', { className: 'contribs' }, component.contributions.map(contribution))
          : null,
      ]));
    }
    if (penalties.length) {
      body.push(el('h4', { className: 'd-subhead', text: t('drawer.countedAgainst') }));
      body.push(el('ul', { className: 'contribs contribs--penalty' }, penalties.map(contribution)));
    }
    const signals = job.signals || job.technologies || [];
    return el('details', { className: 'd-sec d-advanced' }, [
      el('summary', { className: 'd-advanced__summary', text: t('drawer.advanced') }),
      el('p', { className: 'd-sec__lede', text: t('drawer.advancedHelp') }),
      ...body,
      confidenceSection(job),
      signals.length
        ? el('ul', { className: 'chips chips--tech' }, signals.map((signal) => el('li', {
          className: `chip chip--${String(signal.prominence || 'INCIDENTAL').toLowerCase()}`,
          text: signal.label || humanLabel(signal.signal_id),
          attrs: { title: prominenceWords(signal.prominence) },
        })))
        : null,
      (job.unknowns || []).length
        ? el('ul', { className: 'unknowns' }, job.unknowns.map((line) => el('li', { text: String(line) })))
        : null,
    ].filter(Boolean));
  }

  function contribution(row) {
    const uncounted = row.counted === false;
    return el('li', { className: `contrib${uncounted ? ' contrib--uncounted' : ''}` }, [
      el('div', { className: 'contrib__head' }, [
        el('span', { className: 'contrib__label', text: row.label || humanLabel(row.signal_id) }),
        row.source === 'semantic'
          ? el('span', { className: 'contrib__source', text: t('drawer.semanticFinding') })
          : null,
        el('span', {
          className: 'contrib__prominence',
          text: uncounted ? t('drawer.alreadyCounted') : prominenceWords(row.prominence),
        }),
        el('span', {
          className: `contrib__points num ${Number(row.points) < 0 ? 'is-negative' : ''}`,
          text: formatPoints(row.points),
        }),
      ]),
      row.quote
        ? el('blockquote', { className: 'quote', text: row.quote })
        : el('p', { className: 'quote-absent', text: t('drawer.matchedNoLine') }),
    ]);
  }

  /** The labels the shipped search files give the completeness items. */
  const SHIPPED_CONFIDENCE_LABELS = new Set([
    'Full description text available', 'Description is substantial', 'Location stated',
    'Hiring scope explicitly stated', 'Employment type known', 'Compensation stated',
    'Seniority determinable from the body', 'Posting date known',
  ]);

  function confidenceLabel(item) {
    if (item.label && !SHIPPED_CONFIDENCE_LABELS.has(item.label)) return item.label;
    const key = `confidence.item.${item.item_id}`;
    const translated = t(key);
    return translated !== key ? translated : (item.label || humanLabel(item.item_id));
  }

  function confidenceSection(job) {
    const items = job.confidence_items || [];
    if (!items.length) return null;
    const awarded = items.filter((item) => item.awarded);
    return section(t('drawer.secConfidence'), [
      el('p', { className: 'd-note', text: t('drawer.completenessNotFit') }),
      el('p', { className: 'd-note', text: t('drawer.covered', { awarded: awarded.length, total: items.length }) }),
      el('ul', { className: 'conf' }, items.map((item) => el('li', {
        className: `conf__item${item.awarded ? '' : ' conf__item--missing'}`,
      }, [
        el('span', { className: 'conf__glyph', text: item.awarded ? '✓' : '·', attrs: { 'aria-hidden': 'true' } }),
        el('span', { className: 'conf__label', text: confidenceLabel(item) }),
        el('span', { className: 'conf__points num', text: item.awarded ? `+${item.points}` : `0 / ${item.points}` }),
      ]))),
    ]);
  }

  // ======================================================================
  // BEFORE YOU APPLY: three steps, then "Good to know"
  // ======================================================================

  const readDone = (job) => Boolean(jobMark(PREP_STORE, job.job_id));
  const writeDone = (job, value) => setJobMark(PREP_STORE, job.job_id, value ? 1 : null);

  //: Whether a resume exists for a job, asked once per drawer open.
  const madeFor = new Map();
  // The tailoring step's host, filled by `tailorStep`, kept across repaints.
  const tailorHost = el('div', { className: 'd-tailorhost' });

  function paintPrepare(job) {
    const read = readDone(job);
    const rows = preparation ? preparation.requirements || [] : null;
    const proven = (row) => row.readiness === 'MATCHED';
    // Step 2 marks itself: every requirement the ad names has proof (and an ad
    // naming none has nothing to prove). Step 3 marks itself in the Resume
    // helper, where the resume is made.
    const step2 = rows !== null && rows.every(proven);
    // Step 3 is the Resume helper's answer: a resume made for this job.
    const step3 = madeFor.get(job.job_id) === true;
    if (resumeFor && !madeFor.has(job.job_id)) {
      madeFor.set(job.job_id, null);
      Promise.resolve(resumeFor(job.job_id)).then((made) => {
        madeFor.set(job.job_id, Boolean(made));
        if (made && currentJob && currentJob.job_id === job.job_id) paintPrepare(currentJob);
      });
    }
    const done = [read, step2, step3];
    const count = done.filter(Boolean).length;
    const firstOpen = done.findIndex((value) => !value);
    const ring = (index) => `d-step__ring${done[index] ? ' is-done' : firstOpen === index ? ' is-current' : ''}`;

    const step1 = el('section', { className: 'd-card d-step' }, [
      button(done[0] ? '✓' : '', () => {
        writeDone(job, !read);
        paintPrepare(job);
      }, {
        className: ring(0),
        ariaLabel: t('prep3.markDone'),
        attrs: { role: 'checkbox', 'aria-checked': String(read) },
      }),
      el('div', { className: 'd-step__body' }, [
        el('strong', { className: 'd-step__title', text: t('prep3.s1') }),
        el('span', { className: 'd-step__text', text: t(summaryOf(job) ? 'prep3.s1Text' : 'prep3.s1TextNoShort') }),
        el('div', { className: 'd-step__links' }, [
          button(t(summaryOf(job) ? 'prep3.s1Short' : 'prep3.s1About'), () => {
            writeDone(job, true);
            paintPrepare(job);
            selectTab('details');
          }, { className: 'd-link' }),
          extLink(job.url, `${t('prep3.s1Full')} ↗`, { className: 'd-link d-link--quiet' }),
        ]),
      ]),
    ]);

    const skillRows = rows === null
      ? [el('div', { className: 'sk sk--line' })]
      : rows.length
        ? [el('ul', { className: 'd-skills' }, rows.map((row) => el('li', { className: 'd-skills__row' }, [
          el('span', { className: 'd-skills__name', text: row.label }),
          row.readiness === 'MATCHED'
            ? el('span', { className: 'tpill tpill--m1', text: `✓ ${t('prep3.hasProof')}` })
            : row.readiness === 'PARTIAL'
              ? el('span', { className: 'tpill tpill--blue', text: t('prep3.someProof') })
              : button(`+ ${t('prep3.addProof')}`, () => { if (onEvidence) onEvidence(row); }, {
                className: 'tpill tpill--m2 d-skills__add',
              }),
        ])))]
        : [el('p', { className: 'd-note', text: t('prep3.noSkills') })];

    prepDetail.firstChild.textContent = t('prep3.details');
    const step2Card = el('section', { className: 'd-card d-step' }, [
      el('span', { className: ring(1), text: done[1] ? '✓' : '', attrs: { 'aria-hidden': 'true' } }),
      el('div', { className: 'd-step__body' }, [
        el('strong', { className: 'd-step__title', text: t('prep3.s2') }),
        el('span', { className: 'd-step__text', text: t('prep3.s2Text') }),
        ...skillRows,
        prepDetail,
      ]),
    ]);

    const step3Card = el('section', { className: 'd-card d-step' }, [
      el('span', { className: ring(2), text: done[2] ? '✓' : '', attrs: { 'aria-hidden': 'true' } }),
      el('div', { className: 'd-step__body' }, [
        el('strong', { className: 'd-step__title', text: t('prep3.s3') }),
        el('span', { className: 'd-step__text', text: t('prep3.s3Text') }),
        tailorHost,
      ]),
    ]);
    replace(tailorHost, [tailorStep(job, step3)]);

    replace(preparePanel, [
      el('section', { className: 'd-prephead' }, [
        el('div', { className: 'd-prephead__row' }, [
          el('h3', { className: 'd-prephead__title', text: t('prep3.head') }),
          el('span', {
            className: 'd-prephead__count',
            text: count === 3 ? t('prep3.allDone') : t('prep3.count', { n: count }),
          }),
        ]),
        el('div', { className: 'd-fit__bars', attrs: { 'aria-hidden': 'true' } },
          done.map((value) => el('span', { className: `d-fit__bar${value ? ' is-on' : ''}` }))),
      ]),
      step1,
      step2Card,
      step3Card,
      goodToKnow(job),
    ]);
  }

  /**
   * Step 3's action. The Resume helper builds from the person's own career,
   * so before Career Agent holds anything about it the next step shown is the
   * one that gives it something. The posting travels by id only.
   */
  function tailorStep(job, made) {
    const host = el('div', { className: 'd-tailor', attrs: { role: 'group', 'aria-label': t('tailor.groupLabel') } });
    const make = (className = 'btn btn--primary d-tailor__btn') => button(
      made ? t('prep3.s3Open') : t('prep3.s3Make'),
      () => onTailor && onTailor(job),
      { className, attrs: { id: 'drawer-open-tailor' } },
    );
    const ready = () => {
      replace(host, [make()]);
      host.dataset.tailor = 'ready';
    };
    const needsCareer = () => {
      replace(host, [
        el('p', { className: 'd-step__text', text: t('tailor.needsCv') }),
        el('div', { className: 'd-tailor__actions' }, [
          onAddCareer
            ? button(t('tailor.addCv'), () => onAddCareer(), {
              className: 'btn btn--primary d-tailor__btn', attrs: { id: 'drawer-add-career' },
            })
            : null,
          make('btn d-tailor__btn'),
        ].filter(Boolean)),
      ]);
      host.dataset.tailor = 'needs-career';
    };
    if (!careerContext) {
      ready();
      return host;
    }
    Promise.resolve(careerContext()).then((known) => {
      if (currentJob && currentJob.job_id !== job.job_id) return;
      if (known) ready();
      else needsCareer();
    });
    return host;
  }

  /** Contract, when it was posted, pay, where it came from. */
  function goodToKnow(job) {
    const salary = formatSalary(job.salary);
    const posted = parseDate(job.posted_at);
    const old = posted && (Date.now() - posted.getTime()) / 86400000 > 30;
    const contract = job.employment_relationship && job.employment_relationship !== 'UNKNOWN'
      ? vocabLabel(job.employment_relationship)
      : job.employment_type ? vocabLabel(job.employment_type) : null;
    const rows = [
      [t('gtk.contract'), contract || t('value.notStated'), !contract],
      [t('gtk.posted'), posted
        ? t(old ? 'gtk.postedOld' : 'gtk.postedOn', {
          date: formatDate(job.posted_at), age: relativeAge(job.posted_at),
        })
        : t('card.noPostedDate'), !posted, old],
      [t('gtk.pay'), salary || t('drawer.tile.notShown'), !salary],
      [t('gtk.site'), job.provider ? vocabLabel(job.provider) : t('absent.source'), false],
      [t('gtk.firstSeen'), formatDate(job.first_seen_at), false],
    ];
    return section(t('prep3.good'), [
      el('dl', { className: 'd-gtk' }, rows.flatMap(([key, value, absent, warn]) => [
        el('dt', { text: key }),
        el('dd', { className: `${absent ? 'is-absent' : ''}${warn ? ' is-warn' : ''}`.trim(), text: value }),
      ]).concat([
        el('dt', { text: t('gtk.link') }),
        el('dd', {}, [extLink(job.url, job.url || t('drawer.noUrl'), { className: 'link d-gtk__url' })]),
      ])),
    ], { className: 'd-sec--provenance' });
  }

  // ======================================================================
  // NOTES: your words, where it stands, what happened
  // ======================================================================

  function notesSections(job) {
    return [notesSection(job), whereItStands(job), historySection(job)].filter(Boolean);
  }

  function notesSection(job) {
    const area = el('textarea', {
      className: 'input input--notes d-notes',
      attrs: { id: 'd-notes', rows: '6', placeholder: t('drawer.notesPlaceholder') },
      props: { value: job.notes || '' },
      on: {
        blur: (event) => {
          if ((job.notes || '') !== event.target.value) onNotes(job.job_id, event.target.value);
        },
      },
    });
    return section(t('drawer.notesHead'), [
      el('label', { className: 'sr-only', text: t('drawer.notes'), attrs: { for: 'd-notes' } }),
      area,
      el('span', { className: 'd-note d-note--quiet', text: t('drawer.notesSaved') }),
    ], { lede: t('drawer.notesLede') });
  }

  /** The status, and the applied date with the only control that clears it. */
  function whereItStands(job) {
    const statusSelect = select(statusOptions(), job.application_status, (value) => {
      refreshWith(onStatus(job.job_id, value));
    }, { className: 'select select--status', ariaLabel: t('drawer.applicationStatus') });
    return section(t('drawer.whereYouAre'), [
      el('div', { className: 'drawer__statusrow' }, [statusSelect]),
      appliedLine(job),
    ].filter(Boolean), { className: 'd-sec--status' });
  }

  /**
   * The applied date, and the only control that can destroy it (ADR-0012).
   * Statuses after an application get no button: the server refuses to clear
   * a date it would immediately restore.
   */
  function appliedLine(job) {
    if (!job.applied_at) return null;
    const post = ['APPLIED', 'INTERVIEW', 'OFFER', 'HIRED', 'WITHDRAWN'];
    const locked = post.includes(job.application_status);
    return el('p', { className: 'd-note d-applied' }, [
      el('span', { text: t('drawer.appliedOn', { date: formatDate(job.applied_at) }) }),
      locked
        ? el('span', {
          className: 'd-applied__why',
          text: t('drawer.appliedKept', { status: statusLabel(job.application_status) }),
        })
        : button(t('drawer.clearAppliedDate'), () => {
          const ok = window.confirm(t('drawer.confirmClearApplied', {
            date: formatDate(job.applied_at), title: job.title,
          }));
          if (ok) refreshWith(onClearAppliedAt(job.job_id));
        }, { className: 'btn btn--danger btn--small' }),
    ]);
  }

  function historySection(job) {
    const history = job.history || [];
    return section(t('drawer.historyHead'), history.length
      ? [el('ol', { className: 'history' }, history.map((event) => el('li', { className: 'history__row' }, [
        el('span', { className: 'history__when num', text: formatDate(event.occurred_at) }),
        el('span', {
          className: 'history__what',
          text: event.from_status
            ? t('drawer.statusChange', { from: statusLabel(event.from_status), to: statusLabel(event.to_status) })
            : statusLabel(event.to_status),
        }),
        event.note ? el('span', { className: 'history__note', text: event.note }) : null,
      ])))]
      : [el('p', { className: 'd-note', text: t('drawer.noHistory') })]);
  }

  /** Ask the preparation question again when evidence changes under it. */
  function reloadPreparation() {
    if (!currentJob || loadedPrepareFor !== currentJob.job_id) return;
    prepare.load(currentJob.job_id);
  }

  return {
    relabel, root, open, close, reloadPreparation, showTab: selectTab,
    refresh: (job) => refreshWith(Promise.resolve(job)),
    get job() { return currentJob; },
  };
}

//: storage/fit_feedback.py VERDICTS and REASONS, in the same order.
const FIT_VERDICTS = ['ACCURATE', 'TOO_HIGH', 'TOO_LOW', 'NOT_ENOUGH_INFORMATION'];
const FIT_REASONS = {
  TOO_HIGH: ['WORK_NOT_WANTED', 'TOOLS_NOT_WORK', 'SECONDARY_DUTY', 'ONLY_ASKS_EXPERIENCE',
    'SENIORITY', 'CONDITIONS', 'OTHER'],
  TOO_LOW: ['WORK_MATCHES_MORE', 'WORDING_MISSED', 'CENTRAL_AS_SECONDARY', 'TOOLS_MISSED',
    'SENIORITY_FITS', 'OTHER'],
};

/** The catalogue's words for a key, or the id in plain letters. */
function named(key, fallback) {
  const words = t(key);
  return words === key ? humanLabel(fallback) : words;
}

function summaryOf(job) {
  return Boolean(job.enrichment && String(job.enrichment.summary || '').trim());
}

function section(heading, children, { lede = '', className = '' } = {}) {
  return el('section', { className: `d-sec ${className}`.trim() }, [
    el('div', { className: 'd-sec__heads' }, [
      el('h3', { className: 'd-sec__head', text: heading }),
      lede ? el('p', { className: 'd-sec__lede', text: lede }) : null,
    ]),
    ...(Array.isArray(children) ? children : [children]),
  ]);
}

/** Enrichment values are model output. They are printed, never interpreted. */
function renderValue(value) {
  if (value === null || value === undefined) return t('value.notStated');
  if (Array.isArray(value)) return value.map((item) => renderValue(item)).join('; ');
  if (typeof value === 'object') {
    return Object.entries(value).map(([key, item]) => `${key}: ${renderValue(item)}`).join('; ');
  }
  return String(value);
}
