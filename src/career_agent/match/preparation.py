"""What this posting asks for, beside what you have evidence of.

The bridge between "this matches" and "is it worth applying". A score says how
well a posting fits; it does not say which of its requirements you could speak
to in an interview, and that is the question somebody actually has at the
moment of deciding.

**No requirement disappears because the candidate lacks it.** A view that
listed only the matches would be a view that flatters, and the gaps are the
half worth reading -- they are what to prepare, what to be honest about, and
sometimes the reason not to apply. `GAP` is a first-class outcome here.

**Nothing is claimed that no quote supports, on either side.** A requirement is
a signal the posting FIRED, carrying the employer's own sentence. Evidence is a
`VerifiedClaim` the candidate personally confirmed, carrying the line from her
CV. Recognition uses bounded, normalized phrases and a small tested set of
evidence-only paraphrases. Both the evidence quote and the recognized phrase
remain available for checking. No semantic or fuzzy similarity is used.

**This is not a recommendation.** It reports; the person decides. There is no
"you should apply" here and no aggregate readiness number, because a single
figure over requirements would be a fourth measurement and ADR-0004 keeps
three.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from career_agent.config.search_config import SearchConfig
from career_agent.domain.claims import VerifiedClaim
from career_agent.domain.matching import MatchResult
from career_agent.match.lexicon import (
    compile_patterns,
    context_allows,
    context_rule_for,
    is_negated_before,
)
from career_agent.match.text import fold, fold_field, sentence_at

# Evidence-only paraphrases. These do not change posting extraction or scores.
_INTERNAL_APPS = (
    "internal app",
    "internal apps",
    "internal application",
    "internal applications",
    "aplicativo interno",
    "aplicativos internos",
    "aplicação interna",
    "aplicações internas",
)
_DELIVERED = compile_patterns(
    (
        "built",
        "created",
        "developed",
        "implemented",
        "maintained",
        "designed",
        "shipped",
        "delivered",
        "deployed",
        "build",
        "develop",
        "maintain",
        "criei",
        "desenvolvi",
        "implementei",
        "construí",
        "entreguei",
        "implantei",
        "desenvolvimento",
        "criação",
        "manutenção",
        "construção",
    )
)
_OTHER_ACTOR = re.compile(
    r"\b(?:customers?|users?|another team|supplied by|provided by|"
    r"outra equipe|outro time|fornecido por|clientes?|usuarios?)\b"
)
_TOOL_USE = re.compile(r"\b(?:using|used|via|through|with|usando|utilizando|usei|com|por meio)\b")


def _evidence_delivery(quote: str, matched: str, config: SearchConfig | None) -> bool:
    text = fold(quote)[0]
    prefix = text[: text.find(fold(matched)[0])]
    field = fold_field(prefix)
    for action in _DELIVERED.matches_in(field):
        if len(prefix) - action.end() > 180:
            continue
        if _TOOL_USE.search(prefix[action.end() :]):
            continue
        if _OTHER_ACTOR.search(prefix[max(0, action.start() - 45) :]):
            continue
        if config and is_negated_before(field, action.start(), config.negation):
            continue
        return True
    return False


class Readiness(StrEnum):
    """How well one requirement is answered by confirmed evidence.

    `UNRESOLVED` is not a weaker `GAP`. A gap means the posting asked and the
    system recognized no confirmed support; unresolved means it cannot compare,
    which happens when a requirement fired on a signal that has no phrases to
    compare a claim against. Reporting the second as the first would invent a
    shortcoming.
    """

    MATCHED = "MATCHED"
    PARTIAL = "PARTIAL"
    GAP = "GAP"
    UNRESOLVED = "UNRESOLVED"


@dataclass(frozen=True, slots=True)
class Requirement:
    """One thing the posting asks for, and what answers it."""

    signal_id: str
    label: str
    readiness: Readiness
    #: The employer's own sentence, from the matcher.
    posting_quote: str | None = None
    #: The candidate's own line, from a claim she confirmed. `None` for a gap,
    #: because there is nothing to quote and inventing something would be the
    #: whole problem.
    evidence_text: str | None = None
    evidence_key: str | None = None
    #: Which configured phrase connected the two. Shown so a reader can check
    #: the match rather than trust it.
    matched_on: str | None = None


@dataclass(frozen=True, slots=True)
class Concern:
    """Something to settle before applying, that is not about skill.

    `detail` is the sentence, in English, and it is what the terminal prints.
    `code` and `params` are the same sentence taken apart, so an interface can
    say it in the reader's language without either side re-deciding what it
    means -- the alternative was a screen that translated its own headings and
    then printed "The posting states this is EOR." in English underneath them.

    A concern with NO code is one whose words came from the configuration or
    from a gate's own reason, and those are not ours to translate: a phrase
    group the person wrote is the product working, and a gate's reason quotes
    the blocker she named.
    """

    kind: str
    detail: str
    quote: str | None = None
    #: Which of our own sentences this is, when it is one of ours.
    code: str | None = None
    #: What fills its holes. Values are enum names and signal ids, never prose.
    params: dict[str, str] | None = None


@dataclass(frozen=True, slots=True)
class Preparation:
    """One posting, prepared for. Reports; recommends nothing."""

    requirements: tuple[Requirement, ...]
    concerns: tuple[Concern, ...]

    def of(self, readiness: Readiness) -> tuple[Requirement, ...]:
        return tuple(r for r in self.requirements if r.readiness is readiness)

    @property
    def counts(self) -> dict[str, int]:
        return {
            readiness.value: len(self.of(readiness))
            for readiness in Readiness
            if self.of(readiness)
        }


def _phrases(config: SearchConfig, signal_id: str) -> tuple[str, ...]:
    """The configured phrases for one signal, lower-cased.

    Read from the SAME configuration the matcher used, so a requirement and the
    evidence answering it are compared on the employer's terms rather than on a
    second vocabulary invented here.
    """
    signal = config.lexicon.get(signal_id)
    patterns = getattr(signal, "patterns", ()) if signal is not None else ()
    return tuple(str(pattern).lower() for pattern in patterns)


def _answering(
    phrases: tuple[str, ...],
    claims: list[VerifiedClaim],
    *,
    config: SearchConfig | None = None,
    signal_id: str = "",
) -> tuple[VerifiedClaim, str] | None:
    """The first confirmed claim containing one of these phrases.

    Only `verified` claims are consulted. A proposal read off a CV and never
    confirmed is a draft, and letting one answer a requirement would make the
    whole review step decorative.
    """
    patterns = compile_patterns(phrases)
    aliases = compile_patterns(
        _INTERNAL_APPS if signal_id == "internal_tooling" and phrases else ()
    )
    for claim in claims:
        if not claim.verified:
            continue
        # Separate fields cannot accidentally manufacture a phrase at their join.
        for text in [claim.text, *claim.tools]:
            field = fold_field(text)
            for vocabulary, guarded in ((patterns, False), (aliases, True)):
                for hit in vocabulary.matches_in(field):
                    if config and is_negated_before(field, hit.start(), config.negation):
                        continue
                    phrase = vocabulary.configured(hit.group(0))
                    signal = config.lexicon.get(signal_id) if config else None
                    rule = context_rule_for(signal, phrase) if signal else None
                    sentence = sentence_at(
                        text, field.offsets[hit.start()], field.offsets[hit.end() - 1] + 1
                    )
                    if (guarded or rule == "systems_delivery") and not _evidence_delivery(
                        sentence, phrase, config
                    ):
                        continue
                    if (
                        rule
                        and rule != "systems_delivery"
                        and not context_allows(rule, sentence, phrase)
                    ):
                        continue
                    return claim, phrase
    return None


def _requirement(
    config: SearchConfig, signal_id: str, label: str, quote: str | None, claims: list[VerifiedClaim]
) -> Requirement:
    phrases = _phrases(config, signal_id)
    if not phrases:
        # Nothing to compare against. Honest, and rare: it means the signal
        # fired on something with no phrase list, so this system cannot say
        # whether the candidate answers it.
        return Requirement(
            signal_id=signal_id,
            label=label,
            readiness=Readiness.UNRESOLVED,
            posting_quote=quote,
        )

    found = _answering(phrases, claims, config=config, signal_id=signal_id)
    if found is None:
        return Requirement(
            signal_id=signal_id, label=label, readiness=Readiness.GAP, posting_quote=quote
        )

    claim, phrase = found
    # PARTIAL when the evidence is a TOOL or SKILL naming the thing, MATCHED
    # when it is employment or a project -- having used a tool and having done
    # the work are different answers to the same question, and collapsing them
    # would overstate the weaker one.
    readiness = (
        Readiness.MATCHED
        if claim.claim_type.value in {"EMPLOYMENT", "PROJECT", "ACHIEVEMENT"}
        else Readiness.PARTIAL
    )
    return Requirement(
        signal_id=signal_id,
        label=label,
        readiness=readiness,
        posting_quote=quote,
        evidence_text=claim.text,
        evidence_key=claim.claim_key,
        matched_on=phrase,
    )


def transferable_signals(config: SearchConfig, claims: list[VerifiedClaim]) -> tuple[str, ...]:
    """Every configured signal the candidate has CONFIRMED evidence for.

    The same comparison `_requirement` makes, asked of the whole lexicon
    instead of one posting: for each signal, do any of its configured phrases
    appear in the text or the tools of a confirmed claim.

    WHY IT LIVES HERE
    -----------------
    This module and `resume.py` are the only two in `career_agent.match`
    allowed to read a `VerifiedClaim`, and `tests/integration/test_evidence_
    reach.py` fails if a third learns to. Putting this beside the function it
    reuses keeps that boundary exactly where it is: one comparison, one place,
    and no scoring module gains a reason to import a claim.

    WHAT IT IS FOR, AND WHAT IT IS NOT
    -----------------------------------
    It is for ONE control -- "show roles I could transition into" -- which is
    off by default. A person changing profession is blocked by
    `screening.required_any_groups` on every posting in the profession they
    are moving TO, because the search describes where they have BEEN. This is
    what lets them say "I have done these things, show me the work that asks
    for them" without editing their search or inventing a job title.

    **It invents nothing.** A signal appears here only because a phrase the
    CONFIGURATION defines was found in a line the CANDIDATE confirmed. An
    unconfirmed proposal read off a CV is a draft and is skipped, exactly as
    `_answering` skips it -- otherwise the review step would be decorative.

    **It scores nothing.** The result reaches one SQL clause whose only effect
    is to stop hiding a posting. No number moves, and the default list never
    carries it, so "confirming a fact moves no recommendation" stays true.
    """
    confirmed = [claim for claim in claims if claim.verified]
    if not confirmed:
        return ()
    found = [
        signal_id
        for signal_id in config.lexicon
        if _answering(_phrases(config, signal_id), confirmed, config=config, signal_id=signal_id)
        is not None
    ]
    # Sorted so the same evidence produces the same query string, which is what
    # makes the response cacheable by query in the browser store.
    return tuple(sorted(found))


def _concerns(result: MatchResult) -> tuple[Concern, ...]:
    """Everything to settle that is not about capability.

    Each is already decided elsewhere and only gathered here. None of them is
    restated more strongly than the module that produced it: a domestic reading
    stays a likelihood, an unresolved gate stays unresolved.
    """
    found: list[Concern] = []

    for gate in result.gates:
        if gate.result.value == "FAIL":
            found.append(
                Concern(kind="eligibility", detail=gate.reason or gate.gate, quote=gate.quote)
            )
        elif gate.gate == "geography" and gate.result.value == "UNRESOLVED":
            found.append(
                Concern(
                    kind="eligibility",
                    detail="The posting does not state where it hires. Worth asking first.",
                    code="geography_unresolved",
                )
            )

    domestic = result.domestic
    if domestic.context.value == "LIKELY_US_DOMESTIC":
        found.append(
            Concern(
                kind="employment",
                detail=(
                    f"Probably United States employment: it offers {domestic.signal} "
                    "and does not mention hiring elsewhere. Not a refusal."
                ),
                quote=domestic.evidence,
                code="likely_us_domestic",
                params={"signal": str(domestic.signal)},
            )
        )

    employment = result.employment
    if employment.relationship.value != "UNRESOLVED":
        how = "states" if employment.is_explicit else "suggests"
        found.append(
            Concern(
                kind="contract",
                detail=f"The posting {how} this is {employment.relationship.value}.",
                quote=employment.evidence,
                # EXPLICIT and inferred are two different claims and the code
                # carries the difference, so a reader is never shown a
                # benefit-derived guess in the words of a stated fact.
                code=("contract_explicit" if employment.is_explicit else "contract_suggested"),
                params={"relationship": employment.relationship.value},
            )
        )

    if not result.seniority.is_evidence:
        found.append(
            Concern(
                kind="seniority",
                detail="The posting does not state a level. Worth asking what they mean.",
                code="seniority_unstated",
            )
        )

    if result.screening_state.value == "BLOCKED":
        found.append(
            Concern(
                kind="fit",
                detail=result.screening_reason or "This is a different kind of work.",
            )
        )

    return tuple(found)


def prepare(config: SearchConfig, result: MatchResult, claims: list[VerifiedClaim]) -> Preparation:
    """One posting's requirements, against confirmed evidence.

    Requirements are the signals that FIRED, which is what the posting actually
    asked for. A signal observed and not fired is not a requirement, and listing
    every signal in the configuration would bury the handful that matter.
    """
    requirements = tuple(
        _requirement(
            config,
            signal.signal_id,
            signal.label,
            next((hit.quote for hit in signal.hits), None),
            claims,
        )
        for signal in result.signals
        if signal.fired
    )
    return Preparation(requirements=requirements, concerns=_concerns(result))
