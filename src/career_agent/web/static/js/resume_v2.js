/**
 * resume_v2.js -- Resume Workspace V2, the internal page (`?debug=resume-v2`).
 *
 * Not a user surface yet: the Resume helper is still where a resume is made
 * and edited. This page opens a stored ResumeDocument and shows it through
 * the live preview, with the template and the page size switchable. Those two
 * switches change only the copy being previewed, and only its design.
 */

import { getResumeDocument, listResumeDocuments } from './api.js';
import { el } from './dom.js';
import { t } from './i18n.js';
import { createResumePreview } from './resume_preview.js';

const TEMPLATES = ['clean', 'modern', 'compact'];
const PAGES = ['A4', 'LETTER'];

function select(id, label, values, labelOf, onChange) {
  const control = el('select', {
    attrs: { id },
    on: { change: () => onChange(control.value) },
  }, values.map((v) => el('option', { attrs: { value: v }, text: labelOf(v) })));
  return [el('label', { className: 'rvw__label', attrs: { for: id }, text: label }), control];
}

export function createResumeWorkspace({ host }) {
  let current = null;
  let design = {};
  const clicked = el('p', { className: 'rvw__clicked', attrs: { 'aria-live': 'polite' } });
  const preview = createResumePreview({
    onRef: (ref) => { clicked.textContent = t('rv.clicked', { ref }); },
  });
  const docs = el('select', {
    attrs: { id: 'rvw-doc' },
    on: { change: () => open(docs.value) },
  });
  const [templateLabel, template] = select(
    'rvw-template', t('rv.design.template'), TEMPLATES, (v) => t(`rv.template.${v}`),
    (value) => { design = { ...design, template: value }; show(); },
  );
  const [pageLabel, page] = select(
    'rvw-page', t('rv.design.page'), PAGES, (v) => (v === 'A4' ? 'A4' : t('rv.page.letter')),
    (value) => { design = { ...design, page: { ...(current.design.page || {}), size: value } }; show(); },
  );
  const empty = el('p', { className: 'rvw__empty', text: t('rv.docs.empty'), props: { hidden: true } });
  const controls = el('div', { className: 'rvw__controls' }, [
    el('label', { className: 'rvw__label', attrs: { for: 'rvw-doc' }, text: t('rv.docs.label') }), docs,
    templateLabel, template, pageLabel, page,
  ]);
  const head = el('header', { className: 'rvw__head' }, [
    el('h1', { className: 'rvw__title', text: t('rv.title') }),
    el('p', { className: 'rvw__sub', text: t('rv.sub') }),
  ]);
  const root = el('div', { className: 'rvw' }, [head, controls, empty, clicked, preview.root]);
  host.replaceChildren(root);

  function show() {
    if (!current) return null;
    return preview.update({ ...current, design: { ...current.design, ...design } });
  }

  async function open(id) {
    const answer = await getResumeDocument(id);
    current = answer.document;
    design = {};
    template.value = current.design.template;
    page.value = current.design.page.size;
    return show();
  }

  async function load() {
    const listed = await listResumeDocuments();
    docs.replaceChildren(...listed.map((d) => el('option', {
      attrs: { value: d.id },
      text: d.version_number ? `${d.title} V${d.version_number}` : d.title,
    })));
    empty.hidden = listed.length > 0;
    controls.hidden = listed.length === 0;
    if (listed.length) await open(listed[0].id);
  }

  return { show: load, preview };
}
