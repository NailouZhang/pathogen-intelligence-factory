# v7 精简检索策略

## 1. 核心原则

初始检索不再把完整专业词库展开为数百条查询。每个病毒只维护最多 5 个互有区分度的核心概念。这 5 个概念通常覆盖：

1. 最常用的病毒或疾病总称；
2. 重点成员、型别、血清型或谱系；
3. 近期疫情或监测方向；
4. 临床、疫苗、治疗或预防热点；
5. 基因组、变异、传播或其他重点方向。

词组必须与目标病毒直接相关。不能把蛋白名、普通症状、宿主、媒介或药物作为没有病毒身份的独立查询。

## 2. 为什么不追求“查询列出所有别名”

同义全称、缩写、历史名称和成员名称往往返回高度重叠的集合。把它们全部分别送入每个数据库会增加：

- API 请求次数；
- 分页和重试；
- 新闻落地页重复抓取；
- 数据库限流；
- 后续去重成本；
- GitHub Actions 运行时间。

v7 将这些词保留在富词库中，用于检索后的严格复核，而不是全部用于前端召回。

## 3. 宽松不等于无边界

初始查询利用数据库自身的扩展能力：

- PubMed 使用短概念，让 Automatic Term Mapping 处理 MeSH、同义词和词形；
- Europe PMC 使用单一自然语言概念，让其自由文本检索扩展发挥作用；
- Semantic Scholar 使用简短文本，避免传入 PubMed 字段语法；
- OpenAlex 使用普通 `search`，利用词干和全文检索；
- Crossref 使用简短 `query.bibliographic`；
- 新闻使用自然语言短语。

召回后的 Python 规则使用完整富词库：

- 当前名称和历史名称；
- 白名单成员；
- 高特异疾病名称；
- 受限定缩写；
- 上下文词；
- 排除实体；
- 身份出现频次；
- 多检索概念和多提供者收敛。

因此，查询层适度宽松，判定层保持严格。

## 4. 每个 profile 的配置

`profiles/<profile_id>/seed.yaml` 中：

```yaml
search_strategy:
  schema_version: '1.1'
  max_concepts: 5
  provider_expansion_first: true
  deduplicate_semantic_equivalents: true
  concepts:
    - id: hantavirus
      scholarly: hantavirus
      news_en: hantavirus
      news_zh: 汉坦病毒
      role: umbrella
      priority: 1
```

`scholarly` 用于学术数据库，`news_en` 和 `news_zh` 用于新闻来源。`id` 用于来源收敛和审计。

## 5. 防止零结果

- 每个核心概念独立运行，避免一个过窄组合使全部查询为 0；
- PubMed 与 Europe PMC 7 天均为 0 时，运行 90 天 count-only 诊断；
- 90 天探针只判断查询健康，不把旧文献放进日报；
- `source_status.json` 区分 `healthy`、`empty`、`degraded`、`skipped` 和 `failed`；
- Semantic Scholar 无 Key 或 ReliefWeb 名称未审核不会被误报为“本周没有内容”。
