"""Time is the bound; inventory and fairness survive every scheduling decision."""

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from career_agent.net.deadline import BudgetExpired, Deadline
from career_agent.sources.maintenance import Item, plan

NOW = datetime(2026, 9, 22, tzinfo=UTC).timestamp()


def unit(key, seconds, **kwargs):
    return Item(key, "family", key, None, seconds, "MEASURED_BOARD", **kwargs)


@pytest.mark.parametrize("budget", [900, 1800, 3600])
def test_huge_board_does_not_hide_hundred_tiny_boards(budget):
    items = [unit("huge", 5000)] + [unit(str(i), 2) for i in range(100)]
    result = plan(items, budget, now=NOW)
    assert len(result["items"]) == 101
    assert "huge" not in result["selected"]
    assert len(result["selected"]) == 100
    assert result["estimated_seconds"] <= budget
    assert result["items"][0]["reason"] == "NEEDS_LARGER_BUDGET"


def test_oldest_heavy_gets_reserved_turn_before_fast_queue_spends_budget():
    items = [unit("heavy", 600)] + [unit(str(i), 10) for i in range(100)]
    assert "heavy" in plan(items, 900, now=NOW)["selected"]


def test_entire_budget_heavy_and_unknown_each_get_turn_in_three_rounds():
    items = [
        unit("fast", 10),
        unit("heavy", 900),
        replace(unit("unknown", 900), evidence="UNKNOWN_FAMILY_ALLOWANCE"),
    ]
    chosen = set()
    for cycle in range(3):
        result = plan(items, 900, now=NOW, round_number=cycle)
        chosen.update(result["selected"])
        assert result["estimated_seconds"] <= 900
    assert chosen == {"fast", "heavy", "unknown"}


def test_all_heavy_boards_eventually_receive_turn_with_persisted_successes():
    items = [unit(str(i), 400) for i in range(12)]
    done = set()
    for cycle in range(12):
        result = plan(items, 450, now=NOW, round_number=cycle)
        assert len(result["selected"]) == 1
        done.update(result["selected"])
        items = [
            replace(i, last_success="2026-09-22T00:00:00Z") if i.key in done else i for i in items
        ]
    assert len(done) == 12


def test_at_most_one_unknown_and_no_inventory_disappears():
    items = [
        replace(unit(str(i), 120), evidence="UNKNOWN_EXPLORATION_ALLOWANCE") for i in range(10)
    ]
    for cycle in range(3):
        result = plan(items, 3600, now=NOW, round_number=cycle)
        assert len(result["selected"]) == 1
        assert len(result["items"]) == 10


def test_unknown_without_fit_is_visible_not_failed():
    item = replace(unit("unknown", 120), evidence="UNKNOWN_EXPLORATION_ALLOWANCE")
    result = plan([item], 60, now=NOW)
    assert result["items"][0]["reason"] == "NEEDS_LARGER_BUDGET"


def test_success_is_not_repeated_and_interruption_has_bounded_cooldown():
    items = [
        replace(unit("fresh", 2), last_success="2026-09-22T00:00:00Z"),
        replace(unit("interrupted", 2), last_attempt="2026-09-22T00:00:00Z"),
    ]
    assert plan(items, 100, now=NOW)["selected"] == []
    assert plan(items, 100, now=NOW + 3601)["selected"] == ["interrupted"]


def test_policy_block_cannot_be_overridden_with_infinite_freshness_priority():
    item = unit("restricted", 1, blocked="OWNER_RUN_ONLY")
    assert not plan([item], 1000, now=NOW, stale_hours=0)["selected"]


@pytest.mark.parametrize("budget", [0, -1, float("inf"), float("nan")])
def test_invalid_time_budget_refused(budget):
    with pytest.raises(ValueError):
        plan([], budget)


def test_deadline_does_not_shorten_provider_wait_to_make_early_request():
    waits = []
    deadline = Deadline(10, lambda: 5)
    with pytest.raises(BudgetExpired):
        deadline.sleep(6, waits.append)
    assert waits == []


def test_plan_does_not_accept_candidate_preferences():
    import inspect

    assert not {"countries", "preferences", "profile", "seniority"} & set(
        inspect.signature(plan).parameters
    )
