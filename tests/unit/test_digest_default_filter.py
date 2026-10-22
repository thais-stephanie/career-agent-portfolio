"""Home narrows exactly as the Jobs list does (`api._filter_from`)."""

from __future__ import annotations

from types import SimpleNamespace

from career_agent.digest import default_filter, sections


def _config() -> SimpleNamespace:
    level = SimpleNamespace(value="INTERN")
    return SimpleNamespace(
        preferences=SimpleNamespace(
            seniority=SimpleNamespace(excluded=[level]),
            remote=SimpleNamespace(excluded_work_models=["onsite"]),
        ),
        thresholds=SimpleNamespace(shortlist_min_score=60),
    )


def test_home_passes_the_excluded_levels_work_models_and_age_limit() -> None:
    f = default_filter(5, config=_config(), age={"max_age_days": 120, "include_undated": False})
    assert f.excluded_seniorities == ("INTERN",)
    assert f.include_excluded_seniority is False
    assert f.excluded_work_models == ("ONSITE",)
    assert f.include_excluded_work_model is False
    assert f.max_age_days == 120
    assert f.include_undated is False


def test_every_home_section_carries_the_same_narrowing() -> None:
    age = {"max_age_days": 150, "include_undated": True}
    for section in sections(_config(), show=5, age=age):
        assert section.job_filter.max_age_days == 150
        assert section.job_filter.include_undated is True
        assert section.job_filter.excluded_seniorities == ("INTERN",)
