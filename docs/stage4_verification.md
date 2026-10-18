# 第四阶段：批量史料扩充与独立核验验收

执行日期：2026-10-16 至 2026-10-17（Asia/Shanghai）。项目继续位于 `D:\Projects\移动信息化小组作业\Guji ID`，沿用 FastAPI、Jinja2、SQLite 四表、既有查询和导入门槛。没有新建项目、迁移数据库、自动批准或进入第五阶段。

## 最终实际规模

|状态|数量与含义|
|---|---|
|原有候选人物|5|
|新增入选候选人物|15|
|累计入选候选人物|20，含不同身份的两位王珪|
|目录发现线索|23，其中 15 入选、2 条已有目录点/姓名提示未重复增人，其他因本轮目标已达而暂不入选|
|待人工审核人物|20|
|人工批准 / 正式入库人物|0 / 0|
|旧 / 新称呼候选|25 / 30，累计 55，含姓名写法|
|可定位正文证据|累计 52，旧 22、新 30；不是 52 条不同别称|
|DeepSeek 实际核验|32 条：新关系 30 条，旧“文忠”关系 2 条，涉及 17 位人物|
|DeepSeek 原始关系结论|31 通过、1 存疑|
|程序与 Codex 一致性检查后|29 AI 核验通过、3 存疑、0 拒绝；全部仍待人工批准|
|本轮未再次核验的旧称呼|23 条；不能算本轮已核验|
|身份案例|共享称呼 1 类实际案例；完整姓名同名异人 1 类实际案例|
|实际网络采集|2 次公开 API 请求，4 份正史响应用于新批次；其余复用合法缓存|

新增人物：王珪（唐）、戴胄、岑文本、韓琦、曾公亮、陳升之、吳充、王珪（宋）、劉敞、曾鞏、李適、劉允濟、沈佺期、宋之問、閻朝隱。姓名保留实际原文写法，不自动转换简繁或异体。朝代来自目录分类提示，人物资料尚未人工确认。

## 前置条件审查与必要修复

已阅读 README、collection、prompt_log、第一至三阶段验收记录、DDL、采集/审核/核验/批次导入模块。基线 **153 项测试通过**，正式库四表均为空。

第三阶段当时没有完成真实外部语义核验：实际 CLI_ERROR 及停止状态仍保留，不回填成功。本阶段是新的扩容任务，固定主任务 ID 为 `guji-id-stage4-expansion-20261016`，不是换 ID 重试第三阶段的同一输入。

|前置能力|本轮检查结果|
|---|---|
|显式调用既有 DeepSeek 集成|本轮通过现有桥接器真实成功一次，见实际回执|
|真实独立核验记录|已保存 32 个对应任务的输出；首次格式拒收和保守适配均保留|
|AI 状态与人工批准分离|AI 状态在独立核验包；原候选 CSV 均 pending，批准模板仍 pending|
|未批准不能正式入库|保持原门槛；本轮没有调用 approve 或正式 import|
|幂等、增量、冲突、事务回滚|原测试持续通过；实际 dry-run 只写一次性副本|
|原有检索、档案、证据展示|完整回归通过，真实候选另在隔离库验证|

最小必要改动：复用 Client 增加可续用输出目录并保存错误响应的 Retry-After；新增批量调度/配置；核验准备支持固定主任务参数与重复引文块压缩；调用回执保留安全诊断分类；对实际返回的自由叙述增加显式保守适配。未改变 DDL、现有导入器和公众检索过滤条件。

## 可复用采集机制

入口为 `python -m scripts.collect_batch`，配置为 `data/collection_configs/tang_song.json`；不是手工编写新人物名单。

人物发现来自允许访问的正史主传分节及同名开篇“姓名，字某，某地人”声明。目录可以配置具体卷或卷次范围。附传、省略姓氏、多人物合节不自动补全。初次检查发现“孝廣”“孝蘊”为省姓附传，已排除并从缓存补选完整主传；初次调试输出保留在 work/stage4-initial-discovery，不计入最终规模。

人物 ID 按来源书、卷和独立篇目稳定生成。同名不作为唯一键；不同朝代的王珪分别保留 ID。同朝另传的同名只提示潜在重复，不擅自合并。已有候选的目录点及姓名写法用于避免重复扩容。正式库匹配另列在 candidate_discovery_with_db_matches.csv；当前正式库为空，均无正式记录匹配。

每批保存 bulk_batch.json、checkpoint.json、effective_config.json、collection_log.csv、raw/、text/、独立报告和四份要求的标准 CSV。已完成的批次可自动识别为后续扩容基线。重复执行同名批次须显式 --resume；一旦候选中有人工作出的审核状态，拒绝续采覆盖。成功响应经哈希核验后复用，缓存获取日期和修订号保持原值。

429 会保存原始响应和 Retry-After，持久化 not_before 与受限的指数退避；当前进程停止该主机网络请求。未到截止时间不请求，缓存仍可使用。显式续采可在截止之后再尝试；前次失败响应另存，日志定位不会指向被覆盖的失败内容。程序串行限速，无并发绕限、验证码绕过或传输切换。本轮没有遇到新的 429；秒数/HTTP 日期、跨进程退避和限流后缓存使用已用独立测试验证。

维基文库公开 Action API 的程序使用依据 [官方礼仪](https://www.mediawiki.org/wiki/API:Etiquette)和[maxlag 说明](https://www.mediawiki.org/wiki/Manual:Maxlag_parameter)。本轮设置 User-Agent、maxlag=5、串行间隔 5 秒、超时和请求预算。ctext、识典古籍及未确认授权接口的搜韵沿用已有跳过记录，没有重新探测正文或绕过限制。

### 实际采集及离线续采命令

```powershell
python -m scripts.collect_batch --config data/collection_configs/tang_song.json
python -m scripts.collect_batch --config data/collection_configs/tang_song.json --resume --offline
python -m scripts.check_collection --input-dir data/collections/stage4-tang-song-20
```

首次在线只补取《舊唐書》卷70、《宋史》卷312；复用《宋史》卷319、《新唐書》卷202。离线续采没有追加 HTTP 请求，保持稳定 ID 和去重。后续原文整理用同一条完整开篇句支持姓名和字，避免重叠剪辑夸大证据；各关系仍有独立 ID 和原文偏移。

同一批次不存在时执行在线命令；当前交付已存在，需 --resume，或改用新的批次名。缓存不足时使用 --offline 会记录缺失，不联网补抓；不保证必须凑到目标人数。

## 实际 DeepSeek 调用

读取并使用 `C:/Users/LENOVO/.codex/skills/claude/SKILL.md` 和现有 `Invoke-ClaudeAssist.ps1`，没有新造桥接器、直接调用 raw claude 或创建转接代理。用户更新的任务总上限为 5，现有包装器实际返回 max_calls=5；本轮只执行一次授权调用，calls_used=1，没有自动后续调用。

```text
主任务：guji-id-stage4-expansion-20261016
核验批次：VB-c32955de8b1a0cd7
开始：2026-10-16T23:44:24+08:00
结束：2026-10-16T23:45:06+08:00
code：OK；process_exit_code：0；elapsed_ms：41247
transport：direct_deepseek
model：deepseek-v4-flash-vision-exp[1M]
工具权限：关闭；无 MCP、Shell、文件或网络工具
核验范围：local_saved_response_only
```

输入为待验证关系、实际原文、上下文、原始 HTML 摘录和来源元信息，不含采集器 assessment 或 Codex 的通过解释。32 条任务全部通过本地请求/响应、书卷篇、修订、哈希、原文偏移和连续上下文检查。核验包约 20456 UTF-16 字符，未超出既有 24000 门槛。

已实际执行一次：

```powershell
python -m scripts.verify_evidence call --batch-dir data/verifications/stage4-tang-song-20
```

这不是待运行的建议，不要重复调用。未读认证值，未检查直连模式无关的 CC Switch，未修改连接、模型或额度。实际回执 output_tokens=6234；包装器虽配置 2200 tokens，但上游没有将其作为硬上限，报告保留实际用量，没有伪造或截断输出。

### 格式适配与三条分歧

真实输出含 32 个唯一且完整匹配的任务对象；摘要误写为 31。叙述方式被模型输出为中文自由文本，而非约定单字母代码；韩琦姓名一项的身份歧义码为 `-`。严格解析首次拒收，保存在 stage4-tang-song-20/。

修复方式是显式启用本地自由叙述适配，而非重新调用模型：原述逐字保留为 relation_type，标注 narration_format=verbatim_free_text；`-` 保守映射身份未定；未知 verdict、材料类型、列数、理由代码或遗漏任务仍然拒绝。没有用关键词把叙述推断为本人自称，也没有把 31 的摘要数字当统计依据。

适配版保存在 stage4-tang-song-20-reviewed/，parser_derivation.json 记录新增模型调用为 0。原始响应及回执与首次版本一致。

|关系|程序与 Codex 检查|DeepSeek 原始判断|最终处理|
|---|---|---|---|
|韓琦 / 韓琦|传记有明确姓名原文|V，但误标本人原作 S，身份码缺失|U，材料分类冲突及身份码问题待核|
|唐王珪 / 王珪|开篇姓名与字可匹配|V，但关系强度给 C（需推断）|U，不能把模型的间接判断当作明确通过|
|劉允濟 / 允濟|原文确有“劉允濟，字允濟”|U、match=0，怀疑名与字同形及底本讹略|U；保留原文实际匹配与模型语义疑问的差异，待查底本/别证|

其余 29 条关系在客观证据及一致性检查后列为 AI 核验通过，但没有自动改数据库核对状态。核验包中有 8 条高风险任务需重点查看，含两类身份案例；自由叙述与时代分类也纳入抽查。

## 实际身份案例

### 共享称呼：文忠

- 苏轼：独立 Person ID，已取得[《宋史》卷338·蘇軾](https://zh.wikisource.org/w/index.php?oldid=1458054)中的“遂崇贈太師，諡「文忠」”。
- 欧阳修：不同 Person ID，已取得[《宋史》卷319·歐陽脩](https://zh.wikisource.org/w/index.php?oldid=1744467)中的“五年，卒，贈太子太師，諡曰文忠”。

两条独立来源关系本轮均有 AI 意见支持；仍待人工批准。共享谥号不意味着完整姓名相同，不合并人物。

### 完整姓名同名异人：王珪

两份新取回的正史响应分别记载：

- 《舊唐書》卷70：“王珪，字叔玠，太原祁人也。”
- 《宋史》卷312：“王珪，字禹玉，成都華陽人，後徙舒。”

两人分别保留 P-D-470d41f21e8cab39f12341ed 与 P-D-46c39186c9a0fa0d8177b557。朝代分类、字、籍贯、传记及原始修订链接可分别定位。实际 API 取得的链接和全文片段见 identity_cases.json 与 comparison_and_cases.md，未猜测版次或生卒年。唐王珪的姓名任务因模型强度不一致仍为 U，但其“叔玠”及宋王珪“禹玉”任务有独立明确引文；案例有真实史料支持，并非人工批准完成。

## 公众隔离、dry-run 与系统回归

公众页面继续使用原 verified 及直接支持证据规则，正式库为空。没有临时关闭过滤、把 AI 状态直接作为人工确认或调用本批 approve/import。

真实批次的 29 条关系完成了隔离 dry-run：拟新增 17 人、29 称呼、17 来源、29 证据，只写一次性 SQLite 副本；导出的 pending_csv 仍为 pending。本轮 20 人没有全部自动变成这份方案；旧李白、杜甫、白居易未在本次 32 条任务内重新核验。

```powershell
python -m scripts.import_verified_batch dry-run --batch-dir data/verifications/stage4-tang-song-20-reviewed --db data/guji.db --output-dir work/stage4-dry-run-new
work/.venv/Scripts/python.exe -m pytest -q --basetemp=work/pytest-stage4-regression
work/.venv/Scripts/python.exe -m scripts.verify_stage4 --output work/stage4-acceptance-new
work/.venv/Scripts/python.exe -X pycache_prefix=work/pycache-stage4 -m compileall -q app scripts tests
work/.venv/Scripts/python.exe -m pip check
```

整体 **164 passed、1 warning**；编译和依赖检查通过。既有查询、档案、证据、未审核隔离、导入幂等、增量事务/回滚等测试持续通过。警告为已有 FastAPI/Starlette TestClient 的 httpx 弃用提示，没有为此更换架构。

隔离验收库载入实际候选原文，但核对状态仅为 TEST ONLY 模拟，未生成真实人工批准。模型存疑关系和缺证候选保持不公开；在正常过滤规则下验证规范姓名、字、号、共享称呼、完整同名、各自详情和独立原始链接，以及重复导入零新增。屏幕截图顶部有醒目的隔离说明，不是正式网站数据。

Chrome 在 1440×1000、390×844、320×740 三种视口进行了 **30 项布局检查**，无横向溢出或脚本错误。两类检索均返回两个独立候选，不自动跳转或选择第一人，各自来源 URL 不同。未在实体手机、Safari、Firefox 中实测。

正式库前后 SHA-256 相同：`f4931941751bb5dd3c1c5c9b0244d59257b364e6d23ee2658dda9b10e587fac7`，四表仍各为 0。隔离数据库和模拟已核对 CSV 只留在 work/。

## 交付位置和人工操作

|位置|内容|
|---|---|
|data/collections/stage4-tang-song-20/|入选 15 人、30 称呼、30 引文，23 条目录发现记录、响应、日志、配置、检查点、报告|
|data/collections/stage4-review-input/|新增材料加旧共享文忠两条的待核验输入，不是又新增 17 人|
|data/verifications/stage4-tang-song-20/|真实调用和首次严格格式拒收，历史保留|
|data/verifications/stage4-tang-song-20-reviewed/|本地保守适配后的正式审核包：29/3/0、原始模型输出、回执、抽查、批准模板|
|data/verifications/stage4-final-report/|最终规模对比、两类身份案例、正式库匹配结果、55 行人工工作表、3 条差异、dry-run 与验收附件|
|docs/screenshots/stage4/|明确标记的隔离验收截图与验收 JSON|

先处理 human_review_worksheet.csv 中高风险项和缺古籍正文的旧 8 条；本次 3 条存疑须处理具体问题，不能为了入库改成通过。其余旧 23 条本轮未覆盖，保持未核验，不能混入本次 29 条批准清单。查看 8 条代表性样本与全部问题清单后，可按已有阶段三批准机制，对精确批次与精确关系清单作具名决定。

approval_template.json 仍 decision=pending、批准清单为空。本次不会替用户填写、记录或执行批准。后续人工批准和增量导入沿用 docs/stage3_verification.md；与当前实际有关的模型证据应使用本阶段适配审核包，而非第三阶段失败包。

## 今后扩容到 50 / 100 人

先做无网络规划（本轮已验证分别还需 30 / 80 位候选）：

```powershell
python -m scripts.collect_batch --config data/collection_configs/tang_song.json --batch-name tang-song-50 --target-total 50 --max-requests 30 --plan-only
python -m scripts.collect_batch --config data/collection_configs/tang_song.json --batch-name tang-song-100 --target-total 100 --max-requests 60 --max-catalogue-pages 60 --plan-only
```

未来用户决定扩采时执行，不是在本轮继续执行：

```powershell
python -m scripts.collect_batch --config data/collection_configs/tang_song.json --batch-name tang-song-50 --target-total 50 --max-requests 30 --max-catalogue-pages 40
python -m scripts.collect_batch --config data/collection_configs/tang_song.json --batch-name tang-song-100 --target-total 100 --max-requests 60 --max-catalogue-pages 60 --max-candidates 1500
```

失败后同名批次加 --resume，优先缓存可加 --offline。可用 --dynasties 唐 宋、--sources 维基文库、--no-cache 及 JSON 中书卷范围控制任务，不修改 Python 人名列表。本版仅实际实现维基文库接口，其他来源未获适用授权时跳过。50/100 是累计候选目标，不保证网站和证据一定足够，不把未达目标伪报为完成。

核验输入准备另有 scripts.prepare_expansion_review；超过现有桥接上下文/输出能力会拒绝，不偷偷删关系或自动连调。采集命令不调用模型、不批准、不写库；未来核验每次仍需明确授权，同一主任务保留 ID、历史、失败停止和五次总上限，不能把它当成自动五连调许可。

目前按“已审核真实数据可供公开使用”的验收口径，**尚不满足进入第五阶段的数据条件**：批准/正式入库为 0，3 条存疑未闭环，旧批次仍有未覆盖与缺证记录。本阶段候选扩容、真实独立核验、审计和回归已完成，下一步是人工处理及明确批准，而非自动进入第五阶段。
