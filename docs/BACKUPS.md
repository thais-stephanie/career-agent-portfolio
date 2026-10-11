# Backups: who makes them, and the one policy

Career Agent makes very few copies. Nothing copies a whole database on a preference
edit, a skill edit, a collection or an ordinary opening.

| Copy | Made by | When | Size | Kept |
|---|---|---|---|---|
| `<file>.backup` beside a settings file | saving a setting | each save that changes the file | a few kilobytes | one generation, overwritten; not made again if nothing changed |
| `data/backups/auto/*.zip` | the app | only before an upgrade changes a profile that holds data, once per schema step | the profile database (compressed) | newest 3 per profile, at most 2 GiB in total |
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
- If a copy would not fit in the 2 GiB limit even after pruning, none is written
  and the manifest records why; the upgrade is not blocked by it.
- Settings, Backup shows the count, total size and date of each copy.

Restoring: unzip, put the database back where the profile's folder expects it
while Career Agent is closed, copy the settings files into its `config` folder,
then run `career-agent doctor`.
