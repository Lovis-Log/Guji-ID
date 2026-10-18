"""Synthetic policy checks; never imports fabricated records into the formal DB."""
import pytest
from app.db import init_db,connect_db
from app.field_quality import migrate,insert,fact,put_alias,build_terms
from app.catalog import search_people,get_person,get_alias


@pytest.fixture
def quality_db(tmp_path):
    db=tmp_path/'TEST_ONLY_QUALITY.db';init_db(db);migrate(db)
    with connect_db(db) as c:
        insert(c,'quality_batches',dict(batch_id='TEST-B',imported_at='TEST',manifest_sha256='TEST',backup_json='{}',status='committed',after_fingerprint=None))
        insert(c,'people',dict(person_id='TEST-P',canonical_name='僅測試人物',verification_status='verified'))
        insert(c,'people',dict(person_id='TEST-P2',canonical_name='僅測試人物',verification_status='verified'))
        insert(c,'sources',dict(source_id='TEST-S',book_title='TEST_ONLY_NOT_A_HISTORICAL_SOURCE',platform='TEST_ONLY',original_url='https://example.invalid/test'))
    return db


def test_structured_and_supplement_do_not_require_ancient_quotes(quality_db):
    with connect_db(quality_db) as c:
        aid=put_alias(c,'TEST-B','TEST-P','測試別稱','art','TEST-S','TEST-V',{'key':{'id':'TEST-P'}},'C')
        fact(c,'TEST-B','TEST-P','dynasty','測試朝','B','TEST-S','TEST-V',{'field':'test'}, {'table':'TEST'})
        build_terms(c)
    p=get_person(quality_db,'TEST-P')
    assert p['dynasty']=='測試朝' and p['birth_year'] is None
    assert p['field_sources']['dynasty'][0]['grade']=='B'
    a=get_alias(quality_db,aid)
    assert a['verification_label']=='资料补充' and not a['has_classical']
    assert len(a['evidences'])==1 and a['evidences'][0]['evidence_kind']=='structured_record'
    assert search_people(quality_db,'测试别称')[0]['person_id']=='TEST-P'


def test_stronger_fields_preserved_and_conflicts_quarantined(quality_db):
    with connect_db(quality_db) as c:
        fact(c,'TEST-B','TEST-P','native_place','僅測試甲地','A','TEST-S','TEST-V',{}, {})
        fact(c,'TEST-B','TEST-P','native_place','僅測試乙地','B','TEST-S','TEST-V',{}, {})
        assert c.execute("SELECT native_place FROM people WHERE person_id='TEST-P'").fetchone()[0]=='僅測試甲地'
        assert c.execute('SELECT count(*) FROM quality_quarantine').fetchone()[0]==1
        assert c.execute('SELECT grade FROM field_facts WHERE selected=1').fetchone()[0]=='A'
        with pytest.raises(Exception):c.execute("UPDATE field_facts SET grade='D' WHERE selected=1")
    displayed=get_person(quality_db,'TEST-P')['field_sources']['native_place']
    assert [f['value'] for f in displayed]==['僅測試甲地']


def test_homonyms_variants_and_exact_priority(quality_db):
    with connect_db(quality_db) as c:
        insert(c,'people',dict(person_id='TEST-P3',canonical_name='前綴僅測試人物',verification_status='verified'))
        build_terms(c)
    r=search_people(quality_db,'仅测试人物')
    assert {p['person_id'] for p in r[:2]}=={'TEST-P','TEST-P2'}
    assert r[-1]['person_id']=='TEST-P3' and r[-1]['match_mode']=='模糊匹配'
    assert search_people(quality_db,"%' OR 1=1 --")==[]


def test_invalid_supplement_never_exposes_alias(quality_db):
    with connect_db(quality_db) as c:
        aid=put_alias(c,'TEST-B','TEST-P','存疑?稱呼','art','TEST-S','TEST-V',{},'B')
        assert aid is None
        build_terms(c)
    assert search_people(quality_db,'存疑')==[]
