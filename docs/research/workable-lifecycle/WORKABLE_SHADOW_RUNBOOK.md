# Workable shadow: flags, activation and rollback

Stages 0 and 1 of `WORKABLE_LIFECYCLE_IMPLEMENTATION_PLAN.md`. **Nothing here
closes a posting.** The code is `src/career_agent/pipeline/workable_presence.py`.

## Feature flags

All are user environment variables, read when a read ends. A flag is on only
when its value is exactly `on` (case does not matter). Anything else, or no
variable, is off. All three start off.

| Variable | Needs | What it adds |
|---|---|---|
| `CAREER_AGENT_WORKABLE_PRESENCE` | nothing | After each complete, distinct feed version: the ledger row, `last_present_read`, miss counters |
| `CAREER_AGENT_WORKABLE_SHADOW` | PRESENCE | Hypothetical decisions in `workable_shadow_event` and `last_decision` |
| `CAREER_AGENT_WORKABLE_AUDIT` | SHADOW | Network: a seeded sample of up to 50 due postings per read, checked on `apply.workable.com`, 2 s apart, at most 100 requests per run, stopping on 429 or 403 |

The reopen fix is **not** behind a flag. A closed Workable posting the feed lists
again reopens, as in every other source. On 2026-11-05 the real catalogue held 0
closed Workable rows, so it changes nothing there today.

## What runs when

- The flags start no read. They act only at the end of a read the update was doing
  anyway, on its usual schedule.
- Counts as an observation: a read of the shared feed to its end, with a readable
  `Last-Modified` not recorded before.
- Does not count: 304, a cut or malformed transfer, a Stop, a budget, a failure,
  a filtered command-line pass, the same version twice.
- Decisions change only on `BASELINE` and `COUNTED` reads. A held read still resets
  postings it listed, and counts no miss.

| Read outcome | Meaning |
|---|---|
| `BASELINE` | The first read evaluated. Rows it does not list are marked `baseline`: the backlog |
| `COUNTED` | Passed the guards; misses counted |
| `HELD_FEED_EMPTY` | No postings at all |
| `HELD_FEED_SHRANK` | Under 97 % of the last counted read |
| `HELD_TOO_MANY_NEW_MISSES` | More than 15 % of open rows would gain a first miss |
| `HELD_OLDER_VERSION` | Not newer than the last counted read; nothing touched |

| Decision | Reason | Meaning |
|---|---|---|
| `WOULD_HOLD` | `BASELINE_ABSENT`, `FIRST_MISS` | Missing, not due yet |
| `WOULD_CLOSE` | `BACKLOG`, `MISSED_REPEATEDLY` | 2 counted misses over at least 24 h |
| `WOULD_KEEP` | `REAPPEARED`, `REAPPEARED_FROM_BASELINE`, `EMPLOYER_STILL_LISTS` | Listed again before due, or the sample answered published. The flap rate in the report uses only `REAPPEARED` with one miss over `FIRST_MISS` |
| `WOULD_REOPEN` | `REAPPEARED` | Came back after a `WOULD_CLOSE`: a closure that would have been wrong |

## Controlled activation (after a separate approval and integration)

1. Check that no collector or pytest is running, then restart the app once with
   the new code. The server applies migration 0061 on opening: four empty tables.
2. Set the flags, in this order, each for the user:

   ```powershell
   [Environment]::SetEnvironmentVariable('CAREER_AGENT_WORKABLE_PRESENCE', 'on', 'User')
   [Environment]::SetEnvironmentVariable('CAREER_AGENT_WORKABLE_SHADOW', 'on', 'User')
   ```

3. Restart the app so that it sees them.
4. After the first read reports `BASELINE`, check the report, then turn the audit on
   the same way (`CAREER_AGENT_WORKABLE_AUDIT`) and restart.
5. Observe at least 3 complete reads over at least 7 days:

   ```bash
   python scripts/workable_shadow_report.py data/shared/catalogue.db
   ```

Stage 1 ends with the owner reading that report against plan §7. Stages 2 and 3
need their own approval.

If flags stay off for weeks, departures build up past the 15 % guard and reads
hold. That is the guard working. To start over, clear the four tables (below,
keeping the ledger row), and the next read is a new baseline.

## Rollback

| Level | Do | Effect |
|---|---|---|
| Stop observing | Remove or set to `off` the three variables, restart | Nothing more is written. Reads go on as before. Tables stay |
| Start over | `DELETE FROM` the four tables, children first | Observation history lost; the next read is a new baseline |
| Remove | The SQL below on `data/shared/catalogue.db`, with the app closed | Tables gone. Profiles untouched |
| Older code | Check out the older build | Works as it is: a ledger row beyond its files is allowed |

```sql
BEGIN IMMEDIATE;
DROP TABLE workable_audit_sample;
DROP TABLE workable_shadow_event;
DROP TABLE workable_presence;
DROP TABLE workable_feed_read;
DELETE FROM schema_migration WHERE version = 61;
COMMIT;
```

Since nothing else reads or writes these tables, no profile, score, queue or
posting depends on them. No backup is needed for this rollback; the shadow changes
no existing row.
