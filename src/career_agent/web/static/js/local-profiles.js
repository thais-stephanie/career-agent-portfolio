/**
 * local-profiles.js -- which person's Career Agent this is, and changing it.
 *
 * LOCAL PROFILES, NOT ACCOUNTS. There is no sign-in and no password: a
 * profile keeps one person's CV, evidence, preferences, scores and
 * applications apart from another's in this app on this computer. The menu
 * says so in two lines, because anybody using the same computer account can
 * still open every profile.
 *
 * THE RAIL STAYS SHORT. The side rail shows one compact button: whose
 * profile this is. It opens a small MENU that floats over the navigation (it
 * never pushes it down): every profile on this computer, the open one first
 * in its place, one click to switch to another. Renaming and creating are
 * revealed only on demand and never at the same time. Deleting a profile is
 * rarer and permanent enough to live in Settings & Sources instead, behind a
 * typed confirmation (`mountSettings`).
 *
 * Switching reloads the page on Home, so nothing of the previous profile
 * stays in memory on any screen, and other tabs of this app reload too.
 */

import { el, button, replace } from './dom.js';
import { t, getLocale } from './i18n.js';
import * as api from './api.js';

const CHANNEL = 'career-agent-local-profile';

/** The initials on a profile's tile: up to two, from its name's words. */
export function initials(label) {
  const words = String(label || '').split(/\s+/).filter((word) => /[\p{L}\p{N}]/u.test(word));
  const letters = words.slice(0, 2).map((word) => word.match(/[\p{L}\p{N}]/u)[0].toLocaleUpperCase());
  return letters.join('') || '?';
}

export function createLocalProfiles(host) {
  let data = null;
  //: The menu, while it is open; null otherwise.
  let menu = null;
  //: Which panel the menu shows: null, 'rename' or 'create'. Never both.
  let panel = null;
  let settingsHost = null;

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
    drawSettings();
  }

  function tile(profile, size = '') {
    return el('span', {
      className: `lprof__tile lprof__tile--${profile.color || 'teal'}${size ? ` lprof__tile--${size}` : ''}`,
      attrs: { 'aria-hidden': 'true' },
      text: initials(profile.label),
    });
  }

  // ---------------------------------------------------------------- the rail
  function draw() {
    if (!host) return;
    if (!data || !data.enabled || !data.active) {
      closeMenu({ restore: false });
      replace(host, []);
      host.hidden = true;
      return;
    }
    host.hidden = false;
    const active = data.active;
    const open = Boolean(menu);
    replace(host, [
      el('p', { className: 'lprof__eyebrow', attrs: { id: 'lprof-eyebrow' }, text: t('profiles.kicker') }),
      el('button', {
        className: `lprof__trigger${open ? ' is-open' : ''}`,
        attrs: {
          type: 'button',
          id: 'lprof-trigger',
          'aria-haspopup': 'dialog',
          'aria-expanded': String(open),
          'aria-controls': 'lprof-menu',
          'aria-label': t('profiles.activeAria', { name: active.label }),
        },
        on: { click: () => (menu ? closeMenu() : openMenu()) },
      }, [
        tile(active),
        el('span', { className: 'lprof__name', text: active.label }),
        el('span', { className: 'lprof__caret', attrs: { 'aria-hidden': 'true' }, text: open ? '▴' : '▾' }),
      ]),
    ]);
  }

  // ---------------------------------------------------------------- the menu
  function trigger() {
    return document.getElementById('lprof-trigger');
  }

  /**
   * The menu is attached to the page, not to the rail: the rail scrolls on
   * its own and would clip anything that overhangs its edge. It is placed
   * under the button, fixed, and follows it on resize and scroll.
   */
  function place() {
    if (!menu) return;
    const anchor = trigger();
    if (!anchor) return;
    const rect = anchor.getBoundingClientRect();
    const width = Math.min(252, window.innerWidth - 16);
    const left = Math.max(8, Math.min(rect.left - 2, window.innerWidth - width - 8));
    menu.style.width = `${width}px`;
    menu.style.left = `${left}px`;
    menu.style.top = `${rect.bottom + 4}px`;
    menu.style.maxHeight = `${Math.max(160, window.innerHeight - rect.bottom - 16)}px`;
  }

  function openMenu() {
    if (menu || !data || !data.enabled) return;
    panel = null;
    menu = el('div', {
      className: 'lprof__menu',
      attrs: {
        id: 'lprof-menu',
        role: 'dialog',
        'aria-modal': 'false',
        'aria-label': t('profiles.menuLabel'),
      },
    });
    document.body.appendChild(menu);
    renderMenu();
    draw();
    place();
    window.addEventListener('resize', place);
    window.addEventListener('scroll', place, true);
    document.addEventListener('pointerdown', onOutside, true);
    document.addEventListener('focusin', onFocusMove, true);
    document.addEventListener('keydown', onKey, true);
    const first = menu.querySelector('.lprof__edit, .lprof__switch, button');
    if (first) first.focus();
  }

  function closeMenu({ restore = true } = {}) {
    if (!menu) return;
    window.removeEventListener('resize', place);
    window.removeEventListener('scroll', place, true);
    document.removeEventListener('pointerdown', onOutside, true);
    document.removeEventListener('focusin', onFocusMove, true);
    document.removeEventListener('keydown', onKey, true);
    menu.remove();
    menu = null;
    panel = null;
    draw();
    if (restore) {
      const back = trigger();
      if (back) back.focus();
    }
  }

  function inside(target) {
    const anchor = trigger();
    return Boolean(menu && (menu.contains(target) || (anchor && anchor.contains(target))));
  }

  function onOutside(event) {
    if (menu && !inside(event.target)) closeMenu({ restore: false });
  }

  function onFocusMove(event) {
    if (menu && !inside(event.target)) closeMenu({ restore: false });
  }

  function focusable() {
    return [...menu.querySelectorAll('button, input, select, a[href]')].filter((node) => !node.disabled);
  }

  /**
   * The keyboard, while the menu is open, wherever focus is. Esc inside a
   * panel cancels the panel; Esc anywhere else (the menu or its button)
   * closes the menu and gives focus back to the button. Tab moves from the
   * button into the menu and cycles inside it; Shift+Tab from its first item
   * goes back to the button. The menu is attached to the page rather than to
   * the rail, so without this Tab would wander into whatever follows it.
   */
  function onKey(event) {
    if (!menu) return;
    if (event.key === 'Escape') {
      event.preventDefault();
      event.stopPropagation();
      if (panel && menu.contains(document.activeElement)) togglePanel(null);
      else closeMenu();
      return;
    }
    if (event.key !== 'Tab') return;
    const items = focusable();
    if (!items.length) return;
    const active = document.activeElement;
    if (active === trigger() && !event.shiftKey) {
      event.preventDefault();
      items[0].focus();
    } else if (active === items[0] && event.shiftKey) {
      event.preventDefault();
      closeMenu();
    } else if (active === items[items.length - 1] && !event.shiftKey) {
      event.preventDefault();
      items[0].focus();
    }
  }

  function status() {
    return menu ? menu.querySelector('.lprof__status') : null;
  }

  function fail(error) {
    const line = status();
    if (line) line.textContent = error.userMessage || error.message;
  }

  function createdOn(profile) {
    const when = profile.created_at ? new Date(profile.created_at) : null;
    if (!when || Number.isNaN(when.getTime())) return '';
    const date = when.toLocaleDateString(getLocale(), { day: 'numeric', month: 'short', year: 'numeric' });
    return t('profiles.createdOn', { date });
  }

  async function switchTo(profile, target) {
    if (target) target.disabled = true;
    const line = status();
    if (line) line.textContent = t('profiles.switching');
    try {
      await api.switchLocalProfile(profile.id);
      if (channel) channel.postMessage('switched');
      // Land on Home, whatever page this was.
      window.location.assign(window.location.pathname);
    } catch (error) {
      fail(error);
      if (target) target.disabled = false;
    }
  }

  function row(profile) {
    const current = Boolean(profile.active);
    return el('li', {
      className: `lprof__row${current ? ' is-current' : ''}`,
      dataset: { profile: profile.id },
      attrs: current ? { 'aria-current': 'true' } : {},
    }, [
      tile(profile, 'small'),
      el('span', { className: 'lprof__rowtext' }, [
        el('span', { className: 'lprof__rowname', text: profile.label }),
        el('span', { className: 'lprof__meta', text: current ? t('profiles.openNow') : createdOn(profile) }),
      ]),
      current
        ? el('button', {
          className: `lprof__edit${panel === 'rename' ? ' is-open' : ''}`,
          attrs: {
            type: 'button',
            id: 'lprof-action-rename',
            title: t('profiles.renameThis'),
            'aria-label': t('profiles.renameThis'),
            'aria-expanded': String(panel === 'rename'),
            'aria-controls': 'lprof-panel',
          },
          on: { click: () => togglePanel('rename') },
          text: '✎︎',
        })
        : el('button', {
          className: 'lprof__switch',
          attrs: { type: 'button', 'aria-label': t('profiles.switchTo', { name: profile.label }) },
          on: { click: (event) => switchTo(profile, event.currentTarget) },
          text: t('profiles.switch'),
        }),
    ]);
  }

  function renamePanel() {
    const box = el('input', {
      className: 'lprof__input',
      attrs: { id: 'lprof-rename', type: 'text', maxlength: '40', autocomplete: 'off' },
      props: { value: data.active.label },
    });
    const save = async () => {
      const name = box.value.trim();
      if (!name || name === data.active.label) {
        togglePanel(null);
        return;
      }
      try {
        data = await api.renameLocalProfile(data.active.id, name);
        draw();
        togglePanel(null);
        const line = status();
        if (line) line.textContent = t('profiles.renamed');
      } catch (error) {
        fail(error);
      }
    };
    box.addEventListener('keydown', (event) => {
      if (event.key === 'Enter') {
        event.preventDefault();
        save();
      }
    });
    return el('div', { className: 'lprof__panel lprof__panel--rename', attrs: { id: 'lprof-panel' } }, [
      el('label', { className: 'lprof__panellabel', attrs: { for: 'lprof-rename' }, text: t('profiles.renameThis') }),
      box,
      el('div', { className: 'lprof__panelactions' }, [
        button(t('profiles.cancel'), () => togglePanel(null), { className: 'lprof__btn' }),
        button(t('profiles.save'), save, { className: 'lprof__btn lprof__btn--primary', attrs: { id: 'lprof-save' } }),
      ]),
    ]);
  }

  function createPanel() {
    const box = el('input', {
      className: 'lprof__input',
      attrs: {
        id: 'lprof-new',
        type: 'text',
        maxlength: '40',
        autocomplete: 'off',
        placeholder: t('profiles.newPlaceholder'),
        'aria-describedby': 'lprof-new-hint',
      },
    });
    const create = button(t('profiles.create'), () => submit(), {
      className: 'lprof__btn lprof__btn--primary',
      attrs: { id: 'lprof-create' },
    });
    const ready = () => box.value.trim().length > 0;
    const sync = () => { create.disabled = !ready(); };
    async function submit() {
      if (!ready()) return;
      create.disabled = true;
      try {
        const made = await api.createLocalProfile(box.value.trim());
        // A new profile is where its creator wants to be: open it.
        await switchTo(made.created, null);
      } catch (error) {
        fail(error);
        sync();
      }
    }
    box.addEventListener('input', sync);
    box.addEventListener('keydown', (event) => {
      if (event.key === 'Enter') {
        event.preventDefault();
        submit();
      }
    });
    sync();
    return el('div', { className: 'lprof__panel lprof__panel--create', attrs: { id: 'lprof-panel' } }, [
      el('label', { className: 'lprof__panellabel', attrs: { for: 'lprof-new' }, text: t('profiles.newLabel') }),
      box,
      el('p', { className: 'lprof__hint', attrs: { id: 'lprof-new-hint' }, text: t('profiles.newHint') }),
      el('div', { className: 'lprof__panelactions' }, [
        button(t('profiles.cancel'), () => togglePanel(null), { className: 'lprof__btn' }),
        create,
      ]),
    ]);
  }

  function togglePanel(key) {
    const previous = panel;
    panel = panel === key ? null : key;
    renderMenu();
    if (panel === 'rename') {
      const box = menu.querySelector('#lprof-rename');
      if (box) {
        box.focus();
        box.select();
      }
    } else if (panel === 'create') {
      const box = menu.querySelector('#lprof-new');
      if (box) box.focus();
    } else if (previous) {
      // Focus goes back to what opened the panel, never out of the menu.
      const back = menu.querySelector(previous === 'rename' ? '#lprof-action-rename' : '#lprof-action-create');
      if (back) back.focus();
    }
  }

  function renderMenu() {
    if (!menu) return;
    replace(menu, [
      el('p', { className: 'lprof__heading', text: t('profiles.onThisComputer') }),
      el('ul', { className: 'lprof__list', attrs: { 'aria-label': t('profiles.listLabel') } },
        data.profiles.map(row)),
      panel === 'rename' ? renamePanel() : null,
      panel === 'create'
        ? createPanel()
        : el('div', { className: 'lprof__newrow' }, [
          el('button', {
            className: 'lprof__new',
            attrs: {
              type: 'button',
              id: 'lprof-action-create',
              'aria-expanded': 'false',
              'aria-controls': 'lprof-panel',
            },
            on: { click: () => togglePanel('create') },
          }, [
            el('span', { className: 'lprof__plus', attrs: { 'aria-hidden': 'true' }, text: '+' }),
            el('span', { text: t('profiles.newAction') }),
          ]),
        ]),
      el('p', { className: 'lprof__status', attrs: { role: 'status', 'aria-live': 'polite' } }),
      el('div', { className: 'lprof__note' }, [
        el('img', {
          className: 'lprof__noteicon',
          attrs: { src: './px/px-noentry-16.png', alt: '', width: '12', height: '12' },
        }),
        el('p', { text: t('profiles.privacyShort') }),
      ]),
    ].filter(Boolean));
    place();
  }

  // ------------------------------------------------ deleting, in Settings
  /**
   * Deleting is permanent enough to be kept out of the menu: it lives in
   * Settings & Sources, lists only profiles that may be deleted (never the
   * open one, never the installation's original) and asks for the name to be
   * typed. The folder is moved to the trash, not erased.
   */
  function drawSettings() {
    if (!settingsHost) return;
    const block = settingsHost.closest('section');
    const enabled = Boolean(data && data.enabled && data.active);
    if (block) block.hidden = !enabled;
    if (!enabled) {
      replace(settingsHost, []);
      return;
    }
    const deletable = data.profiles.filter((p) => !p.active && !p.original);
    const note = el('p', { className: 'field__hint', text: t('profiles.explain') });
    if (!deletable.length) {
      replace(settingsHost, [note, el('p', { className: 'field__hint', text: t('profiles.nothingToDelete') })]);
      return;
    }
    const which = el('select', { className: 'input', attrs: { id: 'lprof-delete-which' } },
      deletable.map((p) => el('option', { attrs: { value: p.id }, text: p.label })));
    const typed = el('input', {
      className: 'input',
      attrs: {
        id: 'lprof-delete-confirm',
        type: 'text',
        autocomplete: 'off',
        'aria-describedby': 'lprof-delete-hint',
      },
    });
    const line = el('p', { className: 'lprof__settingsstatus', attrs: { role: 'status', 'aria-live': 'polite' } });
    const form = el('form', {
      className: 'lprof__delete',
      on: {
        submit: async (event) => {
          event.preventDefault();
          try {
            data = await api.deleteLocalProfile(which.value, typed.value);
            draw();
            drawSettings();
            const after = settingsHost.querySelector('.lprof__settingsstatus');
            if (after) after.textContent = t('profiles.deleted');
          } catch (error) {
            line.textContent = error.userMessage || error.message;
          }
        },
      },
    }, [
      el('div', { className: 'field' }, [
        el('label', {
          className: 'field__label', attrs: { for: 'lprof-delete-which' }, text: t('profiles.deleteWhich'),
        }),
        which,
      ]),
      el('div', { className: 'field' }, [
        el('label', {
          className: 'field__label', attrs: { for: 'lprof-delete-confirm' }, text: t('profiles.deleteConfirm'),
        }),
        typed,
        el('p', { className: 'field__hint', attrs: { id: 'lprof-delete-hint' }, text: t('profiles.deleteHint') }),
      ]),
      el('button', { className: 'btn lprof__danger', attrs: { type: 'submit' }, text: t('profiles.delete') }),
      line,
    ]);
    replace(settingsHost, [note, form]);
  }

  function mountSettings(node) {
    settingsHost = node;
    drawSettings();
  }

  function retranslate() {
    draw();
    if (menu) {
      // Whatever was typed in a panel survives the language changing.
      const typed = menu.querySelector('#lprof-rename, #lprof-new');
      const kept = typed ? { id: typed.id, value: typed.value } : null;
      renderMenu();
      if (kept) {
        const box = menu.querySelector(`#${kept.id}`);
        if (box) {
          box.value = kept.value;
          box.dispatchEvent(new Event('input', { bubbles: true }));
        }
      }
    }
    drawSettings();
  }

  load();
  return { retranslate, reload: load, mountSettings };
}
