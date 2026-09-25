"""The guided setup a fresh install opens on, in a real browser.

One question per card. These tests hold it to what it promises a person who has
never seen the product: it opens by itself only on a first run, every answer is
saved through the existing whitelisted routes the moment Continue is pressed,
Back shows what was answered, the keyboard can drive it, where somebody lives is
never turned into where they may be hired, and "Find jobs now" runs the same
collectors each source's own button runs -- replaced here, so nothing leaves the
machine.
"""

from __future__ import annotations

import json
import shutil
import socket
import threading
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest
from tests.browser.chrome import Chrome

from career_agent.config.search_config import load_search_config
from career_agent.runtime import RuntimeMode, stamp_identity
from career_agent.storage.db import connect, migrate, transaction
from career_agent.web.api import JobsApi
from career_agent.web.server import ServerConfig, build_server

REPO = Path(__file__).resolve().parents[2]


@dataclass
class Fresh:
    base: str
    app: JobsApi
    config_dir: Path


@pytest.fixture
def fresh(tmp_path: Path) -> Iterator[Fresh]:
    """A personal database with nothing in it and the shipped config, no local file."""
    config_dir = tmp_path / "config"
    shutil.copytree(REPO / "config", config_dir, ignore=shutil.ignore_patterns("*.local.*"))
    db = tmp_path / "personal.db"
    conn = connect(db)
    try:
        migrate(conn)
        with transaction(conn):
            stamp_identity(conn, RuntimeMode.PERSONAL, "guided setup")
    finally:
        conn.close()
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = int(probe.getsockname()[1])
    probe.close()
    app = JobsApi(ServerConfig(db_path=db, config_dir=config_dir, port=port), quiet=True)
    httpd = build_server(app)
    httpd.handle_error = lambda request, client_address: None  # type: ignore[method-assign]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield Fresh(f"http://127.0.0.1:{port}", app, config_dir)
    finally:
        httpd.shutdown()
        thread.join(timeout=5)


def _card(page: Chrome) -> str:
    return str(page.evaluate("document.querySelector('.setup__card')?.dataset.step || ''"))


def _wait_card(page: Chrome, key: str) -> None:
    page.wait_for(
        f"document.querySelector('.setup__card')?.dataset.step === {json.dumps(key)}",
        message=f"the {key} card",
    )


def _click(page: Chrome, selector: str) -> None:
    page.evaluate(f"document.querySelector({json.dumps(selector)}).click()")


def _value(page: Chrome, selector: str, value: str, event: str = "input") -> None:
    page.evaluate(
        f"(() => {{ const n = document.querySelector({json.dumps(selector)});"
        f" n.value = {json.dumps(value)};"
        f" n.dispatchEvent(new Event({json.dumps(event)}, {{bubbles: true}})); }})()"
    )


def _open(page: Chrome, base: str) -> None:
    page.set_viewport(1280, 900)
    page.navigate(base)
    _wait_card(page, "welcome")


def test_a_fresh_install_opens_on_the_setup_not_on_six_steps(page: Chrome, fresh: Fresh) -> None:
    _open(page, fresh.base)
    assert page.evaluate("document.querySelectorAll('.firstrun__step').length") == 0
    header = str(page.evaluate("document.querySelector('#pagehead-title').innerText"))
    assert header == "Set up your search", header
    assert page.console_errors() == []


def test_enter_advances_and_focus_moves_to_each_question(page: Chrome, fresh: Fresh) -> None:
    """Continue is the form's submit button, so Enter in the card submits it.

    `requestSubmit()` is the browser's own implicit submission -- what Enter in
    a field or on the button does. The harness's key events are raw key downs,
    which never produce the character a submission waits for.
    """
    _open(page, fresh.base)
    assert page.evaluate("document.querySelector('#setup-next').type") == "submit"
    assert page.evaluate(
        "document.querySelector('#setup-next').form.classList.contains('setup__card')"
    )
    page.evaluate("document.querySelector('.setup__card').requestSubmit()")
    _wait_card(page, "work")
    assert page.evaluate("document.activeElement.id") == "setup-title"
    assert str(page.evaluate("document.querySelector('.setup__count').innerText")) == "Step 1"


def test_a_missing_answer_is_explained_next_to_the_question(page: Chrome, fresh: Fresh) -> None:
    _open(page, fresh.base)
    _click(page, "#setup-next")
    _wait_card(page, "work")
    _click(page, "#setup-next")
    page.wait_for("document.querySelector('#setup-error').textContent.length > 0")
    assert page.evaluate("document.querySelector('#setup-error').getAttribute('role')") == "alert"
    assert "setup-error" in str(
        page.evaluate("document.querySelector('#setup-work').getAttribute('aria-describedby')")
    )
    assert (
        page.evaluate("document.querySelector('#setup-work').getAttribute('aria-invalid')")
        == "true"
    )
    assert _card(page) == "work", "an empty answer must not advance"


def test_answers_save_as_you_go_and_back_shows_them(page: Chrome, fresh: Fresh) -> None:
    _open(page, fresh.base)
    _click(page, "#setup-next")
    _wait_card(page, "work")
    _value(page, "#setup-work", "customer onboarding")
    _click(page, "#setup-next")
    _wait_card(page, "roles")
    _click(page, "#setup-skip")
    _wait_card(page, "home")
    config, _ = load_search_config(fresh.config_dir)
    assert config.lexicon, "the work phrases were not saved on Continue"

    _value(page, "#setup-country", "BR", "change")
    _click(page, "#setup-next")
    _wait_card(page, "hire")
    config, _ = load_search_config(fresh.config_dir)
    assert config.eligibility.candidate_country == "BR"

    _click(page, "#setup-back")
    _wait_card(page, "home")
    # The country is shown by name and stored by code.
    assert page.evaluate("document.querySelector('#setup-country').value") == "Brazil"
    assert page.evaluate("document.querySelector('#setup-country').dataset.code") == "BR"


def test_where_you_live_is_never_turned_into_where_you_can_be_hired(
    page: Chrome, fresh: Fresh
) -> None:
    """Residence is not proof an employer can hire somebody there. It is asked."""
    _open(page, fresh.base)
    _click(page, "#setup-next")
    _wait_card(page, "work")
    _click(page, "#setup-skip")
    _wait_card(page, "roles")
    _click(page, "#setup-skip")
    _wait_card(page, "home")
    _value(page, "#setup-country", "BR", "change")
    _click(page, "#setup-next")
    _wait_card(page, "hire")
    assert not page.evaluate("document.querySelector('#setup-hire-yes').checked")
    _click(page, "#setup-hire-unsure")
    _click(page, "#setup-next")
    _wait_card(page, "regions")
    config, _ = load_search_config(fresh.config_dir)
    assert config.eligibility.candidate_country == "BR"
    assert config.eligibility.eligible_countries == [], "residence was promoted to eligibility"

    _click(page, "#setup-back")
    _wait_card(page, "hire")
    _click(page, "#setup-hire-yes")
    _click(page, "#setup-next")
    # Brazil confirmed: every region containing it already admits, so the
    # regions card has nothing to ask and is not shown.
    _wait_card(page, "workmodel")
    config, _ = load_search_config(fresh.config_dir)
    assert config.eligibility.eligible_countries == ["BR"]


def test_do_this_later_leaves_and_settings_brings_it_back(page: Chrome, fresh: Fresh) -> None:
    _open(page, fresh.base)
    _click(page, "#setup-later")
    page.wait_for("document.querySelectorAll('.firstrun__step').length === 5")
    page.reload()
    page.wait_for("document.querySelectorAll('.firstrun__step').length === 5")
    assert not page.evaluate("Boolean(document.querySelector('.setup__card'))")

    _click(page, '.topnav__link[data-page="settings"]')
    page.wait_for("document.querySelector('#settings-open-setup')")
    _click(page, "#settings-open-setup")
    _wait_card(page, "welcome")


def test_find_jobs_runs_every_source_through_its_own_work_offline(
    page: Chrome, fresh: Fresh, monkeypatch: pytest.MonkeyPatch
) -> None:
    ran: list[str] = []

    def recorder(key: str):
        def work(state, cancel) -> None:
            ran.append(key)

        return work

    monkeypatch.setattr(
        fresh.app, "_collect_work", lambda limit, provider=None: recorder(f"collect:{provider}")
    )
    monkeypatch.setattr(
        "career_agent.web.source_refresh.feed_work", lambda db, stage, **_: recorder(stage)
    )
    monkeypatch.setattr(
        "career_agent.web.source_refresh.employer_board_work",
        lambda app, families: recorder("employer-boards"),
    )
    monkeypatch.setattr(fresh.app.rescore, "start", lambda work, run_id: None)

    _open(page, fresh.base)
    _click(page, "#setup-later")
    page.wait_for("document.querySelector('#firstrun-find-jobs')")
    _click(page, "#firstrun-find-jobs")
    _wait_card(page, "ready")
    _click(page, "#setup-find")
    page.wait_for("document.querySelector('#setup-see')", timeout=20)
    text = str(page.evaluate("document.querySelector('.setup__find').innerText"))
    assert "Done" in text, text
    assert ran and len(ran) == len(set(ran)), ran


def test_the_setup_speaks_portuguese_and_fits_a_phone(page: Chrome, fresh: Fresh) -> None:
    _open(page, fresh.base)
    page.evaluate("document.querySelector('[data-locale=\"pt-BR\"]').click()")
    page.wait_for("document.querySelector('.setup__title').innerText.includes('Vamos')")
    page.set_viewport(390, 844, mobile=True)
    _click(page, "#setup-next")
    _wait_card(page, "work")
    assert str(page.evaluate("document.querySelector('.setup__count').innerText")) == "Passo 1"
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
    page.evaluate("document.querySelector('[data-locale=\"en\"]').click()")


def test_changing_where_you_live_does_not_carry_the_hiring_answer_over(
    page: Chrome, fresh: Fresh
) -> None:
    """A "Yes" given for one country is not a "Yes" for the next one.

    Answering Yes for Brazil, going back and choosing Portugal as the country
    you live in, must not arrive at the hiring question with Yes already
    ticked for Portugal -- pressing Continue would then record an eligibility
    nobody gave, and drop the one that was given.
    """
    _open(page, fresh.base)
    _click(page, "#setup-next")
    _wait_card(page, "work")
    _click(page, "#setup-skip")
    _wait_card(page, "roles")
    _click(page, "#setup-skip")
    _wait_card(page, "home")
    _value(page, "#setup-country", "BR", "change")
    _click(page, "#setup-next")
    _wait_card(page, "hire")
    _click(page, "#setup-hire-yes")
    _click(page, "#setup-next")
    _wait_card(page, "workmodel")

    _click(page, "#setup-back")
    _wait_card(page, "hire")
    _click(page, "#setup-back")
    _wait_card(page, "home")
    _value(page, "#setup-country", "PT", "change")
    _click(page, "#setup-next")
    _wait_card(page, "hire")
    assert "Portugal" in str(page.evaluate("document.querySelector('.setup__legend').innerText"))
    assert not page.evaluate("document.querySelector('#setup-hire-yes').checked")
    _click(page, "#setup-next")
    _wait_card(page, "regions")
    config, _ = load_search_config(fresh.config_dir)
    assert config.eligibility.candidate_country == "PT"
    assert config.eligibility.eligible_countries == ["BR"]
