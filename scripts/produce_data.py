"""Acquire -> stage -> admit -> transaction -> regression -> audit, no row approval."""
import argparse
import json
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from contextlib import closing
from pathlib import Path

from app.automatic_ingestion import inspect_source, stage_records, run_pipeline, rollback_batch
from app.batch_import import existing_connection
from app.classical_admission import acquire_texts,enrich_classical
from app.collector import now
from app.db import PROJECT_ROOT
from app.source_acquisition import acquire_cbdb
from app.verification import json_read,json_write


def produce(config, output, dry_run=False):
    cfg=json_read(config)
    out=Path(output)
    out.mkdir(parents=True,exist_ok=False)
    release=acquire_cbdb(cfg['source_dir'],version=cfg['source_version'])
    source=Path(cfg['source_dir'])/'cbdb.sqlite3'
    json_write(out/'source_inspection.json',inspect_source(source,release))
    json_write(out/'staging.json',stage_records(source,release,cfg['staging'],cfg['candidate_limit']))
    acquire_texts(source,cfg['staging'],cfg['text_dir'],cfg['text_volumes'])
    kwargs={'target_people':cfg['target_people'],'batch_size':cfg['batch_size'],'max_aliases':cfg['max_alternate_names_per_person']}
    if dry_run:
        with tempfile.TemporaryDirectory(dir=PROJECT_ROOT/'work',prefix='full-pipeline-dry-') as tmp:
            clone=Path(tmp)/'dry.db'
            stage=Path(tmp)/'stage.db'
            with existing_connection(cfg['database']) as src,closing(sqlite3.connect(clone)) as dst:
                src.backup(dst)
            with closing(sqlite3.connect(cfg['staging'])) as src,closing(sqlite3.connect(stage)) as dst:
                src.backup(dst)
            core=run_pipeline(source,release,stage,clone,out/'simulation',**kwargs)
            text=enrich_classical(source,cfg['text_dir'],clone,out/'text_simulation')
        result={'status':'passed','dry_run':True,'formal_database_writes':0,'structured':core,'text':text,'new_model_calls':0}
    else:
        core=run_pipeline(source,release,cfg['staging'],cfg['database'],out/'automatic',**kwargs)
        text=enrich_classical(source,cfg['text_dir'],cfg['database'],out/'text')
        tests=[]
        for label,paths in [('regression',[]),('historic_formal',['tests/formal_acceptance.py']),('stage6_formal',['tests/formal_stage6.py'])]:
            argv=[sys.executable,'-m','pytest',*paths,'-q','-o','cache_dir=work/pytest-cache-stage6','--basetemp',str(PROJECT_ROOT/'work'/('pytest-produce-'+label+'-'+out.name)), '--junitxml',str(out/(label+'.xml'))]
            p=subprocess.run(argv,cwd=PROJECT_ROOT,capture_output=True,text=True,encoding='utf-8',env={**__import__('os').environ,'PYTHONIOENCODING':'utf-8'})
            (out/(label+'.txt')).write_text(p.stdout+'\n'+p.stderr,encoding='utf-8')
            tests.append({'suite':label,'exit_code':p.returncode,'command':argv})
            if p.returncode:
                json_write(out/'FAILED_STOP.json',{'status':'regression_failed_stop','tests':tests,'new_model_calls':0,'no_further_imports':True})
                raise RuntimeError('回归失败，已停止；查看故障报告。结构质量检查仍在每个批次提交前执行。')
        result={'status':'passed','dry_run':False,'structured':core,'text':text,'tests':tests,'new_model_calls':0}
    result['finished_at']=now()
    json_write(out/'pipeline_audit.json',result)
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--config',default='data/collection_configs/stage6_auto.json')
    p.add_argument('--output',required=True)
    p.add_argument('--dry-run',action='store_true')
    a=p.parse_args()
    r=produce(a.config,a.output,a.dry_run)
    print(json.dumps({'status':r['status'],'dry_run':r['dry_run'],'new_model_calls':0},ensure_ascii=False))
