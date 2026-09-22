-- Default discovery and its hidden-count variants should read compact facts,
-- not fetch large result_json rows once per posting while collection writes.
-- The 207k-posting production corpus exposed a cold-read timeout (>180 s).
CREATE INDEX idx_job_match_discovery_gates
ON job_match(config_id, config_version, eligibility_status, screening_state,
             seniority, job_id, match_score);
