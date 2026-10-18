# 古籍原文查阅增强（2026-10-18）

## 结果与边界

正式库现有454人、1195条称呼关系、19条来源、1725条证据。原有13条古籍来源中的20条已公开、可定位原文，现可在各自的文献来源页直接阅读。新增《舊唐書》卷190下两个有明确人物与字号对应的转录摘录：李白“字太白”、杜甫“字子美”。这是两条独立的A级原文证据；没有据此改写出生、死亡、籍贯、号或人物简介。结构化数据库的记录仍按结构化来源呈现，不因另一条古籍原文而变成原典证据。

普通阅读层显示原文摘录、书名、卷次、对应人物及字号关系；学术溯源层提供固定版本原文链接与证据详情；技术原始记录仍在独立折叠面板。选择人物查看 CBDB 来源时，页面另设“其他独立来源中的古籍原文”，明确告知这是来自另一来源的特定关系证据，不证明 CBDB 的其他字段。没有已核实原文的数据库记录会直说这一点，不填入模拟引文。本次使用可核对的转录文本，没有添加未经确认版权或书页定位的古籍影像。

## 获取、校验和许可

原文取自维基文库《[舊唐書/卷190下](https://zh.wikisource.org/w/index.php?oldid=2636533)》固定修订2636533。采集遵循[MediaWiki API 礼仪](https://www.mediawiki.org/wiki/API:Etiquette)：明确 User-Agent、串行调用、缓存后本地重放。原始API响应、解析文本、SHA-256、字符区间和章节锚点保存在 `data/external/wikisource-jts190b/`；机器可读核验清单为 `validated_quotes.json`。原始响应SHA-256是 `0dd9136477bcb450cdf7c12507db6bd1fcb86db327df44279b6d56486a7d7a8c`。引用按照[维基文库版权说明](https://zh.wikisource.org/wiki/Wikisource:%E7%89%88%E6%9D%83%E4%BF%A1%E6%81%AF)标明维基文库固定修订和CC BY-SA 4.0；古籍原作与平台转录/贡献的权利说明分开理解。

`scripts/acquire_jts190b.py`核对实际原始响应与固定修订、篇名、唯一出现的直接“人物—字”措辞、字符位置，并将正式人物ID与CBDB人员编号、朝代、已有字关系一一比对。`scripts/import_jts190b.py`在事务中再次验证缓存哈希、人员身份、原有称呼ID、正式准入与来源，再插入两条来源、两条证据，只将对应的两条关系质量从B提升至A。重复导入返回零新增。旧人物、称呼、来源和证据逐行保留。

用户已激活的 Claude Code 桥接器曾用于只读检查这两处原文的可支持范围：同一任务ID `guji-id-original-source-20261018`，1次真实成功调用、2条记录，模型 `deepseek-flash[1m]`，输入704 tokens、输出2583 tokens；回执在 `outputs/classical-source/claude-review/receipt.json`。模型意见只作为辅助，正式准入由上述可重放的程序校验决定。没有追加模型调用。

## 代码与数据位置

- `app/source_catalog.py`：仅从已公开、经类型识别的古籍证据抽取原文及对应关系，避免把数据库字段当原文；已指定人物的结构化来源才并列显示其他独立古籍来源。
- `app/templates/source.html`、`app/static/styles.css`、`app/static/app.js`：原文引文、出处链接、复制按钮和手机排版。
- `app/catalog.py`、`app/templates/alias.html`：固定修订原文证据的定位与等级标签。
- `scripts/acquire_jts190b.py`、`scripts/import_jts190b.py`、`scripts/verify_jts190b_rollback.py`：采集重放、事务入库和副本回滚验证。
- `app/collector.py`：原始响应与正文在同一目录离线重放时逐字校验并只读使用，不再覆写已保存的采集档案；向另一个输出目录重放时仍生成独立副本。
- `tests/test_classical_source_text.py`、`scripts/verify_classical_source_browser.py`：数据库与真实浏览器验收。

## 可复核的页面

- [李白的《舊唐書》原文来源](http://127.0.0.1:8065/sources/S-JTS190B-d5f669f419047217b3884adf?person_id=P-CBDB-32540)
- [杜甫的《舊唐書》原文来源](http://127.0.0.1:8065/sources/S-JTS190B-00f600e5228df017729fb6da?person_id=P-CBDB-3915)
- [李白的CBDB来源与独立原文](http://127.0.0.1:8065/sources/S-CBDB65-20250520?person_id=P-CBDB-32540)
- [杜甫的CBDB来源与独立原文](http://127.0.0.1:8065/sources/S-CBDB65-20250520?person_id=P-CBDB-3915)

## 验收与恢复

入库前备份：`outputs/classical-source/formal-import/backups/20261018T055244649513Z-5c2540d409a64c05a1c942ecca0d4318.db`；批次ID `ST65-TEXT-8a03f0688b00db665a84`。正式入库结果在 `outputs/classical-source/formal-import/import_result.json`，幂等重放在 `outputs/classical-source/replay/repeat_result.json`。在真实正式库的副本上执行既有批量回滚，结果完整还原454人、1195关系、17来源、1723证据及旧关系质量，正式库未回滚；审计在 `outputs/classical-source/rollback-verification/result.json`。

完整 `pytest -q` 234项通过；正式库专项61项通过；新增的三项原文专项测试通过。JUnit日志在 `outputs/classical-source/regression-first.xml` 和 `outputs/classical-source/formal-all.xml`。Chrome 155和Edge 155真实浏览器在1440、390、320像素视口完成72项流程检查，涵盖人物检索、CBDB与独立来源区分、两人原文、旧王珪原文、辅助来源不误标、固定版本链接、复制、证据跳转、手机无横向溢出及控制台错误；正式库在浏览器过程前后哈希相同。浏览器报告与截图在 `work/classical-source-browser-accepted/`。SQLite `integrity_check=ok`，`foreign_key_check=[]`。真实手机设备未测；390与320像素为桌面浏览器手机模拟视口。

仍有多数结构化来源记录没有已核对的古籍摘录。界面保留其来源和定位，后续只有在获取到可核对的原文或合法书影及准确人物关联后才增补；当前不以缺失原文否定结构化来源本身。
