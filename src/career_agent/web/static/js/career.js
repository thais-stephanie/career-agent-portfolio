/** Experiences own organization; evidence keeps its original text and history. */
import { el, button, replace, field, select } from './dom.js';
import { t } from './i18n.js';
import * as api from './api.js';

let sequence = 0;
const label = (key, params) => t(`career.${key}`, params);
const paragraph = (key, params) => el('p', { text: label(key, params) });

export function careerWorkspace({ onChanged } = {}) {
  const root = el('section', { className: 'career', attrs: { 'aria-label': label('heading') } });
  const header = el('div');
  const work = el('div', { className: 'career__work' });
  const review = el('div', { className: 'career__review', attrs: { 'aria-live': 'polite' } });
  let data;
  let scope = 'inbox';
  let selected = new Set();
  let generation = 0;
  let filters = { q: '', state: '', category: '', offset: 0 };
  root.append(header, work, review);

  const fail = (error) => replace(review, [el('p', {
    attrs: { role: 'alert' }, text: error.userMessage || error.message,
  })]);
  const run = (fn) => async () => { try { await fn(); } catch (error) { fail(error); } };
  function control(name, input) { return field(`career-${++sequence}`, label(name), input); }
  function title(experience) {
    return [experience.company, experience.title || label('roleUnknown')].filter(Boolean).join(' · ');
  }
  function choose(next) {
    scope = next; filters = { q: '', state: '', category: '', offset: 0 };
    selected = new Set(); replace(review, []);
    void showEvidence().then(() => work.scrollIntoView({ block: 'start' })).catch(fail);
  }
  async function load() {
    data = await api.getCareer();
    replace(header, [
      el('h2', { text: label('heading') }), paragraph('guidance'), paragraph('example'),
      paragraph('boundary'),
      el('div', { className: 'career__actions' }, [
        button(label('new'), () => metadata('create')),
        button(label('inboxCount', { count: data.unassigned }), () => choose('inbox')),
        button(label('allEvidence'), () => choose('')),
      ]),
      el('div', { className: 'career__cards' }, data.experiences.map(experience =>
        el('article', { className: 'career__card', dataset: { experience: experience.id } }, [
          el('h3', { text: experience.company || label('independent') }),
          el('p', { text: experience.title || label('roleUnknown') }),
          el('p', { text: `${experience.period_start || label('dateUnknown')} - ${
            experience.current_role ? label('current') : experience.period_end || label('dateUnknown')}` }),
          paragraph('count', { count: experience.count, confirmed: experience.confirmed }),
          button(label('open'), () => choose(experience.id)),
          button(label('edit'), () => metadata('edit', experience)),
        ]))),
      proposals(),
      historyView(),
    ]);
  }
  function historyView() {
    const items = el('div');
    let shown = 0;
    function append(events) {
      shown += events.length;
      items.append(...events.map(event => el('div', {}, [
        el('span', { text: `${label(`action.${event.action}`)} · ${event.created_at}` }),
        event.reversible && !event.undone_by ? button(label('undo'),
          run(() => preview({ action: 'undo', event_id: event.id }))) : null,
      ])));
    }
    append(data.history);
    const more = button(label('more'), run(async () => {
      const page = await api.getCareerHistory(shown); append(page.items);
      more.hidden = shown >= page.total;
    }));
    more.hidden = shown >= data.history_total;
    return el('details', {}, [el('summary', { text: label('history') }), items, more]);
  }
  function proposals() {
    return el('details', { className: 'career__proposals' }, [
      el('summary', { text: label('proposals', { count: data.proposals.length }) }),
      paragraph('proposalHelp'),
      ...data.aliases.map(alias => el('div', { className: 'career__card' }, [
        el('h4', { text: label('possibleDuplicate') }),
        el('p', { text: `${alias.first} / ${alias.second}` }),
        alias.decision ? paragraph(alias.decision === 'MERGED' ? 'merged' : 'separate') :
          el('div', {}, ['merge_companies', 'keep_separate'].map(action =>
            button(label(`action.${action}`), run(() => preview({ action,
              first: alias.first, second: alias.second }))))),
      ])),
      ...data.proposals.map(proposal => el('div', { className: 'career__card' }, [
        el('h4', { text: title(proposal) }),
        el('p', { text: `${proposal.period_start} - ${proposal.period_end || label('dateUnknown')}` }),
        paragraph('proposalCount', { count: proposal.count }),
        proposal.needs_role || proposal.overlap || proposal.conflict || proposal.date_unknown
          ? paragraph('ambiguity') : null,
        button(label('reviewGroup'), () => metadata('create', proposal, proposal.keys)),
      ])),
    ]);
  }
  function metadata(action, initial = {}, keys = [...selected]) {
    const form = el('div', { className: 'career__metadata' });
    const inputs = {};
    for (const name of ['company', 'title', 'period_start', 'period_end', 'display_order']) {
      inputs[name] = el('input', { className: 'input', attrs: { type: name === 'display_order' ? 'number' :
        name.startsWith('period_') ? 'month' : 'text', maxlength: '200' },
      props: { value: initial[name] ?? (name === 'display_order' ? 0 : '') } });
      form.append(control(name, inputs[name]));
    }
    const current = el('input', { className: 'checkbox',
      attrs: { type: 'checkbox' }, props: { checked: !!initial.current_role } });
    inputs.period_end.disabled = current.checked;
    current.addEventListener('change', () => {
      inputs.period_end.disabled = current.checked;
      if (current.checked) inputs.period_end.value = '';
    });
    const kind = select(data.kinds.map(value => ({ value, label: label(`kind.${value}`) })),
      initial.kind || 'EMPLOYMENT', () => {});
    form.append(control('current_role', current), control('kind', kind), paragraph('metadataHelp'),
      button(label('preview'), run(() => {
        const values = Object.fromEntries(Object.entries(inputs).map(([key, input]) =>
          [key, key === 'display_order' ? Number(input.value) : input.value.trim() || null]));
        return preview({ action, keys, ...(action === 'edit' ? { experience_id: initial.id } : {}),
          metadata: { ...values, current_role: current.checked, kind: kind.value } });
      })), button(label('cancel'), () => replace(review, [])));
    replace(review, [el('h3', { text: label(action === 'edit' ? 'edit' : 'new') }), form]);
    inputs.company.focus();
  }
  async function preview(command) {
    const result = await api.previewCareer(command);
    const statements = el('div');
    let visible = 0;
    const more = button(label('more'), () => showMore());
    function showMore() {
      const slice = result.items.slice(visible, visible + 50);
      for (const item of slice) statements.append(el('p', { text: item.text }));
      visible += slice.length; more.hidden = visible >= result.items.length;
    }
    showMore();
    const destination = data.experiences.find(e => e.id === command.experience_id);
    const checked = el('input', { className: 'checkbox', attrs: { type: 'checkbox' } });
    const apply = button(label('apply'), run(async () => {
      apply.disabled = true;
      try {
        await api.applyCareer(result, checked.checked);
        selected.clear(); replace(review, []); await load(); await showEvidence();
        work.scrollIntoView({ block: 'start' });
        if (onChanged) onChanged();
      } finally { apply.disabled = false; }
    }));
    apply.disabled = command.action === 'confirm';
    checked.addEventListener('change', () => { apply.disabled = !checked.checked; });
    replace(review, [el('h3', { text: label(`action.${command.action}`) }),
      paragraph('scope', { count: result.count }), paragraph('preserve'),
      command.first ? paragraph('aliasScope', { first: command.first, second: command.second }) : null,
      command.action === 'move' || command.action === 'merge_experiences'
        ? paragraph('destinationScope', { destination: destination ? title(destination) : label('inbox') }) : null,
      command.metadata ? el('dl', {}, Object.entries(command.metadata).flatMap(([key, value]) => [
        el('dt', { text: label(key) }), el('dd', { text: value ?? label('dateUnknown') }),
      ])) : null,
      statements, more,
      command.action === 'confirm' ? control('reviewed', checked) : null,
      apply, button(label('cancel'), () => replace(review, [])),
    ]);
    review.scrollIntoView({ block: 'nearest' });
  }
  async function showEvidence() {
    const mine = ++generation;
    const page = await api.getCareerEvidence({ experience: scope, ...filters, limit: 40 });
    if (mine !== generation) return;
    const summary = data.experiences.find(e => e.id === scope);
    const status = el('span', { attrs: { role: 'status' } });
    const checkboxes = [];
    function selectionChanged() {
      status.textContent = label('selected', { count: selected.size });
      for (const [checkbox, key] of checkboxes) checkbox.checked = selected.has(key);
    }
    const query = el('input', { className: 'input', attrs: { type: 'search' }, props: { value: filters.q } });
    const filter = () => {
      filters.q = query.value; filters.offset = 0; selected.clear();
      void showEvidence().catch(fail);
    };
    query.addEventListener('keydown', event => { if (event.key === 'Enter') filter(); });
    const state = select(['', 'CONFIRMED', 'PENDING', 'RETIRED', 'REJECTED'].map(value =>
      ({ value, label: value ? label(`state.${value}`) : label('anyState') })), filters.state,
    value => { filters.state = value; filter(); });
    const category = select([{ value: '', label: label('anyCategory') }, ...data.categories.map(value =>
      ({ value, label: label(`category.${value}`) }))], filters.category,
    value => { filters.category = value; filter(); });
    const targets = [{ value: '', label: label('inbox') }, ...data.experiences.map(e =>
      ({ value: e.id, label: title(e) }))];
    const destination = select(targets, '', () => {});
    const changeCategory = select(data.categories.map(value =>
      ({ value, label: label(`category.${value}`) })), 'RESPONSIBILITY', () => {});
    const bulk = (action, extra = {}) => run(() => preview({ action, keys: [...selected], ...extra }));
    const rows = page.items.map(item => {
      const checkbox = el('input', { className: 'checkbox', attrs: { type: 'checkbox', 'aria-label': item.text },
        on: { change: event => { if (event.target.checked) selected.add(item.claim_key);
          else selected.delete(item.claim_key); selectionChanged(); } } });
      checkboxes.push([checkbox, item.claim_key]);
      const source = el('details', {}, [el('summary', { text: label('source') })]);
      source.addEventListener('toggle', () => {
        if (!source.open || source.dataset.loaded) return;
        source.dataset.loaded = 'true';
        source.append(item.evidence_ref ? el('blockquote', { text: item.evidence_ref }) : paragraph('noSourceQuote'),
          ...item.source_records.map(record => {
            const refs = (record.source_ref || '').split(',');
            const names = (record.documents || []).filter(d => refs.includes(d.ref)).map(d => d.title);
            return el('section', {}, [el('h4', { text: names.join(' · ') }),
              el('p', { text: [record.employer, record.role_title, record.period?.start?.original,
                record.period?.end?.original].filter(Boolean).join(' · ') }),
              paragraph('importedStatement'), el('p', { text: record.text }),
              record.evidence?.quote ? el('blockquote', { text: record.evidence.quote }) : null,
              record.evidence?.locator ? el('p', { text: record.evidence.locator }) : null,
            ]);
          }));
        if (item.origin === 'claim') {
          const revisions = el('div');
          source.append(button(label('revisions'), run(async () => {
            const history = await api.getClaimHistory(item.claim_key);
            replace(revisions, history.revisions.map(version => el('div', {}, [
              paragraph('revision', { number: version.revision }), el('p', { text: version.text }),
            ])));
          })), revisions);
        }
      });
      const editHost = el('div');
      function editText() {
        const text = el('textarea', { className: 'input', props: { value: item.text }, attrs: { maxlength: '2000' } });
        replace(editHost, [control('evidenceText', text), paragraph('textEditHelp'),
          button(label('saveText'), run(async () => {
            await api.editClaim(item.claim_key, { text: text.value });
            await load(); await showEvidence(); if (onChanged) onChanged();
          })), button(label('cancel'), () => replace(editHost, []))]);
        text.focus();
      }
      return el('article', { className: 'career__evidence', dataset: {
        evidenceKey: item.claim_key, category: item.category,
      } }, [
        checkbox, el('div', {}, [el('p', { text: item.text }),
          el('small', { text: label('itemMeta', {
            category: label(`category.${item.category}`), state: label(`state.${item.state}`),
          }) }),
          item.origin === 'claim' ? button(label('editText'), editText,
            { className: 'btn btn--small btn--quiet' }) : paragraph('editImportHelp'),
          editHost, source]),
      ]);
    });
    selectionChanged();
    replace(work, [el('h3', { text: summary ? title(summary) : label(scope ? 'inbox' : 'allEvidence') }),
      scope === 'inbox' ? paragraph('inboxHelp') : null,
      el('div', { className: 'career__filters' }, [control('find', query),
        button(label('filter'), filter), control('state', state), control('category', category)]),
      el('div', { className: 'career__actions career__bulk' }, [status,
        button(label('selectFiltered', { count: page.total }), () => {
          selected = new Set(page.keys); selectionChanged(); }),
        summary ? button(label('selectExperience'), () => {
          selected = new Set(summary.keys); selectionChanged(); }) : null,
        button(label('clear'), () => { selected.clear(); selectionChanged(); }),
        button(label('action.confirm'), bulk('confirm')), button(label('action.retire'), bulk('retire')),
      ]),
      el('details', {}, [el('summary', { text: label('organizeSelected') }),
        control('destination', destination),
        button(label('action.move'), () => bulk('move', { experience_id: destination.value || null })()),
        control('category', changeCategory),
        button(label('action.category'), () => bulk('category', { category: changeCategory.value })()),
        button(label('action.split'), () => metadata('split')),
        summary ? button(label('action.merge_experiences'), run(() => preview({
          action: 'merge_experiences', source_id: summary.id, experience_id: destination.value,
        }))) : null,
      ]),
      ...data.categories.map(categoryName => {
        const group = rows.filter(row => row.dataset.category === categoryName);
        return group.length ? el('details', { className: 'career__category', props: { open: true } }, [
          el('summary', { text: `${label(`category.${categoryName}`)} (${group.length})` }), ...group,
        ]) : null;
      }),
      paragraph('page', { start: page.total ? filters.offset + 1 : 0,
        end: Math.min(filters.offset + 40, page.total), total: page.total }),
      filters.offset ? button(label('previous'), run(() => {
        filters.offset = Math.max(0, filters.offset - 40); return showEvidence(); })) : null,
      filters.offset + 40 < page.total ? button(label('more'), run(() => {
        filters.offset += 40; return showEvidence(); })) : null,
    ]);
  }
  void load().catch(fail);
  return root;
}
