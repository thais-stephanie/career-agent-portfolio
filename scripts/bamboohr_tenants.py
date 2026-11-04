"""Validate BambooHR tenant CANDIDATES before anything registers them.

    uv run python scripts/bamboohr_tenants.py firstintuition other-employer

A candidate is a name somebody published: an employer's apply link on an
aggregator, or a link on the employer's own careers page. There is no
enumeration and no index walk: only the names given are asked. A candidate is
ACCEPTED only when its own board says so: robots.txt read first and allowing
`/careers/list`, `/careers/list` answering 200 JSON (an unknown tenant, or one
whose careers page is switched off, answers 302 to the vendor; redirects are
not followed) in the documented `{meta, result: [...]}` shape, with at least
one posting.

Writes nothing. Prints each verdict and, for the accepted ones, the
`companies.yaml` board entries to review and paste. One request per second at
most; an honest User-Agent; no cookie, no session.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import httpx  # noqa: E402

from career_agent.net.robots import RobotsDecision, RobotsPolicy  # noqa: E402
from career_agent.providers.bamboohr import (  # noqa: E402
    TENANT,
    VENDOR_HOSTS,
    origin,
    validate_listing,
)

USER_AGENT = "CareerAgent/0.2 (+https://github.com/thais-stephanie/career-agent-portfolio)"
METHOD = "aggregator_apply_link_validated_2026_11_03"


def verdict(client: httpx.Client, robots: RobotsPolicy, tenant: str) -> tuple[bool, str, int]:
    """(accepted, why, postings)."""
    if not TENANT.fullmatch(tenant) or tenant in VENDOR_HOSTS:
        return False, "not a tenant name", 0
    url = f"{origin(tenant)}/careers/list"
    decision = robots.decide(url)
    if decision is not RobotsDecision.ALLOWED:
        return False, f"robots.txt: {decision.value}", 0
    time.sleep(1)
    response = client.get(url, headers={"Accept": "application/json"}, timeout=30)
    if response.is_redirect:
        return False, f"redirected to {response.headers.get('location')} (no public board)", 0
    if response.status_code != 200:
        return False, f"careers/list answered {response.status_code}", 0
    try:
        body = response.json()
    except ValueError:
        return False, "careers/list is not JSON", 0
    if refusal := validate_listing(body):
        return False, refusal, 0
    rows = [r for r in body["result"] if isinstance(r, dict)]
    if not rows:
        return False, "no open postings (nothing to register yet)", 0
    return True, "active board", len(rows)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("tenants", nargs="+")
    args = parser.parse_args()
    accepted: list[str] = []
    with httpx.Client(headers={"User-Agent": USER_AGENT}, follow_redirects=False) as client:
        robots = RobotsPolicy(agent=USER_AGENT)
        for tenant in sorted(set(args.tenants)):
            ok, why, count = verdict(client, robots, tenant)
            print(f"{'ACCEPT' if ok else 'refuse'}  {tenant:40} {count:5}  {why}")
            if ok:
                accepted.append(tenant)
            time.sleep(1)
    print(f"\n{len(accepted)} of {len(set(args.tenants))} candidates accepted\n")
    for tenant in accepted:
        print(f"      - provider: bamboohr\n        board_identifier: \"{tenant}\"\n"
              f"        board_url: \"{origin(tenant)}/careers\"\n"
              f"        discovery_method: \"{METHOD}\"")  # fmt: skip


if __name__ == "__main__":
    main()
