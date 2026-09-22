/**
 * dom.js -- the only DOM construction helpers in the frontend.
 *
 * Every helper here is text-safe by construction. There is no code path in
 * this module that assigns to `innerHTML`, `outerHTML`, `insertAdjacentHTML`
 * or `document.write`, and no other module in the app is allowed to either.
 * Job data is untrusted content: it arrives from job boards we did not write.
 *
 * The single escape hatch is `el(..., { style })`, which sets individual
 * CSSStyleDeclaration properties, never a `style` attribute string. It has no
 * caller in the app at the moment: the one numeric width it was written for
 * lived in `meters.js`, which was dead code and has been deleted. It stays
 * because the safe shape is the one worth having available when a numeric
 * width is next needed.
 */

import { t } from './i18n.js';

/** Attribute names that must never be set from data. */
const FORBIDDEN_ATTRS = new Set([
  'onclick', 'onerror', 'onload', 'onmouseover', 'onfocus', 'onblur',
  'srcdoc', 'innerhtml',
]);

/**
 * Build an element.
 *
 * @param {string} tag
 * @param {object} [props]
 *   className {string}      -- class attribute
 *   text {string|number}    -- textContent (escaped by definition)
 *   attrs {object}          -- setAttribute pairs; null/undefined/false skip
 *   dataset {object}        -- data-* pairs
 *   props {object}          -- direct property assignment (value, checked, ...)
 *   style {object}          -- individual style properties
 *   on {object}             -- addEventListener pairs
 * @param {Array|Node|string} [children]
 * @returns {HTMLElement}
 */
export function el(tag, props = {}, children = []) {
  const node = document.createElement(tag);

  if (props.className) node.className = String(props.className);
  if (props.text !== undefined && props.text !== null) {
    node.textContent = String(props.text);
  }

  if (props.attrs) {
    for (const [name, value] of Object.entries(props.attrs)) {
      if (value === null || value === undefined || value === false) continue;
      const key = String(name).toLowerCase();
      if (FORBIDDEN_ATTRS.has(key) || key.startsWith('on')) continue;
      node.setAttribute(name, value === true ? '' : String(value));
    }
  }

  if (props.dataset) {
    for (const [name, value] of Object.entries(props.dataset)) {
      if (value === null || value === undefined) continue;
      node.dataset[name] = String(value);
    }
  }

  if (props.props) {
    for (const [name, value] of Object.entries(props.props)) {
      node[name] = value;
    }
  }

  if (props.style) {
    for (const [name, value] of Object.entries(props.style)) {
      if (value === null || value === undefined) continue;
      node.style.setProperty(name, String(value));
    }
  }

  if (props.on) {
    for (const [type, handler] of Object.entries(props.on)) {
      if (typeof handler === 'function') node.addEventListener(type, handler);
    }
  }

  append(node, children);
  return node;
}

/** Append a child, a list of children, or a string, skipping null holes. */
export function append(parent, children) {
  const list = Array.isArray(children) ? children : [children];
  for (const child of list) {
    if (child === null || child === undefined || child === false) continue;
    if (Array.isArray(child)) { append(parent, child); continue; }
    parent.appendChild(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return parent;
}

/** A document fragment holding the given children. */
export function frag(children = []) {
  return append(document.createDocumentFragment(), children);
}

/** Remove every child of a node. */
export function clear(node) {
  while (node.firstChild) node.removeChild(node.firstChild);
  return node;
}

/** Replace the children of a node in one shot. */
export function replace(node, children) {
  return append(clear(node), children);
}

/**
 * Return the url only when it is an http(s) URL we are willing to put in an
 * href. Everything else -- javascript:, data:, vbscript:, file:, a relative
 * fragment, a malformed string -- returns null and the caller renders text.
 */
export function safeUrl(value) {
  if (typeof value !== 'string') return null;
  const trimmed = value.trim();
  if (!/^https?:\/\//i.test(trimmed)) return null;
  try {
    const parsed = new URL(trimmed);
    if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') return null;
    return parsed.href;
  } catch {
    return null;
  }
}

/**
 * An external link, or -- when the URL is not safe to render as one -- the
 * label as plain text so nothing silently disappears.
 */
export function extLink(url, label, { className = '', title = '' } = {}) {
  const href = safeUrl(url);
  if (!href) {
    return el('span', {
      className: `${className} link--unavailable`.trim(),
      text: label,
      attrs: { title: t('dom.noLink') },
    });
  }
  return el('a', {
    className,
    text: label,
    attrs: {
      href,
      target: '_blank',
      rel: 'noopener noreferrer',
      title: title || href,
    },
  });
}

/** A <label> + control pair, with the label properly associated. */
export function field(id, labelText, control, { className = 'field', hint = '' } = {}) {
  control.id = id;
  const wrap = el('div', { className }, [
    el('label', { className: 'field__label', text: labelText, attrs: { for: id } }),
    control,
  ]);
  if (hint) wrap.appendChild(el('p', { className: 'field__hint', text: hint }));
  return wrap;
}

/** A <select> built from [{value,label}] options. */
export function select(options, value, onChange, { className = 'select', ariaLabel = '' } = {}) {
  const node = el('select', {
    className,
    attrs: ariaLabel ? { 'aria-label': ariaLabel } : {},
    on: {
      change: (event) => {
        // The CURRENT value, on the element, so a stylesheet can colour the
        // closed control by what it says. Written here rather than at each
        // call site because every select in the product wants it and only one
        // of them would have remembered.
        node.dataset.value = event.target.value;
        onChange(event.target.value, event);
      },
    },
  });
  node.dataset.value = String(value ?? '');
  for (const option of options) {
    node.appendChild(el('option', {
      text: option.label,
      attrs: { value: option.value },
      props: { selected: String(option.value) === String(value ?? '') },
    }));
  }
  return node;
}

/** An icon-free, text-carrying button. */
export function button(label, onClick, { className = 'btn', ariaLabel = '', type = 'button', attrs = {} } = {}) {
  return el('button', {
    className,
    text: label,
    attrs: { type, ...(ariaLabel ? { 'aria-label': ariaLabel } : {}), ...attrs },
    on: { click: onClick },
  });
}

/** Focusable descendants, in document order. Used by the drawer focus trap. */
export function focusables(root) {
  const selector = [
    'a[href]', 'button:not([disabled])', 'input:not([disabled])',
    'select:not([disabled])', 'textarea:not([disabled])', '[tabindex]:not([tabindex="-1"])',
  ].join(',');
  return Array.from(root.querySelectorAll(selector))
    .filter((node) => node.offsetParent !== null || node === document.activeElement);
}
