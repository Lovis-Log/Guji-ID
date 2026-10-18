"""四份 UTF-8 CSV 整批导入：严格校验、原子提交、同 ID 同内容幂等。"""

import csv
import sqlite3
from pathlib import Path
from urllib.parse import urlsplit

from app.db import PROJECT_ROOT, TABLES, connect_db, default_db_path, init_db
from app.normalization import normalize_name

COLUMNS = {
    "people": ("person_id", "canonical_name", "dynasty", "birth_year", "death_year", "native_place", "identity_description", "verification_status"),
    "aliases": ("alias_id", "person_id", "alias_name", "normalized_name", "alias_type", "verification_status"),
    "sources": ("source_id", "book_title", "volume", "chapter", "platform", "original_url", "edition"),
    "evidences": ("evidence_id", "alias_id", "source_id", "original_text", "usage_relation", "evidence_nature", "verification_status", "notes"),
}
REQUIRED = {
    "people": ("person_id", "canonical_name", "verification_status"),
    "aliases": ("alias_id", "person_id", "alias_name", "alias_type", "verification_status"),
    "sources": ("source_id", "book_title", "platform", "original_url"),
    "evidences": ("evidence_id", "alias_id", "source_id", "original_text", "usage_relation", "evidence_nature", "verification_status"),
}
CHOICES = {
    "verification_status": {"pending", "verified", "disputed", "rejected"},
    "alias_type": {"name", "courtesy", "art", "posthumous", "other", "unknown"},
    "usage_relation": {"supports", "refutes", "mentions", "uncertain"},
    "evidence_nature": {"direct", "indirect"},
}

# Local application gate, not an OS access-control boundary. Only the sealed
# batch approval entry point supplies this capability after validating approval.
_APPROVED_BATCH = object()


def is_formal_database(db_path):
    target = Path(db_path).resolve()
    return target in {(PROJECT_ROOT / "data/guji.db").resolve(), default_db_path().resolve()}


class ImportValidationError(ValueError):
    """面向操作者的可定位错误，不吞掉失败。"""


def _fail(table: str, line: int, message: str) -> None:
    raise ImportValidationError(f"{table}.csv 第 {line} 行：{message}")


def _validate_url(value: str, table: str, line: int) -> None:
    try:
        parsed = urlsplit(value)
        valid = parsed.scheme in {"http", "https"} and parsed.hostname and not parsed.username and not parsed.password
        # 读取 port 也会拒绝不合法的端口；不访问 URL，不宣称验证过内容。
        parsed.port
        if not valid or any(char.isspace() for char in value):
            raise ValueError("invalid URL")
    except ValueError:
        _fail(table, line, "original_url 必须是有效的 http/https 地址，且不能包含空白或账号密码")


def _read_table(directory: Path, table: str) -> list[tuple[int, dict]]:
    path = directory / f"{table}.csv"
    rows = []
    seen_ids = set()
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle, strict=True)
            headers = reader.fieldnames
            if not headers or len(headers) != len(set(headers)) or set(headers) != set(COLUMNS[table]):
                _fail(table, 1, f"表头必须恰好包含：{','.join(COLUMNS[table])}（顺序不限）")
            for raw in reader:
                line = reader.line_num
                if None in raw or any(value is None for value in raw.values()):
                    _fail(table, line, "列数与表头不一致；含逗号、换行的字段请使用 CSV 双引号")
                # 保留原始称呼和原文；其他字段清理首尾空白。空单元格映射为 NULL。
                row = {
                    key: (value if key in {"alias_name", "original_text"} else value.strip()) if value.strip() else None
                    for key, value in raw.items()
                }
                for field in REQUIRED[table]:
                    if row[field] is None:
                        _fail(table, line, f"必填字段 {field} 为空")
                identifier = row[COLUMNS[table][0]]
                if identifier in seen_ids:
                    _fail(table, line, f"批次中出现重复 ID：{identifier}")
                seen_ids.add(identifier)
                for field, choices in CHOICES.items():
                    if field in row and row[field] not in choices:
                        _fail(table, line, f"{field} 必须为 {', '.join(sorted(choices))}")
                if table == "people":
                    for field in ("birth_year", "death_year"):
                        if row[field] is not None:
                            try:
                                value = int(row[field])
                                if not -(2**63) <= value < 2**63 or value == 0:
                                    raise ValueError("year outside SQLite integer range or zero")
                                row[field] = value
                            except ValueError:
                                _fail(table, line, f"{field} 必须为非零整数年份或留空（公元前用负数）")
                    if row["birth_year"] is not None and row["death_year"] is not None and row["birth_year"] > row["death_year"]:
                        _fail(table, line, "birth_year 不能晚于 death_year")
                elif table == "aliases":
                    normalized = normalize_name(row["alias_name"])
                    if row["normalized_name"] is not None and row["normalized_name"] != normalized:
                        _fail(table, line, "normalized_name 与保守规范化结果不一致；可留空自动计算")
                    row["normalized_name"] = normalized
                elif table == "sources":
                    _validate_url(row["original_url"], table, line)
                rows.append((line, row))
    except (OSError, UnicodeError, csv.Error) as exc:
        raise ImportValidationError(f"无法读取 {path.name}：{exc}") from exc
    return rows


def _validate_evidence_chain(connection: sqlite3.Connection) -> None:
    # 这是结构与状态的一致性检查；史料真实性仍需人工核对。
    missing_location = connection.execute("""
        SELECT e.evidence_id FROM evidences e JOIN sources s USING (source_id)
        WHERE e.verification_status = 'verified'
          AND (s.volume IS NULL OR trim(s.volume) = '')
          AND (s.chapter IS NULL OR trim(s.chapter) = '') LIMIT 1
    """).fetchone()
    if missing_location:
        raise ImportValidationError(f"证据 {missing_location['evidence_id']} 标为 verified，但来源缺少卷次和篇目；至少填写一项定位")
    unsupported = connection.execute("""
        SELECT a.alias_id FROM aliases a WHERE a.verification_status = 'verified'
          AND NOT EXISTS (
              SELECT 1 FROM evidences e WHERE e.alias_id = a.alias_id
              AND e.verification_status = 'verified' AND e.usage_relation = 'supports'
              AND e.evidence_nature = 'direct'
          ) LIMIT 1
    """).fetchone()
    if unsupported:
        raise ImportValidationError(f"别称 {unsupported['alias_id']} 标为 verified，但缺少已核对的直接支持证据；应保留 pending 或 disputed")


def import_batch(directory: str | Path, db_path: str | Path, *, _approval=None) -> dict:
    """四个文件都必需（可只有表头）。任何一项失败，整批新增记录回滚。"""
    if is_formal_database(db_path) and _approval is not _APPROVED_BATCH:
        raise ImportValidationError("正式库只允许经明确人工批准的封存批次入口；请使用 scripts.import_verified_batch import；通用 CSV 导入仅用于隔离库")
    parsed = {table: _read_table(Path(directory), table) for table in TABLES}
    init_db(db_path)
    summary = {table: {"inserted": 0, "skipped": 0} for table in TABLES}
    with connect_db(db_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        try:
            for table in TABLES:
                columns = COLUMNS[table]
                primary_key = columns[0]
                for line, row in parsed[table]:
                    existing = connection.execute(
                        f"SELECT * FROM {table} WHERE {primary_key} = ?", (row[primary_key],)
                    ).fetchone()
                    if existing is not None:
                        if all(existing[column] == row[column] for column in columns):
                            summary[table]["skipped"] += 1
                            continue
                        _fail(table, line, f"ID {row[primary_key]} 已存在且内容不同；本脚本不会覆盖既有记录")
                    try:
                        connection.execute(
                            f"INSERT INTO {table} ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})",
                            tuple(row[column] for column in columns),
                        )
                    except sqlite3.IntegrityError as exc:
                        if "FOREIGN KEY" in str(exc):
                            _fail(table, line, f"外键不存在（person_id / alias_id / source_id）：{exc}")
                        _fail(table, line, f"数据约束冲突（重复 ID、重复别称关系或字段无效）：{exc}")
                    summary[table]["inserted"] += 1
            _validate_evidence_chain(connection)
            if connection.execute("PRAGMA foreign_key_check").fetchone():
                raise ImportValidationError("数据库存在外键不一致，整批取消")
            connection.commit()
        except Exception:
            connection.rollback()
            raise
    return summary

