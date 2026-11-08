"""Spec v1 style rules, page limit and page fitting. Synthetic documents only."""

from __future__ import annotations

import pytest
from tests.support_resume import long, rich

from career_agent.resume_doc import budget, drafter, style
from career_agent.resume_doc.jd import Requirement
from career_agent.resume_doc.tailor import Source, Support

EM, EN = style.EM_DASH, style.EN_DASH


def codes(text: str, literals: tuple[str, ...] = ()) -> set[str]:
    return {f["code"] for f in style.findings(text, literals)}


# ------------------------------------------------------------------ style


def test_a_plain_bullet_raises_nothing() -> None:
    assert codes("Release management: Shipped 12 releases on schedule for 4 teams.") == set()


def test_em_dash_is_refused_and_en_dash_only_in_ranges() -> None:
    assert codes(f"Built the report{EM}weekly.") == {"EM_DASH"}
    for ranged in (f"2019 {EN} 2021", f"60{EN}80 tickets", f"March 2025 {EN} October 2026",
                   f"Mar 2022 {EN} Present"):  # fmt: skip
        assert codes(ranged) == set(), ranged
    assert codes(f"Support {EN} and more") == {"EN_DASH_OUTSIDE_RANGE"}


@pytest.mark.parametrize(
    "text",
    [
        "Leveraged dashboards for sales.",
        "Utilizing SQL for reports.",
        "A results-driven analyst.",
        "Built a cutting edge tool.",
        "Known among team players.",
        "Removed a bottleneck in billing.",
        "Built robust pipelines.",
    ],
)
def test_banned_words_in_their_forms(text: str) -> None:
    assert "BANNED_WORD" in codes(text)


def test_banned_phrases_contrast_and_rating() -> None:
    assert "BANNED_PHRASE" in codes("Delivered innovative solutions to clients.")
    assert "BANNED_PHRASE" in codes("A proven track record in support.")
    assert "CONTRASTIVE" in codes("Not just a tool, but a platform for the team.")
    assert "CONTRASTIVE" in codes("This isn't about speed, it's about trust.")
    assert "CONTRASTIVE" in codes("More than just reports.")
    assert "RATING_ADJECTIVE" in codes("An exceptional analyst.")
    assert codes("Shipped it!") == {"EXCLAMATION"}
    assert codes("Why does it matter?") == {"QUESTION"}
    assert codes("Shipped it " + chr(0x1F680)) == {"EMOJI"}


def test_a_banned_word_inside_an_official_name_is_not_the_lines_wording() -> None:
    line = "Certified as Dynamic Routing Practitioner and worked at Robust Ventures Example."
    assert "BANNED_WORD" in codes(line)
    assert codes(line, ("Dynamic Routing Practitioner", "Robust Ventures Example")) == set()
    # A longer word is not the banned one: "Dynamics" is a product name.
    assert codes("Configured Microsoft Dynamics for billing.") == set()


def test_claim_limits_are_read_as_whole_phrases() -> None:
    forbidden = ["installed the agent"]
    assert style.limit_findings("Installed the agent on 200 laptops.", forbidden)
    assert not style.limit_findings("Reinstalled the agents twice.", forbidden)
    # A faithful line can still hold the phrase: flagged for the person, never
    # decided by code (the report marks it a risk, not a refusal).
    assert style.limit_findings("Worked with IT, who installed the agent.", forbidden)


# ------------------------------------------------------------- page limit


def test_the_page_limit_is_the_persons_yes_or_no() -> None:
    assert budget.page_limit(False) == 2
    assert budget.page_limit(True) == 3
    for value in (3, "3", "true", 1, None):
        with pytest.raises(TypeError):
            budget.page_limit(value)  # type: ignore[arg-type]


def test_a_model_answer_cannot_carry_a_page_limit() -> None:
    with pytest.raises(drafter.DrafterError):
        drafter.parse('{"changes": [], "page_limit": 3}')


def test_a_short_resume_fits_without_cuts() -> None:
    doc, result = budget.fit(rich(), [], limit=2, year=2026)
    assert result.status == "FITS" and not result.cuts and doc == rich()


def _visible(doc) -> dict[str, int]:
    return {e.id: sum(not b.hidden for b in e.bullets) for e in doc.experience}


def test_a_long_resume_is_cut_by_hiding_and_every_role_keeps_a_line() -> None:
    original = long()
    doc, result = budget.fit(original, [], limit=2, year=2026)
    assert result.status == "CUT" and result.pages <= 2
    assert result.words <= budget.WORD_BUDGET[2][1]
    # Hidden, never deleted: the same lines are all still in the document.
    assert [b.id for e in doc.experience for b in e.bullets] == [
        b.id for e in original.experience for b in e.bullets
    ]
    assert all(n >= 1 for n in _visible(doc).values())
    hidden = {b.id for e in doc.experience for b in e.bullets if b.hidden}
    assert hidden == {c["ref"].removeprefix("bullet/") for c in result.cuts
                      if c["ref"].startswith("bullet/")}  # fmt: skip
    # The spec's order: skills the posting does not name go before the
    # remaining roles' weakest lines; old roles' lines go first of all.
    steps = [c["step"] for c in result.cuts]
    assert steps[0] == "BULLET_OF_OLD_ROLE"


def _required(line_id: str) -> Support:
    req = Requirement(
        id="r1", source_quote="Coordinated releases", start=0, section="requirements",
        kind="RESPONSIBILITY", hardness="REQUIRED", importance=3, language="en",
        concepts=("coordinated", "release"), named=(),
    )  # fmt: skip
    src = Source(id=line_id, text="x", line_id=line_id, evidence_ids=("claim-x",), shown=True)
    return Support(req, "SHOWN_IN_MASTER", shown=[(src, 1.0)])


def test_proof_of_a_required_ask_is_never_cut_and_the_conflict_is_said() -> None:
    doc = long()
    proof = doc.experience[-1].bullets[0].id
    calls: list[int] = []

    def never_fits(_doc) -> int:
        calls.append(1)
        return 9

    cut, result = budget.fit(doc, [_required(proof)], limit=2, year=2026, measure=never_fits)
    assert result.status == "CONFLICT" and proof in result.protected
    assert not next(b for e in cut.experience for b in e.bullets if b.id == proof).hidden
    assert len(calls) <= budget.MAX_ROUNDS


def test_three_pages_only_with_the_exception_and_a_thin_third_page_is_said() -> None:
    doc, result = budget.fit(rich(), [], limit=3, year=2026, measure=lambda d: 3)
    assert result.limit == 3 and result.status == "FITS"
    assert any("third page" in n for n in result.notes)
    with pytest.raises(ValueError):
        budget.fit(rich(), [], limit=4, year=2026)
