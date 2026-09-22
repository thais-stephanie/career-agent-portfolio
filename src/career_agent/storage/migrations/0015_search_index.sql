-- 0015_search_index.sql
--
-- A full-text index over the text a person actually searches: the job title,
-- the company name and the whole description.
--
-- WHY: `search` was `LOWER(description_text) LIKE '%term%'` over 16,868 rows
-- averaging 6,163 characters -- about 104 MB of text, with no index and no
-- early exit. Measured over HTTP on the real corpus, `?search=engineer` took
-- **8,674 ms** against a ~1,000 ms target, and every other filtered query on
-- the same machine sat between 1,300 and 2,600 ms. Search was not one slow
-- query among many; it was the outlier by a factor of five.
--
-- It is also not a nice-to-have. The product requires description-led
-- discovery as a first-class channel -- a posting whose title is unfamiliar
-- but whose body describes integration and automation work must be findable.
-- That means reading the body of every posting is the NORMAL path, not a rare
-- one, so it has to be indexed.
--
-- PORTABILITY (hazard, stated rather than hidden). FTS5 is SQLite-specific and
-- has no PostgreSQL equivalent; the Postgres answer is `tsvector` + GIN. This
-- is the first genuinely non-portable object in the schema. It is isolated
-- behind `ScoredJobQuery._search_clause`, which falls back to LIKE when the
-- table is absent, so a future Postgres port replaces one method and one
-- migration rather than unpicking the query layer.
--
-- CONTENTLESS, NOT EXTERNAL-CONTENT. The searchable text is a join across
-- `job` (title), `company` (name) and `job_raw` (description, keyed by
-- content_hash and SHARED between jobs whose text is identical). An
-- external-content table wants one owning table and one rowid; there is no
-- such table here. So this stores its own copy, and the cost is disk rather
-- than a fragile mapping.
--
-- STALENESS IS DETECTED, NOT ASSUMED AWAY. This index is rebuilt explicitly
-- (`rescore` refreshes it, and `reindex-search` does it alone) rather than
-- maintained by triggers, because the text it indexes comes from three tables
-- and a trigger set spanning all three is a correctness problem waiting for a
-- migration to forget one. `search_index_state` records what the index was
-- built from, so "this index is behind the corpus" is a question the system
-- can ANSWER instead of a silent wrong answer. Search still works when the
-- index is stale or missing -- it falls back to LIKE and the API says so.

CREATE VIRTUAL TABLE IF NOT EXISTS job_search USING fts5(
    job_id UNINDEXED,
    title,
    company,
    description,
    tokenize = "unicode61 remove_diacritics 2"
);

-- One row. Holds what the index was built from, so drift is detectable.
--
-- `job_count` and `max_seen` together catch the two ways the corpus moves:
-- postings added (count changes) and postings re-normalised in place (the
-- newest `last_seen_at` moves). Neither alone is enough, which is why both
-- are here.
-- `id` is TEXT, not `INTEGER PRIMARY KEY`. The latter is SQLite's implicit
-- rowid alias -- hazard A1, and `test_no_integer_primary_key_rowid_alias`
-- caught this on the first run. A singleton needs a constant key, not a
-- sequence, so the constant is spelled out.
CREATE TABLE IF NOT EXISTS search_index_state (
    id          TEXT NOT NULL PRIMARY KEY CHECK (id = 'singleton'),
    job_count   INTEGER NOT NULL,
    max_seen    TEXT,
    built_at    TEXT NOT NULL
);
