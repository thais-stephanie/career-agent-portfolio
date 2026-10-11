/**
 * switch.js -- the one on/off control (role=switch): the label, what it does,
 * and the track. Used by the Deep reading panel and by the automatic update,
 * so there is one implementation of it.
 */

import { el } from './dom.js';

export function switchControl(on, label, hint, onToggle) {
  const node = el('button', {
    className: 'dr-switch', attrs: { type: 'button', role: 'switch', 'aria-checked': on ? 'true' : 'false' },
    on: { click: () => onToggle(node) },
  }, [
    el('span', { className: 'dr-switch__text' }, [
      el('span', { className: 'dr-switch__label', text: label }),
      el('span', { className: 'dr-switch__hint', text: hint }),
    ]),
    el('span', { className: 'dr-switch__track', attrs: { 'aria-hidden': 'true' } }, [
      el('span', { className: 'dr-switch__knob' }),
    ]),
  ]);
  return node;
}
