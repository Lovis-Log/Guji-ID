"""Read-only rendered acceptance for the three-level authentic catalog UI.

Browser plugin is absent in this session; use existing Python Playwright and
installed Chrome/Edge. TEST_ONLY response injection is browser-local only.
"""
import argparse
import hashlib
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from urllib.parse import quote, urlencode

from playwright.sync_api import sync_playwright

from app.catalog import get_alias, get_person, search_people
from app.db import PROJECT_ROOT


DB = PROJECT_ROOT / 'data' / 'guji.db'
FORBIDDEN = ('record_json', 'locator_json', 'c_personid', 'c_alt_name_chn',
             'c_surname_chn', 'c_by_range', 'BIOG_MAIN', 'ALTNAME_DATA')
WANG_IDS = {'P-D-470d41f21e8cab39f12341ed', 'P-D-46c39186c9a0fa0d8177b557'}


def formal_examples():
    du = next(p for p in search_people(DB, '杜甫') if p['birth_year'] == 712)
    li = get_person(DB, 'P-CBDB-32540')
    tao_people = search_people(DB, '陶渊明')
    connection = sqlite3.connect(DB.as_uri() + '?mode=ro', uri=True)
    try:
        auxiliary = None
        for person in tao_people:
            row = connection.execute("SELECT a.alias_id FROM aliases a JOIN relation_quality q USING(alias_id) WHERE a.person_id=? AND q.grade='C' LIMIT 1", (person['person_id'],)).fetchone()
            if row:
                auxiliary = row[0]
                break
        quote_alias = None
        for (aid,) in connection.execute("SELECT alias_id FROM evidences WHERE verification_status='verified' AND substr(ltrim(original_text),1,1) NOT IN ('{','[') LIMIT 100"):
            record = get_alias(DB, aid)
            if record and any(e['presentation']['kind'] == 'classical_text' and e['presentation']['original_text'] for e in record['evidences']):
                quote_alias = aid
                break
    finally:
        connection.close()
    assert auxiliary and quote_alias
    return du, li, auxiliary, quote_alias


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--base-url', default='http://127.0.0.1:8065')
    parser.add_argument('--output', default='work/readable-browser-' + datetime.now().strftime('%Y%m%d-%H%M%S'))
    args = parser.parse_args()
    output = Path(args.output)
    if not output.is_absolute():
        output = PROJECT_ROOT / output
    output.mkdir(parents=True, exist_ok=False)
    before = hashlib.sha256(DB.read_bytes()).hexdigest()
    report = {'status': 'running', 'base_url': args.base_url, 'browser_path': 'regular Playwright',
              'fallback_reason': 'Browser plugin not available', 'production_database_read_only': True,
              'flow': 'search -> readable person/evidence/source -> academic details -> requested raw JSON dialog',
              'versions': {}, 'checks': [], 'screenshots': [], 'page_errors': [], 'console_errors': [],
              'database_sha256_before': before, 'physical_mobile_tested': False}
    current_page = None
    try:
        du, li, auxiliary, quote_alias = formal_examples()
        with sync_playwright() as pw:
            for browser_name, executable in [('Chrome', 'C:/Program Files/Google/Chrome/Application/chrome.exe'), ('Edge', 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe')]:
                if not Path(executable).exists():
                    continue
                browser = pw.chromium.launch(executable_path=executable, headless=True)
                report['versions'][browser_name] = browser.version
                try:
                    for width, height, size in [(1440, 900, 'desktop'), (390, 844, 'mobile'), (320, 740, 'narrow')]:
                        context = browser.new_context(viewport={'width': width, 'height': height}, is_mobile=width < 760, has_touch=width < 760, permissions=['clipboard-read', 'clipboard-write'])
                        page = context.new_page()
                        current_page = page
                        page.on('pageerror', lambda error: report['page_errors'].append(str(error)))
                        page.on('console', lambda message: report['console_errors'].append(message.text) if message.type == 'error' else None)
                        label = browser_name + '-' + size
                        raw_requests = []
                        page.on('request', lambda request: raw_requests.append(request.url) if '/records/' in request.url else None)

                        def navigate(path):
                            raw_requests.clear()
                            response = page.goto(args.base_url.rstrip('/') + path, wait_until='networkidle')
                            assert response and response.status == 200, (path, response.status if response else None)
                            assert page.locator('main').inner_text().strip() and 'GuJi ID' in page.title()
                            assert not page.locator('vite-error-overlay, nextjs-portal').count()
                            assert not raw_requests, ('raw data fetched before explicit action', path)
                            content = page.locator('#raw-record-content')
                            if content.count():
                                assert not content.inner_text().strip()

                        def check(name, default=False):
                            metrics = page.evaluate('''() => ({width: innerWidth, doc: document.documentElement.scrollWidth, body: document.body.scrollWidth})''')
                            assert max(metrics['doc'], metrics['body']) <= metrics['width'] + 1, (label, name, metrics)
                            if default:
                                text = page.locator('main').inner_text()
                                assert all(token not in text for token in FORBIDDEN), (name, text)
                            report['checks'].append({'browser': browser_name, 'viewport': [width, height], 'state': name, 'url': page.url, 'layout': metrics, 'passed': True})

                        def screenshot(name):
                            target = output / (label + '-' + name + '.png')
                            page.screenshot(path=str(target), full_page=True)
                            report['screenshots'].append(str(target))

                        def academic_details():
                            outer = page.locator('.field-provenance > summary')
                            if outer.count():
                                outer.first.click()
                            nested = page.locator('.provenance-details > summary')
                            if nested.count():
                                nested.first.click()
                            check('academic-details')

                        def raw_dialog(name):
                            trigger = page.locator('a[data-raw-record]:visible').first
                            assert trigger.count(), 'No raw record action after opening academic provenance'
                            href = trigger.get_attribute('href')
                            assert '/records/' in href
                            assert trigger.get_attribute('data-raw-record') == href
                            expected = context.request.get(href if href.startswith('http') else args.base_url.rstrip('/') + href).json()
                            trigger.click()
                            dialog = page.locator('#raw-record-dialog')
                            dialog.wait_for(state='visible')
                            content = page.locator('#raw-record-content')
                            page.wait_for_function('''() => {try{return Boolean(JSON.parse(document.querySelector('#raw-record-content').textContent))}catch{return false}}''')
                            raw = content.inner_text()
                            assert json.loads(raw) == expected
                            assert raw_requests
                            page.locator('#copy-raw-record').click()
                            page.wait_for_function('(expected)=>navigator.clipboard.readText().then(value=>value===expected)', arg=raw)
                            check(name + '-raw-json-dialog')
                            screenshot(name + '-raw-json-dialog')
                            page.keyboard.press('Escape')
                            assert not dialog.is_visible()
                            assert trigger.evaluate('(element)=>element===document.activeElement')
                            check(name + '-raw-dialog-escape-focus')

                        navigate('/')
                        page.get_by_label('输入人物姓名或别称').fill('杜甫')
                        page.get_by_role('button', name='检索称呼', exact=True).click()
                        page.locator('#results').wait_for()
                        check('search-du-fu', default=True)
                        navigate('/people/' + quote(du['person_id'], safe=''))
                        text = page.locator('main').inner_text()
                        assert all(token in text for token in ('杜甫', '712', '770', '子美'))
                        check('du-fu-default', default=True)
                        screenshot('du-fu-readable')
                        academic_details()
                        assert '2025年5月20日' in page.locator('main').inner_text()
                        screenshot('academic-details')
                        raw_dialog('du-fu')

                        navigate('/search?' + urlencode({'q': '王珪'}))
                        ids = page.locator('.candidate-card').evaluate_all('(elements)=>elements.map(e=>e.dataset.personId)')
                        assert set(ids) == WANG_IDS and len(ids) == 2
                        check('two-wang-distinct', default=True)
                        screenshot('two-wang')
                        for pid in sorted(WANG_IDS):
                            navigate('/people/' + pid)
                            check('wang-' + pid, default=True)

                        navigate('/people/' + li['person_id'])
                        assert '太白' in page.locator('.alias-list').inner_text() and '青蓮居士' in page.locator('.alias-list').inner_text()
                        page.locator('.alias-list a').filter(has_text='青蓮居士').first.click()
                        assert '结构化数据库' in page.locator('main').inner_text()
                        assert not page.locator('.original-text').count()
                        check('li-bai-structured-alias', default=True)
                        screenshot('li-bai-structured')
                        academic_details()
                        sources = page.locator('a[href*="/sources/"]:visible')
                        assert sources.count()
                        sources.first.click()
                        assert page.get_by_role('heading', level=1).inner_text().strip()
                        assert page.locator('a[href*="/people/"]').count()
                        assert page.locator('#source-person').input_value() == li['person_id']
                        check('source-related-people', default=True)
                        screenshot('source-readable')
                        page.get_by_label('按人物查看出处').select_option('')
                        page.get_by_role('button', name='查看资料', exact=True).click()
                        assert page.locator('#source-person').input_value() == ''
                        check('source-all-people', default=True)
                        page.get_by_role('link', name='下一页', exact=True).click()
                        assert 'page=2' in page.url
                        check('source-pagination', default=True)

                        navigate('/aliases/' + auxiliary)
                        assert '辅助资料' in page.locator('main').inner_text()
                        assert not page.locator('.original-text').count()
                        check('tao-author-auxiliary', default=True)

                        navigate('/aliases/' + quote_alias)
                        original = page.locator('.original-text').first
                        assert original.inner_text().strip()
                        button = page.locator('[data-copy-target]').first
                        button.click()
                        page.wait_for_function('(expected)=>navigator.clipboard.readText().then(value=>value===expected)', arg=original.inner_text())
                        check('classical-quote-copy', default=True)
                        screenshot('classical-quote')
                        academic_details()
                        raw_dialog('classical')

                        # The fake response is TEST_ONLY and never reaches a database.
                        trigger = page.locator('a[data-raw-record]:visible').first
                        href = trigger.get_attribute('href')
                        unsafe = {'TEST_ONLY': '<img src=x onerror="window.TEST_ONLY_XSS=1">', 'unknown_code': 987654}
                        page.route('**/records/**', lambda route: route.fulfill(status=200, content_type='application/json', body=json.dumps(unsafe)))
                        trigger.click()
                        page.wait_for_function('''() => document.querySelector('#raw-record-content').textContent.includes('TEST_ONLY')''')
                        assert not page.locator('#raw-record-content img').count()
                        assert page.evaluate('window.TEST_ONLY_XSS') is None
                        page.keyboard.press('Escape')
                        check('raw-json-text-only-xss')
                        context.close()
                        current_page = None
                except Exception:
                    if current_page and not current_page.is_closed():
                        current_page.screenshot(path=str(output / 'failure.png'), full_page=True)
                    raise
                finally:
                    browser.close()
        assert report['versions'], 'No installed browser was found'
        assert not report['page_errors'] and not report['console_errors'], (report['page_errors'], report['console_errors'])
        report['status'] = 'passed'
    except Exception as error:
        report['status'] = 'failed'
        report['failure'] = repr(error)
        if current_page and not current_page.is_closed():
            try:
                current_page.screenshot(path=str(output / 'failure.png'), full_page=True)
            except Exception:
                pass
        raise
    finally:
        report['database_sha256_after'] = hashlib.sha256(DB.read_bytes()).hexdigest()
        (output / 'browser_acceptance.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        assert report['database_sha256_after'] == before, 'Formal database changed during read-only acceptance'
    print(json.dumps({'status': report['status'], 'versions': report['versions'], 'checks': len(report['checks']), 'output': str(output)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
