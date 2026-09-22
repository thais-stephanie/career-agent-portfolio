"""Turning a `ScoredJob` into the JSON the browser renders.

This module is the only place that decides what the interface is allowed to
see, and it exists as its own file for one reason: the temptation to blend the
three measurements is strongest exactly here, at the last step before display.

So the rules live in one readable place:

* `match_score` and `data_confidence` are emitted as two fields and are never
  combined, averaged or multiplied.
* `eligibility_status` travels beside them, never folded into either.
* A job with no stored match emits ``match_score: null``; **not zero**. The
  interface must be able to say "not scored yet" rather than "scored zero",
  which are opposite statements about the same job.
* `has_applied` is computed here from the stored status and date. The client
  cannot send it, so it cannot contradict them.

Nothing here reads a database or a configuration file. It is a pure mapping,
which is what makes it testable without a server.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from career_agent.domain.application import ApplicationStatus, has_applied
from career_agent.domain.enums import EligibilityStatus, GateResult, Prominence
from career_agent.domain.matching import MatchResult, ScoredJob
from career_agent.match.lexicon import TECHNOLOGY_SIGNALS as _TECHNOLOGY_SIGNALS

#: Signals whose presence the interface presents as "technologies", as opposed
#: to responsibilities. Naming them here rather than deriving them keeps the
#: card's technology row stable when the lexicon grows a new positive signal.
#: Re-exported from `match.lexicon`, which is where signal identity lives now
#: that the repository counts the technology facet as well. One definition, so
#: the chip a person clicks and the rows they get back are the same set.
TECHNOLOGY_SIGNALS = _TECHNOLOGY_SIGNALS

_PROMINENCE_RANK = {Prominence.PRIMARY: 3, Prominence.SECONDARY: 2, Prominence.INCIDENTAL: 1}


def freshness_for(posted_at: str | None, *, today: date, fresh_days: int, stale_days: int) -> str:
    """How old the posting is, in words the interface can style.

    ``UNKNOWN`` is a real answer and the most common one for boards that do not
    publish a date. It is deliberately not "old": we do not know that.
    """
    if not posted_at:
        return "UNKNOWN"
    parsed = _parse_date(posted_at)
    if parsed is None:
        return "UNKNOWN"
    age = (today - parsed).days
    if age < 0:
        return "FRESH"
    if age <= fresh_days:
        return "FRESH"
    if age <= stale_days:
        return "RECENT"
    return "STALE"


def _parse_date(value: str) -> date | None:
    text = value.strip()
    if not text:
        return None
    # ATS payloads carry both bare dates and full ISO timestamps, with and
    # without a trailing Z. Try the cheap shapes rather than a date library.
    for candidate in (text, text.replace("Z", "+00:00")):
        try:
            return datetime.fromisoformat(candidate).date()
        except ValueError:
            continue
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _band_for(value: int, bands: dict[str, int]) -> str:
    """Map a 0-100 number onto a configured band label.

    Bands are read from the configuration rather than hard-coded so that
    changing what "STRONG" means is a config edit and a version bump, like
    every other preference.
    """
    for label, floor in sorted(bands.items(), key=lambda kv: kv[1], reverse=True):
        if value >= floor:
            return label
    return "WEAK"


def technologies_of(result: MatchResult | None) -> list[dict[str, Any]]:
    if result is None:
        return []
    rows = [
        {"signal_id": s.signal_id, "label": s.label, "prominence": str(s.prominence)}
        for s in result.signals
        if s.fired and s.signal_id in TECHNOLOGY_SIGNALS
    ]
    rows.sort(key=lambda r: _PROMINENCE_RANK.get(Prominence(r["prominence"]), 0), reverse=True)
    return rows


def top_strengths_of(result: MatchResult | None, limit: int = 3) -> list[dict[str, Any]]:
    if result is None:
        return []
    return [
        {"label": c.label, "points": round(c.points, 1), "quote": c.quote}
        for c in result.matched_strengths[:limit]
    ]


def blockers_of(result: MatchResult | None) -> list[dict[str, Any]]:
    if result is None:
        return []
    return [
        {"gate": g.gate, "label": g.blocker_id or g.gate, "reason": g.reason, "quote": g.quote}
        for g in result.blockers
    ]


def primary_gap_of(result: MatchResult | None) -> dict[str, Any] | None:
    """The single most useful thing the person does not know about this job.

    Ordered by what would change the next action: a blocker, then the gate
    that is holding eligibility open, then a missing fact.

    An unresolved gate is only surfaced when it is actually the reason
    eligibility is unresolved. Showing "the posting does not state whether
    work authorization is required" on a job already marked VERIFIED_ELIGIBLE
    was noise on every single card -- the exclusionary gates are silent on
    almost every posting, and their silence is the absence of a
    disqualification, not a doubt. `eligibility_status` already encodes which
    reading applies, so it is what decides here.
    """
    if result is None:
        return None
    if result.blockers:
        first = result.blockers[0]
        return {"kind": "BLOCKER", "label": first.gate, "reason": first.reason}
    if result.eligibility_status is EligibilityStatus.UNRESOLVED and result.unresolved_gates:
        # Geography is the gate that keeps eligibility open; prefer it over
        # whichever happens to come first in the tuple.
        gate = next(
            (g for g in result.unresolved_gates if g.gate == "geography"),
            result.unresolved_gates[0],
        )
        return {"kind": "UNRESOLVED", "label": gate.gate, "reason": gate.reason}
    if result.unknowns:
        return {"kind": "UNKNOWN", "label": "Not stated", "reason": result.unknowns[0]}
    return None


def job_card(
    job: ScoredJob, *, bands: dict[str, dict[str, int]], today: date, recency: dict
) -> dict:
    """The row shape shared by Cards and Table. One producer, two renderings."""
    result = job.result
    status = ApplicationStatus(job.application_status)
    facts: dict[str, Any] = dict(result.posting_facts) if result else {}

    scored = result is not None
    score = result.match_score if result else None
    confidence = result.data_confidence if result else None

    return {
        "job_id": job.job_id,
        "title": job.title,
        "company_name": job.company_name,
        "company_slug": job.company_slug,
        "provider": job.provider,
        "access_method": job.access_method,
        "external_id": job.external_id,
        "url": job.url,
        "location_raw": job.location_raw,
        "department": job.department,
        "posted_at": job.posted_at,
        "first_seen_at": job.first_seen_at,
        "last_seen_at": job.last_seen_at,
        "closed_at": job.closed_at,
        "freshness": freshness_for(
            job.posted_at,
            today=today,
            fresh_days=int(recency.get("fresh_days", 14)),
            stale_days=int(recency.get("stale_days", 45)),
        ),
        # -- the three measurements, kept apart -------------------------
        "scored": scored,
        "match_score": score,
        "fit_band": str(result.fit_band) if result else None,
        "data_confidence": confidence,
        "confidence_band": (
            _band_for(confidence, bands["confidence"]) if confidence is not None else None
        ),
        "eligibility_status": str(result.eligibility_status) if result else None,
        "screening_state": str(result.screening_state) if result else None,
        "title_class": str(result.title.resolved_class) if result and result.title else None,
        "title_reason": result.title.reason if result and result.title else None,
        # The LEVEL, which is what filters and columns have always read, and
        # the PROVENANCE beside it. Two fields rather than one object because
        # `seniority` is a stable public shape -- the filter contract and the
        # table both index it -- and because the interface has to be able to
        # say "the posting did not state this" without the card having to
        # unpack a nested value to find out.
        "seniority": result.seniority.value.value if result else None,
        "seniority_source": result.seniority.source.value if result else None,
        "seniority_stated": result.seniority.is_evidence if result else None,
        "seniority_evidence": result.seniority.evidence if result else None,
        "seniority_sentence": result.seniority.sentence if result else None,
        # -- two readings ABOUT the posting, never about the candidate ----
        #
        # `employment_stated` is the field that carries the whole point. A
        # posting saying `contratacao CLT` and a posting offering `plano de
        # saude` both read as an employment relationship, and only the first
        # of them SAID so. The interface renders the two differently, and it
        # can only do that if the difference reaches it.
        #
        # `domestic_context` is not an eligibility answer and the interface
        # must never present it as one. It says the compensation is structured
        # for one country; `eligibility_status` above is the only field that
        # reports what an employer actually stated about who may apply.
        "employment_relationship": result.employment.relationship.value if result else None,
        "employment_regime": result.employment.regime.value if result else None,
        "employment_stated": result.employment.is_explicit if result else None,
        "employment_evidence": result.employment.evidence if result else None,
        "domestic_context": result.domestic.context.value if result else None,
        "domestic_signal": result.domestic.signal if result else None,
        "domestic_evidence": result.domestic.evidence if result else None,
        # -- a THIRD reading about the posting: what it asked of a newcomer --
        #
        # The card can say "this role does not require previous professional
        # experience" only if the difference between an employer SAYING so and
        # an employer never mentioning it reaches the interface. Four fields
        # rather than one object, for the same reason `seniority` is four: the
        # value is what a filter reads and the quote is what a person reads.
        #
        # `experience_min_years` is 0 when the posting said none is needed and
        # null when no figure was read. Those are different facts and the
        # interface has to be able to tell them apart.
        "experience_requirement": result.experience.requirement.value if result else None,
        "experience_min_years": result.experience.min_years if result else None,
        "experience_evidence": result.experience.quote if result else None,
        "entry_signals": (
            [signal.value for signal in result.experience.entry_signals] if result else []
        ),
        # -- facts read off the posting, as the MATCHER read them ---------
        # Taken from the result rather than re-derived, so the salary on the
        # card is by construction the salary the score was computed from.
        "salary": facts.get("salary"),
        "employment_type": facts.get("employment_type"),
        # HOW MUCH OF THE POSTING THIS IS, so a card can say when a body is an
        # excerpt. It is provenance and never a quality judgement: a source
        # that returns a `snippet` and offers no way to fetch the rest has told
        # us less about a job that may be excellent. The interface must present
        # it as a fact about the SOURCE, which is why it travels beside
        # `access_method` rather than beside a score.
        "content_completeness": facts.get("content_completeness"),
        "work_model": facts.get("work_model"),
        # -- explanation, summarised for the card -------------------------
        "technologies": technologies_of(result),
        "top_strengths": top_strengths_of(result),
        "primary_gap": primary_gap_of(result),
        "blockers": blockers_of(result),
        "unknown_count": len(result.unknowns) if result else 0,
        # -- human workflow state -----------------------------------------
        "application_status": str(status),
        "applied_at": job.applied_at,
        "has_applied": has_applied(status, job.applied_at),
        "saved": job.saved,
        # Present so a card can offer "Unhide" in the restore view and
        # "Hide" everywhere else. A boolean on the wire, because the
        # interface asks only whether it is hidden; the timestamp is what
        # ORDERS the restore view and that ordering is done in SQL.
        "hidden": bool(job.hidden_at),
        "notes": job.notes,
        "enriched": bool(job.enrichment),
        # -- what this row stands for -------------------------------------
        # Emitted always, not only when grouping is on. A card that says "1"
        # and a card that says "8" are then the same shape, and the interface
        # never has to guess whether a missing field means "one posting" or
        # "nobody asked". Grouping HIDES NOTHING: the count and the places are
        # the row's own statement about what it collapsed.
        "duplicate_count": job.duplicate_count,
        "sibling_locations": list(job.sibling_locations),
        "description_excerpt": job.description_text,
    }


def job_detail(job: ScoredJob, *, bands, today: date, recency: dict, history: list[dict]) -> dict:
    """The card, plus everything the drawer needs to justify every number."""
    card = job_card(job, bands=bands, today=today, recency=recency)
    result = job.result
    card["description"] = job.description_text
    card["history"] = history
    card["enrichment"] = job.enrichment or {}

    if result is None:
        card.update(
            components=[], penalties=[], gates=[], confidence_items=[], signals=[], unknowns=[]
        )
        return card

    card["components"] = [
        {
            "component_id": c.component_id,
            "label": c.label,
            "points": round(c.points, 1),
            "max_points": c.max_points,
            "capped": c.capped,
            "note": c.note,
            "contributions": [
                {
                    "signal_id": k.signal_id,
                    "label": k.label,
                    "prominence": str(k.prominence),
                    "weight": k.weight,
                    "points": round(k.points, 1),
                    "quote": k.quote,
                }
                for k in c.contributions
            ],
        }
        for c in result.components
    ]
    card["penalties"] = [
        {
            "signal_id": p.signal_id,
            "label": p.label,
            "prominence": str(p.prominence),
            "points": round(p.points, 1),
            "quote": p.quote,
        }
        for p in result.penalties
    ]
    card["penalty_total"] = round(result.penalty_total, 1)
    card["gates"] = [
        {
            "gate": g.gate,
            "result": str(g.result),
            "reason": g.reason,
            "blocker_id": g.blocker_id,
            "quote": g.quote,
            # The interface styles a FAIL differently from an UNRESOLVED, and
            # this flag saves it from re-deriving that from the string.
            "is_fail": g.result is GateResult.FAIL,
        }
        for g in result.gates
    ]
    card["confidence_items"] = [
        {
            "item_id": i.item_id,
            "label": i.label,
            "points": i.points,
            "awarded": i.awarded,
            "note": i.note,
        }
        for i in result.confidence_items
    ]
    card["signals"] = [
        {
            "signal_id": s.signal_id,
            "label": s.label,
            "prominence": str(s.prominence),
            "responsibility": str(s.responsibility) if s.responsibility else None,
            "hit_count": len(s.hits),
            "negated_count": len(s.negated_hits),
            "quotes": [h.quote for h in s.hits[:3]],
            # A negated hit is shown, not hidden: "the posting says there is no
            # cold calling" is information the person wants.
            "negated_quotes": [h.quote for h in s.negated_hits[:3]],
        }
        for s in result.signals
        if s.fired or s.negated_hits
    ]
    card["unknowns"] = list(result.unknowns)
    card["config_id"] = result.config_id
    card["config_version"] = result.config_version
    card["computed_at"] = result.computed_at
    return card


def utc_today() -> date:
    return datetime.now(UTC).date()
