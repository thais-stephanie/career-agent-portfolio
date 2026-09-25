/**
 * local-profiles.js -- which person's Career Agent this is, and changing it.
 *
 * LOCAL PROFILES, NOT ACCOUNTS. There is no sign-in and no password: a
 * profile keeps one person's CV, evidence, preferences, scores and
 * applications apart from another's in this app on this computer. The panel
 * says so, because anybody using the same computer account can still read
 * the files.
 *
 * The active profile is always on screen, directly under the name of the
 * app. Switching reloads the page, so nothing of the previous profile stays
 * in memory on any screen.
 */

import { el, button, replace } from './dom.js';
import { t } from './i18n.js';
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
  const status = el('p', { className: 'lprof__status', attrs: { role: 'status', 'aria-live': 'polite' } });

  async function load() {
    try {
      data = await api.getLocalProfiles();
    } catch {
      data = null;
    }
    api.setLocalProfile(data && data.enabled && data.active ? data.active.id : null);
    draw();
  }

  function fail(error) {
    status.textContent = error.userMessage || error.message;
  }

  function draw() {
    if (!host) return;
    if (!data || !data.enabled || !data.active) {
      replace(host, []);
      host.hidden = true;
      return;
    }
    host.hidden = false;
    const active = data.active;
    const others = data.profiles.filter((p) => !p.active);

    const switchList = el('ul', { className: 'lprof__list' }, others.map((profile) => el('li', {}, [
      button(t('profiles.switchTo', { name: profile.label }), async (event) => {
        event.target.disabled = true;
        status.textContent = t('profiles.switching');
        try {
          await api.switchLocalProfile(profile.id);
          if (channel) channel.postMessage('switched');
          window.location.reload();
        } catch (error) {
          fail(error);
          event.target.disabled = false;
        }
      }, { className: 'btn btn--quiet lprof__switch', dataset: { profile: profile.id } }),
    ])));

    const newName = el('input', {
      className: 'input', attrs: { id: 'lprof-new', type: 'text', maxlength: '40', autocomplete: 'off' },
    });
    const create = el('form', {
      className: 'lprof__form',
      on: {
        submit: async (event) => {
          event.preventDefault();
          try {
            data = await api.createLocalProfile(newName.value);
            status.textContent = t('profiles.created', { name: data.created.label });
            draw();
          } catch (error) { fail(error); }
        },
      },
    }, [
      el('label', { attrs: { for: 'lprof-new' }, text: t('profiles.newLabel') }),
      newName,
      el('button', { className: 'btn', attrs: { type: 'submit' }, text: t('profiles.create') }),
      el('p', { className: 'field__hint', text: t('profiles.newHint') }),
    ]);

    const renameBox = el('input', {
      className: 'input',
      attrs: { id: 'lprof-rename', type: 'text', maxlength: '40', autocomplete: 'off' },
      props: { value: active.label },
    });
    const rename = el('form', {
      className: 'lprof__form',
      on: {
        submit: async (event) => {
          event.preventDefault();
          try {
            data = await api.renameLocalProfile(active.id, renameBox.value);
            status.textContent = t('profiles.renamed');
            draw();
          } catch (error) { fail(error); }
        },
      },
    }, [
      el('label', { attrs: { for: 'lprof-rename' }, text: t('profiles.renameLabel') }),
      renameBox,
      el('button', { className: 'btn', attrs: { type: 'submit' }, text: t('profiles.rename') }),
    ]);

    const deletable = others.filter((p) => !p.original);
    let remove = null;
    if (deletable.length) {
      const which = el('select', { className: 'input', attrs: { id: 'lprof-delete-which' } },
        deletable.map((p) => el('option', { attrs: { value: p.id }, text: p.label })));
      const typed = el('input', {
        className: 'input', attrs: { id: 'lprof-delete-confirm', type: 'text', autocomplete: 'off' },
      });
      remove = el('form', {
        className: 'lprof__form lprof__danger',
        on: {
          submit: async (event) => {
            event.preventDefault();
            try {
              data = await api.deleteLocalProfile(which.value, typed.value);
              status.textContent = t('profiles.deleted');
              draw();
            } catch (error) { fail(error); }
          },
        },
      }, [
        el('label', { attrs: { for: 'lprof-delete-which' }, text: t('profiles.deleteWhich') }),
        which,
        el('label', { attrs: { for: 'lprof-delete-confirm' }, text: t('profiles.deleteConfirm') }),
        typed,
        el('p', { className: 'field__hint', text: t('profiles.deleteHint') }),
        el('button', { className: 'btn', attrs: { type: 'submit' }, text: t('profiles.delete') }),
      ]);
    }

    replace(host, [
      el('details', { className: 'lprof' }, [
        el('summary', {
          className: 'lprof__summary',
          attrs: { 'aria-label': t('profiles.activeAria', { name: active.label }) },
        }, [
          el('span', {
            className: `lprof__dot lprof__dot--${active.color || 'teal'}`,
            attrs: { 'aria-hidden': 'true' },
          }),
          el('span', { className: 'lprof__kicker', text: t('profiles.kicker') }),
          el('span', { className: 'lprof__name', text: active.label }),
        ]),
        el('div', { className: 'lprof__panel' }, [
          el('p', { className: 'field__hint', text: t('profiles.explain') }),
          others.length ? el('p', { className: 'lprof__label', text: t('profiles.others') }) : null,
          others.length ? switchList : null,
          create,
          rename,
          remove,
          status,
        ].filter(Boolean)),
      ]),
    ]);
  }

  load();
  return { retranslate: draw, reload: load };
}
