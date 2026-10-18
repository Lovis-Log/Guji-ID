"""Reproducible Stage 6.5 entry point; dry run, apply and rollback."""
import argparse
import json
import sqlite3
from contextlib import closing
from pathlib import Path
from app.db import PROJECT_ROOT,connect_db
from app.field_quality import apply_enrichment,rollback
from app.verification import json_write
from app.source_acquisition import file_hash
from scripts.diagnose_stage65 import OUT,SOURCE,source_candidates


def load(name):return json.loads((OUT/name).read_text(encoding='utf-8'))


def run(db,output):
    release=json.loads((SOURCE.parent/'release.json').read_text(encoding='utf-8'))
    ai=load('deepseek_validated.json') if (OUT/'deepseek_validated.json').exists() else None
    core=load('core_source_candidates.json')
    supplement_file=PROJECT_ROOT/'data/external/stage65-supplement/record.json'
    supplement=json.loads(supplement_file.read_text(encoding='utf-8')) if supplement_file.exists() else None
    if supplement:
        if file_hash(PROJECT_ROOT/supplement['raw_path'])!=supplement['sha256']:raise ValueError('supplement cache hash mismatch')
        with connect_db(SOURCE) as src:
            rows=source_candidates(src,supplement['canonical_source_name'])
        rows=[p for p in rows if p['c_birthyear']==supplement['birth_year'] and p['c_deathyear']==supplement['death_year']]
        if len(rows)!=1:raise ValueError('supplement identity not corroborated by source name and both dates')
        for test in core:
            if test['query']==supplement['alias'] or test['query']=='陶渊明':test['candidates']=rows
    result=apply_enrichment(db,SOURCE,release,core,load('legacy_identity_candidates.json'),load('legacy38.json'),output,ai,supplement)
    json_write(Path(output)/'last_run.json',result)
    print(json.dumps({k:v for k,v in result.items() if k not in {'coverage','legacy38'}},ensure_ascii=False))
    return result


def main():
    p=argparse.ArgumentParser();p.add_argument('--db',default='data/guji.db');p.add_argument('--output',default='outputs/stage65-release/import')
    p.add_argument('--dry-run',action='store_true');p.add_argument('--rollback');a=p.parse_args()
    db=Path(a.db).resolve();out=Path(a.output).resolve()
    if not db.exists():p.error('existing database required')
    out.mkdir(parents=True,exist_ok=True)
    if a.rollback:rollback(db,a.rollback,out);return
    if a.dry_run:
        clone=out/'DRY_RUN_COPY.db'
        if clone.exists():p.error('dry-run copy already exists; use a fresh output path')
        with closing(sqlite3.connect(db)) as src,closing(sqlite3.connect(clone)) as dest:src.backup(dest)
        run(clone,out)
    else:run(db,out)


if __name__=='__main__':main()
