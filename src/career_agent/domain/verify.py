"""Does the source actually say what a citation claims it says?

This module is why "EXPLICIT" is a promise rather than a adjective. A model can
produce a fluent, plausible, entirely invented quote; nothing about the text
itself reveals that. So every citation is checked against the archived source,
deterministically, with no LLM involved and no judgement applied.

THE QUESTION IS EXISTENCE, NOT RESEMBLANCE
------------------------------------------
Verification asks one thing: *can we prove the cited text is actually in the
source?* A similarity score cannot answer that, so no similarity score is
allowed to.

An earlier M0 revision permitted ``similarity >= 0.95`` as a third proof tier.
That was superseded during M2 implementation review, and the reason is concrete.
Two sentences differing only in ``not``, or in a country, a currency, an amount,
a frequency, or ``required`` versus ``preferred``, score far above 0.95 in any
reasonable surrounding context -- and those are exactly the tokens the later
gates depend on. A verifier that accepts them would confirm the opposite of what
a posting says, with a citation attached.

So a quote verifies only when it is a **contiguous substring**, either of the
raw text or of the text after deterministic typography canonicalisation.
Formatting is forgiven; facts never are.

A diagnostic ``NEAR_MATCH`` tier was built and then removed. It was meant to
tell "the model paraphrased a real sentence" apart from "the model invented
one", which is a genuinely useful distinction -- but making it work needed a
similarity threshold and forty lines of anchored windowing, and the threshold
turned out to be fiddly enough that a one-comma difference landed on either side
of it depending on quote length. That is too much machinery, in the one module
whose whole job is to be simple and unarguable, for a label that proves nothing.

The distinction is still worth measuring. It now belongs to the evaluation
harness, which reads stored raw output offline and where being approximate is
harmless, rather than to the code that decides what counts as proof.

**PROVIDER_FIELD** is structured, so there is nothing to be tolerant about. The
JSON path either resolves to that value or it does not. This is the pleasing
part of the design: provider metadata is *more* verifiable than prose, not less.

WHAT HAPPENS ON FAILURE
-----------------------
The citing field is downgraded from EXPLICIT to NOT_STATED -- it does not merely
lose its citation, it loses its status. "Explicit" means "the source says so and
here is where"; if we cannot find where, the claim was never explicit. A
hallucinated quote therefore cannot open a gate downstream. At worst it becomes
an open question, which is the safe direction.

Every downgrade is counted, because a rising verification-failure rate is the
earliest signal that a prompt or a model has drifted.
"""

import unicodedata
from dataclasses import dataclass
from typing import Any

from career_agent.domain.enums import (
    VERIFYING_MATCH_KINDS,
    EvidenceSourceKind,
    MatchKind,
)
from career_agent.domain.paths import resolve_path, serialise_value


@dataclass(frozen=True)
class VerificationResult:
    """What happened when one citation was checked.

    ``char_start`` and ``char_end`` are offsets into the *normalised* haystack,
    not the raw text, and are recorded for auditing rather than for slicing the
    original. A NORMALISED match by definition does not correspond to an
    identical span of raw characters.

    ``verified`` is derived from ``match_kind`` rather than passed in, so there
    is no way to construct a result that claims proof from a kind that does not
    provide it.
    """

    evidence_id: str
    match_kind: MatchKind
    match_score: float = 0.0
    char_start: int | None = None
    char_end: int | None = None
    detail: str = ""

    @property
    def verified(self) -> bool:
        return self.match_kind in VERIFYING_MATCH_KINDS


#: Characters that differ between what a posting renders and what a model
#: transcribes, without either being wrong. Normalising these is forgiveness for
#: typography, not for content.
_TRANSLATIONS = str.maketrans(
    {
        "‘": "'",
        "’": "'",
        "‚": "'",
        "‛": "'",
        "“": '"',
        "”": '"',
        "„": '"',
        "′": "'",
        "″": '"',
        "–": "-",  # punctuation-check: allow: the character normalised here
        "—": "-",  # punctuation-check: allow: the character normalised here
        "−": "-",
        " ": " ",
        " ": " ",
        " ": " ",
        " ": " ",
        "﻿": "",
        "​": "",
    }
)


def normalise(text: str) -> str:
    """Canonicalise typography. **This is the complete transformation set.**

    Exactly five operations, applied in this order, and nothing else:

    1. **Unicode NFKC** -- folds ligatures, full-width forms and compatibility
       characters onto their ordinary equivalents.
    2. **Quote, apostrophe and dash equivalents** -- curly quotes to straight,
       the several Unicode apostrophes to ``'``, en/em/minus dashes to ``-``.
    3. **Space equivalents** -- non-breaking, narrow, thin and zero-width
       spaces to an ordinary space; byte-order marks removed.
    4. **Whitespace collapse** -- any run of whitespace, including line breaks
       and tabs, becomes a single space; leading and trailing space is dropped.
    5. **Case folding** -- because Ashby uppercases headings in one of its two
       description formats, so "REMOTE" in a heading and "Remote" in a sentence
       are the same claim rendered differently. Met for real at M1C.

    What it deliberately does **not** do, because each would let a factual
    difference pass as a formatting one: remove words, reorder tokens,
    substitute synonyms, drop negation, alter numbers, alter currencies, alter
    country names, or apply edit distance.

    After this, verification is still an exact contiguous substring test.
    """
    folded = unicodedata.normalize("NFKC", text).translate(_TRANSLATIONS)
    return " ".join(folded.split()).casefold()


def verify_quote(quote: str, description_text: str, evidence_id: str = "") -> VerificationResult:
    """Locate a JOB_DESCRIPTION citation in the archived posting text.

    Two proof tiers, and only two:

        1. contiguous substring of the raw text                  -> EXACT
        2. contiguous substring after typography canonicalisation -> NORMALISED

    Anything else is NOT_FOUND. There is no third tier and no similarity
    scoring: "German is required" and "German is not required" differ by one
    token and score above 0.95 in context, and the second is the one that would
    end a candidacy.
    """
    if not quote.strip():
        return VerificationResult(
            evidence_id, MatchKind.NOT_FOUND, detail="empty quote cites nothing"
        )

    offset = description_text.find(quote)
    if offset != -1:
        return VerificationResult(evidence_id, MatchKind.EXACT, 1.0, offset, offset + len(quote))

    needle, haystack = normalise(quote), normalise(description_text)
    offset = haystack.find(needle)
    if offset != -1:
        return VerificationResult(
            evidence_id, MatchKind.NORMALISED, 1.0, offset, offset + len(needle)
        )

    return VerificationResult(
        evidence_id,
        MatchKind.NOT_FOUND,
        detail=(
            "the posting does not contain this text. It may be close to something the "
            "posting does say -- that is still not proof, because one differing token "
            "can be 'not', a country, or an amount."
        ),
    )


def verify_provider_citation(
    source_field: str,
    source_value: str | None,
    payload: dict[str, Any],
    evidence_id: str = "",
) -> VerificationResult:
    """Resolve a PROVIDER_FIELD citation against the archived payload.

    No tolerance tiers and no fuzzy matching: either the path resolves to that
    value or the citation is false. Comparison is byte-exact against
    `serialise_value`, the same function that produced the value at collection
    time -- which is why both sides now share one implementation in
    `domain/paths.py` rather than each keeping their own.
    """
    actual = serialise_value(resolve_path(payload, source_field))
    if actual is None:
        return VerificationResult(
            evidence_id,
            MatchKind.NOT_FOUND,
            detail=(f"path {source_field!r} does not resolve to a value in the archived payload"),
        )

    if actual == (source_value or ""):
        return VerificationResult(evidence_id, MatchKind.FIELD_MATCH, 1.0)

    return VerificationResult(
        evidence_id,
        MatchKind.NOT_FOUND,
        detail=(
            f"path {source_field!r} holds {actual[:80]!r}, not the cited "
            f"{(source_value or '')[:80]!r}"
        ),
    )


@dataclass(frozen=True)
class VerificationReport:
    """The outcome of checking every citation on one fingerprint."""

    results: tuple[VerificationResult, ...]

    @property
    def verified_count(self) -> int:
        return sum(1 for r in self.results if r.verified)

    @property
    def failed(self) -> tuple[VerificationResult, ...]:
        return tuple(r for r in self.results if not r.verified)

    @property
    def rate(self) -> float:
        """Share of citations that resolved. The prompt-drift canary.

        An empty fingerprint verifies vacuously at 1.0. That is deliberate:
        "cited nothing and nothing failed" is not a verification problem, it is
        a NOT_STATED-rate problem, and the two metrics bracket the behaviour
        from opposite sides.
        """
        return 1.0 if not self.results else self.verified_count / len(self.results)


def verify_all(
    evidence: list[Any],
    description_text: str,
    payload: dict[str, Any] | None = None,
) -> VerificationReport:
    """Check every citation against the source its kind names.

    Takes anything with `id`, `source_kind`, `quote`, `source_field` and
    `source_value` attributes, so it works on both the durable `Evidence` model
    and a transport row without the domain layer importing either.
    """
    results: list[VerificationResult] = []
    for item in evidence:
        if item.source_kind is EvidenceSourceKind.JOB_DESCRIPTION:
            results.append(verify_quote(item.quote or "", description_text, item.id))
        elif item.source_kind is EvidenceSourceKind.PROVIDER_FIELD:
            results.append(
                verify_provider_citation(
                    item.source_field or "", item.source_value, payload or {}, item.id
                )
            )
        else:
            results.append(
                VerificationResult(
                    item.id,
                    MatchKind.NOT_FOUND,
                    detail=f"{item.source_kind} is not a verifiable M2 source",
                )
            )
    return VerificationReport(tuple(results))
