/**
 * evidence_page.js -- Evidence: the proof behind a career.
 *
 * Projects, achievements and certifications, as cards. Each card is ONE
 * confirmed statement of that kind, drawn with its type, when, what, the
 * skills it used, the experience it belongs to and where it came from. The
 * low-level statements that make up an experience are not cards here: they
 * are that experience's highlights, on the Career Profile.
 *
 * Provenance is kept in view, in plain words: "From your CV" with the quote
 * one click away, or "Written by you" for a statement that cites nothing.
 * A typed statement is never presented as a quote.
 *
 * "Stop using" retires a statement: a revision, with its history, undoable.
 * Nothing here deletes evidence, and nothing here changes Search Fit.
 */

import { el, button, field, select, replace } from './dom.js';
import { tCount } from './i18n.js';
import * as api from './api.js';
import {
  badge, chipInput, chips, inlineConfirm, openDrawer, periodLabel, sourceSnippet, toast, uid,
  workCard,
} from './ui.js';

const L = (key, params) => tCount(`evp.${key}`, params);
//: The kinds of proof this page is for, in the order it shows them.
const KINDS = [
  { type: 'PROJECT', tone: 'mint' },
  { type: 'ACHIEVEMENT', tone: 'pink' },
  { type: 'CERTIFICATION', tone: 'lilac' },
];
const SECONDARY = [{ type: 'EDUCATION', tone: 'blue' }];

/** Split "Title: what it was" the way a person wrote it; never invent a title. */
export function titleOf(text) {
  const clean = String(text || '').trim();
  const colon = clean.indexOf(': ');
  if (colon > 3 && colon <= 90) {
    return { title: clean.slice(0, colon), description: clean.slice(colon + 2) };
  }
  if (clean.length <= 100) return { title: clean, description: '' };
  return { title: '', description: clean };
}

export function evidencePage({ onManage = null, onChanged = null } = {}) {
  const root = el('section', { className: 'evp', attrs: { 'aria-label': L('heading') } });
  let ledger = null;
  let career = null;

  async function load() {
    [ledger, career] = await Promise.all([
      api.getEvidence(),
      api.getCareer().catch(() => ({ experiences: [] })),
    ]);
    paint();
  }

  function experienceOf(key) {
    return (career.experiences || []).find((e) => (e.keys || []).includes(key)) || null;
  }

  function paint() {
    const claims = (ledger && ledger.claims) || [];
    const live = claims.filter((c) => c.state === 'CONFIRMED' || (c.verified && !c.state));
    const aside = claims.filter((c) => c.state === 'RETIRED' || c.state === 'DRAFT');
    const groups = [...KINDS, ...SECONDARY].map((kind) => ({
      ...kind, items: live.filter((c) => c.claim_type === kind.type),
    }));
    const primary = groups.filter((g) => KINDS.some((k) => k.type === g.type));
    const anything = groups.some((g) => g.items.length);
    replace(root, [
      el('div', { className: 'evp-toolbar' }, [
        el('p', { className: 'evp-lede', text: L('lede') }),
        button(L('add'), () => editorDrawer(null), { className: 'btn btn--primary' }),
      ]),
      anything ? null : el('div', { className: 'evp-empty cw-card' }, [
        el('h3', { text: L('emptyTitle') }),
        el('p', { text: L('emptyBody') }),
      ]),
      ...primary.map((group) => section(group)),
      ...groups.filter((g) => !primary.includes(g) && g.items.length).map((group) => el('details', {
        className: 'evp-fold',
      }, [el('summary', { text: L('foldTitle', { name: L(`type.${group.type}`), n: group.items.length }) }),
        grid(group)])),
      aside.length ? el('details', { className: 'evp-fold evp-fold--aside' }, [
        el('summary', { text: L('asideTitle', { n: aside.length }) }),
        el('p', { className: 'evp-lede', text: L('asideLede') }),
        el('ul', { className: 'evp-aside' }, aside.map((claim) => el('li', {}, [
          el('span', { text: claim.text }),
          badge(claim.state === 'DRAFT' ? L('draft') : L('retired'), 'plain'),
          button(claim.state === 'DRAFT' ? L('confirmDraft') : L('useAgain'), async () => {
            await api.confirmClaim(claim.claim_key);
            await load();
            if (onChanged) onChanged();
            toast(L('backInUse'));
          }, { className: 'btn btn--small' }),
        ]))),
      ]) : null,
      onManage ? el('p', { className: 'evp-manage' }, [
        button(L('manage'), onManage, { className: 'cw-link' }),
        el('span', { text: L('manageHint') }),
      ]) : null,
    ]);
  }

  function section(group) {
    if (!group.items.length) return null;
    const headId = uid('evp-head');
    return el('section', { className: 'evp-section', attrs: { 'aria-labelledby': headId } }, [
      el('header', { className: 'evp-section__head' }, [
        el('h3', { attrs: { id: headId }, text: L(`group.${group.type}`) }),
        el('span', { className: 'evp-section__rule', attrs: { 'aria-hidden': 'true' } }),
        el('span', { className: 'evp-section__count', text: L('entries', { n: group.items.length }) }),
      ]),
      grid(group),
    ]);
  }

  function grid(group) {
    return el('ul', { className: 'evp-grid' }, group.items.map((claim) => el('li', {}, [
      evidenceCard(claim, group.tone),
    ])));
  }

  function evidenceCard(claim, tone) {
    const { title, description } = titleOf(claim.text);
    const linked = experienceOf(claim.claim_key);
    const when = claim.period_start ? String(claim.period_start).slice(0, 4) : '';
    const confirmHost = el('div');
    const open = button(L('open'), () => detailDrawer(claim), {
      className: 'cw-action', ariaLabel: L('openLabel', { title: title || description.slice(0, 60) }),
    });
    const stop = button(L('stopUsing'), () => inlineConfirm(confirmHost, {
      message: L('stopQuestion'),
      detail: L('stopDetail'),
      confirmLabel: L('stopConfirm'),
      returnTo: stop,
      onConfirm: async () => {
        await api.retireClaim(claim.claim_key);
        await load();
        if (onChanged) onChanged();
        toast(L('stopped'), {
          undo: async () => { await api.confirmClaim(claim.claim_key); await load(); },
        });
      },
    }), { className: 'cw-action cw-action--danger', ariaLabel: L('stopLabel', { title: title || '' }) });
    return workCard({
      className: `evp-card evp-card--${tone}`,
      label: title || description.slice(0, 80),
      actions: [open, stop],
      children: [
        el('div', { className: 'evp-card__top' }, [
          badge(L(`type.${claim.claim_type}`), tone),
          when ? el('span', { className: 'evp-card__year', text: when }) : null,
        ]),
        title ? el('h4', { className: 'evp-card__title', text: title }) : null,
        description ? el('p', { className: 'evp-card__text', text: description }) : null,
        (claim.tools || []).length ? chips(claim.tools, { tone: 'plain', label: L('skills') }) : null,
        linked ? el('p', { className: 'evp-card__link', text: L('linkedTo', {
          role: linked.title || L('roleNotStated'), company: linked.company || '' }) }) : null,
        sourceSnippet({
          origin: claim.evidence ? 'document' : 'self',
          document: claim.source_label || '',
          quote: claim.evidence || '',
        }),
        confirmHost,
      ],
    });
  }

  // -- the drawer: one piece of evidence, its source and its history -------
  function detailDrawer(claim) {
    const { title, description } = titleOf(claim.text);
    const linked = experienceOf(claim.claim_key);
    const drawer = openDrawer({
      eyebrow: L(`type.${claim.claim_type}`),
      title: title || L('untitled'),
    });
    const history = el('div', { className: 'evp-history' });
    const historyFold = el('details', { className: 'evp-fold' }, [
      el('summary', { text: L('history', { n: claim.revisions || 1 }) }),
      history,
    ]);
    historyFold.addEventListener('toggle', async () => {
      if (!historyFold.open || history.dataset.loaded) return;
      history.dataset.loaded = 'true';
      const payload = await api.getClaimHistory(claim.claim_key);
      replace(history, [...payload.revisions.map((revision) => el('div', {
        className: 'evp-history__row',
      }, [
        el('span', { className: 'evp-history__when', text: L('revision', { n: revision.revision }) }),
        el('p', { text: revision.text }),
      ]))]);
    });
    replace(drawer.body, [
      description ? el('p', { className: 'evp-drawer__text', text: description }) : null,
      (claim.tools || []).length ? chips(claim.tools, { tone: 'plain', label: L('skills') }) : null,
      linked ? el('p', { className: 'evp-card__link', text: L('linkedTo', {
        role: linked.title || L('roleNotStated'), company: linked.company || '' }) }) : null,
      claim.period_start ? el('p', { className: 'evp-drawer__when',
        text: periodLabel(claim.period_start, claim.period_end) }) : null,
      el('h3', { className: 'evp-drawer__sub', text: L('sourceHeading') }),
      sourceSnippet({ origin: claim.evidence ? 'document' : 'self',
        document: claim.source_label || '', quote: claim.evidence || '' }),
      historyFold,
    ]);
    replace(drawer.footer, [
      button(L('close'), () => drawer.close(), { className: 'btn' }),
      button(L('edit'), () => { drawer.close(); editorDrawer(claim); }, { className: 'btn btn--primary' }),
    ]);
  }

  // -- add or edit -----------------------------------------------------------
  function editorDrawer(claim) {
    const existing = claim ? titleOf(claim.text) : { title: '', description: '' };
    const drawer = openDrawer({
      eyebrow: claim ? L('editEyebrow') : L('newEyebrow'),
      title: claim ? L('editTitle') : L('addTitle'),
      lede: L('addLede'),
    });
    let chosen = claim ? claim.claim_type : 'PROJECT';
    const typeGroup = el('div', { className: 'evp-types', attrs: { role: 'radiogroup',
      'aria-label': L('typeLabel') } }, KINDS.map((kind) => {
      const option = el('button', {
        className: `evp-type evp-type--${kind.tone}`,
        attrs: { type: 'button', role: 'radio', 'aria-checked': String(kind.type === chosen) },
        on: { click: () => {
          chosen = kind.type;
          for (const node of typeGroup.children) {
            node.setAttribute('aria-checked', String(node.dataset.type === chosen));
          }
        } },
        dataset: { type: kind.type },
      }, [
        el('strong', { text: L(`type.${kind.type}`) }),
        el('span', { text: L(`typeHint.${kind.type}`) }),
      ]);
      return option;
    }));
    const titleInput = el('input', { className: 'input', attrs: { type: 'text', maxlength: '200',
      placeholder: L('titlePlaceholder') }, props: { value: existing.title } });
    const when = el('input', { className: 'input', attrs: { type: 'month' },
      props: { value: (claim && claim.period_start) || '' } });
    const experiences = (career.experiences || []).map((e) => ({
      value: e.id, label: [e.title, e.company].filter(Boolean).join(' · ') || L('roleNotStated'),
    }));
    const linkedNow = claim ? experienceOf(claim.claim_key) : null;
    const link = select([{ value: '', label: L('notLinked') }, ...experiences],
      linkedNow ? linkedNow.id : '', () => {}, {});
    const description = el('textarea', { className: 'input', attrs: { rows: '4', maxlength: '1800',
      placeholder: L('descriptionPlaceholder') }, props: { value: existing.description } });
    const skills = chipInput({ values: claim ? claim.tools || [] : [], label: L('skills'),
      placeholder: L('skillPlaceholder') });
    const error = el('p', { className: 'xp-editor__error', attrs: { role: 'alert' } });
    const id = (name) => `evp-${claim ? claim.claim_key : 'new'}-${name}`;
    const save = button(L('save'), async () => {
      const heading = titleInput.value.trim();
      if (!heading) { error.textContent = L('needTitle'); titleInput.focus(); return; }
      const body = description.value.trim();
      const text = body ? `${heading}: ${body}` : heading;
      save.disabled = true;
      try {
        if (claim) {
          await api.editClaim(claim.claim_key, { text, tools: skills.values(),
            period_start: when.value || null, period_end: null });
          const target = link.value || null;
          if ((linkedNow ? linkedNow.id : null) !== target) {
            await api.changeCareer({ action: 'move', keys: [claim.claim_key], experience_id: target });
          }
          if (chosen !== claim.claim_type) {
            await api.changeCareer({ action: 'category', keys: [claim.claim_key], category: chosen });
          }
        } else {
          await api.createClaim({ claim_type: chosen, text, tools: skills.values(),
            period_start: when.value || null, experience_id: link.value || null });
        }
        drawer.close();
        await load();
        if (onChanged) onChanged();
        toast(claim ? L('savedEdit') : L('saved'));
      } catch (problem) {
        error.textContent = problem.userMessage || problem.message;
        save.disabled = false;
      }
    }, { className: 'btn btn--primary' });
    replace(drawer.body, [
      el('div', { className: 'field' }, [el('span', { className: 'field__label', text: L('typeLabel') }),
        typeGroup]),
      field(id('title'), L('title'), titleInput),
      el('div', { className: 'evp-row' }, [
        field(id('when'), L('when'), when, { hint: L('whenHint') }),
        field(id('link'), L('linkedExperience'), link),
      ]),
      field(id('description'), L('description'), description),
      el('div', { className: 'field' }, [el('span', { className: 'field__label', text: L('skills') }), skills]),
      el('p', { className: 'evp-note', text: claim && claim.evidence ? L('editKeepsQuote') : L('writtenNote') }),
      error,
    ]);
    replace(drawer.footer, [button(L('cancel'), () => drawer.close(), { className: 'btn' }), save]);
  }

  root.load = load;
  root.add = () => editorDrawer(null);
  void load().catch((problem) => {
    replace(root, [el('p', { attrs: { role: 'alert' }, text: problem.userMessage || problem.message })]);
  });
  return root;
}

