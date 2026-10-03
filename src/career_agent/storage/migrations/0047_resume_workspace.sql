-- Migration 0047: Resume Workspace storage (ResumeDocument schema 1.0).
-- scope: profile
--
-- Additive only. Nothing reads these tables yet: the Resume helper keeps its
-- own files until a later change moves it here. Every row is about one
-- person, so every table lives in that profile's own database.
--
-- resume_document holds the WORKING copy (cheap autosave) beside list
-- columns mirrored from it. resume_revision is the append-only history of
-- milestones. jd_snapshot is the exact job ad a tailored version was made
-- for, and never changes. The repository is `career_agent.resume_doc.store`.

CREATE TABLE jd_snapshot (
    id              TEXT PRIMARY KEY,
    job_id          TEXT,
    title           TEXT NOT NULL CHECK (length(title) > 0),
    company         TEXT,
    url             TEXT,
    text            TEXT NOT NULL,
    text_sha256     TEXT NOT NULL,
    language        TEXT,
    -- One snapshot per distinct content: the same ad captured twice is one row.
    snapshot_sha256 TEXT NOT NULL UNIQUE,
    captured_at     TEXT NOT NULL
);

CREATE TRIGGER jd_snapshot_immutable BEFORE UPDATE ON jd_snapshot
BEGIN
    SELECT RAISE(ABORT, 'jd_snapshot is immutable: capture a new snapshot');
END;

CREATE TRIGGER jd_snapshot_kept BEFORE DELETE ON jd_snapshot
BEGIN
    SELECT RAISE(ABORT, 'jd_snapshot is immutable: it is never deleted');
END;

CREATE TABLE resume_document (
    id                  TEXT PRIMARY KEY,
    kind                TEXT NOT NULL CHECK (kind IN ('MASTER', 'TAILORED', 'IMPORTED', 'SCRATCH')),
    title               TEXT NOT NULL,
    language            TEXT NOT NULL,
    parent_document_id  TEXT REFERENCES resume_document (id),
    master_document_id  TEXT REFERENCES resume_document (id),
    jd_snapshot_id      TEXT REFERENCES jd_snapshot (id),
    -- 'job:<job id>' or 'jd:<ad text sha256>': the versions of one job.
    version_group       TEXT,
    version_number      INTEGER CHECK (version_number IS NULL OR version_number > 0),
    label               TEXT,
    preferred           INTEGER NOT NULL DEFAULT 0 CHECK (preferred IN (0, 1)),
    archived_at         TEXT,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL,
    working_json        TEXT NOT NULL DEFAULT '{}',
    working_sha256      TEXT NOT NULL,
    -- A tailored version has a job ad, a group and a number; nothing else does.
    CHECK (kind NOT IN ('TAILORED') OR (jd_snapshot_id IS NOT NULL
        AND version_group IS NOT NULL AND version_number IS NOT NULL)),
    CHECK (kind IN ('TAILORED') OR (jd_snapshot_id IS NULL
        AND version_group IS NULL AND version_number IS NULL)),
    CHECK (preferred IN (0) OR (kind IN ('TAILORED') AND archived_at IS NULL)),
    UNIQUE (version_group, version_number)
);

-- At most one preferred version per job.
CREATE UNIQUE INDEX resume_document_one_preferred
    ON resume_document (version_group) WHERE preferred = 1;

CREATE TABLE resume_revision (
    id                TEXT PRIMARY KEY,
    document_id       TEXT NOT NULL REFERENCES resume_document (id),
    seq               INTEGER NOT NULL CHECK (seq > 0),
    content_json      TEXT NOT NULL DEFAULT '{}',
    content_sha256    TEXT NOT NULL,
    reason            TEXT NOT NULL CHECK (reason IN (
                          'CREATED', 'IMPORTED', 'GENERATED', 'AI_ACCEPTED',
                          'MANUAL_CHECKPOINT', 'RESTORED', 'TEMPLATE_CHANGED',
                          'EXPORTED', 'PRE_MIGRATION')),
    base_revision_id  TEXT REFERENCES resume_revision (id),
    created_at        TEXT NOT NULL,
    UNIQUE (document_id, seq)
);

CREATE TRIGGER resume_revision_append_only BEFORE UPDATE ON resume_revision
BEGIN
    SELECT RAISE(ABORT, 'resume_revision is append-only');
END;

CREATE TRIGGER resume_revision_kept BEFORE DELETE ON resume_revision
BEGIN
    SELECT RAISE(ABORT, 'resume_revision is append-only: it is never deleted');
END;

CREATE TABLE tailoring_run (
    id                   TEXT PRIMARY KEY,
    document_id          TEXT NOT NULL REFERENCES resume_document (id),
    jd_snapshot_id       TEXT NOT NULL REFERENCES jd_snapshot (id),
    master_document_id   TEXT REFERENCES resume_document (id),
    master_revision_id   TEXT REFERENCES resume_revision (id),
    mode                 TEXT NOT NULL,
    provider             TEXT,
    model                TEXT,
    prompt_digests_json  TEXT NOT NULL DEFAULT '{}',
    options_json         TEXT NOT NULL DEFAULT '{}',
    analysis_json        TEXT NOT NULL DEFAULT '{}',
    retrieval_json       TEXT NOT NULL DEFAULT '{}',
    strategy_json        TEXT NOT NULL DEFAULT '{}',
    review_json          TEXT NOT NULL DEFAULT '{}',
    validation_json      TEXT NOT NULL DEFAULT '{}',
    token_usage_json     TEXT NOT NULL DEFAULT '{}',
    status               TEXT NOT NULL CHECK (status IN ('PENDING', 'RUNNING', 'DONE', 'ERROR')),
    started_at           TEXT NOT NULL,
    finished_at          TEXT
);

CREATE TABLE tailoring_change (
    id                    TEXT PRIMARY KEY,
    run_id                TEXT NOT NULL REFERENCES tailoring_run (id),
    op_json               TEXT NOT NULL DEFAULT '{}',
    evidence_ids_json     TEXT NOT NULL DEFAULT '[]',
    requirement_ids_json  TEXT NOT NULL DEFAULT '[]',
    source                TEXT NOT NULL CHECK (source IN ('RULE', 'DRAFTER', 'REVIEWER')),
    reason                TEXT,
    decision              TEXT NOT NULL DEFAULT 'PENDING'
                              CHECK (decision IN ('PENDING', 'ACCEPTED', 'EDITED', 'REJECTED')),
    decided_at            TEXT,
    CHECK (decision IN ('PENDING') OR decided_at IS NOT NULL),
    CHECK (decision NOT IN ('PENDING') OR decided_at IS NULL)
);

CREATE TABLE resume_export (
    id              TEXT PRIMARY KEY,
    document_id     TEXT NOT NULL REFERENCES resume_document (id),
    revision_id     TEXT NOT NULL REFERENCES resume_revision (id),
    format          TEXT NOT NULL CHECK (format IN ('PDF', 'DOCX', 'JSON')),
    template        TEXT NOT NULL,
    file_path       TEXT NOT NULL,
    file_sha256     TEXT NOT NULL,
    page_count      INTEGER,
    engine          TEXT NOT NULL,
    ats_check_json  TEXT NOT NULL DEFAULT '{}',
    created_at      TEXT NOT NULL
);

-- A finding the person set aside, in ONE document. Nothing learns from it.
CREATE TABLE resume_finding_dismissal (
    document_id  TEXT NOT NULL REFERENCES resume_document (id),
    finding_key  TEXT NOT NULL,
    reason       TEXT,
    created_at   TEXT NOT NULL,
    PRIMARY KEY (document_id, finding_key)
);

CREATE INDEX tailoring_run_document ON tailoring_run (document_id);
CREATE INDEX tailoring_change_run ON tailoring_change (run_id);
CREATE INDEX resume_export_document ON resume_export (document_id);
