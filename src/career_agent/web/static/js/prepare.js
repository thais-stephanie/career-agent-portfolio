/**
 * prepare.js -- the third drawer tab. What they ask for, beside what you have.
 *
 * The other two tabs answer "what is this job" and "why this number". This one
 * answers the question somebody actually has at the moment of deciding: which
 * of these requirements could I speak to in an interview, and which could I
 * not.
 *
 * FOUR RULES, and each is visible in the code rather than only intended.
 *
 * **Nothing is hidden for being unmet.** Every requirement the posting fired is
 * drawn, in every state, and the four states are rendered in the order
 * MATCHED, PARTIAL, GAP, UNRESOLVED -- which puts the gaps in the middle of
 * the page rather than under a fold. There is no control that filters them
 * out, because the server has no parameter for it.
 *
 * **A match is never asserted without the sentence behind it.** A row claiming
 * MATCHED or PARTIAL draws the employer's quote, the candidate's own confirmed
 * line, WHERE that line came from, and the configured phrase that connected
 * the two. A reader can check the match instead of trusting it.
 *
 * **The four states never rely on colour.** Each carries a word and a mark.
 * The palette is soft pastel by design, and four pastels are four backgrounds
 * a person cannot rank by looking at them.
 *
 * **A disagreement is a margin note.** The verdict controls record what the
 * person thinks; they change no score, rewrite no quote and create no claim.
 * The mapping stays on screen beside the disagreement, because a correction
 * that erased what it corrected would destroy the thing being corrected.
 */

import { el, button, replace } from './dom.js';
import { t } from './i18n.js';
import * as api from './api.js';

/**
 * The four states, in the order they are drawn, with the mark each carries.
 *
 * The mark is a text character and not an icon font: it survives a copied
 * page, a screen reader reading the row aloud, and a person who cannot tell
 * mint from butter.
 */
const READINESS = [
  // A plain hyphen for a gap, and not a dash: `scripts/punctuation_check.py`
  // forbids U+2014 and U+2013 in text this project wrote. It reads correctly
  // anyway -- a gap is an ABSENCE, and an absence is a blank rather than a
  // cross. A cross would say the requirement was failed, which is a different
  // and worse claim.
  { key: 'MATCHED', mark: '✓' },
  { key: 'PARTIAL', mark: '≈' },
  { key: 'GAP', mark: '-' },
  { key: 'UNRESOLVED', mark: '?' },
];

/** The four answers somebody may give about one requirement. */
const VERDICTS = ['SUPPORTS', 'PARTIALLY_SUPPORTS', 'DOES_NOT_SUPPORT', 'EVIDENCE_MISSING'];

/** Where a confirmed line came from, in the words a person would use. */
const SOURCE_KEYS = {
  RESUME: 'ledger.source.RESUME',
  LINKEDIN: 'ledger.source.LINKEDIN',
  SELF_ATTESTED: 'ledger.source.SELF_ATTESTED',
  DOCUMENT: 'ledger.source.DOCUMENT',
};

/**
 * The Prepare panel for one job.
 *
 * `onEvidence` is called when somebody says a requirement is answered by
 * experience that is not in the profile. It routes to the evidence ledger and
 * writes NOTHING: turning that click into a claim would be this program
 * inventing experience, which is the one thing it exists not to do.
 */
export function createPrepare({ onEvidence = null } = {}) {
  const host = el('div', { className: 'prep' });
  let jobId = null;
  let token = 0;

  async function load(id) {
    jobId = id;
    const mine = ++token;
    replace(host, [el('div', { className: 'sk sk--block' })]);
    try {
      const payload = await api.getPreparation(id);
      if (mine !== token) return;
      replace(host, sections(payload));
    } catch (error) {
      if (mine !== token) return;
      replace(host, [
        el('p', { className: 'state__msg', text: error.userMessage || error.message }),
        button(t('prep.retry'), () => load(id), { className: 'btn' }),
      ]);
    }
  }

  function reset() {
    token += 1;
    jobId = null;
    replace(host, []);
  }

  function sections(payload) {
    return [
      summary(payload),
      concerns(payload),
      requirements(payload),
      resumeSection(payload),
      checklist(payload),
    ].filter(Boolean);
  }

  /**
   * What to lead with, behind a disclosure, loaded when it is opened.
   *
   * Closed by default because it answers a later question than the one this
   * tab opens on: which of these could I speak to comes before how would I
   * arrange them. Loaded on expand rather than with the panel, so reading the
   * requirements does not cost a second request.
   */
  function resumeSection(payload) {
    if (!payload.evidence_available) return null;
    const body = el('div', {});
    const disclosure = el('details', { className: 'prep__resume' }, [
      el('summary', { className: 'prep__reviewsummary', text: t('resume.heading') }),
      el('p', { className: 'prep__meaning', text: t('resume.lede') }),
      body,
    ]);
    disclosure.addEventListener('toggle', async () => {
      if (!disclosure.open || body.firstChild) return;
      replace(body, [el('div', { className: 'sk sk--block' })]);
      try {
        replace(body, resumeBody(await api.getResumePlan(jobId)));
      } catch (error) {
        replace(body, [
          el('p', { className: 'state__msg', text: error.userMessage || error.message }),
        ]);
      }
    });
    return disclosure;
  }

  /**
   * The selection, as something she can take to the employer's own form.
   *
   * A COPY BUTTON AND THE TEXT ITSELF, not a download. The text is already in
   * the payload, so a second request would be a second chance for the two to
   * disagree about what the plan is -- and a `<textarea>` beneath the button
   * is what makes the control honest when the clipboard is refused, which
   * browsers do in more situations than they document.
   *
   * Every line of it is one of her own confirmed sentences or a label from
   * her own configuration. `match/resume_export.py` owns the layout and a
   * test walks its syntax tree to keep it from composing a word.
   */
  function exportBlock(text) {
    const area = el('textarea', {
      className: 'prep__export',
      attrs: { rows: '10', readonly: 'readonly', spellcheck: 'false' },
      props: { value: text },
    });
    const said = el('p', { className: 'prep__exportsaid', attrs: { role: 'status' } });
    const copy = button(t('resume.copy'), async () => {
      try {
        await navigator.clipboard.writeText(text);
        said.textContent = t('resume.copied');
      } catch {
        said.textContent = t('resume.copyFailed');
        area.select();
      }
    }, { className: 'btn btn--primary' });
    return el('div', { className: 'prep__exportblock' }, [
      el('p', { className: 'prep__meaning', text: t('resume.exportLede') }),
      copy,
      said,
      area,
    ]);
  }

  function resumeBody(built) {
    return [
      built.lead_with.length
        ? el('ol', { className: 'prep__lead' }, built.lead_with.map(leadRow))
        : el('p', { className: 'prep__empty', text: t('resume.nothingSpeaks') }),
      built.export_text ? exportBlock(built.export_text) : null,
      built.not_relevant.length
        ? el('details', { className: 'prep__spare' }, [
          el('summary', {
            className: 'prep__reviewsummary',
            text: t('resume.spare', { n: built.not_relevant.length }),
          }),
          el('p', { className: 'prep__meaning', text: t('resume.spareLede') }),
          el('ul', { className: 'prep__sparelist' }, built.not_relevant.map((claim) => el(
            'li',
            { className: 'prep__sparerow', text: claim.text },
          ))),
        ])
        : null,
      built.gaps.length
        ? el('div', { className: 'prep__resumegaps' }, [
          el('p', { className: 'prep__who', text: t('resume.gapsHead') }),
          el('p', { className: 'prep__meaning', text: t('resume.gapsLede') }),
          el('ul', { className: 'prep__sparelist' }, built.gaps.map((label) => el(
            'li',
            { className: 'prep__sparerow', text: label },
          ))),
        ])
        : null,
    ].filter(Boolean);
  }

  function leadRow(suggestion) {
    return el('li', { className: `prep__leadrow prep__leadrow--${suggestion.strength.toLowerCase()}` }, [
      el('p', { className: 'prep__leadtext', text: suggestion.text }),
      el('p', {
        className: 'prep__matched',
        text: t('resume.answers', { list: suggestion.answers.join(' \u00b7 ') }),
      }),
    ]);
  }

  // -- the four counts, as one line ---------------------------------------
  function summary(payload) {
    if (!payload.total) {
      return block(t('prep.requirements'), [
        el('p', {
          className: 'prep__empty',
          text: t('prep.noRequirements'),
        }),
      ]);
    }
    const counts = el('ul', { className: 'prep__tally' }, READINESS.map(({ key, mark }) => el(
      'li',
      { className: `prep__tallyitem prep__tallyitem--${key.toLowerCase()}` },
      [
        el('span', { className: 'prep__mark', attrs: { 'aria-hidden': 'true' }, text: mark }),
        el('span', { className: 'prep__tallycount num', text: String(payload.counts[key] || 0) }),
        el('span', { className: 'prep__tallylabel', text: t(`prep.state.${key}`) }),
      ],
    )));

    const lede = payload.evidence_available
      ? t('prep.leadWithEvidence', { n: payload.evidence_available })
      : t('prep.leadWithoutEvidence');

    return block(t('prep.requirements'), [
      el('p', { className: 'prep__lede', text: lede }),
      counts,
      payload.evidence_available ? null : importPrompt(),
    ].filter(Boolean));
  }

  function importPrompt() {
    return el('p', { className: 'prep__prompt' }, [
      el('span', { text: t('prep.noEvidenceYet') }),
      onEvidence
        ? button(t('prep.goToEvidence'), () => onEvidence(null), { className: 'btn btn--quiet' })
        : null,
    ].filter(Boolean));
  }

  // -- everything to settle that is not about capability -------------------
  function concerns(payload) {
    if (!payload.concerns || !payload.concerns.length) return null;
    return block(t('prep.concerns'), [
      el('p', { className: 'prep__lede', text: t('prep.concernsLede') }),
      el('ul', { className: 'prep__concerns' }, payload.concerns.map((concern) => el(
        'li',
        { className: `prep__concern prep__concern--${concern.kind}` },
        [
          el('span', { className: 'prep__kind', text: t(`prep.concern.${concern.kind}`) }),
          el('p', { className: 'prep__detail', text: concernWords(concern) }),
          quote(concern.quote, 'prep.theySay'),
        ].filter(Boolean),
      ))),
    ]);
  }

  /**
   * One concern, in the reader's language.
   *
   * The server sends the sentence AND the pieces it was built from. A concern
   * with no `code` is one whose words came from the configuration or from a
   * gate's own reason -- a phrase group the person wrote, or the blocker she
   * named -- and those are not ours to translate.
   */
  function concernWords(concern) {
    if (!concern.code) return concern.detail;
    const key = `prep.concernText.${concern.code}`;
    const translated = t(key, concern.params || null);
    return translated === key ? concern.detail : translated;
  }

  // -- the requirements, grouped, gaps included ----------------------------
  function requirements(payload) {
    if (!payload.total) return null;
    const groups = READINESS.map(({ key, mark }) => {
      const rows = payload.requirements.filter((row) => row.readiness === key);
      if (!rows.length) return null;
      return el('section', { className: `prep__group prep__group--${key.toLowerCase()}` }, [
        el('h4', { className: 'prep__grouphead' }, [
          el('span', { className: 'prep__mark', attrs: { 'aria-hidden': 'true' }, text: mark }),
          el('span', { text: t(`prep.state.${key}`) }),
          el('span', { className: 'prep__groupcount num', text: String(rows.length) }),
        ]),
        el('p', { className: 'prep__meaning', text: meaningOf(key, payload) }),
        el('ul', { className: 'prep__rows' }, rows.map((row) => requirementRow(row, mark))),
      ]);
    }).filter(Boolean);
    return el('div', { className: 'prep__groups' }, groups);
  }

  /**
   * What one state means, in the reader's language.
   *
   * The server defines all four and that definition is what the docs and the
   * tests hold to. The catalogue carries the same sentences translated, and is
   * preferred; a state the catalogue has never heard of still shows the
   * server's own words rather than nothing.
   */
  function meaningOf(key, payload) {
    const translated = t(`prep.meaning.${key}`);
    if (translated !== `prep.meaning.${key}`) return translated;
    return (payload.readiness_meaning && payload.readiness_meaning[key]) || '';
  }

  function requirementRow(row, mark) {
    return el('li', { className: 'prep__row', attrs: { 'data-signal': row.signal_id } }, [
      el('div', { className: 'prep__rowhead' }, [
        el('span', {
          className: `prep__state prep__state--${row.readiness.toLowerCase()}`,
        }, [
          el('span', { className: 'prep__mark', attrs: { 'aria-hidden': 'true' }, text: mark }),
          el('span', { text: t(`prep.state.${row.readiness}`) }),
        ]),
        el('span', { className: 'prep__label', text: row.label }),
      ]),
      quote(row.posting_quote, 'prep.theySay'),
      evidenceOf(row),
      verdictControls(row),
    ].filter(Boolean));
  }

  /**
   * The candidate's own line, with its provenance and the phrase that matched.
   *
   * Never an assertion of a match without the sentence behind it. `matched_on`
   * is shown deliberately: it is the configured phrase the two sides were
   * compared on, and printing it is what turns "this matched" into something a
   * reader can check in one glance.
   */
  function evidenceOf(row) {
    if (!row.evidence_text) return null;
    const sourceKey = SOURCE_KEYS[row.evidence_source];
    // The line the claim was read from, shown only when it says something the
    // claim does not. That difference is what an EDIT produces: the claim is
    // her corrected wording and this is what her CV actually said, and hiding
    // the second would leave a citation nobody can check.
    const differs = row.evidence_origin
      && row.evidence_origin.trim() !== row.evidence_text.trim();

    return el('div', { className: 'prep__evidence' }, [
      el('span', { className: 'prep__who' }, [
        el('span', { text: t('prep.youSay') }),
        sourceKey
          ? el('span', { className: 'prep__provenance', text: ` · ${t(sourceKey)}` })
          : null,
      ].filter(Boolean)),
      el('p', { className: 'quote quote--mine', text: row.evidence_text }),
      differs
        ? el('p', { className: 'quote quote--origin', text: t('prep.fromDocument', {
          line: row.evidence_origin,
        }) })
        : null,
      row.matched_on
        ? el('p', {
          className: 'prep__matched',
          text: t('prep.matchedOn', { phrase: row.matched_on }),
        })
        : null,
    ].filter(Boolean));
  }

  function quote(text, labelKey) {
    if (!text) return null;
    return el('div', { className: 'prep__said' }, [
      el('span', { className: 'prep__who', text: t(labelKey) }),
      el('p', { className: 'quote', text }),
    ]);
  }

  /**
   * "Not right?" -- the four answers, behind a disclosure.
   *
   * Collapsed because the common case is agreeing, and four buttons on every
   * row would make a page of requirements read as a page of questions. Open
   * already when a verdict exists, because a disagreement somebody recorded
   * must not be hidden from the person who recorded it.
   */
  function verdictControls(row) {
    const existing = row.review ? row.review.verdict : null;
    const note = el('input', {
      className: 'input prep__note',
      attrs: {
        type: 'text',
        maxlength: '2000',
        placeholder: t('prep.notePlaceholder'),
        'aria-label': t('prep.noteLabel', { requirement: row.label }),
      },
      props: { value: (row.review && row.review.note) || '' },
    });

    const buttons = VERDICTS.map((verdict) => button(
      t(`prep.verdict.${verdict}`),
      () => answer(row, verdict, note.value),
      {
        className: `btn btn--verdict${existing === verdict ? ' is-chosen' : ''}`,
        attrs: { 'aria-pressed': String(existing === verdict) },
      },
    ));

    const clear = existing
      ? button(t('prep.withdraw'), () => answer(row, null, null), { className: 'btn btn--quiet' })
      : null;

    return el('details', {
      className: 'prep__review',
      props: { open: Boolean(existing) },
    }, [
      el('summary', {
        className: 'prep__reviewsummary',
        text: existing ? t(`prep.verdict.${existing}`) : t('prep.notRight'),
      }),
      el('p', { className: 'prep__meaning', text: t('prep.reviewLede') }),
      el('div', { className: 'prep__verdicts' }, buttons),
      note,
      clear,
      existing === 'EVIDENCE_MISSING' && onEvidence
        ? el('p', { className: 'prep__prompt' }, [
          el('span', { text: t('prep.missingLede') }),
          button(t('prep.goToEvidence'), () => onEvidence(row), { className: 'btn btn--quiet' }),
        ])
        : null,
    ].filter(Boolean));
  }

  async function answer(row, verdict, note) {
    if (!jobId) return;
    const payload = await api.reviewRequirement(jobId, row.signal_id, verdict, note || null);
    replace(host, sections(payload));
  }

  // -- what is left to do, stated as facts ---------------------------------
  /**
   * A checklist, and deliberately not a score.
   *
   * Each line is a statement about what has happened, not a task somebody is
   * failing. There is no percentage and no total: a figure over these would be
   * a fourth measurement, and ADR-0004 keeps three.
   */
  function checklist(payload) {
    const items = [
      {
        key: 'eligibility',
        done: !payload.concerns.some((concern) => concern.kind === 'eligibility'),
        detail: payload.concerns.filter((c) => c.kind === 'eligibility').length,
      },
      {
        key: 'requirements',
        done: payload.total > 0,
        detail: payload.total,
      },
      {
        key: 'evidence',
        done: payload.evidence_available > 0,
        detail: payload.evidence_available,
      },
      {
        key: 'gaps',
        done: payload.counts.GAP === 0,
        detail: payload.counts.GAP,
      },
    ];
    return block(t('prep.checklist'), [
      el('p', { className: 'prep__lede', text: t('prep.checklistLede') }),
      el('ul', { className: 'prep__check' }, items.map((item) => el(
        'li',
        { className: `prep__checkrow${item.done ? ' is-done' : ''}` },
        [
          el('span', {
            className: 'prep__mark',
            attrs: { 'aria-hidden': 'true' },
            text: item.done ? '✓' : '-',
          }),
          el('span', {
            className: 'prep__checktext',
            text: t(`prep.check.${item.key}`, { n: item.detail }),
          }),
        ],
      ))),
    ]);
  }

  function block(heading, children) {
    return el('section', { className: 'd-sec prep__sec' }, [
      el('h3', { className: 'd-sec__head', text: heading }),
      ...children,
    ]);
  }

  return { host, load, reset };
}
