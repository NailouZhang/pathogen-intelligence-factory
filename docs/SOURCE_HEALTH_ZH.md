# v6 数据源健康、锚点覆盖和复核审计

单次运行产生：

```text
data/audit/source_status.json
data/audit/query_plan.json
data/audit/anchor_coverage.json
data/audit/relevance_review.json
data/audit/retrieval_funnel.json
```

- `source_status.json`：区分 healthy、empty、degraded、skipped、failed；
- `anchor_coverage.json`：每个安全身份词在 PubMed、Europe PMC、Crossref、Semantic Scholar、OpenAlex 和新闻中的计划、执行和返回数量；
- `relevance_review.json`：Python 全候选数量、LLM/缓存/确定性回退方式，明确显示没有篇数和前缀字符限制；
- `retrieval_funnel.json`：raw → window → candidate → final → displayed；
- 7 天 PubMed 与 Europe PMC 同时为 0 时，`source_status.json` 还会出现 `90-day anchor probe`，仅作 count-only 诊断。

命令：

```bash
python scripts/check_source_health.py output/data/audit/source_status.json
python scripts/audit_query_coverage.py --output /tmp/query-coverage-v6.json
```
