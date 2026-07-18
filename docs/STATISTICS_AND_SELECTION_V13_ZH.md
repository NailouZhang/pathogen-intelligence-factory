# 统计口径与 Top-N 展示选择 v13

## 1. 为什么“相关性通过”不等于“最终展示”

公开仓按以下顺序记录文献和新闻数量：

1. `raw`：数据库或新闻源返回的原始记录数；
2. `after_window`：真实发表日期或新闻发布时间位于报告窗口内；
3. `after_candidate_gate`：通过低成本候选门禁；
4. `after_dedup`：跨来源身份去重后；
5. `after_final_gate`：通过全候选相关性复核；
6. `ready_before_top_n`：正文、正文身份/主题、结构化分析和翻译门禁后，实际可展示的记录；
7. `top_n_limit`：`PIF_MAX_PAPERS` 或 `PIF_MAX_NEWS`；
8. `top_n_excluded`：合格但因页面篇幅限制未展示的数量；
9. `displayed`：网页和公众号最终展示数量。

因此：

```text
相关性复核通过 228
→ 内容与翻译门禁后可展示 207
→ 按优先级、证据强度、时效性和来源质量排序
→ PIF_MAX_PAPERS=50
→ 展示前50篇
```

不是“丢失178篇”，也不是把其余记录判定为不相关。

## 2. 审计文件

每次运行生成：

```text
data/audit/retrieval_funnel.json
data/audit/display_selection.json
data/audit/eligible_papers.jsonl
data/audit/eligible_news.jsonl
```

`eligible_*.jsonl` 包含全部 Top-N 前可展示记录的：

- 稳定ID；
- 标题和日期；
- 优先级等级；
- 质量分；
- 排序原因；
- `display_rank`；
- `selected_for_display`。

这些文件不保存完整全文，避免审计文件膨胀。

## 3. 排序依据

文献和新闻最终排序统一使用：

```text
priority_tier
quality_score
真实发表日期/新闻发布日期
标题稳定排序
```

`quality_score` 综合：

- 病原相关性；
- 数据源可信度；
- 多来源一致性；
- 证据是否完整；
- 研究设计或官方新闻来源；
- 时效性；
- 主题重要性。

## 4. 展示数量配置

GitHub Actions的日程表可为每个运行日设置最大文献和新闻数。Python最终读取：

```text
PIF_MAX_PAPERS
PIF_MAX_NEWS
```

改变这两个值只改变展示篇幅，不改变前面的日期、相关性、正文身份和分析质量门禁。
