"""Invented XML exercises attribution, local filtering and interrupted feeds."""

import httpx

from career_agent.net.fetcher import HttpFetcher
from career_agent.pipeline.workable_collect import WorkableCollector
from career_agent.providers.workable_xml import XML_URL, WorkableXmlProvider
from career_agent.storage.db import connect, migrate

XML = """<source><job><title>Retail associate</title><referencenumber>A1</referencenumber>
<company>Example Retail</company><url><![CDATA[https://apply.workable.com/j/A1?utm_source=x&amp;token=a%2Fb+z]]></url>
<description><![CDATA[<p>Salary USD 40,000 yearly. Full time retail work.</p>]]></description>
<country>US</country><remote>false</remote><jobtype>Full-time</jobtype></job></source>"""


def test_xml_normal_path_persists_and_replays_exact_apply_url(tmp_path):
    seen = []

    def handler(request):
        seen.append(str(request.url))
        return httpx.Response(200, text=XML)

    conn = connect(tmp_path / "scratch.db")
    migrate(conn)
    with HttpFetcher(
        client=httpx.Client(transport=httpx.MockTransport(handler)), request_delay_seconds=0
    ) as fetcher:
        first = WorkableCollector(conn, fetcher).collect()
        second = WorkableCollector(conn, fetcher).collect()
    assert first.xml_jobs == first.jobs_new == 1
    assert second.jobs_seen_again == 1 and second.jobs_new == 0
    assert seen == [XML_URL, XML_URL]
    row = conn.execute("SELECT url FROM job").fetchone()
    assert row[0] == "https://apply.workable.com/j/A1?utm_source=x&amp;token=a%2Fb+z"
    assert not first.failures
    conn.close()


def test_partial_xml_keeps_complete_jobs_and_records_failure(tmp_path):
    path = tmp_path / "partial.xml"
    path.write_text(XML.replace("</source>", "<job><description><![CDATA[broken"))
    conn = connect(tmp_path / "scratch.db")
    migrate(conn)
    with HttpFetcher() as fetcher:
        stats = WorkableCollector(conn, fetcher, xml_path=path).collect()
    assert stats.jobs_new == 1 and stats.failures
    assert stats.claimed_total is None
    assert conn.execute("SELECT status FROM pipeline_run").fetchone()[0] == "FAILED"
    conn.close()


def test_duplicate_reference_keeps_all_geographies_and_apply_attribution(tmp_path):
    import json

    first = XML.removeprefix("<source>").removesuffix("</source>")
    other = first.replace("<country>US</country>", "<country>BR</country>")
    path = tmp_path / "variants.xml"
    path.write_text("<source>" + first + other + "</source>")
    conn = connect(tmp_path / "scratch.db")
    migrate(conn)
    with HttpFetcher() as fetcher:
        stats = WorkableCollector(conn, fetcher, xml_path=path).collect()
    assert stats.xml_jobs == 2
    assert stats.feed_duplicates == 1
    assert stats.jobs_new == stats.claimed_total == 1
    job = conn.execute("SELECT url,location_raw FROM job").fetchone()
    assert job[1] == "US / BR"
    payload = json.loads(
        conn.execute("SELECT payload_json FROM job_provider_payload").fetchone()[0]
    )
    assert payload["xml_variants"] == [{"country": "BR"}]
    assert payload["apply_url"] == job[0]
    assert {p["countryName"] for p in payload["xml_locations"]} == {"US", "BR"}
    conn.close()


def test_local_filter_counts_exclusions_without_server_query(tmp_path):
    path = tmp_path / "feed.xml"
    path.write_text(XML)
    provider = WorkableXmlProvider(None, xml_path=path)
    pages = list(provider.walk(query="software"))
    assert sum(len(p.records) for p in pages) == 0
    assert provider.xml_jobs == provider.filtered_out == 1


def test_unicode_employers_are_distinct_and_legacy_placeholder_is_repaired(tmp_path, monkeypatch):
    from career_agent.pipeline import workable_collect

    first = XML.removeprefix("<source>").removesuffix("</source>")
    a = first.replace("Example Retail", "Αλφα")
    b = first.replace("Example Retail", "Βητα").replace("A1", "B2")
    path = tmp_path / "employers.xml"
    path.write_text("<source>" + a + b + "</source>", encoding="utf-8")
    conn = connect(tmp_path / "scratch.db")
    migrate(conn)
    with HttpFetcher() as fetcher:
        with monkeypatch.context() as old:
            old.setattr(workable_collect, "_slugify", lambda _: "unknown-company")
            WorkableCollector(conn, fetcher, xml_path=path).collect()
        before = list(conn.execute("SELECT id,first_seen_at FROM job ORDER BY id"))
        stats = WorkableCollector(conn, fetcher, xml_path=path).collect()
        repeat = WorkableCollector(conn, fetcher, xml_path=path).collect()
    assert stats.jobs_new == 0 and stats.jobs_seen_again == 2
    assert stats.employer_identities_corrected == 2
    assert repeat.employer_identities_corrected == 0
    assert {
        r[0] for r in conn.execute("SELECT c.name FROM job j JOIN company c ON c.id=j.company_id")
    } == {"Αλφα", "Βητα"}
    assert list(conn.execute("SELECT id,first_seen_at FROM job ORDER BY id")) == before
    assert (
        conn.execute(
            "SELECT COUNT(*) FROM job j JOIN source_board b ON b.id=j.source_board_id"
            " WHERE j.company_id != b.company_id"
        ).fetchone()[0]
        == 0
    )
    assert (
        conn.execute(
            "SELECT active FROM source_board WHERE board_identifier='unknown-company'"
        ).fetchone()[0]
        == 0
    )
    conn.close()


def test_same_folded_name_with_distinct_domains_does_not_merge_employers(tmp_path, monkeypatch):
    from career_agent.pipeline import workable_collect

    first = XML.removeprefix("<source>").removesuffix("</source>")
    a = first.replace("Example Retail", "REACH").replace(
        "</company>", "</company><website>http://reach.invalid</website>"
    )
    b = (
        first.replace("Example Retail", "Reach")
        .replace("A1", "B2")
        .replace("</company>", "</company><website>https://withreach.invalid</website>")
    )
    path = tmp_path / "domains.xml"
    path.write_text("<source>" + a + b + "</source>", encoding="utf-8")
    conn = connect(tmp_path / "scratch.db")
    migrate(conn)
    with HttpFetcher() as fetcher:
        with monkeypatch.context() as old:
            old.setattr(
                WorkableCollector,
                "_employer_identity",
                lambda self, job, name: (workable_collect._slugify(name), None),
            )
            WorkableCollector(conn, fetcher, xml_path=path).collect()
        before = list(conn.execute("SELECT id,first_seen_at FROM job ORDER BY id"))
        stats = WorkableCollector(conn, fetcher, xml_path=path).collect()
        repeat = WorkableCollector(conn, fetcher, xml_path=path).collect()
    assert stats.jobs_new == 0 and stats.jobs_seen_again == 2
    assert stats.employer_identities_corrected == 1
    assert repeat.employer_identities_corrected == repeat.companies_new == 0
    assert conn.execute("SELECT COUNT(DISTINCT company_id) FROM job").fetchone()[0] == 2
    assert list(conn.execute("SELECT id,first_seen_at FROM job ORDER BY id")) == before
    conn.close()


def test_existing_company_registry_metadata_survives_xml_refresh(tmp_path):
    from career_agent.storage.db import transaction
    from career_agent.storage.records import CompanyRecord
    from career_agent.storage.repositories import CompanyRepo

    path = tmp_path / "feed.xml"
    path.write_text(XML, encoding="utf-8")
    conn = connect(tmp_path / "scratch.db")
    migrate(conn)
    with transaction(conn):
        company_id = CompanyRepo(conn).upsert(
            CompanyRecord(
                slug="example-retail",
                name="Example Retail",
                website="https://example.org",
                hq_country="GB",
                canonical_domain="example.org",
                notes="Curated registry note",
                discovery_source="verified_registry",
                priority_reason="published_board",
            )
        )
    before = dict(conn.execute("SELECT * FROM company WHERE id=?", (company_id,)).fetchone())
    with HttpFetcher() as fetcher:
        WorkableCollector(conn, fetcher, xml_path=path).collect()
    after = dict(conn.execute("SELECT * FROM company WHERE id=?", (company_id,)).fetchone())
    assert before == after
    assert conn.execute("SELECT company_id FROM job").fetchone()[0] == company_id
    conn.close()
