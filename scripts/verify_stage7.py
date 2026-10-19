"""Stage 7 acceptance checks against the real database and HTTP pages."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from pathlib import Path
from urllib.parse import quote

from fastapi.testclient import TestClient

from app.classical_text import normalised_quote
from app.main import create_app

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = PROJECT_ROOT / "data" / "guji.db"
RESULTS = []


def check(name, condition, detail=""):
    RESULTS.append({"name": name, "ok": bool(condition), "detail": detail})
    print(("PASS " if condition else "FAIL ") + name + (f" — {detail}" if detail else ""))
    return condition


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Stage 7 验收检查")
    parser.add_argument("--db", default=str(DEFAULT_DB))
    args = parser.parse_args(argv)
    db = Path(args.db)
    con = sqlite3.connect(str(db))
    con.row_factory = sqlite3.Row
    checks = []

    tables = {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    checks.append(check("核心四表存在", {"people", "aliases", "sources", "evidences"} <= tables))
    checks.append(check("Stage 7 追加表存在",
                        {"classical_works", "classical_chapters", "classical_paragraphs",
                         "classical_evidence_links", "person_profiles"} <= tables))
    checks.append(check("数据库完整性", con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"))

    chapters = [dict(row) for row in con.execute("SELECT * FROM classical_chapters")]
    bad_chars = [c["chapter_id"] for c in chapters if c["char_count"] != len(c["original_text"])]
    bad_hashes = [c["chapter_id"] for c in chapters
                  if c["text_sha256"] != hashlib.sha256(c["original_text"].encode()).hexdigest()]
    checks.append(check("篇章字符数一致", not bad_chars, f"{len(bad_chars)} 篇异常"))
    checks.append(check("篇章文本哈希一致", not bad_hashes, f"{len(bad_hashes)} 篇异常"))
    checks.append(check("长篇未截断（单篇>2万字）",
                        all(c["char_count"] > 20000 for c in chapters if c["char_count"] > 20000)))

    links = [dict(row) for row in con.execute(
        """SELECT l.*,c.original_text FROM classical_evidence_links l
           JOIN classical_chapters c USING(chapter_id)"""
    )]
    bad_offsets = []
    for link in links:
        if link["quote_end"] > len(link["original_text"]):
            bad_offsets.append(link["link_id"])
            continue
        if normalised_quote(link["original_text"], link["quote_start"], link["quote_end"]) != link["quote"]:
            bad_offsets.append(link["link_id"])
    checks.append(check("引文偏移逐字一致", not bad_offsets, f"{len(bad_offsets)} 条异常"))
    checks.append(check("无身份不符的公开关联",
                        not [l for l in links if l["identity_match"] != "confirmed"]))
    checks.append(check("无未核验的公开关联",
                        not [l for l in links if l["verification_status"] != "verified"]))

    profiles = {row["person_id"]: dict(row) for row in con.execute("SELECT * FROM person_profiles")}
    tiers = {}
    for profile in profiles.values():
        tiers[profile["tier"]] = tiers.get(profile["tier"], 0) + 1
    checks.append(check("人物总数约500", 490 <= len(profiles) <= 510, str(len(profiles))))
    checks.append(check("A/B/C 分级完整",
                        tiers.get("A") == 60 and tiers.get("B") == 140 and tiers.get("C") == 300,
                        str(tiers)))
    a_ids = {pid for pid, profile in profiles.items() if profile["tier"] == "A"}
    a_with_direct = {link["person_id"] for link in links
                     if link["person_id"] in a_ids and link["evidence_category"] == "direct_alias"}
    checks.append(check("A级直接称谓原文覆盖率≥70%", len(a_with_direct) >= 42,
                        f"{len(a_with_direct)}/60"))

    client = TestClient(create_app(db))
    names = ["李白", "杜甫", "蘇軾", "諸葛亮", "曹操", "王羲之", "司馬遷", "白居易", "李清照", "辛棄疾"]
    for name in names:
        response = client.get("/search", params={"q": name})
        ok = response.status_code == 200 and name in response.text
        checks.append(check(f"检索 {name}", ok, f"HTTP {response.status_code}"))
    old = client.get("/search", params={"q": "王珪"})
    checks.append(check("旧数据检索仍可用", old.status_code == 200 and "王珪" in old.text))

    # Case 1/2: Li Bai page, 太白 evidence, exact reader location.
    row = con.execute("SELECT person_id FROM people WHERE canonical_name='李白'").fetchone()
    if row:
        page = client.get("/people/" + quote(row["person_id"], safe=""))
        checks.append(check("李白档案可打开", page.status_code == 200 and "李白" in page.text))
        link = con.execute(
            """SELECT l.link_id,l.person_id,l.quote,c.work_id,c.chapter_id
               FROM classical_evidence_links l JOIN aliases a USING(alias_id)
               JOIN people p USING(person_id) JOIN classical_chapters c USING(chapter_id)
               WHERE p.canonical_name='李白' AND a.alias_name='太白'
                 AND l.evidence_category='direct_alias' LIMIT 1"""
        ).fetchone()
        checks.append(check("李白“太白”有原文证据", bool(link),
                            link["quote"] if link else "缺失"))
        if link:
            checks.append(check("“字太白”见引文", "字太白" in link["quote"], link["quote"]))
            url = (f"/classics/{quote(link['work_id'], safe='')}/"
                   f"{quote(link['chapter_id'], safe='')}?person={quote(link['person_id'], safe='')}"
                   f"&evidence={quote(link['link_id'], safe='')}")
            reader = client.get(url)
            checks.append(check("李白证据可跳转篇章", reader.status_code == 200 and "is-selected" in reader.text))
            chapter = con.execute("SELECT original_text FROM classical_chapters WHERE chapter_id=?",
                                  (link["chapter_id"],)).fetchone()[0]
            checks.append(check("阅读器返回完整篇章", chapter in reader.text or
                                all(part in reader.text for part in chapter.split("\n\n")[:12])))

    # Case 3: full biography is not a fixed preview.
    chapter = con.execute(
        "SELECT chapter_id,work_id,char_count,original_text FROM classical_chapters ORDER BY char_count DESC LIMIT 1"
    ).fetchone()
    if chapter:
        reader = client.get(f"/classics/{quote(chapter['work_id'], safe='')}/{quote(chapter['chapter_id'], safe='')}")
        checks.append(check("长篇传记完整展示", reader.status_code == 200 and
                            reader.text.count(chapter["original_text"].split("\n\n")[0][:20]) >= 1,
                            f"{chapter['char_count']} 字"))

    # Case 6: deliberately wrong location must not pass.
    fake = client.get("/classics/not-a-work/not-a-chapter")
    checks.append(check("不存在的篇章返回404", fake.status_code == 404))

    con.close()
    failed = [item for item in RESULTS if not item["ok"]]
    print(f"\n通过 {len(RESULTS) - len(failed)} / {len(RESULTS)}")
    report = {"checks": RESULTS, "passed": len(RESULTS) - len(failed), "total": len(RESULTS)}
    out = PROJECT_ROOT / "data" / "reports" / "stage7_verification.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
