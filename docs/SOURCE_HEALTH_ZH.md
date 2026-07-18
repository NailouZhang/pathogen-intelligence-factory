# v8 数据源健康、核心概念覆盖和检索漏斗

每次运行输出：

```text
data/audit/query_plan.json
data/audit/source_status.json
data/audit/anchor_coverage.json
data/audit/relevance_review.json
data/audit/retrieval_funnel.json
```

## source_status.json

区分：

- `healthy/success`：接口成功并返回记录；
- `empty`：接口成功但 7 天内为 0；
- `degraded`：部分查询成功、部分失败；
- `skipped`：缺少可选 Key 或 ReliefWeb 仍待审核；
- `failed`：网络、认证、查询或解析失败。

## anchor_coverage.json

v8 中的“锚点覆盖”指 5 个核心检索概念在 PubMed、Europe PMC、Crossref、Semantic Scholar、OpenAlex 和新闻通道中的执行与返回数量，不再表示完整富词库逐词查询。

## retrieval_funnel.json

记录：

```text
raw
→ after_window
→ after_candidate_gate
→ after_dedup
→ after_final_gate
→ displayed
```

全文和新闻正文补全只发生在 `displayed` 集合。

离线审计：

```bash
python scripts/audit_query_coverage.py \
  --output /tmp/query-coverage-v8.json
```
