# 第六阶段自动数据生产流水线

本阶段新增独立自动准入入口，可信结构化记录无需逐条人工审核或批准。原四表、原10人和17条关系及其批准历史保留；旧手工审核入口作为兼容流程，不是CBDB自动准入的必经步骤。

最终实际数量与测试见 [第六阶段验收报告](stage6_verification.md)。运行配置为 `data/collection_configs/stage6_auto.json`，完整审计在 `outputs/stage6-release/`。

## 数据来源和许可

官方[CBDB SQLite仓库](https://github.com/cbdb-project/cbdb_sqlite)指向Hugging Face发布包；已保存 `latest.json`。本次读取的最新快照日期是20261010，但没有将其用于准入。[官方许可](https://cbdb.hsites.harvard.edu/cbdb-data-licensing-terms)按当前单机版发布日期及数据总量区分使用范围；[下载页](https://cbdb.hsites.harvard.edu/download-cbdb-standalone-database)公布20260602版、649,533条人物记录。

本项目选择官方历史 **20250520** 包，649,533条主表记录中包括ID=0“未詳”占位；正ID为649,532条。课程按非商业用途署名，并对CBDB衍生记录采用CC BY-NC-SA 4.0。另行检验的20260530快照含658,670条主表记录，因超过边界未采用；不能从中任意截取若干ID来声称已经满足许可。其下载缓存仅留作本地评估，不进入网站或交付数据快照。

CBDB文件定位：

- `data/external/cbdb-20250520/release.json`：官方发布路径、固定Hugging Face版本、归档SHA-256、SQLite本地SHA-256、许可边界及获取时间。
- `cbdb.7z`：下载后与官方Hugging Face LFS SHA-256比对；SQLite的字节由该归档验证覆盖。SQLite另算本地摘要，不冒称有第二份上游SQLite校验值。
- `license_review.json`：通过官方网页读取工具取得的来源级许可审阅摘录；不是原始HTML。Python请求许可页曾得到403，没有绕过，原情况记录在文件中。

补证使用[Kanripo](https://github.com/kanripo)组织声明CC BY-SA 4.0范围内的[《宋史》仓库](https://github.com/kanripo/KR2a0032)，固定Git提交及实际文件列表保存于 `data/external/kanripo-stage6/texts.json`。每个文件同时核对上游Git blob与本地SHA-256，正文头实际包含WYG底本标识。CTP接口本次网页读取返回403，未探测受限正文或绕过认证，故未纳入数据源。

不同来源保留各自许可：CBDB派生结构化记录CC BY-NC-SA 4.0，Kanripo转录文本CC BY-SA 4.0；旧来源沿原许可及署名。不把混合库重新统一宣称为另一种许可，也不将来源的限制改写成软件许可。

## 真实字段映射

以已下载数据库的 `PRAGMA table_info` 和代码表为准，架构快照在本阶段 `staging/source_schema.json` 及最终流水线审计目录。

|CBDB字段或表|Guji ID用途|
|---|---|
|BIOG_MAIN.c_personid|外部身份主键；保存为provider=CBDB、external_id，人物ID为P-CBDB-原ID|
|BIOG_MAIN.c_name_chn|原始规范姓名；保留字形，不按姓名合并|
|BIOG_MAIN.c_dy → DYNASTIES.c_dynasty_chn|结构化来源朝代字段；界面明确核准方法|
|ALTNAME_DATA.c_alt_name_chn|原始称呼|
|c_alt_name_type_code → ALTNAME_CODES|按真实代码与中文说明同时校验后映射|
|ALTNAME_DATA主键三元组|源行定位：c_personid、c_alt_name_chn、c_alt_name_type_code|
|c_source → TEXT_CODES.c_textid|保留原书目关联；悬空非零引用隔离，空值不伪造书名|
|c_pages、BIOG_SOURCE_DATA|保留来源字段；不当作已取得的古籍原句|

类型映射：3姓名/曾用名→name，4字→courtesy，5室名/别号类→art，6谥号→posthumous；行第、封爵、小名、小字、赐号、庙号、尊号、法号等保留为other并保存原类别。未知、只有姓氏、无法明确映射的类别隔离。代码5保留其“室名、別號”完整来源标签，不据此声称所有记录都是古人本人自号。

`c_index_year`是索引年，不冒充出生年；`c_index_addr_id`是索引地址，不冒充籍贯；带范围或推导信息的年份不直接写入本批生卒字段。原始字段仍在记录快照中可查。

## 自动准入规则

|级别|条件|正式展示|
|---|---|---|
|A：结构化来源核准|官方许可内的固定版本；哈希正确；源人物ID有效；真实原始行重放一致；类别明确；身份无未解决冲突；约束检查通过|原始字段JSON、来源ID、版本和主键；明确说明不是古籍原文|
|A+：原文证据核准|先有A关系；正文来自许可确认且版本固定的文本；明确完整姓名+字句；该姓名/字组合在CBDB唯一；朝代与本次宋史语料范围相符；原句、字符区间、行号和连续上下文可重放|古籍原文、上下文、卷次、固定Git链接及行号/字符定位；与A记录分开列出|
|B：自动隔离|占位或不明人物、可疑字形、未知/冲突类别、悬空引用、可能与无外部ID的旧人物重复、源行不符、AI失败或证据不足|只留隔离暂存，不作为正式人物/称呼公开|

最终身份规则还检查**全源库**中的同朝代、同完整姓名、同字的不同源ID。索引年不同不足以证明它们是不同人；未有充分区分依据时整体人物候选隔离。单纯同名、但字等身份线索不同的源ID可分别保留；不会按姓名合并。原有唐宋王珪的两个ID不变。

A+只支持保守的明确声明，不解析复杂代词归属、不拼接段落、不删页码标记以制造匹配、不用模型记忆补引文。没有在10卷样本中找到句子不代表关系错误，原文补证候选留B，已有独立A来源资格不因此变成“原文通过”。

## 数据模型和迁移

新增迁移 `app/migrations/006_automatic_admission.sql`，由自动入口执行；旧四表不改列、不覆盖历史行。新增8表：

- `data_releases`：来源、版本、归档与数据库摘要、许可和原始发布元数据。
- `external_people`：外部人物ID→内部Person ID映射及源人物记录摘要。
- `automatic_evidence`：A/A+、证据类型、来源表、主键、记录、定位、哈希与导入批次。
- `auto_batches`、`auto_events`：提交/回滚状态、数量、时间、备份及历史事件。
- `batch_entities`：每批新增四表记录的所有权及摘要，用于安全回滚。
- `search_variants`：原形与OpenCC t2s检索键，始终指向独立Person ID和关系。
- `pipeline_summary`：暂存、隔离数量及原因汇总，不保存隔离人物实体。

隔离原始数据在独立 `data/ingestion/stage6.sqlite3` 的candidates及source_identity_conflicts，不进入正式四表；摘要可用于网站数据概况。旧四表数据库不必先迁移才能启动应用，读取层会兼容没有辅助表的实例。

`verification_status=verified`是通用准入状态，具体人工/A/A+方法由审计与证据元数据区分，不能将它统一解释为人工核实。原文字段在A行中存储的是明确标记的结构化字段JSON；界面使用pre显示，不以古籍引文样式呈现。

## 一条命令运行

现有环境已有依赖。组员新建环境可安装：

```powershell
work/.venv/Scripts/python.exe -m pip install -r requirements-dev.txt -r requirements-acquisition.txt
```

OpenCC是检索键依赖；py7zr只用于官方7z归档。完整实测版本另有第六阶段锁文件。源码及文档继续位于固定项目根目录。

```powershell
# 全流程预演：获取/缓存、暂存、A/A+仅写一次性副本
work/.venv/Scripts/python.exe -m scripts.produce_data --config data/collection_configs/stage6_auto.json --output work/stage6-full-dry-new --dry-run

# 全流程自动运行：正式准入后自动执行回归和两组正式库专项
work/.venv/Scripts/python.exe -m scripts.produce_data --config data/collection_configs/stage6_auto.json --output outputs/stage6-next-run
```

输出目录须尚不存在。当前已达到配置的400人目标，相同输入重跑应零新增；它不会自行扩到500或下一阶段。配置可控制候选上限、目标、每批人数、每人别名上限和补证卷数；本次每批50人、每人最多2条非规范姓名称呼，保留1条规范姓名关系。

低层步骤也可独立运行：

```powershell
work/.venv/Scripts/python.exe -m scripts.acquire_cbdb --output data/external/cbdb-20250520 --version 20250520
work/.venv/Scripts/python.exe -m scripts.expand_data stage --limit 1200 --output work/stage6-stage-new
work/.venv/Scripts/python.exe -m scripts.expand_data dry-run --target 400 --batch-size 50 --output work/stage6-dry-new
work/.venv/Scripts/python.exe -m scripts.expand_data run --target 400 --batch-size 50 --output outputs/stage6-run-new
```

获取阶段使用已保存的来源级许可策略与固定版本，不逐条问用户批准。许可范围不明、实际架构不符、文件摘要不符或官方包不可用时停止，不改用受限最新数据凑数。若要升级来源版本，应保存新发布/许可记录和新的暂存路径，再按同一外部ID增量校验；不覆盖旧记录。

## 断点、幂等、失败与回滚

下载的临时文件只有在校验通过后才改为正式缓存文件；支持服务器确实返回206时的断点继续。已验证包、正文和Git固定版本复用，不反复联网抓取。暂存有源行唯一ID、发布哈希和高水位游标；增大limit只继续读取后续源行，同输入不会重复暂存。

正式导入采用稳定外部ID、关系ID、源行证据ID与事务，每批写入前使用SQLite backup。完整性、外键、直接支持链与证据类型在提交前验证；失败整批回滚并保存故障JSON。正式库提交后即使暂存检查点未完成，重跑也依据正式证据ID识别已提交记录并修复检查点，不重复新增。

每批记录新增实体的摘要。回滚只删该批拥有且未被改动的记录；有后续批次依赖时外键阻止整批回滚，须按逆序处理，不会删除旧10人：

```powershell
work/.venv/Scripts/python.exe -m scripts.expand_data rollback --batch-id '填写实际已提交批次ID' --output outputs/stage6-rollback-new
```

执行回滚前会再做备份；历史事件保留。当前事务回滚、断点续跑、重复零新增、跨批依赖阻止均有隔离测试；本轮规则修订还实际逆序撤销了早期自动批次，确认旧四表原始行逐个未变，再重放最终规则。不要将最早全库备份直接覆盖到仍有其他已导入批次的运行中数据库。

## DeepSeek集成与调用边界

保留既有 `Invoke-ClaudeAssist.ps1`，没有另建API服务、换提供商或访问认证值。默认生产流程不调用模型：客观的未知类型、缺原文、潜在重复ID不能靠语义意见消除。遇到需分析的复杂材料时可显式执行一次建议核验，使用固定主任务ID，仍遵守现有手动调用、总额度及失败停止规则：

```powershell
# 仅在用户明确要求本次一次外部调用后执行；本阶段实际未运行此命令
work/.venv/Scripts/python.exe -m scripts.review_quarantine --explicit-one-call --limit 3 --output outputs/stage6-manual-advice-new
```

输出必须匹配候选ID及现有桥接结构；失败、超时、空值或错误形状保留隔离，且不自动重试。建议记录与准入分开，不因模型说“通过”就制造来源或升级B。原始上下文、脱敏响应、实际可得usage保留；没有费率/账单时不估造费用。本阶段真实调用为0，测试中的包装器失败响应是明确模拟夹具，不计真实调用。

## 运行与网站展示

```powershell
work/.venv/Scripts/python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

首页显示正式人物/关系/来源/证据计数、自动核准与隔离摘要、最近批次结果。人物和关系页面明确标记结构化来源核准；A证据是原始字段，A+才另列实际古籍原句、上下文和定位。旧人工证据仍沿原标记展示。

可先查“王珪”“文忠”，再查“安惇”或其字“处厚”验证简化检索，进入结构化字段；查“柴成務”或“寳臣”查看同一关系中的A记录与A+原文。实际数据与截图以验收报告为准。未经证据明确区分的重名签名不作为展示案例。
