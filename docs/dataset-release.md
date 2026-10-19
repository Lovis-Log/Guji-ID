# GuJi ID 公共数据制品

本文件记录可随代码部署的只读 SQLite 快照 `data/guji-public.db`。正式采集缓存、审批材料与可写工作库不进入仓库。

## 制品信息

| 项目 | 值 |
| --- | --- |
| 文件 | `data/guji-public.db` |
| 生成日期 | 2026-10-19 |
| 来源 | `data/guji.db` 的 SQLite 在线备份 |
| 大小 | 28,348,416 字节 |
| SHA-256 | `a345e71e17e929ca3861362a1a263a2fcf8c2a4de1ee8342307752741b07e83a` |
| 校验文件 | `data/guji-public.db.sha256` |
| 构建脚本 | `python -m scripts.build_public_db` |

重新生成：

```bash
python -m scripts.build_public_db
sha256sum -c data/guji-public.db.sha256
```

## 数据规模

| 表 | 行数 |
| --- | ---: |
| people | 500 |
| aliases | 1,306 |
| sources | 119 |
| evidences | 2,446 |
| classical_works | 16 |
| classical_chapters | 89 |
| classical_evidence_links | 682 |

## 路径清理

正式库的内部批次审计字段包含本机绝对路径：

- `auto_batches.backup_json`
- `auto_events.details_json`
- `quality_batches.backup_json`

构建脚本会把它们改写成可移植的 `<project>/...` 形式，共清理 31 行；不改变页面展示、证据或古籍正文。清理后的制品已通过：

- `PRAGMA integrity_check = ok`
- `PRAGMA foreign_key_check` 无结果
- 全库扫描无 Windows 绝对路径

由于内部备份路径已被改写，`quality_batches.after_fingerprint` 不再适合用于对公开制品做回滚比对；回滚审计以本地正式库和原始批次材料为准。

## 许可与署名

本制品包含古籍整理与人物数据，部署和展示时必须保留来源署名，且仅用于非商业用途：

- **CBDB（China Biographical Database）**：CC BY-NC-SA 4.0，非商业、署名、相同方式共享。
- **Kanripo（漢籍リポジトリ）**：CC BY-SA 4.0，固定 commit 的转录文本。
- **维基文库**：CC BY-SA 4.0，固定修订的转录文本。
- 站点页面已按记录显示来源、版本与许可链接；本文件作为仓库内的数据许可清单。

## 部署使用

```text
GUJI_DB_PATH=data/guji-public.db
```

数据库为只读业务快照；应用只执行查询。`init_db` 在启动时会执行幂等 schema 语句，因此在可写文件系统上运行正常。若部署到只读文件系统，应跳过 `init_db` 或先把制品复制到可写临时目录。

## 不包含的内容

- `data/external/` 原始采集缓存与压缩包
- 审批、回滚备份与本机绝对路径
- 未公开的测试夹具与临时 QA 数据库
