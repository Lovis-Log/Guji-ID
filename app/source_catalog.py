"""Read-only source pages and on-demand technical records.

All visibility decisions reuse the public catalog. No admission, identity
resolution, or database writes occur in this module.
"""
import json
from app.db import connect_db
from app.catalog import PUBLIC_CTE,prepare,get_person,ALIAS_TYPES,USAGE_RELATIONS
from app.field_quality import has_quality
from app.presentation import source_view,field_view,evidence_view,person_view
from app.classical_passage import passage_for

PUBLIC_FIELD = """f.grade IN ('A','B','C') AND p.verification_status='verified'
 AND safe_source_url(s.original_url)=1 AND NOT EXISTS (
 SELECT 1 FROM quality_quarantine q WHERE q.candidate_id=f.fact_id
 AND q.reason='source_value_conflicts_with_preserved_value')"""


def decode(value):
    try:return json.loads(value)
    except (ValueError,TypeError):return value


def _source_row(c,source_id):
    row=c.execute('SELECT * FROM sources WHERE source_id=? AND safe_source_url(original_url)=1',(source_id,)).fetchone()
    if not row:return None
    evidence=c.execute(PUBLIC_CTE+'''SELECT 1 FROM public_evidence e JOIN public_aliases a USING(alias_id)
      WHERE e.source_id=? LIMIT 1''',(source_id,)).fetchone()
    field=None
    if has_quality(c):
        field=c.execute('''SELECT 1 FROM field_facts f JOIN people p USING(person_id)
          JOIN sources s USING(source_id) WHERE f.source_id=? AND '''+PUBLIC_FIELD+' LIMIT 1',(source_id,)).fetchone()
    return dict(row) if evidence or field else None


def _classical_quotes(c, *, source_id=None, person_id=None, excluding=None, limit=8):
    """Only already-public, located ancient text; keep each supporting alias ID."""
    where=[];params=[]
    if source_id is not None:
        where.append('e.source_id=?');params.append(source_id)
    if person_id is not None:
        where.append('a.person_id=?');params.append(person_id)
    if excluding is not None:
        where.append('e.source_id!=?');params.append(excluding)
    rows=c.execute(PUBLIC_CTE+'''SELECT e.*,a.alias_id,a.alias_name,a.alias_type,a.person_id,
      p.canonical_name,p.dynasty FROM public_evidence e JOIN public_aliases a USING(alias_id)
      JOIN people p USING(person_id) WHERE '''+' AND '.join(where)+'''
      ORDER BY p.canonical_name,p.person_id,e.source_id,e.evidence_id LIMIT 120''',tuple(params)).fetchall()
    groups={}
    for row in rows:
        evidence=evidence_view(dict(row))
        view=evidence['presentation']
        if view['kind']!='classical_text' or not view['original_text']:
            continue
        located_url=row['original_url']
        source_commit=''
        if row['platform']=='Kanripo' and located_url.startswith('https://github.com/kanripo/'):
            try:
                loc=json.loads(row['notes']).get('locator',{})
                first,last=loc.get('line_start'),loc.get('line_end')
                source_commit=loc.get('commit') if isinstance(loc.get('commit'),str) else ''
                if isinstance(first,int) and isinstance(last,int) and first>0 and last>=first:
                    located_url=located_url.split('#',1)[0]+f'#L{first}-L{last}'
            except (ValueError,TypeError,AttributeError):
                pass
        key=(row['person_id'],row['source_id'],view['original_text'])
        if key not in groups:
            if len(groups)>=limit:
                break
            groups[key]={'text':view['original_text'],'person_id':row['person_id'],
                'person_name':row['canonical_name'],'dynasty':row['dynasty'],
                'source_id':row['source_id'],'book_title':row['book_title'],
                'volume':row['volume'],'chapter':row['chapter'],'platform':row['platform'],
                'edition':row['edition'],'original_url':located_url,'source_commit':source_commit,
                'passage':passage_for(dict(row),name=row['canonical_name'],
                    alias_name=row['alias_name'],alias_type=row['alias_type']),
                'relations':[]}
        item=groups[key]
        relation={'alias_id':row['alias_id'],'alias_name':row['alias_name'],
                  'type_label':ALIAS_TYPES[row['alias_type']],'evidence_id':row['evidence_id'],
                  'usage_label':USAGE_RELATIONS.get(row['usage_relation'],'关系待确认')}
        if relation not in item['relations']:
            item['relations'].append(relation)
    return list(groups.values())


def get_source(db_path,source_id,person_id=None,page=1,page_size=20):
    """Page source-to-person relationships without merging equal names."""
    person_id=person_id or None
    with connect_db(db_path) as c:
        prepare(c);source=_source_row(c,source_id)
        if not source:return None
        evidence_where='e.source_id=?'+(' AND a.person_id=?' if person_id else '')
        params=(source_id,person_id) if person_id else (source_id,)
        source['relation_count']=c.execute(PUBLIC_CTE+f'''SELECT count(DISTINCT a.alias_id)
           FROM public_evidence e JOIN public_aliases a USING(alias_id) WHERE {evidence_where}''',params).fetchone()[0]
        rels=c.execute(PUBLIC_CTE+f'''SELECT a.alias_id,a.alias_name,a.alias_type,a.person_id,p.canonical_name,p.dynasty,
          min(e.evidence_id) AS evidence_id,count(*) AS evidence_count,
          CASE WHEN count(DISTINCT e.usage_relation)=1 THEN min(e.usage_relation) ELSE 'mixed' END AS usage_relation
          FROM public_evidence e JOIN public_aliases a USING(alias_id) JOIN people p USING(person_id)
          WHERE {evidence_where} GROUP BY a.alias_id
          ORDER BY p.canonical_name,a.alias_type,a.alias_name,a.alias_id LIMIT ? OFFSET ?''',(*params,page_size,(page-1)*page_size)).fetchall()
        source['relations']=[]
        for r in rels:
            item=dict(r);item['type_label']=ALIAS_TYPES[item['alias_type']]
            item['usage_label']=USAGE_RELATIONS.get(item['usage_relation'],'多种材料关系，见证据详情')
            source['relations'].append(item)
        source['fields']=[];source['field_count']=0
        if has_quality(c):
            field_where=PUBLIC_FIELD+' AND f.source_id=? AND f.selected=1'+(' AND f.person_id=?' if person_id else '')
            source['field_count']=c.execute('''SELECT count(*) FROM field_facts f JOIN people p USING(person_id)
              JOIN sources s USING(source_id) WHERE '''+field_where,params).fetchone()[0]
            rows=c.execute('''SELECT f.*,p.canonical_name,s.book_title,s.original_url,s.edition,s.platform
              FROM field_facts f JOIN people p USING(person_id) JOIN sources s USING(source_id)
              WHERE '''+field_where+''' ORDER BY p.canonical_name,f.person_id,f.field LIMIT ? OFFSET ?''',(*params,page_size,(page-1)*page_size)).fetchall()
            source['fields']=[field_view(dict(r)) for r in rows]
        # Infer presentation only from actual visible evidence/fact metadata.
        kinds=set()
        for r in c.execute(PUBLIC_CTE+'''SELECT e.* FROM public_evidence e JOIN public_aliases a USING(alias_id)
           WHERE e.source_id=? LIMIT 8''',(source_id,)):
            kinds.add(evidence_view(dict(r))['presentation']['kind'])
        if has_quality(c):
            for r in c.execute('''SELECT f.*,s.book_title,s.original_url FROM field_facts f
              JOIN people p USING(person_id) JOIN sources s USING(source_id)
              WHERE f.source_id=? AND '''+PUBLIC_FIELD+' LIMIT 8',(source_id,)):
                kinds.add(field_view(dict(r))['presentation']['kind'])
        source['evidence_kinds']=sorted(kinds)
        source['selected_person_id']=person_id or ''
        source['page']=page
        source['total_pages']=max(1,(max(source['relation_count'],source['field_count'])+page_size-1)//page_size)
        people=c.execute(PUBLIC_CTE+'''SELECT DISTINCT p.person_id,p.canonical_name,p.dynasty,p.birth_year,p.death_year,p.native_place
          FROM public_evidence e JOIN public_aliases a USING(alias_id) JOIN people p USING(person_id)
          WHERE e.source_id=? ORDER BY p.canonical_name,p.person_id''',(source_id,)).fetchall()
        related={r['person_id']:dict(r) for r in people}
        if has_quality(c):
            for r in c.execute('''SELECT DISTINCT p.person_id,p.canonical_name,p.dynasty,p.birth_year,p.death_year,p.native_place FROM field_facts f
              JOIN people p USING(person_id) JOIN sources s USING(source_id)
              WHERE f.source_id=? AND '''+PUBLIC_FIELD,(source_id,)):
                related[r['person_id']]=dict(r)
        source['people_options']=[person_view(r) for r in sorted(related.values(),key=lambda p:(p['canonical_name'],p['person_id']))]
        source['classical_quotes']=_classical_quotes(c,source_id=source_id,person_id=person_id)
        source['related_classical_quotes']=[]
        if person_id and 'structured_database' in kinds:
            source['related_classical_quotes']=_classical_quotes(c,person_id=person_id,excluding=source_id,limit=4)
    return source_view(source)


def get_raw_record(db_path,record_type,record_id):
    """Return original stored columns only for a currently visible subject."""
    with connect_db(db_path) as c:
        prepare(c)
        if record_type=='source':
            return _source_row(c,record_id)
        if record_type=='evidence':
            visible=c.execute(PUBLIC_CTE+'''SELECT e.evidence_id FROM public_evidence e
              JOIN public_aliases a USING(alias_id) WHERE e.evidence_id=?''',(record_id,)).fetchone()
            if not visible:return None
            result=dict(c.execute('SELECT * FROM evidences WHERE evidence_id=?',(record_id,)).fetchone())
            if c.execute("SELECT 1 FROM sqlite_master WHERE name='automatic_evidence'").fetchone():
                extra=c.execute('SELECT record_json,locator_json FROM automatic_evidence WHERE evidence_id=?',(record_id,)).fetchone()
                if extra:
                    result['source_record']=decode(extra['record_json'])
                    result['source_record_locator']=decode(extra['locator_json'])
            return result
        if record_type=='field' and has_quality(c):
            row=c.execute('''SELECT f.* FROM field_facts f JOIN people p USING(person_id)
               JOIN sources s USING(source_id) WHERE f.fact_id=? AND '''+PUBLIC_FIELD,(record_id,)).fetchone()
            return dict(row) if row else None
    return None
