"""The Arbeitnow HTML-residue defect, end to end, on a disposable database.

Three paths have to agree on one body, and this file makes each of them show
its work:

* COLLECTION. The collector, over a fake transport serving the archived
  records, stores clean text.
* REBUILD. A corpus holding the DEFECTIVE bodies -- what the old stripper
  wrote -- is repaired by `rebuild-bodies --provider arbeitnow` from the
  archived payloads, with no socket, and the rebuilt body is the collected
  one byte for byte. Rows already in the new shape are not touched and not
  dirtied.
* RESCORE. A repaired row is marked `CONTENT` in the dirty ledger by the
  trigger and its input revision moves. The next targeted pass under the same
  configuration reads exactly those rows in full; a later scoring-only edit,
  which may replay the untouched rows, REFUSES the repaired ones with
  `CONTENT_MOVED` and reads the new advert instead.

Nothing here reaches the vendor: the only transport is a `MockTransport` fed
from `archived_payloads.json`, and the rebuild's own transport raises.
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
from tests.support import committed_config_dir

from career_agent.config.search_config import load_search_config
from career_agent.net.fetcher import HttpFetcher
from career_agent.pipeline.arbeitnow_collect import ArbeitnowCollector
from career_agent.pipeline.rebuild_bodies import rebuild_bodies
from career_agent.pipeline.rescore import rescore
from career_agent.providers.arbeitnow import API_URL, PER_PAGE, advert_text, to_stub
from career_agent.storage.db import connect, migrate, transaction
from career_agent.storage.repositories import JobRawRepo

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "providers" / "arbeitnow"
ARCHIVED = json.loads((FIXTURES / "archived_payloads.json").read_text(encoding="utf-8"))
RECORDS = {name: case["record"] for name, case in ARCHIVED["cases"].items()}
ESCAPED = ("escaped_shortest", "escaped_with_list_and_nbsp")
TAG = re.compile(r"<[a-zA-Z/][^>]{0,40}>")
CONFIG_DIR = committed_config_dir()


def _residue(text: str | None) -> int:
    return len(TAG.findall(text or ""))


def _legacy_strip(html: str) -> str:
    """The body the adapter stored until 2026-09-19. See the unit tests."""
    text = re.sub(r"(?i)<br\s*/?>", "\n", html)
    text = re.sub(r"(?i)</(p|div|li|ul|ol|h[1-6]|tr)>", "\n", text)
    text = re.sub(r"(?i)<li[^>]*>", "\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    for entity, char in (
        ("&nbsp;", " "),
        ("&amp;", "&"),
        ("&lt;", "<"),
        ("&gt;", ">"),
        ("&quot;", '"'),
        ("&#39;", "'"),
    ):
        text = text.replace(entity, char)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = "\n".join(line.strip() for line in text.split("\n"))
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _feed_fetcher() -> HttpFetcher:
    """One page holding every archived record, then the end of the feed."""
    body = {
        "data": list(RECORDS.values()),
        "links": {"next": None},
        "meta": {"per_page": PER_PAGE, "terms": "This is a free public API for jobs."},
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url).startswith(API_URL), request.url
        return httpx.Response(200, json=body)

    return HttpFetcher(
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        request_delay_seconds=0.0,
        backoff_seconds=0.0,
        sleep=lambda _s: None,
    )


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    connection = connect(tmp_path / "arbeitnow.db")
    migrate(connection)
    yield connection
    connection.close()


def _bodies(conn: sqlite3.Connection) -> dict[str, tuple[str | None, str | None]]:
    rows = conn.execute(
        "SELECT j.external_id AS e, j.content_hash AS h, r.description_text AS t FROM job j"
        " LEFT JOIN job_raw r ON r.content_hash = j.content_hash WHERE j.provider = 'arbeitnow'"
    ).fetchall()
    return {str(r["e"]): (r["h"], r["t"]) for r in rows}


def _dirty(conn: sqlite3.Connection) -> dict[str, str]:
    rows = conn.execute(
        "SELECT j.external_id AS e, d.reason AS r FROM job_dirty d JOIN job j ON j.id = d.job_id"
    ).fetchall()
    return {str(r["e"]): str(r["r"]) for r in rows}


def _revisions(conn: sqlite3.Connection) -> dict[str, int]:
    rows = conn.execute(
        "SELECT j.external_id AS e, v.revision AS r"
        " FROM job_input_revision v JOIN job j ON j.id = v.job_id"
    ).fetchall()
    return {str(r["e"]): int(r["r"]) for r in rows}


def _external(name: str) -> str:
    return f"arbeitnow-{RECORDS[name]['slug']}"


def _corrupt_as_the_old_adapter_did(conn: sqlite3.Connection, names: tuple[str, ...]) -> None:
    """Put the named rows back into the shape the old stripper stored."""
    raws = JobRawRepo(conn)
    with transaction(conn):
        for name in names:
            legacy = _legacy_strip(RECORDS[name]["description"])
            assert _residue(legacy) >= 5, f"{name}: the defective shape did not reproduce"
            digest = raws.put(legacy, RECORDS[name]["description"])
            conn.execute(
                "UPDATE job SET content_hash = ? WHERE external_id = ?",
                (digest, _external(name)),
            )


# -- collection --------------------------------------------------------------


def test_collection_stores_clean_text_for_every_shape(conn) -> None:
    stats = ArbeitnowCollector(conn, _feed_fetcher()).collect()
    assert stats.failures == []
    assert stats.jobs_new == len(RECORDS)
    assert stats.descriptions_non_empty == len(RECORDS)
    for name, record in RECORDS.items():
        digest, text = _bodies(conn)[_external(name)]
        assert digest is not None, name
        assert _residue(text) == 0, name
        assert text == advert_text(record["description"]), name


def test_collection_archives_the_vendors_record_untouched(conn) -> None:
    ArbeitnowCollector(conn, _feed_fetcher()).collect()
    for name, record in RECORDS.items():
        stored = conn.execute(
            "SELECT p.payload_json FROM job_provider_payload p JOIN job j ON j.id = p.job_id"
            " WHERE j.external_id = ? AND p.provider = 'arbeitnow'",
            (_external(name),),
        ).fetchone()
        assert json.loads(str(stored["payload_json"])) == record, name


# -- rebuild -----------------------------------------------------------------


@pytest.fixture
def corrupted(conn) -> sqlite3.Connection:
    """A corpus as production holds it: collected, scored, the escaped rows
    carrying markup as text, the ledger clear."""
    ArbeitnowCollector(conn, _feed_fetcher()).collect()
    _corrupt_as_the_old_adapter_did(conn, ESCAPED)
    config, _ = load_search_config(CONFIG_DIR)
    stats = rescore(conn, config)
    assert stats.jobs_scored == len(RECORDS)
    assert _dirty(conn) == {}, "the pass should have cleared what it scored"
    for name in ESCAPED:
        assert _residue(_bodies(conn)[_external(name)][1]) >= 5
    return conn


def test_the_dry_run_names_the_work_and_writes_nothing(corrupted) -> None:
    before = _bodies(corrupted)
    stats = rebuild_bodies(corrupted, provider="arbeitnow", dry_run=True)
    assert stats.jobs_inspected == len(RECORDS)
    assert stats.jobs_body_changed == len(ESCAPED)
    assert stats.jobs_unchanged == len(RECORDS) - len(ESCAPED)
    assert stats.jobs_payload_yields_nothing == 0
    assert stats.jobs_without_payload == 0
    assert _bodies(corrupted) == before
    assert _dirty(corrupted) == {}


def test_the_rebuild_repairs_exactly_the_affected_rows(corrupted) -> None:
    before = _bodies(corrupted)
    stats = rebuild_bodies(corrupted, provider="arbeitnow")
    after = _bodies(corrupted)

    assert stats.jobs_body_changed == len(ESCAPED)
    assert stats.jobs_unchanged == len(RECORDS) - len(ESCAPED)
    assert stats.jobs_payload_yields_nothing == 0
    for name in RECORDS:
        external = _external(name)
        if name in ESCAPED:
            assert after[external][0] != before[external][0], f"{name}: hash did not move"
            assert _residue(after[external][1]) == 0, name
        else:
            assert after[external] == before[external], f"{name}: an unaffected row moved"
    # Every rebuilt body is the collected one, byte for byte.
    for name in ESCAPED:
        assert after[_external(name)][1] == advert_text(RECORDS[name]["description"])


def test_the_rebuild_never_shortens_a_body_into_nothing(corrupted) -> None:
    rebuild_bodies(corrupted, provider="arbeitnow")
    for _digest, text in _bodies(corrupted).values():
        assert text and text.strip()


def test_the_second_rebuild_moves_nothing(corrupted) -> None:
    rebuild_bodies(corrupted, provider="arbeitnow")
    snapshot = _bodies(corrupted)
    again = rebuild_bodies(corrupted, provider="arbeitnow")
    assert again.jobs_body_changed == 0
    assert again.jobs_unchanged == len(RECORDS)
    assert _bodies(corrupted) == snapshot


def test_the_rebuild_touches_no_other_provider(corrupted) -> None:
    """The provider filter is the join. A second provider's rows, defective or
    not, are not this rebuild's business."""
    from career_agent.domain.enums import CollectionStatus
    from career_agent.storage.records import CompanyRecord, JobRecord, SourceBoardRecord
    from career_agent.storage.repositories import CompanyRepo, JobRepo, SourceBoardRepo

    with transaction(corrupted):
        company_id = CompanyRepo(corrupted).upsert(CompanyRecord(slug="other", name="Other"))
        board_id = SourceBoardRepo(corrupted).upsert(
            SourceBoardRecord(company_id=company_id, provider="greenhouse", board_identifier="o")
        )
        digest = JobRawRepo(corrupted).put("<p>markup left as text</p>")
        JobRepo(corrupted).upsert_seen(
            JobRecord(
                company_id=company_id,
                source_board_id=board_id,
                provider="greenhouse",
                external_id="gh-1",
                url="https://boards.greenhouse.io/o/jobs/1",
                title="Other",
                content_hash=digest,
            ),
            status=CollectionStatus.NORMALISED,
        )
    stats = rebuild_bodies(corrupted, provider="arbeitnow")
    assert stats.jobs_inspected == len(RECORDS)
    row = corrupted.execute("SELECT content_hash FROM job WHERE external_id = 'gh-1'").fetchone()
    assert row["content_hash"] == digest


# -- the ledger and the rescore --------------------------------------------------


def test_a_repaired_row_is_marked_content_and_only_it(corrupted) -> None:
    revisions_before = _revisions(corrupted)
    rebuild_bodies(corrupted, provider="arbeitnow")
    marks = _dirty(corrupted)
    assert marks == {_external(name): "CONTENT" for name in ESCAPED}, marks
    revisions_after = _revisions(corrupted)
    for name in RECORDS:
        external = _external(name)
        if name in ESCAPED:
            assert revisions_after[external] > revisions_before[external], name
        else:
            assert revisions_after[external] == revisions_before[external], name


def test_the_targeted_pass_reads_a_repaired_row_in_full(corrupted) -> None:
    """Same configuration version: the ledger names the two repaired rows, the
    pass reads their new adverts, and the scores carry the repaired hash.
    Replay is a cross-version mechanism and is not consulted here."""
    config, _ = load_search_config(CONFIG_DIR)
    rebuild_bodies(corrupted, provider="arbeitnow")
    stats = rescore(corrupted, config)
    assert stats.jobs_scored == len(ESCAPED)
    assert stats.jobs_read_in_full == len(ESCAPED)
    assert stats.jobs_replayed == 0
    assert _dirty(corrupted) == {}
    for name in ESCAPED:
        digest, _text = _bodies(corrupted)[_external(name)]
        row = corrupted.execute(
            "SELECT m.content_hash FROM job_match m JOIN job j ON j.id = m.job_id"
            " WHERE j.external_id = ? AND m.config_version = ?",
            (_external(name), int(config.config_version)),
        ).fetchone()
        assert row["content_hash"] == digest


def test_a_scoring_edit_after_the_repair_refuses_to_replay_the_repaired_rows(corrupted) -> None:
    """A stored reading of markup-as-text is a reading of a DIFFERENT advert.
    When a scoring-only edit makes replay possible, the three untouched rows
    replay from their stored readings and the two repaired rows are refused
    with `CONTENT_MOVED` and read in full."""
    config, _ = load_search_config(CONFIG_DIR)
    rebuild_bodies(corrupted, provider="arbeitnow")
    data = config.model_dump()
    data["config_version"] = config.config_version + 1
    component = data["scoring"]["components"]["responsibilities"]
    component["max"] = component["max"] / 2
    component["weights"] = {k: v / 2 for k, v in component["weights"].items()}
    edited = type(config).model_validate(data)

    stats = rescore(corrupted, edited)
    assert stats.jobs_scored == len(RECORDS)
    assert stats.jobs_replayed == len(RECORDS) - len(ESCAPED)
    assert stats.jobs_read_in_full == len(ESCAPED)
    assert stats.replay_refused == {"CONTENT_MOVED": len(ESCAPED)}, stats.replay_refused


def test_an_unaffected_row_is_not_rescored_by_the_repair(corrupted) -> None:
    config, _ = load_search_config(CONFIG_DIR)
    rebuild_bodies(corrupted, provider="arbeitnow")
    stats = rescore(corrupted, config)
    assert stats.jobs_scored == len(ESCAPED)
    again = rescore(corrupted, config)
    assert again.jobs_scored == 0


# -- the rebuild's own transport ---------------------------------------------


def test_the_rebuild_opens_no_socket_for_this_provider(corrupted, monkeypatch) -> None:
    """Belt and braces beside `test_rebuild_bodies_is_offline.py`: with every
    transport severed, the Arbeitnow rebuild still completes, because the
    adapter reads the archived payload and nothing else."""
    import socket

    def refuse(*_args, **_kwargs):
        raise AssertionError("a rebuild opened a socket")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    stats = rebuild_bodies(corrupted, provider="arbeitnow")
    assert stats.errors == 0
    assert stats.jobs_body_changed == len(ESCAPED)


def test_to_stub_and_the_archive_agree_on_the_payload() -> None:
    for record in RECORDS.values():
        stub = to_stub(record)
        assert stub is not None
        assert stub.payload == record
