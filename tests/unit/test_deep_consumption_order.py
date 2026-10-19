"""The order a deep-reading job spends its calls in (ADR-0035, addendum 4).

Bands of local fit, highest first; inside a band the three strata in turns of
8 : 5 : 7; inside a stratum the highest local score, then the job id. The plan
keeps exactly the postings and the strata it was given.
"""

from __future__ import annotations

import random

from career_agent.semantic.deep_run import TURN, consumption_order


def band(score: int) -> str:
    return next(
        b
        for b, low in (("STRONG", 75), ("GOOD", 55), ("MODERATE", 35), ("WEAK", 0))
        if score >= low
    )


def item(job: str, score: int, stratum: str) -> tuple[dict, str]:
    return {"id": job, "match_score": score, "fit_band": band(score)}, stratum


def plan(counts: dict[tuple[str, str], list[int]]) -> list[tuple[dict, str]]:
    """{(stratum, prefix): [scores]} -> items with ids prefix-0, prefix-1, ..."""
    return [
        item(f"{prefix}-{n:03d}", score, stratum)
        for (stratum, prefix), scores in counts.items()
        for n, score in enumerate(scores)
    ]


def ids(items) -> list[str]:
    return [row["id"] for row, _ in items]


def test_the_turn_is_the_shares_of_the_plan() -> None:
    assert TURN == {"top_local": 8, "lexical": 5, "control": 7}


def test_same_postings_same_strata_only_the_order_moves() -> None:
    rng = random.Random(7)
    picked = [item(f"j{n:04d}", rng.randint(0, 100), rng.choice(list(TURN))) for n in range(500)]
    ordered = consumption_order(picked)
    assert sorted((r["id"], s) for r, s in ordered) == sorted((r["id"], s) for r, s in picked)


def test_higher_bands_are_read_first() -> None:
    rng = random.Random(3)
    picked = [item(f"j{n:04d}", rng.randint(0, 100), rng.choice(list(TURN))) for n in range(400)]
    order = ["STRONG", "GOOD", "MODERATE", "WEAK"]
    seen = [order.index(r["fit_band"]) for r, _ in consumption_order(picked)]
    assert seen == sorted(seen)


def test_inside_a_band_the_strata_take_turns_of_8_5_7() -> None:
    picked = plan(
        {
            ("top_local", "t"): [60] * 30,
            ("lexical", "l"): [60] * 30,
            ("control", "c"): [60] * 30,
        }
    )
    strata = [s for _, s in consumption_order(picked)]
    assert strata[:20] == ["top_local"] * 8 + ["lexical"] * 5 + ["control"] * 7
    assert strata[20:40] == strata[:20]


def test_a_stratum_that_runs_out_gives_its_turn_to_the_others() -> None:
    picked = plan(
        {
            ("top_local", "t"): [60] * 20,
            ("lexical", "l"): [60] * 2,
            ("control", "c"): [60] * 10,
        }
    )
    strata = [s for _, s in consumption_order(picked)]
    assert strata[:17] == ["top_local"] * 8 + ["lexical"] * 2 + ["control"] * 7
    # lexical is empty from here on: 8 top, the 3 control left, the last 4 top
    assert strata[17:] == ["top_local"] * 8 + ["control"] * 3 + ["top_local"] * 4
    # and a band with no lexical posting at all reads the other two in turn
    only_two = plan({("top_local", "t"): [90] * 9, ("control", "c"): [90] * 9})
    assert [s for _, s in consumption_order(only_two)][:15] == ["top_local"] * 8 + ["control"] * 7


def test_inside_a_stratum_the_highest_score_first_and_ties_by_job_id() -> None:
    picked = [
        item("b", 70, "top_local"),
        item("a", 70, "top_local"),
        item("c", 72, "top_local"),
        item("e", 56, "top_local"),
        item("d", 56, "top_local"),
    ]
    assert ids(consumption_order(picked)) == ["c", "a", "b", "d", "e"]


def test_the_order_does_not_depend_on_the_order_given() -> None:
    rng = random.Random(11)
    picked = [
        item(f"j{n:04d}", rng.choice([40, 60, 60, 80]), rng.choice(list(TURN))) for n in range(300)
    ]
    shuffled = picked[:]
    rng.shuffle(shuffled)
    assert ids(consumption_order(picked)) == ids(consumption_order(shuffled))


def test_no_weak_posting_is_read_while_a_higher_band_waits() -> None:
    picked = plan(
        {
            ("top_local", "t"): [80] * 10 + [60] * 10,
            ("lexical", "l"): [14] * 10 + [62] * 3,
            ("control", "c"): [9] * 10 + [57] * 2,
        }
    )
    ordered = consumption_order(picked)
    first_weak = next(n for n, (r, _) in enumerate(ordered) if r["fit_band"] == "WEAK")
    assert all(r["fit_band"] != "WEAK" for r, _ in ordered[:first_weak])
    assert first_weak == 25, "10 strong + 15 good come before any weak posting"
