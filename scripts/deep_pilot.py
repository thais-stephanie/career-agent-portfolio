"""A bounded deep-reading pilot over NAMED postings. Preview by default.

    uv run python scripts/deep_pilot.py --db <personal.db> --config-dir <config>
        --ids <pilot.json> --out <dir>                       # preview, sends nothing
    ... --execute --max-calls 10                             # the owner's decision

The preview writes, for every posting, the EXACT system prompt and user
message a run would send, and what each evidence id stands for at home. The
run refuses unless: `--execute`, `--max-calls` covers the set, the profile
chose Claude Code by name and consented to share evidence with it, and
`claude auth status` reports a claude.ai sign-in. It never falls back to
another provider and pauses on a plan limit. Whether the account's "extra
usage" is on cannot be read from here: the owner checks it first.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from career_agent.semantic import deep
from career_agent.semantic.deep_run import context, request_for, run_deep, select
from career_agent.semantic.providers.local_cli import ClaudeCodeProvider
from career_agent.semantic.settings import load_settings
from career_agent.web.api import JobsApi
from career_agent.web.server import ServerConfig


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--config-dir", type=Path, required=True)
    parser.add_argument("--ids", type=Path, required=True, help="JSON list of {job_id, group}")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--max-calls", type=int, default=0)
    args = parser.parse_args(argv)

    pilot = json.loads(args.ids.read_text(encoding="utf-8"))
    ids = {str(row["job_id"]) for row in pilot}
    api = JobsApi(ServerConfig(db_path=args.db, config_dir=args.config_dir, port=0), quiet=True)
    config = api.search_config()
    settings = load_settings(args.config_dir)
    share = "claude_code" in settings.share_evidence_with
    conn = api.connect()
    try:
        ctx = context(conn, config, share)
        chosen = select(
            conn, config, provider_id="claude_code", limit=len(ids), only=ids
        ).candidates
        missing = ids - {c.job_id for c in chosen}
        requests = []
        for candidate in chosen:
            message, items, sent = request_for(ctx, candidate)
            requests.append(
                {
                    "job_id": candidate.job_id,
                    "title": candidate.title,
                    "local_score": candidate.local_score,
                    "group": next(r["group"] for r in pilot if r["job_id"] == candidate.job_id),
                    "user_message": json.loads(message),
                    "evidence_ids_at_home": sent,
                    "characters_sent": len(deep.SYSTEM_PROMPT) + len(message),
                }
            )
        args.out.mkdir(parents=True, exist_ok=True)
        (args.out / "preview.json").write_text(
            json.dumps(
                {
                    "provider": "Claude Code, Claude subscription",
                    "mode_in_profile": settings.mode.value,
                    "evidence_shared": share,
                    "contract": deep.contract_identity(),
                    "system_prompt": deep.SYSTEM_PROMPT,
                    "answer_schema": deep.answer_schema(),
                    "profile_digest": ctx.digest,
                    "not_in_main_list": sorted(missing),
                    "requests": requests,
                },
                ensure_ascii=False,
                indent=1,
            ),
            encoding="utf-8",
        )
        print(f"preview: {len(requests)} postings, {len(missing)} not in the main list")
        print(f"evidence shared: {share}; mode in profile: {settings.mode.value}")
        if not args.execute:
            print("nothing was sent (preview only)")
            return 0
        if settings.mode.value != "claude_code" or not share:
            print("refused: the profile must choose claude_code and consent to share evidence")
            return 2
        if args.max_calls < len(requests):
            print(f"refused: --max-calls {args.max_calls} < {len(requests)} postings")
            return 2
        provider = ClaudeCodeProvider()
        if provider.availability().facts.get("auth") != "subscription":
            print("refused: Claude Code is not signed in with a Claude subscription")
            return 2
        stats = run_deep(conn, config, provider, share=True, limit=len(requests), only=ids)
        print(json.dumps(stats.as_dict(), indent=1))
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
