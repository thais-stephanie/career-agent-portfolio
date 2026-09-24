/**
 * documents.js -- Documents: every import, in one place.
 *
 * Somebody who wants to remove an old CV comes here: Documents, the CV,
 * Archive or Delete. Each import says what it is, when it was read, how many
 * experiences it describes and how much is still waiting, and offers one
 * primary action -- continue (or look again at) its review.
 *
 * The words are distinct on purpose:
 *
 *   Archive            put it away, keep everything, restore any time
 *   Restore            bring an archived import back exactly as it was
 *   Use this reading   make a replaced reading of your documents the current one
 *   Delete             remove it for good -- after saying, from the server's own
 *                      plan, what goes and what stays (confirmed statements
 *                      stay, with the lines they came from)
 */

import { el, button, replace } from './dom.js';
import { tCount } from './i18n.js';
import * as api from './api.js';
import { formatDate } from './format.js';
import { importReview } from './import_review.js';
import { badge, inlineConfirm, keepFocus, toast, workCard } from './ui.js';

const L = (key, params) => tCount(`docs.${key}`, params);

export function documentsPage({ onChanged = null, onManage = null } = {}) {
  const root = el('section', { className: 'docs', attrs: { 'aria-label': L('heading') } });
  let listing = null;
  let reviewing = null;

  const changed = () => { if (onChanged) onChanged(); };

  async function load() {
    if (reviewing) return;
    const fresh = await api.getDocuments();
    // A review opened while the list was on its way must not be painted over.
    if (reviewing) return;
    listing = fresh;
    paint();
  }

  function open(kind, id, entry = null) {
    reviewing = { kind, id };
    replace(root, [importReview({
      kind, id, focusEntry: entry,
      onChanged: changed,
      onDone: async () => { reviewing = null; await load(); root.querySelector('h2, h3')?.focus(); },
    })]);
    window.scrollTo({ top: 0 });
  }

  // -- upload -------------------------------------------------------------
  const chosen = el('span', { className: 'docs-upload__name', text: L('noFile') });
  const fileInput = el('input', {
    className: 'docs-upload__input',
    attrs: { type: 'file', id: 'docs-file', accept: '.pdf,.docx,.txt,.md' },
    on: { change: (event) => read(event.target) },
  });
  const uploadNote = el('p', { className: 'docs-upload__note', attrs: { role: 'status' } });

  async function read(input) {
    const file = input.files && input.files[0];
    if (!file) return;
    chosen.textContent = file.name;
    uploadNote.textContent = L('reading', { name: file.name });
    try {
      const bytes = await file.arrayBuffer();
      const imported = await api.importCv(file.name, bytes);
      input.value = '';
      changed();
      open('cv', imported.import_id);
    } catch (error) {
      uploadNote.textContent = error.userMessage || error.message;
      input.value = '';
    }
  }

  function uploadCard() {
    return el('div', { className: 'docs-upload cw-card' }, [
      el('div', { className: 'docs-upload__text' }, [
        el('h3', { text: L('importTitle') }),
        el('p', { text: L('importBody') }),
        el('ul', { className: 'docs-upload__finds' }, ['experiences', 'skills', 'certifications', 'education']
          .map((key) => el('li', { text: L(`finds.${key}`) }))),
      ]),
      el('div', { className: 'docs-upload__pick' }, [
        fileInput,
        el('label', { className: 'btn btn--primary', attrs: { for: 'docs-file' }, text: L('choose') }),
        chosen,
        el('p', { className: 'docs-upload__privacy', text: L('privacy') }),
      ]),
      uploadNote,
    ]);
  }

  // -- the list -------------------------------------------------------------
  function paint() { keepFocus(root, draw); }

  function draw() {
    const documents = (listing && listing.documents) || [];
    const live = documents.filter((d) => d.status !== 'archived');
    const archived = documents.filter((d) => d.status === 'archived');
    replace(root, [
      uploadCard(),
      el('h3', { className: 'docs-heading', text: L('yourImports') }),
      documents.length ? null : el('p', { className: 'docs-empty', text: L('empty') }),
      live.length ? el('ul', { className: 'docs-list' }, live.map((doc) => el('li', {}, [row(doc)]))) : null,
      archived.length ? el('details', { className: 'evp-fold' }, [
        el('summary', { text: L('archivedFold', { n: archived.length }) }),
        el('ul', { className: 'docs-list' }, archived.map((doc) => el('li', {}, [row(doc)]))),
      ]) : null,
      onManage ? el('p', { className: 'evp-manage' }, [
        button(L('manage'), onManage, { className: 'cw-link' }),
        el('span', { text: L('manageHint') }),
      ]) : null,
    ]);
  }

  function statusBadge(doc) {
    if (doc.status === 'archived') return badge(L('status.archived'), 'plain');
    if (doc.status === 'superseded') return badge(L('status.replaced'), 'plain');
    if (doc.waiting) return badge(L('status.waiting', { n: doc.waiting }), 'yellow');
    return badge(L('status.done'), 'mint');
  }

  function row(doc) {
    const confirmHost = el('div');
    const kindLabel = doc.kind === 'cv' ? L('kind.cv') : L('kind.package');
    const extension = (doc.name.match(/\.([a-z0-9]{2,4})$/i) || [])[1];
    const primary = doc.status === 'archived' ? null : button(
      doc.waiting ? L('continue') : L('lookAgain'), () => open(doc.kind, doc.id),
      { className: doc.waiting && doc.status === 'active' ? 'btn btn--primary' : 'btn',
        ariaLabel: L('reviewLabel', { name: doc.name }) },
    );
    const secondary = [];
    if (doc.status === 'archived') {
      secondary.push(button(L('restore'), async () => {
        if (doc.kind === 'cv') await api.restoreCvImport(doc.id);
        else await api.restoreIntakePackage(doc.id);
        changed(); await load(); toast(L('restored', { name: doc.name }));
      }, { className: 'btn btn--small' }));
      secondary.push(button(L('lookInside'), () => open(doc.kind, doc.id), { className: 'btn btn--small btn--quiet' }));
    } else {
      if (doc.status === 'superseded') {
        secondary.push(button(L('useThis'), async () => {
          await api.selectIntakePackage(doc.id);
          changed(); await load(); toast(L('inUse', { name: doc.name }));
        }, { className: 'btn btn--small' }));
      }
      secondary.push(button(L('archive'), async () => {
        if (doc.kind === 'cv') await api.archiveCvImport(doc.id);
        else await api.discardIntakePackage(doc.id);
        changed(); await load();
        toast(L('archived', { name: doc.name }), {
          undo: async () => {
            if (doc.kind === 'cv') await api.restoreCvImport(doc.id);
            else await api.restoreIntakePackage(doc.id);
            changed(); await load();
          },
        });
      }, { className: 'btn btn--small btn--quiet', ariaLabel: L('archiveLabel', { name: doc.name }) }));
    }
    const del = button(L('delete'), async () => {
      const plan = doc.kind === 'cv'
        ? (await api.deleteCvImport(doc.id)).plan
        : (await api.deleteIntakePackage(doc.id)).plan;
      const pending = plan.pending ?? plan.waiting ?? 0;
      inlineConfirm(confirmHost, {
        message: L('deleteQuestion', { name: doc.name }),
        detail: [
          plan.confirmed_kept
            ? L('deleteKeeps', { removed: plan.removed, pending, confirmed: plan.confirmed_kept })
            : L('deleteAll', { removed: plan.removed, pending }),
          L('deleteForever'),
        ].join(' '),
        confirmLabel: L('deleteConfirm'),
        returnTo: del,
        onConfirm: async () => {
          if (doc.kind === 'cv') await api.deleteCvImport(doc.id, { confirm: true });
          else await api.deleteIntakePackage(doc.id, { confirm: true });
          changed(); await load(); toast(L('deleted', { name: doc.name }));
        },
      });
    }, { className: 'btn btn--small btn--quiet btn--danger', ariaLabel: L('deleteLabel', { name: doc.name }) });
    secondary.push(del);
    return workCard({
      className: `docs-card docs-card--${doc.status}`,
      label: doc.name,
      tag: 'article',
      children: [
        el('div', { className: 'docs-card__main' }, [
          el('span', { className: 'docs-card__icon', attrs: { 'aria-hidden': 'true' },
            text: (extension || kindLabel).slice(0, 4).toUpperCase() }),
          el('div', { className: 'docs-card__text' }, [
            el('h4', { className: 'docs-card__name', text: doc.name }),
            el('p', { className: 'docs-card__meta', text: L('meta', {
              kind: kindLabel, date: formatDate(doc.created_at),
              experiences: L('experiences', { n: doc.experiences }),
              confirmed: L('confirmed', { n: doc.confirmed }) }) }),
          ]),
          statusBadge(doc),
        ]),
        el('div', { className: 'docs-card__actions' }, [
          el('div', { className: 'docs-card__secondary' }, secondary),
          primary,
        ]),
        confirmHost,
      ],
    });
  }

  root.load = load;
  root.open = open;
  root.choose = () => fileInput.click();
  root.leaveReview = () => { reviewing = null; };
  void load().catch((problem) => {
    replace(root, [el('p', { attrs: { role: 'alert' }, text: problem.userMessage || problem.message })]);
  });
  return root;
}
