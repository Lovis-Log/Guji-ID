# 第三阶段：现有批次证据核验与安全入库

本轮状态：**独立语义核验受阻，未批准、未导入正式库。** 实现了可审计流水线并进行了真实调用尝试；没有取得有效 DeepSeek 判断，不能声称完整多智能体核验已跑通。

## 实际结果（2026-10-16，Asia/Shanghai）

批次 `VB-31978a5b1fe381ff`，材料仅取自已有 `data/collections/2026-10-16-delivery/`。5 位人物、25 条候选称呼关系，未扩大采集。

|项目|实际结果|
|---|---:|
|稳定核验任务|30（22 条证据任务、8 条缺证任务）|
|本地确定性原文与来源关联校验通过|22|
|缺古籍正文|8|
|真实外部调用尝试|1|
|取得有效 Subagent 语义判断|0|
|AI 核验通过|0|
|存疑 / 未完成|30（全部 model_returned=false）|
|明确拒绝|0|
|不同称呼关系待复核|25|
|代表性抽查样本|8|
|全部问题 / 必须复核任务|30|
|人工批准 / 正式新增|0 / 0|

“30 存疑/未完成”是程序对没有外部结果的保守状态，不是 DeepSeek 已逐条判为存疑。没有找到证据不等于历史关系错误。22 条程序通过只证明本地响应和引文一致，不代替语义核验、重新联网或影印底本校勘。

交付目录：`data/verifications/2026-10-16-batch1/`。

## 检查到的既有调用方式

使用用户明确授权的 `claude` 技能：`C:/Users/LENOVO/.codex/skills/claude/SKILL.md`。检查了该文件、其 agents/openai.yaml、现有桥接入口、README 和 Bridge.Core.psm1 的调用/输出逻辑；没有读取认证值或 CC Switch 配置。

现有集成为 **Codex → PowerShell 7 包装器 → Claude Code CLI → 既有供应商配置**，不是另建的原生 Codex 子代理。本阶段将这一路外部独立分析者称作 DeepSeek Subagent；没有添加新的 Agent 框架或转接代理。

唯一入口：`C:/Users/LENOVO/.codex/claude-bridge/Invoke-ClaudeAssist.ps1`。Python 调度器将 JSON 通过 stdin 交给原生 `pwsh -NoProfile -File ... -TimeoutSeconds 120`。没有直接运行 raw claude、修改 provider/model/auth、切换 transport 或弱化 OS 权限。

|约束|已检查的现有实现|
|---|---|
|模型工具|`--tools ""`；文件、Shell、网络工具关闭|
|MCP|严格空配置，禁止 mcp 工具|
|自定义执行|safe-mode、禁钩子、禁技能命令、dontAsk、无权限提示|
|会话|不保存会话，最多 1 个 turn|
|输入|prompt+context 最多 24000 UTF-16 字符；本次约 2.33 万|
|输出|2200 tokens，上游是否遵守由供应商决定|
|超时|允许 1–120 秒；本次使用 120 秒|
|历史与次数|同一 main task 保留默认总上限 2 次、停止状态与防重复记录；本次使用 1 次|

主任务 ID 固定为 `guji-id-stage3-existing-batch-20261016`。当前失败后没有换 ID、删除账本、增加额度或重试。

桥接器要求 JSON 固定四字段：summary、findings、alternatives、verification。为适应输出上限，任务结果编码在 findings 的 issue/evidence/risk 中，说明代码放在 verification；程序严格检查完整任务集合、唯一 ID、枚举、列数和解释代码，再展开为完整逐条 CSV/JSON。这一紧凑格式不是另一个桥接器，也不改变现有模型设置。

外部包仅包含人物与别称的待验证假设、真实原文、前后文、文献/卷篇/URL、修订号/哈希及限长原始 HTML 片段；不包含采集器 assessment、usage_relation、AI/Codex 的通过结论或身份说明推论。完整响应保存在 source_snapshot；工具被关闭的 Subagent 只能核对传入的摘录，不能自行打开文件或访问原站。

## 真实调用与失败记录

实际命令：

```powershell
python -m scripts.verify_evidence call --batch-dir work/stage3-verification-batch
```

该命令已且仅已执行一次，**不要照此重复运行**。回执：

```text
started_at:  2026-10-16T22:50:51+08:00
finished_at: 2026-10-16T22:51:51+08:00
code:        CLI_ERROR
error_phase: cli_process
cli_exit:    1
elapsed_ms:  59480
calls_used:  1
max_calls:   2
retryable:   false
```

这证明既有包装器启动了 CLI 调用尝试，不证明上游 DeepSeek 已收到请求或完成推理。包装器没有返回模型 ID、有效语义判断或底层 CLI stderr，因此不猜测故障究竟来自 CLI 参数、认证还是上游服务。

subagent_raw_output.json 保存桥接器实际返回；call_receipt.json 保存脱敏回执、输入和输出哈希、退出码与时间。stderr 只记哈希和长度，不泄露认证。subagent_environment_check.json 保存检查到的接口、权限、限制与桥接代码哈希，不包含密钥。

技能明确要求：**“On failure ... stop this external invocation”**、**“Do not automatically retry”**。因此没有再次调用、切换提供商或将程序匹配冒充模型核验。后续需要修复现有 CLI，并由用户重新明确授权及说明如何处理同一主任务的停止状态；不能靠清空历史、删除尝试标记、换 task_id 或自动加额恢复。

## 各执行者的职责与产物

|执行者|步骤|产物与限制|
|---|---|---|
|Codex|读取现有五人批次，定义独立关系与稳定任务，准备无结论性解释的输入|tasks.json、reviewer_request.json；不补造古籍内容|
|确定性程序|字段/外键、响应存在性/哈希、实际 API 请求与修订、卷篇、派生正文、原文偏移、上下文比对|每任务 program 结果；缺证与错误分开记录|
|DeepSeek Subagent|通过现有 CLI 尝试独立语义核验|本轮 CLI_ERROR，没有可采用的判断|
|Codex 与程序|比较外部结论、程序证据和透明的明确关系标记；分歧降级|本轮全部保留未完成状态，未进行模型一致性投票|
|程序|按风险排序、生成代表性抽查、完整问题与批准模板|review_queue.csv、spot_checks.csv、all_problem_records.csv|
|人工|抽查、处理问题、对精确清单作最终批准|当前尚未发生；approval_template.json 仍 pending 且选择为空|
|程序与 SQLite|批准校验、只读副本 dry-run、增量去重、单事务写入|本轮 dry-run 因 0 条可用 AI 关系而阻断；正式写入 0|

来源问题、共享身份、弱共现和缺正文排在清单前；其余原文明确但尚缺外部判断的任务列为 medium，依然必须复核。风险级别不会自动提升核对状态。

8 条抽查样本覆盖：苏轼/子瞻、杜甫/子美、苏轼/文忠、欧阳修/文忠、苏轼/東坡居士、苏轼姓名仅提及的弱材料、李白/青蓮居士缺原文、碑志中的和仲。这是可复现的类型覆盖样本，不是用抽样保证全部正确。全部问题另列，不会因抽样而遗漏。

“文忠”属于共享称呼候选，苏轼和欧阳修保留两个不同 Person ID。这与“完整姓名相同的不同人物”不同；后者由单独的碰撞标记和测试覆盖，没有在真实五人批次中虚构案例。

## 文件说明与本地复核

|文件|内容|
|---|---|
|verification_results.csv / parsed_results.json|完整逐条结构化结果；含所有要求字段、模型是否实际返回、程序比对、分歧和批准状态|
|aliases_summary.csv|25 条独立关系汇总，不把多条证据重复算为别称|
|review_report.md|人工可读结果、引用入口、失败边界和全部问题|
|review_queue.csv|风险排序的完整审核清单|
|spot_checks.csv|8 条代表性样本|
|all_problem_records.csv|全部身份、来源、引文或未完成核验的问题任务|
|subagent_raw_output.json / call_receipt.json|真实失败返回及脱敏调用记录|
|source_snapshot/ / input_manifest.json|原始输入完整副本及内容哈希；结果中的 raw_path 相对此快照目录|
|review_seal.json|核验输入、调用和核心审核输出的哈希封存|
|approval_template.json|未批准模板，不能直接导入|
|dry_run/|实际阻断的 dry-run 结果，以及四份空的待批准 CSV|
|execution_record.json / pytest_result.txt|实际测试、数据库前后状态及验收记录|

报告曾做一次本地风险排序修订；report_derivation.json 说明它复用相同请求、相同原始响应和同一次失败回执，新增模型调用为 0。不是第二次核验或第二次 CLI 尝试。

不联网、不调用模型、不写正式库的复核命令：

```powershell
python -m scripts.verify_evidence check --batch-dir data/verifications/2026-10-16-batch1
python -m scripts.import_verified_batch dry-run --batch-dir data/verifications/2026-10-16-batch1 --db data/guji.db --output-dir work/stage3-dry-run-new
```

dry-run 输出目录须不存在。本次实际结果是 blocked_no_ai_verified_records、四表拟新增均为 0、formal_database_writes=0，不声称真实数据导入成功。

## 批次批准与正式导入（仅供后续有效批次）

当前批次没有有效的 AI 通过记录，不能通过填写批准文件绕过此阻碍。可以继续按既有逐条人工审核流程处理资料，但不能把这种人工处理报告为本轮多智能体核验通过。

对于以后真正取得有效外部判断的批次：

1. 人工阅读代表性样本及全部问题，确定 approved_alias_ids。只有原始证据有效、外部判断有效且 Codex 明确关系检查一致的条目可选。缺原文、仅提及、身份未定、无效输出不自动进入计划。
2. 复制 approval_template.json 为单独的决定文件。填写 decision=approved、真实 approver、实际带时区 approved_at、approved_alias_ids、已查看的 checked_task_ids，以及所选关系中全部高风险记录的 accepted_high_risk_task_ids。statement 必须为模板给出的完整确认声明。审核模板本身不改写为批准。
3. 使用以下入口记录明确批准。程序绑定 batch_id、review_seal_sha256、精确 ID 清单、抽查和高风险处理；不会由 AI 替人填写。批准是本地具名记录，不是登录鉴权或数字签名。
4. 批准后才执行 import；不得为当前失败批次擅自执行此步骤。

```powershell
# 仅适用于人工已明确批准的有效批次，BATCH_DIR 和 DECISION_JSON 换成实际位置。
python -m scripts.import_verified_batch approve --batch-dir BATCH_DIR --decision-file DECISION_JSON --output work/human-approval.json
python -m scripts.import_verified_batch import --batch-dir BATCH_DIR --approval work/human-approval.json --export-dir data/reviewed/approved-batch --db data/guji.db
```

导入继续复用 export_reviewed、import_reviewed 和 import_batch；没有更改旧 CSV 字段、DDL 或公众检索的 verified 规则。待批准方案始终是 pending。只有具名批准覆盖的关系及充分支持证据才映射为 verified/supports/direct；保留采集器原来的关系和证据性质供追溯。

人物规范姓名与独立 ID 纳入批准范围；朝代、籍贯、生卒和身份说明中的种子提示不自动变成已核实事实，本机制默认将这些可选信息留空。后续补充须依实际人工核实的资料另行处理，不能覆盖既有更丰富的审核信息。

正式导出中的 evidences.notes 保存 verification_batch_id、verification_task_id、approval_digest、reviewer_model、原始响应/正文哈希及既有审核人时间。人物和来源通过四表外键及批准清单关联到该批次。只修改可编辑导出文件再重算其审计哈希不够，导入会重新推导已批准记录进行比较。

增量策略：同 ID、同内容复用；人物新方案中的空字段不清除旧已核实字段；同 ID 内容或审核状态冲突拒绝；同一关系/来源/引文换证据 ID 仍属重复，拒绝凑数。不同人物不会仅因姓名或别称相同而合并。最终新增使用原 import_batch 的单事务，失败全部回滚。

## 自动测试与验收状态

```powershell
work/.venv/Scripts/python.exe -m pytest tests/test_verification.py -q --basetemp=work/pytest-stage3-incremental
work/.venv/Scripts/python.exe -m pytest -q --basetemp=work/pytest-stage3-final
work/.venv/Scripts/python.exe -X pycache_prefix=work/pycache-stage3 -m compileall -q app scripts tests
work/.venv/Scripts/python.exe -m pip check
```

新增核验/批准/增量/回滚测试 **28 项通过**；整体回归 **153 passed、1 warning**。现有检索、独立人物、档案、证据、CSV 导入、采集原文溯源测试均通过。既有 FastAPI/Starlette TestClient 的 httpx 弃用警告保留。编译和 pip check 通过。

成功模型返回、人工批准和入库的自动测试全部使用 TEST_ONLY_SIMULATED_REVIEWER 和独立测试数据库，不访问真实供应商，不能用来证明本次 DeepSeek 语义核验成功。真实调用失败是单独保留的事实。

正式数据库前后 SHA-256 相同：

```text
f4931941751bb5dd3c1c5c9b0244d59257b364e6d23ee2658dda9b10e587fac7
```

四表仍均为 0。未主动扩大人物、未新抓取史料、未擅自批准、未进入第四阶段。
