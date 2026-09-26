"""The companion handoff is explicit, local and carries nothing but a posting id."""

from urllib.parse import parse_qs, urlparse

from tests.browser.test_browser_acceptance import click, open_list


def test_the_handoff_names_the_posting_and_nothing_private(page, pristine_server):
    # The handoff is the next step once Career Agent holds something about the
    # person's career; before that the drawer asks for it first
    # (`test_post_merge_continuity.py`). One confirmed, invented skill.
    page.navigate(pristine_server)
    page.evaluate(
        "fetch('/api/evidence', {method: 'POST', headers: {'Content-Type': 'application/json'},"
        " body: JSON.stringify({claim_type: 'SKILL', text: 'Invented spreadsheet modelling'})})"
        ".then(r => r.status)"
    )
    open_list(page, pristine_server)
    page.wait_for("document.querySelector('.card:not(.card--skeleton)')")
    job_id = page.evaluate("document.querySelector('.card:not(.card--skeleton)').dataset.jobId")
    click(page, "document.querySelector('.card:not(.card--skeleton)')")
    page.wait_for("document.querySelector('#drawer-open-tailor')")
    href = page.evaluate("document.querySelector('#drawer-open-tailor').getAttribute('href')")
    parsed = urlparse(href)
    assert parsed.path == "/resume-tailor"
    assert parse_qs(parsed.query) == {"job": [job_id]}, "only the posting id travels"
    assert page.evaluate("document.querySelector('#drawer-open-tailor').target") == "_blank"
    assert page.evaluate("document.querySelector('#drawer-open-tailor').dataset.page") is None
    # No copy-and-paste step any more: Tailor reads the posting itself.
    assert not page.evaluate("document.body.innerText.includes('Copy job description')")
