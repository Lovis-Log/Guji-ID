-- Additive migration; no changes to the original four tables.
CREATE TABLE IF NOT EXISTS data_releases (
 release_id TEXT PRIMARY KEY, provider TEXT NOT NULL, version TEXT NOT NULL,
 sqlite_sha256 TEXT NOT NULL, archive_sha256 TEXT NOT NULL,
 license_id TEXT NOT NULL, metadata_json TEXT NOT NULL, UNIQUE(provider,version));
CREATE TABLE IF NOT EXISTS auto_batches (
 batch_id TEXT PRIMARY KEY, release_id TEXT NOT NULL REFERENCES data_releases,
 status TEXT NOT NULL CHECK(status IN ('committed','rolled_back')),
 imported_at TEXT NOT NULL, manifest_sha256 TEXT NOT NULL,
 counts_json TEXT NOT NULL, backup_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS external_people (
 provider TEXT NOT NULL, external_id TEXT NOT NULL, person_id TEXT NOT NULL REFERENCES people,
 release_id TEXT NOT NULL REFERENCES data_releases, record_json TEXT NOT NULL,
 record_sha256 TEXT NOT NULL, created_batch TEXT NOT NULL REFERENCES auto_batches,
 PRIMARY KEY(provider,external_id), UNIQUE(provider,person_id));
CREATE TABLE IF NOT EXISTS automatic_evidence (
 evidence_id TEXT PRIMARY KEY REFERENCES evidences,
 release_id TEXT NOT NULL REFERENCES data_releases,
 method TEXT NOT NULL CHECK(method IN ('A','A+')),
 evidence_kind TEXT NOT NULL CHECK(evidence_kind IN ('structured_record','classical_text')),
 source_table TEXT NOT NULL, record_key_json TEXT NOT NULL, record_json TEXT NOT NULL,
 record_sha256 TEXT NOT NULL, locator_json TEXT NOT NULL,
 batch_id TEXT NOT NULL REFERENCES auto_batches,
 CHECK((method='A' AND evidence_kind='structured_record') OR (method='A+' AND evidence_kind='classical_text')));
CREATE TABLE IF NOT EXISTS batch_entities (
 batch_id TEXT NOT NULL REFERENCES auto_batches,
 table_name TEXT NOT NULL CHECK(table_name IN ('people','aliases','sources','evidences')),
 entity_id TEXT NOT NULL, row_sha256 TEXT NOT NULL,
 PRIMARY KEY(batch_id,table_name,entity_id), UNIQUE(table_name,entity_id));
CREATE TABLE IF NOT EXISTS auto_events (
 event_id INTEGER PRIMARY KEY, batch_id TEXT NOT NULL REFERENCES auto_batches,
 event_type TEXT NOT NULL, occurred_at TEXT NOT NULL, details_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS search_variants (
 variant_id TEXT PRIMARY KEY, term TEXT NOT NULL, person_id TEXT NOT NULL REFERENCES people,
 alias_id TEXT REFERENCES aliases, method TEXT NOT NULL, batch_id TEXT NOT NULL REFERENCES auto_batches);
CREATE INDEX IF NOT EXISTS idx_search_variants_term ON search_variants(term);
CREATE TABLE IF NOT EXISTS pipeline_summary (
 singleton INTEGER PRIMARY KEY CHECK(singleton=1), updated_at TEXT NOT NULL,
 staged INTEGER NOT NULL, quarantined INTEGER NOT NULL, reason_counts_json TEXT NOT NULL);
