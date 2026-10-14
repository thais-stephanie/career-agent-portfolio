# Is the ad still online?

`career_agent/availability.py` asks a posting's own page, or its job board's
official posting API, whether the advert is still up. It is independent of the
match score and of the posting's age, and it never deletes anything.

## The four answers, and the fifth

| State | When | Effect |
|---|---|---|
| Online (`AVAILABLE`) | The page answered and still shows this posting's title, or the board's API (Greenhouse, Lever) still lists its id | None. It is what the page said then, not a promise |
| Removed (`REMOVED`) | HTTP 404 or 410, or the source's own unmistakable words on a page that answers 200 ("this job is no longer available", "vaga encerrada") | Hidden from the default search, with a switch to include it; never read again by a deep reading; kept in saved jobs, applications and resumes; its score unchanged |
| Could not be confirmed (`INCONCLUSIVE`) | 403, 429, 5xx, a timeout, a failed connection, a bot check, a redirect to a careers home, a 200 that does not show the posting | None. Stays visible |
| LinkedIn (`EXCLUDED`) | A LinkedIn address, or a redirect that leads to one | Never asked; never called removed |
| Not checked | No row | None |

Each copy of a posting is checked and filed on its own: one removed copy does
not close the others.

## When it runs

Only when the person presses "Check if ads are still online" in Find jobs.
Never when the app opens, and never on the whole catalogue: one run looks at
most at 200 postings, the saved and tracked ones first, then the main list,
best match first. A run is refused while a collection or a recalculation runs,
and stops between requests if one starts; what was asked is kept. Two runs never
run at once.

## How long an answer stands

| Answer | Asked again after |
|---|---|
| Online | 3 days |
| Removed | 14 days (an advert can come back) |
| Could not be confirmed | 1 day |
| LinkedIn | 30 days (still not asked) |

## Limits and safety

* At most 4 requests at a time, one per domain, 2 seconds apart; a 429 or 503
  with `Retry-After` stops asking that domain for the rest of the run.
* 15 seconds per request, 5 redirects, 400 KB read per page, 10 minutes a run.
* Only `http` and `https` to public addresses: loopback, private, link-local
  (cloud metadata), reserved and multicast addresses are refused before the
  first request and after every redirect. No cookies, no credentials, no
  script is run. The address is resolved again when connecting, so a DNS
  answer that changes in between is not caught; see the note in the module.
* Results live in `job_availability` in the shared catalogue (migration 0055),
  one row per checked posting, replaced by the next check.

Tests use simulated HTTP and DNS only (`tests/unit/test_availability.py`,
`tests/integration/test_availability_api.py`).
