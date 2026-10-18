import copy
import sqlite3
import pytest

from app.db import PROJECT_ROOT, TABLES, connect_db
from app.importer import ImportValidationError, import_batch


def counts(path):
    with connect_db(path) as connection:
        return {table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in TABLES}


def test_relationships_nulls_and_multiline_evidence(db_path, batch, write_batch):
    result = import_batch(write_batch(batch), db_path)
    assert result["people"] == {"inserted": 2, "skipped": 0}
    assert counts(db_path) == {"people": 2, "aliases": 3, "sources": 2, "evidences": 2}
    with connect_db(db_path) as connection:
        # 相同姓名及别称均保留两个身份；同一人物多个别称，同一别称多个证据。
        assert connection.execute("SELECT COUNT(DISTINCT person_id) FROM aliases WHERE alias_name = '仅测试共用别称'").fetchone()[0] == 2
        assert connection.execute("SELECT COUNT(*) FROM aliases WHERE person_id = 'TEST-P1'").fetchone()[0] == 2
        assert connection.execute("SELECT COUNT(*) FROM evidences WHERE alias_id = 'TEST-A1'").fetchone()[0] == 2
        person = connection.execute("SELECT * FROM people WHERE person_id = 'TEST-P1'").fetchone()
        assert person["birth_year"] is None and person["death_year"] is None and person["dynasty"] is None
        assert connection.execute("SELECT original_text FROM evidences WHERE evidence_id = 'TEST-E1'").fetchone()[0] == batch["evidences"][0]["original_text"]
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("DELETE FROM people WHERE person_id = 'TEST-P1'")
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("DELETE FROM sources WHERE source_id = 'TEST-S1'")


def test_repeated_batch_is_idempotent(db_path, batch, write_batch):
    directory = write_batch(batch)
    import_batch(directory, db_path)
    result = import_batch(directory, db_path)
    assert all(result[table] == {"inserted": 0, "skipped": len(batch[table])} for table in TABLES)


def test_conflicting_id_rolls_back_new_rows_and_preserves_existing(db_path, batch, write_batch):
    import_batch(write_batch(batch), db_path)
    modified = copy.deepcopy(batch)
    modified["people"].insert(0, {"person_id": "TEST-NEW", "canonical_name": "仅测试新人物", "verification_status": "pending"})
    modified["people"][2]["canonical_name"] = "仅测试冲突姓名"
    with pytest.raises(ImportValidationError, match="已存在且内容不同"):
        import_batch(write_batch(modified), db_path)
    assert counts(db_path)["people"] == 2
    with connect_db(db_path) as connection:
        assert connection.execute("SELECT canonical_name FROM people WHERE person_id = 'TEST-P2'").fetchone()[0] == batch["people"][1]["canonical_name"]


@pytest.mark.parametrize("table,field", [("aliases", "person_id"), ("evidences", "alias_id"), ("evidences", "source_id")])
def test_missing_foreign_key_rolls_back_whole_batch(db_path, batch, write_batch, table, field):
    batch[table][-1][field] = "TEST-MISSING"
    with pytest.raises(ImportValidationError, match=rf"{table}.csv 第 .*外键不存在"):
        import_batch(write_batch(batch), db_path)
    assert all(value == 0 for value in counts(db_path).values())


@pytest.mark.parametrize("table", TABLES)
def test_duplicate_ids_in_any_file_rejected(db_path, batch, write_batch, table):
    batch[table].append(copy.deepcopy(batch[table][0]))
    with pytest.raises(ImportValidationError, match="重复 ID"):
        import_batch(write_batch(batch), db_path)
    assert all(value == 0 for value in counts(db_path).values())


@pytest.mark.parametrize("table,field", [("people", "canonical_name"), ("aliases", "alias_name"), ("sources", "book_title"), ("evidences", "original_text"), ("people", "verification_status")])
def test_required_fields_cannot_be_blank(db_path, batch, write_batch, table, field):
    batch[table][0][field] = "  "
    with pytest.raises(ImportValidationError, match=rf"必填字段 {field}"):
        import_batch(write_batch(batch), db_path)


@pytest.mark.parametrize("table,field", [("people", "verification_status"), ("aliases", "verification_status"), ("aliases", "alias_type"), ("evidences", "verification_status"), ("evidences", "usage_relation"), ("evidences", "evidence_nature")])
def test_invalid_enumerations_rejected(db_path, batch, write_batch, table, field):
    batch[table][0][field] = "TEST-INVALID"
    with pytest.raises(ImportValidationError, match=field):
        import_batch(write_batch(batch), db_path)


@pytest.mark.parametrize("year", ["未知", "1.5", "0", str(2**80)])
def test_invalid_year_rejected(db_path, batch, write_batch, year):
    batch["people"][0]["birth_year"] = year
    with pytest.raises(ImportValidationError, match="birth_year"):
        import_batch(write_batch(batch), db_path)


def test_year_order_rejected_and_bce_supported(db_path, batch, write_batch):
    batch["people"][0].update(birth_year="-20", death_year="-30")
    with pytest.raises(ImportValidationError, match="不能晚于"):
        import_batch(write_batch(batch), db_path)
    batch["people"][0].update(birth_year="-30", death_year="-20")
    import_batch(write_batch(batch), db_path)
    with connect_db(db_path) as connection:
        assert connection.execute("SELECT birth_year FROM people WHERE person_id='TEST-P1'").fetchone()[0] == -30


@pytest.mark.parametrize("url", ["javascript:alert(1)", "https://", "https://example.invalid/a b", "http://u:p@example.invalid", "https://example.invalid:invalid"])
def test_source_url_syntax_validation(db_path, batch, write_batch, url):
    batch["sources"][0]["original_url"] = url
    with pytest.raises(ImportValidationError, match="original_url"):
        import_batch(write_batch(batch), db_path)


@pytest.mark.parametrize("change", ["missing", "pending", "refutes", "indirect"])
def test_verified_alias_requires_verified_direct_support(db_path, batch, write_batch, change):
    if change == "missing":
        batch["evidences"] = []
    elif change == "pending":
        batch["evidences"][0]["verification_status"] = "pending"
    elif change == "refutes":
        batch["evidences"][0]["usage_relation"] = "refutes"
    else:
        batch["evidences"][0]["evidence_nature"] = "indirect"
    with pytest.raises(ImportValidationError, match="缺少已核对的直接支持证据"):
        import_batch(write_batch(batch), db_path)
    assert all(value == 0 for value in counts(db_path).values())


def test_verified_evidence_requires_location(db_path, batch, write_batch):
    batch["sources"][0]["chapter"] = ""
    with pytest.raises(ImportValidationError, match="至少填写一项定位"):
        import_batch(write_batch(batch), db_path)
    assert all(value == 0 for value in counts(db_path).values())


def test_unreviewed_and_uncertain_records_can_remain_without_evidence(db_path, batch, write_batch):
    batch["aliases"][0]["verification_status"] = "pending"
    batch["evidences"] = []
    import_batch(write_batch(batch), db_path)
    assert counts(db_path)["aliases"] == 3
    with connect_db(db_path) as connection:
        assert {row[0] for row in connection.execute("SELECT verification_status FROM aliases")} == {"pending", "disputed"}


def test_duplicate_alias_relation_different_ids_rejected(db_path, batch, write_batch):
    duplicate = dict(batch["aliases"][0], alias_id="TEST-NEW-A")
    batch["aliases"].append(duplicate)
    with pytest.raises(ImportValidationError, match="数据约束冲突"):
        import_batch(write_batch(batch), db_path)
    assert all(value == 0 for value in counts(db_path).values())


def test_normalized_name_computed_but_never_used_as_unique_key(db_path, batch, write_batch):
    batch["aliases"][0]["alias_name"] = "  仅测试共用别称  "
    import_batch(write_batch(batch), db_path)
    with connect_db(db_path) as connection:
        rows = connection.execute("SELECT person_id FROM aliases WHERE normalized_name='仅测试共用别称'").fetchall()
        assert {row[0] for row in rows} == {"TEST-P1", "TEST-P2"}
        assert connection.execute("SELECT alias_name FROM aliases WHERE alias_id='TEST-A1'").fetchone()[0] == "  仅测试共用别称  "


def test_inconsistent_normalized_name_rejected(db_path, batch, write_batch):
    batch["aliases"][0]["normalized_name"] = "仅测试错误规范化"
    with pytest.raises(ImportValidationError, match="normalized_name"):
        import_batch(write_batch(batch), db_path)


def test_bom_csv_and_arbitrary_header_order_supported(db_path, batch, write_batch):
    import_batch(write_batch(batch, bom=True, reverse_headers=True), db_path)
    assert counts(db_path)["evidences"] == 2


def test_empty_delivered_templates_do_not_seed_data(db_path):
    result = import_batch(PROJECT_ROOT / "data" / "templates", db_path)
    assert all(result[table] == {"inserted": 0, "skipped": 0} for table in TABLES)
    assert all(value == 0 for value in counts(db_path).values())


@pytest.mark.parametrize("problem", ["missing_file", "wrong_header", "duplicate_header", "missing_cell", "extra_cell", "invalid_utf8", "malformed_quote"])
def test_broken_csv_reports_error_and_writes_no_rows(db_path, batch, write_batch, problem):
    directory = write_batch(batch)
    path = directory / "people.csv"
    if problem == "missing_file":
        path.unlink()
    elif problem == "wrong_header":
        path.write_text("person_id,wrong\n", encoding="utf-8")
    elif problem == "duplicate_header":
        text = path.read_text(encoding="utf-8")
        path.write_text(text.replace("canonical_name", "person_id", 1), encoding="utf-8")
    elif problem == "invalid_utf8":
        path.write_bytes(b"\xff\xff")
    else:
        text = path.read_text(encoding="utf-8").splitlines()[0]
        tail = {"missing_cell": "TEST-P", "extra_cell": ",,,,,,,,extra", "malformed_quote": '"unclosed'}[problem]
        path.write_text(text + "\n" + tail + "\n", encoding="utf-8")
    with pytest.raises(ImportValidationError, match="people.csv"):
        import_batch(directory, db_path)
    assert all(value == 0 for value in counts(db_path).values())

