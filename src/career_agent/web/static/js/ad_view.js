/**
 * ad_view.js -- a posting's ad, laid out from the blocks the server built.
 *
 * The server sends structure (headings, paragraphs, lists, "Label: value"
 * rows) and never markup. Everything here becomes DOM text nodes: nothing from
 * an employer is ever handed to innerHTML, no script can run, no image or
 * other resource is fetched, and a link is drawn only when its address is
 * http, https or mailto. The words are the ad's own; this only decides spacing.
 */

import { el } from './dom.js';

const SAFE = /^(?:https?:|mailto:)/i;

/** A run is a string, or {x, b, i, a}. */
function runNode(run) {
  if (typeof run === 'string') return withBreaks(run);
  let node = withBreaks(run.x);
  if (run.b) node = el('strong', {}, node);
  if (run.i) node = el('em', {}, node);
  if (run.a && SAFE.test(run.a)) {
    node = el('a', {
      className: 'ad__link', attrs: { href: run.a, rel: 'noopener noreferrer', target: '_blank' },
    }, node);
  }
  return node;
}

/** Text with its own line breaks kept as <br>, in one fragment. */
function withBreaks(text) {
  const parts = String(text).split('\n');
  const out = [];
  parts.forEach((part, index) => {
    if (index) out.push(el('br'));
    if (part) out.push(document.createTextNode(part));
  });
  return out.length === 1 ? out[0] : fragment(out);
}

function fragment(nodes) {
  const holder = document.createDocumentFragment();
  for (const node of nodes) holder.append(node);
  return holder;
}

function runs(list) {
  return fragment((list || []).map(runNode));
}

function listNode(block, depth = 0) {
  const tag = block.t === 'ol' ? 'ol' : 'ul';
  const list = el(tag, {
    className: `ad__list${block.plain ? ' ad__list--plain' : ''}${depth ? ' ad__list--nested' : ''}`,
  });
  for (const item of block.i || []) {
    const li = el('li', { className: 'ad__item' });
    li.append(runs(item.r));
    if (item.s && depth < 3) li.append(listNode(item.s, depth + 1));
    list.append(li);
  }
  return list;
}

function blockNode(block) {
  switch (block.t) {
    case 'h':
      return el('h4', { className: 'ad__h', text: block.x });
    case 'p': {
      const p = el('p', { className: 'ad__p' });
      p.append(runs(block.r));
      return p;
    }
    case 'ul':
    case 'ol':
      return listNode(block);
    case 'meta': {
      const dl = el('dl', { className: 'ad__meta' });
      for (const row of block.rows || []) {
        const dd = el('dd');
        dd.append(runs(row.r));
        dl.append(el('dt', { text: row.k }), dd);
      }
      return dl;
    }
    default:
      return null;
  }
}

/** The whole ad as a column of blocks. Empty input gives an empty column. */
export function renderAd(blocks) {
  const root = el('div', { className: 'ad', attrs: { 'data-ad-blocks': String((blocks || []).length) } });
  for (const block of blocks || []) {
    const node = blockNode(block);
    if (node) root.append(node);
  }
  return root;
}
