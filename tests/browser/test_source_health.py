"""Settings & Sources shows source health and a "Refresh due sources" button,
from the same rows the sidebar counts. Opening the panel starts nothing."""

from __future__ import annotations

from tests.browser.chrome import Chrome
from tests.browser.test_browser_acceptance import open_settings


def test_the_health_table_and_its_button(page: Chrome, pristine_server: str) -> None:
    page.navigate(pristine_server)
    open_settings(page)
    page.wait_for("document.querySelector('#sources-host .src__health')", message="source health")
    assert page.evaluate("document.getElementById('health-head').textContent") == "Source health"
    rows = int(page.evaluate("document.querySelectorAll('.src__health-table tbody tr').length"))
    cards = int(
        page.evaluate("document.querySelectorAll('#sources-host .src__each .career__card').length")
    )
    assert rows == cards, "the table and the cards are the same rows"
    # Every state is a sentence, never an identifier.
    labels = page.evaluate(
        "Array.from(document.querySelectorAll('.src__health-table td strong'))"
        ".map((n) => n.textContent)"
    )
    assert all(label and "_" not in label for label in labels), labels
    # Opening the panel ran nothing.
    running = page.evaluate("fetch('/api/retrieval').then((r) => r.json()).then((p) => p.running)")
    assert running is False
    page.evaluate("document.getElementById('refresh-due').click()")
    # The demo never collects: the refusal is said on screen, not only in a console.
    page.wait_for(
        "document.getElementById('health-notice').textContent.length > 0"
        " && !document.getElementById('health-notice').textContent.includes('Checking')",
        message="the refresh outcome on screen",
    )
    text = str(page.evaluate("document.getElementById('health-notice').textContent"))
    assert "demo" in text.lower() or "nothing is due" in text.lower(), text
