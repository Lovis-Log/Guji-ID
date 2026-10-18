"""Read-only local provenance/foreign-key audit. No HTTP and no DB writes."""

import argparse
import json
from pathlib import Path

from app.collection_review import check_provenance
from app.collector import digest, read_csv


def check(directory):
    root = Path(directory)
    people = read_csv(root / "candidate_people.csv")
    aliases = read_csv(root / "aliases_pending.csv")
    evidences = read_csv(root / "evidence_pending.csv")
    for rows, key in ((people, "person_id"), (aliases, "alias_id"), (evidences, "evidence_id")):
        if len({r[key] for r in rows}) != len(rows):
            raise ValueError(f"重复 {key}")
        if any(r["verification_status"] != "pending" for r in rows):
            raise ValueError("采集文件中出现了非 pending 的核对状态")
    pids = {p["person_id"] for p in people}
    by_alias = {a["alias_id"]: a for a in aliases}
    unique = {(a["person_id"], a["alias_name"], a["alias_type"]) for a in aliases}
    if len(unique) != len(aliases) or any(a["person_id"] not in pids for a in aliases):
        raise ValueError("重复别称关系或人物外键缺失")
    for evidence in evidences:
        alias = by_alias.get(evidence["alias_id"])
        if not alias or any(alias[k] != evidence[k] for k in ("person_id", "alias_name", "alias_type")):
            raise ValueError("证据人物/别称链错配")
        check_provenance(root, evidence)
    raw_count = 0
    for meta_path in (root / "raw").glob("*.json"):
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        if digest(meta_path.with_suffix(".body").read_bytes()) != meta["sha256"]:
            raise ValueError(f"原始响应哈希不符：{meta_path.name}")
        raw_count += 1
    return {"people": len(people), "aliases": len(aliases), "evidences_checked": len(evidences), "raw_responses_checked": raw_count, "provenance": "passed", "historical_facts": "awaiting_human_review"}


def main():
    parser = argparse.ArgumentParser(description="离线核查采集文件、独立身份和原文溯源；不写数据库")
    parser.add_argument("--input-dir", required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(check(args.input_dir), ensure_ascii=False, indent=2))
    except (ValueError, OSError, KeyError) as exc:
        parser.exit(1, f"溯源检查失败：{exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
