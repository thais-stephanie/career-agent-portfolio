"""An aggregator must not double-count a corpus, and must not degrade it.

The scenario every test here builds is the one that matters: a posting we
already hold from the employer's own ATS, republished by an aggregator. The
right outcome is one job row, unchanged, plus a recorded second sighting.

Everything is offline. The feed is a scripted transport whose responses the test
decides, so the deduplication is exercised without a single real request.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest

from career_agent.net.fetcher import HttpFetcher
from career_agent.pipeline.speedrun_collect import (
    MATCH_CANONICAL_URL,
    MATCH_CONTENT_HASH,
    MATCH_EXTERNAL_ID,
    MATCH_ORIGIN_URL,
    PROVIDER,
    ContractRefused,
    SpeedrunCollector,
)
from career_agent.storage.db import connect, migrate, transaction
from career_agent.storage.records import CompanyRecord, JobRecord, SourceBoardRecord
from career_agent.storage.repositories import (
    CompanyRepo,
    DiscoverySourceRepo,
    JobRawRepo,
    JobRepo,
    SourceBoardRepo,
)

ASHBY_ID = "228d2bfe-cada-406a-a4c0-99c4ec13a242"
ASHBY_URL = f"https://jobs.ashbyhq.com/Abridge/{ASHBY_ID}"
ATS_DESCRIPTION = "Own the internal platform. Deep EMR integrations and clinical tooling."


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    connection = connect(tmp_path / "career.db")
    migrate(connection)
    yield connection
    connection.close()


def seed_ats_job(
    conn: sqlite3.Connection,
    external_id: str = ASHBY_ID,
    url: str = ASHBY_URL,
    description: str = ATS_DESCRIPTION,
    provider: str = "ashby",
) -> str:
    """One posting already collected from the employer's own board."""
    with transaction(conn):
        company_id = CompanyRepo(conn).upsert(CompanyRecord(slug="abridge", name="Abridge"))
        board_id = SourceBoardRepo(conn).upsert(
            SourceBoardRecord(company_id=company_id, provider=provider, board_identifier="Abridge")
        )
        content_hash = JobRawRepo(conn).put(description)
        return JobRepo(conn).upsert_seen(
            JobRecord(
                company_id=company_id,
                source_board_id=board_id,
                provider=provider,
                external_id=external_id,
                url=url,
                title="Staff IT Engineer",
                content_hash=content_hash,
            )
        )


def listing(**overrides: Any) -> dict[str, Any]:
    base = {
        "id": ASHBY_ID,
        "title": "Staff IT Engineer",
        "company": "Abridge",
        "company_slug": "abridge",
        "company_url": "https://speedrun-talent-network.com/companies/abridge",
        "url": "https://speedrun-talent-network.com/jobs/staff-it-engineer-abridge-228d2bfe",
        "location": "SF Office",
        "workplace_type": "Hybrid",
        "employment_type": "FullTime",
        "function": "engineering",
        "seniority": "staff",
        "remote": False,
        "comp_min": 200000,
        "comp_max": 240000,
        "comp_currency": "USD",
        "comp_period": None,
        "published_at": "2026-09-05T01:23:24.024+00:00",
        "stealth": False,
        "cohort": None,
        "tier": "a16z",
    }
    base.update(overrides)
    return base


def detail(entry: dict[str, Any], apply_url: str | None = ASHBY_URL, text: str = "") -> dict:
    body = dict(entry)
    body["status"] = "open"
    body["comp_summary"] = "$200K to $240K"
    body["apply"] = {"kind": "external", "url": apply_url} if apply_url else None
    body["description_text"] = text or "A republished copy of the posting text."
    return body


class ScriptedFeed:
    """One page of listings plus their details, served over a mock transport."""

    def __init__(self, entries: list[dict[str, Any]], details: dict[str, dict] | None = None):
        self.entries = entries
        self.details = details or {}
        self.requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if path.endswith("/openapi.json"):
            raise AssertionError("the contract check should be disabled in these tests")
        if "/jobs/" in path:
            job_id = path.rsplit("/", 1)[-1]
            return httpx.Response(200, json={"job": self.details[job_id]})
        page = int(request.url.params.get("page", "0"))
        entries = self.entries if page == 0 else []
        return httpx.Response(
            200,
            json={
                "jobs": entries,
                "total": len(self.entries),
                "page": page,
                "page_size": 50,
                "total_pages": 1,
                "source": "career-agent",
            },
        )

    @property
    def detail_requests(self) -> list[httpx.Request]:
        return [r for r in self.requests if "/jobs/" in r.url.path]


def collector(conn: sqlite3.Connection, feed: ScriptedFeed) -> SpeedrunCollector:
    fetcher = HttpFetcher(
        client=httpx.Client(transport=httpx.MockTransport(feed.handler)),
        request_delay_seconds=0.0,
        max_attempts=2,
        backoff_seconds=0.0,
        sleep=lambda _s: None,
    )
    return SpeedrunCollector(conn, fetcher)


def job_count(conn: sqlite3.Connection) -> int:
    return int(conn.execute("SELECT COUNT(*) FROM job").fetchone()[0])


# =========================================================================
# the four deterministic rules
# =========================================================================


def test_a_posting_we_already_hold_by_id_costs_no_detail_request(conn) -> None:
    """Rule 1, and it is the reason a repeat run is cheap.

    This aggregator mints its job id from the source posting's id, so a posting
    already in the corpus is recognised before anything is fetched.
    """
    job_id = seed_ats_job(conn)
    feed = ScriptedFeed([listing()])
    stats = collector(conn, feed).collect(max_pages=1, check_contract=False)

    assert stats.duplicates == {MATCH_EXTERNAL_ID: 1}
    assert stats.jobs_new == 0
    assert job_count(conn) == 1
    assert feed.detail_requests == [], "a known posting must not cost a detail fetch"

    sightings = DiscoverySourceRepo(conn).for_job(job_id)
    assert len(sightings) == 1
    assert sightings[0]["source"] == PROVIDER
    assert sightings[0]["matched_by"] == MATCH_EXTERNAL_ID


def test_the_employer_apply_link_resolves_a_posting_whose_id_differs(conn) -> None:
    """Rule 2, the authoritative one.

    The aggregator gives the posting its own id, so rule 1 cannot fire. The
    apply link still points at the employer's ATS, and the ATS adapter
    recognises its own URL.
    """
    job_id = seed_ats_job(conn)
    entry = listing(id="feed-only-identifier-0001")
    feed = ScriptedFeed([entry], {entry["id"]: detail(entry)})
    stats = collector(conn, feed).collect(max_pages=1, check_contract=False)

    assert stats.duplicates == {MATCH_ORIGIN_URL: 1}
    assert job_count(conn) == 1
    assert len(feed.detail_requests) == 1

    sighting = DiscoverySourceRepo(conn).for_job(job_id)[0]
    assert sighting["matched_by"] == MATCH_ORIGIN_URL
    assert sighting["origin_url"] == ASHBY_URL


def test_a_link_no_adapter_claims_still_matches_on_the_url_string(conn) -> None:
    """Rule 3, for a posting collected from a system we have no adapter for.

    The two URLs differ in host case, in a trailing slash and in a query string,
    and are the same posting. They do NOT differ in path case, because
    `canonical_url` deliberately leaves the path alone: paths are
    case-sensitive by RFC 3986, and lowercasing one vendor's would be asserting
    that two different paths are one posting for every other.
    """
    stored = "https://careers.example.com/roles/staff-it-engineer"
    job_id = seed_ats_job(conn, external_id="manual-1", url=stored, provider="manual_import")
    entry = listing(id="feed-only-identifier-0002")
    published = "https://CAREERS.EXAMPLE.COM/roles/staff-it-engineer/?utm_source=career-agent"
    feed = ScriptedFeed([entry], {entry["id"]: detail(entry, apply_url=published)})
    stats = collector(conn, feed).collect(max_pages=1, check_contract=False)

    assert stats.duplicates == {MATCH_CANONICAL_URL: 1}
    assert job_count(conn) == 1
    assert DiscoverySourceRepo(conn).for_job(job_id)[0]["matched_by"] == MATCH_CANONICAL_URL


def test_a_path_that_differs_only_in_case_is_not_assumed_to_be_the_same_posting(
    conn,
) -> None:
    """The other half of that decision, asserted so it cannot drift.

    A path is case-sensitive. Treating `/Roles/X` and `/roles/x` as one posting
    would be a guess, and a false duplicate hides a job the person should see.
    """
    seed_ats_job(
        conn,
        external_id="manual-2",
        url="https://careers.example.com/roles/staff-it-engineer",
        provider="manual_import",
    )
    entry = listing(id="feed-only-identifier-0009")
    published = "https://careers.example.com/ROLES/STAFF-IT-ENGINEER"
    feed = ScriptedFeed([entry], {entry["id"]: detail(entry, apply_url=published)})
    stats = collector(conn, feed).collect(max_pages=1, check_contract=False)

    assert stats.duplicates == {}
    assert stats.jobs_new == 1


def test_byte_identical_text_matches_when_no_link_does(conn) -> None:
    """Rule 4, last because it is the only one that can hold across postings.

    Still equality rather than resemblance: the description is the same bytes.
    """
    job_id = seed_ats_job(conn, external_id="other-1", url="https://elsewhere.test/a")
    entry = listing(id="feed-only-identifier-0003")
    feed = ScriptedFeed([entry], {entry["id"]: detail(entry, apply_url=None, text=ATS_DESCRIPTION)})
    stats = collector(conn, feed).collect(max_pages=1, check_contract=False)

    assert stats.duplicates == {MATCH_CONTENT_HASH: 1}
    assert job_count(conn) == 1
    assert DiscoverySourceRepo(conn).for_job(job_id)[0]["matched_by"] == MATCH_CONTENT_HASH


# =========================================================================
# never replace richer data with poorer
# =========================================================================


def test_a_duplicate_never_overwrites_the_employer_record(conn) -> None:
    """The central promise, asserted field by field.

    The aggregator's copy is shorter, its title differs, and its URL is its own.
    None of that may reach the job row.
    """
    job_id = seed_ats_job(conn)
    before = dict(conn.execute("SELECT * FROM job WHERE id = ?", (job_id,)).fetchone())

    entry = listing(id="feed-only-identifier-0004", title="Staff IT Eng (truncated)")
    feed = ScriptedFeed([entry], {entry["id"]: detail(entry, text="Short blurb.")})
    collector(conn, feed).collect(max_pages=1, check_contract=False)

    after = dict(conn.execute("SELECT * FROM job WHERE id = ?", (job_id,)).fetchone())
    for column in ("provider", "external_id", "url", "title", "content_hash", "source_board_id"):
        assert after[column] == before[column], f"{column} was overwritten by the aggregator"

    text = conn.execute(
        "SELECT description_text FROM job_raw WHERE content_hash = ?", (after["content_hash"],)
    ).fetchone()["description_text"]
    assert text == ATS_DESCRIPTION


def test_the_aggregator_never_becomes_the_provider_of_a_posting_it_did_not_originate(
    conn,
) -> None:
    seed_ats_job(conn)
    entry = listing()
    feed = ScriptedFeed([entry], {entry["id"]: detail(entry)})
    collector(conn, feed).collect(max_pages=1, check_contract=False)

    providers = {row[0] for row in conn.execute("SELECT DISTINCT provider FROM job")}
    assert providers == {"ashby"}


# =========================================================================
# a posting only this source has
# =========================================================================


def test_a_posting_nobody_else_carries_is_collected(conn) -> None:
    entry = listing(id="brand-new-0001", company="Novel Labs", company_slug="novel-labs")
    feed = ScriptedFeed([entry], {entry["id"]: detail(entry, apply_url="https://unknown.test/x")})
    stats = collector(conn, feed).collect(max_pages=1, check_contract=False)

    assert stats.jobs_new == 1
    assert stats.duplicates == {}
    row = conn.execute("SELECT * FROM job").fetchone()
    assert row["provider"] == PROVIDER
    assert row["external_id"] == "brand-new-0001"
    assert row["title"] == "Staff IT Engineer"
    assert row["location_raw"] == "SF Office"
    assert row["department"] == "engineering"


def test_each_company_gets_its_own_board_so_attribution_survives(conn) -> None:
    """ADR-0008 through an aggregator.

    Registering the feed as one board would attach every employer's postings to
    a single row and make every per-company number meaningless.
    """
    entries = [
        listing(id="a-1", company="Alpha", company_slug="alpha"),
        listing(id="a-2", company="Alpha", company_slug="alpha"),
        listing(id="b-1", company="Beta", company_slug="beta"),
    ]
    feed = ScriptedFeed(
        entries, {e["id"]: detail(e, apply_url=f"https://unknown.test/{e['id']}") for e in entries}
    )
    stats = collector(conn, feed).collect(max_pages=1, check_contract=False)

    assert stats.jobs_new == 3
    boards = conn.execute(
        "SELECT sb.board_identifier, c.slug, COUNT(j.id) AS n FROM source_board sb"
        " JOIN company c ON c.id = sb.company_id"
        " LEFT JOIN job j ON j.source_board_id = sb.id"
        " WHERE sb.provider = ? GROUP BY sb.id ORDER BY c.slug",
        (PROVIDER,),
    ).fetchall()
    assert [(r["slug"], r["n"]) for r in boards] == [("alpha", 2), ("beta", 1)]


def test_a_stealth_listing_is_collected_under_a_derived_slug(conn) -> None:
    """The company name is masked; the work is still real."""
    entry = listing(id="stealth-1", company="Stealth", company_slug=None, stealth=True)
    feed = ScriptedFeed([entry], {entry["id"]: detail(entry, apply_url="https://unknown.test/s")})
    stats = collector(conn, feed).collect(max_pages=1, check_contract=False)
    assert stats.jobs_new == 1
    assert conn.execute("SELECT slug FROM company").fetchone()["slug"] == "stealth"


def test_running_twice_adds_nothing_and_refreshes_the_sighting(conn) -> None:
    job_id = seed_ats_job(conn)
    feed = ScriptedFeed([listing()])
    collector(conn, feed).collect(max_pages=1, check_contract=False)
    collector(conn, feed).collect(max_pages=1, check_contract=False)

    assert job_count(conn) == 1
    assert len(DiscoverySourceRepo(conn).for_job(job_id)) == 1
    assert DiscoverySourceRepo(conn).counts_by_match(PROVIDER) == {MATCH_EXTERNAL_ID: 1}


# =========================================================================
# reporting, and refusing
# =========================================================================


def test_the_run_reports_where_the_origin_links_pointed(conn) -> None:
    """The measurement M1D section 14 could not make.

    Which systems hold the postings our company registry never reaches is
    answerable from real apply URLs, and is the reason this connector was worth
    building even before its own postings mattered.
    """
    entries = [listing(id=f"n-{i}", company=f"C{i}", company_slug=f"c{i}") for i in range(3)]
    feed = ScriptedFeed(
        entries,
        {
            entries[0]["id"]: detail(entries[0], apply_url="https://jobs.workable.com/x/1"),
            entries[1]["id"]: detail(entries[1], apply_url="https://jobs.workable.com/x/2"),
            entries[2]["id"]: detail(entries[2], apply_url="https://apply.workday.com/y/3"),
        },
    )
    stats = collector(conn, feed).collect(max_pages=1, check_contract=False)
    assert stats.origin_hosts == {"jobs.workable.com": 2, "apply.workday.com": 1}
    assert stats.origin_kinds == {"external": 3}


def test_the_run_reports_what_the_api_would_not_serve(conn) -> None:
    entry = listing(id="x-1", company="X", company_slug="x")

    class Deep(ScriptedFeed):
        def handler(self, request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            if "/jobs/" in request.url.path:
                job_id = request.url.path.rsplit("/", 1)[-1]
                return httpx.Response(200, json={"job": self.details[job_id]})
            return httpx.Response(
                200,
                json={
                    "jobs": [entry],
                    "total": 48129,
                    "page": int(request.url.params.get("page", "0")),
                    "page_size": 50,
                    "total_pages": 963,
                },
            )

    feed = Deep([entry], {entry["id"]: detail(entry, apply_url="https://unknown.test/x")})
    stats = collector(conn, feed).collect(max_pages=1, check_contract=False)
    assert stats.claimed_total == 48129
    assert stats.beyond_reach == 48129 - 201 * 50
    assert stats.stopped_early is True


def test_a_contract_that_no_longer_matches_refuses_before_any_feed_request(conn) -> None:
    """A connector that walks on after a failed contract check writes wrong data."""
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        if request.url.path.endswith("/openapi.json"):
            return httpx.Response(200, json={"openapi": "3.1.0", "paths": {}})
        return httpx.Response(200, json={"jobs": []})

    fetcher = HttpFetcher(
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        request_delay_seconds=0.0,
        max_attempts=1,
        sleep=lambda _s: None,
    )
    with pytest.raises(ContractRefused):
        SpeedrunCollector(conn, fetcher).collect(max_pages=1)
    assert seen == ["/api/v1/openapi.json"], "no feed request may follow a failed contract check"


def test_a_detail_failure_is_counted_and_does_not_end_the_pass(conn) -> None:
    entries = [listing(id=f"n-{i}", company=f"C{i}", company_slug=f"c{i}") for i in range(3)]

    class Flaky(ScriptedFeed):
        def handler(self, request: httpx.Request) -> httpx.Response:
            if "/jobs/n-1" in request.url.path:
                self.requests.append(request)
                return httpx.Response(500, json={"error": {"code": "internal"}})
            return super().handler(request)

    feed = Flaky(
        entries,
        {e["id"]: detail(e, apply_url=f"https://unknown.test/{e['id']}") for e in entries},
    )
    stats = collector(conn, feed).collect(max_pages=1, check_contract=False)
    assert stats.details_failed == 1
    assert stats.jobs_new == 2, "the other two postings were still collected"
    assert stats.failures


# =========================================================================
# What an independent functional review found, measured against the corpus
# =========================================================================


def test_the_same_description_under_a_different_title_is_a_different_job(conn) -> None:
    """Rule 4 used to be the description hash and nothing else.

    Measured on `data/m1d2/career.db`: 1,136 content hashes are shared by more
    than one job, 386 of those span more than one title, and 3,084 jobs of
    21,218 (14.5%) share a hash with something. The worst group is 53 jobs with
    53 DISTINCT titles and one description -- a retailer pasting the same text
    into a posting for each shop:

        "Bilingual Sales Representative (Spanish)"  Toronto
        "Sales Representative - Tecumseh Mall"      Windsor
        "Sales Representative - Masonville Place"   London

    Those are 53 jobs. Under the old rule an incoming posting matching that
    text was filed as a sighting of whichever row SQLite returned first -- and
    with no ORDER BY, not reliably the same row twice -- and then vanished
    from the list. This module's docstring names that as the expensive
    failure: a false duplicate hides a job the person should have seen.
    """
    seed_ats_job(conn, external_id="retail-1", url="https://elsewhere.test/shop-1")
    entry = listing(id="feed-only-identifier-0101", title="Sales Representative - Windsor")
    feed = ScriptedFeed([entry], {entry["id"]: detail(entry, apply_url=None, text=ATS_DESCRIPTION)})
    stats = collector(conn, feed).collect(max_pages=1, check_contract=False)

    assert stats.duplicates == {}, "the same text under another title was called a duplicate"
    assert stats.jobs_new == 1
    assert job_count(conn) == 2


def test_the_same_description_at_a_different_company_is_a_different_job(conn) -> None:
    """The other half of the same correction.

    Agencies and franchises reuse a description across employers. Two
    companies posting the same words are two jobs, and a person applying to
    one has not applied to the other.
    """
    seed_ats_job(conn, external_id="agency-1", url="https://elsewhere.test/agency-1")
    entry = listing(
        id="feed-only-identifier-0102",
        company="Northwind Systems",
        company_slug="northwind-systems",
    )
    feed = ScriptedFeed([entry], {entry["id"]: detail(entry, apply_url=None, text=ATS_DESCRIPTION)})
    stats = collector(conn, feed).collect(max_pages=1, check_contract=False)

    assert stats.duplicates == {}, "the same text at another company was called a duplicate"
    assert stats.jobs_new == 1
    assert job_count(conn) == 2


def test_a_posting_only_this_source_carries_is_refreshed_on_the_next_pass(conn) -> None:
    """A second pass over a posting NOBODY else holds.

    `_held_external_ids` read every row in `job`, including the ones this
    collector had written itself, so on the second pass a posting matched rule
    1 against ITSELF and the handler returned before fetching anything.

    Three things broke at once, and this asserts all three:

      * `last_seen_at` froze, so the posting could never age or be marked gone;
      * a rewritten description never entered the corpus;
      * a `job_discovery_source` row was written whose `source` equalled the
        job's own `provider` -- the exact row migration 0018 forbids, "a row
        saying 'we found it where we found it' carries nothing".

    The existing repeat-run test does not catch it because it seeds an ASHBY
    posting first, so rule 1 fires against another provider's row, which is
    the case that was always correct.
    """
    entry = listing(id="feed-only-identifier-0103")
    first = ScriptedFeed([entry], {entry["id"]: detail(entry, apply_url=None, text="Text v1.")})
    stats = collector(conn, first).collect(max_pages=1, check_contract=False)
    assert stats.jobs_new == 1

    row = dict(conn.execute("SELECT * FROM job").fetchone())
    assert row["provider"] == PROVIDER

    second = ScriptedFeed([entry], {entry["id"]: detail(entry, apply_url=None, text="Text v2.")})
    again = collector(conn, second).collect(max_pages=1, check_contract=False)

    assert again.jobs_new == 0
    assert again.jobs_seen_again == 1, "a second pass never reached the seen-again path"
    assert again.jobs_changed == 1, "a rewritten description never entered the corpus"
    assert again.duplicates == {}, again.duplicates

    after = dict(conn.execute("SELECT * FROM job").fetchone())
    assert after["content_hash"] != row["content_hash"]
    assert job_count(conn) == 1

    text = conn.execute(
        "SELECT description_text FROM job_raw WHERE content_hash = ?", (after["content_hash"],)
    ).fetchone()["description_text"]
    assert text == "Text v2."


def test_no_posting_is_ever_recorded_as_a_sighting_of_itself(conn) -> None:
    """Migration 0018's rule, asserted rather than commented.

    `job_discovery_source.source` names ANOTHER source that also carries this
    posting. A row where it equals the job's own provider is not a sighting;
    it is noise that inflates every count derived from the table.
    """
    entry = listing(id="feed-only-identifier-0104")
    for text in ("Text v1.", "Text v2.", "Text v3."):
        feed = ScriptedFeed([entry], {entry["id"]: detail(entry, apply_url=None, text=text)})
        collector(conn, feed).collect(max_pages=1, check_contract=False)

    rows = conn.execute(
        "SELECT s.source AS s, j.provider AS p"
        " FROM job_discovery_source s JOIN job j ON j.id = s.job_id"
    ).fetchall()
    offenders = [dict(row) for row in rows if row["s"] == row["p"]]
    assert offenders == [], offenders


def test_a_missing_company_url_is_absent_rather_than_the_word_none(conn) -> None:
    """`str(entry.get("company_url")) or None` is not a null-safe idiom.

    `str(None)` is the four characters `None`, which is truthy, so `or None`
    could never fire. The string reached `company.website` and
    `source_board.board_url` for every feed entry that omitted the field, and
    `company.website` is rendered.
    """
    entry = listing(id="feed-only-identifier-0105")
    entry.pop("company_url")
    feed = ScriptedFeed([entry], {entry["id"]: detail(entry, apply_url=None, text="Some text.")})
    collector(conn, feed).collect(max_pages=1, check_contract=False)

    website = conn.execute("SELECT website FROM company WHERE slug = 'abridge'").fetchone()[0]
    board_url = conn.execute(
        "SELECT board_url FROM source_board WHERE provider = ?", (PROVIDER,)
    ).fetchone()[0]
    assert website is None, repr(website)
    assert board_url is None, repr(board_url)


def test_a_link_pointing_back_at_this_aggregator_resolves_by_url_not_by_id(conn) -> None:
    """Finding 7: the eight-hex prefix is not an `external_id`, and never was.

    The comment above `_POSTING_URL` said "the caller matches on a prefix".
    No caller does: `get_by_external` is an exact match and the stored id is
    the full 36-character UUID, so `('speedrun', '228d2bfe')` can never hit.
    Measured against the corpus by an independent functional review.

    Rule 3 is what covers it, and this is that claim rather than a comment
    about it: a posting already held whose URL is a Speedrun URL is found by
    the URL, one row stays one row, and the sighting names the rule honestly.
    """
    feed_url = "https://speedrun-talent-network.com/jobs/staff-it-engineer-abridge-228d2bfe"
    job_id = seed_ats_job(conn, external_id="held-by-url-1", url=feed_url, provider="ashby")

    entry = listing(id="feed-only-identifier-0106")
    feed = ScriptedFeed(
        [entry], {entry["id"]: detail(entry, apply_url=feed_url, text="Different text entirely.")}
    )
    stats = collector(conn, feed).collect(max_pages=1, check_contract=False)

    assert stats.duplicates == {MATCH_CANONICAL_URL: 1}, stats.duplicates
    assert job_count(conn) == 1
    assert DiscoverySourceRepo(conn).for_job(job_id)[0]["matched_by"] == MATCH_CANONICAL_URL
