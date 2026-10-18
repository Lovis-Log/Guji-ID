"""Verify the actual two-batch production rollback chain on a disposable copy."""
import json
import sqlite3
from contextlib import closing
from app.db import PROJECT_ROOT,connect_db
from app.field_quality import rollback
from app.verification import json_write


def main():
    out=PROJECT_ROOT/'outputs/stage65-release/multibatch-rollback';out.mkdir(parents=True,exist_ok=False)
    clone=out/'TEST_ONLY_ACTUAL_TWO_BATCH_COPY.db'
    first=json.loads((PROJECT_ROOT/'outputs/stage65-release/formal-import/import_result.json').read_text(encoding='utf-8'))['batch_id']
    second=json.loads((PROJECT_ROOT/'outputs/stage65-release/formal-increment/import_result.json').read_text(encoding='utf-8'))['batch_id']
    with closing(sqlite3.connect(PROJECT_ROOT/'data/guji.db')) as src,closing(sqlite3.connect(clone)) as dest:src.backup(dest)
    try:rollback(clone,first,out/'wrong-order')
    except ValueError as e:assert 'later data changed' in str(e)
    else:raise AssertionError('older batch rollback bypassed later changes')
    rollback(clone,second,out/'latest-batch')
    with connect_db(clone) as c:assert c.execute('SELECT count(*) FROM people').fetchone()[0]==453
    rollback(clone,first,out/'original-batch')
    baseline=json.loads((PROJECT_ROOT/'outputs/stage65-release/baseline.json').read_text(encoding='utf-8'))
    with connect_db(clone) as c:
        for t,rows in baseline.items():assert [dict(r) for r in c.execute('SELECT * FROM '+t)]==rows
        assert c.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
        assert not c.execute('PRAGMA foreign_key_check').fetchall()
    json_write(out/'result.json',{'status':'passed','wrong_order_refused':True,'latest_batch_rolls_back_to_453':True,
      'all_stage65_batches_roll_back_to_original_400':True,'original_four_tables_exact':True,'production_database_modified':False,
      'rollback_order':[second,first]})
    print('actual two-batch rollback chain passed; formal DB unchanged')


if __name__=='__main__':main()
