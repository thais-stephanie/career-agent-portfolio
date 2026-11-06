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

## The two populations

Every absence belongs to one of two populations, named by each event's `reason`, so
its origin stays readable however many events the posting gathers.

- **Backlog:** already missing from the baseline read. It stays backlog until the feed
  lists it again, even if the employer's page answers that it is still published.
- **New misses:** listed after the baseline, then gone. A backlog posting that the
  feed lists again and that then leaves is a new miss.

Every decision is **hypothetical**. `WOULD_REOPEN` is a posting back in the feed after
a `WOULD_CLOSE`; its `closed_at` never changed and nothing was reopened. The report's
`real_closed_workable_postings` counts real closures, kept apart on purpose.

| Decision | Reason (stored) | Population (derived in the report) | Meaning |
|---|---|---|---|
| `WOULD_HOLD` | `BASELINE_ABSENT` | `BACKLOG` | Missing, not due yet |
| `WOULD_HOLD` | `FIRST_MISS` | `NEW_MISSES` | Missing, not due yet |
| `WOULD_CLOSE` | `BACKLOG` | `WOULD_CLOSE_BACKLOG` | 2 counted misses over at least 24 h |
| `WOULD_CLOSE` | `MISSED_REPEATEDLY` | `WOULD_CLOSE_NEW` | 2 counted misses over at least 24 h |
| `WOULD_KEEP` or `WOULD_REOPEN` | `REAPPEARED_FROM_BASELINE` | `REAPPEARED_FROM_BACKLOG` | Listed again, before or after a `WOULD_CLOSE` |
| `WOULD_KEEP` or `WOULD_REOPEN` | `REAPPEARED` | `REAPPEARED_FROM_NEW_MISSES` | Listed again, before or after a `WOULD_CLOSE` |
| `WOULD_KEEP` | `EMPLOYER_STILL_LISTS` | `EMPLOYER_STILL_LISTS` | The sample answered published. Restarts the count, not the origin |

### Reading the report

Each entry of `reads` has the same fields:

| Field | Counts |
|---|---|
| `baseline_absent` | Missing from the baseline read; 0 on every other read |
| `new_misses` | First misses of this read, without the backlog; 0 on the baseline read |
| `absent_backlog`, `absent_new` | Everything this read did not list, by population |
| `would_close_backlog`, `would_close_new` | Became due on this read, by population |
| `reappeared_backlog`, `reappeared_new` | Listed again on this read, by population |
| `sampled_backlog`, `sampled_new` | Selected on this read, by population |
| `combined_absent`, `combined_would_close`, `combined_reappeared` | **Combined totals.** Do not read them as new departures |
| `audit` | Per population: `eligible`, `selected`, `consulted`, `not_asked`, `published`, `gone`, `unknown`, `requests`, `errors` |
| `audit_runs` | How each run of the sample ended: `asked`, `requests`, `stopped` |

`new_misses_flap_rate` is `REAPPEARED` with one miss over `FIRST_MISS`. The backlog is
outside it.

### The audit sample

- Up to 50 postings per read: 25 from each population. What one population cannot
  use goes to the other, never past 50.
- Seeded by the feed version and the population, over the sorted eligible postings.
  The version is on the read each sample row points to.
- A posting is sampled once, ever.
- Selected, consulted and answered are three different things. `not_asked` is selected
  but not asked yet. `UNKNOWN` is never a confirmation, and it is asked at most 3 times.
- No answer closes anything.

**This sample is stratified, not proportional.** It says nothing about the mix of all
absences, so never add the two populations up to a rate. Plan §7 asks for 150 sampled
answers: read that per population. 150 answers that are mostly backlog do not validate
new misses.

## Controlled activation (after a separate approval and integration)

1. Check that no collector or pytest is running, then restart the app once with
   the new code. The server applies migration 0061 on opening: four empty tables.
   That opening first makes the automatic profile backup, about 8 minutes for the main
   profile. Opening the interface can also start the automatic update.
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
hold. That is the guard working, not a reason to clear anything by itself. See
"Clear the observations" below.

## Turning it off and going back

Use the first that does the job. **Removing tables is not a way to turn this off.**

### 1. Turn it off: the flags

Remove the three variables or set them to `off`, then restart. Nothing more is
written, reads go on as before, and the tables and their history stay. This is the
routine way, and it lasts.

### 2. Go back to an older version

Check out the older build. It works as it is: a ledger row beyond its files is
allowed, and it ignores the four tables. Tested with `ba53276` on migrated copies.
The tables and their history stay.

### 3. Clear the observations (only with the owner's explicit authorization)

`DELETE FROM` the four tables, children first, keeping the ledger row. **This ends the
observation.** The next read is a new baseline, so everything then missing becomes
backlog, including departures already watched as new misses, and the days observed
start again. The result needs a new evaluation against plan §7 and cannot be added to
the earlier one. Keep events and audit answers until the owner authorizes deleting
them. Export the report first.

### 4. Remove the tables (not routine, not on the real install without authorization)

The SQL below, on `data/shared/catalogue.db`, with the app closed. It destroys the same
history as level 3.

**It does not last on a build that carries the migration.** The next opening applies
0061 again and the tables return, empty. Only an older build keeps them away, so this
is for level 2 situations where the tables themselves are the problem.

```sql
BEGIN IMMEDIATE;
DROP TABLE workable_audit_sample;
DROP TABLE workable_shadow_event;
DROP TABLE workable_presence;
DROP TABLE workable_feed_read;
DELETE FROM schema_migration WHERE version = 61;
COMMIT;
```

Each profile keeps its own ledger row for 61, which is harmless: a profile holds none
of these tables. Since nothing else reads or writes them, no profile, score, queue or
posting depends on them.
