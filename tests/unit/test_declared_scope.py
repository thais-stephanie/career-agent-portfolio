"""A board that ASKS the employer where it may hire has an answer worth reading.

The distinction this file exists to hold is between two things that look alike
in a database column and are not alike at all:

  `San Francisco, CA`      on a Greenhouse posting -- an OFFICE
  `Anywhere in the World`  in a We Work Remotely `region` -- a HIRING SCOPE

The first is a place a company has desks. Reading it as a statement about who
the company may employ is the conflation invariant 3 exists to forbid, and it
is how a candidate in Brazil ends up told she is eligible for a job in
California. The second is the employer's answer to that exact question,
because the board asked it.

Measured on the 91 postings collected from We Work Remotely: 89 say `Anywhere
in the World`, one says `USA Only`, one says `Jabalpur`. Before the gate read
the field, 83 of the 89 were UNRESOLVED -- the board had published a worldwide
hiring scope and the product ignored it.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from career_agent.config.search_config import load_search_config
from career_agent.domain.enums import EligibilityStatus, GateResult
from career_agent.match.gates import eligibility_status_from, evaluate_gates
from career_agent.providers.registry import available_providers, publishes_hiring_scope

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def config():
    """The committed worked example, with no local file able to override it.

    A test must never read `search.local.yaml`: it is the owner's private
    search, it is gitignored, and a test that passes because of what is in it
    proves nothing on anybody else's machine.
    """
    import tempfile

    directory = Path(tempfile.mkdtemp())
    shutil.copy(ROOT / "config" / "search.worked-example.yaml", directory)
    shutil.copy(ROOT / "config" / "places.yaml", directory)
    loaded, _ = load_search_config(directory, use_example=True)
    return loaded


def geography(config, scope: str | None):
    gates = evaluate_gates(config, {}, "Engineer", "We build data pipelines.", declared_scope=scope)
    return next(g for g in gates if g.gate == "geography"), eligibility_status_from(gates)


# =========================================================================
# 1. THE FIELD IS READ
# =========================================================================


@pytest.mark.parametrize("scope", ["Anywhere in the World", "Latin America", "LATAM"])
def test_a_declared_worldwide_scope_opens_the_gate(config, scope: str) -> None:
    gate, status = geography(config, scope)
    assert gate.result is GateResult.PASS
    assert status is EligibilityStatus.VERIFIED_ELIGIBLE


def test_a_declared_restriction_closes_it(config) -> None:
    """`USA Only` is an answer, not a mention.

    It needs no sentence around it and gets no intent anchoring, because the
    field is the claim. That is the whole difference between this and reading
    prose.
    """
    gate, status = geography(config, "USA Only")
    assert gate.result is GateResult.FAIL
    assert status is EligibilityStatus.VERIFIED_NOT_ELIGIBLE


def test_a_scope_nobody_recognises_is_not_guessed_at(config) -> None:
    """`Jabalpur` is a city in India and the answer is still UNRESOLVED.

    The tempting rule is that anything not matching an eligible scope must be
    a refusal -- the employer did answer, after all. It is refused here because
    the risk is not symmetric: a false PASS shows a job the candidate cannot
    take, and a false FAIL HIDES one she can. A region string this
    configuration has no pattern for means the configuration has no pattern for
    it, and saying so is the honest answer.
    """
    gate, status = geography(config, "Jabalpur")
    assert gate.result is GateResult.UNRESOLVED
    assert status is EligibilityStatus.UNRESOLVED
    assert status is not EligibilityStatus.VERIFIED_NOT_ELIGIBLE


def test_no_declared_scope_leaves_the_gate_where_it_was(config) -> None:
    """Every ATS posting takes this path, which is most of the corpus."""
    gate, _ = geography(config, None)
    assert gate.result is GateResult.UNRESOLVED


# =========================================================================
# 2. AND ONLY FROM A BOARD THAT PUBLISHES ONE
# =========================================================================


#: The only adapters allowed to answer "where may this employer hire".
#:
#: An allow-list rather than a count, so adding a source cannot widen the gate
#: by accident: a new provider is False here until somebody names it, and
#: naming it is a reviewable line in a commit.
#:
#: `wwr`        `region` is free text the employer chose: `Anywhere in the
#:              World`, `USA Only`. The employer's answer, not an office.
#: `himalayas`  `locationRestrictions` is an ARRAY of countries the employer
#:              will hire from, named for the restriction and documented as
#:              one. Measured 2026-09-07: present on 20 of the first 20 rows.
#:              This feed does not publish an office at all, which is part of
#:              why the field cannot be one.
#: `jobicy`     `jobGeo` is the employer's answer to where applicants may
#:              live. Measured over one bounded LATAM window: country and
#:              region lists (`Brazil`, `LATAM`, `Brazil,  Canada,  Mexico,
#:              USA`) with bodies behind the narrow ones saying "applicants
#:              must reside in Mexico". Like Himalayas, this feed publishes no
#:              office at all, which is part of why the field cannot be one.
#:              **`Anywhere` is handled in the adapter, not here.** It is the
#:              vendor's DEFAULT -- 52 of 200 rows -- and `hiring_scope`
#:              returns None for it, so the gate sees nothing stated. A
#:              capability says what the FIELD means; a value meaning "unset"
#:              is a row-level fact and silencing the whole field for it would
#:              throw away the 148 rows that do state something.
#: `remotive`   `candidate_required_location`, documented first-party as
#:              "Geographical restriction for the remote candidate, if any".
#:              Named for the restriction and documented as one; the feed
#:              publishes no office. Measured 2026-09-11 on all 18 rows.
#: `jobgether`  `location` is where the applicant may be, not a desk:
#:              querying `locations=brazil` returns rows whose value is
#:              `Anywhere`, `Latin America` and `Brazil`, and the offer
#:              page's JSON-LD `applicantLocationRequirements` matches it.
#:              Unlike Jobicy, `Anywhere` is the vendor saying anywhere.
#: `fourdayweek` the remote-allowed entries of `locations`, which the
#:              OpenAPI names `remote_allowed` and describes the country
#:              filter as matching "office or remote-allowed locations". The
#:              adapter puts ONLY those in `location_raw` for a remote role;
#:              a remote role that names none states nothing, and an office
#:              reaches the gate through `structured_geography` beside the
#:              work arrangement, never as a scope.
#: `dynamitejobs` schema.org `applicantLocationRequirements` on each posting
#:              page: the countries the employer will hire from, a list named
#:              for the restriction. Exhaustive, and read as one.
BOARDS_THAT_ASK: frozenset[str] = frozenset(
    {"wwr", "himalayas", "jobicy", "remotive", "jobgether", "fourdayweek", "dynamitejobs"}
)


def test_only_the_board_that_asks_may_answer() -> None:
    """The guard that keeps invariant 3 true.

    Every ATS reports an office. If `publishes_hiring_scope` were ever True for
    one of them, `San Francisco, CA` would go straight to the eligibility gate
    and every posting at a company with a Brazilian office would read as
    eligible.
    """
    for board in BOARDS_THAT_ASK:
        assert publishes_hiring_scope(board) is True, (
            f"{board} is listed as a board that asks, and no longer says so"
        )
    for provider in available_providers():
        if provider in BOARDS_THAT_ASK:
            continue
        assert publishes_hiring_scope(provider) is False, (
            f"{provider} reports an office; an office is not a hiring scope"
        )


def test_the_list_of_boards_that_ask_stays_short_and_deliberate() -> None:
    """A guard on the guard.

    The allow-list above is only worth anything while somebody has to edit it.
    If a future change made `publishes_hiring_scope` default to True, this
    fails rather than the list quietly becoming every provider.
    """
    asking = {p for p in available_providers() if publishes_hiring_scope(p)}
    assert asking == BOARDS_THAT_ASK


def test_an_unknown_provider_publishes_nothing() -> None:
    """`manual_import` is a posting somebody pasted, and has no adapter.

    Absence is never permission here either: an unknown name answers False
    rather than raising or defaulting to True.
    """
    assert publishes_hiring_scope("manual_import") is False
    assert publishes_hiring_scope(None) is False
    assert publishes_hiring_scope("") is False


def test_the_matcher_never_names_a_provider() -> None:
    """`tests/unit/test_provider_neutrality.py` enforces this generally; this
    asserts the specific thing that would be easiest to get wrong here.

    IT READS THE CODE AND NOT THE COMMENTS, and that is a correction rather than
    a loophole. The first version read the whole file, and it failed the day a
    fifth board family was registered -- because `A_DUTY` carries an employer's
    own sentence as evidence for why it exists:

        "you will support the payrolls either directly through Workday and ADP"

    That is a posting quoted verbatim, and ADR-0002 makes a quote evidence only
    while it is a contiguous substring of the original. Editing it to get a test
    green would be falsifying the evidence for a rule. The claim being made here
    is that the GATE never branches on a vendor, so the gate's executable text is
    what the test reads; `tests/unit/test_provider_neutrality.py` reached the
    same answer for the same reason and walks the syntax tree.
    """
    source = (ROOT / "src" / "career_agent" / "match" / "gates.py").read_text(encoding="utf-8")
    code = _without_comments(source)
    for provider in available_providers():
        assert provider not in code.lower(), (
            f"the gate names {provider}; it must ask the registry instead"
        )


def _without_comments(source: str) -> str:
    """`source` with every `#` comment removed, by tokenising rather than by regex.

    A regex for "from a hash to the end of the line" would cut a hash inside a
    string literal, which is how a URL fragment or a regex character class
    silently disappears from what a guard can see.
    """
    import io
    import tokenize

    kept: list[str] = []
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type != tokenize.COMMENT:
            kept.append(token.string)
    return "\n".join(kept)


# =========================================================================
# 3. AND THE FIELD DECIDES, RATHER THAN GOING FIRST
# =========================================================================

#: A body that states a hiring scope in a sentence about hiring.
#:
#: It clears the `HIRING_INTENT` anchor V1.2 added, and it is meant to: the
#: point of these tests is that a sentence which passes every check we have for
#: prose still may not overrule a field the employer filled in.
BODY_SAYING_LATAM = (
    "We build data pipelines. We work remotely across the US, Europe, LatAm, "
    "and beyond, and we hire wherever our teams are."
)


def _geography_with_body(config, scope: str | None, body: str):
    gates = evaluate_gates(config, {}, "Engineer", body, declared_scope=scope)
    return next(g for g in gates if g.gate == "geography"), eligibility_status_from(gates)


def test_prose_may_not_open_a_gate_the_declared_scope_did_not(config) -> None:
    """The defect, reproduced.

    Found 2026-09-07 on the first collection from a board that publishes a
    country list. A posting whose declared scope said exactly `United States`
    came out VERIFIED_ELIGIBLE for a candidate in Brazil, because its body
    said "We work remotely across the US, Europe, LatAm, and beyond" -- while
    fifteen sibling postings carrying the SAME scope string read
    VERIFIED_NOT_ELIGIBLE, because their bodies happened not to.

    One field, two opposite verdicts, decided by marketing prose. The
    resolution is precedence: the board asked, the employer answered, and a
    sentence elsewhere in the advert does not overrule the answer.
    """
    gate, status = _geography_with_body(config, "United States", BODY_SAYING_LATAM)

    assert gate.result is not GateResult.PASS
    assert status is not EligibilityStatus.VERIFIED_ELIGIBLE


def test_the_same_prose_still_answers_when_no_field_was_published(config) -> None:
    """The fix must not close the prose path for every ATS in the corpus.

    Most postings here come from boards that publish no scope at all, and for
    those a hiring sentence in the body remains the only evidence there is.
    """
    gate, status = _geography_with_body(config, None, BODY_SAYING_LATAM)

    assert gate.result is GateResult.PASS
    assert status is EligibilityStatus.VERIFIED_ELIGIBLE


def test_a_declared_scope_that_does_include_her_still_passes(config) -> None:
    """The other half. A field that answers the question is read."""
    gate, status = _geography_with_body(config, "Brazil", "We build data pipelines.")

    assert gate.result is GateResult.PASS
    assert status is EligibilityStatus.VERIFIED_ELIGIBLE


def test_an_unrecognised_declared_scope_is_unresolved_and_not_refused(config) -> None:
    """The Jabalpur rule, restated for the new precedence.

    A scope this configuration has no pattern for means the configuration has
    no pattern for it. The risk is not symmetric: a false PASS shows a job she
    cannot take, a false FAIL hides one she can.
    """
    gate, status = _geography_with_body(config, "Jabalpur", BODY_SAYING_LATAM)

    assert gate.result is GateResult.UNRESOLVED
    assert status is EligibilityStatus.UNRESOLVED


def test_one_scope_string_always_yields_one_verdict(config) -> None:
    """The property whose absence was the symptom.

    The same declared scope must reach the same answer whatever the body says,
    or the field is not deciding anything.
    """
    quiet = _geography_with_body(config, "United States", "We build data pipelines.")
    loud = _geography_with_body(config, "United States", BODY_SAYING_LATAM)

    assert quiet[0].result == loud[0].result
    assert quiet[1] == loud[1]
