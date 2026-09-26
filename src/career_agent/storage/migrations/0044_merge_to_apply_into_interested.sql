-- Migration 0044: "To apply" is merged into "Interested".
-- scope: profile
--
-- TO_APPLY ("decided to apply; not yet applied") could not be told apart
-- from SHORTLISTED ("Interested") by the people using it, so the stage is
-- retired and every application standing at it moves to SHORTLISTED.
--
-- What is preserved: the job, the application row and its id, `saved`,
-- `notes`, `applied_at`, `created_at` and `updated_at` (the move is a rename,
-- not a new decision, so no date moves), and every history event as written.
-- History is append-only: one event per moved application records the merge,
-- dated at the application's own `updated_at` so it sorts after the moves it
-- follows. Old events that say TO_APPLY stay as they are, and readers map
-- the retired value (domain/application.py `LEGACY_STATUSES`).
--
-- The CHECK constraints still admit TO_APPLY. Rebuilding both tables to drop
-- one word would put every application at risk for no protection the code
-- does not already give: the vocabulary no longer has the value, so nothing
-- writes it.

INSERT INTO job_application_event
    (id, job_id, from_status, to_status, applied_at, note, occurred_at)
SELECT
    'm0044-' || id, job_id, 'TO_APPLY', 'SHORTLISTED', applied_at,
    '"To apply" was merged into "Interested".', updated_at
FROM job_application
WHERE status = 'TO_APPLY';

UPDATE job_application SET status = 'SHORTLISTED' WHERE status = 'TO_APPLY';
