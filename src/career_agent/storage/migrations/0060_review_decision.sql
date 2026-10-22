-- Migration 0060: what the person decided about a posting after reading it.
-- scope: profile
--
-- Measurement only. Nothing reads this table to score, rank, filter, read,
-- gate or retrieve. It is the ground truth a ranking is evaluated against
-- (`evaluation/ranking.py`), so every review stops being lost in a CSV.
--
-- KEEP means "worth keeping for consideration or application". It does not
-- mean strong fit, a good chance of being hired, no gaps, or an ideal job:
-- stretches and middling postings are KEEPs when the person keeps them.
--
-- One row per posting: deciding again replaces the decision and keeps
-- `created_at`, the first time it was decided. Beside it, the EXPOSURE: what
-- the system showed at that moment, so a later rescore or reading never
-- rewrites it and an evaluation can replay it. Every exposure column is
-- nullable and NULL means "not known", never "none".

CREATE TABLE review_decision (
    job_id              TEXT    NOT NULL PRIMARY KEY,
    decision            TEXT    NOT NULL CHECK (decision IN ('KEEP', 'MAYBE', 'REJECT')),
    -- Why it is not a clear KEEP. Only for MAYBE or REJECT.
    primary_reason      TEXT    CHECK (primary_reason IS NULL OR primary_reason IN (
                            'WRONG_PRIMARY_WORK',
                            'MANDATORY_EXPERIENCE_GAP',
                            'MANDATORY_TOOL_PLATFORM_GAP',
                            'MANDATORY_CREDENTIAL_GAP',
                            'LOCATION_OR_WORK_AUTH',
                            'COMPENSATION',
                            'SENIORITY_OR_SCOPE',
                            'WORK_MODEL_OR_SCHEDULE',
                            'COMPANY_OR_OPPORTUNITY_QUALITY',
                            'NOT_INTERESTED_DESPITE_FIT',
                            'DUPLICATE',
                            'STALE_OR_CLOSED',
                            'INSUFFICIENT_INFORMATION')),
    -- More reasons from the same list, as a JSON array; any decision may
    -- carry them (a KEEP with a known gap is a stretch worth recording).
    secondary_reasons_json TEXT NOT NULL DEFAULT '[]',
    note                TEXT,
    -- The posting drawer, an imported CSV, or a recall panel.
    origin              TEXT    NOT NULL CHECK (origin IN ('APP', 'IMPORT', 'PANEL')),
    -- The exposure: the system's reading when the person decided.
    content_hash        TEXT,
    provider            TEXT,
    config_id           TEXT,
    config_version      INTEGER,
    match_schema        INTEGER,
    match_score         REAL,
    fit_band            TEXT,
    eligibility_status  TEXT,
    -- The deep level the reading gave, and whether it was current, current
    -- for the work only (`evidence`), out of date (`stale`) or absent (`unread`).
    deep_level          TEXT,
    deep_state          TEXT    CHECK (deep_state IS NULL OR deep_state IN (
                            'current', 'evidence', 'stale', 'unread')),
    deep_contract       TEXT,
    deep_assessment     TEXT,
    freshness           TEXT,
    -- Where and how the posting was served: the list's sort and the
    -- posting's 1-based rank in it, the lane (the Jobs list, another page, a
    -- panel), and the list's query (filters) as the screen sent it.
    served_sort         TEXT,
    served_rank         INTEGER,
    review_lane         TEXT,
    served_query        TEXT,
    created_at          TEXT    NOT NULL,
    updated_at          TEXT    NOT NULL,
    CHECK (primary_reason IS NULL OR decision IN ('MAYBE', 'REJECT'))
);
