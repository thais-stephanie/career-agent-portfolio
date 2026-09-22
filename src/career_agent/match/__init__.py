"""Deterministic matching: the LLM observes, code decides (ADR-0001).

Nothing in this package asks a model whether a posting is a match. It reads
`config/search*.yaml`, finds configured phrases in the posting, and computes
every gate, classification and point from what it found. The one public entry
point is `match_job`; the modules under it are the steps it runs, exported so
they can be tested and explained one at a time.
"""

from career_agent.match.engine import JobFacts, evaluate_screening, match_job

__all__ = ["JobFacts", "evaluate_screening", "match_job"]
