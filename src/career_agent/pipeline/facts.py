"""What the matcher is told about a posting, derived in exactly one place.

WHY THIS FILE EXISTS
--------------------
There were two of them. `rescore.py` built `JobFacts` from what was PERSISTED
-- the job row plus the provider's archived payload, read through that
provider's own field map and compensation reader -- and `demo_seed.py` built
it from the corpus FILE, passing an employment type and a full salary block
straight in as keyword arguments.

The result was a database that disagreed with itself. Measured on a fresh
demo: pressing Recalculate moved 18 of 19 scores, `data_confidence` dropping
by 10 or 20 on almost every row, because a forced rescore could not
reconstruct facts that had never been written down.

THE INVARIANT THIS FILE MAKES STRUCTURAL
-----------------------------------------
**A posting's derived facts must be reconstructible from the raw source
payload and metadata that would exist for the provider it came from.**

Not "should be". Reconstructible: seed a database, force a rescore, and every
semantically meaningful column is unchanged --
`tests/integration/test_demo_is_reconstructible.py` asserts exactly that.

The way to make that true is not to remember it in two places. It is to have
one function, so a fact that reaches the matcher has to come through the
provider's own reader, whichever caller is asking.

WHAT IT MEANS FOR THE DEMO
---------------------------
The demo corpus demonstrates what Career Agent can know from a source, not
what a fixture author wishes it knew. A posting imitating a provider whose
adapter exposes no employment type has no employment type, and its card says
"not stated" -- which is the truth about that source, and is the product
working.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from career_agent.domain.enums import LocalContractRegime
from career_agent.domain.provider_values import (
    Engagement,
    MetadataDimension,
    Normalisation,
    normalise_engagement,
    resolve_deterministically,
)
from career_agent.match.engine import JobFacts
from career_agent.match.pay import read_pay
from career_agent.providers.base import CompensationHint, resolve_metadata
from career_agent.providers.registry import (
    compensation_reader_for,
    content_completeness_for,
    field_map_for,
    publishes_hiring_scope,
)


def compensation_from(provider: str | None, payload: Any) -> CompensationHint | None:
    """What this archived payload says about pay, through the provider's reader.

    No vendor is named here and no payload path is spelled. The registry
    answers which reader belongs to which provider, and the reader knows where
    in its own JSON the numbers sit -- Greenhouse writes a custom field per
    country, Ashby a tier per currency, and neither shape belongs in generic
    code.

    None means "nothing archived, or nothing about pay in what was", which the
    matcher scores `salary_unknown` and never treats as a refusal.
    """
    if payload is None or not provider:
        return None
    reader = compensation_reader_for(provider)
    if reader is None:
        return None
    return reader(payload)


def employment_type_from(provider: str | None, payload: Any) -> str | None:
    """The employment type this provider DECLARED, normalised, or None.

    **Only ALIAS resolutions are kept.** `resolve_deterministically` answers
    `UNMAPPED` for a spelling nobody has catalogued -- including compound ones
    like `Full Time/Part Time`, which describe a role that is either and whose
    collapse to one value would be a decision rather than a normalisation.
    Storing an unmapped string would put a spelling into the filter vocabulary
    that no chip offers and no test covers; leaving it out means the posting
    reads NOT_STATED, which is what "the provider said something we do not
    understand" honestly amounts to.

    None is also the answer for a provider whose adapter maps no
    employment-type dimension at all. That is not a gap in this function: it is
    the honest reading of a source that does not publish one.
    """
    if payload is None or not provider:
        return None
    for observation in resolve_metadata(provider, field_map_for(provider), payload):
        if observation.dimension is not MetadataDimension.EMPLOYMENT_TYPE_HINT:
            continue
        answer = resolve_deterministically(observation.dimension, observation.source_value)
        if answer is not None and answer.kind is Normalisation.ALIAS:
            return str(answer.value)
    return None


def engagement_from(provider: str | None, payload: Any) -> Engagement | None:
    """What the board's own contract-type field says about the engagement.

    THE RAW VALUE, not the normalised one, and that distinction is the whole
    function. `employment_type_from` above resolves through the portable
    vocabulary, where `vacancy_legal_entity` and `vacancy_type_freelancer` both
    land on `CONTRACT` -- correct for a filter chip, and the exact place where
    "this person invoices through a company under Brazilian law" is lost.

    So this reads the same declared value a second time against a table that
    keeps the two questions apart, and it reads it BEFORE normalisation because
    the statute is in the spelling.

    None for a provider that maps no employment-type dimension, and None for a
    spelling nobody has catalogued. Both are the honest answer rather than a
    gap: a contract type this system does not understand must not become one it
    acts on.
    """
    if payload is None or not provider:
        return None
    for observation in resolve_metadata(provider, field_map_for(provider), payload):
        if observation.dimension is not MetadataDimension.EMPLOYMENT_TYPE_HINT:
            continue
        engagement = normalise_engagement(observation.source_value)
        if engagement is not None:
            return engagement
    return None


def workplace_type_from(provider: str | None, payload: Any) -> str | None:
    """REMOTE / HYBRID / ONSITE as the BOARD'S OWN FIELD stated it, or None.

    The same shape as `employment_type_from` next door and for the same
    reason: only ALIAS resolutions are kept. A spelling nobody has catalogued
    -- Get on Board's `remote_local`, for instance -- resolves UNMAPPED and
    yields None, because a workplace type this system does not understand must
    not become one it acts on.

    None is also the answer for a provider whose adapter maps no work-model
    dimension. That is the honest reading of a board that never asked.

    It matters more than it looks. `gates.structured_geography` reads this to
    decide whether a posting's location is an OFFICE or a REMOTE HIRING
    REGION, and those are opposite meanings for the same column.
    """
    if payload is None or not provider:
        return None
    for observation in resolve_metadata(provider, field_map_for(provider), payload):
        if observation.dimension is not MetadataDimension.WORK_MODEL_HINT:
            continue
        answer = resolve_deterministically(observation.dimension, observation.source_value)
        if answer is not None and answer.kind is Normalisation.ALIAS:
            return str(answer.value)
    return None


def job_facts(
    *,
    job_id: str,
    title: str,
    description: str,
    location_raw: str | None,
    posted_at: str | None,
    provider: str | None,
    payload: Any,
    sightings: Sequence[tuple[str, Any]] = (),
) -> JobFacts:
    """Everything the deterministic matcher is allowed to know, from what exists.

    The arguments are the job ROW and the archived PAYLOAD, and nothing else.
    A caller cannot hand in a fact of its own: there is no parameter for one.
    That is the whole point -- `demo_seed` used to pass an employment type and
    a pay period that no rescore could ever reproduce, and the only way to stop
    that happening again is for the shape of this function to make it
    impossible rather than for a comment to ask nicely.
    """
    pay = compensation_from(provider, payload)
    # THE FALLBACK, AND THE ORDER IS THE POINT. A structured field the provider
    # published is a better fact than a sentence a parser understood, so the text
    # is read ONLY when the payload said nothing about money. Measured on
    # 2026-09-10: 10,958 postings state pay in the description and 4,858 carried
    # a structured field, so this is most of the market -- including every
    # `Salario: R$ 4.500` in Brazil, where the corpus held 37 salaries in reais
    # against 78,806 postings.
    stated = read_pay(description) if pay is None else None
    engagement = engagement_from(provider, payload)
    return JobFacts(
        job_id=job_id,
        title=title or "",
        description=description,
        location_raw=location_raw,
        posted_at=posted_at,
        provider=provider,
        access_method=_access_method(provider),
        # The same string as `location_raw`, and a different CLAIM. Passed only
        # for a board that ASKS the employer where it may hire; for every ATS
        # this is None, because their location field names an OFFICE and an
        # office is not a hiring scope. The registry answers because this layer
        # may ask it and the matcher may not.
        # And when this provider publishes no scope, the first SIGHTING by an
        # aggregator that does: a Jobgether lead folded into its employer's
        # own Greenhouse posting carries `Anywhere`, which is the one fact the
        # lead was worth (2026-09-11). Sightings are persisted rows, so the
        # fact is reconstructible from what exists, as every fact here is.
        declared_hiring_scope=(
            location_raw
            if provider and publishes_hiring_scope(provider)
            else _sighting_scope(sightings)
        ),
        # None all the way through when the board said nothing, which the
        # matcher scores `salary_unknown` and never rejects. Compensation is a
        # preference, never a filter.
        salary_min=pay.min_value if pay else (stated.min_value if stated else None),
        salary_max=pay.max_value if pay else (stated.max_value if stated else None),
        salary_currency=pay.currency if pay else (stated.currency if stated else None),
        salary_period=pay.period if pay else (stated.period if stated else None),
        # The other currencies the same payload stated, if any. The scorer
        # picks; the pipeline only carries.
        salary_alternates=pay.alternate_bands if pay else (),
        employment_type=employment_type_from(provider, payload),
        # THE SAME FIELD READ A SECOND TIME, for the half the line above
        # cannot carry. `vacancy_legal_entity` normalises to `CONTRACT`, which
        # is right for a filter and drops the fact that the engagement is
        # pessoa juridica under Brazilian law. These two are what fill
        # `contract_regime`, a column that has been NULL on every scored row
        # since migration 0020 created it -- because the blocker was never the
        # reader, it was that no source in the corpus published the field.
        declared_relationship=engagement.relationship.value if engagement else None,
        declared_contract_regime=(
            engagement.regime.value
            if engagement and engagement.regime is not LocalContractRegime.UNRESOLVED
            else None
        ),
        # The board's own structured answer, which is what tells the geography
        # gate whether `location_raw` names an office or a hiring region.
        workplace_type=workplace_type_from(provider, payload),
        # WHOSE SHORTNESS IS THIS? Derived from the adapter's declared ability
        # to obtain a whole description and from whether any text was stored,
        # so it is reconstructible from what exists -- the same rule every
        # other fact in this function follows.
        content_completeness=content_completeness_for(
            provider, has_text=bool(description.strip())
        ).value,
    )


def _sighting_scope(sightings: Sequence[tuple[str, Any]]) -> str | None:
    from career_agent.providers.registry import hiring_scope_of

    for source, payload in sightings:
        scope = hiring_scope_of(source, payload)
        if scope:
            return scope
    return None


def _access_method(provider: str | None) -> str:
    from career_agent.storage.mvp_repo import access_method_for

    return access_method_for(provider)
