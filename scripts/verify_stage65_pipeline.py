"""Real-data copy: repeat import, later-write guard, full batch rollback and reapply."""
import json
import sqlite3
import argparse
from contextlib import closing
from pathlib import Path
from app.db import PROJECT_ROOT,connect_db
from app.field_quality import fingerprint,rollback
from app.verification import json_write
from scripts.enrich_stage65 import run


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',default='outputs/stage65-release/pipeline-validation-final')
    parser.add_argument('--source-db');args=parser.parse_args()
    source=Path(args.source_db) if args.source_db else Path(json.loads((PROJECT_ROOT/'outputs/stage65-release/formal-import/import_result.json').read_text(encoding='utf-8'))['backup']['path'])
    out=PROJECT_ROOT/args.output;out.mkdir(parents=True,exist_ok=False)
    clone=out/'TEST_ONLY_REAL_DATA_COPY.db'
    with closing(sqlite3.connect(source)) as src,closing(sqlite3.connect(clone)) as dst:src.backup(dst)
    before={}
    with connect_db(clone) as c:
        for t in ('people','aliases','sources','evidences'):before[t]=[dict(r) for r in c.execute('SELECT * FROM '+t)]
    first=run(clone,out/'first')
    with connect_db(clone) as c:expected=fingerprint(c)
    repeat=run(clone,out/'repeat');assert repeat['cache_reused'] is True
    with connect_db(clone) as c:
        assert fingerprint(c)==expected
        original=c.execute('SELECT person_id,identity_description FROM people ORDER BY person_id LIMIT 1').fetchone()
        c.execute('UPDATE people SET identity_description=? WHERE person_id=?',('TEST_ONLY_LATER_WRITE',original[0]))
    try:rollback(clone,first['batch_id'],out/'blocked-rollback')
    except ValueError as e:assert 'later data changed' in str(e)
    else:raise AssertionError('unsafe rollback did not refuse later write')
    with connect_db(clone) as c:c.execute('UPDATE people SET identity_description=? WHERE person_id=?',(original[1],original[0]))
    rollback(clone,first['batch_id'],out/'rollback')
    with connect_db(clone) as c:
        for t,rows in before.items():assert [dict(r) for r in c.execute('SELECT * FROM '+t)]==rows
        assert c.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
        assert not c.execute('PRAGMA foreign_key_check').fetchall()
    reapplied=run(clone,out/'reapply')
    with connect_db(clone) as c:assert fingerprint(c)==expected
    json_write(out/'result.json',{'status':'passed','repeat_zero_changes':True,'later_write_guard':True,'rollback_original_four_tables_exact':True,'reapply_identical':True,'batch_id':first['batch_id'],'test_copy':str(clone)})
    print('idempotency, guarded rollback and exact reapply passed')


if __name__=='__main__':main()
