"""All model successes here are explicitly synthetic protocol fixtures.

No test invokes a real CLI/provider. Real stage-three call failed and is retained
separately in the delivered call receipt; these tests cannot replace that review.
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlencode

import pytest

from app.batch_import import APPROVAL_STATEMENT, dry_run, import_approved, materialize_approved, plan, record_approval
from app.catalog import get_alias, search_people
from app.collector import ALIAS_FIELDS, EVIDENCE_FIELDS, LOG_FIELDS, PEOPLE_FIELDS, digest, read_csv, write_csv
from app.db import connect_db, init_db
from app.verification import (
    check_task, invoke_reviewer, json_read, json_write, parse_review,
    prepare_batch, redact, summarize_batch, validate_inputs, validate_review,
)
from test_collection import review_run, seed


@pytest.fixture
def verification_source(review_run):
    root, tables, _ = review_run
    evidence = tables[2][2]
    raw_path = root / evidence["raw_path"]
    json_write(raw_path.with_suffix(".json"), {"request_url": "https://zh.wikisource.org/w/api.php?" + urlencode({"action": "parse", "page": evidence["book_title"] + "/" + evidence["volume"]}), "http_status": 200, "sha256": digest(raw_path.read_bytes()), "error": "", "retrieved_at": "2026-10-16T12:00:00+08:00"})
    write_csv(root / "collection_log.csv", LOG_FIELDS, [])
    return root


@pytest.fixture
def prepared(tmp_path, verification_source):
    root = tmp_path / "TEST_ONLY_VERIFICATION_BATCH"
    prepare_batch(verification_source, root)
    return root


def fake_review(root, *, failed=False, alteration=None):
    """SIMULATED reviewer response for unit tests, never claimed as DeepSeek."""
    tasks = json_read(root / "tasks.json")
    findings = [{"issue": t["task_id"], "evidence": "V|H|1|N|E|O|D1", "risk": "A"} for t in tasks]
    analysis = {"summary": "TEST ONLY local_saved_response_only; simulated protocol", "findings": findings, "alternatives": [], "verification": ["D1=TEST ONLY synthetic explicit courtesy-name relation"]}
    if alteration:
        alteration(analysis)
    envelope = {"ok": not failed, "code": "CLI_ERROR" if failed else "OK", "analysis": analysis}
    json_write(root / "subagent_raw_output.json", envelope)
    json_write(root / "call_receipt.json", {"status": "failed" if failed else "completed", "batch_id": json_read(root / "batch.json")["batch_id"], "request_sha256": digest((root / "reviewer_request.json").read_text(encoding="utf-8")), "stdout_sha256": digest((root / "subagent_raw_output.json").read_text(encoding="utf-8")), "finished_at": datetime.now(timezone.utc).isoformat(), "code": envelope["code"], "model": "TEST_ONLY_SIMULATED_REVIEWER_NOT_REAL", "simulated_unit_test_only": True})


def decision_for(root):
    result = json_read(root / "approval_template.json")
    _, selected = plan(root)
    results = json_read(root / "parsed_results.json")
    result.update(decision="approved", approver="TEST_ONLY_HUMAN_NOT_A_REAL_APPROVAL", approved_at=datetime.now(timezone.utc).isoformat(), approved_alias_ids=sorted({t["alias"]["alias_id"] for t, _ in selected}), checked_task_ids=[r["task_id"] for r in read_csv(root / "spot_checks.csv")], accepted_high_risk_task_ids=[r["task_id"] for r in results if r["mandatory_review"]], statement=APPROVAL_STATEMENT)
    return result


def test_prepare_stable_task_ids_full_provenance_and_blind_input(tmp_path, verification_source):
    roots = [tmp_path / "batch1", tmp_path / "batch2"]
    first = prepare_batch(verification_source, roots[0])
    second = prepare_batch(verification_source, roots[1])
    assert first["batch_id"] == second["batch_id"]
    assert first["program_passed"] == 1
    assert json_read(roots[0] / "tasks.json") == json_read(roots[1] / "tasks.json")
    context = json_read(roots[0] / "reviewer_request.json")["context"]
    assert "raw_html_excerpt" in context
    assert "original_text" in context
    assert "assessment" not in context and "usage_relation" not in context
    assert "verification_status" not in context
    assert first["request_characters"] <= 24000


@pytest.mark.parametrize("field,value", [("original_text", "TEST invented quote"), ("context", "TEST invented context"), ("volume", "卷999"), ("chapter", "TEST nonexistent heading"), ("person_id", "TEST_WRONG_PERSON")])
def test_deterministic_invalid_source_or_identity_never_passes(prepared, field, value):
    task = json_read(prepared / "tasks.json")[0]
    evidence = {**task["evidence"], field: value}
    checked = check_task(prepared / "source_snapshot", task["person"], task["alias"], evidence)
    assert checked["ok"] is False
    assert checked["issues"]


def test_missing_primary_is_uncertain_even_if_mock_reviewer_claims_verified(tmp_path, verification_source):
    aliases = read_csv(verification_source / "aliases_pending.csv")
    missing = {**aliases[0], "alias_id": "TEST-MISSING-A", "alias_name": "仅测试缺证号", "normalized_name": "仅测试缺证号", "alias_type": "art", "evidence_count": "0"}
    write_csv(verification_source / "aliases_pending.csv", ALIAS_FIELDS, aliases + [missing])
    root = tmp_path / "batch"
    prepare_batch(verification_source, root)
    fake_review(root)
    summary = summarize_batch(root)
    assert summary["missing_primary_evidence"] == 1
    row = next(r for r in json_read(root / "parsed_results.json") if r["alias_id"] == "TEST-MISSING-A")
    assert row["verdict"] == "uncertain"
    assert row["subagent_verdict"] == "verified"
    assert row["disagreement"]


@pytest.mark.parametrize("kind", ["missing", "duplicate", "unknown", "bad_enum", "bad_reason", "bad_shape"])
def test_invalid_model_output_fails_closed_for_whole_semantic_batch(prepared, kind):
    def alter(data):
        if kind == "missing":
            data["findings"] = []
        elif kind == "duplicate":
            data["findings"] *= 2
        elif kind == "unknown":
            data["findings"][0]["issue"] = "UNKNOWN"
        elif kind == "bad_enum":
            data["findings"][0]["evidence"] = "AUTO_APPROVED|H|1|N|E|O|D1"
        elif kind == "bad_reason":
            data["verification"] = []
        else:
            data["findings"][0]["extra"] = "not allowed"
    fake_review(prepared, alteration=alter)
    summary = summarize_batch(prepared)
    assert summary["actual_subagent_judgments"] == 0
    assert summary["ai_verified"] == 0
    assert summary["status"] == "blocked_subagent"


def test_actual_call_failure_is_not_a_simulated_pass_and_cannot_be_approved(prepared, tmp_path):
    fake_review(prepared, failed=True)
    summary = summarize_batch(prepared)
    assert summary["ai_verified"] == 0
    assert summary["actual_subagent_judgments"] == 0
    assert json_read(prepared / "parsed_results.json")[0]["model_returned"] is False
    assert "CLI_ERROR" in (prepared / "review_report.md").read_text(encoding="utf-8")
    decision = decision_for(prepared)
    decision_path = tmp_path / "decision.json"
    json_write(decision_path, decision)
    with pytest.raises(ValueError, match="没有有效 AI"):
        record_approval(prepared, decision_path, tmp_path / "approval.json")
    assert not (tmp_path / "approval.json").exists()
    target = tmp_path / "must-not-exist.db"
    with pytest.raises(ValueError):
        import_approved(prepared, decision_path, tmp_path / "no-export", target)
    assert not target.exists()


def test_snapshot_and_report_tampering_invalidate_seal(prepared):
    fake_review(prepared)
    summarize_batch(prepared)
    (prepared / "verification_results.csv").write_text("TEST tampered", encoding="utf-8")
    with pytest.raises(ValueError, match="审核材料已改变"):
        validate_review(prepared)


def test_raw_response_tampering_invalidates_snapshot(prepared):
    task = json_read(prepared / "tasks.json")[0]
    (prepared / "source_snapshot" / task["evidence"]["raw_path"]).write_bytes(b"TEST tampered")
    with pytest.raises(ValueError, match="快照已变化"):
        validate_inputs(prepared)


def test_no_implicit_cli_call_and_one_attempt_marker(prepared, tmp_path, monkeypatch):
    bridge_root = tmp_path / "TEST_ONLY_CODEX_HOME/claude-bridge"
    bridge_root.mkdir(parents=True)
    (bridge_root / "Invoke-ClaudeAssist.ps1").write_text("TEST ONLY PLACEHOLDER NEVER EXECUTED", encoding="utf-8")
    monkeypatch.setenv("CODEX_HOME", str(bridge_root.parent))
    calls = []
    def fake_run(args, **kwargs):
        calls.append(args)
        assert args[0:3] == ["pwsh", "-NoProfile", "-File"]
        assert args[3] == str(bridge_root / "Invoke-ClaudeAssist.ps1")
        assert "Bypass" not in str(args)
        return SimpleNamespace(returncode=1, stdout=json.dumps({"ok": False, "code": "CLI_ERROR", "cli_exit_code": 1}), stderr="TEST ONLY stderr")
    monkeypatch.setattr("app.verification.subprocess.run", fake_run)
    receipt = invoke_reviewer(prepared)
    assert receipt["status"] == "failed" and len(calls) == 1
    with pytest.raises(FileExistsError):
        invoke_reviewer(prepared)
    assert len(calls) == 1
    assert "TEST ONLY stderr" not in (prepared / "call_receipt.json").read_text(encoding="utf-8")


def test_redaction_covers_key_and_bearer_patterns():
    assert "sk-TEST_ONLY_FAKE_KEY" not in redact("sk-TEST_ONLY_FAKE_KEY")
    assert "TEST_FAKE_BEARER" not in redact("Bearer TEST_FAKE_BEARER")


def test_positive_dry_run_uses_disposable_db_and_leaves_target_unchanged(prepared, tmp_path):
    fake_review(prepared)
    summarize_batch(prepared)
    db = tmp_path / "test_target.db"
    init_db(db)
    before = digest(db.read_bytes())
    result = dry_run(prepared, db, tmp_path / "dry")
    assert result["status"] == "passed_on_disposable_database_only"
    assert result["human_approved"] is False
    assert result["formal_database_writes"] == 0
    assert digest(db.read_bytes()) == before
    assert read_csv(tmp_path / "dry/pending_csv/aliases.csv")[0]["verification_status"] == "pending"


def test_approval_is_required_before_any_export_or_db_write(prepared, tmp_path):
    fake_review(prepared)
    summarize_batch(prepared)
    approval = prepared / "approval_template.json"
    target = tmp_path / "no.db"
    with pytest.raises(ValueError, match="明确人工批准"):
        import_approved(prepared, approval, tmp_path / "no-export", target)
    assert not target.exists()
    assert not (tmp_path / "no-export").exists()


def test_explicit_simulated_human_approval_imports_idempotently_and_keeps_audit(prepared, tmp_path):
    fake_review(prepared)
    summarize_batch(prepared)
    decision = tmp_path / "TEST_ONLY_HUMAN_DECISION.json"
    json_write(decision, decision_for(prepared))
    approval = tmp_path / "approval.json"
    record_approval(prepared, decision, approval)
    export, target = tmp_path / "approved", tmp_path / "TEST_ONLY.db"
    first = import_approved(prepared, approval, export, target)
    assert first["import"]["aliases"]["inserted"] == 1
    assert first["counts_delta"] == {t: 1 for t in ("people", "aliases", "sources", "evidences")}
    second = import_approved(prepared, approval, export, target)
    assert second["import"]["aliases"]["inserted"] == 0
    assert second["reused"]["aliases"] == 1
    assert second["counts_delta"] == {t: 0 for t in ("people", "aliases", "sources", "evidences")}
    assert second["backup"]["counts"] == first["counts_after"]
    assert digest(Path(second["backup"]["path"]).read_bytes()) == second["backup"]["sha256"]
    people = search_people(target, "仅测试字")
    assert len(people) == 1
    alias_id = json_read(prepared / "tasks.json")[0]["alias"]["alias_id"]
    assert get_alias(target, alias_id)["evidences"]
    with connect_db(target) as connection:
        assert connection.execute("SELECT dynasty FROM people").fetchone()[0] is None
        notes = json.loads(connection.execute("SELECT notes FROM evidences").fetchone()[0])
        assert notes["verification_batch_id"] == first["batch_id"]
        assert notes["approval_digest"] == digest(approval.read_bytes())
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_existing_review_conflict_never_overwrites_and_preserves_transaction(prepared, tmp_path):
    fake_review(prepared)
    summarize_batch(prepared)
    approval = tmp_path / "TEST_ONLY_APPROVAL.json"
    json_write(approval, decision_for(prepared))
    target = tmp_path / "TEST_ONLY.db"
    init_db(target)
    task = json_read(prepared / "tasks.json")[0]
    with connect_db(target) as connection:
        connection.execute("INSERT INTO people (person_id,canonical_name,verification_status) VALUES (?, 'TEST_ONLY_DIFFERENT_REVIEW', 'verified')", (task["person"]["person_id"],))
    before = digest(target.read_bytes())
    with pytest.raises(ValueError, match="不会覆盖"):
        import_approved(prepared, approval, tmp_path / "export", target)
    assert digest(target.read_bytes()) == before


def test_modified_export_cannot_rehash_itself_to_expand_approved_scope(prepared, tmp_path):
    fake_review(prepared)
    summarize_batch(prepared)
    approval = tmp_path / "TEST_ONLY_APPROVAL.json"
    json_write(approval, decision_for(prepared))
    export = tmp_path / "export"
    materialize_approved(prepared, approval, export)
    rows = read_csv(export / "people.csv")
    rows[0]["canonical_name"] = "TEST_ONLY_UNAPPROVED_REPLACEMENT"
    from app.importer import COLUMNS
    write_csv(export / "people.csv", COLUMNS["people"], rows)
    audit = json_read(export / "review_audit.json")
    audit["files"]["people.csv"] = digest((export / "people.csv").read_bytes())
    json_write(export / "review_audit.json", audit)
    target = tmp_path / "no.db"
    with pytest.raises(ValueError, match="不等于本批明确批准"):
        import_approved(prepared, approval, export, target)
    assert not target.exists()


def test_duplicate_quote_with_new_id_is_rejected_before_review(tmp_path, verification_source):
    rows = read_csv(verification_source / "evidence_pending.csv")
    write_csv(verification_source / "evidence_pending.csv", EVIDENCE_FIELDS, rows + [{**rows[0], "evidence_id": "TEST-DUPLICATE-ID"}])
    with pytest.raises(ValueError, match="重复引文"):
        prepare_batch(verification_source, tmp_path / "batch")


def test_same_canonical_name_and_shared_alias_keep_independent_ids_and_need_human(tmp_path, verification_source):
    people = read_csv(verification_source / "candidate_people.csv")
    aliases = read_csv(verification_source / "aliases_pending.csv")
    evidences = read_csv(verification_source / "evidence_pending.csv")
    other_person = {**people[0], "person_id": "TEST-SYNTHETIC-P2"}
    other_alias = {**aliases[0], "person_id": other_person["person_id"], "alias_id": "TEST-OTHER-ALIAS"}
    other_evidence = {**evidences[0], "person_id": other_person["person_id"], "alias_id": other_alias["alias_id"], "evidence_id": "TEST-OTHER-EVIDENCE"}
    write_csv(verification_source / "candidate_people.csv", PEOPLE_FIELDS, people + [other_person])
    write_csv(verification_source / "aliases_pending.csv", ALIAS_FIELDS, aliases + [other_alias])
    write_csv(verification_source / "evidence_pending.csv", EVIDENCE_FIELDS, evidences + [other_evidence])
    root = tmp_path / "batch"
    prepare_batch(verification_source, root)
    fake_review(root)
    summarize_batch(root)
    results = json_read(root / "parsed_results.json")
    assert {r["person_id"] for r in results} == {people[0]["person_id"], "TEST-SYNTHETIC-P2"}
    assert all(r["identity_ambiguity"] == "same_full_name_distinct_people" and r["mandatory_review"] for r in results)
    decision = decision_for(root)
    decision["accepted_high_risk_task_ids"] = []
    approval = tmp_path / "not-accepted.json"
    json_write(approval, decision)
    with pytest.raises(ValueError, match="高风险"):
        record_approval(root, approval, tmp_path / "approval.json")


def test_existing_richer_person_facts_are_not_erased_by_incremental_reimport(prepared, tmp_path):
    fake_review(prepared)
    summarize_batch(prepared)
    approval = tmp_path / "TEST_ONLY_APPROVAL.json"
    json_write(approval, decision_for(prepared))
    export, target = tmp_path / "export", tmp_path / "TEST_ONLY.db"
    import_approved(prepared, approval, export, target)
    with connect_db(target) as connection:
        connection.execute("UPDATE people SET native_place='TEST_ONLY_OLD_REVIEWED_PLACE'")
    result = import_approved(prepared, approval, export, target)
    assert result["reused"]["people"] == 1
    with connect_db(target) as connection:
        assert connection.execute("SELECT native_place FROM people").fetchone()[0] == "TEST_ONLY_OLD_REVIEWED_PLACE"


def test_formal_transaction_rolls_back_all_new_tables_on_final_evidence_failure(prepared, tmp_path):
    fake_review(prepared)
    summarize_batch(prepared)
    approval = tmp_path / "TEST_ONLY_APPROVAL.json"
    json_write(approval, decision_for(prepared))
    target = tmp_path / "TEST_ONLY.db"
    init_db(target)
    with connect_db(target) as connection:
        connection.execute("CREATE TRIGGER test_only_fail BEFORE INSERT ON evidences BEGIN SELECT RAISE(ABORT,'TEST ONLY forced evidence failure'); END")
    before = digest(target.read_bytes())
    with pytest.raises(ValueError, match="TEST ONLY forced evidence failure"):
        import_approved(prepared, approval, tmp_path / "export", target)
    assert digest(target.read_bytes()) == before
    with connect_db(target) as connection:
        assert all(connection.execute(f"SELECT count(*) FROM {t}").fetchone()[0] == 0 for t in ("people", "aliases", "sources", "evidences"))


def test_incremental_new_alias_reuses_person_and_source_without_replacing_old_review(tmp_path, verification_source):
    aliases = read_csv(verification_source / "aliases_pending.csv")
    evidences = read_csv(verification_source / "evidence_pending.csv")
    person = read_csv(verification_source / "candidate_people.csv")[0]
    extra_alias = {**aliases[0], "alias_id": "TEST-CANONICAL-ALIAS", "alias_name": person["canonical_name"], "normalized_name": person["canonical_name"], "alias_type": "name"}
    extra_evidence = {**evidences[0], "evidence_id": "TEST-CANONICAL-EVIDENCE", "alias_id": extra_alias["alias_id"], "alias_name": extra_alias["alias_name"], "alias_type": "name"}
    write_csv(verification_source / "aliases_pending.csv", ALIAS_FIELDS, aliases + [extra_alias])
    write_csv(verification_source / "evidence_pending.csv", EVIDENCE_FIELDS, evidences + [extra_evidence])
    root = tmp_path / "batch"
    prepare_batch(verification_source, root)
    fake_review(root)
    summarize_batch(root)
    target = tmp_path / "TEST_ONLY.db"
    for index, aid in enumerate((aliases[0]["alias_id"], extra_alias["alias_id"])):
        decision = decision_for(root)
        decision["approved_alias_ids"] = [aid]
        approval = tmp_path / f"TEST_ONLY_APPROVAL_{index}.json"
        json_write(approval, decision)
        result = import_approved(root, approval, tmp_path / f"export-{index}", target)
        assert result["import"]["aliases"]["inserted"] == 1
        assert {r["alias_id"] for r in read_csv(tmp_path / f"export-{index}" / "aliases.csv")} == {aid}
        assert {r["alias_id"] for r in read_csv(tmp_path / f"export-{index}" / "evidences.csv")} == {aid}
        if index == 1:
            assert result["reused"]["people"] == 1
            assert result["reused"]["sources"] == 1
    with connect_db(target) as connection:
        assert connection.execute("SELECT count(*) FROM people").fetchone()[0] == 1
        assert connection.execute("SELECT count(*) FROM aliases").fetchone()[0] == 2
        assert connection.execute("SELECT count(*) FROM evidences").fetchone()[0] == 2


def test_model_summary_count_is_not_used_as_actual_review_count(prepared):
    fake_review(prepared, alteration=lambda a: a.update(summary="local_saved_response_only 核验2个任务"))
    summary = summarize_batch(prepared)
    assert summary["actual_subagent_judgments"] == 1
    assert summary["external_summary_count_claim"] == 2
    assert summary["summary_count_discrepancy"] is True


def test_free_text_with_unspecified_identity_is_kept_uncertain(prepared):
    fake_review(prepared, alteration=lambda a: a["findings"][0].update(evidence="V|H|1|-|E|仅测试史传记其字|D1"))
    summary = summarize_batch(prepared, allow_free_text_narration=True)
    assert summary["actual_subagent_judgments"] == 1
    assert summary["ai_verified"] == 0
    row = json_read(prepared / "parsed_results.json")[0]
    assert row["relation_type"] == "仅测试史传记其字"
    assert row["verdict"] == "uncertain"
    assert row["raw_identity_ambiguity"] == "-"
