"""Real Chrome/Edge reading flow from a person to fixed ancient source text."""
import argparse
import hashlib
import json
from pathlib import Path
from urllib.parse import quote as url_quote, urlencode

from playwright.sync_api import sync_playwright

from app.db import PROJECT_ROOT, connect_db
from scripts.acquire_jts190b import ROOT


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--base-url',default='http://127.0.0.1:8065')
    parser.add_argument('--output',default='outputs/classical-source/browser-final')
    args=parser.parse_args()
    out=PROJECT_ROOT/args.output;out.mkdir(parents=True,exist_ok=False)
    db=PROJECT_ROOT/'data/guji.db';before=hashlib.sha256(db.read_bytes()).hexdigest()
    manifest=json.loads((ROOT/'validated_quotes.json').read_text(encoding='utf-8'))
    checks=[];errors=[];consoles=[];versions={};screens=[]
    with connect_db(db) as c:
        source_ids={r['person_id']:c.execute('''SELECT e.source_id FROM evidences e WHERE e.alias_id=? AND e.original_text=?''',
           (r['alias_id'],r['quote'])).fetchone()[0] for r in manifest['cases']}
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
                    page=context.new_page();page.on('pageerror',lambda e:errors.append(str(e)))
                    page.on('console',lambda m:consoles.append(m.text) if m.type=='error' else None)
                    prefix=browser_name+'-'+label
                    def check(name):
                        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth'),(prefix,name,page.url)
                        checks.append({'browser':browser_name,'viewport':[width,height],'check':name,'url':page.url})
                    for case in manifest['cases']:
                        person=case['name'];pid=case['person_id'];sid=source_ids[pid]
                        page.goto(args.base_url+'/search?'+urlencode({'q':person}),wait_until='domcontentloaded')
                        card=page.locator(f'[data-person-id="{pid}"]')
                        assert card.count()==1
                        card.locator('.candidate-footer a').click()
                        page.get_by_role('heading',name=person,exact=True).wait_for()
                        assert case['quote'] in page.locator('.identity-evidences').inner_text()
                        check(person+'-person-identity-quote')
                        page.locator('.profile-source-summary a[href*="S-CBDB65-"]').first.click()
                        page.locator('.source-originals blockquote').first.wait_for()
                        assert '其他独立来源中的古籍原文' in page.locator('.source-originals').inner_text()
                        cross_text=page.locator('.source-originals blockquote').first.inner_text()
                        assert case['quote'] in cross_text and len(cross_text)>=100
                        assert page.locator('.source-originals mark.source-relevant-line').first.inner_text()==case['quote']
                        assert '当前数据库记录提供结构化资料' in page.locator('.source-originals').inner_text()
                        link=page.locator('.source-original-links a').first.get_attribute('href')
                        assert link == manifest['revision_url']+'#'+url_quote(case['section_anchor'],safe='')
                        check(person+'-cbdb-cross-source-quote')
                        if browser_name=='Chrome' and label in {'desktop','mobile'}:
                            path=out/(prefix+'-'+person+'-cbdb-with-independent-quote.png')
                            page.screenshot(path=str(path),full_page=True);screens.append(str(path))
                        page.goto(args.base_url+'/sources/'+sid+'?'+urlencode({'person_id':pid}),wait_until='networkidle')
                        direct_text=page.locator('.source-originals blockquote').first.inner_text()
                        assert case['quote'] in direct_text and len(direct_text)>=100
                        assert page.locator('.source-originals mark.source-relevant-line').first.inner_text()==case['quote']
                        assert '此来源中的古籍原文' in page.locator('.source-originals').inner_text()
                        assert '舊唐書' in page.locator('.source-original-citation').first.inner_text()
                        assert str(manifest['revision_id']) in page.locator('.source-original-citation').first.inner_text()
                        assert 'CC BY-SA 4.0' in page.locator('.source-original-citation').first.inner_text()
                        assert page.locator('pre:visible').count()==0
                        check(person+'-fixed-classical-source')
                        button=page.locator('.source-copy-quote').first
                        if button.is_visible():
                            button.click()
                            page.wait_for_function('(text)=>navigator.clipboard.readText().then(value=>value===text)',arg=direct_text)
                            assert '原文已复制' in page.locator('.source-copy-status').first.inner_text()
                            check(person+'-copy-quote')
                        if browser_name=='Chrome' and label in {'desktop','mobile'}:
                            path=out/(prefix+'-'+person+'-fixed-book-source.png')
                            page.screenshot(path=str(path),full_page=True);screens.append(str(path))
                        page.goto(args.base_url+'/aliases/'+case['alias_id'])
                        assert any(case['quote'] in value for value in page.locator('.original-text').all_inner_texts())
                        assert '维基文库' in page.locator('.evidence-section').inner_text()
                        check(person+'-alias-citation')
                    # Existing manually verified records also appear on their own source page.
                    page.goto(args.base_url+'/sources/S-064296061ba0e6bf5adbf022?person_id=P-D-470d41f21e8cab39f12341ed')
                    assert '王珪，字叔玠' in page.locator('.source-originals blockquote').first.inner_text()
                    check('legacy-wang-gui-classical-source')
                    # Auxiliary author metadata is never presented as ancient original text.
                    page.goto(args.base_url+'/sources/S-WS65-2307897?person_id=P-CBDB-3835')
                    assert page.locator('.source-originals blockquote').count()==0
                    check('auxiliary-not-ancient')
                    context.close()
            finally:browser.close()
    assert versions and not errors and not consoles,(errors,consoles)
    after=hashlib.sha256(db.read_bytes()).hexdigest()
    assert before==after
    result={'status':'passed','base_url':args.base_url,'versions':versions,'checks':checks,'check_count':len(checks),
      'screenshots':screens,'script_errors':errors,'console_errors':consoles,
      'database_sha256_before':before,'database_sha256_after':after,'physical_mobile_tested':False}
    (out/'browser_acceptance.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:result[k] for k in ('status','versions','check_count')},ensure_ascii=False))


if __name__=='__main__':main()
