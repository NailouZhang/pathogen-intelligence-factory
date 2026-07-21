# v17.1 多语言分析与终审输出连续性修复

## 修复目标

v17.0 在禽流感运行中暴露出两个独立问题：非英文来源文本可能在 LLM 失败时被确定性抽取器写入英文结构化字段；HTML 审计又把日文汉字误判为中文，导致已完成的情报流程在最终审计阶段退出。另一个问题是两级断崖降级只负责恢复数量，没有覆盖首次运行的低接受比例，也没有明确规定“未达到恢复目标时仍应生成有效低量报告”。

v17.1 不改变三仓职责、21种病毒调度、数据库召回、微信不可变包和本地 Runner 路径，只加强语言契约、终审恢复和最终审计。

## 多语言四层保护

第一层在记录进入分析时识别 Latin、Han、Hiragana、Katakana、Hangul 和 Cyrillic 字符，保存 `source_language`、`title_original`、`abstract_original` 或 `content_original`。日文包含平假名或片假名时优先判定为 `ja`，不会再因为汉字而判定为中文。

第二层在 LLM 成功、LLM 失败和旧分析缓存命中三条路径上统一执行 `sanitize_english_analysis`。英文结构化字段只保留通过英文脚本校验的内容；日文、中文、韩文或西里尔文原句被转移到后台 `source_language_evidence`，公开英文要素使用证据不足的英文占位说明。原文没有删除，仍以明确的 `lang` 和 `data-source-language` 标记保存在来源区域。

第三层由渲染器再次检查每个英文 `<dd>`。即使上游或旧缓存遗漏了净化，非英文内容也会在渲染时替换为安全英文说明。原始非英文摘要和正文放在 `source-original` 区域，并使用 `lang="ja"`、`lang="zh"` 或其他来源语言标签。

第四层是工作流二次修复。第一次 HTML 审计失败时，Workflow 调用 `scripts/recover_language_contract.py`，重新净化 `latest.json`、重建 Pages 和微信包、再次校验微信字符预算，再执行第二次严格 HTML 审计。只有第二次仍存在结构性错误时才阻止发布。

## 三级终审恢复

标准终审仍使用21种病毒的完整内置终审词库，禁止回退五个核心检索词。标题、摘要或简讯、正文分别评分，文献和新闻阈值也相互独立。

一级恢复降低软字段阈值，但保留排除规则；二级恢复允许多个字段的身份与上下文联合证明，并适当放宽软排除；三级恢复只接受具有明确目标病毒身份、可靠元数据或可信新闻来源的记录。三级不会接受仅包含“感染、疫苗、监测”等普通上下文而没有目标病毒身份的记录。

以下硬冲突在标准、一级、二级和三级中永不放宽：DOI/PMID等标识符冲突、正文与元数据身份冲突、明确错误病毒或错误实体、模型明确返回 `N`。

## 首次运行低比例保护

新增参数：

```text
PIF_REVIEW_CLIFF_GUARD_MIN_ACCEPTED_RATIO=0.15
```

当候选不少于100且接受比例低于15%时，即使接受数量仍高于10，也会启动恢复。因此禽流感日志中的 `19/156=12.2%` 会触发保护。恢复目标取以下值中的最大值，并且不超过候选总数：绝对下限10、候选数的15%、历史有效结果的20%。

## 是否保证最终输出

v17.1 保证“流程输出连续”，但不通过伪造相关文献保证固定数量。处理结果分三种：达到恢复目标时发布 `recovered_output`；未达到目标但仍有合格记录时发布 `qualified_low_volume_output`；没有任何通过硬安全门禁的记录时发布 `empty_valid_issue`。第三种仍会生成合法的 `latest.json`、Pages 页面、微信包和后台审计，只显示本期没有满足标准的内容，不会因为数量为零直接中断。

以下错误仍会阻止发布，因为继续发布会破坏系统可信性：21套内置词库文件缺失或 SHA 不一致、Schema 损坏、发布包哈希不一致、无法解决的微信48,000字符预算、第二次审计后仍存在结构性 HTML 错误、跨仓凭据或 Git 推送失败。

## 关键生产参数

```text
PIF_VOCAB_SOURCE=bundled
PIF_VOCAB_BUNDLE_VERSION=2026.07-v17.1
PIF_REVIEW_ALLOW_CORE_TERMS_FALLBACK=false
PIF_REVIEW_CLIFF_GUARD_ENABLED=true
PIF_REVIEW_CLIFF_GUARD_MIN_CANDIDATES=100
PIF_REVIEW_CLIFF_GUARD_MIN_ACCEPTED=10
PIF_REVIEW_CLIFF_GUARD_MIN_ACCEPTED_RATIO=0.15
PIF_REVIEW_CLIFF_GUARD_PREVIOUS_RATIO=0.20
```

## 验收

```bash
python3 validate_v17_1_acceptance.py
bash validate_bundle.sh
```

必须得到：

```text
TOTAL=22 PASS=22 FAIL=0
BUNDLE_V17_1_VALIDATION=PASS
```
