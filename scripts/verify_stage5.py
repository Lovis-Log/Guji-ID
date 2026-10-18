"""Real Chrome/Edge end-to-end acceptance using the actual approved database.

No fixture imports, production mutations, external model calls or source fetches.
Screenshots show actual approved records. Mobile means browser viewport/touch
emulation, not a physical handset test.
"""
import argparse
from pathlib import Path
from urllib.parse import urlencode

from app.batch_import import database_counts, existing_connection
from app.collector import digest
from app.db import PROJECT_ROOT
from app.verification import json_read, json_write
from scripts.verify_stage2_browser import check_layout, server


def verify(output):
    from playwright.sync_api import sync_playwright
    output = Path(output).resolve()
    if output.exists() or not any(output.is_relative_to(PROJECT_ROOT / folder) for folder in ("work", "outputs")):
        raise ValueError("请使用项目 work/ 或 outputs/ 内尚不存在的验收目录")
    release = PROJECT_ROOT / "outputs/stage5-release"
    acceptance = json_read(release / "import_acceptance.json")
    if acceptance["status"] != "passed":
        raise ValueError("正式入库未验收通过，禁止第五阶段验收")
    formal = PROJECT_ROOT / "data/guji.db"
    before = digest(formal.read_bytes())
    output.mkdir(parents=True)
    browsers = [("Chrome", Path("C:/Program Files/Google/Chrome/Application/chrome.exe")),
                ("Edge", Path("C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"))]
    available = [(name, path) for name, path in browsers if path.is_file()]
    if not available:
        raise ValueError("未找到实际浏览器；不能声称已完成浏览器验收")
    layouts, checks, errors, versions = [], [], [], {}
    def passed(name, **details):
        checks.append({"check": name, "passed": True, **details})
    with sync_playwright() as p, server(formal, output) as base:
        for browser_name, exe in available:
            browser = p.chromium.launch(executable_path=str(exe), headless=True)
            versions[browser_name] = browser.version
            try:
                for width, height, size in ((1440, 1000, "desktop"), (390, 844, "mobile"), (320, 740, "narrow")):
                    label = browser_name + "-" + size
                    context = browser.new_context(viewport={"width": width, "height": height}, is_mobile=width < 760, has_touch=width < 760, reduced_motion="reduce")
                    context.grant_permissions(["clipboard-read", "clipboard-write"], origin=base)
                    page = context.new_page()
                    page.on("pageerror", lambda error: errors.append(str(error)))
                    def layout(state):
                        check_layout(page, layouts, label + "-" + state)
                    def search(q):
                        page.goto(base)
                        page.get_by_label("输入人物姓名或别称").fill(q)
                        page.get_by_role("button", name="检索称呼", exact=True).click()
                        page.locator("#results").wait_for()
                        assert page.url.endswith("#results")
                        assert page.locator("#results").evaluate("e => e.getBoundingClientRect().top < innerHeight")
                    page.goto(base, wait_until="networkidle")
                    assert page.locator(".search-examples a").count() > 0
                    assert page.get_by_role("button", name="检索称呼", exact=True).bounding_box()["height"] >= 44
                    page.get_by_role("button", name="检查服务状态").click()
                    page.get_by_text("应用与数据库连接正常", exact=True).wait_for()
                    assert page.evaluate("getComputedStyle(document.documentElement).scrollBehavior") == "auto"
                    layout("home")
                    page.screenshot(path=str(output / (label + "-home.png")), full_page=True)
                    passed(label + "-home-health-accessibility")
                    for query in ("戴胄", "景仁"):
                        search(query)
                        assert page.locator(".candidate-card").count() == 1
                        layout("search-" + query)
                        passed(label + "-search-" + query)
                    for query, clue_words in (("文忠", ("苏轼", "欧阳修")), ("王珪", ("叔玠", "禹玉", "太原祁", "成都華陽"))):
                        search(query)
                        assert page.locator(".candidate-card").count() == 2
                        page.get_by_text("存在多个候选，不能确定", exact=True).wait_for()
                        cards_text = page.locator(".candidate-grid").inner_text()
                        assert all(word in cards_text for word in clue_words)
                        ids = page.locator(".candidate-card").evaluate_all("els => els.map(e => e.dataset.personId)")
                        assert len(set(ids)) == 2
                        layout(query + "-results")
                        page.screenshot(path=str(output / (label + "-" + query + ".png")), full_page=True)
                        passed(label + "-disambiguation-" + query, distinct_ids=ids)
                        urls = set()
                        for pid in ids:
                            page.goto(base + "/search?" + urlencode({"q": query}))
                            page.locator(f'[data-person-id="{pid}"] .candidate-footer a').click()
                            page.get_by_role("heading", name="身份信息", exact=True).wait_for()
                            assert page.locator(".identity-evidences").is_visible()
                            assert page.get_by_role("link", name="返回称呼检索", exact=True).get_attribute("href").endswith("#results")
                            layout(query + "-" + pid + "-profile")
                            links = page.locator(".alias-list a")
                            target = links.filter(has_text="文忠") if query == "文忠" else links.filter(has_text="字")
                            target.first.click()
                            page.get_by_role("heading", name="古籍证据", exact=True).wait_for()
                            assert page.locator(".evidence-record").count() == 1
                            source = page.locator(".source-link a").first.get_attribute("href")
                            assert source.startswith("https://zh.wikisource.org/w/index.php?oldid=")
                            urls.add(source)
                            quote = page.locator(".original-text").inner_text()
                            page.locator(".copy-quote").first.click()
                            page.get_by_text("原文已复制，可粘贴并结合出处引用。", exact=True).wait_for()
                            assert page.evaluate("navigator.clipboard.readText()") == quote
                            page.locator(".evidence-context summary").click()
                            assert page.locator(".evidence-context").get_attribute("open") is not None
                            layout(query + "-" + pid + "-evidence")
                            page.screenshot(path=str(output / (label + "-" + pid + "-evidence.png")), full_page=True)
                            passed(label + "-profile-evidence-copy-" + pid, source_url=source)
                        assert len(urls) == 2
                    search("未收录的古籍称呼")
                    page.get_by_role("heading", name="未找到已核对的匹配人物", exact=True).wait_for()
                    layout("no-result")
                    passed(label + "-no-result")
                    hidden = page.goto(base + "/aliases/A-0d5d106989f70b80b344b781")
                    assert hidden.status == 404
                    page.get_by_role("heading", name="未找到可公开展示的称呼证据").wait_for()
                    layout("held-relation-404")
                    passed(label + "-unapproved-hidden")
                    invalid = page.goto(base + "/search?" + urlencode({"q": "测" * 101}))
                    assert invalid.status == 422 and page.get_by_role("link", name="返回称呼检索").is_visible()
                    layout("validation-error")
                    passed(label + "-html-validation-error")
                    missing = page.goto(base + "/not-a-page")
                    assert missing.status == 404 and page.get_by_role("link", name="返回称呼检索").is_visible()
                    layout("missing-page")
                    passed(label + "-html-404")
                    page.goto(base)
                    page.route("**/health", lambda route: route.fulfill(status=503, content_type="application/json", body='{"status":"unavailable"}'))
                    page.get_by_role("button", name="检查服务状态").click()
                    page.get_by_text("服务检查失败，请稍后重试", exact=True).wait_for()
                    assert page.get_by_role("button", name="检查服务状态").is_enabled()
                    page.unroute("**/health")
                    page.get_by_role("button", name="检查服务状态").click()
                    page.get_by_text("应用与数据库连接正常", exact=True).wait_for()
                    passed(label + "-health-failure-and-retry", simulated_network_failure=True)
                    context.close()
                context = browser.new_context(java_script_enabled=False, viewport={"width": 390, "height": 844})
                page = context.new_page()
                page.goto(base + "/search?" + urlencode({"q": "王珪"}))
                assert page.locator(".candidate-card").count() == 2
                page.locator(".candidate-footer a").first.click()
                page.locator(".alias-list a").first.click()
                assert page.locator(".original-text").is_visible()
                assert not page.locator(".copy-quote").is_visible()
                passed(browser_name + "-no-javascript-core-flow")
                context.close()
            finally:
                browser.close()
    assert not errors, errors
    assert digest(formal.read_bytes()) == before, "只读验收期间正式库被修改"
    with existing_connection(formal) as c:
        assert c.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    report = {"status": "passed", "database": str(formal), "data_scope": "actual_human_approved_formal_database", "versions": versions,
              "viewports": [[1440, 1000], [390, 844], [320, 740]], "physical_mobile_tested": False,
              "checks": checks, "functional_checks": len(checks), "layout_checks": layouts, "layout_check_count": len(layouts),
              "page_errors": errors, "counts": database_counts(formal), "database_sha256_before": before, "database_sha256_after": digest(formal.read_bytes())}
    json_write(output / "browser_acceptance.json", report)
    print({"status": "passed", "versions": versions, "functional_checks": len(checks), "layout_checks": len(layouts), "formal_db_unchanged": True})
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    verify(args.output)
