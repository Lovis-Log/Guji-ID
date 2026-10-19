"""Safely remove Stage 7 tables while preserving the original four tables.

The script refuses to run without ``--yes``, makes a fresh backup first, and
drops only the additive Stage 7 tables.  It never touches people, aliases,
sources or evidences.

    python -m scripts.rollback_stage7 --db data/guji.db --yes
    python -m scripts.rollback_stage7 --db data/guji.db --restore data/backups/xxx.db --yes
"""

from __future__ import annotations

import argparse
import hashlib
import shutil
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

STAGE7_TABLES = (
    "classical_evidence_links",
    "classical_paragraphs",
    "classical_chapters",
    "classical_works",
    "classical_quarantine",
    "person_timeline_events",
    "person_relations",
    "person_literary_works",
    "person_disputes",
    "person_profiles",
    "classical_processing_runs",
)
CORE_TABLES = ("people", "aliases", "sources", "evidences")


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def counts(path: Path) -> dict:
    con = sqlite3.connect(str(path))
    try:
        return {table: con.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                for table in CORE_TABLES}
    finally:
        con.close()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="回滚 Stage 7 追加表")
    parser.add_argument("--db", default="data/guji.db")
    parser.add_argument("--backup-dir", default="data/backups")
    parser.add_argument("--restore", help="直接恢复指定备份文件，而不是仅删除追加表")
    parser.add_argument("--yes", action="store_true", help="确认执行不可逆操作")
    args = parser.parse_args(argv)
    db = Path(args.db)
    if not db.exists():
        parser.error("数据库不存在")
    if not args.yes:
        parser.error("这是不可逆操作；确认后请加 --yes")

    backup_dir = Path(args.backup_dir)
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    safety = backup_dir / f"guji_pre_stage7_rollback_{stamp}.db"
    shutil.copy2(db, safety)
    print(f"已创建回滚前备份：{safety}\nsha256={digest(safety)}")

    if args.restore:
        source = Path(args.restore)
        if not source.exists():
            parser.error("指定备份不存在")
        con = sqlite3.connect(str(source))
        try:
            con.execute("PRAGMA integrity_check").fetchone()
        finally:
            con.close()
        shutil.copy2(source, db)
        print(f"已恢复：{source} -> {db}")
        print("恢复后核心表计数：", counts(db))
        return 0

    before = counts(db)
    con = sqlite3.connect(str(db))
    con.execute("PRAGMA foreign_keys = ON")
    try:
        con.execute("BEGIN IMMEDIATE")
        for table in STAGE7_TABLES:
            con.execute(f'DROP TABLE IF EXISTS "{table}"')
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    finally:
        con.close()
    after = counts(db)
    if before != after:
        print("警告：核心四表计数发生变化！请立即用回滚前备份恢复。", file=sys.stderr)
        return 1
    print("已删除 Stage 7 追加表；核心四表计数未变：", after)
    print("如需恢复 Stage 7 数据，请从回滚前备份恢复。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
