# Pathogen Intelligence Factory v7 — 21 病毒精简检索、富词复核与 Top-50 内容补全

公开仓库：`NailouZhang/pathogen-intelligence-factory`

本仓库每天北京时间 02:00 按顺序处理 3 个病毒主题，一周覆盖 21 个主题。每个主题检索过去 7 天的学术文献与新闻，完成双语分析、GitHub Pages 展示，并为私有本地 Runner 生成 `wechat-package/v2`。

## v7 的核心策略

```text
固定权威网页与人工主题边界
→ 每个病毒最多 5 个有区分度的核心检索概念
→ 各数据库专属、简短、宽松查询
→ 汇总全部元数据与数据库自带摘要
→ Python 对 100% 候选进行富词相关性复核
→ Gemini/Groq 只处理 Python 无法可靠判定的边界记录
→ DOI/PMID/标题/作者/年份/URL 多层去重
→ 依据相关性、来源收敛、研究设计、热点与时效排序
→ 选取最多 50 篇文献与 50 条新闻
→ 只对最终展示集合抓取合法开放全文或新闻正文
→ 双语翻译、五要素分析、综合综述
→ GitHub Pages + 微信发布包
```

与 v6 相比，v7 不再为每个身份词、成员名、缩写和研究方向生成数百条查询。富词库仍完整保留，但主要用于检索后的 Python/LLM 复核。每个 profile 对九类查询通道各编译 5 条概念，总审计计划为 45 条；实际接口请求由各提供者分页和语言通道决定。

## 为什么减少检索词不会放松主题边界

- 初始查询采用常见、重要、热点且与目标病毒直接相关的短语，利用 PubMed Automatic Term Mapping、Semantic Scholar/OpenAlex 词干和全文搜索等提供者能力扩大召回。
- 所有候选仍由完整身份锚点、成员白名单、限定缩写、疾病、上下文词和排除实体进行 Python 复核。
- 明确相关和明确无关记录由 Python 决定；只有边界记录进入 Gemini/Groq 紧凑证据复核。
- 全文、PDF 和新闻正文只在 Top-50 选择之后抓取，不再为数百条候选浪费网络时间。

## 每周调度

`config/weekly_virus_schedule.yaml` 定义 7 天 × 每天 3 个 profile。YAML 中的顺序就是实际执行顺序。

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

完整说明见：

- `docs/ARCHITECTURE_V7_ZH.md`
- `docs/LEAN_RETRIEVAL_V7_ZH.md`
- `docs/PROVIDER_ADAPTER_MATRIX_V7_ZH.md`
- `docs/RELEVANCE_DEDUP_RANKING_V7_ZH.md`
- `docs/LEGAL_FULLTEXT_V7_ZH.md`
- `docs/LLM_AND_TOKEN_V7_ZH.md`
- `docs/CREDENTIALS_V7_ZH.md`
- `docs/INSTALL_ZH.md`
- `docs/GITHUB_UPDATE_PUBLIC_ZH.md`
- `docs/RUNBOOK_V7_ZH.md`
