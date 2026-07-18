# v7 检索引擎适配矩阵

| 来源 | 输入形式 | 日期与分页 | v7 请求预算 | 说明 |
|---|---|---|---:|---|
| PubMed E-utilities | 每个核心概念一个短查询，不硬编码数据库外语法 | EPDAT/PDAT/CRDT/EDAT；retstart/retmax；长查询 POST | 5 个概念，每概念最多 250，合并最多 2000 PMID | 利用 Automatic Term Mapping，后置 Python 严格复核 |
| Europe PMC | 单一自然语言概念，不强加字段标签或复杂 Boolean | FIRST_PDATE/CREATION_DATE；cursorMark | 5 个概念，每概念最多 250 | 充分使用自由文本扩展，精度由后置复核控制 |
| Crossref | 简短 `query.bibliographic` | publication 与 indexed 两个时间通道 | 5 个概念 × 2 通道，每通道最多 80 | 不传入 AND/OR/NOT 长表达式 |
| Semantic Scholar | 去除不稳定标点的短文本 | publicationDateOrYear；bulk token | 5 个概念，每概念最多 100；无 Key 时匿名降速 | 当前 Key 可选，失败不阻断主链 |
| OpenAlex | 普通 `search` | publication date filter；cursor；per_page | 5 个概念，每概念最多 100 | 需要 OPENALEX_API_KEY，依靠词干和全文搜索扩展召回 |
| bioRxiv/medRxiv | 直接拉取 7 天窗口元数据 | API 游标 | 每服务器最多 1200 | 本地 Python 富词复核，不增加查询词 |
| Google News RSS | 英文和中文自然短语 | RSS 自带时间排序，后置日期过滤 | 5 英文 + 5 中文 | 聚合补充源 |
| Bing News RSS | 英文和中文自然短语 | RSS 后置日期过滤 | 5 英文 + 5 中文 | 聚合补充源 |
| GDELT | 每个英文概念一个短语 | startdatetime/enddatetime | 5 个概念 | 避免嵌套布尔表达式 |
| ReliefWeb | 每个英文概念一个查询 | API 日期过滤 | 5 个概念 | `appname=wiv-virology-literature-tracker-42x` 审核前记录 pending/skipped |
| WHO 搜索 | 每个英文概念搜索候选页面 | 页面日期与正文后置判断 | 最多 5 个概念 | 辅助来源，页面结构变化会记录健康状态 |

## 失败策略

单个提供者失败时，记录来源、查询、HTTP 错误、页数和已获得数量。只有全部主要学术来源均失败或跳过时，profile 才整体失败。成功但 0 条与接口失败分开记录。
