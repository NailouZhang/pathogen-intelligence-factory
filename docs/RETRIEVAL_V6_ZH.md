# v6 检索架构：逐锚点召回与数据库专属适配

## 1. 基本原则

初始检索只允许使用三类身份入口：

1. 可独立证明主题身份的完整病毒名、正式名称和高特异疾病名；
2. 人工白名单成员、型别、亚型或血清型；
3. 歧义缩写与必要上下文组成的限定片段。

蛋白、基因、宿主、媒介、普通症状、药物、疫苗、科名和属名不允许独立成为身份入口。

每个安全身份词都建立逐词独立查询。组合 OR、分子、流行病学、临床和基因组查询用于补充发现与分类，不作为唯一入口。

## 2. 数据库专属编译

### PubMed

- 单锚点：`"Puumala virus"[Title/Abstract]`；
- 限定缩写：`PUUV[Title/Abstract] AND (virus[Title/Abstract] OR hantavirus[Title/Abstract])`；
- 分组查询、研究方向查询和分词 AND 兜底；
- EPDAT、PDAT、CRDT、EDAT 组成 7 天窗口；
- 长查询使用 POST，`retstart/retmax` 分页，每批 100 个 PMID EFetch。

### Europe PMC

- 单锚点：`TITLE_ABS:"Puumala virus"`；
- 限定缩写与 `TITLE_ABS`；
- `FIRST_PDATE` 与 `CREATION_DATE`；
- `cursorMark` 分页。

### Semantic Scholar

- 完整身份词逐词查询；
- 连字符归一，例如 `SARS-CoV-2` 生成 `SARS CoV 2`；
- bulk search 的限定缩写采用 `ABBR +(context1 | context2)`；
- 不发送 PubMed 字段标签或日期表达式；
- 无 API Key 时执行全部编译查询，但每条请求保守取数并增加间隔；来源审计会明确标记匿名模式和限流失败。

### OpenAlex

- 每个身份词分别执行 `search.exact` 与普通 `search`；
- 普通通道补充标点、词干和词序变化；
- 另保留少量分组 OR 和限定缩写查询；
- 使用 API Key、发布日期 filter、`per_page` 和 cursor。

### Crossref

- 不发送 Boolean 表达式；
- 每个身份词独立作为 `query.bibliographic`；
- 分别检查 publication、created 和 indexed 三个时间通道；
- 合并后按 DOI、PMID 和标题去重，再由本地主题闸门判断。

### bioRxiv / medRxiv

- 获取完整 7 天窗口候选；
- 本地逐词身份闸门过滤；
- 不依赖服务器端复杂 Boolean。

## 3. 新闻适配

- Google/Bing RSS：逐身份词、疫情、临床防控、基因组三类短查询；
- GDELT：单层 OR 组和 `-term` 排除，不使用 PubMed `NOT (...)`；
- ReliefWeb：独立 Boolean 和 URL 参数 `appname`；
- WHO：候选搜索后抓取落地页正文；
- 新闻标题泛化时，只要来自身份查询，先获取正文再做最终拒绝。

## 4. 召回保护

- 每个身份词有独立查询，防止热门成员占满分组查询的分页额度；
- 受限定缩写、历史名、标点变体和分词 AND 作为补充；
- 候选阶段只删除明确上下文噪声或排除实体；
- 摘要和新闻正文补全后重新判断；
- PubMed 与 Europe PMC 7 天均为 0 时执行 90 天 count-only 单锚点探针；
- `anchor_coverage.json` 记录每个身份词在各来源的计划、执行和命中数量。
