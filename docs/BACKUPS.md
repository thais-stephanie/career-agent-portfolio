# Backups: who makes them, and the one policy

Career Agent makes very few copies. Nothing copies a whole database on a preference
edit, a skill edit, a collection or an ordinary opening.

| Copy | Made by | When | Size | Kept |
|---|---|---|---|---|
| `<file>.backup` beside a settings file | saving a setting | each save that changes the file | a few kilobytes | one generation, overwritten; not made again if nothing changed |
| `data/backups/auto/*.zip` | the app | only before an upgrade changes a profile that holds data, once per schema step | the profile database (compressed) | newest 3 per profile; older ones are pruned toward 2 GiB, but never the newest verified copy of a profile |
| `C:\Users\<you>\CareerAgent-recovery-20261009` | the full reset of 2026-10-09 (an operator procedure) | once, outside the installation | the whole `data/` and `config/`, with a sha256 per file | until you delete it; the app never reads, lists or prunes it |
| `backups/career-agent-*.zip` | you, with `career-agent backup` | when you ask | profile, or `--catalogue` | until you delete it |
| `resume_helper_backups/*.zip` | moving the retired Resume helper's files | once, verified first | small | until you delete it |
| `data/profiles/.trash/` | deleting a profile | when you delete one | the profile folder, moved not copied | until you empty it |
| `data/backups/pre-*.zip` and similar | an operator procedure around a code upgrade, NOT the app | by hand | up to the whole catalogue | the app never lists, prunes or deletes them |

## The automatic backup

- The profile database and its private settings. A credential file (`.env`) is
  never included. The shared job catalogue is public, re-collectable data and is
  not copied automatically (`career-agent backup --catalogue` makes one).
- One per profile per schema step: reopening or retrying an upgrade makes no
  equivalent copy.
- Verified before it is listed: the archive is read back and the snapshot inside
  is integrity-checked. Older copies are removed only after the new one is
  verified, only if `data/backups/auto/manifest.json` lists them, and the newest
  verified copy of each profile is never removed.
- **A copy that cannot be made or verified stops the upgrade.** The migration is not
  applied, the profile is left exactly as it was, and opening it is refused with a
  sentence that says what to do (free disk space, or make a copy yourself with
  `career-agent backup`). The failure is also recorded in the manifest and shown in
  Settings, Backup. The only way past it is the explicit override
  `CAREER_AGENT_ALLOW_UPGRADE_WITHOUT_BACKUP=1`, which is recorded as `OVERRIDDEN`.
  (An upgrade run by hand with `career-agent migrate` is an operator's act and is not
  covered by this.)
- **The 2 GiB figure limits accumulation. It is never a reason to go without a copy or to
  delete the last valid one.** After a new copy is verified, older ones are pruned, oldest
  first, until the folder is under 2 GiB. Pruning stops when only the newest verified copy
  of each profile is left. So if **one** protected copy is itself larger than 2 GiB, it is
  still written and kept, and the folder is simply over its limit: Settings, Backup says so,
  and the next upgrade's verified copy is what replaces it. If the disk cannot hold it, that
  is the failure above, not a silent skip.
- Settings, Backup shows the count, total size and date of each copy.

Restoring: unzip, put the database back where the profile's folder expects it
while Career Agent is closed, copy the settings files into its `config` folder,
then run `career-agent doctor`.
