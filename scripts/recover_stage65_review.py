"""Validate a user-recovered real bridge result; no paid/model calls."""
import hashlib
import json
import os
from collections import Counter
from pathlib import Path
from app.db import PROJECT_ROOT,connect_db
from app.collector import digest,plain
from app.automatic_ingestion import search_key
from app.verification import json_write
from scripts.diagnose_stage65 import OUT,SOURCE


def main():
    value=json.loads((OUT/'deepseek_recovered_response.json').read_text(encoding='utf-8'))
    tasks=json.loads((OUT/'deepseek_input.json').read_text(encoding='utf-8'));findings=value['analysis']['findings']
    assert value['ok'] is True and len(findings)==len(tasks)==20
    assert [(r['issue'],r['field']) for r in findings]==[(r['id'],r['field']) for r in tasks]
    root=Path(os.environ.get('CODEX_HOME',str(Path.home()/'.codex')))
    ledger=json.loads((root/'claude-bridge/state'/(hashlib.sha256(b'guji-id-stage65-20261017').hexdigest()+'.json')).read_text(encoding='utf-8-sig'))
    event=next(e for e in ledger['events'] if e.get('calls')==value['calls_used'] and e.get('code')=='OK')
    checks=[]
    originals={r['alias_id']:r for r in json.loads((OUT/'legacy38.json').read_text(encoding='utf-8'))}
    with connect_db(SOURCE) as src:
        for task,result in zip(tasks,findings):
            assert result['judgment'] in {'supported','uncertain','conflict'} and isinstance(result['evidence'],str) and isinstance(result['risk'],str)
            if task['field']=='identity_link':
                if result['judgment']=='supported':
                    selected=[p for p in task['candidates'] if p['c_personid']==result['source_id']];assert len(selected)==1
                    p=selected[0];actual=src.execute('SELECT c_name_chn FROM BIOG_MAIN WHERE c_personid=?',(result['source_id'],)).fetchone()
                    assert actual and search_key(actual[0])==search_key(task['name'])
                    actual_aliases={search_key(r[0]) for r in src.execute('SELECT c_alt_name_chn FROM ALTNAME_DATA WHERE c_personid=?',(result['source_id'],))}
                    assert actual_aliases & {search_key(a) for a in task['aliases']}
                else:assert result['source_id'] is None
                checks.append({'id':task['id'],'passed':True,'method':'stable_source_id_name_and_alias_corroboration','judgment':result['judgment']})
            else:
                original=originals[task['id']];found=False
                for folder in ('2026-10-16-delivery','stage4-review-input','stage4-tang-song-20'):
                    raw=PROJECT_ROOT/'data/collections'/folder/original['discovery_raw_path'];meta=raw.with_suffix('.json')
                    if not raw.exists() or not meta.exists():continue
                    body=raw.read_bytes();metadata=json.loads(meta.read_text(encoding='utf-8'))
                    assert digest(body)==metadata['sha256']
                    parsed=json.loads(body.decode('utf-8')).get('parse',{});text=plain(parsed.get('text',{}).get('*',''))
                    if task['text'] not in text or str(parsed.get('revid')) not in task['url']:continue
                    assert search_key(task['alias']) in search_key(task['text'])
                    found=True;checks.append({'id':task['id'],'passed':True,'method':'raw_sha256_revision_alias_and_exact_context','raw_sha256':digest(body),'source':task['url'],'judgment':result['judgment']});break
                assert found,task['id']
    assert len(checks)==20
    validation={'source':'user_recovered_real_response','ledger_success_event':event,'record_checks':checks,'passed':20,'failed':0,
       'judgments':dict(Counter(f['judgment'] for f in findings)),'automatic_admission':False}
    json_write(OUT/'deepseek_recovered_validation.json',validation)
    json_write(OUT/'deepseek_validated.json',{'input_sha256':digest(json.dumps(tasks,ensure_ascii=False,separators=(',',':'))),'findings':findings,'provenance':'recovered real call #4; source checks in deepseek_recovered_validation.json'})
    json_write(OUT/'deepseek_receipt_recovered.json',{'task_id':'guji-id-stage65-20261017','request_id':'recovered-real-call-4',
      'record_source':'user_supplied_recovered_response','entry':str(root/'claude-bridge/Invoke-ClaudeAssist.ps1'),'finished_at':event['utc'],
      'input_sha256':digest(json.dumps(tasks,ensure_ascii=False,separators=(',',':'))),'batches':1,'records':20,'wrapper_invocations':1,'automatic_retries':0,
      'status':'completed','successful_records':20,'failed_records':0,'code':'OK','model':value['model'],'transport':value['transport'],
      'calls_used':4,'max_calls_at_call':5,'usage':value['usage'],'elapsed_ms':value['elapsed_ms'],'cost_usd':None,'cost_information':'Actual billed cost not returned; token usage is available'})
    print(json.dumps({'cache_reused':True,'paid_calls_added':0,'source_checks_passed':20,'judgments':validation['judgments']},ensure_ascii=False))


if __name__=='__main__':main()
