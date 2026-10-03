"""Functional recovery A, as the owner met it: a list that comes back without
loading again, the heart on My applications, the rail's one count, and the
profile's layout."""

from __future__ import annotations

import json

from tests.browser.chrome import Chrome
from tests.browser.conftest import DESKTOP

#: Read off the rendered rail; each was asked to go.
REMOVED_FROM_RAIL = (
    "your data stays on this computer",
    " open",
    "interviews",
    "all job sites working",
    "jobs found",
)

#: Every list request since the page started, and whether a skeleton ever drew.
WATCH = """(() => {
  if (window.__watch) return;
  window.__watch = { lists: [], done: [], skeleton: false };
  const real = window.fetch;
  window.fetch = (url, opts) => {
    const text = String(url);
    if (!/\\/api\\/jobs\\?/.test(text)) return real(url, opts);
    window.__watch.lists.push(text);
    return real(url, opts).then((answer) => { window.__watch.done.push(text); return answer; });
  };
  new MutationObserver(() => {
    if (document.querySelector('.card--skeleton, table.jobs[aria-hidden="true"]')) {
      window.__watch.skeleton = true;
    }
  }).observe(document.body, { subtree: true, childList: true });
})()"""


def _open_jobs(page: Chrome, server: str) -> None:
    page.set_viewport(*DESKTOP)
    page.navigate(f"{server}/#jobs")
    page.wait_for("document.querySelector('.card:not(.card--skeleton)') !== null", message="cards")
    page.evaluate(WATCH)


def _lists(page: Chrome) -> list[str]:
    return list(page.evaluate("window.__watch.lists"))


def _click(page: Chrome, selector: str) -> None:
    page.evaluate(f"document.querySelector({json.dumps(selector)}).click()")


def test_cards_and_list_are_two_presentations_kept_in_hand(page: Chrome, server: str) -> None:
    _open_jobs(page, server)
    # The other presentation is fetched ahead once the cards are on screen.
    page.evaluate("document.getElementById('direction').click()")
    page.wait_for(
        "window.__watch.done.some((u) =>"
        " !u.includes('group_duplicates') && u.includes('limit=25'))",
        message="the list presentation fetched ahead",
    )
    page.wait_for("document.querySelector('.card:not(.card--skeleton)') !== null", message="cards")
    before = len(_lists(page))
    page.evaluate("window.__watch.skeleton = false")
    for selector, ready in (
        ("#view-table", "table.jobs:not([aria-hidden]) tbody tr"),
        ("#view-cards", ".card:not(.card--skeleton)"),
        ("#view-table", "table.jobs:not([aria-hidden]) tbody tr"),
    ):
        _click(page, selector)
        page.wait_for(f"document.querySelector({json.dumps(ready)}) !== null", message=selector)
    assert len(_lists(page)) == before, _lists(page)[before:]
    assert page.evaluate("window.__watch.skeleton") is False, "a switch drew a skeleton"


def test_leaving_find_jobs_and_coming_back_reads_nothing_again(page: Chrome, server: str) -> None:
    _open_jobs(page, server)
    before = len(_lists(page))
    for target in ("profile", "home", "jobs"):
        _click(page, f'.topnav__link[data-page="{target}"]')
        page.wait_for(
            f"document.querySelector('.topnav__link[data-page=\"{target}\"]')"
            ".getAttribute('aria-current') === 'page'",
            message=target,
        )
    page.wait_for("document.querySelector('.card:not(.card--skeleton)') !== null", message="cards")
    assert len(_lists(page)) == before


def test_a_new_search_reads_a_new_list(page: Chrome, server: str) -> None:
    _open_jobs(page, server)
    before = len(_lists(page))
    page.evaluate(
        "(() => { const box = document.getElementById('f-search');"
        " box.value = 'data'; box.dispatchEvent(new Event('input', { bubbles: true })); })()"
    )
    page.wait_for(
        f"window.__watch.lists.slice({before}).some((u) => u.includes('search=data'))",
        message="a request for the new search",
    )


def test_a_heart_puts_the_job_on_my_applications(page: Chrome, pristine_server: str) -> None:
    _open_jobs(page, pristine_server)
    job_id = page.evaluate("document.querySelector('.card').dataset.jobId")
    title = page.evaluate("document.querySelector('.card .card__title').textContent.trim()")
    page.evaluate(
        f"fetch('/api/jobs/{job_id}/saved', {{ method: 'PATCH',"
        " headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ saved: true }) })"
    )
    page.wait_for(
        f"fetch('/api/jobs/{job_id}').then((r) => r.json()).then((j) => j.saved)",
        message="saved",
    )
    _click(page, '.topnav__link[data-page="applications"]')
    page.wait_for(
        f"[...document.querySelectorAll('.kanban .kcard')]"
        f".some((c) => c.dataset.jobId === '{job_id}')",
        message="the saved job on the board",
    )
    column = page.evaluate(
        f"document.querySelector('.kcard[data-job-id=\"{job_id}\"]').closest('.kanban > *')"
        ".querySelector('h3, h2, header').textContent"
    )
    assert "Saved" in column, column
    assert title
    # Unsaved, and never moved: off the board.
    page.evaluate(
        f"fetch('/api/jobs/{job_id}/saved', {{ method: 'PATCH',"
        " headers: { 'Content-Type': 'application/json' },"
        " body: JSON.stringify({ saved: false }) })"
    )
    page.reload()
    page.wait_for("document.querySelector('.kanban') !== null", message="the board")
    page.wait_for(
        f"![...document.querySelectorAll('.kanban .kcard')]"
        f".some((c) => c.dataset.jobId === '{job_id}')",
        message="the unsaved job gone",
    )


def test_the_rail_footer_is_the_job_count_alone(page: Chrome, server: str) -> None:
    page.set_viewport(*DESKTOP)
    page.navigate(server)
    page.wait_for("document.querySelector('.sidenav__stat--jobs') !== null", message="the count")
    text = str(page.evaluate("document.querySelector('.sidenav').innerText")).lower()
    assert "jobs retrieved" in text
    for gone in REMOVED_FROM_RAIL:
        assert gone not in text, f"the rail still says {gone!r}"
    # Personal data is the ordinary case and carries no tag (demo data does).
    assert page.evaluate("document.getElementById('health-mode').innerText").lower() != "personal"
    # In Portuguese too.
    page.evaluate("document.querySelector('#locale-host [data-locale=\"pt-BR\"]').click()")
    page.wait_for(
        "document.querySelector('.sidenav').innerText.includes('vagas coletadas')",
        message="vagas coletadas",
    )
    page.evaluate("document.querySelector('#locale-host [data-locale=\"en\"]').click()")


def test_every_destination_fits_on_one_line_in_both_languages(page: Chrome, server: str) -> None:
    page.set_viewport(*DESKTOP)
    page.navigate(server)
    page.wait_for("document.querySelector('.topnav__label') !== null", message="the rail")
    cut = (
        "[...document.querySelectorAll('.topnav__link[data-page] .topnav__label')]"
        ".filter((l) => l.scrollWidth > l.clientWidth || l.getClientRects().length > 1)"
        ".map((l) => l.textContent)"
    )
    assert page.evaluate(cut) == []
    page.evaluate("document.querySelector('#locale-host [data-locale=\"pt-BR\"]').click()")
    page.wait_for("document.documentElement.lang.startsWith('pt')", message="Portuguese")
    assert page.evaluate(cut) == []
    page.evaluate("document.querySelector('#locale-host [data-locale=\"en\"]').click()")


#: A profile with every kind of fact confirmed: nothing for "Make your profile
#: stronger" to ask for.
COMPLETE_LEDGER = {
    "claims": [
        {
            "claim_key": "e1",
            "claim_type": "EMPLOYMENT",
            "verified": True,
            "text": "Built X",
            "employer": "Acme",
            "period_start": "2021-01",
            "period_end": "2024-01",
        },
        {"claim_key": "s1", "claim_type": "SKILL", "verified": True, "text": "SQL"},
        {"claim_key": "p1", "claim_type": "PROJECT", "verified": True, "text": "Shipped Y"},
        {"claim_key": "q1", "claim_type": "CERTIFICATION", "verified": True, "text": "Cert Z"},
    ]
}


def test_a_complete_profile_leaves_no_hole_and_the_tabs_are_centred(
    page: Chrome, server: str
) -> None:
    page.set_viewport(*DESKTOP)
    page.navigate(server)
    page.wait_for("document.querySelector('.sidenav .topnav__link') !== null", message="rail")
    ledger = json.dumps(COMPLETE_LEDGER)
    page.evaluate(
        "(() => { const real = window.fetch; window.fetch = (url, opts) =>"
        " /\\/api\\/evidence$/.test(String(url))"
        f" ? Promise.resolve(new Response({json.dumps(ledger)},"
        " { headers: { 'Content-Type': 'application/json' } })) : real(url, opts); })()"
    )
    _click(page, '.topnav__link[data-page="profile"]')
    page.wait_for("document.querySelector('.profile__complete') !== null", message="one line")
    assert page.evaluate("document.querySelector('.profile__stronger')") is None
    # The freed row is shared by what remains, side by side.
    widths = page.evaluate(
        "[...document.querySelectorAll('.profile__overview--complete > *')]"
        ".map((n) => Math.round(n.getBoundingClientRect().width))"
    )
    assert len(widths) == 2 and min(widths) > 200, widths
    offset = page.evaluate(
        "(() => { const tabs = document.querySelector('#page-profile .profiletabs')"
        ".getBoundingClientRect(); const box = document.getElementById('page-profile')"
        ".getBoundingClientRect(); return Math.abs((tabs.left + tabs.width / 2)"
        " - (box.left + box.width / 2)); })()"
    )
    assert offset <= 2, f"the tabs are {offset}px off centre"
