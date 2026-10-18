"""Expanded real-source acceptance in a conspicuously isolated test database.

Verified flags here are test simulations, never human approval or formal imports.
The normal application visibility filter is unchanged and tested as-is.
"""

import argparse
import json
import shutil
from pathlib import Path

from app.batch_import import existing_connection, write_tables
from app.catalog import get_alias, search_people
from app.collector import digest, read_csv
from app.db import PROJECT_ROOT
from app.importer import COLUMNS, import_batch
from app.main import create_app
from app.verification import check_task, json_read, json_write, lexical_check
from scripts.verify_stage2_browser import check_layout, server


def build_isolated_preview(roots, review_dir, output):
    output = Path(output).resolve()
    if not output.is_relative_to((PROJECT_ROOT / "work").resolve()) or output.exists():
        raise ValueError("隔离验收输出须位于项目 work/ 内且尚不存在")
    output.mkdir(parents=True)
    results = json_read(Path(review_dir) / "parsed_results.json")
    uncertain = {r["alias_id"] for r in results if r["verdict"] != "verified"}
    people, aliases, sources, evidences = {}, {}, {}, {}
    selected = set()
    for root in map(Path, roots):
        ps = {p["person_id"]: p for p in read_csv(root / "candidate_people.csv")}
        aa = {a["alias_id"]: a for a in read_csv(root / "aliases_pending.csv")}
        for p in ps.values():
            people[p["person_id"]] = {**{k: p.get(k, "") for k in COLUMNS["people"]}, "verification_status": "verified", "identity_description": "仅用于隔离验收的状态模拟；该真实候选尚未人工批准。" + (p.get("identity_description") or "")}
        for a in aa.values():
            aliases[a["alias_id"]] = {**{k: a[k] for k in COLUMNS["aliases"]}, "verification_status": "pending"}
        for e in read_csv(root / "evidence_pending.csv"):
            task = {"alias": aa[e["alias_id"]], "evidence": e, "program": check_task(root, ps[e["person_id"]], aa[e["alias_id"]], e)}
            explicit = e["alias_id"] not in uncertain and lexical_check(task) == "explicit_marker_in_located_context"
            if explicit:
                selected.add(e["alias_id"])
            sources[e["source_id"]] = dict(zip(COLUMNS["sources"], (e["source_id"], e["book_title"], e["volume"], e["chapter"], e["source_platform"], e["source_url"], f"修订 {e['revision_id']}")))
            notes = json.dumps({"relation_type": e["relation_type"], "evidence_type": e["evidence_type"], "context": e["context"], "remarks": "TEST ONLY：隔离库状态模拟，非真实人工批准；原文与 URL 来自实际响应。"}, ensure_ascii=False)
            evidences[e["evidence_id"]] = dict(zip(COLUMNS["evidences"], (e["evidence_id"], e["alias_id"], e["source_id"], e["original_text"], "supports" if explicit else e["usage_relation"], "direct" if explicit else e["evidence_nature"], "verified" if explicit else "pending", notes)))
    for aid in selected:
        aliases[aid]["verification_status"] = "verified"
    tables = {"people": list(people.values()), "aliases": list(aliases.values()), "sources": list(sources.values()), "evidences": list(evidences.values())}
    write_tables(output / "TEST_ONLY_csv", tables)
    db = output / "REAL_CANDIDATES_TEST_ONLY.db"
    result = import_batch(output / "TEST_ONLY_csv", db)
    return db, result


def main():
    parser = argparse.ArgumentParser(description="20人真实候选的隔离状态模拟与浏览器验收；不会批准或写正式库")
    parser.add_argument("--output", default="work/stage4-acceptance")
    parser.add_argument("--old-dir", default="data/collections/2026-10-16-delivery")
    parser.add_argument("--new-dir", default="data/collections/stage4-tang-song-20")
    parser.add_argument("--review-dir", default="data/verifications/stage4-tang-song-20-reviewed")
    parser.add_argument("--case-dir", default="data/verifications/stage4-expansion-report")
    args = parser.parse_args()
    output = (PROJECT_ROOT / args.output).resolve()
    formal = PROJECT_ROOT / "data/guji.db"
    before = digest(formal.read_bytes())
    db, imports = build_isolated_preview([args.old_dir, args.new_dir], args.review_dir, output)
    with existing_connection(db) as connection:
        preview_people = connection.execute("SELECT count(*) FROM people").fetchone()[0]
    cases = json_read(Path(args.case_dir) / "identity_cases.json")
    api_checks = []
    from fastapi.testclient import TestClient
    with TestClient(create_app(db)) as client:
        for case in cases:
            response = client.get("/search", params={"q": case["name"]}, follow_redirects=False)
            assert response.status_code == 200 and "location" not in response.headers
            assert "存在多个候选，不能确定" in response.text
            for member in case["members"]:
                assert f'data-person-id="{member["person_id"]}"' in response.text
                assert client.get("/people/" + member["person_id"]).status_code == 200
            api_checks.append({"case_type": case["case_type"], "query": case["name"], "independent_candidates": len(case["members"]), "passed": True})
        # A pending candidate with no evidence stays hidden, even in the test copy.
        assert search_people(db, "青蓮居士") == []
        assert "未找到已核对的匹配人物" in client.get("/search", params={"q": "TEST_ONLY_NOT_COLLECTED"}).text
        art_names = [a["alias_name"] for a in read_csv(output / "TEST_ONLY_csv/aliases.csv") if a["alias_type"] == "art" and a["verification_status"] == "verified"]
        assert art_names and search_people(db, art_names[0])
        for root in (Path(args.old_dir), Path(args.new_dir)):
            for person in read_csv(root / "candidate_people.csv"):
                assert person["person_id"] in {p["person_id"] for p in search_people(db, person["canonical_name"])}
        assert client.get("/health").json()["status"] == "ok"
    # Reimporting a complete copy is identical, not a new cohort.
    again = import_batch(output / "TEST_ONLY_csv", db)
    assert all(value["inserted"] == 0 for value in again.values())
    from playwright.sync_api import sync_playwright
    browser_exe = next((p for p in (Path("C:/Program Files/Google/Chrome/Application/chrome.exe"), Path("C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe")) if p.exists()), None)
    if browser_exe is None:
        raise ValueError("本机浏览器不可用；不宣称已完成窄屏验收")
    layouts, errors = [], []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=str(browser_exe), headless=True)
        try:
            with server(db, output) as base:
                for width, height, label in ((1440,1000,"desktop"),(390,844,"mobile"),(320,740,"narrow")):
                    page = browser.new_page(viewport={"width":width,"height":height}, device_scale_factor=1)
                    page.on("pageerror", lambda error: errors.append(str(error)))
                    page.add_init_script("""document.addEventListener('DOMContentLoaded', () => {const note=document.createElement('aside'); note.textContent='TEST ONLY · 隔离验收库。真实候选尚未人工批准；本页核对状态为测试模拟，正式库仍为 0。'; note.style.cssText='padding:12px 20px;background:#eee2d9;color:#643b2d;font:14px/1.7 sans-serif;border-bottom:1px solid #944935'; document.body.prepend(note);});""")
                    for case in cases:
                        page.goto(base)
                        page.get_by_label("输入人物姓名或别称").fill(case["name"])
                        page.get_by_role("button", name="检索称呼").click()
                        page.get_by_text("存在多个候选，不能确定", exact=True).wait_for()
                        assert page.locator(".candidate-card").count() == len(case["members"])
                        check_layout(page, layouts, case["case_type"] + "-results-" + label)
                        page.screenshot(path=str(output / ("TEST_ONLY-" + case["case_type"] + "-" + label + ".png")), full_page=True)
                        urls = set()
                        for member in case["members"]:
                            page.goto(base + "/people/" + member["person_id"])
                            check_layout(page, layouts, member["person_id"] + "-profile-" + label)
                            links = page.locator(".alias-list a")
                            target = links.filter(has_text=case["name"]) if case["case_type"] == "shared_appellation" else links.filter(has_text="字")
                            target.first.click()
                            page.get_by_role("heading", name="古籍证据", exact=True).wait_for()
                            check_layout(page, layouts, member["person_id"] + "-evidence-" + label)
                            url = page.locator(".source-link a").first.get_attribute("href")
                            assert url.startswith("https://zh.wikisource.org/w/index.php?oldid=")
                            urls.add(url)
                        assert len(urls) == len(case["members"])
                    page.close()
        finally:
            browser.close()
    assert not errors
    assert digest(formal.read_bytes()) == before
    report = {"status":"passed_in_isolated_database_only", "data_warning":"Real source texts, simulated verified flags. No human approval or formal data import.", "candidate_people":preview_people, "test_database":str(db), "initial_test_import":imports, "repeat_test_import":again, "case_checks":api_checks, "layout_checks":layouts, "page_errors":errors, "formal_database_sha256_before":before, "formal_database_sha256_after":digest(formal.read_bytes()), "human_approved":0, "formal_database_writes":0}
    json_write(output / "acceptance.json", report)
    print(json.dumps({"status":report["status"], "candidate_people":preview_people, "case_checks":api_checks, "layout_checks":len(layouts), "formal_database_unchanged":True}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
