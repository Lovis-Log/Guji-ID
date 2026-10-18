"""Rollback the 35-quote Kanripo batch only on a disposable formal-data copy."""
import json
import sqlite3
from contextlib import closing

from app.db import PROJECT_ROOT, connect_db
from app.field_quality import fingerprint, rollback
from app.verification import json_write


def main():
    out=PROJECT_ROOT/'work/paragraph-expansion/kanripo-rollback-verification'
    out.mkdir(parents=True,exist_ok=False)
    receipt=json.loads((PROJECT_ROOT/'work/paragraph-expansion/kanripo-formal/import_result.json').read_text(encoding='utf-8'))
    clone=out/'TEST_ONLY_REAL_DATA_COPY.db'
    with closing(sqlite3.connect(PROJECT_ROOT/'data/guji.db')) as source,closing(sqlite3.connect(clone)) as target:
        source.backup(target)
    rollback(clone,receipt['batch_id'],out/'rollback')
    with connect_db(clone) as current,connect_db(receipt['backup']['path']) as previous:
        assert fingerprint(current)==fingerprint(previous)
        for table in ('people','aliases','sources','evidences','relation_quality'):
            assert [tuple(r) for r in current.execute('SELECT * FROM '+table)]==[
                tuple(r) for r in previous.execute('SELECT * FROM '+table)]
        counts={table:current.execute('SELECT count(*) FROM '+table).fetchone()[0]
                for table in ('people','aliases','sources','evidences')}
        assert counts==receipt['counts_before']
        assert current.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
        assert not current.execute('PRAGMA foreign_key_check').fetchall()
    report={'status':'passed','batch_id':receipt['batch_id'],'restored_counts':counts,
        'all_previous_rows_and_relation_grades_exact':True,'formal_database_modified':False,
        'backup':receipt['backup']['path'],'test_copy':str(clone)}
    json_write(out/'result.json',report)
    print(json.dumps(report,ensure_ascii=False))


if __name__=='__main__':main()
