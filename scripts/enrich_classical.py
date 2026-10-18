import argparse
import json
from pathlib import Path
from app.classical_admission import acquire_texts, enrich_classical
from app.db import default_db_path

if __name__ == "__main__":
    p = argparse.ArgumentParser(description="获取允许本地处理的Kanripo固定版本文本；严格原文声明匹配后自动A+准入")
    p.add_argument("command", choices=["acquire", "enrich"])
    p.add_argument("--source", default="data/external/cbdb-20250520/cbdb.sqlite3")
    p.add_argument("--staging", default="data/ingestion/stage6.sqlite3")
    p.add_argument("--text-dir", default="data/external/kanripo-stage6")
    p.add_argument("--max-volumes", type=int, default=10)
    p.add_argument("--db", default=None)
    p.add_argument("--output", default="outputs/stage6-release/text-admission")
    a = p.parse_args()
    result = acquire_texts(a.source, a.staging, a.text_dir, a.max_volumes) if a.command == "acquire" else enrich_classical(a.source, a.text_dir, a.db or default_db_path(), a.output)
    print(json.dumps(result, ensure_ascii=False, indent=2))
