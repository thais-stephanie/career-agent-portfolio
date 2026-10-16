"""Validate Pinpoint tenant CANDIDATES before anything registers them.

    uv run python scripts/pinpoint_tenants.py --from-common-crawl [--index CC-MAIN-2026-34]
    uv run python scripts/pinpoint_tenants.py acme other-employer

A candidate is only a name: from an employer's careers page, or from a public
web index (Common Crawl lists hosts real pages linked, weeks late and
incomplete). It is ACCEPTED only when its own board says so: robots.txt read
first and allowing `/postings.json`, `postings.json` answering the documented
`{data: [...]}` with at least one posting, every posting link on the tenant's
own host, and none of the canned demonstration postings an abandoned trial
tenant serves (`providers.pinpoint.validate_listing`).

Writes nothing. Prints each verdict and, for the accepted ones, the
`companies.yaml` board entries to review and paste. One request per second
at most; an honest User-Agent; no cookie, no session.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import httpx  # noqa: E402

from career_agent.net.robots import RobotsDecision, RobotsPolicy  # noqa: E402
from career_agent.providers.pinpoint import (  # noqa: E402
    TENANT,
    VENDOR_DOMAIN,
    VENDOR_HOSTS,
    origin,
    validate_listing,
)

USER_AGENT = "CareerAgent/0.2 (+https://github.com/thais-stephanie/career-agent-portfolio)"
CDX = "https://index.commoncrawl.org/{index}-index"
METHOD = "common_crawl_candidate_validated_2026_10_16"


def common_crawl_candidates(client: httpx.Client, index: str, limit: int) -> list[str]:
    """Tenant names Common Crawl saw on the vendor's domain. Candidates only."""
    response = client.get(
        CDX.format(index=index),
        params={
            "url": VENDOR_DOMAIN,
            "matchType": "domain",
            "output": "json",
            "fl": "url",
            "limit": str(limit),
        },  # fmt: skip
        timeout=60,
    )
    response.raise_for_status()
    names: set[str] = set()
    for line in response.text.splitlines():
        try:
            host = (urlsplit(json.loads(line)["url"]).hostname or "").lower()
        except (ValueError, KeyError):
            continue
        if host.endswith("." + VENDOR_DOMAIN):
            name = host[: -len(VENDOR_DOMAIN) - 1]
            if TENANT.fullmatch(name) and name not in VENDOR_HOSTS:
                names.add(name)
    return sorted(names)


def verdict(client: httpx.Client, robots: RobotsPolicy, tenant: str) -> tuple[bool, str, int]:
    """(accepted, why, postings)."""
    if not TENANT.fullmatch(tenant) or tenant in VENDOR_HOSTS:
        return False, "not a tenant name", 0
    base = origin(tenant)
    decision = robots.decide(f"{base}/postings.json")
    if decision is not RobotsDecision.ALLOWED:
        return False, f"robots.txt: {decision.value}", 0
    time.sleep(1)
    response = client.get(f"{base}/postings.json", timeout=30)
    if response.status_code != 200:
        return False, f"postings.json answered {response.status_code}", 0
    try:
        body = response.json()
    except ValueError:
        return False, "postings.json is not JSON", 0
    if refusal := validate_listing(body, tenant):
        return False, refusal, 0
    rows = [r for r in body["data"] if isinstance(r, dict)]
    if not rows:
        return False, "no open postings (nothing to register yet)", 0
    host = f"{tenant}.{VENDOR_DOMAIN}"
    if any((urlsplit(str(r.get("url") or "")).hostname or "") != host for r in rows):
        return False, "a posting link is off the tenant's host", len(rows)
    return True, "active board", len(rows)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("tenants", nargs="*")
    parser.add_argument("--from-common-crawl", action="store_true")
    parser.add_argument("--index", default="CC-MAIN-2026-34")
    parser.add_argument("--limit", type=int, default=4000)
    parser.add_argument("--max-tenants", type=int, default=60)
    args = parser.parse_args()
    with httpx.Client(headers={"User-Agent": USER_AGENT}, follow_redirects=False) as client:
        candidates = list(args.tenants)
        if args.from_common_crawl:
            candidates += common_crawl_candidates(client, args.index, args.limit)
        candidates = sorted(set(candidates))[: args.max_tenants]
        accepted: list[tuple[str, int]] = []
        robots = RobotsPolicy(agent=USER_AGENT)
        for tenant in candidates:
            ok, why, count = verdict(client, robots, tenant)
            print(f"{'ACCEPT' if ok else 'refuse'}  {tenant:40} {count:5}  {why}")
            if ok:
                accepted.append((tenant, count))
            time.sleep(1)
    print(f"\n{len(accepted)} of {len(candidates)} candidates accepted\n")
    for tenant, _count in accepted:
        print(f"      - provider: pinpoint\n        board_identifier: \"{tenant}\"\n"
              f"        board_url: \"{origin(tenant)}\"\n"
              f"        discovery_method: \"{METHOD}\"")  # fmt: skip


if __name__ == "__main__":
    main()
