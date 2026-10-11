# UX clarity pass (2026-10-11), branch claude/ux-clarity, base main 334aeb5

Scope checklist. `[x]` = implemented and tested, `[ ]` = pending.
Not merged, not pushed. Synthetic data only.

- [x] 3. Job detail opens with an old score (root cause: `get_job` asked for the
      CURRENT config version only and answered 404 where the list served the
      previous revision). Detail, preparation and application now open with the
      newest stored score labelled stale, or with no score at all.
- [x] 1. Onboarding for every new profile (finishing or postponing the setup wrote one browser flag for everybody; storage about a person is now scoped by profile id; welcome header; honest privacy and unknown-vs-unmet copy)
- [x] 2. Orientation after editing the profile (server `effects` from the digests the stale checks already use: needs_rescore, offer_discovery, deep_stale; one notice, one action, nothing started)
- [x] 4. Job site alerts and per-source actions (one truth for Home, sidebar and Settings: shared postings count as run; a failure after storing postings is partial for everyone; plain-language card per site with its one action and collapsed technical details; "Workable" not "Jobs by Workable XML")
- [x] 5. Optional update on open (off by default; maintenance engine reused; 10 min, 60 sites, 1000 requests, 6 h gap; scoring capped at 5000 postings; docs/AUTO_UPDATE.md)
- [x] 6. Score explanation ("How this score works": closed by default, seven short rows at reading width, accurate about what takes part; gone from an empty applications board together with the recalculation notice)
- [x] 7. Providers and privacy (switch + provider chips with the effective state said; Deep reading shown apart with its provider, model, connection and consent; the deep provider is never derived from the Smart matching choice; honest "what leaves" text)
- [x] 8. Resume Tailor in the sidebar (page + job picker + make-Master-and-return bar; drawer wording; history recorded in docs/RESUME_WORKSPACE.md)
- [x] 9. Settings, People on this computer (every profile listed, current marked, rename in place, switch, delete only when applicable; text at reading width)
- [ ] 10. Text wrapping across the app
- [x] 11. Backups and disk use (app makes no database copy on edits or openings; one verified automatic backup before an upgrade, newest 3 per profile, 2 GiB cap, listed in Settings; docs/BACKUPS.md; inventory delivered separately)
