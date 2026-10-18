"""Manual one-call advisory command; does not mutate admission decisions."""
import argparse
import json
import sqlite3
from app.quarantine_review import review_once

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--staging',default='data/ingestion/stage6.sqlite3')
    p.add_argument('--output',required=True)
    p.add_argument('--limit',type=int,default=3)
    p.add_argument('--explicit-one-call',action='store_true',help='仅在本次用户明确要求一次调用后提供')
    a=p.parse_args()
    with sqlite3.connect(a.staging) as c:
        rows=[{'candidate_id':cid,'reason':reason,'source_record':json.loads(payload)} for cid,reason,payload in c.execute("SELECT candidate_id,reason,payload_json FROM candidates WHERE decision='B' ORDER BY candidate_id LIMIT ?",(a.limit,))]
    print(json.dumps(review_once(rows,a.output,explicitly_authorized=a.explicit_one_call),ensure_ascii=False,indent=2))
