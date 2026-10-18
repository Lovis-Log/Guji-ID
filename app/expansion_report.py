"""Join candidate, program, AI and formal counts without mixing their states."""

import json
from collections import defaultdict
from pathlib import Path

from app.batch_import import existing_connection
from app.collector import digest, read_csv, write_csv
from app.verification import json_read, json_write, validate_review


def expansion_report(old_dir, new_dir, review_dir, db_path, output_dir):
    output = Path(output_dir)
    if output.exists():
        raise ValueError("扩容报告目录已存在")
    output.mkdir(parents=True)
    roots = [Path(old_dir), Path(new_dir)]
    people, aliases, evidences, evidence_root = {}, {}, {}, {}
    for root in roots:
        people.update({p["person_id"]: p for p in read_csv(root / "candidate_people.csv")})
        aliases.update({a["alias_id"]: a for a in read_csv(root / "aliases_pending.csv")})
        for e in read_csv(root / "evidence_pending.csv"):
            evidences[e["evidence_id"]] = e
            evidence_root[e["evidence_id"]] = str(root)
    _, tasks, results = validate_review(review_dir)
    result_by_alias = {r["alias_id"]: r for r in results}
    by_person, shared = defaultdict(list), defaultdict(set)
    for person in people.values():
        by_person[person["canonical_name"]].append(person)
    for alias in aliases.values():
        shared[alias["alias_name"]].add(alias["person_id"])
    cases = []

    def members(pids, alias_name, category):
        items = []
        for pid in sorted(pids):
            ee = [e for e in evidences.values() if e["person_id"] == pid and (e["alias_name"] == alias_name or category == "full_name_homonym")]
            ee.sort(key=lambda e: (e["alias_type"] != "courtesy", e["evidence_id"]))
            e = ee[0] if ee else None
            items.append({"person_id": pid, "canonical_name": people[pid]["canonical_name"], "dynasty_hint": people[pid]["dynasty"], "native_place_candidate": people[pid]["native_place"], "evidence": e, "source_root": evidence_root[e["evidence_id"]] if e else "", "human_approved": False})
        return items

    for name, pp in by_person.items():
        if len(pp) > 1:
            cases.append({"case_type": "full_name_homonym", "name": name, "members": members({p["person_id"] for p in pp}, name, "full_name_homonym"), "status": "independent_primary_texts_available_awaiting_human_approval"})
    for name, pids in shared.items():
        if len(pids) > 1 and len({people[pid]["canonical_name"] for pid in pids}) > 1:
            cases.append({"case_type": "shared_appellation", "name": name, "members": members(pids, name, "shared_appellation"), "status": "independent_primary_texts_available_awaiting_human_approval"})
    json_write(output / "identity_cases.json", cases)
    worksheet = []
    for alias in aliases.values():
        ev = [e for e in evidences.values() if e["alias_id"] == alias["alias_id"]]
        result = result_by_alias.get(alias["alias_id"])
        chosen = next((e for e in ev if result and e["evidence_id"] == result["evidence_id"]), ev[0] if ev else None)
        risk = result["risk"] if result else "high" if not ev else "medium"
        worksheet.append({"person_id": alias["person_id"], "person_name": alias["person_name"], "alias_id": alias["alias_id"], "alias_name": alias["alias_name"], "alias_type": alias["alias_type"], "risk": risk, "ai_status": result["ai_status"] if result else "not_reviewed_this_round", "subagent_verdict": result["subagent_verdict"] if result else "", "codex_difference": result["disagreement"] if result else "", "original_text": chosen["original_text"] if chosen else "", "source_url": chosen["source_url"] if chosen else alias["discovery_url"], "source_kind": "classical_text" if chosen else "discovery_clue_only", "needs_human_action": (result["explanation"] if result and result["mandatory_review"] else "抽查原文与叙述方式后作批次批准" if result else "本轮未核验；逐条处理或留待后续核验" if ev else "缺古籍正文，补查或保持不入库"), "human_decision": "待处理", "human_notes": ""})
    rank = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    worksheet.sort(key=lambda r: (rank[r["risk"]], r["person_id"], r["alias_id"]))
    write_csv(output / "human_review_worksheet.csv", worksheet[0].keys(), worksheet)
    differences = [r for r in results if r["disagreement"]]
    fields = ("task_id", "person_name", "alias_name", "subagent_verdict", "verdict", "program_original_text_match", "original_text_match", "codex_check", "disagreement", "source_url")
    write_csv(output / "program_ai_differences.csv", fields, [{k: r[k] for k in fields} for r in differences])
    summary = json_read(Path(review_dir) / "summary.json")
    old_people = read_csv(roots[0] / "candidate_people.csv")
    new_people = read_csv(roots[1] / "candidate_people.csv")
    with existing_connection(db_path) as connection:
        formal = {table: connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0] for table in ("people", "aliases", "sources", "evidences")}
        discovered = read_csv(roots[1] / "discovery_candidates.csv")
        for item in discovered:
            matches = connection.execute("SELECT person_id, dynasty, verification_status FROM people WHERE canonical_name=?", (item["canonical_name"],)).fetchall()
            item["database_match_status"] = "same_name_requires_identity_review" if matches else "no_formal_database_match"
            item["possible_database_person_ids"] = "|".join(r["person_id"] for r in matches)
        write_csv(output / "candidate_discovery_with_db_matches.csv", discovered[0].keys(), discovered)
    scale = {"old_candidate_people": len(old_people), "new_candidate_people": len(new_people), "cumulative_candidate_people": len(people), "awaiting_human_people": sum(p["review_status"] == "待审核" for p in people.values()), "human_approved_people_this_round": 0, "formal_database": formal, "old_alias_candidates": len(read_csv(roots[0] / "aliases_pending.csv")), "new_alias_candidates": len(read_csv(roots[1] / "aliases_pending.csv")), "cumulative_alias_candidates": len(aliases), "locatable_evidence_rows": len(evidences), "actual_subagent_reviewed_relations_this_round": summary["actual_subagent_judgments"], "ai_verified_after_codex_checks": summary["ai_verified"], "uncertain": summary["uncertain_or_not_reviewed"], "rejected": summary["rejected"], "aliases_not_reviewed_this_round": sum(a["alias_id"] not in result_by_alias for a in aliases.values()), "shared_appellation_cases": sum(c["case_type"] == "shared_appellation" for c in cases), "full_name_homonym_cases": sum(c["case_type"] == "full_name_homonym" for c in cases), "duplicates_or_possible_existing_discovery_records": json_read(roots[1] / "bulk_batch.json")["duplicate_or_possible_existing_candidates"], "program_ai_disagreements": len(differences), "formal_database_sha256": digest(Path(db_path).read_bytes())}
    json_write(output / "scale_comparison.json", scale)
    lines = ["# 第四阶段新旧数据与身份案例", "", "所有新旧采集记录仍为待审核。下列 AI 核验不构成人工批准，正式库未新增。", "", "```json", json.dumps(scale, ensure_ascii=False, indent=2), "```", "", "## 两类身份案例", ""]
    for case in cases:
        lines += [f"### {case['name']} / {case['case_type']}", "", "|独立人物|朝代提示|籍贯候选|原文与独立出处|", "|---|---|---|---|"]
        for member in case["members"]:
            e = member["evidence"]
            citation = f"[{e['book_title']} {e['volume']} {e['chapter']}]({e['source_url']})：{e['original_text']}" if e else "缺原文"
            lines.append(f"|{member['canonical_name']}（{member['person_id']}）|{member['dynasty_hint']}|{member['native_place_candidate'] or '未填'}|{citation}|")
        lines += ["", "这里有实际获取的独立文本支持区分身份；尚未人工批准，不在公众检索展示。共享称呼与完整姓名相同分别统计，不互相替代。", ""]
    lines += ["## 人工处理顺序", "", "1. 高风险：两位王珪各自的姓名/字、苏轼/欧阳修共享文忠、韩琦材料分类、刘允济的字与底本问题。", "2. 缺古籍正文的旧候选 8 条保持待查，不能以作者介绍页充当证据。", "3. 抽查本轮 AI 通过的原文和其自由叙述，完成明确批次批准；旧批次未覆盖的 23 条称呼不能算本轮已核验。", "", "模型原始说明、首次严格格式拒收和本地适配报告均保留；程序与 AI 的差异单独列入 program_ai_differences.csv。", ""]
    (output / "comparison_and_cases.md").write_text("\n".join(lines), encoding="utf-8")
    return scale
