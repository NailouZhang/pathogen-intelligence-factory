# v6 检索引擎适配矩阵

| 来源 | 单身份入口 | 补充召回 | 日期与分页 | 凭据 |
|---|---|---|---|---|
| PubMed | 每个身份词独立 `[Title/Abstract]` | 限定缩写、分组 OR、分词 AND、研究方向 | EPDAT/PDAT/CRDT/EDAT，POST，retstart/retmax | NCBI_API_KEY 建议 |
| Europe PMC | 每个身份词独立 `TITLE_ABS` | 限定缩写、分组和分词 AND | FIRST_PDATE/CREATION_DATE，cursorMark | 无 |
| Semantic Scholar | 每个完整身份词独立纯文本 | bulk 原生 `+`/`|` 限定缩写，连字符归一 | publicationDateOrYear，token | Key 可选；无 Key 降速 |
| OpenAlex | 每个身份词分别 `search.exact` 和普通 `search` | 分组 OR 与限定缩写 | publication date filter，cursor | OPENALEX_API_KEY |
| Crossref | 每个身份词独立 `query.bibliographic` | 无 PubMed Boolean；本地主题闸门 | published/created/indexed，cursor | 无 Key；mailto 建议 |
| bioRxiv/medRxiv | 7 天窗口全部候选 | 本地身份过滤 | API 分页 | 无 |
| Google/Bing RSS | 单身份和短主题查询 | 疫情、临床防控、基因组 | RSS 窗口后本地日期过滤 | 无 |
| GDELT | 单身份与单层 OR | `-term` 低风险排除 | maxrecords/时间参数 | 无 |
| ReliefWeb | 单身份和 Boolean | 日期与机构结果 | API 分页 | 预批准 appname |
| WHO | 身份词搜索 | 落地页正文复核 | 本地日期解析 | 无 |

任何来源的查询字符串都由独立编译器生成，禁止把 PubMed 查询直接复制给其他引擎。
