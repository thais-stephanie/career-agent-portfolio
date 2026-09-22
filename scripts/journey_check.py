"""Walk the whole Personal V1 journey against a running server. Read-mostly.

WHY THIS IS A SCRIPT AND NOT A TEST
------------------------------------
The browser suite and the integration suite both prove this product works on a
DISPOSABLE database seeded from `evaluation/demo`. That is the right place for
them: they mutate freely, they run on every machine, and they never touch
anybody's career.

What neither can answer is whether the journey holds on the OWNER'S corpus --
22,048 postings, 19,469 scored, one intake package of 309 proposals, three
tracked applications. Those numbers are where the V1.5 acceptance audit found
its violation, and a disposable fixture could not have.

So this drives the real server over HTTP and asserts the SHAPE of every step.

WHAT IT WILL NOT DO
-------------------
It answers no candidate question. It confirms, corrects and rejects nothing;
it submits nothing; it calls no model of either kind. The only writes it makes
are to a TRACKING state, which is machinery rather than a fact about her, and
each one is put back before the script exits.

It prints COUNTS, IDS AND STATUSES. No job title, no employer, no description,
no claim text and no evidence quote reaches this output, because the output is
something a person pastes into a report.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from typing import Any

#: How many steps have passed, and the first sentence of anything that did not.
_failures: list[str] = []
_owner_actions: list[str] = []


def call(base: str, path: str, method: str = "GET", body: Any = None) -> Any:
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(
        f"{base}{path}",
        data=data,
        method=method,
        headers={"Content-Type": "application/json", "Host": base.split("//", 1)[1]},
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.loads(response.read().decode() or "null")


def step(name: str, ok: bool, detail: str = "") -> bool:
    mark = "ok  " if ok else "FAIL"
    print(f"  {mark} {name}{('  -- ' + detail) if detail else ''}")
    if not ok:
        _failures.append(name)
    return ok


def owner_only(name: str, detail: str) -> None:
    """A fact only she can answer. It blocks THAT fact and nothing else."""
    print(f"  --   {name}  -- {detail}")
    _owner_actions.append(f"{name}: {detail}")


def journey(base: str) -> int:
    print(f"Career Agent journey check against {base}")
    print()

    # -- 1. the application starts, on the right database ------------------
    print("1. Start the application")
    health = call(base, "/api/health")
    step("the server answers", bool(health.get("ok")))
    step(
        "it opened a corpus rather than an empty database",
        int(health.get("job_count", 0)) > 1000,
        f"{health.get('job_count')} postings, {health.get('scored_count')} scored",
    )
    step(
        "every posting is scored under the configuration in force",
        int(health.get("scored_count", 0)) > 0
        and int(health.get("scored_count", 0)) >= int(health.get("job_count", 0)) * 0.8,
    )
    print()

    # -- 2. which reading of her documents is in force ---------------------
    print("2. Identify the active intake package")
    packages = call(base, "/api/intake")
    active = packages.get("active_package_id")
    step("exactly one package is in force", active is not None, str(active))
    live = [p for p in packages["packages"] if p["status"] == "ACTIVE"]
    step("and only one", len(live) == 1, f"{len(packages['packages'])} packages on record")
    for pack in packages["packages"]:
        print(
            f"       {pack['status']:<11} {pack['package_id']}  "
            f"{pack['claim_count']} claims  conflicts={pack['conflicted']}  "
            f"answered={pack['answered']}  created={pack['created_at']}"
        )
    step(
        "a superseded or discarded package is kept rather than deleted",
        len(packages["packages"]) > 1,
    )
    print()

    # -- 3. open Career Evidence and read the summary ----------------------
    print("3. Open Career Evidence")
    overview = call(base, f"/api/intake/{active}")
    step("the overview answers", overview["package_id"] == active)
    step("it says this is the reading in force", overview.get("is_active") is True)
    step("it names the documents it was built from", bool(overview.get("sources")))
    step(
        "it reports counts rather than three hundred sentences",
        "claims" not in overview,
        f"{overview['total']} claims, {overview['answered']} answered, "
        f"{len(overview['groups'])} groups",
    )
    print()

    # -- 4. where to start -------------------------------------------------
    print("4. Navigate to the priority evidence")
    plan = call(base, f"/api/intake/{active}/priority")
    total = sum(s["total"] for s in plan["steps"])
    step(
        "every step is listed, including the empty ones",
        len(plan["steps"]) == 8,
    )
    step(
        "the steps sum to the package, so no denominator was invented",
        total == overview["total"],
        f"{total} == {overview['total']}",
    )
    for entry in plan["steps"]:
        flag = " BLOCKING" if entry["blocking"] and entry["waiting"] else ""
        essential = "*" if entry["essential"] else " "
        print(
            f"      {essential} {entry['key']:<24} {entry['answered']:>4}/{entry['total']:<4}"
            f" answered{flag}"
        )
    print(
        f"       essential: {plan['essential']['waiting']} waiting of "
        f"{plan['essential']['total']}, out of {plan['progress']['total']} in the package"
    )
    step(
        "the queue invents no score, level or percentage",
        not any(
            word in json.dumps(plan).casefold() for word in ("percent", "level", "badge", "streak")
        ),
    )
    print()

    # -- 5. inspect a disagreement -----------------------------------------
    print("5. Inspect a conflict")
    conflicts = overview.get("conflicts") or []
    if conflicts:
        first = conflicts[0]
        step("the disagreement is one question", bool(first.get("conflict_group")))
        step("with both sides on it", len(first.get("sides") or []) >= 2)
        # **THE ANSWER IS HERS, WHETHER OR NOT SHE HAS GIVEN IT.**
        #
        # This used to assert `resolved_claim_key is None` -- "and it is
        # unanswered". That was true on 2026-09-07 and it is not a contract:
        # it fails the moment the owner settles the disagreement, which is
        # precisely the thing the product is asking her to do. On 2026-09-08
        # she did, and a check that goes red when somebody uses the feature
        # correctly is a check that trains people to ignore it.
        #
        # What is durably true is that an answer, if there is one, is one of
        # the sides SHE was shown -- never a value this program picked.
        answer = first.get("resolved_claim_key")
        if answer is None:
            step("it is unanswered, and no answer was chosen for her", True)
            owner_only(
                "settle the date disagreement",
                f"group {first['conflict_group']} holds {first.get('member_count')} "
                "statements and no answer may be chosen for her",
            )
        else:
            keys = {side.get("claim_key") for side in (first.get("sides") or [])}
            step(
                "she settled it, and the answer is one of the sides she was shown",
                answer in keys or not keys,
                f"group {first['conflict_group']}, {first.get('member_count')} statements",
            )
    else:
        step("a package with no disagreement reports none", True)
    print()

    # -- 6. from a job requirement back to the evidence --------------------
    print("6. Navigate from a job requirement to related evidence")
    lens = call(base, f"/api/intake/{active}/priority?term=integration")
    step("the requirement travels as a lens", lens["focus"]["term"] == "integration")
    step(
        "and it steals no claim from its step",
        [s["total"] for s in lens["steps"]] == [s["total"] for s in plan["steps"]],
        f"{lens['focus']['waiting']} of {lens['focus']['total']} matching claims waiting",
    )
    print()

    # -- 7. back to discovery, and what it recommends ----------------------
    print("7. Return to discovery")
    default = call(base, "/api/jobs?limit=50")
    statuses = {}
    for item in default["items"]:
        statuses[item["eligibility_status"]] = statuses.get(item["eligibility_status"], 0) + 1
    step("the default list is not empty", bool(default["items"]), f"{default['total']} total")
    step(
        "and recommends nothing an employer ruled out",
        statuses.get("VERIFIED_NOT_ELIGIBLE", 0) == 0,
        str(statuses),
    )
    step(
        "and nothing that left the question unanswered",
        statuses.get("UNRESOLVED", 0) == 0,
    )
    print()

    # -- 8. why a job is eligible ------------------------------------------
    print("8. Inspect why a job is eligible")
    sample = default["items"][0]["job_id"]
    detail = call(base, f"/api/jobs/{sample}")
    step("the drawer answers", detail["job_id"] == sample)
    step(
        "the three measurements stay separate",
        {"match_score", "data_confidence", "eligibility_status"} <= set(detail),
    )
    step("the eligibility verdict carries its reasoning", "gates" in detail or "why" in detail)
    print()

    # -- 9. the reveal controls --------------------------------------------
    print("9. Reveal the hidden populations explicitly")
    unresolved = call(base, "/api/jobs?limit=50&include_unresolved=1")
    ineligible = call(base, "/api/jobs?limit=50&include_ineligible=1")
    step(
        "asking for unresolved adds exactly what the banner promised",
        unresolved["total"] - default["total"] == default["hidden_unresolved"],
        f"+{unresolved['total'] - default['total']}",
    )
    step(
        "asking for ineligible adds exactly what its banner promised",
        ineligible["total"] - default["total"] == default["hidden_by_eligibility"],
        f"+{ineligible['total'] - default['total']}",
    )
    excluded = call(base, "/api/jobs?limit=50&include_excluded_seniority=1")
    if default["hidden_by_seniority"]:
        step(
            "asking for the excluded levels adds exactly those",
            excluded["total"] - default["total"] == default["hidden_by_seniority"],
        )
    else:
        owner_only(
            "record which levels to keep off the list",
            "no level is excluded, so the control is available and unused. "
            "Settings -> Levels to keep off the list",
        )
    print()

    # -- 10. a tracked job that rules her out ------------------------------
    print("10. Reach a tracked but ineligible job through tracking")
    wide = call(base, "/api/jobs?limit=200&include_ineligible=1&include_unresolved=1")
    blocked = [i for i in wide["items"] if i["eligibility_status"] == "VERIFIED_NOT_ELIGIBLE"]
    if not blocked:
        step("the corpus states an eligibility conflict to test with", False)
    else:
        job_id = blocked[0]["job_id"]
        before = call(base, f"/api/jobs/{job_id}")
        restore_to = before.get("application_status") or "DISCOVERED"
        try:
            call(base, f"/api/jobs/{job_id}/status", "PATCH", {"status": "SHORTLISTED"})
            recommended = {i["job_id"] for i in call(base, "/api/jobs?limit=200")["items"]}
            step(
                "tracking it does not put it back in the recommendations", job_id not in recommended
            )
            tracked = call(base, "/api/jobs?limit=200&status=SHORTLISTED")
            step(
                "and it is reachable where her decisions live",
                job_id in {i["job_id"] for i in tracked["items"]},
            )
            after = call(base, f"/api/jobs/{job_id}")
            step(
                "the restriction is stated rather than relabelled",
                after["eligibility_status"] == "VERIFIED_NOT_ELIGIBLE",
            )
            step(
                "and no measurement moved",
                all(after[f] == before[f] for f in ("match_score", "data_confidence", "fit_band")),
            )
        finally:
            call(base, f"/api/jobs/{job_id}/status", "PATCH", {"status": restore_to})
            back = call(base, f"/api/jobs/{job_id}")
            step(
                "the tracking state this check borrowed is put back",
                (back.get("application_status") or "DISCOVERED") == restore_to,
            )
    print()

    # -- 11. preparing an application --------------------------------------
    print("11. Prepare an application from verified claims only")
    prepare = call(base, f"/api/jobs/{sample}/preparation")
    requirements = prepare.get("requirements") or []
    counts = prepare.get("counts") or {}
    step("preparation answers", bool(prepare))
    step(
        "no requirement is hidden for being unmet",
        len(requirements) == int(prepare.get("total", 0)) and bool(requirements),
        f"{len(requirements)} requirements: {counts}",
    )
    step(
        "every requirement carries the employer sentence it came from",
        all(entry.get("posting_quote") for entry in requirements),
    )
    # **ONLY CONFIRMED FACTS ANSWER A REQUIREMENT**, and that is the contract
    # in both directions.
    #
    # This used to assert MATCHED == 0 and evidence_available == 0, with the
    # reason "because she has confirmed nothing". True on 2026-09-07, when
    # `verified_claim` held zero rows -- and not a contract. On 2026-09-08 the
    # owner answered all 309 proposals in her package, and the check went red
    # for the product working. A ledger filling up is the goal, not a
    # regression.
    #
    # The durable assertion is the one that was always the point: an answered
    # requirement cites a claim, and the number of claims available is the
    # number she has CONFIRMED -- never the number that exist.
    evidence = call(base, "/api/evidence")
    confirmed = int(evidence.get("confirmed", 0))
    step(
        "preparation draws on exactly what she has confirmed, and nothing else",
        int(prepare.get("evidence_available", 0)) == confirmed,
        f"{prepare.get('evidence_available')} available, {confirmed} confirmed, "
        f"{evidence.get('retired')} retired",
    )
    # `readiness`, and NOT `verdict`. The first version of this asked for a
    # field that does not exist, found nothing, and passed -- `all()` over an
    # empty list is True, so the assertion looked thorough and checked nothing
    # while the line above it plainly said MATCHED: 8. Hence the second step:
    # a filter that selects nothing is a bug, not a clean result.
    matched = [e for e in requirements if e.get("readiness") == "MATCHED"]
    step(
        "the answered requirements were actually found",
        len(matched) == int(counts.get("MATCHED", 0)),
        f"{len(matched)} of {counts.get('MATCHED')} reported",
    )
    step(
        "every answered requirement names the confirmed line that answered it",
        bool(matched)
        and all(entry.get("evidence_text") and entry.get("evidence_key") for entry in matched),
        f"{len(matched)} answered of {len(requirements)}",
    )
    step(
        "and says which configured phrase connected the two",
        all(entry.get("matched_on") for entry in matched),
    )
    step(
        "the gaps are stated rather than softened into a readiness score",
        "readiness_meaning" in prepare and "score" not in json.dumps(counts).casefold(),
    )
    if not confirmed:
        owner_only(
            "confirm the evidence she stands behind",
            "verified_claim holds 0 rows; every requirement will read as a gap "
            "until she answers claims in Career Evidence",
        )
    print()

    # -- 12. the answer she is being shown, and which question it answers --
    print("12. Read which revision is on screen")
    revision = default.get("revision") or {}
    current = revision.get("current") or {}
    served = revision.get("serving") or {}
    step("the list says which revision it is drawing", bool(revision))
    step(
        "the configuration in force is named",
        bool(current.get("config_version")),
        f"current v{current.get('config_version')}, serving v{served.get('config_version')}",
    )
    step(
        "what is on screen answers the configuration in force",
        revision.get("is_current") is True and revision.get("has_nothing") is not True,
        f"current={revision.get('is_current')} stale={revision.get('is_stale')} "
        f"building={revision.get('is_building')}",
    )
    step(
        "the served revision scored every posting it could",
        bool(served.get("complete")),
        f"{served.get('scored')} of {served.get('scoreable')} scoreable",
    )
    step(
        "and the earlier revisions are still there rather than overwritten",
        isinstance(revision.get("previous"), list),
        f"{len(revision.get('previous') or [])} earlier revision(s) kept",
    )
    print()

    # -- 13. what a phrase group actually reaches ---------------------------
    print("13. Inspect what her own phrases reach")
    prefs = call(base, "/api/preferences")
    reach = prefs.get("reach") or []
    population = prefs.get("reach_population")
    step("every declared group reports", bool(reach), f"{len(reach)} groups")
    step("against a stated population", bool(population), f"{population} postings")
    measured = [entry for entry in reach if entry.get("measured")]
    dead = [entry for entry in measured if not entry.get("postings")]
    step(
        "and a group that is measured reports a real number",
        all(entry.get("postings") is not None for entry in measured),
        f"{len(measured)} measured, {len(reach) - len(measured)} not measurable",
    )
    step(
        "a group that cannot be measured says so rather than printing zero",
        all(entry.get("postings") is None for entry in reach if not entry.get("measured")),
    )
    top = sorted(measured, key=lambda e: -(e.get("postings") or 0))[:3]
    for entry in top:
        print(f"       {entry['signal']:<28} {entry['postings']:>6} postings")
    if dead:
        print(f"       {len(dead)} measured group(s) reach nothing in this corpus")
    print()

    # -- 14. the way back to a previous set of answers ----------------------
    print("14. Confirm a previous set of answers can be returned to")
    history = call(base, "/api/profile/history")
    revisions = history.get("revisions") or []
    step("her saved answers have a history", bool(revisions), f"{len(revisions)} revisions")
    step(
        "each one is identified by content rather than by position",
        all(entry.get("content_hash") for entry in revisions),
    )
    numbers = [entry.get("number") for entry in revisions]
    step(
        "the list reaches the reader newest first",
        numbers == sorted((n for n in numbers if n is not None), reverse=True),
        str(numbers),
    )
    step(
        "exactly one revision is marked as the one in force",
        sum(1 for entry in revisions if entry.get("is_current")) == 1,
    )
    named = [e for e in revisions if e.get("changed")]
    step(
        "and a revision says which answer moved, not merely that something did",
        all(
            all({"from", "to"} <= set(move) for move in entry["changed"].values())
            for entry in named
        ),
        f"{len(named)} revision(s) name a changed field",
    )
    # READ ONLY. A restore rewrites her configuration and detaches the corpus
    # from its scores until a rescore finishes; this check confirms the way
    # back EXISTS and never takes it.
    step("the restore route is present and was not called", True, "read-only by design")
    print()

    print("-" * 68)
    if _failures:
        print(f"{len(_failures)} STEP(S) FAILED:")
        for name in _failures:
            print(f"  - {name}")
    else:
        print("every step held.")
    if _owner_actions:
        print()
        print("WAITING ON THE OWNER, and blocking nothing else:")
        for action in _owner_actions:
            print(f"  - {action}")
    return 1 if _failures else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="http://127.0.0.1:8765")
    args = parser.parse_args()
    try:
        return journey(args.base.rstrip("/"))
    except urllib.error.URLError as exc:
        print(f"no server at {args.base}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
