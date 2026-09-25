"""Recompute the arithmetic of a score without reading the advert again.

`engine.match_job` runs seven steps and the first six are the expensive ones:

    observe -> classify -> gates -> screening -> score -> confidence -> assemble

Everything those six produce is stored in `job_match.result_json`. So when a
configuration edit is confined to the sections `match/score.py` reads, the
answer can be recomputed from the stored observations. That is this module, and
it is deliberately a thin mirror of the tail of `match_job` rather than a second
scoring engine: the same four functions, called in the same order, with the same
arguments.

WHAT IT REUSES, all read out of the stored result:
    the observed signals with their hits, the title classification, the gate
    outcomes and the eligibility status, the screening verdict, and the
    seniority, employment, domestic-context and experience readings.

WHAT IT RECOMPUTES, all from `config`:
    the score components, the soft penalties, the match score, the fit band,
    the data confidence with its items, and the analysis-confidence band.

WHAT IT REFUSES. Nothing here decides whether a replay is allowed; that is
`storage.mvp_repo.replay_candidates` and `pipeline.rescore`, which compare the
stored `input_digest` and input revision against the posting's current ones. By
the time this function is called the decision is made, and its only job is to
produce the same `MatchResult` a full pass would have produced.
"""

from __future__ import annotations

from career_agent.domain.enums import GateResult
from career_agent.domain.matching import MatchResult, ObservedSignal, SemanticEvidence
from career_agent.match.engine import JobFacts, posting_facts
from career_agent.match.lexicon import body_only, prominence_of
from career_agent.match.score import (
    confidence_band_for,
    data_confidence,
    fit_band_for,
    match_score_from,
    score_components,
    soft_penalties,
)


def observed_from(config, stored: MatchResult) -> dict[str, ObservedSignal]:
    """The dictionary `observe` would have returned, from what was stored.

    `match_job` stores only the signals that were SEEN:
    `tuple(s for s in observed.values() if s.hits or s.negated_hits)`, because
    fifty empty rows would bury the handful that fired. `observe` returns all of
    them, and its docstring says why: "a caller never has to distinguish absent
    from this dict from found and negated".

    THE SILENT ONES HAVE TO COME BACK, AND THE ORACLE IS WHY THIS IS HERE.
    The first version of this rebuilt the dictionary from the stored tuple
    alone, on the reasoning that a signal with no hits scores nothing. It
    scores nothing and it SAYS something:
    `score_components` writes "No configured body phrase was recognized for
    ..." and names the signals it looked for. With them absent it fell back to
    raw identifiers, so a replayed card read `gtm_systems_work` where a scored
    card read `GTM / revenue systems`.
    `tests/integration/test_incremental_rescore.py` caught it on the
    configuration-version case, which is exactly the case replay exists for.

    Reconstructing them from `config.lexicon` is exact rather than approximate,
    because the lexicon is a GUARDED section: a replay is only reached when the
    reading identity matches, and the lexicon is part of that identity. So the
    configuration being scored under has the same signals, with the same labels
    and the same responsibilities, as the one the readings were taken under.
    `prominence_of(config, ())` is INCIDENTAL by definition, which is what
    `observe` assigns a signal with no live hits.
    """
    observed = {signal.signal_id: signal for signal in stored.signals}
    for signal_id, signal_cfg in config.lexicon.items():
        if signal_id in observed:
            continue
        observed[signal_id] = ObservedSignal(
            signal_id=signal_id,
            label=signal_cfg.label,
            responsibility=signal_cfg.responsibility,
            prominence=prominence_of(config, ()),
            hits=(),
            negated_hits=(),
        )
    return observed


def geography_resolved_in(stored: MatchResult) -> bool:
    """Whether the geography gate reached a verdict, as `match_job` asks it.

    `data_confidence` takes this, and `match_job` computes it from the gates it
    has just evaluated. The gates are stored, so the same question is asked of
    the same answers.
    """
    return any(
        gate.gate == "geography" and gate.result is not GateResult.UNRESOLVED
        for gate in stored.gates
    )


def replay(
    config,
    stored: MatchResult,
    job: JobFacts,
    *,
    computed_at: str,
    semantic: SemanticEvidence | None = None,
) -> MatchResult:
    """The result `match_job(config, job)` would return, from stored readings.

    ``job`` must come from `pipeline.facts.job_facts` -- the one derivation.
    Building it any other way is the defect V1.4 fixed: a `JobFacts` assembled
    from the `job_match` columns omits the salary alternates, the declared
    relationship, the declared contract regime and the workplace type, and the
    compensation component reads all four. Measured while this was being built:
    127 of 600 postings came out one point apart, every one of them on
    `compensation_contract`.
    """
    observed = observed_from(config, stored)
    scoring_signals = body_only(config, observed)
    components = score_components(
        config,
        observed_body=scoring_signals,
        seniority=stored.seniority,
        employment=stored.employment,
        job_facts=job,
        semantic=semantic,
    )
    penalties = soft_penalties(config, scoring_signals)
    score = match_score_from(components, penalties)

    confidence, confidence_items, confidence_unknowns = data_confidence(
        config,
        job,
        stored.seniority,
        geography_resolved=geography_resolved_in(stored),
    )

    compensation = next(c for c in components if c.component_id == "compensation_contract")
    unknowns = confidence_unknowns + ((compensation.note,) if compensation.note else ())

    return MatchResult(
        config_id=config.config_id,
        config_version=config.config_version,
        match_score=score,
        data_confidence=confidence,
        # CARRIED, NOT RECOMPUTED. Each of these is produced by a step that
        # reads a guarded configuration section, so a replay is only reached
        # when none of them could have changed.
        eligibility_status=stored.eligibility_status,
        screening_state=stored.screening_state,
        screening_reason=stored.screening_reason,
        fit_band=fit_band_for(score, config),
        analysis_confidence=confidence_band_for(confidence, config),
        title=stored.title,
        components=components,
        penalties=penalties,
        penalty_total=sum(p.points for p in penalties),
        gates=stored.gates,
        confidence_items=confidence_items,
        signals=stored.signals,
        unknowns=unknowns,
        seniority=stored.seniority,
        employment=stored.employment,
        domestic=stored.domestic,
        experience=stored.experience,
        # Recomputed from `job` rather than carried, because it is a projection
        # of the facts this call was handed and `match_job` computes it the same
        # way. If the facts had moved, the replay would have been refused.
        posting_facts=posting_facts(job),
        semantic=semantic,
        computed_at=computed_at,
    )
