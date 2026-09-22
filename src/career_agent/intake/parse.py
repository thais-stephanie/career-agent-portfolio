"""Bytes into a validated package, with identity derived here.

WHY `claim_key` IS NOT TAKEN FROM THE FILE
-------------------------------------------
A package could carry an id per claim, and it must not be believed if it does.
Two reasons, and the second is the one that matters:

1. Two generators asked for the same career produce different ids, so a
   re-import would offer forty claims the candidate already answered.
2. An id supplied by the file is an id that can COLLIDE, deliberately or by
   accident, with a claim already in the review -- and the row it collides with
   is one somebody has already confirmed. A file must not be able to choose
   which existing row it lands on.

So identity is derived from the claim's own content, here, by code in this
repository. Same content, same key; different content, different key. That is
what makes "you have already answered this" a statement about the claim rather
than about whoever generated it.

The key deliberately does NOT include `source_ref`. A CV and a LinkedIn export
stating the same sentence about the same employer are the same claim seen
twice, and giving them different keys would present the candidate with the same
line to answer twice. What it DOES include is the normalised period, so two
documents that disagree about when a role ended produce two keys -- which is
exactly the pair `conflicts.py` then groups.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from typing import Any

from pydantic import ValidationError

from career_agent.intake.models import (
    MAX_CLAIMS,
    IntakePackage,
    IntakeParseError,
    ProposedClaim,
    reject_forbidden_keys,
)

#: The largest package this will read, in bytes.
#:
#: A CV and a LinkedIn export produce a few hundred kilobytes at the outside.
#: The cap exists so a wrong file -- a video, a database -- fails as a size
#: error rather than as an out-of-memory one.
MAX_PACKAGE_BYTES = 4 * 1024 * 1024


def parse_package(raw: bytes | str | dict[str, Any]) -> IntakePackage:
    """One package, validated, or `IntakeParseError` naming what is wrong.

    Every failure mode here is a REFUSAL rather than a repair. A package this
    cannot read in full is a package whose claims would have to be guessed at,
    and a guessed claim about somebody's career is the thing the whole contract
    exists to prevent.
    """
    document = _as_mapping(raw)

    # Before the model, because `extra="forbid"` says "unexpected field" and
    # this says why the field is unwelcome. See `reject_forbidden_keys`.
    reject_forbidden_keys(document)

    claims = document.get("claims")
    if isinstance(claims, list) and len(claims) > MAX_CLAIMS:
        raise IntakeParseError(
            f"the package declares {len(claims)} claims; the cap is {MAX_CLAIMS}. "
            "Is this a career or a corpus?"
        )

    try:
        return IntakePackage.model_validate(document)
    except ValidationError as exc:
        raise IntakeParseError(_readable(exc)) from exc


def _as_mapping(raw: bytes | str | dict[str, Any]) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, bytes):
        if len(raw) > MAX_PACKAGE_BYTES:
            raise IntakeParseError(
                f"the file is {len(raw) // 1024} KB; an intake package is capped at "
                f"{MAX_PACKAGE_BYTES // 1024} KB. Is this the right file?"
            )
        try:
            raw = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise IntakeParseError(
                "the file is not UTF-8 text. An intake package is JSON; this looks binary."
            ) from exc
    if len(raw) > MAX_PACKAGE_BYTES:
        raise IntakeParseError(
            f"the package is capped at {MAX_PACKAGE_BYTES // 1024} KB. Is this the right file?"
        )
    try:
        document = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise IntakeParseError(
            f"this is not valid JSON: {exc.msg} at line {exc.lineno}, column {exc.colno}. "
            "If an assistant produced it, it may have wrapped the JSON in prose or in a "
            "code fence -- the file should begin with a brace."
        ) from exc
    if not isinstance(document, dict):
        raise IntakeParseError(
            f"an intake package is a JSON object; this file's top level is a "
            f"{type(document).__name__}."
        )
    return document


def _readable(exc: ValidationError) -> str:
    """Pydantic's report, as a sentence naming the field.

    Kept to the first few problems: a generator that got the shape wrong got it
    wrong in the same way forty times, and printing forty identical lines
    buries the one useful sentence.
    """
    lines = []
    for error in exc.errors()[:5]:
        where = ".".join(str(p) for p in error["loc"]) or "$"
        lines.append(f"{where}: {error['msg']}")
    remaining = len(exc.errors()) - len(lines)
    if remaining > 0:
        lines.append(f"...and {remaining} more problem(s) of the same kind")
    return "; ".join(lines)


# =========================================================================
# identity
# =========================================================================

_PUNCT = re.compile(r"[^a-z0-9]+")


def _fold(text: str) -> str:
    """Accent-folded, case-folded, whitespace-collapsed.

    So that the same sentence typed with different keyboards, or exported by
    two tools that disagree about curly quotes, is recognised as one claim
    rather than offered to the candidate twice.
    """
    stripped = unicodedata.normalize("NFKD", text)
    ascii_only = "".join(ch for ch in stripped if not unicodedata.combining(ch))
    return _PUNCT.sub(" ", ascii_only.casefold()).strip()


def claim_identity(claim: ProposedClaim) -> str:
    """The stable key for one proposed claim. Derived, never supplied.

    Content-addressed over the four things that make a claim what it is: its
    kind, its employer, its normalised period, and its text. Not its source --
    see the module docstring -- and not its evidence, because two documents
    quoting different sentences to support the same claim are still supporting
    the same claim.
    """
    period = claim.period
    start = (period.start.normalized or "") if period and period.start else ""
    end = (period.end.normalized or "") if period and period.end else ""
    current = "current" if period and period.current else ""
    material = "\x1f".join(
        [
            claim.type.value,
            _fold(claim.employer or ""),
            start,
            end,
            current,
            _fold(claim.text),
        ]
    )
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]
    # A readable prefix so a person reading a review or a log can tell an
    # employment row from a skill row without decoding anything.
    return f"{claim.type.value.lower()}-{digest}"


def canonical_json(package: IntakePackage) -> str:
    """The package as bytes for hashing. Stable across key order and spacing.

    `package_sha256` is what makes re-importing the same file a recognition
    rather than a second review, so it must not change because a generator
    emitted its keys in a different order.
    """
    return json.dumps(
        package.model_dump(mode="json", exclude_none=True),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def package_digest(package: IntakePackage) -> str:
    return hashlib.sha256(canonical_json(package).encode("utf-8")).hexdigest()
