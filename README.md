# Pathogen Intelligence Factory v17.4

私有主仓，负责21种病毒的文献/新闻检索、相关性判定、正文补全、双语分析、GitHub Pages静态站和微信公众号发布包生成。

## v17.4语义契约

每个 `config/vocabularies/<profile_id>/canonical_vocabulary.json` 是唯一语义真源，包含主题边界、五个核心检索概念及终审映射、目标/成员/疾病/限定实体、近邻排除、权威证据、翻译词典、验证案例和消费者契约。其他JSON由 `scripts/compile_canonical_views.py` 生成。

```bash
PYTHONPATH=src python scripts/validate_canonical_vocabularies.py --project-root . --output canonical-validation.json
PYTHONPATH=src python scripts/audit_vocabulary_consumers.py --project-root . --output consumer-audit.json
python -m pytest -q
```

## 离线演示

```bash
python scripts/run_daily.py \
  --profile marburg_virus \
  --output-dir /tmp/marburg-demo \
  --state-dir /tmp/marburg-demo/data/state \
  --demo
```

## 生产审计

`data/audit/display_continuity.json` 直接指出终审、证据、分析和展示四个阶段的最大下降点。翻译失败不再删除英文分析成功的主文献。
