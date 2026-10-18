"""Translate user-completed review rows into the existing approval contract.

No model calls, approval recording, or database writes. Never invent decisions.
"""
import argparse
import json
from pathlib import Path

from app.batch_import import APPROVAL_STATEMENT, validate_decision
from app.collector import digest, read_csv
from app.verification import json_read, validate_review
from scripts.prepare_stage45_review import DECISIONS, REVIEW


def build_decision(worksheet_dir, approver, approved_at, review=REVIEW):
    root, review = Path(worksheet_dir), Path(review)
    manifest = json_read(root / "review_manifest.json")
    batch, _, results = validate_review(review)
    if manifest["batch_id"] != batch["batch_id"] or manifest["review_seal_sha256"] != digest((review / "review_seal.json").read_bytes()):
        raise ValueError("工作表不属于当前封存核验包")
    expected = {r["alias_id"]: {k: str(v) for k, v in r.items()} for r in manifest["immutable_rows"]}
    rows = read_csv(root / "human_review_worksheet.csv")
    if len(rows) != len(expected) or {r["alias_id"] for r in rows} != set(expected):
        raise ValueError("工作表关系缺失或重复")
    for r in rows:
        if {k: v for k, v in r.items() if k not in {"human_decision", "human_notes"}} != expected[r["alias_id"]]:
            raise ValueError("工作表史料、建议或定位字段已改动；须另建修订包，不能在批准时改证据")
        if r["human_decision"] not in DECISIONS:
            raise ValueError("人工决定只允许 APPROVED/REJECTED/HOLD/PENDING")
    approved = [r for r in rows if r["human_decision"] == "APPROVED"]
    if not approved:
        raise ValueError("没有人工 APPROVED 关系；不生成批准决定")
    if any(r["review_group"] != "PRIORITY_REVIEW" for r in approved):
        raise ValueError("本阶段小批次只接受首批推荐关系；暂缓和候补须另行复核授权")
    if any(not r["human_notes"].strip() for r in approved):
        raise ValueError("每条批准须填写核对依据和身份区分备注")
    spots = read_csv(root / "mandatory_spot_checks.csv")
    required_spots = set(manifest["spot_task_ids"])
    if len(spots) != len(required_spots) or {s["task_id"] for s in spots} != required_spots:
        raise ValueError("代表性抽查清单缺失或重复")
    if any(s["check_completed"] != "CHECKED" or not s["human_notes"].strip() for s in spots):
        raise ValueError("八条代表性/争议记录须实际核对，填写 CHECKED 和备注；不代表批准")
    aids = {r["alias_id"] for r in approved}
    decision = json_read(review / "approval_template.json")
    decision.update(decision="approved", approver=approver, approved_at=approved_at,
                    approved_alias_ids=sorted(aids), checked_task_ids=sorted(required_spots | {r["task_id"] for r in approved}),
                    accepted_high_risk_task_ids=sorted(r["task_id"] for r in results if r["alias_id"] in aids and r["mandatory_review"]),
                    statement=APPROVAL_STATEMENT)
    validate_decision(review, decision)
    return decision


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worksheet-dir", required=True)
    parser.add_argument("--approver", required=True)
    parser.add_argument("--approved-at", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    try:
        decision = build_decision(args.worksheet_dir, args.approver, args.approved_at)
        path = Path(args.output)
        with path.open("x", encoding="utf-8") as f:
            json.dump(decision, f, ensure_ascii=False, indent=2)
    except (ValueError, OSError, KeyError) as exc:
        parser.exit(1, f"人工决定准备被拒：{exc}\n")
    print("仅生成明确人工决定文件；尚未记录批准、尚未入库。")


if __name__ == "__main__":
    main()
