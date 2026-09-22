/**
 * help.js -- an explanation where the word is, and only when it is asked for.
 *
 * WHAT THIS REPLACED
 * ------------------
 * A permanent block under the toolbar headed "What Match and Posting detail
 * mean", open on every load of the job list. Two problems with it, and the
 * second is the one that matters:
 *
 *   1. It cost a fifth of the first screen to a sentence most readers had
 *      already read.
 *   2. It was nowhere near either number. Somebody puzzled by "67%" on a card
 *      had to find a paragraph at the top of the page and work out which half
 *      of it was about the thing they were looking at.
 *
 * So: a `?` beside the word itself, and the explanation appears under it.
 *
 * WHY NOT A TOOLTIP
 * -----------------
 * `title` is still there and still useful with a mouse. It is not an
 * explanation: it never appears on a touch screen, it cannot be reached by
 * keyboard in most browsers, and it vanishes while you are reading it. A
 * control that only works for one kind of reader is not the whole answer.
 *
 * WHY NOT A DIALOG
 * ----------------
 * The reader is comparing this explanation with the number beside it. A modal
 * covers the number.
 */

import { el, button } from './dom.js';
import { t } from './i18n.js';

/**
 * A `?` and the paragraph it reveals, as one element.
 *
 * `keys` is a list of catalogue keys, in reading order. Several rather than
 * one because Match and Posting detail are explained together or not at all:
 * the whole point of ADR-0004 is that they are separate measurements, and
 * separating the explanations is how a reader comes to think one qualifies
 * the other.
 */
export function helpNote(label, keys) {
  const body = el('div', { className: 'help__body', attrs: { hidden: 'hidden' } },
    keys.map((key) => el('p', { className: 'help__p', text: t(key) })));

  const toggle = button('?', () => {
    const open = body.hasAttribute('hidden');
    if (open) body.removeAttribute('hidden');
    else body.setAttribute('hidden', 'hidden');
    toggle.setAttribute('aria-expanded', String(open));
  }, {
    className: 'help__toggle',
    // Named after what it explains, never "help". A screen reader announcing
    // "button, question mark" beside four other question marks tells nobody
    // which one they are on.
    ariaLabel: t('help.about', { what: label }),
  });
  toggle.setAttribute('aria-expanded', 'false');
  toggle.dataset.stopsOpen = 'true';

  return el('span', { className: 'help' }, [toggle, body]);
}
