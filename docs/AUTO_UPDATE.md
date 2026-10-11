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

## What it never does

- No AI call, no Deep reading, no recalculation of the whole list, nothing about
  you is sent. Search words only reach sites that are asked for them, as in a
  manual search.
- It does not run when the app is closed. There is no Windows service.
- It does not update sites that cannot honour a budget yet: Workable's
  whole-catalogue download and Gupy are updated only by hand. Sites that must be
  run by their owner, and sites whose terms forbid it, are never asked.

## Seeing it, stopping it, and what arrives

Progress shows in the bar at the top ("Updating your job sites in the
background") with Stop, and navigation is never blocked. When it ends, a notice
says how many new jobs are available and offers "Update the list". The list,
your filters and the posting you are reading are not replaced until you press it.

A limited update can leave sites pending. It is not a promise of full coverage:
use the update buttons for a complete one.

## Where the code is

`web/auto_update.py` (routes `GET/PATCH /api/auto-update`, `POST /api/auto-update/run`),
`pipeline/maintenance.py` (the engine), `net/fetcher.py` (request cap),
`static/js/autoupdate.js` and `autoupdate-notice.js`.
