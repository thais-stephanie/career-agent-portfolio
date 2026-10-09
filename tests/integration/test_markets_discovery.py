"""The markets she names order discovery; where she lives does not (ADR-0032).

No network: every source's work is a recorder (the same one the refresh-plan
suite uses), and each profile has its own database and its own settings.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from tests.integration.test_source_refresh_plan import Recorder, _key, _providers, _rows, _run
from tests.support import committed_config_dir

from career_agent.runtime import RuntimeMode, stamp_identity
from career_agent.storage.db import connect, migrate, transaction
from career_agent.web.api import JobsApi
from career_agent.web.server import ServerConfig


def _profile(tmp_path: Path, name: str) -> JobsApi:
    config_dir = tmp_path / name / "config"
    shutil.copytree(committed_config_dir(), config_dir)
    db = tmp_path / name / "personal.db"
    conn = connect(db)
    migrate(conn)
    with transaction(conn):
        stamp_identity(conn, RuntimeMode.PERSONAL, name)
    conn.close()
    return JobsApi(ServerConfig(db_path=db, config_dir=config_dir), quiet=True)


def _markets(api: JobsApi, markets: list[str]) -> dict:
    return api.handle_api("PATCH", "/api/profile", {}, {"changes": {"target_markets": markets}})


@pytest.fixture
def api(tmp_path: Path) -> JobsApi:
    return _profile(tmp_path, "one")


def _regions(api: JobsApi) -> dict[str, str]:
    from career_agent.sources.health import health

    with connect(api.config.db_path) as conn:
        entries = health(conn, catalogue_path=api.config.config_dir / "source_catalogue.yaml")
    return {e.source.id: e.source.region for e in entries}


def test_choosing_markets_asks_for_no_recalculation(api: JobsApi) -> None:
    version = api.search_config().config_version
    answer = _markets(api, ["EMEA"])
    assert answer["rescore_required"] is False
    assert answer["config_version"] == version
    assert "unchanged" in answer["note"]
    # A change the matcher reads still asks for one.
    answer = api.handle_api(
        "PATCH", "/api/profile", {}, {"changes": {"compensation_currency": "JPY"}}
    )
    assert answer["rescore_required"] is True and answer["config_version"] == version + 1


def test_without_markets_nothing_is_paused_and_worldwide_boards_go_first(api: JobsApi) -> None:
    _markets(api, [])
    rows = _rows(api)
    assert not [sid for sid, row in rows.items() if row["state"] == "PAUSED"], (
        "a source was paused that nobody paused"
    )
    regions = _regions(api)
    order = api.source_priority(_entries(api))
    worldwide = [order[s] for s, r in regions.items() if (r or "global") == "global"]
    regional = [order[s] for s, r in regions.items() if (r or "global") != "global"]
    assert max(worldwide) < min(regional)


@pytest.mark.parametrize("market", ["EMEA", "EU"])
def test_a_chosen_market_goes_first_and_the_rest_still_run(
    api: JobsApi, monkeypatch, market: str
) -> None:
    _markets(api, [market])
    regions = _regions(api)
    order = api.source_priority(_entries(api))
    european = [s for s, r in regions.items() if r == "eu"]
    assert european and all(order[s] == 0 for s in european)
    assert all(order[s] > 0 for s, r in regions.items() if r != "eu")

    recorder = Recorder(api, monkeypatch)
    _run(api, "refresh-all")
    providers = _providers(api)
    ran = recorder.ran
    feeds = [_key(providers[s]) for s in european if providers.get(s) and _key(providers[s]) in ran]
    assert feeds, "the European feed did not run"
    others = [k for k in ran if k not in feeds and not k.startswith("collect:")]
    assert others, "every other source stopped running"
    first_other = min(ran.index(k) for k in others if k != "employer-boards")
    assert max(ran.index(k) for k in feeds) < first_other


@pytest.mark.parametrize("market", ["EMEA", "EU", "EUROPE"])
def test_an_explicit_pause_survives_any_market(api: JobsApi, monkeypatch, market: str) -> None:
    """The European feed (Arbeitnow in the committed catalogue) stays paused
    whichever European scope she names: only she can un-pause it."""
    regions = _regions(api)
    european = next(s for s, r in regions.items() if r == "eu")
    api.handle_api("PATCH", "/api/sources/schedule", {}, {"source_id": european, "mode": "PAUSED"})
    _markets(api, [market])
    assert _rows(api)[european]["state"] == "PAUSED"
    recorder = Recorder(api, monkeypatch)
    _run(api, "refresh-all")
    assert _key(_providers(api)[european]) not in recorder.ran


def test_one_profiles_markets_and_pauses_do_not_reach_another(tmp_path: Path) -> None:
    first, second = _profile(tmp_path, "first"), _profile(tmp_path, "second")
    regions = _regions(first)
    european = next(s for s, r in regions.items() if r == "eu")
    _markets(first, ["EMEA"])
    first.handle_api(
        "PATCH", "/api/sources/schedule", {}, {"source_id": european, "mode": "PAUSED"}
    )

    assert first.source_priority(_entries(first))[european] == 0
    assert second.source_priority(_entries(second))[european] > 0
    assert _rows(second)[european]["state"] != "PAUSED"


def _entries(api: JobsApi) -> list:
    from career_agent.sources.health import health

    with connect(api.config.db_path) as conn:
        return list(health(conn, catalogue_path=api.config.config_dir / "source_catalogue.yaml"))
