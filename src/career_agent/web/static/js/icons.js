/**
 * icons.js -- the few pictures this product draws, as inline SVG.
 *
 * WHY INLINE, AND WHY NOT AN EMOJI
 * --------------------------------
 * The language control used to be two letters and nothing else, and the
 * obvious way to add a flag is the Unicode regional-indicator pair. It is
 * also the wrong way here: Windows ships no colour flag glyphs, so a
 * regional-indicator pair renders as two boxed letters -- `GB`, `BR` -- on
 * the machine this product is written on and run on. An icon that is a
 * fallback rectangle on the owner's own screen is not an icon.
 *
 * WHY NOT A FILE
 * --------------
 * An `<img src="flags/br.svg">` is a second request, cannot inherit
 * `currentColor`, and cannot follow the theme. These are a few hundred bytes
 * each and they are drawn in the document.
 *
 * WHY NOT `innerHTML`
 * -------------------
 * `scripts/frontend_check.mjs` forbids it, and that rule is load-bearing:
 * job descriptions are third-party text and a single sink is all it takes.
 * The rule has no exception for a string a developer is sure about, because
 * every such string starts out being one.
 *
 * THE ICON IS NEVER THE WHOLE MESSAGE. Every control that carries one of
 * these also carries its word -- `EN`, `PT`, `Light`, `Dark`, `Auto` -- and
 * the icon is marked `aria-hidden`. A moon can mean "you are in dark" or
 * "switch to dark", and a flag is a country while a language is not.
 */

const NS = 'http://www.w3.org/2000/svg';

/**
 * Build one SVG from a declarative shape list.
 *
 * `shapes` is a list of `[tagName, attributes]`. Nothing here reads a
 * caller-supplied string as markup, which is the whole point.
 */
export function svgIcon(viewBox, shapes, { className = 'icon', attrs = {} } = {}) {
  const root = document.createElementNS(NS, 'svg');
  root.setAttribute('viewBox', viewBox);
  root.setAttribute('class', className);
  // Decoration beside a word. A screen reader must not announce it, and it
  // must never be the only thing a control says.
  root.setAttribute('aria-hidden', 'true');
  root.setAttribute('focusable', 'false');
  for (const [name, value] of Object.entries(attrs)) root.setAttribute(name, value);
  for (const [tag, attributes] of shapes) {
    const node = document.createElementNS(NS, tag);
    for (const [name, value] of Object.entries(attributes)) node.setAttribute(name, value);
    root.appendChild(node);
  }
  return root;
}
