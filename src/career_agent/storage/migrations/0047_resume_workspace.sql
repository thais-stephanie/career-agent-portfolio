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
    working_json        TEXT NOT NULL CHECK (json_valid(working_json)),
    working_sha256      TEXT NOT NULL,
    CHECK ((kind = 'TAILORED') = (jd_snapshot_id IS NOT NULL)),
    CHECK ((kind = 'TAILORED') = (version_group IS NOT NULL)),
    CHECK ((kind = 'TAILORED') = (version_number IS NOT NULL)),
    CHECK (preferred = 0 OR (kind = 'TAILORED' AND archived_at IS NULL)),
    UNIQUE (version_group, version_number)
);

-- At most one preferred version per job.
CREATE UNIQUE INDEX resume_document_one_preferred
    ON resume_document (version_group) WHERE preferred = 1;

CREATE TABLE resume_revision (
    id                TEXT PRIMARY KEY,
    document_id       TEXT NOT NULL REFERENCES resume_document (id),
    seq               INTEGER NOT NULL CHECK (seq > 0),
    content_json      TEXT NOT NULL CHECK (json_valid(content_json)),
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

CREATE TABLE tailoring_run (
    id                   TEXT PRIMARY KEY,
    document_id          TEXT NOT NULL REFERENCES resume_document (id),
    jd_snapshot_id       TEXT NOT NULL REFERENCES jd_snapshot (id),
    master_document_id   TEXT REFERENCES resume_document (id),
    master_revision_id   TEXT REFERENCES resume_revision (id),
    mode                 TEXT NOT NULL,
    provider             TEXT,
    model                TEXT,
    prompt_digests_json  TEXT CHECK (prompt_digests_json IS NULL OR json_valid(prompt_digests_json)),
    options_json         TEXT CHECK (options_json IS NULL OR json_valid(options_json)),
    analysis_json        TEXT CHECK (analysis_json IS NULL OR json_valid(analysis_json)),
    retrieval_json       TEXT CHECK (retrieval_json IS NULL OR json_valid(retrieval_json)),
    strategy_json        TEXT CHECK (strategy_json IS NULL OR json_valid(strategy_json)),
    review_json          TEXT CHECK (review_json IS NULL OR json_valid(review_json)),
    validation_json      TEXT CHECK (validation_json IS NULL OR json_valid(validation_json)),
    token_usage_json     TEXT CHECK (token_usage_json IS NULL OR json_valid(token_usage_json)),
    status               TEXT NOT NULL CHECK (status IN ('PENDING', 'RUNNING', 'DONE', 'ERROR')),
    started_at           TEXT NOT NULL,
    finished_at          TEXT
);

CREATE TABLE tailoring_change (
    id                    TEXT PRIMARY KEY,
    run_id                TEXT NOT NULL REFERENCES tailoring_run (id),
    op_json               TEXT NOT NULL CHECK (json_valid(op_json)),
    evidence_ids_json     TEXT NOT NULL CHECK (json_valid(evidence_ids_json)),
    requirement_ids_json  TEXT NOT NULL CHECK (json_valid(requirement_ids_json)),
    source                TEXT NOT NULL CHECK (source IN ('RULE', 'DRAFTER', 'REVIEWER')),
    reason                TEXT,
    decision              TEXT NOT NULL DEFAULT 'PENDING'
                              CHECK (decision IN ('PENDING', 'ACCEPTED', 'EDITED', 'REJECTED')),
    decided_at            TEXT,
    CHECK ((decision = 'PENDING') = (decided_at IS NULL))
);

CREATE TABLE resume_export (
    id              TEXT PRIMARY KEY,
    document_id     TEXT NOT NULL REFERENCES resume_document (id),
    revision_id     TEXT NOT NULL REFERENCES resume_revision (id),
    format          TEXT NOT NULL CHECK (format IN ('PDF', 'DOCX', 'JSON')),
    template        TEXT NOT NULL,
    file_path       TEXT NOT NULL,
    file_sha256     TEXT NOT NULL,
    page_count      INTEGER CHECK (page_count IS NULL OR page_count > 0),
    engine          TEXT NOT NULL,
    ats_check_json  TEXT CHECK (ats_check_json IS NULL OR json_valid(ats_check_json)),
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

CREATE INDEX resume_revision_document ON resume_revision (document_id, seq);
CREATE INDEX tailoring_run_document ON tailoring_run (document_id);
CREATE INDEX tailoring_change_run ON tailoring_change (run_id);
CREATE INDEX resume_export_document ON resume_export (document_id);
