"""Real-source paragraphs must stay bound to the right person and sentence."""
import hashlib
import json

from fastapi.testclient import TestClient

from app.catalog import PUBLIC_CTE, prepare
from app.classical_passage import passage_for
from app.db import PROJECT_ROOT, connect_db
from app.main import create_app
from app.presentation import evidence_view
from app.source_catalog import get_source
from scripts.import_cached_courtesy_text import checked_cases
from scripts.import_kanripo_paragraphs import checked_candidates

DB = PROJECT_ROOT / 'data/guji.db'


def _classical_rows(old_only=False):
    with connect_db(DB) as c:
        prepare(c)
        rows = c.execute(PUBLIC_CTE + '''SELECT e.*,a.person_id,a.alias_name,a.alias_type,
            p.canonical_name,p.dynasty FROM public_evidence e
            JOIN public_aliases a USING(alias_id) JOIN people p USING(person_id)''').fetchall()
    result = [dict(r) for r in rows if evidence_view(dict(r))['presentation']['kind'] == 'classical_text']
    if old_only:
        result = [r for r in result if not r['evidence_id'].startswith('E7-')]
    return result


def _assert_old_rows_preserved(previous, current, table, key):
    for row in previous.execute('SELECT * FROM ' + table):
        found = current.execute(f'SELECT * FROM {table} WHERE {key}=?', (row[key],)).fetchone()
        assert found is not None and tuple(found) == tuple(row), (table, row[key])


def test_every_public_ancient_evidence_has_a_longer_exact_highlighted_passage():
    rows = _classical_rows(old_only=True)
    assert len(rows) == 61
    assert len({r['person_id'] for r in rows}) == 54
    for row in rows:
        passage = passage_for(row, name=row['canonical_name'],
            alias_name=row['alias_name'],alias_type=row['alias_type'])
        assert passage, row['evidence_id']
        assert len(passage['text']) >= 100, row['evidence_id']
        assert len(passage['text']) > len(row['original_text']) + 25
        assert passage['text'] == passage['before'] + passage['highlight'] + passage['after']
        assert passage['text'].count(passage['highlight']) == 1
        assert '¶' not in passage['text'] and '<pb:' not in passage['text']


def test_four_new_courtesy_sentences_are_byte_located_and_not_field_imports():
    receipt = json.loads((PROJECT_ROOT / 'work/paragraph-expansion/formal/import_result.json').read_text(encoding='utf-8'))
    assert receipt['new_sources'] == receipt['new_evidences'] == 4
    assert receipt['counts_before'] == {'people':454,'aliases':1195,'sources':19,'evidences':1725}
    assert receipt['counts_after'] == {'people':454,'aliases':1195,'sources':23,'evidences':1729}
    old = receipt['backup']
    assert hashlib.sha256(__import__('pathlib').Path(old['path']).read_bytes()).hexdigest() == old['sha256']
    cases = checked_cases(DB)
    assert [r['name'] for r in cases] == ['王維','曾鞏','韓琦','陳升之']
    with connect_db(DB) as c:
        for case in cases:
            evidence = c.execute('SELECT * FROM evidences WHERE alias_id=? AND original_text=?',
                                 (case['alias_id'],case['quote'])).fetchone()
            assert evidence and evidence['verification_status'] == 'verified'
            source = c.execute('SELECT * FROM sources WHERE source_id=?',(evidence['source_id'],)).fetchone()
            assert source['book_title'] == case['book_title'] and source['volume'] == case['volume']
            assert f"oldid={case['revision_id']}" in source['original_url']
            notes = json.loads(evidence['notes'])
            assert notes['locator']['start'] == case['start']
            assert notes['locator']['end'] == case['end']
            assert notes['context'] == case['context']
            assert notes['quality_grade'] == 'A'
            assert c.execute('SELECT grade FROM relation_quality WHERE alias_id=?',
                             (case['alias_id'],)).fetchone()[0] == 'A'
        with connect_db(old['path']) as previous:
            for table,key in (('people','person_id'),('aliases','alias_id')):
                _assert_old_rows_preserved(previous,c,table,key)
            for table,key in (('sources','source_id'),('evidences','evidence_id')):
                for row in previous.execute('SELECT * FROM '+table):
                    assert tuple(c.execute(f'SELECT * FROM {table} WHERE {key}=?',(row[key],)).fetchone()) == tuple(row)
        assert c.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        assert not c.execute('PRAGMA foreign_key_check').fetchall()


def test_reading_pages_mark_related_sentence_and_keep_equal_names_separate():
    with TestClient(create_app(DB)) as client:
        for name in ('王維','曾鞏','韓琦','陳升之','李白','杜甫'):
            row = next(r for r in _classical_rows(old_only=True) if r['canonical_name'] == name)
            source = get_source(DB,row['source_id'],person_id=row['person_id'])
            passage = source['classical_quotes'][0]['passage']
            html = client.get('/sources/'+row['source_id'],params={'person_id':row['person_id']})
            assert html.status_code == 200
            assert passage['highlight'] in html.text and passage['after'][:20] in html.text
            assert '<mark class="source-relevant-line">' in html.text
            assert '<pre class="raw-record-content"' in html.text and 'hidden></pre>' in html.text
            alias_html = client.get('/aliases/'+row['alias_id'])
            assert alias_html.status_code == 200
            assert '<mark class="source-relevant-line">' in alias_html.text
        wang = [r for r in _classical_rows() if r['canonical_name'] == '王珪']
        assert {r['dynasty'] for r in wang} == {'唐','宋'}
        assert len({r['person_id'] for r in wang}) == 2
        for r in wang:
            html = client.get('/sources/'+r['source_id'],params={'person_id':r['person_id']}).text
            assert r['original_text'] in html
            assert ('叔玠' in html) == (r['dynasty'] == '唐')
        for person in ('苏轼','欧阳修'):
            row = next(r for r in _classical_rows() if r['canonical_name'] == person)
            passage = passage_for(row,name=row['canonical_name'],alias_name=row['alias_name'],alias_type=row['alias_type'])
            assert '諡' in passage['highlight'] and '文忠' in passage['highlight']


def test_pinned_kanripo_batch_has_35_distinct_headwords_and_keeps_old_rows():
    receipt=json.loads((PROJECT_ROOT/'work/paragraph-expansion/kanripo-formal/import_result.json').read_text(encoding='utf-8'))
    assert receipt['new_sources']==7 and receipt['new_evidences']==35
    assert receipt['counts_before']=={'people':454,'aliases':1195,'sources':23,'evidences':1729}
    assert receipt['counts_after']=={'people':454,'aliases':1195,'sources':30,'evidences':1764}
    backup=__import__('pathlib').Path(receipt['backup']['path'])
    assert hashlib.sha256(backup.read_bytes()).hexdigest()==receipt['backup']['sha256']
    cases,excluded=checked_candidates(backup)
    assert len(cases)==35 and len({r['person_id'] for r in cases})==35
    assert excluded['existing_ancient']==3
    with connect_db(DB) as current,connect_db(backup) as old:
        for table,key in (('people','person_id'),('aliases','alias_id')):
            _assert_old_rows_preserved(old,current,table,key)
        for table,key in (('sources','source_id'),('evidences','evidence_id')):
            for row in old.execute('SELECT * FROM '+table):
                assert tuple(current.execute(f'SELECT * FROM {table} WHERE {key}=?',(row[key],)).fetchone())==tuple(row)
        for case in cases:
            row=current.execute("SELECT e.*,s.original_url FROM evidences e JOIN sources s USING(source_id) WHERE e.alias_id=? AND e.evidence_id LIKE 'E-KR-PARA-%'",
                (case['alias_id'],)).fetchone()
            assert row and row['original_text']==case['quote']
            notes=json.loads(row['notes'])
            assert notes['locator']['sha256']==case['sha256']
            assert notes['locator']['start']==case['start']
            assert notes['locator']['end']==case['end']
            assert current.execute('SELECT grade FROM relation_quality WHERE alias_id=?',
                (case['alias_id'],)).fetchone()[0]=='A'
        assert current.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
        assert not current.execute('PRAGMA foreign_key_check').fetchall()


def test_kanripo_reading_links_to_exact_source_lines():
    rows=[r for r in _classical_rows() if r['evidence_id'].startswith('E-KR-PARA-')]
    assert len(rows)==35
    with TestClient(create_app(DB)) as client:
        for row in rows:
            source=get_source(DB,row['source_id'],person_id=row['person_id'])
            quote=next(q for q in source['classical_quotes'] if q['text']==row['original_text'])
            assert '#L' in quote['original_url']
            assert row['canonical_name']+'字'+row['alias_name'] in quote['passage']['highlight']
        for row in rows[::8]:
            html=client.get('/sources/'+row['source_id'],params={'person_id':row['person_id']}).text
            assert '<mark class="source-relevant-line">' in html
            assert '#L' in html and '排版行、页标记已合并' in html
