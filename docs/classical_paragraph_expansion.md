> 历史阶段说明：本文记录第七阶段之前的原文摘录规模（61条/54人）。第七阶段已扩充为89篇完整篇章、682条定位证据；当前指标以 [Stage 7 验收报告](stage7_verification.md) 和 `data/reports/stage7_acceptance.json` 为准。

# 古籍连续段落与相关句标注（2026-10-18）

## 用户可见的变化

在人物的称呼证据页及古籍来源页，直接展示已核对原文所在的连续文字；与该人物称呼关系直接对应的原文句子或无标点转录行以红色标注。复制按钮复制整段文字。古籍卡片仍提供书名、卷次、固定来源链接和具体称呼关系；Kanripo《宋史》链接定位到固定 Git 提交中的行号。排版符号 `¶`、`<pb:…>` 只在阅读层合并，并注明这一点；数据库中的原始转录和定位未改。原始 JSON 仍收在独立技术面板。

CBDB 来源本身继续标为结构化数据库。如果指定人物另有古籍证据，该段原文列在“其他独立来源中的古籍原文”下；没有核对到原文的人物只展示其已有的结构化资料。上下文作为阅读材料，不自动认定其中提到的籍贯、年代、经历等为已选用人物字段。两位王珪的段落按人物ID与各自唐、宋来源隔离；苏轼和欧阳修共享“文忠”时，分别标注本人的《宋史》传记中含“諡”的句子。

## 数据范围与来源

整改前有22条公开古籍证据，涉及15个独立人物ID。正式库目前有**61条公开古籍证据、54个独立人物ID**，正式总数为**454人、1195条称呼关系、30条来源、1764条证据**；A/B/C关系分别为61/1133/1，人物及称呼总量未改变。仍有400位人物没有可公开的A级古籍原文关系，其可信CBDB结构化记录仍按B级显示。这次未以短摘录臆造更多古籍文字。

既有20条古籍证据使用已保存的原文上下文；李白、杜甫从[《舊唐書》卷190下固定修订](https://zh.wikisource.org/w/index.php?oldid=2636533)的原始响应与正文哈希重放。新增四条维基文库古籍证据，人物与卷次如下：

| 人物 | 关系 | 固定版本 |
| --- | --- | --- |
| 王維 | 字摩詰 | [《新唐書》卷202](https://zh.wikisource.org/w/index.php?oldid=2612253) |
| 曾鞏 | 字子固 | [《宋史》卷319](https://zh.wikisource.org/w/index.php?oldid=1744467) |
| 韓琦 | 字稚圭 | [《宋史》卷312](https://zh.wikisource.org/w/index.php?oldid=1480739) |
| 陳升之 | 字暘叔 | [《宋史》卷312](https://zh.wikisource.org/w/index.php?oldid=1480739) |

四条均从先前通过维基文库 Action API 保存的真实响应中读取，核对原始响应哈希、解析正文、卷题、修订号、唯一姓名/字直接句、字符位置和CBDB稳定人物ID。清单在 `work/paragraph-expansion/formal/validated_manifest.json`。没有重新联网获取或编造书影。

另从[Kanripo《宋史》仓库](https://github.com/kanripo/KR2a0032)固定提交 `144d5640186b8e519673e5603a18667e55b1f449` 的十卷本地缓存，新增35位宋代人物的直接姓名/字句。逐文件校验SHA-256与Git blob哈希；仅接纳传记行首唯一的“姓名字某”、CBDB同朝同名同字唯一人物、已存在的正式人物和字关系。三条原有Kanripo证据保持不变；另外412条关系没有满足“固定十卷中的唯一传记行首直接句”，仍不因此提升为A级。清单在 `work/paragraph-expansion/kanripo-formal/validated_manifest.json`。按原有来源说明标注Kanripo转录文本的CC BY-SA 4.0；维基文库转录文本的许可依据见[版权信息](https://zh.wikisource.org/wiki/Wikisource:%E7%89%88%E6%9D%83%E4%BF%A1%E6%81%AF)。

## 实现与校验

`app/classical_passage.py`只使用已入库、已核对的原文上下文或哈希验证后的《舊唐書》缓存，截取连续文字，逐字定位高亮片段。`app/source_catalog.py`、`app/catalog.py`把该结果附加到来源和称呼证据；`app/templates/source.html`、`app/templates/alias.html`与`app/static/styles.css`呈现可读引文、红色 `<mark>` 和卷次来源。模板分别转义引文的前段、标注段、后段，不把来源文字作为HTML执行。`scripts/import_cached_courtesy_text.py`与`scripts/import_kanripo_paragraphs.py`负责确定性核验、备份、事务入库、重复导入不新增；没有覆盖原有证据或基础字段。

四人批次 `ST65-TEXT-MORE-f075a99f5e7b5db6`：19→23来源、1725→1729证据；入库前备份在 `work/paragraph-expansion/formal/backups/20261018T061246215052Z-a25abf139fa84e31b5820b4984e3fba4.db`。35人批次 `ST65-KR-PARA-5861df9f32222083`：23→30来源、1729→1764证据；入库前备份在 `work/paragraph-expansion/kanripo-formal/backups/20261018T101656303056Z-3761babfa2794814aefbd95c6a132bd7.db`。两个批次均先在正式库副本完整试运行；重复导入均返回零新增。分别在副本执行批量回滚，逐行恢复到各自入库前的人物、关系、来源、证据及质量等级；报告在 `work/paragraph-expansion/rollback-verification/result.json` 和 `work/paragraph-expansion/kanripo-rollback-verification/result.json`。正式库未回滚，SQLite完整性与外键检查通过。

真实浏览器使用 Chrome 155 与 Edge 155，在1440px桌面、390px和320px模拟手机视口执行。李白/杜甫旧路径72项、四名新增维基文库人物132项、十卷Kanripo抽样200项通过；涵盖检索→人物→CBDB独立来源→古籍段落→对应称呼证据、复制整段、原文行链接、同名隔离、谥号句子、手机无横向溢出与控制台无错误。报告与截图在 `work/paragraph-expansion/browser-li-du/`、`browser-more-1/`、`browser-kanripo-final/`。最后一次Kanripo浏览器复查在服务重启、固定提交号显示优化之后执行，仍是200项通过。这是实际桌面浏览器的手机模拟视口，未声称实机测试。最终完整pytest **239项通过**，正式库专项**61项通过**；仅有既存的Starlette/httpx弃用警告。

本轮按用户要求尝试通过 Claude Code CLI 的既有桥接委派一组8条语义边界审查；桥接返回结构化 `TASK_BUSY`、`ok=false`、未取得审查结果。遵照 claude 技能的失败停止规则，没有自动重试、更换任务ID或把主模型判断冒充外部模型核验。入库仅依赖上述程序化直接原文与来源校验。
