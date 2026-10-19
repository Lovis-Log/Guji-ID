"""Actual licensed transcription and public reading paths; formal DB read only."""
import hashlib
import json
from pathlib import Path
from urllib.parse import quote as url_quote

from fastapi.testclient import TestClient

from app.catalog import get_alias,get_person
from app.db import PROJECT_ROOT,connect_db
from app.main import create_app
from app.source_catalog import get_source,get_raw_record
from scripts.acquire_jts190b import ROOT,validated_page,candidate_rows

DB=PROJECT_ROOT/'data/guji.db'
OLDID='2636533'


def test_fixed_revision_quotes_are_exact_and_classified_per_person():
    manifest=json.loads((ROOT/'validated_quotes.json').read_text(encoding='utf-8'))
    cached_files=[ROOT/manifest[key] for key in ('raw_path','text_path')]
    cache_before=[(path.stat().st_mtime_ns,hashlib.sha256(path.read_bytes()).hexdigest()) for path in cached_files]
    page,_=validated_page()  # replay cached response; never fetch the network
    assert cache_before==[(path.stat().st_mtime_ns,hashlib.sha256(path.read_bytes()).hexdigest()) for path in cached_files]
    cases=candidate_rows(page)
    assert page['revision']==int(OLDID) and len(cases)==2
    assert [(r['name'],r['courtesy']) for r in cases]==[('李白','太白'),('杜甫','子美')]
    with connect_db(DB) as c:
        for case in cases:
            raw=c.execute("SELECT * FROM evidences WHERE alias_id=? AND original_text=?",(case['alias_id'],case['quote'])).fetchone()
            assert raw and raw['verification_status']=='verified' and raw['usage_relation']=='supports'
            meta=json.loads(raw['notes'])
            assert meta['evidence_kind']=='classical_text' and meta['quality_grade']=='A'
            assert (meta['locator']['start'],meta['locator']['end'])==(case['start'],case['end'])
            assert meta['locator']['raw_sha256']==page['raw_sha256']
            source=c.execute('SELECT * FROM sources WHERE source_id=?',(raw['source_id'],)).fetchone()
            assert source and source['book_title']=='舊唐書' and source['volume']=='卷190下'
            assert source['original_url']==f"https://zh.wikisource.org/w/index.php?oldid={OLDID}#"+url_quote(case['section_anchor'],safe='')
            alias=get_alias(DB,case['alias_id'])
            assert alias['has_classical'] and alias['verification_label']=='原文证据核实'
            assert any(e['evidence_id']==raw['evidence_id'] and e['presentation']['kind']=='classical_text' for e in alias['evidences'])
            original=get_raw_record(DB,'evidence',raw['evidence_id'])
            assert all(original[col]==raw[col] for col in raw.keys())


def test_source_pages_show_ancient_text_without_conflating_cbdb():
    cases=candidate_rows(validated_page()[0])
    with TestClient(create_app(DB)) as client:
        for case in cases:
            with connect_db(DB) as c:
                row=c.execute('''SELECT e.source_id FROM evidences e WHERE e.alias_id=? AND e.original_text=?''',
                              (case['alias_id'],case['quote'])).fetchone()
                assert row
                sid=row[0]
            ancient=get_source(DB,sid,person_id=case['person_id'])
            assert ancient['presentation']['kind']=='classical_text'
            assert [q['text'] for q in ancient['classical_quotes']]==[case['quote']]
            ancient_html=client.get('/sources/'+sid,params={'person_id':case['person_id']})
            assert ancient_html.status_code==200 and case['quote'] in ancient_html.text
            assert '此来源中的古籍原文' in ancient_html.text
            assert f'oldid={OLDID}' in ancient_html.text
            cbdb=get_source(DB,'S-CBDB65-20250520',person_id=case['person_id'])
            assert cbdb['presentation']['kind']=='structured_database'
            assert not cbdb['classical_quotes']
            assert any(q['text']==case['quote'] and q['source_id']==sid for q in cbdb['related_classical_quotes'])
            cbdb_html=client.get('/sources/S-CBDB65-20250520',params={'person_id':case['person_id']})
            assert cbdb_html.status_code==200 and case['quote'] in cbdb_html.text
            assert '其他独立来源中的古籍原文' in cbdb_html.text
            assert '当前数据库记录提供结构化资料' in cbdb_html.text


def test_new_quotations_do_not_change_names_years_or_other_evidence():
    receipt=json.loads((PROJECT_ROOT/'outputs/classical-source/formal-import/import_result.json').read_text(encoding='utf-8'))
    before=Path(receipt['backup']['path'])
    assert hashlib.sha256(before.read_bytes()).hexdigest()==receipt['backup']['sha256']
    assert receipt['counts_after']['people']==receipt['counts_before']['people']==454
    assert receipt['counts_after']['aliases']==receipt['counts_before']['aliases']==1195
    with connect_db(before) as old,connect_db(DB) as current:
        for table,key in (('people','person_id'),('aliases','alias_id')):
            for row in old.execute('SELECT * FROM '+table):
                found=current.execute(f'SELECT * FROM {table} WHERE {key}=?',(row[key],)).fetchone()
                assert found is not None and dict(found)==dict(row), (table,row[key])
        for table,key in (('sources','source_id'),('evidences','evidence_id')):
            for row in old.execute('SELECT * FROM '+table):
                assert dict(current.execute(f'SELECT * FROM {table} WHERE {key}=?',(row[key],)).fetchone())==dict(row)
        assert current.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
        assert not current.execute('PRAGMA foreign_key_check').fetchall()
    for name,pid in [('李白','P-CBDB-32540'),('杜甫','P-CBDB-3915')]:
        person=get_person(DB,pid)
        assert person['canonical_name']==name
        assert any(name in row['original_text'] for row in person['identity_evidences'])
