"""Optional real-browser acceptance check; fixtures stay under work/.

Requires existing dev dependencies plus playwright; uses installed Chrome/Edge.
Does not import any data into the formal database. Screenshots containing people
are explicitly TEST_ONLY; they use the same synthetic fixture as unit tests.
"""

import argparse
import hashlib
import json
import os
import runpy
import socket
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from urllib.request import urlopen

from app.db import PROJECT_ROOT, connect_db, init_db


@contextmanager
def server(db_path, run_dir):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    with (run_dir / (db_path.stem + "-server.log")).open("w", encoding="utf-8") as log:
        process = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port)], cwd=PROJECT_ROOT, env={**os.environ, "GUJI_DB_PATH": str(db_path)}, stdout=log, stderr=log, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        url = f"http://127.0.0.1:{port}"
        try:
            for _ in range(80):
                if process.poll() is not None:
                    raise RuntimeError("浏览器验收服务启动失败，参见本次 server.log")
                try:
                    with urlopen(url + "/health", timeout=1) as response:
                        if response.status == 200:
                            break
                except OSError:
                    time.sleep(0.1)
            else:
                raise RuntimeError("等待验收服务超时")
            yield url
        finally:
            process.terminate()
            process.wait(timeout=10)


def check_layout(page, checks, label):
    result = page.evaluate("""() => ({width: innerWidth, scrollWidth: document.documentElement.scrollWidth, inputVisible: !document.querySelector('#name-query') || document.querySelector('#name-query').getBoundingClientRect().width > 120})""")
    assert result["scrollWidth"] <= result["width"], (label, result)
    assert result["inputVisible"], (label, result)
    checks.append({"page": label, **result})


def main():
    parser = argparse.ArgumentParser(description="使用真实浏览器验证阶段二；虚构数据仅留在 work/")
    parser.add_argument("--output", default="work/stage2-browser")
    parser.add_argument("--browser", help="本机 Chrome/Edge 可执行文件")
    args = parser.parse_args()
    root = (PROJECT_ROOT / args.output).resolve()
    if not root.is_relative_to((PROJECT_ROOT / "work").resolve()) or root.exists():
        parser.error("输出必须位于项目 work/ 下且尚不存在")
    root.mkdir(parents=True)
    browser_exe = args.browser
    if not browser_exe:
        browser_exe = next((str(p) for p in [Path("C:/Program Files/Google/Chrome/Application/chrome.exe"), Path("C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe")] if p.exists()), None)
    if not browser_exe:
        parser.error("请用 --browser 指定已安装的 Chrome 或 Edge")
    from playwright.sync_api import sync_playwright

    formal = PROJECT_ROOT / "data/guji.db"
    before_hash = hashlib.sha256(formal.read_bytes()).hexdigest()
    fixture_db = root / "TEST_ONLY_SYNTHETIC.db"
    init_db(fixture_db)
    fixtures = runpy.run_path(str(PROJECT_ROOT / "tests/test_catalog.py"))
    fixtures["public_db"].__wrapped__(fixture_db)
    with connect_db(fixture_db) as connection:
        connection.execute("INSERT INTO evidences VALUES ('TEST-E-SELF', 'TEST-A1', 'TEST-S1', 'TEST ONLY: synthetic self-naming example; not historical text.', 'supports', 'direct', 'verified', ?)", (json.dumps({"relation_type": "人物本人直接自称", "evidence_type": "仅测试本人作品"}, ensure_ascii=False),))
    checks, errors = [], []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=browser_exe, headless=True)
        try:
            with server(formal, root) as base:
                for width, height, name in ((1440, 1000, "desktop"), (390, 844, "mobile"), (320, 740, "narrow")):
                    page = browser.new_page(viewport={"width": width, "height": height}, device_scale_factor=1)
                    page.on("pageerror", lambda error: errors.append(str(error)))
                    page.goto(base, wait_until="networkidle")
                    assert page.get_by_text("暂无可公开检索的已核对人物", exact=True).is_visible()
                    page.get_by_role("button", name="检查服务状态").click()
                    page.get_by_text("应用与数据库连接正常", exact=True).wait_for()
                    check_layout(page, checks, "formal-empty-" + name)
                    page.screenshot(path=str(root / ("formal-empty-" + name + ".png")), full_page=True)
                    page.close()
            with server(fixture_db, root) as base:
                for width, height, name in ((1440, 1000, "desktop"), (390, 844, "mobile"), (320, 740, "narrow")):
                    page = browser.new_page(viewport={"width": width, "height": height}, device_scale_factor=1)
                    page.on("pageerror", lambda error: errors.append(str(error)))
                    page.goto(base)
                    page.get_by_label("输入人物姓名或别称").fill("仅测试共用字")
                    page.get_by_role("button", name="检索称呼").click()
                    page.get_by_text("存在多个候选，不能确定", exact=True).wait_for()
                    assert page.locator(".candidate-card").count() == 2
                    assert "/search?" in page.url and page.url.endswith("#results")
                    page.wait_for_function("document.querySelector('#results').getBoundingClientRect().top < innerHeight", timeout=5000)
                    check_layout(page, checks, "TEST_ONLY-results-" + name)
                    page.screenshot(path=str(root / ("TEST_ONLY-results-" + name + ".png")), full_page=True)
                    page.locator('[data-person-id="TEST-P1"] .text-link').click()
                    page.get_by_role("heading", name="身份信息", exact=True).wait_for()
                    check_layout(page, checks, "TEST_ONLY-profile-" + name)
                    page.screenshot(path=str(root / ("TEST_ONLY-profile-" + name + ".png")), full_page=True)
                    page.locator(".alias-list a").filter(has_text="仅测试共用字").click()
                    page.get_by_role("heading", name="古籍证据", exact=True).wait_for()
                    assert page.locator(".evidence-record").count() == 2
                    assert page.get_by_text("后世文献记载其自称", exact=True).is_visible()
                    assert page.get_by_text("人物本人直接自称", exact=True).is_visible()
                    assert page.locator(".source-link a").first.get_attribute("href") == "https://example.invalid/test-only?revision=7&part=1"
                    check_layout(page, checks, "TEST_ONLY-evidence-" + name)
                    page.screenshot(path=str(root / ("TEST_ONLY-evidence-" + name + ".png")), full_page=True)
                    page.goto(base + "/search?q=没有收录的测试称呼")
                    page.get_by_role("heading", name="未找到已核对的匹配人物", exact=True).wait_for()
                    check_layout(page, checks, "TEST_ONLY-no-match-" + name)
                    page.screenshot(path=str(root / ("TEST_ONLY-no-match-" + name + ".png")), full_page=True)
                    assert page.goto(base + "/aliases/TEST-A4").status == 404
                    page.get_by_role("heading", name="未找到可公开展示的称呼证据", exact=True).wait_for()
                    check_layout(page, checks, "TEST_ONLY-unavailable-" + name)
                    page.close()
        finally:
            browser.close()
    after_hash = hashlib.sha256(formal.read_bytes()).hexdigest()
    assert before_hash == after_hash, "正式数据库发生变化"
    assert errors == [], errors
    report = {"result": "passed", "browser": browser_exe, "layout_checks": checks, "page_errors": errors, "formal_database_sha256_before": before_hash, "formal_database_sha256_after": after_hash, "fixture_database": str(fixture_db), "fixture_warning": "Synthetic data for UI tests only; not historical evidence"}
    (root / "browser-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
