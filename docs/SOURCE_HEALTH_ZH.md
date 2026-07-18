# v9 数据源健康、检索漏斗和输出数量审计

每次运行生成 `data/audit/source_status.json`、`query_plan.json`、`anchor_coverage.json`、`relevance_review.json`、`retrieval_funnel.json`、`papers.jsonl` 和 `news.jsonl`。

重点检查：

- `after_final_gate`：相关性复核后候选数量；
- `selected_for_content_enrichment`：进入 50+30 补位池的数量；
- `content_rejected`：内容补全失败数量；
- `translation_rejected`：翻译不就绪数量；
- `displayed`：最终页面数量；
- `paper_ready_pool`、`news_ready_pool`：最终选取前的就绪池。

当就绪池不少于 50 而 displayed 小于 50 时，测试必须失败。新闻正文审计记录 attempted_urls、resolved_url、content_status、content_method、content_length、标题正文相似度和错误原因。
