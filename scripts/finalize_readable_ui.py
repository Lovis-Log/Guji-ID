"""Measured handoff for the read-only presentation refactor."""
import ast
import hashlib
import json
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from app.db import PROJECT_ROOT,connect_db
from app.field_quality import fingerprint
from app.verification import json_write


def load(path):return json.loads(Path(path).read_text(encoding='utf-8'))


def test_summary(path):
    suites=ET.parse(path).getroot().findall('testsuite')
    result={key:sum(int(s.get(key,0)) for s in suites) for key in ('tests','failures','errors','skipped')}
    assert result['failures']==result['errors']==result['skipped']==0,result
    return result


def main():
    out=PROJECT_ROOT/'outputs/readable-ui';baseline=load(out/'baseline.json')
    browser=load(out/'browser-delivery/browser_acceptance.json')
    tests={'regression':test_summary(out/'regression-final.xml'),'formal':test_summary(out/'formal-final.xml')}
    assert browser['status']=='passed' and not browser['page_errors'] and not browser['console_errors']
    assert tests['regression']['tests']==231 and tests['formal']['tests']==61
    db=PROJECT_ROOT/'data/guji.db';current_hash=hashlib.sha256(db.read_bytes()).hexdigest()
    with connect_db(db) as c:
        counts={t:c.execute('SELECT count(*) FROM '+t).fetchone()[0] for t in baseline['counts']}
        data_fingerprint=fingerprint(c)
        integrity=c.execute('PRAGMA integrity_check').fetchone()[0]
        fk=[tuple(r) for r in c.execute('PRAGMA foreign_key_check')]
    assert current_hash==baseline['database_sha256']==browser['database_sha256_after']
    assert counts==baseline['counts'] and data_fingerprint==baseline['database_fingerprint']
    assert integrity=='ok' and not fk
    def public_policy(path):
        tree=ast.parse(Path(path).read_text(encoding='utf-8'))
        return next(ast.literal_eval(node.value) for node in tree.body if isinstance(node,ast.Assign)
          and any(isinstance(t,ast.Name) and t.id=='PUBLIC_CTE' for t in node.targets))
    assert public_policy(PROJECT_ROOT/'app/catalog.py')==public_policy(out/'source-before/app/catalog.py')
    runtime={}
    for label,args in [('python_syntax',[sys.executable,'-m','compileall','-q','app','scripts','tests']),
       ('dependencies',[sys.executable,'-m','pip','check']),('javascript_syntax',['node','--check','app/static/app.js'])]:
        p=subprocess.run(args,cwd=PROJECT_ROOT,capture_output=True,text=True,encoding='utf-8')
        assert p.returncode==0,(label,p.stdout,p.stderr)
        runtime[label]={'exit_code':p.returncode,'stdout':p.stdout.strip(),'stderr':p.stderr.strip()}
    audit=load(out/'source_audit.json');review=load(out/'claude-review/receipt.json')
    result={'status':'passed','service_url':'http://127.0.0.1:8065/','counts':counts,'database_sha256_before':baseline['database_sha256'],
      'database_sha256_after':current_hash,'data_fingerprint_unchanged':True,'public_policy_unchanged':True,
      'integrity_check':integrity,'foreign_key_errors':fk,'tests':tests,'browser_checks':len(browser['checks']),
      'browser_versions':browser['versions'],'browser_viewports':['1440×900','390×844','320×740'],
      'source_types':audit['kinds'],'runtime':runtime,'claude_review':review,
      'codex_subagents':['presentation_mapping','readable_templates','presentation_tests'],
      'physical_mobile_tested':False,'known_unresolved_functional_issues':[]}
    json_write(out/'acceptance.json',result)
    text=f'''# 全站结构化数据可读展示整改交付

软件地址：[http://127.0.0.1:8065/](http://127.0.0.1:8065/)。示例：[杜甫](http://127.0.0.1:8065/people/P-CBDB-3915)、[李白](http://127.0.0.1:8065/people/P-CBDB-32540)。

## 实际结果

人物、称呼、证据和新增文献来源页使用统一可读展示模型。默认阅读层及展开的学术出处都不再打印CBDB JSON、Python字典、原始列名或哈希。只有用户选择“查看原始记录”时，才从只读接口请求完整记录，在独立技术弹窗中显示、复制。无JavaScript时仍可由该链接直接读取原始JSON。

杜甫默认显示唐、712—770、字子美、来源CBDB和精确版本20250520。李白称呼证据改为自然中文关系句，删除对同一称呼的重复字段陈列。日期、地点、类别均由真实数据生成，没有按人名硬编码。反对、仅提及和未定材料仅描述“记录涉及”，不被写成肯定归属。

全库仍为{counts['people']}人、{counts['aliases']}条称呼、{counts['sources']}条来源、{counts['evidences']}条证据。数据库字节哈希和业务指纹均与整改前一致；没有迁移、删除或重写来源、证据、核验状态、身份关联、入库算法。

## 修改组件与文件

| 范围 | 文件 | 实际职责 |
| --- | --- | --- |
| 通用语义转换 | `app/presentation.py` | 纯函数、中文字段、日期/版本、类别保守映射、来源分类、缺失降级、自然语言证据摘要 |
| 既有只读视图接入 | `app/catalog.py` | 在旧对象上增加展示模型，安全过滤简介和身份摘录，不改公开查询规则 |
| 文献与技术只读查询 | `app/source_catalog.py` | 来源与人物/字段/证据对应、分页、人物筛选、完整原始记录访问门槛 |
| 路由 | `app/main.py` | `/sources/…`及`/records/…`，其余检索及人物路由保留 |
| 人物、称呼、文献页面 | `app/templates/person.html`、`alias.html`、`source.html`、`index.html` | 普通阅读、学术出处和技术层分离，搜索预览也只取安全展示值 |
| 公共组件 | `app/templates/_source_card.html`、`_raw_record_dialog.html`、`base.html` | 共享书目卡、中文定位、真实原文引文、原始数据弹窗 |
| 排版与交互 | `app/static/styles.css`、`app.js` | 宋体与纸白、细线书目、移动换行、克制展开动画、降动效、按需加载/复制/Escape/焦点返回 |
| 测试 | `tests/test_readable_presentation.py`、`formal_readable_presentation.py`、`scripts/verify_readable_presentation.py` | 合成夹具隔离、真实正式库检查、真实浏览器及XSS |
| 旧契约更新 | `tests/test_automatic_ingestion.py`、`test_catalog.py` | 删除旧“必须直接输出原始JSON”的要求；搜索紧凑纪年仍保留公元前标记，详情完整纪年断言保留 |

项目没有Git仓库，整改前源文件快照保存在 `outputs/readable-ui/source-before/`，数据库只读备份为 `outputs/readable-ui/before-presentation.db`；旧界面真实截图在 `outputs/readable-ui/before/`。未把独立沉浸式设计原型当作已接入的历史数据页面，阅读区沿用其纸白、书目与朱批风格，没有添加伪造书影。

## 如何转换结构化数据

| 机器字段或结构 | 可读展示 |
| --- | --- |
| `c_name_chn`、`canonical_name` | 姓名 |
| `c_birthyear`、`c_deathyear` | 生年、卒年；紧凑生卒范围或完整公元纪年，保留已知约数/范围 |
| `c_alt_name_chn`、已知别称类别码 | 字、号、其他姓名形式等；只采用已确认代码表，不猜未知码 |
| `c_personid` | 学术定位中的数据库人物编号 |
| `BIOG_MAIN`、`ALTNAME_DATA`、`BIOG_ADDR_DATA` | 人物基本资料、别称记录、人物地址记录 |
| `bibliography`、卷篇页码 | 中文书目及数据库所记页码；引用书目不等于已经取得原典 |
| `record_json`、`locator_json` | 阅读层不显示；可解释部分转成中文事实、版本和定位，完整原值只在技术层 |

适配旧扁平记录及`person/raw`、`row`、`fields/key`、`rows`等真实载荷。字段出处仅保留与该字段相关的信息，不在每个生年/籍贯卡中重复整个人物主表。0、null、空值和未知枚举不默认显示；无法解释的结构保留清楚的来源说明和原始入口，不改写成史实。

## 三类证据与三级信息层

1. **古籍原文**：保留真实引文、书名、卷篇与位置、称呼关系及原始链接。字段学术出处也可直接读古籍摘录，不必先看JSON。源库没有保存作者或时代时不补造。
2. **结构化数据库**：明确标记数据库来源，以自然中文说明人物/字号关系，提供中文来源表含义、精确版本、记录定位与官方链接；“直接记录”不标成古籍直接证明。
3. **辅助来源**：作者资料等单独标记，不因旧`structured_record`标记而误认CBDB或古籍原文。C级陶渊明作者页实测属于此类。

全部17条来源分类检查：13条古籍来源、3条结构化数据库来源、1条辅助来源。每条证据独立分类，人物的多条来源分别对应，未按同名合并身份。

默认层显示人物、称呼、简洁来源和版本；学术层显示书目、页码、卷篇、中文定位与引用；技术层是独立原始记录弹窗，默认关闭且初始内容为空，只有点击才fetch。关闭后清空内容并恢复焦点，支持复制、Escape及直接打开JSON。

新增来源页可以按当前人物筛选和分页，不把数据库来源关联的数百人一次塞成大段记录。来源列表中的每项关系链接到正确的证据锚点，保留两位王珪各自身份。

## 原始数据仍完整可用

接口：`GET /records/field/{{id}}`、`/records/evidence/{{id}}`、`/records/source/{{id}}`。

原底表字段逐列保留，JSON字段保持原始字符串。旧自动入库辅助表的完整源载荷和定位另外提供为`source_record`、`source_record_locator`，不因阅读转换丢失。技术接口复用当前公开规则：D级、冲突隔离、待核人物及未公开证据/孤立来源均404；它不是绕过核验的管理接口。JSON响应使用正确内容类型和nosniff；弹窗使用textContent，不把原始内容当HTML执行。

## 实际测试与浏览器验收

- 全量回归 **{tests['regression']['tests']}通过、0失败、0跳过**，包含原检索/导入/隔离测试及新增展示测试。
- 正式数据专项 **{tests['formal']['tests']}通过**：7项本次真实页面/数据专项，以及54项核心人物与身份回归。
- 真实浏览器 **{len(browser['checks'])}项通过、48张截图**，{json.dumps(browser['versions'],ensure_ascii=False)}；1440×900、390×844、320×740。
- 杜甫生卒、子美、CBDB和版本；李白多称呼；混合来源；只有结构化来源；真实古籍摘录/定位；C级作者资料；缺失字段；两位王珪；来源筛选分页；默认无原始字段/JSON；原始逐列保真均实测。
- 原始JSON按需请求、复制、Escape焦点回归、原文复制、浏览器内TEST_ONLY恶意内容注入均通过。恶意夹具仅作用于测试库或浏览器拦截，没有写入正式资料。
- 无横向溢出，无脚本/控制台错误。最后移除了一处空摘要容器装饰，并额外验证桌面/手机截图，不改变任何内容。
- Python compileall、pip check、JavaScript语法通过。保留既有Starlette/httpx弃用警告，不影响通过结果。
- Browser插件当前不可用，使用现有Playwright和已安装Chrome/Edge；没有冒充实体手机或Safari/Firefox验收。

结果：`outputs/readable-ui/acceptance.json`；XML：`regression-final.xml`、`formal-final.xml`；浏览器：`browser-delivery/browser_acceptance.json`。交付截图 `delivery-desktop-du-fu-readable.png`、`delivery-mobile-li-bai-readable.png` 等均在该目录。

## 并行实现及Claude记录

按用户要求调用三个Codex原生子代理，分别负责纯语义映射、模板/视觉组件、自动化/真实浏览器验收；主代理统一接入路由、来源查询并整合，分配了不重叠的文件范围。

用户显式激活claude后曾通过现有桥接做一次展示契约只读审查，任务ID `guji-id-readable-ui-20261017`。该次在调整前返回CLI_ERROR / output_limit，没有可用建议，未伪称审核通过；日志保留在 `outputs/readable-ui/claude-review/`。后续无需再付费推断历史资料，实际正确性依赖本地转换、原生子代理复核、真实库和浏览器测试。没有新增DeepSeek API客户端，没有重新批量核验历史数据。

## 未解决事项与数据保护

没有发现仍未修复的本次功能问题。源库未提供的作者、年代、页码及未知编码继续按缺失或待确认处理，不编造。移动端为浏览器视口验收，未进行实体手机及其他浏览器测试。

SQLite完整性为{integrity}，外键错误0；整改前后数据库SHA-256完全相同：

`{current_hash}`

业务数据指纹不变，原公开SQL规则文本也不变。正式库454人、1195条称呼、17条来源、1723条证据保持原样。本次没有进入新的史料扩容或核验阶段。
'''
    (PROJECT_ROOT/'docs/readable_ui.md').write_text(text,encoding='utf-8')
    (out/'REPORT.md').write_text(text,encoding='utf-8')
    print(json.dumps({'status':'passed','counts':counts,'regression':tests['regression']['tests'],'formal':tests['formal']['tests'],'browser_checks':len(browser['checks']),'database_unchanged':True,'report':str(PROJECT_ROOT/'docs/readable_ui.md')},ensure_ascii=False))


if __name__=='__main__':main()
