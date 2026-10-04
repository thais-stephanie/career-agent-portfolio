/**
 * resume_v2.js -- Resumes: Home, My resumes and the Editor.
 *
 * THE resume page of Career Agent. Every resume is a ResumeDocument: the
 * Master made from the confirmed Career Profile, a resume imported from a
 * PDF or DOCX (`resume_import.js`), one started blank, and the versions made
 * for a job. My resumes is `resume_library.js`. The previous Resume Helper
 * stays reachable from Settings as "Legacy Resume Helper", and its resumes
 * move here only when the person asks (with a backup first).
 *
 * Opening this page creates nothing: the Master is made when the person
 * chooses "Build from My Profile", after being told what it uses.
 *
 * The editor holds ONE document object: every edit is a function over a copy
 * of it, recorded for undo, sent to the preview (120 ms after the last
 * change, newest render wins) and to autosave (600 ms, the working copy
 * only). A milestone revision is written on purpose: "Save version point", a
 * template change, leaving after edits.
 *
 * Nothing here writes Career Evidence, search settings or scores. Adding a
 * line "from evidence" copies an already-confirmed statement into THIS
 * resume; typing a line makes it the person's own words, labelled as such.
 *
 * DOWNLOAD (PDF, DOCX, JSON) first finishes saving, through the same flush
 * the version points use, and sends the hash of what was saved: the file is
 * made from that exact revision, never from an older copy while newer edits
 * are on screen. Edits that cannot be saved, or a change in another window,
 * refuse the download and say why. What the re-read file showed is listed
 * as named checks, never a score.
 */

import {
  createJobResume, createResumeDocument, createResumeMaster, downloadResumeExport, exportResume, getCareer,
  getLocalProfile, getResumeDocument, getResumeMaster, listResumeDocuments, manageResume, saveResumeCheckpoint,
  saveResumeWorkingCopy, tailorForJob, tailorPasted,
} from './api.js';
import { button, el } from './dom.js';
import { formatDate } from './format.js';
import { t, tCount } from './i18n.js';
import {
  createAutosave, createHistory, evidenceLine, move, newLine, rewordLine, ulid,
} from './resume_editor.js';
import { createResumeImport } from './resume_import.js';
import { createJobPanel } from './resume_job.js';
import { createLibrary, legacyNotice, versionLabel } from './resume_library.js';
import { createResumePreview } from './resume_preview.js';
import { openDrawer } from './ui.js';

const SECTIONS = ['headline', 'summary', 'experience', 'projects', 'education', 'certifications', 'skills'];
const LINK_KINDS = ['LINKEDIN', 'GITHUB', 'PORTFOLIO', 'WEBSITE', 'OTHER'];
const EMAIL = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;
const WEB = /^https?:\/\/\S+$/;
const COUNTRY = /^[A-Z]{2}$/;
const PREVIEW_MS = 120;
const FORMATS = ['PDF', 'DOCX', 'JSON'];
/** Labels that stand in for a person (the model's PLACEHOLDER_NAMES): never a name on a file. */
const PLACEHOLDER_NAMES = new Set(['you', 'candidate', 'my profile', 'meu perfil']);
const realName = (name) => {
  const plain = String(name || '').split(/\s+/).filter(Boolean).join(' ');
  return Boolean(plain) && !PLACEHOLDER_NAMES.has(plain.toLowerCase());
};
/** A check's state as a mark beside its words: never colour alone. */
const MARKS = { PASS: '✓', WARNING: '!', FAIL: '✗', NOT_MEASURED: '○' };

/** Every line object of a document: text blocks, then skill items. */
function linesOf(d) {
  const blocks = [d.headline, d.summary].filter(Boolean);
  for (const entry of [...d.experience, ...d.projects, ...d.education]) blocks.push(...entry.bullets);
  for (const custom of d.custom_sections) blocks.push(...custom.items);
  return { blocks, items: d.skills.flatMap((g) => g.items) };
}

function sectionLabel(doc, ref) {
  if (ref.startsWith('custom:')) {
    const custom = doc.custom_sections.find((c) => `custom:${c.id}` === ref);
    return (custom && custom.heading) || t('rv.section.custom');
  }
  return t(`rv.section.${ref}`);
}

/** Every section the document can show, in its layout order. */
function sectionOrder(doc) {
  const known = [...SECTIONS, ...doc.custom_sections.map((c) => `custom:${c.id}`)];
  return [
    ...doc.layout.section_order.filter((r) => known.includes(r)),
    ...known.filter((r) => !doc.layout.section_order.includes(r)),
  ];
}

function originLabel(line) {
  if (line.override === 'EDITED') return t('rv.origin.edited');
  if (line.origin === 'USER_AUTHORED') return t('rv.origin.yours');
  if (line.origin === 'IMPORTED') return t('rv.origin.imported');
  return t('rv.origin.evidence');
}

const smallButton = (label, onClick, extra = {}) => button(label, onClick, { className: 'btn btn--small', ...extra });

/** Every resume of a grouped list, newest edit first. */
const flatten = (lib) => [
  ...(lib.master ? [lib.master] : []), ...lib.others, ...lib.jobs.flatMap((g) => g.versions),
].sort((a, b) => b.updated_at.localeCompare(a.updated_at));

export function createResumeWorkspace({
  host, onEvidence = () => {}, onLegacy = () => {}, onAddEvidence = () => {},
}) {
  const tabs = {};
  const views = {};
  for (const name of ['home', 'list', 'editor', 'import', 'tailor']) {
    views[name] = el('section', {
      className: `rvw__view rvw__view--${name}`, attrs: { id: `rvw-view-${name}` },
    });
    if (name === 'import' || name === 'tailor') continue; // reached from a button, not a tab
    tabs[name] = button(t(`rv.tab.${name}`), () => show(name), {
      className: 'rvw__tab', attrs: { 'aria-controls': `rvw-view-${name}` },
    });
  }
  const nav = el('nav', { className: 'rvw__tabs' }, Object.values(tabs));
  function label() {
    nav.setAttribute('aria-label', t('rv.title'));
    for (const [name, tab] of Object.entries(tabs)) tab.textContent = t(`rv.tab.${name}`);
  }
  label();
  const root = el('div', { className: 'rvw' }, [nav, ...Object.values(views)]);
  host.replaceChildren(root);

  let editor = null;
  const library = createLibrary({
    onOpen: (id) => void open(id).catch(failed),
    onOpenRestored: (answer) => void open(answer.id, { answer, restored: true }).catch(failed),
    onHome: () => show('home'),
    onImport: () => void startImport(),
  });
  views.list.append(library.root);

  function show(name) {
    for (const [key, view] of Object.entries(views)) view.hidden = key !== name;
    for (const [key, tab] of Object.entries(tabs)) {
      tab.setAttribute('aria-current', key === name ? 'page' : 'false');
    }
    tabs.editor.disabled = !editor;
    root.dataset.view = name;
    if (name === 'home') void drawHome();
    if (name === 'list') void library.load();
  }

  function close() {
    if (editor) editor.destroy();
    editor = null;
    views.editor.replaceChildren();
    show('list');
  }

  async function open(id, { answer = null, restored = false } = {}) {
    // An editor whose edits could not be saved stays open and says why.
    if (editor && !(await editor.leave())) {
      show('editor');
      return;
    }
    const shown = answer || await getResumeDocument(id);
    if (editor) editor.destroy();
    editor = createEditor(shown, {
      onClose: () => show('list'), onDiscard: close, onOpen: (next) => open(next), restored, onAddEvidence,
    });
    views.editor.replaceChildren(editor.root);
    show('editor');
  }

  const failed = () => {
    if (editor) editor.failed();
  };

  // -- Home ------------------------------------------------------------------
  async function drawHome() {
    let lib;
    let master;
    try {
      [lib, master] = await Promise.all([listResumeDocuments(), getResumeMaster()]);
    } catch (error) {
      views.home.replaceChildren(el('p', {
        className: 'rve__notice rve__notice--bad', attrs: { role: 'alert' }, text: error.userMessage || t('rv.failed'),
      }));
      return;
    }
    const there = Boolean(master.master);
    const changes = master.evidence_changes
      ? Object.values(master.evidence_changes).reduce((sum, keys) => sum + keys.length, 0) : 0;
    const scratch = smallButton(t('rv.home.scratch'), () => {
      void createResumeDocument({ title: t('rv.home.scratchTitle') }).then((made) => open(made.id)).catch(failed);
    });
    const importing = smallButton(t('rv.home.importButton'), () => void startImport(), {
      className: there ? 'btn btn--small btn--primary' : 'btn btn--small',
    });
    const masterBox = el('div', { className: 'rvw__masterbox' });
    const build = () => masterBox.replaceChildren(
      el('p', { className: 'rvw__explain', text: t('rv.home.buildExplain') }),
      el('div', { className: 'rvl__rename' }, [
        smallButton(t('rv.home.buildConfirm'), () => {
          void createResumeMaster().then((made) => open(made.master.id)).catch(failed);
        }, { className: 'btn btn--small btn--primary', attrs: { id: 'rvw-build-confirm' } }),
        smallButton(t('ui.cancel'), () => masterBox.replaceChildren()),
      ]),
    );
    const masterCard = el('article', { className: 'rvw__card' }, there ? [
      el('h2', { className: 'rvw__cardtitle', text: t('rv.home.master') }),
      el('p', { text: master.master.title }),
      el('p', { className: 'rve__note', text: t('rv.lib.edited', { date: formatDate(master.master.updated_at) }) }),
      changes ? el('div', { className: 'rve__notice', attrs: { role: 'note' } }, [
        el('p', { text: t('rv.home.newerEvidence') }),
        smallButton(t('rv.home.reviewChanges'), () => void open(master.master.id).catch(failed)),
      ]) : null,
      smallButton(t('rv.home.editMaster'), () => void open(master.master.id).catch(failed), {
        className: 'btn btn--small btn--primary',
      }),
    ] : [
      el('h2', { className: 'rvw__cardtitle', text: t('rv.home.master') }),
      el('p', { text: t('rv.home.masterNone') }),
      el('div', { className: 'rvw__choices' }, [
        smallButton(t('rv.home.build'), build, { className: 'btn btn--small btn--primary' }),
        smallButton(t('rv.home.importButton'), () => void startImport()),
        scratch,
      ]),
      masterBox,
    ]);
    const all = flatten(lib);
    const recent = all.filter((d) => d.kind !== 'MASTER').slice(0, 3);
    const notice = await legacyNotice({
      profile: getLocalProfile(),
      onMoved: () => show('list'),
      onLegacy,
    });
    views.home.replaceChildren(...[
      ...(notice ? [notice] : []),
      el('div', { className: 'rvw__cards' }, [
        masterCard,
        el('article', { className: 'rvw__card' }, [
          el('h2', { className: 'rvw__cardtitle', text: t('rv.home.import') }),
          el('p', { text: t('rv.home.importLede') }),
          importing,
        ]),
        there ? el('article', { className: 'rvw__card' }, [
          el('h2', { className: 'rvw__cardtitle', text: t('rv.tailor.card') }),
          el('p', { text: t('rv.tailor.cardLede') }),
          el('details', { className: 'rvt__paste' }, [
            el('summary', { className: 'btn btn--small', text: t('rv.tailor.paste') }), pastedForm(),
          ]),
        ]) : null,
        el('article', { className: 'rvw__card' }, [
          el('h2', { className: 'rvw__cardtitle', text: t('rv.tab.list') }),
          el('p', { text: all.length ? tCount('rv.home.count', { n: all.length }) : t('rv.home.none') }),
          there ? scratch : null,
          smallButton(t('rv.home.seeAll'), () => show('list')),
        ]),
      ]),
      recent.length ? el('h2', { className: 'rvw__h2', text: t('rv.home.recent') }) : null,
      recent.length ? el('ul', { className: 'rvl__list' }, recent.map((d) => el('li', { className: 'rvl__row' }, [
        el('div', { className: 'rvl__main' }, [
          el('p', { className: 'rvl__name', text: versionLabel(d) }),
          el('p', { className: 'rvl__meta', text: t('rv.lib.edited', { date: formatDate(d.updated_at) }) }),
        ]),
        smallButton(t('rv.home.continue'), () => void open(d.id).catch(failed), {
          ariaLabel: `${t('rv.home.continue')}: ${d.title}`,
        }),
      ]))) : null,
    ].filter(Boolean));
  }

  /**
   * Tailor from the Master: one request runs every step on the server, so the
   * steps are listed for what they are, then marked done together. Nothing is
   * saved unless every check passes; then the version opens in the Editor.
   */
  async function runTailor(request) {
    const steps = ['read', 'find', 'build', 'check'].map((key) => el('li', {
      className: 'rvt__step', text: t(`rv.tailor.step.${key}`),
    }));
    const state = el('p', { className: 'rvt__state', attrs: { role: 'status', 'aria-live': 'polite' } });
    state.textContent = t('rv.tailor.working');
    const box = el('section', { className: 'rvt', attrs: { 'aria-labelledby': 'rvt-h' } }, [
      el('h2', { className: 'rvw__h2', attrs: { id: 'rvt-h', tabindex: '-1' }, text: t('rv.tailor.preparing') }),
      el('ol', { className: 'rvt__steps' }, steps),
      state,
      el('p', { className: 'rve__note', text: t('rv.job.noAi') }),
    ]);
    views.tailor.replaceChildren(box);
    show('tailor');
    box.querySelector('h2').focus();
    try {
      const made = await request();
      for (const step of steps) step.dataset.done = 'true';
      state.textContent = t('rv.tailor.done');
      await open(made.id, { answer: made });
    } catch (error) {
      const code = error.detail && error.detail.code;
      state.textContent = t(code === 'tailor_failed' ? 'rv.tailor.unsafe'
        : code === 'no_master' ? 'rv.job.needMaster' : 'rv.tailor.failed');
      box.append(smallButton(t('rv.back'), () => show('home')));
    }
  }

  function pastedForm() {
    const title = el('input', { className: 'input', attrs: { maxlength: '300', id: 'rvt-title' } });
    const company = el('input', { className: 'input', attrs: { maxlength: '200', id: 'rvt-company' } });
    const text = el('textarea', { className: 'rve__textarea rvt__ad', attrs: { id: 'rvt-text', rows: '6' } });
    const field = (id, key, node) => el('label', { className: 'rve__field', attrs: { for: id } }, [
      el('span', { className: 'rve__label', text: t(key) }), node,
    ]);
    const hint = el('p', { className: 'rve__hint', attrs: { 'aria-live': 'polite' } });
    const form = el('form', {
      className: 'rvt__form',
      attrs: { novalidate: '' },
      on: {
        submit: (event) => {
          event.preventDefault();
          if (!title.value.trim() || text.value.trim().length < 20) {
            hint.textContent = t('rv.tailor.need');
            return;
          }
          void runTailor(() => tailorPasted({
            title: title.value.trim(), company: company.value.trim() || null, text: text.value,
          }));
        },
      },
    }, [
      field('rvt-title', 'rv.tailor.title', title),
      field('rvt-company', 'rv.tailor.company', company),
      field('rvt-text', 'rv.tailor.ad', text),
      hint,
      button(t('rv.tailor.go'), null, { className: 'btn btn--small btn--primary', type: 'submit' }),
    ]);
    return form;
  }

  /** Import: pick, review, save; then the saved document opens in the Editor. */
  async function startImport() {
    const hasMaster = Boolean((await getResumeMaster()).master);
    const flow = createResumeImport({
      hasMaster,
      onSaved: (id) => { views.import.replaceChildren(); void open(id); },
      onCancel: () => { views.import.replaceChildren(); show('home'); },
      onEvidence,
      onScratch: () => {
        void createResumeDocument({ title: t('rv.home.scratchTitle') }).then((made) => open(made.id)).catch(failed);
      },
    });
    views.import.replaceChildren(flow.root);
    show('import');
  }

  window.addEventListener('beforeunload', (event) => {
    if (editor && editor.dirty()) event.preventDefault();
  });

  return {
    show: () => { show(editor ? 'editor' : 'home'); },
    /** Open one resume in the Editor (from the job drawer). */
    openDocument: (id) => open(id),
    /** Tailor from the Master for a job (no AI), then the Editor. */
    tailorForJob: (jobId) => runTailor(() => tailorForJob(jobId)),
    /** A version for this job, made by hand from the Master or another version; then the Editor. */
    async createForJob(jobId, from = null) {
      const made = await createJobResume(jobId, from);
      await open(made.id, { answer: made });
    },
    /** Leaving the page: finish saving, and mark the visit if it changed anything. */
    leave: () => (editor ? editor.leave() : Promise.resolve(true)),
    /** The language changed: every word again, nothing else. */
    relabel() {
      label();
      if (editor) editor.relabel();
      if (root.dataset.view === 'home') void drawHome();
      if (root.dataset.view === 'list') library.relabel();
    },
  };
}

// ===========================================================================
// the editor
// ===========================================================================

function createEditor(answer, { onClose, onDiscard, onOpen, restored = false, onAddEvidence = () => {} }) {
  let doc = structuredClone(answer.document);
  let editedSinceOpen = false;
  let previewTimer = null;
  let jobTimer = null;
  let layout = null;
  const history = createHistory();
  const saveState = el('p', { className: 'rve__state', attrs: { role: 'status', 'aria-live': 'polite' } });
  const notices = el('div', { className: 'rve__notices' });
  const say = (state, key = `rv.save.${state}`) => {
    saveState.textContent = t(key);
    saveState.dataset.state = state;
  };
  const autosave = createAutosave({
    sha: answer.sha256,
    save: async (copy, sha) => {
      try {
        return (await saveResumeWorkingCopy(answer.id, copy, sha)).sha256;
      } catch (error) {
        if (error.detail && error.detail.code === 'evidence_not_confirmed') drawEvidence(error.detail.lines);
        throw error;
      }
    },
    onState: (state) => {
      say(state);
      if (state === 'conflict') drawConflict();
      // What the job panel says follows what is saved, a moment after.
      if (state === 'saved' && jobPanel) {
        clearTimeout(jobTimer);
        jobTimer = setTimeout(() => void jobPanel.load(), 700);
      }
    },
  });
  say('saved');

  const preview = createResumePreview({ onRef: (ref) => focusRef(ref) });
  const form = el('form', {
    className: 'rve__form', attrs: { novalidate: '' }, on: { submit: (e) => e.preventDefault() },
  });
  const check = el('ul', { className: 'rve__check' });
  const design = el('div', { className: 'rve__design' });
  const undoButton = smallButton(t('rv.undo'), () => undo());
  const redoButton = smallButton(t('rv.redo'), () => redo());
  const backButton = smallButton(t('rv.back'), () => void leave().then((left) => { if (left) onClose(); }));
  const pointButton = smallButton(t('rv.checkpoint'), () => void checkpoint('MANUAL_CHECKPOINT', true));
  const kindTag = el('span', { className: 'rvw__kind' });
  const downloadLabel = el('span', { className: 'rve__formatlabel', attrs: { id: `rve-dlname-${answer.id}` } });
  const downloadHint = el('span', { className: 'rve__note', attrs: { id: `rve-dl-${answer.id}` } });
  const formatButtons = FORMATS.map((format) => smallButton(format, () => void exportAs(format), {
    attrs: { 'aria-describedby': downloadHint.id },
  }));
  const downloadGroup = el('div', {
    className: 'rve__formats', attrs: { role: 'group', 'aria-labelledby': downloadLabel.id },
  }, [downloadLabel, ...formatButtons, downloadHint]);
  // One live region for the whole download: its words change, it is not recreated.
  const exportBox = el('div', { className: 'rve__export', attrs: { 'aria-live': 'polite' } });
  let exporting = false;
  const designSummary = el('summary');
  const checkSummary = el('summary');
  const switcher = el('div', { className: 'rve__switch', attrs: { role: 'group' } });
  const modeEdit = button(t('rv.mode.edit'), () => setMode('edit'), {
    className: 'rve__mode', attrs: { 'aria-pressed': 'true' },
  });
  const modePreview = button(t('rv.mode.preview'), () => setMode('preview'), {
    className: 'rve__mode', attrs: { 'aria-pressed': 'false' },
  });
  const title = el('input', {
    className: 'input rve__titleinput',
    attrs: { 'aria-label': t('rv.docTitle'), maxlength: '300', 'data-key': 'title' },
    props: { value: doc.title },
    on: {
      input: (e) => {
        edit((d) => { d.title = e.target.value; }, 'title');
        title.setAttribute('aria-invalid', problems().has('title') ? 'true' : 'false');
      },
    },
  });
  const bar = el('header', { className: 'rve__bar' }, [
    backButton, title, kindTag, undoButton, redoButton, pointButton, downloadGroup, saveState,
  ]);
  const jobPanel = answer.document.target ? createJobPanel({
    documentId: answer.id,
    preferred: answer.preferred,
    onApply: (action) => void applySuggestion(action),
    onFocus: (lineId) => {
      const node = [...form.querySelectorAll('[data-ref]')].find((n) => n.dataset.ref.endsWith('/bullet/' + lineId));
      if (node) focusRef(node.dataset.ref);
    },
    onAddEvidence,
    onPrefer: () => manageResume(answer.id, 'prefer'),
  }) : null;
  const side = el('div', { className: 'rve__side' }, [
    jobPanel ? jobPanel.root : null,
    el('details', { className: 'rve__panel' }, [designSummary, design]),
    el('details', { className: 'rve__panel', props: { open: true } }, [checkSummary, check]),
    preview.root,
  ]);
  switcher.append(modeEdit, modePreview);
  const root = el('div', { className: 'rve', dataset: { mode: 'edit' } }, [
    bar, notices, exportBox, switcher, el('div', { className: 'rve__grid' }, [form, side]),
  ]);

  /** Every word of the editor's own, in the current language. */
  function label() {
    const kindLabel = t(`rv.kind.${answer.kind}`);
    kindTag.textContent = answer.version_number ? `${kindLabel} · V${answer.version_number}` : kindLabel;
    for (const [node, key] of [
      [backButton, 'rv.back'], [undoButton, 'rv.undo'], [redoButton, 'rv.redo'],
      [pointButton, 'rv.checkpoint'], [modeEdit, 'rv.mode.edit'], [modePreview, 'rv.mode.preview'],
      [designSummary, 'rv.panel.design'], [checkSummary, 'rv.panel.check'], [downloadLabel, 'rv.export.download'],
    ]) node.textContent = t(key);
    syncDownload();
    title.setAttribute('aria-label', t('rv.docTitle'));
    switcher.setAttribute('aria-label', t('rv.mode.label'));
    saveState.textContent = t(`rv.save.${saveState.dataset.state || 'saved'}`);
  }
  label();
  root.addEventListener('keydown', (event) => {
    const typing = event.target.closest('input, textarea, select, [contenteditable]');
    if (typing || !(event.ctrlKey || event.metaKey)) return;
    const key = event.key.toLowerCase();
    if (key === 'z' && !event.shiftKey) { event.preventDefault(); undo(); }
    if ((key === 'z' && event.shiftKey) || key === 'y') { event.preventDefault(); redo(); }
  });

  function setMode(mode) {
    root.dataset.mode = mode;
    modeEdit.setAttribute('aria-pressed', String(mode === 'edit'));
    modePreview.setAttribute('aria-pressed', String(mode === 'preview'));
    if (mode === 'preview') schedulePreview(0);
  }

  // -- the one way the document changes ------------------------------------
  function edit(fn, key = null, { redraw = false } = {}) {
    const before = doc;
    const next = structuredClone(doc);
    fn(next);
    history.record(before, key);
    doc = next;
    editedSinceOpen = true;
    changed({ redraw });
  }

  /** An edit that changes the form's shape (add, remove, move, hide). */
  const reshape = (fn) => () => edit(fn, null, { redraw: true });

  function syncButtons() {
    undoButton.disabled = !history.canUndo;
    redoButton.disabled = !history.canRedo;
    syncDownload();
  }

  /** A file needs a real name on it; a disabled control says why. */
  function syncDownload() {
    const noName = !realName(doc.identity.full_name);
    for (const b of formatButtons) b.disabled = exporting || noName;
    downloadHint.textContent = noName ? t('rv.export.needName') : '';
  }

  function changed({ redraw }) {
    if (redraw) drawForm();
    syncButtons();
    schedulePreview();
    // Edits the server would refuse are held here, unsaved and said to be.
    if (valid()) autosave.schedule(doc);
    else autosave.hold();
  }

  /** Undo or redo: a whole earlier (or later) document, if there is one. */
  function step(state) {
    if (!state) return;
    doc = state;
    changed({ redraw: true });
  }
  const undo = () => step(history.undo(doc));
  const redo = () => step(history.redo(doc));

  function schedulePreview(ms = PREVIEW_MS) {
    clearTimeout(previewTimer);
    if (!valid()) return;
    previewTimer = setTimeout(async () => {
      const result = await preview.update(doc);
      if (!result) return;
      layout = result;
      drawCheck();
    }, ms);
  }

  // -- saving ---------------------------------------------------------------
  /** A version point of what is SAVED; refused while edits are not saved (false).
   *  Null when saved but refused for evidence no longer confirmed: the person may still leave. */
  async function checkpoint(reason, announce = false) {
    if (!valid() || !(await autosave.flush())) {
      drawUnsaved();
      return false;
    }
    try {
      await saveResumeCheckpoint(answer.id, reason);
    } catch (error) {
      if (!(error.detail && error.detail.code === 'evidence_not_confirmed')) throw error;
      drawEvidence(error.detail.lines);
      return null;
    }
    if (announce) say('saved', 'rv.save.point');
    return true;
  }

  /** True when everything is saved (and the visit marked); false keeps the page here. */
  async function leave() {
    clearTimeout(previewTimer);
    if (!editedSinceOpen) {
      if (await autosave.flush()) return true;
      drawUnsaved();
      return false;
    }
    if ((await checkpoint('MANUAL_CHECKPOINT')) === false) return false;
    editedSinceOpen = false;
    return true;
  }

  // -- download -------------------------------------------------------------
  async function exportAs(format) {
    if (exporting) return;
    exporting = true;
    syncDownload();
    drawExport('working');
    let sent = null;
    try {
      if (!valid() || !(await autosave.flush())) {
        if (autosave.state === 'conflict') drawConflict();
        else drawUnsaved();
        drawExport('failed', { message: t('rv.export.unsaved') });
        return;
      }
      sent = autosave.sha;
      // The page count the preview shows for exactly this document.
      clearTimeout(previewTimer);
      const shown = format === 'PDF' ? await preview.update(doc) : null;
      if (shown) {
        layout = shown;
        drawCheck();
      }
      // Typed meanwhile: the saved copy is no longer what is on screen.
      if (autosave.dirty || autosave.sha !== sent) {
        drawExport('failed', { message: t('rv.export.changed') });
        return;
      }
      const made = await exportResume(answer.id, {
        format,
        expected_sha256: sent,
        ...(shown ? { preview_pages: shown.pages, preview_overflow: shown.overflow, page_breaks: shown.breaks } : {}),
      });
      await downloadResumeExport(made.download, `resume.${format.toLowerCase()}`);
      // Edits made while the file was being made are saved, and not in this file.
      drawExport('ready', { ...made, later: autosave.dirty || autosave.sha !== sent });
    } catch (error) {
      const detail = error.detail || {};
      if (detail.code === 'evidence_not_confirmed') drawEvidence(detail.lines || []);
      const changed = error.status === 409 && sent !== null && autosave.sha !== sent;
      drawExport('failed', { message: changed ? t('rv.export.changed') : (error.userMessage || t('rv.failed')) });
    } finally {
      exporting = false;
      syncDownload();
    }
  }

  function drawExport(state, made = {}) {
    exportBox.dataset.state = state;
    if (state === 'working') {
      exportBox.replaceChildren(el('p', { className: 'rve__note', text: t('rv.export.working') }));
      return;
    }
    if (state === 'failed') {
      exportBox.replaceChildren(el('div', { className: 'rve__notice rve__notice--bad', attrs: { role: 'alert' } }, [
        el('p', { text: made.message }),
      ]));
      return;
    }
    const pages = made.page_count ? t('rv.export.pages', { n: made.page_count }) : '';
    const items = made.checks.map((c) => {
      const params = { n: '', preview: '', ...(c.params || {}) };
      const own = `rv.ats.${c.check}.${c.status}`;
      const words = t(t(own) === own ? `rv.ats.${c.check}` : own, params);
      const issues = (c.issues || []).map((i) => t(`rv.ats.issue.${i}`)).join(' ');
      return el('li', { className: 'rve__atscheck', dataset: { status: c.status } }, [
        el('span', { className: 'rve__mark', attrs: { 'aria-hidden': 'true' }, text: MARKS[c.status] }),
        el('span', {
          text: t(issues ? 'rv.ats.lineIssues' : 'rv.ats.line', {
            status: t(`rv.ats.status.${c.status}`), words, issues,
          }),
        }),
      ]);
    });
    exportBox.replaceChildren(el('div', { className: 'rve__notice' }, [
      el('p', {
        className: 'rve__exporthead',
        text: [t(made.verified ? 'rv.export.ready' : 'rv.export.problem', { format: made.format }), pages]
          .filter(Boolean).join(' · '),
      }),
      made.later ? el('p', { className: 'rve__note', text: t('rv.export.later') }) : null,
      el('ul', { className: 'rve__atslist' }, items),
      smallButton(t('rv.export.dismiss'), () => exportBox.replaceChildren()),
    ]));
  }

  /** Lines whose evidence is not confirmed now: kept only as the person's own words, if she says so. */
  function drawEvidence(lines) {
    const ids = new Set(lines);
    notices.replaceChildren(el('div', { className: 'rve__notice rve__notice--bad', attrs: { role: 'alert' } }, [
      el('p', { text: t('rv.evidence.unconfirmed', { n: ids.size }) }),
      smallButton(t('rv.evidence.own'), () => {
        notices.replaceChildren();
        reshape((d) => {
          const { blocks, items } = linesOf(d);
          for (const b of blocks.filter((x) => ids.has(x.id))) {
            Object.assign(b, { origin: 'USER_AUTHORED', evidence_ids: [], override: 'NONE', original_text: null });
          }
          for (const i of items.filter((x) => ids.has(x.id))) {
            Object.assign(i, { origin: 'USER_AUTHORED', evidence_ids: [] });
          }
          for (const e of [...d.projects, ...d.education, ...d.certifications]) {
            if (ids.has(e.id)) e.claim_key = null;
          }
        })();
      }),
    ]));
  }

  function drawUnsaved() {
    if (autosave.state === 'conflict') return;
    notices.replaceChildren(el('div', { className: 'rve__notice rve__notice--bad', attrs: { role: 'alert' } }, [
      el('p', { text: t('rv.unsaved') }),
      smallButton(t('rv.unsaved.leave'), () => onDiscard()),
    ]));
  }

  function failed() {
    notices.replaceChildren(el('div', { className: 'rve__notice rve__notice--bad', attrs: { role: 'alert' } }, [
      el('p', { text: t('rv.failed') }),
    ]));
  }

  async function reload() {
    const latest = await getResumeDocument(answer.id);
    doc = structuredClone(latest.document);
    history.clear();
    autosave.reset(latest.sha256);
    notices.replaceChildren();
    editedSinceOpen = false;
    drawForm();
    syncButtons();
    schedulePreview(0);
  }

  function drawConflict() {
    notices.replaceChildren(el('div', { className: 'rve__notice rve__notice--bad', attrs: { role: 'alert' } }, [
      el('p', { text: t('rv.conflict') }),
      smallButton(t('rv.conflict.reload'), () => void reload().catch(failed)),
      smallButton(t('rv.conflict.copy'), () => {
        void createResumeDocument({ from: doc }).then((copy) => {
          // This copy now lives in the duplicate: nothing here is left to save.
          autosave.reset(autosave.sha);
          editedSinceOpen = false;
          onOpen(copy.id);
        }).catch(failed);
      }),
    ]));
  }

  // -- validity: what the model requires, said beside the field -------------
  function problems() {
    const out = new Map();
    const need = (value, ref) => { if (!String(value || '').trim()) out.set(ref, 'rv.need.text'); };
    need(doc.title, 'title');
    const id = doc.identity;
    if (id.email && !EMAIL.test(id.email)) out.set('identity/email', 'rv.need.email');
    if (id.country && !COUNTRY.test(id.country)) out.set('identity/country', 'rv.need.country');
    for (const link of id.links) if (!WEB.test(link.url)) out.set(`identity/link/${link.id}`, 'rv.need.url');
    for (const e of doc.experience) {
      need(e.employer, `experience/${e.id}/employer`);
      need(e.display_title, `experience/${e.id}`);
    }
    for (const p of doc.projects) need(p.name, `projects/${p.id}`);
    for (const e of doc.education) need(e.institution, `education/${e.id}`);
    for (const c of doc.certifications) need(c.name, `certifications/${c.id}`);
    for (const g of doc.skills) {
      need(g.name, `skills/${g.id}`);
      for (const i of g.items) need(i.label, `skills/${g.id}/item/${i.id}`);
    }
    for (const c of doc.custom_sections) need(c.heading, `section/custom:${c.id}`);
    return out;
  }
  const valid = () => problems().size === 0;

  // -- the form -------------------------------------------------------------
  /** A text field bound to `ref`; `apply(d, value)` writes it into a copy. */
  function input(ref, value, apply, { label, multiline = false, attrs = {} } = {}) {
    const hint = el('span', { className: 'rve__hint', attrs: { id: `hint-${ref.replace(/[^A-Za-z0-9]/g, '-')}` } });
    const node = el(multiline ? 'textarea' : 'input', {
      className: multiline ? 'rve__textarea' : 'input',
      attrs: { 'data-ref': ref, 'data-key': ref, 'aria-label': label, 'aria-describedby': hint.id, ...attrs },
      props: { value: value ?? '' },
      on: {
        input: (event) => {
          const typed = event.target.value;
          edit((d) => apply(d, typed), ref);
          mark();
        },
      },
    });
    function mark() {
      const problem = problems().get(ref);
      node.setAttribute('aria-invalid', problem ? 'true' : 'false');
      hint.textContent = problem ? t(problem) : '';
    }
    mark();
    return el('span', { className: 'rve__control' }, [node, hint]);
  }

  function labelled(text, control) {
    return el('label', { className: 'rve__field' }, [el('span', { className: 'rve__label', text }), control]);
  }

  function toggle(text, checked, onChange) {
    return el('label', { className: 'rve__toggle' }, [
      el('input', {
        attrs: { type: 'checkbox' }, props: { checked }, on: { change: (e) => onChange(e.target.checked) },
      }),
      el('span', { text }),
    ]);
  }

  function small(label, onClick, aria, disabled = false) {
    const node = smallButton(label, onClick, { ariaLabel: aria });
    node.disabled = disabled;
    return node;
  }

  /** Up, down: the keyboard way to reorder; drag is only a shortcut. */
  function moves(list, index, locate) {
    return [
      small('↑', reshape((d) => { move(locate(d), index, -1); }), t('rv.moveUp'), index === 0),
      small('↓', reshape((d) => { move(locate(d), index, 1); }), t('rv.moveDown'), index === list.length - 1),
    ];
  }

  /** Each line of a list: its words, where they came from, and its moves. */
  function lines(items, refPrefix, ownerLabel, locate) {
    const rows = items.map((line, index) => el('li', {
      className: 'rve__line', dataset: { hidden: String(line.hidden) },
    }, [
      input(`${refPrefix}/bullet/${line.id}`, line.text, (d, value) => { rewordLine(locate(d)[index], value); }, {
        label: t('rv.line.label', { n: index + 1, of: ownerLabel }), multiline: true,
      }),
      el('div', { className: 'rve__linebar' }, [
        el('span', {
          className: 'rve__origin',
          dataset: { origin: line.override === 'EDITED' ? 'EDITED' : line.origin },
          text: originLabel(line),
        }),
        ...moves(items, index, locate),
        toggle(t('rv.hide'), line.hidden, (on) => reshape((d) => { locate(d)[index].hidden = on; })()),
        line.origin === 'USER_AUTHORED' && line.override === 'NONE'
          ? small(t('rv.remove'), reshape((d) => { locate(d).splice(index, 1); }), t('rv.remove'))
          : null,
      ]),
    ]));
    return el('ol', { className: 'rve__lines' }, rows);
  }

  function lineActions(locate, ownerLabel, experienceId = null) {
    return el('div', { className: 'rve__actions' }, [
      smallButton(t('rv.addLine'), reshape((d) => { locate(d).push(newLine()); })),
      smallButton(t('rv.addEvidence'), () => void pickEvidence(locate, ownerLabel, experienceId)),
    ]);
  }

  function identityFields() {
    const id = doc.identity;
    const field = (labelKey, ref, name, attrs = {}) => labelled(t(labelKey), input(ref, id[name], (d, v) => {
      d.identity[name] = v || (name === 'full_name' ? '' : null);
    }, { label: t(labelKey), attrs }));
    const links = id.links.map((link, index) => el('div', { className: 'rve__linkrow' }, [
      el('select', {
        className: 'select',
        attrs: { 'aria-label': t('rv.link.kind') },
        on: { change: (e) => edit((d) => { d.identity.links[index].kind = e.target.value; }) },
      }, LINK_KINDS.map((k) => el('option', {
        attrs: { value: k }, text: t(`rv.link.${k}`), props: { selected: k === link.kind },
      }))),
      input(`identity/link/${link.id}`, link.url, (d, v) => { d.identity.links[index].url = v.trim(); }, {
        label: t('rv.link.url'), attrs: { type: 'url', placeholder: 'https://' },
      }),
      small(t('rv.remove'), reshape((d) => { d.identity.links.splice(index, 1); }), t('rv.remove')),
    ]));
    const country = (d, v) => { d.identity.country = v.trim().toUpperCase() || null; };
    return fieldset('identity', t('rv.section.identity'), [
      field('rv.id.name', 'identity/name', 'full_name'),
      field('rv.id.email', 'identity/email', 'email', { type: 'email' }),
      field('rv.id.phone', 'identity/phone', 'phone', { maxlength: '40' }),
      field('rv.id.city', 'identity/location', 'city'),
      field('rv.id.region', 'identity/region', 'region'),
      labelled(t('rv.id.country'), input('identity/country', id.country, country, {
        label: t('rv.id.country'), attrs: { maxlength: '2' },
      })),
      ...links,
      smallButton(t('rv.link.add'), reshape((d) => {
        d.identity.links.push({ id: ulid(), kind: 'LINKEDIN', url: '', label: null });
      })),
      el('p', { className: 'rve__note', text: t('rv.id.note') }),
    ]);
  }

  function textBlockField(kind, multiline) {
    const block = doc[kind];
    const ref = block ? `${kind}/${block.id}` : `${kind}/new`;
    return input(ref, block ? block.text : '', (d, value) => {
      if (d[kind]) {
        rewordLine(d[kind], value);
        return;
      }
      const line = newLine(value);
      delete line.hidden;
      delete line.flags;
      d[kind] = line;
    }, { label: t(`rv.section.${kind}`), multiline });
  }

  function fieldset(key, legend, children) {
    return el('fieldset', { className: 'rve__set', dataset: { section: key } }, [
      el('legend', { className: 'rve__legend', text: legend }), ...children,
    ]);
  }

  function entryFields(list, entry, index, fields, refBase) {
    const linked = list === 'experience' && entry.experience_id;
    const locateEntry = (d) => d[list][index];
    const ownerLabel = entry.display_title || entry.name || entry.institution || '';
    const children = fields.map(([field, labelKey, required]) => {
      const main = ['display_title', 'name', 'institution'].includes(field);
      const ref = main ? refBase : `${refBase}/${field}`;
      const attrs = linked && field === 'employer' ? { readonly: '' } : {};
      return labelled(t(labelKey), input(ref, entry[field], (d, value) => {
        locateEntry(d)[field] = value || (required ? '' : null);
      }, { label: t(labelKey), attrs }));
    });
    if (linked && entry.source_title) {
      children.push(el('p', { className: 'rve__note', text: t('rv.sourceTitle', { title: entry.source_title }) }));
    }
    children.push(el('div', { className: 'rve__entrybar' }, [
      toggle(t('rv.hideEntry'), entry.hidden, (on) => reshape((d) => { locateEntry(d).hidden = on; })()),
      ...moves(doc[list], index, (d) => d[list]),
      linked ? null : small(t('rv.remove'), reshape((d) => { d[list].splice(index, 1); }), t('rv.remove')),
    ]));
    if ('bullets' in entry) {
      const locate = (d) => locateEntry(d).bullets;
      children.push(
        lines(entry.bullets, refBase, ownerLabel, locate),
        lineActions(locate, ownerLabel, entry.experience_id || null),
      );
    }
    return el('details', { className: 'rve__entry', props: { open: true }, dataset: { ref: refBase } }, [
      el('summary', { className: 'rve__entrytitle', text: ownerLabel || t('rv.untitled') }), ...children,
    ]);
  }

  function listSection(list, fields, blank) {
    return fieldset(list, sectionLabel(doc, list), [
      ...doc[list].map((entry, index) => entryFields(list, entry, index, fields, `${list}/${entry.id}`)),
      smallButton(t(`rv.add.${list}`), reshape((d) => { d[list].push({ id: ulid(), hidden: false, ...blank() }); })),
    ]);
  }

  function certificationSection() {
    const blank = () => ({
      id: ulid(), hidden: false, name: '', issuer: null, issued: null, expires: null, claim_key: null,
    });
    return fieldset('certifications', sectionLabel(doc, 'certifications'), [
      ...doc.certifications.map((c, index) => el('div', { className: 'rve__linkrow' }, [
        input(`certifications/${c.id}`, c.name, (d, v) => { d.certifications[index].name = v; }, {
          label: t('rv.cert.name'),
        }),
        input(`certifications/${c.id}/issuer`, c.issuer, (d, v) => { d.certifications[index].issuer = v || null; }, {
          label: t('rv.cert.issuer'),
        }),
        toggle(t('rv.hide'), c.hidden, (on) => reshape((d) => { d.certifications[index].hidden = on; })()),
        c.claim_key
          ? null
          : small(t('rv.remove'), reshape((d) => { d.certifications.splice(index, 1); }), t('rv.remove')),
      ])),
      smallButton(t('rv.add.certifications'), reshape((d) => { d.certifications.push(blank()); })),
    ]);
  }

  function skillGroup(group, gi) {
    const items = (d) => d.skills[gi].items;
    const rows = group.items.map((item, ii) => el('li', { className: 'rve__line' }, [
      input(`skills/${group.id}/item/${item.id}`, item.label, (d, v) => { items(d)[ii].label = v; }, {
        label: t('rv.skills.item'),
      }),
      el('div', { className: 'rve__linebar' }, [
        el('span', {
          className: 'rve__origin', dataset: { origin: item.origin }, text: originLabel({ ...item, override: 'NONE' }),
        }),
        ...moves(group.items, ii, items),
        small(t('rv.remove'), reshape((d) => { items(d).splice(ii, 1); }), t('rv.skills.removeHint')),
      ]),
    ]));
    const blank = () => ({ id: ulid(), label: '', origin: 'USER_AUTHORED', evidence_ids: [] });
    return el('div', { className: 'rve__group' }, [
      labelled(t('rv.skills.group'), input(`skills/${group.id}`, group.name, (d, v) => { d.skills[gi].name = v; }, {
        label: t('rv.skills.group'),
      })),
      el('ol', { className: 'rve__lines' }, rows),
      el('div', { className: 'rve__actions' }, [
        smallButton(t('rv.skills.add'), reshape((d) => { items(d).push(blank()); })),
        toggle(t('rv.hide'), group.hidden, (on) => reshape((d) => { d.skills[gi].hidden = on; })()),
        small(t('rv.remove'), reshape((d) => { d.skills.splice(gi, 1); }), t('rv.skills.removeHint')),
      ]),
    ]);
  }

  function skillsSection() {
    return fieldset('skills', sectionLabel(doc, 'skills'), [
      ...doc.skills.map((group, gi) => skillGroup(group, gi)),
      smallButton(t('rv.skills.addGroup'), reshape((d) => {
        d.skills.push({ id: ulid(), name: '', hidden: false, items: [] });
      })),
      el('p', { className: 'rve__note', text: t('rv.skills.note') }),
    ]);
  }

  function customSection(custom, index) {
    const locate = (d) => d.custom_sections[index].items;
    const ref = `section/custom:${custom.id}`;
    return fieldset(`custom:${custom.id}`, custom.heading || t('rv.section.custom'), [
      labelled(t('rv.custom.heading'), input(ref, custom.heading, (d, v) => {
        d.custom_sections[index].heading = v;
      }, { label: t('rv.custom.heading') })),
      lines(custom.items, `custom:${custom.id}`, custom.heading, locate),
      lineActions(locate, custom.heading),
    ]);
  }

  /** Order and visibility of whole sections: buttons, and drag as a shortcut. */
  function layoutSection() {
    const order = sectionOrder(doc);
    const hidden = new Set(doc.layout.hidden_sections);
    const setOrder = (next) => reshape((d) => { d.layout.section_order = next; })();
    const rows = order.map((ref, index) => el('li', {
      className: 'rve__order',
      attrs: { draggable: 'true' },
      dataset: { ref },
      on: {
        dragstart: (e) => { e.dataTransfer.setData('text/plain', ref); },
        dragover: (e) => e.preventDefault(),
        drop: (e) => {
          e.preventDefault();
          const from = order.indexOf(e.dataTransfer.getData('text/plain'));
          if (from < 0 || from === index) return;
          const next = [...order];
          next.splice(index, 0, next.splice(from, 1)[0]);
          setOrder(next);
        },
      },
    }, [
      el('span', { className: 'rve__ordername', text: sectionLabel(doc, ref) }),
      small('↑', () => setOrder(move([...order], index, -1)), `${t('rv.moveUp')}: ${sectionLabel(doc, ref)}`,
        index === 0),
      small('↓', () => setOrder(move([...order], index, 1)), `${t('rv.moveDown')}: ${sectionLabel(doc, ref)}`,
        index === order.length - 1),
      toggle(t('rv.show'), !hidden.has(ref), (on) => reshape((d) => {
        const set = new Set(d.layout.hidden_sections);
        if (on) set.delete(ref);
        else set.add(ref);
        d.layout.hidden_sections = [...set].sort();
      })()),
    ]));
    return fieldset('layout', t('rv.section.layout'), [
      el('ol', { className: 'rve__orders' }, rows),
      smallButton(t('rv.add.custom'), reshape((d) => {
        const id = ulid();
        d.custom_sections.push({ id, heading: '', hidden: false, items: [] });
        d.layout.section_order = [...sectionOrder(d).filter((r) => r !== `custom:${id}`), `custom:${id}`];
      })),
    ]);
  }

  const BLANK = {
    experience: () => ({
      employer: '', display_title: '', source_title: null, location: null, start: null, end: null,
      current: false, experience_id: null, bullets: [],
    }),
    projects: () => ({ name: '', role: null, url: null, start: null, end: null, claim_key: null, bullets: [] }),
    education: () => ({
      institution: '', degree: null, field_of_study: null, location: null, start: null, end: null,
      claim_key: null, bullets: [],
    }),
  };

  function drawForm() {
    const active = document.activeElement;
    const key = active && root.contains(active) ? active.dataset.key : null;
    const caret = key && typeof active.selectionStart === 'number'
      ? [active.selectionStart, active.selectionEnd] : null;
    title.value = doc.title;
    form.replaceChildren(
      identityFields(),
      fieldset('headline', t('rv.section.headline'), [textBlockField('headline', false)]),
      fieldset('summary', t('rv.section.summary'), [textBlockField('summary', true)]),
      listSection('experience', [
        ['display_title', 'rv.exp.title', true],
        ['employer', 'rv.exp.employer', true],
        ['location', 'rv.exp.location', false],
      ], BLANK.experience),
      listSection('projects', [
        ['name', 'rv.proj.name', true], ['role', 'rv.proj.role', false], ['url', 'rv.proj.url', false],
      ], BLANK.projects),
      listSection('education', [
        ['institution', 'rv.edu.institution', true],
        ['degree', 'rv.edu.degree', false],
        ['field_of_study', 'rv.edu.field', false],
      ], BLANK.education),
      certificationSection(),
      skillsSection(),
      ...doc.custom_sections.map((c, i) => customSection(c, i)),
      layoutSection(),
    );
    if (key) {
      const again = form.querySelector(`[data-key="${CSS.escape(key)}"]`) || (key === 'title' ? title : null);
      if (again) {
        again.focus();
        if (caret && typeof again.setSelectionRange === 'function') again.setSelectionRange(...caret);
      }
    }
    drawDesign();
  }

  // -- design ---------------------------------------------------------------
  function drawDesign() {
    const d = doc.design;
    const choice = (labelKey, value, options, apply) => labelled(t(labelKey), el('select', {
      className: 'select', attrs: { 'aria-label': t(labelKey) }, on: { change: (e) => apply(e.target.value) },
    }, options.map(([v, label]) => el('option', {
      attrs: { value: v }, text: label, props: { selected: String(v) === String(value) },
    }))));
    const number = (labelKey, value, [min, max, step], apply) => labelled(t(labelKey), el('input', {
      className: 'input',
      attrs: { type: 'number', min: String(min), max: String(max), step: String(step), 'aria-label': t(labelKey) },
      props: { value: String(value) },
      on: {
        change: (e) => {
          const n = Number(e.target.value);
          if (Number.isFinite(n)) apply(Math.min(max, Math.max(min, n)));
        },
      },
    }));
    const set = (fn) => edit(fn);
    const named = (prefix, values) => values.map((v) => [v, t(`${prefix}.${v}`)]);
    design.replaceChildren(
      choice('rv.design.template', d.template, named('rv.template', ['clean', 'modern', 'compact']), (v) => {
        // A new template is a milestone: the version BEFORE it is kept, then the change is made.
        void (valid() ? checkpoint('TEMPLATE_CHANGED') : Promise.resolve())
          .then(() => set((x) => { x.design.template = v; })).catch(failed);
      }),
      choice('rv.design.page', d.page.size, [['A4', 'A4'], ['LETTER', t('rv.page.letter')]], (v) => {
        set((x) => { x.design.page.size = v; });
      }),
      choice('rv.design.font', d.typography.font, [['sans', 'Calibri / Arial'], ['serif', 'Cambria / Georgia']],
        (v) => set((x) => { x.design.typography.font = v; })),
      choice('rv.design.spacing', d.spacing, named('rv.spacing', ['tight', 'normal', 'airy']),
        (v) => set((x) => { x.design.spacing = v; })),
      choice('rv.design.accent', d.accent, named('rv.accent', ['black', 'navy', 'teal', 'burgundy', 'forest']),
        (v) => set((x) => { x.design.accent = v; })),
      choice('rv.design.dates', d.date_format,
        [['mon_yyyy', t('rv.dates.mon')], ['mm_yyyy', '03/2024'], ['yyyy', '2024']],
        (v) => set((x) => { x.design.date_format = v; })),
      number('rv.design.margins', d.page.margins_mm, [8, 30, 1],
        (v) => set((x) => { x.design.page.margins_mm = v; })),
      number('rv.design.size', d.typography.base_pt, [9.5, 12, 0.5],
        (v) => set((x) => { x.design.typography.base_pt = v; })),
      number('rv.design.lineHeight', d.typography.line_height, [1, 1.6, 0.05],
        (v) => set((x) => { x.design.typography.line_height = v; })),
    );
  }

  // -- check ----------------------------------------------------------------
  function drawCheck() {
    const items = ((layout && layout.findings) || []).map((f) => ({
      ref: f.ref, text: t(`rv.find.${f.kind}`), severity: f.severity,
    }));
    for (const ref of (layout && layout.overflow) || []) {
      items.push({ ref, text: t('rv.find.TALLER_THAN_PAGE'), severity: 'problem' });
    }
    if (layout && layout.pages > 2) {
      items.push({ ref: 'document', text: t('rv.find.PAGES', { n: layout.pages }), severity: 'advice' });
    }
    if (!items.length) {
      check.replaceChildren(el('li', { className: 'rve__finding', text: t('rv.find.none') }));
      return;
    }
    check.replaceChildren(...items.map((item) => el('li', {
      className: 'rve__finding', dataset: { severity: item.severity },
    }, [button(item.text, () => focusRef(item.ref), { className: 'btn btn--link' })])));
  }

  // -- preview -> field -----------------------------------------------------
  function focusRef(ref) {
    let path = ref;
    while (path) {
      const target = form.querySelector(`[data-ref="${CSS.escape(path)}"]`);
      if (target) {
        if (root.dataset.mode === 'preview') setMode('edit');
        const details = target.closest('details');
        if (details) details.open = true;
        target.focus();
        target.scrollIntoView({ block: 'center' });
        target.classList.add('rve__flash');
        setTimeout(() => target.classList.remove('rve__flash'), 1400);
        return;
      }
      path = path.includes('/') ? path.slice(0, path.lastIndexOf('/')) : '';
      if (/\/(bullet|item|link)$/.test(path)) path = path.slice(0, path.lastIndexOf('/'));
    }
  }

  // -- add from confirmed evidence ------------------------------------------
  async function pickEvidence(locate, ownerLabel, experienceId) {
    const career = await getCareer();
    // Every line of the document, wherever it sits, already cites what it cites.
    const held = new Set();
    const lists = [...doc.experience, ...doc.projects, ...doc.education].map((e) => e.bullets)
      .concat(doc.custom_sections.map((c) => c.items));
    for (const list of lists) for (const b of list) for (const k of b.evidence_ids) held.add(k);
    const drawer = openDrawer({
      title: t('rv.evidence.title'),
      lede: t('rv.evidence.lede', { to: ownerLabel || t('rv.untitled') }),
    });
    const first = (e) => (e.id === experienceId ? 0 : 1);
    const experiences = [...(career.experiences || [])].sort((a, b) => first(a) - first(b));
    const add = (h) => smallButton(t('rv.evidence.add'), () => {
      reshape((d) => { locate(d).push(evidenceLine(h.claim_key, h.text)); })();
      drawer.close();
    }, { ariaLabel: `${t('rv.evidence.add')}: ${h.text}` });
    const groups = experiences.filter((e) => (e.highlights || []).length).map((e) => el('section', {
      className: 'rve__evgroup',
    }, [
      el('h3', { className: 'rve__evtitle', text: [e.title, e.company].filter(Boolean).join(' · ') }),
      el('ul', { className: 'rve__evlist' }, e.highlights.map((h) => el('li', {}, [
        el('p', { className: 'rve__evtext', text: h.text }),
        held.has(h.claim_key) ? el('span', { className: 'rve__note', text: t('rv.evidence.already') }) : add(h),
      ]))),
    ]));
    drawer.body.replaceChildren(...(groups.length ? groups : [el('p', { text: t('rv.evidence.none') })]));
  }

  // -- a suggestion, applied as an ordinary undoable edit ---------------------
  async function applySuggestion(action) {
    reshape((d) => {
      if (action.type === 'add_bullet') {
        const entry = d.experience.find((e) => e.id === action.entry_id);
        if (entry) {
          entry.bullets.unshift({
            id: ulid(), text: action.text, origin: action.origin, evidence_ids: action.evidence_ids,
            requirement_ids: action.requirement_ids, override: 'NONE', original_text: null, hidden: false,
            flags: [],
          });
        }
      } else if (action.type === 'show') {
        for (const line of linesOf(d).blocks) if (line.id === action.line_id) line.hidden = false;
      } else if (action.type === 'add_skill') {
        if (!d.skills.length) d.skills.push({ id: ulid(), name: t('rv.skills.group'), hidden: false, items: [] });
        d.skills[0].items.unshift({
          id: ulid(), label: action.label, origin: 'EVIDENCE_VERBATIM', evidence_ids: action.evidence_ids,
        });
      }
    })();
    await autosave.flush();
  }

  // -- the Master and its evidence ------------------------------------------
  async function masterNotice() {
    if (answer.kind !== 'MASTER') return;
    const changes = (await getResumeMaster()).evidence_changes;
    if (!changes) return;
    const n = Object.values(changes).reduce((sum, keys) => sum + keys.length, 0);
    if (!n) return;
    notices.append(el('div', { className: 'rve__notice', attrs: { role: 'note' } }, [
      el('p', { text: t('rv.evidenceChanged', { n }) }),
    ]));
  }

  syncButtons();
  drawForm();
  schedulePreview(0);
  void masterNotice();
  if (jobPanel) void jobPanel.load();
  if (restored) {
    // Restored as a new version; lines whose evidence is no longer confirmed
    // are said now, not trusted again.
    say('saved', 'rv.save.restored');
    if ((answer.unconfirmed || []).length) drawEvidence(answer.unconfirmed);
  }

  return {
    root,
    leave,
    relabel() {
      label();
      if (jobPanel) jobPanel.relabel();
      drawForm();
      drawCheck();
    },
    failed,
    destroy: () => {
      clearTimeout(previewTimer);
      clearTimeout(jobTimer);
      preview.destroy();
    },
    dirty: () => autosave.dirty,
  };
}
