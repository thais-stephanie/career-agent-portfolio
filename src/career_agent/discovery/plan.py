"""The targeted lane's query plan: bounded terms times market scopes.

Terms, strongest evidence first:

1. `anchor` -- a role the person named (their exact words);
2. `alias`  -- a role-family alias a planner generated from an anchor;
3. `work`   -- the person's own work-intent phrases, so somebody who named no
               role is still searched for by the work they want.

Never one giant keyword query: each term is its own search, in each scope the
source can express, and results are deduplicated AFTER retrieval so a scope
that surfaces a different subset is never pruned before it is measured.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from career_agent.config.search_config import SearchConfig
from career_agent.discovery.anchors import RoleAnchors, folded, load_anchors
from career_agent.discovery.scopes import MarketScope, market_scopes

#: At most this many work phrases join the plan when no anchor exists, and
#: this many when anchors do: the anchors already say what to search for.
MAX_WORK_TERMS_ALONE = 20
MAX_WORK_TERMS_WITH_ANCHORS = 4
MAX_ALIAS_TERMS = 12


@dataclass(frozen=True)
class QueryTerm:
    text: str
    #: `anchor`, `alias` or `work`.
    origin: str


@dataclass(frozen=True)
class Query:
    term: QueryTerm
    scope: MarketScope

    @property
    def key(self) -> str:
        return f"{folded(self.term.text)}|{self.scope.key}"


def _work_phrases(config: SearchConfig) -> list[str]:
    weights = config.scoring.components.responsibilities.weights
    ranked = sorted(
        (sid for sid, w in weights.items() if w > 0),
        key=lambda sid: (-weights[sid], list(weights).index(sid)),
    )
    out = []
    for sid in ranked:
        signal = config.lexicon.get(sid)
        if signal is not None:
            out.append(str(signal.label).split(" (")[0])
    return out


def query_terms(config: SearchConfig, anchors: RoleAnchors) -> tuple[QueryTerm, ...]:
    from career_agent.discovery.aliases import effective

    anchors = effective(anchors)
    terms: list[QueryTerm] = [QueryTerm(a.text, "anchor") for a in anchors.anchors]
    terms += [QueryTerm(text, "alias") for text in anchors.alias_texts()[:MAX_ALIAS_TERMS]]
    limit = MAX_WORK_TERMS_WITH_ANCHORS if anchors.anchors else MAX_WORK_TERMS_ALONE
    terms += [QueryTerm(text, "work") for text in _work_phrases(config)[:limit]]
    seen: set[str] = set()
    unique = []
    for term in terms:
        key = folded(term.text)
        if key and key not in seen:
            seen.add(key)
            unique.append(term)
    return tuple(unique)


def plan_queries(
    terms: tuple[QueryTerm, ...],
    scopes: tuple[MarketScope, ...],
    *,
    max_queries: int,
) -> tuple[Query, ...]:
    """Every term in every scope, in term order, capped at `max_queries`."""
    if max_queries <= 0:
        return ()
    plan: list[Query] = []
    seen: set[str] = set()
    for term in terms:
        for scope in scopes:
            query = Query(term, scope)
            if query.key in seen:
                continue
            seen.add(query.key)
            plan.append(query)
            if len(plan) >= max_queries:
                return tuple(plan)
    return tuple(plan)


def targeted_plan(
    config: SearchConfig,
    config_dir: Path,
    *,
    max_queries: int,
    scope_filter: object = None,
) -> tuple[Query, ...]:
    """The plan for one source. `scope_filter(scope) -> bool` keeps only the
    scopes that source can express."""
    scopes = market_scopes(config)
    if callable(scope_filter):
        scopes = tuple(s for s in scopes if scope_filter(s))
    return plan_queries(
        query_terms(config, load_anchors(config_dir)), scopes, max_queries=max_queries
    )
