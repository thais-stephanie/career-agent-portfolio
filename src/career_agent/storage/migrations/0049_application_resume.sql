-- Migration 0049: which resume a person says they used for an application.
-- scope: profile
--
-- Additive only. One row per job: the person marked this resume version as
-- the one they sent, and the revision it was at when they said so. Nothing
-- writes it on its own: an export is never taken to mean "sent". Marking
-- another version replaces the row; the resume and its history stay.

CREATE TABLE application_resume (
    job_id       TEXT PRIMARY KEY,
    document_id  TEXT NOT NULL REFERENCES resume_document (id),
    revision_id  TEXT NOT NULL REFERENCES resume_revision (id),
    marked_at    TEXT NOT NULL
);

CREATE INDEX application_resume_document ON application_resume (document_id);
