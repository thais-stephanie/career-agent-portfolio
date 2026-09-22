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
READER_IDENTITY = "readers-3"


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
