"""The shared facts of one posting, made once for every profile (ADR-0037).

`extract` reads an advert with the existing readers (seniority, pay,
engagement, places, experience) and the two generic layers of v2 (language,
sentence context), and returns one fact per component. Nothing here reads a
preference: two profiles asking about the same posting get the same object.

WHAT IS STORED AND WHAT IS NOT (measured on 300 real postings, 2026-10-14):

* The canonical tokens are NOT stored. They were 91% of the first prototype
  (23.7 KB of a 26 KB row, five times the advert itself) and rebuilding them
  from the advert costs 2 ms. `work_units` makes them on demand and keeps
  them on this object for the profiles of the same pass.
* A quote is never copied when a span will do: pay and the U.S.-band
  sentence are spans of the canonical text (`job_raw.description_text`),
  which is already stored, and a span of it is a contiguous substring of it
  (ADR-0002) by construction.
* What is expensive to read again IS stored, compactly: the sentence
  contexts, the machinery sections, and the readings `match_job` takes from
  `SharedReading` (seniority, engagement, domestic context, experience, the
  U.S.-band sentence, the schedule facts). `shared_from_facts` rebuilds a
  `SharedReading` from them with the cheap folding only.
* What is cheap is recomputed: where the board says the posting is
  (`resolve_place`, 0.03 ms) and the structured salary (the posting's own
  metadata, in `job`).

Sentence contexts are a short string, one item per sentence: a context
letter, the gap since the previous sentence ended and the sentence length
(`R0.85 Q2.40`); sections are the same with the section kind. Readable with
`decode_sentences` and `decode_sections`, and small.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from career_agent.config.search_config import Negation
from career_agent.match.authorization import Requirement
from career_agent.match.engine import JobFacts, SharedReading, read_shared
from career_agent.match.gates import (
    EligibilityFacts,
    ScopeClause,
    negation_key,
    security_outcome,
)
from career_agent.match.pay import read_pay
from career_agent.match.places import ResolvedPlace, resolve_place
from career_agent.match.text import (
    PRIMARY_SECTION,
    SECONDARY_SECTION,
    Section,
    fold_field,
    section_kind_at,
    sentence_at,
)
from career_agent.matching_v2 import components as comp
from career_agent.matching_v2 import evidence as ev
from career_agent.matching_v2 import text_structure
from career_agent.matching_v2.context import Context, Sentence, sentences
from career_agent.matching_v2.language import LanguageTable, Token, detect_language, tokens
from career_agent.storage.mvp_repo import (
    _domestic_from_dict,
    _domestic_to_dict,
    _employment_from_dict,
    _employment_to_dict,
    _experience_from_dict,
    _experience_to_dict,
    _seniority_from_dict,
    _seniority_to_dict,
)

#: The section headings of the shipped configurations (machinery: the starter
#: and the worked example carry the same lists). Passed in by the caller from
#: the configuration it holds; this is the fallback for a caller with none.
DEFAULT_HEADINGS: tuple[tuple[str, ...], tuple[str, ...]] = ((), ())

_CONTEXT_CODE = {
    Context.RESPONSIBILITY: "R",
    Context.REQUIREMENT: "Q",
    Context.TOOL: "T",
    Context.CONDITION: "D",
    Context.COMPENSATION: "P",
    Context.LOCATION: "L",
    Context.BENEFIT: "B",
    Context.COMPANY_DESCRIPTION: "A",
    Context.OTHER: "O",
}
_CODE_CONTEXT = {v: k for k, v in _CONTEXT_CODE.items()}
_SECTION_CODE = {PRIMARY_SECTION: "P", SECONDARY_SECTION: "S", None: "N"}
_CODE_SECTION = {v: k for k, v in _SECTION_CODE.items()}


def encode_sentences(cut: list[Sentence]) -> str:
    out, last = [], 0
    for s in cut:
        out.append(f"{_CONTEXT_CODE[s.context]}{s.start - last}.{s.end - s.start}")
        last = s.end
    return " ".join(out)


def decode_sentences(text: str) -> list[Sentence]:
    out, last = [], 0
    for item in text.split():
        gap, length = item[1:].split(".")
        start = last + int(gap)
        out.append(Sentence(start, start + int(length), _CODE_CONTEXT[item[0]]))
        last = start + int(length)
    return out


def encode_sections(sections: tuple[Section, ...]) -> str:
    return " ".join(f"{_SECTION_CODE[k]}{s}.{e - s}" for s, e, k in sections)


def decode_sections(text: str) -> tuple[Section, ...]:
    out = []
    for item in text.split():
        start, length = item[1:].split(".")
        out.append((int(start), int(start) + int(length), _CODE_SECTION[item[0]]))
    return tuple(out)


@dataclass
class JobFeatures:
    job_id: str
    content_hash: str
    #: component -> stored fact (JSON-serialisable, compact); None for a
    #: component that is recomputed on demand (`work_facts`, `location_facts`).
    payloads: dict[str, Any]
    #: component -> stamp
    stamps: dict[str, str]
    #: How many times `extract` read this posting to make it (1 when fresh).
    reads: int = 1
    shared: SharedReading | None = field(default=None, compare=False)
    #: The canonical tokens, per unit, made on demand (never stored).
    units: list[tuple[str, Context, int, list[Token]]] | None = field(default=None, compare=False)
    #: The semantic notes by position, decoded on demand (never stored).
    note_index: ev.NoteIndex | None = field(default=None, compare=False)

    @property
    def sentences(self) -> list[Sentence]:
        return decode_sentences(self.payloads["sentence_context"])

    @property
    def notes(self) -> ev.NoteIndex:
        if self.note_index is None:
            self.note_index = ev.NoteIndex(
                ev.decode(self.payloads.get("semantic_evidence_facts") or "")
            )
        return self.note_index

    @property
    def language(self) -> str:
        return str(self.payloads.get("language") or "und")


def job_inputs(
    job: JobFacts, content_hash: str, headings: tuple, negation: str = ""
) -> dict[str, str]:
    """The raw inputs of a posting's components, as digests. `negation` is
    `gates.negation_key` of the negation lists the facts are read with."""
    return {
        "machinery.negation": negation,
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


def _span(text: str, quote: str | None) -> list[int] | None:
    if not quote:
        return None
    at = text.find(quote)
    return None if at < 0 else [at, at + len(quote)]


def extract(
    job: JobFacts,
    content_hash: str,
    headings: tuple[tuple[str, ...], tuple[str, ...]] = DEFAULT_HEADINGS,
    only: set[str] | None = None,
    table: LanguageTable | None = None,
    negation: Negation | None = None,
) -> JobFeatures:
    """Read one posting into its shared components. `only` limits the work to
    the components a caller found stale; the others are not computed.
    Without `negation` (machinery) the eligibility facts are not read here and
    each profile reads them for itself, as production does."""
    del table  # tokens are made on demand (`work_units`)
    wanted = set(only) if only is not None else set(comp.JOB_COMPONENTS)
    shared = read_shared(job, list(headings[0]), list(headings[1]), negation)
    payloads: dict[str, Any] = {}
    description = job.description
    if "text_parser" in wanted:
        payloads["text_parser"] = encode_sections(shared.sections)
    if "language" in wanted:
        payloads["language"] = detect_language(description)
    cuts = text_structure.cuts(description)
    if "text_structure" in wanted:
        payloads["text_structure"] = text_structure.encode(cuts)
    cut = None
    if "sentence_context" in wanted:
        cut = _with_machinery_sections(sentences(description, cuts), shared.sections)
        payloads["sentence_context"] = encode_sentences(cut)
    if "semantic_evidence_facts" in wanted:
        if cut is None:
            cut = _with_machinery_sections(sentences(description, cuts), shared.sections)
        language = payloads.get("language") or detect_language(description)
        payloads["semantic_evidence_facts"] = ev.encode(ev.annotate(description, cut, language))
    if "work_facts" in wanted:
        payloads["work_facts"] = None
    if "pay_facts" in wanted:
        text_pay = read_pay(description)
        payloads["pay_facts"] = {
            "text": None
            if text_pay is None
            else [
                text_pay.min_value,
                text_pay.max_value,
                text_pay.currency,
                text_pay.period,
                _span(description, text_pay.raw),
            ],
            "us_band": _span(description, shared.us_band),
        }
    if "location_facts" in wanted:
        payloads["location_facts"] = None
    if "seniority_facts" in wanted:
        payloads["seniority_facts"] = {
            "seniority": _seniority_to_dict(shared.seniority),
            "experience": _experience_to_dict(shared.experience),
        }
    if "engagement_facts" in wanted:
        payloads["engagement_facts"] = {
            "employment": _employment_to_dict(shared.employment_stated),
            "domestic": _domestic_to_dict(shared.domestic),
            "explicit": shared.explicit,
        }
    if "eligibility_facts" in wanted:
        payloads["eligibility_facts"] = (
            None if shared.eligibility is None else eligibility_to_dict(shared.eligibility)
        )
    key = "" if negation is None else negation_key(negation)
    inputs = job_inputs(job, content_hash, headings, key)
    stamps = {name: comp.stamp(name, inputs) for name in payloads}
    return JobFeatures(
        job_id=str(job.job_id or ""),
        content_hash=content_hash,
        payloads=payloads,
        stamps=stamps,
        shared=shared,
    )


def shared_from_facts(job: JobFacts, features: JobFeatures) -> SharedReading:
    """The `SharedReading` of a posting from its stored facts: the folding is
    redone (cheap, and the gates need it), the readings are not."""
    p = features.payloads
    sections = decode_sections(p["text_parser"])
    us = p["pay_facts"]["us_band"]
    return SharedReading(
        sections=sections,
        title_field=fold_field(job.title),
        description_field=fold_field(job.description, sections),
        seniority=_seniority_from_dict(p["seniority_facts"]["seniority"]),
        employment_stated=_employment_from_dict(p["engagement_facts"]["employment"]),
        domestic=_domestic_from_dict(p["engagement_facts"]["domestic"]),
        experience=_experience_from_dict(p["seniority_facts"]["experience"]),
        place=resolve_place(job.location_raw),
        us_band=None if us is None else job.description[us[0] : us[1]],
        explicit=p["engagement_facts"]["explicit"],
        eligibility=None
        if p.get("eligibility_facts") is None
        else eligibility_from_dict(p["eligibility_facts"], job.description),
    )


def eligibility_to_dict(f: EligibilityFacts) -> dict[str, Any]:
    """Compact: every statement is a span of the advert, never a copy."""
    return {
        "neg": f.negation,
        "clauses": [
            [
                c.family,
                c.start,
                c.end,
                c.stated,
                list(c.place.countries),
                list(c.place.regions),
                list(c.place.stated_regions),
                c.exhaustive,
                c.explicit,
            ]
            for c in f.clauses
        ],
        "attendance": None if f.attendance is None else list(f.attendance),
        "security": None if f.security is None else [f.security.char_start, f.security.char_end],
        "requirements": [
            [r.kind, r.country, r.region, r.char_start, r.char_end] for r in f.requirements
        ],
    }


def eligibility_from_dict(d: dict[str, Any], text: str) -> EligibilityFacts:
    security = d["security"]
    return EligibilityFacts(
        negation=d["neg"],
        clauses=tuple(
            ScopeClause(
                family, start, end, stated, ResolvedPlace(tuple(c), tuple(r), tuple(sr)), ex, expl
            )
            for family, start, end, stated, c, r, sr, ex, expl in d["clauses"]
        ),
        attendance=None if d["attendance"] is None else (d["attendance"][0], d["attendance"][1]),
        security=None
        if security is None
        else security_outcome(text[security[0] : security[1]], security[0]),
        requirements=tuple(
            Requirement(kind, country, sentence_at(text, start, end), start, end, region)
            for kind, country, region, start, end in d["requirements"]
        ),
    )


def work_units(
    job: JobFacts, features: JobFeatures, table: LanguageTable | None = None
) -> list[tuple[str, Context, int, list[Token]]]:
    """(field, context, sentence start, canonical tokens) for the title and
    every sentence, made from the advert once and kept on `features`."""
    if features.units is None:
        description = job.description
        units: list[tuple[str, Context, int, list[Token]]] = [
            ("title", Context.TITLE, 0, tokens(job.title, table))
        ]
        for s in features.sentences:
            toks = [
                Token(t.root, t.start + s.start, t.end + s.start, t.sentence, t.relation, t.word)
                for t in tokens(description[s.start : s.end], table)
            ]
            units.append(("description", s.context, s.start, toks))
        features.units = units
    return features.units


def set_language(job: JobFacts, features: JobFeatures, language: str) -> None:
    """State a posting's language (a one-sentence case is too short to tell)
    and re-read what depends on it: the semantic notes."""
    features.payloads["language"] = language
    features.payloads["semantic_evidence_facts"] = ev.encode(
        ev.annotate(job.description, features.sentences, language)
    )
    features.note_index = None
