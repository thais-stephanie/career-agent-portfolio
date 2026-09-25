/**
 * local-profiles.js -- which person's Career Agent this is, and changing it.
 *
 * LOCAL PROFILES, NOT ACCOUNTS. There is no sign-in and no password: a
 * profile keeps one person's CV, evidence, preferences, scores and
 * applications apart from another's in this app on this computer. The panel
 * says so, because anybody using the same computer account can still read
 * the files.
 *
 * THE RAIL STAYS SHORT. The side rail shows one compact control: whose
 * profile this is, and a way in. Everything else (switching, creating,
 * renaming, deleting) lives in the app's standard side drawer, where
 * switching comes first and each management action reveals its one input
 * only when chosen. Switching reloads the page, so nothing of the previous
 * profile stays in memory on any screen.
 */

import { el, button, replace } from './dom.js';
import { t } from './i18n.js';
import { openDrawer } from './ui.js';
import * as api from './api.js';

const CHANNEL = 'career-agent-local-profile';

export function createLocalProfiles(host) {
  let data = null;
  // Every other tab of this app reloads when one switches profile.
  let channel = null;
  try {
    channel = new BroadcastChannel(CHANNEL);
    channel.onmessage = () => window.location.reload();
  } catch {
    channel = null;
  }

  async function load() {
    try {
      data = await api.getLocalProfiles();
    } catch {
      data = null;
    }
    api.setLocalProfile(data && data.enabled && data.active ? data.active.id : null);
    draw();
  }

  function dot(color) {
    return el('span', {
      className: `lprof__dot lprof__dot--${color || 'teal'}`,
      attrs: { 'aria-hidden': 'true' },
    });
  }

  // ---------------------------------------------------------------- the rail
  function draw() {
    if (!host) return;
    if (!data || !data.enabled || !data.active) {
      replace(host, []);
      host.hidden = true;
      return;
    }
    host.hidden = false;
    const active = data.active;
    replace(host, [
      el('button', {
        className: 'lprof__trigger',
        attrs: {
          type: 'button',
          id: 'lprof-trigger',
          'aria-haspopup': 'dialog',
          'aria-label': t('profiles.activeAria', { name: active.label }),
        },
        on: { click: () => manage() },
      }, [
        dot(active.color),
        el('span', { className: 'lprof__text' }, [
          el('span', { className: 'lprof__kicker', text: t('profiles.kicker') }),
          el('span', { className: 'lprof__name', text: active.label }),
        ]),
        el('span', { className: 'lprof__caret', attrs: { 'aria-hidden': 'true' }, text: '▾' }),
      ]),
    ]);
  }

  // -------------------------------------------------------------- the drawer
  function manage() {
    const drawer = openDrawer({
      eyebrow: t('profiles.kicker'),
      title: t('profiles.drawerTitle'),
      lede: t('profiles.explain'),
      onClose: () => {
        // The rail is redrawn, so focus returns to the new trigger.
        draw();
        const trigger = document.getElementById('lprof-trigger');
        if (trigger) trigger.focus();
      },
    });
    const status = el('p', {
      className: 'lprof__status',
      attrs: { role: 'status', 'aria-live': 'polite', tabindex: '-1' },
    });
    const slot = el('div', { className: 'lprof__slot', attrs: { id: 'lprof-slot' } });
    let open = null;

    function fail(error) {
      status.textContent = error.userMessage || error.message;
    }

    function list() {
      return el('ul', { className: 'lprof__list', attrs: { 'aria-label': t('profiles.listLabel') } },
        data.profiles.map((profile) => el('li', {}, [
          profile.active
            ? el('div', { className: 'lprof__row lprof__row--active', attrs: { 'aria-current': 'true' } }, [
              dot(profile.color),
              el('span', { className: 'lprof__rowname', text: profile.label }),
              el('span', { className: 'lprof__inuse', text: t('profiles.inUse') }),
            ])
            : el('button', {
              className: 'lprof__row lprof__switch',
              dataset: { profile: profile.id },
              attrs: { type: 'button', 'aria-label': t('profiles.switchTo', { name: profile.label }) },
              on: {
                click: async (event) => {
                  const target = event.currentTarget;
                  target.disabled = true;
                  status.textContent = t('profiles.switching');
                  try {
                    await api.switchLocalProfile(profile.id);
                    if (channel) channel.postMessage('switched');
                    window.location.reload();
                  } catch (error) {
                    fail(error);
                    target.disabled = false;
                  }
                },
              },
            }, [
              dot(profile.color),
              el('span', { className: 'lprof__rowname', text: profile.label }),
              el('span', { className: 'lprof__go', attrs: { 'aria-hidden': 'true' }, text: '\u2192' }),
            ]),
        ])));
    }

    function form(key, fields, submitLabel, onSubmit, { danger = false } = {}) {
      const node = el('form', {
        className: `lprof__form${danger ? ' lprof__form--danger' : ''}`,
        dataset: { action: key },
        on: {
          submit: async (event) => {
            event.preventDefault();
            try { await onSubmit(); } catch (error) { fail(error); }
          },
        },
      }, [
        ...fields,
        el('div', { className: 'lprof__formactions' }, [
          el('button', {
            className: danger ? 'btn lprof__danger' : 'btn btn--primary',
            attrs: { type: 'submit' },
            text: submitLabel,
          }),
          button(t('profiles.cancel'), () => toggle(null), { className: 'btn btn--link' }),
        ]),
      ]);
      return node;
    }

    function field(id, label, input, hint = '') {
      return el('div', { className: 'lprof__field' }, [
        el('label', { attrs: { for: id }, text: label }),
        input,
        hint ? el('p', { className: 'field__hint', text: hint }) : null,
      ].filter(Boolean));
    }

    const forms = {
      create: () => {
        const box = el('input', {
          className: 'input', attrs: { id: 'lprof-new', type: 'text', maxlength: '40', autocomplete: 'off' },
        });
        return form('create', [field('lprof-new', t('profiles.newLabel'), box, t('profiles.newHint'))],
          t('profiles.create'), async () => {
            data = await api.createLocalProfile(box.value);
            status.textContent = t('profiles.created', { name: data.created.label });
            toggle(null, { focus: status });
          });
      },
      rename: () => {
        const box = el('input', {
          className: 'input',
          attrs: { id: 'lprof-rename', type: 'text', maxlength: '40', autocomplete: 'off' },
          props: { value: data.active.label },
        });
        return form('rename', [field('lprof-rename', t('profiles.renameLabel'), box)],
          t('profiles.rename'), async () => {
            data = await api.renameLocalProfile(data.active.id, box.value);
            status.textContent = t('profiles.renamed');
            toggle(null, { focus: status });
          });
      },
      remove: () => {
        const deletable = data.profiles.filter((p) => !p.active && !p.original);
        const which = el('select', { className: 'input', attrs: { id: 'lprof-delete-which' } },
          deletable.map((p) => el('option', { attrs: { value: p.id }, text: p.label })));
        const typed = el('input', {
          className: 'input', attrs: { id: 'lprof-delete-confirm', type: 'text', autocomplete: 'off' },
        });
        return form('remove', [
          field('lprof-delete-which', t('profiles.deleteWhich'), which),
          field('lprof-delete-confirm', t('profiles.deleteConfirm'), typed, t('profiles.deleteHint')),
        ], t('profiles.delete'), async () => {
          data = await api.deleteLocalProfile(which.value, typed.value);
          status.textContent = t('profiles.deleted');
          toggle(null, { focus: status });
        }, { danger: true });
      },
    };

    function toggle(key, { focus = null } = {}) {
      const previous = open;
      open = open === key ? null : key;
      render();
      // Never let focus fall out of the drawer when its content is redrawn:
      // into the revealed field, back onto the action that closed, or onto
      // the status line that says what happened.
      if (open) {
        const first = slot.querySelector('input, select');
        if (first) first.focus();
      } else if (focus) {
        focus.focus();
      } else if (previous) {
        const back = document.getElementById(`lprof-action-${previous}`);
        if (back) back.focus();
      }
    }

    function actions() {
      const deletable = data.profiles.some((p) => !p.active && !p.original);
      const action = (key, label) => button(label, () => toggle(key), {
        className: `btn btn--link lprof__action${open === key ? ' is-open' : ''}`,
        attrs: {
          id: `lprof-action-${key}`,
          'aria-expanded': String(open === key),
          'aria-controls': 'lprof-slot',
        },
      });
      return el('div', { className: 'lprof__actions' }, [
        action('create', t('profiles.newAction')),
        action('rename', t('profiles.renameAction')),
        deletable ? action('remove', t('profiles.deleteAction')) : null,
      ].filter(Boolean));
    }

    function render() {
      const rows = list();
      replace(slot, open ? [forms[open]()] : []);
      replace(drawer.body, [
        el('p', { className: 'lprof__label', text: t('profiles.switchHeading') }),
        rows,
        el('p', { className: 'lprof__label lprof__label--quiet', text: t('profiles.manageHeading') }),
        actions(),
        slot,
        status,
      ]);
    }

    render();
  }

  load();
  return { retranslate: draw, reload: load };
}
