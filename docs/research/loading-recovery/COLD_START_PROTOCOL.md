# Cold-start validation protocol (prepared, not run)

The goal is to know how long the app takes to become usable on the first
opening after a natural restart, without confusing it with a warm opening.
Prepared on 2026-11-18 and updated the same day after Step 3 was published.
It has not been run, because the computer had not restarted. Every figure
measured so far is warm (the operating system held part of the files in its
cache), or "out of the cache" for one copied file (Step 3, section 13), which
is not a cold boot.

## 1. What is in place

| Step | State | What it changed on the opening |
|---|---|---|
| 1c Home | published (`de8e69a`) | Home derives deep levels for its own population only |
| 2 sources | published (`9636ecf`) | one pass over `job` |
| 3 outdated | published (`8ec7263`), **not installed** | covering receipt indexes. Needs migrations 0062/0063, see `STEP3_OPERATIONAL_UPGRADE.md` |
| 4 load order | measured, no change | |
| 7 Resume Tailor | branch `claude/resume-tailor-perf-stage7`, not published | its two lists share one derivation of the deep levels |

The live checkout runs neither Step 3 nor Stage 7 until the owner installs
them. Write down which commit is installed and the schema of each database
before any run.

## 2. Five times, never mixed

1. **Migration time.** Only the first opening after an upgrade that brings
   migrations. For 0062/0063 it is about 3.4 s on this install (measured on a
   copy). It is measured on its own, by the procedure's
   `career-agent migrate --db ...` step, **before** the first start. The first
   start then has nothing to migrate.
2. **Process start.** From the shortcut or `launch.py` until the server
   answers its first request: imports, opening the databases, the
   catalogue attach.
3. **Home.** Shell, dashboard, sources, outdated, and the end of Home's
   requests (section 5).
4. **Resume Tailor.** Opening the Tailor: Start usable, the saved list, the
   best list, both ready.
5. **Cold file cache.** Times 2 to 4 measured on the first opening after a
   natural restart, against the same times on a warm second opening.

Never report the first opening after an upgrade (which carries 1) as a normal
first opening after a restart.

## 3. What makes an opening cold

SQLite opens the files and reads pages on demand. Its own page cache (2 MB per
connection by default) lives only as long as a request's connection. What
survives between requests and processes is the operating system's file cache,
which a restart empties. Home's memo, the outdated memo and the deep-level maps
live in the server process, and a new process starts without them.

The files are large: catalogue 9.9 GB, profile 5.9 GB. A warm first opening
already reads hundreds of MB through the OS:

- Home 0.6 to 1.1 GB;
- health 0.4 to 0.8 GB;
- outdated 0.3 to 1.2 GB;
- sources 0.2 to 0.7 GB;
- each Tailor list 0.1 to 1.0 GB.

A cold opening fetches the distinct pages among those from the disk.

## 4. Preconditions

- The computer restarted naturally (no forced restart, no cache flushing).
- After the restart, nothing has opened the app or read its databases: no
  shortcut, no CLI, no test suite, no backup, no indexer. Do not warm anything up.
- Other projects may run, but write down which ones are running.
- The owner is present: this is an operational opening of the real install.
- The code and the schema agree. The published code from `8ec7263` on needs
  schema 63. On schema 61 measure with `9636ecf`, or migrate first, as a
  separate step, by the procedure.

## 5. Runs, one per restart

A cold opening can only be measured once per restart. Choose one run per restart.

**Run 1, Home's handlers alone (no server, no browser, read only).** From
the scratch folder that holds the harness, as the first thing after the
restart:

```powershell
bash runload.sh cold1.jsonl 1
```

This is `realload.py`, order A (today's order), with its read-only guards. It
runs one round of four openings: the first one is cold and the next three are
progressively warmer. Record the first one only as cold.

**Run 2, the Tailor's lists alone (read only).** Again the first thing after a
restart:

```powershell
bash runtailor.sh coldtailor.jsonl 1 A
```

This is `realtailor.py`, both modes, the first list in a new process. Use
`A` (`9636ecf`) on schema 61. Use `B` only on a tree whose code matches the
installed schema.

**Run 3, the whole page (a later restart).** The server is started without
opening the UI:

```powershell
.venv\Scripts\python.exe scripts\launch.py --no-open --port 8765
```

Headless Chrome is driven through `tests/browser/chrome.py`, with a CDP
`Fetch` rule answering `POST /api/auto-update/run` with
`{"started": false, "reason": "DISABLED"}`. The rule is first proven against a
recording probe server. If more than 6 hours have passed since the last
update, opening the UI starts a real collection, so the rule must hold before
the first navigation. Without a proven interception, do not run this.

Open `/`, wait for the page to settle, open the Tailor, wait for both lists,
then close cleanly with `POST /api/app/quit`.

## 6. What to record

Per run, from the opening:

| Indicator | Runs 1 and 2 (handlers) | Run 3 (page) |
|---|---|---|
| process start | (not in the run) | first answer from the server |
| interactive shell | firstrun end | `DOMContentLoaded` + first paint of the shell (`data-view`) |
| dashboard | Home end | `data-view="dashboard"` |
| source indicators | sources end | rail status filled |
| outdated alert | outdated end | the task row's count drawn |
| all of Home's requests | page end | last of the five requests ends (resource timing) |
| Tailor lists | saved end, best end, both | each list part drawn (`data-part`) |
| server's first query | first statement time (trace) | server log |
| CPU, RAM, I/O | thread CPU, bytes read, peak memory per handler | process `ReadTransferCount`, working set, free RAM sampled every 2 s |

Write down the free RAM, the CPU load of other processes and the time since
the restart. The sampler uses InvariantCulture: a pt-BR PowerShell writes
decimal commas.

## 7. Integrity, before and after

The same checks as every real measurement:

- the SHA-256 of the 24 database and WAL files;
- `state3.py` (schema, ledger, counters, rowids);
- WAL sizes, with SHM mtimes recorded apart;
- the CSV hash;
- no process left behind, port 8765 free after `POST /api/app/quit`.

In run 3 the server is the product, and it may write its own state on
opening (logs, keys it records when the app starts). List what it wrote, and
do not count it as a harness write.

## 8. Keeping the harness

The harness is:

- `realload.py`, `realtailor.py`, `realout.py` and their `make_*.py` generators;
- `runload.sh`, `runtailor.sh`, `machine.ps1`, `state3.py`, `hashlist.txt`;
- the synthetic warm-up corpora `s3/base` (schema 61) and `s3/base_m` (63);
- the code trees `s3/src_p` (`9636ecf`) and `s3/src_b`.

It lives in the measuring session's temporary scratch folder. A restart does
not empty it; Storage Sense or a manual cleanup of `%TEMP%` would.

Its guards refuse a warm-up corpus or a scratch root under the install, so it
cannot be kept in `out/`. Committing it to the repository is not proposed: it
carries absolute paths of this machine, and it reads the live install by
design.

The safe way to keep it, if the owner wants it to outlive the session: copy
the scripts and the two synthetic corpora (about 175 MB, invented data, no
real data) to a folder of its own outside the install, for example a
`career-agent-perf-harness` folder beside the archive. Then point
`SCRATCHPAD` in the generators at it. Do it **before** a restart: rebuilding
it after one would read the databases warm.

## 9. Reading the result

The target is a dashboard under 10 s cold:

- If run 1 shows Home under 10 s but run 3 does not, the gap is the process
  start, the imports or the browser.
- If both are over 10 s, the next lever is the distinct pages Home reads cold
  (Step 3 shows what a covering index or a larger page cache cuts).
- For the Tailor, a cold first list is bound by deriving the deep levels
  (about 4.4 to 7.4 s warm on the real profile). Stage 7 removes only the
  duplicate derivation.

Never report a warm second opening as a cold one.
