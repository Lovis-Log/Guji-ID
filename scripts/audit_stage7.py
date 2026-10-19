"""Compute Stage 7 acceptance metrics from real SQLite data.

Run from the project root:

    python -m scripts.audit_stage7
    python -m scripts.audit_stage7 --db work/stage7_test.db

The output deliberately keeps ``distinct_quote_fragments`` and ``evidence_links``
separate: one passage can support several genuine alias relations.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = PROJECT_ROOT / "data" / "guji.db"
REPORT_JSON = PROJECT_ROOT / "data" / "reports" / "stage7_acceptance.json"
REPORT_MD = PROJECT_ROOT / "data" / "reports" / "stage7_acceptance.md"

BASIC_FIELDS = ("canonical_name", "dynasty", "birth_year", "death_year",
                "native_place", "identity_description")


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def table_exists(con, name):
    return bool(con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone())


def main(argv=None):
    parser = argparse.ArgumentParser(description="Stage 7 验收指标")
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)
    db = Path(args.db)
    con = sqlite3.connect(str(db))
    con.row_factory = sqlite3.Row
    if not table_exists(con, "classical_chapters"):
        raise SystemExit("数据库缺少 Stage 7 表；请先运行建库程序。")

    people = [dict(row) for row in con.execute("SELECT * FROM people")]
    profiles = {row["person_id"]: dict(row) for row in con.execute("SELECT * FROM person_profiles")}
    tiers = Counter(profile["tier"] for profile in profiles.values())
    a_ids = [pid for pid, profile in profiles.items() if profile["tier"] == "A"]

    basic = {}
    for field in BASIC_FIELDS:
        known = 0
        for person in people:
            if person["person_id"] not in a_ids:
                continue
            value = person.get(field)
            if field == "identity_description" and not value:
                value = (profiles.get(person["person_id"], {}).get("biography_modern") or "")
            if value not in (None, ""):
                known += 1
        basic[field] = {"known": known, "total": len(a_ids),
                        "rate": round(known / len(a_ids), 4) if a_ids else 0}

    links = [dict(row) for row in con.execute(
        """SELECT l.*, c.work_id, c.completeness, c.char_count, c.original_text
           FROM classical_evidence_links l JOIN classical_chapters c USING(chapter_id)"""
    )]
    verified_links = [link for link in links if link["verification_status"] == "verified"]
    a_links = [link for link in verified_links if link["person_id"] in a_ids]
    a_with_direct = {link["person_id"] for link in a_links
                     if link["evidence_category"] == "direct_alias"}
    all_with_any = {link["person_id"] for link in verified_links}
    all_with_direct = {link["person_id"] for link in verified_links
                       if link["evidence_category"] == "direct_alias"}

    works = [dict(row) for row in con.execute("SELECT * FROM classical_works")]
    chapters = [dict(row) for row in con.execute("SELECT * FROM classical_chapters")]
    full_chapters = [chapter for chapter in chapters if chapter["completeness"] == "full"]
    distinct_quotes = {(link["chapter_id"], link["quote_start"], link["quote_end"])
                       for link in verified_links}
    located_full = [link for link in verified_links if link["completeness"] == "full"]

    # Integrity checks are recomputed from the stored text, not trusted flags.
    truncation = []
    for chapter in chapters:
        text = chapter["original_text"]
        if chapter["char_count"] != len(text):
            truncation.append({"chapter_id": chapter["chapter_id"], "type": "char_count"})
        if chapter["text_sha256"] != __import__("hashlib").sha256(text.encode()).hexdigest():
            truncation.append({"chapter_id": chapter["chapter_id"], "type": "hash"})
        if "\x00" in text:
            truncation.append({"chapter_id": chapter["chapter_id"], "type": "nul"})

    duplicate_groups = Counter((link["chapter_id"], link["quote_start"], link["quote_end"])
                               for link in verified_links)
    duplicate_links = sum(count - 1 for count in duplicate_groups.values() if count > 1)
    mismatched = [link for link in links if link["identity_match"] != "confirmed"]
    unverified = [link for link in links if link["verification_status"] != "verified"]

    aliases = [dict(row) for row in con.execute(
        """SELECT a.*, p.canonical_name, pr.tier FROM aliases a
           JOIN people p USING(person_id) LEFT JOIN person_profiles pr USING(person_id)
           ORDER BY p.canonical_name, a.alias_name"""
    )]
    alias_ids_with_classical = {link["alias_id"] for link in verified_links if link["alias_id"]}
    aliases_without = [alias for alias in aliases if alias["alias_id"] not in alias_ids_with_classical]

    per_person = []
    link_by_person = {}
    for link in verified_links:
        link_by_person.setdefault(link["person_id"], []).append(link)
    for person in people:
        pid = person["person_id"]
        person_links = link_by_person.get(pid, [])
        per_person.append({
            "person_id": pid,
            "name": person["canonical_name"],
            "tier": profiles.get(pid, {}).get("tier", ""),
            "links": len(person_links),
            "distinct_quotes": len({(l["chapter_id"], l["quote_start"], l["quote_end"])
                                    for l in person_links}),
            "works": len({l["work_id"] for l in person_links}),
            "chapters": len({l["chapter_id"] for l in person_links}),
            "direct_alias": len([l for l in person_links if l["evidence_category"] == "direct_alias"]),
            "biography": len([l for l in person_links if l["evidence_category"] == "biography"]),
        })
    per_person.sort(key=lambda item: (-item["links"], item["name"]))

    distribution = Counter(item["links"] for item in per_person)
    timeline = con.execute("SELECT count(*) FROM person_timeline_events").fetchone()[0]
    relations = con.execute("SELECT count(*) FROM person_relations").fetchone()[0]
    disputes = con.execute("SELECT count(*) FROM person_disputes").fetchone()[0]
    literary = con.execute("SELECT count(*) FROM person_literary_works").fetchone()[0]

    metrics = {
        "generated_at": now(),
        "database": str(db),
        "1_people": {"total": len(people), "tiers": dict(tiers)},
        "2_a_basic_field_completeness": basic,
        "3_a_with_direct_alias_classical": {
            "people": len(a_with_direct), "total_a": len(a_ids),
            "rate": round(len(a_with_direct) / len(a_ids), 4) if a_ids else 0,
            "missing": sorted(person["canonical_name"] for person in people
                              if person["person_id"] in a_ids
                              and person["person_id"] not in a_with_direct),
        },
        "4_people_with_any_classical": {
            "people": len(all_with_any), "total": len(people),
            "rate": round(len(all_with_any) / len(people), 4) if people else 0,
        },
        "5_works_and_editions": {
            "works": len(works),
            "titles": len({work["title"] for work in works}),
            "editions": len({(work["title"], work["digital_edition"]) for work in works}),
            "platforms": dict(Counter(work["source_platform"] for work in works)),
        },
        "6_full_chapters": {"full": len(full_chapters), "all": len(chapters),
                            "chars": sum(chapter["char_count"] for chapter in chapters)},
        "7_verified_quote_fragments": len(distinct_quotes),
        "8_links_located_in_full_chapters": {
            "links": len(located_full), "all_verified_links": len(verified_links),
            "rate": round(len(located_full) / len(verified_links), 4) if verified_links else 0,
        },
        "9_truncation_anomalies": truncation,
        "10_duplicates_and_mismatches": {
            "duplicate_link_locations": duplicate_links,
            "distinct_shared_locations": sum(1 for count in duplicate_groups.values() if count > 1),
            "identity_rejected_or_ambiguous": len(mismatched),
            "unverified_links": len(unverified),
        },
        "11_aliases_without_classical": {
            "aliases": len(aliases_without), "all_aliases": len(aliases),
            "a_tier": len([a for a in aliases_without if a["tier"] == "A"]),
            "sample": [{"person": a["canonical_name"], "alias": a["alias_name"],
                        "type": a["alias_type"], "tier": a["tier"]}
                       for a in aliases_without[:80]],
        },
        "12_evidence_distribution": dict(sorted(distribution.items())),
        "modules": {"timeline_events": timeline, "relations": relations,
                    "disputes": disputes, "literary_entries": literary},
        "per_person": per_person,
    }
    REPORT_JSON.parent.mkdir(parents=True, exist_ok=True)
    REPORT_JSON.write_text(json.dumps(metrics, ensure_ascii=False, indent=2) + "\n",
                           encoding="utf-8")

    lines = [
        "# Stage 7 验收指标（真实数据库查询）",
        "",
        f"生成时间：{metrics['generated_at']}　数据库：`{db}`",
        "",
        "## 总览",
        "",
        f"- 人物总数：{len(people)}（A {tiers.get('A',0)} / B {tiers.get('B',0)} / C {tiers.get('C',0)}）",
        f"- 古籍作品/版本：{len(works)} 种版本，{len({w['title'] for w in works})} 个书名",
        f"- 完整篇章：{len(full_chapters)} / {len(chapters)}；保存正文共 {sum(c['char_count'] for c in chapters)} 字",
        f"- 已核实原文片段（去重位置）：{len(distinct_quotes)}",
        f"- 证据关联：{len(verified_links)}；可定位到完整篇章：{len(located_full)}",
        f"- A级有人物直接称谓原文者：{len(a_with_direct)} / {len(a_ids)}",
        f"- 全库有任一条古籍证据者：{len(all_with_any)} / {len(people)}",
        f"- 原文截断异常：{len(truncation)}；身份不符/待核关联：{len(mismatched)}",
        "",
        "## A级基础字段完整率",
        "",
        "| 字段 | 已知 | 总数 | 比例 |",
        "| --- | --- | --- | --- |",
    ]
    for field, item in basic.items():
        lines.append(f"| {field} | {item['known']} | {item['total']} | {item['rate']:.1%} |")
    lines += [
        "",
        "## A级人物原文证据",
        "",
        "| 人物 | 关联 | 去重原文 | 作品 | 篇章 | 直接称谓 | 生平 |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for item in per_person:
        if item["tier"] != "A":
            continue
        lines.append(f"| {item['name']} | {item['links']} | {item['distinct_quotes']} | "
                     f"{item['works']} | {item['chapters']} | {item['direct_alias']} | {item['biography']} |")
    lines += ["", "## 证据数量分布（全部人物）", "",
              "| 关联条数 | 人物数 |", "| --- | --- |"]
    for count, total in sorted(distribution.items()):
        lines.append(f"| {count} | {total} |")
    REPORT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")
    if not args.quiet:
        print("\n".join(lines[:22]))
        print(f"\nJSON: {REPORT_JSON}\nMarkdown: {REPORT_MD}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
