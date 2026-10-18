"""Actual production browser checks for automatic admission and legacy behavior."""
import argparse
import json
from pathlib import Path
from urllib.parse import urlencode
from app.batch_import import database_counts,existing_connection
from app.automatic_ingestion import search_key
from app.collector import digest
from app.db import PROJECT_ROOT
from app.verification import json_write
from scripts.verify_stage2_browser import server,check_layout


def verify(output):
    from playwright.sync_api import sync_playwright
    out=Path(output).resolve()
    if out.exists() or not any(out.is_relative_to(PROJECT_ROOT/f) for f in ('work','outputs')):
        raise ValueError('须使用项目内尚不存在的验收目录')
    out.mkdir(parents=True)
    db=PROJECT_ROOT/'data/guji.db'
    before=digest(db.read_bytes())
    with existing_connection(db) as c:
        rows=c.execute("SELECT a.* FROM aliases a WHERE person_id LIKE 'P-CBDB-%' AND NOT EXISTS(SELECT 1 FROM automatic_evidence q JOIN evidences e USING(evidence_id) WHERE e.alias_id=a.alias_id AND q.method='A+')").fetchall()
        structured=next(dict(r) for r in rows if search_key(r['alias_name'])!=r['alias_name'])
        classical=dict(c.execute("SELECT a.* FROM automatic_evidence q JOIN evidences e USING(evidence_id) JOIN aliases a USING(alias_id) WHERE q.method='A+' LIMIT 1").fetchone())
    checks,layouts,errors,versions=[],[],[],{}
    with sync_playwright() as p,server(db,out) as base:
        for name,exe in [('Chrome','C:/Program Files/Google/Chrome/Application/chrome.exe'),('Edge','C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe')]:
            if not Path(exe).exists():
                continue
            browser=p.chromium.launch(executable_path=exe,headless=True)
            versions[name]=browser.version
            try:
                for width,height,size in [(1440,1000,'desktop'),(390,844,'mobile'),(320,740,'narrow')]:
                    context=browser.new_context(viewport={'width':width,'height':height},has_touch=width<760,is_mobile=width<760)
                    page=context.new_page()
                    page.on('pageerror',lambda error:errors.append(str(error)))
                    label=name+'-'+size
                    def ok(state):
                        check_layout(page,layouts,label+'-'+state)
                        checks.append({'check':label+'-'+state,'passed':True})
                    page.goto(base,wait_until='networkidle')
                    assert page.get_by_role('heading',name='自动数据流水线').is_visible()
                    page.get_by_role('button',name='检查服务状态').click()
                    page.get_by_text('应用与数据库连接正常',exact=True).wait_for()
                    ok('home')
                    page.screenshot(path=str(out/(label+'-home.png')),full_page=True)
                    page.get_by_label('输入人物姓名或别称').fill(search_key(structured['alias_name']))
                    page.get_by_role('button',name='检索称呼',exact=True).click()
                    page.locator('#results').wait_for()
                    assert page.locator(f'[data-person-id="{structured["person_id"]}"]').count()==1
                    ok('simplified-search-preserves-identity')
                    page.locator(f'[data-person-id="{structured["person_id"]}"] .candidate-footer a').click()
                    page.get_by_role('heading',name='身份信息',exact=True).wait_for()
                    assert '来源人物ID：CBDB' in page.content()
                    ok('structured-person')
                    page.goto(base+'/aliases/'+structured['alias_id'])
                    page.get_by_role('heading',name='结构化来源证据',exact=True).wait_for()
                    assert page.locator('.structured-record').count()>=1
                    assert page.locator('.original-text').count()==0
                    assert 'CC BY-NC-SA 4.0' in page.content()
                    ok('structured-evidence-not-a-quote')
                    page.screenshot(path=str(out/(label+'-structured-evidence.png')),full_page=True)
                    page.goto(base+'/aliases/'+classical['alias_id'])
                    assert page.locator('.original-text').count()>=1 and page.locator('.structured-record').count()>=1
                    assert '原文证据核准' in page.content() and '原文定位' in page.content()
                    href=page.locator('.source-link a').all_text_contents()
                    assert href
                    ok('classical-and-structured-distinguished')
                    page.screenshot(path=str(out/(label+'-classical-evidence.png')),full_page=True)
                    for term,minimum in [('文忠',4),('王珪',2),('鄭驤',2)]:
                        page.goto(base+'/search?'+urlencode({'q':term})+'#results')
                        page.get_by_text('存在多个候选，不能确定',exact=True).wait_for()
                        assert page.locator('.candidate-card').count()>=minimum
                        ids=page.locator('.candidate-card').evaluate_all('es=>es.map(e=>e.dataset.personId)')
                        assert len(ids)==len(set(ids))
                        if term=='文忠':
                            assert '苏轼' in page.locator('.candidate-grid').inner_text() and '欧阳修' in page.locator('.candidate-grid').inner_text()
                        if term=='王珪':
                            assert '叔玠' in page.locator('.candidate-grid').inner_text() and '禹玉' in page.locator('.candidate-grid').inner_text()
                        if term=='鄭驤':
                            assert '结构化来源中的称呼' in page.locator('.candidate-grid').inner_text()
                        ok('disambiguation-'+term)
                        page.screenshot(path=str(out/(label+'-'+term+'.png')),full_page=True)
                    assert page.goto(base+'/aliases/A-0d5d106989f70b80b344b781').status==404
                    ok('legacy-held-id-remains-hidden')
                    page.goto(base+'/search?'+urlencode({'q':'TEST_UNRECORDED_NAME_000'}))
                    page.get_by_role('heading',name='未找到已核对的匹配人物',exact=True).wait_for()
                    ok('no-result')
                    context.close()
            finally:
                browser.close()
    assert versions and not errors
    assert digest(db.read_bytes())==before
    result={'status':'passed','database':'actual_production','counts':database_counts(db),'versions':versions,'functional_checks':len(checks),'layout_checks':len(layouts),'checks':checks,'layouts':layouts,'script_errors':errors,'physical_mobile_tested':False,'database_sha256_before':before,'database_sha256_after':digest(db.read_bytes())}
    json_write(out/'browser_acceptance.json',result)
    print({k:result[k] for k in ('status','counts','functional_checks','layout_checks','versions')})


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',required=True)
    verify(p.parse_args().output)
