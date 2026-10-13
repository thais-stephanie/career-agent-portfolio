"""What a stored reading is an answer to, so it can be reused or refused.

A score is two halves that cost three orders of magnitude apart. Reading the
advert -- folding it, finding lexicon signals, classifying the title, reading
seniority, experience, employment and domestic context, evaluating the
geography gates -- is the expensive half. Turning those observations into a
number under a set of weights is the cheap half.

`job_match.result_json` already stores the expensive half. So a configuration
edit that touches only the arithmetic can be answered by replaying the cheap
half over what is already stored, and `match.replay` is that. The only thing
missing was a way to know, for a stored row, whether the expensive half is
still a valid answer.

THE SECTION SPLIT IS NOT A JUDGEMENT. IT IS READ OFF THE CODE.

    match/score.py    reads  scoring, preferences, thresholds, confidence
    match/lexicon.py  reads  lexicon, negation, prominence
    match/taxonomy.py reads  taxonomy, ambiguous_titles
    match/gates.py    reads  eligibility, negation
    match/engine.py   reads  prominence, screening

`score.py` is exactly what replay recomputes, and the four sections it reads
are read by nothing else. That disjointness is the whole soundness argument
and `tests/unit/test_replay_identity.py` asserts it by walking the syntax tree,
so a scorer that starts reading the lexicon fails the build rather than
silently making replay wrong.

UNKNOWN SECTIONS ARE GUARDED, NOT IGNORED. The guarded set is computed as
"every field of `SearchConfig` except the replay-safe four and the three
identity fields". A section added next year is therefore guarded by default,
and the failure mode of forgetting about it is a refused replay rather than a
wrong score.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

#: The sections `match/score.py` reads, and that nothing else reads. A change
#: confined to these can be answered by replaying the arithmetic.
REPLAY_SAFE_SECTIONS = frozenset(
    {
        "scoring",
        "preferences",
        "thresholds",
        "confidence",
    }
)

#: Which configuration this is and what it is called. Not inputs to anything;
#: they are already stored in their own columns.
IDENTITY_FIELDS = frozenset({"config_id", "config_version", "label"})

#: WHAT THE STORED READINGS WERE PRODUCED BY, as a version this repository
#: controls.
#:
#: `MATCH_SCHEMA_VERSION` answers "has the SHAPE of a stored result changed".
#: It does not move when a reader's MEANING changes, and CLAUDE.md records what
#: that cost: a restarted rescore reported 38,769 scored and 64,340 "already
#: scored" because content hash, configuration version and schema version all
#: matched and only the code had changed. The remedy on record is `--force`,
#: which is a corpus-sized pass.
#:
#: This is the identity that makes such a change targeted instead. **Bump it in
#: the same commit that changes what any of these produce**:
#:
#:   match/text.py        folding, section splitting, sentence boundaries
#:   match/lexicon.py     which signals fire, their hits and prominence
#:   match/taxonomy.py    the title classification
#:   match/seniority.py   the seniority reading
#:   match/experience.py  the experience reading and entry signals
#:   match/employment.py  the employment and domestic-context readings
#:   match/places.py      the resolved place
#:   match/gates.py       the gate outcomes and the eligibility status
#:   pipeline/facts.py    the derived JobFacts a replay rebuilds
#:
#: A bump invalidates every stored reading, so the next pass reads the adverts
#: again -- which is correct, and is the point: it is expressible, deliberate
#: and visible in a diff, rather than a flag somebody has to remember to pass.
#:
#: NOT bumped for a change to `match/score.py`: that is what replay recomputes.
#:
#: HISTORY, which is the evidence that the rule works:
#:
#:   readers-1  the identity this branch introduced.
#:   readers-2  `ceec020 fix(gates): nothing configured is not "nothing
#:              permitted"; the candidate's own country counts`. It changes
#:              what `structured_geography` and `evaluate_gates` PRODUCE --
#:              their own measurement is 13 of 21 demo postings moving off
#:              VERIFIED_NOT_ELIGIBLE. Replay carries gate outcomes, so
#:              without this bump a replayed row would serve a pre-fix
#:              eligibility verdict over a corrected reader. The bump refuses
#:              every reading written before the fix; the first pass after it
#:              reads the adverts again.
#:   readers-3  HCE01 (ADR-0029): `evaluate_gates` answers seven gates, not
#:              five. `credential` and `requirement` can FAIL a posting that
#:              states the phrase and are UNRESOLVED on every other, so a
#:              reading written by readers-2 carries no answer on either.
#:              The schema floor (`MATCH_SCHEMA_VERSION` 9) already refuses
#:              every such row as a replay source; the identity moves as well
#:              because this module's rule is "what the gates PRODUCE moved",
#:              and a rule with an exception is not a rule.
#:   readers-4  Explicit eligibility declarations: the work_authorization gate
#:              answers a posting's requirements (authorization in a country,
#:              citizenship, no sponsorship, contractor engagement) with what
#:              the candidate declared, and a stated requirement nobody
#:              answered holds the status UNRESOLVED. A readers-3 reading
#:              carries none of that.
#:   readers-5  `resolve_place` reads a single place that ends in its ISO
#:              country code (`Mississauga, ON, CA`, `Riyadh, Riyadh
#:              Province, SA`, `Home Office, BR`). The countries it produces
#:              move for every such posting, and `structured_geography` and
#:              the gates read them.
#:   readers-6  EU, EUROPE and EMEA are three scopes (2026-10-09). A posting
#:              saying "EU" no longer admits a candidate in the United
#:              Kingdom, "Europe" no longer one in Nigeria; sixteen countries
#:              join the gazetteer; an unambiguous trailing code beats a town
#:              named like a country (`Dublin, OH, US`); and "authorized to
#:              work in the EU" is a stated requirement (PENDING), where it
#:              used to be dropped. A remote posting located only by a
#:              region label refuses nobody outside it (UNRESOLVED, was
#:              FAIL). Countries, regions and both gates move.
#:   readers-7  The eligibility form (2026-10-09, ADR-0034): a remote
#:              posting's published location never refuses by itself; an
#:              on-site or hybrid job in a country she declared she is
#:              authorized to work in is not refused; and "where she lives +
#:              authorized there" counts as a country an employer can hire
#:              her in. The geography gate moves.
#:   readers-8  Where the work is (2026-10-09, ADR-0034 addendum): a
#:              requirement that names no country takes the posting's
#:              location only for an on-site or hybrid role, and "we do not
#:              sponsor visas" refuses only on-site or hybrid work; "US
#:              residents only" and a declared "USA Only" refuse. The
#:              work_authorization and geography gates move.
#:   readers-9  Brazil relevance (2026-10-10): a wizard-shaped phrase is read in
#:              its other inflections, orders and coordinations
#:              (`phrase_forms`); a range of years is read by its lower bound
#:              and years no longer name a level; a labelled contract line in
#:              the body is a contract statement; "the LATAM region" resolves;
#:              "we do not sponsor visas" on a remote role is answered for a
#:              candidate authorized where she lives. Signals, seniority,
#:              employment and both gates move.
#:   readers-10 Explicit facts the card used to call "not stated"
#:              (2026-10-12): an ad that lists several levels (a talent pool
#:              for junior, mid and senior) keeps them all and adopts one; an
#:              hourly rate ("hourly rate of $40-$85 USD") is read as pay and
#:              an hour count is not a pay period; part-time and the weekly
#:              hours are read; a talent pool and a time-zone requirement are
#:              read as what they are. Seniority, pay and employment move.
#:   readers-11 Optional preferences only add (2026-10-13): each work or tool
#:              phrase found adds a fixed share of its component, whatever
#:              was named beside it and whatever its weight; the tools guard
#:              is gone. An American employer hiring in Latin America that
#:              states no engagement is probably a contractor (the effective
#:              engagement, beside the stated one); a working pattern
#:              (full-time, part-time) is no longer an engagement; a USD
#:              figure with no period takes the advert's own period, or for
#:              a posting hiring in Latin America the owner's defaults (under
#:              7,000 a month, over 30,000 a year). Scores, employment, pay
#:              and the contract facet move.
READER_IDENTITY = "readers-11"


def guarded_sections(config: Any) -> list[str]:
    """Every configuration field whose change must refuse a replay, sorted."""
    fields = set(type(config).model_fields)
    return sorted(fields - REPLAY_SAFE_SECTIONS - IDENTITY_FIELDS)


def input_digest(config: Any) -> str:
    """Content address of everything a stored reading depends on.

    Two rows with the same digest were read by the same code from the same
    reading configuration, so one's observations answer the other's question.
    The reader identity is folded in rather than kept in a second column,
    because "is this reading still valid" is one question and answering it with
    two comparisons is how the two drift apart.

    Computed from the canonical JSON dump, like `SearchConfig.digest`, so a
    reformatted file is not a change of meaning and a re-expanded fragment is.
    """
    dumped = config.model_dump(mode="json")
    guarded = {name: dumped[name] for name in guarded_sections(config) if name in dumped}
    payload = json.dumps(
        {"reader_identity": READER_IDENTITY, "sections": guarded},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
