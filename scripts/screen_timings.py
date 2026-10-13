"""Time the requests behind Home, Find jobs and the deep reading, in process.

Read-only use of a database: run it against the synthetic corpus
(`scripts/synthetic_volume.py`), never against somebody's own. "first" is a
freshly started server; "return" is the same server asked again, which is
what coming back to a screen costs.

    python scripts/screen_timings.py --data <out>/data --config <out>/config
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

#: What each screen asks for when it opens, in the order the page asks.
SCREENS: dict[str, list[tuple[str, dict]]] = {
    "home": [
        ("/api/health", {}),
        ("/api/home", {}),
        ("/api/firstrun", {}),
    ],
    "find_jobs": [
        ("/api/jobs", {"narrowings": ["later"]}),
        ("/api/jobs/narrowings", {}),
    ],
    "deep": [
        ("/api/deep", {}),
        ("/api/deep/coverage", {}),
        ("/api/deep/reviews", {}),
    ],
}


def main() -> None:
    from career_agent.web.api import JobsApi
    from career_agent.web.server import ApiError, ServerConfig

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--rounds", type=int, default=2)
    args = parser.parse_args()
    app = JobsApi(
        ServerConfig(db_path=args.data / "personal.db", config_dir=args.config, port=0),
        quiet=True,
    )
    for round_ in range(args.rounds):
        label = "first" if round_ == 0 else "return"
        for screen, calls in SCREENS.items():
            total = 0.0
            parts = []
            for path, query in calls:
                started = time.perf_counter()
                try:
                    app.handle_api("GET", path, query, {})
                    outcome = ""
                except ApiError as exc:
                    outcome = f" ({exc.status})"
                took = time.perf_counter() - started
                total += took
                parts.append(f"{path} {took:.2f}s{outcome}")
            print(f"{label:6} {screen:9} {total:6.2f}s  " + "; ".join(parts), flush=True)


if __name__ == "__main__":
    main()
