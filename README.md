# Pathogen Intelligence Factory 17.4.1

私有主仓，负责21个Profile的排班、Canonical词库、文献与新闻检索、日期门、候选复核、确定性和保守LLM去重、证据补全、终审、双语分析、静态站、微信发布包、审计及跨仓同步。

## 本次修订

- CI真实检出Pages与私有Publisher后执行三仓契约测试；
- 文献LLM模糊去重要求候选组索引、`same_work=true`、置信度≥0.90和确定性佐证；
- 新闻模糊去重不再把同一事件的不同报道直接合并；
- LLM仅背景误判只能在强目标身份和独立证据下保守纠偏，N硬噪声不可覆盖；
- 21套派生词库统一为`2026.07-v17.4`；
- 11个实际提示词全部纳入消费者审计。

## 本地验证

```bash
python -m pytest
python scripts/validate_all_profiles.py
python scripts/validate_canonical_vocabularies.py --project-root . --output /tmp/canonical.json
python scripts/audit_vocabulary_consumers.py --project-root . --output /tmp/consumers.json
python scripts/audit_pipeline_logic.py --project-root . --output /tmp/pipeline.json
python scripts/audit_query_coverage.py --project-root . --output /tmp/queries.json
```

跨仓pytest必须设置真实仓库路径：

```bash
PAGES_REPO_DIR=/path/to/pathogen-intelligence-pages \
PUBLISHER_REPO_DIR=/path/to/pathogen-wechat-publisher \
python -m pytest
```
