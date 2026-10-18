"""Admit exact courtesy-name sentences from already cached Wikisource revisions."""
import argparse
import json
import sqlite3
from contextlib import closing
from pathlib import Path
from urllib.parse import parse_qs, quote as url_quote, urlsplit

from app.automatic_ingestion import canonical_json, inspect_source
from app.batch_import import backup_database
from app.collector import digest, identifier, now, plain
from app.db import PROJECT_ROOT, connect_db
from app.field_quality import fingerprint
from app.verification import json_write

ROOT = PROJECT_ROOT / 'data/collections/stage4-tang-song-20'
CBDB = PROJECT_ROOT / 'data/external/cbdb-20250520/cbdb.sqlite3'
CASES = (
    ('P-CBDB-32174', '王維', '摩詰', '唐', '新唐書/卷202'),
    ('P-CBDB-7364', '曾鞏', '子固', '宋', '宋史/卷319'),
    ('P-CBDB-630', '韓琦', '稚圭', '宋', '宋史/卷312'),
    ('P-CBDB-77', '陳升之', '暘叔', '宋', '宋史/卷312'),
)


def checked_cases(db):
    releases = json.loads((CBDB.parent / 'release.json').read_text(encoding='utf-8'))
    inspect_source(CBDB, releases)
    pages = {}
    for title in {r[4] for r in CASES}:
        matches = []
        for meta_path in (ROOT / 'raw').glob('*.json'):
            meta = json.loads(meta_path.read_text(encoding='utf-8'))
            query = parse_qs(urlsplit(meta.get('request_url', '')).query)
            if query.get('page') == [title] and meta.get('http_status') == 200:
                matches.append((meta_path, meta))
        if len(matches) != 1:
            raise ValueError(f'固定版本缓存缺失或不唯一：{title}')
        meta_path, meta = matches[0]
        raw_path = meta_path.with_suffix('.body')
        raw = raw_path.read_bytes()
        if digest(raw) != meta['sha256'] or meta.get('error'):
            raise ValueError(f'原始响应哈希/状态错误：{title}')
        parsed = json.loads(raw)['parse']
        if parsed['title'] != title or not isinstance(parsed['revid'], int) or parsed['revid'] < 1:
            raise ValueError(f'卷题/固定修订号错误：{title}')
        text = plain(parsed['text']['*'])
        text_path = ROOT / 'text' / (digest('raw/' + raw_path.name) + '.txt')
        if text_path.read_text(encoding='utf-8') != text:
            raise ValueError(f'离线正文缓存不等于原始响应：{title}')
        pages[title] = {'raw_sha256': digest(raw), 'text_sha256': digest(text),
                        'raw_path': str(raw_path.relative_to(PROJECT_ROOT)),
                        'text_path': str(text_path.relative_to(PROJECT_ROOT)),
                        'revision_id': parsed['revid'], 'text': text,
                        'sections': parsed.get('sections') or []}
    results = []
    with connect_db(db) as formal, connect_db(CBDB) as source:
        for pid, name, courtesy, dynasty, title in CASES:
            page = pages[title]
            marker = f'{name}，字{courtesy}'
            text = page['text']
            if text.count(marker) != 1:
                raise ValueError(f'直接姓名/字句缺失或不唯一：{name}')
            start = text.index(marker)
            end = text.find('。', start, start + 70) + 1
            if end <= start or '\n' in text[start:end]:
                raise ValueError(f'直接句跨段或过长：{name}')
            person = formal.execute('SELECT canonical_name,dynasty FROM people WHERE person_id=?',(pid,)).fetchone()
            aliases = formal.execute("""SELECT alias_id FROM aliases WHERE person_id=? AND alias_name=?
                AND alias_type='courtesy' AND verification_status='verified'""",(pid,courtesy)).fetchall()
            if not person or (person['canonical_name'],person['dynasty']) != (name,dynasty) or len(aliases) != 1:
                raise ValueError(f'正式身份或字号关系不唯一：{name}')
            source_id = int(pid.rsplit('-',1)[1])
            cbdb = source.execute('SELECT c_name_chn,c_dy FROM BIOG_MAIN WHERE c_personid=?',(source_id,)).fetchone()
            if not cbdb or cbdb['c_name_chn'] != name:
                raise ValueError(f'CBDB编号与人物姓名不符：{name}')
            matches = source.execute('''SELECT DISTINCT b.c_personid FROM BIOG_MAIN b
                JOIN ALTNAME_DATA a USING(c_personid) WHERE b.c_name_chn=? AND b.c_dy=?
                AND a.c_alt_name_chn=? AND a.c_alt_name_type_code=4''',
                (name,cbdb['c_dy'],courtesy)).fetchall()
            if {r[0] for r in matches} != {source_id}:
                raise ValueError(f'同名同朝同字不能唯一对应：{name}')
            anchors = [s.get('anchor') for s in page['sections'] if s.get('line') == name and s.get('level') == '1']
            if len(anchors) > 1 or (anchors and not anchors[0]):
                raise ValueError(f'人物章节锚点不唯一：{name}')
            paragraph_end = text.find('\n', end)
            if paragraph_end < 0: paragraph_end = min(len(text), end + 280)
            if paragraph_end - start < 130:
                next_end = text.find('\n', paragraph_end + 1)
                if next_end > paragraph_end:
                    paragraph_end = min(next_end, start + 300)
            context = text[start:paragraph_end]
            if len(context) > 420:
                stop = text.find('。', start + 150, start + 310)
                context = text[start:stop + 1] if stop >= 0 else text[start:start + 280]
            if not context.startswith(text[start:end]) or len(context) < len(text[start:end]) + 30:
                raise ValueError(f'连续上下文不足：{name}')
            results.append({'person_id':pid,'name':name,'courtesy':courtesy,'dynasty':dynasty,
                'alias_id':aliases[0][0], 'book_title':title.split('/')[0],
                'volume':title.split('/')[1],'source_title':title,
                'revision_id':page['revision_id'],'revision_url':f"https://zh.wikisource.org/w/index.php?oldid={page['revision_id']}",
                'section_anchor':anchors[0] if anchors else '',
                'raw_path':page['raw_path'],'text_path':page['text_path'],
                'raw_sha256':page['raw_sha256'],'text_sha256':page['text_sha256'],
                'quote':text[start:end],'context':context,'start':start,'end':end,
                'line_start':text.count('\n',0,start)+1,'line_end':text.count('\n',0,end-1)+1})
    return results


def apply(db, output):
    output = Path(output); output.mkdir(parents=True,exist_ok=True)
    cases = checked_cases(db)
    manifest = {'policy':'cached-fixed-courtesy-v1','cases':cases}
    signature = digest(canonical_json(manifest)); batch = 'ST65-TEXT-MORE-' + signature[:16]
    with connect_db(db) as c:
        old = c.execute('SELECT status FROM quality_batches WHERE batch_id=?',(batch,)).fetchone()
        if old and old[0] == 'committed':
            result = {'status':'idempotent_noop','batch_id':batch,'new_sources':0,'new_evidences':0}
            json_write(output/'repeat_result.json',result);return result
        if old: raise ValueError('同批次存在非已提交状态')
    backup = backup_database(db,output/'backups')
    with connect_db(db) as c:
        c.execute('BEGIN IMMEDIATE')
        try:
            before = {t:c.execute('SELECT count(*) FROM '+t).fetchone()[0] for t in ('people','aliases','sources','evidences')}
            c.execute("INSERT INTO quality_batches(batch_id,imported_at,manifest_sha256,backup_json,status) VALUES(?,?,?,?,'committed')",
                      (batch,now(),signature,canonical_json(backup)))
            for case in cases:
                stable = (case['revision_id'],case['name'],case['alias_id'])
                sid = identifier('S-WS-PARA',*stable)
                eid = identifier('E-WS-PARA',sid,case['start'],case['end'],case['raw_sha256'])
                original_url = case['revision_url'] + ('#'+url_quote(case['section_anchor'],safe='') if case['section_anchor'] else '')
                source_row = (sid,case['book_title'],case['volume'],case['name'],'维基文库',original_url,
                              f"维基文库修订 {case['revision_id']}；古籍转录文本 / CC BY-SA 4.0")
                if c.execute('SELECT 1 FROM sources WHERE source_id=?',(sid,)).fetchone():
                    raise ValueError('目标来源ID已被占用')
                c.execute('INSERT INTO sources(source_id,book_title,volume,chapter,platform,original_url,edition) VALUES(?,?,?,?,?,?,?)',source_row)
                notes = {'evidence_kind':'classical_text','verification_method':'wikisource_fixed_text_v1',
                    'quality_grade':'A','relation_type':f"《{case['book_title']}》史传明记人物的字",
                    'evidence_type':'古籍转录文本','source_version':str(case['revision_id']),
                    'license_id':'CC-BY-SA-4.0','context':case['context'],
                    'remarks':'此句仅确认姓名与字；其余叙事是阅读上下文，不自动核准其他人物字段。',
                    'locator':{k:case[k] for k in ('revision_id','raw_path','text_path','raw_sha256',
                        'text_sha256','start','end','line_start','line_end','section_anchor')}}
                c.execute('''INSERT INTO evidences(evidence_id,alias_id,source_id,original_text,
                    usage_relation,evidence_nature,verification_status,notes) VALUES(?,?,?,?,'supports','direct','verified',?)''',
                    (eid,case['alias_id'],sid,case['quote'],canonical_json(notes)))
                old_q = c.execute('SELECT grade FROM relation_quality WHERE alias_id=?',(case['alias_id'],)).fetchone()
                if not old_q or old_q[0] not in ('A','B','C'):raise ValueError('待核关系不准入')
                if old_q[0] != 'A':
                    c.execute("UPDATE relation_quality SET grade='A',source_id=?,basis_json=?,batch_id=? WHERE alias_id=?",
                        (sid,canonical_json({'revision':case['revision_id'],'raw_sha256':case['raw_sha256'],
                            'quote':case['quote'],'start':case['start'],'end':case['end']}),batch,case['alias_id']))
            if c.execute('PRAGMA integrity_check').fetchone()[0] != 'ok' or c.execute('PRAGMA foreign_key_check').fetchall():
                raise ValueError('数据库完整性检查失败')
            after = {t:c.execute('SELECT count(*) FROM '+t).fetchone()[0] for t in before}
            if (after['people'],after['aliases']) != (before['people'],before['aliases']) or (
                    after['sources']-before['sources'],after['evidences']-before['evidences']) != (4,4):
                raise ValueError('增量计数异常')
            c.execute('UPDATE quality_batches SET after_fingerprint=? WHERE batch_id=?',(fingerprint(c),batch))
            c.commit()
        except Exception:
            c.rollback();raise
    result = {'status':'committed','batch_id':batch,'new_sources':4,'new_evidences':4,
        'people':[c['name'] for c in cases],'counts_before':before,'counts_after':after,'backup':backup}
    json_write(output/'validated_manifest.json',manifest)
    json_write(output/'import_result.json',result)
    return result


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--db',default='data/guji.db')
    parser.add_argument('--output',default='work/paragraph-expansion/formal')
    parser.add_argument('--dry-run',action='store_true')
    args=parser.parse_args();db=(PROJECT_ROOT/args.db).resolve();out=(PROJECT_ROOT/args.output).resolve()
    if args.dry_run:
        out.mkdir(parents=True,exist_ok=False)
        clone=out/'TEST_ONLY_REAL_DATABASE_COPY.db'
        with closing(sqlite3.connect(db)) as src,closing(sqlite3.connect(clone)) as dst:src.backup(dst)
        result=apply(clone,out);result['dry_run']=True
    else: result=apply(db,out)
    print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__': main()
