/**
 * firstrun.js -- "I just installed this. What do I do now?"
 *
 * WHY THIS EXISTS
 * ---------------
 * Every part of the answer was already built, and none of it was in an order.
 * Reading a CV, reviewing what it appears to say, saying where you may legally
 * work, describing the work you want, scoring a corpus: six screens, each
 * correct, each reachable only by somebody who already knew it was there. The
 * documented route through them was three terminal commands, which is a
 * runbook rather than an onboarding, and a person who does not write software
 * stops at the first one.
 *
 * So this is the order, on one screen, with every step saying what it is FOR.
 *
 * WHAT IT IS NOT
 * --------------
 * **It is not a gate.** Nothing here has to be finished for the product to
 * work. Every screen already works with none of it answered; what changes is
 * what the product can CONCLUDE -- without a hiring scope no posting can ever
 * pass the geography gate, without confirmed evidence every requirement reads
 * as a gap. Each step says which of those it unlocks, and each can be left
 * alone forever.
 *
 * **It is not a progress bar.** Six steps are a fact about THIS FLOW, which
 * has a length. A percentage would be a fact about a person, and there is no
 * honest denominator for one -- the same refusal `home.profile_gaps` makes and
 * the reason the rail shows no "LVL 7".
 *
 * **It invents nothing and confirms nothing.** The upload step reads documents
 * and stages proposals; the word the screen uses for them is "found", never
 * "your experience". A claim becomes true when a person says so, one at a
 * time, on the evidence screen.
 *
 * **It does not own any step's real screen.** Four of the six hand off to the
 * surface that already owns the question. Re-implementing the evidence review
 * here would be a second review with its own bugs, and the one that is
 * authoritative would be whichever was written last.
 */

import { el, button, replace } from './dom.js';
import { t } from './i18n.js';
import * as api from './api.js';
import { phraseProblem } from './format.js';

/**
 * The six steps, in the only order that works.
 *
 * `key` matches what `GET /api/firstrun` returns, so the server decides what
 * is done and this file decides what to say about it. A step whose key the
 * server stops sending simply stops being drawn, rather than being drawn with
 * an invented state.
 *
 * The ORDER is an argument. Career context first because it is one click and
 * it changes what the later steps explain. Documents before evidence because
 * there is nothing to review until something has been read. "Where you may
 * work" before "what work you want" because the first is the only one that can
 * make the whole list empty. And jobs last, because everything above it
 * changes the answer.
 */
const STEPS = [
  { key: 'career_stage', icon: '1' },
  { key: 'documents', icon: '2' },
  { key: 'evidence', icon: '3', goTo: 'evidence' },
  { key: 'where', icon: '4', goTo: 'profile' },
  { key: 'work', icon: '5', goTo: 'profile' },
  { key: 'jobs', icon: '6', goTo: 'jobs' },
];

/**
 * Which document kinds the flow offers, and what each one is read AS.
 *
 * The kind is DECLARED, never sniffed from the filename: a file called
 * `profile.pdf` could be a CV or a LinkedIn export, and `intake/build.py`
 * reads an export's Experience section with a different reader because reading
 * it with the CV's produced 255 employment claims with no employer on any of
 * them. Guessing wrong mislabels the provenance of every claim in the file.
 */
const KINDS = [
  { value: 'RESUME', labelKey: 'firstrun.kind.resume' },
  { value: 'LINKEDIN', labelKey: 'firstrun.kind.linkedin' },
  { value: 'DOCUMENT', labelKey: 'firstrun.kind.document' },
];

/** What a file picker will accept. The same four `cv/extract.py` can read. */
const ACCEPT = '.pdf,.docx,.txt,.md';

export function createFirstRun({ onGoTo = null, onChanged = null, onSetup = null } = {}) {
  const root = el('section', { className: 'firstrun' });
  let state = null;
  let token = 0;
  //: Files chosen but not yet sent, as `{ file, kind }`. Held here rather than
  //: read off the inputs at submit time so that the kind a person chose beside
  //: each file survives adding another one.
  let chosen = [];

  //: WHAT JUST HAPPENED TO THE DOCUMENTS, held across a redraw.
  //:
  //: The most important sentence in this flow is the one printed after an
  //: import: "N statements found. None of them is true yet." A successful read
  //: re-reads the whole first-run state and redraws the list, which destroyed
  //: the paragraph that sentence was in -- so the reassurance appeared for
  //: about a hundred milliseconds and then vanished, and what a person was
  //: left looking at was a tick.
  //:
  //: Kept here and re-rendered, and CLEARED the moment another file is chosen:
  //: a message about the last import sitting above a new one is worse than no
  //: message, because it reads as a result.
  let notice = '';

  //: WHICH STEPS THIS VISIT SHOWS. The steps still open when Home was
  //: arrived at. A step already done then is not an open task and is not
  //: drawn again as one; a step answered DURING the visit stays where it is,
  //: ticked, so the answer can be seen and taken back without the row
  //: jumping away under the cursor. `null` until the first load of a visit.
  let shown = null;

  /** Start a new visit: the next load decides again which steps are open. */
  function beginVisit() {
    shown = null;
  }

  async function load() {
    const mine = ++token;
    try {
      state = await api.getFirstRun();
      if (mine !== token) return;
      if (shown === null) {
        shown = new Set((state.steps || []).filter((step) => !step.done).map((step) => step.key));
      }
      draw();
    } catch (error) {
      if (mine !== token) return;
      replace(root, [el('p', { className: 'state__msg', text: error.userMessage || error.message })]);
    }
  }

  /** Whether there is anything left to do. The caller decides what to do with it. */
  /**
   * Whether this looks like a first run: the server's own verdict (nothing
   * confirmed and no search described), which is when Home offers the guided
   * setup unprompted.
   */
  function isFresh() {
    return Boolean(state && state.fresh);
  }

  /** How many postings hold a score, from the same state. Zero before any. */
  function scoredCount() {
    const jobs = ((state && state.steps) || []).find((step) => step.key === 'jobs');
    return jobs ? Number(jobs.scored || 0) : 0;
  }

  function outstanding() {
    if (!state) return true;
    return (state.steps || []).some((step) => !step.done);
  }

  /** How many steps are still open. */
  function remaining() {
    return ((state && state.steps) || []).filter((step) => !step.done).length;
  }

  /** How many steps this visit draws. Zero means setup was already complete. */
  function visible() {
    return visibleSteps().length;
  }

  /** Whether every step is drawn, which is a first run rather than a finish. */
  function showingAll() {
    const steps = (state && state.steps) || [];
    return steps.length > 0 && visible() === steps.length;
  }

  function visibleSteps() {
    const steps = (state && state.steps) || [];
    return shown === null ? steps : steps.filter((step) => shown.has(step.key));
  }

  function draw() {
    const steps = visibleSteps();
    const all = showingAll();
    replace(root, [
      el('header', { className: 'firstrun__head' }, [
        // NO HEADING HERE. The `<details>` this is drawn into on Home has a
        // `<summary>` carrying the same words, and the first version rendered
        // "Start here" twice, one line apart. The summary wins because it is
        // what stays visible when the section is collapsed.
        el('p', {
          className: 'firstrun__lede',
          text: all ? t('firstrun.lede') : t('firstrun.ledeLeft'),
        }),
        // THE PRIVACY SENTENCE, at the top and not in a footnote. It is the
        // first thing somebody needs to know before choosing a file, and it
        // says what actually happens: a Python program on this computer reads
        // it. Claiming the browser does would be a nicer sentence and a false
        // one, and a false privacy claim is worse than none.
        // Only while there is a document step on screen: it is a sentence about
        // choosing a file, and without the step it answers nothing.
        steps.some((step) => step.key === 'documents')
          ? el('p', { className: 'firstrun__privacy', text: t('firstrun.privacy') })
          : null,
      ].filter(Boolean)),
      el('ol', { className: 'firstrun__steps' }, steps.map(stepRow).filter(Boolean)),
    ]);
  }

  function stepRow(step) {
    const spec = STEPS.find((row) => row.key === step.key);
    if (!spec) return null;
    const done = Boolean(step.done);
    return el('li', {
      className: `firstrun__step${done ? ' is-done' : ''}`,
      dataset: { step: step.key },
    }, [
      el('span', { className: 'firstrun__num', text: done ? '✓' : spec.icon }),
      el('div', { className: 'firstrun__body' }, [
        el('h4', { className: 'firstrun__steptitle', text: t(`firstrun.step.${step.key}`) }),
        el('p', { className: 'firstrun__why', text: t(`firstrun.why.${step.key}`) }),
        stateLine(step),
        ...controls(step, spec, done),
      ].filter(Boolean)),
    ]);
  }

  /**
   * What is recorded, in the step's own terms.
   *
   * A COUNT rather than a tick wherever there is one to give. "9 statements
   * waiting" is actionable; "not done" is a judgement about somebody's
   * evening.
   */
  function stateLine(step) {
    const words = {
      career_stage: () => (step.value
        ? t(`firstrun.stage.${step.value}`)
        : t('firstrun.state.unanswered')),
      documents: () => (step.done
        ? t('firstrun.state.documents', { n: (step.packages || 0) + (step.documents || 0) })
        : t('firstrun.state.noDocuments')),
      evidence: () => (step.confirmed
        ? t('firstrun.state.confirmed', { n: step.confirmed })
        : (step.waiting
          ? t('firstrun.state.waiting', { n: step.waiting })
          : t('firstrun.state.nothingToReview'))),
      where: () => (step.country
        ? t('firstrun.state.country', { code: step.country })
        : t('firstrun.state.noCountry')),
      work: () => (step.phrases
        ? t('firstrun.state.phrases', { n: step.phrases })
        : t('firstrun.state.noPhrases')),
      jobs: () => (step.scored
        ? t('firstrun.state.scored', { n: step.scored })
        : t('firstrun.state.noScores')),
    }[step.key];
    return el('p', { className: 'firstrun__state', text: words ? words() : '' });
  }

  function controls(step, spec, done) {
    if (step.key === 'career_stage') return [stageControl(step)];
    if (step.key === 'documents') return [uploadControl()];
    if (step.key === 'work' && !done) return [firstSearchControl()];
    // WHERE YOU LIVE, AND WHO MAY HIRE YOU: answered in the guided setup, at
    // the card that asks it, rather than on a profile page of sixteen fields.
    if (step.key === 'where' && onSetup) {
      return [
        el('div', { className: 'firstrun__actions' }, [
          button(t(`firstrun.go.${step.key}`), () => onSetup('home'), {
            className: done ? 'btn' : 'btn btn--primary',
          }),
        ]),
      ];
    }
    // FIND JOBS starts finding them -- the guided setup's last card, with its
    // progress and a Stop -- instead of opening an empty list that then has to
    // explain where the button is.
    if (step.key === 'jobs' && !done && onSetup) {
      return [
        el('div', { className: 'firstrun__actions' }, [
          button(t('empty.findJobs'), () => onSetup('ready'), {
            className: 'btn btn--primary',
            attrs: { id: 'firstrun-find-jobs' },
          }),
        ]),
      ];
    }
    if (!spec.goTo || !onGoTo) return [];
    return [
      el('div', { className: 'firstrun__actions' }, [
        button(t(`firstrun.go.${step.key}`), () => onGoTo(spec.goTo), {
          className: done ? 'btn' : 'btn btn--primary',
        }),
      ]),
    ];
  }

  function firstSearchControl() {
    const status = el('p', { attrs: { role: 'status' } });
    const work = el('textarea', { className: 'input', attrs: { id: 'fr-work', rows: '3', maxlength: '2000' } });
    const skills = el('textarea', { className: 'input', attrs: { id: 'fr-skills', rows: '2', maxlength: '2000' } });
    const send = button(t('firstrun.searchSave'), async () => {
      const split = node => node.value.split('\n').map(v => v.trim()).filter(Boolean);
      const problem = phraseProblem(split(work)) || phraseProblem(split(skills));
      if (problem) {
        status.textContent = problem;
        return;
      }
      send.disabled = true;
      try {
        await api.createFirstSearch({ role_examples: split(work), skills: split(skills) });
        if (onChanged) onChanged();
        await load();
      } catch (error) {
        status.textContent = error.userMessage || error.message;
        send.disabled = false;
      }
    }, { className: 'btn btn--primary', attrs: { id: 'fr-search-save' } });
    return el('div', { className: 'firstrun__search' }, [
      el('label', { attrs: { for: 'fr-work' }, text: t('firstrun.workLabel') }), work,
      el('label', { attrs: { for: 'fr-skills' }, text: t('firstrun.skillsLabel') }), skills,
      el('p', { text: t('firstrun.searchHelp') }), send, status,
    ]);
  }

  // =====================================================================
  // step 1 -- where are you in your career right now
  // =====================================================================
  /**
   * Six answers, one of which is "I would rather not say".
   *
   * THAT OPTION IS LOAD-BEARING. "I would rather not answer" and "nobody has
   * asked me yet" are different facts, and a flow that offered only five would
   * have made declining look like not having got round to it. Clearing the
   * answer is a third state again, and the control offers that too.
   *
   * It is CONTEXT and the copy says so: it changes what the product explains
   * and which filters it offers, and it changes no score, no eligibility
   * verdict and nothing about which postings are collected.
   */
  function stageControl(step) {
    const status = el('p', {
      className: 'firstrun__status',
      attrs: { role: 'status', 'aria-live': 'polite' },
    });
    const stages = state.career_stages || [];
    const buttons = stages.map((value) => button(t(`firstrun.stage.${value}`), async () => {
      try {
        await api.setCareerStage(value);
        if (onChanged) onChanged();
        await load();
      } catch (error) {
        status.textContent = error.userMessage || error.message;
      }
    }, {
      className: 'chipbtn',
      // `aria-pressed` through `attrs`, which is what `button()` accepts.
      // A toggle that looks chosen and does not SAY it is chosen is invisible
      // to a screen reader.
      attrs: { 'aria-pressed': step.value === value ? 'true' : 'false' },
    }));
    return el('div', { className: 'firstrun__stages' }, [
      el('div', { className: 'firstrun__stagelist' }, buttons),
      step.value
        ? button(t('firstrun.stage.clear'), async () => {
          await api.setCareerStage(null);
          if (onChanged) onChanged();
          await load();
        }, { className: 'btn btn--quiet' })
        : null,
      status,
    ].filter(Boolean));
  }

  // =====================================================================
  // step 2 -- import your career information
  // =====================================================================
  /**
   * The file picker, and the one thing it must not do: claim anything.
   *
   * `<input type="file">` draws its own button and its own "no file chosen"
   * text in the OPERATING SYSTEM'S language, which on an English page on a
   * Portuguese machine is two words of another language in the middle of a
   * sentence about privacy. The input stays the input -- visually hidden
   * rather than `display:none`, so it keeps its place in the tab order and its
   * `accept` list -- and the label is what a person presses.
   */
  function uploadControl() {
    const status = el('p', {
      className: 'firstrun__status',
      attrs: { role: 'status', 'aria-live': 'polite' },
      // What the last import said, restored after the redraw that reading a
      // document causes. See `notice`.
      text: notice,
    });
    const list = el('ul', { className: 'firstrun__files' });

    const input = el('input', {
      className: 'ev__file',
      attrs: { type: 'file', id: 'fr-file', accept: ACCEPT, multiple: 'multiple' },
      on: {
        change: (event) => {
          // A new choice retires the last result. See `notice`.
          notice = '';
          for (const file of event.target.files || []) {
            // The FIRST file defaults to RESUME and the rest to DOCUMENT,
            // because the commonest first run is one CV. Every row can be
            // changed: the kind is declared, never guessed from the name.
            chosen.push({ file, kind: chosen.length ? 'DOCUMENT' : 'RESUME' });
          }
          event.target.value = '';
          paintFiles();
        },
      },
    });

    function paintFiles() {
      replace(list, chosen.map((entry, index) => el('li', { className: 'firstrun__file' }, [
        el('span', { className: 'firstrun__filename', text: entry.file.name }),
        el('select', {
          className: 'select select--inline',
          attrs: { 'aria-label': t('firstrun.kindLabel', { name: entry.file.name }) },
          on: { change: (event) => { chosen[index].kind = event.target.value; } },
        }, KINDS.map((kind) => el('option', {
          text: t(kind.labelKey),
          attrs: { value: kind.value, ...(kind.value === entry.kind ? { selected: 'selected' } : {}) },
        }))),
        button(t('firstrun.removeFile'), () => {
          chosen.splice(index, 1);
          paintFiles();
        }, { className: 'btn btn--quiet' }),
      ])));
      send.disabled = chosen.length === 0;
    }

    const send = button(t('firstrun.read'), async () => {
      if (!chosen.length) return;
      send.disabled = true;
      notice = t('firstrun.reading');
      status.textContent = notice;
      try {
        const documents = [];
        for (const entry of chosen) {
          documents.push({
            filename: entry.file.name,
            kind: entry.kind,
            content_base64: await base64Of(entry.file),
          });
        }
        const result = await api.createIntake(documents);
        chosen = [];
        paintFiles();
        // WHAT WAS FOUND, and the sentence that keeps it honest: nothing is
        // true yet. The count is of STATEMENTS READ, and the next step is a
        // person answering them one at a time.
        notice = result.reopened
          ? t('firstrun.reopened', { n: result.claims_found })
          : t('firstrun.found', { n: result.claims_found });
        status.textContent = notice;
        if (onChanged) onChanged();
        // AFTER the notice is set, because `load` redraws this control and the
        // new one reads `notice` to restore the sentence.
        await load();
      } catch (error) {
        // The server's own sentence, which names the file and says what was
        // wrong with it. Held like a success is, because a failure that
        // disappears is worse than one that stays.
        notice = error.userMessage || error.message;
        status.textContent = notice;
        send.disabled = chosen.length === 0;
      }
    }, { className: 'btn btn--primary' });

    // PAINTED ONCE ON BUILD, not left empty and filled in by the first change
    // event. This control is rebuilt on every redraw -- a language switch, a
    // saved answer, a finished import -- and `chosen` outlives the rebuild, so
    // a redraw between choosing a file and pressing Read used to drop the list
    // on the floor and disable the button over files that were still there.
    paintFiles();

    return el('div', { className: 'firstrun__upload' }, [
      el('div', { className: 'ev__filerow' }, [
        input,
        el('label', { className: 'btn ev__filebtn', attrs: { for: 'fr-file' }, text: t('firstrun.choose') }),
      ]),
      el('p', { className: 'firstrun__hint', text: t('firstrun.supported', { kinds: ACCEPT.split(',').join(' ') }) }),
      list,
      el('div', { className: 'firstrun__actions' }, [send]),
      status,
    ]);
  }

  /**
   * One file as base64, without a data URL prefix.
   *
   * `FileReader.readAsDataURL` would give `data:application/pdf;base64,...`
   * and the server validates strict base64, so the prefix is a 400 rather than
   * something it tolerates. Built from the bytes instead, in chunks: one
   * `String.fromCharCode(...bytes)` call over a 30 MB file exceeds the
   * argument limit and throws a RangeError that reads like nothing at all.
   */
  async function base64Of(file) {
    const bytes = new Uint8Array(await file.arrayBuffer());
    const CHUNK = 0x8000;
    let binary = '';
    for (let at = 0; at < bytes.length; at += CHUNK) {
      binary += String.fromCharCode(...bytes.subarray(at, at + CHUNK));
    }
    return btoa(binary);
  }

  /**
   * Redraw from the state already held, in whatever language is now in force.
   *
   * A LANGUAGE SWITCH, and it needed its own entry point. Home rebuilds the
   * section AROUND this node on `relabel()`, and the node itself kept the
   * children it was drawn with -- so the whole first-run list stayed in
   * English inside a Portuguese page, which is the exact defect the
   * localisation gate exists to catch and which this panel was too new to be
   * covered by.
   *
   * It does not re-fetch. Asking the server again to change six words would be
   * a request that answers a question nobody asked, and the same reasoning
   * keeps `home.relabel` off the network.
   */
  function redraw() {
    if (state) draw();
  }

  return {
    root, load, redraw, outstanding, remaining, visible, showingAll, beginVisit, isFresh, scoredCount,
  };
}
