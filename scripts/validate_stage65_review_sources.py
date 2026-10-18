"""Reproduce AI packet source records from original CBDB and cached Action API bytes."""
import json
import argparse
from pathlib import Path
from app.db import PROJECT_ROOT,connect_db
from app.collector import digest,plain
from app.verification import json_write
from app.source_acquisition import verify_hash
from scripts.diagnose_stage65 import SOURCE


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--input-dir',default='outputs/stage65-release/deepseek-cases')
    args=parser.parse_args();out=PROJECT_ROOT/args.input_dir
    cases=json.loads((out/'input.json').read_text(encoding='utf-8'))
    release=json.loads((SOURCE.parent/'release.json').read_text(encoding='utf-8'));verify_hash(SOURCE,release['sqlite_sha256'])
    if any('additional_biography_source' in r for r in cases):
        from scripts.review_stage65_cases import prepare
        expected,_=prepare(len(cases),True)
        assert cases==expected,'enriched source context differs from authoritative-source reproduction'
    checks=[]
    with connect_db(PROJECT_ROOT/'data/guji.db') as c,connect_db(SOURCE) as src:
        for case in cases:
            if case['field']=='native_place':
                verified=True
                for r in case['records']:
                    matches=[dict(x) for x in src.execute('''SELECT d.*,a.c_name_chn AS address_name,t.c_addr_desc_chn AS address_type
                     FROM BIOG_ADDR_DATA d JOIN ADDR_CODES a USING(c_addr_id) JOIN BIOG_ADDR_CODES t USING(c_addr_type)
                     WHERE d.c_personid=? AND d.c_addr_id=? AND d.c_addr_type=?''',(r['c_personid'],r['c_addr_id'],r['c_addr_type']))]
                    verified &= any(all(x.get(k)==v for k,v in r.items()) for x in matches)
                checks.append({'id':case['id'],'passed':bool(verified),'method':'exact_source_table_and_key_join','source':case['source_ref']})
            else:
                q=c.execute('SELECT source_json FROM quality_quarantine WHERE candidate_id=?',(case['id'],)).fetchone()
                original=json.loads(q[0]);found=False
                for folder in ('2026-10-16-delivery','stage4-review-input','stage4-tang-song-20'):
                    raw=PROJECT_ROOT/'data/collections'/folder/original['discovery_raw_path']
                    meta=raw.with_suffix('.json')
                    if not raw.exists() or not meta.exists():continue
                    body=raw.read_bytes();metadata=json.loads(meta.read_text(encoding='utf-8'))
                    if digest(body)!=metadata.get('sha256'):continue
                    data=json.loads(body.decode('utf-8'));parsed=data.get('parse',{})
                    if str(parsed.get('revid')) not in case['source_ref']:continue
                    text=plain(parsed.get('text',{}).get('*',''))
                    if case['text'] not in text:continue
                    found=True
                    checks.append({'id':case['id'],'passed':True,'method':'raw_digest_revision_and_exact_substring',
                       'source':case['source_ref'],'raw_path':str(raw.relative_to(PROJECT_ROOT)),'raw_sha256':digest(body),'text_start':text.index(case['text'])})
                    break
                if not found:checks.append({'id':case['id'],'passed':False,'method':'source_cache_not_reproduced'})
    result={'records':len(checks),'passed':sum(x['passed'] for x in checks),'failed':sum(not x['passed'] for x in checks),'checks':checks}
    json_write(out/'source_validation.json',result)
    print(json.dumps({k:v for k,v in result.items() if k!='checks'}))
    assert result['failed']==0


if __name__=='__main__':main()
