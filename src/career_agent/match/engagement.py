"""The engagement the product USES when a posting does not state one.

THE OWNER'S RULE (2026-10-13). When there is evidence that the employer is an
American company AND that it hires people in Latin America, and the advert
does not say how the worker is engaged, the effective engagement is a
contractor: "Probably Contractor". That is how a United States company
ordinarily engages somebody in Brazil or Argentina without an entity there.

What stays apart, so nothing is rewritten:

* the STATED reading (`match.employment.read_employment`), which is what the
  advert or its board said and is never replaced;
* the EFFECTIVE engagement, which filters and the score use;
* the BASIS: the quote (or registry record) for the American employer and
  the quote for hiring in Latin America.

A stated engagement always wins, including a stated employee. A working
pattern (full-time, part-time) is not an engagement and does not block the
default. An American employer is NEVER deduced from a currency or from the
advert's language: only the employer's curated headquarters, or the advert
saying in words that the company is American / based in the United States.
The default never reaches a gate: the contractor-authorisation gate answers
only to an engagement the employer STATED (`match.authorization`).
"""

from __future__ import annotations

import re
from dataclasses import replace

from career_agent.domain.enums import EmploymentRelationship
from career_agent.domain.matching import EmploymentReading, GateOutcome
from career_agent.match.places import resolve_place

#: The registry's curated headquarters, recorded as the basis in this form.
REGISTRY_US = "registry:hq_country=US"

#: Where a scope sentence is cut into places: list punctuation and the small
#: words that join places in English, Portuguese and Spanish.
_PLACE_BREAKS = re.compile(r"[,;/()]|\b(?:and|or|e|ou|y|in|em|en|from|based)\b", re.IGNORECASE)

_US = r"(?:U\.S\.A?\.?|US|USA|United States(?: of America)?|American|North[- ]American)"
_ORG = (
    r"(?:company|companies|startup|start-up|client|clients|firm|business|employer|"
    r"organization|organisation|corporation|agency|SaaS company|tech company|"
    r"software company|fintech|healthtech|edtech)"
)
_US_PLACES = (
    r"(?:the\s+)?(?:U\.S\.A?\.?|US|USA|United States|California|New York|San Francisco|"
    r"Silicon Valley|Texas|Austin|Florida|Miami|Seattle|Boston|Chicago|Los Angeles|"
    r"Denver|Atlanta|Delaware|Washington,? D\.?C\.?)"
)

#: Statements that the EMPLOYER (or the client a role is placed with) is
#: American. Case matters for `US`, which is otherwise the pronoun.
_US_COMPANY: tuple[re.Pattern[str], ...] = (
    re.compile(
        rf"\b(?:[Aa]n?|[Oo]ur|[Tt]he|[Ff]or|[Ww]ith|[Jj]oin)\s+(?:[\w-]+\s+){{0,3}}?"
        rf"{_US}[- ](?:based\s+)?{_ORG}\b"
    ),
    re.compile(rf"\b{_US}[- ]based\s+(?:[\w-]+\s+){{0,2}}?{_ORG}\b"),
    re.compile(rf"\b(?:[Hh]eadquartered|[Bb]ased|HQ'?d)\s+in\s+{_US_PLACES}\b"),
    re.compile(
        r"\b(?:empresa|startup|cliente|companhia)s?\s+(?:norte[- ])?american[ao]s?\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:empresa|startup|cliente|companhia)\s+(?:com\s+sede|sediada|baseada)\s+"
        r"(?:nos|em)\s+(?:EUA|Estados Unidos)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\bempresa\s+estadounidense\b", re.IGNORECASE),
    re.compile(r"\bcon\s+sede\s+en\s+(?:EE\.?\s?UU\.?|Estados Unidos)\b", re.IGNORECASE),
)


def us_company_evidence(description: str, company_country: str | None) -> str | None:
    """The basis for "this employer is American", or None.

    The curated registry first, then the advert's own words, returned as the
    exact substring that said so (ADR-0002). Never a currency, never a
    language, never a benefit such as a 401(k): those describe pay or
    domestic employment, not where the company is.
    """
    if (company_country or "").strip().upper() == "US":
        return REGISTRY_US
    for pattern in _US_COMPANY:
        found = pattern.search(description or "")
        if found:
            return found.group(0)
    return None


def latam_hiring_evidence(declared_scope: str | None, gates: tuple[GateOutcome, ...]) -> str | None:
    """The basis for "this employer hires in Latin America", or None.

    Only a stated hiring scope counts: the board's own scope field, or the
    sentence the geography gate quoted. Both are resolved through the same
    gazetteer the gate uses; a scope naming Latin America, or any country in
    it, is hiring there. An office location is not a hiring scope and is not
    read here (invariant 3).
    """
    for text in (declared_scope, *(g.quote for g in gates if g.gate == "geography")):
        if not text:
            continue
        # The whole text, then each place in it: a quoted sentence such as
        # "we hire in Brazil, Argentina and Mexico" names three places, and
        # "Latin America are welcome to apply" starts with one (its first
        # one to four words are tried).
        pieces = [text]
        for part in _PLACE_BREAKS.split(text):
            words = (part or "").split()
            pieces.extend(" ".join(words[:n]) for n in range(min(len(words), 4), 0, -1))
        if any("LATAM" in resolve_place(piece).regions for piece in pieces if piece):
            return text
    return None


def with_default(
    reading: EmploymentReading,
    *,
    description: str,
    company_country: str | None,
    declared_scope: str | None,
    gates: tuple[GateOutcome, ...],
) -> EmploymentReading:
    """`reading`, with the contractor default attached when the rule applies.

    The stated reading is left exactly as it was; the default rides beside it
    in `effective` and its two quotes. A stated engagement, of any kind, means
    there is nothing to default.
    """
    if reading.is_explicit and reading.relationship is not EmploymentRelationship.UNRESOLVED:
        return reading
    american = us_company_evidence(description, company_country)
    if american is None:
        return reading
    latam = latam_hiring_evidence(declared_scope, gates)
    if latam is None:
        return reading
    return replace(
        reading,
        effective=EmploymentRelationship.CONTRACTOR_B2B,
        default_us_evidence=american,
        default_latam_evidence=latam,
    )
