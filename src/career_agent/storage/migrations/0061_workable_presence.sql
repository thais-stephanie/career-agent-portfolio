-- Migration 0061: which Workable postings each complete feed version listed.
-- scope: catalogue
--
-- Shadow only (docs/research/workable-lifecycle/). Nothing reads these tables to
-- close, score, rank, filter or show a posting, and writing them never touches
-- `job`, so no trigger fires: no dirty row and no population revision.
--
-- Public data, the same for every profile. Dropping all four loses only the
-- observation history.

-- One row per feed version evaluated. `version` is the feed's own Last-Modified,
-- so one version cannot be counted twice, by any profile or window. `seq` is the
-- order of observation: a ULID does not order two reads in the same millisecond.
CREATE TABLE workable_feed_read (
    id                  TEXT    NOT NULL PRIMARY KEY,
    seq                 INTEGER NOT NULL UNIQUE,
    version             TEXT    NOT NULL UNIQUE,
    version_at          TEXT    NOT NULL,
    observed_at         TEXT    NOT NULL,
    outcome             TEXT    NOT NULL CHECK (outcome IN (
                            'BASELINE', 'COUNTED', 'HELD_FEED_EMPTY', 'HELD_FEED_SHRANK',
                            'HELD_TOO_MANY_NEW_MISSES', 'HELD_OLDER_VERSION')),
    feed_unique         INTEGER NOT NULL,
    open_rows           INTEGER NOT NULL,
    present_rows        INTEGER NOT NULL,
    absent_rows         INTEGER NOT NULL,
    new_misses          INTEGER NOT NULL,
    reappeared          INTEGER NOT NULL,
    would_close         INTEGER NOT NULL,
    policy_json         TEXT    NOT NULL DEFAULT '{}'
);

-- The current state of one posting. `misses` counts consecutive counted reads
-- without it. `baseline` marks a posting already missing from the first read
-- ever evaluated: the backlog, not a departure anyone watched happen.
CREATE TABLE workable_presence (
    job_id              TEXT    NOT NULL PRIMARY KEY
                                REFERENCES job (id) ON DELETE CASCADE,
    last_present_read   TEXT,
    misses              INTEGER NOT NULL DEFAULT 0,
    first_miss_at       TEXT,
    first_miss_read     TEXT,
    baseline            INTEGER NOT NULL DEFAULT 0,
    last_decision       TEXT,
    decided_read        TEXT
) WITHOUT ROWID;

-- Append-only: every hypothetical decision, written when it changes. No foreign
-- key to `job` on purpose, so the record outlives the posting.
CREATE TABLE workable_shadow_event (
    read_id             TEXT    NOT NULL REFERENCES workable_feed_read (id),
    job_id              TEXT    NOT NULL,
    decision            TEXT    NOT NULL CHECK (decision IN (
                            'WOULD_HOLD', 'WOULD_CLOSE', 'WOULD_REOPEN', 'WOULD_KEEP')),
    reason              TEXT    NOT NULL,
    misses              INTEGER NOT NULL,
    created_at          TEXT    NOT NULL,
    PRIMARY KEY (read_id, job_id, decision)
) WITHOUT ROWID;

-- The seeded sample checked against Workable's posting page. NULL answer: not
-- asked yet.
CREATE TABLE workable_audit_sample (
    read_id             TEXT    NOT NULL REFERENCES workable_feed_read (id),
    job_id              TEXT    NOT NULL,
    url                 TEXT    NOT NULL,
    answer              TEXT    CHECK (answer IS NULL OR answer IN ('GONE', 'PUBLISHED', 'UNKNOWN')),
    detail              TEXT,
    attempts            INTEGER NOT NULL DEFAULT 0,
    requests            INTEGER NOT NULL DEFAULT 0,
    asked_at            TEXT,
    PRIMARY KEY (read_id, job_id)
) WITHOUT ROWID;
