"""Synthetic external source records; never used as historical evidence."""
import copy
import json
import sqlite3
from pathlib import Path
import pytest

from app.automatic_ingestion import (TYPE_MAP, canonical_json, decide, inspect_source, migrate,
    payload_matches_source, rollback_batch, run_pipeline, search_key, stage_records, validate_auxiliary_review)
from app.batch_import import database_counts, existing_connection
from app.collector import digest
from app.db import connect_db, init_db
from app.source_acquisition import extract_database, file_hash, verify_hash


@pytest.fixture
def auto_source(tmp_path):
    path = tmp_path / "TEST_ONLY_CBDB.sqlite3"
    with sqlite3.connect(path) as c:
        c.executescript("""
        CREATE TABLE BIOG_MAIN(c_personid INTEGER PRIMARY KEY,c_name_chn TEXT,c_dy INTEGER);
        CREATE TABLE ALTNAME_DATA(c_personid INTEGER,c_alt_name_chn TEXT,c_alt_name_type_code INTEGER,c_source INTEGER,c_pages TEXT,
          PRIMARY KEY(c_personid,c_alt_name_chn,c_alt_name_type_code));
        CREATE TABLE ALTNAME_CODES(c_name_type_code INTEGER PRIMARY KEY,c_name_type_desc_chn TEXT);
        CREATE TABLE TEXT_CODES(c_textid INTEGER PRIMARY KEY,c_title_chn TEXT);
        CREATE TABLE DYNASTIES(c_dy INTEGER PRIMARY KEY,c_dynasty_chn TEXT);
        CREATE TABLE BIOG_SOURCE_DATA(c_personid INTEGER,c_textid INTEGER,c_pages TEXT);
        """)
        c.executemany("INSERT INTO BIOG_MAIN VALUES(?,?,1)", [(1, "仅测试甲人"), (2, "仅测试乙人"), (3, "仅测试丙人"), (4, "仅测试丁人")])
        c.executemany("INSERT INTO ALTNAME_DATA VALUES(?,?,?,?,NULL)", [(1, "仅测试共用字", 4, 10), (2, "仅测试共用字", 4, 10), (3, "仅测试未知类", 0, 10), (4, "仅测试悬空来源", 4, 999)])
        c.executemany("INSERT INTO ALTNAME_CODES VALUES(?,?)", [(4, "字"), (0, "未詳")])
        c.execute("INSERT INTO TEXT_CODES VALUES(10,'TEST ONLY NOT A HISTORICAL BOOK')")
        c.execute("INSERT INTO DYNASTIES VALUES(1,'仅测试朝代')")
    release = {"provider": "CBDB", "version": "20200101", "sqlite_sha256": file_hash(path), "archive_sha256": "a" * 64,
        "license_id": "CC-BY-NC-SA-4.0", "license_scope_date": "20260602", "license_scope_max_people": 100,
        "download_url": "https://example.invalid/TEST_ONLY_CBDB.zip", "simulated_test_only": True}
    stage = tmp_path / "TEST_ONLY_STAGE.sqlite3"
    stage_records(path, release, stage, 100)
    return path, release, stage


def run(auto_source, tmp_path, **kwargs):
    source, release, stage = auto_source
    return run_pipeline(source, release, stage, tmp_path / "TEST_ONLY_FORMAL.db", tmp_path / "reports", target_people=4, **kwargs)


def test_staging_uses_real_schema_mapping_and_resumes_without_duplicates(auto_source):
    source, release, stage = auto_source
    assert inspect_source(source, release)["positive_people"] == 4
    result = stage_records(source, release, stage, 100)
    assert result["staged_candidates"] == 8


def test_hash_and_license_scope_fail_closed(auto_source):
    source, release, _ = auto_source
    for field, value in (("sqlite_sha256", "0" * 64), ("license_scope_max_people", 1), ("license_scope_date", "20190101")):
        with pytest.raises(ValueError):
            inspect_source(source, {**release, field: value})


def test_auto_admission_quarantines_invalid_types_and_sources(auto_source, tmp_path):
    result = run(auto_source, tmp_path)
    assert result["counts_after"]["people"] == 2
    assert result["counts_after"]["aliases"] == 4
    assert result["quarantined"] == 2
    assert result["quarantine_reasons"] == {"dangling_bibliographic_source": 1, "unknown_or_conflicting_type": 1}
    with existing_connection(tmp_path / "TEST_ONLY_FORMAL.db") as c:
        assert c.execute("SELECT count(*) FROM automatic_evidence WHERE method='A'").fetchone()[0] == 4
        assert c.execute("SELECT count(DISTINCT person_id) FROM aliases WHERE alias_name='仅测试共用字'").fetchone()[0] == 2


def test_external_ids_keep_same_full_name_as_separate_people(auto_source, tmp_path):
    source, release, stage = auto_source
    with sqlite3.connect(source) as c:
        c.execute("UPDATE BIOG_MAIN SET c_name_chn='仅测试同名人物' WHERE c_personid IN (1,2)")
        c.execute("UPDATE ALTNAME_DATA SET c_alt_name_chn='仅测试另一个字' WHERE c_personid=2")
    release["sqlite_sha256"] = file_hash(source)
    stage.unlink()
    stage_records(source, release, stage)
    result = run(auto_source, tmp_path)
    assert result["counts_after"]["people"] == 2
    with existing_connection(tmp_path / "TEST_ONLY_FORMAL.db") as c:
        assert c.execute("SELECT count(*) FROM people WHERE canonical_name='仅测试同名人物'").fetchone()[0] == 2


def test_legacy_overlap_is_quarantined_instead_of_name_merge(auto_source, tmp_path):
    db = tmp_path / "TEST_ONLY_FORMAL.db"
    init_db(db)
    with connect_db(db) as c:
        c.execute("INSERT INTO people(person_id,canonical_name) VALUES('LEGACY-TEST','仅测试甲人')")
    result = run(auto_source, tmp_path)
    assert result["quarantine_reasons"]["possible_legacy_identity_overlap"] == 2
    assert result["counts_after"]["people"] == 2


def test_repeat_incremental_import_is_idempotent(auto_source, tmp_path):
    first = run(auto_source, tmp_path)
    second = run(auto_source, tmp_path)
    assert first["inserted"]["people"] == 2
    assert not any(second["inserted"].values())
    assert second["batches_committed_this_run"] == 0


def test_resume_after_first_batch_and_rollback_owns_only_new_rows(auto_source, tmp_path):
    first = run(auto_source, tmp_path, batch_size=1, max_batches=1)
    assert first["counts_after"]["people"] == 1
    final = run(auto_source, tmp_path, batch_size=1)
    assert final["counts_after"]["people"] == 2
    db = tmp_path / "TEST_ONLY_FORMAL.db"
    with existing_connection(db) as c:
        batches = [r[0] for r in c.execute("SELECT batch_id FROM auto_batches ORDER BY rowid")]
    with pytest.raises(sqlite3.IntegrityError):
        rollback_batch(db, batches[0], tmp_path / "rollback")
    assert database_counts(db)["people"] == 2
    rollback_batch(db, batches[1], tmp_path / "rollback")
    assert database_counts(db)["people"] == 1


def test_batch_failure_rolls_back_all_data_and_retains_fault_report(auto_source, tmp_path):
    db = tmp_path / "TEST_ONLY_FORMAL.db"
    migrate(db)
    with connect_db(db) as c:
        c.execute("CREATE TRIGGER TEST_ONLY_FAIL BEFORE INSERT ON evidences BEGIN SELECT RAISE(ABORT,'TEST ONLY EVIDENCE FAILURE'); END")
    with pytest.raises(sqlite3.IntegrityError):
        run(auto_source, tmp_path)
    assert database_counts(db) == dict.fromkeys(("people", "aliases", "sources", "evidences"), 0)
    assert list((tmp_path / "reports").glob("*-FAILED.json"))


def test_dry_run_leaves_formal_and_staging_unchanged(auto_source, tmp_path):
    db = tmp_path / "TEST_ONLY_FORMAL.db"
    init_db(db)
    before, stage_hash = file_hash(db), file_hash(auto_source[2])
    result = run(auto_source, tmp_path, dry_run=True)
    assert result["formal_database_writes"] == 0 and result["counts_after"]["people"] == 2
    assert file_hash(db) == before and file_hash(auto_source[2]) == stage_hash


@pytest.mark.parametrize("response", [None, {}, {"ok": False}, {"ok": True, "verdict": "supported", "text_match": False}])
def test_ai_failure_cannot_authorize_a_relation(response):
    assert not validate_auxiliary_review(response, {"c_personid": 1})


def test_changed_staging_record_is_quarantined_even_after_self_rehash(auto_source, tmp_path):
    with sqlite3.connect(auto_source[2]) as c:
        cid, text = c.execute("SELECT candidate_id,payload_json FROM candidates WHERE json_extract(payload_json,'$.table')='ALTNAME_DATA' LIMIT 1").fetchone()
        payload = json.loads(text)
        payload["alias"] = "TEST ONLY FABRICATED"
        text = canonical_json(payload)
        c.execute("UPDATE candidates SET payload_json=?,payload_sha256=? WHERE candidate_id=?", (text, digest(text), cid))
    result = run(auto_source, tmp_path)
    assert result["quarantine_reasons"]["source_snapshot_or_record_mismatch"] == 1


def test_normalization_is_retrieval_only_preserves_original_name():
    assert search_key("歐陽修") == search_key("欧阳修")
    assert normalize_original("歐陽修") == "歐陽修"


def normalize_original(name):
    from app.normalization import normalize_name
    return normalize_name(name)


def test_strict_original_declaration_requires_identity_marker_and_locatable_text():
    from app.classical_admission import locate_declaration
    raw = 'TEST ONLY\n仅测试甲人字厚卿仅测试甲地人也。'
    loc = locate_declaration(raw, '仅测试甲人', '厚卿')
    assert loc and raw[loc['start']:loc['end']] == loc['quote']
    assert locate_declaration('仅测试甲人遇到厚卿，仅有共现', '仅测试甲人', '厚卿') is None
    assert locate_declaration(raw, '仅测试乙人', '厚卿') is None


def test_classical_enrichment_real_offsets_and_idempotence(auto_source, tmp_path):
    from app.classical_admission import enrich_classical
    from app.verification import json_write
    run(auto_source, tmp_path)
    db = tmp_path / 'TEST_ONLY_FORMAL.db'
    with connect_db(db) as c:
        c.execute("UPDATE people SET dynasty='宋'")
    root = tmp_path / 'TEST_ONLY_TEXTS'
    root.mkdir()
    raw = 'TEST ONLY NOT HISTORY\n仅测试甲人字仅测试共用字仅测试甲地人也。\n仅测试乙人字仅测试共用字仅测试乙地人也。'
    (root/'KR2a0032_001.txt').write_text(raw,encoding='utf-8')
    json_write(root/'texts.json',{'repository':'kanripo/KR2a0032','commit':'TEST_ONLY_COMMIT','license_id':'CC-BY-SA-4.0','files':[
        {'path':'KR2a0032_001.txt','sha256':file_hash(root/'KR2a0032_001.txt'),'book_title':'TEST ONLY NOT A BOOK','volume':'TEST ONLY 卷1','chapter':'TEST ONLY','url':'https://example.invalid/TEST_ONLY_TEXT','edition':'TEST ONLY'}]})
    result=enrich_classical(auto_source[0],root,db,tmp_path/'text_reports')
    assert result['admitted']==2
    assert enrich_classical(auto_source[0],root,db,tmp_path/'text_reports')['admitted']==0
    from app.catalog import get_alias
    with existing_connection(db) as c:
        assert c.execute("SELECT count(*) FROM automatic_evidence WHERE method='A+'").fetchone()[0]==2
        row=c.execute("SELECT e.* FROM automatic_evidence a JOIN evidences e USING(evidence_id) WHERE a.method='A+' LIMIT 1").fetchone()
        assert row['original_text'] in raw
        assert get_alias(db,row['alias_id'])['has_classical'] is True


def test_structured_page_never_labels_json_as_classical_quote(auto_source, tmp_path):
    from fastapi.testclient import TestClient
    from app.main import create_app
    from app.catalog import get_person,search_people
    run(auto_source,tmp_path)
    db=tmp_path/'TEST_ONLY_FORMAL.db'
    with TestClient(create_app(db)) as client:
        with existing_connection(db) as c:
            alias=c.execute("SELECT alias_id FROM aliases LIMIT 1").fetchone()[0]
        page=client.get('/aliases/'+alias).text
        assert '结构化数据库' in page and 'data-raw-record' in page
        assert '原始结构化字段（非古籍原文）' not in page
        assert '<pre class="structured-record">' not in page
        assert '<blockquote class="original-text"' not in page
        assert 'CC BY-NC-SA 4.0' in page
    assert get_person(db,'P-CBDB-1')['identity_evidences']==[]


def test_manual_bridge_requires_explicit_call_and_failed_schema_stays_quarantined(tmp_path,monkeypatch):
    from app.quarantine_review import review_once,valid_response
    from types import SimpleNamespace
    import app.quarantine_review as bridge
    candidates=[{'candidate_id':'TEST-ONLY-C','reason':'TEST_ONLY','source_record':{'not_real_data':True}}]
    with pytest.raises(ValueError,match='明确要求'):
        review_once(candidates,tmp_path/'not_created')
    assert not (tmp_path/'not_created').exists()
    def fake_run(*args,**kwargs):
        return SimpleNamespace(returncode=1,stdout='{"ok":false,"code":"TEST_ONLY_TIMEOUT"}',stderr='TEST ONLY synthetic failure')
    monkeypatch.setattr(bridge.subprocess,'run',fake_run)
    receipt=review_once(candidates,tmp_path/'TEST_ONLY_BRIDGE',explicitly_authorized=True)
    assert receipt['status']=='failed_keep_isolated' and receipt['records_remain_quarantined']
    assert not valid_response({'ok':True,'analysis':{'summary':'TEST','findings':[],'alternatives':[],'verification':[]}},['TEST-ONLY-C'])


def test_incremental_staging_highwater_and_cached_resume(auto_source,tmp_path):
    source,release,_=auto_source
    stage=tmp_path/'TEST_ONLY_INCREMENTAL.sqlite3'
    first=stage_records(source,release,stage,2)
    assert first['staged_candidates']==4
    second=stage_records(source,release,stage,4)
    assert second['staged_candidates']==8
    third=stage_records(source,release,stage,4)
    assert third['cache_reused'] and third['source_alt_rows_consumed']==4


@pytest.mark.parametrize('response',[None,{}, {'ok':False}])
def test_empty_or_failed_ai_response_in_actual_admission_is_B(auto_source,tmp_path,response):
    source,release,stage=auto_source
    db=tmp_path/'TEST_ONLY_DECISION.db'
    migrate(db)
    with sqlite3.connect(stage) as s,existing_connection(db) as c:
        payload=json.loads(s.execute("SELECT payload_json FROM candidates WHERE json_extract(payload_json,'$.key.c_personid')=1 AND json_extract(payload_json,'$.table')='ALTNAME_DATA'").fetchone()[0])
        payload['ai_review']=response
        assert decide(payload,c)==('B','invalid_or_failed_ai_review')


def test_same_dynasty_name_and_courtesy_signature_is_quarantined(auto_source,tmp_path):
    source,release,stage=auto_source
    with sqlite3.connect(source) as c:
        c.execute("UPDATE BIOG_MAIN SET c_name_chn='仅测试潜在重复' WHERE c_personid IN (1,2)")
    release['sqlite_sha256']=file_hash(source)
    stage.unlink()
    stage_records(source,release,stage)
    result=run(auto_source,tmp_path)
    assert result['counts_after']['people']==0
    assert result['quarantine_reasons']['ambiguous_source_identity_signature']==4
