"""The one semantic contract every provider answers.

Every provider, API-backed, subscription-backed or local, receives these
instructions and must answer in this shape. Provider-specific output never
leaks past this module: adapters return text, `parse_answer` turns it into the
transport model, and `gate.publish` decides what of it is true.

VERSIONING
----------
`CONTRACT_VERSION` is a label a person bumps. `contract_identity()` is a digest
of the label, the instructions and the schema together, so editing the prompt
without bumping the label still changes every cache key. An answer to old
instructions is never served as an answer to new ones.
"""

from __future__ import annotations

import hashlib
import json
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from career_agent.semantic.intent import SearchIntent

CONTRACT_VERSION = "semantic-3"

#: How much of a posting is sent. Quotes must come from the text actually sent,
#: so a finding can never cite a part of the posting the provider did not see.
MAX_POSTING_CHARS = 12_000


class Verdict(StrEnum):
    STRONG = "strong"
    PARTIAL = "partial"
    NONE = "none"
    UNRESOLVED = "unresolved"


class Strength(StrEnum):
    STRONG = "strong"
    PARTIAL = "partial"


class TMatch(BaseModel):
    """One intent item the posting supports, and the words that show it."""

    model_config = ConfigDict(extra="ignore")

    intent_id: str
    strength: Strength
    quotes: list[str] = Field(default_factory=list)


class TAspect(BaseModel):
    model_config = ConfigDict(extra="ignore")

    verdict: Verdict
    matches: list[TMatch] = Field(default_factory=list)
    #: Matches the provider sent that were not matches in this contract's
    #: shape (a "none" strength, a misspelt key), dropped one by one by
    #: `parse_answer` and counted here. Never sent by a provider.
    malformed: int = Field(default=0, ge=0)


class TAnswer(BaseModel):
    """What a provider must return. Nothing here is believed yet."""

    model_config = ConfigDict(extra="ignore")

    #: The provider's own summary of the role. Context for a reader of the
    #: evaluation; never evidence and never points.
    role_core: str = ""
    work: TAspect
    tools: TAspect
    other: TAspect
    #: Metadata only. It never becomes points.
    provider_confidence: float | None = Field(default=None, ge=0.0, le=1.0)


def answer_schema() -> dict[str, Any]:
    """A plain JSON Schema for providers that accept one."""
    match = {
        "type": "object",
        "additionalProperties": False,
        "required": ["intent_id", "strength", "quotes"],
        "properties": {
            "intent_id": {"type": "string"},
            "strength": {"type": "string", "enum": ["strong", "partial"]},
            "quotes": {"type": "array", "items": {"type": "string"}},
        },
    }
    aspect = {
        "type": "object",
        "additionalProperties": False,
        "required": ["verdict", "matches"],
        "properties": {
            "verdict": {"type": "string", "enum": [v.value for v in Verdict]},
            "matches": {"type": "array", "items": match},
        },
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["role_core", "work", "tools", "other"],
        "properties": {
            "role_core": {"type": "string"},
            "work": aspect,
            "tools": aspect,
            "other": aspect,
        },
    }


SYSTEM_PROMPT = """You compare ONE job posting with what ONE person wants from their next role.
You do not decide whether they should apply and you never produce a score.

The person's intent has three lists, each item with an id:
- work: the kind of work they want to do.
- tools: tools, platforms or methods they want to use.
- other: any other desired signals.

First decide what this role MAINLY does: its two to four core
responsibilities, as the posting's own role description states them. Write
that as "role_core" (at most 30 words, your words).

Then, for each list, find the items the POSTING genuinely supports.
- The posting does not need to reuse the person's words. "Design systems that
  remove manual handoffs across revenue operations" can support an item
  "workflow automation". Judge meaning, not keywords.
- strength "strong": the item IS one of the role's core responsibilities in
  role_core, in the specific sense the person means. A generic neighbour is
  not strong: building a web application is not "business application
  development" for revenue teams, and a data pipeline is not "systems
  integration" of business platforms, unless the posting says so.
- When an item carries a qualifier that narrows it (a domain such as
  business, revenue, finance or CRM, or a field such as AI), "strong" needs
  the posting's work to carry that same qualifier. The activity without it
  (generic web, software or data work) is at most "partial".
- Most postings support only one to three items strongly. "strong" is for
  items at the centre of role_core; anything else genuinely present is
  "partial".
- strength "partial": the item is a stated but secondary duty of this role, or
  a close neighbour of what the person asked for.
- List ONLY the items the posting supports. Never list an item as "none" or
  without a quote.
- Never match from: the company description or mission, benefits, the hiring
  process, other teams' work, or a generic line such as "collaborate with
  stakeholders" or "improve processes". Never match a work item from a tools
  list alone.
- Prefer the most specific item. One sentence normally supports one work item.
- Every match needs at least one quote. A quote is copied CHARACTER FOR
  CHARACTER from the posting text, one sentence or phrase, at most 300
  characters. Never paraphrase, translate, fix typos or join separate pieces.
- Use only ids that appear in the intent. Never invent an id.

verdict for each list:
- "strong": at least one strong match.
- "partial": only partial matches.
- "none": the list has items and the posting supports none of them.
- "unresolved": the posting is too short, garbled or ambiguous to tell, or the
  list is empty.

Answer with ONE json object and nothing else, exactly this shape:
{"role_core": "what this role mainly does",
 "work": {"verdict": "strong|partial|none|unresolved",
          "matches": [{"intent_id": "W1", "strength": "strong|partial",
                       "quotes": ["exact text from the posting"]}]},
 "tools": {"verdict": "...", "matches": [...]},
 "other": {"verdict": "...", "matches": [...]}}"""


def contract_identity() -> str:
    """Label plus instructions plus schema: what a cached answer answered."""
    canonical = json.dumps(
        [CONTRACT_VERSION, SYSTEM_PROMPT, answer_schema(), MAX_POSTING_CHARS],
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12]
    return f"{CONTRACT_VERSION}+{digest}"


def posting_text(description: str) -> str:
    """The exact text a provider sees, and the only text a quote may cite."""
    return description[:MAX_POSTING_CHARS]


def user_message(intent: SearchIntent, title: str, posting: str) -> str:
    """The per-job half of the request. Title for orientation, body as source.

    The title is sent so the reader knows what role it is looking at, and it is
    deliberately NOT quotable: the gate checks quotes against the body alone,
    because the title buying fit is the one thing Search Fit never allows.
    """
    payload = {
        "intent": intent.for_provider(),
        "job_title_for_context_only_not_quotable": title[:200],
        "posting": posting,
    }
    return json.dumps(payload, ensure_ascii=False)


class AnswerRejected(ValueError):
    """The provider's text is not an answer in this contract's shape."""


def parse_answer(raw: str) -> TAnswer:
    """Text to the transport model. Tolerates a code fence, nothing else."""
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise AnswerRejected(f"not a json document: {exc.msg}") from exc
    if not isinstance(data, dict):
        raise AnswerRejected("the answer is not a json object")
    # A malformed FINDING is refused on its own; it does not take the rest of
    # a well-formed answer with it. Measured on the first real batch: 28 of
    # 300 answers listed items the model had rejected with strength "none",
    # or misspelt one key, and each lost every valid finding beside it.
    for name in ("work", "tools", "other"):
        aspect = data.get(name)
        if isinstance(aspect, dict) and isinstance(aspect.get("matches"), list):
            kept = []
            for match in aspect["matches"]:
                try:
                    TMatch.model_validate(match)
                except ValidationError:
                    continue
                kept.append(match)
            aspect["malformed"] = len(aspect["matches"]) - len(kept)
            aspect["matches"] = kept
    try:
        return TAnswer.model_validate(data)
    except ValidationError as exc:
        raise AnswerRejected(f"schema: {exc.error_count()} error(s)") from exc
