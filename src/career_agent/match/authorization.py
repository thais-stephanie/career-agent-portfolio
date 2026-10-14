"""The right to do the work: what a posting REQUIRES, read against what the
candidate DECLARED. Neither alone answers anything.

Four questions, kept apart because they have different answers:

* EMPLOYMENT AUTHORIZATION in a country: "must be authorized to work in the
  United States", "right to work in the UK", "valid work permit". Answered
  only by the countries she declared she IS (`authorized_countries`) or is
  NOT (`not_authorized_countries`) authorized to be employed in. NEVER by
  `eligible_countries`: that list has always said where employers may hire
  her FROM (a location and hiring-scope fact the geography gate reads), and
  a person in Brazil may list a country whose employers hire her remotely,
  as a contractor, with no right to be employed there.
* VISA SPONSORSHIP: "we do not sponsor visas". Answered by the countries
  where she declared she would NEED sponsorship (`sponsorship_needed`), or
  by a declared authorization there, which needs none.
* CITIZENSHIP: "US citizenship required", "green card holder". Nothing the
  candidate declares answers it positively: citizenship is never read from
  nationality, residence or anything else. A declared lack of authorization
  in that country does answer it: no citizen lacks it.
* ENGAGEMENT AS A CONTRACTOR: a posting that states, explicitly or in its
  own contract-type field, that the work is done as an independent
  contractor (invoice, PJ, B2B). She answers twice: for a client in the
  country she lives in (`contractor`) and for a client abroad
  (`contractor_abroad`); a no at home is not a no abroad. No reader here
  identifies the contracting party's country (a posting's location is where
  the WORK is), so it is COMPATIBLE only when she said yes to both,
  INCOMPATIBLE only when she said no to both, and PENDING otherwise.

WHERE THE CANDIDATE LIVES (`candidate_country`) answers none of these: a
residence is the location question, which the geography gate asks. And a
question the candidate has not answered is PENDING, quoted: it is never a
pass, and never a refusal either.

Each requirement is a sentence the employer wrote, quoted with its offsets.
A phrase that names a place this product cannot resolve to a country is not
read as a requirement about any country.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from career_agent.config.search_config import Eligibility, Negation, SearchConfig
from career_agent.domain.enums import EmploymentRelationship, EmploymentSource, GateResult
from career_agent.domain.matching import EmploymentReading, GateOutcome
from career_agent.match.lexicon import is_negated_before
from career_agent.match.places import ResolvedPlace, display_names, resolve_place
from career_agent.match.text import FoldedText, sentence_at

AUTHORIZATION, CITIZENSHIP, NO_SPONSORSHIP, CONTRACTOR = (
    "AUTHORIZATION", "CITIZENSHIP", "NO_SPONSORSHIP", "CONTRACTOR",
)  # fmt: skip
COMPATIBLE, INCOMPATIBLE, PENDING = "COMPATIBLE", "INCOMPATIBLE", "PENDING"

_PLACE = r"(?P<place>[a-z][a-z .'-]{1,40}?)(?=\s*(?:[.;,:!?()\n]|$|\s(?:without|with|now|and|or|at|on|as|who|which|prior|by|to|before|once|if)\b))"  # noqa: E501
#: "authorized to work in the United States", "right to work in the UK",
#: "autorizacao para trabalhar no Brasil" (folded: no accents).
_AUTHORIZED_IN = re.compile(
    r"\b(?:(?:legally\s+)?(?:authori[sz]ed|permitted|eligible)\s+to\s+(?:live\s+and\s+)?work"
    r"|right\s+to\s+work|work\s+(?:authori[sz]ation|permit)"
    r"|autorizacao\s+(?:de|para)\s+trabalh(?:o|ar)|permissao\s+(?:de|para)\s+trabalh(?:o|ar))"
    r"\s+(?:in|within|for|no|na|nos|em)\s+(?:the\s+)?" + _PLACE
)
#: The same requirement with no country named: "must have work authorization",
#: "proof of work authorization", "valid work permit".
_AUTHORIZED = re.compile(
    r"\b(?:(?:must|should|need\s+to|required\s+to)\s+(?:be\s+)?(?:legally\s+)?"
    r"(?:authori[sz]ed|eligible)\s+to\s+work"
    r"|(?:valid|current|existing|proof\s+of)\s+(?:work\s+(?:authori[sz]ation|permit)|right\s+to\s+work))\b"
)
_DEMONYMS = {
    "u.s.": "US", "us": "US", "u.s": "US", "american": "US", "united states": "US",
    "uk": "GB", "british": "GB", "canadian": "CA", "australian": "AU", "german": "DE",
    "french": "FR", "brazilian": "BR", "brasileiro": "BR", "brasileira": "BR", "indian": "IN",
    "mexican": "MX", "irish": "IE", "dutch": "NL", "spanish": "ES", "portuguese": "PT",
}  # fmt: skip
_CITIZEN = re.compile(
    r"\b(?P<who>u\.s\.?|us|american|united\s+states|uk|british|canadian|australian|german|french"
    r"|brazilian|brasileir[oa]|indian|mexican|irish|dutch|spanish|portuguese)\s+citizen(?:ship|s)?\b"
    r"|\bgreen\s+card\b"
    r"|\b(?:must\s+be\s+a\s+citizen|citizenship\s+(?:is\s+)?required)\b"
)
_NO_SPONSORSHIP = re.compile(
    r"\b(?:(?:not|unable\s+to|cannot|can['’ ]?t|won['’ ]?t|will\s+not|do\s+not|does\s+not"
    r"|don['’ ]?t|are\s+not\s+able\s+to|is\s+not\s+able\s+to)\s+(?:currently\s+)?"
    r"(?:offer|provide|support)(?:\s+\w+){0,2}?\s+(?:visa\s+|work\s+visa\s+|immigration\s+)?sponsorship"
    r"|(?:not|unable\s+to|cannot|can['’ ]?t|won['’ ]?t|will\s+not|do\s+not|does\s+not"
    r"|don['’ ]?t|are\s+not\s+able\s+to)\s+(?:currently\s+)?sponsor"
    r"|no\s+(?:visa\s+|immigration\s+)?sponsorship"
    r"|without\s+(?:the\s+need\s+for\s+)?(?:visa\s+|immigration\s+)?sponsorship"
    r"|sponsorship\s+(?:is\s+)?not\s+(?:available|offered|provided)"
    r"|nao\s+(?:oferecemos|patrocinamos|fornecemos)\s+(?:o\s+)?visto)"
)
#: A country written beside a sponsorship sentence: "sponsorship in the US".
_COUNTRY_NEAR = re.compile(
    r"\b(?:in|for|within|no|na|em)\s+(?:the\s+)?" + _PLACE.replace("(?P<place>", "(?P<near>")
)


@dataclass(frozen=True)
class Requirement:
    kind: str
    #: ISO alpha-2, or None when the posting names no country.
    country: str | None
    quote: str
    char_start: int
    char_end: int
    #: A REGION the posting names instead of a country ("authorized to work in
    #: the EU"). Never turned into a country and never answered from one: a
    #: right to work in Portugal is not a right to work across the EU.
    region: str | None = None


def _country(text: str | None) -> str | None:
    """One country a place phrase names, or None."""
    if not text:
        return None
    plain = text.strip(" .")
    if plain in _DEMONYMS:
        return _DEMONYMS[plain]
    found = resolve_place(plain).countries
    return found[0] if len(found) == 1 else None


def _one(place: ResolvedPlace | None) -> str | None:
    """The posting's own single country, for a requirement that names none."""
    return place.countries[0] if place and len(place.countries) == 1 else None


def read_requirements(
    config: SearchConfig | Negation, field: FoldedText, place: ResolvedPlace | None = None
) -> list[Requirement]:
    """Every explicit requirement about the right to work, in text order.
    Reads the posting and the negation lists only (a configuration or its
    `negation` section): the same for every candidate."""
    negation = config.negation if isinstance(config, SearchConfig) else config
    out: list[Requirement] = []

    def add(
        kind: str, country: str | None, match: re.Match[str], region: str | None = None
    ) -> None:
        if is_negated_before(field, match.start(), negation):
            return
        start = field.offsets[match.start()]
        end = field.offsets[match.end() - 1] + 1
        quote = sentence_at(field.original, start, end)
        out.append(Requirement(kind, country, quote, start, end, region))

    text = field.folded
    claimed: list[tuple[int, int]] = []
    for match in _AUTHORIZED_IN.finditer(text):
        country = _country(match.group("place"))
        if country:  # an unresolvable place is not a country requirement
            add(AUTHORIZATION, country, match)
        else:
            # "authorized to work in the EU": a named scope, kept as one. It
            # used to be dropped, so the gate read no requirement at all.
            named = resolve_place(match.group("place").strip(" .")).stated_regions
            if len(named) == 1 and named[0] != "WORLDWIDE":
                add(AUTHORIZATION, None, match, named[0])
        claimed.append(match.span())
    for match in _AUTHORIZED.finditer(text):
        # The same sentence read with its country already: not a second one.
        if not any(match.start() < b and match.end() > a for a, b in claimed):
            add(AUTHORIZATION, _one(place), match)
    for match in _CITIZEN.finditer(text):
        who = match.group("who")
        country = (
            _DEMONYMS.get(re.sub(r"\s+", " ", who)) if who
            else "US" if "green" in match.group(0) else _one(place)
        )  # fmt: skip
        add(CITIZENSHIP, country, match)
    for match in _NO_SPONSORSHIP.finditer(text):
        near = _COUNTRY_NEAR.search(text, match.end(), min(len(text), match.end() + 60))
        country = _country(near.group("near")) if near else None
        add(NO_SPONSORSHIP, country or _one(place), match)
    return sorted(out, key=lambda r: r.char_start)


def contractor_requirement(
    employment: EmploymentReading | None, place: ResolvedPlace | None = None
) -> Requirement | None:
    """A posting that SAYS the work is done as an independent contractor: in
    its own contract-type field, or in so many words. A benefit or a likely
    regime is not a statement and is not read. It carries no country: the
    posting's location is where the work is, not where the client is."""
    if employment is None or employment.relationship is not EmploymentRelationship.CONTRACTOR_B2B:
        return None
    if employment.source not in (
        EmploymentSource.EXPLICIT_STATEMENT,
        EmploymentSource.STRUCTURED_FIELD,
    ):
        return None
    del place  # where the work is; never the contracting party's country
    return Requirement(CONTRACTOR, None, employment.evidence or "", -1, -1)


#: Why, per requirement and answer. `{c}` is the country the posting names.
_WHY = {
    (AUTHORIZATION, "NO_COUNTRY"): (
        "The posting requires work authorization without naming the country."
    ),
    (AUTHORIZATION, INCOMPATIBLE): (
        "The posting requires authorization to work in {c}; you said you do not have it."
    ),
    (AUTHORIZATION, COMPATIBLE): (
        "The posting requires authorization to work in {c}; you said you have it."
    ),
    (AUTHORIZATION, "REGION"): (
        "The posting requires authorization to work in {r}. Career Agent does not answer a "
        "requirement about a whole region from the countries you named; check it yourself."
    ),
    (AUTHORIZATION, PENDING): (
        "The posting requires authorization to work in {c}; you have not said whether you have it."
    ),
    (CITIZENSHIP, INCOMPATIBLE): (
        "The posting requires {c} citizenship; you said you are not authorized to work in {c}."
    ),
    (CITIZENSHIP, PENDING): (
        "The posting requires a citizenship; Career Agent never infers one from where you live."
    ),
    (NO_SPONSORSHIP, "NO_COUNTRY"): (
        "The posting does not sponsor visas; it does not say for which country."
    ),
    (NO_SPONSORSHIP, INCOMPATIBLE): (
        "The posting does not sponsor visas in {c}; you said you would need sponsorship there."
    ),
    (NO_SPONSORSHIP, COMPATIBLE): (
        "The posting does not sponsor visas in {c}; you said you are authorized to work there."
    ),
    (NO_SPONSORSHIP, PENDING): (
        "The posting does not sponsor visas in {c}; you have not said whether you would need it."
    ),
}


def _contractor(eligibility: Eligibility) -> tuple[str, str]:
    """A contractor engagement. Where the CONTRACTING PARTY is decides which of
    her two answers counts (at home: `contractor`; abroad: `contractor_abroad`),
    and no reader here identifies it: a posting's location ("Remote, Brazil")
    is where the WORK is, which is her own country, never proof of where the
    client is, and a PJ regime is not either (foreign clients engage Brazilian
    PJs). So only two answers decide without that context: yes in both places,
    or no in both. A no at home is never a no abroad."""
    lead = "The posting engages an independent contractor"
    home, abroad = eligibility.contractor, eligibility.contractor_abroad
    if home is True and abroad is True:
        return COMPATIBLE, f"{lead}; you said you can work as one at home and for clients abroad."
    if home is False and abroad is False:
        return INCOMPATIBLE, (
            f"{lead}; you said you cannot work as one, at home or for clients abroad."
        )
    return PENDING, (
        f"{lead} and does not say where the contracting party is; your answer depends on it "
        "(at home or abroad)."
    )


def answer(requirement: Requirement, eligibility: Eligibility) -> tuple[str, str]:
    """COMPATIBLE, INCOMPATIBLE or PENDING, and the sentence that says why."""
    c = requirement.country
    if requirement.kind == CONTRACTOR:
        return _contractor(eligibility)
    yes = {x.upper() for x in eligibility.authorized_countries}
    no = {x.upper() for x in eligibility.not_authorized_countries}
    sponsor = {x.upper() for x in eligibility.sponsorship_needed}
    if requirement.region is not None:
        name = display_names()[1].get(requirement.region, requirement.region)
        name = f"the {name}" if requirement.region == "EU" else name
        return PENDING, _WHY[(requirement.kind, "REGION")].format(r=name)
    if c is None:
        state = "NO_COUNTRY" if requirement.kind != CITIZENSHIP else PENDING
    elif requirement.kind == AUTHORIZATION:
        state = INCOMPATIBLE if c in no else COMPATIBLE if c in yes else PENDING
    elif requirement.kind == CITIZENSHIP:
        # Never COMPATIBLE: an authorization is not a citizenship.
        state = INCOMPATIBLE if c in no else PENDING
    else:
        state = INCOMPATIBLE if c in sponsor else COMPATIBLE if c in yes else PENDING
    why = _WHY[(requirement.kind, state)].format(c=c)
    return (PENDING if state == "NO_COUNTRY" else state), why


def _where_the_work_is(
    requirement: Requirement, state: str, why: str, on_site: bool
) -> tuple[str, str]:
    """A visa is needed to work IN a country. "We do not sponsor visas in the
    US" refuses nobody whose work is done from somewhere else, so outside an
    on-site or hybrid job it is left to confirm, never a refusal. A stated
    AUTHORIZATION or CITIZENSHIP requirement is different: the employer asked
    for it in so many words, wherever the work is."""
    if requirement.kind == NO_SPONSORSHIP and state == INCOMPATIBLE and not on_site:
        c = requirement.country
        return PENDING, (
            f"The posting does not sponsor visas in {c}. That matters only if the work must be "
            f"done in {c}, and this posting does not say it must; confirm before applying."
        )
    return state, why


def authorization_gate(
    config: SearchConfig,
    field: FoldedText,
    place: ResolvedPlace | None = None,
    employment: EmploymentReading | None = None,
    workplace_type: str | None = None,
    requirements: Sequence[Requirement] | None = None,
) -> GateOutcome | None:
    """The work_authorization gate from requirements and declarations, or None
    when the posting states no requirement. Any INCOMPATIBLE answer decides
    (FAIL); else any PENDING one (UNRESOLVED, quoted, so the eligibility
    status cannot read as verified); else COMPATIBLE (PASS).

    The posting's location names the country of a requirement that names none
    only when the board's own field says the work is done there (on site or
    hybrid). A remote role's published location, or one whose work model is
    not stated, may be the company's office (invariant 3), and the company's
    country is not where she would work."""
    on_site = workplace_type in ("ONSITE", "HYBRID")
    found = (
        list(requirements)
        if requirements is not None
        else read_requirements(config, field, place if on_site else None)
    )
    contractor = contractor_requirement(employment, place)
    if contractor is not None:
        found.append(contractor)
    if not found:
        return None
    answered = [
        (r, *_where_the_work_is(r, *answer(r, config.eligibility), on_site=on_site)) for r in found
    ]
    home = config.eligibility.candidate_country.strip().upper()
    if workplace_type == "REMOTE" and home in {
        x.upper() for x in config.eligibility.authorized_countries
    }:
        # "We do not sponsor visas", no country named, on a REMOTE role: a visa
        # is needed to work IN a country, and she declared she may work where
        # she lives. Whether the role may be done from there is the geography
        # gate's question, which still decides the status; this one has no
        # question left to ask her.
        answered = [
            (
                r,
                COMPATIBLE,
                "The posting does not sponsor visas. The role is remote and you said you "
                "are authorized to work where you live, so no visa is needed to do it from there.",
            )
            if r.kind == NO_SPONSORSHIP and r.country is None and s == PENDING
            else (r, s, why)
            for r, s, why in answered
        ]
    for state, result in ((INCOMPATIBLE, GateResult.FAIL), (PENDING, GateResult.UNRESOLVED),
                          (COMPATIBLE, GateResult.PASS)):  # fmt: skip
        hit = next(((r, why) for r, s, why in answered if s == state), None)
        if hit is not None:
            requirement, why = hit
            located = requirement.char_start >= 0
            return GateOutcome(
                gate="work_authorization",
                result=result,
                reason=why,
                quote=requirement.quote or None,
                char_start=requirement.char_start if located else None,
                char_end=requirement.char_end if located else None,
            )
    return None
