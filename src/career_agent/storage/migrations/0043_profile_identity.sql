-- Migration 0043: which local profile a database belongs to.
--
-- One installation can now hold several local profiles (runtime/profiles.py),
-- each with its own database. The profile id is stamped the first time a
-- profile opens its database, and a database stamped for one profile is
-- refused when opened as another: one person's Career Profile, evidence and
-- applications must never be served as someone else's because two paths got
-- swapped.
--
-- Nullable and additive: an existing database is unstamped until its profile
-- first opens it, and a database used without profiles (the CLI with --db,
-- the demo) is never stamped at all.

ALTER TABLE database_identity ADD COLUMN profile_id TEXT;
