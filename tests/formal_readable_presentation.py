"""Explicit, read-only acceptance over authentic formal records.

Run this file explicitly; no init/migrate/ingest or TestClient lifespan is used.
"""
import hashlib
import json
import sqlite3
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient

from app.catalog import get_alias, get_person, search_people
from app.db import PROJECT_ROOT
from app.main import create_app

DB = PROJECT_ROOT / 'data' / 'guji.db'
WANG_IDS = {'P-D-470d41f21e8cab39f12341ed', 'P-D-46c39186c9a0fa0d8177b557'}


@pytest.fixture(scope='module', autouse=True)
def preserve_formal_db():
    before = hashlib.sha256(DB.read_bytes()).hexdigest()
    yield
    assert hashlib.sha256(DB.read_bytes()).hexdigest() == before


@pytest.fixture(scope='module')
def client():
    # Intentionally skip lifespan: create_app's lifespan calls init_db.
    return TestClient(create_app(DB))


def readonly():
    c = sqlite3.connect(DB.as_uri() + '?mode=ro', uri=True)
    c.row_factory = sqlite3.Row
    return c


def test_du_fu_real_years_alias_and_version_are_readable(client):
    person = next(p for p in search_people(DB, '杜甫') if p['birth_year'] == 712)
    detail = get_person(DB, person['person_id'])
    assert (detail['birth_year'], detail['death_year']) == (712, 770)
    assert '子美' in {a['alias_name'] for a in detail['aliases']}
    html = client.get('/people/' + quote(person['person_id'], safe='')).text
    assert '杜甫' in html and '712' in html and '770' in html and '子美' in html
    assert '2025年5月20日' in html
    for key in ('record_json', 'locator_json', 'c_surname_chn', 'c_by_range'):
        assert key not in html
    assert 'data-raw-record' in html


def test_li_bai_distinguishes_structured_database_and_multiple_names(client):
    person = get_person(DB, 'P-CBDB-32540')
    assert {'太白', '青蓮居士'} <= {a['alias_name'] for a in person['aliases']}
    for name in ('太白', '青蓮居士'):
        alias = next(a for a in person['aliases'] if a['alias_name'] == name)
        full = get_alias(DB, alias['alias_id'])
        structured = [e for e in full['evidences'] if e['presentation']['kind'] == 'structured_database']
        assert structured
        html = client.get('/aliases/' + alias['alias_id']).text
        assert '李白' in html and name in html and '结构化数据库' in html
        assert all(not e['presentation']['original_text'] for e in structured)
        assert 'c_alt_name_chn' not in html and 'c_alt_name_type_code' not in html
        evidence_id = structured[0]['evidence_id']
        raw_response = client.get('/records/evidence/' + quote(evidence_id, safe=''))
        assert raw_response.status_code == 200
        with readonly() as c:
            original = dict(c.execute('SELECT * FROM evidences WHERE evidence_id=?', (evidence_id,)).fetchone())
        assert all(raw_response.json().get(key) == value for key, value in original.items())


def test_same_name_people_keep_both_stable_ids_and_eras(client):
    rows = search_people(DB, '王珪')
    assert {p['person_id'] for p in rows} == WANG_IDS
    assert {p['dynasty'] for p in rows} == {'唐', '宋'}
    html = client.get('/search', params={'q': '王珪'}).text
    for pid in WANG_IDS:
        assert pid in html
        profile = client.get('/people/' + pid)
        assert profile.status_code == 200
    assert '唐' in html and '宋' in html


def test_real_quote_and_auxiliary_author_page_are_distinct(client):
    tao = next(p for p in search_people(DB, '陶渊明') if p['canonical_name'] in {'陶潛', '陶潜', '陶淵明', '陶渊明'})
    with readonly() as c:
        supplement = c.execute("SELECT a.alias_id FROM aliases a JOIN relation_quality q USING(alias_id) WHERE a.person_id=? AND q.grade='C' LIMIT 1", (tao['person_id'],)).fetchone()
        candidates = c.execute("SELECT e.alias_id FROM evidences e WHERE e.verification_status='verified' AND substr(ltrim(e.original_text),1,1) NOT IN ('{','[') ORDER BY e.evidence_id LIMIT 100").fetchall()
    assert supplement, 'Formal Tao author-page supplement is required'
    aux = get_alias(DB, supplement['alias_id'])
    assert any(e['presentation']['kind'] == 'auxiliary' for e in aux['evidences'])
    html = client.get('/aliases/' + supplement['alias_id']).text
    assert '辅助资料' in html and ('不作为古籍原文' in html or '不是古籍原文' in html)
    classical = None
    for row in candidates:
        alias = get_alias(DB, row['alias_id'])
        if alias and any(e['presentation']['kind'] == 'classical_text' and e['presentation']['original_text'] for e in alias['evidences']):
            classical = alias
            break
    assert classical
    response = client.get('/aliases/' + classical['alias_id'])
    assert response.status_code == 200 and '古籍原文' in response.text
    assert 'data-copy-target' in response.text


def test_formal_raw_record_has_every_original_column_and_source_links(client):
    person = get_person(DB, 'P-CBDB-32540')
    field = person['field_sources']['birth_year'][0]
    response = client.get('/records/field/' + field['fact_id'])
    assert response.status_code == 200
    with readonly() as c:
        original = dict(c.execute('SELECT * FROM field_facts WHERE fact_id=?', (field['fact_id'],)).fetchone())
    assert all(response.json().get(key) == value for key, value in original.items())
    assert json.loads(response.json()['record_json'])
    source = client.get('/sources/' + quote(field['source_id'], safe=''), params={'person_id': person['person_id']})
    assert source.status_code == 200 and '李白' in source.text
    assert '/people/' in source.text and '/records/source/' in source.text


def test_formal_evidence_and_source_raw_data_are_lossless(client):
    with readonly() as c:
        candidates = c.execute("SELECT e.*, a.alias_id, p.record_json AS full_record, p.locator_json AS full_locator FROM evidences e JOIN aliases a USING(alias_id) JOIN automatic_evidence p USING(evidence_id) WHERE p.method='A' AND e.verification_status='verified' ORDER BY e.evidence_id").fetchall()
    selected = None
    for row in candidates:
        alias = get_alias(DB, row['alias_id'])
        if alias and any(e['evidence_id'] == row['evidence_id'] for e in alias['evidences']):
            selected = row
            break
    assert selected
    response = client.get('/records/evidence/' + quote(selected['evidence_id'], safe=''))
    assert response.status_code == 200
    payload = response.json()
    with readonly() as c:
        evidence = dict(c.execute('SELECT * FROM evidences WHERE evidence_id=?', (selected['evidence_id'],)).fetchone())
        source = dict(c.execute('SELECT * FROM sources WHERE source_id=?', (selected['source_id'],)).fetchone())
    assert all(payload.get(key) == value for key, value in evidence.items())
    for key, original in [('source_record', selected['full_record']), ('source_record_locator', selected['full_locator'])]:
        assert key in payload
        decoded = json.loads(payload[key]) if isinstance(payload[key], str) else payload[key]
        assert decoded == json.loads(original)
    response = client.get('/records/source/' + quote(selected['source_id'], safe=''))
    assert response.status_code == 200
    assert all(response.json().get(key) == value for key, value in source.items())


def test_formal_missing_fields_have_an_explicit_reading_state(client):
    with readonly() as c:
        row = c.execute("SELECT person_id FROM people WHERE verification_status='verified' AND birth_year IS NULL AND death_year IS NULL LIMIT 1").fetchone()
    assert row
    response = client.get('/people/' + quote(row['person_id'], safe=''))
    assert response.status_code == 200
    assert '待考' in response.text or '待补' in response.text
    assert '<dd>None</dd>' not in response.text and '<dd>null</dd>' not in response.text
