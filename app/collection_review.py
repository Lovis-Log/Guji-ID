"""Explicit human decisions -> existing four-table importer format."""

import json
from datetime import datetime
from pathlib import Path

from app.collector import digest, plain, read_csv, source_location, write_csv
from app.importer import COLUMNS, import_batch

STATUSES = {"待审核", "通过", "存疑", "拒绝"}
AUDIT = "review_audit.json"


def safe_file(root, relative):
    target = (root / relative).resolve()
    if not target.is_relative_to(root.resolve()) or not target.is_file():
        raise ValueError(f"原始资料路径缺失或越界：{relative}")
    return target


def approved(rows, kind):
    result = {}
    key = {"people": "person_id", "aliases": "alias_id", "evidences": "evidence_id"}[kind]
    for row in rows:
        if not row.get(key) or row[key] in result:
            raise ValueError(f"{kind} ID 为空或重复")
        # Include pending IDs in duplicate detection as well.
        result[row[key]] = row
        if row.get("review_status") not in STATUSES:
            raise ValueError(f"{row[key]}：审核状态应为 待审核/通过/存疑/拒绝")
        if row["review_status"] == "通过":
            if not row.get("reviewer", "").strip():
                raise ValueError(f"{row[key]}：通过记录缺少审核人")
            try:
                timestamp = datetime.fromisoformat(row.get("reviewed_at", ""))
                if timestamp.utcoffset() is None:
                    raise ValueError("缺时区")
            except ValueError as exc:
                raise ValueError(f"{row[key]}：reviewed_at 应为带时区 ISO 时间") from exc
    return {k: row for k, row in result.items() if row["review_status"] == "通过"}


def check_provenance(root, evidence):
    raw = safe_file(root, evidence["raw_path"]).read_bytes()
    text = safe_file(root, evidence["text_path"]).read_text(encoding="utf-8")
    parsed = json.loads(raw)["parse"]
    left, right = int(evidence["start_offset"]), int(evidence["end_offset"])
    if (digest(raw) != evidence["raw_sha256"] or digest(text) != evidence["text_sha256"]
            or plain(parsed["text"]["*"]) != text
            or str(parsed["revid"]) != evidence["revision_id"]
            or evidence["source_url"] != f"https://zh.wikisource.org/w/index.php?oldid={parsed['revid']}"
            or evidence["book_title"] != source_location({"title": parsed["title"], "html": parsed["text"]["*"]})[0]
            or not 0 <= left < right <= len(text)
            or text[left:right] != evidence["original_text"]
            or evidence["alias_name"] not in evidence["original_text"]):
        raise ValueError(f"{evidence['evidence_id']}：原始内容、修订链接、哈希或偏移校验失败")


def export_reviewed(input_dir, output_dir):
    root, output = Path(input_dir), Path(output_dir)
    if output.exists():
        raise ValueError("审核导出目录已存在；使用新目录保留审核历史")
    people = approved(read_csv(root / "candidate_people.csv"), "people")
    aliases = approved(read_csv(root / "aliases_pending.csv"), "aliases")
    evidences = approved(read_csv(root / "evidence_pending.csv"), "evidences")
    if not aliases:
        raise ValueError("没有人工通过的别称；不会生成导入批次或写入数据库")
    sources = {}
    for alias in aliases.values():
        if alias["person_id"] not in people:
            raise ValueError(f"{alias['alias_id']}：对应人物尚未审核通过")
        if not any(e["alias_id"] == alias["alias_id"] and e["usage_relation"] == "supports" and e["evidence_nature"] == "direct" for e in evidences.values()):
            raise ValueError(f"{alias['alias_id']}：缺少人工通过的 supports/direct 证据；仅出现名称不够")
    for evidence in evidences.values():
        alias = aliases.get(evidence["alias_id"])
        if alias is None or any(evidence[k] != alias[k] for k in ("person_id", "alias_name", "alias_type")):
            raise ValueError(f"{evidence['evidence_id']}：别称/人物审核链不一致")
        check_provenance(root, evidence)
        if not (evidence["volume"].strip() or evidence["chapter"].strip()):
            raise ValueError("已通过证据必须有卷次或篇目定位")
        source = dict(zip(COLUMNS["sources"], (evidence["source_id"], evidence["book_title"], evidence["volume"], evidence["chapter"], evidence["source_platform"], evidence["source_url"], f"维基文库修订 {evidence['revision_id']}")))
        if source["source_id"] in sources and sources[source["source_id"]] != source:
            raise ValueError("同 source_id 的来源内容冲突")
        sources[source["source_id"]] = source
    used_people = {a["person_id"] for a in aliases.values()}
    tables = {"people": [{k: row.get(k, "") for k in COLUMNS["people"]} for row in people.values() if row["person_id"] in used_people], "aliases": [{k: row.get(k, "") for k in COLUMNS["aliases"]} for row in aliases.values()], "sources": list(sources.values()), "evidences": []}
    for evidence in evidences.values():
        notes = json.dumps({k: evidence.get(k, "") for k in ("context", "relation_type", "evidence_type", "retrieval_date", "assessment", "remarks", "raw_path", "raw_sha256", "text_path", "text_sha256", "start_offset", "end_offset", "reviewer", "reviewed_at")}, ensure_ascii=False)
        tables["evidences"].append({**{k: evidence.get(k, "") for k in COLUMNS["evidences"]}, "notes": notes})
    for table in ("people", "aliases", "evidences"):
        for row in tables[table]:
            # This transition is allowed only after explicit review above.
            row["verification_status"] = "verified"
    output.mkdir(parents=True)
    hashes = {}
    for table, rows in tables.items():
        path = output / f"{table}.csv"
        write_csv(path, COLUMNS[table], rows)
        hashes[path.name] = digest(path.read_bytes())
    audit = {"input_dir": str(root.resolve()), "files": hashes, "decisions": {kind: [{key: row[key] for key in (COLUMNS[kind][0], "reviewer", "reviewed_at")} for row in collection.values()] for kind, collection in (("people", people), ("aliases", aliases), ("evidences", evidences))}}
    (output / AUDIT).write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
    return {table: len(rows) for table, rows in tables.items()}


def import_reviewed(input_dir, db_path):
    root = Path(input_dir)
    try:
        audit = json.loads((root / AUDIT).read_text(encoding="utf-8"))
        for table in COLUMNS:
            name = table + ".csv"
            if digest((root / name).read_bytes()) != audit["files"][name]:
                raise ValueError(f"审核导出后 CSV 已改变：{name}；请重新导出")
            rows = read_csv(root / name)
            if table != "sources":
                if any(row["verification_status"] != "verified" for row in rows):
                    raise ValueError("只允许全部已审核记录进入此导入入口")
                decisions = {r[COLUMNS[table][0]]: r for r in audit["decisions"][table]}
                for row in rows:
                    decision = decisions.get(row[COLUMNS[table][0]], {})
                    if not decision.get("reviewer") or not decision.get("reviewed_at"):
                        raise ValueError("缺少人工审核审计记录")
        if not read_csv(root / "aliases.csv"):
            raise ValueError("空审核批次不得创建正式数据库")
    except (KeyError, TypeError, json.JSONDecodeError, OSError) as exc:
        raise ValueError("缺少完整审核导出文件/审计记录；先运行 export_reviewed") from exc
    return import_batch(root, db_path)
