"""Actual production browser acceptance, including named users' critical searches."""
import argparse
import json
from pathlib import Path
from urllib.parse import urlencode
from playwright.sync_api import sync_playwright
from app.db import PROJECT_ROOT
from app.catalog import search_people,get_person
from app.source_acquisition import file_hash
from app.verification import json_write
from scripts.verify_stage2_browser import check_layout


def main():
    args=argparse.ArgumentParser();args.add_argument('--base-url',default='http://127.0.0.1:8065');args.add_argument('--output',default='outputs/stage65-release/browser');a=args.parse_args()
    out=PROJECT_ROOT/a.output;out.mkdir(parents=True,exist_ok=False)
    db=PROJECT_ROOT/'data/guji.db';before=file_hash(db)
    checks=[];layouts=[];errors=[];consoles=[];versions={};flows=[]
    core=json.loads((PROJECT_ROOT/'data/core_people_stage65.json').read_text(encoding='utf-8'))
    terms=['李白','太白','青莲居士','杜甫','苏轼','王安石','王珪','文忠','陶渊明']
    with sync_playwright() as p:
        for browser_name,exe in [('Chrome','C:/Program Files/Google/Chrome/Application/chrome.exe'),('Edge','C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe')]:
            if not Path(exe).exists():continue
            browser=p.chromium.launch(executable_path=exe,headless=True);versions[browser_name]=browser.version
            try:
                for width,height,size in [(1440,900,'desktop'),(390,844,'mobile'),(320,740,'narrow')]:
                    context=browser.new_context(viewport={'width':width,'height':height},is_mobile=width<760,has_touch=width<760)
                    page=context.new_page();page.on('pageerror',lambda e:errors.append(str(e)))
                    page.on('console',lambda m:consoles.append(m.text) if m.type=='error' else None)
                    label=browser_name+'-'+size
                    def ok(state):
                        check_layout(page,layouts,label+'-'+state)
                        checks.append({'check':label+'-'+state,'passed':True,'url':page.url,'title':page.title()})
                    page.goto(a.base_url,wait_until='networkidle')
                    assert 'GuJi ID' in page.title() and page.locator('main').inner_text().strip()
                    page.get_by_role('button',name='检查服务状态').click()
                    page.get_by_text('应用与数据库连接正常',exact=True).wait_for()
                    page.get_by_label('输入人物姓名或别称').focus();assert page.get_by_label('输入人物姓名或别称').evaluate('(e)=>e===document.activeElement')
                    ok('home')
                    page.screenshot(path=str(out/(label+'-home.png')),full_page=False)
                    for term in terms:
                        expected=search_people(db,term);assert expected
                        page.goto(a.base_url,wait_until='domcontentloaded')
                        page.get_by_label('输入人物姓名或别称').fill(term)
                        page.get_by_role('button',name='检索称呼',exact=True).click()
                        page.locator('#results').wait_for()
                        ids=page.locator('.candidate-card').evaluate_all('(es)=>es.map(e=>e.dataset.personId)')
                        assert ids==[r['person_id'] for r in expected],(term,ids)
                        assert len(ids)==len(set(ids))
                        if term=='王珪':assert len(ids)==2
                        if term=='太白':assert '匹配字：太白' in page.locator('#results').inner_text()
                        if term=='青莲居士':assert '匹配号：青蓮居士' in page.locator('#results').inner_text()
                        ok('search-'+term)
                        if term in {'李白','王珪'}:page.screenshot(path=str(out/(label+'-search-'+term+'.png')),full_page=True)
                        targets=expected if term=='王珪' else [expected[0]]
                        for target in targets:
                            pid=target['person_id'];person=get_person(db,pid)
                            page.goto(a.base_url+'/search?'+urlencode({'q':term}),wait_until='domcontentloaded')
                            page.locator(f'[data-person-id="{pid}"] .candidate-footer a').click()
                            page.get_by_role('heading',name=person['canonical_name'],exact=True).wait_for()
                            assert page.title().startswith(person['canonical_name'])
                            assert page.locator('[data-field="dynasty"]').inner_text().strip()
                            assert page.locator('.profile-facts').inner_text().count('暂无已核实信息')==0
                            page.locator('.field-provenance > summary').click()
                            assert page.locator('.field-source').first.is_visible()
                            assert page.locator('.field-source a').first.get_attribute('href').startswith('https://')
                            ok('profile-'+pid)
                            flows.append({'browser':browser_name,'viewport':size,'query':term,'person_id':pid,
                              'visible_facts':page.locator('.profile-facts').inner_text(),'sources_visible':True})
                            if term in {'李白','王珪'}:page.screenshot(path=str(out/(label+'-profile-'+pid+'.png')),full_page=True)
                            if term=='李白':
                                page.locator('.alias-list a').filter(has_text='青蓮居士').first.click()
                                page.get_by_role('heading',name='青蓮居士',exact=True).wait_for()
                                assert '结构化来源' in page.locator('main').inner_text()
                                assert page.locator('.structured-record').count()>0 and page.locator('.original-text').count()==0
                                ok('li-bai-structured-evidence')
                                page.screenshot(path=str(out/(label+'-li-bai-evidence.png')),full_page=True)
                    page.goto(a.base_url+'/search?'+urlencode({'q':'TEST_ONLY_UNRECORDED_65'}))
                    assert page.locator('.candidate-card').count()==0
                    page.get_by_role('heading',name='未找到已核对的匹配人物',exact=True).wait_for();ok('no-result')
                    if browser_name=='Chrome' and size=='desktop':
                        for name,era in core:
                            page.goto(a.base_url+'/search?'+urlencode({'q':name}),wait_until='domcontentloaded')
                            assert page.locator('.candidate-card').count()>0,name
                            ok('core50-'+name)
                    context.close()
            finally:browser.close()
    assert file_hash(db)==before
    result={'status':'passed' if versions and not errors and not consoles else 'failed','base_url':a.base_url,'browser_path':'regular Playwright','fallback_reason':'Browser plugin not available',
       'production_database':True,'versions':versions,'functional_checks':len(checks),'layout_checks':len(layouts),
       'checks':checks,'layouts':layouts,'flows':flows,'script_errors':errors,'console_errors':consoles,
       'database_sha256_before':before,'database_sha256_after':file_hash(db),'physical_mobile_tested':False}
    json_write(out/'browser_acceptance.json',result)
    assert result['status']=='passed',(errors,consoles)
    print(json.dumps({k:result[k] for k in ('status','versions','functional_checks','layout_checks')},ensure_ascii=False))


if __name__=='__main__':main()
