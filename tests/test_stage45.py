"""Offline preparation + synthetic approvals confined to isolated test databases."""
from pathlib import Path

import pytest

from app.batch_import import backup_database, database_counts, dry_run, import_approved, materialize_approved
from app.collector import digest, read_csv, write_csv
from app.db import connect_db, init_db
from app.importer import import_batch
from app.verification import json_read, json_write, summarize_batch
from scripts.prepare_stage45_review import prepare
from scripts.stage45_decision import build_decision
from test_collection import review_run, seed
from test_verification import decision_for, fake_review, prepared, verification_source


def test_generic_import_rejects_configured_formal_database_before_open(tmp_path, monkeypatch, batch, write_batch):
    target = tmp_path / "TEST_ONLY_PROTECTED.db"
    monkeypatch.setenv("GUJI_DB_PATH", str(target))
    with pytest.raises(ValueError, match="正式库只允许"):
        import_batch(write_batch(batch), target)
    assert not target.exists()


def test_reviewed_legacy_entry_cannot_bypass_formal_gate(prepared, tmp_path, monkeypatch):
    from app.collection_review import import_reviewed
    fake_review(prepared)
    summarize_batch(prepared)
    approval = tmp_path / "TEST_ONLY_APPROVAL.json"
    json_write(approval, decision_for(prepared))
    export = tmp_path / "export"
    materialize_approved(prepared, approval, export)
    target = tmp_path / "TEST_ONLY_PROTECTED.db"
    monkeypatch.setenv("GUJI_DB_PATH", str(target))
    with pytest.raises(ValueError, match="正式库只允许"):
        import_reviewed(export, target)
    assert not target.exists()
    # The sealed approved route remains usable for a configured formal target.
    result = import_approved(prepared, approval, export, target)
    assert result["counts_delta"]["aliases"] == 1


def test_backup_is_restorable_and_includes_committed_wal(tmp_path):
    import sqlite3
    target = tmp_path / "TEST_ONLY.db"
    init_db(target)
    with connect_db(target) as live:
        live.execute("PRAGMA journal_mode=WAL")
        live.execute("INSERT INTO people(person_id,canonical_name) VALUES ('TEST-P','TEST_ONLY_SYNTHETIC')")
        backup = backup_database(target, tmp_path / "backups")
        with sqlite3.connect(backup["path"]) as saved:
            assert saved.execute("SELECT canonical_name FROM people").fetchone()[0] == "TEST_ONLY_SYNTHETIC"
        restored = tmp_path / "TEST_ONLY_RESTORED.db"
        with sqlite3.connect(backup["path"]) as source, sqlite3.connect(restored) as destination:
            source.backup(destination)
        assert database_counts(restored) == database_counts(target) == backup["counts"]


def test_backup_failure_stops_before_import(prepared, tmp_path, monkeypatch):
    import app.batch_import as module
    fake_review(prepared)
    summarize_batch(prepared)
    approval = tmp_path / "TEST_ONLY_APPROVAL.json"
    json_write(approval, decision_for(prepared))
    target = tmp_path / "TEST_ONLY.db"
    init_db(target)
    before = digest(target.read_bytes())
    def fail(*args):
        raise OSError("TEST ONLY backup denied")
    monkeypatch.setattr(module, "backup_database", fail)
    with pytest.raises(OSError, match="backup denied"):
        import_approved(prepared, approval, tmp_path / "export", target)
    assert digest(target.read_bytes()) == before


def test_selected_dry_run_rejects_unknown_and_empty_scopes(prepared, tmp_path):
    fake_review(prepared)
    summarize_batch(prepared)
    for i, scope in enumerate(([], ["TEST-UNKNOWN"])):
        with pytest.raises(ValueError, match="预演关系清单"):
            dry_run(prepared, tmp_path / "not_created.db", tmp_path / f"dry-{i}", alias_ids=scope)
    assert not (tmp_path / "not_created.db").exists()


def test_real_preparation_keeps_all_pending_and_excludes_uncertain_old_relations(tmp_path):
    output = tmp_path / "OFFLINE_REVIEW_ONLY"
    summary = prepare(output)
    assert (summary["candidate_people"], summary["relations"], summary["evidence_rows"]) == (20, 55, 52)
    assert summary["recommended_people"] == 10 and summary["recommended_relations"] == 17
    assert summary["groups"] == {"PRIORITY_REVIEW": 17, "HOLD": 5, "OLD_NOT_REVERIFIED": 23, "RESERVE": 10}
    rows = read_csv(output / "human_review_worksheet.csv")
    assert all(r["human_decision"] == "PENDING" for r in rows)
    assert all(r["ai_status"] == "ai_verified" for r in rows if r["review_group"] == "PRIORITY_REVIEW")
    with pytest.raises(ValueError, match="没有人工 APPROVED"):
        build_decision(output, "TEST_ONLY_NOT_AN_ACTUAL_APPROVER", "2026-10-17T10:00:00+08:00")
    with pytest.raises(ValueError, match="不覆盖人工决定"):
        prepare(output)


def synthetic_worksheet(prepared, tmp_path):
    fake_review(prepared)
    summarize_batch(prepared)
    task = json_read(prepared / "tasks.json")[0]
    root = tmp_path / "TEST_ONLY_WORKSHEET"
    root.mkdir()
    row = {"alias_id": task["alias"]["alias_id"], "task_id": task["task_id"], "review_group": "PRIORITY_REVIEW",
           "original_text": "TEST ONLY immutable synthetic evidence", "human_decision": "APPROVED", "human_notes": "TEST ONLY simulated human, not real approval"}
    write_csv(root / "human_review_worksheet.csv", list(row), [row])
    spots = [{"task_id": s["task_id"], "check_completed": "CHECKED", "human_notes": "TEST ONLY checked"} for s in read_csv(prepared / "spot_checks.csv")]
    write_csv(root / "mandatory_spot_checks.csv", list(spots[0]), spots)
    template = json_read(prepared / "approval_template.json")
    json_write(root / "review_manifest.json", {"batch_id": template["batch_id"], "review_seal_sha256": template["review_seal_sha256"], "immutable_rows": [{k: v for k, v in row.items() if k not in {"human_decision", "human_notes"}}], "spot_task_ids": [s["task_id"] for s in spots]})
    return root, row


def test_synthetic_human_sheet_maps_exact_approved_scope_without_recording(prepared, tmp_path):
    from datetime import datetime, timezone
    root, row = synthetic_worksheet(prepared, tmp_path)
    result = build_decision(root, "TEST_ONLY_SIMULATED_HUMAN", datetime.now(timezone.utc).isoformat(), review=prepared)
    assert result["approved_alias_ids"] == [row["alias_id"]]
    assert not (root / "approval.json").exists()


@pytest.mark.parametrize("field,value,message", [("human_decision", "yes", "只允许"), ("original_text", "TEST ONLY altered", "字段已改动"), ("human_notes", "", "批准须填写")])
def test_sheet_invalid_decision_and_changed_evidence_rejected(prepared, tmp_path, field, value, message):
    root, row = synthetic_worksheet(prepared, tmp_path)
    row[field] = value
    write_csv(root / "human_review_worksheet.csv", list(row), [row])
    with pytest.raises(ValueError, match=message):
        build_decision(root, "TEST_ONLY_SIMULATED_HUMAN", "2026-10-17T10:00:00+08:00", review=prepared)
