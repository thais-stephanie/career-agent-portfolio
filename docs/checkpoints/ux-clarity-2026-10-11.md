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
- [ ] 5. Optional update on open, bounded, off by default
- [ ] 6. Score explanation
- [ ] 7. Providers and privacy panel
- [ ] 8. Resume Tailor in the sidebar
- [ ] 9. Settings, People on this computer
- [ ] 10. Text wrapping across the app
- [ ] 11. Backups and disk use
