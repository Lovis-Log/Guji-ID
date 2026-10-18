"""Field-level quality, deterministic source mapping, incremental import and guarded rollback.

AI advises identity linking only. All accepted fields/relations are reproduced from
the licensed, hash-checked SQLite snapshot or a cached explicitly located source.
"""
import json
import re
import sqlite3
from collections import Counter
from contextlib import closing
from pathlib import Path
from app.collector import digest, identifier, now
from app.db import PROJECT_ROOT, connect_db
from app.importer import COLUMNS
from app.normalization import normalize_name

LABELS={'A':'原文证据核实','B':'结构化来源','C':'资料补充','D':'冲突与存疑'}
FIELDS={'canonical_name':'姓名','dynasty':'朝代','birth_year':'生年','death_year':'卒年','native_place':'籍贯','identity_description':'人物简介'}


def canonical_json(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'))


def search_key(value):
    from app.automatic_ingestion import search_key as key
    return key(value)


def json_write(path,value):
    from app.verification import json_write as write
    write(path,value)


def has_quality(c):
    return c.execute("SELECT 1 FROM sqlite_master WHERE name='field_facts'").fetchone() is not None


def migrate(db):
    sql=(PROJECT_ROOT/'app/migrations/0065_field_quality.sql').read_text(encoding='utf-8')
    with connect_db(db) as c:
        c.executescript('BEGIN IMMEDIATE;\n'+sql+'\nCOMMIT;')


def fingerprint(c):
    tables=[r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name!='quality_batches' ORDER BY name")]
    return digest(canonical_json({t:sorted([dict(r) for r in c.execute('SELECT * FROM "'+t+'"')],key=canonical_json) for t in tables}))


def insert(c,table,row):
    cols=list(row)
    c.execute(f"INSERT INTO {table}({','.join(cols)}) VALUES({','.join('?' for _ in cols)})",tuple(row.values()))


def quarantine(c,batch,key,person,field,reason,record):
    c.execute('INSERT OR IGNORE INTO quality_quarantine VALUES(?,?,?,?,?,?,?)',
       (str(key),person,field,'D',reason,canonical_json(record),batch))


def fact(c,batch,pid,field,value,grade,sid,version,record,locator,qualifier=''):
    if value is None or value=='':return
    current=c.execute('SELECT * FROM people WHERE person_id=?',(pid,)).fetchone()[field]
    selected=c.execute('SELECT * FROM field_facts WHERE person_id=? AND field=? AND selected=1',(pid,field)).fetchone()
    equivalent=current==value or (isinstance(current,str) and isinstance(value,str) and search_key(current)==search_key(value))
    fid=identifier('F65',pid,field,grade,sid,value,qualifier)
    if c.execute('SELECT 1 FROM field_facts WHERE fact_id=?',(fid,)).fetchone():return
    choose=current is None or equivalent
    if selected:
        if selected['grade'] <= grade:
            choose=False
        elif equivalent:
            c.execute('UPDATE field_facts SET selected=0 WHERE fact_id=?',(selected['fact_id'],))
        else:choose=False
    # Existing non-empty values without provenance are protected as well.
    if current is not None and not equivalent:
        quarantine(c,batch,fid,pid,field,'source_value_conflicts_with_preserved_value',{'incoming':value,'current':current,'source':record})
    insert(c,'field_facts',dict(fact_id=fid,person_id=pid,field=field,value_json=canonical_json(value),grade=grade,
        source_id=sid,source_version=version,record_json=canonical_json(record),locator_json=canonical_json(locator),
        qualifier=qualifier,selected=int(choose),batch_id=batch))
    if choose and current is None:
        c.execute('UPDATE people SET '+field+'=? WHERE person_id=?',(value,pid))


def relation_grade(c,aid,grade,sid,basis,batch):
    row=c.execute('SELECT grade FROM relation_quality WHERE alias_id=?',(aid,)).fetchone()
    if row and row[0]<=grade:return
    c.execute('INSERT OR REPLACE INTO relation_quality VALUES(?,?,?,?,?)',(aid,grade,sid,canonical_json(basis),batch))


def put_alias(c,batch,pid,name,kind,sid,version,raw,grade='B',quote=None):
    if not isinstance(name,str) or not 1<=len(name.strip())<=40 or name!=name.strip() or re.search(r'[\x00-\x1f\[\]()?？+□/；;]',name):
        quarantine(c,batch,identifier('D65',pid,name,kind),pid,'alias','invalid_alias',raw);return
    row=c.execute('SELECT alias_id FROM aliases WHERE person_id=? AND alias_type=? AND alias_name=?',(pid,kind,name)).fetchone()
    aid=row[0] if row else identifier('A65',pid,name,kind)
    if row is None:
        insert(c,'aliases',dict(alias_id=aid,person_id=pid,alias_name=name,normalized_name=normalize_name(name),alias_type=kind,verification_status='verified'))
    eid=identifier('E65',aid,sid,raw)
    if not c.execute('SELECT 1 FROM evidences WHERE evidence_id=?',(eid,)).fetchone():
        notes={'evidence_kind':'classical_text' if grade=='A' else 'structured_record','verification_method':'stage65',
            'quality_grade':grade,'source_version':version,'record_key':raw.get('key',{}),
            'relation_type':'来源直接记录的称呼关系','evidence_type':LABELS[grade],
            'remarks':'原始来源记录，模型回答不作为史实依据。'}
        insert(c,'evidences',dict(evidence_id=eid,alias_id=aid,source_id=sid,original_text=quote or canonical_json(raw),
          usage_relation='supports',evidence_nature='direct',verification_status='verified',notes=canonical_json(notes)))
    relation_grade(c,aid,grade,sid,raw,batch)
    return aid


def source_person(c,src,batch,pid,external,release,sid):
    from app.automatic_ingestion import TYPE_MAP
    p=dict(src.execute('SELECT * FROM BIOG_MAIN WHERE c_personid=?',(external,)).fetchone())
    version=release['version']
    record={'table':'BIOG_MAIN','key':{'c_personid':external},'fields':p}
    locator={'table':'BIOG_MAIN','c_personid':external,'sqlite_sha256':release['sqlite_sha256']}
    dynasty=(src.execute('SELECT c_dynasty_chn FROM DYNASTIES WHERE c_dy=?',(p['c_dy'],)).fetchone() or [None])[0]
    if dynasty in {'未詳','不詳','未知'}:dynasty=None
    for field,value in [('canonical_name',p['c_name_chn']),('dynasty',dynasty)]:
        fact(c,batch,pid,field,value,'B',sid,version,record,locator)
    by,dy=p.get('c_birthyear'),p.get('c_deathyear')
    if by and dy and by>dy:
        quarantine(c,batch,identifier('D65',pid,'years'),pid,'years','birth_after_death',record)
    else:
        ranges={-1:'之前',1:'之后',2:'约',300:'元豐五年前',301:'元豐五年後'}
        for field,col,rc in [('birth_year','c_birthyear','c_by_range'),('death_year','c_deathyear','c_dy_range')]:
            val=p.get(col)
            if isinstance(val,int) and val!=0 and -3000<=val<=2026:
                code=p.get(rc)
                if code not in (None,0,*ranges.keys()):
                    quarantine(c,batch,identifier('D65',pid,field),pid,field,'unknown_year_range',record);continue
                fact(c,batch,pid,field,val,'B',sid,version,record,{**locator,'column':col,'range_column':rc},ranges.get(code,''))
    # Actual typed address rows only. Never use c_index_addr_id as native place.
    addresses=[dict(r) for r in src.execute('''SELECT d.*,a.c_name_chn AS address_name,t.c_addr_desc_chn AS address_type
      FROM BIOG_ADDR_DATA d JOIN ADDR_CODES a USING(c_addr_id) JOIN BIOG_ADDR_CODES t USING(c_addr_type)
      WHERE d.c_personid=? AND d.c_addr_type IN (1,7)''',(external,))]
    unique={a['address_name'] for a in addresses if a['address_name'] and a['address_name'] not in {'未詳','不詳'}}
    if len(unique)==1:
        fact(c,batch,pid,'native_place',next(iter(unique)),'B',sid,version,{'table':'BIOG_ADDR_DATA','rows':addresses},
             {'tables':['BIOG_ADDR_DATA','BIOG_ADDR_CODES','ADDR_CODES'],'c_personid':external},'来源所记籍贯（基本地址或本贯），非出生地')
    elif len(unique)>1:
        quarantine(c,batch,identifier('D65',pid,'native_place'),pid,'native_place','multiple_native_addresses',addresses)
    for r in src.execute('SELECT * FROM ALTNAME_DATA WHERE c_personid=?',(external,)):
        a=dict(r);code=a['c_alt_name_type_code'];kind=TYPE_MAP.get(code,('',None))[1]
        type_row=src.execute('SELECT c_name_type_desc_chn FROM ALTNAME_CODES WHERE c_name_type_code=?',(code,)).fetchone()
        if kind is None or not type_row or type_row[0]!=TYPE_MAP[code][0]:
            quarantine(c,batch,identifier('D65',external,a),pid,'alias','unknown_or_conflicting_type',a);continue
        if a['c_alt_name_chn']==p['c_name_chn'] and kind!='name':
            quarantine(c,batch,identifier('D65',external,a),pid,'alias','canonical_name_conflicts_with_type',a);continue
        ref=src.execute('SELECT c_textid,c_title_chn FROM TEXT_CODES WHERE c_textid=?',(a.get('c_source'),)).fetchone()
        if a.get('c_source') and not ref:
            quarantine(c,batch,identifier('D65',external,a),pid,'alias','dangling_bibliographic_source',a);continue
        raw={'table':'ALTNAME_DATA','key':{'c_personid':external,'c_alt_name_chn':a['c_alt_name_chn'],'c_alt_name_type_code':code},
             'row':a,'type_label':type_row[0],'bibliography':dict(ref) if ref else None}
        put_alias(c,batch,pid,a['c_alt_name_chn'],kind,sid,version,raw)


def build_terms(c):
    c.execute('DELETE FROM catalog_terms')
    for row in c.execute("SELECT person_id,canonical_name FROM people WHERE verification_status='verified'").fetchall():
        for term in {normalize_name(row[1]),search_key(row[1])}:
            c.execute('INSERT OR IGNORE INTO catalog_terms VALUES(?,?,?,?,?)',(term,row[0],None,row[1],'NFC/OpenCC t2s; retrieval only'))
    for row in c.execute("SELECT a.* FROM aliases a JOIN relation_quality q USING(alias_id) JOIN people p USING(person_id) WHERE a.verification_status='verified' AND p.verification_status='verified'").fetchall():
        for term in {normalize_name(row['alias_name']),search_key(row['alias_name'])}:
            c.execute('INSERT OR IGNORE INTO catalog_terms VALUES(?,?,?,?,?)',(term,row['person_id'],row['alias_id'],row['alias_name'],'NFC/OpenCC t2s; retrieval only'))


def enrich_fields(c,people):
    for p in people:
        p['field_sources']={}
        if not has_quality(c):continue
        for f in c.execute('''SELECT f.*,s.book_title,s.original_url FROM field_facts f JOIN sources s USING(source_id)
            WHERE person_id=? AND grade!='D' AND NOT EXISTS (
              SELECT 1 FROM quality_quarantine q WHERE q.candidate_id=f.fact_id
              AND q.reason='source_value_conflicts_with_preserved_value')
            ORDER BY selected DESC,grade,fact_id''',(p['person_id'],)):
            item=dict(f);item.update(label=FIELDS[f['field']],grade_label=LABELS[f['grade']],value=json.loads(f['value_json']))
            p['field_sources'].setdefault(f['field'],[]).append(item)
        for field in ('birth_year','death_year'):
            selected=next((f for f in p['field_sources'].get(field,[]) if f['selected']),None)
            if selected and selected['qualifier']:
                key='birth_label' if field=='birth_year' else 'death_label'
                p[key]=(selected['qualifier']+p[key]) if selected['qualifier']=='约' else (p[key]+selected['qualifier'])
        if p['birth_year'] is not None or p['death_year'] is not None:
            p['lifespan']=' 至 '.join(v for v in (p['birth_label'] if p['birth_year'] is not None else '生年待考',p['death_label'] if p['death_year'] is not None else '卒年待考'))
        rows=c.execute("SELECT external_id FROM quality_identity WHERE person_id=? AND provider='CBDB'",(p['person_id'],)).fetchall()
        if not rows and c.execute("SELECT 1 FROM sqlite_master WHERE name='external_people'").fetchone():
            rows=c.execute("SELECT external_id FROM external_people WHERE person_id=? AND provider='CBDB'",(p['person_id'],)).fetchall()
        if rows:p['source_person_id']=rows[0][0]
        grades={f['grade'] for facts in p['field_sources'].values() for f in facts if f['selected']}
        p['verification_label']='姓名与来源可追溯' if grades else '已收录身份；基础资料待补'
        if p.get('source_person_id'):
            p['structured_aliases']=[dict(a) for a in c.execute('''SELECT a.alias_name,a.alias_type FROM aliases a
                JOIN relation_quality q USING(alias_id) WHERE a.person_id=? AND a.alias_type IN ('courtesy','art')
                AND a.verification_status='verified' ORDER BY a.alias_type,a.alias_id LIMIT 4''',(p['person_id'],))]
        p['field_conflicts']=[dict(r) for r in c.execute("SELECT field,reason FROM quality_quarantine WHERE person_id=? AND field IN ('dynasty','birth_year','death_year','native_place')",(p['person_id'],))]
    return people


def apply_enrichment(db,source,release,core,identities,held,output,ai=None,supplement=None):
    from app.automatic_ingestion import inspect_source
    from app.batch_import import backup_database
    inspect_source(source,release)
    out=Path(output);out.mkdir(parents=True,exist_ok=True)
    manifest=digest(canonical_json({'release':release,'core':core,'identities':identities,'held':held,'ai':ai,'supplement':supplement,'policy':'stage65-v1'}))
    batch='ST65-'+manifest[:20]
    migrate(db)
    with connect_db(db) as c:
        prior=c.execute('SELECT status FROM quality_batches WHERE batch_id=?',(batch,)).fetchone()
        if prior and prior[0]=='committed':return {'batch_id':batch,'cache_reused':True,'inserted':0}
    backup=backup_database(db,out/'backups')
    issues=[];mapping={}
    with connect_db(db) as c,connect_db(source) as src:
        c.execute('BEGIN IMMEDIATE')
        try:
            insert(c,'quality_batches',dict(batch_id=batch,imported_at=now(),manifest_sha256=manifest,backup_json=canonical_json(backup),status='committed',after_fingerprint=None))
            sid='S-CBDB65-'+release['version']
            if not c.execute('SELECT 1 FROM sources WHERE source_id=?',(sid,)).fetchone():
                insert(c,'sources',dict(source_id=sid,book_title='中国历代人物传记资料库（CBDB）',volume=release['version'],chapter='BIOG_MAIN / ALTNAME_DATA / BIOG_ADDR_DATA',platform='CBDB 官方发布',original_url=release['download_url'],edition='CBDB '+release['version']+' / CC BY-NC-SA 4.0'))
            mapping={int(r['external_id']):r['person_id'] for r in c.execute("SELECT * FROM external_people WHERE provider='CBDB'")}
            for r in c.execute("SELECT * FROM quality_identity WHERE provider='CBDB'"):
                if r['external_id'] in mapping and mapping[r['external_id']]!=r['person_id']:raise ValueError('conflicting stable external identity')
                mapping[r['external_id']]=r['person_id']
            judgments={r['issue']:r for r in (ai or {}).get('findings',[])}
            for task in identities:
                j=judgments.get(task['id'],{})
                candidates=task['candidates']
                selected=[p for p in candidates if p['c_personid']==j.get('source_id')]
                # Exact source alias corroboration is mandatory even after supported AI judgment.
                anchors={search_key(a) for a in task['aliases']}
                valid=(j.get('judgment')=='supported' and len(selected)==1 and search_key(selected[0]['c_name_chn'])==search_key(task['name'])
                    and any(search_key(a['c_alt_name_chn']) in anchors for a in selected[0]['aliases']))
                # A failed model call is never a positive review. Independent deterministic
                # name + typed alias + located biography chapter + dynasty matching is allowed.
                refs=[dict(r) for r in c.execute('''SELECT DISTINCT s.* FROM aliases a JOIN evidences e USING(alias_id)
                   JOIN sources s USING(source_id) WHERE a.person_id=? AND e.verification_status='verified' ''',(task['id'],))]
                eras={'舊唐書':'唐','新唐書':'唐','宋史':'宋'}
                deterministic=[p for p in candidates if search_key(p['c_name_chn'])==search_key(task['name'])
                  and any(search_key(a['c_alt_name_chn']) in anchors for a in p['aliases'])
                  and any(eras.get(s['book_title'])==p['dynasty'] and search_key(s['chapter']) in
                    {search_key(task['name']),*[search_key(a['c_alt_name_chn']) for a in p['aliases'] if a['c_alt_name_type_code']==3]} for s in refs)]
                if not j and len(deterministic)==1:
                    selected=deterministic;valid=True
                ext=selected[0]['c_personid'] if valid else None
                if valid and (ext not in mapping or mapping[ext]==task['id']):
                    mapping[ext]=task['id']
                    if not c.execute("SELECT 1 FROM quality_identity WHERE provider='CBDB' AND external_id=?",(ext,)).fetchone():
                        insert(c,'quality_identity',dict(provider='CBDB',external_id=ext,person_id=task['id'],basis_json=canonical_json({'task':task,'advice':j,'deterministic_multi_field_match':not bool(j)}),batch_id=batch))
                else:
                    quarantine(c,batch,task['id'],task['id'],'identity_link','identity_requires_corroboration',{'task':task,'advice':j})
            # Core coverage is a priority queue, not a numeric prefix. All independently identified homonyms stay separate.
            coverage=[]
            for test in core:
                candidates=[p for p in test['candidates'] if search_key(test['expected_era']) in search_key(p['dynasty'])]
                # The familiar query may be an alternate name of the intended
                # author (e.g. 溫庭筠 under canonical 溫廷筠). Preserve all source
                # identities matching name OR typed alias and era, without merging.
                chosen=candidates
                if not chosen:issues.append({'query':test['query'],'reason':'no_unambiguous_source_name_and_era'});continue
                for p in chosen:
                    ext=p['c_personid']
                    if ext not in mapping:
                        # Never create a duplicate legacy identity merely to bypass a held link.
                        old=c.execute("SELECT person_id,canonical_name FROM people WHERE person_id NOT LIKE 'P-CBDB-%'").fetchall()
                        if any(search_key(o['canonical_name'])==search_key(p['c_name_chn']) for o in old):continue
                        pid=f'P-CBDB-{ext}'
                        insert(c,'people',dict(person_id=pid,canonical_name=p['c_name_chn'],verification_status='verified'))
                        mapping[ext]=pid
                        insert(c,'quality_identity',dict(provider='CBDB',external_id=ext,person_id=pid,basis_json=canonical_json({'priority_query':test['query'],'source_person_id':ext}),batch_id=batch))
                    coverage.append({'query':test['query'],'person_id':mapping[ext],'source_person_id':ext})
            # Label all existing accepted relations using their actual evidence kind, without changing old evidence rows.
            for a in c.execute("SELECT a.alias_id,e.source_id,e.notes FROM aliases a JOIN evidences e USING(alias_id) WHERE a.verification_status='verified' AND e.verification_status='verified' AND e.usage_relation='supports' AND e.evidence_nature='direct'").fetchall():
                try:n=json.loads(a['notes'] or '{}')
                except ValueError:n={}
                grade=n.get('quality_grade') if isinstance(n,dict) else None
                if grade not in {'A','B','C'}:
                    grade='B' if isinstance(n,dict) and n.get('evidence_kind')=='structured_record' else 'A'
                relation_grade(c,a['alias_id'],grade,a['source_id'],{'existing_evidence':True},batch)
            # Extract only explicit native-place strings from already located original quotes.
            for e in c.execute('''SELECT DISTINCT p.person_id,p.canonical_name,e.original_text,s.* FROM people p
              JOIN aliases a USING(person_id) JOIN evidences e USING(alias_id) JOIN sources s USING(source_id)
              WHERE p.person_id NOT LIKE 'P-CBDB-%' AND e.verification_status='verified' ''').fetchall():
                era={'舊唐書':'唐','新唐書':'唐','宋史':'宋'}.get(e['book_title'])
                if not era:continue
                basis={'original_text':e['original_text'],'book_title':e['book_title'],'volume':e['volume'],'chapter':e['chapter']}
                loc={'volume':e['volume'],'chapter':e['chapter']}
                fact(c,batch,e['person_id'],'canonical_name',e['canonical_name'],'A',e['source_id'],e['edition'] or '',basis,loc,'已核对传记身份及篇目定位；姓名保持原规范写法')
                fact(c,batch,e['person_id'],'dynasty',era,'A',e['source_id'],e['edition'] or '',basis,loc,'传记所属正史朝代')
                m=re.match(r'^([^，]+)，字[^，]+，([^，。]+?)人(?:也)?[，。]',e['original_text'])
                if m and search_key(m[1])==search_key(e['canonical_name']):
                    fact(c,batch,e['person_id'],'native_place',m[2],'A',e['source_id'],e['edition'] or '',basis,loc)
            for ext,pid in mapping.items():source_person(c,src,batch,pid,ext,release,sid)
            if supplement:
                # Explicit author-page name declaration, corroborated by canonical name + both dates.
                matches=[]
                for ext,pid in mapping.items():
                    row=src.execute('SELECT c_name_chn,c_birthyear,c_deathyear FROM BIOG_MAIN WHERE c_personid=?',(ext,)).fetchone()
                    if tuple(row)==(supplement['canonical_source_name'],supplement['birth_year'],supplement['death_year']):matches.append((ext,pid))
                if len(matches)!=1:raise ValueError('supplement identity ambiguous')
                ext,pid=matches[0];ss=supplement['source_id']
                if not c.execute('SELECT 1 FROM sources WHERE source_id=?',(ss,)).fetchone():
                    insert(c,'sources',dict(source_id=ss,book_title=supplement['source_title'],volume=supplement['version'],chapter='作者资料（非古籍原文）',platform='维基文库',original_url=supplement['url'],edition='CC BY-SA 4.0'))
                put_alias(c,batch,pid,supplement['alias'],'name',ss,supplement['version'],{'table':'author_page','key':supplement['locator'],'record':supplement['identity_description']},'C')
                fact(c,batch,pid,'identity_description',supplement['identity_description'],'C',ss,supplement['version'],{'text':supplement['identity_description']},supplement['locator'])
            # Re-evaluate all 38 held candidates. Source identity plus exact typed alias required.
            legacy_results=[]
            for a in held:
                pid=a['person_id'];name=a['alias_name'];kind=a['alias_type']
                match=c.execute('SELECT a.alias_id,q.grade FROM aliases a JOIN relation_quality q USING(alias_id) WHERE a.person_id=? AND a.alias_name=? AND a.alias_type=?',(pid,name,kind)).fetchone()
                if match:
                    status='supported_existing_source_relation'
                else:
                    status='D_requires_located_relation_or_identity'
                    quarantine(c,batch,a['alias_id'],pid,'legacy_alias',status,a)
                legacy_results.append({'candidate_id':a['alias_id'],'person_id':pid,'alias_name':name,'result':status,'resolved_alias_id':match[0] if match else None})
            build_terms(c)
            if c.execute('PRAGMA integrity_check').fetchone()[0]!='ok' or c.execute('PRAGMA foreign_key_check').fetchall():raise ValueError('database integrity failure')
            c.execute('UPDATE quality_batches SET after_fingerprint=? WHERE batch_id=?',(fingerprint(c),batch))
            c.commit()
        except Exception:c.rollback();raise
    result={'batch_id':batch,'cache_reused':False,'mapped_people':len(mapping),'core_missing':issues,'backup':backup,'coverage':coverage,'legacy38':legacy_results}
    json_write(out/'import_result.json',result)
    return result


def rollback(db,batch,output):
    """Restore the exact pre-batch SQLite backup only if no later data changes exist."""
    from app.batch_import import backup_database
    from app.source_acquisition import file_hash
    with connect_db(db) as c:
        row=c.execute('SELECT * FROM quality_batches WHERE batch_id=? AND status=\'committed\'',(batch,)).fetchone()
        if not row:raise ValueError('committed batch not found')
        if fingerprint(c)!=row['after_fingerprint']:raise ValueError('later data changed; full snapshot rollback refused')
        backup=json.loads(row['backup_json'])
    if file_hash(backup['path'])!=backup['sha256']:raise ValueError('backup digest mismatch')
    recovery=backup_database(db,Path(output)/'before_rollback')
    with closing(sqlite3.connect(backup['path'])) as src,closing(sqlite3.connect(db)) as dest:src.backup(dest)
    json_write(Path(output)/'rollback_receipt.json',{'batch_id':batch,'restored_backup':backup,'recovery_backup':recovery,'rolled_back_at':now()})
    return backup
