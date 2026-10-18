"""Browser acceptance across the ten pinned Song History source volumes."""
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
    parser.add_argument('--output',default='work/paragraph-expansion/browser-kanripo')
    args=parser.parse_args();out=PROJECT_ROOT/args.output;out.mkdir(parents=True,exist_ok=False)
    db=PROJECT_ROOT/'data/guji.db';before=hashlib.sha256(db.read_bytes()).hexdigest()
    manifest=json.loads((PROJECT_ROOT/'work/paragraph-expansion/kanripo-formal/validated_manifest.json').read_text(encoding='utf-8'))
    cases=manifest['cases'];by_file={}
    for case in cases:by_file.setdefault(case['file'],case)
    sampled=list(by_file.values())
    assert len(cases)==35 and len(sampled)==10
    with connect_db(db) as c:
        for case in sampled:
            case['source_id']=c.execute("SELECT source_id FROM evidences WHERE alias_id=? AND evidence_id LIKE 'E-KR-PARA-%'",
                (case['alias_id'],)).fetchone()[0]
    checks=[];errors=[];screens=[];versions={}
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
                    page.on('console',lambda m:errors.append(m.text) if m.type=='error' else None)
                    def check(item):
                        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth'),(item,page.url,width)
                        checks.append({'browser':browser_name,'viewport':[width,height],'check':item,'url':page.url})
                    for case in sampled:
                        name,pid=case['person_name'],case['person_id']
                        page.goto(args.base_url+'/search?'+urlencode({'q':name}),wait_until='domcontentloaded')
                        card=page.locator(f'[data-person-id="{pid}"]')
                        assert card.count()==1
                        card.locator('.candidate-footer a').click()
                        page.get_by_role('heading',name=name,exact=True).wait_for()
                        assert case['quote'] in page.locator('.identity-evidences').inner_text()
                        check(name+'-profile')
                        page.goto(args.base_url+'/sources/S-CBDB65-20250520?'+urlencode({'person_id':pid}))
                        assert '其他独立来源中的古籍原文' in page.locator('.source-originals').inner_text()
                        assert name+'字'+case['courtesy'] in page.locator('.source-originals mark').first.inner_text()
                        page.get_by_role('link',name='查看这条独立来源的档案').first.click()
                        assert case['source_id'] in page.url
                        block=page.locator('.source-originals blockquote').first
                        paragraph=block.inner_text()
                        assert len(paragraph)>=100 and name+'字'+case['courtesy'] in paragraph
                        assert page.locator('.source-originals mark').first.evaluate(
                            '(node)=>getComputedStyle(node).color')=='rgb(160, 47, 37)'
                        link=page.locator('.source-original-links a').first.get_attribute('href')
                        assert case['commit'] in link and f"#L{case['line_start']}-L{case['line_end']}" in link
                        assert '排版行、页标记已合并' in page.locator('.source-original-entry').first.inner_text()
                        check(name+'-source-line-paragraph')
                        if browser_name=='Chrome' and label in {'desktop','mobile'} and case['file'] in ('KR2a0032_277.txt','KR2a0032_301.txt'):
                            path=out/f'{browser_name}-{label}-{name}-{case["file"]}.png'
                            page.screenshot(path=str(path),full_page=True);screens.append(str(path))
                        if label=='desktop':
                            button=page.locator('.source-copy-quote').first
                            button.click()
                            page.wait_for_function('(expected)=>navigator.clipboard.readText().then(text=>text===expected)',arg=paragraph)
                            check(name+'-copy-full-paragraph')
                        page.goto(args.base_url+'/aliases/'+case['alias_id'])
                        assert name+'字'+case['courtesy'] in page.locator('.original-text mark').first.inner_text()
                        assert '#L' in page.locator('.evidence-section').inner_html()
                        check(name+'-alias-evidence')
                    context.close()
            finally:browser.close()
    assert versions and not errors,errors
    after=hashlib.sha256(db.read_bytes()).hexdigest();assert before==after
    report={'status':'passed','versions':versions,'sampled_people':len(sampled),'sampled_volumes':len(by_file),
        'check_count':len(checks),'checks':checks,'screenshots':screens,'errors':errors,
        'database_sha256_before':before,'database_sha256_after':after,'physical_mobile_tested':False}
    (out/'browser_acceptance.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:report[k] for k in ('status','versions','sampled_people','check_count')},ensure_ascii=False))


if __name__=='__main__':main()
