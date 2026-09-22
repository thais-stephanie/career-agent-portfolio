-- Candidate organization only. Existing evidence and imported payloads stay intact.
CREATE TABLE career_company (
    id TEXT PRIMARY KEY,
    candidate_id TEXT NOT NULL REFERENCES candidate(id),
    label TEXT NOT NULL,
    merged_into TEXT REFERENCES career_company(id),
    archived INTEGER NOT NULL DEFAULT 0,
    UNIQUE(candidate_id, label)
);

CREATE TABLE career_experience (
    id TEXT PRIMARY KEY,
    candidate_id TEXT NOT NULL REFERENCES candidate(id),
    company_id TEXT REFERENCES career_company(id),
    title TEXT,
    period_start TEXT,
    period_end TEXT,
    current_role INTEGER NOT NULL DEFAULT 0,
    kind TEXT NOT NULL DEFAULT 'EMPLOYMENT',
    display_order INTEGER NOT NULL DEFAULT 0,
    archived INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE INDEX idx_career_experience_candidate ON career_experience(candidate_id, archived);

CREATE TABLE career_evidence_link (
    candidate_id TEXT NOT NULL REFERENCES candidate(id),
    claim_key TEXT NOT NULL,
    experience_id TEXT REFERENCES career_experience(id),
    category TEXT,
    display_order INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY(candidate_id, claim_key)
);

CREATE TABLE career_company_decision (
    candidate_id TEXT NOT NULL REFERENCES candidate(id),
    first_label TEXT NOT NULL,
    second_label TEXT NOT NULL,
    decision TEXT NOT NULL,
    PRIMARY KEY(candidate_id, first_label, second_label)
);

CREATE TABLE career_history_event (
    id TEXT PRIMARY KEY,
    candidate_id TEXT NOT NULL REFERENCES candidate(id),
    action TEXT NOT NULL,
    before_json TEXT NOT NULL DEFAULT '{}',
    after_json TEXT NOT NULL DEFAULT '{}',
    scope_json TEXT NOT NULL DEFAULT '[]',
    reversible INTEGER NOT NULL DEFAULT 1,
    undone_by TEXT REFERENCES career_history_event(id),
    created_at TEXT NOT NULL
);
CREATE INDEX idx_career_history_candidate ON career_history_event(candidate_id, created_at);
