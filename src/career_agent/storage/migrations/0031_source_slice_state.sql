-- Where a query-scoped source's walk got to, slice by slice, so a bounded run
-- resumes where the last one stopped instead of starting again from whichever
-- slice one candidate's markets put first. Provider-neutral: a slice key is
-- the vendor's own geography and vocabulary, never a candidate's. Additive.
CREATE TABLE source_slice_state (
    provider         TEXT NOT NULL,
    slice_key        TEXT NOT NULL,
    state            TEXT NOT NULL,          -- NOT_STARTED, RUNNING, PARTIAL, COMPLETE, FAILED, PAUSED_PROVIDER_LIMIT
    times_walked     INTEGER NOT NULL DEFAULT 0,
    last_started_at  TEXT,
    last_finished_at TEXT,
    last_ended       TEXT,                   -- the collector's own word: exhausted, capped, caught_up, budget, stopped, failed
    last_pages       INTEGER NOT NULL DEFAULT 0,
    last_rows        INTEGER NOT NULL DEFAULT 0,
    last_new         INTEGER NOT NULL DEFAULT 0,
    last_error       TEXT,
    PRIMARY KEY (provider, slice_key)
);
CREATE INDEX idx_source_slice_state_walks ON source_slice_state(provider, times_walked, last_finished_at);
