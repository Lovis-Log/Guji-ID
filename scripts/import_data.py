import argparse
import json
import sqlite3

from app.db import default_db_path
from app.importer import ImportValidationError, import_batch


def main() -> int:
    parser = argparse.ArgumentParser(description="按依赖顺序、整批事务导入四份 CSV；失败返回非零退出码")
    parser.add_argument("--input-dir", required=True, help="包含 people/aliases/sources/evidences.csv 的目录")
    parser.add_argument("--db", default=None, help="SQLite 文件路径；默认 GUJI_DB_PATH 或 data/guji.db")
    args = parser.parse_args()
    try:
        result = import_batch(args.input_dir, args.db or default_db_path())
    except (ImportValidationError, OSError, sqlite3.Error) as exc:
        parser.exit(1, f"导入失败，整批未提交：{exc}\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

