"""Test the four-person ancient-text batch rollback on a disposable copy."""
import json
import sqlite3
from contextlib import closing

from app.db import PROJECT_ROOT, connect_db
from app.field_quality import rollback, fingerprint
from app.verification import json_write


def main():
    out=PROJECT_ROOT/'work/paragraph-expansion/rollback-verification'
    out.mkdir(parents=True,exist_ok=False)
    clone=out/'TEST_ONLY_REAL_DATA_COPY.db'
    receipt=json.loads((PROJECT_ROOT/'work/paragraph-expansion/formal/import_result.json').read_text(encoding='utf-8'))
    original=receipt['backup']['path']
    with closing(sqlite3.connect(PROJECT_ROOT/'data/guji.db')) as src,closing(sqlite3.connect(clone)) as dst:
        src.backup(dst)
    rollback(clone,receipt['batch_id'],out/'rollback')
    with connect_db(clone) as current,connect_db(original) as previous:
        assert fingerprint(current)==fingerprint(previous)
        for table in ('people','aliases','sources','evidences','relation_quality'):
            assert [tuple(r) for r in current.execute('SELECT * FROM '+table)]==[
                tuple(r) for r in previous.execute('SELECT * FROM '+table)]
        assert current.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
        assert not current.execute('PRAGMA foreign_key_check').fetchall()
        counts={table:current.execute('SELECT count(*) FROM '+table).fetchone()[0]
                for table in ('people','aliases','sources','evidences')}
        assert counts==receipt['counts_before']
    report={'status':'passed','batch_id':receipt['batch_id'],'restored_counts':counts,
        'all_previous_rows_and_relation_grades_exact':True,'formal_database_modified':False,
        'backup':original,'test_copy':str(clone)}
    json_write(out/'result.json',report)
    print(json.dumps(report,ensure_ascii=False))


if __name__=='__main__':main()
