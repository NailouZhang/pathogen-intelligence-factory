# 数据源与来源策略

## 1. 专业词库权威来源

每个病毒的来源由对应 `profiles/<profile_id>/seed.yaml` 固定声明。来源类型包括：

- ICTV 报告页：正式分类与分类单元；
- ViralZone：病毒学、基因组、蛋白、复制与宿主；
- NCBI Taxonomy/Bookshelf：补充分类或医学背景；
- WHO、CDC、中国疾控及其他国家级公共卫生机构：疾病、传播、临床、诊断、防控和监测。

程序不会使用搜索引擎寻找这些页面，也不会从搜索结果页自动选取 URL。

## 2. 学术检索来源

- PubMed E-utilities；
- Europe PMC REST；
- Crossref REST；
- Semantic Scholar Academic Graph；
- OpenAlex Works；
- bioRxiv；
- medRxiv。

每个来源使用由同一严格 profile 编译的数据库专用查询，而不是将一个 PubMed 查询原样复制到所有来源。

## 3. 摘要与开放全文补充

- PubMed XML 多段摘要；
- Europe PMC metadata/fullTextXML；
- PMC BioC；
- Crossref TDM/全文链接；
- Semantic Scholar/OpenAlex 开放 PDF；
- DOI 落地页公开元数据和正文；
- 公共 PDF 临时解析。

没有摘要或正文时只展示可验证元数据，不编造研究结果。

## 4. 新闻来源

- WHO、CDC、国家级卫生与疾控机构；
- ReliefWeb；
- GDELT；
- Google News RSS；
- Bing News RSS；
- 大学、医院和公共卫生实验室发布页。

聚合器只负责发现，不视为最终权威来源。卡片保留真实发布机构、落地 URL、正文获取状态和审计信息。

## 5. 失败隔离

每个来源独立记录状态。单一来源失败不会终止整个 profile；固定权威页面抓取失败会尝试缓存，学术或新闻适配器失败会记录在 `source_status` 中并继续其他来源。
