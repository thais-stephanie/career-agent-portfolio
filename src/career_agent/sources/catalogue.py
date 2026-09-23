"""The source catalogue, and the one claim it is not allowed to make alone.

`config/source_catalogue.yaml` says what each source is. This module reads it and then
**checks the one status that is a claim about our own behaviour**:
`LIVE_INTEGRATION` means "this retrieves and persists real postings", which is
not something a configuration file can assert about itself.

So a source that declares a live integration is downgraded unless both hold:

* the provider it names is actually in the registry -- an adapter exists;
* the corpus actually holds postings from it -- the adapter has run and
  something survived.

A source that fails either is reported as `CONFIGURED` with a reason. That is
the difference between "we wired this up" and "this works", and the product is
supposed to be the kind that tells you which one it is looking at.

Provider neutrality: the vendor names live in the YAML, which is data. This
module never spells one.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from career_agent.yaml_io import safe_load

#: Anchored on the PACKAGE, not on the process working directory.
#:
#: `Path("config") / ...` meant `serve` run from anywhere but the repository
#: root reported an empty catalogue -- eighteen sources silently becoming none,
#: which reads as "this product has no sources" rather than as an error. It was
#: filed as a LOW review finding and accepted as "consistent with the rest of
#: the CLI, which already assumes repo root". That defence is weaker than it
#: sounds: the rest of the CLI FAILS when it cannot find its file, and this
#: returned an empty list.
#:
#: `parents[3]` is `src/career_agent/sources/` -> the repository root. Callers
#: that know better still pass a path; `JobsApi` passes its own `config_dir`,
#: so the server reads the same configuration directory it was started with.
CONFIG_DIR = Path(__file__).resolve().parents[3] / "config"
DEFAULT_CATALOGUE = CONFIG_DIR / "source_catalogue.yaml"


class Coverage(StrEnum):
    """How much of this source a person can actually use, in five words.

    Separate from :class:`SourceStatus`, which says by what MECHANISM a source
    is reached. The two answer different questions and were being conflated:
    `MANUAL_IMPORT` covers both "the terms forbid us to fetch this" and "we
    simply have not written an adapter", and a matrix that spells those the
    same way cannot be used to decide what to build next.

    ``OPERATIONAL`` and ``PARTIAL`` are claims about our own behaviour, so
    neither may be asserted by a configuration file alone -- :func:`resolve`
    downgrades both to ``PLANNED`` unless the corpus proves them, exactly as it
    already does for ``LIVE_INTEGRATION``.
    """

    #: Retrieval has run and postings from it are in the corpus.
    OPERATIONAL = "OPERATIONAL"
    #: Retrieval works, and something a person would expect is missing.
    PARTIAL = "PARTIAL"
    #: A first-party page forbids automated access. The reason is recorded
    #: verbatim; this is a fact about permission, not about difficulty.
    BLOCKED = "BLOCKED"
    #: No documented interface, or the terms could not be read at all. A
    #: statement about our evidence, not an accusation against the source.
    UNSUPPORTED = "UNSUPPORTED"
    #: Reachable, and characterised by measurement, but the vendor publishes no
    #: terms for it. Permission is UNDECLARED rather than granted or refused.
    #:
    #: Added in V3 because the five values above could not express what three
    #: preflights actually found. Workday CXS and the Gupy candidate feed both
    #: answer without authentication, both have measured schemas and measured
    #: pagination, and neither vendor documents the endpoint at all. Calling
    #: that `UNSUPPORTED` reads as "this cannot be done", which is false and
    #: hides a real option; calling it `PLANNED` reads as "apparently
    #: permitted", which asserts a permission nobody granted.
    #:
    #: This is the same correction the file made once before, when `status` and
    #: `coverage` were split because `MANUAL_IMPORT` covered both "the terms
    #: forbid this" and "we have not written an adapter". One label, two facts,
    #: and the matrix could not be used to decide what to build next.
    #:
    #: It is deliberately NOT a green light. Invariant 2 still holds: silence
    #: is not permission. What this value says is that the blocker is a policy
    #: question rather than a technical one, and it names which.
    UNDOCUMENTED = "UNDOCUMENTED"
    #: Permitted, and there is nothing here to collect. Not a refusal, not a
    #: difficulty, and not a gap in our evidence.
    #:
    #: Added 2026-09-09, and it is the THIRD time this enum has had to split a
    #: label that was doing two jobs -- after `status`/`coverage` and after
    #: `UNDOCUMENTED`. `somewhere.com` names `ClaudeBot` in its robots file
    #: with `Allow: /`, and publishes no candidate-facing openings at all:
    #: `/jobs` redirects to a page written for employers listing the role
    #: TYPES the agency sources. Filing that as `UNSUPPORTED` made `permission`
    #: derive FORBIDDEN, which is the opposite of what the vendor said.
    #:
    #: The distinction matters for what gets built next. `UNSUPPORTED` says
    #: "look for another way in"; this says "there is nothing in there", and
    #: the two send somebody in completely different directions.
    NOTHING_PUBLISHED = "NOTHING_PUBLISHED"
    #: Permitted, or apparently permitted, and not yet demonstrated. Nothing
    #: here may be described as working.
    PLANNED = "PLANNED"

    @property
    def label(self) -> str:
        """The exact name. Used in reports, ADRs and the capability matrix."""
        return {
            Coverage.OPERATIONAL: "Operational",
            Coverage.PARTIAL: "Partially operational",
            Coverage.BLOCKED: "Blocked by terms or robots",
            Coverage.UNSUPPORTED: "Unsupported",
            Coverage.UNDOCUMENTED: "Reachable, terms undeclared",
            Coverage.NOTHING_PUBLISHED: "Permitted, nothing published",
            Coverage.PLANNED: "Planned, not demonstrated",
        }[self]

    @property
    def plain_label(self) -> str:
        """The same six answers for somebody looking for a job.

        "Partially operational" and "Planned, not demonstrated" are accurate
        and they are a status vocabulary, which is a thing a person reads once
        and then has to remember. These say what it MEANS for them: whether
        jobs from this source turn up in their list, and if not, why not.

        Both exist rather than one replacing the other, because the exact
        labels are what `docs/product/source-capability-matrix.md` and the
        ADRs are written in, and renaming a classification to improve a
        tooltip is how a vocabulary quietly forks.
        """
        return {
            Coverage.OPERATIONAL: "Jobs are coming in",
            Coverage.PARTIAL: "Jobs are coming in, with a gap",
            Coverage.BLOCKED: "We are not allowed to fetch these",
            Coverage.UNSUPPORTED: "We have no way in",
            Coverage.UNDOCUMENTED: "Reachable, but nobody has said we may",
            Coverage.NOTHING_PUBLISHED: "They do not publish open jobs",
            # "not built yet" was wrong for half the rows it labelled, and it
            # contradicted the sentence printed directly under it. PLANNED covers
            # two different situations -- permitted and unbuilt (Programathor),
            # and built but never run (We Work Remotely, Get on Board) -- and the
            # DOWNGRADE_PLAIN messages below already tell those apart precisely.
            # The heading only has to be true of both, so it says nothing about
            # whether an adapter exists and leaves that to the line that knows.
            Coverage.PLANNED: "Possible, nothing from it yet",
        }[self]


class Permission(StrEnum):
    """What the SOURCE has said about being read. One of three dimensions.

    `Coverage` answers "can I use this" in one word, and it has always been
    three questions wearing one answer: whether we are allowed to read a
    source, how far the code that reads it has got, and how much of a posting
    comes back. `BLOCKED` is a permission, `PLANNED` is a maturity, and
    `PARTIAL` is content depth -- three different kinds of fact ranked as
    though they were points on one scale.

    That mattered little while every undeclared source was also uncollected.
    Under ADR-0018 it stopped being true on the same day: Gupy is UNDECLARED
    and COLLECTING and FULL_ADVERT, and no single value can say that.

    `Coverage` is not replaced. It is the one-word answer a panel leads with
    and dozens of rows, tests and documents are written against it. These three
    are DERIVED beside it, from facts the catalogue already holds, so nothing
    has to be restated in YAML to be read correctly.
    """

    #: A first-party page affirmatively allows it.
    PERMITTED = "PERMITTED"
    #: A first-party page forbids it, in terms or in robots.txt. The reason is
    #: quoted on the row.
    FORBIDDEN = "FORBIDDEN"
    #: The source has published nothing either way.
    #:
    #: Under the policy adopted 2026-09-09 this is a source we MAY read,
    #: bounded and politely (ADR-0018). It is still not a grant, and the
    #: distinction is kept because the day a vendor publishes terms is the day
    #: this row has to be re-read.
    UNDECLARED = "UNDECLARED"

    @property
    def label(self) -> str:
        return {
            Permission.PERMITTED: "Allowed by the source",
            Permission.FORBIDDEN: "Refused by the source",
            Permission.UNDECLARED: "The source has not said",
        }[self]


class Maturity(StrEnum):
    """How far the code that reads this source has got. Nothing about policy."""

    #: No adapter. Whether one is allowed is a different question.
    NOT_BUILT = "NOT_BUILT"
    #: An adapter exists and is registered, and the corpus holds nothing from
    #: it. The adapter existing is not the source working.
    BUILT = "BUILT"
    #: The corpus holds postings from it.
    COLLECTING = "COLLECTING"

    @property
    def label(self) -> str:
        return {
            Maturity.NOT_BUILT: "No connector yet",
            Maturity.BUILT: "Connector written, never run",
            Maturity.COLLECTING: "Collecting",
        }[self]


class ContentDepth(StrEnum):
    """How much of a posting comes back. Nothing about policy or maturity."""

    #: The employer's whole advert is obtainable, in the list or after a
    #: detail request. Either way the stored text is complete.
    FULL_ADVERT = "FULL_ADVERT"
    #: A snippet, and no documented way to get the rest. Such a posting must
    #: never be scored as though its description were complete.
    EXCERPT_ONLY = "EXCERPT_ONLY"
    #: No adapter, so the question does not arise.
    UNKNOWN = "UNKNOWN"

    @property
    def label(self) -> str:
        return {
            ContentDepth.FULL_ADVERT: "The whole advert",
            ContentDepth.EXCERPT_ONLY: "An excerpt only",
            ContentDepth.UNKNOWN: "Not known yet",
        }[self]


class SourceStatus(StrEnum):
    """What a person can actually do with a source, in four words."""

    LIVE_INTEGRATION = "LIVE_INTEGRATION"
    MANUAL_IMPORT = "MANUAL_IMPORT"
    SEARCH_LINK = "SEARCH_LINK"
    UNAVAILABLE = "UNAVAILABLE"
    #: Declared live, but the claim did not hold at runtime.
    CONFIGURED = "CONFIGURED"

    @property
    def label(self) -> str:
        return {
            SourceStatus.LIVE_INTEGRATION: "Live integration",
            SourceStatus.MANUAL_IMPORT: "Manual import",
            SourceStatus.SEARCH_LINK: "Search link",
            SourceStatus.UNAVAILABLE: "Unavailable or restricted",
            SourceStatus.CONFIGURED: "Configured, not yet proven",
        }[self]


#: What a downgraded row says instead of the sentence the catalogue wrote.
#:
#: Keyed on the exact `downgraded_because` text, because that string is
#: produced here, in this file, four lines away -- there is no parsing and no
#: guessing. A reason with no entry falls back to the reason itself, which is
#: readable enough and is never silence.
#: Coverage values that assert nothing has been retrieved from a source.
#:
#: A row carrying one of these over a non-zero posting count is a defect in
#: `source_catalogue.yaml`, not a state a source can legitimately be in.
#: BLOCKED and UNSUPPORTED are here for the same reason PLANNED is: if the
#: corpus holds postings from a source we have declared we may not fetch, that
#: is the single most important thing the panel could tell anybody.
CLAIMS_NOTHING_COLLECTED: frozenset[Coverage] = frozenset(
    {
        Coverage.PLANNED,
        Coverage.BLOCKED,
        Coverage.UNSUPPORTED,
        Coverage.UNDOCUMENTED,
        Coverage.NOTHING_PUBLISHED,
    }
)


DOWNGRADE_PLAIN: dict[str, str] = {
    "no adapter is registered for this source": ("Nothing has been built to fetch these yet."),
    "no database was supplied, so the claim could not be checked": (
        "We could not check whether any of these have arrived."
    ),
    "the adapter exists but has never persisted a posting": (
        "The connector is written, and no job has come through it yet."
    ),
    "no posting from this source is in the corpus": ("Nothing from here has arrived yet."),
    # Found by the test below rather than by me: this fifth reason exists and
    # I had written four entries. That is exactly the drift the check is for.
    "declared operational without a verified live integration": (
        "The catalogue calls this working, and nothing in your database proves it."
    ),
}


@dataclass(frozen=True, slots=True)
class Source:
    id: str
    name: str
    region: str
    status: SourceStatus
    authorization: str
    provider: str | None = None
    note: str | None = None
    search_url: str | None = None
    #: How many postings the corpus holds from this source. None when the
    #: source has no adapter and the question does not apply.
    collected: int | None = None
    #: Why a declared live integration was downgraded, if it was.
    downgraded_because: str | None = None
    #: The five-value answer to "can I use this". Verified, never declared.
    coverage: Coverage = Coverage.PLANNED
    #: How many BOARDS this connector reaches, and how many of them have ever
    #: returned a posting. `None` where the source is not a connector family.
    #:
    #: Two numbers rather than one, because they are the pair that stops "234
    #: boards" from being read as "234 sources": a board is one employer's
    #: careers page, and 234 of them are reached through THREE connectors.
    boards: int | None = None
    boards_with_postings: int | None = None
    #: The exact technical or legal reason, for a BLOCKED or UNSUPPORTED row.
    #: Quoted from a first-party page wherever the quote is short enough.
    reason: str | None = None
    #: The same reason for the person using the product, who has not heard of
    #: robots.txt and does not need to. Shown FIRST in the panel; `reason`
    #: stays verbatim behind a disclosure, because the quote is the evidence
    #: and paraphrasing it away would leave the claim unsupported.
    reason_plain: str | None = None
    #: What would move this row, for a PLANNED one. Absent when nothing would.
    unblocked_by: str | None = None
    #: Set when the DECLARATION claims less than the corpus can prove.
    #:
    #: `resolve` has always refused a claim the corpus does not support. It had
    #: no answer for the opposite, and on 2026-09-07 the opposite happened: the
    #: owner collected 409 Get on Board postings while the catalogue still said
    #: `PLANNED`, so `source-health` printed "Never collected. Nothing has
    #: failed; nothing has run." above a count of 409.
    #:
    #: **It is not resolved by upgrading.** Which of OPERATIONAL and PARTIAL a
    #: source deserves is a judgement about what a person would expect to find
    #: and is missing; a resolver inventing one would be asserting exactly the
    #: kind of claim this module exists to stop being asserted. So the
    #: contradiction is REPORTED, loudly, and the catalogue is what gets fixed.
    contradicted_because: str | None = None
    # An access/availability failure is distinct from a vendor prohibition.
    collection_blocker: str | None = None

    @property
    def permission(self) -> Permission:
        """What the SOURCE said, derived from the coverage the catalogue holds.

        `BLOCKED` and `UNSUPPORTED` are both refusals: the first is a robots or
        terms statement, the second is a source whose terms could not be read
        at all, and neither is a source we may read. `UNDOCUMENTED` is silence.
        Everything else got here by being permitted.
        """
        if self.coverage in (Coverage.BLOCKED, Coverage.UNSUPPORTED):
            return Permission.FORBIDDEN
        if self.coverage is Coverage.UNDOCUMENTED:
            return Permission.UNDECLARED
        # NOTHING_PUBLISHED falls through to PERMITTED on purpose. It is the
        # value for a source that ALLOWED us and has no openings to give, and
        # deriving a refusal from an empty board would put words in a vendor's
        # mouth that its robots file contradicts.
        return Permission.PERMITTED

    @property
    def maturity(self) -> Maturity:
        """How far the code got. Asked of the registry and the corpus, never
        of the YAML, because both of those are facts and the YAML is a claim."""
        if not self.provider:
            return Maturity.NOT_BUILT
        if self.collected:
            return Maturity.COLLECTING
        return Maturity.BUILT

    @property
    def content_depth(self) -> ContentDepth:
        """What a posting from here contains, from the adapter's own capability.

        `obtains_full_description` rather than `full_description_in_list`: the
        first asks whether the whole advert can be had at all, which is the
        question a reader has. Speedrun answers False to the second and True to
        the first, and Jooble answers False to both for a reason no second
        request can fix.
        """
        if not self.provider:
            return ContentDepth.UNKNOWN
        try:
            from career_agent.providers.registry import capabilities_for

            return (
                ContentDepth.FULL_ADVERT
                if capabilities_for(self.provider).obtains_full_description
                else ContentDepth.EXCERPT_ONLY
            )
        except (KeyError, ImportError):
            return ContentDepth.UNKNOWN

    @property
    def plain_explanation(self) -> str | None:
        """The sentence a person reads, which is not always the one written.

        `reason_plain` is written against the coverage the catalogue DECLARES.
        `resolve` refuses that declaration when the corpus does not back it,
        because coverage is verified and never declared -- and a downgraded row
        printing the declared sentence says the opposite of its own heading.
        Seen in a screenshot: "Jobs from Lever boards are in your list" sitting
        under "Possible, not built yet".
        """
        if self.downgraded_because:
            return DOWNGRADE_PLAIN.get(self.downgraded_because, self.downgraded_because)
        return self.reason_plain

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "region": self.region,
            "status": self.status.value,
            "status_label": self.status.label,
            "authorization": self.authorization,
            "note": self.note,
            "search_url": self.search_url,
            "collected": self.collected,
            "downgraded_because": self.downgraded_because,
            "coverage": self.coverage.value,
            "coverage_label": self.coverage.label,
            "boards": self.boards,
            "boards_with_postings": self.boards_with_postings,
            "reason": self.reason,
            "reason_plain": self.plain_explanation,
            #: The sentence the FILE wrote, kept separate. On a downgraded row
            #: it is a claim the corpus did not support, so it travels with the
            #: rest of the unverified detail rather than as the answer.
            "declared_reason_plain": self.reason_plain,
            "unblocked_by": self.unblocked_by,
            "coverage_plain": self.coverage.plain_label,
            "contradicted_because": self.contradicted_because,
            # THE THREE DIMENSIONS `coverage` HAS ALWAYS COMPRESSED INTO ONE.
            #
            # Derived, never stored, so no row had to be restated to be read
            # correctly. They say what a single value cannot: Jooble is
            # PERMITTED and BUILT and EXCERPT_ONLY, which is why it stays off
            # despite nothing forbidding it; Remote OK is PERMITTED and
            # NOT_BUILT, which is an entirely different reason for the same
            # word `PLANNED`.
            "permission": self.permission.value,
            "permission_label": self.permission.label,
            "maturity": self.maturity.value,
            "maturity_label": self.maturity.label,
            "content_depth": self.content_depth.value,
            "content_depth_label": self.content_depth.label,
        }


def load_catalogue(path: Path | None = None) -> list[dict[str, Any]]:
    """The raw declarations. No verification; that is :func:`resolve`."""
    source = path or DEFAULT_CATALOGUE
    data = safe_load(source.read_text(encoding="utf-8"))
    return list(data.get("sources") or [])


def resolve(
    conn: sqlite3.Connection | None = None,
    path: Path | None = None,
) -> list[Source]:
    """The catalogue, with every live-integration claim checked.

    ``conn`` is optional so the catalogue can be read without a database -- the
    documentation build and the tests both want that. Its posting count is then
    reported as unknown rather than as zero, because "we did not look" and
    "there are none" are different answers.

    **But the STATUS is downgraded, which it did not used to be.** Without a
    database, a declared live integration was returned as LIVE_INTEGRATION on
    the strength of an adapter existing -- so a caller with no connection got
    the product's strongest claim about a source, unverified, from a file that
    is not allowed to make it. That was filed as a LOW review finding and left
    open as "the API always passes a connection", which is a fact about today's
    callers rather than a property of this function.

    It now returns CONFIGURED and says why. The count stays unknown; the claim
    becomes unverified. Absence of a database is not permission to assert.
    """
    from career_agent.providers.registry import available_providers

    registered = set(available_providers())
    counts = _counts_by_provider(conn) if conn is not None else None
    boards = _boards_by_provider(conn) if conn is not None else {}
    producing = _producing_boards_by_provider(conn) if conn is not None else {}

    resolved: list[Source] = []
    # `declaration` rather than `raw`, and the name is load-bearing. It is one
    # entry of OUR OWN `source_catalogue.yaml`, not a vendor response, and the
    # provider-neutrality guard reads `raw` as the name of a vendor blob --
    # correctly, since a neutral module has no business subscripting one. The
    # variable was misnamed; the guard was right.
    for declaration in load_catalogue(path):
        declared = SourceStatus(str(declaration["status"]))
        provider = declaration.get("provider")
        collected: int | None = None
        downgraded: str | None = None
        status = declared

        if declared is SourceStatus.LIVE_INTEGRATION:
            if not provider or provider not in registered:
                status = SourceStatus.CONFIGURED
                downgraded = "no adapter is registered for this source"
            elif counts is None:
                status = SourceStatus.CONFIGURED
                downgraded = "no database was supplied, so the claim could not be checked"
            else:
                collected = counts.get(provider, 0)
                if collected == 0:
                    status = SourceStatus.CONFIGURED
                    downgraded = "the adapter exists but has never persisted a posting"
        elif provider and counts is not None:
            collected = counts.get(provider, 0)

        # The coverage claim, verified the same way the status claim is.
        # OPERATIONAL and PARTIAL both assert that retrieval has RUN, which no
        # file may say about itself -- so a declared one with no postings in
        # the corpus becomes PLANNED, and says why.
        declared_coverage = Coverage(str(declaration.get("coverage", Coverage.PLANNED.value)))
        coverage = declared_coverage
        if declared_coverage in (Coverage.OPERATIONAL, Coverage.PARTIAL):
            if status is not SourceStatus.LIVE_INTEGRATION:
                coverage = Coverage.PLANNED
                downgraded = downgraded or (
                    "declared operational without a verified live integration"
                )
            elif counts is not None and not collected:
                coverage = Coverage.PLANNED
                downgraded = downgraded or "no posting from this source is in the corpus"

        # The other direction, which had no check at all until 2026-09-07.
        # A declaration saying nothing has come from this source, over a corpus
        # holding postings from it, is a contradiction -- and the panel renders
        # the declaration, so the reader is told the opposite of what they can
        # see. Reported rather than silently corrected: see
        # `Source.contradicted_because`.
        contradicted: str | None = None
        if collected and coverage in CLAIMS_NOTHING_COLLECTED:
            contradicted = (
                f"the catalogue declares {coverage.value} while the corpus holds "
                f"{collected} posting(s) from this source"
            )

        resolved.append(
            Source(
                id=str(declaration["id"]),
                name=str(declaration["name"]),
                region=str(declaration.get("region", "global")),
                status=status,
                authorization=str(declaration.get("authorization", "")).strip(),
                provider=provider,
                note=(str(declaration["note"]).strip() if declaration.get("note") else None),
                search_url=declaration.get("search_url"),
                collected=collected,
                downgraded_because=downgraded,
                contradicted_because=contradicted,
                collection_blocker=declaration.get("collection_blocker"),
                coverage=coverage,
                boards=(boards.get(provider) if provider else None),
                boards_with_postings=(producing.get(provider) if provider else None),
                reason=(str(declaration["reason"]).strip() if declaration.get("reason") else None),
                reason_plain=(
                    str(declaration["reason_plain"]).strip()
                    if declaration.get("reason_plain")
                    else None
                ),
                unblocked_by=(
                    str(declaration["unblocked_by"]).strip()
                    if declaration.get("unblocked_by")
                    else None
                ),
            )
        )
    return resolved


def _boards_by_provider(conn: sqlite3.Connection) -> dict[str, int]:
    """How many employer boards each connector is configured to reach.

    The number that stops "234 boards" from reading as "234 independent
    sources": a board is one employer's careers page, and every one of them is
    reached through one of three connector families.
    """
    try:
        rows = conn.execute(
            "SELECT provider, COUNT(*) AS n FROM source_board GROUP BY provider"
        ).fetchall()
    except sqlite3.Error:
        return {}
    return {str(row["provider"]): int(row["n"]) for row in rows}


def _producing_boards_by_provider(conn: sqlite3.Connection) -> dict[str, int]:
    """How many of those boards have ever returned a posting.

    Reported beside the configured count rather than instead of it, because
    "116 configured, 102 producing" and "116 boards" are different claims and
    only the first is measured.
    """
    try:
        rows = conn.execute(
            "SELECT sb.provider AS provider, COUNT(DISTINCT j.source_board_id) AS n"
            " FROM source_board sb JOIN job j ON j.source_board_id = sb.id"
            " GROUP BY sb.provider"
        ).fetchall()
    except sqlite3.Error:
        return {}
    return {str(row["provider"]): int(row["n"]) for row in rows}


def _counts_by_provider(conn: sqlite3.Connection) -> dict[str, int]:
    """Postings held per provider. Never raises on a database without `job`."""
    try:
        rows = conn.execute(
            "SELECT provider, COUNT(*) AS n FROM job WHERE closed_at IS NULL GROUP BY provider"
        ).fetchall()
    except sqlite3.Error:
        return {}
    return {str(row["provider"]): int(row["n"]) for row in rows}
