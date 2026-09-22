"""Compare optimized reads with the pre-hardening SQL over adversarial siblings."""

from dataclasses import replace

import pytest
from tests.unit.test_mvp_repo import CONFIG_ID, CONFIG_VERSION, DIGEST, a_full_result, seed_job

from career_agent.domain.enums import EligibilityStatus, ScreeningState
from career_agent.storage.db import connect, migrate
from career_agent.storage.mvp_repo import (
    _REPRESENTATIVE_TEMPLATE,
    ApplicationRepo,
    JobFilter,
    MatchRepo,
    ScoredJobQuery,
)


class LegacyQuery(ScoredJobQuery):
    def _from_for(self, needed, a="", f=None):
        return super()._from_for(needed, a, None)

    def _where(self, config_id, config_version, f, *, alias=""):
        sql, params = super()._where(config_id, config_version, f, alias=alias)
        return sql.replace(
            f"jm{alias}.job_id IN (SELECT job_id FROM job_application WHERE saved = 1)",
            f"COALESCE(ja{alias}.saved, 0) = 1",
        ), params

    def _representative_clause(self, config_id, config_version, f):
        inner = replace(f, group_duplicates=False)
        where4, params4 = self._where(config_id, config_version, inner, alias="4")
        where3, params3 = self._where(config_id, config_version, inner, alias="3")
        needed = self._joins_needed(inner)
        return _REPRESENTATIVE_TEMPLATE.format(
            from3=self._from_for(needed, "3", inner),
            from4=self._from_for(needed, "4", inner),
            where3=where3,
            where4=where4,
        ), [*params4, *params3]

    def count(self, config_id, config_version, f):
        where, params = self._where(config_id, config_version, f)
        return self.conn.execute(
            "SELECT COUNT(*)" + self._from_for(self._joins_needed(f), f=f) + where, params
        ).fetchone()[0]


@pytest.fixture
def queries(tmp_path):
    conn = connect(tmp_path / "equivalence.db")
    migrate(conn)
    for n in range(32):
        job, content = seed_job(
            conn,
            slug=f"employer-{n // 8}",
            external_id=str(n),
            title=f"Role {n % 2}",
            location_raw=f"Place {n % 3}",
        )
        result = a_full_result(
            match_score=(n % 4) * 10,
            eligibility_status=list(EligibilityStatus)[n % len(EligibilityStatus)],
            screening_state=ScreeningState.BLOCKED if n % 3 else ScreeningState.NOT_BLOCKED,
        )
        MatchRepo(conn).store(job, content, result, config_digest=DIGEST)
        if n % 5 == 0:
            ApplicationRepo(conn).set_saved(job, True)
        if n % 7 == 0:
            ApplicationRepo(conn).set_hidden(job, True)
    yield LegacyQuery(conn), ScoredJobQuery(conn)
    conn.close()


@pytest.mark.parametrize(
    "extra",
    [
        {},
        {"include_ineligible": False},
        {"include_unresolved": False},
        {"include_off_target": False},
        {"include_ineligible": False, "include_unresolved": False, "include_off_target": False},
        {"include_ineligible": False, "include_unresolved": False, "exempt_tracked": True},
        {"eligibility": ("UNRESOLVED",), "include_unresolved": False},
        {"saved_only": True},
        {"user_hidden_only": True, "include_user_hidden": True},
        {"excluded_seniorities": ("SENIOR",)},
        {"min_score": 10},
        {"search": "Role"},
        {"providers": ("greenhouse",)},
        {"sort": "title", "direction": "asc"},
    ],
)
@pytest.mark.parametrize("grouped", [True, False])
def test_exact_pages_facets_counters_and_group_election(queries, extra, grouped):
    old, new = queries
    f = JobFilter(group_duplicates=grouped, limit=3, **extra)
    identity = (CONFIG_ID, CONFIG_VERSION)
    total = old.count(*identity, f)
    assert new.count(*identity, f) == total
    assert new.facets(*identity, f) == old.facets(*identity, f)
    for offset in range(0, total + 3, 3):
        page = replace(f, offset=offset)
        assert new.page(*identity, page) == old.page(*identity, page)
    for name in (
        "hidden_by_eligibility",
        "hidden_unresolved",
        "hidden_by_screening",
        "hidden_by_seniority",
        "hidden_by_the_candidate",
    ):
        assert getattr(new, name)(*identity, f) == getattr(old, name)(*identity, f)
