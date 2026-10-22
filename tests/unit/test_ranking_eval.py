"""The ranking evaluation's arithmetic: groups, splits, metrics, policies, gate."""

from __future__ import annotations

import math

import pytest

from career_agent.evaluation import ranking
from career_agent.evaluation.ranking import Golden, GoldenItem, Shown


def test_strict_and_review_worthy_are_separate_binary_perspectives() -> None:
    order = ["REJECT", "MAYBE", "KEEP", "REJECT"]
    strict = ranking.metrics(order, ("KEEP",), cuts=(2, 10))
    worthy = ranking.metrics(order, ("KEEP", "MAYBE"), cuts=(2, 10))
    assert strict["P@2"] == 0.0 and worthy["P@2"] == 0.5
    assert strict["positives"] == 1 and worthy["positives"] == 2
    assert strict["recall_within_labeled_surface@10"] == 1.0
    assert strict["NDCG@10"] == pytest.approx((1 / math.log2(4)) / 1.0)
    assert strict["positive_ranks"] == [3]
    assert strict["rank_distribution"]["1-10"] == 1
    assert strict["prevalence"] == 0.25


def test_metrics_without_positives_say_unknown_not_zero() -> None:
    m = ranking.metrics(["REJECT", "REJECT"], ("KEEP",), cuts=(10,))
    assert m["recall_within_labeled_surface@10"] is None
    assert m["NDCG@10"] is None


def _fact(job_id: str, company: str, title: str, body: str, length: int = 5000) -> dict:
    return {
        "job_id": job_id,
        "company_id": company,
        "title": title,
        "content_hash": body,
        "byte_length": length,
    }


def test_variants_of_one_opportunity_share_a_group() -> None:
    facts = [
        _fact("a", "c1", "Implementation Engineer", "h1"),
        _fact("b", "c1", "Implementation Engineer (Remote)", "h2"),
        _fact("c", "c1", "Something else", "h2"),
        _fact("d", "c2", "Implementation Engineer", "h1"),
    ]
    g = ranking.groups(facts)
    assert g["a"] == g["b"] == g["c"]  # same title, then same body at the same company
    assert g["d"] != g["a"]  # another company is another opportunity


def test_an_empty_body_never_joins_two_vacancies() -> None:
    facts = [
        _fact("a", "c1", "Account Executive", "empty", 0),
        _fact("b", "c1", "Data Engineer", "empty", 0),
    ]
    g = ranking.groups(facts)
    assert g["a"] != g["b"]


def test_splits_are_group_aware_stratified_and_deterministic() -> None:
    group_of = {f"j{i}": f"g{i // 2}" for i in range(40)}
    decision = {j: "KEEP" if int(j[1:]) < 10 else "REJECT" for j in group_of}
    first = ranking.assign_frozen_splits(group_of, decision, seed=7)
    assert first == ranking.assign_frozen_splits(group_of, decision, seed=7)
    assert set(first.values()) == {"development", "regression"}
    keep_groups = {group_of[j] for j in group_of if decision[j] == "KEEP"}
    assert any(first[g] == "regression" for g in keep_groups)
    assert any(first[g] == "development" for g in keep_groups)


def test_leakage_names_a_group_on_both_sides() -> None:
    items = [
        GoldenItem("a", "KEEP", "development", "g1"),
        GoldenItem("b", "REJECT", "regression", "g1"),
    ]
    assert ranking.leakage(items) == ["g1"]


def _shown(
    job_id: str,
    score: int,
    level: str | None = None,
    stale: bool = False,
    eligibility: str = "VERIFIED_ELIGIBLE",
) -> Shown:
    return Shown(job_id, "t", "c", score, "GOOD", eligibility, level, stale)


ITEMS = [
    _shown("a", 90),
    _shown("b", 60, "STRONG"),
    _shown("c", 80, "LOW"),
    _shown("d", 99, "STRONG", stale=True),
    _shown("e", 70),
    _shown("f", 50, "OTHER_WORK"),
]


def test_shipped_orders_mirror_the_jobs_list() -> None:
    assert [s.job_id for s in ranking.order(ITEMS, "score")] == ["d", "a", "c", "e", "b", "f"]
    # current readings by level, then out-of-date ones, then unread; score breaks ties
    assert [s.job_id for s in ranking.order(ITEMS, "deep_current")] == [
        "b",
        "c",
        "f",
        "d",
        "a",
        "e",
    ]


def test_offline_alternatives() -> None:
    # unread between the aligned levels and low priority
    assert [s.job_id for s in ranking.order(ITEMS, "deep_unread_middle")] == [
        "d",
        "b",
        "a",
        "e",
        "c",
        "f",
    ]
    # every fourth slot is the best unread posting left
    explore = [s.job_id for s in ranking.order(ITEMS, "deep_exploration")]
    assert explore[3] == "a" and sorted(explore) == sorted(s.job_id for s in ITEMS)
    served = {"c": 1, "a": 2}
    assert [s.job_id for s in ranking.order(ITEMS, "served_history", served)] == ["c", "a"]


def test_duplicate_adjusted_gives_one_slot_per_group_with_its_best_decision() -> None:
    label = {
        "a": GoldenItem("a", "REJECT", "development", "g1"),
        "b": GoldenItem("b", "KEEP", "development", "g1"),
        "c": GoldenItem("c", "REJECT", "development", "g2"),
    }
    ranked = [_shown("a", 90), _shown("c", 80), _shown("b", 70)]
    assert ranking.collapse(ranked, label) == ["KEEP", "REJECT"]


def _golden() -> Golden:
    items = [
        GoldenItem("a", "KEEP", "development", "g1", served_rank=3),
        GoldenItem(
            "b", "REJECT", "regression", "g2", primary_reason="WRONG_PRIMARY_WORK", served_rank=1
        ),
        GoldenItem("c", "KEEP", "regression", "g3", served_rank=2),
        GoldenItem("d", "MAYBE", "regression", "g4", served_rank=4),
        GoldenItem("z", "KEEP", ranking.LOCKBOX, "g9"),
    ]
    golden = Golden(items, "2026-10-21T00:00:00+00:00", 1)
    golden.frozen_digest = ranking.frozen_digest(items)
    return golden


SEEN = {
    s.job_id: s
    for s in (
        _shown("a", 50, "STRONG"),
        _shown("b", 90),
        _shown("c", 70),
        _shown("d", 60),
        _shown("z", 99),
    )
}


def test_evaluate_never_reads_the_lockbox_by_default() -> None:
    report = ranking.evaluate(_golden(), SEEN, bootstrap=False)
    assert set(report["populations"]) == {"labeled_surface", "development", "regression"}
    assert report["labelled"] == 4 and report["lockbox_size"] == 1
    regression = report["populations"]["regression"]["score"]
    assert regression["strict_pursuit"]["positive_ranks"] == [2]
    assert regression["review_worthy"]["positive_ranks"] == [2, 3]
    assert regression["top20_reasons"] == {"WRONG_PRIMARY_WORK": 1, "NO_REASON": 1}
    assert "served_history" in report["populations"]["labeled_surface"]
    opened = ranking.evaluate(_golden(), SEEN, open_lockbox=True, bootstrap=False)
    assert ranking.LOCKBOX in opened["populations"]


def _baseline(golden: Golden) -> tuple[dict, dict]:
    report = ranking.evaluate(golden, SEEN, bootstrap=False)
    return report, {"frozen_digest": golden.frozen_digest, "labelled": len(golden.frozen())}


def test_the_gate_passes_an_identical_run() -> None:
    golden = _golden()
    report, saved = _baseline(golden)
    assert ranking.gate(report, report, golden, saved) == []


def test_the_gate_catches_edited_labels_leakage_and_an_opened_lockbox() -> None:
    golden = _golden()
    report, saved = _baseline(golden)
    edited = _golden()
    edited.items[0] = GoldenItem("a", "REJECT", "development", "g1")
    edited.frozen_digest = ranking.frozen_digest(edited.items)
    assert any("changed" in f for f in ranking.gate(report, report, edited, saved))
    leaky = _golden()
    leaky.items.append(GoldenItem("e", "REJECT", "regression", "g1"))
    leaky.frozen_digest = golden.frozen_digest
    run = ranking.evaluate(leaky, SEEN, bootstrap=False)
    assert any("more than one split" in f for f in ranking.gate(run, report, leaky, saved))
    opened = ranking.evaluate(golden, SEEN, open_lockbox=True, bootstrap=False)
    assert any("lockbox" in f for f in ranking.gate(opened, report, golden, saved))


def test_the_gate_catches_a_large_regression_and_a_not_eligible_posting() -> None:
    golden = _golden()
    report, saved = _baseline(golden)
    worse = dict(SEEN, c=_shown("c", 10))
    for i in range(30):  # pushes the regression KEEP out of the top 20
        golden.items.append(GoldenItem(f"r{i}", "REJECT", "regression", f"gr{i}"))
        worse[f"r{i}"] = _shown(f"r{i}", 80)
    golden.frozen_digest = saved["frozen_digest"]
    failures = ranking.gate(ranking.evaluate(golden, worse, bootstrap=False), report, golden, saved)
    assert any("NDCG@20" in f for f in failures)
    blocked = dict(SEEN, b=_shown("b", 90, eligibility="VERIFIED_NOT_ELIGIBLE"))
    failures = ranking.gate(
        ranking.evaluate(_golden(), blocked, bootstrap=False), report, _golden(), saved
    )
    assert any("rules her out" in f for f in failures)


def test_the_golden_file_round_trips(tmp_path) -> None:  # noqa: ANN001
    golden = _golden()
    ranking.save(tmp_path / "g.json", golden)
    back = ranking.load(tmp_path / "g.json")
    assert back.items == golden.items
    assert back.frozen_digest == ranking.frozen_digest(golden.items)
