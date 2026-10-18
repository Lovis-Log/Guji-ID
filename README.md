# GuJi ID · 古籍人物别称规范档与同名消歧器

《移动信息化》课程项目。当前维护 **Stage 6.5：人物检索、逐字段资料补全和分级展示修复**。正式库已有 **454位人物、1195条称呼关系、30条来源、1764条证据**，原400人与921条关系全部保留。证据包含结构化记录，不能统称为古籍原文。新人物及字段主要取自许可范围内的 CBDB 20250520；陶渊明通行姓名另有固定修订的C级作者资料来源。

软件地址：[http://127.0.0.1:8065/](http://127.0.0.1:8065/)。当前环境启动：`work\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8065`。

2026-10-18 已完成全站可读展示整改：默认显示人物与证据中文摘要，“学术出处”提供书目与定位，完整JSON只通过“查看原始记录”按需进入独立技术面板。新增来源档案页，支持人物筛选及分页；底层正式数据完全未变。组件、接口、测试和真实浏览器结果见 [展示整改报告](docs/readable_ui.md)。

随后完成古籍原文查阅增强：来源页直接展示已核对的原文摘录、卷次、固定版本链接与所支持的称呼关系；CBDB 等结构化来源仅在指定人物时另列独立原典摘录，明确区分来源性质。新增《舊唐書》卷190下对李白“太白”、杜甫“子美”的两条原文证据，均不据此推断其他字段。获取、许可、备份、回滚与浏览器验收见 [古籍原文查阅报告](docs/classical_source_reading.md)。上段的“底层正式数据完全未变”仅指此前展示层整改时的状态。

最新的[古籍段落扩展报告](docs/classical_paragraph_expansion.md)记录了连续原文和相关句红色标注：当前61条公开古籍证据涉及54个独立人物ID；从已有固定版本缓存为王維、曾鞏、韓琦、陳升之补入4条证据，并为另35位宋代人物补入《宋史》直接姓名/字句。古籍上下文不自动扩展为其他基础字段事实。

本阶段实际数据、DeepSeek成功及失败记录、浏览器验收和回滚说明见 [Stage 6.5报告](docs/stage65_verification.md)，根因见 [简短诊断](docs/stage65_diagnosis.md)。下方第六阶段数字和原流程保留为历史背景，当前指标以上述报告为准。本阶段止于 Stage 6.5，冲突及待核资料继续隔离。

自动处理 **1,999条真实源候选**，最终规则隔离60条；旧38条候选关系仍保留。配置、许可、映射及回滚见 [自动流水线说明](docs/stage6_pipeline.md)，实际数据和测试见 [第六阶段验收报告](docs/stage6_verification.md)。CBDB合格记录不再要求人工逐条批准，本阶段没有新付费模型调用。

第四阶段曾扩充到20位候选人物、55条称呼、52条可定位证据，DeepSeek辅助核验32条关系，一致性检查后29条AI通过、3条存疑。当时人工批准/正式入库为0；原报告不回写，见 [第四阶段报告](docs/stage4_verification.md)。

阶段4.5推荐的10人17条已在第五阶段明确批准入库，历史材料在 [第五阶段交付目录](outputs/stage5-release/)，本阶段逐行验证其不变。原手工入口仍保护明确批准范围；新自动入口 `scripts.produce_data` 对官方许可内数据实行A/A+/B准入，不要求逐条人工批准。通用四表入口仍不能绕过正式准入。

第三阶段的历史运行曾因 CLI_ERROR 受阻，未取得有效语义判断；失败包和停止状态保留，没有回写成功。本轮第四阶段的新扩容任务已通过真实成功调用验证既有集成当前可用。历史记录见 [第三阶段说明](docs/stage3_verification.md)与 [原失败批次](data/verifications/2026-10-16-batch1/)。

原采集说明见 [docs/collection.md](docs/collection.md)。最早的 [5人采集批次](data/collections/2026-10-16-delivery/)仍保留原待审核状态，不因后续准入改写快照。当前状态以第五阶段批准及第六阶段自动批次审计为准。

本项目固定保存在 `D:\Projects\移动信息化小组作业\Guji ID`，后续开发与交付继续使用此目录。PowerShell 可先执行：

```powershell
Set-Location -LiteralPath 'D:\Projects\移动信息化小组作业\Guji ID'
```

FastAPI + Jinja2 + HTML/CSS/JavaScript + Python内置sqlite3，单体应用。页面只用正式准入数据，虚构夹具只在测试库。可演示“文忠”多人共享、唐宋王珪完整同名，以及结构化来源与古籍原文证据的区别。旧10人的可选字段仍空，新CBDB朝代字段按结构化来源标识；索引年/索引地址不冒充生年/籍贯。

## 安装与启动

推荐 Python 3.12。本次实际验证环境为 Windows、Python 3.12.10。以下命令都在包含本文件的 `guji-id` 项目根目录执行。

Windows PowerShell（直接调用虚拟环境中的 Python，无需激活脚本或修改执行策略）：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m scripts.init_db
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

仅运行应用可装 `requirements.txt`；测试需 `requirements-dev.txt`；下载历史7z还需 `requirements-acquisition.txt`。完整实测环境见 `requirements-stage6-lock.txt`，旧锁文件保留为历史参考。

macOS / Linux 可使用相同流程，将 `.\.venv\Scripts\python.exe` 替换为 `.venv/bin/python`，创建环境使用 `python3 -m venv .venv`。本次未在这两个平台实测。

启动时自动幂等初始化四张核心表；默认数据库为项目 `data/guji.db`。第六阶段新增表由自动流水线执行独立迁移，不必为启动已有软件重复运行入库。

- 首页：[http://127.0.0.1:8000/](http://127.0.0.1:8000/)
- 健康检查：[http://127.0.0.1:8000/health](http://127.0.0.1:8000/health)
- 接口文档：[http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)（Swagger UI 资源默认来自外部 CDN，离线时可能无法显示；首页及核心接口无需外部资源）

`/health` 正常响应：`{"status":"ok","database":"ok","stage":6}`。它检查四张核心表能否读取及外键是否开启。数据库错误返回503，意外错误500，路由错误404/405，参数错误422；浏览器有友好错误页，机器请求保留JSON格式。内部路径与异常不暴露给页面。

需要指定数据库时，初始化和导入使用 `--db`；服务启动使用同一个路径的环境变量：

```powershell
$env:GUJI_DB_PATH = 'C:\your-project\data\guji.db'
.\.venv\Scripts\python.exe -m scripts.init_db
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

`--db` 的优先级高于环境变量；环境变量高于默认路径。相对的自定义路径相对于执行命令时的目录。配置应使用 SQLite 文件路径，不使用 `:memory:`。

## 查阅人物与证据

| 界面 | 路径 | 行为 |
| --- | --- | --- |
| 搜索首页 / 结果 | `/`、`/search?q=称呼` | 姓名或别称精确查询；结果保留所有不同 Person ID；手机提交后定位到结果区 |
| 人物规范档案 | `/people/{person_id}` | 展示已核对身份、可用生卒和籍贯、已有直接支持证据的已核对别称 |
| 称呼与古籍证据 | `/aliases/{alias_id}` | 每条文献完整展示古籍、卷篇、原文、平台、原始链接、关系、性质、状态及可用上下文 |

查询使用SQLite绑定参数。原始名称继续NFC及首尾空白清理；Stage 6.5为姓名与称呼建立OpenCC t2s检索键，支持受控简繁/异体检索；原名和身份ID不改、不据此合并。精确匹配优先，部分名称匹配次之；模糊结果最多50个，精确同名结果全部保留。同一称呼可对应多位人物，结果不跳转到第一人。

公开规则统一放在 `app/catalog.py`：

- 规范姓名：仅查询 `people.verification_status=verified` 的人物。
- 别称：人物与别称均须 verified；A类沿用有定位的直接原文支持，B/C类可依显式的可信结构化或补充来源准入，不另要求古籍摘录。D类及待核关系不公开。
- 档案页的别称列表、别称详情、搜索使用相同规则；直接访问隐藏记录返回相同的友好 404 页面。
- 已公开别称还可展示其其他已核对且定位完整的证据，包括间接、反对、仅提及材料，逐条明确标注性质；pending/disputed/rejected 不展示。
- 首页数据概况仍保留四表全状态数量，并明确标注其范围；“当前可公开检索”数量另按上述规则计算，不把待审核规模当成已确认规模。

Stage 6.5新增逐字段来源：姓名、朝代、生卒、籍贯可分别具有A/B/C等级；D类冲突隔离，不因一个字段未知而隐藏整个人物。详情展示已知部分，缺项集中折叠，来源可以展开；与已保留可靠值冲突的观察记录不会在公开来源列表作为事实展示。身份 verified 不会自动确认任何别称，不补造文献。

`direct` 只说明材料直接支持别称归属，不能推断为“人物本人直接自称”。原作与后世文献的区别在已核对证据的 `notes` 中保存：支持普通备注文本，也兼容此前采集导出的 JSON，其中 `relation_type` 表示称呼叙述方式，`evidence_type` 表示材料类型，`context` 表示实际上下文，`remarks` 表示核对说明。实际据本人原作确认后才可写“人物本人直接自称”；史传中的“自号”应写“后世文献记载其自称”。未填写时显示暂无已核实信息，不按关键词自动判断。JSON 内的本地原始文件路径、哈希和审核内部信息不在公众页输出。

### 当前需要补充的正式 CSV 字段

正式库现有400人、921条称呼关系。古籍身份摘录只取可公开的原文证据；结构化字段在不同区域显示，原文不足不补造。旧候选不因新来源入库而升级，以下四表约束与原字段保持兼容。

| CSV | 必需内容 | 暂不明确时 |
| --- | --- | --- |
| people.csv | person_id、canonical_name、verification_status | 朝代、生卒、籍贯、身份说明可留空；人物未核对用 pending，公众不显示 |
| aliases.csv | alias_id、person_id、alias_name、alias_type、verification_status | normalized_name 留空自动计算；同名异人分别保留 person_id；别称未核对不公开 |
| sources.csv | source_id、book_title、platform、original_url | volume/chapter 至少一项实际定位，edition 可空；不猜测网址 |
| evidences.csv | evidence_id、alias_id、source_id、original_text、usage_relation、evidence_nature、verification_status | notes 可补充上述叙述方式；只有人工确认的 supports/direct/verified 才能支持公开别称 |

原有四表入口及 `scripts.export_reviewed` / `scripts.import_reviewed` 保留供隔离验证；默认正式路径及 `GUJI_DB_PATH` 配置路径的写入统一要求封存批次批准入口。字段详解见 [数据字典](docs/data_dictionary.md)。

## 导入真实数据

原手工批次 `VB-c32955de8b1a0cd7` 的17条关系继续保留，批准材料在第五阶段交付目录。新增CBDB关系由自动批次A核准，独立古籍原句为A+，B保存在隔离库；不会借此升级旧38条候选的ID。

自动入口为 `scripts.produce_data`，详见第六阶段运行说明。启动已有软件不必重新导入；相同输入重跑零新增。原手工批准入口 `scripts.import_verified_batch` 仍可用于其原适用材料，下列原有四表流程用于隔离验证。

1. 复制 `data/templates/` 中四份只有表头的 CSV 到自己的批次目录，例如 `data/import_batch/`。不要把测试夹具作为正式数据。
2. 根据真实史料填写，稳定保存各表 ID。人物 ID 不从姓名生成，不因同名、同号而复用。未知信息留空。尚未核对的数据显式使用 `pending`。
3. 确保四个文件都在同一目录；没有记录的表保留表头即可。使用 UTF-8（支持 Excel 的 UTF-8 BOM）保存。
4. 在隔离库执行整批验证：

```powershell
.\.venv\Scripts\python.exe -m scripts.import_data --input-dir data/import_batch --db work/manual_validation_only.db
```

想先验证流程，可对交付的空模板运行以下命令；它不会增加数据：

```powershell
.\.venv\Scripts\python.exe -m scripts.import_data --input-dir data/templates --db work/empty_template_validation.db
```

导入顺序固定为 `people → aliases → sources → evidences`。字段名必须与模板完全一致，无重复或额外字段，列的顺序不限。单元格为空或全空白映射为数据库 NULL；必填字段、核对状态不能为空。包含逗号、双引号或换行的值按标准 CSV 规则用双引号包围，内部双引号写为两个双引号。

导入器预读四份 CSV，随后用 `BEGIN IMMEDIATE` 在单个事务中写入并检查证据状态关系：

- 同批任一文件内重复 ID：拒绝。
- 数据库中已有相同 ID 且所有字段相同：跳过并计入 `skipped`。
- 已有相同 ID 但内容不同：拒绝，明确提示文件和行号；不静默覆盖、不实现更新。
- 缺少必填字段、非法枚举/年份/URL、外键不存在、重复的同人物同类别同原始别称：拒绝。
- 任一错误均取消整批新增，不留下部分导入的记录；首次导入时建好的空表和数据库文件可以保留。
- 成功输出每张表的 `inserted` / `skipped`；失败输出原因并返回退出码 1。CSV 错误会定位文件和行号；跨表证据链错误定位对应 ID。

复用旧 Person ID 或别称 ID 的新批次可只填写新增记录，但仍须提供四份文件。重复导入依靠稳定 ID；为同一条证据重新生成新 ID 会被视为新记录。

**核对边界：**URL 检查仅验证格式，不联网确认页面存在或内容真实；`verified` 只能由人工核对后在数据中标记，程序不会自动提升状态。已核对别称必须有 `verified + supports + direct` 的证据，已核对证据的来源必须有卷次或篇目至少一项。间接材料、仅提及、反证或待核对证据不能单独确认别称归属。原文是否准确、定位是否正确、史料是否足以证明关系，均需人工判断。

来源不足的资料应保留 `pending` / `disputed`，不能为了通过校验补造卷次或原文。证据表要求有原文，只有传闻而没有原文时可先保存待核对别称，不建立证据记录。本阶段没有审核界面和更新命令；修改已有数据应先做备份，经人工复核后另行处理，不要通过分配新人物 ID 来绕过冲突。

## 数据结构与字段

完整 DDL 在 `app/schema.sql`，详细数据字典在 `docs/data_dictionary.md`。

```text
people (1) ── (N) aliases (1) ── (N) evidences (N) ── (1) sources
```

| 表 | 主键 | 关键约束 |
| --- | --- | --- |
| people | person_id（TEXT） | 规范姓名必填；同名人物 ID 独立；不确定的人物信息可为空 |
| aliases | alias_id（TEXT） | person_id 外键；同一人物、原始称呼、类别组合唯一；全局名称不唯一 |
| sources | source_id（TEXT） | 古籍名称、来源平台、原始 URL 必填；版本可为空 |
| evidences | evidence_id（TEXT） | alias_id、source_id 外键；原文、使用关系、性质、状态必填 |

所有外键开启 `ON DELETE/UPDATE RESTRICT`，避免误删断链；人物姓名、别称原名、规范化名、状态、关系外键有索引。DB 层执行必填、枚举、年份、唯一性和外键约束；规范化、URL 与跨表证据状态检查由导入器执行。直接手工改 SQLite 可绕过这些导入检查，应统一通过导入器新增记录。

核对状态：`pending` 待核对、`verified` 已核对、`disputed` 存疑、`rejected` 驳回。人物、别称、证据各自保存状态，不互相自动继承。

名称规范化仅为 `unicodedata.normalize('NFC', name.strip())`。原始 `alias_name` 保留输入，`normalized_name` 留空时自动计算；非空时必须与计算值相符。不简繁转换、不改异体字、不改全半角、不去内部空白与标点，不进行相似度匹配；规范化名不唯一，不能作为身份归并依据。

## 测试

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -X pycache_prefix=work/pycache-check -m compileall -q app scripts tests
.\.venv\Scripts\python.exe -m pip check
```

自动测试在 pytest 临时目录 `.pytest_tmp/` 创建数据库和 CSV，该目录会在下一次测试时清理，不存放正式资料。所有 ID 都用 `TEST-` 前缀，来源明确写为 `TEST_ONLY_SYNTHETIC_SOURCE_NOT_A_BOOK`，网址使用 `example.invalid`，不是历史资料。测试不使用默认正式数据库，测试数量不计入首页统计；缓存和临时数据不进入交付压缩包。

覆盖：四张表与重复建库、外键/删除约束、四种状态、同名多人物、一人多别称、一别称多证据、空值与公元前年份、CSV BOM/多行原文/表头错误/必填字段/枚举/重复 ID、幂等/冲突/整批回滚、证据状态关系、CLI 退出码、首页/静态文件/模板下载/健康检查和异常响应，以及阶段二检索、规范档案、证据展示、公开状态规则、XSS 转义、查询参数绑定、异常来源隐藏和完整页面路径。

可选真实浏览器验收（不增加应用运行依赖）：

```powershell
.\.venv\Scripts\python.exe -m pip install playwright==1.64.0
.\.venv\Scripts\python.exe -m scripts.verify_stage6 --output work/stage6-browser-new
```

当前验收脚本使用本机Chrome/Edge读取实际扩容正式库，不注入模拟数据或自动下载浏览器；截图、版本、明细与前后哈希写入新目录。手机为触屏/窄视口模拟，不声称实体手机或Safari实测。旧阶段二/五脚本保留其历史数据规模假设，当前使用第六阶段脚本。

正式数据专项测试（只读，不自动入库）：

```powershell
.\.venv\Scripts\python.exe -m pytest tests/formal_acceptance.py -q --basetemp=work/pytest-formal-new
.\.venv\Scripts\python.exe -m pytest tests/formal_stage6.py -q --basetemp=work/pytest-stage6-formal-new
```

本次结果、来源许可、已知限制和回滚步骤见 `docs/stage6_verification.md` 与 `docs/stage6_pipeline.md`；早期验收文档保持历史状态。

## 项目目录

```text
guji-id/
├── app/
│   ├── __init__.py
│   ├── main.py                 # FastAPI 工厂、路由、异常处理、启动建库
│   ├── db.py                   # sqlite3 连接与初始化
│   ├── schema.sql              # 四张表及索引
│   ├── importer.py             # CSV 校验与事务导入
│   ├── normalization.py        # 保守名称规范化
│   ├── catalog.py              # 公开可见规则、精确检索、人物与证据查询
│   ├── collector.py            # 既有独立采集模块，本阶段不新增采集能力
│   ├── collection_review.py    # 既有人工审核导出与导入
│   ├── templates/             # 首页、人物档案、称呼证据、不可用页面、共用骨架
│   └── static/                # styles.css、app.js
├── data/
│   ├── .gitkeep
│   └── templates/             # 四份空白 CSV，不含示例记录
├── scripts/
│   ├── __init__.py
│   ├── init_db.py
│   ├── import_data.py
│   └── verify_stage2_browser.py # 可选真实浏览器验收；仅独立测试库含虚构数据
├── tests/                     # 临时数据库中的虚构测试夹具与测试
├── docs/
│   ├── data_dictionary.md
│   ├── prompt_log.md
│   ├── verification.md
│   └── screenshots/           # 实际浏览器的桌面与手机宽度截图
├── .gitignore
├── AGENTS.md                  # 固定本项目后续开发和交付位置
├── pytest.ini
├── requirements.txt
├── requirements-dev.txt
├── requirements-lock.txt
└── README.md
```

正式库 `data/guji.db` 已加入.gitignore，交接须带正式数据快照及来源许可/审计，不能只克隆源码便声称有正式数据。完整实测环境见 `requirements-stage6-lock.txt`；旧锁文件保留为历史参考。当前400人的有界自动扩容已验收，没有进入第七阶段。RapidFuzz、组合筛选、自动历史身份裁决、知识图谱、RAG、OCR、聊天和账号系统仍未实现。

实现时参考了 [FastAPI 模板文档](https://fastapi.tiangolo.com/advanced/templates/)、[FastAPI 测试文档](https://fastapi.tiangolo.com/tutorial/testing/) 与 [Python sqlite3 文档](https://docs.python.org/3/library/sqlite3.html)。

