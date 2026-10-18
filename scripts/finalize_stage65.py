"""Produce the measured Stage 6.5 handoff only after real tests and browser QA."""
import json
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from app.db import PROJECT_ROOT
from app.source_acquisition import file_hash
from app.verification import json_write
from scripts.report_stage65 import main as report

OUT=PROJECT_ROOT/'outputs/stage65-release'


def load(path):return json.loads(Path(path).read_text(encoding='utf-8'))


def test_result(path):
    suites=ET.parse(path).getroot().findall('testsuite')
    result={k:sum(int(s.get(k,0)) for s in suites) for k in ('tests','failures','errors','skipped')}
    assert result['failures']==result['errors']==result['skipped']==0,result
    return result


def main():
    report();q=load(OUT/'quality_report.json')
    browser=load(OUT/'browser-release/browser_acceptance.json')
    assert browser['status']=='passed' and browser['database_sha256_after']==file_hash(PROJECT_ROOT/'data/guji.db')
    tests={'regression':test_result(OUT/'regression-release.xml'),'formal':test_result(OUT/'formal-tests-final.xml')}
    assert tests['regression']['tests']==213 and tests['formal']['tests']==54
    assert load(OUT/'multibatch-rollback/result.json')['status']=='passed'
    assert load(OUT/'primary-identity-dry/comparison.json')['C_grade_preserved_on_incremental_replay'] is True
    assert load(OUT/'deepseek_recovered_validation.json')['passed']==20
    assert q['core50']['passed']==50 and q['integrity_check']=='ok' and not q['foreign_key_errors']
    runtime={}
    for label,args in [('compileall',['-m','compileall','-q','app','scripts','tests']),('pip_check',['-m','pip','check'])]:
        p=subprocess.run([sys.executable,*args],cwd=PROJECT_ROOT,text=True,capture_output=True,encoding='utf-8')
        assert p.returncode==0,(label,p.stdout,p.stderr)
        runtime[label]={'exit_code':p.returncode,'stdout':p.stdout.strip(),'stderr':p.stderr.strip()}
    json_write(OUT/'runtime_checks.json',runtime);json_write(OUT/'test_summary.json',tests)
    before=q['original_cohort_before'];after=q['original_cohort_after'];allq=q['all_people_after'];ds=q['deepseek']
    rows=[]
    for field,label in [('dynasty','朝代'),('birth_year','生年'),('death_year','卒年'),('both_years','生卒年同时具备'),('either_year','至少一项生卒年'),('native_place','籍贯'),('courtesy_or_art','字或号'),('valid_noncanonical_alias','有效非本名称呼')]:
        rows.append(f"| {label} | {before[field]['count']}/400（{before[field]['percent']}%） | {after[field]['count']}/400（{after[field]['percent']}%） | {allq[field]['count']}/{allq['people']}（{allq[field]['percent']}%） |")
    grade_rows=[f"| {g} | {q['selected_field_grades'].get(g,0)} | {q['relation_grades'].get(g,0)} | {q['quality_record_grades'][g]} |" for g in ('A','B','C')]
    grade_rows.append(f"| D | 隔离，不作为已确认字段 | 隔离，不作为已确认关系 | {q['quarantine_records']} |")
    first=load(OUT/'formal-import/import_result.json');second=load(OUT/'formal-increment/import_result.json')
    call_rows=[]
    for number,status in sorted(ds['call_statuses'].items(),key=lambda x:int(x[0])):
        receipt=next((r for r in ds['receipts'] if str(r.get('calls_used'))==number),None)
        desc=f"{receipt['records']}条；有效返回{receipt['successful_records']}条" if receipt else '修复期间复现；项目未封存输入/响应，不推测记录数'
        call_rows.append(f'| {number} | {status} | {desc} |')
    problems='\n'.join(f"- {reason}：{count}条。" for reason,count in q['quarantine_reasons'].items())
    text=f'''# Guji ID Stage 6.5 实际交付报告

软件地址：**http://127.0.0.1:8065/**。正式库：`data/guji.db`。工作止于 Stage 6.5。

## 诊断和已实现修复

李白原来不在正式库。CBDB有诗人李白（32540），但旧程序按人物ID排序截取1200条称呼，再在400人停止，遗漏了该记录。旧程序还遗漏生卒字段和类型明确的籍贯表，原400人生年、卒年、籍贯均为空；10位手工人物没有跨库链接，连朝代也为空。检索缺少规范姓名简繁键、部分名称匹配和优先级。完整简短诊断见 `docs/stage65_diagnosis.md`。

现在以原400人补全优先，再从真实源补充核心人物；不再用前段抽样代表知名度。姓名和别称建立NFC/OpenCC检索键，原名及ID不变；精确优先、模糊次之，精确同名全部保留，模糊最多50个。没有硬编码李白结果。查询不依赖生卒/籍贯非空。两位王珪分别保留，文忠共享关系也保留。

进一步把50人测试绑定到稳定源ID后，发现温庭筠诗人记录以“溫廷筠”为规范名，“溫庭筠”为别名；已修正只优先导入同名规范名的分支，正式补入94814，保留同名他人。核心名单及源ID仅为测试锚点，实际数据仍逐行来自许可快照。

## 正式库和覆盖率

新增 **{q['new_people']}人、{q['new_aliases']}条称呼关系**。现有 **{q['counts']['people']}人、{q['counts']['aliases']}条称呼、{q['counts']['sources']}条来源、{q['counts']['evidences']}条证据**。原400人ID与姓名、原921条关系及原来源/证据逐行保留。结构化记录不是古籍原文，1723不能理解为1723段原典。

核心50人均要求命中相应CBDB稳定人物ID，并核对朝代，实际 **50/50，100%**，无缺失项。同名其他人不能代替测试目标。逐项结果见 `core50_results.json`。

| 字段 | 原400人修复前 | 同一400人修复后 | 全库修复后 |
| --- | --- | --- | --- |
{chr(10).join(rows)}

原400人已填写的实质字段1554项均有独立字段来源，覆盖率100%；全库为{allq['explicit_field_provenance']['with_field_level_provenance']}/{allq['explicit_field_provenance']['populated_fields']}。修复前没有独立逐字段审计行，不代表原有CBDB记录和古籍证据不存在。CBDB人物ID模板文字不算人物简介；原400人仍无可靠成篇简介，本次没有编造。仅陶潜作者资料补入一条C级身份说明。

年份只读取实际生卒列，0按未知，范围/约数保留；索引年不冒充生年。籍贯来自BIOG_ADDR_DATA的基本籍贯或本贯类型，不把索引地址或出生地冒充籍贯。基本地址是来源所记归属，不能宣称等同现代意义的出生地。

## 质量等级和展示

| 等级 | 已选用字段值 | 称呼关系 | 质量记录合计 |
| --- | --- | --- | --- |
{chr(10).join(grade_rows)}

A为原文证据核实，B为结构化来源，C为资料补充，D为冲突与存疑。合计单位是“选用的字段值＋关系判定”；D是本轮隔离候选，不能与人物数或证据数混用。全部来源观察数另列在 `quality_report.json`，未选用观察不作为已确认事实统计。历史入库方法A/A+代码不回写，分别对应新等级B/A，避免改写历史日志。

人物不同字段可有不同等级。详情只显示已知部分，缺项折叠；逐字段可展开版本、记录和定位。冲突的B来源观察不在公开详情中作为补充事实展示。C级作者资料在重放时仍保持C级，不因JSON结构记录而误升B级。D记录不公开、不强制通过。

历史38条候选全部重新进入自动规则：6条可关联已有来源关系，32条仍隔离。隔离原因如下：

{problems}

两位王珪无合并冲突；戴胄的正史朝代、籍贯可展示，但其跨CBDB身份因候选缺字仍不确定，不能填造生卒。旧第六阶段60条隔离记录继续保存在 `data/ingestion/stage6.sqlite3`；它们是历史流水线快照，与本轮候选有来源重叠，未简单相加。

## DeepSeek真实调用和结果

入口一直是现有 `Invoke-ClaudeAssist.ps1` → Claude Code CLI → 配置好的DeepSeek Anthropic兼容接口。主任务ID一直是 `guji-id-stage65-20261017`。没有新建API客户端、切换提供方、清除历史或模拟成功。

账本实际模型尝试 **{ds['calls_used']}次**；用户追加授权后的总上限{ds['max_calls']}，没有为用满额度而继续调用。已封存项目批次{ds['sealed_project_batches']}批，记录尝试{ds['record_attempts']}条；其中成功处理{ds['successful_records']}条、失败/无完整结果{ds['failed_record_attempts']}条，包含重复尝试，不是这些数量的不同人物。另有修复复现调用未封存业务输入，记录数不推测。

| 账本次数 | 实际状态 | 已封存记录范围 |
| --- | --- | --- |
{chr(10).join(call_rows)}

第4次的真实成功响应由用户从修复过程恢复，原样保存为 `deepseek_recovered_response.json`；账本OK事件、20条ID/字段、9条稳定身份关联和原始资料哈希/修订号全部复核。该缓存复用没有新增付费调用。结果为 **19条supported、1条uncertain**；它们不是19条自动新增史实。唐王珪33013、宋王珪1845分开；戴胄不通过。核对详情在 `deepseek_recovered_validation.json`。

后续40条、20条批次在旧120秒限制超时；用户改为600秒后补充真实年代、地址备注、书名和身份候选再核验20条，最后得到 **output_limit / 8192 tokens** 的明确失败。失败停止规则继续生效；没有把这些失败记录计为AI核验通过。其来源输入分别已由本地程序重现，但模型语义判断未完成，仍隔离。此处不声称全部疑难资料已由AI解决。

成功响应可获得用量：input_tokens **5287**、output_tokens **5165**、cache_read **0**、cache_creation **0**。失败/超时用量及真实DeepSeek账单金额未返回，费用为未知，不按0元或Claude估价冒充实际账单。全部回执、响应、授权备份与账本快照保留。

## 实际软件及浏览器验收

正式软件由最终服务进程运行于 http://127.0.0.1:8065/ 。Browser插件不可用，使用已安装真实Chrome/Edge的Playwright；不是TestClient代替浏览器。版本：{json.dumps(browser['versions'],ensure_ascii=False)}。

实际测试1440×900、390×844、320×740；**{browser['functional_checks']}项功能检查、{browser['layout_checks']}项布局检查通过**。从表单真实搜索李白、太白、青莲居士、杜甫、苏轼、王安石、两位王珪、文忠、陶渊明；点击人物卡片，核对可见朝代、生卒/籍贯，展开字段来源，进入青莲居士证据页，确认JSON结构化记录没有冒充古籍原句。桌面Chrome另逐项搜索50人。姓名、字和号的匹配原因可见；空结果、健康检查、键盘焦点均实测。JS与控制台错误0；横向溢出0。原favicon 404已修复。真实物理手机和Safari/Firefox未测试。

浏览器过程及截图在 `browser-release/`；浏览器前后数据库哈希相同，未改史料。

## 测试、备份和回滚

- 全量既有及新增回归：**213通过，0失败，0跳过**，`regression-release.xml`。
- 50人稳定身份、李白字号、原400人/921关系、同名与完整性专项：**54通过**，`formal-tests-final.xml`。
- SQLite integrity_check=ok，foreign_key_check无错误；compileall及pip check通过。测试中仅有既有Starlette/httpx弃用警告。
- 独立真实数据副本验证重复导入零新增、后续修改阻止快照覆盖、整批回滚、重新导入；额外验证实际两批的回滚顺序保护，先恢复453人，再精确恢复原400人与921关系。正式库没有执行测试回滚。
- 最终重复导入回执为cache_reused=true、inserted=0。人工未逐条批准；准入由真实来源、类型/身份/字段规则及独立程序校验完成。

首批备份：`{first['backup']['path']}`

增量备份：`{second['backup']['path']}`

完整Stage 6.5回滚必须倒序：

```powershell
work\\.venv\\Scripts\\python.exe -m scripts.enrich_stage65 --rollback {second['batch_id']} --output outputs/stage65-rollback-latest
work\\.venv\\Scripts\\python.exe -m scripts.enrich_stage65 --rollback {first['batch_id']} --output outputs/stage65-rollback-original
```

有后续数据修改时，保护机制拒绝快照回滚，不能覆盖新数据；先评估后续批次。回滚前还会再备份当前库，原快照有SHA-256及完整性检查。实际副本回滚回执见 `multibatch-rollback/result.json`，旧单批重放验证见 `pipeline-validation-final/result.json`。

正式库SHA-256：`{q['db_sha256']}`。

## 来源及使用范围

CBDB采用官方固定历史发布20250520，按既有许可审查用于非商业课程，注明CBDB及版本，衍生数据遵循CC BY-NC-SA 4.0；未采用超出许可总量的较新快照。许可说明： https://cbdb.hsites.harvard.edu/cbdb-data-licensing-terms 。陶渊明作者资料仅作C级补充，固定修订 https://zh.wikisource.org/w/index.php?oldid=2307897 ，CC BY-SA 4.0；缓存和哈希保留，不冒充古籍原文。

本报告采用真实库、真实测试和真实调用数据。仍有冲突和史料缺失；本次交付没有进入第七阶段。
'''
    (PROJECT_ROOT/'docs/stage65_verification.md').write_text(text,encoding='utf-8')
    (OUT/'REPORT.md').write_text(text,encoding='utf-8')
    json_write(OUT/'delivery_manifest.json',{'stage':'6.5','status':'delivered_with_declared_quarantine',
       'counts':q['counts'],'database_sha256':q['db_sha256'],'core50':q['core50']['percent'],'tests':tests,
       'browser_checks':browser['functional_checks'],'deepseek_successful_records':ds['successful_records'],
       'deepseek_calls':ds['calls_used'],'remaining_conflicts':q['quarantine_records'],'service_url':'http://127.0.0.1:8065/'})
    print(json.dumps({'report':str(PROJECT_ROOT/'docs/stage65_verification.md'),'counts':q['counts'],'tests':tests,'core50':q['core50']['percent']},ensure_ascii=False))


if __name__=='__main__':main()
