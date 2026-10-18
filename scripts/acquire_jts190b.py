"""Cache and validate one fixed Wikisource transcription for direct naming quotes."""
import json
import re
from pathlib import Path

from app.collector import Client, digest
from app.db import PROJECT_ROOT, connect_db
from app.verification import json_write

ROOT = PROJECT_ROOT / 'data/external/wikisource-jts190b'
SOURCE = PROJECT_ROOT / 'data/external/cbdb-20250520/cbdb.sqlite3'
TARGETS = (
    {'person_id': 'P-CBDB-32540', 'source_person_id': 32540, 'name': '李白', 'courtesy': '太白', 'marker': '李白字太白'},
    {'person_id': 'P-CBDB-3915', 'source_person_id': 3915, 'name': '杜甫', 'courtesy': '子美', 'marker': '杜甫，字子美'},
)


def validated_page():
    """The collector uses a descriptive User-Agent, maxlag, one HTTPS request and byte cache."""
    cached = ROOT / 'raw'
    existing = cached.exists() and any(cached.glob('*.body'))
    client = Client(ROOT, replay=ROOT if existing else None, resume=ROOT.exists(), timeout=35, interval=2, max_requests=1)
    page = client.page('舊唐書/卷190下')
    if page['title'] != '舊唐書/卷190下' or not isinstance(page['revision'], int) or page['revision'] < 1:
        raise ValueError('维基文库返回的卷题/修订号不匹配')
    return page, client.logs


def candidate_rows(page, db_path=None):
    sections = {s.get('line'): s.get('anchor') for s in page['sections'] if s.get('level') == '1'}
    if not all(t['name'] in sections and sections[t['name']] for t in TARGETS):
        raise ValueError('李白/杜甫分节无法唯一定位，停止准入')
    text = page['text']
    result = []
    with connect_db(SOURCE) as source, connect_db(db_path or PROJECT_ROOT / 'data/guji.db') as formal:
        for target in TARGETS:
            if text.count(target['marker']) != 1:
                raise ValueError('原文姓名与字句缺失或不唯一：' + target['name'])
            start = text.index(target['marker'])
            end = text.find('。', start, start + 80) + 1
            if end <= start or '\n' in text[start:end] or not target['courtesy'] in text[start:end]:
                raise ValueError('原文摘录跨行/句或字无法定位：' + target['name'])
            quote = text[start:end]
            if not quote.startswith(target['marker']) or not quote.endswith('。') or text[start:end] != quote:
                raise ValueError('原文字符位置与摘录不一致')
            person = formal.execute('SELECT canonical_name,dynasty FROM people WHERE person_id=?', (target['person_id'],)).fetchone()
            if not person or person['canonical_name'] != target['name'] or person['dynasty'] != '唐':
                raise ValueError('正式人物稳定ID/朝代不匹配')
            alias = formal.execute('''SELECT alias_id FROM aliases WHERE person_id=? AND alias_name=?
                AND alias_type='courtesy' AND verification_status='verified' ''', (target['person_id'], target['courtesy'])).fetchall()
            if len(alias) != 1:
                raise ValueError('正式库字号关系缺失或不唯一')
            cbdb = source.execute('SELECT c_name_chn,c_dy FROM BIOG_MAIN WHERE c_personid=?', (target['source_person_id'],)).fetchone()
            if not cbdb or cbdb['c_name_chn'] != target['name']:
                raise ValueError('原始CBDB人物ID与姓名不符')
            matching = source.execute('''SELECT DISTINCT b.c_personid FROM BIOG_MAIN b
                JOIN ALTNAME_DATA a USING(c_personid)
                WHERE b.c_name_chn=? AND b.c_dy=? AND a.c_alt_name_chn=? AND a.c_alt_name_type_code=4''',
                (target['name'], cbdb['c_dy'], target['courtesy'])).fetchall()
            if {r[0] for r in matching} != {target['source_person_id']}:
                raise ValueError('同名同朝同字无法唯一对应源人物：' + target['name'])
            line_start = text.count('\n', 0, start) + 1
            line_end = text.count('\n', 0, end - 1) + 1
            result.append({**target, 'alias_id': alias[0]['alias_id'], 'quote': quote,
                           'start': start, 'end': end, 'line_start': line_start, 'line_end': line_end,
                           'section_anchor': sections[target['name']]})
    return result


def main():
    page, logs = validated_page()
    rows = candidate_rows(page)
    manifest = {'provider': 'Wikisource', 'book_title': '舊唐書', 'volume': '卷190下',
        'revision_id': page['revision'], 'revision_url': page['url'],
        'title': page['title'], 'raw_path': page['raw_path'], 'raw_sha256': page['raw_sha256'],
        'text_path': page['text_path'], 'text_sha256': page['text_sha256'],
        'license_id': 'CC-BY-SA-4.0', 'license_basis_url': 'https://zh.wikisource.org/wiki/Wikisource:版权信息',
        'scope': 'fixed-revision transcription; no book image or manuscript facsimile',
        'cases': rows}
    target = ROOT / 'validated_quotes.json'
    if target.exists() and json.loads(target.read_text(encoding='utf-8')) != manifest:
        raise ValueError('固定修订的缓存清单发生变化，禁止覆盖')
    json_write(target, manifest)
    json_write(ROOT / 'acquisition_log.json', {'requests_on_this_run': sum(r['status'] == 'fetched' for r in logs),
        'cache_sha256': digest(json.dumps(manifest, ensure_ascii=False, sort_keys=True)),
        'events': logs})
    print(json.dumps({'revision_id': page['revision'], 'raw_sha256': page['raw_sha256'],
                      'quoted_people': [r['name'] for r in rows], 'requests_on_this_run': sum(r['status'] == 'fetched' for r in logs)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
