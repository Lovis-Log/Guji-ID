"""Browser acceptance for the immersive A+B homepage.

Read-only: this script only issues GET requests to a running service. Start it
against a database copy, for example:

    GUJI_DB_PATH=work/guji_v02_qa/guji_qa.db \
      .venv/Scripts/python -m uvicorn app.main:app --port 8071

Then:

    .venv/Scripts/python scripts/verify_immersive_ab.py \
      --base http://127.0.0.1:8071 --out work/guji_v02_qa/after
"""

import argparse
import json
from pathlib import Path

from playwright.sync_api import sync_playwright

CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
WANG_IDS = {"P-D-46c39186c9a0fa0d8177b557", "P-D-470d41f21e8cab39f12341ed"}


def launch(browser):
    options = {"headless": True}
    if Path(CHROME).is_file():
        options["executable_path"] = CHROME
    return browser.chromium.launch(**options)


def wire_errors(page, bucket):
    page.on("pageerror", lambda error: bucket["js"].append(str(error)))
    page.on("console", lambda message: bucket["console"].append(message.text) if message.type == "error" else None)


def overflow(page):
    return page.evaluate("document.documentElement.scrollWidth > innerWidth")


def archive_text(page):
    return page.locator("[data-archive-display]").inner_text()


def check_name_within_identity(page, label, failures):
    name = page.locator(".archive-person-name").bounding_box()
    identity = page.locator(".archive-identity").bounding_box()
    content = page.locator(".archive-content").bounding_box()
    check(name is not None and identity is not None, f"{label}: missing long-name boxes", failures)
    if not name or not identity:
        return
    check(name["x"] >= identity["x"] - 1 and name["x"] + name["width"] <= identity["x"] + identity["width"] + 1,
          f"{label}: long name escapes the identity column", failures)
    if content and content["y"] < identity["y"] + identity["height"]:
        check(name["x"] + name["width"] <= content["x"] + 1,
              f"{label}: long name overlaps the archive content", failures)


def check(condition, message, failures):
    if not condition:
        failures.append(message)


def screenshot(page, out, name):
    page.screenshot(path=str(out / f"{name}.png"))


def scroll_instant(page, y):
    page.evaluate("(value) => window.scrollTo({top: value, behavior: 'instant'})", y)


def settle_scroll(page):
    """Smooth hash navigation can outlast networkidle; wait for it to stop."""
    previous = page.evaluate("window.scrollY")
    for _ in range(30):
        page.wait_for_timeout(100)
        current = page.evaluate("window.scrollY")
        if abs(current - previous) < 1:
            return current
        previous = current
    return previous


def wait_for_results(page):
    settle_scroll(page)
    page.wait_for_function(
        "() => { const node = document.querySelector('#results');"
        " if (!node) return false; const top = node.getBoundingClientRect().top;"
        " return top >= 0 && top < 260; }",
        timeout=5000,
    )


def run_full(browser, base, out, label, width, height, failures, report):
    start_failures = len(failures)
    errors = {"js": [], "console": []}
    data = {"width": width, "height": height}
    report["viewports"][label] = data
    page = browser.new_page(viewport={"width": width, "height": height}, device_scale_factor=1)
    wire_errors(page, errors)

    page.goto(base + "/", wait_until="networkidle")
    check(page.locator("#hero-search").count() == 0, f"{label}: cover still has a search input", failures)
    check(page.locator("#hero-search-form").count() == 0, f"{label}: cover still has a search form", failures)
    check(page.locator("#name-query").count() == 1, f"{label}: paper search form is missing", failures)
    check(not overflow(page), f"{label}: home has horizontal overflow", failures)
    check(page.locator("h1").first.inner_text().startswith("千载人名"), f"{label}: hero heading changed", failures)
    check("古籍原文库" in page.locator("#main-nav").inner_text(), f"{label}: classics link missing from main navigation", failures)
    data["home"] = {"hero_search_absent": True, "bottom_search_present": True, "overflow": overflow(page)}
    screenshot(page, out, f"home-hero-{label}")

    story_height = page.locator(".story").evaluate("(node) => node.offsetHeight")
    story_distance = max(1, story_height - height)
    scroll_instant(page, story_distance * .52)
    page.wait_for_timeout(250)
    check(page.locator("#stage-name").inner_text() == "寻名 · 展卷", f"{label}: act II stage label mismatch", failures)
    screenshot(page, out, f"scene-macro-{label}")

    scroll_instant(page, story_distance * .93)
    page.wait_for_timeout(250)
    check(page.locator("#stage-name").inner_text() == "考据 · 入卷", f"{label}: act III stage label mismatch", failures)
    screenshot(page, out, f"scene-paper-{label}")

    # Repeated scroll reversals should not throw or leave the sticky scene unusable.
    for index in range(50):
        portion = (index % 10) / 9
        scroll_instant(page, story_distance * portion)
        page.wait_for_timeout(18)
    page.wait_for_timeout(150)

    # The paper-white intro search must be wide and land directly on results,
    # without replaying the three-act story above it.
    page.goto(base + "/", wait_until="networkidle")
    page.locator("#name-query").scroll_into_view_if_needed()
    page.wait_for_timeout(250)
    intro_input = page.locator("#name-query").bounding_box()
    intro_width = intro_input["width"] if intro_input else 0
    minimum_width = 400 if width > 760 else 180
    check(intro_width >= minimum_width, f"{label}: bottom search input is too narrow ({intro_width}px)", failures)
    example_terms = page.locator(".search-examples button").all_inner_texts()
    check(example_terms == ["李白", "杜甫", "白居易", "辛弃疾"],
          f"{label}: curated examples changed: {example_terms}", failures)
    example_fill_only = False
    if example_terms:
        page.locator(".search-examples button").first.click()
        page.wait_for_timeout(200)
        example_fill_only = (page.url.rstrip("/") == base and
                             page.locator("#name-query").input_value() == example_terms[0])
        check(example_fill_only, f"{label}: example click navigated instead of filling the input", failures)
    page.locator("#name-query").fill("王珪")
    with page.expect_navigation():
        page.locator(".intro .search-controls button").click()
    page.wait_for_load_state("networkidle")
    wait_for_results(page)
    story_display = page.locator(".story").evaluate("(node) => getComputedStyle(node).display")
    intro_results_top = page.locator("#results").bounding_box()["y"]
    check(story_display == "none", f"{label}: search page still renders the three-act story", failures)
    check(page.url.endswith("#results"), f"{label}: bottom search did not target #results", failures)
    check(page.locator(".candidate-card").count() == 2, f"{label}: bottom search lost 王珪 homonyms", failures)
    results_animation = page.locator("#results").evaluate("(node) => getComputedStyle(node).animationName")
    check("results-reveal" in results_animation, f"{label}: results fade animation is missing", failures)
    data["intro_search"] = {"input_width": round(intro_width, 1), "story_display": story_display,
                            "results_top": round(intro_results_top, 1), "api": "bottom intro form",
                            "examples": example_terms, "example_fill_only": example_fill_only,
                            "results_animation": results_animation}
    page.wait_for_timeout(650)
    screenshot(page, out, f"intro-search-direct-{label}")

    # Search via keyboard from the paper section, then verify the fixed header does not cover results.
    page.goto(base + "/", wait_until="networkidle")
    page.locator("#name-query").scroll_into_view_if_needed()
    page.locator("#name-query").focus()
    page.keyboard.type("王珪")
    with page.expect_navigation():
        page.keyboard.press("Enter")
    page.wait_for_load_state("networkidle")
    wait_for_results(page)
    topbar_height = page.locator("#topbar").bounding_box()["height"]
    heading_box = page.locator("#results-title").bounding_box()
    check(page.url.endswith("#results"), f"{label}: keyboard search did not keep #results", failures)
    check(page.evaluate("window.scrollY") > 0, f"{label}: search results did not scroll into view", failures)
    check(heading_box is not None and heading_box["y"] >= topbar_height,
          f"{label}: fixed header covers results heading", failures)
    ids = page.locator(".candidate-card").evaluate_all("nodes => nodes.map(node => node.dataset.personId)")
    check(set(ids) == WANG_IDS, f"{label}: 王珪 homonyms not kept separate: {ids}", failures)
    check(page.locator(".identity-comparison-list li").count() == 2,
          f"{label}: homonym comparison board is missing candidates", failures)
    check(page.locator("#candidate-1").count() == 1 and page.locator("#candidate-2").count() == 1,
          f"{label}: homonym candidate anchors are missing", failures)
    check(page.locator("[data-archive-display]").get_attribute("data-person-id") == "",
          f"{label}: homonym search incorrectly selected one identity", failures)
    data["wang_gui"] = {"result_ids": ids, "archive_person_id": "", "result_top": round(heading_box["y"], 1)}
    page.wait_for_timeout(650)
    screenshot(page, out, f"search-wang-gui-{label}")
    page.locator("[data-archive-display]").screenshot(path=str(out / f"archive-neutral-{label}.png"))

    # 李白: real search -> canonical person -> dynamic title slip.
    page.goto(base + "/search?q=%E6%9D%8E%E7%99%BD#results", wait_until="networkidle")
    wait_for_results(page)
    check(page.locator(".candidate-card").count() == 1, f"{label}: 李白 result count is not 1", failures)
    with page.expect_navigation():
        page.locator(".candidate-card h3 a").first.click()
    page.wait_for_load_state("networkidle")
    check(page.url.startswith(base + "/people/P-CBDB-32540"), f"{label}: 李白 canonical URL mismatch: {page.url}", failures)
    check(page.locator("#profile-summary-title").is_visible(), f"{label}: person summary is not the first visible record section", failures)
    page.locator(".record-section-nav a[href='#classical-evidence']").click()
    check(page.locator("#classical-evidence details.record-disclosure").first.evaluate("el => el.open"),
          f"{label}: section navigation did not open the evidence disclosure", failures)
    page.locator(".archive-disclosure > summary").click()
    check(page.locator("[data-archive-display]").get_attribute("data-person-id") == "P-CBDB-32540",
          f"{label}: 李白 archive id mismatch", failures)
    check(page.locator(".archive-person-name").inner_text() == "李白", f"{label}: 李白 title slip mismatch", failures)
    check(page.locator("[data-archive-panel='a']").is_visible(), f"{label}: A mode is not visible", failures)
    check(not page.locator("[data-archive-mode='b']").is_disabled(), f"{label}: B mode should be available for the bound AI figure", failures)
    page.locator("[data-archive-mode='b']").click()
    check(page.locator("[data-ai-figure]").count() == 1, f"{label}: bound 李白 AI figure is missing", failures)
    check("暂无已核实的真实书影" in page.locator("#archive-scan-status").inner_text(),
          f"{label}: missing truthful no-scan status", failures)
    check("AI生成·拟古演绎，非真实古籍书影" in archive_text(page),
          f"{label}: AI figure disclosure is missing", failures)
    check(not overflow(page), f"{label}: 李白 person page has horizontal overflow", failures)
    data["li_bai"] = {
        "url": page.url,
        "person_id": page.locator("[data-archive-display]").get_attribute("data-person-id"),
        "slip_name": page.locator(".archive-person-name").inner_text(),
        "b_enabled_for_figure": not page.locator("[data-archive-mode='b']").is_disabled(),
        "scan_status": page.locator("#archive-scan-status").inner_text(),
    }
    screenshot(page, out, f"person-li-bai-{label}")
    page.locator("[data-archive-display]").screenshot(path=str(out / f"archive-li-bai-b-{label}.png"))

    # 苏轼: no baked-in 李白 and no inherited scan.
    page.goto(base + "/search?q=%E8%8B%8F%E8%BD%BC#results", wait_until="networkidle")
    wait_for_results(page)
    check(page.locator(".candidate-card").count() == 1, f"{label}: 苏轼 result count is not 1", failures)
    with page.expect_navigation():
        page.locator(".candidate-card h3 a").first.click()
    page.wait_for_load_state("networkidle")
    page.locator(".archive-disclosure > summary").click()
    check(page.locator(".archive-person-name").inner_text() == "苏轼", f"{label}: 苏轼 title slip mismatch", failures)
    check("李白" not in archive_text(page), f"{label}: 李白 leaks into 苏轼 archive", failures)
    check(not page.locator("[data-archive-mode='b']").is_disabled(), f"{label}: 苏轼 B mode should be available for its bound AI figure", failures)
    page.locator("[data-archive-mode='b']").click()
    check(page.locator("[data-ai-figure]").count() == 1, f"{label}: bound 苏轼 AI figure is missing", failures)
    check(not overflow(page), f"{label}: 苏轼 person page has horizontal overflow", failures)
    data["su_shi"] = {"slip_name": page.locator(".archive-person-name").inner_text(),
                      "li_bai_in_archive": "李白" in archive_text(page),
                      "b_enabled_for_figure": not page.locator("[data-archive-mode='b']").is_disabled()}
    screenshot(page, out, f"person-su-shi-{label}")
    page.locator("[data-archive-display]").screenshot(path=str(out / f"archive-su-shi-a-{label}.png"))

    # A 4-character name must stay inside the title-slip column.
    page.goto(base + "/people/P-CBDB-65803", wait_until="networkidle")
    page.locator(".archive-disclosure > summary").click()
    check(page.locator(".archive-person-name").inner_text() == "納蘭性德", f"{label}: long-name slip mismatch", failures)
    check_name_within_identity(page, label, failures)
    check(not overflow(page), f"{label}: long-name page has horizontal overflow", failures)
    name_box = page.locator(".archive-person-name").bounding_box()
    data["long_name"] = {"slip_name": page.locator(".archive-person-name").inner_text(),
                         "box": {key: round(value, 1) for key, value in name_box.items()} if name_box else None}
    page.locator("[data-archive-display]").screenshot(path=str(out / f"archive-long-name-{label}.png"))

    # 文徵明 is genuinely absent from the formal database; the empty state must be honest.
    page.goto(base + "/search?q=%E6%96%87%E5%BE%B5%E6%98%8E#results", wait_until="networkidle")
    wait_for_results(page)
    check(page.locator(".candidate-card").count() == 0, f"{label}: 文徵明 unexpectedly resolves", failures)
    check(page.locator(".empty-state").is_visible(), f"{label}: missing empty state for 文徵明", failures)
    check("未找到已核对的匹配人物" in page.locator(".empty-state").inner_text(),
          f"{label}: 文徵明 empty-state wording changed", failures)
    data["wen_zhengming"] = {"result_count": 0,
                             "empty_state": page.locator(".empty-state").inner_text().splitlines()[0]}
    page.wait_for_timeout(650)
    screenshot(page, out, f"search-wen-zhengming-{label}")

    # Keyboard reachability of the paper search controls.
    page.goto(base + "/", wait_until="networkidle")
    page.locator("#name-query").scroll_into_view_if_needed()
    page.locator("#name-query").focus()
    page.keyboard.press("Tab")
    check(page.evaluate("document.activeElement.tagName") == "BUTTON",
          f"{label}: keyboard cannot reach the paper search submit", failures)

    if width <= 760:
        page.locator("#menu-toggle").click()
        check(page.locator("#main-nav").is_visible(), f"{label}: mobile navigation did not open", failures)
        screenshot(page, out, f"mobile-menu-{label}")
        page.locator("#menu-toggle").click()

    check(not errors["js"], f"{label}: JS errors: {errors['js']}", failures)
    check(not errors["console"], f"{label}: console errors: {errors['console']}", failures)
    data["errors"] = errors

    # Reduced motion must retain a visible, operable paper search.
    reduced = browser.new_page(viewport={"width": width, "height": height}, reduced_motion="reduce")
    reduced.goto(base + "/", wait_until="networkidle")
    check(reduced.locator("#hero-search").count() == 0, f"{label}: reduced-motion cover still has a search input", failures)
    reduced.locator("#name-query").scroll_into_view_if_needed()
    check(reduced.locator("#name-query").is_visible(), f"{label}: reduced-motion paper search is hidden", failures)
    check(not overflow(reduced), f"{label}: reduced-motion page overflows", failures)
    data["reduced_motion"] = {"hero_search_absent": True, "paper_search_visible": True, "overflow": overflow(reduced)}
    reduced.screenshot(path=str(out / f"reduced-motion-{label}.png"))
    reduced.close()
    page.close()
    data["failures"] = failures[start_failures:]


def run_layout_only(browser, base, out, label, width, height, failures, report):
    start_failures = len(failures)
    errors = {"js": [], "console": []}
    data = {"width": width, "height": height}
    report["viewports"][label] = data
    page = browser.new_page(viewport={"width": width, "height": height}, device_scale_factor=1)
    wire_errors(page, errors)
    page.goto(base + "/", wait_until="networkidle")
    check(page.locator("#hero-search").count() == 0, f"{label}: cover still has a search input", failures)
    check(page.locator("#name-query").count() == 1, f"{label}: paper search form is missing", failures)
    check("古籍原文库" in page.locator("#main-nav").inner_text(), f"{label}: classics link missing from main navigation", failures)
    check(not overflow(page), f"{label}: home has horizontal overflow", failures)
    data["home"] = {"hero_search_absent": True, "bottom_search_present": True, "overflow": overflow(page)}
    screenshot(page, out, f"home-hero-{label}")

    page.goto(base + "/search?q=%E7%8E%8B%E7%8F%AA#results", wait_until="networkidle")
    wait_for_results(page)
    check(not overflow(page), f"{label}: 王珪 page has horizontal overflow", failures)
    check(page.locator(".candidate-card").count() == 2, f"{label}: 王珪 should show two candidates", failures)
    data["wang_gui"] = {
        "result_ids": page.locator(".candidate-card").evaluate_all("nodes => nodes.map(node => node.dataset.personId)"),
        "overflow": overflow(page),
    }
    screenshot(page, out, f"search-wang-gui-{label}")

    page.goto(base + "/people/P-0180cd77-04c3-471a-937a-cc8e77709e52", wait_until="networkidle")
    page.locator(".archive-disclosure > summary").click()
    check(not overflow(page), f"{label}: 苏轼 page has horizontal overflow", failures)
    check(page.locator(".archive-person-name").inner_text() == "苏轼", f"{label}: 苏轼 slip mismatch", failures)
    data["su_shi"] = {"slip_name": page.locator(".archive-person-name").inner_text(), "overflow": overflow(page)}
    screenshot(page, out, f"person-su-shi-{label}")

    page.goto(base + "/people/P-CBDB-65803", wait_until="networkidle")
    page.locator(".archive-disclosure > summary").click()
    check(not overflow(page), f"{label}: long-name page has horizontal overflow", failures)
    check_name_within_identity(page, label, failures)
    data["long_name"] = {"slip_name": page.locator(".archive-person-name").inner_text(), "overflow": overflow(page)}
    screenshot(page, out, f"person-long-name-{label}")

    check(not errors["js"], f"{label}: JS errors: {errors['js']}", failures)
    check(not errors["console"], f"{label}: console errors: {errors['console']}", failures)
    data["errors"] = errors
    data["failures"] = failures[start_failures:]
    page.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8071")
    parser.add_argument("--out", default="work/guji_v02_qa/after")
    args = parser.parse_args()
    base = args.base.rstrip("/")
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    failures = []
    report = {"base": base, "viewports": {}, "failures": failures}

    with sync_playwright() as playwright:
        browser = launch(playwright)
        run_full(browser, base, out, "desktop", 1440, 900, failures, report)
        run_full(browser, base, out, "mobile", 390, 844, failures, report)
        run_layout_only(browser, base, out, "laptop", 1366, 768, failures, report)
        run_layout_only(browser, base, out, "narrow", 320, 720, failures, report)
        browser.close()

    report["passed"] = not failures
    (out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
