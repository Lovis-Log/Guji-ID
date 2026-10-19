import sqlite3

import pytest

from app.db import connect_db, init_db
from app.normalization import normalize_name


def test_schema_initialization_is_repeatable_and_empty(db_path):
    init_db(db_path)
    with connect_db(db_path) as connection:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        assert {"people", "aliases", "sources", "evidences"} <= tables
        assert {"classical_works", "classical_chapters", "classical_paragraphs",
                "classical_evidence_links", "person_profiles"} <= tables
        assert connection.execute("SELECT COUNT(*) FROM people").fetchone()[0] == 0
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert "idx_aliases_normalized_name" in {row[1] for row in connection.execute("PRAGMA index_list(aliases)")}


def test_foreign_keys_enabled_on_each_connection(db_path):
    for _ in range(2):
        with connect_db(db_path) as connection:
            assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
            with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
                connection.execute("INSERT INTO aliases VALUES ('TEST-A', 'TEST-MISSING', '仅测试', '仅测试', 'name', 'pending')")


@pytest.mark.parametrize("status", ["pending", "verified", "disputed", "rejected"])
def test_all_four_statuses_are_distinct(db_path, status):
    with connect_db(db_path) as connection:
        connection.execute("INSERT INTO people(person_id, canonical_name, verification_status) VALUES (?, ?, ?)", ("TEST-P", "仅测试虚构人物", status))
        assert connection.execute("SELECT verification_status FROM people").fetchone()[0] == status


@pytest.mark.parametrize("sql", [
    "INSERT INTO people(person_id,canonical_name,verification_status) VALUES('TEST-P','仅测试','confirmed')",
    "INSERT INTO people(person_id,canonical_name) VALUES('TEST-P','   ')",
    "INSERT INTO people(person_id,canonical_name,birth_year,death_year) VALUES('TEST-P','仅测试',20,10)",
    "INSERT INTO people(person_id,canonical_name,birth_year) VALUES('TEST-P','仅测试',0)",
    "INSERT INTO people(person_id,canonical_name,birth_year) VALUES('TEST-P','仅测试','未知')",
])
def test_database_rejects_invalid_core_fields(db_path, sql):
    with connect_db(db_path) as connection:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(sql)


def test_normalization_is_conservative_and_keeps_homonyms_separate():
    assert normalize_name(" \u3000测试称呼\t") == "测试称呼"
    assert normalize_name("e\u0301") == "é"
    assert normalize_name("测试  别称·Ａ") == "测试  别称·Ａ"
    assert normalize_name("後") != normalize_name("后")
    assert normalize_name("Ａ") != normalize_name("A")

