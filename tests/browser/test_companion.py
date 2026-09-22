"""The companion handoff is explicit, local and does not rewrite posting text."""

from tests.browser.test_browser_acceptance import click, open_list


def test_copy_posting_and_companion_entry(page, pristine_server):
    open_list(page, pristine_server)
    page.wait_for("document.querySelector('.card:not(.card--skeleton)')")
    page.evaluate(
        "Object.defineProperty(navigator, 'clipboard', {configurable: true, "
        "value: {writeText: async (text) => { window.copiedPosting = text; }}})"
    )
    click(page, "document.querySelector('.card:not(.card--skeleton)')")
    page.wait_for("document.body.innerText.includes('Copy job description')")
    click(
        page,
        "Array.from(document.querySelectorAll('button')).find(n => "
        "n.textContent === 'Copy job description')",
    )
    page.wait_for("typeof window.copiedPosting === 'string' && window.copiedPosting.length > 50")
    assert page.evaluate("document.body.innerText.includes('Copied. Open Resume Tailor Beta')")
    assert page.evaluate("document.querySelector('a[href=\"/resume-tailor\"]').target") == "_blank"
    assert (
        page.evaluate("document.querySelector('a[href=\"/resume-tailor\"]').dataset.page") is None
    )
