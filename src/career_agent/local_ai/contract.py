"""The compact enrichment document, and the verification that is its point.

WHY THIS IS SMALL
-----------------
The hosted `JobFingerprint` carries 33 dimensions, each with a status, a
vocabulary and a citation. That document is the product of a frontier model
given eight thousand tokens of instruction, and even then Stage 0 Take 3 landed
at 18 assembled fingerprints of 27.

A 4B model running locally is not that model, and asking it the same question
produces confident nonsense rather than an error. So it is asked a much smaller
question: summarise, list what the posting names, and flag what looks like a
problem -- every claim attached to a sentence the posting actually contains.

THE ENRICHMENT NEVER CARRIES A SCORE
------------------------------------
There is no numeric field here, no percentage, and no "match". If a future
change feels like it wants one, that is precisely the boundary this contract
exists to hold: the model observes, deterministic Python decides (ADR-0001).
``recommended_action`` is a **reading suggestion** for a human triaging a queue
-- "this one is worth opening" -- and is never a ranking, never comparable
between postings, and never an input to eligibility.

VERIFICATION IS THE MODULE
--------------------------
Everything above is why `verify()` exists. A local model hallucinates quotes
more freely than a hosted one, and nothing about a fabricated sentence looks
wrong. So every quote is checked as a contiguous substring of the source, EXACT
or after the repo's one typography canonicalisation, using the same
`domain.verify.verify_quote` the hosted pipeline uses -- because a second
implementation of "is this proof" would be a second definition of proof.

An item whose quote does not verify is **dropped**, not repaired and not
downgraded, and the drop is recorded with its reason. A drop rate is a prompt
drift signal; a silently shortened list is not.
"""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from career_agent.domain.verify import verify_quote

#: Bumped when the shape of the document changes. It is part of the cache
#: envelope, so an old entry is discarded rather than deserialised into a model
#: that no longer means the same thing.
LOCAL_ENRICHMENT_SCHEMA_VERSION = 1

MAX_SUMMARY_CHARS = 400
MAX_TECHNOLOGIES = 12
MAX_STRENGTHS = 6
MAX_GAPS = 6
MAX_RISK_FLAGS = 6

#: Generous on purpose. These bounds exist to stop a runaway generation, not to
#: police wording: a real item that overshoots would fail validation and take
#: the whole response with it, whereas an unverifiable one is merely dropped.
MAX_ITEM_TEXT_CHARS = 300
MAX_ITEM_QUOTE_CHARS = 600

RecommendedAction = Literal["READ_IN_FULL", "SKIM", "DEPRIORITISE"]
Confidence = Literal["HIGH", "MEDIUM", "LOW"]

#: The fields whose entries must each be backed by a quote. Named once, because
#: `verify`, `json_schema` and the acceptability arithmetic must agree.
EVIDENCED_FIELDS: tuple[str, ...] = ("technologies", "strengths", "gaps", "risk_flags")


class EvidencedItem(BaseModel):
    """One observation and the sentence that proves the posting made it."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    text: str = Field(min_length=1, max_length=MAX_ITEM_TEXT_CHARS)
    quote: str = Field(min_length=1, max_length=MAX_ITEM_QUOTE_CHARS)


class LocalEnrichment(BaseModel):
    """Exactly what the local model is allowed to return. Unverified.

    This is the raw answer. Nothing downstream should accept it directly --
    `verify()` turns it into a `VerifiedEnrichment`, and only that has been
    checked against the posting.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    #: May be empty. Emptiness is a real observation about the answer, and
    #: `VerifiedEnrichment.is_acceptable` is where it is judged -- rejecting it
    #: here would turn a bad answer into a parse error and lose the diagnosis.
    summary: str = Field(default="", max_length=MAX_SUMMARY_CHARS)
    technologies: list[EvidencedItem] = Field(default_factory=list, max_length=MAX_TECHNOLOGIES)
    strengths: list[EvidencedItem] = Field(default_factory=list, max_length=MAX_STRENGTHS)
    gaps: list[EvidencedItem] = Field(default_factory=list, max_length=MAX_GAPS)
    risk_flags: list[EvidencedItem] = Field(default_factory=list, max_length=MAX_RISK_FLAGS)
    recommended_action: RecommendedAction = "READ_IN_FULL"
    confidence: Confidence = "LOW"


class RejectedItem(BaseModel):
    """An item that claimed a sentence the posting does not contain."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    field: str
    text: str
    quote: str
    reason: str


class VerifiedEnrichment(BaseModel):
    """The enrichment after every quote was checked. The only accepted form.

    Carries its rejections rather than discarding them: "the model produced
    eleven items and eight of them cited sentences that do not exist" is the
    single most useful thing to know about a local model, and it is invisible
    if the drops are silent.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: int = LOCAL_ENRICHMENT_SCHEMA_VERSION
    summary: str = ""
    technologies: list[EvidencedItem] = Field(default_factory=list)
    strengths: list[EvidencedItem] = Field(default_factory=list)
    gaps: list[EvidencedItem] = Field(default_factory=list)
    risk_flags: list[EvidencedItem] = Field(default_factory=list)
    recommended_action: RecommendedAction = "READ_IN_FULL"
    confidence: Confidence = "LOW"
    rejected: list[RejectedItem] = Field(default_factory=list)

    @property
    def verified_count(self) -> int:
        return sum(len(getattr(self, name)) for name in EVIDENCED_FIELDS)

    @property
    def rejected_count(self) -> int:
        return len(self.rejected)

    @property
    def is_acceptable(self) -> bool:
        """Whether this answer may be shown to a human or written to the cache.

        Three refusals, and each names a distinct failure:

        * **no summary** -- the one field that is always answerable was not
          answered, so the generation did not really happen;
        * **more than half the items dropped** -- the model is inventing
          sentences at a rate that makes the surviving ones untrustworthy too;
        * **nothing verified although items were returned** -- the strongest
          form of the same signal.

        An answer with a summary and no items at all is acceptable: "this
        posting states nothing worth listing" is a legitimate observation, and
        distinguishing it from a fabrication is what the first two rules do.
        """
        if not self.summary.strip():
            return False
        total = self.verified_count + self.rejected_count
        if total == 0:
            return True
        if self.rejected_count * 2 > total:
            return False
        return self.verified_count > 0


def verify(enrichment: LocalEnrichment, source_text: str) -> VerifiedEnrichment:
    """Drop every item whose quote is not in `source_text`, and say why.

    `source_text` must be the archived posting text -- the same bytes the
    prompt was built from. Verifying against anything else proves nothing.
    """
    kept: dict[str, list[EvidencedItem]] = {}
    rejected: list[RejectedItem] = []

    for name in EVIDENCED_FIELDS:
        survivors: list[EvidencedItem] = []
        for item in getattr(enrichment, name):
            result = verify_quote(item.quote, source_text)
            if result.verified:
                survivors.append(item)
            else:
                rejected.append(
                    RejectedItem(
                        field=name,
                        text=item.text,
                        quote=item.quote,
                        reason=result.detail or "quote is not a contiguous substring of the source",
                    )
                )
        kept[name] = survivors

    # Named one by one rather than splatted: the four field names are already a
    # constant that `EVIDENCED_FIELDS` and `json_schema` agree on, and a keyword
    # splat here would hide a typo from the type checker until runtime.
    return VerifiedEnrichment(
        summary=enrichment.summary,
        technologies=kept["technologies"],
        strengths=kept["strengths"],
        gaps=kept["gaps"],
        risk_flags=kept["risk_flags"],
        recommended_action=enrichment.recommended_action,
        confidence=enrichment.confidence,
        rejected=rejected,
    )


#: The item schema, inlined rather than referenced.
#:
#: `LocalEnrichment.model_json_schema()` emits `$defs` plus four `$ref`s to
#: them. Ollama's `format` parameter is compiled to a GBNF grammar, and `$ref`
#: indirection is the part of JSON Schema that support for reliably thins out
#: first. A hand-written flat schema is forty lines and cannot surprise us; a
#: generated one is one line and can.
_ITEM_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "text": {"type": "string", "maxLength": MAX_ITEM_TEXT_CHARS},
        "quote": {"type": "string", "maxLength": MAX_ITEM_QUOTE_CHARS},
    },
    "required": ["text", "quote"],
}


def _item_list(max_items: int) -> dict[str, Any]:
    return {"type": "array", "maxItems": max_items, "items": dict(_ITEM_SCHEMA)}


def json_schema() -> dict[str, Any]:
    """The JSON Schema to hand to Ollama's `format` parameter.

    Returned freshly built each call: it is passed into a request body that
    callers are free to mutate, and a shared mutable default would make one
    request able to corrupt the next.
    """
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "summary": {"type": "string", "maxLength": MAX_SUMMARY_CHARS},
            "technologies": _item_list(MAX_TECHNOLOGIES),
            "strengths": _item_list(MAX_STRENGTHS),
            "gaps": _item_list(MAX_GAPS),
            "risk_flags": _item_list(MAX_RISK_FLAGS),
            "recommended_action": {
                "type": "string",
                "enum": ["READ_IN_FULL", "SKIM", "DEPRIORITISE"],
            },
            "confidence": {"type": "string", "enum": ["HIGH", "MEDIUM", "LOW"]},
        },
        "required": [
            "summary",
            "technologies",
            "strengths",
            "gaps",
            "risk_flags",
            "recommended_action",
            "confidence",
        ],
    }
