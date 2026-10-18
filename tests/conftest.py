"""全部为虚构夹具：仅运行时写入 pytest 临时目录，绝不写入正式 data/。"""

import csv

import pytest

from app.db import TABLES, init_db
from app.importer import COLUMNS


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "test_only.db"
    init_db(path)
    return path


@pytest.fixture
def batch():
    # example.invalid 是不可用的测试地址；以下不是古籍资料或历史人物。
    return {
        "people": [
            {"person_id": "TEST-P1", "canonical_name": "仅测试虚构同名人物", "verification_status": "pending"},
            {"person_id": "TEST-P2", "canonical_name": "仅测试虚构同名人物", "verification_status": "disputed"},
        ],
        "aliases": [
            {"alias_id": "TEST-A1", "person_id": "TEST-P1", "alias_name": "仅测试共用别称", "alias_type": "art", "verification_status": "verified"},
            {"alias_id": "TEST-A2", "person_id": "TEST-P2", "alias_name": "仅测试共用别称", "alias_type": "art", "verification_status": "pending"},
            {"alias_id": "TEST-A3", "person_id": "TEST-P1", "alias_name": "仅测试第二别称", "alias_type": "unknown", "verification_status": "disputed"},
        ],
        "sources": [
            {"source_id": "TEST-S1", "book_title": "TEST_ONLY_SYNTHETIC_SOURCE_NOT_A_BOOK", "chapter": "TEST-LOCATION-1", "platform": "TEST_ONLY", "original_url": "https://example.invalid/test-only/1"},
            {"source_id": "TEST-S2", "book_title": "TEST_ONLY_SYNTHETIC_SOURCE_NOT_A_BOOK", "volume": "TEST-LOCATION-2", "platform": "TEST_ONLY", "original_url": "https://example.invalid/test-only/2"},
        ],
        "evidences": [
            {"evidence_id": "TEST-E1", "alias_id": "TEST-A1", "source_id": "TEST-S1", "original_text": "TEST ONLY: synthetic claim, not historical text.\nSecond test line.", "usage_relation": "supports", "evidence_nature": "direct", "verification_status": "verified", "notes": "仅验证约束的虚构夹具"},
            {"evidence_id": "TEST-E2", "alias_id": "TEST-A1", "source_id": "TEST-S2", "original_text": "TEST ONLY: uncertain synthetic claim.", "usage_relation": "uncertain", "evidence_nature": "indirect", "verification_status": "pending"},
        ],
    }


@pytest.fixture
def write_batch(tmp_path):
    def write(rows, *, bom=False, reverse_headers=False):
        directory = tmp_path / "test_only_csv"
        directory.mkdir(exist_ok=True)
        for table in TABLES:
            columns = list(COLUMNS[table])
            if reverse_headers:
                columns.reverse()
            with (directory / f"{table}.csv").open("w", encoding="utf-8-sig" if bom else "utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=columns)
                writer.writeheader()
                writer.writerows(rows[table])
        return directory
    return write

