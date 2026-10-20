"""Provider research POC. NOT a collector.

Fetches at most a handful of public URLs per run, normalises in memory and
prints field coverage. Opens no database, writes nothing outside --save-dir
(which defaults to nowhere), calls no model.

    uv run python scripts/poc/provider_poc.py pinpoint safetywing indrive
    uv run python scripts/poc/provider_poc.py jazzhr <tenant>
    uv run python scripts/poc/provider_poc.py recruitee <tenant>
    uv run python scripts/poc/provider_poc.py breezy <tenant>
    uv run python scripts/poc/provider_poc.py bamboohr <tenant>

Run live on 2026-10-16: pinpoint, jazzhr. NOT run: recruitee, breezy,
bamboohr (field names follow upstream code, unverified against a live board).

Every tenant's own robots.txt is read first and a disallowed path is skipped.
Upstream reference for the endpoints: career-ops-hq/career-ops (MIT),
providers/<name>.mjs at 55a5165. Nothing is copied; endpoints are facts.
"""

from __future__ import annotations

import html
import json
import re
import sys
import time
import urllib.error
import urllib.request
import urllib.robotparser
from typing import Any

UA = "CareerAgent-research/0.1 (+personal job search research; bounded)"
PAUSE_S = 2.0
TAG = re.compile(r"<[^>]+>")


def get(url: str) -> tuple[int, str]:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, ""
    finally:
        time.sleep(PAUSE_S)


def allowed(origin: str, path: str) -> bool:
    status, body = get(origin + "/robots.txt")
    if status == 404:
        return True  # silence is permission for collection (ADR-0018)
    if status != 200:
        return False  # unreadable is not silence
    rp = urllib.robotparser.RobotFileParser()
    rp.parse(body.splitlines())
    return rp.can_fetch(UA, origin + path)


def text(s: Any) -> str:
    return html.unescape(TAG.sub(" ", s or "")).strip() if isinstance(s, str) else ""


# --- one function per family: url -> list[normalised dict] -----------------


def pinpoint(slug: str) -> list[dict]:
    origin = f"https://{slug}.pinpointhq.com"
    if not allowed(origin, "/postings.json"):
        print(f"  {slug}: robots disallows /postings.json (or unreadable) -> skip")
        return []
    status, body = get(origin + "/postings.json")
    if status != 200:
        print(f"  {slug}: HTTP {status}")
        return []
    out = []
    for j in json.loads(body).get("data", []):
        # Upstream #4190: abandoned demo tenants serve canned postings.
        demo = "youtube.com/embed/https://www.youtube.com/embed/pFxm6fszrpw" in (
            j.get("description") or ""
        )
        loc = j.get("location") or {}
        body_parts = [
            j.get(k)
            for k in (
                "description",
                "key_responsibilities",
                "skills_knowledge_expertise",
                "benefits",
            )
        ]
        out.append(
            {
                "id": j.get("id"),
                "title": j.get("title"),
                "url": j.get("url"),
                "location": loc.get("name")
                or ", ".join(x for x in (loc.get("city"), loc.get("province")) if x),
                "workplace_type": j.get("workplace_type"),
                "employment_type": j.get("employment_type"),
                "salary": (
                    j.get("compensation_minimum"),
                    j.get("compensation_maximum"),
                    j.get("compensation_currency"),
                    j.get("compensation_frequency"),
                )
                if j.get("compensation_visible")
                else None,
                "posted_at": None,  # not in the payload
                "deadline_at": j.get("deadline_at"),
                "description_chars": sum(len(text(p)) for p in body_parts),
                "demo_trap": demo,
            }
        )
    return out


JSONLD = re.compile(r"<script[^>]+application/ld\+json[^>]*>(.*?)</script>", re.S | re.I)
JAZZ_LINK = re.compile(
    r'href="(https://[a-z0-9-]+\.applytojob\.com/apply/[A-Za-z0-9]+/[^"]*)"[^>]*>([^<]+)<'
)


def jazzhr(tenant: str, details: int = 2) -> list[dict]:
    origin = f"https://{tenant}.applytojob.com"
    if not allowed(origin, "/apply"):
        print(f"  {tenant}: robots disallows /apply -> skip")
        return []
    status, body = get(origin + "/apply")
    if status != 200:
        print(f"  {tenant}: HTTP {status}")
        return []
    seen, out = set(), []
    for url, title in JAZZ_LINK.findall(body):
        if url in seen:
            continue
        seen.add(url)
        out.append(
            {
                "title": text(title),
                "url": url,
                "description_chars": 0,
                "posted_at": None,
                "location": None,
                "remote_hint": None,
            }
        )
    for row in out[:details]:  # detail page carries JSON-LD (one request each)
        st, page = get(row["url"])
        for blob in JSONLD.findall(page):
            try:
                node = json.loads(blob, strict=False)
            except ValueError:
                continue
            if isinstance(node, dict) and node.get("@type") == "JobPosting":
                row["description_chars"] = len(text(node.get("description")))
                row["posted_at"] = node.get("datePosted")
                row["remote_hint"] = node.get("jobLocationType")
                jl = node.get("jobLocation")
                jl = jl[0] if isinstance(jl, list) and jl else jl
                addr = (jl or {}).get("address", {}) if isinstance(jl, dict) else {}
                row["location"] = ", ".join(
                    str(addr.get(k))
                    for k in ("addressLocality", "addressRegion", "addressCountry")
                    if addr.get(k)
                )
    return out


def recruitee(slug: str) -> list[dict]:
    origin = f"https://{slug}.recruitee.com"
    if not allowed(origin, "/api/offers/"):
        print(f"  {slug}: robots disallows -> skip")
        return []
    status, body = get(origin + "/api/offers/")
    if status != 200:
        print(f"  {slug}: HTTP {status}")
        return []
    return [
        {
            "id": o.get("id"),
            "title": o.get("title"),
            "url": o.get("careers_url"),
            "location": o.get("location"),
            "country": o.get("country_code"),
            "remote": o.get("remote"),
            "salary": o.get("salary"),
            "posted_at": o.get("published_at"),
            "description_chars": len(text(o.get("description"))) + len(text(o.get("requirements"))),
        }
        for o in json.loads(body).get("offers", [])
    ]


def breezy(slug: str) -> list[dict]:
    origin = f"https://{slug}.breezy.hr"
    if not allowed(origin, "/json"):
        print(f"  {slug}: robots disallows -> skip")
        return []
    status, body = get(origin + "/json")
    if status != 200:
        print(f"  {slug}: HTTP {status}")
        return []
    return [
        {
            "id": o.get("id"),
            "title": o.get("name"),
            "url": o.get("url"),
            "location": (o.get("location") or {}).get("name"),
            "remote": (o.get("location") or {}).get("is_remote"),
            "salary": o.get("salary"),
            "posted_at": o.get("published_date"),
            "description_chars": 0,  # list carries none; detail page needed
        }
        for o in json.loads(body)
    ]


def bamboohr(slug: str) -> list[dict]:
    origin = f"https://{slug}.bamboohr.com"
    if not allowed(origin, "/careers/list"):
        print(f"  {slug}: robots disallows -> skip")
        return []
    status, body = get(origin + "/careers/list")
    if status != 200:
        print(f"  {slug}: HTTP {status}")
        return []
    return [
        {
            "id": o.get("id"),
            "title": o.get("jobOpeningName"),
            "url": f"{origin}/careers/{o.get('id')}",
            "location": ", ".join(
                str(v)
                for v in (
                    (o.get("location") or {}).get("city"),
                    (o.get("location") or {}).get("state"),
                )
                if v
            ),
            "remote": o.get("isRemote"),
            "posted_at": None,
            "description_chars": 0,
        }
        for o in json.loads(body).get("result", [])
    ]


FAMILIES = {f.__name__: f for f in (pinpoint, jazzhr, recruitee, breezy, bamboohr)}
REMOTE = re.compile(
    r"\b(remote|anywhere|worldwide|global|latam|latin america|brazil|brasil)\b", re.I
)


def summarise(family: str, tenant: str, rows: list[dict]) -> dict:
    def has(k: str) -> int:
        return sum(1 for r in rows if r.get(k) not in (None, "", 0, False))

    summary = {
        "family": family,
        "tenant": tenant,
        "postings": len(rows),
        "with_location": has("location"),
        "with_description": has("description_chars"),
        "with_posted_at": has("posted_at"),
        "with_salary": has("salary"),
        "remote_or_global_hint": sum(
            1
            for r in rows
            if REMOTE.search(
                " ".join(
                    str(r.get(k) or "")
                    for k in ("location", "workplace_type", "remote", "remote_hint")
                )
            )
            or r.get("remote") is True
        ),
        "demo_trap": has("demo_trap"),
    }
    return summary


def main(argv: list[str]) -> int:
    if len(argv) < 2 or argv[0] not in FAMILIES:
        print(__doc__)
        return 2
    family, tenants = argv[0], argv[1:]
    if len(tenants) > 8:
        print("refusing: at most 8 tenants per run")
        return 2
    for t in tenants:
        rows = FAMILIES[family](t)
        print(json.dumps(summarise(family, t, rows)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
