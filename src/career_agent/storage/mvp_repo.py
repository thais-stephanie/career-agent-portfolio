"""Storage for the Personal MVP: the deterministic score, the human's workflow
state, and the ONE query both views read from.

The decision this module embodies is that **the Cards view and the Table view
are two renderings of one query, never two queries that agree by inspection**.
A filter that means "score >= 70" in a list and "score > 70" in a count is not
a bug anyone reports; it is a number that is quietly wrong forever. So
:class:`ScoredJobQuery` builds every WHERE clause in one private helper,
:meth:`ScoredJobQuery._where`, and ``count``, ``page`` and ``facets`` all call
it. The proof is a test: ``count(f) == len(page(f))`` when the page is large
enough to hold everything.

Three more commitments are visible below.

**Serialisation is byte-stable.** ``serialise_match`` sorts keys and drops
whitespace, so re-storing an identical result produces identical bytes and a
diff of the column means the result actually changed.

**The score and the workflow state never touch.** ``MatchRepo`` writes what the
matcher concluded; ``ApplicationRepo`` writes what the person did. Re-scoring
deletes and rewrites the first and cannot reach the second.

**A status change and its history entry are one write.** ``set_status`` appends
to ``job_application_event`` inside the same transaction that updates
``job_application``, so the current state and the story of how it got there
cannot come apart -- see :func:`_atomic` for how that survives a caller who
already owns a transaction.

Conventions inherited from ``repositories.py``: ULID ids generated here in
Python, positional ``?`` parameters everywhere, ``ON CONFLICT ... DO UPDATE``
never ``INSERT OR REPLACE``, and timestamps written by ``now_utc()`` rather
than by a SQL date function.
"""

from __future__ import annotations

import json
import sqlite3
from collections import Counter
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Any

from career_agent.clock import new_id, now_utc
from career_agent.domain.application import (
    ApplicationStatus,
    normalise_application_state,
    requires_applied_at,
)
from career_agent.domain.enums import (
    AnalysisConfidence,
    DomesticContext,
    EligibilityStatus,
    EmploymentRelationship,
    EmploymentSource,
    FitBand,
    GateResult,
    LocalContractRegime,
    Prominence,
    ResponsibilityCategory,
    ScreeningState,
    Seniority,
    SenioritySource,
)
from career_agent.domain.matching import (
    MATCH_SCHEMA_VERSION,
    NOT_STATED_EXPERIENCE,
    UNRESOLVED_DOMESTIC,
    UNRESOLVED_EMPLOYMENT,
    ConfidenceItem,
    DomesticReading,
    EmploymentReading,
    EntrySignal,
    ExperienceReading,
    ExperienceRequirement,
    GateOutcome,
    MatchResult,
    ObservedSignal,
    Penalty,
    ScoreComponent,
    ScoreContribution,
    ScoredJob,
    SeniorityReading,
    SignalHit,
    TitleAdjustment,
    TitleClass,
    TitleClassification,
)
from career_agent.match.lexicon import TECHNOLOGY_SIGNALS
from career_agent.storage.search_index import is_current, to_match_query

#: What a job's workflow status is when nobody has touched it. A job with no
#: `job_application` row is DISCOVERED; there is no second place that decides.
DEFAULT_APPLICATION_STATUS = ApplicationStatus.DISCOVERED

#: Written into `job_match.config_digest` when the caller did not supply one.
#: The column is NOT NULL because a score must always say which configuration
#: it belongs to; this value says "the bytes were not recorded", which is a
#: different and more honest thing than an empty string that looks like a hash.
UNRECORDED_CONFIG_DIGEST = "UNRECORDED"

#: `ScoredJob.access_method` has no column, and deliberately does not get one:
#: it is a pure function of the provider, and a column would be a second place
#: for the same fact to be wrong. `source_board.discovery_method` was NOT
#: borrowed -- that answers how the BOARD IDENTIFIER was found, which is a
#: different question about a different object.
ACCESS_METHOD_UNRECORDED = "UNRECORDED"

#: Access methods. These are OUR vocabulary, not any vendor's.
ACCESS_METHOD_ATS = "ats_structured"
ACCESS_METHOD_MANUAL = "manual_import"
#: A third party's republication of a posting that originates somewhere else.
ACCESS_METHOD_AGGREGATOR = "aggregator_api"


def access_method_for(provider: str | None) -> str:
    """How a posting was reached, derived from who reached it.

    Membership of the provider registry used to be the whole question, on the
    reasoning that anything the registry can construct is a structured ATS
    adapter by definition. That reasoning was true of three providers and
    stopped being true of the fourth.

    An aggregator republishes postings that originate in other systems. Its text
    may be complete, truncated, reformatted or editorially rewritten, and the
    employer never wrote it in that form. Calling that `ats_structured` would
    tell a person, on the card and in the drawer, that they are looking at the
    employer's own record when they are not.

    So the answer comes from the adapter's declared `kind` rather than from its
    membership. Still no vendor name anywhere: `provider_kind` is a registry
    lookup, exactly like `available_providers` was, and a new aggregator gets
    the right answer by declaring what it is rather than by being added here.
    """
    from career_agent.providers.base import ProviderKind
    from career_agent.providers.registry import provider_kind

    if not provider:
        return ACCESS_METHOD_UNRECORDED
    if provider == ACCESS_METHOD_MANUAL:
        return ACCESS_METHOD_MANUAL
    kind = provider_kind(provider)
    if kind is ProviderKind.ATS:
        return ACCESS_METHOD_ATS
    if kind is ProviderKind.AGGREGATOR:
        return ACCESS_METHOD_AGGREGATOR
    return ACCESS_METHOD_UNRECORDED


#: The confidence item whose award means "this posting states compensation".
#: `has_salary` is a filter over what the matcher concluded, not a column: no
#: table holds a salary, and reading one out of the description text at query
#: time would be manufacturing a fact.
#:
#: It MUST be the id the configuration declares and the scorer emits, which is
#: `salary_known` (`config/search.worked-example.yaml`, `match/score.py`). It said
#: `compensation_stated` -- the item's LABEL, "Compensation stated", turned
#: into an identifier -- so the LIKE looked for a tag no result has ever
#: carried and `has_salary=true` returned nothing, in a corpus and in the demo
#: seed alike. Nothing failed: the filter was simply always empty, which is the
#: shape a wrong constant takes when both sides of it are strings. The tests
#: that covered this filter built their own `ConfidenceItem` with the wrong id
#: too, so they agreed with the bug. They now import this name instead of
#: spelling it, which is what stops the pair from drifting again.
SALARY_CONFIDENCE_ITEM = "salary_known"

#: Sort keys the API accepts, mapped to the column each one means. User input is
#: matched against this and rejected otherwise; nothing a caller supplies is
#: ever interpolated into SQL.
SORT_COLUMNS: dict[str, str] = {
    "score": "jm.match_score",
    "confidence": "jm.data_confidence",
    "posted": "j.posted_at",
    "company": "c.name",
    "title": "j.title",
    "status": "COALESCE(ja.status, 'DISCOVERED')",
    # WHEN SHE HID IT, for the restore view and for nothing else. Forty
    # postings set aside over a month are not equally likely to be the one she
    # wants back, and score order puts the one she hid a minute ago wherever
    # its match happens to fall. `ja.hidden_at` is NULL on every visible row,
    # so this key is only meaningful beside `user_hidden_only`.
    "hidden": "ja.hidden_at",
}
SORT_KEYS: frozenset[str] = frozenset(SORT_COLUMNS)

#: Sort keys whose column lives on `job_application`, so the query has to join
#: it even when no filter asked for it. Derived from the table above rather
#: than listed, so a seventh key ordering by a candidate's own state cannot be
#: added without the join following it.
_SORTS_ON_APPLICATION: frozenset[str] = frozenset(
    key for key, column in SORT_COLUMNS.items() if "ja." in column
)
SORT_DIRECTIONS: frozenset[str] = frozenset({"asc", "desc"})

#: How much description text a list row carries. Sixty full descriptions is
#: roughly 400 KB down the wire for text no card displays.
PAGE_DESCRIPTION_CHARS = 400


class SortKeyError(ValueError):
    """A sort key or direction that is not on the whitelist."""


class ApplicationDateRefused(ValueError):
    """Clearing an applied date the current status would immediately restore.

    A domain rule the person can act on -- "move the status first" -- rather
    than an internal error, so the API can turn it into a 409 with the reason
    instead of a 500 with a traceback.
    """


# =====================================================================
# SERIALISATION
#
# Explicit encoders rather than `dataclasses.asdict` plus a generic loader.
# The inverse has to be written by hand either way -- enums must come back as
# enums and sequences as tuples -- and a hand-written pair that sits next to
# its partner is far easier to keep honest than a reflective one.
# =====================================================================


def _hit_to_dict(hit: SignalHit) -> dict[str, Any]:
    return {
        "signal_id": hit.signal_id,
        "label": hit.label,
        "field_name": hit.field_name,
        "pattern": hit.pattern,
        "quote": hit.quote,
        "char_start": hit.char_start,
        "char_end": hit.char_end,
        "negated": hit.negated,
        "section": hit.section,
    }


def _hit_from_dict(data: dict[str, Any]) -> SignalHit:
    return SignalHit(
        signal_id=data["signal_id"],
        label=data["label"],
        field_name=data["field_name"],
        pattern=data["pattern"],
        quote=data["quote"],
        char_start=data["char_start"],
        char_end=data["char_end"],
        negated=bool(data.get("negated", False)),
        section=data.get("section"),
    )


def _signal_to_dict(signal: ObservedSignal) -> dict[str, Any]:
    return {
        "signal_id": signal.signal_id,
        "label": signal.label,
        "responsibility": signal.responsibility.value if signal.responsibility else None,
        "prominence": signal.prominence.value,
        "hits": [_hit_to_dict(h) for h in signal.hits],
        "negated_hits": [_hit_to_dict(h) for h in signal.negated_hits],
    }


def _signal_from_dict(data: dict[str, Any]) -> ObservedSignal:
    responsibility = data.get("responsibility")
    return ObservedSignal(
        signal_id=data["signal_id"],
        label=data["label"],
        responsibility=ResponsibilityCategory(responsibility) if responsibility else None,
        prominence=Prominence(data["prominence"]),
        hits=tuple(_hit_from_dict(h) for h in data.get("hits", ())),
        negated_hits=tuple(_hit_from_dict(h) for h in data.get("negated_hits", ())),
    )


def _title_to_dict(title: TitleClassification) -> dict[str, Any]:
    return {
        "base_class": title.base_class.value,
        "resolved_class": title.resolved_class.value,
        "adjustment": title.adjustment.value,
        "rule_id": title.rule_id,
        "rule_label": title.rule_label,
        "ambiguity_rule": title.ambiguity_rule,
        "reason": title.reason,
        "supporting_signals": list(title.supporting_signals),
    }


def _title_from_dict(data: dict[str, Any]) -> TitleClassification:
    return TitleClassification(
        base_class=TitleClass(data["base_class"]),
        resolved_class=TitleClass(data["resolved_class"]),
        adjustment=TitleAdjustment(data["adjustment"]),
        rule_id=data.get("rule_id"),
        rule_label=data.get("rule_label"),
        ambiguity_rule=data.get("ambiguity_rule"),
        reason=data.get("reason", ""),
        supporting_signals=tuple(data.get("supporting_signals", ())),
    )


def _contribution_to_dict(row: ScoreContribution) -> dict[str, Any]:
    return {
        "signal_id": row.signal_id,
        "label": row.label,
        "prominence": row.prominence.value,
        "weight": row.weight,
        "points": row.points,
        "quote": row.quote,
    }


def _contribution_from_dict(data: dict[str, Any]) -> ScoreContribution:
    return ScoreContribution(
        signal_id=data["signal_id"],
        label=data["label"],
        prominence=Prominence(data["prominence"]),
        weight=data["weight"],
        points=data["points"],
        quote=data.get("quote"),
    )


def _component_to_dict(component: ScoreComponent) -> dict[str, Any]:
    return {
        "component_id": component.component_id,
        "label": component.label,
        "points": component.points,
        "max_points": component.max_points,
        "contributions": [_contribution_to_dict(c) for c in component.contributions],
        "capped": component.capped,
        "note": component.note,
    }


def _component_from_dict(data: dict[str, Any]) -> ScoreComponent:
    return ScoreComponent(
        component_id=data["component_id"],
        label=data["label"],
        points=data["points"],
        max_points=data["max_points"],
        contributions=tuple(_contribution_from_dict(c) for c in data.get("contributions", ())),
        capped=bool(data.get("capped", False)),
        note=data.get("note"),
    )


def _penalty_to_dict(penalty: Penalty) -> dict[str, Any]:
    return {
        "signal_id": penalty.signal_id,
        "label": penalty.label,
        "prominence": penalty.prominence.value,
        "weight": penalty.weight,
        "points": penalty.points,
        "quote": penalty.quote,
    }


def _penalty_from_dict(data: dict[str, Any]) -> Penalty:
    return Penalty(
        signal_id=data["signal_id"],
        label=data["label"],
        prominence=Prominence(data["prominence"]),
        weight=data["weight"],
        points=data["points"],
        quote=data.get("quote"),
    )


def _gate_to_dict(gate: GateOutcome) -> dict[str, Any]:
    return {
        "gate": gate.gate,
        "result": gate.result.value,
        "reason": gate.reason,
        "blocker_id": gate.blocker_id,
        "quote": gate.quote,
        "char_start": gate.char_start,
        "char_end": gate.char_end,
    }


def _gate_from_dict(data: dict[str, Any]) -> GateOutcome:
    return GateOutcome(
        gate=data["gate"],
        result=GateResult(data["result"]),
        reason=data.get("reason", ""),
        blocker_id=data.get("blocker_id"),
        quote=data.get("quote"),
        char_start=data.get("char_start"),
        char_end=data.get("char_end"),
    )


def _confidence_to_dict(item: ConfidenceItem) -> dict[str, Any]:
    return {
        "item_id": item.item_id,
        "label": item.label,
        "points": item.points,
        "awarded": item.awarded,
        "note": item.note,
    }


def _confidence_from_dict(data: dict[str, Any]) -> ConfidenceItem:
    return ConfidenceItem(
        item_id=data["item_id"],
        label=data["label"],
        points=data["points"],
        awarded=bool(data["awarded"]),
        note=data.get("note"),
    )


#: Tags for the two derived membership indexes inside ``result_json``. Each
#: entry is written as ``|<tag>:<id>|``, so a LIKE never has to anchor on the
#: surrounding JSON key and can never drift from one index into the other.
FIRED_TAG = "fired"
AWARDED_TAG = "award"


def membership_of(result: MatchResult) -> str:
    """The filter index for one result: OUR identifiers, and nothing else.

    This is written to `job_match.membership`, a column that contains no
    posting text of any kind. It used to live inside `result_json` beside the
    evidence quotes, and a posting containing `|award:compensation_stated|`
    could therefore satisfy the `has_salary` filter with no salary in it --
    third-party text steering a deterministic filter. Two homes now: quotes in
    the blob for display, identifiers in this column for querying.
    """
    positive_body = sorted(
        {
            contribution.signal_id
            for component in result.components
            if component.component_id
            in {"responsibilities", "technologies", "automation_integration"}
            for contribution in component.contributions
            if contribution.points > 0
        }
    )
    return (
        _delimited(FIRED_TAG, [s.signal_id for s in result.signals if s.fired])
        + _delimited(AWARDED_TAG, [i.item_id for i in result.confidence_items if i.awarded])
        + _delimited("body_positive", positive_body)
        + "|body_scoring_measured:v1|"
    )


def _delimited(tag: str, ids: Sequence[str]) -> str:
    """A pipe-fenced, tagged membership string: ``|fired:a|fired:b|``.

    The fences are what make a LIKE precise. ``%|fired:crm|%`` cannot match a
    signal called ``crm_2``, which a bare substring search over the JSON would,
    and which would make the signal filter quietly wrong in exactly the cases
    nobody checks. The tag does the same job across indexes: an awarded
    confidence item can never satisfy a signal predicate.
    """
    return "|" + "|".join(f"{tag}:{value}" for value in ids) + "|" if ids else "||"


def _escape_like(text: str) -> str:
    r"""Neutralise the LIKE metacharacters. Paired with ``ESCAPE '\\'`` at every
    call site, so ``100%`` and ``foo_bar`` mean themselves."""
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def serialise_match(result: MatchResult) -> str:
    """The whole ``MatchResult`` as stable JSON bytes.

    ``sort_keys`` plus the tightest separators means an unchanged result
    re-serialises identically, so a changed column is evidence the result
    changed rather than evidence the encoder felt different today.

    The two keys prefixed with ``_`` are DERIVED indexes, not data: flat
    membership strings for the signals that fired and the confidence items that
    were awarded. They exist because ``result_json`` is the only home those two
    facts have, and a LIKE over the nested structure cannot ask "did signal X
    fire?" without also matching signals that were observed and did not.
    :func:`deserialise_match` ignores them, so the round trip is unaffected.
    """
    payload: dict[str, Any] = {
        "_fired": _delimited(FIRED_TAG, [s.signal_id for s in result.signals if s.fired]),
        "_awarded": _delimited(
            AWARDED_TAG, [i.item_id for i in result.confidence_items if i.awarded]
        ),
        "config_id": result.config_id,
        "config_version": result.config_version,
        "schema_version": result.schema_version,
        "posting_facts": dict(result.posting_facts),
        "match_score": result.match_score,
        "data_confidence": result.data_confidence,
        "eligibility_status": result.eligibility_status.value,
        "screening_state": result.screening_state.value,
        "screening_reason": result.screening_reason,
        "fit_band": result.fit_band.value,
        "analysis_confidence": result.analysis_confidence.value,
        "title": _title_to_dict(result.title) if result.title else None,
        "components": [_component_to_dict(c) for c in result.components],
        "penalties": [_penalty_to_dict(p) for p in result.penalties],
        "penalty_total": result.penalty_total,
        "gates": [_gate_to_dict(g) for g in result.gates],
        "confidence_items": [_confidence_to_dict(i) for i in result.confidence_items],
        "signals": [_signal_to_dict(s) for s in result.signals],
        "unknowns": list(result.unknowns),
        # The whole reading, not just the level. `job_match.seniority` keeps
        # holding the VALUE so every existing query and filter is untouched;
        # the source, confidence and quote ride here, which is why this change
        # needed no migration.
        "seniority": _seniority_to_dict(result.seniority),
        # Two readings ABOUT the posting. Neither has a column, deliberately:
        # no query filters on them yet, and adding two columns for a fact the
        # interface only ever displays would be a migration bought with
        # nothing. If a filter is ever wanted, that is when a column earns its
        # place.
        "employment": _employment_to_dict(result.employment),
        "domestic": _domestic_to_dict(result.domestic),
        # A THIRD reading, and this one DOES have columns -- three of them,
        # from migration 0027, because a filter has to be able to ask.
        #
        # **It is here as well, and that is not duplication.** The columns are
        # what SQL narrows on; this is what the CARD renders. They were split
        # for one commit and the two immediately disagreed: the facets counted
        # six postings asking for a stated minimum while every card said "the
        # posting did not say", because `job_card` reads the deserialised
        # result and the result had thrown the reading away. A card and a
        # filter disagreeing about one posting is the class of defect this
        # codebase keeps closing.
        #
        # The quote in particular has nowhere else to live: it is the
        # employer's own sentence, and no column holds it.
        "experience": _experience_to_dict(result.experience),
        "computed_at": result.computed_at,
    }
    return json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _experience_to_dict(reading: ExperienceReading) -> dict[str, Any]:
    return {
        "requirement": reading.requirement.value,
        "min_years": reading.min_years,
        "quote": reading.quote,
        "entry_signals": [signal.value for signal in reading.entry_signals],
    }


def _experience_from_dict(data: dict[str, Any] | None) -> ExperienceReading:
    """A reading, or the NOT_STATED one for a row written before 0027.

    `None` here means the row predates the reading, and `NOT_STATED` is the
    right thing to build from it: the posting was not asked, so nothing about
    it was concluded. `MATCH_SCHEMA_VERSION` 7 is what makes such a row
    visibly stale, and an ordinary rescore refreshes it.
    """
    if not data:
        return NOT_STATED_EXPERIENCE
    return ExperienceReading(
        requirement=ExperienceRequirement(data["requirement"]),
        min_years=data.get("min_years"),
        quote=data.get("quote"),
        entry_signals=tuple(EntrySignal(value) for value in data.get("entry_signals", ())),
    )


def _employment_to_dict(reading: EmploymentReading) -> dict[str, Any]:
    return {
        "relationship": reading.relationship.value,
        "regime": reading.regime.value,
        "source": reading.source.value,
        "confidence": reading.confidence.value,
        "evidence": reading.evidence,
    }


def _employment_from_dict(data: Any) -> EmploymentReading:
    """Read one back, or say nothing for a row written before it existed.

    A row scored under schema 3 has no `employment` key at all, and 2,563
    closed postings in the owner's corpus are exactly that. They come back
    UNRESOLVED rather than crashing, because the honest thing to say about a
    reading that was never taken is that it was never taken.
    """
    if not isinstance(data, dict):
        return UNRESOLVED_EMPLOYMENT
    return EmploymentReading(
        relationship=EmploymentRelationship(data["relationship"]),
        regime=LocalContractRegime(data["regime"]),
        source=EmploymentSource(data["source"]),
        confidence=AnalysisConfidence(data["confidence"]),
        evidence=data.get("evidence"),
    )


def _domestic_to_dict(reading: DomesticReading) -> dict[str, Any]:
    return {
        "context": reading.context.value,
        "confidence": reading.confidence.value,
        "evidence": reading.evidence,
        "signal": reading.signal,
    }


def _domestic_from_dict(data: Any) -> DomesticReading:
    if not isinstance(data, dict):
        return UNRESOLVED_DOMESTIC
    return DomesticReading(
        context=DomesticContext(data["context"]),
        confidence=AnalysisConfidence(data["confidence"]),
        evidence=data.get("evidence"),
        signal=data.get("signal"),
    )


def _seniority_to_dict(reading: SeniorityReading) -> dict[str, Any]:
    return {
        "value": reading.value.value,
        "source": reading.source.value,
        "confidence": reading.confidence.value,
        "evidence": reading.evidence,
    }


def _seniority_from_dict(data: Any) -> SeniorityReading:
    """Read a reading back, tolerating the shape that came before it.

    A row written under the previous semantics stored a bare string, and there
    are 21,202 of them in the owner's corpus. They come back as DEFAULT with no
    evidence rather than as a crash, because the honest thing to say about a
    value whose provenance was never recorded is that we do not know it. A
    rescore replaces them.
    """
    if isinstance(data, str):
        legacy = {"MANAGER": Seniority.LEAD, "DIRECTOR": Seniority.LEAD, "UNCLEAR": Seniority.MID}
        value = legacy.get(data) or Seniority(data)
        return SeniorityReading(
            value=value,
            source=SenioritySource.DEFAULT,
            confidence=AnalysisConfidence.LOW,
            evidence=None,
        )
    return SeniorityReading(
        value=Seniority(data["value"]),
        source=SenioritySource(data["source"]),
        confidence=AnalysisConfidence(data["confidence"]),
        evidence=data.get("evidence"),
    )


def deserialise_match(payload: str) -> MatchResult:
    """The exact inverse of :func:`serialise_match`.

    Enums come back as enums and every sequence as a tuple, because the domain
    dataclasses are frozen and compared by value: a list where a tuple belongs
    makes two identical results unequal.
    """
    data: dict[str, Any] = json.loads(payload)
    title = data.get("title")
    return MatchResult(
        config_id=data["config_id"],
        config_version=data["config_version"],
        schema_version=data["schema_version"],
        match_score=data["match_score"],
        data_confidence=data["data_confidence"],
        eligibility_status=EligibilityStatus(data["eligibility_status"]),
        screening_state=ScreeningState(data["screening_state"]),
        screening_reason=data.get("screening_reason", ""),
        fit_band=FitBand(data["fit_band"]),
        analysis_confidence=AnalysisConfidence(data["analysis_confidence"]),
        title=_title_from_dict(title) if title else None,
        components=tuple(_component_from_dict(c) for c in data.get("components", ())),
        penalties=tuple(_penalty_from_dict(p) for p in data.get("penalties", ())),
        penalty_total=data.get("penalty_total", 0.0),
        gates=tuple(_gate_from_dict(g) for g in data.get("gates", ())),
        confidence_items=tuple(_confidence_from_dict(i) for i in data.get("confidence_items", ())),
        signals=tuple(_signal_from_dict(s) for s in data.get("signals", ())),
        unknowns=tuple(data.get("unknowns", ())),
        seniority=_seniority_from_dict(data["seniority"]),
        employment=_employment_from_dict(data.get("employment")),
        domestic=_domestic_from_dict(data.get("domestic")),
        experience=_experience_from_dict(data.get("experience")),
        posting_facts=dict(data.get("posting_facts") or {}),
        computed_at=data.get("computed_at", ""),
    )


# =====================================================================
# TRANSACTIONS
# =====================================================================


@contextmanager
def _atomic(conn: sqlite3.Connection) -> Iterator[None]:
    """Make a multi-statement write all-or-nothing, nested or not.

    The connection runs in autocommit, so two statements issued back to back
    are two independent commits and a crash between them leaves a status
    change with no history entry. ``BEGIN`` fixes that -- but SQLite refuses a
    ``BEGIN`` inside an open transaction, and callers (the CLI, the tests) very
    reasonably wrap whole operations in ``db.transaction``. So this yields to
    an existing transaction rather than starting a second one: whoever opened
    it owns the commit, and the atomicity guarantee holds either way.

    ``IMMEDIATE``, not the default deferred, and the difference is not
    academic. A deferred transaction that reads and then writes takes the write
    lock late; under WAL a second writer that arrives in between gets
    ``SQLITE_BUSY_SNAPSHOT``, and SQLite does **not** run the busy handler for
    that error -- so despite `busy_timeout` the loser fails instantly with
    "database is locked". Concretely: a `rescore` running while someone edits a
    status in the UI would lose the edit behind a generic 500. Taking the lock
    up front makes `busy_timeout` apply, and the second writer waits instead.
    """
    if conn.in_transaction:
        yield
        return
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


class _Repo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn


#: How many of a period there are in a year. Used only to make two salaries
#: comparable within ONE currency; see `facet_columns` for why no rate between
#: currencies appears anywhere in this file.
#:
#: 2080 is 40 hours x 52 weeks and 260 is five days x 52 weeks -- the
#: conventional full-time year. They are approximations of a contract nobody
#: stated, which is exactly why they are named here rather than inlined.
_PERIODS_PER_YEAR: dict[str, float] = {
    "HOUR": 2080.0,
    "HOURLY": 2080.0,
    "DAY": 260.0,
    "DAILY": 260.0,
    "WEEK": 52.0,
    "WEEKLY": 52.0,
    "MONTH": 12.0,
    "MONTHLY": 12.0,
    "YEAR": 1.0,
    "YEARLY": 1.0,
    "ANNUAL": 1.0,
}


def annualise(value: float | None, period: str | None) -> float | None:
    """A stated figure expressed per year, or None if that cannot be known.

    An unstated period returns None rather than assuming yearly. Most postings
    that name a number do name its period, and treating a monthly 8,000 as an
    annual 8,000 would put it below every filter a person sets -- silently
    hiding the posting rather than failing to place it.
    """
    if value is None or not period:
        return None
    factor = _PERIODS_PER_YEAR.get(str(period).strip().upper())
    return value * factor if factor else None


#: What `facet_columns` returns, in order.
#:
#: The tuple is positional because the INSERT that consumes it is, and a
#: positional tuple is exactly the thing a caller indexes with `[-1]` -- which
#: two tests did, and which broke the moment a thirteenth column was added
#: between them and the end. Naming the positions costs one line per column and
#: means a reader, a test and a future column never have to count.
FACET_COLUMN_NAMES: tuple[str, ...] = (
    "work_model",
    "employment_type",
    "salary_min",
    "salary_max",
    "salary_currency",
    "salary_period",
    "salary_annual_min",
    "salary_annual_max",
    "countries",
    "regions",
    "employment_context",
    "contract_regime",
    "content_completeness",
    "experience_requirement",
    "experience_min_years",
    "entry_signals",
)


def facet_columns(result: MatchResult) -> tuple[object, ...]:
    """The filterable columns, lifted out of the result that produced them.

    `FACET_COLUMN_NAMES` names them, in this order.

    Nothing is derived here that the matcher did not already conclude: the
    salary and the work model come from `posting_facts`, which is the reading
    the compensation component was scored from, so the card, the score and the
    filter cannot disagree about one posting.

    **No currency is ever converted.** `salary_annual_*` only multiplies by the
    period, so "at least 120,000 a year" can be asked of a posting quoting a
    monthly rate in the SAME currency. There is no offline exchange rate that
    is true on the day a posting was collected, and a wrong one would silently
    reorder every salary comparison in the product -- so `min_salary` requires
    a currency at the API instead.

    Countries and regions are what the BOARD PRINTED, resolved through
    `config/places.yaml`. They are not `hiring_scope` and nothing may read them
    as one (CLAUDE.md invariant 3). Pipe-fenced like `membership`, so `|BR|`
    cannot match a longer code.
    """
    facts = result.posting_facts or {}
    salary = facts.get("salary") or {}
    if not isinstance(salary, dict):
        salary = {}

    minimum = _as_float(salary.get("min"))
    maximum = _as_float(salary.get("max"))
    period = _as_text(salary.get("period"))

    countries = _fence(facts.get("countries"))
    regions = _fence(facts.get("regions"))

    # The last two are readings rather than posting fields, and they are here
    # for one reason: `domestic.context` and the Brazilian regime have been
    # computed on every posting since V1.2 and unreachable from SQL the whole
    # time, so the only place either appeared was a sentence in one drawer.
    #
    # `employment_context` IS NOT AN ELIGIBILITY COLUMN. A posting offering a
    # 401(k) and never mentioning hiring elsewhere is probably United States
    # domestic employment; that is worth telling somebody abroad and it is not
    # the employer having said no. `eligibility_status` stays the only column
    # that records a refusal.
    return (
        _as_text(facts.get("work_model")),
        _as_text(facts.get("employment_type")),
        minimum,
        maximum,
        _as_text(salary.get("currency")),
        period,
        annualise(minimum, period),
        annualise(maximum, period),
        countries,
        regions,
        result.domestic.context.value if result.domestic else None,
        _regime_of(result),
        # AND WHOSE SHORTNESS THIS IS. Provenance rather than a reading: the
        # matcher was handed it, having derived it from the adapter's declared
        # ability to obtain a whole description. Stored so a filter can ask,
        # because a filter may never LIKE over `result_json` -- migration 0013
        # exists because one did.
        _as_text(facts.get("content_completeness")),
        # WHAT THE POSTING ASKED FOR, and which invitations it extended.
        # Three columns because they are three facts: how hard the ask is, the
        # figure when one was stated unambiguously, and the set of things the
        # employer said about beginners. `min_years` is 0 when the posting said
        # none is needed and NULL when no figure was read -- see migration 0027
        # for why folding those together would undo the whole point.
        result.experience.requirement.value if result.experience else None,
        result.experience.min_years if result.experience else None,
        _fence([signal.value for signal in result.experience.entry_signals])
        if result.experience
        else "",
    )


def _regime_of(result: MatchResult) -> str | None:
    """The named national engagement regime, when one was actually read.

    `UNRESOLVED` is stored as NULL rather than as the word. It is the honest
    answer for the overwhelming majority of postings, and a column holding
    eighteen thousand copies of "we could not tell" is a column whose index
    answers nothing -- while NULL is what every other unknown facet here
    already uses.
    """
    employment = result.employment
    if employment is None:
        return None
    regime = employment.regime.value
    return None if regime == "UNRESOLVED" else regime


def _as_float(value: object) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _as_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text.upper() if text else None


def _fence(values: object) -> str:
    """``|BR|AR|`` for a sequence, ``''`` for nothing.

    Empty string rather than ``||`` for the empty case, so "this row resolved
    to no country" and "this row has not been re-scored yet" look identical to
    a filter -- both match nothing, which is the correct answer to both.
    """
    if not values or not isinstance(values, (list, tuple)):
        return ""
    cleaned = sorted({str(v).strip().upper() for v in values if str(v).strip()})
    return "|" + "|".join(cleaned) + "|" if cleaned else ""


# =====================================================================
# THE SCORE
# =====================================================================


class MatchRepo(_Repo):
    """Deterministic scores, one row per (posting, configuration version)."""

    def store(
        self,
        job_id: str,
        content_hash: str,
        result: MatchResult,
        *,
        config_digest: str,
        input_digest: str | None = None,
        computed_at: str | None = None,
    ) -> str:
        """Write the score, replacing any earlier row for the same version.

        An upsert rather than a delete-and-insert: re-scoring the same posting
        under the same configuration can only reproduce the same arithmetic, so
        there is nothing to preserve, and ``ON CONFLICT ... DO UPDATE`` keeps
        the row identity stable for anything that later references it.
        """
        row_id = new_id()
        title_class = result.title.resolved_class if result.title else TitleClass.UNCLASSIFIED
        self.conn.execute(
            "INSERT INTO job_match"
            " (id, job_id, content_hash, config_id, config_version, config_digest,"
            "  input_digest,"
            "  schema_version, match_score, data_confidence, fit_band, analysis_confidence,"
            "  screening_state, eligibility_status, title_class, seniority, result_json,"
            "  membership, work_model, employment_type, salary_min, salary_max,"
            "  salary_currency, salary_period, salary_annual_min, salary_annual_max,"
            "  countries, regions, employment_context, contract_regime,"
            "  content_completeness, experience_requirement, experience_min_years,"
            "  entry_signals, computed_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,"
            "         ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
            " ON CONFLICT (job_id, config_id, config_version) DO UPDATE SET"
            "   content_hash        = excluded.content_hash,"
            "   config_digest       = excluded.config_digest,"
            "   input_digest        = excluded.input_digest,"
            "   schema_version      = excluded.schema_version,"
            "   match_score         = excluded.match_score,"
            "   data_confidence     = excluded.data_confidence,"
            "   fit_band            = excluded.fit_band,"
            "   analysis_confidence = excluded.analysis_confidence,"
            "   screening_state     = excluded.screening_state,"
            "   eligibility_status  = excluded.eligibility_status,"
            "   title_class         = excluded.title_class,"
            "   seniority           = excluded.seniority,"
            "   result_json         = excluded.result_json,"
            "   membership          = excluded.membership,"
            "   work_model          = excluded.work_model,"
            "   employment_type     = excluded.employment_type,"
            "   salary_min          = excluded.salary_min,"
            "   salary_max          = excluded.salary_max,"
            "   salary_currency     = excluded.salary_currency,"
            "   salary_period       = excluded.salary_period,"
            "   salary_annual_min   = excluded.salary_annual_min,"
            "   salary_annual_max   = excluded.salary_annual_max,"
            "   countries           = excluded.countries,"
            "   regions             = excluded.regions,"
            "   employment_context  = excluded.employment_context,"
            "   contract_regime     = excluded.contract_regime,"
            "   content_completeness = excluded.content_completeness,"
            "   experience_requirement = excluded.experience_requirement,"
            "   experience_min_years = excluded.experience_min_years,"
            "   entry_signals       = excluded.entry_signals,"
            "   computed_at         = excluded.computed_at",
            (
                row_id,
                job_id,
                content_hash,
                result.config_id,
                result.config_version,
                config_digest,
                input_digest,
                result.schema_version,
                result.match_score,
                result.data_confidence,
                result.fit_band.value,
                result.analysis_confidence.value,
                result.screening_state.value,
                result.eligibility_status.value,
                title_class.value,
                result.seniority.value.value,
                serialise_match(result),
                membership_of(result),
                *facet_columns(result),
                computed_at or result.computed_at or now_utc(),
            ),
        )
        row = self.conn.execute(
            "SELECT id FROM job_match WHERE job_id = ? AND config_id = ? AND config_version = ?",
            (job_id, result.config_id, result.config_version),
        ).fetchone()
        return str(row["id"])

    def replay_source_version(
        self, config_id: str, target_version: int, input_digest: str, min_schema: int
    ) -> int | None:
        """The newest scored version whose readings this build may reuse.

        ONE QUERY PER PASS, not per posting. A replay needs a row that was read
        by the same readers from the same reading configuration; which version
        that is cannot change while a pass runs, so it is resolved once and the
        per-page lookup is then an ordinary probe of
        `idx_job_match_population` on `(config_id, config_version, job_id)`.

        Deliberately NOT the target version. A row already written under the
        version being computed is this pass's own output, and replaying from it
        would be reading an answer to the question being asked.
        """
        row = self.conn.execute(
            "SELECT config_version FROM job_match"
            " WHERE config_id = ? AND config_version <> ?"
            "   AND input_digest = ? AND schema_version >= ?"
            " ORDER BY config_version DESC LIMIT 1",
            (config_id, target_version, input_digest, min_schema),
        ).fetchone()
        return int(row["config_version"]) if row else None

    def replay_candidates(
        self,
        job_ids: Sequence[str],
        *,
        config_id: str,
        source_version: int,
        input_digest: str,
        min_schema: int,
    ) -> dict[str, tuple[str, str]]:
        """``job_id -> (result_json, content_hash)`` for rows safe to replay from.

        The digest and the schema floor are re-checked here rather than trusted
        from :meth:`replay_source_version`, because that method proves such a row
        EXISTS and this one proves it exists FOR THIS POSTING. A corpus can hold
        a version that was complete for one board and partial for another.

        The caller still has to check the posting's content hash and its input
        revision against the row. This returns what is needed to do that; it
        does not decide.
        """
        if not job_ids:
            return {}
        found: dict[str, tuple[str, str]] = {}
        ids = list(job_ids)
        for start in range(0, len(ids), 500):
            chunk = ids[start : start + 500]
            marks = ",".join("?" for _ in chunk)
            rows = self.conn.execute(
                f"SELECT job_id, result_json, content_hash FROM job_match"
                f" WHERE config_id = ? AND config_version = ? AND job_id IN ({marks})"
                f"   AND input_digest = ? AND schema_version >= ?",
                (config_id, source_version, *chunk, input_digest, min_schema),
            ).fetchall()
            for row in rows:
                found[str(row["job_id"])] = (str(row["result_json"]), str(row["content_hash"]))
        return found

    def settle_dirty(self, job_ids: Sequence[str]) -> int:
        """Keep work pending until rescore commits a revision-checked receipt and FTS.

        Legacy seed/import callers pass ids without an input snapshot. They cannot
        safely acknowledge work; an offline replay is conservative and bounded.
        """
        return 0

    def get(self, job_id: str, config_id: str, config_version: int) -> MatchResult | None:
        row = self.conn.execute(
            "SELECT result_json FROM job_match"
            " WHERE job_id = ? AND config_id = ? AND config_version = ?",
            (job_id, config_id, config_version),
        ).fetchone()
        if row is None:
            return None
        return deserialise_match(str(row["result_json"]))

    def count_for(self, config_id: str, config_version: int) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) AS n FROM job_match WHERE config_id = ? AND config_version = ?",
            (config_id, config_version),
        ).fetchone()
        return int(row["n"])

    def digests_for(self, config_id: str, config_version: int) -> set[str]:
        """Which configuration BYTES the stored scores were computed from.

        Normally one value. More than one means the file was edited without
        bumping `config_version`, so rows computed under different rules are
        sitting in one version and being compared to each other. The interface
        reports that rather than letting it stay invisible.
        """
        rows = self.conn.execute(
            "SELECT DISTINCT config_digest FROM job_match "
            "WHERE config_id = ? AND config_version = ?",
            (config_id, config_version),
        ).fetchall()
        return {str(row["config_digest"]) for row in rows}

    def scored_job_ids(self, config_id: str, config_version: int) -> set[str]:
        """Every job already scored under this configuration version AND the
        current result schema.

        Read once at the start of a corpus pass so an interrupted `rescore`
        resumes instead of recomputing 18,000 rows. One query and a set of
        ULIDs is a few megabytes; the alternative is 18,000 point lookups.

        **The schema version is part of "already scored", and it was not.** A
        row written before `MATCH_SCHEMA_VERSION` moved carries empty facet
        columns, so the country, region, worksite and salary filters return
        nothing for it. Treating it as done meant an ordinary `rescore` skipped
        exactly the rows that needed rewriting, and only `--force` fixed a
        database -- which nobody would know to run, because nothing said the
        rows were stale.
        """
        rows = self.conn.execute(
            "SELECT job_id FROM job_match"
            " WHERE config_id = ? AND config_version = ? AND schema_version >= ?",
            (config_id, config_version, MATCH_SCHEMA_VERSION),
        ).fetchall()
        return {str(row["job_id"]) for row in rows}

    def stale_schema_count(self, config_id: str, config_version: int) -> int:
        """Rows scored under an OLDER result schema than this build writes.

        Reported by `/api/health` so the interface can say "these scores were
        computed before the filter columns existed" instead of four filters
        quietly answering zero. The same discipline as the stale search index,
        which is reported for the same reason.
        """
        row = self.conn.execute(
            "SELECT COUNT(*) AS n FROM job_match"
            " WHERE config_id = ? AND config_version = ? AND schema_version < ?",
            (config_id, config_version, MATCH_SCHEMA_VERSION),
        ).fetchone()
        return int(row["n"])

    def delete_for(self, config_id: str, config_version: int) -> int:
        """Drop every score for one configuration version. Returns the count.

        Used before a full re-score under an unchanged version, when the
        configuration file itself was edited in place. Touches nothing a human
        wrote: `job_application` and its history are a different table for
        exactly this reason.
        """
        cursor = self.conn.execute(
            "DELETE FROM job_match WHERE config_id = ? AND config_version = ?",
            (config_id, config_version),
        )
        return int(cursor.rowcount)


# =====================================================================
# THE HUMAN'S WORKFLOW STATE
# =====================================================================


class ApplicationRepo(_Repo):
    """Where the person is with a job, plus the append-only history of moves."""

    def get(self, job_id: str) -> tuple[ApplicationStatus, str | None, bool, str | None] | None:
        """``(status, applied_at, saved, notes)``, or None if never edited.

        None is meaningfully different from ``(DISCOVERED, None, False, None)``:
        it says nobody has touched this job. The query layer renders both the
        same way; only this method can tell them apart.
        """
        row = self.conn.execute(
            "SELECT status, applied_at, saved, notes FROM job_application WHERE job_id = ?",
            (job_id,),
        ).fetchone()
        if row is None:
            return None
        return (
            ApplicationStatus(row["status"]),
            row["applied_at"],
            bool(row["saved"]),
            row["notes"],
        )

    def _current_status(self, job_id: str) -> ApplicationStatus | None:
        row = self.conn.execute(
            "SELECT status FROM job_application WHERE job_id = ?", (job_id,)
        ).fetchone()
        return ApplicationStatus(row["status"]) if row is not None else None

    def _current_date(self, job_id: str) -> str | None:
        """The stored `applied_at`, so an omitted one can mean "keep it"."""
        row = self.conn.execute(
            "SELECT applied_at FROM job_application WHERE job_id = ?", (job_id,)
        ).fetchone()
        return row["applied_at"] if row is not None else None

    def set_status(
        self,
        job_id: str,
        status: ApplicationStatus,
        *,
        applied_at: str | None = None,
        note: str | None = None,
        now: str | None = None,
    ) -> None:
        """Move a job to a status, repairing the date and recording the move.

        ``normalise_application_state`` decides the date, not this method: a
        post-application status with no date gets today's, and NO status drops
        one. The default is derived from ``now`` rather than read from a clock
        in here, so a caller can make the whole operation deterministic.

        **A status move never clears the date, in either direction.** Moving
        APPLIED back to SHORTLISTED used to NULL the column, so one drag on the
        board destroyed the record of having applied. The date belongs to the
        application, not to the stage; :meth:`set_applied_at` is the only way
        to remove it, and it exists precisely so that removing it has to be
        something a person asked for. ADR-0012.

        The event row is written in the same transaction as the update. A
        history that can lag behind the state it describes is worse than no
        history, because it looks authoritative.

        **An omitted `applied_at` means "leave it alone", not "clear it".**
        `normalise_application_state` documents that REJECTED and ARCHIVED keep
        whatever date they have, but this method never loaded the stored one,
        so `APPLIED(2026-01-01) -> REJECTED` with no date in the request wrote
        `NULL` and flipped `has_applied` from true to false. That is silent
        loss of the single fact this product promises to track, on the most
        ordinary transition there is: applying, and then being turned down.
        """
        stamp = now or now_utc()
        with _atomic(self.conn):
            previous = self._current_status(job_id)
            effective_date = applied_at if applied_at is not None else self._current_date(job_id)
            resolved_status, resolved_date = normalise_application_state(
                status, effective_date, default_applied_at=stamp[:10]
            )
            self.conn.execute(
                "INSERT INTO job_application"
                " (id, job_id, status, applied_at, saved, notes, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, 0, NULL, ?, ?)"
                " ON CONFLICT (job_id) DO UPDATE SET"
                "   status     = excluded.status,"
                "   applied_at = excluded.applied_at,"
                "   updated_at = excluded.updated_at",
                (new_id(), job_id, resolved_status.value, resolved_date, stamp, stamp),
            )
            self.conn.execute(
                "INSERT INTO job_application_event"
                " (id, job_id, from_status, to_status, applied_at, note, occurred_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    new_id(),
                    job_id,
                    previous.value if previous is not None else None,
                    resolved_status.value,
                    resolved_date,
                    note,
                    stamp,
                ),
            )

    def set_applied_at(
        self,
        job_id: str,
        applied_at: str | None,
        *,
        note: str | None = None,
        now: str | None = None,
    ) -> None:
        """Set or CLEAR the applied date, without touching the status.

        The other half of ADR-0012. Once a status move stops clearing the date,
        there has to be a way to say "I recorded that wrongly, I never actually
        applied" -- otherwise the fix trades silent data loss for data you
        cannot correct, which is not obviously the better failure.

        Here ``None`` means CLEAR, and it can mean that safely because the date
        is what this method is *about*. That is the whole difference from
        :meth:`set_status`, where an omitted ``applied_at`` means "leave it":
        there the field is incidental to the request, so absence is silence;
        here it is the subject of the request, so absence is an instruction.
        The two readings live in two methods rather than in one flag, because a
        flag is a thing a caller forgets.

        Clearing a date on a post-application status would immediately be
        undone -- ``normalise_application_state`` puts today's date back -- so
        it is refused with the reason instead, and the caller is told to move
        the status first. Answering "cleared" and storing something else is the
        failure mode this whole area keeps producing.

        The move is written to the history like any other, with `from_status`
        equal to `to_status`, because "the date changed and the stage did not"
        is a thing that happened and the drawer shows what happened.
        """
        stamp = now or now_utc()
        with _atomic(self.conn):
            current = self._current_status(job_id)
            status = current if current is not None else DEFAULT_APPLICATION_STATUS
            if applied_at is None and requires_applied_at(status):
                raise ApplicationDateRefused(
                    f"{status.value} means an application was sent, so its date "
                    "cannot be cleared. Move the status first if it was recorded "
                    "in error."
                )
            self.conn.execute(
                "INSERT INTO job_application"
                " (id, job_id, status, applied_at, saved, notes, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, 0, NULL, ?, ?)"
                " ON CONFLICT (job_id) DO UPDATE SET"
                "   applied_at = excluded.applied_at,"
                "   updated_at = excluded.updated_at",
                (new_id(), job_id, status.value, applied_at, stamp, stamp),
            )
            self.conn.execute(
                "INSERT INTO job_application_event"
                " (id, job_id, from_status, to_status, applied_at, note, occurred_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    new_id(),
                    job_id,
                    status.value,
                    status.value,
                    applied_at,
                    note
                    or (
                        "applied date cleared"
                        if applied_at is None
                        else f"applied date set to {applied_at}"
                    ),
                    stamp,
                ),
            )

    def set_saved(self, job_id: str, saved: bool, *, now: str | None = None) -> None:
        """Bookmark or un-bookmark. Appends no event and moves no status.

        Saving is orthogonal to the workflow: a job can be bookmarked at any
        stage, and writing a status event for it would put a move into the
        history that never happened. The status and date columns are named
        only in the INSERT that creates the row, never in the UPDATE.
        """
        stamp = now or now_utc()
        self.conn.execute(
            "INSERT INTO job_application"
            " (id, job_id, status, applied_at, saved, notes, created_at, updated_at)"
            " VALUES (?, ?, ?, NULL, ?, NULL, ?, ?)"
            " ON CONFLICT (job_id) DO UPDATE SET"
            "   saved      = excluded.saved,"
            "   updated_at = excluded.updated_at",
            (
                new_id(),
                job_id,
                DEFAULT_APPLICATION_STATUS.value,
                int(saved),
                stamp,
                stamp,
            ),
        )

    def set_hidden(
        self,
        job_id: str,
        hidden: bool,
        *,
        reason: str | None = None,
        now: str | None = None,
    ) -> str | None:
        """Hide a posting from the discovery views, or put it back.

        Appends no event and moves no status, for the same reason `set_saved`
        does not: hiding is not a position in the application workflow. A job
        she has applied to and then hidden is still APPLIED, and writing a
        move into `job_application_event` would put a transition into the
        history that never happened.

        Returns the timestamp written, so a caller can offer an Undo that names
        the exact row it would restore rather than the most recent one it can
        find.

        `reason` is OPTIONAL and stays optional. Hiding without saying why is
        the commonest case and must remain unremarkable; the column exists so a
        reason CAN be counted, not so that one is owed. It is cleared with the
        hide, because a reason attached to a posting that is no longer hidden is
        a note about a decision that was reversed -- and keeping it would invite
        a later query to treat "she once said wrong country about this" as a
        signal. It is not one.
        """
        stamp = now or now_utc()
        self.conn.execute(
            "INSERT INTO job_application"
            " (id, job_id, status, applied_at, saved, notes, hidden_at, hidden_reason,"
            "  created_at, updated_at)"
            " VALUES (?, ?, ?, NULL, 0, NULL, ?, ?, ?, ?)"
            " ON CONFLICT (job_id) DO UPDATE SET"
            "   hidden_at     = excluded.hidden_at,"
            "   hidden_reason = excluded.hidden_reason,"
            "   updated_at    = excluded.updated_at",
            (
                new_id(),
                job_id,
                DEFAULT_APPLICATION_STATUS.value,
                stamp if hidden else None,
                reason if hidden else None,
                stamp,
                stamp,
            ),
        )
        return stamp if hidden else None

    def hidden_reasons(self) -> dict[str, int]:
        """What she has said about the postings she set aside, counted.

        **Evidence for a conversation, never an input to one.** Nothing in
        `career_agent.match` may read this, and `test_hidden_reason.py` asserts
        that a reason changes no score and no preference.

        Counted rather than listed because counting is the point: "eleven of
        the last twenty were the wrong seniority" is actionable and "eleven
        notes" is not.
        """
        rows = self.conn.execute(
            "SELECT hidden_reason, COUNT(*) AS n FROM job_application"
            " WHERE hidden_at IS NOT NULL AND hidden_reason IS NOT NULL"
            " GROUP BY hidden_reason ORDER BY n DESC"
        ).fetchall()
        return {str(row[0]): int(row[1]) for row in rows}

    def siblings_of(self, job_id: str) -> list[str]:
        """Every posting the grouped views fold into the same card as this one.

        The SAME identity the query layer groups by -- `(company_id, title)` --
        read from the one place that defines it rather than spelled again here,
        because a second definition that drifted would hide a set the reader
        never saw as one card.

        Includes `job_id` itself, and returns just it when nothing else shares
        the pair.
        """
        rows = self.conn.execute(
            "SELECT other.id"
            " FROM job me JOIN job other"
            "   ON other.company_id = me.company_id AND other.title = me.title"
            " WHERE me.id = ?"
            " ORDER BY other.id",
            (job_id,),
        ).fetchall()
        return [str(row["id"]) for row in rows]

    def set_notes(self, job_id: str, notes: str | None, *, now: str | None = None) -> None:
        """Replace the free-text note. Appends no event and moves no status."""
        stamp = now or now_utc()
        self.conn.execute(
            "INSERT INTO job_application"
            " (id, job_id, status, applied_at, saved, notes, created_at, updated_at)"
            " VALUES (?, ?, ?, NULL, 0, ?, ?, ?)"
            " ON CONFLICT (job_id) DO UPDATE SET"
            "   notes      = excluded.notes,"
            "   updated_at = excluded.updated_at",
            (new_id(), job_id, DEFAULT_APPLICATION_STATUS.value, notes, stamp, stamp),
        )

    def history(self, job_id: str) -> list[dict[str, Any]]:
        """Every recorded move, oldest first.

        ``occurred_at`` is ISO-8601 UTC text, so a plain string sort is correct
        date ordering; ``id`` breaks ties because ULIDs are time-sortable and
        two moves can land in the same second.
        """
        rows = self.conn.execute(
            "SELECT id, job_id, from_status, to_status, applied_at, note, occurred_at"
            " FROM job_application_event WHERE job_id = ? ORDER BY occurred_at, id",
            (job_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    def counts_by_status(self) -> dict[str, int]:
        """How many jobs sit at each status, counting EDITED jobs only.

        A job with no row here has never been touched. Counting those as
        DISCOVERED would report the size of the corpus as a workflow fact;
        :meth:`ScoredJobQuery.facets` is where the implicit ones are folded in,
        because only there is the population being counted well defined.
        """
        rows = self.conn.execute(
            "SELECT status, COUNT(*) AS n FROM job_application GROUP BY status"
        ).fetchall()
        return {str(row["status"]): int(row["n"]) for row in rows}


# =====================================================================
# THE ONE QUERY
# =====================================================================


@dataclass(frozen=True)
class JobFilter:
    """Everything the list view can ask for, in one immutable value.

    Frozen because a filter is passed to ``count``, ``page`` and ``facets`` in
    turn, and a mutable one that a helper adjusted along the way is how three
    numbers on one screen stop agreeing.
    """

    #: Case-insensitive substring over title, company name and description.
    search: str | None = None
    #: Company SLUGS, not display names: the slug is stable, the name is not.
    companies: tuple[str, ...] = ()
    providers: tuple[str, ...] = ()
    #: `job_match.title_class` values.
    role_classes: tuple[str, ...] = ()
    #: `ApplicationStatus` values. An untouched job counts as DISCOVERED.
    statuses: tuple[str, ...] = ()
    eligibility: tuple[str, ...] = ()
    fit_bands: tuple[str, ...] = ()
    min_score: int | None = None
    max_score: int | None = None
    min_confidence: int | None = None
    saved_only: bool = False
    #: Only postings a LOCAL model has actually observed. One of the five
    #: pipeline stages the interface distinguishes, and the only one that is a
    #: fact about our own processing rather than about the posting.
    enriched_only: bool = False
    has_salary: bool | None = None
    remote_only: bool = False
    posted_within_days: int | None = None
    #: Postings THIS MACHINE first held STRICTLY AFTER an ISO-8601 timestamp.
    #:
    #: `first_seen_at`, deliberately, and never `posted_at`. This answers "what
    #: is new SINCE I LAST LOOKED", which is a fact about a reader; `posted_at`
    #: is a fact about an employer, and a board that publishes no dates would
    #: make every one of its postings permanently invisible to the question.
    #: The two are never mixed into one filter for the same reason the digest
    #: never prints one word for both.
    #:
    #: Strictly AFTER, not at-or-after. If she looked at T she saw everything
    #: this machine held at T, so a posting whose `first_seen_at` IS T was in
    #: the list she was reading. At-or-after re-showed the whole list the
    #: moment she marked it read.
    first_seen_after: str | None = None
    #: Lexicon signal ids that must have FIRED, not merely been looked for.
    signals: tuple[str, ...] = ()
    #: `LIKELY_US_DOMESTIC`, `INTERNATIONAL_STATED` or `UNRESOLVED`.
    #:
    #: HOW A POSTING READS, never whether the candidate may take it. Kept as
    #: its own field beside `eligibility` rather than folded into it, because
    #: the whole point of the reading is that the two answers are different: a
    #: 401(k) and silence about hiring elsewhere is context worth surfacing and
    #: is not a refusal. A caller asking for one can never receive the other.
    employment_context: tuple[str, ...] = ()
    #: `ContentCompleteness` values. HOW MUCH OF THE POSTING was stored, and
    #: never a judgement about the job: `PARTIAL_CONTENT` says the SOURCE
    #: returns an excerpt and offers no way to fetch the rest, which is a fact
    #: about an aggregator rather than about an employer.
    content_completeness: tuple[str, ...] = ()
    #: `CLT` or `PJ`, when the posting actually named one. There is no
    #: `UNRESOLVED` to ask for: the column is NULL when nothing was read, and
    #: "postings that did not say" is the absence of this filter rather than a
    #: value of it.
    contract_regime: tuple[str, ...] = ()
    #: Collapse the postings an employer published once per location down to
    #: one representative row per `(company_id, title)`.
    #:
    #: **Default False, and that default is load-bearing.** Every existing
    #: caller -- the CLI, the table view, every test written before this
    #: existed -- keeps the population it has always had, and grouping is only
    #: ever something that was asked for. See :meth:`ScoredJobQuery._where` for
    #: which row wins and why the choice is deterministic.
    group_duplicates: bool = False

    # -- the twelve that section 16 of the corrective report listed as absent --
    #
    # Every one reads a column written by `facet_columns` from the SAME
    # `MatchResult` the card renders, so a filter cannot disagree with the row
    # it returns. None of them touches `result_json`, which holds evidence
    # quotes copied out of job descriptions -- migration 0013 exists because a
    # filter pointed at that blob once already.
    #
    # `countries` and `regions` are what the BOARD PRINTED, resolved through
    # `config/places.yaml`. They are NOT hiring scope, and a posting that
    # resolved to nothing is EXCLUDED by them rather than included: absence is
    # never permission, so silence about geography cannot satisfy a request for
    # a country. Invariant 3 keeps eligibility elsewhere.
    countries: tuple[str, ...] = ()
    regions: tuple[str, ...] = ()
    #: Shorthands for two `regions` values people ask for by name. Separate
    #: controls because "can I take this from Latin America" and "is this open
    #: to anywhere" are the two questions this candidate actually asks, and
    #: burying them in a region list makes them one click further away.
    latam_only: bool = False
    worldwide_only: bool = False
    #: REMOTE / HYBRID / ONSITE / NOT_STATED. Distinct from `remote_only`,
    #: which is the older convenience over `location_raw` and stays for the
    #: URLs that already carry it.
    worksites: tuple[str, ...] = ()
    seniorities: tuple[str, ...] = ()
    employment_types: tuple[str, ...] = ()
    #: Annualised, and compared WITHIN one currency. `salary_currencies` must
    #: name exactly one when this is set -- the API enforces it -- because
    #: nothing in this system converts between currencies and a comparison
    #: across them would be silently meaningless.
    min_salary: int | None = None
    salary_currencies: tuple[str, ...] = ()
    salary_periods: tuple[str, ...] = ()
    #: Lexicon signal ids restricted to the technology family. The same index
    #: `signals` reads; a separate parameter because "which tools" and "which
    #: signals fired" are different questions to a person.
    technologies: tuple[str, ...] = ()
    # -- what the posting asked for in the way of previous experience ------
    #
    # Three fields because migration 0027 stored three facts, and because the
    # three questions a person actually asks are different questions.
    #
    #: `ExperienceRequirement` values the posting must carry. A closed
    #: vocabulary, checked at the API, so "no rows" and "that is not a value"
    #: stay tellable apart.
    experience_requirements: tuple[str, ...] = ()
    #: The most previous experience the posting may DEMAND, in years.
    #:
    #: **A posting that stated no minimum is EXCLUDED by this**, exactly as a
    #: posting that resolved to no country is excluded by `countries`. Absence
    #: is never permission: "up to 2 years" asks for postings that SAID they
    #: want at most two, and 92% of the corpus said nothing at all. Including
    #: silence would return almost the whole corpus under a heading claiming
    #: every posting in it is open to a beginner.
    #:
    #: `NONE_REQUIRED` postings carry `experience_min_years = 0`, so they are
    #: inside every value of this filter, which is the correct answer.
    experience_max_years: int | None = None
    #: `EntrySignal` values the posting must carry AT LEAST ONE of. A set
    #: filter over a pipe-fenced column, so several values OR together the way
    #: `countries` does.
    entry_signals: tuple[str, ...] = ()

    #: Signals the CANDIDATE has confirmed evidence for, used to widen the
    #: off-target narrowing rather than to score anything.
    #:
    #: **This is the only field on this class derived from what the candidate
    #: has confirmed, and it is inert unless a caller fills it.** It exists for
    #: one control -- "show roles I could transition into" -- which is OFF by
    #: default and which a person switches on deliberately.
    #:
    #: It does not move a score, a band or an eligibility verdict, and it
    #: cannot: it appears in exactly one clause, beside `include_off_target`,
    #: where its effect is to STOP hiding a posting. The promise that
    #: confirming a fact moves no recommendation holds because the default list
    #: never carries this and because nothing here writes anything.
    #:
    #: WHY IT IS NEEDED. `screening.required_any_groups` blocks a posting when
    #: none of the signals the search asked for fired. For somebody changing
    #: profession that is every posting in the profession they are moving to --
    #: the search describes where they have been. A lawyer moving into
    #: administration has confirmed evidence for documentation, scheduling,
    #: client communication and compliance; those signals DO fire on
    #: administrative postings, and this is what lets the person say so.
    transferable_signals: tuple[str, ...] = ()

    #: Free-text phrases the posting must ALL carry, and phrases it must carry
    #: NONE of. The preference editor maintains the same vocabulary as weighted
    #: signals; these are the ad-hoc, this-search-only version.
    keywords: tuple[str, ...] = ()
    excluded_keywords: tuple[str, ...] = ()

    #: THE SOFT PAIR, and the difference from the two above is the whole point.
    #:
    #: `keywords` and `excluded_keywords` decide WHETHER a posting is in the
    #: list. These two decide only WHERE IN IT it appears: a preferred phrase
    #: lifts a posting above its peers and an avoided one drops it below them,
    #: and neither can remove anything.
    #:
    #: That distinction is not cosmetic. "I would rather not see agency work"
    #: and "never show me agency work" are different sentences, and a product
    #: that only offers the second makes somebody choose between a mild
    #: preference and never seeing a job they might have taken.
    #:
    #: **Nothing here reaches the stored score.** They are ORDER BY terms
    #: computed per request, so two candidates reading the same posting see the
    #: same `match_score` and the same explanation, and `JobFingerprint` stays
    #: candidate independent (invariant 4).
    preferred_keywords: tuple[str, ...] = ()
    avoided_keywords: tuple[str, ...] = ()

    #: Whether to show postings a gate has VERIFIED as ruling this person out.
    #:
    #: **True here, and False at the web layer. That split is deliberate.**
    #:
    #: Every other field in this class defaults to "do not narrow", and a value
    #: object that quietly narrows would make `JobFilter()` mean something other
    #: than "everything" for exactly one field. A caller counting a corpus would
    #: get a smaller number than the corpus and nothing would say why.
    #:
    #: Hiding these by DEFAULT is a product decision about the discovery views,
    #: so it lives where the product is: `JobsApi._filter_from` reads the
    #: `include_ineligible` query parameter, which is absent by default, so
    #: every browser request narrows and every other caller does not.
    #:
    #: WHAT IT HIDES IS NARROW ON PURPOSE. Only `VERIFIED_NOT_ELIGIBLE`, which
    #: `gates.eligibility_status_from` produces when one of the five gates
    #: actually FAILED against a stated requirement: geography, work
    #: authorization, security clearance, physical presence, travel. It is a
    #: confirmed conflict with something the employer wrote down.
    #:
    #: It does NOT hide `UNRESOLVED`, which is 19,352 of 21,202 rows and means
    #: the posting never addressed the question. Absence is never permission,
    #: and it is not a disqualification either: hiding silence would turn a
    #: missing sentence into a rejection, which is the exact inversion this
    #: product exists to refuse. A low score, an odd title and a missing tool
    #: are not eligibility at all and are untouched by this.
    include_ineligible: bool = True

    #: Whether to show postings this person's own SEARCH set aside as not the
    #: work they asked for. `screening_state == BLOCKED`.
    #:
    #: The same True-here-False-at-the-web-layer split, for the same reason,
    #: and a SEPARATE field on purpose. It answers a different question from
    #: `include_ineligible` and conflating them was a real defect once: three
    #: cards carried "this posting states a requirement you do not meet" while
    #: having zero blockers, because the interface read `BLOCKED` as an
    #: employer rejecting somebody. It is the opposite. The EMPLOYER rules you
    #: out; the SEARCH rules the work out.
    #:
    #: What it hides is a hard conflict with what the person said they want:
    #: none of the signals in `screening.required_any_groups` fired anywhere,
    #: in the body or the title. It is a third of the corpus, it is the reason
    #: a sales role with a quota is in a systems search at all, and it is not a
    #: judgement about the job.
    #:
    #: SOFT preferences are untouched. `scoring.soft_penalties` deprioritises
    #: and never hides -- a posting that mentions people management loses
    #: points and stays visible -- because "I would rather not" and "this is
    #: not the job I am looking for" are different sentences.
    include_off_target: bool = True
    #: Postings where NOTHING IN THE POSTING SAYS whether she could take it.
    #:
    #: A third population, added 2026-09-07, and the reason is a measurement.
    #: 12,574 of 19,469 open postings are UNRESOLVED on geography -- almost
    #: always because the board publishes an office and no workplace type, so
    #: there is no way to tell an office from a hiring region. They are real
    #: jobs and some of them are hers; none of them is a RECOMMENDATION.
    #:
    #: Mixed into "best matches" they buried the eligible ones: the twenty
    #: highest-scoring postings contained a hybrid role in Gurugram, offices in
    #: Boston, Dublin and Bengaluru, and one job she could actually take.
    #: Compatibility ranked them; eligibility never got to speak.
    #:
    #: True here, which is the NEUTRAL value, exactly as for the two above. The
    #: narrowing is applied by the web layer because it is a decision about a
    #: VIEW rather than a property of the query, and `career-agent daily` and
    #: the CLI keep the whole population.
    include_unresolved: bool = True
    #: Levels the CANDIDATE'S OWN configuration asked not to be shown, and
    #: whether to show them anyway.
    #:
    #: The list travels on the filter rather than being read from the config
    #: inside the query, for the reason every other candidate fact does: this
    #: layer knows about columns and the web layer knows about the person.
    #: Empty is the neutral value and excludes nothing.
    excluded_seniorities: tuple[str, ...] = ()
    include_excluded_seniority: bool = True

    #: Whether to show postings SHE hid, one at a time, by pressing a control.
    #:
    #: The third narrowing, and the only one that is not a fact about somebody
    #: else. The two above answer "an employer stated a requirement you do not
    #: meet" and "your search configuration calls this other work"; this one
    #: answers "I read it and I do not want to see it again", which neither of
    #: them can express and which none of the ten `ApplicationStatus` values
    #: can either. Marking a posting REJECTED to get it off a screen would
    #: record an application that was never sent.
    #:
    #: True here and False at the web layer, exactly like the other two: the
    #: default is "everything" so a caller counting a corpus gets the corpus,
    #: and the NARROWING is a product decision about a view.
    #:
    #: THERE IS NO EXEMPTION, and its absence is deliberate. The automatic
    #: narrowings above spare anything she saved or is tracking, because the
    #: machine must never retract a decision she made. This narrowing IS the
    #: decision, so sparing a saved posting from it would be a control that
    #: silently does not work on the jobs she cared about most.
    include_user_hidden: bool = True

    #: ONLY the postings she hid. The restore view, and nothing else uses it.
    #:
    #: A separate flag rather than `include_user_hidden` doing double duty:
    #: "show me these as well" and "show me only these" are different
    #: questions, and one field answering both is how a widening control comes
    #: to narrow.
    user_hidden_only: bool = False

    #: Whether a posting she is TRACKING is spared from the automatic
    #: narrowings above.
    #:
    #: **False is the neutral value, and this field is the 2026-09-07
    #: correction.** Until now the exemption was unconditional: every automatic
    #: narrowing carried ``OR saved = 1 OR status != 'DISCOVERED'``, so a
    #: posting she had shortlisted came back into the DEFAULT list however its
    #: gates landed. The reasoning was right and the placement was wrong.
    #:
    #: WHAT WAS RIGHT. A job she saved, shortlisted or applied to must never
    #: vanish from under a decision she already took. A later rescore finding a
    #: conflict is a reason to WARN her, never to quietly retract her choice.
    #: That is still true and nothing here weakens it.
    #:
    #: WHAT WAS WRONG. "Must not vanish" was implemented as "must appear
    #: EVERYWHERE", and the default list is not a record of her decisions. It is
    #: a RECOMMENDATION: these are the postings this product is putting forward
    #: as work she could take. A posting an employer has verifiably ruled her
    #: out of is not one of those, and shortlisting it does not make it one.
    #: The morning audit found exactly that -- one violation in the default top
    #: 50 of the real corpus, and it was a posting she had SHORTLISTED.
    #:
    #: So the two questions are separated. ACCESS is preserved by the tracking
    #: views, which set this True: Applications, Saved, the restore view, the
    #: digest's tracking section, direct job detail, and any explicit reveal.
    #: RECOMMENDATION is kept honest by leaving it False everywhere else.
    #:
    #: Nothing is deleted, no status is cleared and no history moves. The
    #: posting is one click away in the place that is about her decisions,
    #: carrying the restriction that applies to it.
    exempt_tracked: bool = False

    sort: str = "score"
    direction: str = "desc"
    limit: int = 60
    offset: int = 0


def _like_term(text: str) -> str:
    """A case-folded substring pattern with the LIKE metacharacters neutralised."""
    return f"%{_escape_like(text.lower())}%"


#: Facets whose buckets OVERLAP, because one posting carries several values.
#:
#: Every other facet PARTITIONS the population, so its buckets sum to
#: `count(f)` -- which is the property that lets the interface print a number
#: beside a chip and promise it. These four cannot: a posting is in two
#: countries, fires six signals and can say both `entry level` and `training
#: provided`. They are held to the weaker and still useful property that every
#: bucket equals what its own filter returns.
#:
#: **Named here because it was named in five test files.** Adding the sixth
#: member broke four of them at once, each asserting a sum over a facet whose
#: buckets were never meant to sum. One name, imported.
SET_VALUED_FACETS: frozenset[str] = frozenset(
    {"country", "region", "signal", "technology", "entry_signal"}
)


#: The value a person asks for when they want the postings that said nothing.
#: Stored as NULL, offered as a word, because a filter list cannot hold NULL
#: and "not stated" is a real answer a person wants to see rather than an
#: absence they should have to infer.
NOT_STATED = "NOT_STATED"


def _fenced_pattern(value: str) -> str:
    """A LIKE pattern matching one entry of a pipe-fenced set: ``%|BR|%``.

    The fences are what make it precise -- without them `|BR|` would match a
    longer code that happened to contain those letters. Same device, same
    reason, as :func:`_membership_pattern`.
    """
    return f"%|{_escape_like(value.strip().upper())}|%"


def _membership_pattern(tag: str, value: str) -> str:
    """A LIKE pattern that matches one tagged entry of a derived index.

    The only way this can produce a false positive is a stored quote that
    literally contains ``|fired:<id>|``, pipes and all. That is the accepted
    limit of a containment filter over serialised JSON.
    """
    return f"%|{_escape_like(tag)}:{_escape_like(value)}|%"


#: The tables `_select` reads. `page` and `get_one` need every one of them
#: whatever the filter asked for, because the SELECT list -- not the WHERE
#: clause -- is what makes them required.
_SELECT_TABLES = frozenset({"c", "jr", "ja", "je"})

#: How many locations a grouped row carries down the wire. The interface says
#: "+N more" from `duplicate_count`, so nothing is hidden by the cap -- it only
#: stops a role published in ninety cities from putting ninety strings on a card.
MAX_SIBLING_LOCATIONS = 12

#: How many `(company_id, title)` pairs go into one sibling lookup. Same
#: reasoning as `repositories._IN_CHUNK`: SQLite's parameter ceiling is 32,766
#: on current builds and 999 on the oldest still in the wild, and each pair
#: costs two parameters, so 100 pairs is 200 parameters and a whole page of 60
#: fits in a single round trip.
_PAIR_CHUNK = 100

#: The one extra clause that turns the population into one representative row
#: per `(company_id, title)`: the ids elected to stand for their group.
#:
#: The rule is "highest `match_score`, ties broken by the lowest `j.id`", and
#: the two halves are the two aggregates. `g` finds each group's best score;
#: `MIN(j3.id)` picks among the rows that tie on it. ULIDs are time-sortable,
#: so the lowest id is the posting collected first. The ordering is TOTAL on
#: purpose -- siblings usually carry the same text and therefore the same
#: score, and a rule that left ties unbroken would let SQLite return a
#: different representative between two runs of one query, so a card would
#: change identity under a reload with nothing having happened.
#:
#: WHY NOT THE OBVIOUS CORRELATED FORM
#: -----------------------------------
#: The natural way to say this is `NOT EXISTS (... a sibling that beats me)`,
#: correlated on `(company_id, title)`. It is shorter, and it was tried, and on
#: the real corpus it costs **23 seconds** for a count that this costs 0.3 --
#: measured at `data/m1d2/career.db`, 18,549 scored postings.
#:
#: The reason is in the query plan, not in the row count. SQLite has no
#: statistics on these tables, so its heuristic sees `idx_job_match_config`
#: (config_id, config_version) and `idx_job_company_title` (company_id, title)
#: as equally selective two-column equality lookups -- and drives the subquery
#: from `job_match`, matching all 18,549 rows on every probe. Reordering the
#: FROM does not move it; only ANALYZE or an index hint would, and neither
#: belongs in a query whose correctness must not depend on maintenance state.
#:
#: This form asks the question once instead of once per candidate row, so the
#: planner's choice stops mattering. It is also plain SQL: no window function
#: (`ROW_NUMBER() OVER (PARTITION BY ...)` says the same thing, and is exactly
#: the construct the eventual PostgreSQL move must not have to translate) and
#: no SQLite-specific bare-column-in-an-aggregate trick.
#:
#: THE POPULATION IT ELECTS WITHIN IS THE FILTERED ONE
#: --------------------------------------------------
#: Both inner passes carry the SAME filters as the outer query, which is what
#: makes grouping a collapse of the rows that were going to be shown rather
#: than a second, invisible filter.
#:
#: Electing over the whole configuration version instead is simpler, and it is
#: WRONG in a way that loses postings. Where the best-scoring sibling fails a
#: filter that a lower-scoring one passes, the best is filtered out and the
#: rest lose the election, so NOT ONE row of that role survives. Measured on
#: the corpus before this was fixed:
#:
#:     min_score=55                     132 roles ->  132 shown   (0 lost)
#:     min_score=55 + remote_only        20 roles ->   18 shown   (2 lost)
#:     eligibility LIKELY/VERIFIED      825 roles ->  815 shown  (10 lost)
#:     search "forward deployed"        379 roles ->  377 shown   (2 lost)
#:
#: `min_score` is safe because it is monotone in the election key -- if any
#: sibling clears the floor, the highest-scoring one does. Nothing else is:
#: `remote_only`, `saved_only`, the status and eligibility filters and the
#: search term all read fields on which siblings differ, and differing by
#: location is the entire reason these postings are separate rows.
#:
#: A grouped view that silently drops a matching role is worse than a
#: duplicated one, because the reader cannot tell. Cost of doing it properly:
#: the two inner passes get the filter too, so under a selective filter they
#: scan LESS than the unfiltered version did.
#:
#: Built by `ScoredJobQuery._representative_clause`, which reuses the same
#: clause builder the outer query uses under the aliases `3` and `4`. It cannot
#: drift from the outer filter, because it IS the outer filter.
_REPRESENTATIVE_TEMPLATE = (
    "j.id IN ("
    " SELECT MIN(j3.id){from3}"
    " JOIN ("
    "   SELECT j4.company_id AS cid, j4.title AS t, MAX(jm4.match_score) AS best"
    "   {from4}{where4}"
    "   GROUP BY j4.company_id, j4.title"
    " ) g ON g.cid = j3.company_id AND g.t = j3.title AND g.best = jm3.match_score"
    "{where3}"
    " GROUP BY j3.company_id, j3.title"
    ")"
)


def _cutoff(now: str, days: int) -> str:
    """The ISO-8601 instant ``days`` before ``now``.

    Computed in Python and bound as a parameter. SQLite's date functions have
    no PostgreSQL equivalent (hazard A3), and the timestamps in this database
    are text that already sorts correctly, so a plain ``>=`` is enough.
    """
    moment = datetime.fromisoformat(now.replace("Z", "+00:00")) - timedelta(days=days)
    return moment.isoformat().replace("+00:00", "Z")


def _fenced(value: Any) -> list[str]:
    """The values inside a `|a|b|c|` column. Empty for NULL or an empty string."""
    if not value:
        return []
    return [part for part in str(value).strip("|").split("|") if part]


def _ranked(counter: Counter[str]) -> dict[str, int]:
    """`n DESC, bucket ASC` -- the order the SQL `GROUP BY` produced."""
    return dict(sorted(counter.items(), key=lambda kv: (-kv[1], kv[0])))


#: The closed vocabulary of `job_match.eligibility_status`, in the order the
#: CHECK constraint declares it. The narrowings are written as the complement
#: of what they hide, over this list.
ELIGIBILITY_STATUSES: tuple[str, ...] = (
    "VERIFIED_ELIGIBLE",
    "LIKELY_ELIGIBLE",
    "UNRESOLVED",
    "VERIFIED_NOT_ELIGIBLE",
)


class ScoredJobQuery(_Repo):
    """The single read path behind both the Cards view and the Table view.

    Cards and Table differ in what they draw, never in what they select. Both
    call :meth:`page`; the counter and the filter chips call :meth:`count` and
    :meth:`facets`; and all three derive their WHERE clause from
    :meth:`_where`. That is the whole reason this class exists rather than a
    handful of query functions.
    """

    #: Every read starts here. `job_match` leads because the configuration
    #: version is the most selective predicate and because a posting with no
    #: score under this version has no place in a scored list.
    #:
    #: Parameterised by an alias SUFFIX so the grouping clause can join the
    #: very same tables again under `3` and `4` without a second, hand-copied
    #: FROM that could quietly disagree with this one about, say, whether the
    #: enrichment join is LEFT.
    _FROM_TEMPLATE = (
        " FROM job_match jm{s}"
        " JOIN job j{s} ON j{s}.id = jm{s}.job_id"
        " JOIN job_raw jr{s} ON jr{s}.content_hash = jm{s}.content_hash"
        " JOIN company c{s} ON c{s}.id = j{s}.company_id"
        " LEFT JOIN job_application ja{s} ON ja{s}.job_id = j{s}.id"
        # LEFT, and on job_id alone: an enrichment computed before a
        # re-normalisation still belongs to the job. Whether it belongs to the
        # CURRENT text is what `job_enrichment.content_hash` answers, and the
        # interface shows that rather than the query silently hiding it.
        " LEFT JOIN job_enrichment je{s} ON je{s}.job_id = j{s}.id"
    )

    #: The unsuffixed FROM, which is what `count`, `page`, `facets` and
    #: `get_one` read. Named as before so those four are unchanged.
    _FROM = _FROM_TEMPLATE.format(s="")

    #: Which joined table each optional alias belongs to, in the order they
    #: must be emitted. `jm` and `j` are not here: they are always required,
    #: because one is the population and the other is what a posting IS.
    _OPTIONAL_JOINS = (
        ("c", " JOIN company c{s} ON c{s}.id = j{s}.company_id"),
        ("jr", " JOIN job_raw jr{s} ON jr{s}.content_hash = jm{s}.content_hash"),
        ("ja", " LEFT JOIN job_application ja{s} ON ja{s}.job_id = j{s}.id"),
        ("je", " LEFT JOIN job_enrichment je{s} ON je{s}.job_id = j{s}.id"),
    )

    def _joins_needed(self, f: JobFilter, extra: frozenset[str] = frozenset()) -> frozenset[str]:
        """The tables this query actually has to touch.

        `facets` used to join all six for each of six dimensions -- including
        `job_raw`, which holds 104 MB of description text that no facet reads
        and no filter reads unless free-text search has fallen back to LIKE.
        Measured on the real corpus, that was 658 ms of a 2,720 ms unfiltered
        request, for rows nothing looked at.

        Derived from the filter rather than declared per call site, so a new
        filter cannot forget to ask for the table it reads. Getting this wrong
        in the permissive direction costs speed; getting it wrong in the other
        direction is a missing-table SQL error, which is loud. That asymmetry
        is why it is computed here and not passed in.
        """
        needed = set(extra)
        if f.companies:
            needed.add("c")
        # The automatic narrowings read `ja.saved` and `ja.status` ONLY when the
        # caller asked for the tracking exemption. Until 2026-09-07 they always
        # did, so this join sat on almost every query; the default discovery
        # query no longer needs it at all, which is a measured saving rather
        # than a tidy-up. `include_user_hidden` is separate and always reads
        # `ja.hidden_at` -- that narrowing has no exemption, on purpose.
        # ORDER BY reads it too. `SORT_COLUMNS` names `ja.status` and
        # `ja.hidden_at`, and a sort key is not a filter, so nothing above
        # would have asked for the table it sorts on. Derived from the same
        # object rather than declared at the call sites, for the reason in the
        # docstring: a missing table is a loud error and a forgotten join at a
        # call site is a silent one.
        if (
            f.statuses
            or f.saved_only
            or f.user_hidden_only
            or f.sort in _SORTS_ON_APPLICATION
            or not f.include_user_hidden
            or (
                f.exempt_tracked
                and (
                    not f.include_ineligible
                    or not f.include_off_target
                    or (not f.include_unresolved and "UNRESOLVED" not in f.eligibility)
                    or (f.excluded_seniorities and not f.include_excluded_seniority)
                )
            )
        ):
            needed.add("ja")
        if f.enriched_only:
            needed.add("je")
        if f.search and not self._search_is_indexed():
            # Only the LIKE fallback reads the description and the company
            # name. The FTS path matches on `job_search` and needs neither.
            needed.update(("c", "jr"))
        if (f.keywords or f.excluded_keywords) and not self._search_is_indexed():
            needed.update(("c", "jr"))
        return frozenset(needed)

    def _from_for(self, needed: frozenset[str], a: str = "", f: JobFilter | None = None) -> str:
        # Ordinary filters leave index choice to SQLite. A general gates
        # hint once made a provider chip a 102-second skip-scan; the only
        # exception below is a fully constrained saved-id lookup.
        # Saved-only has an explicit finite job_id set. All three leading
        # population-index columns are constrained, so this is a point seek,
        # not the gates-index skip-scan a general provider hint once caused.
        # Without it SQLite prefers scanning the entire covering gates index
        # even to find six saved postings. No hint applies to other filters.
        index = " INDEXED BY idx_job_match_population" if f and f.saved_only else ""
        base = f" FROM job_match jm{a}{index} JOIN job j{a} ON j{a}.id = jm{a}.job_id"
        return base + "".join(
            sql.format(s=a) for alias, sql in self._OPTIONAL_JOINS if alias in needed
        )

    def __init__(self, conn: sqlite3.Connection) -> None:
        super().__init__(conn)
        #: Overridable so `posted_within_days` is testable without waiting.
        self.now: str | None = None
        #: Whether the FTS index is current. Resolved once per query object.
        self._indexed: bool | None = None

    # -- the shared clause ------------------------------------------------

    def _where(
        self, config_id: str, config_version: int, f: JobFilter, *, alias: str = ""
    ) -> tuple[str, list[Any]]:
        """The WHERE clause and its bound values. The only place either is built.

        Every value a caller supplies is bound as a positional parameter. The
        only text ever interpolated is a run of ``?`` placeholders, whose length
        comes from a list we hold, never from user input -- and the ``alias``
        suffix, which is never a caller's value: it is ``""``, ``"3"`` or
        ``"4"``, chosen here.

        ``alias`` exists so the grouping clause can apply THESE SAME
        predicates to a second and third copy of the same join. Reuse rather
        than a parallel implementation, because a duplicate filter that drifts
        from this one would elect a representative out of a population the
        caller never asked about, and nothing would say so.
        """
        a = alias
        clauses = [f"jm{a}.config_id = ?", f"jm{a}.config_version = ?"]
        params: list[Any] = [config_id, config_version]

        if f.search:
            clause, search_params = self._search_clause(f.search, a)
            clauses.append(clause)
            params.extend(search_params)

        for column, values in (
            (f"c{a}.slug", f.companies),
            (f"j{a}.provider", f.providers),
            (f"jm{a}.title_class", f.role_classes),
            (f"jm{a}.eligibility_status", f.eligibility),
            (f"jm{a}.fit_band", f.fit_bands),
            (f"COALESCE(ja{a}.status, 'DISCOVERED')", f.statuses),
        ):
            if values:
                placeholders = ", ".join("?" for _ in values)
                clauses.append(f"{column} IN ({placeholders})")
                params.extend(values)

        if f.min_score is not None:
            clauses.append(f"jm{a}.match_score >= ?")
            params.append(f.min_score)
        if f.max_score is not None:
            clauses.append(f"jm{a}.match_score <= ?")
            params.append(f.max_score)
        if f.min_confidence is not None:
            clauses.append(f"jm{a}.data_confidence >= ?")
            params.append(f.min_confidence)

        # The tracking exemption, built once. Empty unless the caller asked for
        # it, so a narrowing narrows by default; see `JobFilter.exempt_tracked`
        # for why that reversed on 2026-09-07.
        tracked = (
            f" OR COALESCE(ja{a}.saved, 0) = 1"
            f" OR COALESCE(ja{a}.status, 'DISCOVERED') != 'DISCOVERED'"
            if f.exempt_tracked
            else ""
        )

        # THE ELIGIBILITY NARROWINGS, WRITTEN POSITIVELY. `eligibility_status`
        # is a closed vocabulary (the CHECK on `job_match`), so "not
        # VERIFIED_NOT_ELIGIBLE and not UNRESOLVED" is exactly "one of the
        # other two" -- and written as one `IN (...)` on the third column of
        # `idx_job_match_discovery_gates` it is a range the planner can seek,
        # where two `!=` were predicates it could only test row by row.
        # Measured 2026-09-12 on the 245,871-row revision: a count under the
        # default narrowings plus a worksite chip went from 2.5-4.6 s to 0.26 s
        # once the index could be entered by eligibility, and unlike naming
        # the index with INDEXED BY this leaves the planner free to drive from
        # `job` when a provider chip makes that the cheaper side (the named
        # index turned that shape into a 102 s skip-scan).
        hidden_statuses: list[str] = []
        if not f.include_ineligible:
            # A posting that a gate verified as ruling this person out.
            #
            # A job she saved, shortlisted or applied to must never disappear
            # from under a decision she already took -- and the place that
            # promise is kept is the TRACKING views, which pass
            # `exempt_tracked=True`. It is not kept by putting the posting back
            # into a list of recommendations, which is what this clause used to
            # do unconditionally.
            hidden_statuses.append("VERIFIED_NOT_ELIGIBLE")

        if f.excluded_seniorities and not f.include_excluded_seniority:
            # Levels she said not to show her. The tracking exemption applies
            # here too when the caller asks for it, and a tracking view is
            # where a shortlisted Principal role stays reachable.
            placeholders = ", ".join("?" for _ in f.excluded_seniorities)
            clauses.append(
                f"(jm{a}.seniority IS NULL OR jm{a}.seniority NOT IN ({placeholders}){tracked})"
            )
            params.extend(f.excluded_seniorities)

        if not f.include_unresolved and "UNRESOLVED" not in f.eligibility:
            # ASKED FOR BEATS HIDDEN BY DEFAULT. Choosing the "Did not say"
            # eligibility filter is asking for exactly the postings this
            # narrowing hides, and applying both leaves a facet whose count
            # says 3 above a list that says nothing -- a count and a list
            # disagreeing, which is the defect this codebase keeps closing.
            #
            # Nothing in the posting answered the question. A posting she
            # saved stays reachable through the tracking views, which ask for
            # the exemption; it is not a recommendation while it is silent.
            #
            # This is NOT a refusal and the wording everywhere says so. The
            # posting did not say; that is a fact about the posting.
            hidden_statuses.append("UNRESOLVED")
        if hidden_statuses:
            shown = [status for status in ELIGIBILITY_STATUSES if status not in hidden_statuses]
            marks = ", ".join(f"'{status}'" for status in shown)
            clauses.append(f"(jm{a}.eligibility_status IN ({marks}){tracked})")

        if not f.include_off_target:
            # Work the person's own search set aside. Same shape as the two
            # above: reachable from tracking, absent from recommendations.
            #
            # PLUS, when a caller asked for it, the postings that fire a signal
            # the person has CONFIRMED evidence for. That is the transition
            # control, and it can only ever widen: it adds a disjunct to a
            # clause whose job is to hide, so switching it on shows more and
            # switching it off restores exactly the previous list.
            transferable = ""
            if f.transferable_signals:
                terms = " OR ".join(
                    f"jm{a}.membership LIKE ? ESCAPE '\\'" for _ in f.transferable_signals
                )
                transferable = f" OR ({terms})"
            # `= 'NOT_BLOCKED'`, not `!= 'BLOCKED'`: the same closed vocabulary
            # argument as the eligibility list above, one column further into
            # the gates index.
            clauses.append(f"(jm{a}.screening_state = 'NOT_BLOCKED'{tracked}{transferable})")
            if f.transferable_signals:
                params.extend(
                    _membership_pattern(FIRED_TAG, signal) for signal in f.transferable_signals
                )

        if not f.include_user_hidden:
            # What she hid, and no exemption at all -- not even an opt-in one.
            # See `include_user_hidden`: the clauses above can spare a tracked
            # posting because the MACHINE decided to hide it; here SHE did, and
            # a hide that quietly failed on a saved job would be a broken
            # control.
            clauses.append(f"ja{a}.hidden_at IS NULL")

        if f.user_hidden_only:
            clauses.append(f"ja{a}.hidden_at IS NOT NULL")

        if f.saved_only:
            # Start from the small saved set, rather than probe the application
            # table for every score in the revision. job_id is UNIQUE there;
            # a missing/NULL saved flag still cannot satisfy saved=1.
            clauses.append(f"jm{a}.job_id IN (SELECT job_id FROM job_application WHERE saved = 1)")

        if f.enriched_only:
            clauses.append(f"je{a}.id IS NOT NULL")

        # `remote_only` is a convenience over the location string the BOARD
        # printed. It is not an eligibility claim and never becomes one:
        # remote is not worldwide, and hiring scope lives on the fingerprint.
        if f.remote_only:
            clauses.append(f"LOWER(COALESCE(j{a}.location_raw, '')) LIKE ? ESCAPE '\\'")
            params.append("%remote%")

        if f.posted_within_days is not None:
            clauses.append(f"j{a}.posted_at IS NOT NULL AND j{a}.posted_at >= ?")
            params.append(_cutoff(self.now or now_utc(), f.posted_within_days))

        # `first_seen_at` is NOT NULL on every row, so this needs no null guard
        # and -- unlike the clause above -- excludes nothing for want of a date
        # the board never published.
        if f.first_seen_after is not None:
            clauses.append(f"j{a}.first_seen_at > ?")
            params.append(f.first_seen_after)

        # The two containment filters. Both are a LIKE, and therefore the only
        # predicates here that no index can serve -- a substring match has no
        # B-tree to sit on. That cost is real and accepted.
        #
        # They run against `membership`, NOT against `result_json`. The blob
        # holds evidence quotes copied verbatim out of job descriptions, so a
        # LIKE over it let a posting containing `|award:compensation_stated|`
        # satisfy the salary filter with no salary in it -- third-party text
        # steering a deterministic filter. `membership` contains only
        # identifiers this system generated. The `|` fences still do their
        # other job: stopping `|crm|` from matching a signal called `crm_2`.
        for signal_id in f.signals:
            clauses.append(f"jm{a}.membership LIKE ? ESCAPE '\\'")
            params.append(_membership_pattern(FIRED_TAG, signal_id))

        if f.has_salary is not None:
            operator = "LIKE" if f.has_salary else "NOT LIKE"
            clauses.append(f"jm{a}.membership {operator} ? ESCAPE '\\'")
            params.append(_membership_pattern(AWARDED_TAG, SALARY_CONFIDENCE_ITEM))

        # A technology is a lexicon signal that fired, read from the SAME
        # membership index `signals` uses. Two parameters, one index: the
        # person asks "which tools", the developer asks "which signals", and
        # a second index would be a second thing to keep true.
        for technology in f.technologies:
            clauses.append(f"jm{a}.membership LIKE ? ESCAPE '\\'")
            params.append(_membership_pattern(FIRED_TAG, technology))

        # The two columns migration 0020 added. Asked with IN rather than
        # LIKE: they are single-valued, they hold OUR identifiers and never
        # posting text, and they have an index behind them.
        #
        # `employment_context` is NOT `eligibility`. A query for one can never
        # return the other, which is the property the separate column exists
        # to guarantee rather than merely to document.
        if f.employment_context:
            marks = ", ".join("?" for _ in f.employment_context)
            clauses.append(f"jm{a}.employment_context IN ({marks})")
            params.extend(f.employment_context)

        # A separate parameter from every quality filter, and it must stay
        # one: "show me only the postings I actually have in full" is a
        # question about this system's reach, and "show me the ones that score
        # well" is a question about the jobs. Answering the first with the
        # second would let a source's limitation read as a verdict on an
        # employer.
        if f.content_completeness:
            marks = ", ".join("?" for _ in f.content_completeness)
            clauses.append(f"jm{a}.content_completeness IN ({marks})")
            params.extend(f.content_completeness)

        # -- the ten columns migration 0017 added, and one from 0020 -------
        for column, values in (
            (f"jm{a}.work_model", f.worksites),
            (f"jm{a}.seniority", f.seniorities),
            (f"jm{a}.employment_type", f.employment_types),
            # MOVED HERE IN V1.7, from its own `IN (...)` clause above. It
            # gained a facet the same day, and a facet emits a `NOT_STATED`
            # bucket -- which the old clause could not match, so the one row
            # in the rail that every non-Brazilian posting sits in answered a
            # 400. The five columns beside it had solved that years earlier.
            (f"jm{a}.contract_regime", f.contract_regime),
            (f"jm{a}.salary_currency", f.salary_currencies),
            (f"jm{a}.salary_period", f.salary_periods),
        ):
            if not values:
                continue
            # NOT_STATED is a value the person can ask for, and it is stored as
            # NULL rather than as a word. Asked for explicitly it must match
            # those rows; NOT asked for, it must not -- `IN (...)` alone would
            # silently drop every unstated posting from a worksite filter and
            # from nothing else, which is the kind of asymmetry nobody notices.
            wanted = [v for v in values if v != NOT_STATED]
            parts = []
            if wanted:
                parts.append(f"{column} IN ({', '.join('?' for _ in wanted)})")
                params.extend(wanted)
            if NOT_STATED in values:
                parts.append(f"{column} IS NULL")
            clauses.append("(" + " OR ".join(parts) + ")")

        # Countries and regions are pipe-fenced sets, so several values are an
        # OR: "Brazil or Argentina", never "both at once".
        for column, values in (
            (f"jm{a}.countries", f.countries),
            (f"jm{a}.regions", f.regions),
        ):
            if not values:
                continue
            clauses.append("(" + " OR ".join(f"{column} LIKE ? ESCAPE '\\'" for _ in values) + ")")
            params.extend(_fenced_pattern(v) for v in values)

        for flag, region in ((f.latam_only, "LATAM"), (f.worldwide_only, "WORLDWIDE")):
            if flag:
                clauses.append(f"jm{a}.regions LIKE ? ESCAPE '\\'")
                params.append(_fenced_pattern(region))

        if f.experience_requirements:
            placeholders = ", ".join("?" for _ in f.experience_requirements)
            clauses.append(f"jm{a}.experience_requirement IN ({placeholders})")
            params.extend(f.experience_requirements)

        if f.experience_max_years is not None:
            # A STATED minimum, at or below the figure asked for. NULL is
            # excluded rather than included: a posting that never stated a
            # minimum has not said it would take a beginner, and reading
            # silence as an invitation is the same defect as reading it as
            # permission. See the field's own note.
            clauses.append(f"jm{a}.experience_min_years <= ?")
            params.append(int(f.experience_max_years))

        if f.entry_signals:
            # A pipe-fenced set, so several values are an OR: "entry level or
            # recent graduates", never "both at once".
            clauses.append(
                "("
                + " OR ".join(f"jm{a}.entry_signals LIKE ? ESCAPE '\\'" for _ in f.entry_signals)
                + ")"
            )
            params.extend(_fenced_pattern(value) for value in f.entry_signals)

        if f.min_salary is not None:
            # The TOP of the band, falling back to the bottom when a posting
            # stated only one number. Asking "at least 120k" of a 100k-140k
            # posting must include it: the band reaches the figure, and a
            # filter on the floor would hide every range that straddles it.
            clauses.append(f"COALESCE(jm{a}.salary_annual_max, jm{a}.salary_annual_min) >= ?")
            params.append(float(f.min_salary))

        # Ad-hoc phrases, ANDed for wanted and NOTted for unwanted. These read
        # posting TEXT on purpose -- unlike every filter above -- because that
        # is what a keyword is. They go through the same search clause builder,
        # so they get the index when it is current and the honest LIKE
        # fallback when it is not.
        for phrase in f.keywords:
            clause, phrase_params = self._search_clause(phrase, a)
            clauses.append(clause)
            params.extend(phrase_params)
        for phrase in f.excluded_keywords:
            clause, phrase_params = self._search_clause(phrase, a)
            clauses.append(f"NOT ({clause})")
            params.extend(phrase_params)

        # ONE clause, and it lives here rather than in `page` on purpose:
        # `count`, `page` and `facets` all derive from this method, so putting
        # the restriction anywhere else is how the three numbers on one screen
        # stop agreeing. Adding it here means they cannot.
        #
        # Only at the top level. The two inner passes are built with grouping
        # already switched off, which is what stops this recursing.
        if f.group_duplicates and not alias:
            clause, group_params = self._representative_clause(config_id, config_version, f)
            clauses.append(clause)
            params.extend(group_params)

        return " WHERE " + " AND ".join(clauses), params

    def _search_clause(self, text: str, a: str) -> tuple[str, list[Any]]:
        """Free text over title, company and description.

        Prefers the FTS5 index and falls back to LIKE when it is absent or
        behind the corpus.

        THE TWO PATHS DO NOT RETURN THE SAME ROWS, and an earlier version of
        this docstring claimed they did. Measured on the real corpus:

            search=engineer         FTS 12,241   LIKE 12,246
            search=ngine            FTS      0   LIKE 12,617
            search=data engineer    FTS  9,662   LIKE    922

        Two structural differences, neither of which is a bug in either path.
        LIKE matches a SUBSTRING, so `ngine` finds `engineer`; FTS matches
        whole tokens with a prefix on the last one only. And LIKE needs the
        typed phrase contiguous within ONE field, while FTS ANDs the tokens
        ACROSS title, company and description -- which is why a two-word search
        differs by a factor of ten.

        FTS is the better semantics for a search box, and it is what the
        product is designed around. But a person who reloads a page after the
        index goes stale gets a genuinely different answer, so the fallback is
        REPORTED rather than silent: `search_is_indexed` reaches the interface
        through /api/health, and the header says the index is stale.

        The fallback is not silent: :attr:`search_is_indexed` is what the API
        reports, so "your search was slow because the index is stale" is
        something the interface can say rather than something the person has to
        guess. A silent fallback would be the same defect as a filter that
        silently does nothing.

        Measured on the real corpus, `search=engineer`: 8,674 ms through LIKE,
        against a ~1,000 ms target for the whole request.
        """
        if self._search_is_indexed():
            match = to_match_query(text)
            if match is None:
                # Only punctuation was typed. Nothing is searchable, so nothing
                # matches -- returning everything would be the "unknown filter"
                # defect wearing a different hat.
                return "1 = 0", []
            return (
                f"j{a}.id IN (SELECT job_id FROM job_search WHERE job_search MATCH ?)",
                [match],
            )

        term = _like_term(text)
        return (
            (
                f"(LOWER(j{a}.title) LIKE ? ESCAPE '\\'"
                f" OR LOWER(c{a}.name) LIKE ? ESCAPE '\\'"
                f" OR LOWER(jr{a}.description_text) LIKE ? ESCAPE '\\')"
            ),
            [term, term, term],
        )

    def _search_is_indexed(self) -> bool:
        """Cached per query object: `is_current` costs an aggregate over `job`,
        and `count`, `page` and `facets` each build the clause independently
        inside one request."""
        if self._indexed is None:
            self._indexed = is_current(self.conn)
        return self._indexed

    @property
    def search_is_indexed(self) -> bool:
        """For the API, so the interface can report a degraded search."""
        return self._search_is_indexed()

    def _representative_clause(
        self, config_id: str, config_version: int, f: JobFilter
    ) -> tuple[str, list[Any]]:
        """The ids elected to stand for their `(company_id, title)` group.

        The same filters and election keys -- see the historical
        :data:`_REPRESENTATIVE_TEMPLATE` for the election rule and for the
        measurement that says why the population must be the FILTERED one.

        ROW_NUMBER elects the maximum score and then the smallest job id in
        each filtered group. It never elects over an unfiltered population.
        """
        inner = replace(f, group_duplicates=False)
        where3, params3 = self._where(config_id, config_version, inner, alias="3")
        # The election reads `company_id`, `title` and `match_score`, all of
        # which live on `j` and `jm`. Everything else is joined only if the
        # FILTER needs it -- the same reasoning as `facets`, and it matters
        # here because the election runs inside every grouped query.
        needed = self._joins_needed(inner)
        # One election over the filtered population. The former MAX + MIN
        # join read it twice, then grouped again to break score ties. Ranking
        # by the same two keys elects exactly the same id in one pass.
        clause = (
            "j.id IN (SELECT elected_id FROM ("
            " SELECT j3.id AS elected_id, ROW_NUMBER() OVER ("
            " PARTITION BY j3.company_id, j3.title"
            " ORDER BY jm3.match_score DESC, j3.id ASC) AS election_rank"
            + self._from_for(needed, "3", inner)
            + where3
            + ") WHERE election_rank = 1)"
        )
        return clause, params3

    @staticmethod
    def _order_by(f: JobFilter) -> str:
        """The ORDER BY clause, built only from whitelisted keys.

        Raises rather than falling back to a default: a request that asks to be
        sorted by something we do not have is a caller bug, and silently
        returning score order would hide it behind plausible-looking output.
        `j.id` breaks ties so pagination cannot drop or repeat a row.
        """
        if f.sort not in SORT_KEYS:
            raise SortKeyError(f"unknown sort key {f.sort!r}; expected one of {sorted(SORT_KEYS)}")
        if f.direction.lower() not in SORT_DIRECTIONS:
            raise SortKeyError(f"unknown sort direction {f.direction!r}; expected asc or desc")
        return f" ORDER BY {SORT_COLUMNS[f.sort]} {f.direction.upper()}, j.id ASC"

    def _soft_order(self, f: JobFilter, alias: str = "") -> tuple[str, list[Any]]:
        """The PREFER and AVOID terms, which sort and never filter.

        Returned separately from `_order_by` because they are the only part of
        the ordering that carries parameters, and because they must be able to
        produce nothing at all: a candidate who has set neither gets exactly the
        SQL this product produced before they existed.

        Each phrase contributes one point in one direction, so three preferred
        phrases matched beats two. The sum is computed here and stored nowhere,
        which is what keeps a display preference out of `job_match`.
        """
        if not f.preferred_keywords and not f.avoided_keywords:
            return "", []
        terms: list[str] = []
        params: list[Any] = []
        for phrase in f.preferred_keywords:
            clause, phrase_params = self._search_clause(phrase, alias)
            terms.append(f"(CASE WHEN {clause} THEN 1 ELSE 0 END)")
            params.extend(phrase_params)
        for phrase in f.avoided_keywords:
            clause, phrase_params = self._search_clause(phrase, alias)
            terms.append(f"(CASE WHEN {clause} THEN -1 ELSE 0 END)")
            params.extend(phrase_params)
        return " + ".join(terms), params

    # -- the three readers -------------------------------------------------

    def count(self, config_id: str, config_version: int, f: JobFilter) -> int:
        # A grouped count needs the number of groups, never their elected
        # representatives. Both group keys are NOT NULL in the schema.
        # Keep the exact filtered electorate, including tracking exemptions.
        inner = replace(f, group_duplicates=False)
        where, params = self._where(config_id, config_version, inner)
        source = self._from_for(self._joins_needed(inner), f=inner) + where
        sql = (
            "SELECT COUNT(*) AS n FROM (SELECT j.company_id, j.title"
            + source
            + " GROUP BY j.company_id, j.title)"
            if f.group_duplicates
            else "SELECT COUNT(*) AS n" + source
        )
        row = self.conn.execute(sql, params).fetchone()
        return int(row["n"])

    def hidden_by_eligibility(
        self,
        config_id: str,
        config_version: int,
        f: JobFilter,
        *,
        narrow_total: int | None = None,
    ) -> int:
        """How many postings THIS query would have returned but for the gate.

        Counted rather than estimated, and counted against the SAME filter with
        one field flipped, so it is exactly "how many more you would see if you
        ticked the box". A number derived any other way would drift from what
        the box actually does the first time another filter interacted with it.

        Zero when the person has already asked to see them: there is nothing
        hidden to report, and a disclosure saying "0 hidden" is noise.

        `narrow_total` is the count of `f` when the caller already has it,
        which the list route always does. Without it this ran `count(f)` a
        second time, and an independent functional review measured what that
        costs on the real corpus: 577 ms of the 1,066 ms this method took on a
        grouped query, for an answer already sitting in a local variable. The
        parameter is optional rather than required so the method still stands
        alone, and it is keyword-only so it cannot be passed by accident.
        """
        if f.include_ineligible:
            return 0
        widened = replace(f, include_ineligible=True)
        narrow = (
            narrow_total if narrow_total is not None else self.count(config_id, config_version, f)
        )
        return self.count(config_id, config_version, widened) - narrow

    def hidden_by_seniority(
        self,
        config_id: str,
        config_version: int,
        f: JobFilter,
        *,
        narrow_total: int | None = None,
    ) -> int:
        """How many postings this query set aside for being the wrong level.

        The fourth of four, and the only one that is a preference rather than
        a fact about the posting. An employer ruled you out, your search ruled
        the work out, nobody could tell -- and this one is "you said you did
        not want to see these".
        """
        if f.include_excluded_seniority or not f.excluded_seniorities:
            return 0
        widened = replace(f, include_excluded_seniority=True)
        narrow = (
            narrow_total if narrow_total is not None else self.count(config_id, config_version, f)
        )
        return self.count(config_id, config_version, widened) - narrow

    def hidden_unresolved(
        self,
        config_id: str,
        config_version: int,
        f: JobFilter,
        *,
        narrow_total: int | None = None,
    ) -> int:
        """How many postings this query set aside because nothing answered.

        The third of three, and a separate method for the reason the other two
        are separate: they are three different sentences to a reader. An
        employer ruled you out, your search ruled the work out, and the posting
        did not say. Only the first is a rejection.
        """
        if f.include_unresolved:
            return 0
        widened = replace(f, include_unresolved=True)
        narrow = (
            narrow_total if narrow_total is not None else self.count(config_id, config_version, f)
        )
        return self.count(config_id, config_version, widened) - narrow

    def hidden_by_screening(
        self,
        config_id: str,
        config_version: int,
        f: JobFilter,
        *,
        narrow_total: int | None = None,
    ) -> int:
        """How many postings this query set aside as not the work asked for.

        Deliberately a separate method from `hidden_by_eligibility` rather than
        one returning a pair. They are two different sentences to a reader --
        "an employer ruled you out" and "your search ruled the work out" -- and
        a single number covering both would be the same conflation that put a
        rejection notice on three postings that had none.
        """
        if f.include_off_target:
            return 0
        widened = replace(f, include_off_target=True)
        narrow = (
            narrow_total if narrow_total is not None else self.count(config_id, config_version, f)
        )
        return self.count(config_id, config_version, widened) - narrow

    def hidden_by_the_candidate(
        self,
        config_id: str,
        config_version: int,
        f: JobFilter,
        *,
        narrow_total: int | None = None,
    ) -> int:
        """How many postings this query left out because she hid them.

        A third method rather than a third element of a tuple, for the reason
        the second one exists: these are three different sentences to a reader,
        and one number covering all of them would say "47 hidden" about
        populations she can only put back one way each.
        """
        if f.include_user_hidden:
            return 0
        widened = replace(f, include_user_hidden=True)
        narrow = (
            narrow_total if narrow_total is not None else self.count(config_id, config_version, f)
        )
        return self.count(config_id, config_version, widened) - narrow

    def page(
        self,
        config_id: str,
        config_version: int,
        f: JobFilter,
        *,
        full_description: bool = False,
    ) -> list[ScoredJob]:
        """One page of scored postings, fully populated including ``result``.

        The description is truncated by SUBSTR in SQL rather than in Python:
        sixty untruncated descriptions is roughly 400 KB of text no card
        renders, and the cost of it is paid on the wire before Python sees a
        row. :meth:`get_one` is the detail view and truncates nothing.

        When ``group_duplicates`` is on, ONE follow-up query gives the whole
        page its siblings -- never one query per row, which is the shape that
        turns a 60-row page into 61 round trips. Same batching discipline as
        ``ProviderPayloadRepo.latest_for_jobs``, and the same reason.

        When it is off the follow-up is skipped entirely: every row is its own
        group, ``duplicate_count`` is 1 by construction, and asking the
        database to confirm that would be pure cost on the default path.
        """
        where, params = self._where(config_id, config_version, f)
        description = (
            "jr.description_text"
            if full_description
            else f"SUBSTR(jr.description_text, 1, {PAGE_DESCRIPTION_CHARS})"
        )
        # Through the SAME builder count() and facets() use. They were on
        # different FROMs -- lean there, full here -- which meant a `job_match`
        # row whose `content_hash` had no `job_raw` row would be COUNTED and
        # bucketed and then silently missing from the page. No such row exists
        # in the corpus today, so this is the shape of the defect rather than
        # the defect; but "two numbers on one screen disagree" is exactly what
        # this class exists to prevent, and it should not depend on referential
        # integrity that nothing asserts. `_select` reads all four optional
        # tables, so all four are requested.
        # PREFER and AVOID sort AHEAD of the chosen order, and only here. They
        # are absent from `count` and `facets` on purpose: they change nothing
        # about WHICH postings match, so a total that moved when somebody typed a
        # preferred word would be reporting a lie about the corpus.
        soft, soft_params = self._soft_order(f)
        order = self._order_by(f)
        if soft:
            order = f" ORDER BY ({soft}) DESC," + order[len(" ORDER BY ") :]
        sql = (
            self._select(description)
            + self._from_for(self._joins_needed(f, _SELECT_TABLES), f=f)
            + where
            + order
            + " LIMIT ? OFFSET ?"
        )
        rows = self.conn.execute(sql, [*params, *soft_params, f.limit, f.offset]).fetchall()
        if not f.group_duplicates:
            return [self._to_scored_job(row) for row in rows]
        groups = self._siblings_for(
            config_id,
            config_version,
            [(str(r["company_id"]), str(r["title"])) for r in rows],
            within=f,
        )
        return [self._to_scored_job(row, groups=groups) for row in rows]

    def get_one(self, job_id: str, config_id: str, config_version: int) -> ScoredJob | None:
        """One posting in full, for the detail view. No truncation.

        The siblings are read unconditionally, because the drawer is where the
        person asks "what exactly am I looking at". A posting that shares its
        title with seven others at the same company is worth saying whether or
        not the list that led here was grouped. One job, one small query.
        """
        row = self.conn.execute(
            self._select("jr.description_text")
            + self._from_for(_SELECT_TABLES)
            + " WHERE jm.config_id = ? AND jm.config_version = ? AND j.id = ?",
            (config_id, config_version, job_id),
        ).fetchone()
        if row is None:
            return None
        # `within=None` on purpose: the drawer was reached from one posting,
        # not from a filtered list, so the honest population is every posting
        # under this configuration version. `page` passes its filter instead --
        # see :meth:`_siblings_for`.
        groups = self._siblings_for(
            config_id, config_version, [(str(row["company_id"]), str(row["title"]))], within=None
        )
        return self._to_scored_job(row, groups=groups)

    def _siblings_for(
        self,
        config_id: str,
        config_version: int,
        pairs: Sequence[tuple[str, str]],
        *,
        within: JobFilter | None,
    ) -> dict[tuple[str, str], tuple[int, tuple[str, ...]]]:
        """``(company_id, title) -> (how many postings, which locations)``.

        Pairs are matched as pairs -- ``(company = ? AND title = ?) OR ...`` --
        rather than as ``company IN (...) AND title IN (...)``. The second form
        is shorter and wrong: it is a cross product, so a page holding two
        titles at one company would count postings that share neither pair with
        the row being described. Each OR term probes `idx_job_company_title`
        directly, so the shape that is correct is also the shape that is fast.

        ``within`` IS THE POPULATION, AND IT MUST BE THE ELECTORATE
        ----------------------------------------------------------
        The election in :data:`_REPRESENTATIVE_TEMPLATE` runs over the
        FILTERED rows, so the count standing beside a representative has to be
        taken over those same rows. Counting over the whole configuration
        version instead makes `duplicate_count` claim siblings the grouping
        never collapsed: under `remote_only` the corpus returns 20 postings and
        20 groups -- nothing to collapse -- while the unfiltered count sums to
        23, so three cards would announce a sibling that is not in the list and
        cannot be reached from it. The badge and the list stop agreeing, which
        is the failure this class exists to prevent.

        With the filter applied, ``sum(duplicate_count)`` equals
        :meth:`count` with grouping off, under every filter. That is the
        invariant, and a test asserts it over the real filters.

        ``within=None`` counts over the configuration version and is for
        :meth:`get_one` alone, which has no list context: the drawer answers
        "how many times is this published", not "how many of them are in the
        view you came from".
        """
        wanted = list(dict.fromkeys(pairs))
        if not wanted:
            return {}
        # Alias `5`, so this clause cannot collide with the election's `3`/`4`.
        # Grouping is switched off in it for the same reason it is switched off
        # in the election's inner passes: the population being counted is the
        # filtered rows, not the representatives among them.
        scope = replace(within, group_duplicates=False) if within is not None else JobFilter()
        where5, params5 = self._where(config_id, config_version, scope, alias="5")
        needed5 = self._joins_needed(scope)
        counts: dict[tuple[str, str], int] = {}
        places: dict[tuple[str, str], list[str]] = {}
        for start in range(0, len(wanted), _PAIR_CHUNK):
            chunk = wanted[start : start + _PAIR_CHUNK]
            # The only interpolation is the repeated clause text. Every company
            # id and every title is still bound.
            terms = " OR ".join("(j5.company_id = ? AND j5.title = ?)" for _ in chunk)
            values: list[Any] = [*params5]
            for company_id, title in chunk:
                values.extend((company_id, title))
            rows = self.conn.execute(
                "SELECT j5.company_id AS company_id, j5.title AS title,"
                " j5.location_raw AS location_raw"
                + self._from_for(needed5, "5", scope)
                + where5
                + f" AND ({terms})"
                + " ORDER BY j5.company_id, j5.title, j5.id",
                values,
            ).fetchall()
            for row in rows:
                key = (str(row["company_id"]), str(row["title"]))
                counts[key] = counts.get(key, 0) + 1
                place = (row["location_raw"] or "").strip()
                if place:
                    places.setdefault(key, []).append(place)
        return {
            key: (count, tuple(dict.fromkeys(sorted(places.get(key, ())))))
            for key, count in counts.items()
        }

    def facets(
        self, config_id: str, config_version: int, f: JobFilter
    ) -> dict[str, dict[str, int]]:
        """Counts per filter chip, over exactly the rows :meth:`count` counts.

        Each facet is grouped over the SAME filtered population rather than
        over the population minus its own filter. That makes every facet's
        total equal ``count(f)`` -- which is the property the interface needs
        to be able to state, and the one a test asserts.
        """
        where, params = self._where(config_id, config_version, f)
        # Each dimension names the table it reads, so a facet joins that table
        # and no other. `job_raw` appears in none of them, which is the point:
        # it was being joined six times per request for 104 MB of description
        # text that no facet looks at.
        dimensions = {
            "company": ("c.slug", "c"),
            "provider": ("j.provider", None),
            "title_class": ("jm.title_class", None),
            "eligibility_status": ("jm.eligibility_status", None),
            "status": ("COALESCE(ja.status, 'DISCOVERED')", "ja"),
            "fit_band": ("jm.fit_band", None),
            # The columns migration 0017 added. NULL becomes the word the
            # filter accepts, so the chip a person clicks and the value the
            # API receives are the same string -- and "not stated" is offered
            # as a choice rather than being an absence they have to infer.
            "worksite": (f"COALESCE(jm.work_model, '{NOT_STATED}')", None),
            "seniority": ("jm.seniority", None),
            "employment_type": (f"COALESCE(jm.employment_type, '{NOT_STATED}')", None),
            # WHICH NATIONAL STATUTE, when a posting named one. Added V1.7,
            # the day the column stopped being NULL on every row: Gupy
            # publishes the employer's own contract type and 37,368
            # postings resolved to CLT and 621 to PJ, from 0 and 0.
            #
            # `NOT_STATED` is a bucket here and is NOT in the filter's
            # vocabulary, deliberately: a person may see how many postings
            # never said, and asking for those is the absence of this
            # filter rather than a value of it. Every posting outside
            # Brazil sits in that bucket and always will.
            "contract_regime": (f"COALESCE(jm.contract_regime, '{NOT_STATED}')", None),
            "salary_currency": (f"COALESCE(jm.salary_currency, '{NOT_STATED}')", None),
            "salary_period": (f"COALESCE(jm.salary_period, '{NOT_STATED}')", None),
            # WHAT THE POSTING ASKED FOR. Migration 0027, and a facet from the
            # first day rather than later: a control with no counts behind it
            # is not a filter, which this codebase learned when the panel drew
            # a technology group nobody was tallying.
            #
            # `NOT_STATED` is a real member of the vocabulary here, unlike the
            # contract regime above, because the READING has it: a posting that
            # never mentioned experience produces `NOT_STATED` rather than
            # NULL. NULL is what a row scored before this migration has, and
            # COALESCE folds those into the same bucket -- which is honest,
            # because in both cases nothing was read.
            "experience_requirement": (
                f"COALESCE(jm.experience_requirement, '{NOT_STATED}')",
                None,
            ),
        }
        # ONE statement for all of it, tallied in Python.
        #
        # This used to be fourteen statements: eleven `GROUP BY` queries, two
        # set columns and one membership scan, each re-executing the whole
        # `WHERE`. Under `group_duplicates` that `WHERE` carries a two-level
        # correlated subquery choosing one representative per (company,
        # title); SQLite reuses it within a statement and cannot reuse it
        # across fourteen. Measured on the 21,202-row corpus: 9,411 ms, of
        # which essentially none was the tallying.
        #
        # Reading a column in Python is not a new liberty taken here. It is
        # what `country`, `region` and `signal` have always required, because
        # one posting carries several of each and no `GROUP BY` over a fenced
        # string can bucket them. The other eleven were in SQL because each
        # was written on its own, not because they had to be.
        #
        # `c` and `ja` are joined unconditionally now: two of the dimensions
        # need them, so asking per dimension bought nothing once there is one
        # query. Both are LEFT-shaped or keyed, so neither changes which rows
        # come back -- and `count()`, which is what these have to total, goes
        # through the same `_where`.
        needed = self._joins_needed(f, frozenset({"c", "ja"}))
        selected = ", ".join(f"{column} AS d_{name}" for name, (column, _) in dimensions.items())

        # **PLAIN TUPLES, NOT `sqlite3.Row`, and the difference is the whole
        # response time of the interface.**
        #
        # Measured 2026-09-09 on 103,010 rows: fetching five columns took 16.0
        # seconds with the connection's `sqlite3.Row` factory and 3.1 without
        # it. Reading the values afterwards took 0.12 seconds either way -- so
        # nearly thirteen seconds went into CONSTRUCTING row objects whose only
        # use here is to be indexed once each and discarded.
        #
        # This is the one query in the repository that reads every matching row
        # rather than a page of sixty, which is why it is the only one that
        # cares. V1.2 took it from 9,411ms to 1,431ms over 21,202 rows by making
        # it ONE statement; the corpus is five times bigger now and the row
        # factory became the cost that statement was hiding.
        #
        # A cursor carries its own factory, so the connection's is untouched and
        # nothing else in this class changes behaviour.
        cursor = self.conn.cursor()
        cursor.row_factory = None
        rows = cursor.execute(
            f"SELECT {selected},"
            " jm.countries AS s_country, jm.regions AS s_region, jm.membership AS s_membership,"
            " jm.entry_signals AS s_entry" + self._from_for(needed, f=f) + where,
            params,
        ).fetchall()

        # The SELECT order, which is the dimension order followed by the three
        # fenced columns. Derived rather than written down twice: a hand-kept
        # list of positions is a list that silently points at the wrong column
        # the first time somebody adds a dimension.
        at = {name: index for index, name in enumerate(dimensions)}
        country_at = len(dimensions)
        region_at = country_at + 1
        membership_at = country_at + 2
        entry_at = country_at + 3

        tallies: dict[str, Counter[str]] = {name: Counter() for name in dimensions}
        countries: Counter[str] = Counter()
        regions: Counter[str] = Counter()
        fired: Counter[str] = Counter()
        entry: Counter[str] = Counter()
        prefix = f"{FIRED_TAG}:"

        for row in rows:
            for name, index in at.items():
                # `str(...)` including of None, which is what `GROUP BY` over
                # a nullable column produced through `str(row["bucket"])`. The
                # dimensions that must never say "None" already COALESCE in
                # SQL; the rest are NOT NULL by schema.
                tallies[name][str(row[index])] += 1
            for value in _fenced(row[country_at]):
                countries[value] += 1
            for value in _fenced(row[region_at]):
                regions[value] += 1
            for tagged in _fenced(row[membership_at]):
                if tagged.startswith(prefix) and tagged[len(prefix) :]:
                    fired[tagged[len(prefix) :]] += 1
            for value in _fenced(row[entry_at]):
                entry[value] += 1

        # `n DESC, bucket ASC`, which is the order the eleven queries produced
        # and the order the panel draws its chips in.
        result: dict[str, dict[str, int]] = {
            name: _ranked(counter) for name, counter in tallies.items()
        }

        # Countries and regions are SETS. Their counts do NOT sum to
        # `count(f)` -- a posting in two countries is in two buckets -- and
        # that is the honest shape rather than a defect. The single-valued
        # facets above still sum, which is the property the interface asserts
        # of them.
        result["country"] = _ranked(countries)
        result["region"] = _ranked(regions)
        # A SET as well, for the same reason: one posting can say `entry level`
        # AND `training provided`, so these buckets overlap and do not sum to
        # `count(f)`. The single-valued facets above still sum.
        result["entry_signal"] = _ranked(entry)

        # Which lexicon signals fired. Emitted as TWO facets over one index,
        # because "which signals fired" and "which tools does this use" are
        # the same read and different questions -- and because the panel once
        # offered a technology group whose counts nobody was producing, so it
        # appeared only after a value was already chosen. A control that
        # cannot be clicked is not a filter.
        ranked_fired = _ranked(fired)
        result["signal"] = ranked_fired
        result["technology"] = {
            key: n for key, n in ranked_fired.items() if key in TECHNOLOGY_SIGNALS
        }
        return result

    def _tagged_facet(
        self, tag: str, where: str, params: list[Any], f: JobFilter
    ) -> dict[str, int]:
        """Counts per id inside the `|tag:id|` membership index.

        Read in Python rather than in SQL for the same reason `_set_facet` is:
        one posting carries several ids, so no GROUP BY over the column can
        bucket them. The column holds ONLY identifiers this system generated
        -- migration 0013 exists because a filter once read the blob that also
        holds evidence quotes -- so splitting it is safe in a way splitting
        `result_json` would not be.
        """
        needed = self._joins_needed(f)
        rows = self.conn.execute(
            "SELECT jm.membership AS m, COUNT(*) AS n"
            + self._from_for(needed, f=f)
            + where
            + " AND jm.membership <> '' GROUP BY jm.membership",
            params,
        ).fetchall()
        prefix = f"{tag}:"
        counts: dict[str, int] = {}
        for row in rows:
            for entry in str(row["m"]).strip("|").split("|"):
                if entry.startswith(prefix):
                    key = entry[len(prefix) :]
                    if key:
                        counts[key] = counts.get(key, 0) + int(row["n"])
        return dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))

    def _set_facet(
        self, column: str, where: str, params: list[Any], f: JobFilter
    ) -> dict[str, int]:
        """Counts for one pipe-fenced set column, one value at a time.

        The candidate values come from the rows themselves rather than from a
        fixed list, so a country the gazetteer learns tomorrow appears without
        a code change, and one that never occurs is not offered as a chip that
        returns nothing.
        """
        needed = self._joins_needed(f)
        rows = self.conn.execute(
            f"SELECT {column} AS fenced, COUNT(*) AS n"
            + self._from_for(needed, f=f)
            + where
            + f" AND {column} <> '' GROUP BY {column}",
            params,
        ).fetchall()
        counts: dict[str, int] = {}
        for row in rows:
            for value in str(row["fenced"]).strip("|").split("|"):
                if value:
                    counts[value] = counts.get(value, 0) + int(row["n"])
        return dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))

    # -- row assembly -------------------------------------------------------

    @staticmethod
    def _select(description_expression: str) -> str:
        return (
            # `company_id` is selected for the sibling lookup, not for display:
            # `ScoredJob` carries the SLUG, which is what the interface filters
            # on. Grouping keys on the id because that is what `job` actually
            # stores and what `idx_job_company_title` is built over.
            "SELECT j.id AS job_id, j.title, j.provider, j.external_id, j.url,"
            " j.company_id AS company_id,"
            " j.location_raw, j.department, j.posted_at, j.first_seen_at, j.last_seen_at,"
            " j.closed_at, jm.content_hash, c.name AS company_name, c.slug AS company_slug,"
            f" {description_expression} AS description_text, jm.result_json,"
            " ja.status AS application_status, ja.applied_at, ja.saved, ja.hidden_at,"
            " ja.notes,"
            " je.payload_json AS enrichment_json, je.content_hash AS enrichment_hash"
        )

    @staticmethod
    def _to_scored_job(
        row: sqlite3.Row,
        *,
        groups: dict[tuple[str, str], tuple[int, tuple[str, ...]]] | None = None,
    ) -> ScoredJob:  # noqa: D401
        count, locations = _group_of(row, groups)
        return ScoredJob(
            duplicate_count=count,
            sibling_locations=locations,
            job_id=str(row["job_id"]),
            title=str(row["title"]),
            company_name=str(row["company_name"]),
            company_slug=str(row["company_slug"]),
            provider=str(row["provider"]),
            access_method=access_method_for(row["provider"]),
            external_id=str(row["external_id"]),
            url=row["url"],
            location_raw=row["location_raw"],
            department=row["department"],
            posted_at=row["posted_at"],
            first_seen_at=row["first_seen_at"],
            last_seen_at=row["last_seen_at"],
            closed_at=row["closed_at"],
            content_hash=str(row["content_hash"]),
            description_text=str(row["description_text"]),
            result=deserialise_match(str(row["result_json"])),
            application_status=str(row["application_status"] or DEFAULT_APPLICATION_STATUS.value),
            applied_at=row["applied_at"],
            saved=bool(row["saved"]),
            hidden_at=row["hidden_at"],
            notes=row["notes"],
            enrichment=_enrichment_of(row),
        )


def _group_of(
    row: sqlite3.Row, groups: dict[tuple[str, str], tuple[int, tuple[str, ...]]] | None
) -> tuple[int, tuple[str, ...]]:
    """What this row stands for: ``(how many postings, which locations)``.

    ``(1, ())`` when nothing was looked up, which is the honest reading of "we
    did not ask": a row with no sibling information stands for itself alone.
    Never ``(0, ...)`` -- a posting always counts itself.

    ``(1, ())`` again for a group of one, even though that posting does have a
    location. The locations are what a grouped row DISCLOSES, and a singleton
    discloses nothing: its own place is already on the card under "Where".
    Emptying it makes "this row stands for others" a single condition rather
    than a count and a list that have to be read together.

    The row's OWN location leads the list, and the rest follow in sorted order.
    Putting it first is the difference between "this card is about somewhere,
    and also these places" and an alphabetical list in which the posting you
    would actually open is in an arbitrary position.
    """
    if not groups:
        return 1, ()
    key = (str(row["company_id"]), str(row["title"]))
    found = groups.get(key)
    if found is None or found[0] <= 1:
        return 1, ()
    count, locations = found
    mine = (row["location_raw"] or "").strip()
    ordered = [mine, *(place for place in locations if place != mine)] if mine else list(locations)
    return max(count, 1), tuple(ordered[:MAX_SIBLING_LOCATIONS])


def _enrichment_of(row: sqlite3.Row) -> dict[str, Any]:
    """The stored local observation, or an empty dict.

    Never raises. A damaged payload reads as "no enrichment", because a broken
    cache file must degrade the drawer, not the whole list query.

    `stale` is the one thing computed here: an enrichment whose `content_hash`
    no longer matches the job's is an observation of TEXT THAT CHANGED. It is
    shown, labelled stale, rather than hidden -- hiding it would quietly lose
    the fact that the posting was edited.
    """
    keys = row.keys()
    if "enrichment_json" not in keys:
        return {}
    payload = row["enrichment_json"]
    if not payload:
        return {}
    try:
        parsed = json.loads(str(payload))
    except (TypeError, ValueError):
        return {}
    if not isinstance(parsed, dict):
        return {}
    if "enrichment_hash" in keys and "content_hash" in keys:
        parsed["stale"] = row["enrichment_hash"] != row["content_hash"]
    return parsed
