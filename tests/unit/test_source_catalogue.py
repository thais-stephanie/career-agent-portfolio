"""The source catalogue, and the claim it is not allowed to make about itself.

"Live integration" is a statement about our own behaviour -- that this source
retrieves and persists real postings -- so a configuration file may declare it
but may not be believed. These tests are mostly about the downgrade path,
because the failure mode being prevented is a product that lists nine sources
as working when three of them do.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest
import yaml

from career_agent.sources import Coverage, SourceStatus, catalogue, load_catalogue, resolve
from career_agent.sources.catalogue import DOWNGRADE_PLAIN, Source

REPO_ROOT = Path(__file__).resolve().parents[2]
CATALOGUE = REPO_ROOT / "config" / "source_catalogue.yaml"


def _corpus(providers: dict[str, int]) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE job (id TEXT PRIMARY KEY, provider TEXT, closed_at TEXT)")
    n = 0
    for provider, count in providers.items():
        for _ in range(count):
            n += 1
            conn.execute("INSERT INTO job VALUES (?, ?, NULL)", (f"j{n}", provider))
    return conn


def _write(tmp_path: Path, sources: list[dict]) -> Path:
    path = tmp_path / "sources.yaml"
    path.write_text(yaml.safe_dump({"schema_version": 1, "sources": sources}), encoding="utf-8")
    return path


# =========================================================================
# The shipped catalogue
# =========================================================================


def test_every_declared_status_is_one_of_the_four_plus_the_downgrade() -> None:
    """A fifth status invented in YAML would render as an unknown label."""
    for raw in load_catalogue(CATALOGUE):
        SourceStatus(str(raw["status"]))  # raises on anything else


def test_every_source_says_why(tmp_path: Path) -> None:
    """`authorization` is the whole point of the matrix: a status without a
    reason is an opinion, and this file exists so uncertainty is resolved by
    reading a first-party page rather than by writing a scraper."""
    for raw in load_catalogue(CATALOGUE):
        assert str(raw.get("authorization", "")).strip(), f"{raw['id']} gives no reason"


def test_a_search_link_source_carries_a_link(tmp_path: Path) -> None:
    """Otherwise it is just an unavailable source with a friendlier word."""
    for raw in load_catalogue(CATALOGUE):
        if str(raw["status"]) == "SEARCH_LINK":
            assert raw.get("search_url"), f"{raw['id']} is a search link with no URL"


def test_the_three_wired_sources_resolve_live_against_the_real_corpus() -> None:
    """Both halves of the check, against the registry and against postings.

    Read WITH a database, because a live claim now needs both. The
    without-a-database case is its own test below and no longer returns a live
    source at all.
    """
    live = {
        s.id
        for s in resolve(_corpus({"greenhouse": 5, "lever": 5, "ashby": 5}), CATALOGUE)
        if s.status is SourceStatus.LIVE_INTEGRATION
    }
    assert live, "no source resolved live; the registry check is inverted"
    for source in resolve(_corpus({"greenhouse": 5, "lever": 5, "ashby": 5}), CATALOGUE):
        if source.status is SourceStatus.LIVE_INTEGRATION:
            assert source.provider, "a live source must name the adapter that proves it"


# =========================================================================
# The downgrade -- what this module exists for
# =========================================================================


def test_a_live_claim_without_an_adapter_is_downgraded(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        [
            {
                "id": "invented",
                "name": "Invented Board",
                "region": "global",
                "status": "LIVE_INTEGRATION",
                "provider": "no-such-provider",
                "authorization": "declared in a file, which is not evidence",
            }
        ],
    )
    source = resolve(_corpus({}), path)[0]
    assert source.status is SourceStatus.CONFIGURED
    assert "no adapter" in (source.downgraded_because or "")


def test_a_live_claim_that_has_never_collected_anything_is_downgraded(
    tmp_path: Path,
) -> None:
    """The half that catches the more likely mistake: the adapter exists, was
    wired up, and has never actually returned a posting."""
    real = next(
        raw for raw in load_catalogue(CATALOGUE) if str(raw["status"]) == "LIVE_INTEGRATION"
    )
    path = _write(tmp_path, [dict(real)])
    source = resolve(_corpus({}), path)[0]
    assert source.status is SourceStatus.CONFIGURED
    assert "never persisted" in (source.downgraded_because or "")
    assert source.collected == 0


def test_a_live_claim_with_postings_behind_it_stands(tmp_path: Path) -> None:
    real = next(
        raw for raw in load_catalogue(CATALOGUE) if str(raw["status"]) == "LIVE_INTEGRATION"
    )
    path = _write(tmp_path, [dict(real)])
    source = resolve(_corpus({str(real["provider"]): 7}), path)[0]
    assert source.status is SourceStatus.LIVE_INTEGRATION
    assert source.collected == 7


def test_without_a_database_the_count_is_unknown_and_the_claim_is_unverified(
    tmp_path: Path,
) -> None:
    """Two different answers, and this used to give only one of them.

    "We did not look" and "there are none" are different, so the COUNT stays
    None rather than becoming zero -- that half is unchanged and still right.

    The STATUS is the half that was wrong. Without a database, a declared live
    integration came back as LIVE_INTEGRATION on the strength of an adapter
    existing: the product's strongest claim about a source, unverified, from a
    file that is explicitly not allowed to make it. It was filed as a LOW
    finding and left open because "the API always passes a connection" -- which
    is a fact about today's callers, not a property of this function, and the
    next caller is exactly who a guard is for.

    Absence of a database is not permission to assert. CONFIGURED, and it says
    why.
    """
    real = next(
        raw for raw in load_catalogue(CATALOGUE) if str(raw["status"]) == "LIVE_INTEGRATION"
    )
    path = _write(tmp_path, [dict(real)])
    source = resolve(None, path)[0]
    assert source.collected is None, "an unknown count must not become zero"
    assert source.status is SourceStatus.CONFIGURED
    assert "could not be checked" in (source.downgraded_because or "")


def test_the_catalogue_is_found_from_any_working_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The other half of the same finding, and the worse half.

    `DEFAULT_CATALOGUE` was `Path("config") / ...`, resolved against the process
    working directory. Run `serve` from anywhere but the repository root and
    eighteen sources became none -- silently, because a missing file is an
    exception but a missing DIRECTORY is just a catalogue that is empty. It
    reads as "this product has no sources".

    The defence recorded at the time was that the rest of the CLI already
    assumes repo root. The rest of the CLI FAILS when it cannot find its file.
    """
    monkeypatch.chdir(tmp_path)
    assert not (tmp_path / "config").exists()
    assert len(load_catalogue()) >= 18


def test_a_manual_source_is_never_promoted_by_having_postings(
    tmp_path: Path,
) -> None:
    """The check only ever downgrades. A source whose terms forbid automated
    reading does not become permitted because rows exist -- those rows came
    from a person pasting them, which is the permitted path."""
    path = _write(
        tmp_path,
        [
            {
                "id": "paste-only",
                "name": "Paste Only",
                "region": "br",
                "status": "MANUAL_IMPORT",
                "provider": "paste-only",
                "authorization": "terms forbid automated reading",
            }
        ],
    )
    source = resolve(_corpus({"paste-only": 40}), path)[0]
    assert source.status is SourceStatus.MANUAL_IMPORT
    assert source.collected == 40


@pytest.mark.parametrize("status", list(SourceStatus))
def test_every_status_has_a_human_label(status: SourceStatus) -> None:
    assert status.label and status.label != status.value


# =========================================================================
# Coverage: the five-value answer, and the two it may not assert alone
# =========================================================================

#: Brazilian sources. Called out by id rather than by region string so that a
#: row losing its `region: br` shows up as a KeyError here instead of quietly
#: dropping out of the rule below.
BRAZILIAN = frozenset(
    {
        "gupy",
        "programathor",
        "vagas",
        "indeed_br",
        "linkedin_br",
        "catho",
        "infojobs_br",
        "trampos",
        "sine",
    }
)


def test_every_source_is_classified() -> None:
    """Eighteen sources, eighteen coverage values. A source with none would
    default to PLANNED and read as "we are working on it", which is a claim."""
    for raw in load_catalogue(CATALOGUE):
        assert "coverage" in raw, f"{raw['id']} has no coverage classification"
        assert Coverage(raw["coverage"])


def test_a_blocked_or_unsupported_source_records_the_exact_reason() -> None:
    """ "Blocked" without a reason is an assertion nobody can check.

    The whole point of separating BLOCKED from UNSUPPORTED is that one is a
    prohibition and the other is missing evidence, and a reader can only tell
    which if the row says.
    """
    for raw in load_catalogue(CATALOGUE):
        if raw["coverage"] in (Coverage.BLOCKED.value, Coverage.UNSUPPORTED.value):
            assert raw.get("reason", "").strip(), f"{raw['id']} is {raw['coverage']} with no reason"


def test_a_planned_source_says_what_would_move_it() -> None:
    """PLANNED without a next step is a wish. The exception is the one row that
    is not a single source at all."""
    for raw in load_catalogue(CATALOGUE):
        if raw["coverage"] == Coverage.PLANNED.value and raw["id"] != "company_career_pages":
            assert raw.get("unblocked_by", "").strip(), f"{raw['id']} is PLANNED with no next step"


def test_no_brazilian_platform_is_reported_as_working_without_a_corpus() -> None:
    """The acceptance rule, and it has MORE teeth than it used to, not fewer.

    It used to forbid a Brazilian row from declaring `OPERATIONAL` or `PARTIAL`
    at all, because none had ever been retrieved from. Gupy has now been, so
    the ban on the WORD would only push the guarantee somewhere weaker. What
    the rule is actually for is that nobody can make a Brazilian source look
    like it works by editing YAML.

    So the assertion moved to where that is decided: `resolve` over an EMPTY
    corpus. Whatever a Brazilian row claims, a database with no postings from
    it must report `PLANNED`. A hopeful edit still buys nothing, and a genuine
    integration is no longer forbidden from saying what it is.
    """
    import sqlite3

    from career_agent.storage.db import migrate

    empty = sqlite3.connect(":memory:")
    empty.row_factory = sqlite3.Row
    migrate(empty)
    try:
        resolved = {source.id: source for source in resolve(empty, CATALOGUE)}
    finally:
        empty.close()

    for source_id in BRAZILIAN:
        if source_id not in resolved:
            continue
        source = resolved[source_id]
        assert source.coverage is not Coverage.OPERATIONAL, (
            f"{source_id} reports OPERATIONAL over a corpus holding none of its postings"
        )
        assert source.coverage is not Coverage.PARTIAL, (
            f"{source_id} reports PARTIAL over a corpus holding none of its postings"
        )


def test_a_brazilian_row_that_claims_to_work_names_a_registered_adapter() -> None:
    """The other half. `resolve` can only downgrade a claim it can check, and
    it checks the provider named on the row -- so a row claiming retrieval with
    no adapter behind it would pass the test above by being unverifiable."""
    from career_agent.providers.registry import available_providers

    registered = set(available_providers())
    for raw in load_catalogue(CATALOGUE):
        if raw["id"] not in BRAZILIAN:
            continue
        if raw["coverage"] in (Coverage.OPERATIONAL.value, Coverage.PARTIAL.value):
            assert raw.get("provider") in registered, (
                f"{raw['id']} claims {raw['coverage']} without naming a registered adapter"
            )


def test_gupy_is_collected_and_still_declares_both_of_its_ceilings() -> None:
    """The row most likely to be quietly overstated, pinned by name.

    It used to assert Gupy had never been retrieved from, which was correct
    until 2026-09-09 and is the decision ADR-0018 reversed. What the test
    guards is unchanged: this endpoint answers, the numbers look encouraging,
    and the two facts most easily lost in an upgrade are the ceilings that say
    what a run does NOT reach.

    Both are pinned because they are different facts. One is the vendor's --
    `offset` refused at 10,000 -- and one is ours, the shared 50-page cap that
    binds first. A row that mentioned only the vendor's would read as though
    this product reaches 10,000 per partition, and it reaches 5,000.
    """
    gupy = next(raw for raw in load_catalogue(CATALOGUE) if raw["id"] == "gupy")
    assert gupy["coverage"] == Coverage.PARTIAL.value
    assert gupy["provider"] == "gupy"
    assert "10,000" in gupy["note"], "the vendor's ceiling"
    assert "5,000" in gupy["note"], "our own page cap, which binds first"
    assert "72,682" in gupy["note"], "the on-site partition nobody collects whole"


def test_the_gupy_row_does_not_claim_a_hiring_scope() -> None:
    """Invariant 3, in Portuguese, asserted where a reader would meet it.

    `country: Brasil` is where the work is. A Brazilian board read by a
    Brazilian candidate is the single most tempting place in this product to
    read that as "hires anywhere in Brazil", and the row must say which it is.
    """
    from career_agent.providers.registry import capabilities_for

    assert capabilities_for("gupy").publishes_hiring_scope is False
    gupy = next(raw for raw in load_catalogue(CATALOGUE) if raw["id"] == "gupy")
    assert "publishes_hiring_scope" in gupy["reason"]


def test_an_undocumented_row_never_claims_permission() -> None:
    """`UNDOCUMENTED` is not a quieter way of saying yes.

    Every row carrying it has to name what is undeclared and what would settle
    it, or the label becomes a place to park a source nobody decided about.
    """
    for raw in load_catalogue(CATALOGUE):
        if raw.get("coverage") != Coverage.UNDOCUMENTED.value:
            continue
        assert raw.get("unblocked_by", "").strip(), f"{raw['id']} is UNDOCUMENTED with no next step"
        assert raw["coverage"] != Coverage.OPERATIONAL.value
        # It must say something about the state of permission, not only about
        # the endpoint working.
        authorization = raw.get("authorization", "").lower()
        assert any(
            word in authorization
            for word in ("undeclared", "documents", "documented", "no first-party", "nowhere")
        ), f"{raw['id']} is UNDOCUMENTED but its authorization does not say what is missing"


def test_an_operational_claim_without_postings_is_downgraded(tmp_path: Path) -> None:
    """OPERATIONAL asserts that retrieval has RUN, so a file cannot say it.

    The same treatment LIVE_INTEGRATION already gets, extended to the field a
    reader is actually going to look at.
    """
    path = _write(
        tmp_path,
        [
            {
                "id": "hopeful",
                "name": "Hopeful",
                "status": "LIVE_INTEGRATION",
                "provider": "greenhouse",
                "coverage": "OPERATIONAL",
                "authorization": "documented",
            }
        ],
    )
    [source] = resolve(_corpus({"greenhouse": 0}), path)
    assert source.coverage is Coverage.PLANNED
    # The status downgrade ran first and already recorded the sharper reason,
    # which the coverage downgrade preserves rather than overwriting with its
    # own vaguer one. Asserted on the substance, not the wording.
    assert "never persisted" in (source.downgraded_because or "")


def test_an_operational_claim_on_a_non_live_source_is_downgraded(tmp_path: Path) -> None:
    """A source we may not even fetch cannot be operational, whatever it says."""
    path = _write(
        tmp_path,
        [
            {
                "id": "paste-only",
                "name": "Paste Only",
                "status": "MANUAL_IMPORT",
                "coverage": "OPERATIONAL",
                "authorization": "terms forbid fetching",
            }
        ],
    )
    [source] = resolve(_corpus({}), path)
    assert source.coverage is Coverage.PLANNED


def test_an_operational_claim_with_postings_behind_it_stands(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        [
            {
                "id": "real",
                "name": "Real",
                "status": "LIVE_INTEGRATION",
                "provider": "greenhouse",
                "coverage": "OPERATIONAL",
                "authorization": "documented",
            }
        ],
    )
    [source] = resolve(_corpus({"greenhouse": 12}), path)
    assert source.coverage is Coverage.OPERATIONAL
    assert source.collected == 12


@pytest.mark.parametrize("coverage", list(Coverage))
def test_every_coverage_has_a_human_label(coverage: Coverage) -> None:
    assert coverage.label and coverage.label != coverage.value


def test_the_matrix_distinguishes_boards_from_connector_families() -> None:
    """234 boards are not 234 sources, and the numbers have to say so.

    A board is one employer's careers page. Every one of them is reached
    through one of THREE connector families, and presenting the board count as
    a source count is the specific overstatement this pair of fields exists to
    prevent.
    """
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE job (id TEXT PRIMARY KEY, provider TEXT, closed_at TEXT,"
        " source_board_id TEXT)"
    )
    conn.execute("CREATE TABLE source_board (id TEXT PRIMARY KEY, provider TEXT)")
    conn.execute("INSERT INTO source_board VALUES ('b1', 'greenhouse')")
    conn.execute("INSERT INTO source_board VALUES ('b2', 'greenhouse')")
    conn.execute("INSERT INTO job VALUES ('j1', 'greenhouse', NULL, 'b1')")

    live = [s for s in resolve(conn, CATALOGUE) if s.provider == "greenhouse"]
    assert live, "the catalogue lost its greenhouse row"
    assert live[0].boards == 2
    assert live[0].boards_with_postings == 1, "a configured board is not a producing one"


# =========================================================================
# The panel is read by a job seeker, not by the person who wrote the adapter
# =========================================================================


def test_every_source_explains_itself_in_words_a_job_seeker_uses() -> None:
    """`reason` is evidence. `reason_plain` is the answer.

    The panel exists so somebody can ask "why is there nothing here from
    Gupy?". What it printed was a sentence about robots.txt, a 404 and a
    "first-party statement", every word of which is true and none of which
    answers that question. An independent UX review, reading as a
    nontechnical job seeker, called it the least usable panel in the product.

    Both fields stay: the quote is what makes the claim checkable, and a
    paraphrase that replaced it would leave the classification unsupported.
    """
    for row in load_catalogue():
        plain = row.get("reason_plain")
        assert plain, f"{row['id']} has no plain-language reason"
        assert len(str(plain).split()) >= 6, f"{row['id']}: too terse to be an explanation"


def test_the_plain_reasons_carry_no_engineering_vocabulary() -> None:
    """The words that made the old panel unreadable, named.

    None of these is a bad word. Each is precise, each belongs in `reason`,
    and none of them means anything to somebody who wants a job.
    """
    banned = (
        "robots.txt",
        "404",
        "403",
        "200",
        "endpoint",
        "api",
        "json",
        "http",
        "first-party",
        "schema",
        "pagination",
        "payload",
        "authenticated",
        "disallow",
        "user-agent",
        "crawler",
        "rate limit",
    )
    for row in load_catalogue():
        text = str(row.get("reason_plain") or "").lower()
        # Whole words, so "capable" is not a hit for "api".
        found = [
            word for word in banned if re.search(rf"(?<![a-z]){re.escape(word)}(?![a-z])", text)
        ]
        assert not found, f"{row['id']}: {found} in the plain reason"


def test_every_coverage_value_has_both_a_label_and_a_plain_label() -> None:
    """A new coverage value must not be able to reach the panel unnamed.

    `UNDOCUMENTED` was added in V3 and the panel's own ORDER and TONE lists,
    written before it existed, did not mention it -- so the two rows it was
    invented for sorted last with no tone. This is the Python half of that
    lesson: neither map may fall through.
    """
    for value in Coverage:
        assert value.label, value
        assert value.plain_label, value
        assert value.plain_label != value.label, f"{value}: the plain label is the exact one"


def test_a_downgraded_row_never_prints_the_sentence_it_was_downgraded_from() -> None:
    """Found by looking at a screenshot, not by an assertion.

    `sources-desktop.png` showed, one line under the other:

        POSSIBLE, NOT BUILT YET
          Lever  GLOBAL
          Jobs from Lever boards are in your list, descriptions and all.

    Both are in the panel and they cannot both be true. `reason_plain` is
    written against the coverage the CATALOGUE DECLARES, and `resolve` exists
    to refuse that declaration when the corpus does not back it. The plain
    sentence has to be refused with it.
    """
    declared = Source(
        id="lever",
        name="Lever",
        region="global",
        status=SourceStatus.LIVE_INTEGRATION,
        authorization="",
        coverage=Coverage.PLANNED,
        reason_plain="Jobs from Lever boards are in your list, descriptions and all.",
        downgraded_because="the adapter exists but has never persisted a posting",
    )
    payload = declared.as_dict()

    assert payload["reason_plain"] == (
        "The connector is written, and no job has come through it yet."
    )
    assert payload["declared_reason_plain"].startswith("Jobs from Lever boards are in your list")

    # And a row that was NOT downgraded says what the catalogue wrote.
    kept = replace(declared, downgraded_because=None, coverage=Coverage.OPERATIONAL)
    assert kept.as_dict()["reason_plain"] == declared.reason_plain


def test_every_downgrade_reason_this_module_produces_has_plain_words() -> None:
    """The map is keyed on strings produced a few lines away in this file.

    A new downgrade path is one `downgraded = "..."` line, and it would fall
    back to its own engineering wording in the panel. Cheap to check, and it
    keeps the two lists from drifting the way ORDER and TONE did.
    """
    text = Path(catalogue.__file__).read_text(encoding="utf-8")
    produced = set(re.findall(r'downgraded = (?:downgraded or )?\(?\s*"([^"]+)"', text))
    assert produced, "the downgrade reasons could not be found at all"
    assert not produced - set(DOWNGRADE_PLAIN), sorted(produced - set(DOWNGRADE_PLAIN))


# =========================================================================
# the contradiction guard: a declaration claiming LESS than the corpus proves
# =========================================================================


def test_a_planned_declaration_over_a_corpus_holding_postings_is_reported(
    tmp_path: Path,
) -> None:
    """The defect this guard was built from, reproduced.

    On 2026-09-07 the owner collected 409 Get on Board postings while the
    catalogue still declared PLANNED, and `source-health` printed "Never
    collected. Nothing has failed; nothing has run." above a count of 409.
    `resolve` refused unsupported claims and had no check at all for the
    opposite direction.
    """
    path = _write(
        tmp_path,
        [
            {
                "id": "understated",
                "name": "Understated Board",
                "region": "latam",
                "status": "LIVE_INTEGRATION",
                "provider": "getonbrd",
                "authorization": "robots.txt allows a generic reader",
                "coverage": "PLANNED",
            }
        ],
    )

    [source] = resolve(_corpus({"getonbrd": 409}), path)

    assert source.contradicted_because is not None
    assert "409" in source.contradicted_because
    assert "PLANNED" in source.contradicted_because


def test_the_guard_does_not_invent_a_coverage_of_its_own(tmp_path: Path) -> None:
    """Reported, never silently corrected.

    Which of OPERATIONAL and PARTIAL a source deserves is a judgement about
    what a person would expect and is missing. A resolver choosing one would
    be asserting exactly the kind of claim this module exists to stop being
    asserted, so the declaration stands and the contradiction is surfaced.
    """
    path = _write(
        tmp_path,
        [
            {
                "id": "understated",
                "name": "Understated Board",
                "region": "latam",
                "status": "LIVE_INTEGRATION",
                "provider": "getonbrd",
                "authorization": "robots.txt allows a generic reader",
                "coverage": "PLANNED",
            }
        ],
    )

    [source] = resolve(_corpus({"getonbrd": 409}), path)

    assert source.coverage is Coverage.PLANNED


def test_a_blocked_source_holding_postings_is_the_loudest_case(tmp_path: Path) -> None:
    """If the corpus holds postings from a source we declared we may not
    fetch, that is the single most important thing the panel could say."""
    path = _write(
        tmp_path,
        [
            {
                "id": "forbidden",
                "name": "Forbidden Board",
                "region": "br",
                "status": "MANUAL_IMPORT",
                "provider": "getonbrd",
                "authorization": "robots.txt says no",
                "coverage": "BLOCKED",
            }
        ],
    )

    [source] = resolve(_corpus({"getonbrd": 3}), path)

    assert source.contradicted_because is not None
    assert "BLOCKED" in source.contradicted_because


def test_a_source_with_no_postings_is_never_contradicted(tmp_path: Path) -> None:
    """The ordinary case. PLANNED and empty is what PLANNED means."""
    path = _write(
        tmp_path,
        [
            {
                "id": "quiet",
                "name": "Quiet Board",
                "region": "br",
                "status": "MANUAL_IMPORT",
                "provider": "getonbrd",
                "authorization": "robots.txt says no",
                "coverage": "BLOCKED",
            }
        ],
    )

    [source] = resolve(_corpus({}), path)

    assert source.contradicted_because is None


def test_the_shipped_catalogue_contradicts_nothing_in_the_committed_corpus() -> None:
    """The gate. A catalogue entry that understates a working source is a
    defect, and this is what fails when one is committed."""
    counts = {"greenhouse": 11763, "ashby": 6787, "lever": 2586, "getonbrd": 409, "wwr": 91}
    offenders = [
        (s.name, s.contradicted_because)
        for s in resolve(_corpus(counts), CATALOGUE)
        if s.contradicted_because
    ]
    assert offenders == []


# -- the three dimensions `coverage` used to compress into one ----------------


def test_a_forbidden_source_is_a_permission_fact_and_nothing_else() -> None:
    """Torre and Vagas are FORBIDDEN. That says nothing about whether an
    adapter exists, and for Torre one does -- written, tested and switched off
    at `ROBOTS_DISALLOWS_API`."""
    from career_agent.sources.catalogue import Permission

    rows = {source.id: source for source in resolve(None, CATALOGUE)}
    assert rows["torre"].permission is Permission.FORBIDDEN
    assert rows["vagas"].permission is Permission.FORBIDDEN


def test_an_undeclared_source_is_not_a_forbidden_one() -> None:
    """The distinction ADR-0018 turns on: silence is not a refusal, and the two
    must not carry one word between them.

    THIS USED TO ASSERT IT OF WORKDAY, which was the only row deriving
    `UNDECLARED`. Its authorization paragraph ended "silence is not permission"
    -- invariant 2 applied to a question about network requests rather than to
    a question about what a posting says about a person -- and ADR-0018
    reversed exactly that on 2026-09-09. Leaving the row alone would have kept
    it reading "nobody has said we may" about a source the policy now allows,
    which is the stale status that keeps a buildable source invisible.

    So the property is asserted of the DERIVATION, which is where it lives, and
    the fact that no row needs it today is recorded below rather than being
    quietly true.
    """
    from career_agent.sources.catalogue import Coverage, Permission, Source, SourceStatus

    silent = Source(
        id="example",
        name="Example",
        region="global",
        status=SourceStatus.UNAVAILABLE,
        authorization="the vendor has published nothing about this endpoint",
        coverage=Coverage.UNDOCUMENTED,
    )
    assert silent.permission is Permission.UNDECLARED
    assert silent.permission is not Permission.FORBIDDEN


def test_unreadable_leads_do_not_claim_permission_or_a_production_path() -> None:
    """Technical blockers stay distinct from an explicit vendor prohibition."""
    from career_agent.sources.catalogue import Permission

    for source in resolve(None, CATALOGUE):
        if source.permission is Permission.UNDECLARED:
            assert source.collection_blocker, source.id
            assert source.provider is None, source.id
            assert source.unblocked_by, source.id


def test_permission_and_maturity_are_independent() -> None:
    """The case that proves the split is worth having.

    Three rows carry the same `PLANNED`, are all three PERMITTED, and are
    `PLANNED` for three unrelated reasons. One word said all of it; three say
    which.

    * company career pages -- permitted, and NOBODY HAS WRITTEN THE ADAPTER.
    * Jooble -- adapter written, switched off because its contract returns a
      300-character SNIPPET rather than a posting.
    * Remote OK -- adapter written, full adverts, and nothing has run because
      `remoteok.com/robots.txt` names the agent maintaining this repository
      with `Disallow: /`. Permission for the OWNER'S collector and permission
      for THIS READER are not the same question, and the row is honest about
      holding zero postings for a reason that is neither of the other two.

    Remote OK stood in the first slot until 2026-09-09, when it stopped being
    unbuilt. This test moved rather than weakened: it still needs a genuinely
    NOT_BUILT row and it names one.
    """
    from career_agent.sources.catalogue import ContentDepth, Maturity, Permission

    rows = {source.id: source for source in resolve(None, CATALOGUE)}
    unbuilt, jooble, remoteok = (
        rows["company_career_pages"],
        rows["jooble"],
        rows["remoteok"],
    )

    assert unbuilt.coverage is jooble.coverage is remoteok.coverage, "one coverage value"
    assert unbuilt.permission is Permission.PERMITTED
    assert jooble.permission is Permission.PERMITTED
    assert remoteok.permission is Permission.PERMITTED

    assert unbuilt.maturity is Maturity.NOT_BUILT
    assert jooble.maturity is Maturity.BUILT
    assert remoteok.maturity is Maturity.BUILT

    assert jooble.content_depth is ContentDepth.EXCERPT_ONLY
    assert remoteok.content_depth is ContentDepth.FULL_ADVERT


def test_maturity_is_asked_of_the_corpus_and_not_of_the_yaml() -> None:
    """`COLLECTING` is a fact about a database. Over no database at all,
    nothing may claim it, exactly as `coverage` may not."""
    from career_agent.sources.catalogue import Maturity

    for source in resolve(None, CATALOGUE):
        assert source.maturity is not Maturity.COLLECTING


def test_content_depth_reads_the_adapter_rather_than_the_row() -> None:
    """A row cannot talk itself into completeness: the answer comes from the
    registered adapter's own `obtains_full_description`."""
    from career_agent.providers.registry import capabilities_for
    from career_agent.sources.catalogue import ContentDepth

    for source in resolve(None, CATALOGUE):
        if not source.provider:
            assert source.content_depth is ContentDepth.UNKNOWN
            continue
        expected = (
            ContentDepth.FULL_ADVERT
            if capabilities_for(source.provider).obtains_full_description
            else ContentDepth.EXCERPT_ONLY
        )
        assert source.content_depth is expected
