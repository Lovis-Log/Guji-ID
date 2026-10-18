"""Restore the actual new quote batch on a disposable production-data copy."""
import json
import sqlite3
from contextlib import closing
from pathlib import Path

from app.db import PROJECT_ROOT, connect_db
from app.field_quality import rollback, fingerprint
from app.verification import json_write


def main():
    out=PROJECT_ROOT/'outputs/classical-source/rollback-verification'
    out.mkdir(parents=True,exist_ok=False)
    clone=out/'TEST_ONLY_REAL_DATA_COPY.db'
    manifest=json.loads((PROJECT_ROOT/'outputs/classical-source/formal-import/import_result.json').read_text(encoding='utf-8'))
    before=manifest['backup']['path']
    with closing(sqlite3.connect(PROJECT_ROOT/'data/guji.db')) as source,closing(sqlite3.connect(clone)) as target:
        source.backup(target)
    rollback(clone,manifest['batch_id'],out/'rollback')
    with connect_db(clone) as c,connect_db(before) as original:
        assert fingerprint(c)==fingerprint(original)
        for table in ('people','aliases','sources','evidences','relation_quality'):
            assert [dict(r) for r in c.execute('SELECT * FROM '+table)]==[dict(r) for r in original.execute('SELECT * FROM '+table)]
        assert c.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
        assert not c.execute('PRAGMA foreign_key_check').fetchall()
        assert c.execute('SELECT count(*) FROM sources').fetchone()[0]==17
        assert c.execute('SELECT count(*) FROM evidences').fetchone()[0]==1723
    report={'status':'passed','batch_id':manifest['batch_id'],
      'formal_database_modified':False,'restored_counts':{'people':454,'aliases':1195,'sources':17,'evidences':1723},
      'all_original_people_aliases_sources_evidences_and_relation_grades_exact':True,
      'backup':before,'test_copy':str(clone)}
    json_write(out/'result.json',report)
    print(json.dumps({k:report[k] for k in ('status','restored_counts','formal_database_modified')},ensure_ascii=False))


if __name__=='__main__':main()
