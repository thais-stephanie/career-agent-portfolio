/**
 * detail.js -- the drawer. Why this number, and what we did not learn.
 *
 * The drawer is where the product has to keep its promise: every point is
 * traceable to a quote, every gate carries its reason, and every confidence
 * item that was NOT awarded is shown alongside the ones that were, because
 * "we could not read this posting" is information, not an empty space.
 *
 * The full description is rendered as escaped text with paragraph breaks. The
 * server text never touches innerHTML; a job description is a string a stranger
 * wrote.
 */

import { el, button, extLink, select, replace, clear, focusables } from './dom.js';
import {
  formatDate, formatPoints, formatSalary, gateTone, humanLabel, paragraphs,
  prominenceWords, relativeAge, statusLabel, statusOptions, vocabLabel,
} from './format.js';
import { badges } from './badges.js';
import { helpNote } from './help.js';
import { createPrepare } from './prepare.js';
import { t, tState } from './i18n.js';
import * as api from './api.js';

/** The phrase components the product names itself (Search Fit v5). */
const PHRASE_COMPONENTS = new Set(['responsibilities', 'technologies', 'automation_integration']);

export function createDrawer({
  onStatus, onSave, onNotes, onClearAppliedAt, onClosed, onChanged,
  getOllama = () => ({}), onEvidence = null,
  careerContext = null, onAddCareer = null,
}) {
  let invoker = null;
  let currentJob = null;
  let requestToken = 0;
  // Set while an enrichment request is in flight. Aborts the request and stops
  // its elapsed-seconds timer, so neither survives a close or a re-open.
  let enrichCleanup = null;

  const detailPanel = el('div', {
    className: 'drawer__tabpanel',
    attrs: { role: 'tabpanel', id: 'drawer-panel-details', 'aria-labelledby': 'drawer-tab-details' },
  });
  const whyPanel = el('div', {
    className: 'drawer__tabpanel',
    attrs: { role: 'tabpanel', id: 'drawer-panel-why', 'aria-labelledby': 'drawer-tab-why' },
    props: { hidden: true },
  });
  const preparePanel = el('div', {
    className: 'drawer__tabpanel',
    attrs: {
      role: 'tabpanel',
      id: 'drawer-panel-prepare',
      'aria-labelledby': 'drawer-tab-prepare',
    },
    props: { hidden: true },
  });

  // Its own module, and its own request. The first two tabs are rendered from
  // the job payload that is already loaded; preparation is a separate question
  // with a separate answer, and asking it on every drawer open would make
  // reading a posting cost a second query nobody asked for.
  const prepare = createPrepare({ onEvidence });
  preparePanel.append(prepare.host);

  const TABS = [
    // `Job details` and `Why this matches`, which are the names the product
    // specification gives these two. Both were shorter before ("The job",
    // "Why it matched") and both meant the same thing; the specification
    // names them, so they are named that.
    //
    // THREE, AND THE THIRD IS NOT A FOURTH VIEW OF THE SCORE. It answers the
    // question the other two cannot: which of these requirements could I
    // speak to, and which could I not. Eight tabs, one per section of the
    // V1.3 brief, would have put each answer behind a guess about which tab
    // holds it; three keeps the drawer readable and the sections are headings
    // inside them.
    //
    // The label is a KEY rather than a string. `createDrawer` runs during
    // module setup, before `setLocale` has read the stored choice, so a
    // resolved string here would freeze the drawer in English -- which is
    // exactly what happened to these two tabs until the third one made it
    // visible. `relabel()` below resolves them, once at start and again on
    // every language switch.
    { key: 'details', labelKey: 'drawer.tab.details', panel: detailPanel },
    { key: 'why', labelKey: 'drawer.tab.why', panel: whyPanel },
    { key: 'prepare', labelKey: 'drawer.tab.prepare', panel: preparePanel },
  ];

  // The chosen tab survives while the drawer is open, including across the
  // re-render a status change triggers. It resets on close, because the next
  // job is a new question and "the job" is the right answer to open on.
  let activeTab = 'details';
  // Which job the Prepare tab currently holds, so re-rendering the drawer
  // after a status change does not re-ask the server for the same answer.
  let loadedPrepareFor = null;

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

  /**
   * Put the current language on the tabs.
   *
   * Called once after the locale is resolved and again whenever it changes.
   * Only OUR words move: the panels below hold a job title, a description and
   * evidence quotes, and none of those is touched in either direction.
   */
  function relabel() {
    TABS.forEach((tab, index) => {
      tabButtons[index].textContent = t(tab.labelKey);
    });
    tabList.setAttribute('aria-label', t('drawer.tabs.label'));
    // The drawer's shell is built ONCE, at module setup, which happens before
    // `setLocale` has read the stored choice. Its tabs were relabelled here
    // and its close button was not, so the one word on the drawer that is not
    // a tab stayed English on a Portuguese page.
    closeButton.textContent = `${t('action.close')} ✕`;
    closeButton.setAttribute('aria-label', t('drawer.close'));
  }

  /**
   * Roving tabindex, which is what the tab pattern requires and what a plain
   * row of buttons does not give: one stop in the tab order for the whole set,
   * and the arrow keys to move inside it.
   */
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
    activeTab = key;
    // Loaded on demand. Opening a posting to read it must not cost a second
    // request, and most opens never reach this tab.
    if (key === 'prepare' && currentJob && loadedPrepareFor !== currentJob.job_id) {
      loadedPrepareFor = currentJob.job_id;
      prepare.load(currentJob.job_id);
    }
    TABS.forEach((tab, index) => {
      const chosen = tab.key === key;
      tabButtons[index].setAttribute('aria-selected', String(chosen));
      tabButtons[index].setAttribute('tabindex', chosen ? '0' : '-1');
      tab.panel.hidden = !chosen;
    });
  }

  const bodyHost = el('div', { className: 'drawer__body' }, [tabList, detailPanel, whyPanel, preparePanel]);
  const titleNode = el('h2', { className: 'drawer__title', attrs: { id: 'drawer-title' }, text: '' });

  /** Paint both panels. Both are always built; only one is visible. */
  function paint(job) {
    replace(detailPanel, detailSections(job));
    replace(whyPanel, whySections(job));
    if (!bodyHost.contains(tabList)) {
      replace(bodyHost, [tabList, detailPanel, whyPanel, preparePanel]);
    }
    selectTab(activeTab);
  }

  const closeButton = button('', () => close(), { className: 'btn btn--close' });
  // Its words, straight away. `relabel` is declared above and closes over a
  // `const` that does not exist until this line, so the call belongs here
  // rather than beside the function.
  relabel();

  const panel = el('div', {
    className: 'drawer__panel',
    attrs: {
      role: 'dialog',
      'aria-modal': 'true',
      'aria-labelledby': 'drawer-title',
      tabindex: '-1',
    },
  }, [
    el('div', { className: 'drawer__head' }, [
      titleNode,
      closeButton,
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

  async function open(jobId, invokedBy) {
    if (enrichCleanup) enrichCleanup();
    invoker = invokedBy || document.activeElement;
    root.hidden = false;
    document.body.classList.add('has-drawer');
    titleNode.textContent = t('drawer.loading');
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
    prepare.reset();
    loadedPrepareFor = null;
    currentJob = null;
    if (invoker && document.contains(invoker)) invoker.focus();
    invoker = null;
    if (onClosed) onClosed();
  }

  /**
   * TWO TABS, AND THE ORDER INSIDE THEM IS THE ARGUMENT.
   *
   * The drawer used to be one column of thirteen sections that opened on the
   * score breakdown: a list of component names, point totals and raw signal
   * ids. That is the most precise thing this product knows and the worst
   * possible first screen, because it answers "how was this computed" to
   * somebody who has not yet been told "what is this job".
   *
   * So: the facts of the job in one tab, the reasoning in the other. The
   * reasoning tab leads with a sentence in English, then the reasons, then the
   * gaps, and only then the arithmetic, which is folded away behind a
   * disclosure that says it is the advanced view. Nothing was deleted.
   */
  /**
   * The handoff to Resume Tailor, offered only as far as it can be used.
   *
   * Tailor builds a resume from the person's own CV. Before Career Agent holds
   * anything about their career, "Open Resume Tailor" looked like the next
   * step and led to a tool with nothing to work from, so the next step shown
   * is the one that gives it something. Tailor keeps its own store, which is
   * never read from here: somebody who already gave it a CV there still has
   * the link, one line down.
   */
  function tailorSection(job) {
    // ONE ACTION GROUP. The copy button, the link out and the note used to be
    // three loose siblings of different sizes; they are one labelled group
    // now, with the controls on a single row that wraps on a narrow drawer.
    // The heading names the group for a screen reader as well as on screen.
    const host = el('section', {
      className: 'd-sec d-tailor',
      attrs: { role: 'group', 'aria-labelledby': 'd-tailor-head' },
    });
    const heading = () => el('h3', {
      className: 'd-sec__head', text: t('tailor.groupLabel'), attrs: { id: 'd-tailor-head' },
    });
    host.appendChild(heading());
    const link = (text, className) => el('a', { className, text, attrs: {
      href: '/resume-tailor', target: '_blank', rel: 'noopener noreferrer', id: 'drawer-open-tailor',
    } });
    const actions = (children) => el('div', { className: 'd-tailor__actions' }, children.filter(Boolean));
    const ready = () => {
      const note = el('p', {
        className: 'd-tailor__note', text: t('tailor.note'), attrs: { 'aria-live': 'polite' },
      });
      const copy = button(t('tailor.copy'), async () => {
        try {
          await navigator.clipboard.writeText(job.description || job.description_excerpt || '');
          note.textContent = t('tailor.copied');
        } catch {
          note.textContent = t('tailor.unavailable');
        }
      }, { className: 'btn d-tailor__btn' });
      replace(host, [
        heading(),
        actions([copy, link(t('tailor.open'), 'btn btn--primary d-tailor__btn')]),
        note,
      ]);
      host.dataset.tailor = 'ready';
    };
    const needsCareer = () => {
      replace(host, [
        heading(),
        el('p', { className: 'd-tailor__note', text: t('tailor.needsCv') }),
        actions([
          onAddCareer
            ? button(t('tailor.addCv'), () => onAddCareer(), {
              className: 'btn btn--primary d-tailor__btn', attrs: { id: 'drawer-add-career' },
            })
            : null,
          link(t('tailor.openOwn'), 'btn d-tailor__btn'),
        ]),
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

  function detailSections(job) {
    return [
      identity(job),
      compensationSection(job),
      tailorSection(job),
      descriptionSection(job),
      duplicatesSection(job),
      notesSection(job),
      historySection(job),
      enrichmentSection(job),
      provenanceSection(job),
    ].filter(Boolean);
  }

  function whySections(job) {
    return [
      whyOverview(job),
      // WHAT THIS POSTING ASKED OF SOMEBODY STARTING OUT, before the strengths
      // and before the gaps. High up on purpose: for a career changer, a new
      // graduate or anybody looking for a first job, it is the first thing they
      // need and the one sentence the product used not to say at all.
      experienceSection(job),
      strengthsSection(job),
      signalsSection(job),
      gapsSection(job),
      gatesSection(job),
      unknownsSection(job),
      scoreBreakdown(job),
    ].filter(Boolean);
  }

  // -- what this posting asked of a newcomer (migration 0027) ------------
  /**
   * The employer's ask for previous experience, in a sentence, with the line it
   * was read from.
   *
   * WHY IT IS A SECTION AND NOT A BADGE
   * -----------------------------------
   * Because the honest answer is usually several clauses. "This role asks for
   * at least three years" and "these are the things it says to people starting
   * out" and "here is the line it said that in" do not fit on a chip, and a
   * chip reading `REQUIRED_MINIMUM` would be this product's vocabulary on
   * screen -- the thing `test_plain_language.py` exists to fail.
   *
   * SILENCE GETS ITS OWN SENTENCE, and that is the load-bearing part. A
   * posting that never mentions experience is not a posting that said none is
   * needed, and rendering the two the same way would manufacture an invitation
   * nobody extended. The wording says which one this is.
   *
   * NOTHING HERE IS A SCORE. No number on this card moves because of what this
   * section says; no gate reads it. It is evidence about the POSTING, in the
   * same family as the employment and domestic readings beside it.
   */
  function experienceSection(job) {
    const requirement = job.experience_requirement;
    const signals = job.entry_signals || [];
    if (!requirement && !signals.length) return null;

    const years = job.experience_min_years;
    const sentence = {
      NONE_REQUIRED: () => t('drawer.experienceNone'),
      REQUIRED_MINIMUM: () => (years === null || years === undefined
        ? t('drawer.experienceUnquantified')
        : t('drawer.experienceYears', { n: years })),
      REQUIRED_UNQUANTIFIED: () => t('drawer.experienceUnquantified'),
      PREFERRED: () => t('drawer.experiencePreferred'),
      NICE_TO_HAVE: () => t('drawer.experienceBonus'),
      NOT_STATED: () => t('drawer.experienceSilent'),
    }[requirement];

    return section(t('drawer.experienceHeading'), [
      el('p', { className: 'd-note', text: sentence ? sentence() : t('drawer.experienceSilent') }),
      // The invitations, each as the employer's own kind of statement rather
      // than as one merged claim. "Entry level" and "training provided" are
      // two different promises and a reader weighs them differently.
      signals.length
        ? el('ul', { className: 'd-list' }, signals.map((value) => el('li', {
          text: tState('entrySignal', value),
        })))
        : null,
      // ADR-0002: a quote is evidence only while it is a contiguous substring
      // of what the employer wrote, which is why this is the posting's own
      // line and never a sentence this product composed about it.
      job.experience_evidence
        ? el('blockquote', { className: 'quote', text: job.experience_evidence })
        : null,
    ].filter(Boolean));
  }

  // -- tab 2, lead paragraph ---------------------------------------------

  /**
   * The number, and then the same number as a sentence.
   *
   * A percentage with no sentence beside it invites the reading this product
   * spends most of its design refusing: that it is a probability of being
   * hired. It is not. It is how much of what you said you wanted appears in
   * the text of this posting, and the sentence says so.
   */
  function whyOverview(job) {
    const scored = job.match_score !== null && job.match_score !== undefined;
    if (!scored) {
      return section(t('drawer.whySection'), [
        el('p', {
          className: 'd-note',
          text: t('drawer.unscored'),
        }),
      ]);
    }

    const score = Math.round(job.match_score);
    const detail = job.data_confidence === null || job.data_confidence === undefined
      ? null : Math.round(job.data_confidence);
    const strengths = (job.top_strengths || []).length;
    const blockers = (job.blockers || []).length;

    // The BAND the score was given, never a second set of cut-offs. This
    // sentence used 70/50/30 while every band used 75/55/35, so a 72 was
    // called a strong fit beside a badge that said GOOD.
    const howClose = {
      STRONG: t('drawer.closeStrong'),
      GOOD: t('drawer.closeReasonable'),
      MODERATE: t('drawer.closePartial'),
    }[job.fit_band] || t('drawer.closeWeak');

    const parts = [t('drawer.scoredOutOf', { score, howClose })];
    // Counted from the list that is actually drawn, not from `top_strengths`,
    // which the API caps at three. The sentence said "It picked up 3 things
    // you are looking for, quoted below" above a list of eight.
    const shown = strengthRows(job).length;
    if (shown) {
      parts.push(shown === 1
        ? t('drawer.pickedUpOne')
        : t('drawer.pickedUp', { n: shown }));
    }
    if (detail !== null) {
      const readable = detail >= 70 ? t('drawer.readableHigh')
        : detail >= 40 ? t('drawer.readableMid')
          : t('drawer.readableLow');
      parts.push(readable);
    }
    if (blockers) {
      parts.push(t('drawer.wouldRuleOut'));
    }
    if (job.semantic) {
      parts.push(t('drawer.semanticUsed', { provider: t(`ai.mode.${job.semantic.provider}`) }));
    }

    return section(t('drawer.secWhyMatches'), [
      el('div', { className: 'why__head' }, [
        badges(job, { size: 'large' }),
        // The explanation, beside the numbers rather than in a block at the
        // top of a different screen. All three at once, because ADR-0004's
        // whole point is that they are three separate measurements and
        // explaining one alone is how a reader decides it qualifies another.
        helpNote(t('help.theseNumbers'), [
          'badge.matchHelp',
          'badge.confidenceHelp',
          'help.eligibility',
        ]),
      ]),
      el('p', { className: 'why__summary', text: parts.join(' ') }),
    ]);
  }

  /**
   * The rows the reasoning tab draws, capped, so the sentence above them can
   * count the same thing the reader counts.
   */
  function strengthRows(job) {
    const rows = [];
    for (const component of job.components || []) {
      for (const contrib of component.contributions || []) {
        if (Number(contrib.points) > 0 && contrib.counted !== false) rows.push(contrib);
      }
    }
    rows.sort((a, b) => Number(b.points) - Number(a.points));
    return rows.slice(0, 8);
  }

  /** The reasons, as words and quotes, before any arithmetic. */
  function strengthsSection(job) {
    const rows = strengthRows(job);
    if (!rows.length) return null;

    // "Each line is quoted from the posting itself. Nothing here is inferred"
    // sat above three lines reading "The posting matched, without a quotable
    // line." Three of three contradicted it. The claim is now made about the
    // lines that ARE quotes, and only when there are any, because this
    // product's whole promise is that evidence is a quote that exists.
    const quoted = rows.filter((row) => row.quote).length;
    const lede = quoted === rows.length
      ? t('drawer.allQuoted')
      : quoted > 0
        ? t('drawer.someQuoted', { n: quoted })
        : t('drawer.noneQuoted');

    return section(t('drawer.secStrengths'), [
      el('ul', { className: 'reasons' }, rows.map((row) => el('li', {
        className: 'reason',
      }, [
        el('p', { className: 'reason__label', text: row.label || humanLabel(row.signal_id) }),
        row.quote
          ? el('blockquote', { className: 'quote', text: row.quote })
          : el('p', {
            className: 'quote-absent',
            text: t('drawer.matchedNoQuote'),
          }),
      ]))),
    ], { lede });
  }

  /** What is missing or unclear, which is a different thing from a blocker. */
  function gapsSection(job) {
    const missing = (job.confidence_items || []).filter((item) => !item.awarded);
    if (!missing.length) return null;
    return section(t('drawer.secGaps'), [
      el('ul', { className: 'gaps' }, missing.map((item) => el('li', {
        className: 'gap',
        text: item.note || item.label || humanLabel(item.item_id),
      }))),
    ], {
      lede: t('drawer.gapsLede'),
    });
  }

  // -- header -----------------------------------------------------------
  function identity(job) {
    const statusSelect = select(statusOptions(), job.application_status, (value) => {
      // Re-rendered from the SERVER's answer, not from the value just chosen.
      // The applied line below reads both the date and the status -- whether
      // the date exists, and whether it may be cleared at this stage -- so a
      // status change that repainted nothing left a control on screen that
      // described the previous state.
      refreshWith(onStatus(job.job_id, value));
    }, { className: 'select select--status', ariaLabel: t('drawer.applicationStatus') });

    return el('section', { className: 'd-sec d-sec--identity' }, [
      el('p', { className: 'd-company', text: job.company_name || t('absent.companyStated') }),
      badges(job, { size: 'md' }),
      el('div', { className: 'd-actions' }, [
        statusSelect,
        button(job.saved ? t('drawer.saved') : t('drawer.save'),
          () => onSave(job.job_id, !job.saved), {
          className: `btn btn--save${job.saved ? ' is-on' : ''}`,
          attrs: { 'aria-pressed': job.saved ? 'true' : 'false' },
        }),
        extLink(job.url, `${t('card.apply')} ↗︎`, { className: 'btn btn--link' }),
      ]),
      appliedLine(job),
    ]);
  }

  /**
   * Redraw the drawer from whatever the mutation resolved to.
   *
   * The handlers in `main.js` return the server's updated job -- the same full
   * detail shape `open()` fetches -- so the drawer can repaint from the
   * response instead of issuing a second GET for a row it was just handed.
   *
   * Tolerant of a handler that resolves to nothing, because that is what every
   * one of them did before the applied line needed this, and a drawer that
   * throws on an undefined is worse than a drawer that leaves itself alone.
   */
  function refreshWith(result) {
    return Promise.resolve(result).then((job) => {
      if (!job || !job.job_id) return;
      if (!currentJob || currentJob.job_id !== job.job_id) return;
      currentJob = job;
      paint(job);
    });
  }

  /**
   * The applied date, and the only control that can destroy it.
   *
   * The drawer is where this belongs rather than the card or the row. Clearing
   * the date is rare, irreversible and destroys a fact about the past, so it
   * should cost a deliberate visit, while a status move, which is frequent and
   * reversible, costs one drag and no longer touches the date at all
   * (ADR-0012).
   *
   * `POST_APPLICATION` statuses get no button. The server refuses to clear a
   * date it would immediately restore, and offering a control that always
   * fails is worse than not offering one; the line says why instead.
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
          // The confirmation is the point of the control, not decoration
          // around it. This is the one action in the interface that deletes
          // something a person cannot recover from anywhere else.
          const ok = window.confirm(t('drawer.confirmClearApplied', {
            date: formatDate(job.applied_at),
            title: job.title,
          }));
          if (ok) refreshWith(onClearAppliedAt(job.job_id));
        }, { className: 'btn btn--danger btn--small' }),
    ]);
  }

  // -- the score, component by component ---------------------------------
  function scoreBreakdown(job) {
    const components = job.components || [];
    const penalties = job.penalties || [];
    const body = [];

    if (job.match_score === null || job.match_score === undefined) {
      body.push(el('p', {
        className: 'd-note',
        text: t('drawer.unscoredNumber'),
      }));
    }

    if (job.title_class || job.title_reason) {
      body.push(el('div', { className: 'd-row d-row--title' }, [
        el('span', { className: 'd-row__label', text: `The job title reads as ${humanLabel(job.title_class)}` }),
        el('p', { className: 'd-row__reason', text: job.title_reason || '' }),
      ]));
    }

    for (const component of components) {
      if (component.configured === false) {
        // Not part of this search at all: no phrases were configured here.
        // Different from a configured component the posting did not match,
        // which keeps its 0 of max below.
        body.push(el('article', { className: 'component component--unconfigured' }, [
          el('header', { className: 'component__head' }, [
            el('h4', { className: 'component__label', text: componentLabel(component) }),
          ]),
          el('p', { className: 'component__note', text: t('drawer.notConfigured') }),
        ]));
        continue;
      }
      body.push(el('article', { className: 'component' }, [
        el('header', { className: 'component__head' }, [
          el('h4', { className: 'component__label', text: componentLabel(component) }),
          el('span', {
            className: 'component__points num',
            text: `${Number(component.points || 0).toFixed(1)} / ${Number(component.max_points || 0).toFixed(1)}`,
          }),
        ]),
        component.capped
          ? el('p', {
            className: 'component__note',
            text: t('drawer.cappedAt', {
              points: Number(component.max_points).toFixed(1),
            }),
          })
          : null,
        component.guarded
          ? el('p', { className: 'component__note', text: t('drawer.toolsGuard') })
          : component.component_id === 'seniority' && component.note
            ? el('p', { className: 'component__note', text: t('absent.levelSentence') })
            : component.note ? el('p', { className: 'component__note', text: component.note }) : null,
        (component.contributions || []).length
          ? el('ul', { className: 'contribs' }, component.contributions.map(contribution))
          : el('p', { className: 'component__note', text: t('drawer.nothingMatchedHere') }),
      ]));
    }

    if (penalties.length) {
      body.push(el('h4', { className: 'd-subhead', text: t('drawer.countedAgainst') }));
      body.push(el('ul', { className: 'contribs contribs--penalty' }, penalties.map(contribution)));
    }

    if (!components.length && !penalties.length) {
      body.push(el('p', { className: 'd-note', text: t('drawer.noBreakdown') }));
    }

    // Folded away, and labelled as the advanced view. Every number this
    // product computes is still here and still traceable to a quote; it is
    // simply no longer the first thing a person meets.
    return el('details', { className: 'd-sec d-advanced' }, [
      el('summary', { className: 'd-advanced__summary', text: t('drawer.advanced') }),
      el('p', {
        className: 'd-sec__lede',
        text: t('drawer.advancedHelp'),
      }),
      ...body,
      // The confidence items, item by item, with the unawarded ones shown
      // beside the awarded ones. This lives here rather than in the reasoning
      // tab's own flow because it is the numeric half: `gapsSection` above
      // already says in words what the posting left out.
      confidenceSection(job),
    ]);
  }

  function componentLabel(component) {
    // The three phrase components are named by the product, in the reader's
    // language, whatever an older configuration file called them.
    const named = PHRASE_COMPONENTS.has(component.component_id)
      ? t(`component.${component.component_id}`) : '';
    return named || component.label || humanLabel(component.component_id);
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

  // -- gates -------------------------------------------------------------
  function gatesSection(job) {
    const gates = job.gates || [];
    if (!gates.length) {
      return section(t('drawer.secGates'), [
        el('p', { className: 'd-note', text: t('drawer.nothingChecked') }),
      ]);
    }
    return section(t('drawer.secGates'), [
      el('ul', { className: 'gates' }, gates.map((gate) => el('li', {
        className: `gate gate--${gateTone(gate.result)}`,
      }, [
        el('div', { className: 'gate__head' }, [
          el('span', {
            className: 'gate__glyph',
            text: gate.result === 'PASS' ? '✓' : gate.result === 'FAIL' ? '✕' : '?',
            attrs: { 'aria-hidden': 'true' },
          }),
          el('span', { className: 'gate__name', text: humanLabel(gate.gate) }),
          el('span', { className: 'gate__result', text: gateWords(gate.result) }),
        ]),
        el('p', { className: 'gate__reason', text: gate.reason || '' }),
        gate.quote ? el('blockquote', { className: 'quote', text: gate.quote }) : null,
      ])))],
    {
      lede: t('drawer.gatesLede'),
    });
  }

  // -- confidence --------------------------------------------------------
  /** A completeness row's words, in the reader's language when the catalogue
   *  knows the item; the configuration's own label otherwise. */
  function confidenceLabel(item) {
    const key = `confidence.item.${item.item_id}`;
    const translated = t(key);
    return translated !== key ? translated : (item.label || humanLabel(item.item_id));
  }

  function confidenceNote(item) {
    const key = `confidence.note.${item.item_id}.${item.awarded ? 'yes' : 'no'}`;
    const translated = t(key);
    return translated !== key ? translated : (item.note || '');
  }

  function confidenceSection(job) {
    const items = job.confidence_items || [];
    if (!items.length) return null;
    const awarded = items.filter((item) => item.awarded);
    const missing = items.filter((item) => !item.awarded);
    return section(t('drawer.secConfidence'), [
      // Completeness is about the POSTING, never about the person: say so
      // before the first "0 / 10", so an omission is not read as a bad fit.
      el('p', { className: 'd-note', text: t('drawer.completenessNotFit') }),
      el('p', {
        className: 'd-note',
        text: t('drawer.covered', { awarded: awarded.length, total: items.length }),
      }),
      el('ul', { className: 'conf' }, items.map((item) => el('li', {
        className: `conf__item${item.awarded ? '' : ' conf__item--missing'}`,
      }, [
        el('span', { className: 'conf__glyph', text: item.awarded ? '✓' : '·', attrs: { 'aria-hidden': 'true' } }),
        el('span', { className: 'conf__label', text: confidenceLabel(item) }),
        el('span', { className: 'conf__points num', text: item.awarded ? `+${item.points}` : `0 / ${item.points}` }),
        confidenceNote(item) ? el('span', { className: 'conf__note', text: confidenceNote(item) }) : null,
      ]))),
      missing.length
        ? el('p', {
          className: 'd-note',
          text: t('drawer.whyNotHigher'),
        })
        : null,
    ]);
  }

  function unknownsSection(job) {
    const unknowns = job.unknowns || [];
    if (!unknowns.length) return null;
    return section(t('drawer.secUnknowns'), [
      el('ul', { className: 'unknowns' }, unknowns.map((line) => el('li', { text: String(line) }))),
    ]);
  }

  // -- signals -----------------------------------------------------------
  function signalsSection(job) {
    const signals = job.signals || job.technologies || [];
    if (!signals.length) return null;
    return section(t('drawer.secSignals'), [
      el('ul', { className: 'chips chips--tech' }, signals.map((signal) => el('li', {
        className: `chip chip--${String(signal.prominence || 'INCIDENTAL').toLowerCase()}`,
        text: signal.label || humanLabel(signal.signal_id),
        attrs: { title: prominenceWords(signal.prominence) },
      }))),
    ]);
  }

  function compensationSection(job) {
    const salary = formatSalary(job.salary);
    return section(t('drawer.secPay'), [
      el('dl', { className: 'kv' }, [
        el('dt', { text: t('drawer.salary') }),
        el('dd', {
          className: salary ? '' : 'fact--absent',
          text: salary || t('drawer.salaryUnstated'),
        }),
        el('dt', { text: t('drawer.employmentType') }),
        el('dd', {
          className: job.employment_type ? '' : 'fact--absent',
          text: job.employment_type ? vocabLabel(job.employment_type) : t('drawer.notStated'),
        }),
        el('dt', { text: t('drawer.worksite') }),
        el('dd', { text: job.work_model ? vocabLabel(job.work_model) : t('drawer.notStated') }),
        el('dt', { text: t('drawer.seniority') }),
        // Two different sentences, and the difference is the point. A stated
        // level is reported with the words that stated it; an unstated one
        // says so, and says what is being assumed instead of it.
        el('dd', {
          className: job.seniority_stated ? '' : 'fact--absent',
        }, job.seniority_stated
          ? [
            el('span', { text: vocabLabel(job.seniority) }),
            job.seniority_evidence
              ? el('blockquote', { className: 'quote quote--tight', text: job.seniority_evidence })
              : null,
          ].filter(Boolean)
          : [el('span', { text: t('absent.levelSentence') })]),
      ]),
    ]);
  }

  // -- description -------------------------------------------------------
  function descriptionSection(job) {
    const blocks = paragraphs(job.description || job.description_excerpt || '');
    return section(t('drawer.secDescription'), blocks.length
      ? blocks.map((block) => el('p', { className: 'desc__p', text: block }))
      : [el('p', { className: 'd-note', text: t('drawer.noDescription') })],
    { className: 'd-sec--desc' });
  }

  // -- notes -------------------------------------------------------------
  function notesSection(job) {
    const area = el('textarea', {
      className: 'input input--notes',
      attrs: { id: 'd-notes', rows: '4', placeholder: t('drawer.notesPlaceholder') },
      props: { value: job.notes || '' },
      on: {
        blur: (event) => {
          if ((job.notes || '') !== event.target.value) onNotes(job.job_id, event.target.value);
        },
      },
    });
    return section(t('drawer.notes'), [
      el('label', { className: 'sr-only', text: t('drawer.notes'), attrs: { for: 'd-notes' } }),
      area,
    ]);
  }

  function historySection(job) {
    const history = job.history || [];
    if (!history.length) return null;
    return section(t('drawer.secHistory'), [
      el('ol', { className: 'history' }, history.map((event) => el('li', { className: 'history__row' }, [
        el('span', { className: 'history__when num', text: formatDate(event.occurred_at) }),
        el('span', {
          className: 'history__what',
          text: event.from_status
            ? t('drawer.statusChange', {
              from: statusLabel(event.from_status),
              to: statusLabel(event.to_status),
            })
            : statusLabel(event.to_status),
        }),
        event.note ? el('span', { className: 'history__note', text: event.note }) : null,
      ]))),
    ]);
  }

  // -- local model -------------------------------------------------------

  /**
   * `health.ollama.reachable` is three-valued and the three states mean
   * different things. `null` is "we have not asked": a page load never opens a
   * socket, not even to localhost, and it must not be rendered as "not
   * running". Only `false` is a checked, failed contact.
   */
  function enrichHint(ollama) {
    const endpoint = ollama.endpoint || t('drawer.configuredEndpoint');
    if (ollama.configured === false) {
      return t('drawer.noLocalModel');
    }
    if (ollama.reachable === false) {
      return t('drawer.localModelSilent', { endpoint });
    }
    if (ollama.reachable === true) {
      return t('drawer.localModelReady', {
        model: ollama.model || t('drawer.theLocalModel'),
        endpoint,
      });
    }
    return t('drawer.localModelUntried');
  }

  function enrichmentSection(job) {
    const ollama = getOllama() || {};
    const unavailable = ollama.reachable === false || ollama.configured === false;

    const enrichment = job.enrichment && Object.keys(job.enrichment).length ? job.enrichment : null;
    const messageHost = el('p', { className: 'enrich__msg', attrs: { 'aria-live': 'polite' } });
    // Updated every second, so it is hidden from the accessibility tree: the
    // polite message beside it carries the state a screen reader needs.
    const elapsedHost = el('span', { className: 'enrich__elapsed', attrs: { 'aria-hidden': 'true' } });

    const hint = el('p', {
      className: `enrich__hint${unavailable ? ' enrich__hint--blocked' : ''}`,
      attrs: { id: 'enrich-hint' },
      text: enrichHint(ollama),
    });

    let controller = null;
    let timer = null;

    const cancelButton = button(t('action.cancel'), () => {
      if (controller) controller.abort();
    }, { className: 'btn', ariaLabel: t('drawer.cancelLocalModel') });
    cancelButton.hidden = true;

    function stopTimer() {
      if (timer) clearInterval(timer);
      timer = null;
      controller = null;
      enrichCleanup = null;
      cancelButton.hidden = true;
      elapsedHost.textContent = '';
    }

    const runButton = button(t('drawer.askLocalModel'), async () => {
      controller = new AbortController();
      const started = Date.now();
      const seconds = () => Math.round((Date.now() - started) / 1000);

      runButton.disabled = true;
      cancelButton.hidden = false;
      messageHost.className = 'enrich__msg';
      messageHost.textContent = t('drawer.asking', {
        model: ollama.model || t('drawer.theLocalModelLower'),
      });
      elapsedHost.textContent = '0s';
      timer = setInterval(() => { elapsedHost.textContent = `${seconds()}s`; }, 1000);
      enrichCleanup = () => {
        if (controller) controller.abort();
        stopTimer();
      };

      try {
        const updated = await api.enrichJob(job.job_id, controller.signal);
        const took = seconds();
        stopTimer();
        messageHost.textContent = t('drawer.doneIn', { seconds: took });
        if (onChanged) onChanged(updated);
        open(job.job_id, invoker);
      } catch (error) {
        stopTimer();
        // `expected` covers the two normal outcomes: the model is not running
        // (503) and the person pressed Cancel. Neither is a fault, so neither
        // is phrased or logged as one.
        messageHost.className = 'enrich__msg enrich__msg--calm';
        messageHost.textContent = error.expected
          ? (error.userMessage || error.message)
          : t('drawer.localModelFailed', { error: error.userMessage || error.message });
        runButton.disabled = false;
      }
    }, { className: 'btn', attrs: { 'aria-describedby': 'enrich-hint' } });

    if (unavailable) runButton.disabled = true;

    const body = [
      el('p', {
        className: 'enrich__banner',
        text: t('drawer.localModelNote'),
      }),
      enrichment
        ? el('dl', { className: 'kv kv--enrich' }, Object.entries(enrichment).flatMap(([key, value]) => [
          el('dt', { text: humanLabel(key) }),
          el('dd', { text: renderValue(value) }),
        ]))
        : el('p', { className: 'd-note', text: t('drawer.localModelIdle') }),
      el('div', { className: 'enrich__actions' }, [runButton, cancelButton, elapsedHost, messageHost]),
      hint,
    ];

    return section(t('drawer.secEnrich'), body, { className: 'd-sec--enrich' });
  }

  /**
   * The other postings of this same role, if there are any.
   *
   * Shown whether or not the list that led here was grouped, because the fact
   * is about the posting and not about the list. It is also the honest answer
   * to the question a grouped card provokes -- "eight locations, but which?"
   * -- and to the one an ungrouped table provokes, which is why the same
   * employer and title is on screen eight times.
   */
  function duplicatesSection(job) {
    const count = Number(job.duplicate_count || 1);
    if (count <= 1) return null;
    const places = job.sibling_locations || [];
    const hidden = count - places.length;
    return section(t('drawer.secPlaces'), [
      el('p', {
        className: 'd-sec__lede',
        text: t('drawer.placesLede', { company: job.company_name, n: count }),
      }),
      el('ul', { className: 'd-places' }, [
        ...places.map((place) => el('li', { className: 'd-places__item', text: place })),
        hidden > 0
          ? el('li', {
            className: 'd-places__item d-places__item--more',
            text: t('drawer.morePlaces', { n: hidden }),
          })
          : null,
      ].filter(Boolean)),
    ]);
  }

  function provenanceSection(job) {
    return section(t('drawer.secProvenance'), [
      el('dl', { className: 'kv' }, [
        el('dt', { text: t('drawer.jobBoard') }),
        el('dd', { text: vocabLabel(job.provider) }),
        el('dt', { text: t('drawer.postedOn') }),
        el('dd', { text: `${formatDate(job.posted_at)} (${relativeAge(job.posted_at)})` }),
        el('dt', { text: t('drawer.firstSeen') }),
        el('dd', { text: formatDate(job.first_seen_at) }),
        el('dt', { text: t('drawer.lastSeen') }),
        el('dd', { text: formatDate(job.last_seen_at) }),
        el('dt', { text: t('drawer.original') }),
        el('dd', {}, [extLink(job.url, job.url || t('drawer.noUrl'), { className: 'link' })]),
      ]),
    ]);
  }

  /**
   * Ask the preparation question again for the job on screen.
   *
   * Called when the confirmed evidence changes underneath an open drawer. A
   * no-op when the tab was never opened: there is nothing stale to correct.
   */
  function reloadPreparation() {
    if (!currentJob || loadedPrepareFor !== currentJob.job_id) return;
    prepare.load(currentJob.job_id);
  }

  return {
    relabel, root, open, close, reloadPreparation, get job() { return currentJob; } };
}

/**
 * A gate result in words. PASS / FAIL / UNRESOLVED is the domain vocabulary and
 * it is exactly right in the domain; on screen, "Unresolved" reads as an error
 * rather than as "the posting did not say".
 */
function gateWords(result) {
  // Resolved at render, never at module load. A table of finished words here
  // would be built before `setLocale` had read the stored choice, which is
  // the trap `STATUS_OPTIONS` fell into on every card in the product.
  const key = String(result || '').toUpperCase();
  const translated = t(`gate.${key}`);
  return translated === `gate.${key}` ? humanLabel(result) : translated;
}

function section(heading, children, { lede = '', className = '' } = {}) {
  return el('section', { className: `d-sec ${className}`.trim() }, [
    el('h3', { className: 'd-sec__head', text: heading }),
    lede ? el('p', { className: 'd-sec__lede', text: lede }) : null,
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
