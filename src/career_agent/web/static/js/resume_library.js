/**
 * resume_library.js -- My resumes, and moving the retired Resume helper's resumes.
 *
 * MY RESUMES is drawn from ONE request (`GET /api/resume/documents`): the
 * server groups the Master, the standalone resumes and each job's versions,
 * numbered by the store. Nothing here counts versions or works out which job
 * a resume belongs to; it draws what it is given.
 *
 * Each row has one primary action and a "More" disclosure (a native
 * <details>, so it opens and closes from the keyboard). Its panel holds what
 * a row action needs: a rename field, a confirmation, the history with
 * Restore, the downloads. Archive is the strongest action offered; nothing
 * here deletes a resume or its history.
 *
 * COMPARE reads two versions of one job and lists what differs in the
 * resume's own words (added, removed, changed). It writes nothing.
 *
 * THE OLD RESUMES are never moved by opening this page. The person sees what
 * would move, that a backup comes first, and chooses; a failure says so and
 * leaves the old helper there to fall back on.
 */

import {
  compareResumes, copyResume, createJobResume, downloadResumeExport, getLegacyResumes, getResumeHistory,
  listResumeDocuments, listResumeExports, manageResume, moveLegacyResumes, restoreResume,
} from './api.js';
import { button, el } from './dom.js';
import { formatDate } from './format.js';
import { t, tCount } from './i18n.js';
import { inlineConfirm } from './ui.js';

const FILTERS = ['all', 'master', 'jobs', 'imported', 'archived'];
/** The preferred mark is a word and a sign, never a colour alone. */
const STAR = '★';

const small = (label, onClick, extra = {}) => button(label, onClick, { className: 'btn btn--small', ...extra });
const labelled = (text, control) => el('label', { className: 'rve__field' }, [
  el('span', { className: 'rve__label', text }), control,
]);

function failure(error) {
  return (error && error.userMessage) || t('rv.failed');
}

/** "Edited 04 Oct 2026 · Clean · 2 pages · Downloaded PDF 03 Oct 2026". */
function metaLine(item) {
  const last = item.last_export;
  return [
    t('rv.lib.edited', { date: formatDate(item.updated_at) }),
    item.template ? t(`rv.template.${item.template}`) : '',
    last && last.page_count ? t('rv.export.pages', { n: last.page_count }) : '',
    last ? t('rv.lib.downloaded', { format: last.format, date: formatDate(last.created_at) }) : '',
  ].filter(Boolean).join(' · ');
}

/** "V2", or "V2 · US version" once renamed: a version is named after its job until then. */
export function versionLabel(item, heading = '') {
  const number = item.version_number ? `V${item.version_number}` : '';
  return [number, item.title === heading ? '' : item.title].filter(Boolean).join(' \u00b7 ');
}

export function createLibrary({ onOpen, onOpenRestored, onHome, onImport, onAnalyze = () => {} }) {
  let filter = 'all';
  let current = null;
  let busy = false;
  /** Run one act at a time; a click while one is out does nothing. */
  async function once(fn) {
    if (busy) return;
    busy = true;
    try {
      await fn();
    } catch (error) {
      status.textContent = failure(error);
    } finally {
      busy = false;
    }
  }
  const status = el('p', { className: 'rvl__status', attrs: { role: 'status', 'aria-live': 'polite' } });
  const filters = el('div', { className: 'rvl__filters', attrs: { role: 'group' } });
  const body = el('div', { className: 'rvl__body' });
  const root = el('div', { className: 'rvl' }, [filters, status, body]);

  function drawFilters() {
    filters.setAttribute('aria-label', t('rv.lib.show'));
    filters.replaceChildren(...FILTERS.map((key) => button(t(`rv.lib.filter.${key}`), () => {
      filter = key;
      void load();
    }, { className: 'rvl__filter', attrs: { 'aria-pressed': String(key === filter) } })));
  }

  async function load({ focus = null, said = '' } = {}) {
    drawFilters();
    try {
      current = await listResumeDocuments(filter === 'archived');
    } catch (error) {
      body.replaceChildren(el('p', { className: 'rve__notice rve__notice--bad', text: failure(error) }));
      return;
    }
    status.textContent = said;
    draw();
    const row = focus && [...body.querySelectorAll('.rvl__row')].find((r) => r.dataset.doc === focus);
    const target = row && (row.querySelector('.rvl__primary') || row.querySelector('summary'));
    if (target) target.focus();
  }

  function draw() {
    const { master, others, jobs } = current;
    const show = (key) => filter === 'all' || filter === key || filter === 'archived';
    const sections = [];
    if (master && show('master')) {
      sections.push(section('master', t('rv.lib.master'), [list([row(master)])]));
    }
    if (jobs.length && show('jobs')) {
      sections.push(section('jobs', t('rv.lib.jobs'), jobs.map((group) => jobGroup(group))));
    }
    if (others.length && show('imported')) {
      sections.push(section('imported', t('rv.lib.imported'), [list(others.map((d) => row(d)))]));
    }
    if (sections.length) {
      body.replaceChildren(...sections);
      return;
    }
    body.replaceChildren(empty());
  }

  function empty() {
    if (filter === 'archived') return el('p', { className: 'rvw__empty', text: t('rv.lib.noneArchived') });
    if (filter === 'jobs') return el('p', { className: 'rvw__empty', text: t('rv.lib.noneJobs') });
    if (filter === 'master') {
      return el('div', { className: 'rvw__empty' }, [
        el('p', { text: t('rv.lib.noneMaster') }),
        small(t('rv.lib.toHome'), () => onHome()),
      ]);
    }
    if (filter === 'imported') {
      return el('div', { className: 'rvw__empty' }, [
        el('p', { text: t('rv.lib.noneImported') }),
        small(t('rv.home.importButton'), () => onImport()),
      ]);
    }
    return el('div', { className: 'rvw__empty' }, [
      el('p', { text: t('rv.docs.empty') }),
      small(t('rv.lib.toHome'), () => onHome()),
    ]);
  }

  const section = (key, title, children) => el('section', {
    className: 'rvl__section', dataset: { section: key }, attrs: { 'aria-labelledby': `rvl-h-${key}` },
  }, [el('h2', { className: 'rvw__h2', attrs: { id: `rvl-h-${key}` }, text: title }), ...children]);

  const list = (rows) => el('ul', { className: 'rvl__list' }, rows);

  // -- one resume ------------------------------------------------------------
  function row(item, group = null, heading = '') {
    const panel = el('div', { className: 'rvl__panel' });
    const name = versionLabel(item, heading);
    const more = el('details', { className: 'rvl__more' });
    const close = () => { more.open = false; };
    const act = (label, fn) => small(label, () => { close(); fn(); });
    const actions = [];
    if (item.archived) {
      actions.push(item.kind === 'MASTER'
        ? act(t('rv.lib.makeMaster'), () => confirmMaster(item, panel))
        : act(t('rv.lib.unarchive'), () => void change(item, 'unarchive', t('rv.lib.unarchived'))));
    } else if (item.kind === 'TAILORED') {
      if (!item.preferred) {
        actions.push(act(t('rv.lib.prefer'), () => void change(item, 'prefer', t('rv.lib.preferredSaid'))));
      }
      actions.push(act(t('rv.lib.another'), () => void another(group, item)));
      actions.push(act(t('rv.lib.rename'), () => rename(item, panel)));
      actions.push(act(t('rv.lib.archive'), () => confirmArchive(item, panel)));
    } else if (item.kind === 'MASTER') {
      actions.push(act(t('rv.lib.duplicate'), () => void duplicate(item)));
    } else {
      actions.push(act(t('rv.lib.rename'), () => rename(item, panel)));
      actions.push(act(t('rv.lib.duplicate'), () => void duplicate(item)));
      actions.push(act(t('rv.lib.makeMaster'), () => confirmMaster(item, panel)));
      actions.push(act(t('rv.lib.archive'), () => confirmArchive(item, panel)));
    }
    actions.push(act(t('rv.lib.history'), () => void history(item, panel)));
    if (!item.archived) actions.push(act(t('rv.an.title'), () => onAnalyze(item.id)));
    more.append(
      el('summary', {
        className: 'btn btn--small rvl__moresum',
        attrs: { 'aria-label': `${t('rv.lib.more')}: ${name}` },
        text: t('rv.lib.more'),
      }),
      el('div', { className: 'rvl__menu', attrs: { role: 'group', 'aria-label': t('rv.lib.more') } }, actions),
    );
    const verb = t(item.kind === 'TAILORED' ? 'rv.open' : 'rv.lib.edit');
    const primary = item.archived ? null : button(verb, () => onOpen(item.id), {
      className: 'btn btn--small btn--primary rvl__primary', ariaLabel: `${verb}: ${name}`,
    });
    return el('li', { className: 'rvl__row', dataset: { doc: item.id, kind: item.kind } }, [
      el('div', { className: 'rvl__main' }, [
        el('p', { className: 'rvl__name' }, [
          el('span', { className: 'rvl__title', text: name }),
          item.tailored ? el('span', { className: 'rvw__kind', text: t('rv.lib.tailored') }) : null,
          item.ai_assisted ? el('span', { className: 'rvw__kind', text: t('rv.lib.aiAssisted') }) : null,
          item.preferred ? el('span', { className: 'rvl__preferred', text: `${STAR} ${t('rv.lib.preferred')}` }) : null,
          item.kind === 'MASTER' || item.kind === 'TAILORED'
            ? null : el('span', { className: 'rvw__kind', text: t(`rv.kind.${item.kind}`) }),
        ]),
        el('p', { className: 'rvl__meta', text: metaLine(item) }),
      ]),
      el('div', { className: 'rvl__actions' }, [primary, more]),
      panel,
    ]);
  }

  async function change(item, action, said, extra = {}) {
    try {
      await manageResume(item.id, action, extra);
    } catch (error) {
      status.textContent = failure(error);
      return;
    }
    await load({ focus: item.id, said });
  }

  const duplicate = (item) => once(async () => {
    const made = await copyResume(item.id, t('rv.lib.copyTitle', { title: item.title }));
    await load({ focus: made.id, said: t('rv.lib.copied') });
  });

  // A pasted ad's versions have no job id: a copy is that group's next version too.
  const another = (group, item) => once(async () => {
    const made = group.job_id ? await createJobResume(group.job_id, item.id) : await copyResume(item.id);
    onOpen(made.id);
  });

  function rename(item, panel) {
    const input = el('input', {
      className: 'input', attrs: { maxlength: '300', 'aria-label': t('rv.lib.newName') }, props: { value: item.title },
    });
    const save = () => {
      const title = input.value.trim();
      if (title && title !== item.title) void change(item, 'rename', t('rv.lib.renamed'), { title });
      else panel.replaceChildren();
    };
    input.addEventListener('keydown', (event) => {
      if (event.key === 'Enter') save();
      if (event.key === 'Escape') panel.replaceChildren();
    });
    panel.replaceChildren(el('div', { className: 'rvl__rename' }, [
      labelled(t('rv.lib.newName'), input),
      small(t('rv.lib.save'), save, { className: 'btn btn--small btn--primary' }),
      small(t('ui.cancel'), () => panel.replaceChildren()),
    ]));
    input.focus();
    input.select();
  }

  function confirmArchive(item, panel) {
    inlineConfirm(panel, {
      message: t('rv.lib.archiveQ', { title: item.title }),
      detail: t('rv.lib.archiveDetail'),
      confirmLabel: t('rv.lib.archive'),
      onConfirm: () => change(item, 'archive', t('rv.lib.archived')),
    });
  }

  function confirmMaster(item, panel) {
    inlineConfirm(panel, {
      message: t('rv.lib.masterQ', { title: item.title }),
      detail: t('rv.lib.masterDetail'),
      confirmLabel: t('rv.lib.makeMaster'),
      tone: 'blue',
      onConfirm: async () => {
        const made = await manageResume(item.id, 'make_master');
        filter = 'all';
        await load({ focus: made.id, said: t('rv.lib.masterSaid') });
      },
    });
  }

  // -- history and downloads -------------------------------------------------
  async function history(item, panel) {
    panel.replaceChildren(el('p', { className: 'rve__note', text: t('rv.lib.loading') }));
    let log;
    let files;
    try {
      [log, files] = await Promise.all([getResumeHistory(item.id), listResumeExports(item.id)]);
    } catch (error) {
      panel.replaceChildren(el('p', { className: 'rve__notice rve__notice--bad', text: failure(error) }));
      return;
    }
    const restore = (rev) => small(t('rv.hist.restore'), async (event) => {
      event.currentTarget.disabled = true;
      try {
        onOpenRestored(await restoreResume(item.id, rev.id, log.sha256));
      } catch (error) {
        event.currentTarget.disabled = false;
        status.textContent = failure(error);
      }
    }, { ariaLabel: `${t('rv.hist.restore')}: ${t(`rv.hist.reason.${rev.reason}`)}, ${formatDate(rev.created_at)}` });
    const milestones = log.revisions.map((rev) => el('li', { className: 'rvl__hist' }, [
      el('span', { text: `${t(`rv.hist.reason.${rev.reason}`)} · ${formatDate(rev.created_at)}` }),
      rev.current || item.archived
        ? (rev.current ? el('span', { className: 'rvl__current', text: t('rv.hist.current') }) : null)
        : restore(rev),
    ]));
    const downloads = files.map((file) => el('li', { className: 'rvl__hist' }, [
      el('span', {
        text: [
          file.format, formatDate(file.created_at),
          t(file.verified ? 'rv.export.checked' : 'rv.export.problemShort'),
          file.page_count ? t('rv.export.pages', { n: file.page_count }) : '',
        ].filter(Boolean).join(' · '),
      }),
      file.available
        ? small(t('rv.hist.download'), () => void downloadResumeExport(
          file.download, `resume.${file.format.toLowerCase()}`,
        ).catch((error) => { status.textContent = failure(error); }), {
          ariaLabel: `${t('rv.hist.download')}: ${file.format}, ${formatDate(file.created_at)}`,
        })
        : el('span', { className: 'rve__note', text: t('rv.hist.gone') }),
    ]));
    panel.replaceChildren(el('div', {
      className: 'rvl__history', attrs: { role: 'region', 'aria-label': t('rv.lib.history') },
    }, [
      el('h3', { className: 'rvl__h3', text: t('rv.hist.versions') }),
      el('ul', { className: 'rvl__hists' }, milestones),
      el('h3', { className: 'rvl__h3', text: t('rv.hist.downloads') }),
      downloads.length
        ? el('ul', { className: 'rvl__hists' }, downloads)
        : el('p', { className: 'rve__note', text: t('rv.hist.noDownloads') }),
      small(t('rv.lib.closePanel'), () => panel.replaceChildren()),
    ]));
  }

  // -- a job's versions ------------------------------------------------------
  function jobGroup(group) {
    const panel = el('div', { className: 'rvl__panel' });
    const heading = [group.title, group.company].filter(Boolean).join(' · ');
    const versions = group.versions;
    return el('article', { className: 'rvl__group', dataset: { group: group.key } }, [
      el('h3', { className: 'rvl__grouptitle', text: heading }),
      list(versions.map((v) => row(v, group, heading))),
      versions.length > 1 && !versions.some((v) => v.archived)
        ? small(t('rv.cmp.open'), () => drawCompare(group, panel))
        : null,
      panel,
    ]);
  }

  function drawCompare(group, panel) {
    const options = group.versions.map((v) => el('option', { attrs: { value: v.id }, text: `V${v.version_number}` }));
    const pick = (labelKey, chosen) => {
      const node = el('select', { className: 'select', attrs: { 'aria-label': t(labelKey) } },
        options.map((o) => o.cloneNode(true)));
      node.value = chosen;
      return labelled(t(labelKey), node);
    };
    const newest = group.versions[0].id;
    const oldest = group.versions[group.versions.length - 1].id;
    const a = pick('rv.cmp.a', oldest);
    const b = pick('rv.cmp.b', newest);
    const result = el('div', { className: 'rvl__compare', attrs: { 'aria-live': 'polite' } });
    const run = async () => {
      const [x, y] = [a.querySelector('select').value, b.querySelector('select').value];
      if (x === y) {
        result.replaceChildren(el('p', { className: 'rve__note', text: t('rv.cmp.same') }));
        return;
      }
      try {
        drawChanges(result, await compareResumes(x, y));
      } catch (error) {
        result.replaceChildren(el('p', { className: 'rve__notice rve__notice--bad', text: failure(error) }));
      }
    };
    panel.replaceChildren(el('div', {
      className: 'rvl__comparebox', attrs: { role: 'region', 'aria-label': t('rv.cmp.open') },
    }, [
      el('div', { className: 'rvl__rename' }, [a, b, small(t('rv.cmp.run'), () => void run(), {
        className: 'btn btn--small btn--primary',
      }), small(t('rv.lib.closePanel'), () => panel.replaceChildren())]),
      result,
    ]));
    void run();
  }

  return { root, load, relabel: () => { if (current) { drawFilters(); draw(); } } };
}

/** Where a change is, in words: a section's name, or an entry's own name. */
function whereOf(change) {
  if (['section', 'headline', 'summary', 'entry'].includes(change.area)) return t(`rv.section.${change.where}`);
  if (change.area === 'skill') return t('rv.section.skills');
  if (change.area === 'order') return t('rv.section.layout');
  return change.where || t('rv.untitled');
}

const sections = (refs) => refs.map((r) => (r.startsWith('custom:') ? t('rv.section.custom') : t(`rv.section.${r}`)))
  .join(' · ');

function drawChanges(host, answer) {
  const { a, b, changes } = answer;
  const title = el('p', {
    className: 'rvl__cmphead', text: t('rv.cmp.head', { a: a.version_number, b: b.version_number }),
  });
  if (!changes.length) {
    host.replaceChildren(title, el('p', { className: 'rve__note', text: t('rv.cmp.none') }));
    return;
  }
  const items = changes.map((c) => {
    const [before, after] = c.area === 'order' ? [sections(c.before), sections(c.after)] : [c.before, c.after];
    return el('li', { className: 'rvl__change', dataset: { change: c.change } }, [
      el('p', {}, [
        el('strong', { text: t(`rv.cmp.change.${c.change}`) }),
        el('span', { text: ` · ${t(`rv.cmp.area.${c.area}`)} · ${whereOf(c)}` }),
      ]),
      before ? el('p', { className: 'rvl__before', text: t('rv.cmp.before', { text: before }) }) : null,
      after ? el('p', { className: 'rvl__after', text: t('rv.cmp.after', { text: after }) }) : null,
    ]);
  });
  host.replaceChildren(title, el('ul', { className: 'rvl__changes' }, items));
}

// ===========================================================================
// the retired Resume helper's resumes
// ===========================================================================

const NOT_NOW = 'careerAgent.rv.legacyNotNow.v1';

/** "Not now", per profile and per browser; without storage the message just comes back. */
function notNowAnswers() {
  try {
    return JSON.parse(window.localStorage.getItem(NOT_NOW) || '{}') || {};
  } catch {
    return {};
  }
}

const answerOf = (found) => `${found.state}:${found.remaining}`;
const notNow = (profile, found) => notNowAnswers()[profile || 'default'] === answerOf(found);

function rememberNotNow(profile, found) {
  try {
    const all = { ...notNowAnswers(), [profile || 'default']: answerOf(found) };
    window.localStorage.setItem(NOT_NOW, JSON.stringify(all));
  } catch {
    // Not remembered: asked again next time.
  }
}

/**
 * The message about the retired Resume helper's resumes, or null when there
 * is none to show. Nothing is moved until "Move them" is pressed, after the
 * preflight; the helper itself is gone, so a failure offers to try again,
 * says which items stayed and that the old files were not changed.
 */
export async function legacyNotice({ profile, onMoved, onClose = () => {}, force = false }) {
  let found;
  try {
    found = await getLegacyResumes();
  } catch {
    return null;
  }
  const retry = found.state === 'MOVED' && found.remaining > 0;
  if (found.state === 'NONE' || (found.state === 'MOVED' && !retry) || (!force && notNow(profile, found))) {
    return null;
  }
  const box = el('section', {
    className: 'rve__notice rvl__legacy', attrs: { 'aria-labelledby': 'rvl-legacy-h' },
  });
  const say = (children) => box.replaceChildren(
    el('h2', { className: 'rvw__h2', attrs: { id: 'rvl-legacy-h', tabindex: '-1' }, text: t('rv.legacy.found') }),
    ...children.filter(Boolean),
  );
  const close = () => { rememberNotNow(profile, found); box.remove(); onClose(); };

  function ask() {
    say([
      el('p', { text: tCount(retry ? 'rv.legacy.someLeft' : 'rv.legacy.lede', { n: found.remaining }) }),
      el('div', { className: 'rvl__rename' }, [
        small(t(retry ? 'rv.legacy.retry' : 'rv.legacy.review'), () => preflight(), {
          className: 'btn btn--small btn--primary',
        }),
        small(t('rv.legacy.notNow'), close),
      ]),
    ]);
  }

  /** Old downloads that name no single moved version: kept, never guessed. */
  function unmatched(units) {
    const n = units.filter((u) => u.why === 'UNMATCHED').length;
    return n ? el('p', { className: 'rve__note', text: tCount('rv.legacy.unmatched', { n }) }) : null;
  }

  /** "Try again", the failed items on request, and leaving the old files be. */
  function failed(units) {
    const details = units.length
      ? el('details', { className: 'rvl__details' }, [
        el('summary', { text: t('rv.legacy.details') }),
        el('ul', { className: 'rvl__what' }, units.map((u) => el('li', {
          text: [
            u.name ? `${t(`rv.legacy.unit.${u.kind}`)}: ${u.name}` : t(`rv.legacy.unit.${u.kind}`),
            u.why === 'UNMATCHED' ? t('rv.legacy.keptUnchanged') : '',
          ].filter(Boolean).join(' '),
        }))),
        el('p', { className: 'rve__note', text: t('rv.legacy.unsafe') }),
      ])
      : null;
    return [
      el('p', { className: 'rve__note', text: t('rv.legacy.unchanged') }),
      details,
      el('div', { className: 'rvl__rename' }, [
        // Trying again cannot match a download no single version names.
        units.length && units.every((u) => u.why === 'UNMATCHED')
          ? null
          : small(t('rv.legacy.retry'), () => preflight(), { className: 'btn btn--small btn--primary' }),
        small(t('rv.legacy.keep'), close),
      ].filter(Boolean)),
    ];
  }

  function preflight() {
    const counts = [
      ['contactOne', found.contact ? 1 : 0],
      ['base', found.base_resumes],
      ['versions', found.job_versions],
      ['drafts', found.drafts],
      ['exports', found.exports],
    ].filter(([, n]) => n > 0).map(([key, n]) => el('li', { text: tCount(`rv.legacy.what.${key}`, { n }) }));
    const move = small(t('rv.legacy.move'), async () => {
      move.disabled = true;
      say([el('p', { attrs: { role: 'status' }, text: t('rv.legacy.moving') })]);
      try {
        result(await moveLegacyResumes());
      } catch (error) {
        say([
          el('p', { attrs: { role: 'alert' }, text: error.detail && error.detail.code === 'backup_failed'
            ? t('rv.legacy.noBackup') : t('rv.legacy.failed') }),
          ...failed([]),
        ]);
        box.querySelector('h2').focus();
      }
    }, { className: 'btn btn--small btn--primary' });
    say([
      el('p', { text: t('rv.legacy.willMove') }),
      el('ul', { className: 'rvl__what' }, counts),
      found.unfinished
        ? el('p', { className: 'rve__note', text: tCount('rv.legacy.unfinished', { n: found.unfinished }) })
        : null,
      el('p', { className: 'rvl__backup', text: t('rv.legacy.backup') }),
      el('p', { className: 'rve__note', text: t('rv.legacy.kept') }),
      el('div', { className: 'rvl__rename' }, [move, small(t('ui.cancel'), () => ask())]),
    ]);
    box.querySelector('h2').focus();
  }

  function result(out) {
    const moved = [
      ['masters', out.masters], ['imported', out.imported], ['versions', out.job_versions], ['exports', out.exports],
    ].filter(([, n]) => n > 0).map(([key, n]) => el('li', { text: tCount(`rv.legacy.moved.${key}`, { n }) }));
    const nothingNew = !moved.length && out.already > 0;
    say([
      el('p', { attrs: { role: 'status' }, text: t(nothingNew ? 'rv.legacy.already' : 'rv.legacy.done') }),
      moved.length ? el('ul', { className: 'rvl__what' }, moved) : null,
      out.failed
        ? el('p', { attrs: { role: 'alert' }, text: tCount('rv.legacy.someFailed', { n: out.failed }) })
        : null,
      unmatched(out.failures || []),
      ...(out.failed ? failed(out.failures || []) : []),
      small(t('rv.legacy.seeThem'), () => onMoved(), { className: 'btn btn--small btn--primary' }),
    ]);
    box.querySelector('h2').focus();
  }

  ask();
  return box;
}
