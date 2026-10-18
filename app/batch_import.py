"""Batch approval and incremental import over the existing review/import modules.

AI review never writes verified records by itself. Approval is an explicit local
human decision, bound to the sealed evidence package (not an identity signature).
"""

import json
import shutil
import sqlite3
import tempfile
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from app.collection_review import export_reviewed, import_reviewed
from app.collector import ALIAS_FIELDS, EVIDENCE_FIELDS, PEOPLE_FIELDS, digest, read_csv, write_csv
from app.db import PROJECT_ROOT, TABLES
from app.importer import COLUMNS, _APPROVED_BATCH, _read_table, import_batch
from app.verification import check_task, json_read, json_write, validate_review

APPROVAL_STATEMENT = "我已抽查代表性原文、处理所选记录的高风险问题，并批准本清单的独立人物与别称归属；AI 结论不代替原始文献。"


def plan(batch_dir):
    batch, tasks, results = validate_review(batch_dir)
    by_task = {t["task_id"]: t for t in tasks}
    selected = []
    for row in results:
        task = by_task[row["task_id"]]
        if (row["ai_status"] == "ai_verified" and row["verdict"] == "verified" and row["model_returned"] is True
                and row["original_text_match"] is True and task["program"]["ok"] is True
                and row["relationship_strength"] == "explicit" and row["codex_check"] == "explicit_marker_in_located_context"
                and row["task_digest"] == task["task_digest"]):
            if not check_task(Path(batch_dir) / "source_snapshot", task["person"], task["alias"], task["evidence"])["ok"]:
                raise ValueError("拟导入证据未通过当前原始资料复查")
            selected.append((task, row))
    return batch, selected


def proposal_tables(batch, selected):
    tables = {t: {} for t in TABLES}
    for task, result in selected:
        p, a, e = task["person"], task["alias"], task["evidence"]
        # Seed dynasty/identity hints are not reviewed historical facts.
        person = {k: "" for k in COLUMNS["people"]}
        person.update(person_id=p["person_id"], canonical_name=p["canonical_name"], verification_status="pending")
        tables["people"][p["person_id"]] = person
        tables["aliases"][a["alias_id"]] = {**{k: a[k] for k in COLUMNS["aliases"]}, "verification_status": "pending"}
        source = dict(zip(COLUMNS["sources"], (e["source_id"], e["book_title"], e["volume"], e["chapter"], e["source_platform"], e["source_url"], f"维基文库修订 {e['revision_id']}")))
        if e["source_id"] in tables["sources"] and tables["sources"][e["source_id"]] != source:
            raise ValueError("相同来源 ID 内容冲突")
        tables["sources"][e["source_id"]] = source
        notes = json.dumps({"relation_type": result["relation_type"], "evidence_type": result["evidence_type"], "context": e["context"], "remarks": result["explanation"], "verification_batch_id": batch["batch_id"], "verification_task_id": task["task_id"], "approval_status": "awaiting_human_approval", "collector_usage_relation": e["usage_relation"], "collector_evidence_nature": e["evidence_nature"]}, ensure_ascii=False)
        tables["evidences"][e["evidence_id"]] = dict(zip(COLUMNS["evidences"], (e["evidence_id"], a["alias_id"], e["source_id"], e["original_text"], "supports", "direct", "pending", notes)))
    return {table: list(rows.values()) for table, rows in tables.items()}


def write_tables(directory, tables):
    directory = Path(directory)
    directory.mkdir(parents=True)
    for table in TABLES:
        write_csv(directory / f"{table}.csv", COLUMNS[table], tables[table])


@contextmanager
def existing_connection(path):
    path = Path(path).resolve()
    connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        yield connection
    finally:
        connection.close()


def comparable_notes(value):
    try:
        obj = json.loads(value or "{}")
    except ValueError:
        return value
    if not isinstance(obj, dict):
        return obj
    omitted = {"verification_batch_id", "verification_task_id", "approval_digest", "approval_status", "reviewer", "reviewed_at"}
    return {k: v for k, v in obj.items() if k not in omitted}


def incremental_tables(directory, db_path):
    parsed = {table: [row for _, row in _read_table(Path(directory), table)] for table in TABLES}
    reused = {table: 0 for table in TABLES}
    if not Path(db_path).exists():
        return parsed, reused
    with existing_connection(db_path) as connection:
        remaining = {table: [] for table in TABLES}
        for table in TABLES:
            for row in parsed[table]:
                current = connection.execute(f"SELECT * FROM {table} WHERE {COLUMNS[table][0]} = ?", (row[COLUMNS[table][0]],)).fetchone()
                if current is None:
                    if table == "evidences":
                        source = next((s for s in parsed["sources"] if s["source_id"] == row["source_id"]), None)
                        if source and connection.execute("SELECT 1 FROM evidences e JOIN sources s USING(source_id) WHERE e.alias_id=? AND s.original_url=? AND e.original_text=? LIMIT 1", (row["alias_id"], source["original_url"], row["original_text"])).fetchone():
                            raise ValueError("既有相同关系/来源/引文使用了不同证据 ID；请复用既有 ID，禁止重复导入")
                    remaining[table].append(row)
                    continue
                for key in COLUMNS[table]:
                    # An unknown optional proposal never erases richer old facts.
                    if table == "people" and row[key] is None:
                        continue
                    same = comparable_notes(current[key]) == comparable_notes(row[key]) if table == "evidences" and key == "notes" else current[key] == row[key]
                    if not same:
                        raise ValueError(f"{table} 的 {row[COLUMNS[table][0]]} 与既有审核记录冲突；不会覆盖")
                reused[table] += 1
    return remaining, reused


def dry_run(batch_dir, db_path, output_dir, alias_ids=None):
    output = Path(output_dir)
    if output.exists():
        raise ValueError("dry-run 输出已存在")
    batch, selected = plan(batch_dir)
    if alias_ids is not None:
        eligible = {t["alias"]["alias_id"] for t, _ in selected}
        if not isinstance(alias_ids, list) or not alias_ids or len(set(alias_ids)) != len(alias_ids) or not set(alias_ids) <= eligible:
            raise ValueError("预演关系清单为空、重复或包含未通过的关系")
        selected = [(t, r) for t, r in selected if t["alias"]["alias_id"] in alias_ids]
    tables = proposal_tables(batch, selected)
    write_tables(output / "pending_csv", tables)
    report = {"batch_id": batch["batch_id"], "mode": "dry_run", "human_approved": False, "formal_database_writes": 0, "proposed_counts": {t: len(r) for t, r in tables.items()}, "status": "blocked_no_ai_verified_records" if not selected else "validation_pending", "simulated": None}
    if selected:
        work = PROJECT_ROOT / "work"
        work.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="verification-dry-run-", dir=work) as temporary:
            temp = Path(temporary)
            clone = temp / "dry_run_only.db"
            if Path(db_path).exists():
                with existing_connection(db_path) as source, closing(sqlite3.connect(clone)) as destination:
                    source.backup(destination)
            # Verified states exist only in this disposable simulation; no human
            # reviewer is fabricated and the exported proposal remains pending.
            simulated = {table: [{**r, **({"verification_status": "verified"} if table != "sources" else {})} for r in rows] for table, rows in tables.items()}
            write_tables(temp / "simulated_csv", simulated)
            incremental, reused = incremental_tables(temp / "simulated_csv", clone)
            write_tables(temp / "incremental_csv", incremental)
            report["simulated"] = {"import": import_batch(temp / "incremental_csv", clone), "reused": reused}
            report["status"] = "passed_on_disposable_database_only"
    json_write(output / "dry_run.json", report)
    return report


def database_counts(db_path):
    if not Path(db_path).exists():
        return {table: 0 for table in TABLES}
    with existing_connection(db_path) as connection:
        return {table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in TABLES}


def backup_database(db_path, backup_dir):
    """Use SQLite backup so committed WAL data is included; fail before import."""
    if not Path(db_path).exists():
        return None
    directory = Path(backup_dir)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + "-" + uuid4().hex + ".db")
    with existing_connection(db_path) as source, closing(sqlite3.connect(target)) as destination:
        source.backup(destination)
        if destination.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("导入前备份完整性检查失败，停止导入")
    return {"path": str(target.resolve()), "sha256": digest(target.read_bytes()), "counts": database_counts(target)}


def validate_decision(batch_dir, decision):
    root = Path(batch_dir)
    batch, selected = plan(root)
    if not selected:
        raise ValueError("没有有效 AI 核验通过的关系；不能以批次批准绕过外部失败或缺证")
    if decision.get("batch_id") != batch["batch_id"] or decision.get("review_seal_sha256") != digest((root / "review_seal.json").read_bytes()):
        raise ValueError("批准没有绑定当前批次和审核材料哈希")
    if decision.get("decision") != "approved" or not isinstance(decision.get("approver"), str) or not decision["approver"].strip() or decision.get("statement") != APPROVAL_STATEMENT:
        raise ValueError("缺少明确人工批准、审核人或批准声明")
    try:
        approved_at = datetime.fromisoformat(decision["approved_at"])
        if approved_at.utcoffset() is None or (approved_at - datetime.now(timezone.utc)).total_seconds() > 300:
            raise ValueError("无时区或未来时间")
    except (ValueError, KeyError, TypeError) as exc:
        raise ValueError("批准时间应为实际的带时区 ISO 时间") from exc
    for field in ("approved_alias_ids", "checked_task_ids", "accepted_high_risk_task_ids"):
        if not isinstance(decision.get(field), list) or any(not isinstance(x, str) for x in decision[field]) or len(set(decision[field])) != len(decision[field]):
            raise ValueError(f"批准清单格式错误：{field}")
    approved = set(decision["approved_alias_ids"])
    eligible = {t["alias"]["alias_id"] for t, _ in selected}
    if not approved or not approved <= eligible:
        raise ValueError("批准清单为空或包含未通过的关系")
    _, all_tasks, results = validate_review(root)
    tids = {t["task_id"] for t in all_tasks}
    checked, accepted = set(decision["checked_task_ids"]), set(decision["accepted_high_risk_task_ids"])
    samples = {r["task_id"] for r in read_csv(root / "spot_checks.csv")}
    if not samples <= checked or not checked <= tids or not accepted <= checked:
        raise ValueError("代表性抽查样本尚未全部确认，或含未知任务")
    mandatory = {r["task_id"] for r in results if r["alias_id"] in approved and r["mandatory_review"]}
    if not mandatory <= accepted:
        raise ValueError("所选关系的全部高风险/分歧记录尚未逐项确认")
    return batch, [(t, r) for t, r in selected if t["alias"]["alias_id"] in approved]


def record_approval(batch_dir, decision_path, output_path):
    decision = json_read(decision_path)
    validate_decision(batch_dir, decision)
    output = Path(output_path)
    if output.exists():
        raise ValueError("批准记录已存在；不覆盖旧审核结果")
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as handle:
        json.dump(decision, handle, ensure_ascii=False, indent=2)
    return {"approval_path": str(output.resolve()), "approval_sha256": digest(output.read_bytes()), "approved_aliases": len(decision["approved_alias_ids"]), "database_writes": 0}


def materialize_approved(batch_dir, approval_path, output_dir):
    root, output = Path(batch_dir), Path(output_dir)
    approval = json_read(approval_path)
    batch, selected = validate_decision(root, approval)
    if output.exists():
        raise ValueError("已批准四表输出目录存在，不覆盖审核文件")
    selected_alias = {t["alias"]["alias_id"] for t, _ in selected}
    selected_people = {t["person"]["person_id"] for t, _ in selected}
    selected_evidence = {t["evidence"]["evidence_id"]: (t, r) for t, r in selected}
    work = PROJECT_ROOT / "work"
    work.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="approved-review-copy-", dir=work) as temporary:
        review_copy = Path(temporary) / "source"
        shutil.copytree(root / "source_snapshot", review_copy)
        specs = (("candidate_people.csv", PEOPLE_FIELDS, "person_id", selected_people), ("aliases_pending.csv", ALIAS_FIELDS, "alias_id", selected_alias), ("evidence_pending.csv", EVIDENCE_FIELDS, "evidence_id", set(selected_evidence)))
        for filename, fields, key, accepted in specs:
            rows = read_csv(review_copy / filename)
            for row in rows:
                if row[key] not in accepted:
                    continue
                row.update(review_status="通过", reviewer=approval["approver"], reviewed_at=approval["approved_at"])
                if key == "person_id":
                    for field in ("dynasty", "birth_year", "death_year", "native_place", "identity_description"):
                        row[field] = ""
                if key == "evidence_id":
                    _, result = selected_evidence[row[key]]
                    row.update(usage_relation="supports", evidence_nature="direct", relation_type=result["relation_type"], evidence_type=result["evidence_type"], remarks=result["explanation"])
            write_csv(review_copy / filename, fields, rows)
        export_reviewed(review_copy, output)
    rows = read_csv(output / "evidences.csv")
    for row in rows:
        task, result = selected_evidence[row["evidence_id"]]
        notes = json.loads(row["notes"])
        notes.update(verification_batch_id=batch["batch_id"], verification_task_id=task["task_id"], approval_digest=digest(Path(approval_path).read_bytes()), reviewer_model=result["reviewer_model"], source_scope="local_saved_response_only", collector_usage_relation=task["evidence"]["usage_relation"], collector_evidence_nature=task["evidence"]["evidence_nature"])
        row["notes"] = json.dumps(notes, ensure_ascii=False)
    write_csv(output / "evidences.csv", COLUMNS["evidences"], rows)
    shutil.copyfile(approval_path, output / "approval.json")
    audit = json_read(output / "review_audit.json")
    audit["input_dir"] = str((root / "source_snapshot").resolve())
    audit["files"]["evidences.csv"] = digest((output / "evidences.csv").read_bytes())
    audit["verification_batch_id"] = batch["batch_id"]
    audit["approval_sha256"] = digest((output / "approval.json").read_bytes())
    json_write(output / "review_audit.json", audit)
    return {"batch_id": batch["batch_id"], "files": audit["files"], "approval_sha256": audit["approval_sha256"]}


def import_approved(batch_dir, approval_path, export_dir, db_path):
    # Validate approval before creating/opening a writable formal database.
    validate_decision(batch_dir, json_read(approval_path))
    export = Path(export_dir)
    if not export.exists():
        materialize_approved(batch_dir, approval_path, export)
    audit = json_read(export / "review_audit.json")
    batch, _ = plan(batch_dir)
    if audit.get("verification_batch_id") != batch["batch_id"] or audit.get("approval_sha256") != digest(Path(approval_path).read_bytes()) or digest((export / "approval.json").read_bytes()) != audit["approval_sha256"]:
        raise ValueError("四表导出与批准批次不一致")
    for table in TABLES:
        if digest((export / f"{table}.csv").read_bytes()) != audit["files"][f"{table}.csv"]:
            raise ValueError("已批准 CSV 被修改")
    # Existing verifier checks the audit and all reviewed flags; do this on a
    # disposable database before the formal transaction, preserving its gate.
    work = PROJECT_ROOT / "work"
    with tempfile.TemporaryDirectory(prefix="verified-incremental-", dir=work) as temporary:
        temp = Path(temporary)
        # Bind actual rows to the sealed approved selection, rather than trusting
        # an editable audit file that merely re-hashes a changed CSV.
        materialize_approved(batch_dir, approval_path, temp / "expected_export")
        if audit != json_read(temp / "expected_export/review_audit.json"):
            raise ValueError("四表导出不等于本批明确批准的记录")
        import_reviewed(export, temp / "reviewed_validation.db")
        incremental, reused = incremental_tables(export, db_path)
        write_tables(temp / "csv", incremental)
        before = database_counts(db_path)
        backup = backup_database(db_path, export / "pre_import_backups")
        result = import_batch(temp / "csv", db_path, _approval=_APPROVED_BATCH)
        after = database_counts(db_path)
    return {"batch_id": batch["batch_id"], "import": result, "reused": reused, "approval_sha256": audit["approval_sha256"], "backup": backup, "counts_before": before, "counts_after": after, "counts_delta": {t: after[t] - before[t] for t in TABLES}}
