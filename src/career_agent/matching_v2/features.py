"""The shared facts of one posting, made once for every profile (ADR-0037).

`extract` reads an advert with the existing readers (seniority, pay,
engagement, places) and the two new generic layers (language, sentence
context), and returns one payload per component, each stamped with the
versions that made it and the inputs it read. Nothing here reads a
preference: two profiles asking about the same posting get the same object.

`SharedReading` (in `match.engine`) is the in-memory half, the folded text the
gates still scan; `JobFeatures` is the storable half.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from career_agent.match.engine import JobFacts, SharedReading, read_shared
from career_agent.match.pay import read_pay
from career_agent.match.text import PRIMARY_SECTION, SECONDARY_SECTION, section_kind_at
from career_agent.matching_v2 import components as comp
from career_agent.matching_v2.context import Context, Sentence, sentences
from career_agent.matching_v2.language import LanguageTable, detect_language, load_table, tokens

#: The section headings of the shipped configurations (machinery: the starter
#: and the worked example carry the same lists). Passed in by the caller from
#: the configuration it holds; this is the fallback for a caller with none.
DEFAULT_HEADINGS: tuple[tuple[str, ...], tuple[str, ...]] = ((), ())


@dataclass(frozen=True)
class JobFeatures:
    job_id: str
    content_hash: str
    #: component -> payload (JSON-serialisable)
    payloads: dict[str, Any]
    #: component -> stamp
    stamps: dict[str, str]
    #: How many times `extract` read this posting to make it (1 when fresh).
    reads: int = 1
    shared: SharedReading | None = field(default=None, compare=False)


def job_inputs(job: JobFacts, content_hash: str, headings: tuple) -> dict[str, str]:
    """The raw inputs of a posting's components, as digests."""
    return {
        "job.text": comp.digest([content_hash, job.title]),
        "job.metadata": comp.digest(
            {
                "location_raw": job.location_raw,
                "board_work_type": job.workplace_type,
                "declared_hiring_scope": job.declared_hiring_scope,
                "stated_pay": [
                    job.salary_min,
                    job.salary_max,
                    job.salary_currency,
                    job.salary_period,
                ],
                "hours_type": job.employment_type,
                "declared": [job.declared_relationship, job.declared_contract_regime],
            }
        ),
        "machinery.headings": comp.digest([list(h) for h in headings]),
    }


def _tokens_payload(
    text: str, offset: int = 0, table: LanguageTable | None = None
) -> list[list[Any]]:
    return [
        [t.root, t.start + offset, t.end + offset, t.relation.value, t.word]
        for t in tokens(text, table)
    ]


def _with_machinery_sections(cut: list[Sentence], sections: tuple) -> list[Sentence]:
    """A sentence of no known purpose that sits under one of the configured
    section headings (the machinery the current reader marks PRIMARY or
    SECONDARY by) takes that section's purpose, so the two readers agree on
    what is a duty and what is a requirement. A purpose v2 already knew from
    its own headings (benefits, about us, ...) is kept."""
    out = []
    for s in cut:
        if s.context is Context.OTHER:
            kind = section_kind_at(sections, s.start)
            if kind == PRIMARY_SECTION:
                s = Sentence(s.start, s.end, Context.RESPONSIBILITY, s.heading)
            elif kind == SECONDARY_SECTION:
                s = Sentence(s.start, s.end, Context.REQUIREMENT, s.heading)
        out.append(s)
    return out


def extract(
    job: JobFacts,
    content_hash: str,
    headings: tuple[tuple[str, ...], tuple[str, ...]] = DEFAULT_HEADINGS,
    only: set[str] | None = None,
    table: LanguageTable | None = None,
) -> JobFeatures:
    """Read one posting into its shared components. `only` limits the work to
    the components a caller found stale; the others are not computed."""
    wanted = set(only) if only is not None else set(comp.JOB_COMPONENTS)
    shared = read_shared(job, list(headings[0]), list(headings[1]))
    payloads: dict[str, Any] = {}
    description = job.description
    if "text_parser" in wanted:
        payloads["text_parser"] = {
            "chars": len(description),
            "sections": [list(s) for s in shared.sections],
        }
    cut = _with_machinery_sections(sentences(description), shared.sections)
    if "language" in wanted:
        payloads["language"] = {"language": detect_language(description)}
    if "sentence_context" in wanted:
        payloads["sentence_context"] = [[s.start, s.end, s.context.value] for s in cut]
    if "work_facts" in wanted:
        table = table or load_table()
        payloads["work_facts"] = {
            "title": _tokens_payload(job.title, 0, table),
            "sentences": [
                [
                    s.start,
                    s.end,
                    s.context.value,
                    _tokens_payload(description[s.start : s.end], s.start, table),
                ]
                for s in cut
            ],
        }
    if "pay_facts" in wanted:
        text_pay = read_pay(description)
        payloads["pay_facts"] = {
            "structured": [job.salary_min, job.salary_max, job.salary_currency, job.salary_period],
            "text": None
            if text_pay is None
            else {
                "min": text_pay.min_value,
                "max": text_pay.max_value,
                "currency": text_pay.currency,
                "period": text_pay.period,
                "quote": text_pay.raw,
            },
        }
    if "location_facts" in wanted:
        place = shared.place
        payloads["location_facts"] = {
            "location_raw": job.location_raw,
            "board_work_type": job.workplace_type,
            "declared_hiring_scope": job.declared_hiring_scope,
            "countries": sorted(place.countries),
            "regions": sorted(place.regions),
            "statements": [[s.start, s.end] for s in cut if s.context is Context.LOCATION],
        }
    if "seniority_facts" in wanted:
        reading = shared.seniority
        payloads["seniority_facts"] = {
            "value": str(reading.value),
            "source": str(reading.source),
            "evidence": reading.evidence,
        }
    if "engagement_facts" in wanted:
        employment = shared.employment_stated
        payloads["engagement_facts"] = {
            "relationship": str(employment.relationship),
            "regime": str(employment.regime),
            "source": str(employment.source),
            "evidence": employment.evidence,
        }
    inputs = job_inputs(job, content_hash, headings)
    stamps = {name: comp.stamp(name, inputs) for name in payloads}
    return JobFeatures(
        job_id=str(job.job_id or ""),
        content_hash=content_hash,
        payloads=payloads,
        stamps=stamps,
        shared=shared,
    )
