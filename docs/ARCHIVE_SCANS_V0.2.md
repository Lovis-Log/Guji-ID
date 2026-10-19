# A+B 古籍展示索引（V0.3）

B 区「书影与图像」分为两个互不混同的层：

1. **真实书影**：正式清单是 [`app/archive_scans.json`](../app/archive_scans.json)。目前仍为 `[]`，因此页面显示“暂无可核验的真实书影”，不以任何 AI 图像替代。
2. **AI 拟古绘像**：正式映射清单是 [`app/figure_media.json`](../app/figure_media.json)。十张图均为 AI 生成，统一标注 **“AI生成·拟古演绎，非真实古籍书影”**；图像中的古文字与印章不进入古籍证据表，也不作为人物姓名、字号或原文的证明。

## 真实书影条目

每个可展示条目需由人工核实后填写：`person_id`（规范人物 ID）、`evidence_id`（该人物公开的直接支持证据 ID）、`provider`（藏馆或资源提供方）、`image_url`（可使用的 HTTPS 图片地址或已放入 `/static/archive-scans/` 的本地文件）、`source_url`（必须等于证据来源的原始链接）、`license`（明确使用说明）、`review_status`（仅 `verified` 才展示）。可选填 `book_title`、`volume`、`edition`、`holding`；一旦填写，必须与正式证据或来源记录一致。文献题名、卷次和章节默认从正式证据所关联的来源读取。

可选的 `highlight` 使用相对原图 0–1 的 `x`、`y`、`width`、`height`，并仅在人工确认页内位置后设置 `reviewed: true`。缺失或未核对时展示整页，不画定位框。

展示层按 `person_id + evidence_id + source_url` 三重条件，使用现有公开证据规则核对清单；关联错误、状态未核实、资源缺失或元数据不足的条目不会显示。

## AI 拟古绘像映射

`app/figure_media.json` 的每个条目必须同时满足：

- `person_id` 对应正式库中 `verification_status = 'verified'` 的人物；
- `db_name` 与该人物主键对应的 `canonical_name` 完全一致；
- `ai_generated` 为 `true`，`review_status` 为 `verified`；
- `image_url` 指向 `/static/figure-media/` 下实际存在的 WebP 文件；
- 清单顶层 `disclosure` 必须精确等于 **“AI生成·拟古演绎，非真实古籍书影”**。

十人映射（均未新增或猜测 CBDB ID）：

| 图像 | 正式库 person_id | 规范姓名 | 说明 |
| --- | --- | --- | --- |
| 李白 | `P-CBDB-32540` | 李白 | |
| 杜甫 | `P-CBDB-3915` | 杜甫 | |
| 白居易 | `P-CBDB-32227` | 白居易 | |
| 王維 | `P-CBDB-32174` | 王維 | 700–761，字摩詰；未绑定同名且无别称/证据的 `P-CBDB-175377` |
| 蘇軾 | `P-0180cd77-04c3-471a-937a-cc8e77709e52` | 苏轼 | 显示名按正式库规范姓名 |
| 李清照 | `P-CBDB-19713` | 李清照 | |
| 辛棄疾 | `P-CBDB-30359` | 辛棄疾 | |
| 陶淵明 | `P-CBDB-3835` | 陶潛 | “陶淵明”为正式库已核对姓名，按陶潛主键绑定 |
| 屈原 | `P-S7-e471cf86a00e` | 屈原 | |
| 司馬遷 | `P-S7-949c8f1cd5e7` | 司馬遷 | |

没有对应条目的其他人物保持原默认状态：B 区不启用，A 数字题签与现有文字证据不受影响。

## 展示与失败回退

- A 的纸张和古籍光影是视觉示意，真实原文摘录仅取已公开的直接证据。
- B 区在“存在已核实真实书影”或“存在与人物绑定的 AI 拟古绘像”时启用。
- 真实书影加载失败时，页面在真实书影区域提示失败，但不会禁用 AI 拟古绘像，也不会影响 A 模式文字证据。
- 首页未指定人物时，B 区展示十人 AI 拟古绘像图录；选择具体人物后只显示与其规范 ID 绑定的图像。

自动测试：[`tests/test_archive_display.py`](../tests/test_archive_display.py) 校验真实书影的人物、证据、来源三重关联；[`tests/test_immersive_home.py`](../tests/test_immersive_home.py) 校验 A 题签随规范 ID 更新、同名候选保持独立、无书影/图像时 B 保持禁用；[`tests/test_figure_media.py`](../tests/test_figure_media.py) 校验十人映射、规范姓名绑定、AI 标注、B 区分层、静态 WebP 可访问及非证据字段。所有测试夹具均使用临时数据库，不写入正式 `data/guji.db`。
