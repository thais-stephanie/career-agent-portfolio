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

Settings means Career Profile → Preferences unless stated otherwise.

| Answer | Label in the product | Asked today | Stored at | Read by | Category | Functional | Depends on | In onboarding | Why |
|---|---|---|---|---|---|---|---|---|---|
| Kinds of work | Kinds of work, one per line | Setup, Home | `lexicon` + `scoring.components.responsibilities.weights` | `score._weighted_component` | Search Fit | Yes | – | Yes | Main scoring input |
| Tools and skills | Tools or skills you use | Setup, Home | `lexicon` + `technologies.weights` | same | Search Fit | Yes | – | Yes, same card | Main scoring input |
| Career stage | Where are you in your career? | Setup, Home step 1 | `candidate_state.career_stage` | nothing except echoing the answer | Inert | No | – | **No (B)** | Copy claimed it changed explanations and filters; nothing reads it |
| Where you live | Where do you currently live? | Setup, Settings | `eligibility.candidate_country` | gates (residence as a place an employer can name), `plan_refresh` | Eligibility + retrieval | Yes | – | Yes | Decides the gate and which regional sources run |
| Can be hired where you live | Can companies hire you in {country}? | Setup, Settings | `eligibility.eligible_countries` | gates, `plan_refresh` | Eligibility + retrieval | Yes | where you live | Yes | Explicit yes or not sure; residence is never copied |
| Other hiring countries | Countries that can employ you directly | Setup, Settings | `eligibility.eligible_countries` | gates | Eligibility | Yes | – | Yes, same card | – |
| Hiring regions | Which regions can you work in? | Setup, Settings | `eligibility.eligible_scopes` | `gates._region_verdict` | Eligibility | Only for a region containing where you live | where you live | Yes, only regions that contain the home country, and only once it is known | A scope without a home country fails every on-site and hybrid posting |
| Ways of working you prefer | How do you prefer to work? | Settings only | `preferences.remote.accepted_work_models` | display only (before V2) | Inert → **Search Fit (A)** | Now yes | – | Yes | Wired: a posting whose stated work model you prefer earns the component maximum |
| Ways of working you would rather avoid | – (new) | – | `preferences.remote.avoided_work_models` | score | **Search Fit (A)** | Yes | – | Yes, same card | Soft: earns nothing on that component. Never eligibility |
| Ways of working never to show | – (new) | – | `preferences.remote.excluded_work_models` | score + Discover narrowing | **Search Fit + Narrowing (A)** | Yes | – | Yes, same card | Same safe home as excluded levels: hidden by default, revealable, tracked jobs exempt. Never eligibility |
| Only remote | Only show me fully remote roles | derived | `preferences.remote.require_remote` | nothing | Inert | No | ways of working | No | Kept, and derived on the server: true exactly when hybrid and on-site are both "never show". Copy no longer promises a filter it is not |
| Arrangements that work for you | Which working arrangements work for you? | Settings only | `preferences.contract.preferred` | `score._compensation_component` | Search Fit | Yes (±1 of 75) | – | Yes | Employee, contractor, employer of record: the three the reader recognises |
| Arrangements you would rather not | Any of those you would prefer less? | Settings only | `preferences.contract.unwanted` | score | Search Fit | **Could never fire** | arrangements | Yes, same card | Settings only offered values already preferred, and the scorer checks preferred first. The two lists are now disjoint, validated on the server |
| Levels you are looking for | Which levels make sense for you right now? | Settings only | `preferences.seniority.preferred` | display only | Inert | No | – | **No (B)** | The seniority points come from a fixed table; wiring a preference into it needs a scoring decision this phase does not take |
| Levels to keep off your list | Any levels you would rather not see? | Setup, Settings | `preferences.seniority.excluded` | Discover narrowing | Narrowing | Yes (Discover only) | – | Yes | – |
| Travel you accept | How much work travel would you be comfortable with? | Settings only | `preferences.travel.max_tolerated_pct` | display only | Inert | No | – | **No (B)** | The travel gate reads phrase blockers, never this number |
| Pay target | How much would you like to earn each month? | Setup, Settings | `preferences.compensation.target_monthly_amount` | score | Search Fit | Yes (3 of 75) | currency | Yes | – |
| Pay currency | In which currency? | Setup, Settings | `preferences.compensation.currency` | score (band choice) | Search Fit | Yes | – | Yes, same card | A salary in another currency counts as unknown; no rates are written from the web |
| CV / career data | Read your CV | Evidence, Home | intake / CV review tables | Career Evidence, Tailor gating | Display / evidence | Yes | – | Yes, optional | Read and staged, never confirmed by being read |
| Search phrases (desired/negative/excluded) | Settings → Search phrases | Settings | `lexicon`, `soft_penalties`, `eligibility.blockers` | score, gate | Search Fit / Eligibility | Yes | existing signals | No | Edits existing signals only; onboarding writes the first ones |
| Prefer / avoid / never show keywords | Settings → Search settings | Settings | browser only | Discover ordering and filtering | Display ordering | Yes, Discover only | – | No | A per-browser view preference, not part of the search |
| Source schedule | Settings → Sources | Settings | `candidate_state` | retrieval | Retrieval | Yes | – | No | Source management is a later phase |

## Decisions

- **Wired (A):** preferred, avoided and never-shown ways of working. The
  "rather not" contract list is made reachable by keeping it disjoint from the
  preferred list.
- **Left out of onboarding, documented here as debt (B):** career stage,
  preferred seniority levels, travel tolerance, "only remote". They remain
  editable where they were, with copy that no longer promises an effect.
- **Removed (C):** nothing.

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
  changed in Settings → Search phrases.
