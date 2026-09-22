"""Normalising provider metadata values that do not need a model.

The project rule is older than this milestone: **never use an LLM when
deterministic logic is sufficient.** Measuring the provider family made it
concrete. Its input is a median of 12 tokens, and answering it costs ~2,015
tokens of prompt and schema -- a ratio of about 168 to 1. Before paying that
18,551 times, it is worth asking which of the four dimensions actually needs
interpreting.

Three do not:

    employment_type_hint   FullTime / Full-Time / Full Time / Part Time /
                           Contract / Permanent -- a handful of spellings of a
                           closed set
    work_model_hint        Hybrid / Remote / remote / onsite / OnSite /
                           hybrid / unspecified -- likewise
    compensation_hint      the prompt asked the model to pass the value through
                           unchanged, which is an echo, not an interpretation

One does:

    hiring_location_hint   "Remote (EMEA)", "US or Canada, remote",
                           "San Francisco, CA", "Remote - United States" --
                           free text whose meaning a rule cannot safely fix,
                           and the highest-stakes dimension in the system

Restricting the model to that one dimension takes the provider family from
18,551 calls to 3,368 across the corpus: 81.9% fewer, and 30.6M fewer input
tokens at M4.5.

WHAT THIS MODULE MAY NOT DO
---------------------------
It never guesses. A value it does not recognise returns None and goes to the
model, or stays unresolved -- it does not fall through to a plausible default.
The point is to remove work a rule can do *correctly*, not to replace judgement
with a lookup table.

In particular it does not touch geography. `US` looks trivially mappable and
`San Francisco` looks obvious, but `Remote - United States` and
`US or Canada, remote` and `Multiple locations` are not, and a rule that gets
the easy ones right and the hard ones silently wrong is worse than no rule.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from career_agent.domain.enums import (
    EmploymentRelationship,
    LocalContractRegime,
    MetadataDimension,
    WorkModel,
)

#: Bumped when an alias table changes. Stored beside a normalised value so a
#: historical record shows which table produced it -- the raw provider value
#: stays immutable and authoritative either way, and an alias revision must
#: never rewrite what the provider originally supplied.
NORMALIZER_VERSION = 2

#: Dimensions this module resolves without a model. Anything not listed here
#: goes to the LLM, which is the safe direction for a new dimension.
DETERMINISTIC_DIMENSIONS: frozenset[MetadataDimension] = frozenset(
    {
        MetadataDimension.EMPLOYMENT_TYPE_HINT,
        MetadataDimension.WORK_MODEL_HINT,
        MetadataDimension.COMPENSATION_HINT,
    }
)

#: The one dimension that genuinely needs interpretation.
INTERPRETED_DIMENSIONS: frozenset[MetadataDimension] = frozenset(
    {MetadataDimension.HIRING_LOCATION_HINT}
)


def _key(value: str) -> str:
    """Fold spelling variants that are not meaning variants.

    `FullTime`, `Full-Time`, `Full Time` and `full time` are one value written
    four ways, and every one of them appears in the corpus.
    """
    return "".join(ch for ch in value.lower() if ch.isalnum())


#: Every employment-type spelling observed in the 18,551-job corpus, mapped to
#: the canonical form. Derived from measured values, not imagined ones -- a
#: table of plausible spellings nobody has seen is a table that will be wrong
#: about the ones they actually use.
_EMPLOYMENT_TYPES: dict[str, str] = {
    "fulltime": "FULL_TIME",
    "parttime": "PART_TIME",
    "contract": "CONTRACT",
    "contractor": "CONTRACT",
    "temporary": "TEMPORARY",
    "temp": "TEMPORARY",
    "intern": "INTERNSHIP",
    "internship": "INTERNSHIP",
    "permanent": "FULL_TIME",
    "regular": "FULL_TIME",
    "freelance": "CONTRACT",
    "volunteer": "VOLUNTEER",
    "apprenticeship": "INTERNSHIP",
    "seasonal": "TEMPORARY",
    # -- Gupy's closed list, added V1.7 --------------------------------------
    #
    # 40,595 postings in the corpus carried a declared contract type that
    # resolved to nothing, so the confidence item read "The job board does not
    # state what kind of engagement this is" above a posting where the employer
    # had picked one from a dropdown, and the employment-type facet offered no
    # bucket a Brazilian search could stand in.
    #
    # This is the COARSE vocabulary and it is lossy on purpose: `vacancy_legal_
    # entity` and `vacancy_type_freelancer` both land on CONTRACT here, and the
    # difference between them -- pessoa juridica under Brazilian law, versus a
    # freelancer -- is carried by `contract_regime` instead, which exists
    # precisely because one field could not hold both.
    #
    # `vacancy_type_apprentice` follows the `apprenticeship: INTERNSHIP` entry
    # already above it rather than inventing a seventh member. The precise
    # answer is `EmploymentRelationship.APPRENTICE`, which the engagement table
    # gives; this column is the filter chip.
    "vacancytypeeffective": "FULL_TIME",
    "vacancytypetrainee": "FULL_TIME",
    "vacancylegalentity": "CONTRACT",
    "vacancytypeautonomous": "CONTRACT",
    "vacancytypefreelancer": "CONTRACT",
    "vacancytypetemporary": "TEMPORARY",
    "vacancytypeinternship": "INTERNSHIP",
    "vacancytypeapprentice": "INTERNSHIP",
    # -- Jobgether's closed list, measured 2026-09-11 --------------------------
    # `contractType` is one of full-time, part-time, fixed-term, freelance,
    # internships; the response spells them `Full time`, `Fixed term`,
    # `Internships`. `fixedterm` is TEMPORARY because it names a term and not
    # a relationship; `internships` follows `internship`.
    "fixedterm": "TEMPORARY",
    "internships": "INTERNSHIP",
    # ABSENT, and each absence is the same decision the engagement table made:
    # `vacancy_type_talent_pool` is not a contract, `vacancy_type_outsource`
    # leaves open whose employee the worker is, and `vacancy_type_intermittent`
    # fits none of the six members without a decision this table may not make.
}

#: Work-model spellings, likewise measured. Note what is absent: nothing maps a
#: negative to a positive. A provider saying a role is *not* remote has not said
#: it is onsite, and inventing that distinction is exactly the kind of confident
#: wrong answer the whole design exists to prevent.
_WORK_MODELS: dict[str, WorkModel] = {
    "remote": WorkModel.REMOTE,
    "fullyremote": WorkModel.REMOTE,
    "hybrid": WorkModel.HYBRID,
    # Jobgether's `remote` field, measured 2026-09-11: `Full Remote`,
    # `Remote-first`, `Hybrid`. Both remote spellings say the role is done
    # remotely; neither says from where, which `location` answers separately.
    "fullremote": WorkModel.REMOTE,
    "remotefirst": WorkModel.REMOTE,
    "onsite": WorkModel.ONSITE,
    "inoffice": WorkModel.ONSITE,
    "office": WorkModel.ONSITE,
    "unspecified": WorkModel.UNCLEAR,
}


@dataclass(frozen=True, slots=True)
class Engagement:
    """How a declared contract-type value engages the worker, and under what.

    Two fields because they answer two questions and a Brazilian posting says
    both at once. `EmploymentRelationship` is portable -- an employee is an
    employee in Sao Paulo and in Munich. `LocalContractRegime` names a national
    statute and is therefore a much stronger claim, so it is `UNRESOLVED`
    unless the SPELLING ITSELF is that statute's vocabulary.
    """

    relationship: EmploymentRelationship
    regime: LocalContractRegime = LocalContractRegime.UNRESOLVED


#: What a declared contract-type spelling says about the engagement.
#:
#: Read the two halves of this table separately, because they are asserting
#: very different amounts.
#:
#: The GENERIC spellings -- `fulltime`, `contractor`, `intern` -- say only what
#: relationship the worker is in. Their regime stays `UNRESOLVED` on purpose: a
#: Greenhouse posting in Denver marked `FullTime` is an employee under Colorado
#: law and calling that CLT would be absurd, so the fact that the same word
#: means a CLT hire on a Brazilian board cannot be encoded here.
#:
#: The BRAZILIAN spellings are Gupy's own closed vocabulary, and every one of
#: them is a term of Brazilian labour law rather than a word that happens to be
#: Portuguese: `vacancy_legal_entity` is pessoa juridica, `vacancy_type_
#: apprentice` is the jovem aprendiz contract of CLT art. 428, `vacancy_type_
#: intermittent` is the contrato intermitente created by art. 443 in 2017.
#: When the spelling IS the statute, naming the statute is reading rather than
#: guessing.
#:
#: WHAT IS DELIBERATELY ABSENT, and each absence is a decision:
#:
#: * `vacancy_type_talent_pool` -- 1,158 rows in the corpus and NOT A CONTRACT
#:   AT ALL. It is a banco de talentos: a standing invitation to be considered
#:   later. It is collected like everything else, because deciding what a
#:   person wants to see belongs to the filter layer, and it is unmapped here
#:   because there is no engagement to describe.
#: * `vacancy_type_outsource` -- terceirizado. The worker is somebody's
#:   employee, and which employer is exactly what the word leaves open.
#: * `vacancy_type_associate`, `vacancy_type_parter` (the vendor's own
#:   spelling), `vacancy_type_lecturer` -- 254 rows between them and no
#:   engagement this vocabulary can state without inventing one.
_ENGAGEMENTS: dict[str, Engagement] = {
    # -- portable spellings, relationship only ----------------------------
    "fulltime": Engagement(EmploymentRelationship.EMPLOYEE),
    "parttime": Engagement(EmploymentRelationship.EMPLOYEE),
    "permanent": Engagement(EmploymentRelationship.EMPLOYEE),
    "regular": Engagement(EmploymentRelationship.EMPLOYEE),
    "contract": Engagement(EmploymentRelationship.CONTRACTOR_B2B),
    "contractor": Engagement(EmploymentRelationship.CONTRACTOR_B2B),
    "freelance": Engagement(EmploymentRelationship.CONTRACTOR_B2B),
    "temporary": Engagement(EmploymentRelationship.TEMPORARY),
    "temp": Engagement(EmploymentRelationship.TEMPORARY),
    "seasonal": Engagement(EmploymentRelationship.TEMPORARY),
    "intern": Engagement(EmploymentRelationship.INTERN),
    "internship": Engagement(EmploymentRelationship.INTERN),
    "apprenticeship": Engagement(EmploymentRelationship.APPRENTICE),
    "volunteer": Engagement(EmploymentRelationship.OTHER),
    # -- Brazilian statute vocabulary, relationship AND regime ------------
    #: Efetivo. The permanent hire, and in this vocabulary that is the CLT.
    #: 33,225 postings, which is what makes `contract_regime` stop being a
    #: column that has been NULL since the day it was added.
    "vacancytypeeffective": Engagement(EmploymentRelationship.EMPLOYEE, LocalContractRegime.CLT),
    #: Pessoa juridica. The candidate invoices through a company of their own.
    "vacancylegalentity": Engagement(EmploymentRelationship.CONTRACTOR_B2B, LocalContractRegime.PJ),
    #: Jovem aprendiz, CLT art. 428: registered em carteira like any employee.
    "vacancytypeapprentice": Engagement(EmploymentRelationship.APPRENTICE, LocalContractRegime.CLT),
    #: Contrato intermitente, CLT art. 443 s3. Also an employee.
    "vacancytypeintermittent": Engagement(EmploymentRelationship.EMPLOYEE, LocalContractRegime.CLT),
    #: Estagio. An INTERN is not a CLT employee -- Lei 11.788 says so in as
    #: many words -- so the relationship is stated and the regime is not.
    "vacancytypeinternship": Engagement(EmploymentRelationship.INTERN),
    #: Trabalho temporario runs under Lei 6.019, which is its own statute and
    #: not the CLT. Relationship yes, regime no.
    "vacancytypetemporary": Engagement(EmploymentRelationship.TEMPORARY),
    #: A trainee is an employee. Which statute is not said by the word.
    "vacancytypetrainee": Engagement(EmploymentRelationship.EMPLOYEE),
    #: Autonomo is a PERSON invoicing directly, which is precisely what PJ is
    #: not. Contractor, and no regime.
    "vacancytypeautonomous": Engagement(EmploymentRelationship.CONTRACTOR_B2B),
    "vacancytypefreelancer": Engagement(EmploymentRelationship.CONTRACTOR_B2B),
}


def normalise_engagement(raw: str) -> Engagement | None:
    """What a declared contract-type value says about the engagement, or None.

    None for a spelling nobody has catalogued, and that is the same refusal
    `normalise_employment_type` makes next door: a contract type this system
    does not understand must never become one it acts on.
    """
    return _ENGAGEMENTS.get(_key(raw))


def normalise_employment_type(raw: str) -> str | None:
    """`FullTime` -> `FULL_TIME`. None when the spelling is not one we have seen.

    Compound values such as `Full Time/Part Time` deliberately return None: the
    provider is describing a role that is either, and collapsing that to one
    would be a decision, not a normalisation.
    """
    return _EMPLOYMENT_TYPES.get(_key(raw))


def normalise_work_model(raw: str) -> WorkModel | None:
    """`OnSite` -> ONSITE. None when unrecognised.

    Boolean-looking values are handled by the caller, which knows the field
    name; `true` on a field named for remoteness means remote, and `false`
    means *not remote*, which is UNCLEAR rather than ONSITE.
    """
    return _WORK_MODELS.get(_key(raw))


def normalise_compensation(raw: str) -> str:
    """Pass the value through unchanged.

    Kept as a named function rather than an implicit no-op because the point is
    that this dimension was previously being sent to a model whose instruction
    was, in so many words, to return what it was given. Salary arithmetic is
    deterministic code's job at M3; storing the raw string faithfully is all M2
    owes it.
    """
    return raw


class Normalisation(StrEnum):
    """How a raw provider value became the value we store.

    Three states, not two, because a boolean produced a misleading metric the
    first time this was measured. `compensation_hint` reported a 0%
    "normalisation rate", which reads as a broken alias table -- and would
    invite someone to fix it by adding aliases. Nothing is broken: preserving
    the raw compensation string is the intended behaviour, and normalising it
    is M3's job, not M2's.

    A rate is only meaningful over values the system *tried* to normalise.
    """

    #: Matched an audited alias exactly. `FullTime` -> `FULL_TIME`.
    ALIAS = "ALIAS"
    #: Preserved verbatim on purpose. No normalisation was ever intended.
    PASSTHROUGH = "PASSTHROUGH"
    #: We tried and the alias table did not recognise it. Kept raw, and
    #: reported, because a growing unmapped set means the table is going stale.
    UNMAPPED = "UNMAPPED"


@dataclass(frozen=True)
class Resolved:
    """What code could work out about one provider value on its own.

    UNMAPPED is real and worth keeping visible. The corpus contains 595
    employment-type values across 16 spellings that are not employment types at
    all -- `Mid-Senior Level`, `Director`, `Executive`, `Remote` -- because
    employers put the wrong thing in the field. Recording them as-is is honest;
    mapping them onto the nearest enum member would manufacture a fact out of a
    data-entry mistake.
    """

    value: str
    kind: Normalisation

    @property
    def normalised(self) -> bool:
        return self.kind is Normalisation.ALIAS


def resolve_deterministically(dimension: MetadataDimension, raw: str) -> Resolved | None:
    """The single entry point. `None` means "a model has to read this".

    `None` never means "there is nothing here". A caller treating it as absence
    would turn an unrecognised value into silence, which is the failure the
    whole design exists to prevent.
    """
    if dimension is MetadataDimension.EMPLOYMENT_TYPE_HINT:
        mapped = normalise_employment_type(raw)
        if mapped:
            return Resolved(mapped, Normalisation.ALIAS)
        return Resolved(raw, Normalisation.UNMAPPED)
    if dimension is MetadataDimension.WORK_MODEL_HINT:
        model = normalise_work_model(raw)
        if model:
            return Resolved(model.value, Normalisation.ALIAS)
        return Resolved(raw, Normalisation.UNMAPPED)
    if dimension is MetadataDimension.COMPENSATION_HINT:
        return Resolved(normalise_compensation(raw), Normalisation.PASSTHROUGH)
    # HIRING_LOCATION_HINT, and anything added later, goes to the model. New
    # dimensions default to interpretation, which is the safe direction.
    return None


def needs_a_model(observations: Sequence[Any]) -> list[Any]:
    """Which observations still require interpretation.

    An empty result means this posting's provider family can be assembled with
    **zero** LLM calls. On the current corpus that never happens, because every
    posting carries a location hint -- but the code does not assume it, and a
    provider or corpus without one costs nothing.
    """
    return [
        o
        for o in observations
        if resolve_deterministically(MetadataDimension(o.dimension), o.source_value) is None
    ]
