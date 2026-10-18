-- 本阶段只创建表，不迁移已有结构；数据库文件应使用本项目初始化。
CREATE TABLE IF NOT EXISTS people (
    person_id TEXT PRIMARY KEY NOT NULL CHECK (length(trim(person_id)) > 0),
    canonical_name TEXT NOT NULL CHECK (length(trim(canonical_name)) > 0),
    dynasty TEXT,
    birth_year INTEGER CHECK (birth_year IS NULL OR (typeof(birth_year) = 'integer' AND birth_year != 0)),
    death_year INTEGER CHECK (death_year IS NULL OR (typeof(death_year) = 'integer' AND death_year != 0)),
    native_place TEXT,
    identity_description TEXT,
    verification_status TEXT NOT NULL DEFAULT 'pending'
        CHECK (verification_status IN ('pending', 'verified', 'disputed', 'rejected')),
    CHECK (birth_year IS NULL OR death_year IS NULL OR birth_year <= death_year)
);

CREATE TABLE IF NOT EXISTS aliases (
    alias_id TEXT PRIMARY KEY NOT NULL CHECK (length(trim(alias_id)) > 0),
    person_id TEXT NOT NULL REFERENCES people(person_id) ON DELETE RESTRICT ON UPDATE RESTRICT,
    alias_name TEXT NOT NULL CHECK (length(trim(alias_name)) > 0),
    normalized_name TEXT NOT NULL CHECK (length(trim(normalized_name)) > 0),
    alias_type TEXT NOT NULL CHECK (alias_type IN ('name', 'courtesy', 'art', 'posthumous', 'other', 'unknown')),
    verification_status TEXT NOT NULL DEFAULT 'pending'
        CHECK (verification_status IN ('pending', 'verified', 'disputed', 'rejected')),
    UNIQUE (person_id, alias_name, alias_type)
);

CREATE TABLE IF NOT EXISTS sources (
    source_id TEXT PRIMARY KEY NOT NULL CHECK (length(trim(source_id)) > 0),
    book_title TEXT NOT NULL CHECK (length(trim(book_title)) > 0),
    volume TEXT,
    chapter TEXT,
    platform TEXT NOT NULL CHECK (length(trim(platform)) > 0),
    original_url TEXT NOT NULL CHECK (length(trim(original_url)) > 0),
    edition TEXT
);

CREATE TABLE IF NOT EXISTS evidences (
    evidence_id TEXT PRIMARY KEY NOT NULL CHECK (length(trim(evidence_id)) > 0),
    alias_id TEXT NOT NULL REFERENCES aliases(alias_id) ON DELETE RESTRICT ON UPDATE RESTRICT,
    source_id TEXT NOT NULL REFERENCES sources(source_id) ON DELETE RESTRICT ON UPDATE RESTRICT,
    original_text TEXT NOT NULL CHECK (length(trim(original_text)) > 0),
    usage_relation TEXT NOT NULL CHECK (usage_relation IN ('supports', 'refutes', 'mentions', 'uncertain')),
    evidence_nature TEXT NOT NULL CHECK (evidence_nature IN ('direct', 'indirect')),
    verification_status TEXT NOT NULL DEFAULT 'pending'
        CHECK (verification_status IN ('pending', 'verified', 'disputed', 'rejected')),
    notes TEXT
);

-- 名称索引故意不唯一，同名、同字、同号可分别属于多个 Person ID。
CREATE INDEX IF NOT EXISTS idx_people_name ON people(canonical_name);
CREATE INDEX IF NOT EXISTS idx_people_status ON people(verification_status);
CREATE INDEX IF NOT EXISTS idx_aliases_name ON aliases(alias_name);
CREATE INDEX IF NOT EXISTS idx_aliases_normalized_name ON aliases(normalized_name);
CREATE INDEX IF NOT EXISTS idx_aliases_person ON aliases(person_id);
CREATE INDEX IF NOT EXISTS idx_aliases_status ON aliases(verification_status);
CREATE INDEX IF NOT EXISTS idx_evidences_alias ON evidences(alias_id);
CREATE INDEX IF NOT EXISTS idx_evidences_source ON evidences(source_id);
CREATE INDEX IF NOT EXISTS idx_evidences_status ON evidences(verification_status);

