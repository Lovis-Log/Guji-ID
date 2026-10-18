"""Read-only tests of actual production data. Run explicitly after enrichment."""
import json
import pytest
from app.db import PROJECT_ROOT,connect_db
from app.catalog import search_people,get_person
from app.automatic_ingestion import search_key

DB=PROJECT_ROOT/'data/guji.db'
CORE=json.loads((PROJECT_ROOT/'data/core_people_stage65.json').read_text(encoding='utf-8'))
CORE_IDS=json.loads((PROJECT_ROOT/'data/core_people_stage65_ids.json').read_text(encoding='utf-8'))


@pytest.mark.parametrize('name,era',CORE)
def test_core_source_backed_search(name,era):
    rows=search_people(DB,name)
    assert any(search_key(era) in search_key(p['dynasty'] or '') for p in rows), name
    with connect_db(DB) as c:
        expected=c.execute("SELECT person_id FROM external_people WHERE provider='CBDB' AND external_id=? UNION SELECT person_id FROM quality_identity WHERE provider='CBDB' AND external_id=?",(str(CORE_IDS[name]),CORE_IDS[name])).fetchone()
    assert expected and expected[0] in {p['person_id'] for p in rows},(name,CORE_IDS[name])
    for p in rows:
        if search_key(era) in search_key(p['dynasty'] or ''):
            detail=get_person(DB,p['person_id'])
            assert detail['field_sources']['canonical_name'] and detail['field_sources']['dynasty']


def test_li_bai_real_fields_and_aliases():
    p=get_person(DB,'P-CBDB-32540')
    assert p['canonical_name']=='李白' and p['dynasty']=='唐'
    assert (p['birth_year'],p['death_year'])==(701,762)
    assert {'太白','青蓮居士'} <= {a['alias_name'] for a in p['aliases']}
    for query,kind in [('太白','courtesy'),('青莲居士','art')]:
        r=next(x for x in search_people(DB,query) if x['person_id']==p['person_id'])
        assert any(a['alias_type']==kind for a in r['matched_aliases'])
    assert all(p['field_sources'][f] for f in ('dynasty','birth_year','death_year'))


def test_original_ids_and_relations_preserved():
    baseline=json.loads((PROJECT_ROOT/'outputs/stage65-release/baseline.json').read_text(encoding='utf-8'))
    with connect_db(DB) as c:
        for old in baseline['people']:
            p=c.execute('SELECT * FROM people WHERE person_id=?',(old['person_id'],)).fetchone()
            assert p and p['canonical_name']==old['canonical_name']
        for table in ('aliases','sources','evidences'):
            key={'aliases':'alias_id','sources':'source_id','evidences':'evidence_id'}[table]
            for old in baseline[table]:assert dict(c.execute('SELECT * FROM '+table+' WHERE '+key+'=?',(old[key],)).fetchone())==old
        assert c.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
        assert not c.execute('PRAGMA foreign_key_check').fetchall()


def test_two_wang_and_shared_alias_remain_distinct():
    rows=search_people(DB,'王珪')
    assert len(rows)==2 and {p['dynasty'] for p in rows}=={'唐','宋'}
    assert len({p['person_id'] for p in rows})==2
    assert {'叔玠','禹玉'} <= {a['alias_name'] for p in rows for a in get_person(DB,p['person_id'])['aliases']}
    assert len(search_people(DB,'文忠'))>=4


def test_substantial_original_cohort_improvement_and_grade_traceability():
    baseline=json.loads((PROJECT_ROOT/'outputs/stage65-release/baseline.json').read_text(encoding='utf-8'))
    ids=[p['person_id'] for p in baseline['people']]
    with connect_db(DB) as c:
        rows=[dict(c.execute('SELECT * FROM people WHERE person_id=?',(pid,)).fetchone()) for pid in ids]
        assert sum(p['birth_year'] is not None for p in rows)>120
        assert sum(p['native_place'] is not None for p in rows)>300
        assert not c.execute("SELECT 1 FROM field_facts WHERE grade='D' AND selected=1").fetchall()
        assert not c.execute('SELECT 1 FROM field_facts f LEFT JOIN sources s USING(source_id) WHERE s.source_id IS NULL').fetchall()
