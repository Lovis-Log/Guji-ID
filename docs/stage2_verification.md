# 阶段二 MVP 验收

日期：2026-10-16（Asia/Shanghai）。项目根目录：`D:\Projects\移动信息化小组作业\Guji ID`。

## 开始前审查

检查了 AGENTS.md、README、现有 FastAPI 工厂/异常处理、四表 DDL、规范化、CSV 导入器、页面资源及既有测试。基线为 **93 passed、1 warning**。正式库四表均为 0，外键检查无错误；没有阻断第一阶段的问题。

没有更换架构、迁移/重建表或删除数据。复用原 create_app、lifespan、短连接、模板布局、配色和四表导入。`/health` 的 stage 更新为 2，相应旧断言同步更新；其余错误响应和模板下载保持原有行为。原采集脚本保留，本阶段没有执行新采集。

界面采取保留式扩展：DESIGN_VARIANCE=3、MOTION_INTENSITY=1、VISUAL_DENSITY=5，以研究查阅的可读性为主，不添加装饰动画或前端框架。保留现有纸色、墨色、宋体标题和系统正文字体，维持单一浅色主题。

## 增量实现与测试

|步骤|实际操作|对应结果|
|---|---|---|
|检索|catalog.py 共用公开规则、参数绑定、NFC 与 strip；姓名和别称精确匹配|10 项目录测试通过|
|人物档案|已核对身份和别称列表；缺字段保持空状态，隐藏人物直接访问 404|18 项目录及原应用相关测试通过|
|古籍证据|多文献、叙述方式、定位、来源链接和原文；不由 direct 推断自称|27 项目录测试通过|
|同名候选与完整路径|所有独立 ID 均展示；规范姓名同名、共用字、多称呼归同人、无结果和输入限制|38 项目录及原应用相关测试通过|
|整体回归|原数据库/导入/采集/CLI/页面测试与新增目录测试一起运行|125 passed、1 warning|

新增测试不访问历史网站。仅使用 pytest 临时目录或 work/ 下标注的虚构库；测试人物、书名、原文和 example.invalid 链接都不是真实史料，不进入正式库。

## 公开数据门槛

- 规范姓名只命中人物 verified 记录；人物档案只允许 verified 人物。
- 通过别称检索及展示别称时，人物和别称须 verified，并有 verified/supports/direct 证据，附原文、实际卷篇定位与有效 HTTP(S) URL。
- 所有入口共用相同 CTE，直接访问别称详情也不能看到待核对/存疑资料。
- 可公开别称的其他已核对证据按各自关系和性质展示，不将反证、提及或间接证据自动当作支持依据。
- notes 的 relation_type、evidence_type、context、remarks 按实际值展示；direct 不映射为自称。本地响应路径和审核内部字段不输出。
- 人物级 verified 依赖人工确认已填字段；现有表无逐字段状态，部分信息未核实时须留空。README 列明所需 CSV 字段。

## 整体命令与结果

```powershell
work/.venv/Scripts/python.exe -m pytest -q --basetemp=work/pytest-stage2-final
work/.venv/Scripts/python.exe -X pycache_prefix=work/pycache-stage2 -m compileall -q app scripts tests
work/.venv/Scripts/python.exe -m pip check
```

整体回归：**125 passed，1 warning，0 failed**。警告仍为现有 FastAPI/Starlette TestClient 对 httpx 的第三方弃用提示。没有为此更换既有依赖。pip check 返回 No broken requirements found。

普通 compileall 首次因既有源码缓存目录不可写而出现 PermissionError；将缓存定向到 work/pycache-stage2 后编译通过（退出码 0），不修改现有目录权限。

## 真实浏览器验收

可选 Playwright 1.64.0 只安装到测试虚拟环境，使用本机 Chrome，不加入应用 requirements。命令：

```powershell
work/.venv/Scripts/python.exe -m scripts.verify_stage2_browser --output work/stage2-browser-acceptance
```

先启动正式库应用验证真实空状态和健康检查，再启动独立虚构库验证：共用字检索仍留在结果页、全部候选显示、手动选择人物、进入多条证据、区分本人自称与后世记载、无匹配、缺证据的直接链接 404。不会访问测试来源网址。

1440×1000、390×844、320×740 三种视口，共 **18 项页面布局检查**；各页 document.scrollWidth 等于视口宽度，无横向溢出、无 JavaScript 页面异常。搜索提交后定位到结果锚点；验收脚本等待现有平滑滚动完成后再检查位置。

截图中 formal-empty 为正式库真实空状态；TEST_ONLY 为独立测试库，不能当作历史资料。报告和截图在 docs/screenshots/stage2/；原始浏览器日志在 work/ 下对应验收目录。

## 正式数据库保护

正式库前后 SHA-256 相同：

```text
f4931941751bb5dd3c1c5c9b0244d59257b364e6d23ee2658dda9b10e587fac7
```

people、aliases、sources、evidences 仍各为 0。既有待审核采集文件没有导入、提升核对状态或计入公开检索。

## 人工事项与验证边界

功能 MVP 已完成；真实史料的公开命中仍待人工审核并导入真实 CSV。README 列明了四表必填字段、卷篇和 URL、状态、直接支持证据与叙述方式。证据不足的资料保持 pending/disputed，真实页面显示空状态。

手机与桌面验收使用 Windows Chrome 响应式视口，未在实体 iOS/Android、Safari 或 Firefox 中实测。没有进入阶段三的模糊匹配、属性组合筛选或复杂消歧。
