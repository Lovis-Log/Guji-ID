-- Stage 7 追加结构：古籍原文库、篇章、段落、证据定位与人物深度档案。
-- 只新增表，不修改或删除既有四表及其记录。所有时间均为 ISO 8601 文本。

CREATE TABLE IF NOT EXISTS classical_works (
    work_id TEXT PRIMARY KEY NOT NULL CHECK (length(trim(work_id)) > 0),
    title TEXT NOT NULL CHECK (length(trim(title)) > 0),
    title_aliases TEXT,
    author TEXT,
    dynasty TEXT,
    category TEXT,
    source_platform TEXT NOT NULL CHECK (length(trim(source_platform)) > 0),
    source_url TEXT NOT NULL CHECK (length(trim(source_url)) > 0),
    digital_edition TEXT,
    license_id TEXT NOT NULL CHECK (length(trim(license_id)) > 0),
    license_url TEXT NOT NULL CHECK (length(trim(license_url)) > 0),
    license_scope TEXT,
    retrieved_at TEXT NOT NULL,
    source_commit TEXT,
    text_sha256 TEXT,
    description TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS classical_chapters (
    chapter_id TEXT PRIMARY KEY NOT NULL CHECK (length(trim(chapter_id)) > 0),
    work_id TEXT NOT NULL REFERENCES classical_works(work_id) ON DELETE RESTRICT,
    volume TEXT,
    chapter_title TEXT NOT NULL CHECK (length(trim(chapter_title)) > 0),
    sequence INTEGER NOT NULL,
    source_path TEXT NOT NULL,
    source_url TEXT NOT NULL,
    source_sha256 TEXT NOT NULL CHECK (length(source_sha256) = 64),
    text_sha256 TEXT NOT NULL CHECK (length(text_sha256) = 64),
    original_text TEXT NOT NULL CHECK (length(original_text) > 0),
    search_text TEXT NOT NULL CHECK (length(search_text) > 0),
    char_count INTEGER NOT NULL CHECK (char_count > 0),
    paragraph_count INTEGER NOT NULL DEFAULT 0 CHECK (paragraph_count >= 0),
    page_unit_count INTEGER NOT NULL DEFAULT 0 CHECK (page_unit_count >= 0),
    completeness TEXT NOT NULL CHECK (completeness IN ('full', 'partial')),
    completeness_note TEXT,
    verification_status TEXT NOT NULL CHECK (verification_status IN ('verified', 'pending', 'disputed', 'rejected')),
    license_id TEXT NOT NULL,
    retrieved_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (work_id, sequence),
    CHECK (char_count = length(original_text))
);

CREATE TABLE IF NOT EXISTS classical_paragraphs (
    paragraph_id TEXT PRIMARY KEY NOT NULL CHECK (length(trim(paragraph_id)) > 0),
    chapter_id TEXT NOT NULL REFERENCES classical_chapters(chapter_id) ON DELETE RESTRICT,
    ordinal INTEGER NOT NULL CHECK (ordinal >= 1),
    scheme TEXT NOT NULL CHECK (scheme IN ('page', 'reading')),
    text TEXT NOT NULL CHECK (length(text) > 0),
    char_start INTEGER NOT NULL CHECK (char_start >= 0),
    char_end INTEGER NOT NULL CHECK (char_end > char_start),
    page_label TEXT,
    UNIQUE (chapter_id, scheme, ordinal)
);

CREATE TABLE IF NOT EXISTS classical_evidence_links (
    link_id TEXT PRIMARY KEY NOT NULL CHECK (length(trim(link_id)) > 0),
    evidence_id TEXT NOT NULL REFERENCES evidences(evidence_id) ON DELETE RESTRICT,
    person_id TEXT NOT NULL REFERENCES people(person_id) ON DELETE RESTRICT,
    alias_id TEXT REFERENCES aliases(alias_id) ON DELETE RESTRICT,
    chapter_id TEXT NOT NULL REFERENCES classical_chapters(chapter_id) ON DELETE RESTRICT,
    paragraph_id TEXT REFERENCES classical_paragraphs(paragraph_id) ON DELETE RESTRICT,
    quote TEXT NOT NULL CHECK (length(trim(quote)) > 0),
    quote_start INTEGER NOT NULL CHECK (quote_start >= 0),
    quote_end INTEGER NOT NULL CHECK (quote_end > quote_start),
    evidence_category TEXT NOT NULL CHECK (evidence_category IN
        ('direct_alias', 'direct_identity', 'biography', 'literary', 'later_comment')),
    claim TEXT NOT NULL CHECK (length(trim(claim)) > 0),
    explanation TEXT,
    identity_match TEXT NOT NULL CHECK (identity_match IN ('confirmed', 'ambiguous', 'rejected')),
    verification_status TEXT NOT NULL CHECK (verification_status IN ('verified', 'pending', 'disputed', 'rejected')),
    quote_sha256 TEXT NOT NULL CHECK (length(quote_sha256) = 64),
    created_at TEXT NOT NULL,
    UNIQUE (evidence_id, chapter_id, quote_start, quote_end)
);

CREATE TABLE IF NOT EXISTS classical_quarantine (
    candidate_id TEXT PRIMARY KEY NOT NULL CHECK (length(trim(candidate_id)) > 0),
    person_id TEXT,
    chapter_id TEXT,
    candidate_json TEXT NOT NULL,
    reason TEXT NOT NULL CHECK (length(trim(reason)) > 0),
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'accepted', 'rejected')),
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS person_profiles (
    person_id TEXT PRIMARY KEY NOT NULL REFERENCES people(person_id) ON DELETE RESTRICT,
    tier TEXT NOT NULL CHECK (tier IN ('A', 'B', 'C')),
    biography_modern TEXT,
    biography_basis TEXT,
    chronology_note TEXT,
    research_note TEXT,
    completeness_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS person_timeline_events (
    event_id TEXT PRIMARY KEY NOT NULL CHECK (length(trim(event_id)) > 0),
    person_id TEXT NOT NULL REFERENCES people(person_id) ON DELETE RESTRICT,
    year_label TEXT NOT NULL CHECK (length(trim(year_label)) > 0),
    year_value INTEGER,
    certainty TEXT NOT NULL CHECK (certainty IN ('exact', 'approximate', 'disputed', 'unknown')),
    event_text TEXT NOT NULL CHECK (length(trim(event_text)) > 0),
    place TEXT,
    evidence_link_id TEXT REFERENCES classical_evidence_links(link_id) ON DELETE RESTRICT,
    source_note TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS person_relations (
    relation_id TEXT PRIMARY KEY NOT NULL CHECK (length(trim(relation_id)) > 0),
    person_id TEXT NOT NULL REFERENCES people(person_id) ON DELETE RESTRICT,
    related_person_id TEXT REFERENCES people(person_id) ON DELETE RESTRICT,
    related_name TEXT NOT NULL CHECK (length(trim(related_name)) > 0),
    relation_type TEXT NOT NULL CHECK (length(trim(relation_type)) > 0),
    evidence_link_id TEXT REFERENCES classical_evidence_links(link_id) ON DELETE RESTRICT,
    note TEXT,
    verification_status TEXT NOT NULL CHECK (verification_status IN ('verified', 'pending', 'disputed', 'rejected')),
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS person_literary_works (
    entry_id TEXT PRIMARY KEY NOT NULL CHECK (length(trim(entry_id)) > 0),
    person_id TEXT NOT NULL REFERENCES people(person_id) ON DELETE RESTRICT,
    title TEXT NOT NULL CHECK (length(trim(title)) > 0),
    genre TEXT,
    chapter_id TEXT REFERENCES classical_chapters(chapter_id) ON DELETE RESTRICT,
    attribution_status TEXT NOT NULL CHECK (attribution_status IN ('attributed', 'disputed', 'unknown')),
    note TEXT,
    evidence_link_id TEXT REFERENCES classical_evidence_links(link_id) ON DELETE RESTRICT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS person_disputes (
    dispute_id TEXT PRIMARY KEY NOT NULL CHECK (length(trim(dispute_id)) > 0),
    person_id TEXT NOT NULL REFERENCES people(person_id) ON DELETE RESTRICT,
    topic TEXT NOT NULL CHECK (length(trim(topic)) > 0),
    account_a TEXT NOT NULL,
    account_b TEXT,
    note TEXT,
    evidence_link_id TEXT REFERENCES classical_evidence_links(link_id) ON DELETE RESTRICT,
    verification_status TEXT NOT NULL CHECK (verification_status IN ('verified', 'pending', 'disputed', 'rejected')),
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS classical_processing_runs (
    run_id TEXT PRIMARY KEY NOT NULL CHECK (length(trim(run_id)) > 0),
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL CHECK (status IN ('running', 'completed', 'failed', 'rolled_back')),
    config_sha256 TEXT,
    stats_json TEXT NOT NULL DEFAULT '{}',
    error TEXT
);

CREATE INDEX IF NOT EXISTS idx_classical_chapters_work ON classical_chapters(work_id, sequence);
CREATE INDEX IF NOT EXISTS idx_classical_paragraphs_chapter ON classical_paragraphs(chapter_id, scheme, ordinal);
CREATE INDEX IF NOT EXISTS idx_classical_links_person ON classical_evidence_links(person_id, chapter_id);
CREATE INDEX IF NOT EXISTS idx_classical_links_alias ON classical_evidence_links(alias_id);
CREATE INDEX IF NOT EXISTS idx_classical_links_chapter ON classical_evidence_links(chapter_id, quote_start);
CREATE INDEX IF NOT EXISTS idx_profiles_tier ON person_profiles(tier, person_id);
CREATE INDEX IF NOT EXISTS idx_timeline_person ON person_timeline_events(person_id, year_value, year_label);
CREATE INDEX IF NOT EXISTS idx_relations_person ON person_relations(person_id);
CREATE INDEX IF NOT EXISTS idx_literary_person ON person_literary_works(person_id);
CREATE INDEX IF NOT EXISTS idx_disputes_person ON person_disputes(person_id);
