/**
 * evidence.js -- what is true about you, and how it got there.
 *
 * An overlay rather than a rail panel, and that is a content decision. A CV
 * review is forty proposals each carrying the line it was read from; in a
 * 280-pixel column every one of them wraps to five lines and the comparison
 * that makes the review trustworthy -- proposal beside source -- stops being
 * possible to make at a glance.
 *
 * THREE THINGS THIS SURFACE PROMISES.
 *
 * **Reading confirms nothing.** Importing a CV produces PROPOSALS. Each one
 * shows the line it came from, and becomes a fact about somebody only when
 * she says so. The counts on this screen distinguish the two at all times.
 *
 * **Edit is the answer that matters.** A CV line is often nearly right, and a
 * review offering only accept-or-reject pushes people into keeping a sentence
 * they would have corrected. Editing stores her wording; the source line stays
 * exactly as the document had it, beside it, forever.
 *
 * **Nothing is destroyed.** Retiring a claim writes a new revision that is no
 * longer confirmed. The claim stays, its history stays, and preparation stops
 * drawing on it immediately -- because it may already have prepared an
 * application somebody sent, and the record of what she believed then is the
 * record.
 */

import { el, button, replace, focusables, field } from './dom.js';
import { formatDate } from './format.js';
import { t } from './i18n.js';
import * as api from './api.js';
import { careerWorkspace } from './career.js';
import { cvReview } from './cv_review.js';

/** Claim kinds somebody may write by hand, in the order they are offered. */
const TYPES = ['EMPLOYMENT', 'PROJECT', 'ACHIEVEMENT', 'SKILL', 'TOOL', 'EDUCATION',
  'CERTIFICATION'];

/**
 * The overlay. One instance, opened from the rail and from the Prepare tab.
 *
 * `onChanged` fires whenever the confirmed set moves, so an open Prepare tab
 * can be re-asked rather than quietly showing an answer computed against
 * evidence that no longer exists.
 */
export function createEvidence({ onChanged = null } = {}) {
  let invoker = null;
  let openImport = null;   // the staged read being reviewed, if any
  // The intake package being reviewed, and the one group or state of it that
  // is open. Two levels rather than one, and that IS the product decision:
  // the owner's real package holds 309 proposals and a list of 309 is a wall.
  let openPackage = null;
  let openList = null;
  let filterText = '';
  let intakeFilter = '';
  //: The requirement somebody arrived here from, if they arrived from one.
  let focused = '';
  let token = 0;
  //: The last package list rendered, so a row can name the package that
  //: displaced it rather than printing its identifier.
  let lastPackages = [];
  //: WHERE TO START for the package on screen: every step with its counts.
  //: Fetched beside the overview rather than folded into it, because it is a
  //: reading order over the same claims and the overview is what they are.
  let openPlan = null;

  const bodyHost = el('div', { className: 'drawer__body ev' });
  const titleNode = el('h2', {
    className: 'drawer__title',
    attrs: { id: 'evidence-title' },
    text: '',
  });

  const closeButton = button(t('ledger.close'), () => close(), {
    className: 'btn btn--close',
    ariaLabel: t('ledger.close'),
  });

  const panel = el('div', {
    className: 'drawer__panel drawer__panel--wide',
    attrs: {
      role: 'dialog',
      'aria-modal': 'true',
      'aria-labelledby': 'evidence-title',
      tabindex: '-1',
    },
  }, [
    el('div', { className: 'drawer__head' }, [titleNode, closeButton]),
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

  // =====================================================================
  async function open(invokedBy) {
    invoker = invokedBy || document.activeElement;
    root.hidden = false;
    document.body.classList.add('has-drawer');
    titleNode.textContent = t('ledger.title');
    closeButton.textContent = t('ledger.close');
    panel.focus();
    await refresh();
    const first = focusables(panel)[0];
    if (first) first.focus();
  }

  function close() {
    if (root.hidden) return;
    root.hidden = true;
    document.body.classList.remove('has-drawer');
    openImport = null;
    openPackage = null;
    openList = null;
    filterText = '';
    intakeFilter = '';
    focused = '';
    token += 1;
    replace(bodyHost, []);
    if (invoker && document.contains(invoker)) invoker.focus();
    invoker = null;
  }

  async function refresh() {
    const mine = ++token;
    // A package already open is the screen, and re-fetching the home view
    // would paint over it. This is not an optimisation: `focusOn` opens a
    // package and the PAGE mounts itself afterwards, so without this the
    // trip from a job's requirement ended on the home view every time --
    // silently, because nothing had failed.
    if (openPackage) {
      await paintPackage();
      return;
    }
    replace(bodyHost, [el('div', { className: 'sk sk--block' })]);
    try {
      const [ledger, imports, packages] = await Promise.all([
        api.getEvidence(), api.getCvImports(), api.getIntakePackages(),
      ]);
      if (mine !== token) return;
      const home = !openImport && !openPackage;
      // NOTHING HERE YET. Five empty review sections and three paragraphs of
      // instructions are not a start; one card that says what this page is
      // for and opens the CV picker is.
      const empty = home
        && !(ledger.claims || []).length
        && !(imports.imports || []).length
        && !(packages.packages || []).length;
      if (empty) {
        replace(bodyHost, [
          startCard(),
          careerWorkspace({ onChanged: () => { if (onChanged) onChanged(); } }),
          // Kept: it holds the form for writing a fact by hand, which is the
          // only way in for somebody without a CV. The import review is not
          // drawn -- there is nothing imported to review.
          el('details', {}, [el('summary', { text: t('career.evidenceEditor') }),
            ledgerSection(ledger)]),
          sourcesFold(packages, imports),
        ]);
        return;
      }
      // THE ORDER IS THE DESIGN. What needs her, then what the product knows
      // about her, then where that came from. It used to be the reverse.
      replace(bodyHost, [
        openImport ? reviewView(openImport) : null,
        home ? careerWorkspace({ onChanged: () => { if (onChanged) onChanged(); } }) : null,
        home ? el('details', {}, [el('summary', { text: t('career.importReview') }),
          attentionBlock(packages, ledger, imports)]) : null,
        home ? el('details', {}, [el('summary', { text: t('career.evidenceEditor') }),
          ledgerSection(ledger)]) : null,
        home && ledger.candidate && ledger.claims.length ? addForm() : null,
        home ? sourcesFold(packages, imports) : null,
      ].filter(Boolean));
    } catch (error) {
      if (mine !== token) return;
      replace(bodyHost, [
        el('p', { className: 'state__msg', text: error.userMessage || error.message }),
        button(t('ledger.retry'), () => refresh(), { className: 'btn' }),
      ]);
    }
  }

  /** The empty page's one card: what this is, and the CV picker. */
  function startCard() {
    return el('section', { className: 'ev__start', attrs: { 'aria-labelledby': 'ev-start-title' } }, [
      el('h2', { className: 'ev__starttitle', attrs: { id: 'ev-start-title' }, text: t('evstart.title') }),
      el('p', { className: 'ev__startbody', text: t('evstart.body') }),
      el('div', { className: 'ev__startactions' }, [
        button(t('evstart.import'), () => {
          // The picker lives in "Sources and imports"; open it, then open
          // the picker itself. The click is the person's, so the browser
          // lets the file dialog open.
          const fold = bodyHost.querySelector('.ev__sources');
          if (fold) fold.open = true;
          const input = bodyHost.querySelector('#ev-file');
          if (input) input.click();
        }, { className: 'btn btn--primary', attrs: { id: 'ev-start-import' } }),
      ]),
      el('p', { className: 'ev__starthint', text: t('evstart.hint') }),
    ]);
  }

  function announce(message, tone = 'ok') {
    return el('p', {
      className: `ev__flash ev__flash--${tone}`,
      attrs: { role: 'status', 'aria-live': 'polite' },
      text: message,
    });
  }


  // =====================================================================
  // THE CANDIDATE INTAKE PACKAGE
  //
  // WHY THIS IS NOT ONE LIST
  // ------------------------
  // The owner's real package holds 309 proposed claims. The terminal answer
  // to that is a list, and a list of 309 sentences about your own career is
  // the thing people close the tab on. Nothing about it is wrong; it is just
  // unreviewable, which makes every claim in it unreviewed forever.
  //
  // So this surface has three levels and no route that returns everything:
  //
  //   the packages    what arrived, and how far the reading of it has got
  //   one package     the documents behind it, the disagreements, and one
  //                   HEADING per group -- "Acme, 2020 to 2021, 9 to look at"
  //   one group       the claims themselves, with their provenance
  //
  // A reader meets a heading she recognises before she meets a sentence, and
  // she can answer nine things about one employer and stop.
  //
  // WHAT THIS SURFACE MAY NOT DO
  // ----------------------------
  // There is no "confirm everything". Every fact confirmed here can end up on
  // a real application, and one click cannot honestly mean somebody read
  // three hundred sentences. The only batch action offered is the one that
  // creates nothing: setting the rest aside as "not sure yet", which is
  // reversible and produces no claim.
  // =====================================================================

  /** The review states a claim can be filed under, as chips on the overview. */
  const INTAKE_STATES = ['CONFIRMED', 'CORRECTED_BY_USER', 'UNRESOLVED', 'REJECTED'];

  /** States that still want an answer from her. */
  function isWaiting(claim) {
    return claim.review_state === 'UNREVIEWED'
      || claim.review_state === 'CONFLICT'
      || claim.review_state === 'UNRESOLVED';
  }

  /**
   * How far she has got, as a bar and as a sentence.
   *
   * The sentence says what is LEFT, never what percentage is "complete". A
   * completion figure is the shape that pushes somebody into confirming a
   * sentence to finish a bar, and these are facts about her own career.
   */
  function meter(done, total) {
    const fill = el('span', { className: 'ev__meterfill' });
    fill.style.width = total ? `${Math.round((done / total) * 100)}%` : '0%';
    return el('div', {
      className: 'ev__meter',
      attrs: { role: 'img', 'aria-label': t('intake.progressLabel', { done, total }) },
    }, [fill]);
  }

  /** What a package's own declared sources call one of its refs. */
  function documentName(refs) {
    const sources = (openPackage && openPackage.sources) || [];
    const names = (refs || []).map((ref) => {
      const found = sources.find((s) => s.ref === ref);
      return found ? found.title : ref;
    });
    return names.join(', ');
  }

  // =====================================================================
  // THE FIRST QUESTION, ANSWERED FIRST
  //
  // This page used to open on its own machinery: two cards named
  // `career-agent-import.json`, one marked IN FORCE and one PUT AWAY, above a
  // paragraph explaining that choosing which READING to work from confirms
  // nothing. Everything a person needed to know in order to read that was a
  // fact about the extraction pipeline, and the thing they actually came for
  // -- their own career -- was three screens down.
  //
  // The page answers three questions now, in this order:
  //
  //   is there anything I need to look at?   <- this block
  //   what does Career Agent know about me?  <- the evidence browser
  //   where did that come from?              <- a fold at the bottom
  //
  // It used to answer the third one first.
  // =====================================================================

  /**
   * Does anything need her, and what does she press.
   *
   * The count is the ACTIVE reading's, because that is the one the rest of
   * the product reads; an archived import with three hundred unanswered
   * proposals is not work waiting to be done, it is work she put down.
   */
  function attentionBlock(packages, ledger, imports = {}) {
    const all = packages.packages || [];
    const active = byId(all, packages.active_package_id);
    const inPackage = active ? waitingIn(active.counts || {}) : 0;
    // ONE DEFINITION OF WAITING (storage/review_counts.py): the package in
    // force plus every CV read that is not archived. The same figure Home and
    // the setup show.
    const counted = (imports.counts || {}).waiting;
    const waiting = Number.isFinite(counted) ? counted : inPackage;
    const cvWaiting = (imports.imports || []).find((item) => !item.archived && item.pending);
    const kids = [];

    if (waiting) {
      kids.push(el('h3', {
        className: 'ev__attendhead',
        text: waiting === 1 ? t('attend.oneWaiting') : t('attend.waiting', { n: waiting }),
      }));
      kids.push(el('p', { className: 'ev__lede', text: t('attend.waitingLede') }));
      kids.push(el('div', { className: 'ev__attendacts' }, [
        button(t('attend.continue'), async () => {
          if (inPackage) {
            await openPackageReview(active.package_id);
            return;
          }
          if (!cvWaiting) return;
          openImport = await api.getCvReview(cvWaiting.import_id);
          await paintReview();
        }, { className: 'btn btn--primary' }),
      ]));
    } else {
      kids.push(el('h3', { className: 'ev__attendhead', text: t('attend.clear') }));
      kids.push(el('p', {
        className: 'ev__lede',
        text: ledger.candidate && ledger.claims.length
          ? t('attend.clearLede')
          : t('attend.nothingYet'),
      }));
    }

    // The quiet half. Two counts, and they are not a score: nothing here is
    // out of anything, because there is no number of confirmed facts that
    // would mean somebody had finished describing their career.
    if (ledger.candidate && (ledger.confirmed || ledger.retired)) {
      kids.push(el('p', {
        className: 'ev__attendsum',
        text: t('attend.summary', { confirmed: ledger.confirmed, retired: ledger.retired }),
      }));
    }

    return el('section', {
      className: `card card--static ev__attend${waiting ? ' ev__attend--waiting' : ''}`,
    }, kids);
  }

  // =====================================================================
  // WHERE IT CAME FROM: the third question, in the third place
  // =====================================================================

  /**
   * Imports, their history, and the CV reader, all behind one disclosure.
   *
   * Everything in here is TRUE and almost none of it is what somebody came
   * for. It is the audit trail: which document produced which proposal, which
   * reading is the one in force, and how to add another. Keeping it available
   * is the point of the product; keeping it FIRST was the defect.
   */
  function sourcesFold(packages, imports) {
    const all = packages.packages || [];
    const active = packages.active_package_id || null;
    const live = all.filter((pack) => pack.status !== 'DISCARDED');
    const away = all.filter((pack) => pack.status === 'DISCARDED');
    lastPackages = all;

    const inside = [];
    // SAID ONCE. It used to be printed under every package card, which is
    // three times the reassurance and a third of the trust.
    inside.push(el('p', { className: 'ev__lede', text: t('intake.lede') }));
    if (live.length) {
      inside.push(el('ul', { className: 'ev__imports' },
        live.map((pack) => packageRow(pack, active))));
    }
    if (away.length) {
      inside.push(el('details', { className: 'fold fold--quiet' }, [
        el('summary', {
          className: 'fold__summary',
          text: t('intake.historyHeading', { n: away.length }),
        }),
        el('ul', { className: 'ev__imports' }, away.map((pack) => packageRow(pack, active))),
      ]));
    }
    inside.push(intakeSection(imports));

    return el('details', { className: 'fold ev__sources' }, [
      el('summary', { className: 'fold__summary', text: t('intake.heading') }),
      el('div', { className: 'ev__stack' }, inside.filter(Boolean)),
    ]);
  }

  // ---------------------------------------------------------------------
  // one import, and what it produced
  // ---------------------------------------------------------------------
  function byId(packages, packageId) {
    return (packages || []).find((pack) => pack.package_id === packageId) || null;
  }

  function nameOf(pack) {
    if (!pack) return '';
    return pack.filename || pack.generator || pack.package_id;
  }

  /** The documents a package declares, by kind, as a plain phrase. */
  /**
   * `SELF:career-agent local extractor`, in words.
   *
   * The stored value is an IDENTIFIER: a kind and a name joined with a colon,
   * written once when the package was imported and useful as a record. It was
   * being printed to the screen verbatim, so a page that spends its whole
   * length avoiding jargon said "Prepared by SELF:career-agent local
   * extractor" in its third line.
   *
   * The KIND is the thing worth knowing, and it is the single most useful
   * fact while deciding how carefully to read forty claims: did this program
   * read the document, or did an assistant somebody chose? For `SELF` the
   * name is dropped, because there is only one local extractor and naming it
   * tells the reader nothing. For `EXTERNAL_AI` the name is kept, because
   * WHICH assistant is hers and it matters.
   */
  function whoRead(raw) {
    const text = String(raw || '');
    const colon = text.indexOf(':');
    const kind = colon === -1 ? text : text.slice(0, colon);
    const name = colon === -1 ? '' : text.slice(colon + 1).trim();
    const key = `intake.readBy.${kind}`;
    const said = t(key);
    // A kind this build has never heard of falls back to what was stored.
    // Untranslated is better than missing, and it is visible as untranslated.
    if (said === key) return name || text;
    return kind === 'EXTERNAL_AI' && name ? t('intake.readByNamed', { name }) : said;
  }

  function documentsOf(pack) {
    const kinds = (pack.sources || []).map((s) => t(`intake.source.${s.kind}`));
    return kinds.length ? kinds.join(', ') : t('intake.source.DOCUMENT');
  }

  /**
   * WHAT AN IMPORT IS CALLED, to a person.
   *
   * Two of these were on screen at once and both were called
   * `career-agent-import.json`, so the only thing telling them apart was a
   * badge reading IN FORCE against one reading PUT AWAY. A filename is not an
   * identity when two things share it; the DOCUMENTS and the DAY are.
   *
   * The filename is still shown, underneath, because it is what she would
   * type to find the file. It is metadata, not a name.
   */
  function titleOf(pack) {
    const documents = documentsOf(pack);
    const when = formatDate(pack.created_at);
    if (documents && when) return t('intake.importTitle', { documents, when });
    return documents || when || nameOf(pack);
  }

  /** Reviewed, or how many are left. Never "looked at", and never a score. */
  function progressOf(pack, waiting, answered) {
    if (!pack.claim_count) return t('intake.nothingIn');
    if (!waiting) return t('intake.reviewDone', { n: answered });
    return t('intake.needReview', { n: waiting });
  }

  function packageRow(pack, active) {
    const counts = pack.counts || {};
    const waiting = waitingIn(counts);
    const answered = pack.claim_count - waiting;
    const isActive = pack.package_id === active;
    const archived = pack.status === 'DISCARDED';
    const actions = [];

    // The primary action is only primary on the reading in force. Offering
    // "Continue review" on a superseded import would walk somebody into
    // answering proposals that are not the ones the rest of the product
    // reads, which is the one thing this screen's badges exist to prevent.
    if (isActive) {
      actions.push(button(startLabel(waiting, answered), async () => {
        await openPackageReview(pack.package_id);
      }, { className: waiting ? 'btn btn--primary' : 'btn' }));
    } else if (archived) {
      actions.push(button(t('intake.restore'), () => restore(pack), { className: 'btn' }));
    } else if (pack.status === 'INCOMPLETE') {
      // Nothing to select and nothing to review. Said rather than hidden: an
      // import that vanished would look like one that never happened.
      actions.push(el('span', { className: 'ev__importmeta', text: t('intake.incompleteWhy') }));
    } else {
      actions.push(button(t('intake.useThisOne'), () => useThisOne(pack), { className: 'btn' }));
    }
    actions.push(button(t('intake.inspect'), async () => {
      await openPackageReview(pack.package_id);
    }, { className: 'btn btn--quiet' }));
    // ARCHIVING IS QUIET AND LAST. It is the only control here that takes
    // something away, and a destructive-looking action beside a primary one
    // is how somebody archives the reading they meant to review.
    if (!archived) {
      actions.push(button(t('intake.putAway'), () => putAway(pack, waiting), {
        className: 'btn btn--quiet',
      }));
    }
    // DELETING IS ITS OWN ACT. Archive keeps everything and is undone with
    // one click; delete removes every unconfirmed claim for good, so it
    // first shows exactly what goes and what stays, from the server's plan.
    const confirmHost = el('div', { className: 'ev__confirmhost' });
    actions.push(button(t('lifecycle.delete'), () => deleteFlow(confirmHost, {
      name: titleOf(pack),
      plan: async () => (await api.deleteIntakePackage(pack.package_id)).plan,
      remove: () => api.deleteIntakePackage(pack.package_id, { confirm: true }),
    }), { className: 'btn btn--quiet btn--danger', attrs: { 'data-action': 'delete' } }));

    return el('li', {
      className: `ev__import${isActive ? ' ev__import--active' : ''}`,
    }, [
      el('div', { className: 'ev__importhead' }, [
        el('span', { className: 'ev__importname', text: titleOf(pack) }),
        el('span', {
          className: `ev__importstate ev__importstate--${String(pack.status).toLowerCase()}`,
          text: t(`intake.status.${pack.status}`),
        }),
      ]),
      // The progress, in the words of somebody reviewing rather than of the
      // thing being reviewed. "309 of 309 looked at" is a cursor position.
      el('p', {
        className: 'ev__importprogress',
        text: progressOf(pack, waiting, answered),
      }),
      waiting && pack.claim_count ? meter(answered, pack.claim_count) : null,
      // The filename and who read it: metadata, under the name, quiet.
      el('p', {
        className: 'ev__importmeta',
        text: t('intake.importFrom', { file: nameOf(pack), who: whoRead(pack.generator) }),
      }),
      pack.conflicted
        ? el('p', {
          className: 'ev__importmeta ev__importmeta--warn',
          text: t('intake.conflictCount', { n: pack.conflicted }),
        })
        : null,
      // WHY this one is not the active reading, which "a newer one exists"
      // does not answer. Only shown when something actually displaced it.
      pack.superseded_by
        ? el('p', {
          className: 'ev__importmeta',
          text: t('intake.supersededBy', { name: titleOf(byId(lastPackages, pack.superseded_by) || {}) }),
        })
        : null,
      el('div', { className: 'ev__importactions' }, actions.filter(Boolean)),
      confirmHost,
    ].filter(Boolean));
  }

  /**
   * THE DELETE CONFIRMATION, for a CV read or a package: what goes, what
   * stays, and that it cannot be undone -- all from the server's own plan,
   * so the sentence cannot promise something the delete does not do.
   */
  async function deleteFlow(host, { name, plan, remove }) {
    let counts;
    try {
      counts = await plan();
    } catch (error) {
      replace(host, [announce(error.userMessage || error.message, 'bad')]);
      return;
    }
    const pending = counts.pending ?? counts.waiting ?? 0;
    const said = counts.confirmed_kept
      ? t('cvr.deleteKeeps', { removed: counts.removed, pending, rejected: counts.rejected,
        confirmed: counts.confirmed_kept })
      : t('cvr.deleteAll', { removed: counts.removed, pending, rejected: counts.rejected });
    const yes = button(t('cvr.deleteConfirm'), async () => {
      yes.disabled = true;
      try {
        await remove();
      } catch (error) {
        yes.disabled = false;
        replace(host, [announce(error.userMessage || error.message, 'bad')]);
        return;
      }
      if (onChanged) onChanged();
      await refresh();
      bodyHost.prepend(announce(t('lifecycle.deleted', { name }), 'ok'));
    }, { className: 'btn btn--danger', attrs: { 'data-action': 'delete-confirm' } });
    replace(host, [el('div', { className: 'cvr__confirm', attrs: { role: 'alertdialog' } }, [
      el('p', { className: 'cvr__confirmtitle', text: t('cvr.deleteTitle', { name }) }),
      el('p', { text: said }),
      el('p', { className: 'ev__note', text: t('cvr.deleteArchiveInstead') }),
      el('p', { text: t('cvr.deleteForever') }),
      el('div', { className: 'cvr__actions' }, [
        yes,
        button(t('cvr.cancel'), () => replace(host, []), { className: 'btn' }),
      ]),
    ])]);
    yes.focus();
  }

  /** Put this reading in force. Reversible, and it confirms nothing. */
  async function useThisOne(pack) {
    try {
      await api.selectIntakePackage(pack.package_id);
    } catch (error) {
      bodyHost.prepend(announce(error.userMessage || error.message, 'bad'));
      return;
    }
    await refresh();
  }

  /** Take one back out of the drawer, and say where it landed. */
  async function restore(pack) {
    let landed = null;
    try {
      landed = await api.restoreIntakePackage(pack.package_id);
    } catch (error) {
      bodyHost.prepend(announce(error.userMessage || error.message, 'bad'));
      return;
    }
    await refresh();
    // WHICH state it landed in. Beside a live review it lands aside, and a
    // message saying otherwise would send her looking for it in the wrong
    // place.
    bodyHost.prepend(announce(landed.status === 'ACTIVE'
      ? t('intake.restoredActive', { name: nameOf(pack) })
      : t('intake.restoredAside', { name: nameOf(pack) }), 'ok'));
  }

  /**
   * ARRIVING FROM A JOB, with a requirement in mind.
   *
   * The Prepare tab already knew which requirement somebody said they could
   * not evidence; it just threw the answer away and opened this screen at the
   * top. So the loop was: read a requirement, come here, forget what you came
   * for, scroll.
   *
   * Now the same click lands on what is WAITING FOR AN ANSWER that mentions
   * the requirement. Nothing is confirmed, nothing is pre-selected and the
   * filter says out loud what it is doing, with one button to drop it -- a
   * screen that quietly showed a subset would be a screen that hides claims.
   */
  async function focusOn(term) {
    const needle = String(term || '').trim();
    // Any refresh already in flight belongs to the previous screen.
    token += 1;
    focused = needle;
    intakeFilter = needle;
    filterText = needle;
    let packages = { packages: [] };
    try {
      packages = await api.getIntakePackages();
    } catch (error) {
      packages = { packages: [] };
    }
    // THE READING IN FORCE, not merely the first one that is not put away.
    // Landing somebody on a superseded package would have her answer claims
    // the rest of the product does not read.
    const active = byId(packages.packages, packages.active_package_id);
    const pack = active && waitingIn(active.counts || {}) ? active : null;
    if (!pack) {
      await refresh();
      return;
    }
    openPackage = await api.getIntakeOverview(pack.package_id);
    // The queue too, carrying the requirement she came from, so the screen
    // she lands on can say how much of what is waiting mentions it.
    openPlan = await api.getIntakePriority(openPackage.package_id, { term: needle });
    const payload = await api.getIntakeClaims(openPackage.package_id, { state: 'UNREVIEWED' });
    openList = {
      kind: 'state',
      id: 'UNREVIEWED',
      title: t('intake.state.UNREVIEWED'),
      claims: payload.claims || [],
    };
    await paintPackage();
  }

  function waitingIn(counts) {
    return (counts.UNREVIEWED || 0) + (counts.CONFLICT || 0) + (counts.UNRESOLVED || 0);
  }

  function startLabel(waiting, answered) {
    if (!waiting) return t('intake.reopenReview');
    return answered ? t('intake.continueReview') : t('intake.startReview');
  }

  async function putAway(pack, waiting) {
    // It asks, because it takes something off her screen. The question says
    // what actually happens -- nothing is deleted and nothing confirmed is
    // touched -- rather than implying the claims go with it.
    const ok = window.confirm(t('intake.putAwayConfirm', {
      name: pack.filename || pack.generator,
      waiting,
    }));
    if (!ok) return;
    try {
      await api.discardIntakePackage(pack.package_id);
      await refresh();
    } catch (error) {
      bodyHost.prepend(announce(error.userMessage || error.message, 'bad'));
    }
  }

  async function openPackageReview(packageId) {
    try {
      openPackage = await api.getIntakeOverview(packageId);
      openPlan = await api.getIntakePriority(packageId, { term: focused || null });
    } catch (error) {
      bodyHost.prepend(announce(error.userMessage || error.message, 'bad'));
      return;
    }
    openList = null;
    intakeFilter = '';
    await paintPackage();
  }

  async function reloadPackage() {
    if (!openPackage) return;
    openPackage = await api.getIntakeOverview(openPackage.package_id);
    // The queue is DERIVED, so it has to be re-asked: settling a disagreement
    // empties the first step and answering a claim moves a count. A stale
    // plane beside a fresh overview is two numbers disagreeing on one screen.
    openPlan = await api.getIntakePriority(openPackage.package_id, { term: focused || null });
  }

  async function paintPackage() {
    replace(bodyHost, [openList ? listView() : packageView()]);
    panel.scrollTop = 0;
  }

  // ---------------------------------------------------------------------
  // level two: one package
  // ---------------------------------------------------------------------
  function packageView() {
    const pack = openPackage;
    const counts = pack.counts || {};
    const waiting = (counts.UNREVIEWED || 0) + (counts.CONFLICT || 0) + (counts.UNRESOLVED || 0);
    const back = button(t('cv.backToEvidence'), async () => {
      openPackage = null;
      openList = null;
      await refresh();
      if (onChanged) onChanged();
    }, { className: 'btn btn--quiet' });

    return el('div', { className: 'ev__review' }, [
      el('div', { className: 'ev__reviewhead' }, [
        el('h3', {
          className: 'd-sec__head',
          text: t('intake.overviewHeading', { name: pack.filename || pack.generator }),
        }),
        back,
      ]),
      el('p', {
        className: 'ev__lede',
        text: waiting
          ? t('intake.overviewLede', { waiting, total: pack.total })
          : t('intake.overviewDone', { total: pack.total }),
      }),
      meter(pack.answered, pack.total),
      el('p', { className: 'ev__note', text: t('cv.reviewSafety') }),
      whereToStartBlock(),
      documentsBlock(pack.sources || []),
      ...conflictBlock(pack.conflicts || []),
      groupsBlock(pack.groups || []),
      statesBlock(counts),
    ]);
  }

  // ---------------------------------------------------------------------
  // WHERE TO START
  //
  // Three hundred unanswered statements is not a task, it is a wall. The
  // grouped view already stops the SCREEN being a wall; this answers the
  // question underneath it -- of these three hundred, which ones matter
  // before the product is usable at all.
  //
  // Two things it deliberately is not.
  //
  // It is not a RANKING OF TRUTH. A step decides where a statement is met and
  // never what it means; the same four answers are offered in every step and
  // nothing is hidden from any of them. The wording says so, in both
  // languages, above the list.
  //
  // It is not a SCORE. Every number here is "N of M in this step", where M is
  // how many claims are in it and the steps add up to the package. There is
  // no percentage of a target nobody set, no level and no badge -- and a unit
  // test greps the payload for exactly those words.
  // ---------------------------------------------------------------------
  function whereToStartBlock() {
    if (!openPlan) return null;
    const essential = openPlan.essential || { total: 0, waiting: 0, answered: 0 };
    const progress = openPlan.progress || { total: 0, waiting: 0, answered: 0 };
    const focus = openPlan.focus || { term: null, total: 0, waiting: 0 };

    const children = [
      el('h4', { className: 'ev__grouphead' }, [
        el('span', { text: t('intake.startHeading') }),
      ]),
      el('p', { className: 'ev__note', text: t('intake.startNavigationOnly') }),
      // WHAT "ENOUGH TO START" MEANS, spelled out rather than implied by a
      // bar. The denominator is named in the same sentence as the number.
      el('p', {
        className: 'ev__lede',
        text: essential.waiting
          ? t('intake.startEssential', {
            waiting: essential.waiting, total: essential.total, all: progress.total,
          })
          : t('intake.startEssentialDone', { all: progress.total, waiting: progress.waiting }),
      }),
      el('p', { className: 'ev__note', text: t('intake.startEssentialMeans') }),
    ];

    // WHAT SHE CAME FOR, when she came from a requirement. Above the steps,
    // because it is why she is on this screen at all.
    if (focus.term) {
      children.push(el('p', {
        className: 'ev__figure',
        text: t('intake.startFocus', {
          term: focus.term, waiting: focus.waiting, total: focus.total,
        }),
      }));
    }

    children.push(el('ul', { className: 'ev__cards' },
      (openPlan.steps || []).map((step) => stepCard(step))));
    return el('section', { className: 'ev__group ev__group--start' }, children);
  }

  function stepCard(step) {
    const name = t(`intake.step.${step.key}`);
    const done = !step.waiting;
    return el('li', {
      className: `ev__card ev__card--step${step.essential ? ' ev__card--essential' : ''}`,
    }, [
      el('div', { className: 'ev__cardhead' }, [
        el('p', { className: 'ev__proposal', text: name }),
        el('span', {
          className: `ev__tag ev__tag--${done ? 'done' : 'waiting'}`,
          // A step at zero says which zero it is. "You answered everything
          // here" and "your package holds none of these" are opposite facts
          // and they must not render the same.
          text: step.total === 0
            ? t('intake.stepNone')
            : (done ? t('intake.allAnswered') : t('intake.waiting', { n: step.waiting })),
        }),
      ]),
      el('p', { className: 'ev__meta', text: t(`intake.stepWhy.${step.key}`) }),
      el('p', {
        className: 'ev__meta',
        text: t('intake.stepCount', { answered: step.answered, total: step.total }),
      }),
      // MECHANICALLY blocking, and the sentence says why rather than telling
      // her it is important.
      step.blocking && step.waiting
        ? el('p', { className: 'ev__figure', text: t('intake.stepBlocking') })
        : null,
      el('div', { className: 'ev__actions' }, [
        step.total
          ? button(t('intake.openStep'), () => showStep(step), {
            className: step.essential ? 'btn' : 'btn btn--quiet',
            ariaLabel: t('intake.openStepLabel', { name }),
          })
          : null,
      ].filter(Boolean)),
    ].filter(Boolean));
  }

  async function showStep(step) {
    await show({ kind: 'step', id: step.key, title: t(`intake.step.${step.key}`) });
  }

  /** The documents this package says it was built from. */
  function documentsBlock(sources) {
    return el('section', { className: 'ev__group' }, [
      el('h4', { className: 'ev__grouphead' }, [el('span', { text: t('intake.documents') })]),
      el('ul', { className: 'ev__docs' }, sources.map((source) => el('li', {
        className: 'ev__doc',
      }, [
        el('span', { className: 'ev__type', text: t(`intake.source.${source.kind}`) }),
        // The candidate's own filename. Never translated: it is what she
        // calls the document, and it is how she tells two of them apart.
        el('span', { className: 'ev__docname', text: source.title }),
      ]))),
    ]);
  }

  // ---------------------------------------------------------------------
  // the disagreements
  //
  // Answered ONCE, at the top, before any of the claims they hold. Two
  // documents disagreeing about when a role ran is one question, and asking
  // it thirty-two times -- once per sentence about that role -- is how a
  // review becomes something nobody finishes.
  // ---------------------------------------------------------------------
  function conflictBlock(conflicts) {
    if (!conflicts.length) return [];
    return [el('section', { className: 'ev__group' }, [
      el('h4', { className: 'ev__grouphead' }, [
        el('span', { text: t('intake.conflictHeading') }),
        el('span', { className: 'ev__groupcount num', text: String(conflicts.length) }),
      ]),
      el('p', { className: 'ev__note', text: t('intake.conflictNothingConfirmed') }),
      el('ul', { className: 'ev__cards' }, conflicts.map((c) => conflictCard(c))),
    ])];
  }

  function conflictCard(conflict) {
    const resolved = conflict.resolved_claim_key;
    const sides = (conflict.sides || []).map((side) => el('li', {
      className: `ev__side${resolved === side.claim_key ? ' is-chosen' : ''}`,
    }, [
      // WHAT THE DOCUMENT WROTE, first and largest. `Feb 2021` is the
      // evidence; `2021-02` is somebody's reading of it, and showing only the
      // reading would hide the half she can actually check.
      el('p', { className: 'ev__sidedates', text: sideSpan(side) }),
      el('p', { className: 'ev__sidemeta', text: t('intake.conflictRead', { reading: sideReading(side) }) }),
      el('p', {
        className: 'ev__sidemeta',
        // Two keys rather than one with an "(s)". A catalogue with no plural
        // machinery still has to be readable in both languages, and "1
        // statements" is the kind of thing that makes a careful reader trust
        // the rest of the screen less.
        text: t(side.claim_count === 1 ? 'intake.conflictFromOne' : 'intake.conflictFrom', {
          sources: documentName(side.sources),
          n: side.claim_count,
        }),
      }),
      resolved === side.claim_key
        ? el('p', { className: 'ev__sidechosen', text: t('intake.conflictChosen') })
        : null,
      resolved
        ? null
        : button(t('intake.conflictChoose'), () => chooseSide(conflict, side.claim_key), {
          className: 'btn btn--accept',
          ariaLabel: t('intake.conflictChooseLabel', { dates: sideSpan(side) }),
        }),
    ].filter(Boolean)));

    return el('li', { className: 'ev__card ev__card--conflict' }, [
      el('div', { className: 'ev__cardhead' }, [
        el('p', { className: 'ev__proposal', text: conflict.employer || t('intake.conflictUnnamed') }),
        el('span', {
          className: `ev__tag ev__tag--${resolved ? 'done' : 'waiting'}`,
          text: resolved ? t('intake.conflictSettled') : t('intake.conflictOpen'),
        }),
      ]),
      el('p', {
        className: 'ev__meta',
        text: t('intake.conflictLede', { n: conflict.member_count }),
      }),
      el('ul', { className: 'ev__sides' }, sides),
      resolved
        ? el('div', { className: 'ev__actions' }, [
          button(t('intake.conflictReopen'), () => reopenSide(conflict), { className: 'btn btn--quiet' }),
        ])
        : null,
    ].filter(Boolean));
  }

  /** A period in the document's own words. */
  function sideSpan(side) {
    const start = side.start_original || t('intake.noDate');
    if (side.current) return t('intake.spanCurrent', { start });
    if (!side.end_original) return t('intake.spanOpen', { start });
    return t('intake.span', { start, end: side.end_original });
  }

  /** And the same period as this program read it. */
  function sideReading(side) {
    if (!side.start && !side.end) return t('intake.noReading');
    if (side.current) return t('intake.spanCurrent', { start: side.start || t('intake.noDate') });
    if (!side.end) return t('intake.spanOpen', { start: side.start || t('intake.noDate') });
    return t('intake.span', { start: side.start || t('intake.noDate'), end: side.end });
  }

  async function chooseSide(conflict, claimKey) {
    try {
      await api.resolveIntakeConflict(openPackage.package_id, conflict.conflict_group, claimKey);
      await reloadPackage();
      await paintPackage();
      bodyHost.prepend(announce(t('intake.conflictSaved'), 'ok'));
    } catch (error) {
      bodyHost.prepend(announce(error.userMessage || error.message, 'bad'));
    }
  }

  async function reopenSide(conflict) {
    await api.resolveIntakeConflict(openPackage.package_id, conflict.conflict_group, null, {
      reopen: true,
    });
    await reloadPackage();
    await paintPackage();
  }

  // ---------------------------------------------------------------------
  // where to start
  // ---------------------------------------------------------------------
  /**
   * An EMPLOYER's name, untranslated, because it is hers and because it is
   * what makes a heading recognisable at a glance. Everything else is a kind
   * of thing, and those are ours to say in her language.
   */
  function groupTitle(group) {
    return group.employer || t(`claim.type.${group.kind}`);
  }

  function groupsBlock(groups) {
    return el('section', { className: 'ev__group' }, [
      el('h4', { className: 'ev__grouphead' }, [
        el('span', { text: t('intake.groupsHeading') }),
        el('span', { className: 'ev__groupcount num', text: String(groups.length) }),
      ]),
      el('p', { className: 'ev__note', text: t('intake.groupsLede') }),
      el('ul', { className: 'ev__cards' }, groups.map((g) => groupCard(g))),
    ]);
  }

  function groupCard(group) {
    const title = groupTitle(group);
    // Its own modifier: a conflict card and a group card can both be headed
    // with the same employer, and "the card that says Contoso" is then two
    // different things on one screen.
    return el('li', { className: 'ev__card ev__card--group' }, [
      el('div', { className: 'ev__cardhead' }, [
        el('p', { className: 'ev__proposal', text: title }),
        el('span', {
          className: `ev__tag ev__tag--${group.waiting ? 'waiting' : 'done'}`,
          text: group.waiting
            ? t('intake.waiting', { n: group.waiting })
            : t('intake.allAnswered'),
        }),
      ]),
      groupPeriod(group),
      (group.kinds || []).length > 1
        ? el('p', {
          className: 'ev__meta',
          text: t('intake.groupKinds', {
            kinds: group.kinds.map((kind) => t(`claim.type.${kind}`)).join(', '),
          }),
        })
        : null,
      group.conflicted
        ? el('p', { className: 'ev__figure', text: t('intake.groupConflicted', { n: group.conflicted }) })
        : null,
      el('div', { className: 'ev__actions' }, [
        button(t('intake.openGroup'), () => showGroup(group), {
          className: 'btn',
          ariaLabel: t('intake.openGroupLabel', { name: title }),
        }),
      ]),
    ].filter(Boolean));
  }

  function groupPeriod(group) {
    const period = group.period || {};
    const one = group.total === 1;
    if (!period.start) {
      return el('p', {
        className: 'ev__meta',
        text: t(one ? 'intake.groupSizeOne' : 'intake.groupSize', { n: group.total }),
      });
    }
    return el('p', {
      className: 'ev__meta',
      text: t(one ? 'intake.groupPeriodOne' : 'intake.groupPeriod', {
        span: sideSpan({
          start_original: period.start ? period.start.original : null,
          end_original: period.end ? period.end.original : null,
          current: Boolean(period.current),
        }),
        n: group.total,
      }),
    });
  }

  function statesBlock(counts) {
    const chips = INTAKE_STATES.map((state) => {
      const n = counts[state] || 0;
      const chip = button(t('intake.stateChip', { state: t(`intake.state.${state}`), n }), () => {
        showState(state);
      }, { className: 'btn btn--quiet' });
      // Nothing to look at is not a thing to click. Disabled rather than
      // hidden: the state existing is worth knowing even at zero, exactly as
      // the server's summary keeps every zero.
      if (!n) chip.disabled = true;
      return chip;
    });
    return el('section', { className: 'ev__group' }, [
      el('h4', { className: 'ev__grouphead' }, [el('span', { text: t('intake.statesHeading') })]),
      el('div', { className: 'ev__actions' }, chips),
    ]);
  }

  // ---------------------------------------------------------------------
  // level three: one group, or one state
  // ---------------------------------------------------------------------
  async function showGroup(group) {
    await show({ kind: 'group', id: group.group_id, title: groupTitle(group) });
  }

  async function showState(state) {
    await show({ kind: 'state', id: state, title: t(`intake.state.${state}`) });
  }

  /**
   * Open one list. A failure SAYS SO.
   *
   * Without the catch a dropped connection left the button doing nothing at
   * all, which reads as a broken control rather than as a failed request.
   */
  async function show(list) {
    try {
      const payload = await api.getIntakeClaims(openPackage.package_id, selectorFor(list));
      openList = {
        ...list,
        claims: payload.claims || [],
        matched: payload.matched || 0,
        truncated: Boolean(payload.truncated),
      };
      intakeFilter = '';
      await paintPackage();
    } catch (error) {
      bodyHost.prepend(announce(error.userMessage || error.message, 'bad'));
    }
  }

  /**
   * One list, one selector. Derived from the list's own kind rather than
   * restated at each call site: `show` and `reloadList` used to spell the
   * same ternary twice, and a third selector added to one of them would have
   * left the other quietly reloading the wrong slice.
   */
  function selectorFor(list) {
    if (list.kind === 'group') return { group: list.id };
    if (list.kind === 'step') return { step: list.id };
    return { state: list.id };
  }

  async function reloadList() {
    if (!openList) return;
    const payload = await api.getIntakeClaims(openPackage.package_id, selectorFor(openList));
    openList = { ...openList, claims: payload.claims || [] };
  }

  function listView() {
    const claims = openList.claims;
    const waiting = claims.filter(isWaiting);
    // A claim held by an unsettled disagreement is NOT part of the batch.
    // "Not sure yet" would clear the marker that says why it is waiting, and
    // leave a screen saying she looked at something she cannot answer until
    // she answers a different question first.
    const settleable = waiting.filter((claim) => claim.review_state !== 'CONFLICT');
    const shown = filteredClaims(claims);

    const back = button(t('intake.backToPackage'), async () => {
      openList = null;
      await reloadPackage();
      await paintPackage();
    }, { className: 'btn btn--quiet' });

    // "Nothing matches that" and "there is nothing here" are different
    // sentences, and telling somebody their search failed when the state is
    // simply empty sends them looking for a typo.
    const nothing = () => el('li', {
      className: 'ev__none',
      text: claims.length ? t('intake.noMatch') : t('intake.emptyList'),
    });
    const list = el('ul', { className: 'ev__cards' }, shown.length
      ? shown.map((claim) => claimCard(claim))
      : [nothing()]);

    const search = el('input', {
      className: 'input',
      attrs: { type: 'search', id: 'ev-intake-search', placeholder: t('intake.searchPlaceholder') },
      props: { value: intakeFilter },
      on: {
        input: (event) => {
          intakeFilter = event.target.value;
          const next = filteredClaims(claims);
          replace(list, next.length ? next.map((claim) => claimCard(claim)) : [nothing()]);
        },
      },
    });

    return el('div', { className: 'ev__review' }, [
      el('div', { className: 'ev__reviewhead' }, [
        el('h3', { className: 'd-sec__head', text: openList.title }),
        back,
      ]),
      el('p', {
        className: 'ev__lede',
        text: waiting.length
          ? t('intake.listLede', { waiting: waiting.length, total: claims.length })
          : t('intake.listDone', { total: claims.length }),
      }),
      focused
        ? el('div', { className: 'ev__focus' }, [
          el('p', { className: 'ev__note', text: t('intake.focusedOn', { term: focused }) }),
          button(t('intake.dropFocus'), () => {
            focused = '';
            intakeFilter = '';
            paintPackage();
          }, { className: 'btn btn--quiet' }),
        ])
        : null,
      openList.truncated
        ? el('p', {
          className: 'ev__note',
          text: t('intake.truncated', { shown: claims.length, matched: openList.matched }),
        })
        : null,
      claims.length > 6 ? field('ev-intake-search', t('intake.search'), search) : null,
      settleable.length > 1 ? batchBlock(settleable) : null,
      list,
    ].filter(Boolean));
  }

  function filteredClaims(claims) {
    const needle = intakeFilter.trim().toLowerCase();
    if (!needle) return claims;
    return claims.filter((claim) => (claim.text || '').toLowerCase().includes(needle)
      || (claim.corrected_text || '').toLowerCase().includes(needle)
      || (claim.employer || '').toLowerCase().includes(needle)
      || ((claim.evidence || {}).quote || '').toLowerCase().includes(needle));
  }

  /**
   * The ONE batch action, and the reason the obvious one is missing.
   *
   * "Set the rest aside" writes UNRESOLVED, which creates no claim, changes
   * nothing about what she stands behind and can be undone one row at a time.
   * "Confirm all" would create verified facts that can end up on a real
   * application, from one click that cannot honestly mean she read them.
   */
  function batchBlock(waiting) {
    return el('div', { className: 'ev__batch' }, [
      el('p', { className: 'ev__note', text: t('intake.noBulkConfirm') }),
      el('div', { className: 'ev__actions' }, [
        button(t('intake.batchUnsure', { n: waiting.length }), async () => {
          if (!window.confirm(t('intake.batchUnsureConfirm', { n: waiting.length }))) return;
          for (const claim of waiting) {
            // Sequential on purpose: each answer is its own committed row, and
            // this way an interruption leaves the ones already answered
            // answered rather than half a transaction.
            await api.answerIntakeClaim(openPackage.package_id, claim.claim_key, 'UNRESOLVED');
          }
          await reloadList();
          await reloadPackage();
          await paintPackage();
        }, { className: 'btn btn--quiet' }),
      ]),
    ]);
  }

  /**
   * One proposed claim, with everything the package cited for it.
   *
   * The provenance is drawn ALWAYS -- the document it came from, the quote or
   * the place in it, and both halves of every date. A review whose citation
   * appears only sometimes teaches people to stop looking for it, and looking
   * for it is the entire mechanism by which a package written by somebody
   * else's model can be trusted at all.
   */
  function claimCard(claim) {
    const state = claim.review_state;
    const conflicted = state === 'CONFLICT';
    const answered = state === 'CONFIRMED' || state === 'CORRECTED_BY_USER'
      || state === 'REJECTED' || state === 'UNRESOLVED';
    const evidence = claim.evidence || {};
    const period = claim.period || {};

    const editor = el('textarea', {
      className: 'input ev__editor',
      attrs: { rows: '3', 'aria-label': t('cv.editLabel') },
      props: { value: claim.corrected_text || claim.text || '', hidden: true },
    });

    const answer = async (kind, text = null) => {
      try {
        await api.answerIntakeClaim(openPackage.package_id, claim.claim_key, kind, text);
        await reloadList();
        await reloadPackage();
        await paintPackage();
        if (onChanged) onChanged();
      } catch (error) {
        bodyHost.prepend(announce(error.userMessage || error.message, 'bad'));
      }
    };

    const saveEdit = button(t('cv.saveEdit'), () => {
      const text = editor.value.trim();
      if (text) answer('CORRECT', text);
    }, { className: 'btn btn--accept' });
    saveEdit.hidden = true;
    editor.addEventListener('input', () => { saveEdit.hidden = false; });

    const actions = [];
    if (conflicted) {
      // NO CONFIRM HERE, deliberately. Every claim about a role whose dates
      // are disputed carries those dates, so confirming one would write a
      // date two documents disagree about onto a fact she stands behind.
      // The disagreement is one question, answered once, upstairs.
      actions.push(button(t('intake.settleFirst'), async () => {
        openList = null;
        await reloadPackage();
        await paintPackage();
      }, { className: 'btn' }));
    } else if (!answered) {
      actions.push(button(t('intake.confirm'), () => answer('CONFIRM'), {
        className: 'btn btn--accept',
        ariaLabel: t('cv.acceptLabel', { text: claim.text }),
      }));
      actions.push(button(t('cv.edit'), () => {
        editor.hidden = !editor.hidden;
        if (!editor.hidden) editor.focus();
      }, { className: 'btn' }));
      actions.push(button(t('cv.reject'), () => answer('REJECT'), {
        className: 'btn btn--reject',
        ariaLabel: t('cv.rejectLabel', { text: claim.text }),
      }));
      actions.push(button(t('intake.unsure'), () => answer('UNRESOLVED'), {
        className: 'btn btn--quiet',
      }));
    } else if (state === 'REJECTED' || state === 'UNRESOLVED') {
      actions.push(button(t('intake.reopen'), () => answer('REOPEN'), { className: 'btn btn--quiet' }));
    } else {
      // CONFIRMED or CORRECTED. Undoing it is not an act of this review: the
      // claim exists in Career Evidence now and may already have prepared an
      // application somebody sent, so it is retired THERE, as a revision.
      actions.push(el('span', { className: 'ev__meta', text: t('intake.retireInLedger') }));
    }

    return el('li', {
      className: `ev__card${answered ? ` is-${stateClass(state)}` : ''}`
        + (conflicted ? ' ev__card--conflict' : ''),
    }, [
      el('div', { className: 'ev__cardhead' }, [
        el('p', { className: 'ev__proposal', text: claim.corrected_text || claim.text }),
        el('span', {
          className: `ev__tag ev__tag--${answered ? 'done' : 'waiting'}`,
          text: t(`intake.state.${state}`),
        }),
      ]),
      claim.corrected_text
        ? el('div', { className: 'ev__from' }, [
          el('span', { className: 'ev__fromlabel', text: t('intake.asItArrived') }),
          el('p', { className: 'quote quote--origin', text: claim.text }),
        ])
        : null,
      el('div', { className: 'ev__from' }, [
        el('span', {
          className: 'ev__fromlabel',
          text: t('intake.fromDocument', { document: documentName(claim.sources) }),
        }),
        evidence.quote
          ? el('p', { className: 'quote', text: evidence.quote })
          : el('p', { className: 'ev__meta', text: t('intake.foundIn', { where: evidence.locator }) }),
      ]),
      period.start ? datesBlock(period) : null,
      (claim.tools || []).length
        ? el('p', { className: 'ev__meta', text: t('intake.tools', { tools: claim.tools.join(', ') }) })
        : null,
      // A FIGURE, in the sentence she stated it in. "40%" on its own is a
      // number this program invented and cannot attribute to anything, so
      // there is nowhere in a package to put one and nowhere here to show one.
      ...(claim.metrics || []).map((metric) => el('p', {
        className: 'ev__figure',
        text: t('intake.figure', { sentence: metric.original }),
      })),
      conflicted
        ? el('p', { className: 'ev__figure', text: t('intake.claimConflicted') })
        : null,
      editor,
      saveEdit,
      el('div', { className: 'ev__actions' }, actions),
    ].filter(Boolean));
  }

  /**
   * Both halves of every date: what the document wrote, and how it was read.
   *
   * The reading is what the matcher can use; the wording is the evidence, and
   * it is the only half she can check. A package supplying only the reading
   * has thrown away the thing that would let her notice it was wrong, and a
   * screen showing only the reading does the same thing one layer later.
   */
  function datesBlock(period) {
    const wrote = sideSpan({
      start_original: period.start ? period.start.original : null,
      end_original: period.end ? period.end.original : null,
      current: Boolean(period.current),
    });
    const read = sideReading({
      start: period.start ? period.start.normalized : null,
      end: period.end ? period.end.normalized : null,
      current: Boolean(period.current),
    });
    return el('div', { className: 'ev__dates' }, [
      el('p', { className: 'ev__meta', text: t('intake.datesWrote', { span: wrote }) }),
      el('p', { className: 'ev__meta', text: t('intake.datesRead', { span: read }) }),
    ]);
  }

  function stateClass(state) {
    return {
      CONFIRMED: 'accepted',
      CORRECTED_BY_USER: 'edited',
      REJECTED: 'rejected',
      UNRESOLVED: 'unsure',
    }[state] || 'pending';
  }

  // =====================================================================
  // Reading a CV
  // =====================================================================
  /**
   * The file picker, the privacy sentence, and any review still open.
   *
   * The privacy sentence says what actually happens, which is that Career
   * Agent -- a Python program on this computer -- reads the file. It
   * deliberately does not claim the browser does it: that would be a nicer
   * sentence and a false one, and a false privacy claim is worse than none.
   */
  function intakeSection(imports) {
    // THE NATIVE FILE CONTROL, DRESSED.
    //
    // `<input type="file">` draws its own button and its own "no file chosen"
    // text, in the OPERATING SYSTEM'S language and nobody else's. On an
    // English page on a Portuguese machine it read "Escolher arquivo /
    // Nenhum arquivo escolhido", which is two words of another language in
    // the middle of a sentence about privacy, in a shape no other control in
    // this product has.
    //
    // The input is still the input: visually hidden rather than display:none,
    // so it keeps its place in the tab order and its `accept` list, and the
    // label is what a person sees and presses. A label with `for` opens the
    // picker on click, and the focus ring follows the input onto the label so
    // a keyboard reader can still see where they are.
    const chosen = el('span', { className: 'ev__filename', text: t('cv.noFile') });
    const input = el('input', {
      className: 'ev__file',
      attrs: {
        type: 'file',
        id: 'ev-file',
        accept: (imports.supported || []).join(','),
      },
      on: {
        change: (event) => {
          const file = event.target.files && event.target.files[0];
          chosen.textContent = file ? file.name : t('cv.noFile');
          readChosen(event.target);
        },
      },
    });

    const rows = (imports.imports || []).map((item) => {
      const confirmHost = el('div', { className: 'ev__confirmhost' });
      const open = async () => {
        openImport = await api.getCvReview(item.import_id);
        await paintReview();
      };
      return el('li', {
        className: `ev__import${item.archived ? ' ev__import--archived' : ''}`,
        dataset: { importId: item.import_id },
      }, [
        el('div', { className: 'ev__importhead' }, [
          el('span', { className: 'ev__importname', text: item.source_name }),
          el('span', {
            className: `ev__importstate ev__importstate--${item.archived ? 'archived'
              : item.status.toLowerCase()}`,
            text: item.archived ? t('cv.archivedState')
              : (item.pending ? t('cv.pendingCount', { n: item.pending }) : t('cv.reviewed')),
          }),
        ]),
        el('p', {
          className: 'ev__importmeta',
          text: t('cv.importMetaJobs', {
            experiences: item.entries,
            confirmed: item.confirmed,
            rejected: item.rejected,
            total: item.total,
          }),
        }),
        el('div', { className: 'ev__importactions' }, [
          item.archived
            ? button(t('cvr.restore'), async () => {
              await api.restoreCvImport(item.import_id);
              if (onChanged) onChanged();
              await refresh();
            }, { className: 'btn' })
            : button(item.pending ? t('cv.continueReview') : t('cv.reopenReview'), open,
              { className: 'btn' }),
          item.archived ? button(t('cv.inspect'), open, { className: 'btn btn--quiet' })
            : button(t('cvr.archive'), () => discard(item), {
              className: 'btn btn--quiet', attrs: { 'data-action': 'archive' },
            }),
          button(t('lifecycle.delete'), () => deleteFlow(confirmHost, {
            name: item.source_name,
            plan: async () => (await api.deleteCvImport(item.import_id)).plan,
            remove: () => api.deleteCvImport(item.import_id, { confirm: true }),
          }), { className: 'btn btn--quiet btn--danger', attrs: { 'data-action': 'delete' } }),
        ]),
        confirmHost,
      ]);
    });

    return section(t('cv.heading'), [
      // The catalogue first, the server's own sentence as the fallback. Both
      // say the same thing; only one of them is in the reader's language, and
      // a privacy promise nobody can read is not a promise.
      el('p', { className: 'ev__privacy', text: t('cv.privacy') || imports.privacy }),
      el('div', { className: 'ev__filerow' }, [
        input,
        el('label', { className: 'btn ev__filebtn', attrs: { for: 'ev-file' }, text: t('cv.choose') }),
        chosen,
      ]),
      el('p', {
        className: 'ev__hint',
        text: t('cv.supported', { kinds: (imports.supported || []).join(' ') }),
      }),
      el('p', { className: 'ev__note', text: t('cv.nothingConfirmed') }),
      rows.length
        ? el('ul', { className: 'ev__imports' }, rows)
        : null,
    ].filter(Boolean));
  }

  async function readChosen(input) {
    const file = input.files && input.files[0];
    if (!file) return;
    const busy = announce(t('cv.reading', { name: file.name }), 'busy');
    bodyHost.prepend(busy);
    try {
      const bytes = await file.arrayBuffer();
      openImport = await api.importCv(file.name, bytes);
      input.value = '';
      await paintReview();
    } catch (error) {
      busy.remove();
      bodyHost.prepend(announce(error.userMessage || error.message, 'bad'));
      input.value = '';
    }
  }

  /**
   * ARCHIVE a read. Reversible and immediate: every row stays, nothing waits,
   * and the row offers Restore. It used to be a hard delete with this name.
   */
  async function discard(item) {
    try {
      await api.archiveCvImport(item.import_id);
    } catch (error) {
      bodyHost.prepend(announce(error.userMessage || error.message, 'bad'));
      return;
    }
    if (onChanged) onChanged();
    await refresh();
    bodyHost.prepend(announce(t('cv.archivedFlash', { name: item.source_name }), 'ok'));
  }

  async function paintReview() {
    replace(bodyHost, [reviewView(openImport)]);
  }

  // =====================================================================
  // The review: a summary, then experiences, then suggestions. See
  // cv_review.js for why it is not one list of cards.
  // =====================================================================
  function reviewView(review) {
    return cvReview(review, {
      onBack: async () => {
        openImport = null;
        await refresh();
        if (onChanged) onChanged();
      },
      onChanged: () => { if (onChanged) onChanged(); },
    });
  }

  /**
   * WHAT CONFIRMING A FACT ACTUALLY REACHES.
   *
   * Confirming three hundred statements is an hour of somebody's evening, and
   * the reasonable expectation afterwards is that the recommendations move.
   * They do not. `career_agent.match` reads a `VerifiedClaim` in exactly two
   * modules -- `preparation.py` and `resume.py` -- and neither of them scores
   * anything: a posting is scored from what the employer wrote, and
   * eligibility is decided against the countries in her PREFERENCES.
   *
   * So this block is a promise the code keeps, and the promise is checked:
   * `tests/integration/test_evidence_reach.py` fails if a scoring module ever
   * learns to read a claim, which forces the copy to change with it rather
   * than quietly becoming a lie.
   *
   * It says the negative out loud on purpose. A screen that lets somebody
   * spend the hour believing otherwise is not being neutral about it.
   */
  /**
   * HOW THE EVIDENCE IS USED, ON REQUEST.
   *
   * This was a permanent five-line box teaching application preparation,
   * recommendation scoring, eligibility and where preferences live, above a
   * list of somebody's own experience. Every line of it is true and useful
   * once; none of it is what she came to read, and asking somebody to study
   * the scoring architecture before browsing their own career is the wrong
   * order.
   *
   * It is a disclosure now. The correction it exists to make -- that
   * confirming a fact moves no recommendation -- is the first line inside,
   * and the summary is the question a person would actually ask.
   */
  function usesBlock() {
    return el('details', { className: 'fold fold--quiet ev__uses' }, [
      el('summary', { className: 'fold__summary', text: t('ledger.uses.heading') }),
      el('ul', { className: 'ev__uses-list' }, [
        el('li', { className: 'ev__uses-yes', text: t('ledger.uses.prepare') }),
        el('li', { className: 'ev__uses-no', text: t('ledger.uses.notScore') }),
        el('li', { className: 'ev__uses-no', text: t('ledger.uses.notEligibility') }),
      ]),
      el('p', { className: 'ev__uses-where', text: t('ledger.uses.where') }),
    ]);
  }

  // =====================================================================
  // The ledger
  // =====================================================================
  /**
   * WHAT CAREER AGENT KNOWS ABOUT YOU.
   *
   * The second of the page's three questions, and the one most people come
   * for. One toolbar over the whole set -- search, a state filter, and a
   * selection mode that is off until asked for -- and then the categories,
   * compact, closed, with their counts on them.
   *
   * The filter offers ALL, CONFIRMED and SET ASIDE and no more, because those
   * are the only states a claim in this ledger has. "Needs review" and
   * "conflict" belong to an IMPORT, are answered upstairs, and a filter here
   * for either would be a control whose count is permanently zero.
   */
  function ledgerSection(ledger) {
    if (!ledger.candidate || !ledger.claims.length) {
      return section(t('ledger.heading'), [
        el('p', { className: 'ev__lede', text: t('ledger.empty') }),
        usesBlock(),
        addForm(),
      ]);
    }

    const list = el('div', { className: 'ev__cats' }, []);
    const repaint = () => replace(list, rowsFor(ledger));

    const search = el('input', {
      className: 'input ev__search',
      attrs: {
        type: 'search',
        id: 'ev-search',
        'aria-label': t('ledger.search'),
        placeholder: t('ledger.searchPlaceholder'),
      },
      props: { value: filterText },
      on: {
        input: (event) => {
          filterText = event.target.value;
          repaint();
        },
      },
    });

    // THE STATE FILTER, with its counts on it. A filter whose bucket is empty
    // still appears and still says zero: hiding it would answer "are any of
    // mine set aside" by removing the question.
    const standing = ledger.claims.filter((claim) => claim.verified).length;
    const aside = ledger.claims.length - standing;
    const filters = el('div', {
      className: 'segmented ev__states',
      attrs: { role: 'group', 'aria-label': t('ledger.filterLabel') },
    }, [
      ['ALL', ledger.claims.length],
      ['CONFIRMED', standing],
      ['ASIDE', aside],
    ].map(([key, n]) => {
      const node = button('', () => {
        stateFilter = key;
        paintStates();
        repaint();
      }, { className: 'segmented__btn' });
      node.dataset.state = key;
      replace(node, [
        el('span', { text: t(`ledger.state.${key}`) }),
        el('span', { className: 'ev__statecount num', text: String(n) }),
      ]);
      return node;
    }));

    function paintStates() {
      for (const node of filters.querySelectorAll('.segmented__btn')) {
        node.setAttribute('aria-pressed', String(node.dataset.state === stateFilter));
      }
    }

    // SELECTION IS A MODE, and its label says what turning it on is for.
    // "Select" beside a search box reads as a verb applied to the search.
    const selectToggle = button('', () => {
      selecting = !selecting;
      if (!selecting) selected.clear();
      paintSelectToggle();
      repaint();
      syncBulkBar();
    }, { className: 'btn btn--small' });

    function paintSelectToggle() {
      selectToggle.textContent = selecting ? t('ledger.selectDone') : t('ledger.selectStart');
      selectToggle.setAttribute('aria-pressed', String(selecting));
    }

    bulkBar = el('div', { className: 'ev__bulk', attrs: { role: 'status' } });
    bulkRepaint = () => {
      bulkBar.hidden = !selecting || selected.size === 0;
      if (bulkBar.hidden) {
        replace(bulkBar, []);
        return;
      }
      replace(bulkBar, [
        el('span', {
          className: 'ev__bulkcount num',
          text: t('ledger.selectedCount', { n: selected.size }),
        }),
        // REMOVE ONLY, and that is the whole of it. Removing is reversible and
        // creates nothing -- it writes a revision with `verified = false`, the
        // claim keeps its history and its evidence, and putting it back is one
        // press. A bulk CONFIRM is deliberately absent: it is "confirm
        // everything" wearing a checkbox, and nothing becomes true here
        // without a person saying so one claim at a time.
        button(t('ledger.bulkRetire'), async () => {
          // ONLY THE ONES THAT ARE STILL STANDING. A selection can hold claims
          // that are already set aside -- "select all here" takes the whole
          // group -- and asking the server to retire one of those is a 409
          // that says, correctly, that there is nothing to do. Sending it
          // anyway made a batch of twenty fail on its third claim.
          const live = new Set(
            (ledger.claims || []).filter((c) => c.verified).map((c) => c.claim_key),
          );
          const keys = [...selected].filter((key) => live.has(key));
          if (!keys.length) {
            selected.clear();
            syncBulkBar();
            return;
          }
          if (!window.confirm(t('ledger.bulkRetireConfirm', { n: keys.length }))) return;

          // ONE AT A TIME, AND ONE FAILURE DOES NOT ABANDON THE REST. Each
          // call returns the whole ledger, so this is deliberately sequential
          // rather than parallel -- twenty concurrent writes to one SQLite
          // file is how a batch earns a 500. What a failure must not do is
          // leave the reader with a half-done batch and no idea which half.
          let done = 0;
          const failed = [];
          for (const key of keys) {
            try {
              await api.retireClaim(key);
              done += 1;
            } catch (error) {
              failed.push(String(error.userMessage || error.message || error));
            }
          }
          selected.clear();
          await refresh();
          if (onChanged) onChanged();
          if (failed.length) {
            window.alert(t('ledger.bulkPartly', { done, failed: failed.length }));
          }
        }, { className: 'btn btn--small' }),
        button(t('ledger.selectClear'), () => {
          selected.clear();
          repaint();
          syncBulkBar();
        }, { className: 'btn btn--small btn--quiet' }),
      ]);
    };

    paintSelectToggle();
    paintStates();
    const built = section(t('ledger.heading'), [
      // ONE SENTENCE, and the architecture behind a disclosure. This used to
      // be a five-line box teaching preparation, scoring, eligibility and
      // where preferences live, in front of somebody who came to look at
      // their own experience.
      el('p', { className: 'ev__lede', text: t('ledger.lede') }),
      usesBlock(),
      el('div', { className: 'ev__tools' }, [search, filters, selectToggle]),
      bulkBar,
      list,
    ]);
    repaint();
    syncBulkBar();
    return built;
  }

  //: The bar is rebuilt by `ledgerSection`; these two let a row reach it
  //: without the row needing to know where it lives.
  let bulkBar = null;
  let bulkRepaint = null;

  function syncBulkBar() {
    if (bulkRepaint) bulkRepaint();
  }

  //: The claim types that read better as a word than as a paragraph.
  //
  //: A skill and a tool are one or two words each. Everything else here is a
  //: sentence somebody wrote about their working life and needs the room.
  const CHIP_TYPES = new Set(['SKILL', 'TOOL']);

  //: SELECTION MODE, and what is selected.
  //
  //: Off by default: a checkbox beside every row is a screen that asks to be
  //: processed, and most visits here are to read or to fix one thing. The
  //: reader turns it on, and turning it off clears the set -- a selection
  //: that survived being hidden would be a batch nobody could see.
  let selecting = false;
  const selected = new Set();

  //: WHICH STATES ARE SHOWING. `ALL`, `CONFIRMED` or `ASIDE`, and no more:
  //: those are the only states a claim in this ledger has. Anything about
  //: being unreviewed or in conflict belongs to an IMPORT and is answered
  //: upstairs, so a filter for it here would never count above zero.
  let stateFilter = 'ALL';

  //: How many rows a group draws before it offers the rest.
  //
  //: Not a page size -- there is no second request -- but a rendering budget.
  //: The largest group in the owner's own ledger is 168 skills, and a hundred
  //: and sixty-eight rows carrying an edit box and three buttons each is the
  //: wall this whole section exists to take down.
  const GROUP_PREVIEW = 12;

  /** Does this claim pass the state filter the toolbar is set to? */
  function passesState(claim) {
    if (stateFilter === 'CONFIRMED') return Boolean(claim.verified);
    if (stateFilter === 'ASIDE') return !claim.verified && claim.state !== 'DRAFT';
    return true;
  }

  /**
   * The work, grouped the way a CV is: one employer, one period, its lines.
   *
   * Two hundred and forty-three confirmed statements about work are not 243
   * jobs. They are the sentences under about eight roles, and a flat list of
   * them is the spreadsheet this section exists to stop being. The key is
   * employer AND period, because two spells at one company are two entries
   * and merging them would invent a tenure nobody claimed.
   *
   * Claims that name no employer are not filed under one. They get a group of
   * their own, LAST, saying so.
   */
  function employerGroups(claims) {
    const groups = new Map();
    for (const claim of claims) {
      const key = [claim.employer || '', claim.period_start || '', claim.period_end || ''].join('|');
      let group = groups.get(key);
      if (!group) {
        group = {
          employer: claim.employer || '',
          start: claim.period_start || '',
          end: claim.period_end || '',
          claims: [],
        };
        groups.set(key, group);
      }
      group.claims.push(claim);
    }
    return [...groups.values()].sort((a, b) => {
      if (Boolean(a.employer) !== Boolean(b.employer)) return a.employer ? -1 : 1;
      if (Boolean(a.start) !== Boolean(b.start)) return a.start ? -1 : 1;
      if (a.start !== b.start) return a.start < b.start ? 1 : -1;
      return a.employer.localeCompare(b.employer);
    });
  }

  /** An employer and its dates, as one line. Never a reformatted date. */
  function groupHeading(group) {
    const who = group.employer || t('ledger.employerNotStated');
    if (group.start && group.end) {
      return t('ledger.groupWhen', { who, start: group.start, end: group.end });
    }
    if (group.start) return t('ledger.groupFrom', { who, start: group.start });
    return who;
  }

  /**
   * THE CATEGORIES, AND WHAT OPENING ONE GIVES YOU.
   *
   * Every claim used to be rendered at once: 309 rows on one page, 933
   * buttons, and the page measured 45,692px. Grouping fixed the page and left
   * the CATEGORY unusable -- opening Experience was still 243 bordered cards
   * with four controls each.
   *
   * So a category is a compact row that says its name, its count and a line
   * of what is in it, and opening one gives a dense list rather than a stack
   * of panels. Experience is grouped again inside, by employer and period,
   * because that is the unit somebody actually remembers.
   *
   * SEARCHING OPENS EVERYTHING. A closed category that happens to hold the
   * match is a search that failed as far as the reader is concerned.
   */
  function rowsFor(ledger) {
    const needle = filterText.trim().toLowerCase();
    const shown = ledger.claims.filter((claim) => passesState(claim) && (!needle
      || claim.text.toLowerCase().includes(needle)
      || (claim.evidence || '').toLowerCase().includes(needle)
      || (claim.employer || '').toLowerCase().includes(needle)
      || claim.claim_type.toLowerCase().includes(needle)));
    if (!shown.length) {
      return [el('p', { className: 'ev__none', text: t('ledger.noMatch') })];
    }

    // TYPES first and in its order, then anything the server sent that this
    // build has not heard of. A claim type nobody listed must still appear:
    // dropping it would hide evidence rather than defer it.
    const seen = [...new Set(shown.map((claim) => claim.claim_type))];
    const order = [...TYPES.filter((type) => seen.includes(type)),
      ...seen.filter((type) => !TYPES.includes(type))];

    return order.map((type) => {
      const claims = shown.filter((claim) => claim.claim_type === type);
      const body = el('div', { className: 'evgroup__body' });
      let shownLimit = GROUP_PREVIEW;

      // SKILLS AND TOOLS ARE CHIPS, and everything else is rows.
      //
      // A skill is one or two words. Rendering 48 of them as full rows, each
      // with an edit box and three buttons, is the wall the grouping just
      // took down rebuilt one level in -- and it reads nothing like the thing
      // it describes, which is a list of words. Pressing a chip opens that
      // one claim's detail underneath, with the same edit and the same
      // removal. Nothing is lost.
      const asChips = CHIP_TYPES.has(type) && !selecting;
      // Grouping the WORK by employer, and only where the data carries one.
      const byEmployer = type === 'EMPLOYMENT'
        && claims.some((claim) => claim.employer)
        && !selecting && !needle;

      function paintChips() {
        const open = el('div', { className: 'evchip__open' });
        const chips = claims.map((claim) => {
          const node = button(claim.text, () => {
            const already = node.getAttribute('aria-expanded') === 'true';
            for (const other of body.querySelectorAll('.evchip')) {
              other.setAttribute('aria-expanded', 'false');
            }
            node.setAttribute('aria-expanded', String(!already));
            replace(open, already ? [] : [claimRow(claim, { expanded: true })]);
          }, {
            className: `evchip${claim.verified ? '' : ' evchip--retired'}`,
          });
          node.setAttribute('aria-expanded', 'false');
          return node;
        });
        replace(body, [el('div', { className: 'evchips' }, chips), open]);
      }

      function paint(limit) {
        shownLimit = limit;
        const rows = claims.slice(0, limit).map((claim) => claimRow(claim));
        if (claims.length > limit) {
          rows.push(el('div', { className: 'evgroup__more' }, [
            button(t('ledger.showAllInGroup', { n: claims.length }),
              () => paint(claims.length), { className: 'btn btn--small' }),
          ]));
        }
        replace(body, [el('div', { className: 'ev__rows' }, rows)]);
      }

      function paintByEmployer() {
        const folds = employerGroups(claims).map((group) => {
          // BUILT WHEN OPENED, not when drawn. Two hundred and forty-three
          // rows, each carrying a textarea and four buttons, is a thousand
          // nodes constructed for a screen showing twenty-four headings. A
          // closed `<details>` does not PAINT its contents, which is why this
          // was fast enough to miss, but it still builds them.
          const rows = el('div', { className: 'ev__rows' });
          let built = false;
          const fold = el('details', { className: 'fold fold--quiet ev__employer' }, [
            el('summary', { className: 'fold__summary' }, [
              el('span', { className: 'ev__employername', text: groupHeading(group) }),
              el('span', { className: 'ev__employercount num', text: String(group.claims.length) }),
            ]),
            rows,
          ]);
          fold.addEventListener('toggle', () => {
            if (!fold.open || built) return;
            built = true;
            replace(rows, group.claims.map((claim) => claimRow(claim)));
          });
          return fold;
        });
        replace(body, [
          // SAID OUT LOUD, BECAUSE IT IS VISIBLE. A CV and a LinkedIn export
          // name the same employer differently often enough that two headings
          // for one company is the normal case, and side by side it reads as
          // a bug. Nothing here merges them: deciding that two names are one
          // company is a judgement about her own history.
          el('p', { className: 'ev__lede ev__aside', text: t('ledger.twoNames') }),
          ...folds,
        ]);
      }

      if (asChips) paintChips();
      else if (byEmployer) paintByEmployer();
      else paint(GROUP_PREVIEW);

      // SELECT ALL IN THIS CATEGORY, and only while selecting. Scoped to what
      // is on screen rather than to the whole ledger: "all" across 309 claims
      // in four categories is a promise nobody can check before pressing it,
      // and the only bulk action takes things away.
      const all = selecting
        ? button(t('ledger.selectGroup'), (event) => {
          // The button is inside a `<summary>`, and a click there toggles the
          // disclosure. Selecting a category should not also close it.
          event.preventDefault();
          event.stopPropagation();
          const keys = claims.map((claim) => claim.claim_key);
          const already = keys.every((key) => selected.has(key));
          for (const key of keys) {
            if (already) selected.delete(key);
            else selected.add(key);
          }
          paint(shownLimit);
          syncBulkBar();
        }, { className: 'btn btn--small btn--quiet evgroup__all' })
        : null;

      return el('details', {
        className: 'evgroup',
        // Open only when a search or a filter put it here. Otherwise the
        // shape of somebody's evidence is what they meet, not its contents.
        props: { open: Boolean(needle) },
      }, [
        el('summary', { className: 'evgroup__summary' }, [
          el('span', { className: 'evgroup__label', text: t(`claimGroup.${type}`) }),
          el('span', { className: 'evgroup__count num', text: String(claims.length) }),
          el('span', { className: 'evgroup__preview', text: previewOf(claims) }),
          el('span', { className: 'evgroup__spacer' }),
          all,
        ].filter(Boolean)),
        body,
      ]);
    });
  }

  /**
   * A line of what is inside a closed category.
   *
   * Four bordered rectangles reading only "Experience 243" are a table of
   * contents with no contents. For words -- skills, tools -- the preview is
   * the first few of them, which is the most information per pixel this
   * screen can offer. For sentences it is the employers, because "Teem,
   * Pipefy, Globalfy" says more about 243 statements than any one of them.
   */
  function previewOf(claims) {
    const words = claims.filter((claim) => CHIP_TYPES.has(claim.claim_type));
    if (words.length) return words.slice(0, 5).map((claim) => claim.text).join(' · ');
    const employers = [...new Set(claims.map((claim) => claim.employer).filter(Boolean))];
    if (employers.length) return employers.slice(0, 4).join(' · ');
    return '';
  }

  /**
   * ONE CLAIM, AND THE SENTENCE IS THE POINT.
   *
   * This was a bordered card carrying a category label, a source label, the
   * claim, the line it was read from, an edit box, and three buttons. Three
   * of those fitted on a screen. A profile holds two hundred and forty-three.
   *
   * Everything except the sentence is now behind one control. The row shows
   * what she did, a small state chip, and where it came from in two words;
   * pressing "Details" opens the original text, the editor and the actions
   * for that one row. Provenance did not become less available -- it became
   * less permanent, which is the difference between an audit trail and a
   * wall.
   *
   * The CATEGORY LABEL is gone entirely. A row inside Experience does not
   * need to say "work you have done" two hundred and forty-three times.
   */
  function claimRow(claim, { expanded = false } = {}) {
    const hintKey = `ledger.hint.${claim.claim_type}`;
    const hint = t(hintKey);
    const editorHint = hint && hint !== hintKey
      ? el('p', { className: 'ev__hint', text: hint, props: { hidden: true } })
      : null;
    const exampleKey = `ledger.example.${claim.claim_type}`;
    const example = t(exampleKey);
    const editor = el('textarea', {
      className: 'input ev__editor',
      attrs: Object.assign(
        { rows: '3', 'aria-label': t('ledger.editLabel') },
        example && example !== exampleKey ? { placeholder: example } : {},
      ),
      props: { value: claim.text, hidden: true },
    });
    const save = button(t('ledger.save'), async () => {
      const text = editor.value.trim();
      if (!text) return;
      await api.editClaim(claim.claim_key, { text });
      await refresh();
      if (onChanged) onChanged();
    }, { className: 'btn btn--small btn--accept' });
    save.hidden = true;
    editor.addEventListener('input', () => { save.hidden = false; });

    // WHAT THE BUTTON DOES, IN THE WORDS OF THE RESULT. It used to say "Stop
    // using this", which names an internal state transition and sounds like a
    // warning. What actually happens is that the claim stops being drawn on
    // when an application is prepared: it is removed from the profile, not
    // deleted, and every revision and quote stays.
    const toggle = claim.verified
      ? button(t('ledger.retire'), async () => {
        if (!window.confirm(t('ledger.retireConfirm', { text: claim.text }))) return;
        await api.retireClaim(claim.claim_key);
        await refresh();
        if (onChanged) onChanged();
      }, { className: 'btn btn--small btn--quiet' })
      : button(claim.state === 'DRAFT' ? t('ledger.confirmDraft') : t('ledger.confirm'), async () => {
        await api.confirmClaim(claim.claim_key);
        await refresh();
        if (onChanged) onChanged();
      }, { className: 'btn btn--small btn--accept' });

    // The detail half: closed unless asked for, and it holds everything that
    // used to be permanently on screen.
    const detail = el('div', { className: 'evrow__detail', props: { hidden: !expanded } }, [
      claim.evidence_differs
        ? el('div', { className: 'ev__from' }, [
          el('span', { className: 'ev__fromlabel', text: t('ledger.origin') }),
          el('p', { className: 'quote quote--origin', text: claim.evidence }),
        ])
        : null,
      // THE SHORT NAME, because the sentence supplies the preposition.
      // `ledger.source.*` reads "From LinkedIn", which inside "Where this
      // came from: ..." renders "Where this came from: From LinkedIn".
      el('p', {
        className: 'evrow__where',
        text: claim.employer
          ? t('ledger.cameFromAt', {
            source: t(`ledger.sourceShort.${claim.source}`),
            employer: claim.employer,
          })
          : t('ledger.cameFrom', { source: t(`ledger.sourceShort.${claim.source}`) }),
      }),
      claim.revisions > 1
        ? el('p', {
          className: 'evrow__where num',
          text: t('ledger.revision', { n: claim.revision }),
        })
        : null,
      editorHint,
      editor,
      el('div', { className: 'ev__actions' }, [
        button(t('ledger.edit'), () => {
          editor.hidden = !editor.hidden;
          if (editorHint) editorHint.hidden = editor.hidden;
          if (!editor.hidden) editor.focus();
        }, { className: 'btn btn--small' }),
        save,
        toggle,
      ]),
    ].filter(Boolean));

    const more = button(t('ledger.details'), () => {
      detail.hidden = !detail.hidden;
      more.setAttribute('aria-expanded', String(!detail.hidden));
    }, { className: 'btn btn--small btn--quiet evrow__more' });
    more.setAttribute('aria-expanded', String(expanded));

    // The selection box, and it is only built when selecting. An
    // always-present control set to `hidden` still reaches the accessibility
    // tree in some readers, and a checkbox nobody can see is one nobody can
    // explain.
    const pick = selecting
      ? el('input', {
        className: 'checkbox ev__pick',
        attrs: {
          type: 'checkbox',
          'aria-label': t('ledger.selectOne', { text: claim.text.slice(0, 60) }),
        },
        props: { checked: selected.has(claim.claim_key) },
        on: {
          change: (event) => {
            if (event.target.checked) selected.add(claim.claim_key);
            else selected.delete(claim.claim_key);
            syncBulkBar();
          },
        },
      })
      : null;

    return el('div', {
      className: `evrow${claim.verified ? '' : ' evrow--aside'}`
        + (selecting ? ' is-selectable' : ''),
    }, [
      el('div', { className: 'evrow__line' }, [
        pick,
        el('p', { className: 'evrow__text', text: claim.text }),
        // STATUS IS SMALL AND QUIET. Confirmed should feel settled; the chip
        // is drawn only for the state that is NOT settled, so a screen of
        // confirmed evidence carries no chips at all and anything set aside
        // stands out by being the only marked thing on it.
        // A DRAFT was never confirmed; only a withdrawn claim is "set aside".
        claim.verified
          ? null
          : el('span', { className: 'badge evrow__state',
            text: claim.state === 'DRAFT' ? t('ledger.draftTag') : t('ledger.retiredTag') }),
        el('span', { className: 'evrow__src', text: t(`ledger.sourceShort.${claim.source}`) }),
        more,
      ].filter(Boolean)),
      detail,
    ]);
  }

  /**
   * Writing down something you have done, in your own words.
   *
   * Recorded as SELF_ATTESTED and citing nothing, which is the accurate
   * provenance: there is no document behind it, and copying the sentence into
   * the evidence field would manufacture a citation that cites itself.
   */
  /**
   * ADDING SOMETHING, CATEGORY FIRST AND IN YOUR OWN WORDS.
   *
   * It was one textarea and a dropdown of eight claim types, with the same
   * placeholder whatever you picked. Two things were wrong with that. A
   * person adding "SQL" had to write it into a three-row paragraph box, and a
   * person adding a job had no idea what this product wanted -- they could
   * tell a line was wrong and still not know what to write instead.
   *
   * So the CATEGORY is chosen first and the form follows it: the box becomes
   * a single line for a skill or a tool and stays a paragraph for the rest,
   * and the hint and the example change with it. The guidance is the same
   * text the edit flow uses, so what somebody is told when they add is what
   * they are told when they fix.
   *
   * The lede says the part that matters most: you do not have to write this
   * in any particular way. Nothing here parses a format.
   */
  function addForm() {
    const status = el('p', { className: 'ev__note', attrs: { role: 'status', 'aria-live': 'polite' } });
    const hint = el('p', { className: 'ev__hint' });
    // Both boxes exist; which one is shown follows the category. Keeping both
    // rather than rebuilding one means a half-typed line survives a change of
    // mind about which category it belongs to.
    const line = el('input', {
      className: 'input',
      attrs: { type: 'text', id: 'ev-new-text' },
    });
    const para = el('textarea', {
      className: 'input',
      attrs: { id: 'ev-new-text-long', rows: '3' },
    });
    const kind = el('select', { className: 'select', attrs: { id: 'ev-new-type' } },
      TYPES.map((type) => el('option', { attrs: { value: type }, text: t(`claimGroup.${type}`) })));

    //: A skill and a tool are a word. Everything else is a sentence.
    const SHORT = new Set(['SKILL', 'TOOL']);
    const box = () => (SHORT.has(kind.value) ? line : para);
    // The label names whichever box is showing. It used to point at the
    // one-line box only, so the paragraph box -- shown for every category but
    // skills and tools -- reached a screen reader with no name at all.
    const boxLabel = el('label', {
      className: 'field__label',
      attrs: { for: 'ev-new-text' },
      text: t('ledger.addText'),
    });

    function follow() {
      const short = SHORT.has(kind.value);
      line.hidden = !short;
      para.hidden = short;
      boxLabel.htmlFor = short ? 'ev-new-text' : 'ev-new-text-long';
      const hintKey = `ledger.hint.${kind.value}`;
      const hintText = t(hintKey);
      hint.textContent = hintText === hintKey ? '' : hintText;
      hint.hidden = !hint.textContent;
      const exampleKey = `ledger.example.${kind.value}`;
      const example = t(exampleKey);
      const placeholder = example === exampleKey ? '' : example;
      line.setAttribute('placeholder', placeholder);
      para.setAttribute('placeholder', placeholder);
    }
    kind.addEventListener('change', follow);
    follow();

    const text = { get value() { return box().value; }, set value(v) { box().value = v; } };

    // THE FOLD PRIMITIVE, like every other disclosure on the page. It was a
    // bare `<details>` with a caret and 12.5px text, which read as a footnote
    // to the ledger rather than as one of the three things this page is for.
    return el('details', { className: 'fold ev__add' }, [
      el('summary', { className: 'fold__summary', text: t('ledger.addHeading') }),
      el('p', { className: 'ev__note', text: t('ledger.addLede') }),
      field('ev-new-type', t('ledger.addType'), kind),
      el('div', { className: 'ev__addbox' }, [
        boxLabel,
        hint,
        line,
        para,
      ]),
      button(t('ledger.addSubmit'), async () => {
        const value = text.value.trim();
        if (!value) {
          status.textContent = t('ledger.addEmpty');
          return;
        }
        try {
          await api.createClaim({ claim_type: kind.value, text: value });
          text.value = '';
          await refresh();
          if (onChanged) onChanged();
        } catch (error) {
          status.textContent = error.userMessage || error.message;
        }
      }, { className: 'btn btn--accept' }),
      status,
    ]);
  }

  function section(heading, children) {
    return el('section', { className: 'd-sec' }, [
      el('h3', { className: 'd-sec__head', text: heading }),
      ...children,
    ]);
  }

  /**
   * Draw the same content into a PAGE instead of into the overlay.
   *
   * One renderer, two hosts. The overlay stays because the Prepare tab opens
   * it in place -- somebody comparing a requirement against her evidence
   * should not lose the posting to do it -- and the page exists because
   * reviewing forty proposals is not a thing to do inside a dialog on top of
   * a job.
   */
  function mount(host) {
    if (bodyHost.parentElement !== host) {
      host.replaceChildren(bodyHost);
    }
    bodyHost.classList.add('ev--page');
    refresh();
  }

  return { root, open, close, refresh, mount, focusOn };
}
