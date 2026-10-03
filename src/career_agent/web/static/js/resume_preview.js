/**
 * resume_preview.js -- the live resume preview: one rendered document, shown
 * as real pages.
 *
 * The HTML comes from the server's one renderer (`POST /api/resume/render`);
 * nothing here builds resume markup. It is shown in a frame that runs no
 * script and loads nothing from the network (the server serves it under its
 * own policy), so the resume's CSS never touches this app and this app's CSS
 * never touches the resume.
 *
 * DOUBLE BUFFERED. Two frames: the new render loads into the hidden one, is
 * paginated there, and only then becomes the visible one. The resume on
 * screen is never blanked while the next one is on its way.
 *
 * LATEST WINS. Every update has a number; a response or a load event for an
 * older number is dropped, and the older request is aborted.
 *
 * PAGINATION. `paginate` walks the rendered blocks in order with the same two
 * rules the print CSS states: a `[data-block]` is never split, and a
 * `[data-keep]` block stays on the page of the block after it. A block that
 * does not fit where it is starts the next page. A block taller than a whole
 * page cannot be kept whole; it is reported as overflow, never clipped.
 */

import { renderResume } from './api.js';
import { el } from './dom.js';
import { t } from './i18n.js';

/** CSS pixels per millimetre. */
const PX_PER_MM = 96 / 25.4;
/** The grey band drawn between two pages, in CSS pixels. */
const GAP = 24;

/** A node's top and bottom, measured from the top of the document. */
function span(node, root) {
  const box = node.getBoundingClientRect();
  const base = root.getBoundingClientRect().top;
  return { top: box.top - base, bottom: box.bottom - base };
}

/** Consecutive blocks that must share a page: each `[data-keep]` joins the next. */
function groups(blocks) {
  const out = [];
  let current = [];
  for (const block of blocks) {
    current.push(block);
    if (!block.hasAttribute('data-keep')) {
      out.push(current);
      current = [];
    }
  }
  if (current.length) out.push(current);
  return out;
}

/**
 * Lay a rendered resume out on pages, inside its own document.
 * @returns {{pages: number, overflow: string[], width: number, height: number,
 *   pageHeight: number, gap: number}}
 */
export function paginate(doc, page) {
  const root = doc.querySelector('.rv-doc');
  const H = page.height_mm * PX_PER_MM;
  const M = page.margin_mm * PX_PER_MM;
  const top = (n) => n * (H + GAP);
  const bottomLimit = (n) => top(n) + H - M;
  const overflow = [];
  let current = 0;
  for (const group of groups([...root.querySelectorAll('[data-block]')])) {
    const first = group[0];
    const last = group[group.length - 1];
    if (span(last, root).bottom > bottomLimit(current) + 0.5
        && span(first, root).top > top(current) + M + 0.5) {
      // Push the group to the top of the next page.
      const spacer = doc.createElement(first.tagName === 'LI' ? 'li' : 'div');
      spacer.className = 'rv-spacer';
      spacer.setAttribute('aria-hidden', 'true');
      first.before(spacer);
      current += 1;
      // Margins stop collapsing once the spacer has height: correct twice.
      for (let pass = 0; pass < 2; pass += 1) {
        const missing = top(current) + M - span(first, root).top;
        spacer.style.height = `${Math.max(0, (parseFloat(spacer.style.height) || 0) + missing)}px`;
      }
    }
    while (span(last, root).bottom > bottomLimit(current) + 0.5) {
      // Taller than what is left of an empty page: it cannot stay whole.
      overflow.push(first.dataset.ref || '');
      current += 1;
    }
  }
  const pages = current + 1;
  root.style.minHeight = `${pages * H + (pages - 1) * GAP}px`;
  // A last block's own bottom margin may reach past the last page: the frame
  // is as tall as the document, so nothing scrolls inside it or is cut off.
  const height = Math.max(root.scrollHeight, doc.documentElement.scrollHeight);
  for (let n = 1; n < pages; n += 1) {
    const band = doc.createElement('div');
    band.className = 'rv-gap';
    band.setAttribute('aria-hidden', 'true');
    band.style.top = `${top(n) - GAP}px`;
    band.style.height = `${GAP}px`;
    root.append(band);
  }
  return { pages, overflow, width: page.width_mm * PX_PER_MM, height, pageHeight: H, gap: GAP };
}

/**
 * A preview: `update(document)` renders and shows it, `onRef(ref)` hears a
 * click on a rendered block (its `data-ref`).
 */
export function createResumePreview({ onRef = () => {} } = {}) {
  const status = el('p', { className: 'rvp__status', attrs: { 'aria-live': 'polite' } });
  const frames = [0, 1].map((n) => el('iframe', {
    className: 'rvp__frame',
    attrs: {
      sandbox: 'allow-same-origin',
      title: t('rv.preview.title'),
      tabindex: '-1',
      'aria-hidden': n === 0 ? 'false' : 'true',
    },
  }));
  // The back frame is laid out but invisible: a `hidden` frame has no layout
  // to paginate.
  frames[1].dataset.off = '';
  const paper = el('div', { className: 'rvp__paper' }, frames);
  const stage = el('div', { className: 'rvp__stage' }, [paper]);
  const root = el('section', { className: 'rvp', attrs: { 'aria-label': t('rv.preview.title') } }, [
    status, stage,
  ]);

  let shown = 0;
  let serial = 0;
  let controller = null;
  let last = null;

  function fit() {
    if (!last) return;
    const room = Math.max(160, stage.clientWidth - 24);
    const scale = Math.min(1, room / last.width);
    // Whole pixels, rounded up: a frame a fraction narrower than its page
    // grows scrollbars, and scrollbars shrink the page they sit on.
    const width = Math.ceil(last.width);
    const height = Math.ceil(last.height);
    for (const frame of frames) {
      frame.style.width = `${width}px`;
      frame.style.height = `${height}px`;
      frame.style.transform = `scale(${scale})`;
    }
    paper.style.width = `${width * scale}px`;
    paper.style.height = `${height * scale}px`;
  }

  function say(layout) {
    const pages = layout.pages === 1 ? t('rv.preview.page') : t('rv.preview.pages', { n: layout.pages });
    status.textContent = layout.overflow.length ? `${pages} ${t('rv.preview.overflow')}` : pages;
    root.dataset.pages = String(layout.pages);
    root.dataset.overflow = String(layout.overflow.length);
    root.dataset.pageHeight = String(layout.pageHeight);
  }

  function load(frame, url, mine) {
    return new Promise((resolve) => {
      frame.addEventListener('load', () => resolve(mine === serial), { once: true });
      frame.src = url;
    });
  }

  async function update(resumeDocument) {
    serial += 1;
    const mine = serial;
    if (controller) controller.abort();
    controller = new AbortController();
    let rendered;
    try {
      rendered = await renderResume(resumeDocument, { signal: controller.signal });
    } catch (error) {
      if (error.kind === 'aborted' || mine !== serial) return null;
      status.textContent = error.userMessage || t('rv.preview.failed');
      root.dataset.state = 'error';
      return null;
    }
    if (mine !== serial) return null;
    const next = frames[1 - shown];
    if (!(await load(next, rendered.url, mine))) return null;
    const doc = next.contentDocument;
    // A load event left over from an earlier navigation is not this render.
    if (!doc || !doc.URL.endsWith(rendered.url)) return null;
    const layout = paginate(doc, rendered.page);
    doc.addEventListener('click', (event) => {
      const target = event.target.closest('[data-ref]');
      event.preventDefault();
      if (target) onRef(target.dataset.ref);
    });
    last = layout;
    delete next.dataset.off;
    next.setAttribute('aria-hidden', 'false');
    frames[shown].dataset.off = '';
    frames[shown].setAttribute('aria-hidden', 'true');
    shown = 1 - shown;
    fit();
    say(layout);
    root.dataset.state = 'ready';
    root.dataset.serial = String(mine);
    return { ...layout, findings: rendered.findings || [] };
  }

  window.addEventListener('resize', fit);
  return { root, update, destroy: () => window.removeEventListener('resize', fit) };
}
