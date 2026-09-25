/**
 * tags.js -- an OPEN list of short values, entered one at a time.
 *
 * WHY NOT A DROPDOWN
 * ------------------
 * A dropdown is a closed vocabulary, and it is the right control exactly when
 * the vocabulary is closed: three work models, five levels, three engagements.
 * It is the wrong one for a list whose members this program has no opinion
 * about. There are about two hundred countries and this product refuses to
 * hold a list of which ones are real -- having one would be this file having
 * an opinion about somebody's passport.
 *
 * WHY NOT A COMMA-SEPARATED LINE
 * ------------------------------
 * That is what this replaced, and it works right up until somebody looks at
 * it. `BR, PT, AR` in a text box gives no feedback that three values were
 * understood as three, no way to remove the middle one without re-typing the
 * line, and no signal at all when a stray character makes one of them
 * something the server will refuse.
 *
 * A pill is the feedback. Enter or a comma commits one, X removes one, and
 * what is on screen is exactly what will be sent.
 *
 * AN OPEN TAG IS NOT A SCORING WEIGHT
 * -----------------------------------
 * The most important property, and the reason this file says so twice. A
 * value typed here goes to ONE named field through the whitelist in
 * `config/candidate_writer.py`. It does not become a lexicon phrase, it earns
 * no points, and it moves no score. Somewhere else in this product there IS a
 * control that creates weights -- the phrase editor under Search preferences,
 * which says so on its own screen -- and the two must never be confused,
 * because a control that silently taught the matcher a new signal would be
 * the product changing how it reads every posting because somebody typed a
 * word into a box.
 */

import { el, button, clear } from './dom.js';
import { t } from './i18n.js';

/**
 * A tag input over a list of strings.
 *
 * `normalise` runs on every committed value; `onChange` receives the whole
 * list. Neither is optional: a control that guesses at either would be making
 * a decision about somebody's data on its own.
 */
export function tagInput({
  id, label, values, placeholder, normalise, onChange, hint = '',
  // How a stored value is SHOWN. `BR` is what belongs in the file and
  // `Brazil` is what belongs on a chip, and the two were the same string
  // until countries got their names.
  display = (value) => value,
  // `characters` is right for a two-letter country code and wrong for a
  // phrase: it puts a mobile keyboard in caps lock for text somebody is
  // writing in their own words. The country list keeps the default; the
  // phrase editor passes `off`.
  autocapitalize = 'characters',
  // Optional. `max` refuses to commit past that many values and calls
  // `onFull` so the caller can say why; `same` decides when two values are
  // the same one (exact by default).
  max = Infinity,
  onFull = null,
  same = (a, b) => a === b,
}) {
  let current = [...values];

  const pills = el('div', { className: 'tags__pills' });

  function commit(raw) {
    // Commas as well as Enter, because a person pasting `BR, PT` has already
    // told us where the boundaries are and refusing to see them would be the
    // control being pedantic about its own conventions.
    const parts = String(raw)
      .split(',')
      .map((part) => normalise(part.trim()))
      .filter(Boolean);
    if (!parts.length) return;
    const next = [...current];
    let full = false;
    for (const part of parts) {
      if (next.some((item) => same(item, part))) continue;
      if (next.length >= max) {
        full = true;
        continue;
      }
      next.push(part);
    }
    if (full && onFull) onFull();
    if (next.length === current.length) return;
    current = next;
    draw();
    onChange([...current]);
  }

  function remove(value) {
    current = current.filter((item) => item !== value);
    draw();
    onChange([...current]);
  }

  const box = el('input', {
    className: 'input tags__input',
    attrs: {
      type: 'text',
      id,
      autocomplete: 'off',
      autocapitalize,
      spellcheck: 'false',
      placeholder: placeholder || '',
    },
    on: {
      keydown: (event) => {
        if (event.key !== 'Enter' && event.key !== ',') return;
        // Enter inside a form submits it, and a comma would otherwise land in
        // the box after the pill it created.
        event.preventDefault();
        commit(event.target.value);
        event.target.value = '';
      },
      // Leaving the box commits what is in it. Typing a value and clicking
      // Save is what a person does, and losing it would be the control
      // punishing them for not pressing Enter.
      blur: (event) => {
        commit(event.target.value);
        event.target.value = '';
      },
    },
  });

  function draw() {
    clear(pills);
    for (const value of current) {
      pills.appendChild(el('span', { className: 'chip chip--tag' }, [
        el('span', { className: 'chip__text', text: display(value) }),
        button('×', () => remove(value), {
          className: 'chip__x',
          ariaLabel: t('tags.remove', { value: display(value) }),
        }),
      ]));
    }
  }

  draw();

  const root = el('div', { className: 'tags' }, [
    el('label', { className: 'field__label', text: label, attrs: { for: id } }),
    box,
    pills,
    // Said on the control itself, not only in a docstring. This is the
    // sentence that stops an open list from being mistaken for a way to
    // teach the matcher a new word.
    el('p', { className: 'field__hint', text: hint || t('tags.noWeight') }),
  ]);

  return {
    root,
    get value() {
      return [...current];
    },
  };
}
