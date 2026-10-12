/**
 * age_control.js -- "Published in the last": how old a posting may be and still
 * appear in the search. One choice per profile, kept on the server.
 *
 * It is not a filter chip. The limit hides postings by the date their SOURCE
 * published them (never the date this computer collected them), changes no
 * match %, and is lifted from here or from the notice above the list. Postings
 * with no publication date are hidden unless "include postings without a date"
 * is on.
 */

import { el, button } from './dom.js';
import { t } from './i18n.js';
import * as api from './api.js';

const CHOICES = [120, 150, null];

export function createAgeControl() {
  let view = { max_age_days: 120, include_undated: false };
  const chips = el('div', { className: 'fchips', attrs: { role: 'group', 'aria-label': t('age.title') } });
  const nodes = CHOICES.map((days) => {
    const label = days === null ? t('age.none') : t('age.days', { n: days });
    const node = button(label, () => change({ max_age_days: days }), {
      className: 'fchip', attrs: { 'aria-pressed': 'false', 'data-age': String(days) },
    });
    chips.append(node);
    return node;
  });
  const undated = button('', () => change({ include_undated: !view.include_undated }), {
    className: 'fswitch', attrs: { role: 'switch', 'aria-checked': 'false', id: 'age-undated' },
  });
  undated.append(
    el('span', { className: 'fswitch__text', text: t('age.undated') }),
    el('span', { className: 'fswitch__track', attrs: { 'aria-hidden': 'true' } }, [
      el('span', { className: 'fswitch__knob' }),
    ]),
  );
  const note = el('p', { className: 'fess__hint', text: t('age.help') });
  const root = el('div', { className: 'fess__age' }, [chips, undated, note]);

  let turn = 0;
  // One request at a time, in the order the choices were made: two in flight
  // could be handled by the server in the opposite order.
  let queue = Promise.resolve();
  async function change(patch) {
    const before = view;
    const mine = ++turn;
    view = { ...view, ...patch };
    paint();
    const previous = queue;
    let release = () => {};
    queue = new Promise((resolve) => { release = resolve; });
    await previous;
    try {
      const saved = await api.setViewPrefs(patch);
      // Only the newest choice may repaint: an older answer arriving late
      // would put back a limit the person has already changed.
      if (mine === turn) {
        view = saved;
        paint();
      }
      // The list is read again; the stored scores are untouched.
      window.dispatchEvent(new CustomEvent('ca:view-prefs'));
    } catch {
      if (mine === turn) {
        view = before;
        paint();
      }
    } finally {
      release();
    }
  }

  function paint() {
    CHOICES.forEach((days, index) => {
      nodes[index].setAttribute('aria-pressed', String(view.max_age_days === days));
    });
    undated.setAttribute('aria-checked', String(Boolean(view.include_undated)));
    undated.disabled = view.max_age_days === null;
  }

  paint();
  return {
    root,
    sync(next) {
      if (!next) return;
      view = { max_age_days: next.max_age_days ?? null, include_undated: Boolean(next.include_undated) };
      paint();
    },
    relabel() {
      chips.setAttribute('aria-label', t('age.title'));
      nodes.forEach((node, index) => {
        node.textContent = CHOICES[index] === null ? t('age.none') : t('age.days', { n: CHOICES[index] });
      });
      undated.querySelector('.fswitch__text').textContent = t('age.undated');
      note.textContent = t('age.help');
    },
  };
}
