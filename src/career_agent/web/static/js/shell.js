/**
 * The Workspace V2 shell: the rail, the page header and the mobile drawer.
 *
 * -> docs/design/career-agent-workspace-v2/README.md
 *
 * WHY THIS IS ITS OWN MODULE
 * --------------------------
 * `main.js` already decides which page is on screen. What it did not have was
 * a place to say what a page IS -- its eyebrow, its title, the one sentence
 * under it and the one action beside it. Spreading that across five call
 * sites is how five screens come to disagree about how tall a header is.
 *
 * So the page header is a CONTRACT, filled from one table, and every screen
 * gets the same shape whether or not it has anything to put in each slot.
 *
 * WHAT IT REFUSES TO DRAW
 * -----------------------
 * The design puts `LVL 7` and a 72% bar in the user card. Neither is a number
 * this product could honestly produce. The bar shows the EVIDENCE REVIEW
 * instead -- statements answered out of statements that arrived -- which has
 * a denominator a person can point at, and it is absent entirely when no
 * package is staged rather than showing a confident zero.
 *
 * The footer stats are counts from the database. Where a count cannot be had,
 * the tile is omitted rather than filled with a dash that looks like nothing
 * to report.
 */

import { el } from './dom.js';
import { t, getLocale } from './i18n.js';

/** Which pages exist, and what their header says. Keys are `data-page`. */
export const PAGE_HEADERS = {
  home: { title: 'pagehead.title.home', sub: 'pagehead.sub.home' },
  jobs: { title: 'pagehead.title.jobs', sub: 'pagehead.sub.jobs' },
  applications: { title: 'pagehead.title.applications', sub: 'pagehead.sub.applications' },
  profile: { title: 'pagehead.title.profile', sub: 'pagehead.sub.profile' },
  evidence: { title: 'pagehead.title.evidence', sub: 'pagehead.sub.evidence' },
  documents: { title: 'pagehead.title.documents', sub: 'pagehead.sub.documents' },
  manage: { title: 'pagehead.title.manage' },
  settings: { title: 'pagehead.title.settings', sub: 'pagehead.sub.settings' },
  setup: { eyebrow: 'pagehead.eyebrow.setup', title: 'pagehead.title.setup' },
};

/**
 * The breakpoint the rail becomes a drawer at. Matches the stylesheet.
 *
 * Written as the whole query rather than a number interpolated into one: the
 * localisation gate reads a template literal holding words and spaces as
 * product-authored copy, and it is right to -- that is exactly the shape a
 * missed sentence takes. A media query is not copy, so it is not one.
 */
const DRAWER_QUERY = '(max-width:900px)';

export function createShell() {
  const nodes = {
    body: document.body,
    nav: document.getElementById('sidenav'),
    opener: document.getElementById('sidenav-open'),
    openerLabel: document.getElementById('sidenav-open-label'),
    scrim: document.getElementById('sidenav-scrim'),
    stats: document.getElementById('sidenav-stats'),
    tag: document.getElementById('sidenav-tag'),
    badges: {
      jobs: document.getElementById('nav-badge-jobs'),
      applications: document.getElementById('nav-badge-applications'),
    },
    eyebrow: document.getElementById('pagehead-eyebrow'),
    title: document.getElementById('pagehead-title'),
    sub: document.getElementById('pagehead-sub'),
    actions: document.getElementById('pagehead-actions'),
  };

  let opener = null;   // who opened the drawer, so Escape can give it back

  // -------------------------------------------------------------------
  // the page header
  // -------------------------------------------------------------------

  /**
   * Fill the header for one page.
   *
   * `action` is a node or null. AT MOST ONE, per the contract: a header with
   * two primary actions has no primary action.
   */
  function setPage(page, { subtitle = '', action = null } = {}) {
    const spec = PAGE_HEADERS[page] || PAGE_HEADERS.home;
    nodes.eyebrow.textContent = spec.eyebrow ? t(spec.eyebrow) : '';
    nodes.title.textContent = t(spec.title);
    nodes.sub.textContent = subtitle || (spec.sub ? t(spec.sub) : '');
    nodes.actions.replaceChildren(...(action ? [action] : []));
  }

  // -------------------------------------------------------------------
  // the user card
  // -------------------------------------------------------------------

  // -------------------------------------------------------------------
  // the footer stats
  // -------------------------------------------------------------------

  //: The last counts drawn, so a language switch can redraw their LABELS.
  //  Without this the three tiles kept the words they were built with and the
  //  sidebar read "36 vagas" under an English heading. The COUNTS are held
  //  rather than re-fetched: `/api/home` composes them and asking it again to
  //  change three words would be a request that answers a question nobody
  //  asked.
  let lastStats = null;

  /**
   * Three counts, and only the ones that are real.
   *
   * A tile whose value is unknown is OMITTED rather than drawn with a dash:
   * "we could not work this out" and "there are none" are opposite facts and
   * they must not render the same.
   */
  function setStats({ jobs = null, open = null, interview = null } = {}) {
    lastStats = { jobs, open, interview };
    const tiles = [
      ['jobs', jobs, 'sidenav.statJobs'],
      ['open', open, 'sidenav.statOpen'],
      ['interview', interview, 'sidenav.statInterview'],
    ]
      .filter(([, value]) => Number.isFinite(value))
      .map(([key, value, label]) => el('span', { className: `sidenav__stat sidenav__stat--${key}` }, [
        // With the reader's own thousands separator: 154,631 or 154.631.
        el('span', { className: 'sidenav__statvalue num', text: Number(value).toLocaleString(getLocale()) }),
        el('span', { className: 'sidenav__statlabel', text: ` ${t(label)}` }),
      ]));
    nodes.stats.replaceChildren(...tiles);
  }

  /**
   * The green counts beside two destinations: jobs added by the latest
   * refresh, and the jobs being tracked on My applications. A count that is
   * unknown or zero is not drawn, so a badge never says "0" about something.
   * The number is shown; what it counts is said to a screen reader.
   */
  let lastBadges = {};
  function setBadges(counts = lastBadges) {
    lastBadges = counts;
    const words = { jobs: 'nav.badgeJobs', applications: 'nav.badgeApplications' };
    for (const key of ['jobs', 'applications']) {
      const node = nodes.badges[key];
      const value = counts[key];
      if (!node) continue;
      const shown = Number.isFinite(value) && value > 0;
      node.hidden = !shown;
      node.replaceChildren(...(shown ? [
        el('span', { attrs: { 'aria-hidden': 'true' }, text: Number(value).toLocaleString(getLocale()) }),
        el('span', { className: 'sr-only', text: t(words[key], { n: value }) }),
      ] : []));
    }
  }

  // -------------------------------------------------------------------
  // the mobile drawer
  // -------------------------------------------------------------------

  function isDrawer() {
    return window.matchMedia(DRAWER_QUERY).matches;
  }

  function openDrawer() {
    if (!isDrawer()) return;
    opener = document.activeElement;
    nodes.body.classList.add('sidenav-open');
    nodes.opener.setAttribute('aria-expanded', 'true');
    nodes.scrim.hidden = false;
    // Focus the first destination rather than the panel: a drawer that opens
    // with focus on a container gives a keyboard user nothing to press.
    const first = nodes.nav.querySelector('.topnav__link');
    if (first) first.focus();
  }

  function closeDrawer({ restore = true } = {}) {
    if (!nodes.body.classList.contains('sidenav-open')) return;
    nodes.body.classList.remove('sidenav-open');
    nodes.opener.setAttribute('aria-expanded', 'false');
    nodes.scrim.hidden = true;
    if (restore && opener && document.contains(opener)) opener.focus();
    else if (restore) nodes.opener.focus();
    opener = null;
  }

  nodes.opener.addEventListener('click', () => {
    if (nodes.body.classList.contains('sidenav-open')) closeDrawer();
    else openDrawer();
  });
  nodes.scrim.addEventListener('click', () => closeDrawer());

  document.addEventListener('keydown', (event) => {
    if (event.key !== 'Escape') return;
    if (!nodes.body.classList.contains('sidenav-open')) return;
    // The profile menu floats above the drawer: while it is open, Escape
    // is its own (it closes the menu and gives focus back to its button).
    if (document.getElementById('lprof-menu')) return;
    // The drawer is the innermost overlay when it is open, so it takes the
    // key and stops it reaching the job drawer behind it.
    event.stopPropagation();
    closeDrawer();
  }, true);

  // FOCUS CONTAINMENT. Tab inside the open drawer cycles within it; without
  // this, tabbing walks straight into the results underneath, which are
  // covered by a scrim and cannot be seen.
  nodes.nav.addEventListener('keydown', (event) => {
    if (event.key !== 'Tab') return;
    if (!nodes.body.classList.contains('sidenav-open')) return;
    const focusable = [...nodes.nav.querySelectorAll(
      'button, [href], select, input, summary, [tabindex]:not([tabindex="-1"])',
    )].filter((node) => !node.disabled && node.offsetParent !== null);
    if (!focusable.length) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  });

  // A destination closes the drawer: on a phone the page it opened is behind
  // it, and leaving the rail over it is a navigation that appears to do
  // nothing.
  for (const link of nodes.nav.querySelectorAll('.topnav__link')) {
    link.addEventListener('click', () => closeDrawer({ restore: false }));
  }

  /** Re-read every string after a language change. */
  function retranslate(page) {
    nodes.openerLabel.textContent = t('sidenav.menu');
    nodes.opener.setAttribute('aria-label', t('sidenav.menuLabel'));
    nodes.tag.textContent = t('app.tagline');
    for (const link of nodes.nav.querySelectorAll('.topnav__link')) {
      const label = link.querySelector('.topnav__label');
      if (label) label.textContent = t(`nav.${link.dataset.page}`);
    }
    const fixed = {
      'nav-section-search': 'nav.sectionSearch',
      'nav-section-profile': 'nav.sectionProfile',
      'look-label': 'sidenav.look',
      'language-label': 'locale.label',
      'sidenav-privacy': 'sidenav.privacy',
      'site-alert-fix': 'sidenav.alertFix',
    };
    for (const [id, key] of Object.entries(fixed)) {
      const node = document.getElementById(id);
      if (node) node.textContent = t(key);
    }
    const dismiss = document.getElementById('site-alert-close');
    if (dismiss) {
      dismiss.setAttribute('aria-label', t('sidenav.alertDismiss'));
      dismiss.title = t('sidenav.alertDismiss');
    }
    const tailor = document.getElementById('nav-tailor-label');
    if (tailor) tailor.textContent = t('nav.tailor');
    const beta = document.getElementById('nav-tailor-beta');
    if (beta) beta.textContent = t('nav.beta');
    const quit = document.getElementById('quit-app');
    if (quit) quit.textContent = t('app.quit');
    if (lastStats) setStats(lastStats);
    setBadges();
    if (page) setPage(page);
  }

  return { setPage, setStats, setBadges, retranslate, closeDrawer, isDrawer };
}
