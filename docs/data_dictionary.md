# 第一阶段数据字典

每张表的 ID 都是调用方提供的稳定字符串主键，必填且不接受空白。不从姓名推断 ID。NULL 表示未知或尚无信息，不替换为虚构值。CSV 可空字段用空单元格表示，不写字面量 `NULL`。

## people：独立人物档案

| 字段 | SQLite 类型 | 必填 | 含义 |
| --- | --- | --- | --- |
| person_id | TEXT | 是 | 人物唯一标识，跨批次稳定 |
| canonical_name | TEXT | 是 | 规范姓名；全局不唯一 |
| dynasty | TEXT | 否 | 朝代；不确定留空 |
| birth_year | INTEGER | 否 | 公元年，公元前用负数，无公元 0 年 |
| death_year | INTEGER | 否 | 同上；都已知时不早于生年 |
| native_place | TEXT | 否 | 籍贯，不强制映射现代地名 |
| identity_description | TEXT | 否 | 身份说明，不代替证据 |
| verification_status | TEXT | 是 | 四种核对状态之一 |

有范围或争议的年份不要硬填整数，暂时留空并在身份说明中记载待核对情况。整数须在 SQLite 有符号 64 位范围内。

## aliases：别称归属关系

| 字段 | SQLite 类型 | 必填 | 含义 |
| --- | --- | --- | --- |
| alias_id | TEXT | 是 | 别称关系 ID；关系归属不同人物时分别分配 |
| person_id | TEXT | 是 | people 外键 |
| alias_name | TEXT | 是 | 原始称呼，保留原始输入 |
| normalized_name | TEXT | CSV 可空，DB 必填 | CSV 留空时按 NFC + strip 自动计算；不作为身份标识 |
| alias_type | TEXT | 是 | 称呼类别，见下表 |
| verification_status | TEXT | 是 | 该别称归属关系的状态 |

| 类别值 | 含义 |
| --- | --- |
| name | 姓名（可为规范姓名对应称呼或其他姓名形式） |
| courtesy | 字 |
| art | 号 |
| posthumous | 谥号 |
| other | 其他已有依据的称呼类别 |
| unknown | 类别未知，不猜测 |

`(person_id, alias_name, alias_type)` 唯一。同一个 `alias_name` 或 `normalized_name` 可属于多个不同人物，不依据名称唯一性合并。一个人可有多条别称关系，同一关系可有多条证据。

## sources：可追溯文献来源

| 字段 | SQLite 类型 | 必填 | 含义 |
| --- | --- | --- | --- |
| source_id | TEXT | 是 | 来源定位 ID |
| book_title | TEXT | 是 | 真实古籍名称，不使用猜测的题名 |
| volume | TEXT | 否 | 卷次，可附进一步定位；已核对证据要求卷次或篇目至少有一项 |
| chapter | TEXT | 否 | 篇目，可附页码/条目定位，不编造 |
| platform | TEXT | 是 | 原始资料实际所在平台 |
| original_url | TEXT | 是 | HTTP/HTTPS 原始地址，不含账号密码 |
| edition | TEXT | 否 | 版本、底本或校勘信息，未知留空 |

当前以本课程可访问的网络史料为导入范围；没有可追溯 URL 的线下材料暂不导入来源表，避免编造链接。不同卷、篇或版本可以是同书的独立来源记录。来源表不单独宣称“已核实”；核对状态在引用它的证据中保存。

## evidences：原文与关系判断

| 字段 | SQLite 类型 | 必填 | 含义 |
| --- | --- | --- | --- |
| evidence_id | TEXT | 是 | 稳定证据 ID |
| alias_id | TEXT | 是 | aliases 外键，指向具体人物的别称关系 |
| source_id | TEXT | 是 | sources 外键 |
| original_text | TEXT | 是 | 实际抄录的原文片段，保留内容与换行；不自动生成 |
| usage_relation | TEXT | 是 | 该段材料相对本条别称归属关系的作用 |
| evidence_nature | TEXT | 是 | 直接或间接证据 |
| verification_status | TEXT | 是 | 该证据抄录、定位及判断的人工核对状态 |
| notes | TEXT | 否 | 核对说明、歧义、驳回理由等 |

| usage_relation | 含义 |
| --- | --- |
| supports | 支持此称呼属于所关联人物 |
| refutes | 反对该归属，保留反证 |
| mentions | 仅提及称呼，不能确认归属 |
| uncertain | 使用关系无法确定 |

`evidence_nature`：`direct` 直接证据；`indirect` 间接证据。这里的直接/间接是针对人物别称归属是否由原文直接说明，不是对资料可信度自动打分。

## 共用核对状态和规则

| 值 | 中文 | 使用方式 |
| --- | --- | --- |
| pending | 待核对 | 有资料记录，尚未完成人工核实 |
| verified | 已核准 | 旧人工流程显式确认或第六阶段自动准入；具体方法由批准审计及automatic_evidence区分，不能统一解释为人工核实 |
| disputed | 存疑 | 有歧义、冲突或不能确定身份 |
| rejected | 驳回 | 否定的关系或证据，保留以便复查 |

数据库默认状态是 `pending`；CSV 明确要求填写状态，避免空白被误读为已确认。

导入器在提交前检查整个数据库的证据链：

1. `verified` 证据所引用的来源必须有 `volume` 或 `chapter`。
2. `verified` 别称必须有至少一条同时为 `verified`、`supports`、`direct` 的证据。
3. 其他状态可保留在独立 Person ID 下，允许尚无证据，不合并、不自动升级状态。

这些只保证结构一致，不能证明资料内容真实。互相冲突的史料应由人工将关系标为 `disputed`，系统不自动裁决。直接手工写入 SQLite 能绕过 URL、规范化与跨表状态检查；本阶段不提供通用写 API，统一使用导入脚本。

## 第五阶段的展示与正式写入补充

四表结构没有改变。默认正式库及GUJI_DB_PATH配置库仅允许封存批次明确批准入口写入，通用四表导入保留用于隔离验证。当前正式人物的可选朝代、籍贯、生卒、简介均留空，不从候选提示自动升级。

查询返回的 `identity_evidences` 是展示用派生数据，不是新表或新人物事实字段：只取该人物公开可用的 `verified + supports + direct` 证据，并关联文献卷篇、URL、称呼类别；相同来源和原文去重，摘要最多展示两条，完整材料仍在具体称呼页。它遵守原人物、称呼和证据公开门槛。

`evidence_notes` 的展示字段继续采用白名单；含reviewer_model的说明标记为“机器辅助说明”，不输出模型名称、原始文件路径、内部审核人或密钥信息。原文、AI说明及用户批准在存储和界面上分开表达。

## 第六阶段自动准入补充

在用户明确授权取消逐条人工审批后，新增独立的A/A+/B自动入口，原手工批次入口仍维持其既有约束。旧四表列结构不变，新增8张辅助表，DDL见 `app/migrations/006_automatic_admission.sql`；字段与运行方法见 `docs/stage6_pipeline.md`。

A表示结构化来源核准，其evidences.original_text保存原始结构化字段JSON，必须明确显示为“非古籍原文”；A+才表示已定位到独立文本中的原句。方法、证据类型、外部ID、版本、源表和主键、记录/文件摘要、批次、时间分别保留。B原始数据在独立暂存库，不进入正式四表；正式库只存隔离数量汇总。

`direct`表示该种来源直接支持该关系：A为数据库字段直接记录，A+为原文明确声明，不能把两者混为“古籍原文已核验”。CBDB引用书目与页码仍是源字段，不是取得的原文。

称呼normalized_name继续保留NFC/strip结果；OpenCC t2s只生成另表检索键，不改变原名、人物ID、归属或唯一键。姓名同形或检索键相同可命中多个Person ID，不能合并。实际全源同朝代同姓名同字的潜在重复ID属于身份冲突，自动隔离。

