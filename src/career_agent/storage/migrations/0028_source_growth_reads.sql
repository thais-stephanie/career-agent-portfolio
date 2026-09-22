-- Discover must remain readable while a large source adds unscored inventory.
-- Count current open populations without reading job descriptions or match JSON.
CREATE INDEX idx_job_open_population ON job(closed_at, id, content_hash);
CREATE INDEX idx_job_match_population ON job_match(config_id, config_version, job_id);
