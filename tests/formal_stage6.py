"""Explicit read-only checks against the actually expanded production database."""
import json
import sqlite3
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from app.automatic_ingestion import payload_matches_source,search_key,conflicting_source_identities
from app.batch_import import database_counts,existing_connection
from app.catalog import get_alias,search_people
from app.collector import digest,identifier
from app.db import PROJECT_ROOT
from app.main import create_app
from app.source_acquisition import file_hash
from app.verification import json_read

DB=PROJECT_ROOT/'data/guji.db'
ROOT=PROJECT_ROOT/'outputs/stage6-release'


@pytest.fixture(scope='module',autouse=True)
def read_only():
    before=file_hash(DB)
    yield
    assert file_hash(DB)==before


def test_counts_integrity_source_scope_and_external_identity():
    counts=database_counts(DB)
    assert 300 <= counts['people'] <= 500
    assert counts['aliases'] >= 600
    with existing_connection(DB) as c:
        assert c.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
        assert c.execute('PRAGMA foreign_key_check').fetchall()==[]
        # Stage 6 admitted every person through a CBDB external identity.
        # Later stages also admit curated people from classical sources
        # without an external_people row, so assert registry consistency
        # instead of a hard-coded people-minus-seeds offset.
        external_count=c.execute('SELECT count(*) FROM external_people').fetchone()[0]
        assert external_count>=390
        assert c.execute("SELECT count(DISTINCT provider || ':' || external_id) FROM external_people").fetchone()[0]==external_count
        assert c.execute('SELECT count(DISTINCT person_id) FROM external_people').fetchone()[0]==external_count
        for row in c.execute('SELECT * FROM data_releases'):
            meta=json.loads(row['metadata_json'])
            assert meta['version']<=meta['license_scope_date']
            assert meta['people_count']<=meta['license_scope_max_people']


def test_entire_historical_four_tables_remain_identical():
    from app.importer import COLUMNS
    baseline=json_read(ROOT/'baseline.json')
    with existing_connection(DB) as c:
        for table,rows in baseline['original_rows'].items():
            for row in rows:
                current=dict(c.execute('SELECT * FROM '+table+' WHERE '+COLUMNS[table][0]+'=?',(row[COLUMNS[table][0]],)).fetchone())
                # Later evidence-backed stages may fill fields that were null in
                # the Stage 6 snapshot, but must not rewrite an existing value.
                assert current is not None
                assert all(current[key] == value for key,value in row.items() if value is not None)


def test_all_structured_evidence_replays_to_exact_source_record():
    src_path=PROJECT_ROOT/'data/external/cbdb-20250520/cbdb.sqlite3'
    release=json_read(src_path.parent/'release.json')
    assert file_hash(src_path)==release['sqlite_sha256']
    with existing_connection(src_path) as src,existing_connection(DB) as c:
        checked=0
        for row in c.execute("SELECT q.*,e.original_text,e.notes,e.alias_id FROM automatic_evidence q JOIN evidences e USING(evidence_id) WHERE q.method='A'"):
            payload=json.loads(row['record_json'])
            assert digest(row['record_json'])==row['record_sha256']
            assert payload_matches_source(src,payload)
            assert json.loads(row['notes'])['evidence_kind']=='structured_record'
            assert json.loads(row['original_text'])['key']==payload['key']
            checked+=1
        assert checked>=600


def test_all_automatic_classical_quotes_replay_to_exact_file_offsets():
    with existing_connection(DB) as c:
        rows=c.execute("SELECT q.locator_json,e.original_text FROM automatic_evidence q JOIN evidences e USING(evidence_id) WHERE method='A+'").fetchall()
        assert rows
        for row in rows:
            loc=json.loads(row['locator_json'])
            path=PROJECT_ROOT/'data/external/kanripo-stage6'/loc['file']
            assert file_hash(path)==loc['sha256']
            text=path.read_bytes().decode('utf-8')
            assert text[loc['start']:loc['end']]==row['original_text']
            assert text.count('\n',0,loc['start'])+1==loc['line_start']


def test_quarantine_is_not_admitted_and_old_pending_ids_stay_hidden():
    with sqlite3.connect(PROJECT_ROOT/'data/ingestion/stage6.sqlite3') as stage,existing_connection(DB) as c:
        quarantined=stage.execute("SELECT payload_json FROM candidates WHERE decision='B'").fetchall()
        assert quarantined
        for (text,) in quarantined:
            p=json.loads(text)
            eid=identifier('E-CBDB','20250520',p['table'],p['key'])
            assert not c.execute('SELECT 1 FROM evidences WHERE evidence_id=?',(eid,)).fetchone()
    excluded=json_read(PROJECT_ROOT/'outputs/stage5-release/human_approval/user_authorization.json')['excluded_alias_ids']
    assert all(get_alias(DB,aid) is None for aid in excluded)


def test_same_full_name_and_shared_terms_keep_distinct_people():
    for query,minimum in [('王珪',2),('文忠',4),('鄭驤',2)]:
        people=search_people(DB,query)
        assert len(people)>=minimum
        assert len({p['person_id'] for p in people})==len(people)
    assert {'苏轼','欧阳修'} <= {p['canonical_name'] for p in search_people(DB,'文忠')}
    assert search_people(DB,'張諤')==[]


def test_simplified_lookup_preserves_original_alias_and_external_id():
    with existing_connection(DB) as c:
        changed=next(dict(r) for r in c.execute("SELECT * FROM aliases WHERE person_id LIKE 'P-CBDB-%'") if search_key(r['alias_name'])!=r['alias_name'])
    people=search_people(DB,search_key(changed['alias_name']))
    assert changed['person_id'] in {p['person_id'] for p in people}
    assert get_alias(DB,changed['alias_id'])['alias_name']==changed['alias_name']


def test_http_pages_distinguish_evidence_types_and_show_pipeline_summary():
    with existing_connection(DB) as c:
        structured=c.execute("SELECT e.alias_id FROM automatic_evidence q JOIN evidences e USING(evidence_id) WHERE q.method='A' AND NOT EXISTS(SELECT 1 FROM automatic_evidence z JOIN evidences e2 ON e2.evidence_id=z.evidence_id WHERE z.method='A+' AND e2.alias_id=e.alias_id) LIMIT 1").fetchone()[0]
        classical=c.execute("SELECT e.alias_id FROM automatic_evidence q JOIN evidences e USING(evidence_id) WHERE q.method='A+' LIMIT 1").fetchone()[0]
    with TestClient(create_app(DB)) as client:
        page=client.get('/aliases/'+structured).text
        assert '结构化来源核准' in page and '查看原始记录' in page
        assert '<blockquote class="original-text"' not in page
        page=client.get('/aliases/'+classical).text
        assert '原文证据核实' in page and '学术出处与定位' in page and '<blockquote class="original-text"' in page
        home=client.get('/').text
        assert '自动数据流水线' in home and '该批新增' in home
        assert client.get('/health').json()['stage']==7
    from app.auto_catalog import pipeline_counts
    with existing_connection(DB) as c:
        latest=c.execute("SELECT e.batch_id FROM auto_events e JOIN auto_batches b USING(batch_id) WHERE b.status='committed' ORDER BY e.event_id DESC LIMIT 1").fetchone()[0]
    assert pipeline_counts(DB)['latest']['batch_id']==latest


def test_no_admitted_external_person_has_unresolved_overlapping_source_signature():
    with existing_connection(PROJECT_ROOT/'data/external/cbdb-20250520/cbdb.sqlite3') as src,existing_connection(DB) as c:
        for row in c.execute('SELECT record_json FROM external_people'):
            assert conflicting_source_identities(src,json.loads(row['record_json']))==[]
