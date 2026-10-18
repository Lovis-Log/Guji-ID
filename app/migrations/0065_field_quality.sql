CREATE TABLE IF NOT EXISTS quality_batches (
 batch_id TEXT PRIMARY KEY, imported_at TEXT NOT NULL, manifest_sha256 TEXT NOT NULL,
 backup_json TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN ('committed','rolled_back')),
 after_fingerprint TEXT);
CREATE TABLE IF NOT EXISTS quality_identity (
 provider TEXT NOT NULL, external_id INTEGER NOT NULL, person_id TEXT NOT NULL REFERENCES people,
 basis_json TEXT NOT NULL, batch_id TEXT NOT NULL REFERENCES quality_batches,
 PRIMARY KEY(provider,external_id), UNIQUE(provider,person_id));
CREATE TABLE IF NOT EXISTS field_facts (
 fact_id TEXT PRIMARY KEY, person_id TEXT NOT NULL REFERENCES people,
 field TEXT NOT NULL CHECK(field IN ('canonical_name','dynasty','birth_year','death_year','native_place','identity_description')),
 value_json TEXT NOT NULL, grade TEXT NOT NULL CHECK(grade IN ('A','B','C','D')),
 source_id TEXT NOT NULL REFERENCES sources, source_version TEXT NOT NULL,
 record_json TEXT NOT NULL, locator_json TEXT NOT NULL, qualifier TEXT NOT NULL DEFAULT '',
 selected INTEGER NOT NULL CHECK(selected IN (0,1)), batch_id TEXT NOT NULL REFERENCES quality_batches,
 CHECK(grade!='D' OR selected=0));
CREATE UNIQUE INDEX IF NOT EXISTS idx_selected_fact ON field_facts(person_id,field) WHERE selected=1;
CREATE INDEX IF NOT EXISTS idx_field_person ON field_facts(person_id);
CREATE TABLE IF NOT EXISTS relation_quality (
 alias_id TEXT PRIMARY KEY REFERENCES aliases, grade TEXT NOT NULL CHECK(grade IN ('A','B','C')),
 source_id TEXT NOT NULL REFERENCES sources, basis_json TEXT NOT NULL,
 batch_id TEXT NOT NULL REFERENCES quality_batches);
CREATE TABLE IF NOT EXISTS quality_quarantine (
 candidate_id TEXT PRIMARY KEY, person_id TEXT, field TEXT NOT NULL,
 grade TEXT NOT NULL DEFAULT 'D' CHECK(grade='D'), reason TEXT NOT NULL,
 source_json TEXT NOT NULL, batch_id TEXT NOT NULL REFERENCES quality_batches);
CREATE TABLE IF NOT EXISTS catalog_terms (
 term TEXT NOT NULL, person_id TEXT NOT NULL REFERENCES people, alias_id TEXT REFERENCES aliases,
 original TEXT NOT NULL, method TEXT NOT NULL, PRIMARY KEY(term,person_id,original));
CREATE INDEX IF NOT EXISTS idx_catalog_term ON catalog_terms(term);
