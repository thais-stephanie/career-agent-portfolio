/**
 * edit-notice.js -- one short line after a save, with the one action that fits.
 *
 * The server says what an edit really changed (`effects`, web/edit_effects.py).
 * This turns it into a single transient notice. It never starts anything: the
 * action is a button the person presses. The four things a person may want
 * after an edit are four different verbs and are never mixed:
 *
 *   recalculate  the local match % (matcher preferences moved)
 *   search       look for new jobs (the words or markets of a search moved)
 *   read again   the Deep reading (skills, history or evidence it read moved)
 *   reload       a screen (a different matter, and not offered here)
 */

import { button } from './dom.js';
import { toast } from './ui.js';
import { t } from './i18n.js';

/** The notice for a response that carried `effects`, or null for none. */
export function noticeFor(effects) {
  if (!effects) return null;
  const parts = [];
  let action = null;
  if (effects.needs_rescore) {
    parts.push(t('effects.rescore'));
    action = action || 'rescore';
  }
  if (effects.deep_stale > 0) {
    parts.push(t(effects.deep_stale === 1 ? 'effects.deepOne' : 'effects.deepMany', { n: effects.deep_stale }));
    action = action || 'deep';
  }
  if (effects.offer_discovery) {
    parts.push(t('effects.search'));
    action = action || 'search';
  }
  if (!parts.length) return null;
  return { text: `${t('effects.saved')} ${parts.join(' ')}`, action };
}

/**
 * Wire the notice to the page. `actions` are the existing starters:
 * `{ rescore, search, deep }`, each a function. One listener, so a page that
 * saves does not need to know about any of this.
 */
export function watchEdits(actions) {
  window.addEventListener('ca:effects', (event) => {
    const notice = noticeFor(event.detail);
    if (!notice) return;
    const labels = { rescore: 'effects.doRescore', search: 'effects.doSearch', deep: 'effects.doDeep' };
    const extras = notice.action && actions[notice.action]
      ? [button(t(labels[notice.action]), () => actions[notice.action](), { className: 'toast__undo' })]
      : null;
    toast(notice.text, { extras, timeout: 12000 });
  });
}
