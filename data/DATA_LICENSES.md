# 数据署名和许可范围

CBDB来源：China Biographical Database (CBDB)，官方历史版本20250520；源自 [官方SQLite发布仓库](https://github.com/cbdb-project/cbdb_sqlite)链接的历史归档。获准使用范围依据 [CBDB官方许可条款](https://cbdb.hsites.harvard.edu/cbdb-data-licensing-terms)及[当前单机版下载说明](https://cbdb.hsites.harvard.edu/download-cbdb-standalone-database)核对。该版本派生结构化记录按 **CC BY-NC-SA 4.0** 使用、署名与共享，仅用于非商业课程项目。版本、源主键、哈希保留，不声称已经核验古籍原文。

Kanripo来源：Kanseki Repository（漢籍リポジトリ），[KR2a0032《宋史》](https://github.com/kanripo/KR2a0032)，固定提交见 `external/kanripo-stage6/texts.json`；[官方组织](https://github.com/kanripo)声明的转录文本许可为 **CC BY-SA 4.0**。来源页与正文标识WYG底本，原文保留不重写。

旧维基文库及其他已采集来源继续保留各自的固定修订、署名及适用许可。不同来源组件分别保留许可，不对混合数据库整体另行宣称一种替代许可。

`external/cbdb-stage6/` 的20260530缓存是未通过本次许可总量检查的本地评估材料，**未用于准入，也不纳入可分享的交付数据快照**。20261010仅保存发布元数据，没有采用最新SQLite数据。请勿把这些评估缓存当作本项目可发布数据集。

## AI 拟古绘像

`app/static/figure-media/` 下的十张 WebP 由项目方提供的 AI 拟古图片转码而来，原图位于 `guji-figure-originals/images/png/`。这些图片**不是古籍扫描件，也不是可核验史料**，不主张馆藏来源或历史真实性；页面统一标注“AI生成·拟古演绎，非真实古籍书影”。图像中的古文字与印章不进入古籍证据表，也不作为人物姓名、字号或原文的证明。人物映射见 `app/figure_media.json`，仅按正式库 `person_id` 与规范姓名核对绑定。
