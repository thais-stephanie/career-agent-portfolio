/** Ordinary preferences reuse Discover's persistent phrase lists, not scoring vocabulary. */
import { el, button, replace } from './dom.js';
import { tagInput } from './tags.js';
import { t } from './i18n.js';
import { getProfile, getSearchFitReadiness } from './api.js';
import { editableBlock } from './profile.js';
import { roleAnchorsEditor } from './roles.js';

export function renderSearchSettings(host, store) {
  const controls = el('details', {}, [el('summary', { text: t('settings.targets') })]);
  let loaded = false;
  controls.addEventListener('toggle', async () => {
    if (!controls.open || loaded) return;
    loaded = true;
    try {
      const data = await getProfile();
      controls.append(editableBlock(data.editable || [], data.source, data.place_names, 'settings-field-'));
    } catch (error) {
      loaded = false; controls.append(el('p', { text: error.userMessage || error.message }));
    }
  });
  const readiness = el('p', { className: 'settings__readiness', attrs: { role: 'status' } });
  getSearchFitReadiness().then((result) => {
    const missing = (result.missing || []).map((code) => t(`readiness.missing.${code}`));
    readiness.textContent = t(`readiness.${result.state}`, { missing: missing.join(t('readiness.join')) });
    readiness.dataset.state = result.state;
  }).catch(() => {});
  const roles = el('details', { className: 'settings__roles' }, [el('summary', { text: t('roles.settingsTitle') })]);
  let rolesLoaded = false;
  roles.addEventListener('toggle', () => {
    if (!roles.open || rolesLoaded) return;
    rolesLoaded = true;
    roles.append(roleAnchorsEditor({ id: 'settings-roles', autosave: true }).root);
  });
  replace(host, [readiness, el('p', { text: t('settings.searchHelp') }), controls, roles,
    ...['prefer_keyword', 'avoid_keyword', 'exclude_keyword'].map(key => {
      const row = el('div', { className: 'settings__phrases', dataset: { preference: key } });
      function draw() {
        replace(row, [tagInput({
          id: `settings-${key}`, label: t(`settings.${key}`), values: store.get()[key],
          hint: t(`settings.${key}Help`), placeholder: t('settings.addPhrase'),
          normalise: value => value.trim().replace(/\s+/g, ' ').toLowerCase(),
          autocapitalize: 'off', onChange: values => store.set({ [key]: values }),
        }).root, button(t('settings.clearPhrases'), () => { store.set({ [key]: [] }); draw(); })]);
      }
      draw(); return row;
    }), el('p', { text: t('settings.localPreference') }),
  ]);
}
