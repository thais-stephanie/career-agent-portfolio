# Cold-start validation protocol (prepared, not run)

The goal is to know how long the app takes to become usable on the first
opening after a natural restart, without confusing it with a warm opening.
This protocol was prepared on 2026-11-18. It was not run, because the computer
had not restarted. Every figure measured so far is warm: the operating system
held part of the files in its cache.

## 1. What makes an opening cold

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
- sources 0.2 to 0.7 GB.

A cold opening fetches the distinct pages among those from the disk.

## 2. Preconditions

- The computer restarted naturally (no forced restart, no cache flushing).
- After the restart, nothing has opened the app or read its databases: no
  shortcut, no CLI, no test suite, no backup, no indexer. Do not warm anything up.
- Other projects may run, but write down which ones are running.
- The owner is present: this is an operational opening of the real install.

## 3. Two runs, one per restart

A cold opening can only be measured once per restart. Choose one per restart.

**Run 1, the handlers alone (no server, no browser, read only).** From the
session scratch folder, as the first thing after the restart:

```powershell
bash runload.sh cold1.jsonl 1
```

This is `realload.py`, order A (today's order), with its read-only guards.
The harness (`realload.py`, `runload.sh`, `machine.ps1`, `state3.py`,
`hashlist.txt`) and its synthetic warm-up corpus live in the measuring
session's temporary scratch folder. A restart does not empty it; Storage Sense
or a manual cleanup of `%TEMP%` would. Its guards refuse a warm-up corpus under
the install, so it cannot be kept in `out/`. If it is lost, rebuild it from
the Step 2 and Step 4 descriptions before the restart, not after: rebuilding
it after would read the databases warm. It
runs one round of four openings: the first one is cold and the next three are
progressively warmer. Record the first one only as cold.

**Run 2, the whole page (a later restart).** The server is started without
opening the UI:

```powershell
.venv\Scripts\python.exe scripts\launch.py --no-open --port 8765
```

Headless Chrome is driven through `tests/browser/chrome.py`, with a CDP
`Fetch` rule answering `POST /api/auto-update/run` with
`{"started": false, "reason": "DISABLED"}`. The rule is first proven against a
recording probe server. If more than 6 hours have passed since the last
update, opening the UI starts a real collection, so the rule must hold before
the first navigation. Open `/` once, wait for the page to settle, then close
cleanly with `POST /api/app/quit`.

## 4. What to record

Per run, from the opening:

| Indicator | Run 1 (handlers) | Run 2 (page) |
|---|---|---|
| interactive shell | firstrun end | `DOMContentLoaded` + first paint of the shell (`data-view`) |
| dashboard | Home end | `data-view="dashboard"` |
| source indicators | sources end | rail status filled |
| outdated alert | outdated end | the task row's count drawn |
| all of Home's requests | page end | last of the five requests ends (resource timing) |
| server's first query | first statement time (trace) | server log |
| CPU, RAM, I/O | thread CPU, bytes read, peak memory per handler | process `ReadTransferCount`, working set, free RAM sampled every 2 s |

Write down the free RAM, the CPU load of other processes and the time since
the restart. The sampler uses InvariantCulture: a pt-BR PowerShell writes decimal commas.

## 5. Integrity, before and after

The same checks as every real measurement:

- the SHA-256 of the 24 database and WAL files;
- `state3.py` (schema, ledger, counters, rowids);
- WAL sizes, with SHM mtimes recorded apart;
- the CSV hash;
- no process left behind, port 8765 free after `POST /api/app/quit`.

In run 2 the server is the product, and it may write its own state on
opening (logs, keys it records when the app starts). List what it wrote, and
do not count it as a harness write.

## 6. Reading the result

The target is a dashboard under 10 s cold:

- If run 1 shows Home under 10 s but run 2 does not, the gap is the server
  start, the imports or the browser.
- If both are over 10 s, the next lever is the distinct pages Home reads cold.
  The Step 3 measurements show how much a covering index or a larger page
  cache cuts them.

Never report a warm second opening as a cold one.
