# 第一阶段验收记录

当前阶段二验收见 [stage2_verification.md](stage2_verification.md)。以下保留第一阶段的历史运行记录。

验证日期：2026-10-15（Asia/Shanghai）。没有执行后续阶段开发。

## 环境与实际命令

环境：Windows、Python 3.12.10。项目根目录为 `outputs/guji-id/`，本次使用工作区中的工具虚拟环境 `work/guji-venv/`，没有把虚拟环境打包进交付物。

在工作区根目录执行过：

```powershell
python --version
python -m venv work/guji-venv
.\work\guji-venv\Scripts\python.exe -m pip install fastapi uvicorn Jinja2 httpx pytest
```

在 `outputs/guji-id/` 执行过：

```powershell
..\..\work\guji-venv\Scripts\python.exe -m pytest -q
..\..\work\guji-venv\Scripts\python.exe -m scripts.init_db --db data/guji.db
..\..\work\guji-venv\Scripts\python.exe -m scripts.import_data --input-dir data/templates --db data/guji.db
..\..\work\guji-venv\Scripts\python.exe -m pip check
..\..\work\guji-venv\Scripts\python.exe -m compileall -q app scripts tests
..\..\work\guji-venv\Scripts\python.exe -m pip freeze --exclude playwright --exclude pyee --exclude greenlet
```

`pip freeze` 的项目相关结果保存为 `requirements-lock.txt`。另按固定依赖清单执行了 `pip install -r requirements-dev.txt`，确认所装版本一致。Playwright、pyee、greenlet 只用于本次浏览器验证，不是项目运行或 pytest 依赖，不放入项目锁定清单。

## 自动测试结果

初次交付的最终 `python -m pytest -q`：**69 passed，1 warning，0 failed，0 skipped**。当时测试临时目录为 `.pytest_cache/tmp/`；迁移后已修正为独立的 `.pytest_tmp/`，见下方迁移复测记录。测试数据不放入正式 data/ 或交付压缩包。

| 范围 | 结果 |
| --- | --- |
| 建表幂等、四张表、外键每连接开启、完整性 | 通过 |
| 四种状态、空值、年份格式与顺序、约束拒绝非法数据 | 通过 |
| 同名不同 ID、一人多别称、一别称多证据、删除引用保护 | 通过 |
| 原名保留、保守规范化、规范化名不唯一 | 通过 |
| 四份 CSV、UTF-8 BOM、任意列顺序、多行原文 | 通过 |
| 缺文件、错误/重复表头、行宽、编码与引号错误 | 通过 |
| 必填字段、枚举、URL、重复 ID、外键缺失 | 通过 |
| 重复导入跳过、冲突不覆盖、整批回滚且保留已有数据 | 通过 |
| 已核对关系需要已核对直接支持证据和来源定位 | 通过 |
| 待核对/存疑可不附证据、不会自动提升状态 | 通过 |
| 应用启动、首页实际统计、静态资源、四模板下载、健康检查 | 通过 |
| HTTP 404/405/422/500/503 与敏感内部错误不回显 | 通过 |
| CLI 建库/导入/重复导入/错误退出码、环境变量数据库路径 | 通过 |
| 测试临时数据库与独立应用实例隔离 | 通过 |

第三方警告：Starlette 1.8.0 提示 TestClient 使用 httpx 已弃用，未来建议 httpx2。当前固定版本实际执行成功，没有屏蔽警告，也没有宣称未来版本兼容。

`compileall` 通过（退出码 0）。`pip check` 输出 `No broken requirements found.`。空模板导入成功，四表的 inserted/skipped 都为 0。

## 实际 HTTP 和浏览器检查

在工作区执行了单次验证脚本：

```powershell
.\work\guji-venv\Scripts\python.exe -m pip install playwright
.\work\guji-venv\Scripts\python.exe work/verify_running_app.py
```

脚本用当前虚拟环境 Python 启动真实 Uvicorn 子进程，相当于执行以下命令，使用空闲回环端口 52500，并显式指定正式空库：

```powershell
python -m uvicorn app.main:app --host 127.0.0.1 --port 52500
```

检查后已关闭该 Uvicorn 子进程。本次没有保留后台服务；用户启动方式见 README。

以下 10 个地址实际 HTTP 状态均为 200：`/`、`/health`、`/static/styles.css`、`/static/app.js`、四个 `/templates/{table}.csv`、`/openapi.json`、`/docs`。仅验证 `/docs` HTTP 可返回，未验证外部 CDN 的 Swagger UI 交互。

使用本机 Microsoft Edge 无界面模式，分别以 1440×1100 桌面视口、390×844 手机宽度检查：

- 页面空数据提示可见，页面宽度不超过视口，没有横向溢出。
- 点击健康检查按钮，显示应用与数据库连接正常。
- 主动阻断健康检查网络请求，显示失败提示，按钮恢复可用。
- 点击人物模板链接，真实下载文件名为 people.csv。
- JavaScript pageerror 事件数量为 0。
- 保存完整页面截图，并实际查看桌面和手机截图。

截图：[桌面首页](screenshots/homepage-desktop.png)、[手机宽度首页](screenshots/homepage-mobile.png)。

浏览器验证过程中最初两次未完成：首次浏览器尝试使用受限的系统临时目录；将临时目录显式放入工作区后可启动。之后验证脚本按包含空格的可访问名称查找下载链接超时；改为定位实际下载 URL 后通过。两项均为一次性验证工具配置问题，未改动应用行为，没有将失败轮次记为通过。

正式 `data/guji.db` 最终核查：people=0，aliases=0，sources=0，evidences=0；`PRAGMA integrity_check` 返回 `ok`。所有虚构记录仅存在于 pytest 临时目录，本阶段未导入真实史料，也未生成伪造史料。

## 遗留和验证边界

- 无未通过或因环境而跳过的项目自动测试；仅保留上述第三方弃用警告。
- macOS/Linux、其他 Python 版本和移动端真实设备未实测；手机检查使用桌面 Edge 的窄视口，不等同于真实手机浏览器。
- URL 只校验格式，未访问或核实史料页面；无正式数据，因此未做真实史料内容验收。
- 导入器是追加式，不提供审核 UI、更新或迁移。直接手工 SQL 能绕过导入器的 URL、规范化与跨表状态检查。
- 检索、人物详情与证据展示、同名候选和 RapidFuzz 未开发，属于后续阶段。

## 本次新增文件

没有旧代码可修改，全部为新增。文件分工如下：

- `app/main.py`、`app/__init__.py`：应用工厂、路由、启动和异常处理。
- `app/db.py`、`app/schema.sql`、`app/normalization.py`、`app/importer.py`：连接、建表、名称清理、导入事务。
- `app/templates/base.html`、`app/templates/index.html`：中文首页与基础模板。
- `app/static/styles.css`、`app/static/app.js`：响应式样式与健康检查交互。
- `scripts/__init__.py`、`scripts/init_db.py`、`scripts/import_data.py`：命令行入口。
- `data/.gitkeep`、`data/templates/people.csv`、`aliases.csv`、`sources.csv`、`evidences.csv`：数据目录和四模板。运行时生成空库 `data/guji.db`，打包时不包含数据库文件，解压后自动建库。
- `tests/conftest.py`、`test_database.py`、`test_importer.py`、`test_app.py`、`test_cli.py`：严格隔离的虚构夹具和测试。
- `README.md`、`docs/data_dictionary.md`、`docs/prompt_log.md`、`docs/verification.md`、`docs/screenshots/`：运行说明、字典、迭代和验收记录、截图。
- `.gitignore`、`pytest.ini`、`requirements.txt`、`requirements-dev.txt`、`requirements-lock.txt`：忽略规则、测试配置和固定依赖。

## 2026-10-15 · 新目录迁移复测

当前固定项目根目录：`D:\Projects\移动信息化小组作业\Guji ID`。上方旧目录及原始命令保留为首次交付的历史记录。

- 目标目录检查为空后，复制 35 个项目文件（含空正式数据库），逐文件 SHA-256 校验一致；不复制旧的 Python/pytest 缓存。
- 新增项目 AGENTS.md 记录用户的固定位置要求，后续源码、文档、测试与交付都在该目录维护。
- 首次复测出现 1 passed / 68 setup errors：原 `.pytest_cache/tmp` 配置在未创建父目录时失败。改为项目下的 `.pytest_tmp`，加入 .gitignore 并同步 README 后重新运行，最终 **69 passed，1 warning，0 failed，0 skipped**，耗时 2.24 秒。警告仍为已记录的第三方 TestClient 弃用警告。
- 实际在新项目根目录执行：`& 'C:\Users\LENOVO\Documents\Codex\2026-10-15\vibe-coding-guji-id-10-codex\work\guji-venv\Scripts\python.exe' -m pytest -q`。本次复用原有工具虚拟环境验证新目录代码；虚拟环境没有复制，独立安装方式仍按 README 执行。
- 正式数据库复制后核查仍为四表各 0 条，SQLite integrity_check 为 ok；没有导入测试记录。
- 更新后的交付包保存为本项目 `outputs/Guji-ID-stage1.zip`，排除数据库、虚拟环境及测试运行缓存。历史截图对应同一应用界面，此次未重复截图或声称重新进行浏览器测试。
- 仍停留在第一阶段。

