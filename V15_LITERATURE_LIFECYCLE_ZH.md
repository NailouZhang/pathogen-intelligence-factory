# v15.1文献生命周期与数据契约

## 1. 入口召回

每个Profile固定5个简单、独立、可直接检索的病毒身份词。周运行不得漂移。少量高度特异、未被五词覆盖的成员病毒通过 `controlled_supplemental_terms` 进入受控补充检索，并单独审计。

## 2. 初始规范化和日期门

各适配器统一输出DOI、PMID、PMCID、标题、作者、期刊和四类发表日期。首次计算：

```text
first_publication_date > online_date > published_date > print_date
```

初始门只阻挡明显超窗、缺失可用发表日期和明显非文章对象；`created/indexed`不能使旧论文进入周报。

## 3. 宽松身份初筛

依据Profile的身份词、成员词、疾病词、合格缩写上下文和排除实体进行宽松初筛。字符长度只能降低置信度，不能决定保留或删除。明确身份命中的短摘要和纯元数据边界记录继续进入去重。

## 4. 跨来源去重和信息合并

去重优先级：

```text
DOI > PMID > PMCID > 规范标题+第一作者+期刊+年份
```

主对象合并全部可信来源的标识符、作者、期刊、摘要、开放获取链接和日期字段；同时保存 `source_records` 和来源级日期审计。

## 5. 去重后内容补全

同一篇文章只补全一次。顺序：PMC、PubMed、Europe PMC、DOI元数据和开放获取位置、出版商页面。每次候选内容都进行三态身份核验：

- `identity_verified`：标识符一致，或标题/作者/期刊/年份综合一致；
- `identity_uncertain`：没有明确冲突但证据不足；
- `identity_conflict`：DOI/PMID/PMCID冲突或综合身份明显指向另一篇文章。

冲突是单调状态，后续弱匹配不得覆盖。只有 `identity_verified` 内容可写入正文或摘要字段。

## 6. 最终日期重算

跨库合并和补全可能带来更权威、更早的 `first_publication_date`。因此每个批次补全后重新计算规范日期并再次运行窗口门。最终展示、历史去重和未来出版标记全部依据第二次结果。

## 7. 补全后终审

文献终审发生在补全之后。它综合标题、核验摘要/全文、身份词、缩写上下文、排除实体和可靠元数据。终审拒绝只用于明确不相关、标识符冲突、非文章、无规范日期或唯一依据无效的记录。

## 8. 动态递补和全局Top50

默认最多补全150个候选，每批25个。比较池目标由：

```text
PIF_MAX_PAPERS + min(PIF_DISPLAY_CANDIDATE_BUFFER, PIF_MAX_PAPERS)
```

计算，默认50+50=100。系统继续处理直到：

- 100篇完成终审、分析和翻译的主报告候选；
- 候选耗尽；
- 150篇补全预算耗尽。

随后对完整比较池执行一次全局排序，Profile的 `paper_priority_terms` 会影响分值和层级，最终选择Top50，而不是先到先得。

## 9. 研究类型分类

Profile中的 `document_type_terms` 参与研究、系统综述、叙述性综述、病例、监测、方法和评论分类。固定正则只作为没有Profile命中时的通用兜底。

## 10. 主报告内容

主报告必须有核验摘要或全文证据。全文-only记录从正文中提取可追溯的 `full_text_excerpt` 作为英文原文依据，并翻译为中文。研究论文生成七要素，综述生成五要素。顶部总结只基于最终Top50证据。

## 11. 补充文献

终审通过但未进入Top50，或只有核验元数据的记录进入补充目录，最多100条。它们不生成摘要、研究要素或结论。标题翻译使用多接口递补，全部失败时保留英文标题并记录审计。

## 12. 输出和审计

标准数据：`data/latest.json`，schema 6.1。

主要审计：

```text
data/audit/profile.json
data/audit/query_plan.json
data/audit/controlled_supplemental_queries.json
data/audit/publication_date_gate.json
data/audit/literature_completion.json
data/audit/content_identity.jsonl
data/audit/relevance_review.json
data/audit/literature_selection.json
data/audit/translation.jsonl
data/audit/llm_calls.jsonl
data/audit/rendered_html_quality.json
```
