"""Real browser paths for expanded source paragraphs and distinct identities."""
import argparse
import hashlib
import json
from pathlib import Path
from urllib.parse import urlencode

from playwright.sync_api import sync_playwright

from app.db import PROJECT_ROOT, connect_db


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--base-url',default='http://127.0.0.1:8065')
    parser.add_argument('--output',default='work/paragraph-expansion/browser-more')
    args=parser.parse_args()
    out=PROJECT_ROOT/args.output;out.mkdir(parents=True,exist_ok=False)
    db=PROJECT_ROOT/'data/guji.db';before=hashlib.sha256(db.read_bytes()).hexdigest()
    cases=json.loads((PROJECT_ROOT/'work/paragraph-expansion/formal/validated_manifest.json').read_text(encoding='utf-8'))['cases']
    with connect_db(db) as c:
        for case in cases:
            case['source_id']=c.execute('SELECT source_id FROM evidences WHERE alias_id=? AND original_text=?',
                (case['alias_id'],case['quote'])).fetchone()[0]
    checks=[];errors=[];versions={};screens=[]
    with sync_playwright() as p:
        for browser_name,exe in [('Chrome','C:/Program Files/Google/Chrome/Application/chrome.exe'),
                                 ('Edge','C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe')]:
            if not Path(exe).exists():continue
            browser=p.chromium.launch(executable_path=exe,headless=True)
            versions[browser_name]=browser.version
            try:
                for width,height,label in [(1440,900,'desktop'),(390,844,'mobile'),(320,740,'narrow')]:
                    context=browser.new_context(viewport={'width':width,'height':height},is_mobile=width<760,
                        has_touch=width<760,permissions=['clipboard-read','clipboard-write'])
                    page=context.new_page()
                    page.on('pageerror',lambda e:errors.append(str(e)))
                    page.on('console',lambda m:errors.append(m.text) if m.type=='error' else None)
                    prefix=f'{browser_name}-{label}'
                    def check(item):
                        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth'),(prefix,item,page.url)
                        checks.append({'browser':browser_name,'viewport':[width,height],'check':item,'url':page.url})
                    for case in cases:
                        person=case['name'];pid=case['person_id']
                        page.goto(args.base_url+'/search?'+urlencode({'q':person}),wait_until='domcontentloaded')
                        card=page.locator(f'[data-person-id="{pid}"]')
                        assert card.count()==1
                        card.locator('.candidate-footer a').click()
                        page.get_by_role('heading',name=person,exact=True).wait_for()
                        assert case['quote'] in page.locator('.identity-evidences').inner_text()
                        check(person+'-search-profile')
                        page.goto(args.base_url+'/sources/S-CBDB65-20250520?'+urlencode({'person_id':pid}))
                        assert '其他独立来源中的古籍原文' in page.locator('.source-originals').inner_text()
                        assert page.locator('.source-originals mark').first.inner_text()==case['quote']
                        assert len(page.locator('.source-originals blockquote').first.inner_text())>=100
                        page.get_by_role('link',name='查看这条独立来源的档案').first.click()
                        assert case['source_id'] in page.url
                        block=page.locator('.source-originals blockquote').first
                        paragraph=block.inner_text()
                        assert len(paragraph)>=100 and case['quote'] in paragraph
                        assert page.locator('.source-originals mark').first.inner_text()==case['quote']
                        assert page.locator('.source-originals mark').first.evaluate(
                            '(node)=>getComputedStyle(node).color')=='rgb(160, 47, 37)'
                        assert f"oldid={case['revision_id']}" in page.locator('.source-original-links a').first.get_attribute('href')
                        check(person+'-independent-source-paragraph')
                        button=page.locator('.source-copy-quote').first
                        if button.is_visible():
                            button.click()
                            page.wait_for_function('(expected)=>navigator.clipboard.readText().then(text=>text===expected)',arg=paragraph)
                            check(person+'-copy-full-paragraph')
                        if browser_name=='Chrome' and label in {'desktop','mobile'}:
                            path=out/(prefix+'-'+person+'-source-paragraph.png')
                            page.screenshot(path=str(path),full_page=True);screens.append(str(path))
                        page.goto(args.base_url+'/aliases/'+case['alias_id'])
                        assert case['quote'] in page.locator('.original-text').first.inner_text()
                        assert page.locator('.original-text mark').first.inner_text()==case['quote']
                        check(person+'-alias-paragraph')
                    page.goto(args.base_url+'/search?q='+urlencode({'q':'王珪'})[2:])
                    assert page.locator('[data-person-id="P-D-470d41f21e8cab39f12341ed"]').count()==1
                    assert page.locator('[data-person-id="P-D-46c39186c9a0fa0d8177b557"]').count()==1
                    check('two-wang-gui-search-cards')
                    for pid,sid,expected,other in [
                        ('P-D-470d41f21e8cab39f12341ed','S-064296061ba0e6bf5adbf022','叔玠','禹玉'),
                        ('P-D-46c39186c9a0fa0d8177b557','S-bc8cbe2d974902fc45442ce1','禹玉','叔玠')]:
                        page.goto(args.base_url+'/sources/'+sid+'?'+urlencode({'person_id':pid}))
                        block=page.locator('.source-originals blockquote').first.inner_text()
                        assert len(block)>=100 and expected in block and other not in block
                        assert page.locator('.source-originals mark').first.count()==1
                        check('wang-gui-'+expected+'-isolated')
                    for sid,pid in [('S-8a56967034219b8850e4319c','P-0180cd77-04c3-471a-937a-cc8e77709e52'),
                                    ('S-90c26de66654f2454711f6c3','P-b5e5232e-9dc8-42fb-adcd-26220d535678')]:
                        page.goto(args.base_url+'/sources/'+sid+'?'+urlencode({'person_id':pid}))
                        mark=page.locator('.source-originals mark').first.inner_text()
                        assert '文忠' in mark and '諡' in mark
                        assert len(page.locator('.source-originals blockquote').first.inner_text())>=100
                        check('shared-posthumous-context-'+pid)
                    page.goto(args.base_url+'/sources/S-KR-3ded69e308698248ddd72be3?person_id=P-CBDB-317')
                    block=page.locator('.source-originals blockquote').first.inner_text()
                    assert '張問字昌言' in block and '¶' not in block and '<pb:' not in block
                    check('kanripo-layout-marks-readable')
                    context.close()
            finally:browser.close()
    assert versions and not errors,errors
    after=hashlib.sha256(db.read_bytes()).hexdigest()
    assert before==after
    report={'status':'passed','versions':versions,'check_count':len(checks),'checks':checks,
        'screenshots':screens,'script_or_console_errors':errors,'database_sha256_before':before,
        'database_sha256_after':after,'physical_mobile_tested':False}
    (out/'browser_acceptance.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:report[k] for k in ('status','versions','check_count')},ensure_ascii=False))


if __name__=='__main__':main()
