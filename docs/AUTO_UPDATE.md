# Update jobs when the app opens (optional)

A per-profile choice, **off until you turn it on**, in the setup's last card or
in Settings, Job sites ("Keep my jobs up to date"). It is stored in the profile
(`candidate_state`, key `auto_update.on_open`), never in the search settings, so
choosing it does not mark any score out of date.

## What happens

When the app opens and the choice is on, the interface and the jobs already held
are served at once. The page then asks the server once; the server decides.
It starts a background run only if all of these hold:

- the choice is on and this is not the demo;
- the last run started by an opening began more than 6 hours ago (this is how
  reopening the app, a second tab or a second window never starts a second run);
- no collection, recalculation, AI run or maintenance run is going on;
- at least one site is overdue.

Overdue means: its last successful update is older than 3 days, it is not in the
cool-down after a failed attempt (1 hour) or a refusal, the site's terms allow it,
and this profile has not paused it. The sites that waited longest go first, with
the rotating first slot of the maintenance engine so cheap, heavy and unmeasured
sites do not starve each other (ADR-0031).

## The budget, and why these numbers

| Limit | Value | Why |
|---|---|---|
| Time | 10 minutes | Dozens of employer boards at the polite one-request-a-second pace; invisible next to a working session. A first full collection takes about 90 minutes and is never done on opening. |
| Sites | 60 | A ceiling when many small boards are overdue. |
| Requests | 1000 | A second ceiling, whatever the clock says (`HttpFetcher.request_cap`). |
| Between runs | 6 hours | Reopening does not ask the same sites again. |
| Scoring after | 5000 postings | Postings that arrived or changed are scored so they can be shown. If more are waiting (usually because preferences changed and the whole list needs recalculating), nothing is scored automatically and you are told. |

A site cut off by a limit keeps its place at the front of the queue, so nothing is
postponed for ever by always being behind the same neighbours.

## Workable

Workable is included. Its public feed is **one file for the whole catalogue**, so it is
not one site among sixty and does not fit the ten-minute quick run. It is read as a step
of its own after the quick run, with its own allowance of **15 minutes**
(`WORKABLE_BUDGET_SECONDS`), and only when it is overdue and this profile has not paused it.

What was measured on 2026-10-11 (one `GET` aborted after the headers, and `If-Modified-Since`
probes; the file itself was not downloaded):

| Question | Answer |
|---|---|
| Size | 843,345,565 bytes (about 804 MiB) |
| `Last-Modified` | yes, regenerated hourly |
| `ETag` | **no** |
| `Accept-Ranges` / `Range` | **no**: a `Range` request is answered 200 with the whole file |
| `If-Modified-Since` equal to `Last-Modified` | **304**, no body |
| `HEAD` | useless: 200 with an empty body, no validators |

So the update never assumes more than that:

1. **One read for everyone.** An operating-system lock beside the shared catalogue
   (`data/shared/workable/feed.lock`) lets one profile, tab or window read at a time; the
   others find it running and wait for the next opening. The catalogue also records when
   the feed was last confirmed: a second profile that is overdue shortly after the first
   records a successful check without downloading anything.
2. **Asked first.** The request carries the `Last-Modified` of the last feed that was read
   to its end. A 304 ends the update (nothing to write, nothing to score) and counts as a
   successful check.
3. **Read progressively.** The download is streamed to disk and checked chunk by chunk, so
   Stop or the time limit end it within a fraction of a second; a body shorter than
   `Content-Length` is a failure and the partial file is removed. The XML is then parsed
   record by record into an on-disk index (never the whole document in memory).
4. **Only what is new or different is written.** A posting identical to the one held
   (description hash, title, link, place, department, date) is left alone: nothing is
   written and nothing is queued for a new score. New and changed postings are saved with
   their application link unchanged and are scored locally by the ordinary targeted pass.
5. **A read the limit cuts short is a pending update.** The place is kept (`feed.xml` or
   `stage.sqlite` and a cursor in `state.json`, beside the catalogue). It is reported as
   "read in part, the next update continues", it is **not** recorded as a successful
   update, and the next run continues from the cursor **without downloading again**.
   Nothing is ever closed because the feed no longer lists it, so a partial read can never
   close postings; closing would need a complete, validated read and is not done at all.
6. **No room, no start.** It does not begin without about 3 GiB free (the download and its
   index coexist). A leftover read older than 7 days is discarded.

Limits that remain: the download cannot resume in the middle (the server ignores `Range`),
so on a connection slower than about 1 MB/s a 15-minute slice cannot finish the transfer
and the update reports it did not finish; the manual "Update" on Workable's card has no
clock of its own (only Stop). The first ever read of a large catalogue takes several
slices; every later one is a 304 or a small difference.

## What it never does

- No AI call, no Deep reading, no recalculation of the whole list, nothing about
  you is sent. Search words only reach sites that are asked for them, as in a
  manual search.
- It does not run when the app is closed. There is no Windows service.
- It does not update Gupy (its feed host answers 404, so there is nothing to read; see
  `docs/product/source-matrix.md`), sites that must be run by their owner, or sites whose
  terms forbid it. Those are never asked.

## Seeing it, stopping it, and what arrives

Progress shows in the bar at the top ("Updating your job sites in the
background") with Stop, and navigation is never blocked. When it ends, a notice
says how many new jobs are available and offers "Update the list". The list,
your filters and the posting you are reading are not replaced until you press it.

A limited update can leave sites pending. It is not a promise of full coverage:
use the update buttons for a complete one.

## Where the code is

`web/auto_update.py` (routes `GET/PATCH /api/auto-update`, `POST /api/auto-update/run`),
`pipeline/maintenance.py` (the engine), `pipeline/workable_update.py` and
`providers/workable_feed.py` (Workable's shared, resumable read), `net/fetcher.py` (request cap),
`static/js/autoupdate.js` and `autoupdate-notice.js`.
