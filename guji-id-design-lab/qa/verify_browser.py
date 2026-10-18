"""Standard Playwright acceptance script for local developer machines.
Requires: pip install playwright ; python -m playwright install chromium
Run from design-lab directory: python qa/verify_browser.py
"""
from pathlib import Path
from threading import Thread
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from functools import partial
from playwright.sync_api import sync_playwright
import time

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'screenshots'
OUT.mkdir(exist_ok=True)
server = ThreadingHTTPServer(('127.0.0.1', 0), partial(SimpleHTTPRequestHandler,directory=str(ROOT)))
Thread(target=server.serve_forever, daemon=True).start()
URL = f'http://127.0.0.1:{server.server_port}/'
try:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        for width,height,name in [(1440,900,'desktop'),(390,844,'mobile')]:
            page = browser.new_page(viewport={'width':width,'height':height})
            errors = []
            page.on('pageerror',lambda error:errors.append(str(error)))
            page.goto(URL, wait_until='load')
            page.add_style_tag(content='html {scroll-behavior:auto!important}')
            page.screenshot(path=str(OUT/f'{name}-01-hero.png'))
            story=page.locator('.story').evaluate('(x)=>x.offsetHeight')
            page.evaluate('(y)=>window.scrollTo(0,y)',(story-height)*.56)
            page.wait_for_timeout(200)
            page.screenshot(path=str(OUT/f'{name}-02-macro.png'))
            page.evaluate('(y)=>window.scrollTo(0,y)',story+20)
            page.wait_for_timeout(200)
            page.screenshot(path=str(OUT/f'{name}-03-archive.png'))
            page.get_by_role('tab',name='文献证据').click()
            assert page.get_by_text('文献证据待接入').is_visible()
            page.evaluate('window.scrollTo(0,0)')
            page.locator('#hero-search').fill('王珪')
            page.locator('.search-submit').click()
            assert page.locator('#search-dialog').is_visible()
            assert page.locator('#dialog-query').inner_text() == '王珪'
            assert page.evaluate('document.documentElement.scrollWidth')<=width
            assert not errors, errors
            page.close()
            print(f'{name}: screenshots, UI, dialog, overflow, JS: PASS')
        browser.close()
finally:
    server.shutdown()
