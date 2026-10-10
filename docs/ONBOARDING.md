# Onboarding: what each answer does

The guided setup asks only questions whose answers change something the
product does. This file records the audit that decided which questions those
are, what each answer changes, and the preferences that are stored but not yet
read by anything (inert debt).

Categories:

- **Search Fit**: points in the deterministic score (`match/score.py`)
- **Eligibility**: the geography gate (`match/gates.py`)
- **Narrowing**: hidden from Discover by default, with one control to show again
- **Retrieval**: which sources are collected (`sources/scheduling.py`)
- **Display**: shown back, changes nothing else
- **Inert**: stored and never read

## Field matrix

Settings means Career Profile to Preferences unless stated otherwise.

| Answer | Label in the product | Asked today | Stored at | Read by | Category | Functional | Depends on | In onboarding | Why |
|---|---|---|---|---|---|---|---|---|---|
| Kinds of work | Kinds of work, one per line | Setup, Home | `lexicon` + `scoring.components.responsibilities.weights` | `score._weighted_component` | Search Fit | Yes | none | Yes | Main scoring input |
| Tools and skills | Tools or skills you use | Setup, Home | `lexicon` + `technologies.weights` | same | Search Fit | Yes | none | Yes, same card | Main scoring input |
| Career stage | Where are you in your career? | Setup, Home step 1 | `candidate_state.career_stage` | nothing except echoing the answer | Inert | No | none | **No (B)** | Copy claimed it changed explanations and filters; nothing reads it |
| Where you live | Where do you live? | Setup, Settings | `eligibility.candidate_country` | gates (residence and attendance requirements) | Eligibility | Yes | none | Yes | Never a work authorization and never narrows the search (ADR-0034) |
| Where you can be employed | In which countries do you know you can be employed? | Setup (card "hire"), Settings | `eligibility.authorized_countries` | `match.authorization`; with residence, `gates._hireable_countries` | Eligibility | Yes | none | Yes | Only what she knows; "I don't know" is an answer. Where she lives AND is authorized counts as a place an employer can hire her; nothing is read from residence, nationality or a market alone (ADR-0034) |
| Retired: countries that can employ you directly | (no longer asked) | Settings, shown back only when answered | `eligibility.eligible_countries` | gates (admits only) | Eligibility | Yes, as given | none | No | Kept as given, never copied into `authorized_countries`, removable by her (ADR-0034) |
| Not authorized to work | Countries where you know you are not currently authorized to work | Settings, folded | `eligibility.not_authorized_countries` | `match.authorization` | Eligibility | Yes | none | No | An explicit no; answers only a requirement the posting states in so many words, never the company's country or a remote role's location; left out means not said |
| Visa sponsorship | Countries where you would need visa sponsorship to work on site | Settings, folded | `eligibility.sponsorship_needed` | `match.authorization` | Eligibility | Yes | none | No | Refuses only an on-site or hybrid job there that says it does not sponsor; never answered from residence or nationality |
| Markets to search first | Which markets do you want to prioritize? | Setup, Settings | `regions.target` | `discovery.scopes`, `sources.scheduling` | Discovery order | Yes | none (search everywhere) | Yes | Orders sources and targeted searches; never excludes a posting, pauses a source or stands for an authorization; asks for no recalculation (ADR-0032) |
| Contractor at home | Can you provide services as a contractor to a client in the country where you live? | Settings, folded | `eligibility.contractor` | `match.authorization` | Eligibility | Yes | none | No | Separate from employment and visas; not answered is never "no" |
| Contractor for a client abroad | Can you provide services to a client in another country? | Settings, folded | `eligibility.contractor_abroad` | `match.authorization` | Eligibility | Yes | none | No | Answered on its own: a no at home is not a no abroad |
| Hiring regions | Regions named in job ads that include you | Settings, folded | `eligibility.eligible_scopes` | `gates._region_verdict` | Eligibility | Only for a region containing where you live | where you live | No (2026-10-09) | Optional: a region admits only through her residence |
| Ways of working you prefer | How do you prefer to work? | Settings only | `preferences.remote.accepted_work_models` | display only (before V2) | Inert before V2, now **Search Fit (A)** | Now yes | none | Yes | Wired: a posting whose stated work model you prefer earns the component maximum |
| Ways of working you would rather avoid | (new) | none | `preferences.remote.avoided_work_models` | score | **Search Fit (A)** | Yes | none | Yes, same card | Soft: earns nothing on that component. Never eligibility |
| Ways of working never to show | (new) | none | `preferences.remote.excluded_work_models` | score + Discover narrowing | **Search Fit + Narrowing (A)** | Yes | none | Yes, same card | Same safe home as excluded levels: hidden by default, revealable, tracked jobs exempt. Never eligibility |
| Only remote | Only show me fully remote roles | derived | `preferences.remote.require_remote` | nothing | Inert | No | ways of working | No | Kept, and derived on the server: true exactly when hybrid and on-site are both "never show". Copy no longer promises a filter it is not |
| Arrangements that work for you | Which working arrangements work for you? | Settings only | `preferences.contract.preferred` | `score._compensation_component` | Search Fit | Yes (±1 of 75) | none | Yes | Employee, contractor, employer of record: the three the reader recognises |
| Arrangements you would rather not | Any of those you would prefer less? | Settings only | `preferences.contract.unwanted` | score | Search Fit | **Could never fire** | arrangements | Yes, same card | Settings only offered values already preferred, and the scorer checks preferred first. The two lists are now disjoint, validated on the server |
| Levels you are looking for | Which levels make sense for you right now? | Settings only | `preferences.seniority.preferred` | display only | Inert | No | none | **No (B)** | The seniority points come from a fixed table; wiring a preference into it needs a scoring decision this phase does not take |
| Levels to keep off your list | Any levels you would rather not see? | Setup, Settings | `preferences.seniority.excluded` | Discover narrowing | Narrowing | Yes (Discover only) | none | Yes | none |
| Travel you accept | How much work travel would you be comfortable with? | Settings only | `preferences.travel.max_tolerated_pct` | display only | Inert | No | none | **No (B)** | The travel gate reads phrase blockers, never this number |
| Pay target | How much would you like to earn each month? | Setup, Settings | `preferences.compensation.target_monthly_amount` | score | Search Fit | Yes (3 of 75) | currency | Yes | none |
| Pay currency | In which currency? | Setup, Settings | `preferences.compensation.currency` | score (band choice) | Search Fit | Yes | none | Yes, same card | A salary in another currency counts as unknown; no rates are written from the web |
| How jobs are matched | How should jobs be matched to you? | Setup (card "matching", before the first search), Settings | `semantic.local.yaml` `mode` | `semantic.settings`; scores read AI findings only for postings an explicit run evaluated | Matching | Yes | none | Yes, optional | Keywords only is the default and what Skip keeps; an AI provider needs her explicit choice AND a confirmation of what it receives and may cost. Choosing evaluates nothing: the list says which basis its scores have (keywords only, or plus an AI evaluation for N jobs) |
| CV / career data | Read your CV | Evidence, Home | intake / CV review tables | Career Evidence, Tailor gating | Display / evidence | Yes | none | Yes, optional | Read and staged, never confirmed by being read |
| Search phrases (desired/negative/excluded) | Settings to Search phrases | Settings | `lexicon`, `soft_penalties`, `eligibility.blockers` | score, gate | Search Fit / Eligibility | Yes | existing signals | No | Edits existing signals only; onboarding writes the first ones |
| Prefer / avoid / never show keywords | Settings to Search settings | Settings | browser only | Discover ordering and filtering | Display ordering | Yes, Discover only | none | No | A per-browser view preference, not part of the search |
| Source schedule | Settings to Sources | Settings | `candidate_state` | retrieval | Retrieval | Yes | none | No | Source management is a later phase |

## Decisions

- **Wired (A):** preferred, avoided and never-shown ways of working. The
  "rather not" contract list is made reachable by keeping it disjoint from the
  preferred list.
- **Left out of onboarding, documented here as debt (B):** career stage,
  preferred seniority levels, travel tolerance, "only remote". They remain
  editable where they were, with copy that no longer promises an effect.
- **Removed (C):** nothing.

## Right-to-work semantics

`match.authorization` reads four requirements a posting can state: authorization
to work in a country, a citizenship, no visa sponsorship, and an engagement as
an independent contractor (in so many words, or in the posting's own
contract-type field; a benefit or a likely regime is not read). Each is answered
by what the candidate DECLARED:

| The posting requires | Compatible | Incompatible | Pending |
|---|---|---|---|
| Authorization to work in X | You declared you are authorized in X | You declared you are not | Neither was said, or no country is named |
| Citizenship of X | never: an authorization is not a citizenship | You declared you are not authorized in X | Otherwise |
| No visa sponsorship in X | You declared you are authorized in X | You need sponsorship in X | Neither was said, or no country is named |
| A contractor engagement | You said yes at home AND for clients abroad | You said no at home AND for clients abroad | Any other answer: where the contracting party is decides, and no reader identifies it (a posting's location is where the work is, never where the client is) |

`eligible_countries` (the retired "Countries that can employ you directly")
answers none of these: it only ever admits, in the geography gate, and is no
longer asked (ADR-0034). Discovery follows the markets you chose (ADR-0032).
A job that states a requirement nobody answered offers "Answer this in your
profile", which opens the question it needs.

Any incompatible answer fails the work-authorization gate; otherwise any
pending one leaves it UNRESOLVED, quoted, and the eligibility status cannot be
VERIFIED_ELIGIBLE while it waits; otherwise it passes. Where you live answers
none of these. A blocker written in the search file still decides first, so an
existing search behaves as before.

**"I don't know" is an answer.** Each of the three questions (where you may be
employed, contractor work at home, contractor work abroad) offers it on the
profile. It is stored per profile in `candidate_state` (`eligibility.unsure`),
never in the search file, so it changes no score and asks for no
recalculation: a posting that asks the question stays pending, and Home stops
listing the question. Home's "Small things to do" lists only questions with
neither an answer nor an "I don't know", and its row opens the profile at the
first of them.

## Home: small things to do

Every row is an action that exists now, per profile, and the whole row is the
control: a title, one short line, a chevron. "You're all set" appears only when
there is none. A row leaves the list only when the next read of Home no longer
has it (confirmed done, never on a click), and the footer then says so for a
few seconds. A row that appears says nothing, a row whose status is not known
yet is neither there nor gone, and a read answered for another profile closes
nothing.

| Row | Shown when | Action |
|---|---|---|
| Update your match scores (amber line: N jobs are showing old scores) | the targeted rescore's own plan has N > 0 postings (stale or never scored), or a run is going | the same Recalculate flow as Jobs (`js/recalc.js`); the line shows "Updating X of Y" over a thin bar, a run already going is followed, a second start is ignored (the server answers 409), the other rows stay usable |
| Where can you work? (N quick questions) | a question above has no answer and no "I don't know" | opens the profile at that question |
| Fix N job sites | the sources' own status (`/api/sources`, `needs_attention`) says N > 0, for the profile Home was read for (both answers carry `profile`) | opens Settings at Job sites. The status arrives after Home, and Settings & Sources reads it again after each confirmed action there (timing, experimental on/off, refresh now); each time the card alone is redrawn from the Home read already held (no second `/api/home`, no polling added), and a run the recalculation row follows keeps its progress. "Job sites working again" only when the sources that needed attention are COMPLETE, PARTIAL or DUE: a paused, blocked or removed source leaves the row silently, and a running one is waited for |
| Profile gaps | as before | as before |

Preferred markets are optional and never a row: empty means search everywhere.
While N > 0, "Best matches right now" says its order may change.

## Work-model semantics

A posting's work model is what the board said (`engine._work_model`): the
structured field first, then the board's own location words, otherwise not
stated.

Each of Remote, Hybrid and On-site is in at most one of three lists.

| Your answer | Search Fit ("Way of working", max 4) | Discover |
|---|---|---|
| Prefer | 4 | shown |
| Fine (no answer) | 2 | shown |
| Rather avoid | 0 | shown |
| Never show me | 0 | hidden by default; "Include ways of working you said never to show" reveals them; tracked jobs stay visible |
| posting did not say | 2 | shown |

The component is scored only when at least one way of working has an answer,
so a search that never answered keeps exactly the score it had.

None of this is eligibility. Remote never means worldwide: that is decided by
the geography gate from what the employer wrote. Avoiding or never showing
on-site work never makes a posting ineligible.

## Employment-arrangement semantics

The product recognises three arrangements from the board's field or the
posting's own words (`match/employment.py`): employee, contractor (including
Brazilian PJ and B2B), and employer of record.

| Your answer | Contract points (within "Compensation and contract") |
|---|---|
| Works for me | 2 |
| No answer, or the posting did not say | 1 |
| Rather not | 0 |

Internship, apprenticeship and temporary work are recognised in postings but
have no preference field, so they are not asked about.

## Remaining debt

- Career stage, preferred levels and travel are stored but not read.
- Excluded levels and never-shown ways of working narrow Discover, not Home or
  the daily digest.
- The pay target does not convert currencies from the web; a salary in another
  currency counts as unknown.
- Kinds of work cannot be rewritten inside the setup once saved; they are
  changed in Settings to Search phrases.
