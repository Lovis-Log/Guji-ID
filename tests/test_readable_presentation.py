"""TEST_ONLY presentation fixtures. Never write to the formal database."""
import json
import tempfile
from pathlib import Path
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient

from app.db import PROJECT_ROOT, connect_db, init_db
from app.field_quality import build_terms, fact, insert, migrate, put_alias
from app.main import create_app

SENTINEL = 'TEST_ONLY_RAW_PAYLOAD_NEVER_EMBED_DEFAULT'


@pytest.fixture
def readable_db():
    root = PROJECT_ROOT / 'work' / 'TEST_ONLY_readable_presentation'
    root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='case-', dir=root) as directory:
        db = Path(directory) / 'TEST_ONLY.db'
        init_db(db)
        migrate(db)
        with connect_db(db) as c:
            insert(c, 'quality_batches', dict(batch_id='TEST-B', imported_at='TEST_ONLY', manifest_sha256='TEST_ONLY', backup_json='{}', status='committed'))
            for pid, status in [('TEST-P', 'verified'), ('TEST-HIDDEN-P', 'pending')]:
                insert(c, 'people', dict(person_id=pid, canonical_name='仅测试可读人物', verification_status=status))
            for sid in ['TEST/SOURCE', 'TEST-CLASSICAL', 'TEST-ORPHAN', 'TEST-D-SOURCE']:
                insert(c, 'sources', dict(source_id=sid, book_title='TEST_ONLY <img src=x onerror=alert(1)>', volume='TEST_ONLY卷一', chapter='TEST_ONLY篇一', platform='TEST_ONLY', original_url='https://example.invalid/TEST_ONLY/' + quote(sid, safe=''), edition='TEST_ONLY 20250520'))
            raw = {'table': 'ALTNAME_DATA', 'key': {'c_personid': 999999, 'c_alt_name_type_code': 987654}, 'row': {'c_alt_name_chn': '仅测试字', 'unknown_code': 987654, 'unmapped_payload': SENTINEL}}
            aid = put_alias(c, 'TEST-B', 'TEST-P', '仅测试字', 'courtesy', 'TEST/SOURCE', 'TEST_ONLY-20250520', raw, 'B')
            fact(c, 'TEST-B', 'TEST-P', 'birth_year', 712, 'B', 'TEST/SOURCE', 'TEST_ONLY-20250520', {'table': 'BIOG_MAIN', 'row': {'c_birthyear': 712, 'payload': SENTINEL}}, {'column': 'c_birthyear', 'c_personid': 999999})
            fact(c, 'TEST-B', 'TEST-P', 'native_place', '仅测试本贯', 'B', 'TEST/SOURCE', 'TEST_ONLY-20250520', {'row': {'payload': SENTINEL}}, {'table': 'BIOG_ADDR_DATA'}, '来源所记籍贯（基本地址或本贯），非出生地')
            fact(c, 'TEST-B', 'TEST-P', 'native_place', '仅测试冲突地', 'C', 'TEST/SOURCE', 'TEST_ONLY', {'payload': SENTINEL}, {})
            insert(c, 'field_facts', dict(fact_id='TEST-D-FACT', person_id='TEST-P', field='death_year', value_json='770', grade='D', source_id='TEST-D-SOURCE', source_version='TEST_ONLY', record_json=json.dumps({'hidden': SENTINEL}), locator_json='{}', selected=0, batch_id='TEST-B'))
            fact(c, 'TEST-B', 'TEST-HIDDEN-P', 'birth_year', 712, 'B', 'TEST-ORPHAN', 'TEST_ONLY', {'hidden': SENTINEL}, {})
            insert(c, 'evidences', dict(evidence_id='TEST-CLASSICAL-E', alias_id=aid, source_id='TEST-CLASSICAL', original_text='TEST_ONLY：仅测试字。<script>alert("TEST_ONLY")</script>', usage_relation='supports', evidence_nature='direct', verification_status='verified', notes=json.dumps({'evidence_kind': 'classical_text', 'relation_type': '来源记载', 'raw_private': SENTINEL}, ensure_ascii=False)))
            insert(c, 'evidences', dict(evidence_id='TEST-HIDDEN-E', alias_id=aid, source_id='TEST-ORPHAN', original_text='TEST_ONLY hidden evidence', usage_relation='supports', evidence_nature='direct', verification_status='pending', notes=SENTINEL))
            build_terms(c)
        yield db, aid


@pytest.fixture
def readable_client(readable_db):
    with TestClient(create_app(readable_db[0])) as client:
        yield client


def test_default_views_are_readable_without_embedded_raw_payload(readable_db, readable_client):
    _, aid = readable_db
    for path in ['/people/TEST-P', '/aliases/' + aid, '/sources/TEST%2FSOURCE', '/sources/TEST-CLASSICAL']:
        response = readable_client.get(path)
        assert response.status_code == 200, path
        assert SENTINEL not in response.text, path
        assert '<pre class="structured-record">{' not in response.text
        assert '<script>alert("TEST_ONLY")</script>' not in response.text
        assert '<img src=x onerror=alert(1)>' not in response.text
    person = readable_client.get('/people/TEST-P').text
    assert '712' in person and '仅测试本贯' in person
    assert '仅测试冲突地' not in person and 'TEST-D-FACT' not in person
    assert '待补' in person or '待考' in person
    assert 'data-raw-record' in person
    alias = readable_client.get('/aliases/' + aid).text
    assert '仅测试字' in alias and 'TEST_ONLY：仅测试字。' in alias
    assert '&lt;script&gt;' in alias


def test_raw_endpoints_preserve_original_base_table_fields(readable_db, readable_client):
    db, aid = readable_db
    with connect_db(db) as c:
        field = dict(c.execute("SELECT * FROM field_facts WHERE person_id='TEST-P' AND field='birth_year'").fetchone())
        evidence = dict(c.execute("SELECT * FROM evidences WHERE alias_id=? AND evidence_id NOT IN ('TEST-CLASSICAL-E','TEST-HIDDEN-E')", (aid,)).fetchone())
        source = dict(c.execute("SELECT * FROM sources WHERE source_id='TEST/SOURCE'").fetchone())
    for kind, original, pk in [('field', field, 'fact_id'), ('evidence', evidence, 'evidence_id'), ('source', source, 'source_id')]:
        response = readable_client.get('/records/' + kind + '/' + quote(original[pk], safe=''))
        assert response.status_code == 200, (kind, response.text)
        assert response.headers['content-type'].startswith('application/json')
        payload = response.json()
        for key, value in original.items():
            assert key in payload and payload[key] == value, (kind, key)
    assert json.loads(field['record_json'])['row']['payload'] == SENTINEL


def test_private_and_quarantined_records_are_not_addressable(readable_db, readable_client):
    with connect_db(readable_db[0]) as c:
        conflict = c.execute("SELECT fact_id FROM field_facts WHERE person_id='TEST-P' AND selected=0 AND grade='C'").fetchone()[0]
        hidden = c.execute("SELECT fact_id FROM field_facts WHERE person_id='TEST-HIDDEN-P'").fetchone()[0]
    for path in ['/records/field/TEST-D-FACT', '/records/field/' + conflict, '/records/field/' + hidden, '/records/evidence/TEST-HIDDEN-E', '/records/source/TEST-ORPHAN', '/records/source/TEST-D-SOURCE', '/sources/TEST-ORPHAN', '/sources/TEST-D-SOURCE', '/records/people/TEST-P', '/records/field/not-found', '/records/source/%27%20OR%201=1--']:
        response = readable_client.get(path)
        assert response.status_code == 404, (path, response.status_code)
        assert SENTINEL not in response.text


def test_presentation_is_nonmutating_and_never_guesses_unknown_codes():
    from copy import deepcopy
    from app.presentation import evidence_view, field_view, person_view
    raw = {'table': 'BIOG_MAIN', 'row': {'c_name_chn': 'TEST_ONLY人物', 'c_dy': 987654,
           'c_ethnicity': 987655, 'c_index_addr_id': 987656, 'c_birthyear': 712,
           'c_by_range': 987657, 'c_deathyear': 0}}
    record = {'evidence_kind': 'structured_record', 'quality_grade': 'B', 'book_title': 'CBDB TEST_ONLY',
              'original_text': json.dumps(raw, ensure_ascii=False), 'source_version': '20250520'}
    saved = deepcopy(record)
    presented = evidence_view(record)
    assert record == saved
    assert all(presented[key] == value for key, value in saved.items())
    vm = presented['presentation']
    assert vm['kind'] == 'structured_database' and vm['original_text'] == ''
    shown = json.dumps(vm, ensure_ascii=False)
    assert 'TEST_ONLY人物' in shown and '2025年5月20日' in shown
    assert all(str(code) not in shown for code in (987654, 987655, 987656, 987657))
    assert not any(item['label'] in {'生年', '卒年', '籍贯', '朝代'} for item in vm['facts'])
    assert person_view({'canonical_name': 'TEST_ONLY', 'birth_year': None, 'death_year': 0})['presentation']['lifespan'] == '生卒待考'
    assert not field_view({'field': 'birth_year', 'value': 0})['presentation']['visible']
    assert not field_view({'field': 'unrecognized_field', 'value': 'TEST_ONLY'})['presentation']['visible']


def test_auxiliary_author_metadata_cannot_turn_into_a_classical_quote():
    from app.presentation import evidence_view
    source = {'quality_grade': 'C', 'evidence_kind': 'structured_record',
              'book_title': 'TEST_ONLY作者资料页', 'original_text': json.dumps({'table': 'author_page', 'text': 'TEST_ONLY资料记载'})}
    vm = evidence_view(source)['presentation']
    assert vm['kind'] == 'auxiliary' and vm['original_text'] == ''
    assert '辅助' in vm['notice'] and 'CBDB' in vm['notice']


@pytest.mark.parametrize('relation', ['supports', 'refutes', 'mentions', 'uncertain'])
def test_database_relation_summary_does_not_promote_non_support(relation):
    from app.presentation import evidence_view
    evidence={'usage_relation':relation,'evidence_kind':'structured_record','quality_grade':'B',
              'book_title':'CBDB TEST_ONLY','original_text':json.dumps({'table':'ALTNAME_DATA','row':{'c_alt_name_chn':'TEST_ONLY字','c_alt_name_type_code':4}})}
    vm=evidence_view(evidence,{'alias_name':'TEST_ONLY字','alias_type':'courtesy'}, {'canonical_name':'TEST_ONLY人物'})['presentation']
    assert 'TEST_ONLY字' in vm['summary'] and 'TEST_ONLY人物' in vm['summary']
    assert not any(row['value'] in {'TEST_ONLY字','TEST_ONLY人物'} for row in vm['facts'])
    if relation=='supports':
        assert '记载' in vm['summary']
    else:
        assert '涉及' in vm['summary'] and '为TEST_ONLY人物' not in vm['summary']


@pytest.mark.parametrize('unsafe', [None, 0, '0', '', 'null', {'TEST_ONLY': 'raw'}, '["TEST_ONLY"]', '{"TEST_ONLY":1}'])
def test_missing_fields_follow_safe_reading_values_without_mutating_raw_data(unsafe):
    from copy import deepcopy
    from app.presentation import person_view
    raw = {'canonical_name': 'TEST_ONLY人物', 'dynasty': unsafe, 'native_place': unsafe,
           'birth_year': unsafe, 'death_year': unsafe}
    saved = deepcopy(raw)
    result = person_view(raw)
    vm = result['presentation']
    assert vm['missing_fields'] == ['朝代', '籍贯', '生年', '卒年']
    assert not any(item['label'] in vm['missing_fields'] for item in vm['facts'])
    assert vm['lifespan'] == '生卒待考'
    assert raw == saved
    assert all(result[key] == value for key, value in saved.items())


def test_missing_fields_keep_valid_dates_and_do_not_promote_unknown_range():
    from app.presentation import person_view
    raw = {'canonical_name': 'TEST_ONLY人物', 'dynasty': '仅测试朝', 'native_place': None,
           'birth_year': 712, 'death_year': 770,
           'field_sources': {'birth_year': [{'selected': 1, 'qualifier': 987654}]}}
    vm = person_view(raw)['presentation']
    assert vm['missing_fields'] == ['籍贯', '生年']
    assert not any(item['label'] == '生年' for item in vm['facts'])
    assert any(item['label'] == '卒年' and '770' in item['value'] for item in vm['facts'])
    assert '生年待考' in vm['lifespan'] and '987654' not in json.dumps(vm, ensure_ascii=False)
