"""Read-only diagnosis, baseline snapshot and one bounded external-review input."""
import csv
import json
import sqlite3
from pathlib import Path
from app.automatic_ingestion import search_key, canonical_json
from app.db import PROJECT_ROOT, connect_db
from app.verification import json_write
from opencc import OpenCC

OUT = PROJECT_ROOT / 'outputs/stage65-release'
SOURCE = PROJECT_ROOT / 'data/external/cbdb-20250520/cbdb.sqlite3'


def source_candidates(src, name):
    names = tuple({name, OpenCC('s2t').convert(name), search_key(name)})
    marks = ','.join('?' for _ in names)
    rows = src.execute(f'''SELECT DISTINCT b.* FROM BIOG_MAIN b WHERE b.c_name_chn IN ({marks})
        OR b.c_personid IN (SELECT c_personid FROM ALTNAME_DATA WHERE c_alt_name_chn IN ({marks}))''', names * 2)
    result = []
    for row in rows:
        p = dict(row)
        p['dynasty'] = (src.execute('SELECT c_dynasty_chn FROM DYNASTIES WHERE c_dy=?', (p['c_dy'],)).fetchone() or [''])[0]
        p['aliases'] = [dict(a) for a in src.execute('SELECT c_alt_name_chn,c_alt_name_type_code,c_source,c_pages FROM ALTNAME_DATA WHERE c_personid=?', (p['c_personid'],))]
        result.append(p)
    return result


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    with connect_db(PROJECT_ROOT / 'data/guji.db') as c, connect_db(SOURCE) as src:
        baseline = {t: [dict(r) for r in c.execute('SELECT * FROM '+t)] for t in ('people','aliases','sources','evidences')}
        if not (OUT/'baseline.json').exists():
            json_write(OUT/'baseline.json', baseline)
        legacy = [p for p in baseline['people'] if not p['person_id'].startswith('P-CBDB-')]
        core = []
        for name, era in json.loads((PROJECT_ROOT/'data/core_people_stage65.json').read_text(encoding='utf-8')):
            candidates = source_candidates(src, name)
            core.append({'query': name, 'expected_era': era, 'candidates': candidates})
        json_write(OUT/'core_source_candidates.json',core)
        identities=[]
        for p in legacy:
            aliases=[a['alias_name'] for a in baseline['aliases'] if a['person_id']==p['person_id'] and a['alias_type']!='name']
            candidates=source_candidates(src,p['canonical_name'])
            matches=[x for x in candidates if any(search_key(a['c_alt_name_chn']) in {search_key(v) for v in aliases} for a in x['aliases'])]
            quotes=[{'text':e['original_text'][:160], 'source_id':e['source_id']} for e in baseline['evidences'] if any(a['alias_id']==e['alias_id'] and a['person_id']==p['person_id'] for a in baseline['aliases'])][:1]
            identities.append({'id':p['person_id'],'field':'identity_link','name':p['canonical_name'],'aliases':aliases,'quotes':quotes,
                'candidates':[{k:x[k] for k in ('c_personid','c_name_chn','dynasty','c_birthyear','c_deathyear','aliases')} for x in matches or candidates][:5]})
        json_write(OUT/'legacy_identity_candidates.json',identities)
        # The 38 historical held relations are the original 55 minus the 17 admitted in stage 5.
        originals={}
        for folder in ('2026-10-16-delivery','stage4-review-input'):
            originals.update({a['alias_id']:a for a in csv.DictReader((PROJECT_ROOT/f'data/collections/{folder}/aliases_pending.csv').open(encoding='utf-8-sig'))})
        held=[a for a in originals.values() if a['alias_id'] not in {v['alias_id'] for v in baseline['aliases']}]
        json_write(OUT/'legacy38.json',held)
        # 10 identity decisions + 10 semantic checks fit the installed bridge's 2200-token response cap.
        tasks=list(identities)
        for a in held[:10]:
            tasks.append({'id':a['alias_id'],'field':'alias_semantics','person_id':a['person_id'],'name':a['person_name'],'alias':a['alias_name'],'type':a['alias_type'],'text':a['discovery_text'][:220],'url':a['discovery_url']})
        json_write(OUT/'deepseek_input.json',tasks)
        summary={'formal_li_bai':sum(p['canonical_name']=='李白' for p in baseline['people']),
            'source_li_bai':[p['c_personid'] for p in source_candidates(src,'李白')],
            'baseline_counts':{t:len(v) for t,v in baseline.items()},
            'baseline_fields':{f:sum(p[f] is not None for p in baseline['people']) for f in ('dynasty','birth_year','death_year','native_place')},
            'historical_held':len(held),'core_candidates':{r['query']:len(r['candidates']) for r in core},
            'root_causes':['stage_records orders ALTNAME_DATA by c_personid and applies LIMIT 1200; target_people=400 further truncates selection before CBDB 32540 (李白)',
             'materialize copies dynasty only; c_birthyear/c_deathyear and BIOG_ADDR_DATA never mapped; legacy 10 have no external ID link',
             'catalog supports exact canonical names and selected alias variants only; no normalized canonical index or fuzzy search',
             'structured evidence already passed through JSON in original_text; visibility lacks explicit field-level A/B/C/D semantics']}
        json_write(OUT/'diagnosis.json',summary)
        print(json.dumps(summary,ensure_ascii=False))


if __name__=='__main__': main()
