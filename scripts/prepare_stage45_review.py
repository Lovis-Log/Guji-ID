"""Offline review preparation. Never calls a model or writes any database.

CSV here is an audit interchange format, compatible with the stage-four sheet.
The original package and its approval template remain untouched.
"""

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path

from app.batch_import import plan
from app.collector import digest, read_csv, write_csv
from app.verification import check_task, json_read, json_write, validate_review

FINAL = Path("data/verifications/stage4-final-report")
REVIEW = Path("data/verifications/stage4-tang-song-20-reviewed")
COLLECTIONS = [Path("data/collections/2026-10-16-delivery"), Path("data/collections/stage4-tang-song-20")]
DECISIONS = {"APPROVED", "REJECTED", "HOLD", "PENDING"}


def prepare(output, final=FINAL, review=REVIEW, collections=COLLECTIONS):
    output, final, review = Path(output), Path(final), Path(review)
    # Existing baseline files are allowed; review artifacts must never overwrite.
    if (output / "human_review_worksheet.csv").exists():
        raise ValueError("审核工作表已存在，不覆盖人工决定；请使用新输出目录")
    batch, tasks, results = validate_review(review)
    _, eligible = plan(review)
    people = {p["person_id"]: p for p in read_csv(final / "cumulative_candidate_people.csv")}
    aliases, evidence_by_alias, inventory = {}, defaultdict(list), []
    checks = []
    for root in map(Path, collections):
        pp = {p["person_id"]: p for p in read_csv(root / "candidate_people.csv")}
        aa = {a["alias_id"]: a for a in read_csv(root / "aliases_pending.csv")}
        if aliases.keys() & aa.keys():
            raise ValueError("跨批次关系 ID 重复，停止生成")
        aliases.update(aa)
        for e in read_csv(root / "evidence_pending.csv"):
            check = check_task(root, pp[e["person_id"]], aa[e["alias_id"]], e)
            checks.append({"evidence_id": e["evidence_id"], **check})
            item = {**e, "collection_root": root.as_posix(), "deterministic_ok": check["ok"], "deterministic_issues": ";".join(check["issues"])}
            evidence_by_alias[e["alias_id"]].append(item)
            inventory.append(item)
    if set(aliases) != {r["alias_id"] for r in read_csv(final / "human_review_worksheet.csv")}:
        raise ValueError("阶段四工作表与候选关系清单不一致")
    result_by_alias = {r["alias_id"]: r for r in results}
    task_by_alias = {t["alias"]["alias_id"]: t for t in tasks}
    if any(not c["ok"] for c in checks):
        raise ValueError("候选证据未通过确定性校验，停止准备审核批次")
    for t in tasks:
        original = next((e for e in evidence_by_alias[t["alias"]["alias_id"]] if e["evidence_id"] == t["evidence"]["evidence_id"]), None)
        fields = ("person_id", "alias_id", "alias_name", "alias_type", "original_text", "source_url", "book_title", "volume", "chapter", "raw_sha256", "text_sha256", "start_offset", "end_offset")
        if original is None or any(original[k] != t["evidence"][k] for k in fields):
            raise ValueError("当前候选证据与第四阶段封存核验输入不同")
    cases = json_read(final / "identity_cases.json")
    case_people = {m["person_id"] for c in cases for m in c["members"]}
    homonyms = {m["person_id"] for c in cases if c["case_type"] == "full_name_homonym" for m in c["members"]}
    uncertain_people = {r["person_id"] for r in results if r["verdict"] != "verified"}
    # A disputed NAME relation does not upgrade itself via an approved courtesy
    # name. Keep its case slot, with only independently eligible relations.
    held_people = uncertain_people - homonyms
    eligible_by_person = defaultdict(list)
    for t, r in eligible:
        if t["person"]["person_id"] not in held_people:
            eligible_by_person[t["person"]["person_id"]].append((t, r))
    chosen = {pid for pid in case_people if pid in eligible_by_person}
    # Quality gate first, then balanced Tang/Song coverage and stable ordering.
    while len(chosen) < 10:
        remaining = set(eligible_by_person) - chosen
        if not remaining:
            break
        dynasty_counts = Counter(people[pid]["dynasty"] for pid in chosen)
        def rank(pid):
            p = people[pid]
            is_tang_song = p["dynasty"] in {"唐", "宋"}
            shortest = min(len(t["evidence"]["original_text"]) for t, _ in eligible_by_person[pid])
            return (not is_tang_song, dynasty_counts[p["dynasty"]], shortest, p["canonical_name"], pid)
        chosen.add(min(remaining, key=rank))
    priority_aliases = {t["alias"]["alias_id"] for pid in chosen for t, _ in eligible_by_person[pid]}
    eligible_ids = {t["alias"]["alias_id"] for t, _ in eligible}
    previous = {r["alias_id"]: r for r in read_csv(final / "human_review_worksheet.csv")}
    rows = []
    for aid, alias in aliases.items():
        pid = alias["person_id"]
        person, old = people[pid], previous[aid]
        result, task = result_by_alias.get(aid), task_by_alias.get(aid)
        es = evidence_by_alias[aid]
        if aid in priority_aliases:
            group, suggestion = "PRIORITY_REVIEW", "REVIEW_FOR_APPROVAL"
        elif result is None:
            group, suggestion = "OLD_NOT_REVERIFIED", "HOLD"
        elif aid not in eligible_ids or pid in held_people:
            group, suggestion = "HOLD", "HOLD"
        else:
            group, suggestion = "RESERVE", "REVIEW_LATER"
        risk = old.get("needs_human_action", "")
        if result and result.get("disagreement"):
            risk += "；" + result["disagreement"]
        if pid in held_people:
            risk += "；同人物存在未闭环争议，本首批连同其他已 AI 通过关系一并暂缓；不篡改既有 AI 状态"
        if pid in homonyms:
            risk += "；唐宋王珪必须分别核对字、籍贯及篇目，禁止合并；唐姓名存疑不随叔玠通过"
        if result is None:
            risk += "；旧23条本轮未重核，不具有本批机器通过资格"
        if not es:
            risk += "；只有发现线索，无可定位古籍正文"
        row = {**old, "dynasty": person["dynasty"], "batch_id": batch["batch_id"] if result else "",
               "task_id": task["task_id"] if task else "", "review_group": group,
               "book_title": " | ".join(dict.fromkeys(e["book_title"] for e in es)),
               "volume": " | ".join(dict.fromkeys(e["volume"] for e in es)),
               "chapter": " | ".join(dict.fromkeys(e["chapter"] for e in es)),
               "original_text": "\n---\n".join(e["original_text"] for e in es),
               "source_url": " | ".join(dict.fromkeys(e["source_url"] for e in es)) or old["source_url"],
               "context": "\n---\n".join(e["context"] for e in es),
               "evidence_ids": " | ".join(e["evidence_id"] for e in es),
               "evidence_locator": json.dumps([{k: e[k] for k in ("evidence_id", "collection_root", "raw_path", "text_path", "raw_sha256", "text_sha256", "revision_id", "start_offset", "end_offset", "deterministic_ok")} for e in es], ensure_ascii=False),
               "ai_status": result["ai_status"] if result else "not_reviewed_this_round",
               "machine_verdict": result["verdict"] if result else "NOT_REVERIFIED",
               "machine_relation_type": result["relation_type"] if result else "",
               "machine_evidence_type": result["evidence_type"] if result else "",
               "relationship_strength": result["relationship_strength"] if result else "",
               "risk_notes": risk, "suggested_result": suggestion,
               "approval_eligible": aid in eligible_ids, "human_decision": "PENDING", "human_notes": ""}
        rows.append(row)
    rows.sort(key=lambda r: ({"PRIORITY_REVIEW": 0, "HOLD": 1, "OLD_NOT_REVERIFIED": 2, "RESERVE": 3}[r["review_group"]], r["dynasty"], r["person_name"], r["alias_id"]))
    output.mkdir(parents=True, exist_ok=True)
    write_csv(output / "human_review_worksheet.csv", list(rows[0]), rows)
    write_csv(output / "evidence_inventory.csv", list(inventory[0]), inventory)
    people_rows = []
    for pid, p in sorted(people.items(), key=lambda item: (item[0] not in chosen, item[1]["dynasty"], item[1]["canonical_name"])):
        prs = [r for r in rows if r["person_id"] == pid]
        reason = "独立原文有明确字/姓名句；定位和关系门槛通过"
        if pid in case_people:
            reason = "保留身份案例审核位置；独立原文定位通过；必须逐关系审核，不以案例价值认定合格"
        people_rows.append({"person_id": pid, "person_name": p["canonical_name"], "dynasty": p["dynasty"], "priority": pid in chosen,
                            "reason": reason if pid in chosen else ("同人物争议暂缓" if pid in held_people else "本轮未重核，暂缓" if not any(r["task_id"] for r in prs) else "候补或部分关系暂缓，见逐关系清单"),
                            "priority_alias_ids": " | ".join(r["alias_id"] for r in prs if r["alias_id"] in priority_aliases),
                            "priority_relations": " | ".join(r["alias_name"] + " (" + r["alias_type"] + ")" for r in prs if r["alias_id"] in priority_aliases),
                            "human_decision": "PENDING"})
    write_csv(output / "candidate_people_review.csv", list(people_rows[0]), people_rows)
    spots = read_csv(review / "spot_checks.csv")
    spot_rows = [{**s, "check_completed": "PENDING", "human_decision": "PENDING", "human_notes": ""} for s in spots]
    write_csv(output / "mandatory_spot_checks.csv", list(spot_rows[0]), spot_rows)
    json_write(output / "recommended_alias_ids.json", sorted(priority_aliases))
    json_write(output / "approval_template.json", json_read(review / "approval_template.json"))
    json_write(output / "deterministic_checks.json", checks)
    immutable = [{k: v for k, v in r.items() if k not in {"human_decision", "human_notes"}} for r in rows]
    json_write(output / "review_manifest.json", {"batch_id": batch["batch_id"], "review_seal_sha256": digest((review / "review_seal.json").read_bytes()), "immutable_rows": immutable, "spot_task_ids": [s["task_id"] for s in spots]})
    summary = {"candidate_people": len(people), "relations": len(rows), "evidence_rows": len(inventory),
               "deterministic_checks_passed": sum(c["ok"] for c in checks),
               "current_machine_reviewed": len(results), "machine_statuses": dict(Counter(r["ai_status"] for r in results)),
               "groups": dict(Counter(r["review_group"] for r in rows)), "recommended_people": len(chosen), "recommended_relations": len(priority_aliases),
               "recommended_dynasties": dict(Counter(people[pid]["dynasty"] for pid in chosen)), "human_approved": 0, "formal_database_writes": 0, "new_model_calls": 0}
    json_write(output / "preparation_summary.json", summary)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()
    print(json.dumps(prepare(args.output_dir), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
