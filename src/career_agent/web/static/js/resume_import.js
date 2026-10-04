/**
 * resume_import.js -- Import a PDF or DOCX into Resume Workspace V2 (internal page).
 *
 * Pick a file -> the server reads it (nothing is saved) -> "We found this.
 * Check it before saving." Every value is an editable field with how sure the
 * reader was (a word and a mark, never colour alone) and, when it is not
 * sure, the source line it came from. Nothing is stored until Save; Save
 * sends the reviewed values and the server builds the document itself, every
 * line marked as imported. The review is a step before the Editor, not a
 * second editor: once saved, the document opens in the existing Editor.
 *
 * "Propose these details for My experience" is a separate, later choice: it
 * hands the same file to the existing Career Evidence reading, which confirms
 * nothing on anybody's behalf. The file stays in this page's memory only.
 */

import { importCv, readResumeImport, saveResumeImport } from './api.js';
import { button, el } from './dom.js';
import { t } from './i18n.js';
import { ulid } from './resume_editor.js';

const MARKS = { HIGH: '✓', MEDIUM: '~', LOW: '!' };
const LANGUAGES = ['en', 'pt', 'es'];
const slug = (ref) => `rvi-${ref.replace(/[^A-Za-z0-9]/g, '-')}`;
/** The reader's "page 1, line 4" / "paragraph 7" / "table 1, row 2, cell 1", in the page's language. */
function whereText(where) {
  return where.split(' to ').map((one) => {
    const link = one.endsWith(' (link)');
    const bare = link ? one.slice(0, -7) : one;
    let m = bare.match(/^page (\d+), line (\d+)$/);
    let said = bare;
    if (m) said = t('rv.import.where.line', { page: m[1], line: m[2] });
    else if ((m = bare.match(/^paragraph (\d+)$/))) said = t('rv.import.where.paragraph', { n: m[1] });
    else if ((m = bare.match(/^table (\d+), row (\d+), cell (\d+)$/))) {
      said = t('rv.import.where.cell', { table: m[1], row: m[2], cell: m[3] });
    }
    return link ? t('rv.import.where.link', { where: said }) : said;
  }).join(t('rv.import.where.to'));
}
const blankLine = (text = '') => ({
  id: ulid(), text: { value: text, confidence: 'HIGH', source: null, alternatives: [], note: null },
});

export function createResumeImport({ hasMaster: masterAtStart, onSaved, onCancel, onEvidence, onScratch }) {
  let hasMaster = masterAtStart;
  const root = el('section', { className: 'rvi', attrs: { 'aria-labelledby': 'rvi-title' } });
  const status = el('p', { className: 'rvi__status', attrs: { role: 'status', 'aria-live': 'polite' } });
  const alert = el('div', { className: 'rvi__alert', attrs: { role: 'alert' } });
  let file = null;
  let proposal = null;
  let saving = false;
  pick();
  return { root };

  // -- step 1: the file -----------------------------------------------------
  function pick() {
    const input = el('input', {
      className: 'rvi__file',
      attrs: { type: 'file', id: 'rvi-file', accept: '.pdf,.docx' },
      on: {
        change: (event) => {
          const chosen = event.target.files && event.target.files[0];
          if (chosen) void read(chosen);
        },
      },
    });
    root.dataset.step = 'pick';
    root.replaceChildren(
      el('h2', { className: 'rvw__h2', attrs: { id: 'rvi-title' }, text: t('rv.import.title') }),
      el('p', { className: 'rvi__lede', text: t('rv.import.lede') }),
      el('div', { className: 'rvi__pick' }, [
        input,
        el('label', { className: 'btn btn--primary', attrs: { for: 'rvi-file' }, text: t('rv.import.choose') }),
        button(t('rv.import.cancel'), () => onCancel(), { className: 'btn' }),
      ]),
      status, alert,
    );
  }

  async function read(chosen) {
    file = chosen;
    alert.replaceChildren();
    status.textContent = t('rv.import.reading', { name: chosen.name });
    try {
      proposal = await readResumeImport(chosen.name, await chosen.arrayBuffer());
      status.textContent = '';
      review();
    } catch (error) {
      status.textContent = '';
      refused(error);
    }
  }

  function refused(error) {
    const reason = (error.detail && error.detail.reason) || '';
    const known = reason && t(`rv.import.refused.${reason}`) !== `rv.import.refused.${reason}`;
    const children = [el('p', { text: known ? t(`rv.import.refused.${reason}`) : t('rv.import.failed') })];
    if (reason === 'NO_TEXT') {
      children.push(el('ul', { className: 'rvi__ways' }, [
        el('li', { text: t('rv.import.way.docx') }),
        el('li', { text: t('rv.import.way.pdf') }),
        el('li', {}, [button(t('rv.import.way.scratch'), () => onScratch(), { className: 'btn btn--small' })]),
      ]));
    }
    alert.replaceChildren(el('div', { className: 'rve__notice rve__notice--bad' }, children));
    const input = root.querySelector('#rvi-file');
    if (input) input.value = '';
  }

  // -- step 2: the review ---------------------------------------------------
  function review() {
    const p = proposal;
    const toCheck = (p.report.confidence && p.report.confidence.LOW) || 0;
    root.dataset.step = 'review';
    root.replaceChildren(
      el('h2', { className: 'rvw__h2', attrs: { id: 'rvi-title', tabindex: '-1' }, text: t('rv.import.found') }),
      el('p', { className: 'rvi__lede', text: t('rv.import.foundLede', { name: p.report.filename }) }),
      toCheck ? el('p', { className: 'rvi__tocheck', text: t('rv.import.toCheck', { n: toCheck }) }) : null,
      el('div', { className: 'rvi__form' }, [
        set('about', t('rv.import.section.about'), [
          field('title', t('rv.import.f.title'), { value: p.title, confidence: 'NONE' }, (v) => { p.title = v; },
            { required: true }),
          languageField(),
        ]),
        set('identity', t('rv.section.identity'), [
          field('identity/name', t('rv.import.f.name'), p.identity.name, (v) => put(p.identity, 'name', v)),
          field('identity/email', t('rv.import.f.email'), p.identity.email, (v) => put(p.identity, 'email', v)),
          field('identity/phone', t('rv.import.f.phone'), p.identity.phone, (v) => put(p.identity, 'phone', v)),
          field('identity/location', t('rv.import.f.location'), p.identity.location,
            (v) => put(p.identity, 'location', v)),
          ...p.identity.links.map((link) => field(`identity/links/${link.id}`,
            t(`rv.link.${link.kind}`), link.url, (v) => { link.url.value = v; })),
        ]),
        set('headline', t('rv.section.headline'), [
          field('headline', t('rv.section.headline'), p.headline && p.headline.text,
            (v) => { p.headline = p.headline || blankLine(); p.headline.text.value = v; }),
        ]),
        set('summary', t('rv.section.summary'), [
          field('summary', t('rv.section.summary'), p.summary && p.summary.text,
            (v) => { p.summary = p.summary || blankLine(); p.summary.text.value = v; }, { multiline: true }),
        ]),
        entries('experience', [['title', 'rv.import.f.role', true], ['org', 'rv.import.f.company', true],
          ['location', 'rv.import.f.where', false]]),
        entries('projects', [['title', 'rv.import.f.project', true], ['org', 'rv.import.f.projectRole', false],
          ['url', 'rv.import.f.url', false]]),
        entries('education', [['title', 'rv.import.f.degree', false], ['org', 'rv.import.f.school', true],
          ['location', 'rv.import.f.where', false]]),
        entries('certifications', [['title', 'rv.import.f.certificate', true],
          ['org', 'rv.import.f.issuer', false]]),
        groups('skills', p.skills, t('rv.section.skills'), 'rv.import.f.group', 'rv.import.f.skillItems'),
        groups('other', p.other, t('rv.import.section.other'), 'rv.import.f.heading', 'rv.import.f.otherLines'),
      ]),
      extracted(),
      saveArea(),
      status, alert,
    );
    const first = root.querySelector('.rvi__field[data-confidence="LOW"] :is(input, textarea)');
    (first || root.querySelector('#rvi-title')).focus({ preventScroll: false });
  }

  function set(key, legend, children) {
    return el('fieldset', { className: 'rve__set rvi__set', dataset: { section: key } }, [
      el('legend', { className: 'rve__legend', text: legend }), ...children,
    ]);
  }

  /** Set a field's value, making the field when the reader found none. */
  function put(holder, key, value) {
    if (!holder[key]) holder[key] = { value, confidence: 'HIGH', source: null, alternatives: [], note: null };
    else holder[key].value = value;
  }

  /** One value: label, control, how sure the reader was, and where it came from. */
  function field(ref, label, found, apply, { multiline = false, required = false } = {}) {
    const id = slug(ref);
    const confidence = found && found.value ? found.confidence : required ? 'LOW' : 'NONE';
    const control = el(multiline ? 'textarea' : 'input', {
      className: multiline ? 'rve__textarea' : 'input',
      attrs: {
        id, 'data-ref': ref, 'aria-describedby': `${id}-about`, ...(required ? { 'aria-required': 'true' } : {}),
      },
      props: { value: (found && found.value) || '' },
      on: { input: (event) => { apply(event.target.value); clearProblem(control); } },
    });
    const about = el('span', { className: 'rvi__about', attrs: { id: `${id}-about` } });
    if (confidence !== 'NONE') {
      about.append(el('span', { className: 'rvi__conf', dataset: { confidence } }, [
        el('span', { className: 'rve__mark', attrs: { 'aria-hidden': 'true' }, text: MARKS[confidence] }),
        el('span', { text: t(`rv.import.conf.${confidence}`) }),
      ]));
    }
    if (found && found.note) {
      about.append(el('span', { className: 'rvi__why', text: t(`rv.import.note.${found.note}`) }));
    }
    if (found && found.source && confidence !== 'HIGH') {
      about.append(el('span', {
        className: 'rvi__source',
        text: t('rv.import.from', { where: whereText(found.source.where), text: found.source.text.slice(0, 180) }),
      }));
    }
    for (const other of (found && found.alternatives) || []) {
      about.append(button(t('rv.import.use', { value: other }), () => {
        control.value = other;
        apply(other);
      }, { className: 'btn btn--small btn--quiet' }));
    }
    about.append(el('span', { className: 'rve__hint rvi__problem' }));
    return el('div', { className: 'rvi__field', dataset: { confidence } }, [
      el('label', { className: 'rve__label', attrs: { for: id }, text: label }), control, about,
    ]);
  }

  function languageField() {
    const select = el('select', {
      className: 'input', attrs: { id: 'rvi-language' },
      on: { change: (event) => { proposal.language = event.target.value; } },
    }, LANGUAGES.map((code) => el('option', {
      attrs: { value: code, ...(proposal.language === code ? { selected: '' } : {}) }, text: t(`rv.lang.${code}`),
    })));
    return el('div', { className: 'rvi__field', dataset: { confidence: 'NONE' } }, [
      el('label', { className: 'rve__label', attrs: { for: 'rvi-language' }, text: t('rv.import.f.language') }), select,
    ]);
  }

  /** An entry list: its fields, dates, lines; each entry can be removed. */
  function entries(list, fields) {
    const items = proposal[list];
    const box = set(list, t(`rv.section.${list}`), []);
    const draw = () => {
      box.replaceChildren(el('legend', { className: 'rve__legend', text: t(`rv.section.${list}`) }),
        ...(items.length ? [] : [el('p', { className: 'rve__note', text: t('rv.import.none') })]),
        ...items.map((entry, index) => el('div', { className: 'rvi__entry' }, [
          ...fields.map(([key, labelKey, required]) => field(`${entry.id}/${key}`, t(labelKey), entry[key],
            (v) => put(entry, key, v), { required })),
          el('div', { className: 'rvi__dates' }, [
            field(`${entry.id}/start`, t(list === 'certifications' ? 'rv.import.f.issued' : 'rv.import.f.start'),
              entry.start, (v) => put(entry, 'start', v)),
            list === 'certifications' ? null : field(`${entry.id}/end`, t('rv.import.f.end'), entry.end,
              (v) => put(entry, 'end', v)),
            list === 'certifications' ? null : el('label', { className: 'rve__toggle' }, [
              el('input', { attrs: { type: 'checkbox' }, props: { checked: entry.current },
                on: { change: (event) => { entry.current = event.target.checked; } } }),
              el('span', { text: t('rv.import.f.current') }),
            ]),
          ]),
          list === 'certifications' ? null : lineList(entry.lines, entry.id),
          button(t('rv.import.removeEntry'), () => { items.splice(index, 1); draw(); },
            { className: 'btn btn--small btn--quiet' }),
        ])));
    };
    draw();
    return box;
  }

  /** An entry's lines, one field each, as the file wrote them. */
  function lineList(lines, owner) {
    const box = el('ul', { className: 'rve__lines' });
    const draw = () => {
      box.replaceChildren(...lines.map((line, index) => el('li', { className: 'rve__line' }, [
        field(`${owner}/lines/${line.id}`, t('rv.import.f.line', { n: index + 1 }), line.text,
          (v) => { line.text.value = v; }, { multiline: true }),
        button(t('rv.import.removeLine'), () => { lines.splice(index, 1); draw(); },
          { className: 'btn btn--small btn--quiet' }),
      ])), el('li', {}, [button(t('rv.import.addLine'), () => { lines.push(blankLine()); draw(); },
        { className: 'btn btn--small' })]));
    };
    draw();
    return box;
  }

  /** Skill groups, or other content: a name, then one item per line. */
  function groups(key, list, legend, nameKey, itemsKey) {
    const box = set(key, legend, []);
    const draw = () => {
      box.replaceChildren(el('legend', { className: 'rve__legend', text: legend }),
        ...(list.length ? [] : [el('p', { className: 'rve__note', text: t('rv.import.none') })]),
        ...list.map((group, index) => el('div', { className: 'rvi__entry' }, [
          field(`${group.id}/name`, t(nameKey), group.name, (v) => { group.name.value = v; }, { required: true }),
          field(`${group.id}/lines`, t(itemsKey), {
            value: group.lines.map((line) => line.text.value).join('\n'),
            confidence: group.lines.some((line) => line.text.confidence !== 'HIGH') ? 'MEDIUM' : 'HIGH',
          }, (v) => {
            const typed = v.split('\n').map((s) => s.trim()).filter(Boolean);
            group.lines = typed.map((text, i) => {
              const kept = group.lines[i] || blankLine();
              kept.text.value = text;
              return kept;
            });
          }, { multiline: true }),
          button(t(key === 'other' ? 'rv.import.removeSection' : 'rv.import.removeGroup'), () => {
            list.splice(index, 1);
            draw();
          }, { className: 'btn btn--small btn--quiet' }),
        ])));
    };
    draw();
    return box;
  }

  function extracted() {
    const r = proposal.report;
    return el('details', { className: 'rve__panel rvi__raw' }, [
      el('summary', { text: t('rv.import.raw') }),
      el('p', { className: 'rve__note', text: t('rv.import.rawAbout', {
        format: r.format, pages: r.pages, n: r.characters, version: r.parser_version,
      }) }),
      el('ol', { className: 'rvi__rawlist' }, proposal.source.map((line) => el('li', {}, [
        el('span', { className: 'rvi__where', text: whereText(line.where) }), el('span', { text: line.text }),
      ]))),
    ]);
  }

  // -- step 3: save -----------------------------------------------------------
  function saveArea() {
    const choices = hasMaster ? ['IMPORTED', 'REPLACE_MASTER'] : ['MASTER', 'IMPORTED'];
    const radios = el('fieldset', { className: 'rve__set rvi__dest' }, [
      el('legend', { className: 'rve__legend', text: t('rv.import.saveAs') }),
      ...choices.map((value, index) => el('label', { className: 'rve__toggle' }, [
        el('input', { attrs: { type: 'radio', name: 'rvi-dest', value, ...(index === 0 ? { checked: '' } : {}) } }),
        el('span', { text: t(`rv.import.dest.${value}`) }),
      ])),
    ]);
    const save = button(t('rv.import.save'), () => void submit(radios, save), { className: 'btn btn--primary' });
    return el('div', { className: 'rvi__save' }, [
      radios,
      el('div', { className: 'rve__actions' }, [
        save, button(t('rv.import.cancel'), () => onCancel(), { className: 'btn' }),
      ]),
    ]);
  }

  async function submit(radios, save) {
    if (saving) return; // a double click sends one save
    saving = true;
    save.disabled = true;
    alert.replaceChildren();
    status.textContent = t('rv.import.saving');
    const destination = radios.querySelector('input:checked').value;
    try {
      const saved = await saveResumeImport({ ...proposal, source: [] }, destination);
      done(saved);
    } catch (error) {
      status.textContent = '';
      const code = error.detail && error.detail.code;
      if (code === 'master_exists' && !hasMaster) {
        hasMaster = true; // made in another window: offer replacing it, on purpose
        root.querySelector('.rvi__save').replaceWith(saveArea());
      }
      if (code === 'import_incomplete') flag(error.detail.fields || []);
      alert.replaceChildren(el('div', { className: 'rve__notice rve__notice--bad' }, [
        el('p', { text: t(code ? `rv.import.err.${code}` : 'rv.import.failed') }),
      ]));
    } finally {
      saving = false;
      save.disabled = false;
    }
  }

  function flag(refs) {
    let first = null;
    for (const ref of refs) {
      const control = root.querySelector(`[data-ref="${CSS.escape(ref)}"]`);
      if (!control) continue;
      control.setAttribute('aria-invalid', 'true');
      const problem = control.parentElement.querySelector('.rvi__problem');
      if (problem) problem.textContent = t('rv.import.needed');
      first = first || control;
    }
    if (first) first.focus();
  }

  function clearProblem(control) {
    control.setAttribute('aria-invalid', 'false');
    const problem = control.parentElement.querySelector('.rvi__problem');
    if (problem) problem.textContent = '';
  }

  function done(saved) {
    root.dataset.step = 'saved';
    const note = el('p', { className: 'rve__note', attrs: { role: 'status', 'aria-live': 'polite' } });
    const propose = button(t('rv.import.propose'), async () => {
      propose.disabled = true;
      note.textContent = t('rv.import.proposing');
      try {
        const read = await importCv(file.name, await file.arrayBuffer());
        const waiting = (read.summary && read.summary.waiting) || 0;
        note.textContent = t('rv.import.proposed', { n: waiting });
        note.after(button(t('rv.import.toEvidence'), () => onEvidence(), { className: 'btn btn--small' }));
      } catch (error) {
        note.textContent = error.userMessage || t('rv.import.failed');
        propose.disabled = false;
      }
    }, { className: 'btn' });
    root.replaceChildren(
      el('h2', { className: 'rvw__h2', attrs: { id: 'rvi-title', tabindex: '-1' }, text: t('rv.import.saved') }),
      el('p', { className: 'rvi__lede', text: t(`rv.import.savedAs.${saved.kind}`) }),
      el('div', { className: 'rve__actions' }, [
        button(t('rv.import.open'), () => onSaved(saved.id), { className: 'btn btn--primary' }),
        propose,
      ]),
      el('p', { className: 'rve__note', text: t('rv.import.proposeAbout') }),
      note,
    );
    root.querySelector('#rvi-title').focus();
  }
}
