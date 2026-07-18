# Pathogen Intelligence Factory v6 — 21 病毒全锚点、高召回、高精度系统

公开仓库：`NailouZhang/pathogen-intelligence-factory`

本仓库每天北京时间 02:00 按顺序处理 3 个病毒主题，一周覆盖 21 个主题。每个主题检索过去 7 天的文献与新闻，依次完成：固定权威网页词库、数据库专属查询编译、所有安全身份锚点逐词独立检索、组合式补充检索、去重与内容补全、全候选 Python 复核、动态 Token 批次 LLM 复核、A/B/C 等级排序、最终 Top 50 双语与五要素分析、GitHub Pages、`wechat-package/v2` 和私有 Runner 发布。

## v6 的关键变化

- 不使用 Google CSE 自动寻找权威网页；21 个 profile 只使用人工确认的 ICTV、ViralZone、WHO、CDC 等固定来源。
- 每个 `safe_to_use_alone=true` 的完整身份词在 PubMed、Europe PMC、Semantic Scholar、OpenAlex、Crossref 和新闻查询中拥有独立入口，避免热门成员挤掉罕见成员。
- 分组 OR、分子、流行病学、临床和基因组查询只作为补充，不再承担唯一召回责任。
- 缩写只有与必要上下文组合后才可检索；蛋白、基因、宿主、症状、药物和疫苗不能独立证明病毒身份。
- Python 检查 100% 候选；LLM 以身份命中、上下文命中、排除命中和完整证据句构成紧凑包，按 Token 预算动态分批处理到队列为空。
- 不再设置“最多复核 80 篇”或“摘要前 2500 字符”限制；只有模型返回 `U` 的记录才升级为更完整的句子级证据。
- 只有最终展示的 Top 50 文献与 Top 50 新闻执行深度翻译、五要素和综合综述，避免对未展示候选浪费模型 Token。
- 当 PubMed 与 Europe PMC 的 7 天核心查询同时为 0 时，自动执行 90 天逐锚点 count-only 健康探针；探针结果只用于诊断，不进入日报。
- 每条最终记录具有确定性质量分和 A/B/C 优先级，不随机抽取。
- 每次输出来源健康、检索漏斗、单锚点覆盖和全量相关性复核审计。

## 每周调度

`config/weekly_virus_schedule.yaml` 定义 7 天 × 每天 3 个 profile。YAML 顺序就是实际执行顺序，后续可直接编辑。

## 主要输出

```text
output/
├── data/latest.json
├── data/audit/query_plan.json
├── data/audit/source_status.json
├── data/audit/anchor_coverage.json
├── data/audit/relevance_review.json
├── data/audit/retrieval_funnel.json
├── site/index.html
└── wechat-package/
    ├── manifest.json
    ├── article.html
    ├── cover.jpg
    └── images/
```

## 本地验证

```bash
python -m pip install -r requirements.txt
python scripts/validate_all_profiles.py
python scripts/audit_query_coverage.py
python scripts/check_credentials.py || true
python -m pytest -q
python -m compileall -q src scripts tests
```

完整说明见 `docs/INSTALL_ZH.md`、`docs/RETRIEVAL_V6_ZH.md`、`docs/FULL_CORPUS_REVIEW_V6_ZH.md`、`docs/CREDENTIALS_V6_ZH.md` 和 `docs/GITHUB_UPDATE_PUBLIC_ZH.md`。
