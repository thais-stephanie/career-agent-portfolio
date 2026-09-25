/**
 * The colour theme: chosen, remembered, and applied before the page paints.
 *
 * WHY THIS IS A CLASSIC SCRIPT AND NOT A MODULE
 * ---------------------------------------------
 * A module is deferred by definition. It runs after the document has parsed,
 * which means the page paints in whatever theme the stylesheet defaulted to and
 * then jumps. That flash is the one thing a theme control must not do.
 *
 * The usual fix is an inline script in the head. This server sends
 * `script-src 'self'` with no `unsafe-inline`, and that CSP is load-bearing:
 * job descriptions are third-party text, and a policy that forbids inline
 * script means a mistake in rendering them cannot become an exfiltration. So
 * the head gets a same-origin classic script instead, which blocks parsing for
 * the few milliseconds it takes to read one string and set one attribute.
 *
 * It has no imports and no exports, so it parses as a module and the frontend
 * gate reads it like any other file.
 *
 * TWO CONTROLS, THREE STATES, AND THAT IS NOT A CONTRADICTION
 * -----------------------------------------------------------
 * The control offers `light` and `dark`, which is what the design defines and
 * what the owner asked for. There is no third button.
 *
 * The third STATE is having chosen neither, and it is not a button because it
 * is not a choice -- it is the absence of one. Until somebody picks a side,
 * the page follows `prefers-color-scheme` and keeps following it when the
 * machine changes at sunset. The moment either button is pressed the choice is
 * stored and the machine stops being consulted, which is what pressing a
 * button is for.
 *
 * An earlier version made that state a visible "Auto" segment. It was removed:
 * three segments could not fit the 220px rail in Portuguese, where the label
 * reads "Automatico" and was clipped, and a control whose job is to say "I am
 * not deciding" earns less room than the two that decide.
 *
 * What is STORED is the choice, and nothing is stored until one is made. What
 * is APPLIED is the resolved value, written to `data-theme` on the root
 * element. The stylesheet also carries a `prefers-color-scheme` block for the
 * case where this script never ran, and that block is guarded against an
 * explicit `light` so a person who chose light on a dark machine still gets
 * light.
 */

(function () {
  'use strict';

  const KEY = 'careerAgent.theme.v1';
  const CHOICES = ['light', 'dark'];

  const root = document.documentElement;

  /**
   * The stored choice, or `null` when there is not one.
   *
   * `null` is the honest return and the reason there is no `DEFAULT`: a
   * default would be this file picking a side on somebody's behalf and then
   * being wrong for half of all readers. Absence means "nobody has said", and
   * the machine is asked instead.
   *
   * A value written by the earlier three-state version reads back as `system`,
   * which is not in `CHOICES` and therefore resolves to `null` -- so an owner
   * who had chosen Auto lands on exactly the behaviour Auto gave her, with no
   * migration and no stale key to clear.
   *
   * Every read is wrapped: `localStorage` throws rather than returning null in
   * a private window and in browsers configured to block site data, and a theme
   * preference is not worth a blank page.
   */
  function storedChoice() {
    try {
      const value = window.localStorage.getItem(KEY);
      return CHOICES.indexOf(value) >= 0 ? value : null;
    } catch (error) {
      return null;
    }
  }

  function remember(choice) {
    try {
      window.localStorage.setItem(KEY, choice);
    } catch (error) {
      /* A theme that does not persist is a smaller problem than a page that
         does not load. The choice still applies for this visit. */
    }
  }

  function systemPrefersDark() {
    return Boolean(window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches);
  }

  function resolve(choice) {
    if (choice === 'light' || choice === 'dark') return choice;
    return systemPrefersDark() ? 'dark' : 'light';
  }

  /**
   * Write the RESOLVED theme. The stylesheet reads `data-theme`.
   *
   * `data-theme-choice` records what was CHOSEN and is removed when nothing
   * was, so the two attributes stay answers to two different questions: which
   * palette is on screen, and whether a person put it there. A test and the
   * media listener below both need the second one.
   */
  function apply(choice) {
    root.setAttribute('data-theme', resolve(choice));
    if (choice) root.setAttribute('data-theme-choice', choice);
    else root.removeAttribute('data-theme-choice');
  }

  // Before anything paints.
  apply(storedChoice());

  // -----------------------------------------------------------------------
  // The two pictures
  // -----------------------------------------------------------------------
  //
  const LABELS = { light: 'Light', dark: 'Dark' };
  const GLYPHS = { light: '\u2600\uFE0E', dark: '\u263E' };
  const HINTS = {
    light: 'Always use the light theme, whatever this computer prefers.',
    dark: 'Always use the dark theme, whatever this computer prefers.',
  };

  function build(host) {
    const group = document.createElement('div');
    group.className = 'themeswitch';
    group.setAttribute('role', 'group');
    // Named, not decorated. A screen reader announces the group and then each
    // choice; nothing here depends on recognising an icon.
    group.setAttribute('aria-label', 'Colour theme');

    const buttons = CHOICES.map(function (choice) {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'themeswitch__btn';
      button.dataset.theme = choice;
      // A GLYPH, not an emoji and not a picture: the sun and the moon from
      // the design's geometric set, with the text-presentation selector so
      // no platform draws a colour emoji. The WORD is kept for screen
      // readers (visually hidden) and is the tooltip; `main.js` swaps both
      // into the reader's language once the catalogue has loaded.
      const glyph = document.createElement('span');
      glyph.className = 'themeswitch__glyph';
      glyph.setAttribute('aria-hidden', 'true');
      glyph.textContent = GLYPHS[choice];
      button.appendChild(glyph);
      const word = document.createElement('span');
      word.className = 'themeswitch__word sr-only';
      word.textContent = LABELS[choice];
      button.appendChild(word);
      button.title = LABELS[choice];
      button.setAttribute('aria-label', LABELS[choice] + ' theme. ' + HINTS[choice]);
      button.addEventListener('click', function () {
        remember(choice);
        apply(choice);
        sync();
      });
      group.appendChild(button);
      return button;
    });

    /**
     * The pressed button is the one whose theme is ON SCREEN, never the one
     * that was stored -- and with nothing stored those are the same answer
     * arrived at two ways. On a dark machine with no choice made, `Dark`
     * reads as pressed because the page IS dark. A control that showed
     * neither pressed would be describing a state the reader cannot see.
     */
    function sync() {
      const showing = root.getAttribute('data-theme');
      buttons.forEach(function (button) {
        button.setAttribute('aria-pressed', String(button.dataset.theme === showing));
      });
    }

    sync();
    host.appendChild(group);

    // Nothing chosen means follow the machine CONTINUOUSLY, not just at load:
    // opening the page at noon and leaving the tab open should go dark when
    // the machine does. Once either button is pressed a choice exists, this
    // stops firing, and the machine is no longer consulted.
    if (window.matchMedia) {
      const query = window.matchMedia('(prefers-color-scheme: dark)');
      const onChange = function () {
        if (!root.hasAttribute('data-theme-choice')) {
          apply(null);
          sync();
        }
      };
      if (query.addEventListener) query.addEventListener('change', onChange);
      else if (query.addListener) query.addListener(onChange);
    }
  }

  function mount() {
    const host = document.getElementById('theme-host');
    if (host && !host.firstChild) build(host);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', mount);
  } else {
    mount();
  }
})();
