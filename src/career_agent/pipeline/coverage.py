"""What the registry and the corpus actually cover.

Reads only what is already stored -- no network, no LLM, no new tables. Its job
is to answer, honestly and without flattery, whether we are monitoring enough of
the right market for later discovery quality to be measurable at all.

Two things it deliberately refuses to do:

**It never calls HQ market "eligibility".** A company's HQ is a discovery and
ranking signal. A worldwide-remote role at a company anywhere is fully eligible;
a residents-only role at a company in a preferred market is not. Those are
different questions and M3 owns the second one.

**It never invents candidate priorities.** When `profile.local.yaml` is absent
it says the market-prioritised view is unavailable and reports the observed
distribution instead. Reading `profile.example.yaml` would produce a report that
looks complete and describes nobody.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from career_agent.domain.timestamps import to_rfc3339_utc

FRESH_WINDOW_DAYS = 7


@dataclass
class CoverageReport:
    registry: dict[str, Any] = field(default_factory=dict)
    providers: dict[str, Any] = field(default_factory=dict)
    markets: dict[str, Any] = field(default_factory=dict)
    jobs: dict[str, Any] = field(default_factory=dict)
    health: dict[str, Any] = field(default_factory=dict)
    candidates: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "registry": self.registry,
            "providers": self.providers,
            "markets": self.markets,
            "jobs": self.jobs,
            "health": self.health,
            "candidates": self.candidates,
        }


def summarise_candidates(rows: list[dict[str, Any]] | None) -> dict[str, Any]:
    """The sourcing funnel and the unsupported-ATS tally.

    This is the only part of the report that reads the review queue rather than
    the database, because a company we could *not* collect leaves no row in the
    corpus by definition. Absence in the database is not evidence of anything;
    the queue is where the evidence about those companies lives.

    The tally answers one product question: is there a single unsupported ATS
    common enough to justify a fourth provider milestone? It counts only
    companies whose vendor was confirmed by name against a live board. Companies
    whose ATS could not be identified stay UNKNOWN and are reported as such --
    an unidentified vendor is a gap in the measurement, and rolling it into a
    winner would invent the answer the question was asking for.
    """
    if not rows:
        return {
            "available": False,
            "note": "no candidate review queue found; the sourcing funnel cannot be reported",
        }

    statuses: dict[str, int] = {}
    vendors: dict[str, int] = {}
    for row in rows:
        status = str(row.get("status") or "UNKNOWN")
        statuses[status] = statuses.get(status, 0) + 1
        if status == "UNSUPPORTED_ATS":
            vendor = str(row.get("ats") or "UNIDENTIFIED")
            vendors[vendor] = vendors.get(vendor, 0) + 1

    identified = sum(vendors.values())
    unidentified = statuses.get("NO_BOARD_FOUND", 0) + statuses.get("INCONCLUSIVE", 0)
    return {
        "available": True,
        "total": len(rows),
        "by_status": dict(sorted(statuses.items(), key=lambda kv: -kv[1])),
        "unsupported_ats": dict(sorted(vendors.items(), key=lambda kv: -kv[1])),
        "ats_identified": identified,
        "ats_unidentified": unidentified,
        "note": (
            f"{identified} of {identified + unidentified} companies without a supported board "
            "had their ATS confirmed by name against a live board; the rest are unidentified, "
            "not vendor-less"
        ),
    }


def _counts(conn: Any, sql: str, *params: Any) -> dict[str, int]:
    return {str(row[0]): int(row[1]) for row in conn.execute(sql, params)}


def _one(conn: Any, sql: str, *params: Any) -> Any:
    row = conn.execute(sql, params).fetchone()
    return row[0] if row is not None else None


def build_report(
    conn: Any,
    market_priorities: dict[str, list[str]] | None = None,
    candidate_rows: list[dict[str, Any]] | None = None,
) -> CoverageReport:
    report = CoverageReport()
    report.candidates = summarise_candidates(candidate_rows)

    # -- registry --------------------------------------------------------
    active_companies = _one(
        conn,
        "SELECT COUNT(DISTINCT c.id) FROM company c"
        " JOIN source_board sb ON sb.company_id = c.id AND sb.active = 1",
    )
    multi_board = _one(
        conn,
        "SELECT COUNT(*) FROM (SELECT company_id FROM source_board WHERE active = 1"
        " GROUP BY company_id HAVING COUNT(*) > 1)",
    )
    report.registry = {
        "companies_total": _one(conn, "SELECT COUNT(*) FROM company"),
        "companies_active_collectable": active_companies,
        "companies_with_multiple_boards": multi_board,
        "companies_with_canonical_domain": _one(
            conn, "SELECT COUNT(*) FROM company WHERE canonical_domain IS NOT NULL"
        ),
        "boards_total": _one(conn, "SELECT COUNT(*) FROM source_board"),
        "boards_active": _one(conn, "SELECT COUNT(*) FROM source_board WHERE active = 1"),
        "by_discovery_source": _counts(
            conn,
            "SELECT COALESCE(discovery_source, 'unrecorded'), COUNT(*) FROM company"
            " GROUP BY COALESCE(discovery_source, 'unrecorded') ORDER BY 2 DESC",
        ),
    }

    # -- provider coverage ------------------------------------------------
    report.providers = {
        "boards_by_provider": _counts(
            conn,
            "SELECT provider, COUNT(*) FROM source_board WHERE active = 1"
            " GROUP BY provider ORDER BY 2 DESC",
        ),
        "companies_by_provider": _counts(
            conn,
            "SELECT provider, COUNT(DISTINCT company_id) FROM source_board WHERE active = 1"
            " GROUP BY provider ORDER BY 2 DESC",
        ),
        "boards_by_discovery_method": _counts(
            conn,
            "SELECT COALESCE(discovery_method, 'unrecorded'), COUNT(*) FROM source_board"
            " GROUP BY COALESCE(discovery_method, 'unrecorded') ORDER BY 2 DESC",
        ),
    }

    # -- market coverage: a discovery signal, never eligibility -----------
    by_market = _counts(
        conn,
        "SELECT COALESCE(c.hq_country, 'unknown'), COUNT(DISTINCT c.id) FROM company c"
        " JOIN source_board sb ON sb.company_id = c.id AND sb.active = 1"
        " GROUP BY COALESCE(c.hq_country, 'unknown') ORDER BY 2 DESC",
    )
    report.markets = {
        "note": "company HQ. A discovery and ranking signal, never geographic eligibility.",
        "active_companies_by_hq": by_market,
        "distinct_markets": len([k for k in by_market if k != "unknown"]),
    }
    if market_priorities:
        buckets: dict[str, int] = {}
        assigned: set[str] = set()
        for bucket in ("WANT", "INTERESTED", "AVOID", "NEVER"):
            codes = set(market_priorities.get(bucket) or ())
            assigned |= codes
            buckets[bucket] = sum(count for code, count in by_market.items() if code in codes)
        buckets["neutral"] = sum(
            count for code, count in by_market.items() if code not in assigned and code != "unknown"
        )
        buckets["unknown_hq"] = by_market.get("unknown", 0)
        report.markets["by_priority"] = buckets
    else:
        # Saying so beats reading profile.example.yaml and producing a report
        # that looks complete and describes nobody.
        report.markets["by_priority"] = None
        report.markets["priority_note"] = (
            "no candidate profile found, so market-prioritised coverage cannot yet be "
            "measured. The observed HQ distribution is reported instead."
        )

    # -- job coverage -----------------------------------------------------
    cutoff = to_rfc3339_utc((datetime.now(tz=UTC) - timedelta(days=FRESH_WINDOW_DAYS)).isoformat())
    top_companies = [
        {"company": row[0], "open_jobs": int(row[1])}
        for row in conn.execute(
            "SELECT c.slug, COUNT(*) FROM job j JOIN company c ON c.id = j.company_id"
            " WHERE j.closed_at IS NULL GROUP BY c.slug ORDER BY 2 DESC LIMIT 15"
        )
    ]
    report.jobs = {
        "open_total": _one(conn, "SELECT COUNT(*) FROM job WHERE closed_at IS NULL"),
        "closed_total": _one(conn, "SELECT COUNT(*) FROM job WHERE closed_at IS NOT NULL"),
        "open_by_provider": _counts(
            conn,
            "SELECT provider, COUNT(*) FROM job WHERE closed_at IS NULL"
            " GROUP BY provider ORDER BY 2 DESC",
        ),
        "with_description": _one(
            conn, "SELECT COUNT(*) FROM job WHERE content_hash IS NOT NULL AND closed_at IS NULL"
        ),
        "distinct_descriptions": _one(conn, "SELECT COUNT(*) FROM job_raw"),
        "archived_payloads": _one(conn, "SELECT COUNT(*) FROM job_provider_payload"),
        "companies_with_open_jobs": _one(
            conn, "SELECT COUNT(DISTINCT company_id) FROM job WHERE closed_at IS NULL"
        ),
        # posted_at is RFC 3339 UTC at every provider (M1B.1), so this is a
        # string comparison rather than date arithmetic in SQL -- hazard A2.
        "posted_last_7_days": _one(
            conn,
            "SELECT COUNT(*) FROM job WHERE closed_at IS NULL AND posted_at IS NOT NULL"
            " AND posted_at >= ?",
            cutoff,
        ),
        "posted_last_7_days_by_provider": _counts(
            conn,
            "SELECT provider, COUNT(*) FROM job WHERE closed_at IS NULL"
            " AND posted_at IS NOT NULL AND posted_at >= ? GROUP BY provider ORDER BY 2 DESC",
            cutoff,
        ),
        "top_companies_by_open_jobs": top_companies,
        "fresh_window_days": FRESH_WINDOW_DAYS,
    }

    # -- health -----------------------------------------------------------
    empty_boards = _one(
        conn,
        "SELECT COUNT(*) FROM source_board sb WHERE sb.active = 1"
        " AND sb.last_collected_at IS NOT NULL"
        " AND NOT EXISTS (SELECT 1 FROM job j WHERE j.source_board_id = sb.id"
        "                 AND j.closed_at IS NULL)",
    )
    failed = [
        {"company": row[0], "provider": row[1], "board": row[2], "error": row[3]}
        for row in conn.execute(
            "SELECT c.slug, sb.provider, sb.board_identifier, sb.last_error"
            " FROM source_board sb JOIN company c ON c.id = sb.company_id"
            " WHERE sb.last_error IS NOT NULL ORDER BY c.slug"
        )
    ]
    holds = [
        {
            "company": row[0],
            "provider": row[1],
            "board": row[2],
            "since": row[3],
            "observed": row[4],
        }
        for row in conn.execute(
            "SELECT c.slug, sb.provider, sb.board_identifier, sb.suspicious_since,"
            " sb.suspicious_observed FROM source_board sb"
            " JOIN company c ON c.id = sb.company_id"
            " WHERE sb.suspicious_since IS NOT NULL ORDER BY sb.suspicious_since"
        )
    ]
    last_run = conn.execute(
        "SELECT started_at, finished_at, status, stats_json FROM pipeline_run"
        " WHERE stage = 'collect' ORDER BY started_at DESC, id DESC LIMIT 1"
    ).fetchone()
    report.health = {
        "boards_never_collected": _one(
            conn,
            "SELECT COUNT(*) FROM source_board WHERE active = 1 AND last_collected_at IS NULL",
        ),
        "boards_with_last_error": len(failed),
        "failed_boards": failed[:20],
        "empty_boards": empty_boards,
        "suspicious_holds": holds,
        "last_collection_started_at": last_run["started_at"] if last_run else None,
        "last_collection_status": last_run["status"] if last_run else None,
        "last_collection_stats": last_run["stats_json"] if last_run else None,
    }
    return report
