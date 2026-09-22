-- Where a board-discovery walk over an aggregator index got to, employer by
-- employer, and what each employer's page said about where its postings
-- really live. One row per (index, employer); a re-run skips the employers
-- already answered and picks up the ones it has not reached, so a walk over
-- two thousand employers can be done in bounded runs and never repeats the
-- same page. Candidate-independent by construction: the index's own employer
-- key orders the walk, and nothing about a person is a column here.
--
-- `source_board_id` is the board this lead REGISTERED, when it did: a board
-- promoted because an aggregator published the employer's own apply URL and
-- the employer's own ATS then answered for it. Additive.
CREATE TABLE board_discovery_lead (
    id                TEXT PRIMARY KEY,
    index_source      TEXT NOT NULL,           -- the aggregator index walked, e.g. remotesource
    employer_key      TEXT NOT NULL,           -- the index's own employer slug
    employer_name     TEXT,
    employer_website  TEXT,
    sample_url        TEXT NOT NULL,           -- the index page read for this employer
    origin_url        TEXT,                    -- the apply URL that page published
    provider          TEXT,                    -- the board family the origin URL names
    board_identifier  TEXT,
    external_id       TEXT,                    -- the posting on that board, in the family's stored form
    outcome           TEXT NOT NULL CHECK (outcome IN (
                          'REGISTERED', 'ALREADY_REGISTERED', 'VALIDATION_FAILED',
                          'VALIDATION_DEFERRED', 'FEED_COVERED', 'UNSUPPORTED_FAMILY',
                          'NO_ORIGIN', 'UNREADABLE', 'REFUSED_BY_HOST')),
    detail            TEXT,
    source_board_id   TEXT REFERENCES source_board(id),
    first_seen_at     TEXT NOT NULL,
    last_walked_at    TEXT NOT NULL,
    times_walked      INTEGER NOT NULL DEFAULT 1,
    UNIQUE (index_source, employer_key)
);
CREATE INDEX idx_board_discovery_lead_outcome ON board_discovery_lead(index_source, outcome);
