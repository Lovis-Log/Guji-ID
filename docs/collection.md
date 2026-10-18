# 轻量采集与人工审核导入

第四阶段已增加 [配置化批量采集与验收](stage4_verification.md)，入口为 `python -m scripts.collect_batch --config data/collection_configs/tang_song.json`。本文件下述五人结果保留为首次采集历史；当前累计候选为 20 人，人工批准/正式入库仍为 0。

本模块只使用 Python 标准库和已有 SQLite 导入器，不引入爬虫框架或多智能体系统。固定项目目录为 `D:\Projects\移动信息化小组作业\Guji ID`。不改变应用页面、数据库结构或开发阶段。

## 文件与运行方式

|文件|用途|
|---|---|
|`data/collection_seeds.csv`|五位种子、稳定且独立的 Person ID、查询姓名及异体提示|
|`app/collector.py`|公开 API、串行限速、原始响应、规则发现、正文定位、去重和报告|
|`scripts/collect_data.py`|在线采集或严格离线重放入口|
|`scripts/archive_responses.py`|归档此前已实际获取的 API 响应，不联网|
|`scripts/check_collection.py`|独立、只读的原始响应和证据链检查|
|`app/collection_review.py`|审核门槛、原文校验、四表导出和审核批次导入|
|`scripts/export_reviewed.py` / `scripts/import_reviewed.py`|人工审核后的操作入口|
|`tests/test_collection.py`|仅测试夹具：网络失败、身份隔离、误命中、审核链、篡改检查|

Python 3.12；采集和审核工具无新增第三方依赖。测试使用项目的 `requirements-dev.txt`。

```powershell
Set-Location -LiteralPath 'D:\Projects\移动信息化小组作业\Guji ID'

# 在线采集示例。网站恢复允许访问后再由操作者显式运行；本次限流后未再联网重试。
# 每次使用新输出目录，已有目录会被拒绝，不覆盖审核决定。
python -m scripts.collect_data --seeds data/collection_seeds.csv --output data/collections/new-run --interval 2 --timeout 15 --max-requests 90 --max-pages 8

# 当前交付可直接离线重现，不需要再次访问任何网站。
python -m scripts.collect_data --replay data/collections/2026-10-16-delivery --output work/replay-new --max-requests 90 --max-pages 8

# 复查交付的实际原始响应、文本、外键和偏移。
python -m scripts.check_collection --input-dir data/collections/2026-10-16-delivery
```

在线默认每次请求至少间隔 2 秒；超时 15 秒；最多 100 个请求、每人 10 页，均可在小范围内调整。无自动重试，HTTP 401/403/429、安全检查、网络失败及 maxlag 会停止本批对该主机的访问；404/缺页仅跳过该页。禁跟随 HTTP 重定向，不改域名或传输方式绕过限制。种子每批最多 10 人；本轮只采集指定的 5 位。

离线模式不联网：能找到的原始响应才会使用，缺缓存明确记为 `replay_missing`。此前 parse 响应若请求的 prop 较少，可以按实际请求 URL 和 page 字段复用；缺少分节列表时从真实 HTML 标题提取分节，不生成缺失正文。早期探测记录未保留 HTTP 响应头，归档元数据显式说明，获取时间依据当次成功写入文件的修改时间，不能误认为服务器返回的时间。

## 实际执行结果（2026-10-16，北京时间）

最终交付目录：`data/collections/2026-10-16-delivery/`。`run.json` 的 `mode=offline_replay`、`network_requests=0` 指这次整理不联网，原始资料确实来自此前的成功网络请求。

|项目|实际结果|
|---|---:|
|取得真实作者页的候选人物|5|
|称呼候选（人物×原始写法×类别去重）|25|
|其中姓名写法 / 非姓名称呼|6 / 19|
|取得可定位古籍正文的称呼|17（含 5 条姓名写法）|
|其中规则识别为明确关系表述|11|
|正文证据候选|22|
|缺古籍正文的称呼|8|
|重点审核：缺正文或没有明确关系表述|14|
|人工已核实 / 正式库新增|0 / 0|

五人均是待审核身份。姓名“歐陽修”和“歐陽脩”分别保留原始写法，属于同一候选 Person ID 的不同 name 记录，不算第六个人，也不做自动简繁或异体合并。22 条证据不等于 22 条不同称呼。

执行经过及边界：

1. 前期 API 探测实际取得五份作者页、四份史传响应，保存在原始归档中。
2. 首次完整在线脚本在 `2026-10-16-first/` 运行，取得苏轼、李白资料后，访问《贈子厚》时收到 HTTP 429，立即停止该主机。本次没有恢复在线访问、切换站点地址或反复重试。
3. 将已取得的探测响应和首轮响应归档，在本地进行提取与质量修订。交付结果为部分成功，不宣称五人完整在线采集成功。
4. 最终保留 24 份原始响应，合计 1,747,743 字节，包含成功资料和受限响应。22 条证据的响应哈希、HTML 派生正文、正文哈希、修订链接和原文字符偏移全部核验通过。
5. 从交付目录再次离线重放，三份候选 CSV 与交付逐字节一致。

`collection_log.csv` 区分：2 次实际失败/受限响应（ctext 安全检查、维基文库 429）；10 次离线缓存缺失。缓存缺失未产生网络请求。

## 实际取得的原文示例

以下仅展示待审核候选的真实摘录，不表示人工核实完成。

|人物 / 称呼|古籍原文片段|定位与原始链接|
|---|---|---|
|苏轼 / 子瞻|蘇軾，字子瞻，眉州眉山人。|[《宋史》卷338，蘇軾传](https://zh.wikisource.org/w/index.php?oldid=1458054)|
|苏轼 / 和仲|公諱軾，姓蘇氏，字子瞻，一字和仲，世家眉山。|[《欒城集》後集卷第二十二，亡兄子瞻端明墓誌銘](https://zh.wikisource.org/w/index.php?oldid=2346624)|
|李白 / 太白|李白字太白，山東人。|[《舊唐書》卷190下，李白传](https://zh.wikisource.org/w/index.php?oldid=2636533)|
|杜甫 / 子美|杜甫，字子美，本襄陽人，後徙河南鞏縣。|[《舊唐書》卷190下，杜甫传](https://zh.wikisource.org/w/index.php?oldid=2636533)|
|白居易 / 樂天|白居易，字樂天，太原人。|[《舊唐書》卷166，白居易传](https://zh.wikisource.org/w/index.php?oldid=2588799)|
|欧阳修 / 永叔|歐陽脩，字永叔，廬陵人。|[《宋史》卷319，歐陽脩传](https://zh.wikisource.org/w/index.php?oldid=1744467)|

实际文献还包括《新唐書》卷202、《全唐詩》卷216；后者的名称用例保留为弱证据，不能仅凭出现李白姓名确认其他别称。修订号固定页面文本；转录本仍须人工检查版本与校勘，不等同于影印底本确认。

## 来源状态

|平台|本次结果|
|---|---|
|维基文库|公开 Action API 实际成功；后遇 429 停止；剩余整理完全离线|
|中国哲学书电子化计划|API 文档页实际返回安全检查及禁止抓取提示；不抓正文，不实现未授权接口|
|识典古籍|实际取得 robots.txt，通用机器人 `Disallow: /`；未访问正文|
|搜韵|实际取得 robots.txt；未确认适用官方接口及许可，未抓正文，列为人工补查|

API 实现依据 [MediaWiki 官方访问礼仪](https://www.mediawiki.org/wiki/API:Etiquette)、[parse 文档](https://www.mediawiki.org/wiki/API:Parsing_wikitext)及[搜索文档](https://www.mediawiki.org/wiki/API:Search)。本项目只使用其公开读接口。保留来源、修订链接及页面历史署名入口；维基文库编辑文本许可参见[著作权说明](https://zh.wikisource.org/wiki/Wikisource:著作权)。

## 自动判断及人工重点事项

- 作者介绍页用于发现称呼线索，不存为古籍正文证据；真实传记分节中的显式标记可以补充候选。
- 只提取原页面真实段落，排除标题、目录、导航、脚注和授权模板；保留原始 HTML 响应供复核。
- `explicit_relation` 表示规则发现姓名与字的明确表述，仍是 pending。`contextual_relation` 表示传记语境支持但主语待核，`usage_relation=uncertain`；`mention_only` 仅表示名称出现，`usage_relation=mentions`。
- 传记说“自號/自稱”记录为“后世文献记载其自称”，不称作本人作品中的自称。
- 人物传记中的下一分节即关闭当前取材范围，包括子传。实际质量检查排除了把苏过的“斜川居士”归给苏轼，以及把“太白窟”当作李白别称用例的误命中。
- 缺正文：李白“青蓮居士”；杜甫“少陵野老、杜陵野老、杜陵布衣、杜拾遗、杜工部、诗圣”；欧阳修的原始写法“歐陽修”。后者已取得“歐陽脩”写法的史传，需人工确认写法对应关系。
- 重点核对苏轼与欧阳修共用“文忠”的谥号。二者保留不同 Person ID，不能按称呼合并。共用谥号不等同完整姓名相同。
- 针对五位人物，曾检索“杜甫 同名 明”“歐陽修 同名 明代”“李白 同名 宋朝 字”“苏轼 同名 明代”，未取得足以报告的完整姓名同名异人证据。此项尚未成功，不编造案例，不扩大种子人数；以后可另批补充独立身份。

人工补查入口和逐条原文、上下文、来源、卷篇、备注见三份 CSV、`report.md` 以及 `collection_log.csv`。

## 人工审核与 SQLite 导入

三份候选 CSV 使用 UTF-8 BOM，可在 Excel 中打开。不要把待审核文件直接交给旧 `scripts.import_data`；旧工具仍允许 pending 四表导入，保留其原有用途。采集模块的新正式导入入口为 `scripts.import_reviewed`。

人工复制整个交付目录到一个审核目录，保留 `raw/`、`text/`；编辑三份候选 CSV：

1. 人物、别称及相应证据都须设置 `review_status=通过`，填写真实的 `reviewer` 及带时区的 `reviewed_at`，如 `2026-10-17T10:00:00+08:00`。这是格式示例，未替任何真实记录填写。
2. 存疑设置 `review_status=存疑`，否定设置 `拒绝`，其余保持 `待审核`。候选文件原 `verification_status` 保持 pending，不能用改这个字段替代审核。
3. 通过的别称必须有至少一条人工通过的 `usage_relation=supports` 且 `evidence_nature=direct` 证据。人工核实了关系与上下文后，才可把规则初判的 uncertain/indirect 改为 supports/direct；不能仅为通过导入而修改。
4. 无法核实的字段留空；原文和字符偏移必须仍能对上已保存的真实响应。需要新增或更换原文时，应提供真实的新采集资料，不能手填假引文。

```powershell
# 先在审核副本中完成人工判断，再运行。此处 reviewed-copy 是操作者创建的完整审核目录。
python -m scripts.export_reviewed --input-dir data/collections/reviewed-copy --output-dir data/reviewed/approved-batch
python -m scripts.import_reviewed --input-dir data/reviewed/approved-batch --db data/guji.db
```

导出产生与现有模板字段完全一致的 `people.csv`、`aliases.csv`、`sources.csv`、`evidences.csv`，另附 `review_audit.json` 保存审核人与时间、输入目录、导出文件哈希。额外的 context、关系分类、获取日期、原始文件路径与哈希放入 evidences.notes JSON。原始 CSV 及响应目录应与审核导出共同留存。

审核导入会检查审计与文件哈希，仅接受 verified 批次，随后复用现有 `import_batch` 单事务写入，保持幂等及外键检查。审计字段记录操作者填写的决定，不提供审核人身份认证。没有审核通过链时不创建正式批次或写库。

## 验证状态

已实际成功：公开 API 获取、五人离线提取、四份要求的 CSV、原始响应留存、22 条证据溯源、24 份响应哈希、独立身份及共用谥号保留、离线重放一致性。

已实际验证拒绝：未审核交付调用 `export_reviewed` 返回退出码 1，提示没有人工通过的别称，未创建导出目录。正式 SQLite 的 people/aliases/sources/evidences 记录数仍全部为 0。

已实际受限/失败：ctext 安全检查、维基文库 HTTP 429。未测试/不宣称支持：ctext 授权 API、识典古籍正文、搜韵正文、真实人工审核后的正式库导入。审核后导入成功、幂等和拒绝不完整链已用明确标注的虚构测试夹具验证，未以测试数据填充正式数据库。

测试命令：

```powershell
# 本次已在项目 work/.venv 中安装 requirements-dev.txt。
work/.venv/Scripts/python.exe -m pytest -q --basetemp=work/pytest-check
```

当前共 93 项测试通过（含新增 24 项），1 条现有 FastAPI/Starlette TestClient 关于 httpx 的弃用警告。测试全程不访问真实文献网站，不把测试夹具当史料。
