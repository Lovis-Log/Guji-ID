"""Generate measured Stage 6.5 quality/audit metrics from the actual formal DB."""
import json
import hashlib
import os
from pathlib import Path
from collections import Counter
from app.db import PROJECT_ROOT,connect_db
from app.catalog import search_people,public_counts
from app.automatic_ingestion import search_key
from app.verification import json_write
from app.source_acquisition import file_hash

OUT=PROJECT_ROOT/'outputs/stage65-release'


def main():
    baseline=json.loads((OUT/'baseline.json').read_text(encoding='utf-8'))
    db=PROJECT_ROOT/'data/guji.db'
    with connect_db(db) as c:
        current=[dict(r) for r in c.execute('SELECT * FROM people')]
        by_id={p['person_id']:p for p in current};old_ids={p['person_id'] for p in baseline['people']}
        old_now=[by_id[pid] for pid in old_ids]
        selected={(r['person_id'],r['field']) for r in c.execute('SELECT person_id,field FROM field_facts WHERE selected=1')}
        def coverage(people,aliases,live=False):
            n=len(people);ids={p['person_id'] for p in people}
            courtesy={a['person_id'] for a in aliases if a['alias_type'] in {'courtesy','art'} and a['verification_status']=='verified'}
            valid={a['person_id'] for a in aliases if a['alias_type']!='name' and a['verification_status']=='verified'}
            result={'people':n}
            for field in ('dynasty','birth_year','death_year','native_place'):
                count=sum(p[field] is not None for p in people);result[field]={'count':count,'percent':round(count/n*100,2)}
            for field,count in [('both_years',sum(p['birth_year'] is not None and p['death_year'] is not None for p in people)),
               ('either_year',sum(p['birth_year'] is not None or p['death_year'] is not None for p in people)),
               ('courtesy_or_art',len(ids&courtesy)),('valid_noncanonical_alias',len(ids&valid))]:
                result[field]={'count':count,'percent':round(count/n*100,2)}
            filled=[]
            for p in people:
                for field in ('canonical_name','dynasty','birth_year','death_year','native_place','identity_description'):
                    if p[field] is not None and not (field=='identity_description' and p[field].startswith('CBDB人物ID')):filled.append((p['person_id'],field))
            traced=sum(key in selected for key in filled) if live else 0
            result['explicit_field_provenance']={'populated_fields':len(filled),'with_field_level_provenance':traced,'percent':round(traced/len(filled)*100,2)}
            result['real_biography_count']=sum(bool(p['identity_description']) and not p['identity_description'].startswith('CBDB人物ID') for p in people)
            return result
        aliases=[dict(r) for r in c.execute('SELECT * FROM aliases')]
        field_grades=dict(c.execute('SELECT grade,count(*) FROM field_facts GROUP BY grade').fetchall())
        relation_grades=dict(c.execute('SELECT grade,count(*) FROM relation_quality GROUP BY grade').fetchall())
        selected_grades=dict(c.execute('SELECT grade,count(*) FROM field_facts WHERE selected=1 GROUP BY grade').fetchall())
        quarantine=[dict(r) for r in c.execute('SELECT * FROM quality_quarantine')]
        json_write(OUT/'quarantine.json',quarantine)
        result={'stage':'6.5','db_sha256':file_hash(db),'counts':{t:c.execute('SELECT count(*) FROM '+t).fetchone()[0] for t in ('people','aliases','sources','evidences')},
           'new_people':len(current)-len(baseline['people']),'new_aliases':len(aliases)-len(baseline['aliases']),
           'original_cohort_before':coverage(baseline['people'],baseline['aliases']),
           'original_cohort_after':coverage(old_now,aliases,True),'all_people_after':coverage(current,aliases,True),
           'field_observation_grades':field_grades,'selected_field_grades':selected_grades,'relation_grades':relation_grades,
           'quality_record_grades':{g:selected_grades.get(g,0)+relation_grades.get(g,0) for g in ('A','B','C')},
           'quarantine_records':len(quarantine),'quarantine_reasons':dict(Counter(q['reason'] for q in quarantine)),
           'integrity_check':c.execute('PRAGMA integrity_check').fetchone()[0],
           'foreign_key_errors':[tuple(r) for r in c.execute('PRAGMA foreign_key_check')],
           'source_version':'20250520','supplement_source':'Wikisource oldid=2307897',
           'field_provenance_baseline_note':'0 means no dedicated per-field provenance rows; original CBDB/quote evidence was already retained and is not claimed absent.',
           'grade_count_definition':'A/B/C: selected public field values plus independently graded alias relations; D: this run quarantine records. Additional unselected source observations are listed separately and are not counted as confirmed facts.'}
        result['quality_record_grades']['D']=len(quarantine)
        result['legacy38_results']=dict(Counter(r['result'] for r in json.loads((OUT/'formal-import/import_result.json').read_text(encoding='utf-8'))['legacy38']))
    core=[]
    expected_ids=json.loads((PROJECT_ROOT/'data/core_people_stage65_ids.json').read_text(encoding='utf-8'))
    for name,era in json.loads((PROJECT_ROOT/'data/core_people_stage65.json').read_text(encoding='utf-8')):
        rows=search_people(db,name);matched=[p for p in rows if search_key(era) in search_key(p['dynasty'] or '')]
        with connect_db(db) as c:
            expected=c.execute("SELECT person_id FROM external_people WHERE provider='CBDB' AND external_id=? UNION SELECT person_id FROM quality_identity WHERE provider='CBDB' AND external_id=?",(str(expected_ids[name]),expected_ids[name])).fetchone()
        passed=bool(expected) and expected[0] in {p['person_id'] for p in matched}
        core.append({'query':name,'expected_era':era,'expected_cbdb_id':expected_ids[name],'passed':passed,'matched_person_ids':[p['person_id'] for p in matched],'reason':'' if passed else 'expected source identity not in public search results'})
    result['core50']={'passed':sum(p['passed'] for p in core),'total':len(core),'percent':sum(p['passed'] for p in core)/len(core)*100,'rows':core}
    json_write(OUT/'core50_results.json',core)
    receipts=[]
    for f in sorted(OUT.glob('deepseek_receipt*.json')):receipts.append(json.loads(f.read_text(encoding='utf-8')))
    cases={r['id'] for r in json.loads((OUT/'deepseek_input.json').read_text(encoding='utf-8'))}
    judgments=[]
    recovered=OUT/'deepseek_recovered_response.json'
    if recovered.exists() and (OUT/'deepseek_recovered_validation.json').exists():judgments.extend(json.loads(recovered.read_text(encoding='utf-8'))['analysis']['findings'])
    for folder in ('deepseek-cases','deepseek-cases20','deepseek-enriched20'):
        directory=OUT/folder
        if (directory/'receipt.json').exists():
            receipts.append(json.loads((directory/'receipt.json').read_text(encoding='utf-8')))
            cases.update(r['id'] for r in json.loads((directory/'input.json').read_text(encoding='utf-8')))
        if (directory/'validated.json').exists():judgments.extend(json.loads((directory/'validated.json').read_text(encoding='utf-8'))['findings'])
    state_path=Path(os.environ.get('CODEX_HOME',str(Path.home()/'.codex')))/'claude-bridge/state'/(hashlib.sha256(b'guji-id-stage65-20261017').hexdigest()+'.json')
    ledger=json.loads(state_path.read_text(encoding='utf-8-sig'))
    json_write(OUT/'deepseek_ledger_snapshot.json',ledger)
    call_statuses={str(e['calls']):e['code'] for e in ledger['events'] if e.get('code') in {'OK','CLI_ERROR','CLI_TIMEOUT','CLI_RESULT_ERROR'} and e.get('calls')}
    covered={r.get('calls_used') for r in receipts}
    result['deepseek']={'wrapper_invocations':ledger['calls'],'sealed_project_batches':len(receipts),'record_attempts':sum(r['records'] for r in receipts),
      'distinct_records':len(cases),'successful_records':sum(r['successful_records'] for r in receipts),'failed_record_attempts':sum(r['failed_records'] for r in receipts),
      'successful_batches':sum(r['status']=='completed' for r in receipts),'successful_judgments':dict(Counter(r['judgment'] for r in judgments)),
      'call_statuses':call_statuses,'repair_calls_without_sealed_project_receipt':[int(n) for n in call_statuses if int(n) not in covered],
      'model_results_available':any(r['successful_records']>0 for r in receipts),'cost_usd':None,'cost_status':'Actual billed cost unavailable. Token usage retained where returned; failed and timeout usage unknown.',
      'available_token_usage':{k:sum(r.get('usage',{}).get(k,0) for r in receipts) for k in ('input_tokens','output_tokens','cache_read_input_tokens','cache_creation_input_tokens')},
      'receipts':receipts,'calls_used':ledger['calls'],'max_calls':max(5,ledger.get('authorization_base_limit',5)+ledger.get('authorized_extra_calls',0)),
      'stopped_after_failure':ledger['stopped']}
    result['public_counts']=public_counts(db)
    result['overall_acceptance']='passed' if result['deepseek']['model_results_available'] and result['core50']['percent']>=90 and result['integrity_check']=='ok' else 'incomplete'
    json_write(OUT/'quality_report.json',result)
    print(json.dumps({k:result[k] for k in ('counts','new_people','original_cohort_after','quality_record_grades')},ensure_ascii=False))


if __name__=='__main__':main()
