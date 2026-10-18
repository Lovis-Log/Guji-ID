"""Add directly headed Song History courtesy declarations from pinned Kanripo files."""
import argparse
import hashlib
import json
import re
import sqlite3
from contextlib import closing
from pathlib import Path

from app.automatic_ingestion import canonical_json, inspect_source
from app.batch_import import backup_database
from app.collector import digest, identifier, now
from app.db import PROJECT_ROOT, connect_db
from app.field_quality import fingerprint
from app.verification import json_write

ROOT=PROJECT_ROOT/'data/external/kanripo-stage6'
CBDB=PROJECT_ROOT/'data/external/cbdb-20250520/cbdb.sqlite3'


def checked_candidates(db):
    manifest=json.loads((ROOT/'texts.json').read_text(encoding='utf-8'))
    if manifest.get('repository')!='kanripo/KR2a0032' or manifest.get('license_id')!='CC-BY-SA-4.0':
        raise ValueError('来源仓库/许可不符')
    if not re.fullmatch('[0-9a-f]{40}',manifest.get('commit','')):
        raise ValueError('来源提交号不合法')
    release=json.loads((CBDB.parent/'release.json').read_text(encoding='utf-8'))
    inspect_source(CBDB,release)
    files=[]
    for item in manifest['files']:
        if not re.fullmatch(r'KR2a0032_\d{3}\.txt',item['path']):
            raise ValueError('古籍文件路径不合法')
        raw=(ROOT/item['path']).read_bytes()
        blob=hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()
        if digest(raw)!=item['sha256'] or blob!=item['git_blob_sha1']:
            raise ValueError('固定版本正文哈希或Git blob不符')
        if not item['url'].startswith('https://github.com/kanripo/KR2a0032/blob/'+manifest['commit']+'/'):
            raise ValueError('来源链接未锁定提交号')
        files.append((item,raw.decode('utf-8')))
    selected=[];excluded={'existing_ancient':0,'not_unique_pair':0,'no_headline':0}
    with connect_db(db) as formal,connect_db(CBDB) as source:
        rows=formal.execute("""SELECT a.alias_id,a.person_id,a.alias_name,p.canonical_name,p.dynasty
            FROM aliases a JOIN people p USING(person_id) WHERE a.alias_type='courtesy'
            AND a.verification_status='verified' AND p.verification_status='verified'
            AND p.dynasty='宋' AND a.person_id LIKE 'P-CBDB-%' ORDER BY a.alias_id""").fetchall()
        for row in rows:
            name,courtesy=row['canonical_name'],row['alias_name']
            if formal.execute("""SELECT 1 FROM evidences e JOIN sources s USING(source_id)
                WHERE e.alias_id=? AND s.platform='Kanripo' AND e.verification_status='verified'
                AND e.evidence_id NOT LIKE 'E-KR-PARA-%' LIMIT 1""",
                (row['alias_id'],)).fetchone():
                excluded['existing_ancient']+=1;continue
            source_pid=int(row['person_id'].removeprefix('P-CBDB-'))
            original=source.execute('SELECT c_name_chn,c_dy FROM BIOG_MAIN WHERE c_personid=?',(source_pid,)).fetchone()
            if not original or original['c_name_chn']!=name:
                excluded['not_unique_pair']+=1;continue
            matches=source.execute('''SELECT DISTINCT b.c_personid FROM BIOG_MAIN b
                JOIN ALTNAME_DATA a USING(c_personid) WHERE b.c_name_chn=? AND b.c_dy=?
                AND a.c_alt_name_chn=? AND a.c_alt_name_type_code=4''',
                (name,original['c_dy'],courtesy)).fetchall()
            if {r[0] for r in matches}!={source_pid}:
                excluded['not_unique_pair']+=1;continue
            marker=name+'字'+courtesy
            locations=[]
            for item,text in files:
                for match in re.finditer(re.escape(marker),text):
                    if match.start()!=0 and text[match.start()-1]!='\n':continue
                    line_end=text.find('\n',match.end())
                    if line_end<0:line_end=len(text)
                    line=text[match.start():line_end].rstrip('¶')
                    if len(line)>80 or len(line)<len(marker)+3 or '<pb:' in line:continue
                    context_end=min(len(text),match.start()+300)
                    context=text[match.start():context_end]
                    # A later biography heading cannot be displayed as this person's context.
                    next_head=re.search(r'\n[\u3400-\u9fff]{2,4}字[\u3400-\u9fff]{2,4}',context[len(line):])
                    if next_head:context=context[:len(line)+next_head.start()]
                    if len(context)<len(line)+75:continue
                    locations.append((item,text,match.start(),line,context))
            if len(locations)!=1:
                excluded['no_headline']+=1;continue
            item,text,start,line,context=locations[0]
            quality=formal.execute('SELECT grade FROM relation_quality WHERE alias_id=?',(row['alias_id'],)).fetchone()
            if not quality or quality[0] not in ('A','B','C'):
                excluded['not_unique_pair']+=1;continue
            selected.append({'person_id':row['person_id'],'person_name':name,'courtesy':courtesy,
                'alias_id':row['alias_id'],'file':item['path'],'book_title':'宋史','volume':item['volume'],
                'url':item['url'],'edition':item['edition'],'sha256':item['sha256'],
                'commit':manifest['commit'],'quote':line,'context':context,
                'start':start,'end':start+len(line),'line_start':text.count('\n',0,start)+1,
                'line_end':text.count('\n',0,start+len(line))+1})
    if len(selected)!=35:
        raise ValueError(f'预期35条直接传记开头，实际{len(selected)}；停止整批：{excluded}')
    return selected,excluded


def apply(db,output):
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    cases,excluded=checked_candidates(db)
    signature=digest(canonical_json({'policy':'kanripo-headline-courtesy-v1','cases':cases}))
    batch='ST65-KR-PARA-'+signature[:16]
    with connect_db(db) as c:
        old=c.execute('SELECT status FROM quality_batches WHERE batch_id=?',(batch,)).fetchone()
        if old and old[0]=='committed':
            result={'status':'idempotent_noop','batch_id':batch,'new_evidences':0,'new_sources':0}
            json_write(output/'repeat_result.json',result);return result
        if old:raise ValueError('同批次存在非已提交状态')
    backup=backup_database(db,output/'backups')
    with connect_db(db) as c:
        c.execute('BEGIN IMMEDIATE')
        try:
            before={t:c.execute('SELECT count(*) FROM '+t).fetchone()[0] for t in ('people','aliases','sources','evidences')}
            c.execute("INSERT INTO quality_batches(batch_id,imported_at,manifest_sha256,backup_json,status) VALUES(?,?,?,?,'committed')",
                (batch,now(),signature,canonical_json(backup)))
            inserted_sources=0
            for case in cases:
                sid=identifier('S-KR',case['commit'],case['file'])
                eid=identifier('E-KR-PARA',case['alias_id'],case['sha256'],case['start'],case['end'])
                expected=(sid,case['book_title'],case['volume'],case['volume'],'Kanripo',case['url'],case['edition'])
                prior=c.execute('SELECT * FROM sources WHERE source_id=?',(sid,)).fetchone()
                if prior:
                    if tuple(prior)!=expected:raise ValueError('同一卷来源记录不一致')
                else:
                    c.execute('INSERT INTO sources(source_id,book_title,volume,chapter,platform,original_url,edition) VALUES(?,?,?,?,?,?,?)',expected)
                    inserted_sources+=1
                if c.execute('SELECT 1 FROM evidences WHERE evidence_id=?',(eid,)).fetchone():
                    raise ValueError('目标证据ID已占用')
                notes={'verification_method':'kanripo_fixed_headline_v1','evidence_kind':'classical_text',
                    'quality_grade':'A','relation_type':'古籍传记开头明记姓名与字（程序核对）',
                    'evidence_type':'史传记载','license_id':'CC-BY-SA-4.0','context':case['context'],
                    'remarks':'仅姓名与字的直接句准入；周边史传文字作为阅读上下文，不自动更新其他字段。',
                    'locator':{k:case[k] for k in ('commit','file','sha256','start','end','line_start','line_end')}}
                c.execute('''INSERT INTO evidences(evidence_id,alias_id,source_id,original_text,
                    usage_relation,evidence_nature,verification_status,notes) VALUES(?,?,?,?,'supports','direct','verified',?)''',
                    (eid,case['alias_id'],sid,case['quote'],canonical_json(notes)))
                grade=c.execute('SELECT grade FROM relation_quality WHERE alias_id=?',(case['alias_id'],)).fetchone()
                if not grade or grade[0] not in ('A','B','C'):raise ValueError('待核关系不准入')
                if grade[0]!='A':
                    c.execute("UPDATE relation_quality SET grade='A',source_id=?,basis_json=?,batch_id=? WHERE alias_id=?",
                        (sid,canonical_json({'commit':case['commit'],'file':case['file'],
                         'sha256':case['sha256'],'quote':case['quote'],'start':case['start'],'end':case['end']}),
                         batch,case['alias_id']))
            after={t:c.execute('SELECT count(*) FROM '+t).fetchone()[0] for t in before}
            if (after['people'],after['aliases'])!=(before['people'],before['aliases']) or (
               after['evidences']-before['evidences'],after['sources']-before['sources'])!=(35,inserted_sources):
                raise ValueError('增量数量异常')
            if c.execute('PRAGMA integrity_check').fetchone()[0]!='ok' or c.execute('PRAGMA foreign_key_check').fetchall():
                raise ValueError('数据库完整性错误')
            c.execute('UPDATE quality_batches SET after_fingerprint=? WHERE batch_id=?',(fingerprint(c),batch))
            c.commit()
        except Exception:
            c.rollback();raise
    result={'status':'committed','batch_id':batch,'new_sources':inserted_sources,'new_evidences':35,
        'people_with_new_classical_support':len({r['person_id'] for r in cases}),
        'counts_before':before,'counts_after':after,'backup':backup,'excluded':excluded}
    json_write(output/'validated_manifest.json',{'policy':'kanripo-headline-courtesy-v1','cases':cases})
    json_write(output/'import_result.json',result)
    return result


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--db',default='data/guji.db')
    parser.add_argument('--output',default='work/paragraph-expansion/kanripo-formal')
    parser.add_argument('--dry-run',action='store_true')
    args=parser.parse_args();db=(PROJECT_ROOT/args.db).resolve();out=(PROJECT_ROOT/args.output).resolve()
    if args.dry_run:
        out.mkdir(parents=True,exist_ok=False)
        clone=out/'TEST_ONLY_REAL_DATABASE_COPY.db'
        with closing(sqlite3.connect(db)) as src,closing(sqlite3.connect(clone)) as dst:src.backup(dst)
        result=apply(clone,out);result['dry_run']=True
    else:result=apply(db,out)
    print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':main()
