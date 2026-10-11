/**
 * preferences.js -- editing what counts as a good job, without the YAML.
 *
 * The scoring policy is 1,100 lines of configuration, and until this panel
 * existed changing one phrase meant finding it by hand, remembering to bump a
 * version, and re-running a command. This is the half of that workflow a
 * person should actually see: the phrases, grouped by what they do to a score.
 *
 * THREE CATEGORIES, AND THE DIFFERENCE MATTERS. A desired phrase adds match
 * value. A negative phrase subtracts it and the job stays visible. A hard
 * exclusion removes the job from the eligible view -- and a posting has to SAY
 * one, because silence never excludes. The panel states that next to the
 * category rather than assuming the distinction is obvious, since getting it
 * wrong is how somebody accidentally hides half their results.
 *
 * SAVING IS NOT SCORING. A write changes the configuration version; the 18,549
 * stored scores were computed under the old one. The panel says so and does
 * not pretend the list behind it has updated, because silently showing stale
 * scores under a new configuration is the drift this project already reports
 * on elsewhere.
 */

import { el, button, replace } from './dom.js';
import { tagInput } from './tags.js';
import { t } from './i18n.js';
import { getSearchReview, patchSearchReview } from './api.js';

/**
 * A label the SERVER sent, in the reader's language.
 *
 * The key wins where the catalogue knows it; the English the server composed
 * is the fallback for a category this build has never heard of, which is
 * better than a bare identifier and visible as untranslated rather than as
 * missing. Same rule as `profile.js`.
 */
function keyed(key, fallback) {
  if (!key) return fallback || '';
  const text = t(key);
  return text === key ? (fallback || '') : text;
}

/**
 * @param {HTMLElement} mount
 * @param {object} data -- the /api/preferences payload
 * @param {(patch: object) => Promise<object>} save
 */
export function renderPreferences(mount, data, save) {
  const categories = data.categories || [];
  const signals = data.signals || [];
  // **HOW MANY POSTINGS EACH PHRASE GROUP ACTUALLY REACHES.**
  //
  // Without it this screen is a phrase editor operated blind: add a phrase,
  // remove a phrase, and find out what happened by rescoring nineteen
  // thousand postings and reading the list. Keyed by signal so a group can
  // find its own row without the payload having to be ordered.
  const reach = new Map((data.reach || []).map((row) => [row.signal, row]));
  const population = Number(data.reach_population) || 0;

  const children = [
    searchReview(),
    el('p', { className: 'prefs__intro' }, [
      el('span', {
        text: t('prefs.lede'),
      }),
    ]),
    el('p', { className: 'prefs__help', text: t('prefs.reachRevision', {
      version: data.reach_config_version ?? t('value.notStated'),
    }) }),
  ];

  for (const category of categories) {
    const mine = signals.filter((signal) => signal.category === category.id);
    if (!mine.length) continue;

    const group = el('details', { className: `prefs__cat prefs__cat--${category.id}` });
    group.appendChild(el('summary', { className: 'prefs__summary' }, [
      el('span', { className: 'prefs__cat-label', text: keyed(category.label_key, category.label) }),
      el('span', { className: 'prefs__cat-count', text: String(mine.length) }),
    ]));
    group.appendChild(el('p', {
      className: 'prefs__help',
      text: keyed(category.help_key, category.help),
    }));

    for (const signal of mine) {
      group.appendChild(signalEditor(signal, save, reach.get(signal.signal_id), population));
    }
    children.push(group);
  }

  if (!children.length) {
    children.push(el('p', { className: 'prefs__empty', text: t('prefs.none') }));
  }

  replace(mount, children);
  mount.className = 'prefs';
  return mount;
}

/**
 * One phrase group: what it says, what it reaches, and what changing it costs.
 *
 * THE REACH LINE
 * --------------
 * Measured on the owner's corpus 2026-09-08, the numbers this surfaces are not
 * decoration: `documentation_practice` matches 6,506 postings and
 * `selling_hubspot` matches none at all.
 *
 * Neither is a verdict. "No posting says this" and "this work does not exist"
 * are different statements and only she can tell them apart -- but she cannot
 * tell them apart without the number, and until now the screen did not have it.
 *
 * A hard exclusion says NOT MEASURED rather than zero. It is a rule the
 * eligibility gate applies, not a phrase the scorer records, so a zero there
 * would claim something this count has never looked at.
 */
function signalEditor(signal, save, reach, population) {
  // Declared before the control that writes to them: the tag input's
  // `onChange` retracts a pending confirmation, and a `const` referenced
  // before its line is a temporal dead zone rather than a hoisted variable.
  const status = el('span', { className: 'prefs__status', text: '' });
  const root = el('div', { className: 'prefs__signal', dataset: { signalId: signal.signal_id } });

  // CHIPS, not a textarea of one phrase per line.
  //
  // A phrase group is a SET of short strings, and a set is what a chip cloud
  // is for. The textarea made it look like prose: there was no feedback that
  // six lines had been understood as six phrases, removing the middle one
  // meant re-typing around it, and a stray character produced a phrase nobody
  // noticed until the reach count moved. The country list two screens away had
  // already been through this and reached the same answer.
  //
  // The two-press flow is untouched. A phrase group decides what counts as the
  // work she wants across every posting in the corpus, so the first press
  // still SHOWS the change and the second still makes it.
  let phrases = [...(signal.patterns || [])];
  const phraseState = el('p', { className: 'prefs__phrase-state', text: t('prefs.savedPhrases') });
  const tags = tagInput({
    id: `prefs-${signal.signal_id}`,
    label: t('prefs.phrasesFor', { label: signal.label }),
    values: phrases,
    placeholder: t('prefs.addPhrase'),
    hint: t('prefs.scoringPhrasesHint'),
    // Phrases are matched case-insensitively and are the reader's own words,
    // so the only normalisation is the whitespace nobody meant to type.
    normalise: (part) => part.trim().replace(/\s+/g, ' '),
    autocapitalize: 'off',
    onChange: (values) => {
      phrases = values;
      // A pending confirmation describes the OLD edit. Changing the set again
      // must retract it rather than let a second press commit a diff the
      // reader was never shown.
      pending = null;
      diff.hidden = true;
      status.textContent = '';
      status.className = 'prefs__status';
      const original = signal.patterns || [];
      const edited = values.length !== original.length || values.some((p) => !original.includes(p));
      phraseState.textContent = t(edited ? 'prefs.unsavedPhrases' : 'prefs.savedPhrases');
    },
  });

  //: What the pending press would change, or null when nothing is pending.
  //: A phrase group decides what counts as the work she wants across every
  //: posting in the corpus, so the first press SHOWS the change and the second
  //: makes it. Two presses rather than a modal: the diff belongs beside the
  //: text it describes, and a dialogue that covers the textarea is a dialogue
  //: that has to be dismissed to check what it is talking about.
  let pending = null;

  const diff = el('p', { className: 'prefs__diff', props: { hidden: true } });

  const saveButton = button(t('action.save'), async () => {
    const patterns = [...phrases];
    if (!patterns.length) {
      // Refused by the server too; said here so the person is not made to
      // press a button to be told no.
      status.textContent = t('prefs.atLeastOne');
      status.className = 'prefs__status prefs__status--bad';
      return;
    }
    const before = signal.patterns || [];
    const added = patterns.filter((p) => !before.includes(p));
    const removed = before.filter((p) => !patterns.includes(p));
    if (!added.length && !removed.length) {
      // Nothing moved. Saying so beats a save that bumps the configuration
      // version and asks for a recalculation over an edit that changed
      // nothing -- which is exactly what `set_candidate_fields` refuses to do
      // one layer down.
      status.textContent = t('prefs.noChange');
      status.className = 'prefs__status';
      diff.hidden = true;
      pending = null;
      return;
    }
    const signature = JSON.stringify(patterns);
    if (pending !== signature) {
      pending = signature;
      diff.hidden = false;
      diff.textContent = [
        added.length ? t('prefs.diffAdded', { list: added.join(', ') }) : '',
        removed.length ? t('prefs.diffRemoved', { list: removed.join(', ') }) : '',
        t('prefs.diffCost'),
      ].filter(Boolean).join(' ');
      status.textContent = t('prefs.confirm');
      status.className = 'prefs__status';
      return;
    }
    pending = null;
    diff.hidden = true;
    saveButton.disabled = true;
    status.textContent = t('prefs.saving');
    status.className = 'prefs__status';
    try {
      const result = await save({
        category: signal.category,
        signal_id: signal.signal_id,
        patterns,
      });
      signal.patterns = [...patterns];
      phraseState.textContent = t('prefs.savedPhrases');
      status.textContent = result.rescore_required
        ? t('prefs.savedRescore')
        : t('prefs.saved');
      status.className = 'prefs__status prefs__status--ok';
    } catch (error) {
      status.textContent = error.userMessage || error.message;
      status.className = 'prefs__status prefs__status--bad';
    } finally {
      saveButton.disabled = false;
    }
  }, { className: 'prefs__save' });

  root.appendChild(el('label', { className: 'prefs__label', text: signal.label }));
  root.appendChild(el('p', { className: 'prefs__provenance',
    text: t(`review.${signal.origin || 'unknown_provenance'}`) }));
  root.appendChild(phraseState);
  root.appendChild(tags.root);
  root.appendChild(reachLine(reach, population, signal.category));
  root.appendChild(diff);
  root.appendChild(el('div', { className: 'prefs__row' }, [saveButton, status]));
  return root;
}

/** Stored lexical reach is distinct from body scoring and unsaved edits. */
function reachLine(reach, population, category) {
  if (!reach) return el('p', { className: 'prefs__reach', props: { hidden: true } });
  if (!reach.measured) {
    return el('p', {
      className: 'prefs__reach prefs__reach--unmeasured',
      text: t('prefs.reachUnmeasured'),
    });
  }
  const n = Number(reach.postings) || 0;
  const lexical = el('p', {
    className: n === 0 ? 'prefs__reach prefs__reach--none' : 'prefs__reach',
    text: n === 0
      ? t('prefs.reachNone', { total: population.toLocaleString() })
      : t('prefs.reach', {
        n: n.toLocaleString(),
        total: population.toLocaleString(),
        pct: reach.share === null || reach.share === undefined ? '' : reach.share,
      }),
  });
  if (category !== 'desired') return lexical;
  const body = reach.positive_body_postings;
  return el('div', { className: 'prefs__reach-details' }, [
    lexical,
    el('p', { className: 'prefs__help', text: t('prefs.contextReach') }),
    el('p', { className: 'prefs__body-reach', text: body === null || body === undefined
      ? t('prefs.bodyReachUnmeasured')
      : t('prefs.bodyReach', { n: Number(body).toLocaleString() }) }),
  ]);
}

/** Explicit review, with a preview bound to the exact saved configuration. */
function searchReview() {
  const root = el('details', { className: 'search-review' });
  root.appendChild(el('summary', { text: t('review.title') }));
  root.appendChild(el('p', { text: t('review.help') }));
  const rows = el('div');
  root.appendChild(rows);
  let loaded = false;
  async function load() {
    try {
      const report = await getSearchReview();
      replace(rows, report.rows.map(row => {
        const item = el('details', { dataset: { reviewPath: row.path } });
        item.appendChild(el('summary', { text: `${row.path}: ${t(`review.${row.origin}`)}` }));
        const show = value => Array.isArray(value) ? value.join('\n') : JSON.stringify(value, null, 2);
        item.appendChild(el('p', { text: t('prefs.savedPhrases') }));
        item.appendChild(el('pre', { text: show(row.value) }));
        item.appendChild(el('p', { text: t('review.legacy') }));
        item.appendChild(el('pre', { text: show(row.legacy_value) }));
        const editor = el('textarea', { props: { value: show(row.value), hidden: true },
          attrs: { 'aria-label': t('review.editValue', { path: row.path }) } });
        const status = el('p', { className: 'review-status', attrs: { role: 'status' } });
        const preview = el('pre', { className: 'review-diff' });
        let pending = null;
        const confirm = button(t('review.confirm'), async () => {
          if (!pending) return;
          confirm.disabled = true;
          try {
            const result = await patchSearchReview(pending);
            await load();
            rows.prepend(el('p', { className: 'review-saved', text: t(result.rescore_required
              ? 'prefs.savedRescore' : 'review.kept') }));
          } catch (error) { status.textContent = error.userMessage || error.message; }
          finally { confirm.disabled = false; }
        });
        confirm.hidden = true;
        const invalidate = () => { pending = null; confirm.hidden = true; preview.textContent = ''; };
        editor.addEventListener('input', invalidate);
        async function plan(action) {
          invalidate();
          try {
            let value = row.value;
            if (action === 'edit') {
              if (editor.hidden) { editor.hidden = false; editor.focus(); return; }
              value = Array.isArray(row.value) ? editor.value.split('\n').map(v => v.trim()).filter(Boolean)
                : typeof row.value === 'string' ? editor.value.replace(/^"|"$/g, '') : JSON.parse(editor.value);
            }
            const patch = { path: row.path, action, value, expected_hash: report.document_hash };
            const result = await patchSearchReview(patch);
            preview.textContent = result.diff.map(change =>
              `${change.path}\n− ${show(change.before)}\n+ ${show(change.after)}`).join('\n\n');
            status.textContent = t(result.rescore_required ? 'prefs.diffCost' : 'review.keepCost');
            pending = { ...patch, confirmation: result.confirmation };
            confirm.hidden = false;
          } catch (error) { status.textContent = error.userMessage || error.message; }
        }
        const actions = el('div', { className: 'prefs__row' }, [
          button(t('review.keep'), () => plan('keep'), { className: 'review-keep' }),
        ]);
        if (row.editable) {
          actions.appendChild(button(t('review.edit'), () => plan('edit'), { className: 'review-edit' }));
        }
        if (row.removable) {
          actions.appendChild(button(t('review.remove'), () => plan('remove'), { className: 'review-remove' }));
        }
        item.append(editor, actions, preview, status, confirm);
        return item;
      }));
    } catch (error) { rows.textContent = error.userMessage || error.message; loaded = false; }
  }
  root.addEventListener('toggle', () => { if (root.open && !loaded) { loaded = true; load(); } });
  return root;
}
