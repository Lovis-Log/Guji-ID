import argparse
import sqlite3

from app.db import default_db_path, init_db


def main() -> int:
    parser = argparse.ArgumentParser(description="初始化 GuJi ID 四张核心表，不写入示例数据")
    parser.add_argument("--db", default=None, help="SQLite 文件路径；默认 GUJI_DB_PATH 或 data/guji.db")
    args = parser.parse_args()
    path = args.db or default_db_path()
    try:
        init_db(path)
    except (OSError, sqlite3.Error) as exc:
        parser.exit(1, f"初始化失败：{exc}\n")
    print(f"数据库已初始化：{path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

