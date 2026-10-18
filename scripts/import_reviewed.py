import argparse
import json
import sqlite3

from app.collection_review import import_reviewed
from app.db import default_db_path


def main():
    parser = argparse.ArgumentParser(description="校验人工审核批次后事务导入 SQLite")
    parser.add_argument("--input-dir", required=True)
    parser.add_argument("--db", default=None)
    args = parser.parse_args()
    try:
        result = import_reviewed(args.input_dir, args.db or default_db_path())
    except (ValueError, OSError, sqlite3.Error) as exc:
        parser.exit(1, f"审核导入失败，未提交：{exc}\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
