# Guji ID 使用、数据审核与课堂演示说明

本说明对应 2026-10-17 第五阶段交付。固定项目根目录为 `D:\Projects\移动信息化小组作业\Guji ID`。正式数据为 10 人、17 条称呼关系、10 条来源、17 条证据。软件没有联网问答或自动裁决身份功能。

## 1. 系统功能

|操作|页面与实际行为|
|---|---|
|按姓名检索|首页输入“戴胄”“苏轼”或“王珪”，匹配已核对的规范姓名或姓名关系|
|反向查人物|输入字“景仁”“叔玠”“禹玉”或谥号“文忠”，返回独立人物|
|查看多个候选|多个 Person ID 全部显示，明确提示不能仅靠同一称呼确定身份|
|比较身份|卡片展示朝代、籍贯、生卒等可选字段；没有单独核实的字段显示“暂无已核实信息”；另展示已批准原文里的身份线索及出处|
|查看人物档案|点击“查看人物档案”，只列该人物已批准且有直接支持证据的称呼|
|查看证据|点击称呼或“查阅这条称呼的证据”，查看文献、卷篇、原文、修订链接、上下文、材料性质|
|阅读与引用|展开必要上下文；支持时复制原文，拒绝剪贴板权限时给出手动选取提示；打印时隐藏交互控件|
|异常处理|无结果、未公开关系、输入过长、服务异常有明确提示；机器请求保留 JSON 错误格式|

搜索采用 NFC 与首尾空白清理后的精确匹配，不做自动简繁转换、不补异体、不模糊猜测。当前正式批次包含姓名、字、谥号，**没有获批的“号”记录**：按号查询与展示机制已有隔离测试，但课堂不能声称当前正式库已有号类示例。

## 2. 数据来源与审核方法

正式证据来自前期依法获取并保存的维基文库正史转录，包括《舊唐書》《新唐書》《宋史》。每条证据保留卷次、篇目、固定 `oldid` 修订链接、实际原文，以及内部审计中的响应/正文哈希和字符偏移。线上来源使用固定修订链接；本轮未重新联网获取史料，也未新调用付费模型。

史料原件、转录文本和模型判断是不同层次。已存响应与 CSV 通过程序核对，只能证明保存内容和定位一致；DeepSeek 的既有 32 条意见只作辅助参考。批准来自用户在本聊天的明确授权，Codex 代录到独立新目录，没有伪造用户手填 CSV 或逐条浏览日志。

用户原话及批准范围见 `outputs/stage5-release/human_approval/user_authorization.json`：以阶段4.5推荐10人17条为本次精确范围。三个原有争议任务、旧23条未重核关系、额外暂缓及候补记录均未入库。原阶段4/4.5材料保持历史状态，不回写批准。

完整审核链：

```text
候选CSV → 已存史料结构检查 → 既有AI辅助意见 → 用户明确批准精确关系
        → 封存材料哈希与抽查/风险确认 → 备份与dry-run → 事务导入
```

人物可选字段原先只是候选提示，本轮未将它们自动升级成已核实身份字段。结果页的“史料中的身份线索”直接引用已批准证据，不把程序解析的籍贯文字写回人物字段；不从书名推断数据库朝代。

## 3. 人物消歧原理

人物以 `person_id` 区分；姓名不唯一。别称按 `(person_id, alias_name, alias_type)` 建独立关系，证据通过 `alias_id` 关联到具体人物，再引用来源。

```mermaid
flowchart LR
    Q[输入姓名或称呼] --> N[保守规范化与精确匹配]
    N --> P[保留所有独立 Person ID]
    P --> C[比较已批准原文及独立出处]
    C --> A[进入人物档案与具体称呼]
    A --> E[追溯古籍原文和修订链接]
```

“文忠”不是唯一身份标识：苏轼《宋史》卷338与欧阳修《宋史》卷319分别有赠谥原文。检索返回两人，不能自动合并。

王珪的两个 Person ID：

|史传中的独立传主|Person ID|本批已批准关系及区分文字|
|---|---|---|
|唐代王珪，《舊唐書》卷70|P-D-470d41f21e8cab39f12341ed|字“叔玠”；原文“太原祁人也”|
|宋代王珪，《宋史》卷312|P-D-46c39186c9a0fa0d8177b557|姓名“王珪”、字“禹玉”；原文“成都華陽人，後徙舒”|

两人的结构化朝代与籍贯字段仍空，但可以比较获批的原文和书卷。唐王珪被暂缓的 `name` 别称行未导入；其规范姓名属于已批准人物身份，所以姓名检索仍可找到该人物，不等于该暂缓关系也获批。

## 4. 正式入库、备份与回滚

本次已完成入库，不要为启动应用重新操作。正式库是 `data/guji.db`，批次 ID 为 `VB-c32955de8b1a0cd7`，实际批准为其中17条关系子集。

|记录|位置|
|---|---|
|用户授权与精确决定|outputs/stage5-release/human_approval/|
|明确导入清单|human_approval/import_manifest.json|
|导入前备份、原库统计和备份哈希|outputs/stage5-release/pre_import_backup.json|
|最早备份文件|outputs/stage5-release/database_backups/|
|预演报告|outputs/stage5-release/pre_import_dry_run/dry_run.json|
|已批准四表、审计和额外自动备份|outputs/stage5-release/approved_export/|
|首次与重复入库日志|first_import_result.json、repeat_import_result.json|
|正式库验收|outputs/stage5-release/import_acceptance.json|

正式入口仍为 `scripts.import_verified_batch`。它先校验明确批准及审核包哈希，再重建精确四表并比对审计，检查外键、状态和增量冲突。写入前使用 SQLite backup 并检查完整性，事务内写入，失败全部回滚。默认库及环境变量配置的正式库拒绝通用 CSV 绕过；仅有 AI 通过不能写入正式库。

如需对本次已批准批次验证幂等，必须保留原批准和导出路径：

```powershell
work/.venv/Scripts/python.exe -m scripts.import_verified_batch import --batch-dir data/verifications/stage4-tang-song-20-reviewed --approval outputs/stage5-release/human_approval/approval.json --export-dir outputs/stage5-release/approved_export --db data/guji.db
```

本次已实际重复一次，四表零新增。以后重放会新增备份文件，但不应新增数据。任何扩围、改史料或补字段都需独立审核，不能修改旧导出文件后重算哈希冒充原批次。

事务失败自动回滚；若提交后人工决定恢复导入前状态，需要先停止服务及其他写入，再核对 `pre_import_backup.json` 的备份文件哈希和统计。以下 Python 是明确批准恢复后使用的示例，**本轮未在正式库执行**；它会移除本批正式数据，恢复到备份中的空库：

```python
import hashlib, json, sqlite3
from pathlib import Path

meta = json.loads(Path("outputs/stage5-release/pre_import_backup.json").read_text(encoding="utf-8"))
backup = Path(meta["backup"]["path"])
assert hashlib.sha256(backup.read_bytes()).hexdigest() == meta["backup"]["sha256"]
with sqlite3.connect(backup.resolve().as_uri() + "?mode=ro", uri=True) as saved:
    assert saved.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    with sqlite3.connect("data/guji.db") as target:
        saved.backup(target)
        assert target.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert target.execute("PRAGMA foreign_key_check").fetchall() == []
```

恢复后重新比较四表数量及检索结果并保存新的恢复日志。不要只复制一个运行中 SQLite 主文件而遗漏 WAL。本轮已有隔离恢复与强制事务失败测试；正式导入成功，未做生产回滚。

## 5. 运行、组员复现和部署

本机已有环境可直接启动：

```powershell
Set-Location -LiteralPath 'D:\Projects\移动信息化小组作业\Guji ID'
work/.venv/Scripts/python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

访问 [首页](http://127.0.0.1:8000/) 与 [健康检查](http://127.0.0.1:8000/health)。健康响应 stage=5。源码不依赖 Node 或前端构建，无需安装庞大前端框架。

组员在新环境中用 Python 3.12 创建 `.venv`，安装 `requirements-dev.txt`；如果只运行应用，可用 `requirements.txt`。完整版本参考 `requirements-lock.txt`。然后将上述 Python 路径替换成 `.venv/Scripts/python.exe`。本轮未升级任何依赖。

数据库被 gitignore：交接时应同时交付源码、正式 `data/guji.db`、原史料及批准审计；仅克隆源码会自动建空库，不会自动导入人物。正式库若被放到其他路径，启动前设置 `GUJI_DB_PATH` 的绝对路径；所有写入继续走批准门槛。

课堂同局域网部署可将 host 改为 `0.0.0.0`，由组员使用主机局域网地址访问。该启动方式不是本轮已实测的公网部署；跨设备网络、防火墙和公网 HTTPS 需按实际环境单独检查。当前实测为 Windows 本地 HTTP、真实 Chrome/Edge，无公网发布或云服务配置。

## 6. 实际测试与截图

- 完整回归185项通过，含阶段4.5的174项与本阶段11项新回归实例；正式库专项另21项通过。
- Chrome155、Edge155，桌面1440×1000、触屏390×844及320×740窄屏；86项功能检查、102项布局检查通过，页面脚本错误0，所有视口无横向溢出。
- 无 JavaScript 的姓名→档案→原文路径仍可使用。复制失败有提示，健康检查失败可重试；异常网络响应为浏览器层模拟，不是破坏正式库。
- 浏览器验收前后正式库哈希一致；验收过程不注入夹具或写生产数据。所有真实关系的来源对应检查以正式库为对象。
- 编译检查及依赖检查通过。保留既有 Starlette TestClient/httpx 弃用警告；它没有被隐瞒或算作失败。

重跑时使用新的临时目录：

```powershell
work/.venv/Scripts/python.exe -m pytest -q --basetemp=work/pytest-stage5-new
work/.venv/Scripts/python.exe -m pytest tests/formal_acceptance.py -q --basetemp=work/pytest-formal-new
work/.venv/Scripts/python.exe -m scripts.verify_stage5 --output work/stage5-browser-new
```

浏览器脚本需要 Playwright 及已安装的 Chrome/Edge，不自动下载。截图和逐项 JSON 见 `outputs/stage5-release/browser-final/`，运行前的界面审计见 `ui_audit/`。没有实体手机、Safari或Firefox实测，不把两种 Chromium 浏览器等同跨引擎兼容性验证。

## 7. 课堂演示流程（约3分钟）

1. 启动应用，展示正式计数10/17/10/17，并点击“检查服务状态”。说明正式库不等于20人候选全集。
2. 搜“景仁”：反向命中岑文本，查看人物档案→字景仁→《舊唐書》原文及固定来源链接。
3. 搜“文忠”：看到欧阳修与苏轼两个候选，分别查看《宋史》卷319与卷338的赠谥句，说明共享谥号不合并人物。
4. 搜“王珪”：对比两个独立ID及“叔玠／太原祁”和“禹玉／成都華陽”摘录，进入各自证据，说明结构化朝代等字段尚待单独审核。
5. 在浏览器设备模式切换到390或320像素，重复搜索→人物→证据，展开上下文，复制原文；指出来源书卷及修订号。
6. 搜“青蓮居士”：显示无已核对匹配；不要为完成演示新增未批准的号类数据。也可直接访问唐王珪暂缓的姓名关系，验证404。

公开数据始终遵守“输入称呼→保留候选人物→比较身份线索→查看称呼关系→追溯古籍证据”；系统辅助查证，不替人做最终历史判断。

## 8. 已知局限与后续方向

本批数据小、类型覆盖不完整，38条关系仍未入库。韩琦、唐王珪姓名、刘允济的争议没有被整体授权改成模型通过。可选人物元数据尚空，今后补充需明确字段依据和批准范围。现有网络转录并不等于权威底本校勘，争议须查独立版本。

当前为精确检索与证据辅助区分，无自动身份打分或裁决，无模糊匹配、异体自动转换、组合属性筛选、后台账号、OCR、RAG或知识图谱。外部来源链接本轮只核对保存记录和格式，没有重新联网验证可达性。未来可按独立任务增加经批准的朝代/籍贯字段、更多称呼类型、无障碍及真实设备覆盖；本轮不启动扩容至50/100人或新付费核验。
