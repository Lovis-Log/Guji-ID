# A+B 古籍展示索引（V0.2）

正式书影清单是 [`app/archive_scans.json`](../app/archive_scans.json)。目前为 `[]`：**零条 B 模式可用书影**。现有 Design Lab 图片是 AI 视觉示意，不能写入清单、不能称为馆藏扫描。

每个可展示条目需由人工核实后填写以下字段：`person_id`（规范人物 ID）、`evidence_id`（该人物公开的直接支持证据 ID）、`provider`（藏馆或资源提供方）、`image_url`（可使用的 HTTPS 图片地址或已放入 `/static/archive-scans/` 的本地文件）、`source_url`（必须等于证据来源的原始链接）、`license`（明确使用说明）、`review_status`（仅 `verified` 才展示）。文献题名、卷次和章节从正式证据所关联的来源读取，不在清单中另写一份易冲突数据。

可选的 `highlight` 使用相对原图 0–1 的 `x`、`y`、`width`、`height`，并仅在人工确认页内位置后设置 `reviewed: true`。缺失或未核对时展示整页，不画定位框。

展示层会按 `person_id + evidence_id + source_url` 三重条件，使用现有公开证据规则核对清单；关联错误、状态未核实、资源缺失、元数据不足及图片加载失败都会回退 A 数字题签。A 的纸张和古籍光影是视觉示意，真实原文摘录仅取已公开的直接证据。新增真实书影之前仍需人工确认图像版权、稳定链接、来源页和人物归属。本机制没有修改 SQLite 表、采集、核验或入库规则。

自动测试：[`tests/test_archive_display.py`](../tests/test_archive_display.py) 校验人物、证据、来源三重关联与索引状态；[`tests/test_immersive_home.py`](../tests/test_immersive_home.py) 校验 A 题签随规范 ID 更新、同名候选保持独立、B 仅在完整关联时启用、未核对坐标不画框，以及图片加载失败回退 A。浏览器层面的 B 启用、朱砂框、加载失败回退已用独立合成夹具验证；该夹具不写入本清单，也不代表任何真实馆藏。
