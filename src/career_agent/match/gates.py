"""Five eligibility questions, each answered in three values.

The rule that shapes this whole module: **absence is never permission**. A
posting that says nothing about where it hires is UNRESOLVED, and no amount of
silence turns that into PASS. The only way to reach PASS is a sentence the
employer wrote, quoted back; the only way to reach FAIL is likewise a sentence,
quoted back with its offsets so it can be verified against the original.

That asymmetry is deliberate and it is the difference between a matcher and a
guess. "Remote" is not "worldwide". An office address is not a hiring scope. A
timezone is not a country. None of them open a gate here.

Negation applies to blockers exactly as it does to the lexicon: "we do not
require a security clearance" contains "security clearance" and must not fail
the clearance gate. A negated blocker hit records nothing at all -- it is neither
a FAIL nor a PASS, because a sentence saying a barrier is absent is not a
sentence saying the candidate is admitted.
"""

from __future__ import annotations

import re
from bisect import bisect_left
from collections.abc import Sequence
from dataclasses import dataclass

from career_agent.config.search_config import Blocker, SearchConfig
from career_agent.domain.enums import EligibilityStatus, GateResult
from career_agent.domain.matching import GATE_NAMES, GateOutcome, ObservedSignal, SignalHit
from career_agent.match.lexicon import compile_patterns, is_negated_before
from career_agent.match.places import ResolvedPlace, region_contains, resolve_place
from career_agent.match.text import (
    FoldedText,
    Section,
    fold_field,
    section_kind_at,
    sentence_at,
)

#: The gates this matcher can answer, in the order they are reported: the
#: one vocabulary, shared with the configuration loader (`GATE_NAMES`).
#: `credential` (HCE01) is an exclusionary gate like clearance: a blocker on
#: it is an employer REQUIRING a credential in so many words, negation
#: respected, quoted; silence is the absence of a disqualification. Which
#: credential, and that the candidate lacks it, is the candidate's private
#: configuration to say; nothing here names one. `requirement` is where
#: `career-agent setup` files a hard exclusion the person typed; it used to
#: write `other`, a name this loop never visited.
GATE_ORDER: tuple[str, ...] = GATE_NAMES

#: What UNRESOLVED means for each gate, in a sentence the interface can show.
#: These are the exact words that appear when a posting simply did not say.
UNRESOLVED_REASONS: dict[str, str] = {
    "geography": "The posting does not state where it hires.",
    "work_authorization": "The posting does not state whether work authorization is required.",
    "clearance": "The posting does not state whether a security clearance is required.",
    "worksite": "The posting does not state whether physical presence is required.",
    "travel": "The posting does not state how much travel is required.",
    "credential": "The posting does not state whether a credential is required.",
    "requirement": "The posting does not state the requirement you named.",
}


def _find_blocker_hit(
    blocker: Blocker,
    field_name: str,
    field: FoldedText,
    config: SearchConfig,
) -> SignalHit | None:
    """The first non-negated occurrence of any of the blocker's phrases.

    Takes the field already folded. Every blocker of every gate searches the
    same description, and folding it once per blocker was most of what made a
    full rescore an evening rather than a minute.
    """
    if not field.original:
        return None

    compiled = compile_patterns(tuple(blocker.patterns))

    for match in compiled.matches_in(field):
        char_start = field.offsets[match.start()]
        char_end = field.offsets[match.end() - 1] + 1
        if blocker.negation_sensitive and is_negated_before(field, match.start(), config.negation):
            continue
        return SignalHit(
            signal_id=blocker.id,
            label=blocker.label,
            field_name=field_name,
            pattern=compiled.configured(match.group(0)),
            quote=sentence_at(field.original, char_start, char_end),
            char_start=char_start,
            char_end=char_end,
            section=section_kind_at(field.sections, char_start),
        )
    return None


#: A sentence that is ABOUT hiring, working or eligibility.
#:
#: The geography gate is the only one that can declare a candidate ELIGIBLE, so
#: it is the only place where a phrase found in prose grants something rather
#: than withholding it -- and an unanchored phrase granted a great deal.
#: Measured on the corpus: 598 of 1,030 `VERIFIED_ELIGIBLE` postings were made
#: eligible by a scope phrase in a sentence that was not about hiring at all,
#: and 129 of those were located in the United States.
#:
#: Three of the actual sentences, quoted from the corpus:
#:
#:   "Figma empowers teams to ... work together in real time from anywhere in
#:    the world"      -- product marketing, read as WORLDWIDE hiring
#:   ", LGPD (Brazil), PIPEDA (Canada), APPI (Japan)"
#:                    -- a list of privacy statutes, read as LATAM hiring
#:   "Adyen is seeking a Manager, Rewards for the Americas Region (Brazil,
#:    Canada, Mexico and the United States)"
#:                    -- a sales territory, read as LATAM hiring
#:
#: The answer is the one `match/seniority.py` already reached for the same
#: shape of defect: stop searching prose for bare phrases, and require the
#: sentence to be making the claim. Not a blacklist of marketing copy, which
#: would grow forever and still miss the next one.
HIRING_INTENT = re.compile(
    r"""\b(?:
      # -- English ------------------------------------------------------
        hir(?:e|es|ing)|recruit\w*|candidates?|applicants?|employees?|employment
      | eligib\w*|authoriz\w*|work\s+permit|visa
      | you\s+(?:can|may|will|must)\s+(?:work|be\s+based|live|reside)
      | (?:we|the\s+team)\s+(?:are|is)?\s*(?:fully\s+)?(?:remote|distributed)
      | open\s+to | welcome | accept\w*\s+applic
      | based\s+in | located\s+in | residents?\s+of | reside\s+in
      | this\s+(?:role|position|job) | the\s+(?:role|position)\s+is
      | remote\s+(?:role|position|job|team|work)
      | work\s+(?:from|remotely) | position\s+is\s+(?:open|available)
      # -- Portuguese ---------------------------------------------------
      #
      # Not a translation exercise. This product is for a candidate in
      # Brazil, half of its Brazilian postings are written in Portuguese,
      # and an English-only anchor would have silently made every one of
      # them UNRESOLVED -- the failure mode being hardest to notice in the
      # market the product exists to serve. The demo corpus caught it:
      # "Vaga remota para candidatos no Brasil" states a hiring scope as
      # plainly as any sentence in this file.
      | vagas? | candidatos? | contrata\w* | contratar
      | residentes? | resid\w+\s+(?:no|na|em) | mora\w*\s+(?:no|na|em)
      | remot[oa]s?\s+para | abert[oa]s?\s+(?:a|para|à)
      | trabalh\w*\s+(?:remoto|de\s+qualquer)
      | profissionais?\s+(?:de|do|da|no|na)
    )\b""",
    re.VERBOSE,
)


#: A scope phrase that is BOUNDED IN TIME is a benefit, not a hiring scope.
#:
#: Found on the real corpus, 2026-09-07: an `Account Executive DACH (German
#: Speaking)` based in Berlin came out VERIFIED_ELIGIBLE for a candidate in
#: Brazil because its perks list said
#:
#:     "Work from anywhere in the world for 30 days per year"
#:
#: That clears `HIRING_INTENT` -- it genuinely is a sentence about working --
#: and the phrase it matches is a real worldwide pattern. What makes it not a
#: hiring scope is the DURATION: an employer offering thirty days of working
#: abroad is describing a holiday policy, and reading it as permission to be
#: employed from Sao Paulo is the V1.2 defect in its most convincing costume.
#:
#: Deliberately narrow. It matches only an explicit span of time in the same
#: sentence, so "we hire globally" and "work from anywhere in the world" on
#: their own are untouched.
WORKATION_LIMIT = re.compile(
    r"""(?:
        \b(?:for|up\s+to|max(?:imum)?\s+of|at\s+most)\s+
        # `an?` is here because of two REAL postings that reached
        # VERIFIED_ELIGIBLE past this guard on 2026-09-09. The quantity does
        # not have to be a numeral: `work from anywhere in the world for up
        # to A MONTH` is the same holiday policy as `for up to 30 days`, and
        # this alternation had every spelling of the number except the
        # article. It cannot over-match, because a time unit has to follow
        # immediately: `for a role` matches nothing here.
        (?:\d+|an?|a\s+few|one|two|three|four|five|six)[\s-]*

        (?:calendar\s+)?(?:days?|weeks?|months?)
      # `[\s-]*` rather than `\s*`, for the same reason and found the same
      # day: `work from anywhere in the world 4-WEEKS each year` is
      # hyphenated, and `\d+\s*` cannot cross a hyphen. Three postings in
      # one corpus were eligible on a four-week workation because of one
      # character.
      | \b\d+[\s-]*(?:days?|weeks?|months?)\s+(?:per|a|each)\s+(?:year|month|quarter)
      | \bper\s+(?:year|calendar\s+year)\b.*\b(?:days?|weeks?)\b
      | \b\d+[\s-]*(?:days?|weeks?|months?)\s+remote\s+work
      | \b\d+\s+(?:days?|weeks?|months?)\s+of\s+[^.\n]{0,35}\b(?:passport|remote)\s+working
      | \bdurante\s+(?:un\s+periodo\s+de\s+)?\d+\s*(?:dias?|semanas?|meses)\b
      # -- Portuguese ---------------------------------------------------
      | \b(?:por|at[eé])\s+\d+\s*(?:dias?|semanas?|meses)
      | \b\d+\s*(?:dias?|semanas?)\s+por\s+ano
    )""",
    re.VERBOSE | re.IGNORECASE,
)


#: A sentence that names a POLICY DOCUMENT rather than a place of work.
#:
#: Found on the real corpus, 2026-09-09, on a Dallas posting that came out
#: VERIFIED_ELIGIBLE for a candidate in Brazil:
#:
#:     "For information on our data privacy policies, see Privacy, CA Candidate
#:      Privacy, and Brazil Transparency Report"
#:
#: The V1.2 pass caught the same shape once already -- a list of privacy
#: statutes, "LGPD (Brazil), PIPEDA (Canada)", on a Remote-USA role -- and
#: fixed it with `HIRING_INTENT`. This sentence clears that anchor because a
#: privacy notice for CANDIDATES genuinely is about candidates. What it is not
#: about is where anybody may be employed.
POLICY_DOCUMENT = re.compile(
    r"""(?:
        \bprivacy\s+(?:polic\w+|notice|statement)
      | \b(?:candidate|applicant)\s+privacy
      | \btransparency\s+report
      | \bterms\s+of\s+(?:use|service)
      | \bcookie\s+polic\w+
      | \b(?:lgpd|gdpr|ccpa|pipeda)\b
      # -- Portuguese ---------------------------------------------------
      | \bpol[ií]tica\s+de\s+privacidade
      | \baviso\s+de\s+privacidade
    )""",
    re.VERBOSE | re.IGNORECASE,
)

#: A sentence that names a MARKET a role sells into, not a place it hires from.
#:
#: Three postings, one employer, all VERIFIED_ELIGIBLE on 2026-09-09:
#:
#:     "Based in New York, Chicago, Austin, or San Francisco - Reports to SVP,
#:      Partnerships - Scope: AMER + LATAM"
#:
#: The sentence says plainly that the person must be based in one of four
#: United States cities. `LATAM` is the TERRITORY the partnership covers, and
#: the same conflation already cost this product `Commercial Account Executive
#: - LATAM`, based in Denver, which is why the TITLE was removed from this gate
#: entirely. The title is not the only place a market name appears.
MARKET_SCOPE = re.compile(
    r"""(?:
        \bscope\s*[:=]
      | \b(?:sales\s+)?territor(?:y|ies)\b
      | \b(?:the\s+)?(?:market|region)s?\s+(?:you|they|we)\s+(?:will\s+)?(?:cover|serve|own)
      | \b(?:cover|covering|responsible\s+for)\s+the\s+\w+\s+(?:market|region)
      | \breports?\s+to\b.*\bscope\b
      # -- Portuguese ---------------------------------------------------
      | \b(?:atender|atendendo|cobrir|cobrindo)\s+(?:o|a|os|as)\s+mercado
    )""",
    re.VERBOSE | re.IGNORECASE,
)

#: A sentence about where OTHER PEOPLE are, not where the reader may be.
#:
#: Three more postings, one employer, all eligible on the strength of:
#:
#:     "You will work closely with cross-functional teams across the
#:      organization, based in San Francisco, New York, Seattle, Vancouver,
#:      Brazil, and ..."
#:
#: `based in` is in `HIRING_INTENT` for a good reason -- "this role is based in
#: Sao Paulo" is exactly the sentence the gate wants -- and here its SUBJECT is
#: the teams rather than the candidate. Naming a country your future colleagues
#: sit in is not an offer to employ you there.
#:
#: Deliberately narrow: it requires the collective noun to appear BEFORE the
#: locating phrase in the same sentence, so "you will be based in Brazil,
#: working with teams in London" is untouched.
# THE PRODUCT LETS ITS CUSTOMERS HIRE ANYWHERE, alternative (c) below. An HR
# platform's own `description.company` boilerplate, on 283 of 348 of its
# postings on 2026-09-11: "With <the product>, you can hire a new employee
# anywhere in the world and set up their payroll". `you` is the customer,
# `hire` clears HIRING_INTENT, and an office role in Bangalore read as
# worldwide. The vendor is not named here because the gate must never branch
# on one, and a test reads this file's executable text for vendor names; the
# evidence with the name is in ADR-0023.
OTHER_PEOPLE = re.compile(
    r"""(?:
        # (a) A collective the reader works WITH, and where it sits.
        #
        # The preposition is load-bearing and a test is why. "We work remotely
        # across the US, Europe, LatAm" is a sentence V1.2 deliberately allows
        # through -- it is about how the work happens and the reader is inside
        # the "we" -- and an earlier version of this pattern refused it. The
        # difference is the object: `work closely WITH teams ... based in
        # Brazil` attaches the region to somebody else while saying nothing
        # about where the reader may be.
        \b(?:with|alongside)\s+(?:our\s+|the\s+)?(?:cross-functional\s+)?
        (?:teams?|colleagues|coworkers|co-workers|employees|staff|peers)
        \b[^.]{0,70}?\b(?:based\s+in|located\s+in|across|spread\s+across)\b
        # (b) An OFFICE, which invariant 3 already names: a company with a desk
        # in Sao Paulo is not a company that will employ you there.
      | \b(?:offices?|hubs?)\b[^.]{0,40}?\b(?:in|across)\b
      | \b(?:we|the\s+company)\s+(?:has|have)\s+(?:an?\s+)?(?:offices?|hubs?)\b
      | \b(?:customers?|users?)\s+(?:to\s+)?work\s+from\s+anywhere\b
      | \b(?:tools?|software|platform|product)\s+(?:letting|allowing|enabling)\s+
        (?:you|users?|customers?)\s+(?:to\s+)?work\s+from\s+anywhere\b
        # (c) The PRODUCT lets its CUSTOMERS hire anywhere. See the note
        # above this pattern for the sentence that earned it.
      | \b(?:with|using|through|on)\s+[A-Z][\w.-]*,?\s+
        (?:you|companies|clients|businesses|teams)\s+can\s+(?:hire|employ|onboard|pay)\b
      | \b(?:our\s+(?:clients|customers)|companies|businesses|employers)\s+(?:can|to)\s+hire\b
      | \b(?:enables?|enabling|helps?|helping|allows?|allowing|lets?|letting|empowers?)\s+
        (?:companies|businesses|employers|customers|clients|teams|organi[sz]ations)\s+
        (?:to\s+)?(?:hire|employ|onboard|pay)\b
        # -- Portuguese ---------------------------------------------------
      | \bcom\s+(?:o\s+|a\s+|os\s+|as\s+)?(?:times?|equipes?|colegas)\b[^.]{0,70}?
        \b(?:em|sediad\w+\s+em|espalhad\w+\s+(?:por|pel\w+))\b
      | \bescrit[óo]rios?\b[^.]{0,40}?\bem\b
    )""",
    re.VERBOSE | re.IGNORECASE,
)

#: A sentence where the region is part of the JOB'S DUTIES.
#:
#: Two postings, 2026-09-09:
#:
#:     "Knowledge of regional tax, employment, and travel regulations
#:      (especially LATAM)"
#:     "In this role, you will support the payrolls either directly through
#:      Workday and ADP or indirectly via a PEO"
#:
#: The first clears `HIRING_INTENT` on the word `employment` and the second on
#: `this role`. In both, the region is the OBJECT of the work rather than a
#: condition of taking it: somebody in Boston can be required to know Brazilian
#: payroll law, and being required to know it is not permission to live there.
A_DUTY = re.compile(
    r"""(?:
        \bknowledge\s+of\b
      | \bexperience\s+(?:with|in)\s+\w+\s+(?:regulations?|tax|payroll|law)
      | \b(?:regulations?|compliance|payroll|tax|legislation)\b[^.]{0,40}\b(?:for|across|in)\b
      | \bsupport\s+the\s+payrolls?\b
      | \bhiring\s+objectives?\b
      # -- Portuguese ---------------------------------------------------
      | \bconhecimento\s+(?:de|em)\b[^.]{0,30}\b(?:legisla[çc][ãa]o|tributa|folha)
    )""",
    re.VERBOSE | re.IGNORECASE,
)

#: Every narrow negation, in one place so a caller asks one question.
#:
#: They are separate constants rather than one alternation because each is a
#: different objection with a different posting behind it, and a regex nobody
#: can attribute to a real defect is one nobody can safely change.
NOT_A_HIRING_STATEMENT = (POLICY_DOCUMENT, MARKET_SCOPE, OTHER_PEOPLE, A_DUTY)


def _is_about_something_else(hit: SignalHit) -> bool:
    """Whether this sentence mentions a region for a reason that is not hiring.

    The third question asked of one sentence, beside "is it about hiring at
    all" and "does it bound its own offer in time". All three are NEGATIONS of
    a positive match, and all three are narrow on purpose: the risk here is not
    symmetric. A false PASS shows a candidate a job she cannot take and wastes
    an afternoon; a false FAIL hides one she can and costs her the job.

    So each pattern names a shape found on the real corpus rather than a
    category somebody imagined, and each is written to require its own subject
    rather than a bare keyword.
    """
    quote = hit.quote
    return any(pattern.search(quote) is not None for pattern in NOT_A_HIRING_STATEMENT)


def _is_a_workation_benefit(hit: SignalHit) -> bool:
    """Whether this sentence bounds its own offer in time.

    One question of one sentence, exactly as `_states_a_hiring_scope` asks
    one. The two are kept apart because they are different objections: that
    sentence is not about hiring, versus that sentence is about hiring for a
    fortnight.
    """
    return WORKATION_LIMIT.search(hit.quote.lower()) is not None


def _states_a_hiring_scope(hit: SignalHit) -> bool:
    """Is the sentence the phrase sits in ACTUALLY about where this job hires?

    `SignalHit.quote` is already the sentence, cut by `sentence_at`, so this
    asks one question of it and nothing else.
    """
    return HIRING_INTENT.search(hit.quote.lower()) is not None


def _positive_scope(
    config: SearchConfig,
    title_field: FoldedText,
    description_field: FoldedText,
) -> tuple[str, SignalHit] | None:
    """A phrase that positively OPENS the geography gate, for a scope we accept.

    Only scopes listed in `eligible_scopes` are consulted. A posting that
    explicitly hires across EMEA and nowhere else has stated a scope, and that
    statement is not evidence for a Brazil-based candidate.

    **The title is not consulted.** It used to be, and `Commercial Account
    Executive - LATAM`, based in Denver, was `VERIFIED_ELIGIBLE` because of it.
    A region in a title names the MARKET the role sells into, not the place the
    employer will hire somebody to live -- and the accepted invariant is
    already explicit that a title may name a role and inform retrieval but may
    never establish compatibility. Eligibility is compatibility.

    **The description is consulted only where it is making the claim.** Every
    candidate sentence must pass `HIRING_INTENT`; see the note above it for the
    measurement and for three of the sentences that used to qualify.

    **And it must not bound its own offer in time.** "Work from anywhere in the
    world for 30 days per year" passes every test above and is a holiday
    policy. See `WORKATION_LIMIT`.

    Nothing here can FAIL a posting. A scope phrase that does not survive these
    rules leaves the gate UNRESOLVED, which is what silence has always meant
    here, and an UNRESOLVED posting stays visible.
    """
    del title_field

    eligible = set(config.eligibility.eligible_scopes)
    for scope, patterns in config.eligibility.positive_scope_patterns.items():
        if scope not in eligible:
            continue
        blocker = Blocker(
            id=scope, label=f"Hiring scope {scope}", gate="geography", patterns=patterns
        )
        hit = _find_blocker_hit(blocker, "description", description_field, config)
        if (
            hit is not None
            and _states_a_hiring_scope(hit)
            and not _is_a_workation_benefit(hit)
            and not _is_about_something_else(hit)
        ):
            return scope, hit
    return None


# These are statements about where the worker may live, not arbitrary country
# mentions. Parse the qualifier before considering the broad phrase positive.
_SCOPE_CLAUSE = re.compile(
    r"\b(?P<broad>work\s+from\s+anywhere|(?:fully\s+)?remote\s+worldwide|"
    r"we\s+hire\s+(?:globally|worldwide))\b"
    r"(?P<qualifier>\s*(?:,\s*)?(?:but\s+)?(?:only\s+)?"
    r"(?:in|from|within|across|throughout|except|excluding|restricted\s+to|limited\s+to)\b"
    r"(?:(?:[A-Za-z]\.){2,}|[^\n.!?;])*)?"
    r"(?:\s*\((?P<parenthetical>[^)\n]*\bonly)\))?"
    r"|\b(?:applicants?|candidates?|employees?|you)\s+"
    r"(?:must|need\s+to|are\s+required\s+to)\s+"
    r"(?:live|reside|be\s+based)\s+in\s+"
    r"(?P<residence>(?:(?:[A-Za-z]\.){2,}|[^\n.!?;])+)",
    re.IGNORECASE,
)

#: A run of text a country list can occupy: up to a sentence boundary, never
#: across a line, and never through a colon or bracket that would start the
#: next construct. `U.S.A.` survives because the dotted form is matched first.
_LIST = r"(?P<list>(?:(?:[A-Za-z]\.){2,}|[^\n.!?;:()])+)"

#: THE ALLOWLIST FORMS. Every one of these is an employer saying which places
#: it will hire from, in words that name the places. They are the constructs
#: the class of false eligibility this module was corrected for on 2026-09-11
#: used: a posting said "LATAM" in a hiring sentence, opened the gate for a
#: candidate in Brazil, and two lines later said "Eligible countries:
#: Argentina, Chile, Colombia, Mexico". The region was read and the list was
#: not, because nothing here knew what a list looked like.
#:
#: None of these needs `HIRING_INTENT`: "open to candidates based in", "we can
#: hire in" and "eligible countries:" are hiring statements by their shape.
_ALLOWLIST_CLAUSES: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"\b(?:eligible|hiring|approved|supported|permitted|allowed|available)\s+"
        r"(?:countries|locations|regions|markets)\s*[:\-]\s*" + _LIST,
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:open|available)\s+(?:only\s+)?to\s+"
        r"(?:candidates|applicants|people|talent|those|individuals|anyone)\s+"
        r"(?:based|located|residing|living|who\s+(?:live|reside|are\s+based|are\s+located))"
        r"\s+in\s+" + _LIST,
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:we|the\s+company|this\s+(?:role|position))\s+"
        r"(?:can|are\s+able\s+to|is\s+able\s+to|will|may|currently)\s+(?:only\s+)?"
        r"(?:hire|employ|consider|accept)\s+"
        r"(?:candidates\s+|applicants\s+|people\s+|talent\s+|team\s+members\s+)?(?:only\s+)?"
        r"(?:in|from|within|based\s+in|located\s+in|residing\s+in)\s+" + _LIST,
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:must|need\s+to|should|required\s+to)\s+(?:be\s+)?"
        r"(?:based|located|residing|reside|live|living|physically\s+located)\s+in\s+" + _LIST,
        re.IGNORECASE,
    ),
)

#: A REGION LABEL WITH ITS OWN LIST: `LATAM (Argentina, Colombia, Mexico)`,
#: `Latin America: Brazil, Argentina`, `Europe - Germany, France`. The label
#: alone is regional hiring language; the list after it is the employer saying
#: which of the region's countries it meant. This one DOES require the sentence
#: to be about hiring, because `EMEA: Berlin office` is a list of desks.
_REGION_LIST = re.compile(
    r"\b(?P<label>latam|latin\s+america|south\s+america|the\s+americas|americas|europe|emea|"
    r"apac|asia\s+pacific|north\s+america)\b\s*(?:only\s*)?"
    r"(?:\((?P<plist>[^)\n]{2,200})\)|[:\-]\s*(?P<dlist>[^\n.!?;:()]{2,200}))",
    re.IGNORECASE,
)

#: A REGION WITH "ONLY": `Europe only`, `remote within the EU only`, `only in
#: North America`. The word is what makes the region exhaustive rather than
#: illustrative, and an exhaustive region that does not contain the
#: candidate's country refuses -- "Germany + Remote EU only" is not a job
#: somebody in Brazil can take.
_REGION_ONLY = re.compile(
    r"\b(?:(?:the\s+)?(?P<label>europe|eu|emea|apac|asia\s+pacific|north\s+america|latam|"
    r"latin\s+america|south\s+america|the\s+americas|americas|uk|usa|us|canada)"
    r"(?:\s+based|\s+timezones?)?\s+only\b"
    r"|\b(?:only|exclusively|solely)\s+(?:in|from|within|across|to)\s+(?:the\s+)?"
    r"(?P<label2>europe|eu|emea|apac|asia\s+pacific|north\s+america|latam|latin\s+america|"
    r"south\s+america|the\s+americas|americas|uk|usa|us|canada|united\s+states)\b)",
    re.IGNORECASE,
)

#: AN EXCLUSION: `except Brazil`, `we cannot hire in Argentina`. Read for the
#: one thing it can settle -- the candidate's own country named as excluded --
#: and never inverted into a positive scope, because "everywhere except X" is
#: not a list of places anybody said yes to.
_EXCLUSION = re.compile(
    r"\b(?:except(?:\s+for)?|excluding|other\s+than|apart\s+from|with\s+the\s+exception\s+of|"
    r"(?:we\s+)?(?:cannot|can\s*not|can't|are\s+unable\s+to|do\s+not|don't)\s+"
    r"(?:currently\s+)?(?:hire|employ)\s+(?:in|from))\s+" + _LIST,
    re.IGNORECASE,
)

#: Words that turn a list into EXAMPLES. `LATAM, including Argentina and
#: Colombia` has not said Brazil is out; it has named two of the places it
#: means. Directive E: absence from a non-exhaustive list is not exclusion.
_NON_EXHAUSTIVE = re.compile(
    r"\b(?:including|includes|include|such\s+as|for\s+example|e\.?\s?g\.?|like|among\s+others|"
    r"and\s+more|and\s+others|but\s+not\s+limited\s+to|etc|incluindo|por\s+exemplo|como|"
    r"entre\s+outros)\b",
    re.IGNORECASE,
)

#: A list that says "anywhere" in one of the ways a list can.
_ANYWHERE = re.compile(
    r"^\s*(?:anywhere|any\s+(?:country|location|where)|the\s+world|worldwide|globally|"
    r"any\s+country\s+in\s+the\s+world)\b",
    re.IGNORECASE,
)

_LIST_SPLIT = re.compile(r"\s*(?:,|;|/|\||&|\band\b|\bor\b|\be\b|\bou\b)\s*", re.IGNORECASE)


def _resolve_list(text: str) -> ResolvedPlace:
    """A country list as the employer wrote it, resolved part by part.

    Each part is resolved on its own, so `Argentina and Colombia` yields two
    countries rather than one unrecognised token, and a region word in the
    list (`LATAM and Canada`) is kept as a STATED region. Implied regions are
    deliberately not carried: they are the widening this correction removes.
    """
    if _ANYWHERE.match(text):
        return ResolvedPlace(regions=("WORLDWIDE",), stated_regions=("WORLDWIDE",))
    countries: set[str] = set()
    stated: set[str] = set()
    parts = [part for part in _LIST_SPLIT.split(text) if part and part.strip()]
    for part in [text, *parts]:
        place = resolve_place(re.sub(r"^\s*(?:the|a|o|os|as)\s+", "", part, flags=re.IGNORECASE))
        countries.update(place.countries)
        stated.update(place.stated_regions)
    return ResolvedPlace(
        countries=tuple(sorted(countries)),
        regions=tuple(sorted(stated)),
        stated_regions=tuple(sorted(stated)),
    )


def _candidate_countries(config: SearchConfig) -> set[str]:
    """Every country this candidate may be hired from: the configured list
    PLUS the country she lives in. `candidate_country` is a fact about the
    person and it participates wherever the list does, so a settings file
    with `eligible_countries: []` beside `candidate_country: BR` still knows
    she is in Brazil. Three readers used to build this set three different
    ways; two of them forgot the second half."""
    countries = {c.upper() for c in config.eligibility.eligible_countries}
    if config.eligibility.candidate_country:
        countries.add(config.eligibility.candidate_country.upper())
    return countries


def _candidate_geography_known(config: SearchConfig) -> bool:
    """Has this candidate told the product anything about where she may work?

    A fresh install has not: `candidate_country`, `eligible_countries` and
    `eligible_scopes` are all empty in the starter. Against nothing, no place
    is compatible and no place is incompatible, and the only honest verdict
    is UNRESOLVED. Measured 2026-09-18 before this guard existed: the demo
    corpus read 13 of 21 postings VERIFIED_NOT_ELIGIBLE and 0 eligible on a
    fresh install, because an empty set of permitted countries was read as
    "no country is permitted". Absence is never permission, and it is never
    a refusal either.
    """
    return bool(_candidate_countries(config) or config.eligibility.eligible_scopes)


def _scope_verdict(config: SearchConfig, place: ResolvedPlace, *, exhaustive: bool) -> bool | None:
    """Does a stated place admit this candidate? True, False, or None for unknown.

    THE PRECEDENCE, IN ORDER:

    1. A country list that NAMES one of the candidate's countries admits.
    2. A country list that does not, and is EXHAUSTIVE, refuses. `Eligible
       countries: Argentina, Chile, Colombia, Mexico` is the employer answering
       the question, and Brazil is not in the answer.
    3. A country list that does not, and is a list of EXAMPLES, decides
       nothing on its own and falls through to whatever region it was
       illustrating.
    4. A region the employer NAMED admits only when the candidate's settings
       accept it AND the region geographically contains one of the
       candidate's countries. Both. A configured scope that cannot contain
       where the candidate lives -- NORTH_AMERICA for somebody in Brazil --
       is a mistake in a settings file, and this is where the mistake is
       made harmless rather than where it becomes a job they cannot take.
    5. A region the employer named that contains none of the candidate's
       countries refuses: `Remote - Europe` is a stated scope, and it does
       not include Brazil.
    6. A region only IMPLIED by the countries in a list never admits. That
       implication is the whole defect.
    7. A region that DOES contain one of the candidate's countries, but that
       her settings do not accept, decides nothing. `Remote - Worldwide` for
       somebody in Brazil who ticked only LATAM is not a refusal: the region
       provably holds Brazil, so "does not include Brazil" would be false. It
       does not admit either, because rule 4 needs both halves. The same
       holds when she has configured no accepted scopes at all.
    8. A region whose membership the gazetteer cannot answer for her
       countries -- `region_contains` returning None -- decides nothing.
       Unknown membership is not a refusal.

    Nothing here reads `place.regions`; only `countries` and `stated_regions`.

    None when the candidate has configured no geography at all: nothing can
    be compatible or incompatible with nothing.
    """
    if not _candidate_geography_known(config):
        return None
    eligible_countries = _candidate_countries(config)
    eligible_scopes = {s.upper() for s in config.eligibility.eligible_scopes}

    countries = {c.upper() for c in place.countries}
    stated = {r.upper() for r in place.stated_regions}

    if countries & eligible_countries:
        return True
    # A region the employer NAMED beside the countries -- `LATAM and USA` --
    # is consulted before the list is allowed to refuse, because the region
    # is part of the same answer.
    membership = {
        region: [region_contains(region, country) for country in eligible_countries]
        for region in stated
    }
    containing = {region for region, answers in membership.items() if any(answers)}
    if containing & eligible_scopes:
        return True
    if countries and exhaustive:
        return False
    if stated:
        if containing:
            # Rule 7: the employer's region holds her country and her settings
            # do not accept it. Unknown, not refused.
            return None
        if any(answer is None for answers in membership.values() for answer in answers):
            # Rule 8: the gazetteer does not place one of her countries, so
            # nobody here knows whether the region holds it.
            return None
        return False
    return None


@dataclass(frozen=True)
class _ScopeReading:
    """One hiring-scope clause and how much weight it carries in a conflict."""

    outcome: GateOutcome
    #: An explicit list, residence requirement or exclusion, as opposed to a
    #: broad phrase with nothing bounding it. Directive precedence: explicit
    #: inclusion/exclusion > structured field > exhaustive list > regional
    #: language > generic remote language.
    explicit: bool


def _clause_hit(field: FoldedText, start: int, end: int, config: SearchConfig) -> SignalHit | None:
    """The sentence around a clause, or None when it should not be read at all."""
    folded_start = bisect_left(field.offsets, start)
    if is_negated_before(field, folded_start, config.negation):
        return None
    context = sentence_at(field.original, start, end, max_chars=len(field.original) + 1)
    hit = SignalHit(
        signal_id="bounded_hiring_scope",
        label="Stated hiring scope",
        field_name="description",
        pattern=field.original[start:end],
        quote=context,
        char_start=start,
        char_end=end,
    )
    if _is_a_workation_benefit(hit) or _is_about_something_else(hit):
        return None
    return hit


def _outcome_for(
    config: SearchConfig,
    *,
    verdict: bool | None,
    stated: str,
    quote: str,
    start: int,
    end: int,
) -> GateOutcome:
    result = (
        GateResult.UNRESOLVED
        if verdict is None
        else GateResult.PASS
        if verdict
        else GateResult.FAIL
    )
    reason = f"The hiring statement is limited to {stated}. " + (
        "That scope could not be resolved."
        if verdict is None
        else f"It {'includes' if verdict else 'does not include'} "
        f"{config.eligibility.candidate_country_label}."
    )
    return GateOutcome(
        gate="geography",
        result=result,
        reason=reason,
        quote=quote,
        char_start=start,
        char_end=end,
    )


def _is_exhaustive(field: FoldedText, list_start: int, list_text: str) -> bool:
    window = field.original[max(0, list_start - 40) : list_start] + list_text
    return _NON_EXHAUSTIVE.search(window) is None


def _scope_clause_readings(config: SearchConfig, field: FoldedText) -> list[_ScopeReading]:
    """Read bounded hiring clauses without promoting a prefix to worldwide.

    Unknown qualifiers stay unknown. Country lists are compared as countries,
    never widened to their implied regions (US/CA is not all the Americas).
    Quotes and offsets cover the qualifier as well as the broad phrase.

    Four families of clause, each read for what it can settle:

    * a BROAD phrase with an optional qualifier (`work from anywhere in LATAM`);
    * an ALLOWLIST (`open to candidates based in Argentina and Chile`);
    * a REGION WITH ITS OWN LIST (`LATAM (Argentina, Colombia)`);
    * an EXCLUSION (`except Brazil`).
    """
    readings: list[_ScopeReading] = []
    text = field.original

    for match in _SCOPE_CLAUSE.finditer(text):
        start, end = match.span()
        if _clause_hit(field, start, end, config) is None:
            continue
        quote = text[start:end]
        qualifier = match.group("qualifier")
        stated = match.group("residence")
        if match.group("parenthetical"):
            stated = re.sub(r"\s+only$", "", match.group("parenthetical"), flags=re.IGNORECASE)
        if qualifier:
            stated = re.sub(
                r"^\s*,?\s*(?:but\s+)?(?:only\s+)?"
                r"(?:in|from|within|across|throughout|restricted\s+to|limited\s+to)\s+",
                "",
                qualifier,
                flags=re.IGNORECASE,
            ).strip()
        explicit = bool(qualifier or match.group("residence") or match.group("parenthetical"))
        if stated is None or stated.casefold() in {"the world", "world"}:
            place = ResolvedPlace(regions=("WORLDWIDE",), stated_regions=("WORLDWIDE",))
            stated = "worldwide"
        elif re.search(r"\b(?:except|excluding|unless)\b", stated, re.IGNORECASE):
            # Exclusion complements need their own model; never invert a list
            # of excluded countries into an invented positive hiring scope.
            # `_EXCLUSION` below reads the same words for the one thing they
            # can settle.
            continue
        else:
            place = _resolve_list(stated)
        verdict = _scope_verdict(config, place, exhaustive=True)
        readings.append(
            _ScopeReading(
                _outcome_for(
                    config, verdict=verdict, stated=stated, quote=quote, start=start, end=end
                ),
                explicit=explicit,
            )
        )

    for pattern in _ALLOWLIST_CLAUSES:
        for match in pattern.finditer(text):
            start, end = match.span()
            if _clause_hit(field, start, end, config) is None:
                continue
            listed = match.group("list").strip()
            place = _resolve_list(listed)
            if not place.countries and not place.stated_regions:
                readings.append(
                    _ScopeReading(
                        _outcome_for(
                            config,
                            verdict=None,
                            stated=listed,
                            quote=text[start:end],
                            start=start,
                            end=end,
                        ),
                        explicit=True,
                    )
                )
                continue
            exhaustive = _is_exhaustive(field, match.start("list"), listed)
            verdict = _scope_verdict(config, place, exhaustive=exhaustive)
            if verdict is None:
                # Examples that happen not to name the candidate's country.
                # They narrow nothing; whatever region they illustrate decides.
                continue
            readings.append(
                _ScopeReading(
                    _outcome_for(
                        config,
                        verdict=verdict,
                        stated=listed,
                        quote=text[start:end],
                        start=start,
                        end=end,
                    ),
                    explicit=True,
                )
            )

    for match in _REGION_LIST.finditer(text):
        start, end = match.span()
        hit = _clause_hit(field, start, end, config)
        if hit is None or not _states_a_hiring_scope(hit):
            continue
        listed = (match.group("plist") or match.group("dlist") or "").strip()
        list_start = match.start("plist") if match.group("plist") else match.start("dlist")
        place = _resolve_list(listed)
        if not place.countries:
            # `EMEA: Berlin office` resolves to a country; `LATAM: our fastest
            # growing market` resolves to nothing and is not a list.
            continue
        exhaustive = _is_exhaustive(field, list_start, listed)
        if not exhaustive:
            # `LATAM (e.g. Argentina, Colombia)`: the label is the answer and
            # the list illustrates it. An exhaustive list DEFINES the label,
            # so the label must not be allowed to admit what its own
            # definition left out.
            label = resolve_place(match.group("label"))
            named = tuple(sorted(set(place.stated_regions) | set(label.stated_regions)))
            place = ResolvedPlace(countries=place.countries, regions=named, stated_regions=named)
        verdict = _scope_verdict(config, place, exhaustive=exhaustive)
        if verdict is None:
            continue
        readings.append(
            _ScopeReading(
                _outcome_for(
                    config,
                    verdict=verdict,
                    stated=text[start:end].strip(),
                    quote=text[start:end],
                    start=start,
                    end=end,
                ),
                explicit=exhaustive,
            )
        )

    for match in _REGION_ONLY.finditer(text):
        start, end = match.span()
        hit = _clause_hit(field, start, end, config)
        if hit is None or not _states_a_hiring_scope(hit):
            continue
        only = match.group("label") or match.group("label2") or ""
        place = _resolve_list(only)
        verdict = _scope_verdict(config, place, exhaustive=True)
        if verdict is None:
            continue
        readings.append(
            _ScopeReading(
                _outcome_for(
                    config,
                    verdict=verdict,
                    stated=only.strip(),
                    quote=text[start:end],
                    start=start,
                    end=end,
                ),
                explicit=True,
            )
        )

    for match in _EXCLUSION.finditer(text):
        start, end = match.span()
        if _clause_hit(field, start, end, config) is None:
            continue
        listed = match.group("list").strip()
        place = _resolve_list(listed)
        excluded = {c.upper() for c in place.countries}
        mine = {c.upper() for c in config.eligibility.eligible_countries}
        if config.eligibility.candidate_country:
            mine.add(config.eligibility.candidate_country.upper())
        if not excluded & mine:
            continue
        readings.append(
            _ScopeReading(
                GateOutcome(
                    gate="geography",
                    result=GateResult.FAIL,
                    reason=(
                        f"The posting excludes {listed}, which names "
                        f"{config.eligibility.candidate_country_label}."
                    ),
                    quote=text[start:end],
                    char_start=start,
                    char_end=end,
                ),
                explicit=True,
            )
        )

    return readings


def _scope_clause_outcomes(config: SearchConfig, field: FoldedText) -> list[GateOutcome]:
    """The clause outcomes alone, for callers that do not rank them."""
    return [reading.outcome for reading in _scope_clause_readings(config, field)]


def _reconcile_scope_clauses(
    readings: list[_ScopeReading],
    previous: GateOutcome | None,
) -> GateOutcome | None:
    """Fold clause readings into one answer without ever broadening it.

    * A blocker FAIL stands whatever the clauses say.
    * An explicit refusal (allowlist without the candidate's country, a
      residence requirement elsewhere, an exclusion naming it) beats regional
      language and generic remote language: "Remote - Latin America" followed
      by "Eligible countries: Argentina, Chile" is NOT eligible for Brazil.
    * Two EXPLICIT statements that disagree -- one admits, one refuses -- are
      a contradiction, and a contradiction is UNRESOLVED.
    * A structured answer from the board's own field that a prose clause
      contradicts is likewise UNRESOLVED: the field is not overruled by prose
      and the prose is not ignored.
    * Anything unresolved beside a pass is unresolved.
    """
    if previous is not None and previous.result is GateResult.FAIL and previous.blocker_id:
        return previous
    if not readings:
        return previous

    outcomes = [reading.outcome for reading in readings]
    structured = (
        previous
        if previous is not None
        and previous.quote is None
        and previous.result is not GateResult.FAIL
        else None
    )
    explicit_fail = any(r.explicit and r.outcome.result is GateResult.FAIL for r in readings)
    explicit_pass = any(r.explicit and r.outcome.result is GateResult.PASS for r in readings)
    any_pass = any(o.result is GateResult.PASS for o in outcomes)
    any_unresolved = any(o.result is GateResult.UNRESOLVED for o in outcomes)
    first_fail = next((o for o in outcomes if o.result is GateResult.FAIL), None)
    if previous is not None and previous.result is GateResult.FAIL and first_fail is None:
        first_fail = previous

    contradiction = GateOutcome(
        gate="geography",
        result=GateResult.UNRESOLVED,
        reason="Hiring-scope statements disagree or include an unresolved restriction. "
        "Confirm the permitted work location before treating this posting as eligible.",
    )
    if explicit_fail and explicit_pass:
        return contradiction
    if first_fail is not None:
        if structured is not None and structured.result is GateResult.PASS:
            return contradiction
        return first_fail
    if any_unresolved:
        if structured is not None and structured.result is GateResult.UNRESOLVED:
            return structured
        if any_pass or structured is not None:
            return contradiction
        return next(o for o in outcomes if o.result is GateResult.UNRESOLVED)
    # Every clause passes.
    if structured is not None and structured.result is GateResult.UNRESOLVED:
        # The board's own field could not be resolved; prose does not replace
        # it. Same rule `evaluate_gates` applies to a declared scope.
        return structured
    return outcomes[0]


def _declared_scope(config: SearchConfig, declared: str) -> tuple[str, SignalHit] | None:
    """A scope read from a field whose whole job is to answer the question.

    Kept apart from `_positive_scope` above on purpose, and the difference is
    not a shortcut. That function reads PROSE, where a region may be a market,
    a customer base or a privacy statute, so it demands a sentence about
    hiring. This one reads a FIELD the board fills in by asking the employer
    where it can hire; `Anywhere in the World` and `USA Only` are the answers,
    and there is no sentence to anchor because there is no sentence.

    Only providers that publish such a field reach here. An office address must
    never arrive at this function: a place a company has a desk is not a
    statement about who it may employ, and invariant 3 exists because
    conflating them is the most expensive mistake this system can make.
    """
    field = fold_field(declared)
    eligible = set(config.eligibility.eligible_scopes)
    for scope, patterns in config.eligibility.positive_scope_patterns.items():
        if scope not in eligible:
            continue
        blocker = Blocker(
            id=scope, label=f"Hiring scope {scope}", gate="geography", patterns=patterns
        )
        hit = _find_blocker_hit(blocker, "hiring_scope", field, config)
        if hit is not None:
            return scope, hit
    return None


#: What a structured geography reading concluded, and why.
#:
#: Three answers rather than two, for the reason the whole gate has three: a
#: location this configuration cannot place is not a refusal.
@dataclass(frozen=True)
class StructuredGeography:
    """The board's own location fields, read against the candidate's own.

    **There is no `quote`, and that is ADR-0002 being obeyed rather than
    worked around.** Evidence there is a QUOTE THAT EXISTS: a contiguous
    substring of the posting text, verified. A structured location is not in
    the posting text at all -- it is a form field beside it -- so putting
    `Remote U.S.` in `quote` made `GateOutcome` assert that the body said
    something it never said. `tests/integration/test_demo_acceptance.py`
    refused it, correctly, on the first run.

    The employer's own words still reach the reader: they are IN the reason,
    which is a sentence this product composed and is honest about composing.
    """

    result: GateResult
    reason: str


def _place_names(codes: set[str]) -> str:
    """Country and region CODES as the names a person would recognise.

    `config/places.yaml` already carries the display names -- they are what the
    filter chips render -- so this reads them rather than inventing a second
    spelling of `NORTH_AMERICA`.
    """
    from career_agent.match.places import display_names

    countries, regions = display_names()
    named = [countries.get(code) or regions.get(code) or code for code in sorted(codes)]
    return ", ".join(named)


def structured_geography(
    config: SearchConfig,
    workplace_type: str | None,
    place: ResolvedPlace,
    location_raw: str | None,
) -> StructuredGeography | None:
    """What a board's structured location says about whether SHE could take it.

    Returns None when the board published nothing to read, which leaves the
    gate exactly where it was for every source that states no workplace type.

    WHY ONE COLUMN MEANS TWO THINGS
    -------------------------------
    `location_raw` on an ONSITE or HYBRID posting is an OFFICE. Invariant 3
    exists because reading an office as a hiring scope is the most expensive
    mistake this system makes -- a company with a desk in Sao Paulo is not a
    company that will employ you there.

    `location_raw` on a REMOTE posting cannot be an office, because the role
    has none. `Remote U.S.` is the employer stating WHERE THE REMOTE ROLE IS
    OPEN, and reading it as anything else is how a posting restricted to one
    country reached the top of a list for a candidate in another.

    So the same column is read two ways, and the discriminator is the
    employer's own structured answer rather than a guess about the string.

    WHAT EACH READING CONCLUDES
    ---------------------------
    **REMOTE with a resolvable location** is a hiring scope. It PASSES when it
    overlaps what this candidate may be hired from and FAILS when it does not.

    **ONSITE or HYBRID with a resolvable location** is a place she would have
    to be. It FAILS when that country is not one she can work from -- a hybrid
    role in Gurugram is not a role somebody in Brazil can take, and calling it
    unresolved is a politeness that wastes her afternoon.

    **Anything that resolves to nothing** is UNRESOLVED. `Jabalpur` stays
    unresolved, and so does a bare `Remote`. The risk is not symmetric: a false
    PASS shows a job she cannot take, a false FAIL hides one she can.

    CANDIDATE-SPECIFIC, NEVER HARD-CODED
    ------------------------------------
    Everything it compares against comes from `config.eligibility`: her
    countries and her accepted scopes. A candidate configured for the United
    States reads `Remote U.S.` as a PASS through the same code path.
    """
    if not workplace_type:
        return None

    if not _candidate_geography_known(config):
        # Nothing to compare against. A fresh install used to read every
        # located posting as VERIFIED_NOT_ELIGIBLE here, before the person
        # had typed a country. The board's field is real; the answer is
        # UNRESOLVED, and it stays where the gate leaves it.
        return None

    eligible_countries = _candidate_countries(config)
    countries = {c.upper() for c in place.countries}
    regions = {r.upper() for r in place.regions}

    if not countries and not regions:
        # A workplace type with no placeable location. `Remote` on its own is
        # the commonest posting in the corpus and says nothing about hiring.
        return None

    # **The employer's own words where there are any, and readable names where
    # there are not.** A reason reading "open in NORTH_AMERICA, US" is this
    # system's vocabulary on screen, which
    # `tests/browser/test_plain_language.py` is right to refuse: a word the
    # SYSTEM chose is a defect where a word the EMPLOYER wrote is evidence.
    # Trailing punctuation stripped so `Remote U.S.` does not become
    # `Remote U.S..` when a sentence closes around it.
    stated = (location_raw or _place_names(countries | regions)).strip().rstrip(".")

    if workplace_type == "REMOTE":
        # THE LIST IS THE ANSWER. `Remote (United States | Canada)` names two
        # countries; the NORTH_AMERICA it implies is a fact about maps, and a
        # candidate whose settings accept that region has recorded something
        # that cannot be true of Brazil. `_scope_verdict` reads only what the
        # board NAMED and requires a named region to contain a country the
        # candidate may work from. Measured on 2026-09-11 before this change:
        # the top of the recommended list carried a posting "remote and says
        # Remote (United States | Canada), which includes Brazil".
        #
        # THREE ANSWERS, NOT TWO. `_scope_verdict` says True, False or None,
        # and None used to fall into the FAIL branch: a place the settings
        # could not judge was reported as a proven refusal, with a reason
        # that read "does not include Brazil" about Sao Paulo. Unknown is
        # UNRESOLVED, and it is left for the declared scope and the body to
        # settle exactly as a board with no structured field would be.
        verdict = _scope_verdict(config, place, exhaustive=True)
        if verdict is None:
            return None
        if verdict:
            return StructuredGeography(
                GateResult.PASS,
                (
                    f"This posting is remote and says {stated}, which includes "
                    f"{config.eligibility.candidate_country_label}."
                ),
            )
        return StructuredGeography(
            GateResult.FAIL,
            (
                f"This posting is remote and says {stated}. That does not include "
                f"{config.eligibility.candidate_country_label}."
            ),
        )

    if workplace_type in ("ONSITE", "HYBRID"):
        if countries & eligible_countries:
            # She could attend. Not a PASS on its own: being able to reach the
            # office says nothing about whether the employer may hire her, and
            # only a stated scope answers that.
            return None
        if not countries:
            # A region with no country, on a job requiring attendance. Too
            # vague to refuse somebody over.
            return None
        word = "on site" if workplace_type == "ONSITE" else "in a hybrid pattern"
        return StructuredGeography(
            GateResult.FAIL,
            (
                f"This posting is worked {word}, in {stated}, and you are in "
                f"{config.eligibility.candidate_country_label}."
            ),
        )

    return None


_REQUIRED_ATTENDANCE = re.compile(
    r"\b(?:requires?|required\s+to|must|expected\s+to)\b"
    r"[^.!?\n]{0,65}?\b(?:work|working|be\s+present|attend|come)\b"
    r"[^.!?\n]{0,100}?\b(?:office|headquarters|on[- ]site)\b"
)


def _required_attendance(
    config: SearchConfig, field: FoldedText, place: ResolvedPlace | None
) -> GateOutcome | None:
    """An explicit attendance obligation is independent of remote-work benefits.

    Country incompatibility proves failure. A missing/compatible country alone
    cannot prove the candidate can attend this office; that needs confirmation.
    No occupation, employer, country or preferred-title exceptions are involved.
    """
    for match in _REQUIRED_ATTENDANCE.finditer(field.folded):
        if "home office" in match.group() or is_negated_before(
            field, match.start(), config.negation
        ):
            continue
        start = field.offsets[match.start()]
        end = field.offsets[match.end() - 1] + 1
        countries = {c.upper() for c in place.countries} if place else set()
        compatible = countries & _candidate_countries(config)
        # An office in a country she cannot work from is a proven
        # incompatibility ONLY when she has said where she can work.
        incompatible = bool(countries) and not compatible and _candidate_geography_known(config)
        return GateOutcome(
            gate="worksite",
            result=GateResult.FAIL if incompatible else GateResult.UNRESOLVED,
            reason=(
                "The posting explicitly requires office attendance in a country outside "
                "the candidate's permitted work countries. Remote-work benefits do not "
                "override this requirement."
                if incompatible
                else "The posting explicitly requires office attendance. Confirm that the "
                "candidate can meet this requirement; remote-work benefits do not waive it."
            ),
            quote=sentence_at(field.original, start, end),
            char_start=start,
            char_end=end,
        )
    return None


def evaluate_gates(
    config: SearchConfig,
    observed: dict[str, ObservedSignal],
    title: str | FoldedText,
    description: str | FoldedText,
    sections: Sequence[Section] = (),
    declared_scope: str | None = None,
    workplace_type: str | None = None,
    place: ResolvedPlace | None = None,
    location_raw: str | None = None,
) -> tuple[GateOutcome, ...]:
    """Answer every configured gate from the posting text alone.

    `observed` is accepted and not read. The gates deliberately re-search the
    text for blocker phrases rather than reusing lexicon hits: a lexicon signal
    such as `onsite_required` measures how much of the ROLE something is, while
    a blocker is an unambiguous verbatim eligibility statement. Collapsing the
    two would let a bare keyword close a gate, which section 7 of the
    configuration exists to forbid.

    Re-searching is not re-folding. Give it the two `FoldedText` values the
    lexicon already used and `sections` is redundant; give it strings and they
    are folded here, with `sections` supplying the description's spans.

    `declared_scope` is a field in which the EMPLOYER answered "where can you
    hire?", supplied only by providers that publish one. We Work Remotely does:
    its `region` element carries `Anywhere in the World` for 89 of the 91
    postings collected and `USA Only` for one, and until this parameter existed
    the product read neither and left 83 of them UNRESOLVED. A second board
    now supplies one as an array of countries.

    **When it is present it DECIDES the geography gate**, and the description
    is not consulted at all. Not "checked first": consulted instead. A field
    written to answer the question outranks a sentence that mentions it, and
    falling back to the body when the field is not an eligible scope is how a
    posting restricted to the United States came out eligible for a candidate
    in Brazil. See the comment at the geography branch below.

    A caller must never pass an office address here. That is what invariant 3
    forbids, and the engine decides by asking the registry rather than by
    naming a provider.
    """
    del observed

    title_field = title if isinstance(title, FoldedText) else fold_field(title)
    description_field = (
        description if isinstance(description, FoldedText) else fold_field(description, sections)
    )
    scope_field = fold_field(declared_scope) if declared_scope else None
    outcomes: list[GateOutcome] = []

    for gate in GATE_ORDER:
        outcome: GateOutcome | None = None

        for blocker in config.eligibility.blockers:
            if blocker.gate != gate:
                continue
            # The declared field first. `USA Only` in a scope element is the
            # employer answering the question, not a phrase that happens to
            # appear somewhere in a body.
            hit = None
            if scope_field is not None:
                hit = _find_blocker_hit(blocker, "hiring_scope", scope_field, config)
            if hit is None:
                hit = _find_blocker_hit(blocker, "description", description_field, config)
            if hit is None:
                hit = _find_blocker_hit(blocker, "title", title_field, config)
            if hit is not None:
                outcome = GateOutcome(
                    gate=gate,
                    result=GateResult.FAIL,
                    reason=f"{blocker.label}. The posting states it in so many words.",
                    blocker_id=blocker.id,
                    quote=hit.quote,
                    char_start=hit.char_start,
                    char_end=hit.char_end,
                )
                break

        if outcome is None and gate == "worksite":
            outcome = _required_attendance(config, description_field, place)

        if outcome is None and gate == "geography" and place is not None:
            # **THE BOARD'S OWN FIELDS, BEFORE ANY PROSE.** A structured
            # location is the employer answering a form, and a sentence in the
            # advert does not overrule an answered form. See
            # `structured_geography` for why one column means two things.
            structured = structured_geography(config, workplace_type, place, location_raw)
            if structured is not None:
                outcome = GateOutcome(
                    gate=gate,
                    result=structured.result,
                    reason=structured.reason,
                    # NO QUOTE. See `StructuredGeography`: a form field beside
                    # the posting is not a substring of it, and ADR-0002 makes
                    # `quote` mean exactly that.
                )

        if outcome is None and gate == "geography":
            # **A DECLARED SCOPE IS THE ANSWER, NOT THE FIRST GUESS.**
            #
            # This used to be `declared or prose`, which reads as "prefer the
            # field" and behaves as "fall back to the body whenever the field
            # is not an eligible scope". Measured 2026-09-07 on the first
            # collection from a board that publishes a country list: a posting
            # whose declared scope said exactly `United States` came out
            # VERIFIED_ELIGIBLE for a candidate in Brazil, because its body
            # said "We work remotely across the US, Europe, LatAm, and beyond"
            # -- and the same scope string read VERIFIED_NOT_ELIGIBLE on
            # fifteen sibling postings whose bodies happened not to. One
            # field, two opposite verdicts, decided by marketing prose.
            #
            # That is the V1.2 defect wearing a new coat. The fix there was to
            # anchor a prose phrase to a sentence about hiring; "We work
            # remotely across ... LatAm" IS such a sentence, so the anchor
            # cannot help. What settles it is precedence: the board asked the
            # employer where it may hire and the employer answered, and a
            # sentence elsewhere in the advert does not get to overrule that
            # answer.
            #
            # A declared scope this configuration has no pattern for stays
            # UNRESOLVED rather than FAIL -- the Jabalpur rule, and the same
            # asymmetry: a false PASS shows a job she cannot take, a false
            # FAIL hides one she can.
            if declared_scope:
                found = _declared_scope(config, declared_scope)
            else:
                found = _positive_scope(config, title_field, description_field)
            if found is not None:
                scope, hit = found
                outcome = GateOutcome(
                    gate=gate,
                    result=GateResult.PASS,
                    reason=(
                        f"The posting states a hiring scope of {scope}, which includes "
                        f"{config.eligibility.candidate_country_label}."
                    ),
                    quote=hit.quote,
                    char_start=hit.char_start,
                    char_end=hit.char_end,
                )

        if gate == "geography":
            if declared_scope and outcome is None:
                # **THE FIELD AS A LIST.** `Argentina, Colombia, Mexico` in a
                # board's hiring-scope field matched no configured pattern and
                # used to stay UNRESOLVED. It is an exhaustive allowlist the
                # employer filled in, and it does not name Brazil: rule H,
                # the structured restriction decides. `Anywhere` resolves to
                # WORLDWIDE and admits; `Jabalpur` resolves to nothing and
                # stays unresolved.
                verdict = _scope_verdict(config, _resolve_list(declared_scope), exhaustive=True)
                if verdict is None:
                    outcome = GateOutcome(
                        gate=gate,
                        result=GateResult.UNRESOLVED,
                        reason="The provider states a hiring scope that could not be resolved. "
                        "General remote-work prose does not replace that answer.",
                    )
                else:
                    outcome = GateOutcome(
                        gate=gate,
                        result=GateResult.PASS if verdict else GateResult.FAIL,
                        reason=(
                            f"The provider states a hiring scope of {declared_scope.strip()}, "
                            f"which {'includes' if verdict else 'does not include'} "
                            f"{config.eligibility.candidate_country_label}."
                        ),
                    )
            outcome = _reconcile_scope_clauses(
                _scope_clause_readings(config, description_field),
                outcome,
            )
            # A location may explicitly qualify remote work even when the ATS
            # has no separate workplace-type field. Do not let generic benefits
            # prose certify worldwide hiring against that unresolved restriction.
            # This guard only withdraws a PASS; it never turns an office address
            # or the word "remote" into permission or a definitive refusal.
            if outcome is not None and outcome.result is GateResult.PASS and location_raw:
                remote_places = re.findall(
                    r"(?:^|;)\s*remote\s*[-\u2013\u2014:]\s*([^;]+)",
                    location_raw,
                    flags=re.IGNORECASE,
                )
                if remote_places and not any(
                    _scope_verdict(config, _resolve_list(value), exhaustive=True) is True
                    for value in remote_places
                ):
                    outcome = GateOutcome(
                        gate="geography",
                        result=GateResult.UNRESOLVED,
                        reason=(
                            f"The location field restricts remote work: {location_raw}. "
                            "It does not establish permission for this candidate; "
                            "confirm the permitted work location before applying."
                        ),
                    )

        outcomes.append(
            outcome
            or GateOutcome(gate=gate, result=GateResult.UNRESOLVED, reason=UNRESOLVED_REASONS[gate])
        )

    return tuple(outcomes)


#: The gate that has to be positively ANSWERED before the system will say
#: "eligible". Absence is never permission -- but that doctrine is about ONE
#: question: where does this employer allow the worker to live?
#:
#: The other four gates are exclusionary, and treating them the same way was a
#: category error worth naming, because it made `VERIFIED_ELIGIBLE`
#: unreachable in practice. A posting that never mentions security clearance
#: is not "unknown on clearance": clearance, mandatory citizenship, full-time
#: onsite attendance and heavy travel are conditions an employer states when
#: they exist, because they are the conditions that disqualify applicants.
#: Demanding positive proof of their ABSENCE would mean waiting for a posting
#: to say "no clearance required", which almost none do -- and the result is
#: not caution, it is a status column that reads UNRESOLVED for every job and
#: therefore tells the candidate nothing.
#:
#: So: silence on hiring scope is UNRESOLVED, permanently and on purpose.
#: Silence on the exclusionary gates is the absence of a disqualification,
#: which is what it actually means.
CRITICAL_GATES: frozenset[str] = frozenset({"geography"})


def eligibility_status_from(gates: tuple[GateOutcome, ...]) -> EligibilityStatus:
    """Fold the gates into one status, without ever inventing permission.

    `LIKELY_ELIGIBLE` is never produced here. It is reserved for a scope
    corroborated by provider metadata, which the deterministic matcher does not
    read; emitting it from text alone would mean claiming a second source exists.
    """
    if any(gate.result is GateResult.FAIL for gate in gates):
        return EligibilityStatus.VERIFIED_NOT_ELIGIBLE
    if any(
        gate.gate == "worksite" and gate.result is GateResult.UNRESOLVED and gate.quote
        for gate in gates
    ):
        # Evidence of a requirement is different from silence about worksite.
        return EligibilityStatus.UNRESOLVED
    critical = [gate for gate in gates if gate.gate in CRITICAL_GATES]
    if critical and all(gate.result is GateResult.PASS for gate in critical):
        return EligibilityStatus.VERIFIED_ELIGIBLE
    return EligibilityStatus.UNRESOLVED
