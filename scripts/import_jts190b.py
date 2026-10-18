"""Admission of two exact, source-backed Old Book of Tang courtesy-name quotes."""
import argparse
import json
import sqlite3
from contextlib import closing
from pathlib import Path
from urllib.parse import quote as url_quote

from app.automatic_ingestion import canonical_json, inspect_source
from app.batch_import import backup_database
from app.collector import digest, identifier, now
from app.db import PROJECT_ROOT, connect_db
from app.field_quality import fingerprint
from app.source_acquisition import file_hash
from app.verification import json_write
from scripts.acquire_jts190b import ROOT, SOURCE, validated_page, candidate_rows


def checked_manifest(db):
    saved=json.loads((ROOT/'validated_quotes.json').read_text(encoding='utf-8'))
    page,_=validated_page()  # exact cached API bytes; no network request
    if (saved['raw_sha256']!=page['raw_sha256'] or saved['text_sha256']!=page['text_sha256']
            or saved['revision_id']!=page['revision'] or saved['revision_url']!=page['url']
            or saved['title']!=page['title'] or saved['book_title']!='舊唐書'
            or saved['volume']!='卷190下' or saved['license_id']!='CC-BY-SA-4.0'):
        raise ValueError('古籍快照、许可或修订号不匹配')
    fresh=candidate_rows(page,db)
    if saved['cases']!=fresh:
        raise ValueError('摘录、字符定位、人物及字号关系与缓存清单不一致')
    release=json.loads((SOURCE.parent/'release.json').read_text(encoding='utf-8'))
    inspect_source(SOURCE,release)
    return saved


def apply(db, output):
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    manifest=checked_manifest(db)
    signature=digest(canonical_json({'policy':'jts190b-exact-courtesy-v1','revision':manifest['revision_id'],
        'raw_sha256':manifest['raw_sha256'],'text_sha256':manifest['text_sha256'],'cases':manifest['cases']}))
    batch='ST65-TEXT-'+signature[:20]
    with connect_db(db) as c:
        existing=c.execute('SELECT status FROM quality_batches WHERE batch_id=?',(batch,)).fetchone()
        if existing and existing[0]=='committed':
            result={'status':'idempotent_noop','batch_id':batch,'new_sources':0,'new_evidences':0,'backup':None}
            json_write(output/'repeat_result.json',result);return result
        if existing:
            raise ValueError('相同历史批次已存在但不是已提交状态')
    backup=backup_database(db,output/'backups')
    before={};after={}
    with connect_db(db) as c:
        c.execute('BEGIN IMMEDIATE')
        try:
            before={table:c.execute('SELECT count(*) FROM '+table).fetchone()[0] for table in ('people','aliases','sources','evidences')}
            c.execute('''INSERT INTO quality_batches(batch_id,imported_at,manifest_sha256,backup_json,status)
                VALUES(?,?,?,?,'committed')''',(batch,now(),signature,canonical_json(backup)))
            for case in manifest['cases']:
                alias=c.execute('''SELECT a.alias_id,a.alias_name,a.alias_type,p.canonical_name,p.dynasty FROM aliases a
                  JOIN people p USING(person_id) WHERE a.alias_id=? AND a.person_id=? AND p.verification_status='verified'
                  AND a.verification_status='verified' ''',(case['alias_id'],case['person_id'])).fetchone()
                if not alias or (alias['alias_name'],alias['alias_type'],alias['canonical_name'],alias['dynasty'])!=(
                    case['courtesy'],'courtesy',case['name'],'唐'):
                    raise ValueError('正式人物/字/朝代身份发生变化，整批取消')
                sid=identifier('S-JTS190B',manifest['revision_id'],case['section_anchor'])
                eid=identifier('E-JTS190B',sid,case['alias_id'],manifest['raw_sha256'],case['start'],case['end'])
                original_url=manifest['revision_url']+'#'+url_quote(case['section_anchor'],safe='')
                source_row={'source_id':sid,'book_title':'舊唐書','volume':'卷190下','chapter':case['name'],
                  'platform':'维基文库','original_url':original_url,
                  'edition':f"维基文库修订 {manifest['revision_id']}；古籍转录文本 / CC BY-SA 4.0"}
                prior=c.execute('SELECT * FROM sources WHERE source_id=?',(sid,)).fetchone()
                if prior and any(prior[k]!=v for k,v in source_row.items()):
                    raise ValueError('同源ID已有不同内容')
                if not prior:
                    c.execute('''INSERT INTO sources(source_id,book_title,volume,chapter,platform,original_url,edition)
                      VALUES(:source_id,:book_title,:volume,:chapter,:platform,:original_url,:edition)''',source_row)
                notes={'evidence_kind':'classical_text','verification_method':'wikisource_fixed_text_v1',
                  'quality_grade':'A','relation_type':'《舊唐書》史传明记人物的字',
                  'evidence_type':'古籍转录文本','source_version':str(manifest['revision_id']),
                  'license_id':'CC-BY-SA-4.0',
                  'remarks':'此句仅用于确认姓名与字的关系；不据原文地名覆盖籍贯，也不推断生卒年、号或本人自称。',
                  'locator':{'revision_id':manifest['revision_id'],'raw_sha256':manifest['raw_sha256'],
                    'text_sha256':manifest['text_sha256'],'start':case['start'],'end':case['end'],
                    'line_start':case['line_start'],'line_end':case['line_end'],
                    'section_anchor':case['section_anchor']}}
                evidence_row={'evidence_id':eid,'alias_id':case['alias_id'],'source_id':sid,
                  'original_text':case['quote'],'usage_relation':'supports','evidence_nature':'direct',
                  'verification_status':'verified','notes':canonical_json(notes)}
                prior_e=c.execute('SELECT * FROM evidences WHERE evidence_id=?',(eid,)).fetchone()
                if prior_e and any(prior_e[k]!=v for k,v in evidence_row.items()):
                    raise ValueError('同证据ID已有不同原文或来源')
                if not prior_e:
                    c.execute('''INSERT INTO evidences(evidence_id,alias_id,source_id,original_text,
                      usage_relation,evidence_nature,verification_status,notes)
                      VALUES(:evidence_id,:alias_id,:source_id,:original_text,
                      :usage_relation,:evidence_nature,:verification_status,:notes)''',evidence_row)
                relation=c.execute('SELECT grade FROM relation_quality WHERE alias_id=?',(case['alias_id'],)).fetchone()
                if not relation or relation['grade'] not in ('A','B','C'):
                    raise ValueError('原称呼关系没有可信等级，禁止借原文强行通过')
                if relation['grade']!='A':
                    c.execute('''UPDATE relation_quality SET grade='A',source_id=?,basis_json=?,batch_id=?
                      WHERE alias_id=?''',(sid,canonical_json({'book_title':'舊唐書','revision':manifest['revision_id'],
                       'source_sha256':manifest['raw_sha256'],'text_sha256':manifest['text_sha256'],
                       'quote':case['quote'],'start':case['start'],'end':case['end']}),batch,case['alias_id']))
            if c.execute('PRAGMA integrity_check').fetchone()[0]!='ok' or c.execute('PRAGMA foreign_key_check').fetchall():
                raise ValueError('数据库完整性失败，整批取消')
            after={table:c.execute('SELECT count(*) FROM '+table).fetchone()[0] for table in before}
            if after['people']!=before['people'] or after['aliases']!=before['aliases'] or after['sources']-before['sources']!=2 or after['evidences']-before['evidences']!=2:
                raise ValueError('新增数量或原有人物/关系发生异常')
            c.execute('UPDATE quality_batches SET after_fingerprint=? WHERE batch_id=?',(fingerprint(c),batch))
            c.commit()
        except Exception:
            c.rollback();raise
    result={'status':'committed','batch_id':batch,'source_revision':manifest['revision_id'],
      'quoted_people':[r['name'] for r in manifest['cases']],
      'new_sources':2,'new_evidences':2,'counts_before':before,'counts_after':after,
      'backup':backup,'source_raw_sha256':manifest['raw_sha256'],'source_text_sha256':manifest['text_sha256']}
    json_write(output/'import_result.json',result)
    return result


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--db',default='data/guji.db')
    parser.add_argument('--output',default='outputs/classical-source/formal-import')
    parser.add_argument('--dry-run',action='store_true')
    args=parser.parse_args()
    db=Path(args.db).resolve();out=Path(args.output).resolve()
    if not db.exists():parser.error('existing database required')
    out.mkdir(parents=True,exist_ok=True)
    if args.dry_run:
        clone=out/'TEST_ONLY_REAL_DATABASE_COPY.db'
        if clone.exists():parser.error('dry run copy already exists; choose a new output directory')
        with closing(sqlite3.connect(db)) as src,closing(sqlite3.connect(clone)) as dst:src.backup(dst)
        result=apply(clone,out)
        result.update(dry_run=True,formal_database_writes=0)
        json_write(out/'dry_run.json',result)
    else:
        result=apply(db,out)
    print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':main()
